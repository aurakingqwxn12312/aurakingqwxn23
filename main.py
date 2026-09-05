import os
import asyncio
import json
from datetime import datetime, timezone
import discord
from discord import app_commands

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
if TOKEN.startswith("Bot "):
    TOKEN = TOKEN[4:].strip()
if len(TOKEN) >= 2 and TOKEN[0] == TOKEN[-1] and TOKEN[0] in {"'", '"'}:
    TOKEN = TOKEN[1:-1].strip()
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set. Add it in Railway → Variables.")

TEAM_ROLE_IDS = [
    1545598912696688641,
    1545598938407633038,
    1545598955499560960
]

CAPTAIN_ROLE_ID = 1542864703057830049
ROSTER_CAP = 24
ROSTER_CHANNEL_ID = 1516697443453112400
ROSTER_MESSAGE_ID = 1516699793907253301
TRANSACTIONS_CHANNEL_ID = 1542864705721339946
POSITIONS = ("GK", "CB", "FB", "CDM", "CM", "LM", "RM", "LW", "RW", "ST")
MONEY_FILE = os.environ.get("MONEY_FILE", "money_data.json")
CARD_PRICES = {
    "shield": ("🛡️", "Shield", 175000),
    "joker": ("🃏", "Joker", 225000),
    "heart": ("❤️", "Heart", 350000),
}

def load_money_data():
    if not os.path.exists(MONEY_FILE):
        return {"balances": {}, "members": {}, "transactions": []}

    try:
        with open(MONEY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MONEY_FILE} is not valid JSON: {error}") from error

    if not isinstance(data, dict):
        raise RuntimeError(f"{MONEY_FILE} must contain a JSON object.")

    data.setdefault("balances", {})
    data.setdefault("members", {})
    data.setdefault("transactions", [])
    return data

money_data = load_money_data()

def save_money_data():
    temporary_file = f"{MONEY_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(money_data, file, indent=2)
    os.replace(temporary_file, MONEY_FILE)

def money_display(amount):
    if amount % 1000 == 0:
        return f"{amount // 1000}K"
    return f"{amount:,}"

def member_display_name(member):
    return discord.utils.escape_markdown(member.display_name)

def balance_for(member):
    return int(money_data["balances"].get(str(member.id), 0))

def remember_member(member):
    money_data["members"][str(member.id)] = member.display_name

def record_money_change(member, amount, reason, transaction_type, source=None):
    current_balance = balance_for(member)
    new_balance = current_balance + amount
    if new_balance < 0:
        raise ValueError(
            f"{member.display_name} only has {money_display(current_balance)}."
        )

    member_id = str(member.id)
    remember_member(member)
    money_data["balances"][member_id] = new_balance
    money_data["transactions"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": transaction_type,
        "amount": amount,
        "player_id": member_id,
        "source": source,
        "reason": reason,
        "balance_after": new_balance,
    })
    save_money_data()
    return new_balance

def transfer_money(sender, receiver, amount, reason):
    sender_balance = balance_for(sender)
    if sender_balance < amount:
        raise ValueError(
            f"{sender.display_name} only has {money_display(sender_balance)}."
        )

    remember_member(sender)
    remember_member(receiver)
    sender_id = str(sender.id)
    receiver_id = str(receiver.id)
    money_data["balances"][sender_id] = sender_balance - amount
    money_data["balances"][receiver_id] = balance_for(receiver) + amount
    timestamp = datetime.now(timezone.utc).isoformat()
    money_data["transactions"].append({
        "timestamp": timestamp,
        "type": "transfer",
        "amount": amount,
        "from_id": sender_id,
        "to_id": receiver_id,
        "reason": reason,
        "sender_balance_after": money_data["balances"][sender_id],
        "receiver_balance_after": money_data["balances"][receiver_id],
    })
    save_money_data()
    return money_data["balances"][sender_id], money_data["balances"][receiver_id]

async def post_money_transaction(guild, content):
    channel = get_transactions_channel(guild)
    if channel:
        await channel.send(content)
        return True
    return False

def is_authorized(member):
    if member.guild_permissions.administrator:
        return True
    return any(role.id == CAPTAIN_ROLE_ID for role in member.roles)

