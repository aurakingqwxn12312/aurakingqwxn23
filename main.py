import os
import asyncio
import discord
from discord import app_commands

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set. Add it in Railway → Variables.")

TEAM_ROLE_IDS = [
    1515268091133563031,
    1515268090105958400,
    1515268091955646464,
    1515268391915749466
]

CAPTAIN_ROLE_ID = 1542864703057830049
ROSTER_CAP = 21
ROSTER_CHANNEL_ID = 1516697443453112400
ROSTER_MESSAGE_ID = 1516699793907253301
TRANSACTIONS_CHANNEL_ID = 1542864705721339946
POSITIONS = ("GK", "CB", "FB", "CDM", "CM", "LM", "RM", "LW", "RW", "ST")

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
    await interaction.response.send_message(
        content=draft_panel_content(view.assignments),
        view=view
    )
    draft_view = view
    draft_panel_message = await interaction.original_response()

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

    await interaction.response.send_message(content=content)
    draft_result_message = await interaction.original_response()

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
