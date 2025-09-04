from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache, cached_property
import xml.etree.ElementTree as ET

import discord
import requests

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


def save_fleet_and_techs(
    report_api_key_str, defender_id, ships, techs, source, created_at
):
    with data_models.Session() as session:
        api_key_row = data_models.ReportAPIKey(
            report_api_key=report_api_key_str,
            created_at=created_at,
            ogame_id=defender_id,
            source=source,
        )
        api_key_row = session.merge(api_key_row)

        api_key_row.ships.extend(
            data_models.FleetShip(ship_type=stype, count=count)
            for stype, count in ships.items()
            if stype not in PEACEFUL_SHIPS
        )
        api_key_row.techs.extend(
            data_models.FleetTech(tech_type=ttype, level=level)
            for ttype, level in techs.items()
        )
        session.commit()


def parse_battlesim_api(battlesim_api_key, player_id):
    fleet_and_techs = {"techs": {}, "ships": {}}

    for part in battlesim_api_key.split("|"):
        key_str, value_str = part.split(";")
        if not key_str.isdigit():
            continue
        key_id = int(key_str)
        category = "techs" if key_id < 200 else "ships"
        fleet_and_techs[category][key_id] = int(value_str)

    return save_fleet_and_techs(
        report_api_key_str=battlesim_api_key,
        defender_id=player_id,
        ships=fleet_and_techs["ships"],
        techs=fleet_and_techs["techs"],
        source="BattleSim",
        created_at=datetime.utcnow(),
    )


def parse_ogame_sr(api_key_str):
    response = requests.get(f"https://ogapi.faw-kes.de/v1/report/{api_key_str}")
    response.raise_for_status()
    data = response.json()["RESULT_DATA"]

    generic = data["generic"]
    defender_id = generic["defender_user_id"]
    created_at = datetime.utcfromtimestamp(generic["event_timestamp"])

    content = data["details"]
    ships = {ship["ship_type"]: ship["count"] for ship in content["ships"]}
    techs = {
        tech["research_type"]: tech["level"] for tech in content["research"]
    }
    return save_fleet_and_techs(
        report_api_key_str=api_key_str,
        defender_id=defender_id,
        ships=ships,
        techs=techs,
        source="Ogame",
        created_at=created_at,
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


def add_report_api_key(ogame_id, report_api_key):
    with data_models.Session() as session:
        key_model = data_models.ReportAPIKey(
            report_api_key=report_api_key,
            created_at=datetime.utcnow(),
            ogame_id=ogame_id,
        )
        session.add(key_model)
        session.commit()


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
    db_data: data_models.Player
    rank: int
    alliance: str
    best_report_api_key_model: data_models.ReportAPIKey | None = None

    @cached_property
    def fleet_and_techs(self):
        if not self.best_report_api_key_model:
            return None
        return self.best_report_api_key_model.fleet_and_tech_dict()

    def _add_tech_fields(self, embed, id_to_name):
        if not self.fleet_and_techs:
            return
        techs = {
            id_to_name[k]: v
            for k, v in sorted(self.fleet_and_techs["techs"].items())
        }
        drives = (
            f"Combustion: {techs.get('Combustion Drive', 0)}\n"
            f"Impulse: {techs.get('Impulse Drive', 0)}\n"
            f"Hyperspace: {techs.get('Hyperspace Drive', 0)}"
        )
        weapons = (
            f"Weapons: {techs.get('Weapons Technology', 0)}\n"
            f"Shields: {techs.get('Shielding Technology', 0)}\n"
            f"Armor: {techs.get('Armor Technology', 0)}"
        )
        embed.add_field(name="Drives", value=drives, inline=True)
        embed.add_field(name="Weapons", value=weapons, inline=True)

    def _add_planet_fields(self, embed):
        planets = sorted(
            self.db_data.planets,
            key=lambda pl: (pl.galaxy, pl.system, pl.position),
        )
        active = [
            f"{(pl.name or 'Colony')} `{pl.coords_str()}`"
            f"{' 🌙' if pl.has_moon else ''}"
            for pl in planets
            if not pl.destroyed
        ]
        destroyed = [f"`{pl.coords_str()}`" for pl in planets if pl.destroyed]
        embed.add_field(name="Planets", value="\n".join(active), inline=False)
        if destroyed:
            embed.add_field(
                name="Destroyed Planets",
                value="\n".join(destroyed),
                inline=False,
            )

    def _add_ship_fields(self, embed, id_to_name):
        if not self.fleet_and_techs:
            return

        ships = {
            id_to_name[k]: v for k, v in self.fleet_and_techs["ships"].items()
        }
        ship_list = [f"{n}: {c}" for n, c in sorted(ships.items()) if c > 0]
        if ship_list:
            if len(ship_list) > 5:
                half = (len(ship_list) + 1) // 2
                embed.add_field(
                    name="Ships",
                    value="\n".join(ship_list[:half]),
                    inline=True,
                )
                embed.add_field(
                    name="\u200b",
                    value="\n".join(ship_list[half:]),
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Ships", value="\n".join(ship_list), inline=False
                )

    def to_discord_embed(self):
        id_to_name = get_ogame_localization_xml()
        embed = discord.Embed(
            title=f"OGame Player: {self.db_data.name}",
            color=discord.Color.blue(),
        )

        embed.add_field(name="Overall Rank", value=self.rank, inline=False)
        embed.add_field(
            name="Alliance", value=self.alliance or "None", inline=False
        )

        if self.best_report_api_key_model:
            embed.add_field(
                name="Latest Report API Key",
                value=f"{self.best_report_api_key_model.report_api_key}\n\n"
                "**Retrieved on**: "
                f"{self.best_report_api_key_model.timestamp_display_text()}",
                inline=False,
            )
        self._add_tech_fields(embed, id_to_name)
        self._add_planet_fields(embed)
        self._add_ship_fields(embed, id_to_name)

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
        player_model = session.get(data_models.Player, ogame_id)
        _ = player_model.planets

        best_report_api_key_model = get_best_api_key(player_model)
        if best_report_api_key_model:
            _ = best_report_api_key_model.ships
            _ = best_report_api_key_model.techs
            session.expunge(best_report_api_key_model)
        session.expunge(player_model)

    return OgamePlayer(
        db_data=player_model,
        rank=player_data.get("positions", {}).get("position", [])[0],
        alliance=player_data.get("alliance", {}).get("name", "None"),
        best_report_api_key_model=best_report_api_key_model,
    )
