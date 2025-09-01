from enum import Enum
import re


class Currency(Enum):
    metal = 2.6
    crystal = 1.7
    deuterium = 1.0

    @classmethod
    def from_alias(cls, text: str):
        """Match user-friendly aliases like 'deut', 'cryst'."""
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
    def display(self) -> str:
        return self.name.capitalize()

    def convert(self, amount: float, target: "Currency") -> float:
        return amount * target.value / self.value


def parse_amount_and_currency(amount_str: str):
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
