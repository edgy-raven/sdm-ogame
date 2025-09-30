from enum import Enum
import re

import discord

from thoth import members, cargo


class Currency(Enum):
    metal = 2.6
    crystal = 1.7
    deuterium = 1.0

    @classmethod
    def from_alias(cls, text):
        text = text.lower()
        aliases = {
            "metal": cls.metal,
            "crystal": cls.crystal,
            "cryst": cls.crystal,
            "crys": cls.crystal,
            "cris": cls.crystal,
            "deuterium": cls.deuterium,
            "deut": cls.deuterium,
        }
        return aliases.get(text)

    @property
    def display(self):
        return self.name.capitalize()

    def convert(self, amount, target):
        return amount * target.value / self.value


def normalize_location(location):
    location = location.strip().lower().replace("💯", ":100:")
    moon = False
    moon_pattern = r"(?:\s*m|moon|\[m\]|\[moon\])$"
    if re.search(moon_pattern, location):
        moon = True
        location = re.sub(moon_pattern, "", location)
    location = location.replace(".", ":").rstrip()

    if not re.match(r"^\d{1,2}:\d{1,3}:\d{1,2}$", location):
        return None
    if moon:
        location += " [Moon]"
    return location


class DeliveryModal(discord.ui.Modal, title="Accept Trade"):
    location = discord.ui.TextInput(
        label="Delivery Coordinates", placeholder="e.g., 1:200:8", max_length=20
    )

    def __init__(self, trade_view, buyer_id):
        super().__init__()
        self.trade_view = trade_view
        self.buyer_id = buyer_id

    async def on_submit(self, interaction: discord.Interaction):
        location = normalize_location(str(self.location))
        if not location:
            await interaction.response.send_message(
                "❌ Invalid location. Use x:y:z or x.y.z, optionally [Moon].",
                ephemeral=True,
            )
            return
        if self.trade_view.buyer_id:
            return
        self.trade_view.buyer_id = self.buyer_id
        self.trade_view.buyer_location = location

        await interaction.response.edit_message(
            embed=self.trade_view._make_embed(), view=self.trade_view
        )


class TradeView(discord.ui.View):
    def __init__(
        self,
        seller_id,
        offer_amount,
        offer_currency,
        want_amount,
        want_currency,
        location,
    ):
        super().__init__(timeout=None)
        self.seller_id = seller_id
        self.buyer_id = None
        self.offer_amount = offer_amount
        self.offer_currency = offer_currency
        self.want_amount = want_amount
        self.want_currency = want_currency
        self.seller_location = location
        self.buyer_location = None
        self.seller_sent = False
        self.buyer_sent = False
        self.cancelled = False

    def _disable_all_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    def _short_amount(self, value, unit):
        if value >= 1_000_000:
            return f"{value/1_000_000:.3g}M {unit}"
        elif value >= 1_000:
            return f"{value/1_000:.3g}K {unit}"
        return f"{value} {unit}"

    @discord.ui.button(label="Accept Trade", style=discord.ButtonStyle.success)
    async def accept(self, interaction, _):
        if self.buyer_id or interaction.user.id == self.seller_id:
            return
        await interaction.response.send_modal(
            DeliveryModal(self, interaction.user.id)
        )

    @discord.ui.button(label="Mark as Sent", style=discord.ButtonStyle.primary)
    async def mark_sent(self, interaction, _):
        if not self.buyer_id:
            return

        ping_target = None
        if interaction.user.id == self.seller_id:
            self.seller_sent = True
            ping_target = self.buyer_id
        elif interaction.user.id == self.buyer_id:
            self.buyer_sent = True
            ping_target = self.seller_id
        else:
            return

        if self.seller_sent and self.buyer_sent:
            self._disable_all_buttons()
            self.stop()
        await interaction.response.edit_message(
            embed=self._make_embed(), view=self
        )
        if ping_target:
            await interaction.followup.send(
                f"🔔 <@{ping_target}>, the other party has marked "
                "their shipment as sent!",
                ephemeral=False,
            )

    @discord.ui.button(label="Cancel Trade", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, _):
        if interaction.user.id != self.seller_id or self.buyer_id:
            return
        self.cancelled = True
        self._disable_all_buttons()
        await interaction.response.edit_message(
            embed=self._make_embed(), view=self
        )
        self.stop()

    def _make_embed(self):
        if self.cancelled:
            return discord.Embed(
                title="❌ Trade Cancelled",
                color=discord.Color.red(),
                description="This trade has been cancelled by the seller.",
            )

        offer_str = self._short_amount(
            self.offer_amount, self.offer_currency.display
        )
        want_str = self._short_amount(
            self.want_amount, self.want_currency.display
        )

        if self.seller_sent and self.buyer_sent:
            return discord.Embed(
                title="🎉 Trade Completed",
                color=discord.Color.green(),
                description=(
                    f"<@{self.seller_id}> sent **{offer_str}** "
                    f"to <@{self.buyer_id}> at `{self.buyer_location}`.\n"
                    f"<@{self.buyer_id}> sent **{want_str}** "
                    f"to <@{self.seller_id}> at `{self.seller_location}`."
                ),
            )

        embed = discord.Embed(
            title=f"Trade in Progress: {offer_str}",
            color=(
                discord.Color.green()
                if not self.buyer_id
                else discord.Color.yellow()
            ),
        )
        parties = [
            {
                "role": "Seller",
                "user_id": self.seller_id,
                "amount_str": offer_str,
                "amount": self.offer_amount,
                "sent": self.seller_sent,
                "destination": self.buyer_location or "Not yet accepted",
            },
            {
                "role": "Buyer",
                "user_id": self.buyer_id,
                "amount_str": want_str,
                "amount": self.want_amount,
                "sent": self.buyer_sent,
                "destination": self.seller_location,
            },
        ]

        for party in parties:
            field_value = (
                f"📦 Send **{party['amount_str']}** to `{party['destination']}`"
            )
            if party["user_id"]:
                cargo_text = cargo.get_cargo_requirements_text(
                    party["amount"],
                    members.discord_to_ogame_id(party["user_id"]),
                )
                field_value = (
                    f"<@{party['user_id']}>\n"
                    f"{field_value}\n"
                    f"**Cargo Requirements**\n{cargo_text}"
                )
            if party["sent"]:
                field_value += "\n✅ **Marked as Sent**"
            embed.add_field(name=party["role"], value=field_value, inline=True)
        return embed


