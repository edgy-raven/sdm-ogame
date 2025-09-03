import json

import discord
from discord import app_commands

from thoth import ogame_api, members, trade


with open("keyring.json", "r") as f:
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


async def resolve_ogame_id(
    interaction: discord.Interaction, player_name: str
) -> int | None:
    if player_name.startswith("<@"):
        if discord_model := members.discord_to_ogame_id(
            int(player_name.strip("<@!>"))
        ):
            return discord_model.ogame_id
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
    name="ogame_lookup",
    description="Get OGame player info",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(player_name="OGame player name or Discord mention")
async def ogame_lookup(interaction: discord.Interaction, player_name: str):
    if not (ogame_id := await resolve_ogame_id(interaction, player_name)):
        return
    player_obj = ogame_api.get_player_info(ogame_id)
    await interaction.response.send_message(embed=player_obj.to_discord_embed())


@tree.command(
    name="ogame_link",
    description="Link a Discord user",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(
    discord_user="Discord user", player_name="OGame player name"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def ogame_link(
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
            msg = f"❌ OGame player **{e.details['player_name']}** not found."
        elif e.reason == "discord_already_linked":
            msg = (
                f"⚠️ {discord_user.mention} already linked to "
                f"**{e.details['linked_player'].name}**."
            )
        elif e.reason == "player_already_linked":
            msg = (
                f"⚠️ **{e.details['player'].name}** already linked to "
                f"another user."
            )
        else:
            raise
        await interaction.response.send_message(msg, ephemeral=True)


@tree.command(
    name="ogame_unlink",
    description="Unlink a Discord user",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(discord_user="Discord user")
@app_commands.checks.has_permissions(manage_guild=True)
async def ogame_unlink(
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
                f"❌ {discord_user.mention} not linked to any OGame player.",
                ephemeral=True,
            )
        else:
            raise


@tree.command(
    name="ogame_add_key",
    description="Add a Report API key",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(
    player_name="OGame player or mention", api_key="Report API key"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def ogame_add_key(
    interaction: discord.Interaction, player_name: str, api_key: str
) -> None:
    if not (ogame_id := await resolve_ogame_id(interaction, player_name)):
        return
    ogame_api.add_report_api_key(ogame_id, api_key)
    await interaction.response.send_message(
        f"✅ Added Report API key for **{player_name}**."
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