class Client(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.health_server = None

    async def setup_hook(self):
        await self.tree.sync()
        self.health_server = await asyncio.start_server(
            self.health_check,
            host="0.0.0.0",
            port=int(os.environ.get("PORT", "8000"))
        )

    async def health_check(self, reader, writer):
        try:
            await reader.read(1024)
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 2\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"OK"
            )
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        for guild in self.guilds:
            await guild.chunk()

client = Client()

draft_assignments = {}
draft_view = None
draft_panel_message = None
draft_result_message = None

@client.tree.error
async def on_app_command_error(interaction, error):
    print(f"Application command error: {error}")
    message = "Something went wrong while running that command. Please try again."
    if isinstance(error, app_commands.MissingPermissions):
        message = "You do not have permission to use that command."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

def draft_panel_content(assignments):
    lines = [
        "⚽ **LIVE DRAFT**",
        "Click one position below to select it. Clicking a different position moves you.",
        "Your name and position will appear in `/draftresult`.",
        "",
        f"**Players currently selected:** {len(assignments)}"
    ]
    return "\n".join(lines)

def draft_result_content(assignments):
    lines = [
        "⚽ **DRAFT RESULTS**",
        ""
    ]
    for position in POSITIONS:
        players = sorted(
            [
                discord.utils.escape_markdown(player_name)
                for player_name, selected_position in assignments.values()
                if selected_position == position
            ],
            key=str.casefold
        )
        if players:
            lines.extend([f"• {player_name} — **{position}**" for player_name in players])

    if not assignments:
        lines.append("**No players have selected a position yet.**")

    return "\n".join(lines)

async def update_draft_result_message():
    global draft_result_message
    if not draft_result_message:
        return
    try:
        await draft_result_message.edit(content=draft_result_content(draft_assignments))
    except discord.NotFound:
        draft_result_message = None

class DraftView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.assignments = draft_assignments
        self.closed = False

    async def choose_position(self, interaction, position):
        if self.closed:
            return await interaction.response.send_message(
                "This draft is closed.", ephemeral=True
            )

        self.assignments[interaction.user.id] = (
            interaction.user.display_name,
            position
        )
        await interaction.response.edit_message(
            content=draft_panel_content(self.assignments),
            view=self
        )
        await update_draft_result_message()

    async def remove_selection(self, interaction):
        if self.closed:
            return await interaction.response.send_message(
                "This draft is closed.", ephemeral=True
            )

        self.assignments.pop(interaction.user.id, None)
        await interaction.response.edit_message(
            content=draft_panel_content(self.assignments),
            view=self
        )
        await update_draft_result_message()

    @discord.ui.button(label="GK", style=discord.ButtonStyle.primary, row=0)
    async def gk(self, interaction, button):
        await self.choose_position(interaction, "GK")

    @discord.ui.button(label="CB", style=discord.ButtonStyle.primary, row=0)
    async def cb(self, interaction, button):
        await self.choose_position(interaction, "CB")

    @discord.ui.button(label="FB", style=discord.ButtonStyle.primary, row=0)
    async def fb(self, interaction, button):
        await self.choose_position(interaction, "FB")

    @discord.ui.button(label="CDM", style=discord.ButtonStyle.primary, row=0)
    async def cdm(self, interaction, button):
        await self.choose_position(interaction, "CDM")

    @discord.ui.button(label="CM", style=discord.ButtonStyle.primary, row=0)
    async def cm(self, interaction, button):
        await self.choose_position(interaction, "CM")

    @discord.ui.button(label="LM", style=discord.ButtonStyle.primary, row=1)
    async def lm(self, interaction, button):
        await self.choose_position(interaction, "LM")

    @discord.ui.button(label="RM", style=discord.ButtonStyle.primary, row=1)
    async def rm(self, interaction, button):
        await self.choose_position(interaction, "RM")

    @discord.ui.button(label="LW", style=discord.ButtonStyle.primary, row=1)
    async def lw(self, interaction, button):
        await self.choose_position(interaction, "LW")

    @discord.ui.button(label="RW", style=discord.ButtonStyle.primary, row=1)
    async def rw(self, interaction, button):
        await self.choose_position(interaction, "RW")

    @discord.ui.button(label="ST", style=discord.ButtonStyle.primary, row=2)
    async def st(self, interaction, button):
        await self.choose_position(interaction, "ST")

    @discord.ui.button(label="Remove selection", style=discord.ButtonStyle.secondary, row=2)
    async def remove(self, interaction, button):
        await self.remove_selection(interaction)

    @discord.ui.button(label="Close draft", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "Only admins can close a draft.", ephemeral=True
            )

        self.closed = True
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=draft_panel_content(self.assignments) + "\n\n🔒 **DRAFT CLOSED**",
            view=self
        )