class TradeModal(discord.ui.Modal, title="Start a Trade"):
    amount = discord.ui.TextInput(
        label="Amount",
        placeholder="Enter numeric amount (supports K/KK/M/MIL suffixes)",
        max_length=20,
    )
    offer_currency = discord.ui.Label(
        text="Currency Offered (You will send this)",
        component=discord.ui.Select(
            options=[
                discord.SelectOption(label="Metal", value="metal"),
                discord.SelectOption(label="Crystal", value="crystal"),
                discord.SelectOption(label="Deuterium", value="deuterium"),
            ]
        ),
    )
    want_currency = discord.ui.Label(
        text="Currency Wanted (You will receive this)",
        component=discord.ui.Select(
            options=[
                discord.SelectOption(label="Metal", value="metal"),
                discord.SelectOption(label="Crystal", value="crystal"),
                discord.SelectOption(label="Deuterium", value="deuterium"),
            ]
        ),
    )
    delivery_location = discord.ui.TextInput(
        label="Delivery Location",
        placeholder="e.g., 1:200:8 or 2.100.8 Moon",
        max_length=20,
    )

    def __init__(self, buy: bool):
        self.buy = buy

        for component in (
            self.offer_currency.component,
            self.want_currency.component,
        ):
            for option in component.options:
                option.default = False

        if self.buy:
            self.offer_currency.component.options[1].default = True
            self.want_currency.component.options[2].default = True
        else:
            self.offer_currency.component.options[2].default = True
            self.want_currency.component.options[1].default = True
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        pattern = r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(k|kk|m|mil|million)?\s*$"
        m = re.fullmatch(pattern, self.amount.value.lower())
        if not m:
            await interaction.response.send_message(
                "❌ Invalid amount. Use numbers with optional K/KK/M suffix "
                " (e.g., 2kk, 1500).",
                ephemeral=True,
            )
            return
        offer_amount = float(m.group(1))
        suffix = m.group(2)
        if suffix == "k":
            offer_amount *= 1_000
        elif suffix in ("kk", "m", "mil", "million"):
            offer_amount *= 1_000_000
        offer_amount = int(round(offer_amount))

        from_currency = Currency[self.offer_currency.component.values[0]]
        to_currency = Currency[self.want_currency.component.values[0]]
        if self.buy:
            want_amount = offer_amount
            offer_amount = int(
                round(to_currency.convert(want_amount, from_currency))
            )
        else:
            offer_amount = offer_amount
            want_amount = int(
                round(from_currency.convert(offer_amount, to_currency))
            )

        location = normalize_location(str(self.delivery_location))
        if not location:
            await interaction.response.send_message(
                "❌ Invalid delivery location. Use x:y:z or x.y.z, "
                "optionally [Moon].",
                ephemeral=True,
            )
            return

        view = TradeView(
            seller_id=interaction.user.id,
            offer_amount=offer_amount,
            offer_currency=from_currency,
            want_amount=want_amount,
            want_currency=to_currency,
            location=location,
        )
        await interaction.response.send_message(
            embed=view._make_embed(), view=view
        )
