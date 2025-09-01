import discord
from discord import app_commands
import json

from thoth import ogame_api, trade


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


@tree.command(
    name="ogame_lookup",
    description="Get OGame player info",
    guild=discord.Object(id=SDM_GUILD_ID),
)
@app_commands.describe(player_name="The name of the OGame player")
async def ogame_lookup(interaction: discord.Interaction, player_name: str):
    player_id = ogame_api.get_player_id(player_name)
    if not player_id:
        await interaction.response.send_message(
            f"❌ Player **{player_name}** not found.", ephemeral=True
        )
        return
    try:
        ogame_player_obj = ogame_api.get_player_info(player_id)
    except Exception as e:
        await interaction.response.send_message(
            f"⚠️ Error fetching player data: {e}", ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=ogame_player_obj.to_discord_embed()
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
