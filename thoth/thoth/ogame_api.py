from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
import xml.etree.ElementTree as ET

import discord
import requests
from sqlalchemy.orm import selectinload

from thoth import data_models


@lru_cache()
def get_ogame_localization_xml():
    response = requests.get(
        "https://s256-us.ogame.gameforge.com/api/localization.xml"
    )
    response.raise_for_status()

    return {
        int(elem.attrib["id"]): elem.text
        for elem in ET.fromstring(response.content).findall(".//techs/name")
    }


PEACEFUL_SHIPS = {202, 203, 208, 210, 212, 217, 216, 220}


class DuplicateKeyException(Exception):
    pass


def save_report_with_coords(
    report_api_key_str,
    player_id,
    ships,
    techs,
    source,
    created_at,
    coord_str,
    from_moon=False,
    resources=None,
):
    with data_models.Session() as session:
        if session.get(data_models.ReportAPIKey, report_api_key_str):
            raise DuplicateKeyException(
                f"Report API key '{report_api_key_str}' already exists."
            )

        report = data_models.ReportAPIKey(
            report_api_key=report_api_key_str,
            created_at=created_at,
            ogame_id=player_id,
            source=source,
            from_moon=from_moon,
        )
        report.set_coords(coord_str)

        report.ships.extend(
            data_models.Ships(ship_type=stype, count=count)
            for stype, count in ships.items()
            if stype not in PEACEFUL_SHIPS
        )
        report.techs.extend(
            data_models.Techs(tech_type=ttype, level=level)
            for ttype, level in techs.items()
        )
        if resources:
            report.resources = data_models.Resources(
                metal=resources.get("metal", 0),
                crystal=resources.get("crystal", 0),
                deuterium=resources.get("deuterium", 0),
            )
        report = session.merge(report)
        session.flush()
        if not (planet := report.planet):
            planet = data_models.Planet(
                ogame_id=player_id, has_moon=from_moon, manual_edit=created_at
            )
            planet.set_coords(coord_str)
            session.add(planet)
        elif from_moon and not planet.has_moon:
            planet.has_moon = True

        session.commit()


def parse_ogame_sr(sr_api_key):
    response = requests.get(f"https://ogapi.faw-kes.de/v1/report/{sr_api_key}")
    response.raise_for_status()
    data = response.json()["RESULT_DATA"]

    generic = data["generic"]
    defender_id = generic["defender_user_id"]
    created_at = datetime.utcfromtimestamp(generic["event_timestamp"])

    content = data["details"]
    ships = {s["ship_type"]: s["count"] for s in content["ships"]}
    techs = {t["research_type"]: t["level"] for t in content["research"]}

    coord_str = generic.get("defender_planet_coordinates")
    is_moon = generic.get("defender_planet_type") == 3

    return save_report_with_coords(
        report_api_key_str=sr_api_key,
        player_id=defender_id,
        ships=ships,
        techs=techs,
        source="Ogame",
        created_at=created_at,
        coord_str=coord_str,
        from_moon=is_moon,
        resources=content["resources"],
    )


def parse_battlesim_api(battlesim_api_key, player_id):
    fleet_and_techs = {"techs": {}, "ships": {}}
    coord_str = None

    for part in battlesim_api_key.split("|"):
        key_str, value_str = part.split(";")
        if key_str == "coords":
            coord_str = value_str
        elif key_str.isdigit():
            key_id = int(key_str)
            category = "techs" if key_id < 200 else "ships"
            fleet_and_techs[category][key_id] = int(value_str)

    return save_report_with_coords(
        report_api_key_str=battlesim_api_key,
        player_id=player_id,
        ships=fleet_and_techs["ships"],
        techs=fleet_and_techs["techs"],
        source="BattleSim",
        created_at=datetime.utcnow(),
        coord_str=coord_str,
        from_moon=False,
    )


def bulk_update_players():
    players_url = "https://s256-us.ogame.gameforge.com/api/players.xml"

    resp = requests.get(players_url, params={"toJson": 1}, timeout=10)
    resp.raise_for_status()
    players_data = resp.json()

    with data_models.Session() as session:
        for player in players_data.get("player", []):
            attrs = player.get("@attributes", {})
            session.merge(
                data_models.Player(ogame_id=attrs["id"], name=attrs["name"])
            )
        session.commit()


