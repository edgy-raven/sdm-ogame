import argparse
import json

import discord
from discord import app_commands
from discord.ui import View, Button

from thoth import ogame_api, members, trade


parser = argparse.ArgumentParser(description="SDM OGame Discord Bot")
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

client = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=SDM_GUILD_ID))
    await client.change_presence(
        activity=discord.Game("Might of Tsuki no Kami!")
    )


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
    player_obj = ogame_api.get_player_info(ogame_id)
    await interaction.response.send_message(embed=player_obj.to_discord_embed())


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

        r = ogame_api.ReportWithDelta(self.api_key, self.compute_delta)
        await interaction.response.edit_message(
            content="Here is the report for "
            f"**{ogame_api.get_player_name_from_api_key(self.api_key)}**.",
            embed=r.to_discord_embed(),
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
        ogame_api.add_key(api_key, player_id=ogame_id, force=force)
        r = ogame_api.ReportWithDelta(api_key)
        player_name = ogame_api.get_player_name_from_api_key(api_key)

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
    except ogame_api.ReportAPIException as e:
        if e.reason == "duplicate_key":
            player_name = ogame_api.get_player_name_from_api_key(api_key)
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
        deleted_coords = ogame_api.delete_report_and_planet(
            api_key, delete_planet
        )
        if deleted_coords:
            await interaction.response.send_message(
                f"✅ Deleted API key and planet at `{deleted_coords}`"
            )
        else:
            await interaction.response.send_message("✅ Deleted API key")
    except ogame_api.ReportAPIException as e:
        await interaction.response.send_message(
            f"❌ {e.message}",
            ephemeral=True,
        )


@tree.command(
    name="convert",
    description="Convert between resources for trades",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(
    amount="The amount to convert (supports shorthands, e.g.,"
    "2kk deut = 2,000,000 deuterium)",
    to_currency="The currency you want to convert to (default: crystal)",
    from_currency="The currency you are converting from (default: deuterium)",
)
async def convert(
    interaction: discord.Interaction,
    amount: str,
    to_currency: trade.Currency = trade.Currency.crystal,
    from_currency: trade.Currency = trade.Currency.deuterium,
):
    try:
        amount, embedded_currency = trade.parse_amount_and_currency(amount)
    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid amount format.",
            ephemeral=True,
        )
        return

    if embedded_currency:
        from_currency = embedded_currency
    converted = int(round(from_currency.convert(amount, to_currency)))
    await interaction.response.send_message(
        f"💱 {amount:,.0f} {from_currency.display} = "
        f"{converted:,.0f} {to_currency.display}"
    )


client.run(BOT_TOKEN)
