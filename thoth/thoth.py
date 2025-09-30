import argparse
import json

import discord
from discord import app_commands
from discord.ui import View, Button

from thoth import cargo, data_models, ogame_api, members, ptre, reports
from thoth_interactions import trade


parser = argparse.ArgumentParser()
parser.add_argument(
    "--keyring",
    type=str,
    default="keyring.json",
    help="Path to keyring.json file (default: keyring.json)",
)
args = parser.parse_args()

with open(args.keyring, "r") as f:
    keyring = json.load(f)
BOT_TOKEN = keyring.get("bot_token")
SDM_GUILD_ID = keyring.get("guild_id")

data_models.initialize_connection(keyring["db_url"])
ptre.initialize_connection(keyring["ptre_team_key"])

client = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=SDM_GUILD_ID))
    await client.change_presence(
        activity=discord.Game("Might of Tsuki no Kami!")
    )


class PrivateChannel(app_commands.Group):
    def __init__(self):
        super().__init__(name="acs_channel", description="Manage ACS channels")
        self.active_channels: dict[int, discord.TextChannel] = {}

    async def _parse_mentions(self, interaction, mentions_str):
        members = []
        for tok in mentions_str.split():
            if tok.startswith("<@"):
                if member := await interaction.guild.fetch_member(
                    int(tok.strip("<@!>"))
                ):
                    members.append(member)
        return members

    @app_commands.command(name="create")
    @app_commands.describe(
        members="Members to include in the ACS channel",
    )
    async def create(
        self,
        interaction: discord.Interaction,
        members: str,
    ):
        if channel := self.active_channels.get(interaction.user.id):
            return await interaction.response.send_message(
                f"You already have {channel.mention}", ephemeral=True
            )

        channel = await interaction.guild.create_text_channel(
            f"private-{interaction.user.name}",
            overwrites={
                interaction.guild.default_role: discord.PermissionOverwrite(
                    view_channel=False
                ),
                interaction.user: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                ),
                **{
                    m: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                    )
                    for m in await self._parse_mentions(interaction, members)
                },
            },
            category=discord.utils.get(
                interaction.guild.categories, name="Private Channels"
            ),
        )
        self.active_channels[interaction.user.id] = channel
        await interaction.response.send_message(
            f"✅ Created {channel.mention}!"
        )

    async def _require_active_channel(self, interaction: discord.Interaction):
        if not (channel := self.active_channels.get(interaction.user.id)):
            await interaction.response.send_message(
                "No active channel.", ephemeral=True
            )
            return None
        return channel

    @app_commands.command(name="invite")
    @app_commands.describe(members="Members to invite to your ACS channel")
    async def invite(self, interaction: discord.Interaction, members: str):
        if not (channel := await self._require_active_channel(interaction)):
            return
        if not (members := await self._parse_mentions(interaction, members)):
            return await interaction.response.send_message(
                "No members provided.", ephemeral=True
            )

        for m in members:
            await channel.set_permissions(
                m,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
        await interaction.response.send_message(
            f"✅ Added {', '.join(m.mention for m in members)}.",
            ephemeral=True,
        )

    @app_commands.command(name="delete")
    async def delete(self, interaction: discord.Interaction):
        if not (channel := await self._require_active_channel(interaction)):
            return
        await interaction.response.send_message(
            f"🗑️ Deleting {channel.mention}...", ephemeral=True
        )
        await channel.delete(reason="Deleted by creator")
        self.active_channels.pop(interaction.user.id, None)


tree.add_command(PrivateChannel(), guild=discord.Object(id=SDM_GUILD_ID))


async def resolve_ogame_id(interaction, player_name):
    if player_name.startswith("<@"):
        if ogame_id := members.discord_to_ogame_id(
            int(player_name.strip("<@!>"))
        ):
            return ogame_id
        await interaction.response.send_message(
            f"❌ Discord user {player_name} is not linked to any OGame player.",
            ephemeral=True,
        )
        return None
    if ogame_id := ogame_api.get_player_id(player_name):
        return ogame_id
    await interaction.response.send_message(
        f"❌ OGame player **{player_name}** not found.", ephemeral=True
    )
    return None


@tree.command(
    name="lookup",
    description="Get OGame player info",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(player_name="OGame player name or Discord mention")
async def lookup(interaction: discord.Interaction, player_name: str):
    if not (ogame_id := await resolve_ogame_id(interaction, player_name)):
        return
    await interaction.response.defer()
    player_obj = ogame_api.get_player_info(ogame_id)
    await interaction.followup.send(embed=player_obj.to_discord_embed())


@tree.command(
    name="link_discord",
    description="Link a Discord user",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(
    discord_user="Discord user", player_name="OGame player name"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def link_discord(
    interaction: discord.Interaction,
    discord_user: discord.User,
    player_name: str,
):
    try:
        members.link(discord_user.id, player_name)
        await interaction.response.send_message(
            f"✅ Linked {discord_user.mention} to **{player_name}**."
        )
    except members.MembershipError as e:
        if e.reason == "player_not_found":
            msg = f"❌ OGame player **{player_name}** not found."
        elif e.reason == "discord_already_linked":
            msg = f"⚠️ {discord_user.mention} already linked."
        elif e.reason == "player_already_linked":
            msg = f"⚠️ **{player_name}** already linked to another user."
        else:
            raise
        await interaction.response.send_message(msg, ephemeral=True)


@tree.command(
    name="unlink_discord",
    description="Unlink a Discord user",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(discord_user="Discord user")
@app_commands.checks.has_permissions(manage_guild=True)
async def unlink_discord(
    interaction: discord.Interaction, discord_user: discord.User
):
    try:
        members.unlink(discord_user.id)
        await interaction.response.send_message(
            f"✅ Unlinked {discord_user.mention}."
        )
    except members.MembershipError as e:
        if e.reason == "not_linked":
            await interaction.response.send_message(
                f"❌ {discord_user.mention} not linked to an OGame player.",
                ephemeral=True,
            )
        else:
            raise


class ShowReportView(View):
    def __init__(self, api_key, compute_delta=True):
        super().__init__(timeout=120)
        self.api_key = api_key
        self.compute_delta = compute_delta

    @discord.ui.button(
        label="Show Report Contents", style=discord.ButtonStyle.primary
    )
    async def show_report(
        self, interaction: discord.Interaction, button: Button
    ):
        button.disabled = True
        self.stop()

        r = reports.ReportWithDelta(self.api_key, self.compute_delta)
        await interaction.response.edit_message(
            content="Here is the report for "
            f"**{reports.get_player_name_from_api_key(self.api_key)}**.",
            embed=r.to_discord_embed(ogame_api.get_ogame_localization_xml()),
            view=self,
        )


@tree.command(
    name="add_key",
    description="Add a Report API key",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(
    api_key="Report API key",
    player_name="OGame player or mention",
    force="Bypass checks when adding keys",
)
async def add_key(
    interaction: discord.Interaction,
    api_key: str,
    player_name: str = None,
    force: bool = False,
):
    try:
        ogame_id = None
        if not api_key.startswith("sr-"):
            if player_name:
                if not (
                    ogame_id := await resolve_ogame_id(interaction, player_name)
                ):
                    return
            else:
                ogame_id = members.discord_to_ogame_id(interaction.user.id)
            if not ogame_id:
                await interaction.response.send_message(
                    "❌ Could not determine OGame player to add the key for.",
                    ephemeral=True,
                )
                return
        api_key, source = reports.add_key(
            api_key, player_id=ogame_id, force=force
        )
        if source == "Ogame":
            ptre.push_to_ptre(api_key)
        r = reports.ReportWithDelta(api_key)
        player_name = reports.get_player_name_from_api_key(api_key)
        if r.is_sr_report and r.last_report and not r.has_delta:
            msg = (
                f"⚠️ Added key for **{player_name}**, "
                "there is no change since the last report. View report?"
            )
        else:
            msg = f"✅ Added key for **{player_name}**. View report?"

        await interaction.response.send_message(
            msg,
            view=ShowReportView(
                api_key,
                compute_delta=r.is_sr_report and r.has_delta,
            ),
        )
    except reports.ReportAPIException as e:
        if e.reason == "duplicate_key":
            player_name = reports.get_player_name_from_api_key(api_key)
            await interaction.response.send_message(
                f"❌ API key for **{player_name}** exists. View report?",
                view=ShowReportView(api_key, compute_delta=False),
            )
            return
        await interaction.response.send_message(
            f"❌ {e.message}", ephemeral=True
        )
        return


@tree.command(
    name="delete_key",
    description="Delete a Report API key and optionally its associated planet",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(
    api_key="Report API key to delete",
    delete_planet="Whether to also delete associated planet if manually added",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def delete_key(
    interaction: discord.Interaction,
    api_key: str,
    delete_planet: bool = False,
):
    try:
        deleted_coords = reports.delete_key(api_key, delete_planet)
        if deleted_coords:
            await interaction.response.send_message(
                f"✅ Deleted API key and planet at `{deleted_coords}`"
            )
        else:
            await interaction.response.send_message("✅ Deleted API key")
    except reports.ReportAPIException as e:
        await interaction.response.send_message(
            f"❌ {e.message}",
            ephemeral=True,
        )


@tree.command(
    name="trade",
    description="Offer a trade to alliance members",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(direction="Trade direction: buy or sell")
@app_commands.choices(
    direction=[
        app_commands.Choice(name="Buy", value="buy"),
        app_commands.Choice(name="Sell", value="sell"),
    ]
)
async def start_trade(
    interaction: discord.Interaction, direction: app_commands.Choice[str]
):
    buy = direction.value == "buy"
    await interaction.response.send_modal(trade.TradeModal(buy=buy))


@tree.command(
    name="expedition_calculator",
    description="Calculate large cargos needed for an expedition",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(
    res_find="Enhanced Sensor Technology bonus (%)",
    ship_find="Telekinetic Tractor Beam bonus (%)",
    small="Number of small cargos you have",
    pathfinder="Number of pathfinders you have",
)
async def expedition_calculator(
    interaction: discord.Interaction,
    res_find: float,
    ship_find: float,
    small: int = 0,
    pathfinder: int = 1,
):
    ogame_id = members.discord_to_ogame_id(interaction.user.id)
    try:
        needed_large = cargo.expedition_cargos(
            ogame_id, res_find, ship_find, small, pathfinder
        )
    except ValueError as e:
        await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
        return
    fleet_lines = []
    if small:
        fleet_lines.append(f"• Small: {small}")
    if pathfinder:
        fleet_lines.append(f"• Pathfinder: {pathfinder}")
    embed = discord.Embed(
        title="🚀 Expedition Cargo Calculation",
        description=(
            (
                (f"Your fleet:\n" + "\n".join(fleet_lines) + "\n\n")
                if fleet_lines
                else ""
            )
            + f"With **Enhanced Sensor Technology: {res_find}%** and "
            f"**Telekinetic Tractor Beam: {ship_find}%**, you need "
            f"**{needed_large:,} additional large cargos** to carry the "
            "maximum resources."
        ),
        color=discord.Color.blue(),
    )
    await interaction.response.send_message(embed=embed)


client.run(BOT_TOKEN)