def get_player_id(player_name, refresh=True):
    with data_models.Session() as session:
        player_model = (
            session.query(data_models.Player)
            .filter(data_models.Player.name.ilike(player_name))
            .first()
        )

    if not player_model:
        if refresh:
            bulk_update_players()
            return get_player_id(player_name, refresh=False)
        return None
    return player_model.ogame_id


def get_player_name_from_api_key(api_key):
    with data_models.Session() as session:
        api_key_model = session.get(data_models.ReportAPIKey, api_key)
        player = session.get(data_models.Player, api_key_model.ogame_id)
        return player.name


class ReportWithDelta:
    DRIVES = [
        ("Combustion Drive", "Combustion", 115),
        ("Impulse Drive", "Impulse", 117),
        ("Hyperspace Drive", "Hyperspace", 118),
    ]
    WEAPONS = [
        ("Weapons Technology", "Weapons", 109),
        ("Shielding Technology", "Shields", 110),
        ("Armor Technology", "Armor", 111),
    ]
    CORE_TECHS_IDS = {115, 117, 118, 109, 110, 111}

    @property
    def is_sr_report(self):
        return self.report_api_key.startswith("sr-")

    @property
    def has_delta(self):
        return any(d != 0 for c in self.deltas.values() for d in c.values())

    def __init__(self, report_api_key, compute_delta=True):
        self.report_api_key = report_api_key

        with data_models.Session() as session:
            self.new_report = session.get(
                data_models.ReportAPIKey, self.report_api_key
            )
            # Force load relationships
            _ = self.new_report.report_details
            self.deltas = {"resources": {}, "ships": {}, "techs": {}}
            self.last_report = None
            if not self.is_sr_report:
                return

            self.last_report = (
                session.query(data_models.ReportAPIKey)
                .filter(
                    data_models.ReportAPIKey.source == "Ogame",
                    data_models.ReportAPIKey.ogame_id
                    == self.new_report.ogame_id,
                    data_models.ReportAPIKey.from_moon
                    == self.new_report.from_moon,
                    data_models.ReportAPIKey.galaxy == self.new_report.galaxy,
                    data_models.ReportAPIKey.system == self.new_report.system,
                    data_models.ReportAPIKey.position
                    == self.new_report.position,
                    data_models.ReportAPIKey.created_at
                    < self.new_report.created_at,
                )
                .order_by(data_models.ReportAPIKey.created_at.desc())
                .first()
            )
            if not self.last_report or not compute_delta:
                return
            for cat in self.deltas:
                new_cat = self.new_report.report_details[cat]
                old_cat = self.last_report.report_details[cat]
                for k in set(new_cat) | set(old_cat):
                    new_val = new_cat.get(k, 0)
                    old_val = old_cat.get(k, 0)
                    self.deltas[cat][k] = new_val - old_val

    def _line(self, name, val, delta):
        def fmt(n):
            if abs(n) >= 1_000_000_000:
                return f"{n / 1_000_000_000:.4g}B"
            elif abs(n) >= 1_000_000:
                return f"{n / 1_000_000:.4g}M"
            else:
                return f"{n:,}"

        if not self.last_report:
            return f"{name}{':' if name else ''} {fmt(val)}"
        emoji = "🟢" if delta > 0 else "🔴" if delta < 0 else "⚪"
        return (
            f"{emoji} {name}{':' if name else ''} {fmt(val)} "
            f"({'+' if delta > 0 else ''}{fmt(delta)})"
        )

    def add_tech_fields_to_discord_embed(self, embed, id_to_name):
        techs = {
            id_to_name[k]: v
            for k, v in self.new_report.report_details["techs"].items()
        }
        embed.add_field(
            name="Drives",
            value="\n".join(
                [
                    self._line(
                        short,
                        techs.get(long, 0),
                        self.deltas["techs"].get(tech_id, 0),
                    )
                    for long, short, tech_id in self.DRIVES
                ]
            ),
            inline=True,
        )
        embed.add_field(
            name="Weapons",
            value="\n".join(
                [
                    self._line(
                        short,
                        techs.get(long, 0),
                        self.deltas["techs"].get(tech_id, 0),
                    )
                    for long, short, tech_id in self.WEAPONS
                ]
            ),
            inline=True,
        )
        other_techs_content = "\n".join(
            sorted(
                [
                    self._line(
                        id_to_name[tech_id],
                        techs.get(id_to_name[tech_id], 0),
                        delta,
                    )
                    for tech_id, delta in self.deltas["techs"].items()
                    if delta != 0 and tech_id not in self.CORE_TECHS_IDS
                ],
                key=lambda x: x.lower(),
            )
        )
        if other_techs_content:
            embed.add_field(
                name="Other Techs",
                value=other_techs_content,
                inline=False,
            )

    def add_ship_fields_to_discord_embed(self, embed, id_to_name):
        ship_lines = sorted(
            [
                (
                    id_to_name[k],
                    self._line(id_to_name[k], v, self.deltas["ships"].get(k)),
                )
                for k, v in self.new_report.report_details["ships"].items()
            ],
            key=lambda x: x[0],
        )
        ship_lines = [x[1] for x in ship_lines]
        if ship_lines:
            if len(ship_lines) > 5:
                half = (len(ship_lines) + 1) // 2
                embed.add_field(
                    name="Ships",
                    value="\n".join(ship_lines[:half]),
                    inline=True,
                )
                embed.add_field(
                    name="\u200b",
                    value="\n".join(ship_lines[half:]),
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Ships", value="\n".join(ship_lines), inline=False
                )

    def to_discord_embed(self):
        id_to_name = get_ogame_localization_xml()

        player = get_player_name_from_api_key(self.report_api_key)
        embed = discord.Embed(
            title=f"{player} - {self.new_report.coord_str()}"
            f"{' [Moon]' if self.new_report.from_moon else ''}",
            color=discord.Color.blue(),
        )

        if self.is_sr_report:
            for r in ["metal", "crystal", "deuterium"]:
                embed.add_field(
                    name=r.capitalize(),
                    value=self._line(
                        "",
                        self.new_report.report_details["resources"][r],
                        self.deltas["resources"].get(r),
                    ),
                    inline=True,
                )

        self.add_tech_fields_to_discord_embed(embed, id_to_name)
        embed.add_field(name="", value="", inline=False)
        self.add_ship_fields_to_discord_embed(embed, id_to_name)

        if self.last_report:
            embed.add_field(
                name="Previous Report Retrieved On",
                value=self.last_report.timestamp_display_text(),
                inline=False,
            )
        return embed