def get_team_role(member):
    for role in member.roles:
        if role.id in TEAM_ROLE_IDS:
            return role
    return None

def get_transactions_channel(guild):
    return guild.get_channel(TRANSACTIONS_CHANNEL_ID)

def is_admin(member):
    return member.guild_permissions.administrator

@client.tree.command(name="balance", description="Check a player's money balance")
@app_commands.describe(player="The player whose balance you want to check")
async def balance(interaction: discord.Interaction, player: discord.Member = None):
    target = player or interaction.user
    remember_member(target)
    save_money_data()
    await interaction.response.send_message(
        f"💰 **{member_display_name(target)}** has **{money_display(balance_for(target))}**.",
        ephemeral=True
    )

@client.tree.command(name="moneyboard", description="Show the richest players")
async def moneyboard(interaction: discord.Interaction):
    entries = sorted(
        [
            (name, int(money_data["balances"].get(member_id, 0)))
            for member_id, name in money_data["members"].items()
            if int(money_data["balances"].get(member_id, 0)) > 0
        ],
        key=lambda entry: (-entry[1], entry[0].casefold())
    )

    if not entries:
        return await interaction.response.send_message(
            "💰 No player balances have been created yet."
        )

    lines = ["💰 **MONEY LEADERBOARD**", ""]
    for index, (name, amount) in enumerate(entries[:25], start=1):
        lines.append(f"**{index}.** {discord.utils.escape_markdown(name)} — **{money_display(amount)}**")

    await interaction.response.send_message("\n".join(lines))

