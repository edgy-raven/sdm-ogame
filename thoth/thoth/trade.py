from enum import Enum
import math
import re

from sqlalchemy import desc, select

from thoth import data_models, reports


TIERS = [
    (100_000, 500_000),
    (1_000_000, 1_200_000),
    (5_000_000, 1_800_000),
    (25_000_000, 2_400_000),
    (50_000_000, 3_000_000),
    (75_000_000, 3_600_000),
    (100_000_000, 4_200_000),
    (float("inf"), 5_000_000),
]
CARGO_CAPACITIES = {"small": 5_000, "large": 25_000, "pathfinder": 10_000}


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


def parse_amount_and_currency(amount_str):
    pattern = (
        r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(k|kk|m|mil|million)?\s*([a-z]+)?\s*$"
    )
    m = re.fullmatch(pattern, amount_str.lower())
    if not m:
        raise ValueError("invalid format")

    amount = float(m.group(1))
    suffix = m.group(2)
    currency_text = m.group(3)

    if suffix == "k":
        amount *= 1_000
    elif suffix in ("kk", "m", "mil", "million"):
        amount *= 1_000_000

    return amount, Currency.from_alias(currency_text)


def get_hyperspace_level(ogame_id):
    with data_models.Session() as session:
        player_model = session.get(data_models.Player, ogame_id)
        return (
            (report := reports.get_best_api_key(player_model))
            and (hst := report.hyperspace_technology)
            and hst.level
        )


def add_cargo_requirements_to_discord_embed(amount, ogame_id, embed):
    hst_level = get_hyperspace_level(ogame_id)
    ships = {
        k: math.ceil(amount / (v * (1 + 0.05 * (hst_level or 0))))
        for k, v in CARGO_CAPACITIES.items()
    }
    cargo_text = (
        f"\n_Using Hyperspace Tech {hst_level}_"
        if hst_level is not None
        else "\n_Hyperspace Technology unknown, assuming 0. Add your API key!_"
    )
    cargo_text += "\n\n" + "\n".join(f"• {k}: {v}" for k, v in ships.items())
    embed.add_field(name="Cargo Requirements", value=cargo_text, inline=False)


def expedition_cargos(ogame_id, res_find, ship_find, small=0, pathfinder=1):
    with data_models.Session() as session:
        top = session.execute(
            select(data_models.HighScore)
            .order_by(desc(data_models.HighScore.total_pt))
            .limit(1)
        ).scalar_one_or_none()

    max_res = (
        24
        * (1 + max(res_find, ship_find) / 100.0)
        * next(res for threshold, res in TIERS if top.total_pt < threshold)
    )
    hst_level = get_hyperspace_level(ogame_id)

    if not hst_level:
        raise ValueError("Hyperspace Technology level unknown")
    cargo_multiplier = 1 + 0.05 * hst_level
    remaining_capacity = max_res - cargo_multiplier * (
        small * CARGO_CAPACITIES["small"]
        + pathfinder * CARGO_CAPACITIES["pathfinder"]
    )
    return math.ceil(
        max(0, remaining_capacity)
        / (cargo_multiplier * CARGO_CAPACITIES["large"])
    )
