from datetime import datetime, timedelta, timezone

import discord
import requests

from thoth import data_models


class ReportAPIException(Exception):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        self.message = message
        super().__init__(message)


def get_best_api_key(player_model):
    recent_battle_sims = [
        k
        for k in player_model.report_api_keys
        if (
            k.source == "BattleSim"
            and k.created_at.replace(tzinfo=timezone.utc)
            >= datetime.now(timezone.utc) - timedelta(days=7)
        )
    ]
    if recent_battle_sims:
        return max(recent_battle_sims, key=lambda k: k.created_at)

    ogame_keys = [
        k for k in player_model.report_api_keys if k.source == "Ogame"
    ]
    if not ogame_keys:
        return None

    return max(
        ogame_keys,
        key=lambda k: (k.military_ships_total, k.created_at),
    )


def save_report_with_coords(
    report_api_key_str,
    player_id,
    ships,
    techs,
    source,
    created_at,
    coord_str=None,
    from_moon=False,
    resources=None,
    force=False,
):
    military_ships = {
        stype: count
        for stype, count in ships.items()
        if stype not in data_models.Ships.PEACEFUL_SHIPS
    }

    with data_models.Session() as session:
        if session.get(data_models.ReportAPIKey, report_api_key_str):
            raise ReportAPIException(
                "duplicate_key",
                f"Report API key '{report_api_key_str}' already exists.",
            )
        player = session.get(data_models.Player, player_id)
        if not force and player and (best_key := get_best_api_key(player)):
            if sum(military_ships.values()) < best_key.military_ships_total:
                raise ReportAPIException(
                    "fewer_ships",
                    "Report has fewer ships than best existing report.",
                )

        report = data_models.ReportAPIKey(
            report_api_key=report_api_key_str,
            created_at=created_at,
            ogame_id=player_id,
            source=source,
            from_moon=from_moon,
        )
        if coord_str:
            report.set_coords(coord_str)
        report.ships.extend(
            data_models.Ships(ship_type=stype, count=count)
            for stype, count in military_ships.items()
            if count != 0
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
        if coord_str:
            if not (planet := report.planet):
                planet = data_models.Planet(
                    ogame_id=player_id,
                    has_moon=from_moon,
                    manual_edit=created_at,
                )
                planet.set_coords(coord_str)
                session.add(planet)
            elif from_moon and not planet.has_moon:
                planet.has_moon = True

        session.commit()


def parse_ogame_sr(sr_api_key, force=False):
    response = requests.get(f"https://ogapi.faw-kes.de/v1/report/{sr_api_key}")
    response.raise_for_status()
    data = response.json()["RESULT_DATA"]

    generic = data["generic"]
    defender_id = generic["defender_user_id"]
    created_at = datetime.fromtimestamp(
        generic["event_timestamp"], timezone.utc
    )

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
        force=force,
    )


def parse_battlesim_api(battlesim_api_key, player_id, force=False):
    fleet_and_techs = {"techs": {}, "ships": {}}
    coord_str = None

    for part in battlesim_api_key.split("|"):
        if not part:
            continue
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
        created_at=datetime.now(timezone.utc),
        coord_str=coord_str,
        from_moon=False,
        force=force,
    )


def add_key(key, player_id=None, force=False):
    key = key.replace("💯", ":100:")
    if key.startswith("sr-"):
        parse_ogame_sr(key, force=force)
        source = "Ogame"
    else:
        if player_id is None:
            raise ReportAPIException(
                "missing_player_id",
                "player_id is required for BattleSim keys",
            )
        parse_battlesim_api(key, player_id, force=force)
        source = "BattleSim"
    return key, source


def delete_key(key, delete_planet=False):
    key = key.replace("💯", ":100:")
    deleted_coords = None

    with data_models.Session() as session:
        report_model = session.get(data_models.ReportAPIKey, key)
        if not report_model:
            raise ReportAPIException(
                "not_found", f"Report API key '{key}' not found"
            )
        if delete_planet and report_model.has_coordinates():
            planet = session.get(
                data_models.Planet,
                (report_model.ogame_id, *report_model.coordinates_tuple()),
            )
            if planet and planet.has_recent_manual_edit():
                deleted_coords = planet.coord_str()
                session.delete(planet)

        session.delete(report_model)
        session.commit()
        return deleted_coords


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
                # If we are not computing deltas, the last report is not needed.
                self.last_report = None
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

    def to_discord_embed(self, id_to_name):
        player = get_player_name_from_api_key(self.report_api_key)
        title_location = (
            ""
            if not self.new_report.has_coordinates()
            else f" - `{self.new_report.coord_str()}"
            f"{' [Moon]' if self.new_report.from_moon else ''}`"
        )
        embed = discord.Embed(
            title=f"{player}{title_location}",
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