def sync_planets(player_id, planets):
    seen_coords = set()

    # Ogame API updates weekly, expire any manual edits from before then.
    with data_models.Session() as session:
        for planet in planets:
            attrs = planet.get("@attributes", {})
            galaxy, system, position = [
                int(x) for x in attrs.get("coords").split(":")
            ]
            has_moon = "moon" in planet
            planet_name = attrs.get("name")

            seen_coords.add((galaxy, system, position))
            planet_model = session.get(
                data_models.Planet, (player_id, galaxy, system, position)
            )
            if planet_model:
                planet_model.has_moon = planet_model.has_moon or has_moon
                planet_model.name = planet_name
                if not planet_model.has_recent_manual_edit():
                    planet_model.destroyed = False
            else:
                session.add(
                    data_models.Planet(
                        ogame_id=player_id,
                        galaxy=galaxy,
                        system=system,
                        position=position,
                        name=planet_name,
                        has_moon=has_moon,
                        destroyed=False,
                    )
                )
        all_planet_models = (
            session.query(data_models.Planet)
            .filter(data_models.Planet.ogame_id == player_id)
            .all()
        )
        for planet_model in all_planet_models:
            if not (
                (
                    planet_model.galaxy,
                    planet_model.system,
                    planet_model.position,
                )
                in seen_coords
                or planet.has_recent_manual_edit()
            ):
                session.delete(planet)
        session.commit()