@client.tree.command(name="pay", description="Pay another player from your balance")
@app_commands.describe(
    player="The player to pay",
    amount="The amount to pay",
    reason="Why you are paying them"
)
async def pay(
    interaction: discord.Interaction,
    player: discord.Member,
    amount: app_commands.Range[int, 1, 1000000000],
    reason: str
):
    if player.id == interaction.user.id:
        return await interaction.response.send_message(
            "You cannot pay yourself.", ephemeral=True
        )
    if player.bot:
        return await interaction.response.send_message(
            "You cannot pay a bot.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    try:
        sender_balance, receiver_balance = transfer_money(
            interaction.user, player, amount, reason
        )
    except ValueError as error:
        return await interaction.followup.send(str(error), ephemeral=True)

    await post_money_transaction(
        interaction.guild,
        f"💸 **PLAYER PAYMENT**\n"
        f"{member_display_name(interaction.user)} paid {member_display_name(player)} "
        f"**{money_display(amount)}**.\n"
        f"Reason: {reason}"
    )
    await interaction.followup.send(
        f"Payment sent. Your new balance is **{money_display(sender_balance)}**. "
        f"{member_display_name(player)} now has **{money_display(receiver_balance)}**.",
        ephemeral=True
    )

@client.tree.command(name="moneyadd", description="Add money to a player's balance")
@app_commands.describe(
    player="The player receiving money",
    amount="The amount to add",
    reason="For example: league win, scrim win, trade, or free-agent payment"
)
async def moneyadd(
    interaction: discord.Interaction,
    player: discord.Member,
    amount: app_commands.Range[int, 1, 1000000000],
    reason: str
):
    if not is_admin(interaction.user):
        return await interaction.response.send_message(
            "Only admins can add money.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    new_balance = record_money_change(
        player,
        amount,
        reason,
        "credit",
        source=str(interaction.user.id)
    )
    await post_money_transaction(
        interaction.guild,
        f"📈 **MONEY ADDED**\n"
        f"{member_display_name(player)} received **{money_display(amount)}**.\n"
        f"Reason: {reason}\n"
        f"New balance: **{money_display(new_balance)}**"
    )
    await interaction.followup.send(
        f"Added **{money_display(amount)}** to {member_display_name(player)}. "
        f"New balance: **{money_display(new_balance)}**.",
        ephemeral=True
    )

@client.tree.command(name="moneyremove", description="Remove money from a player's balance")
@app_commands.describe(
    player="The player being charged",
    amount="The amount to remove",
    reason="For example: league entry fee, lost bet, trade, or free-agent fee"
)
async def moneyremove(
    interaction: discord.Interaction,
    player: discord.Member,
    amount: app_commands.Range[int, 1, 1000000000],
    reason: str
):
    if not is_admin(interaction.user):
        return await interaction.response.send_message(
            "Only admins can remove money.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    try:
        new_balance = record_money_change(
            player,
            -amount,
            reason,
            "debit",
            source=str(interaction.user.id)
        )
    except ValueError as error:
        return await interaction.followup.send(str(error), ephemeral=True)

    await post_money_transaction(
        interaction.guild,
        f"📉 **MONEY REMOVED**\n"
        f"{member_display_name(player)} paid **{money_display(amount)}**.\n"
        f"Reason: {reason}\n"
        f"New balance: **{money_display(new_balance)}**"
    )
    await interaction.followup.send(
        f"Removed **{money_display(amount)}** from {member_display_name(player)}. "
        f"New balance: **{money_display(new_balance)}**.",
        ephemeral=True
    )

@client.tree.command(name="buycard", description="Buy a card using your balance")
@app_commands.describe(card="The card you want to buy")
@app_commands.choices(card=[
    app_commands.Choice(name="🛡️ Shield — 175K", value="shield"),
    app_commands.Choice(name="🃏 Joker — 225K", value="joker"),
    app_commands.Choice(name="❤️ Heart — 350K", value="heart"),
])
async def buycard(interaction: discord.Interaction, card: app_commands.Choice[str]):
    emoji, card_name, price = CARD_PRICES[card.value]
    await interaction.response.defer(ephemeral=True)
    try:
        new_balance = record_money_change(
            interaction.user,
            -price,
            f"Bought {card_name} card",
            "card_purchase"
        )
    except ValueError as error:
        return await interaction.followup.send(str(error), ephemeral=True)

    await post_money_transaction(
        interaction.guild,
        f"{emoji} **CARD PURCHASE**\n"
        f"{member_display_name(interaction.user)} bought a **{card_name}** card "
        f"for **{money_display(price)}**.\n"
        f"New balance: **{money_display(new_balance)}**"
    )
    await interaction.followup.send(
        f"You bought the {emoji} **{card_name}** card for **{money_display(price)}**. "
        f"Your new balance is **{money_display(new_balance)}**.",
        ephemeral=True
    )

@client.tree.command(name="moneyrules", description="Show the current league money rules")
async def moneyrules(interaction: discord.Interaction):
    await interaction.response.send_message(
        "💰 **LEAGUE MONEY RULES**\n\n"
        "**Ways to earn:**\n"
        "• Trading players\n"
        "• Winning a league game: 40K–80K, or 120K for mercy\n"
        "• Winning a scrim bet\n"
        "• Winning a captain's league bet\n\n"
        "**Ways to lose:**\n"
        "• Trading players\n"
        "• Losing a scrim bet\n"
        "• League game entry fee: 10K\n\n"
        "**Free-agent fees:**\n"
        "• LB player: 30K–150K\n"
        "• Normal free agent: 29K or below\n\n"
        "**Card prices:**\n"
        "• 🛡️ Shield: 175K\n"
        "• 🃏 Joker: 225K\n"
        "• ❤️ Heart: 350K"
    )

def is_captain_of(member, team_role):
    return (
        any(role.id == CAPTAIN_ROLE_ID for role in member.roles) and
        get_team_role(member) is not None and
        get_team_role(member).id == team_role.id
    )

async def move_players(players, new_role):
    for player in players:
        old_role = get_team_role(player)
        if old_role:
            await player.remove_roles(old_role)
        await player.add_roles(new_role)

async def update_roster_message(guild):
    if not ROSTER_CHANNEL_ID:
        return
    channel = guild.get_channel(ROSTER_CHANNEL_ID)
    if not channel:
        return

    await asyncio.sleep(2)

    lines = []
    for role_id in TEAM_ROLE_IDS:
        role = guild.get_role(role_id)
        if not role:
            continue
        members = role.members
        if members:
            names = "\n".join([f"• {m.display_name}" for m in members])
            lines.append(f"**{role.name}** ({len(members)}/{ROSTER_CAP})\n{names}")
        else:
            lines.append(f"**{role.name}** — no players (0/{ROSTER_CAP})")

    content = "📋 **LIVE TEAM ROSTERS**\n\n" + "\n\n".join(lines)

    try:
        msg = await channel.fetch_message(ROSTER_MESSAGE_ID)
        await msg.edit(content=content)
    except discord.NotFound:
        msg = await channel.send(content)
        await msg.pin()

def trade_message(team_a_players, team_b_players, team_a_role, team_b_role, a_accepted, b_accepted, status="pending"):
    team_a_names = "\n".join([f"• {p.mention}" for p in team_a_players])
    team_b_names = "\n".join([f"• {p.mention}" for p in team_b_players])
    a_status = "✅" if a_accepted else "⏳"
    b_status = "✅" if b_accepted else "⏳"

    if status == "confirmed":
        return (
            f"✅ **TRADE CONFIRMED**\n\n"
            f"**{team_b_role.name} receive:**\n{team_a_names}\n\n"
            f"**{team_a_role.name} receive:**\n{team_b_names}"
        )

    footer = (
        "⏳ Waiting for admin to confirm the trade."
        if a_accepted and b_accepted
        else "Both captains must accept before an admin can confirm."
    )

    return (
        f"🔄 **TRADE PENDING**\n\n"
        f"**{team_b_role.name} receive:**\n{team_a_names}\n\n"
        f"**{team_a_role.name} receive:**\n{team_b_names}\n\n"
        f"{a_status} **{team_a_role.name}** captain acceptance\n"
        f"{b_status} **{team_b_role.name}** captain acceptance\n\n"
        f"{footer}"
    )

class TradeView(discord.ui.View):
    def __init__(self, team_a_players, team_b_players, team_a_role, team_b_role):
        super().__init__(timeout=None)
        self.team_a_players = team_a_players
        self.team_b_players = team_b_players
        self.team_a_role = team_a_role
        self.team_b_role = team_b_role
        self.team_a_accepted = False
        self.team_b_accepted = False
        self.confirm_button.disabled = True

    @discord.ui.button(label="Accept (Team A)", style=discord.ButtonStyle.green)
    async def accept_team_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (is_captain_of(interaction.user, self.team_a_role) or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message(
                f"Only the captain of **{self.team_a_role.name}** can accept for their team.", ephemeral=True
            )
        if self.team_a_accepted:
            return await interaction.response.send_message("This team has already accepted.", ephemeral=True)

        self.team_a_accepted = True
        button.disabled = True
        button.label = f"✅ {self.team_a_role.name} Accepted"

        if self.team_a_accepted and self.team_b_accepted:
            self.confirm_button.disabled = False

        await interaction.response.edit_message(
            content=trade_message(
                self.team_a_players, self.team_b_players,
                self.team_a_role, self.team_b_role,
                self.team_a_accepted, self.team_b_accepted
            ),
            view=self
        )

    @discord.ui.button(label="Accept (Team B)", style=discord.ButtonStyle.green)
    async def accept_team_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (is_captain_of(interaction.user, self.team_b_role) or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message(
                f"Only the captain of **{self.team_b_role.name}** can accept for their team.", ephemeral=True
            )
        if self.team_b_accepted:
            return await interaction.response.send_message("This team has already accepted.", ephemeral=True)

        self.team_b_accepted = True
        button.disabled = True
        button.label = f"✅ {self.team_b_role.name} Accepted"

        if self.team_a_accepted and self.team_b_accepted:
            self.confirm_button.disabled = False

        await interaction.response.edit_message(
            content=trade_message(
                self.team_a_players, self.team_b_players,
                self.team_a_role, self.team_b_role,
                self.team_a_accepted, self.team_b_accepted
            ),
            view=self
        )

    @discord.ui.button(label="Confirm Trade (Admin)", style=discord.ButtonStyle.blurple)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only admins can confirm trades.", ephemeral=True)

        await interaction.response.defer()

        await move_players(self.team_a_players, self.team_b_role)
        await move_players(self.team_b_players, self.team_a_role)

        for item in self.children:
            item.disabled = True

        await interaction.message.edit(
            content=trade_message(
                self.team_a_players, self.team_b_players,
                self.team_a_role, self.team_b_role,
                True, True, status="confirmed"
            ) + f"\n\nConfirmed by {interaction.user.mention}",
            view=self
        )
        await update_roster_message(interaction.guild)

class ConfirmSign(discord.ui.View):
    def __init__(self, player, team_role, requested_by):
        super().__init__(timeout=None)
        self.player = player
        self.team_role = team_role
        self.requested_by = requested_by
        self.player_confirmed = False
        self.approve_button.disabled = True

    @discord.ui.button(label="Accept Signing", style=discord.ButtonStyle.green)
    async def player_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player.id:
            return await interaction.response.send_message("Only the player being signed can accept.", ephemeral=True)

        self.player_confirmed = True
        button.disabled = True
        button.label = "✅ Player Accepted"
        self.approve_button.disabled = False

        await interaction.response.edit_message(
            content=
            f"📋 **SIGNING PENDING — ADMIN APPROVAL NEEDED**\n\n"
            f"{self.player.mention} → **{self.team_role.name}**\n\n"
            f"✅ Player has accepted\n\n"
            f"Requested by {self.requested_by.mention}\n\n"
            f"Waiting for an admin to approve.",
            view=self
        )

    @discord.ui.button(label="Approve (Admin)", style=discord.ButtonStyle.blurple)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only admins can approve signings.", ephemeral=True)

        await interaction.response.defer()
        await self.player.add_roles(self.team_role)

        for item in self.children:
            item.disabled = True

        await interaction.message.edit(
            content=
            f"✅ **SIGNING APPROVED**\n\n"
            f"{self.player.mention} has been signed to **{self.team_role.name}**.\n\n"
            f"Requested by {self.requested_by.mention} • Approved by {interaction.user.mention}",
            view=self
        )
        await update_roster_message(interaction.guild)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only admins can reject signings.", ephemeral=True)

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=
            f"❌ **SIGNING REJECTED**\n\n"
            f"{self.player.mention} to **{self.team_role.name}** was rejected by {interaction.user.mention}.",
            view=self
        )

@client.tree.command(name="sign", description="Sign an unsigned player to your team")
@app_commands.describe(player="The player to sign")
async def sign(interaction: discord.Interaction, player: discord.Member):
    if not is_authorized(interaction.user):
        return await interaction.response.send_message("Only captains and admins can use this command.", ephemeral=True)

    if get_team_role(player):
        return await interaction.response.send_message(f"{player.mention} is already on a team. Use `/trade` to move them.", ephemeral=True)

    signer_team = get_team_role(interaction.user)
    if not signer_team and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("You are not on a team, so you cannot sign players.", ephemeral=True)

    if not signer_team:
        return await interaction.response.send_message("You need to be on a team to use `/sign`.", ephemeral=True)

    if len(signer_team.members) >= ROSTER_CAP:
        return await interaction.response.send_message(
            f"**{signer_team.name}** is full ({ROSTER_CAP}/{ROSTER_CAP} players). Release a player before signing a new one.",
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)

    view = ConfirmSign(player, signer_team, interaction.user)
    channel = get_transactions_channel(interaction.guild)
    if not channel:
        return await interaction.followup.send(
            "I could not find the configured transactions channel.", ephemeral=True
        )

    await channel.send(
        f"📋 **SIGNING PENDING**\n\n"
        f"{player.mention} → **{signer_team.name}**\n\n"
        f"Requested by {interaction.user.mention}\n\n"
        f"⏳ Waiting for {player.mention} to accept.",
        view=view
    )
    await interaction.followup.send(
        f"Signing request posted in <#{TRANSACTIONS_CHANNEL_ID}>.",
        ephemeral=True
    )

@client.tree.command(name="draft", description="Start a live position draft board")
async def draft(interaction: discord.Interaction):
    global draft_view, draft_panel_message

    if not is_authorized(interaction.user):
        return await interaction.response.send_message(
            "Only captains and admins can start a draft.", ephemeral=True
        )

    if draft_view and draft_panel_message and not draft_view.closed:
        try:
            await draft_panel_message.edit(
                content=draft_panel_content(draft_assignments),
                view=draft_view
            )
            return await interaction.response.send_message(
                "The live draft panel is already active.", ephemeral=True
            )
        except discord.NotFound:
            draft_panel_message = None

    view = DraftView()
    await interaction.response.defer()
    draft_panel_message = await interaction.followup.send(
        content=draft_panel_content(view.assignments),
        view=view,
        wait=True
    )
    draft_view = view

@client.tree.command(name="draftresult", description="Show or update the draft result message")
async def draftresult(interaction: discord.Interaction):
    global draft_result_message

    if not is_authorized(interaction.user):
        return await interaction.response.send_message(
            "Only captains and admins can show draft results.", ephemeral=True
        )

    content = draft_result_content(draft_assignments)
    if draft_result_message:
        try:
            await draft_result_message.edit(content=content)
            return await interaction.response.send_message(
                "The draft result message was updated.", ephemeral=True
            )
        except discord.NotFound:
            draft_result_message = None

    await interaction.response.defer()
    draft_result_message = await interaction.followup.send(content=content, wait=True)

@client.tree.command(name="draftreset", description="Reset all draft position selections")
async def draftreset(interaction: discord.Interaction):
    global draft_panel_message, draft_view

    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "Only admins can reset the draft.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    draft_assignments.clear()

    if draft_view:
        draft_view.closed = False
        for item in draft_view.children:
            item.disabled = False

    if draft_panel_message:
        try:
            await draft_panel_message.edit(
                content=draft_panel_content(draft_assignments),
                view=draft_view
            )
        except discord.NotFound:
            draft_panel_message = None

    await update_draft_result_message()
    await interaction.followup.send("The draft has been reset.", ephemeral=True)

@client.tree.command(name="release", description="Release a player from your team")
@app_commands.describe(player="The player to release")
async def release(interaction: discord.Interaction, player: discord.Member):
    if not is_authorized(interaction.user):
        return await interaction.response.send_message("Only captains and admins can use this command.", ephemeral=True)

    player_team = get_team_role(player)
    if not player_team:
        return await interaction.response.send_message(f"{player.mention} is not on any team.", ephemeral=True)

    signer_team = get_team_role(interaction.user)
    if not interaction.user.guild_permissions.administrator and (not signer_team or signer_team.id != player_team.id):
        return await interaction.response.send_message("You can only release players from your own team.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    await player.remove_roles(player_team)

    channel = get_transactions_channel(interaction.guild)
    if not channel:
        return await interaction.followup.send(
            "The player was released, but I could not find the configured transactions channel.",
            ephemeral=True
        )

    await channel.send(
        f"📤 **PLAYER RELEASED**\n\n"
        f"{player.mention} has been released from **{player_team.name}**.\n\n"
        f"Released by {interaction.user.mention}"
    )
    await interaction.followup.send(
        f"Release posted in <#{TRANSACTIONS_CHANNEL_ID}>.",
        ephemeral=True
    )
    await update_roster_message(interaction.guild)

@client.tree.command(name="roster", description="List all players on each team")
async def roster(interaction: discord.Interaction):
    if not is_authorized(interaction.user):
        return await interaction.response.send_message("Only captains and admins can use this command.", ephemeral=True)

    await interaction.response.defer()

    guild = interaction.guild
    lines = []
    missing_ids = []

    for role_id in TEAM_ROLE_IDS:
        role = guild.get_role(role_id)
        if not role:
            missing_ids.append(str(role_id))
            continue
        members = role.members
        if members:
            names = "\n".join([f"• {m.display_name}" for m in members])
            lines.append(f"**{role.name}** ({len(members)}/{ROSTER_CAP} players)\n{names}")
        else:
            lines.append(f"**{role.name}** — no players (0/{ROSTER_CAP})")

    if missing_ids:
        lines.append(f"\n⚠️ **Could not find roles with these IDs** (update TEAM_ROLE_IDS in the code):\n" + "\n".join(missing_ids))

    if not lines:
        return await interaction.followup.send(
            f"❌ No team roles found. None of the configured role IDs exist in this server:\n" + "\n".join(str(i) for i in TEAM_ROLE_IDS),
            ephemeral=True
        )

    content = "📋 **TEAM ROSTERS**\n\n" + "\n\n".join(lines)
    if len(content) > 2000:
        chunks = []
        current = "📋 **TEAM ROSTERS**\n\n"
        for line in lines:
            if len(current) + len(line) + 2 > 2000:
                chunks.append(current)
                current = line + "\n\n"
            else:
                current += line + "\n\n"
        if current:
            chunks.append(current)
        await interaction.followup.send(chunks[0])
        for chunk in chunks[1:]:
            await interaction.channel.send(chunk)
    else:
        await interaction.followup.send(content)


@client.tree.command(name="trade", description="Create a trade request between teams")
async def trade(
    interaction: discord.Interaction,
    team_a_player_1: discord.Member,
    team_b_player_1: discord.Member,
    team_a_player_2: discord.Member = None,
    team_a_player_3: discord.Member = None,
    team_a_player_4: discord.Member = None,
    team_a_player_5: discord.Member = None,
    team_b_player_2: discord.Member = None,
    team_b_player_3: discord.Member = None,
    team_b_player_4: discord.Member = None,
    team_b_player_5: discord.Member = None
):
    if not is_authorized(interaction.user):
        return await interaction.response.send_message("Only captains and admins can use this command.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    team_a_players = [p for p in [team_a_player_1, team_a_player_2, team_a_player_3, team_a_player_4, team_a_player_5] if p]
    team_b_players = [p for p in [team_b_player_1, team_b_player_2, team_b_player_3, team_b_player_4, team_b_player_5] if p]

    team_a_role = get_team_role(team_a_player_1)
    team_b_role = get_team_role(team_b_player_1)

    if not team_a_role:
        return await interaction.followup.send(f"{team_a_player_1.mention} has no team role.", ephemeral=True)

    if not team_b_role:
        return await interaction.followup.send(f"{team_b_player_1.mention} has no team role.", ephemeral=True)

    if team_a_role.id == team_b_role.id:
        return await interaction.followup.send("Both players are already on the same team.", ephemeral=True)

    for player in team_a_players + team_b_players:
        if not get_team_role(player):
            return await interaction.followup.send(f"{player.mention} has no team role.", ephemeral=True)

    team_a_after = len(team_a_role.members) - len(team_a_players) + len(team_b_players)
    team_b_after = len(team_b_role.members) - len(team_b_players) + len(team_a_players)

    if team_a_after > ROSTER_CAP:
        return await interaction.followup.send(
            f"This trade would put **{team_a_role.name}** over the {ROSTER_CAP}-player cap ({team_a_after} players).",
            ephemeral=True
        )
    if team_b_after > ROSTER_CAP:
        return await interaction.followup.send(
            f"This trade would put **{team_b_role.name}** over the {ROSTER_CAP}-player cap ({team_b_after} players).",
            ephemeral=True
        )

    view = TradeView(team_a_players, team_b_players, team_a_role, team_b_role)
    channel = get_transactions_channel(interaction.guild)
    if not channel:
        return await interaction.followup.send(
            "I could not find the configured transactions channel.", ephemeral=True
        )

    await channel.send(
        content=trade_message(team_a_players, team_b_players, team_a_role, team_b_role, False, False),
        view=view
    )
    await interaction.followup.send(
        f"Trade request posted in <#{TRANSACTIONS_CHANNEL_ID}>.",
        ephemeral=True
    )

client.run(TOKEN, reconnect=True)
