import math
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
CARGO_CAPACITIES = {
    "Small Cargo": 5_000,
    "Large Cargo": 25_000,
    "Pathfinder": 10_000,
}


def get_hyperspace_level(ogame_id):
    with data_models.Session() as session:
        player_model = session.get(data_models.Player, ogame_id)
        return (
            (report := reports.get_best_api_key(player_model))
            and (hst := report.hyperspace_technology)
            and hst.level
        )


def get_cargo_requirements_text(amount, ogame_id):
    hst_level = get_hyperspace_level(ogame_id)
    ships = {
        k: math.ceil(amount / (v * (1 + 0.05 * (hst_level or 0))))
        for k, v in CARGO_CAPACITIES.items()
    }
    cargo_text = (
        f"_Using Hyperspace Tech {hst_level}_"
        if hst_level is not None
        else "_Hyperspace Technology unknown, assuming 0. Add your API key!_"
    )
    cargo_text += "\n\n" + "\n".join(f"• {k}: {v}" for k, v in ships.items())
    return cargo_text


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
        small * CARGO_CAPACITIES["Small Cargo"]
        + pathfinder * CARGO_CAPACITIES["Pathfinder"]
    )
    return math.ceil(
        max(0, remaining_capacity)
        / (cargo_multiplier * CARGO_CAPACITIES["Large Cargo"])
    )