@dataclass
class OgamePlayer:
    player_model: data_models.Player
    alliance: str | None
    best_report_api_key_model: data_models.ReportAPIKey | None = None

    def _add_planet_fields(self, embed):
        planets = sorted(
            self.player_model.planets,
            key=lambda pl: (pl.galaxy, pl.system, pl.position),
        )

        report = self.best_report_api_key_model
        best_coords = (
            (report.galaxy, report.system, report.position) if report else None
        )
        best_from_moon = report.from_moon if report else False

        active = []
        for pl in planets:
            planet_name = pl.name or "Colony"
            coords = pl.coord_str()
            moon_emoji = " 🌙" if pl.has_moon else ""
            if best_coords == (pl.galaxy, pl.system, pl.position):
                if best_from_moon:
                    moon_emoji += " 🔴"
                else:
                    planet_name += " 🔴"
            active.append(f"{planet_name} `{coords}`{moon_emoji}")

        destroyed = [f"`{pl.coord_str()}`" for pl in planets if pl.destroyed]

        embed.add_field(name="Planets", value="\n".join(active), inline=False)
        if destroyed:
            embed.add_field(
                name="Destroyed Planets",
                value="\n".join(destroyed),
                inline=False,
            )

    def to_discord_embed(self):
        id_to_name = get_ogame_localization_xml()

        embed = discord.Embed(
            title=f"{self.player_model.name}"
            f"{f' - {self.alliance}' if self.alliance else ''}",
            color=discord.Color.blue(),
        )

        last_hs = self.player_model.last_highscore
        second_hs = self.player_model.second_last_highscore

        embed.add_field(
            name="Overall Rank",
            value=f"**{last_hs.total_rk}** {last_hs.total_pt:,}"
            f" ({last_hs.total_pt - second_hs.total_pt:+,})",
            inline=True,
        )
        embed.add_field(
            name="Military Rank",
            value=f"**{last_hs.mil_rk}** {last_hs.mil_pt:,}"
            f" ({last_hs.mil_pt - second_hs.mil_pt:+,})",
            inline=True,
        )

        report_with_delta = None
        if self.best_report_api_key_model:
            embed.add_field(
                name="Latest Report API Key",
                value=f"```{self.best_report_api_key_model.report_api_key}```"
                "**Retrieved on**: "
                f"{self.best_report_api_key_model.timestamp_display_text()}",
                inline=False,
            )
            report_with_delta = ReportWithDelta(
                self.best_report_api_key_model.report_api_key, False
            )
            report_with_delta.add_tech_fields_to_discord_embed(
                embed, id_to_name
            )

        self._add_planet_fields(embed)

        if report_with_delta:
            report_with_delta.add_ship_fields_to_discord_embed(
                embed, id_to_name
            )

        return embed


def get_best_api_key(player_model):
    if battle_sim_keys := [
        k
        for k in player_model.report_api_keys
        if k.source == "BattleSim"
        and k.created_at >= datetime.utcnow() - timedelta(days=7)
    ]:
        return max(battle_sim_keys, key=lambda k: k.created_at)
    if not (
        ogame_keys := [
            k for k in player_model.report_api_keys if k.source == "Ogame"
        ]
    ):
        return None

    totals = [(k, sum(s.count for s in k.ships)) for k in ogame_keys]
    threshold = max(total for _, total in totals) * 0.8
    eligible = [k for k, total in totals if total >= threshold]

    return max(eligible or ogame_keys, key=lambda k: k.created_at)


def get_player_info(ogame_id):
    details_url = "https://s256-us.ogame.gameforge.com/api/playerData.xml"
    resp = requests.get(
        details_url, params={"id": ogame_id, "toJson": 1}, timeout=10
    )
    resp.raise_for_status()
    player_data = resp.json()

    sync_planets(ogame_id, player_data["planets"]["planet"])
    with data_models.Session() as session:
        player_model = (
            session.query(data_models.Player)
            .options(
                selectinload(data_models.Player.planets),
                selectinload(data_models.Player.report_api_keys).selectinload(
                    data_models.ReportAPIKey.ships
                ),
                selectinload(data_models.Player.report_api_keys).selectinload(
                    data_models.ReportAPIKey.techs
                ),
                selectinload(data_models.Player.report_api_keys).selectinload(
                    data_models.ReportAPIKey.resources
                ),
                selectinload(data_models.Player.highscores),
            )
            .get(ogame_id)
        )
        best_report_api_key_model = get_best_api_key(player_model)
        if best_report_api_key_model:
            session.expunge(best_report_api_key_model)
        session.expunge(player_model)

    alliance = player_data.get("alliance")
    return OgamePlayer(
        player_model=player_model,
        alliance=(
            f"{alliance['name']} [{alliance['tag']}]" if alliance else None
        ),
        best_report_api_key_model=best_report_api_key_model,
    )
