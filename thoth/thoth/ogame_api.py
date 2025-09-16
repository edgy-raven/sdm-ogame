from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import math
import xml.etree.ElementTree as ET

from IPython import embed
import discord
import requests
from sqlalchemy.orm import selectinload

from thoth import data_models, reports, ptre


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


def sync_planets(player_id, ogame_planets, ptre_positions=None):
    entries = defaultdict(dict)
    for ogame_planet in ogame_planets:
        galaxy, system, position = [
            int(x) for x in ogame_planet["@attributes"]["coords"].split(":")
        ]
        entries[(galaxy, system, position)]["ogame"] = {
            "has_moon": "moon" in ogame_planet,
            "name": ogame_planet["@attributes"].get("name"),
        }

    for ptre_planet in ptre_positions or []:
        galaxy = int(ptre_planet["galaxy"])
        system = int(ptre_planet["system"])
        position = int(ptre_planet["position"])
        entries[(galaxy, system, position)]["ptre"] = {
            "has_moon": ptre_planet["moon"]["id"] != "-1",
            "name": None,
            "timestamp": datetime.fromtimestamp(
                int(ptre_planet["timestamp_ig"]) // 1000, tz=timezone.utc
            ),
        }

    seen_coords = set()
    # OGame API updates weekly, expire any manual edits from before then
    with data_models.Session() as session:
        for (galaxy, system, position), sources in entries.items():
            ogame_entry = sources.get("ogame")
            ptre_entry = sources.get("ptre")

            if ogame_entry and not ptre_entry:
                has_moon = ogame_entry["has_moon"]
                name = ogame_entry["name"]
                timestamp = None
            elif ptre_entry and not ogame_entry:
                has_moon = ptre_entry["has_moon"]
                name = None
                timestamp = ptre_entry["timestamp"]
            else:
                has_moon = ogame_entry["has_moon"] or ptre_entry["has_moon"]
                name = ogame_entry["name"]
                timestamp = ptre_entry.get("timestamp")

            seen_coords.add((galaxy, system, position))

            if planet_model := session.get(
                data_models.Planet, (player_id, galaxy, system, position)
            ):
                planet_model.has_moon |= has_moon
                if name:
                    planet_model.name = name
                if timestamp and (
                    planet_model.manual_edit is None
                    or timestamp > planet_model.manual_edit
                ):
                    planet_model.manual_edit = timestamp
                if not planet_model.has_recent_manual_edit():
                    planet_model.destroyed = False
            else:
                planet_model = data_models.Planet(
                    ogame_id=player_id,
                    galaxy=galaxy,
                    system=system,
                    position=position,
                    name=name,
                    has_moon=has_moon,
                    destroyed=False,
                    manual_edit=timestamp,
                )
                session.add(planet_model)

        # Cleanup: delete planets not seen unless manually edited recently
        all_planet_models = (
            session.query(data_models.Planet)
            .filter(data_models.Planet.ogame_id == player_id)
            .all()
        )
        for planet_model in all_planet_models:
            if not (
                planet_model.coordinates_tuple() in seen_coords
                or planet_model.has_recent_manual_edit()
            ):
                session.delete(planet_model)

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
            report.coordinates_tuple()
            if report and report.has_coordinates()
            else None
        )
        best_from_moon = (
            report.from_moon if report and report.has_coordinates() else False
        )

        active = []
        for pl in planets:
            planet_name = pl.name or "Colony"
            coords = pl.coord_str()
            moon_emoji = " 🌙" if pl.has_moon else ""
            if best_coords == pl.coordinates_tuple():
                if best_from_moon:
                    moon_emoji += " 🔴"
                else:
                    planet_name += " 🔴"
            active.append(f"{planet_name} `{coords}`{moon_emoji}")

        header = f"Planets ({len(planets)}"
        if report and report.astrophysics:
            header += f" / {1 + math.ceil(report.astrophysics.level / 2)}"
        header += ")"
        embed.add_field(name=header, value="\n".join(active), inline=False)

        destroyed = [f"`{pl.coord_str()}`" for pl in planets if pl.destroyed]
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
        if self.player_model.discord_user:
            embed.add_field(
                name="Linked Discord User",
                value=f"<@{self.player_model.discord_user.discord_id}>",
                inline=False,
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
            report_with_delta = reports.ReportWithDelta(
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


def get_player_info(ogame_id):
    details_url = "https://s256-us.ogame.gameforge.com/api/playerData.xml"
    resp = requests.get(
        details_url, params={"id": ogame_id, "toJson": 1}, timeout=10
    )
    resp.raise_for_status()
    player_data = resp.json()

    sync_planets(
        ogame_id,
        player_data["planets"]["planet"],
        ptre.get_planet_infos(ogame_id),
    )
    ptre.sync_ptre_sr(ogame_id)
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
                selectinload(data_models.Player.report_api_keys).selectinload(
                    data_models.ReportAPIKey.astrophysics
                ),
                selectinload(data_models.Player.highscores),
                selectinload(data_models.Player.discord_user),
            )
            .get(ogame_id)
        )
        best_report_api_key_model = reports.get_best_api_key(player_model)
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
