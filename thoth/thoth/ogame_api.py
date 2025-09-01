from dataclasses import dataclass

import discord
import requests

from thoth import data_models


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


def parse_coords(coords: str):
    g, s, p = coords.split(":")
    return int(g), int(s), int(p)


@dataclass
class OgamePlayer:
    db_data: data_models.Player
    rank: int
    alliance: str

    def to_discord_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"OGame Player: {self.db_data.name}",
            color=discord.Color.blue(),
        )

        embed.add_field(name="Overall Rank", value=self.rank, inline=True)
        embed.add_field(
            name="Alliance", value=self.alliance or "None", inline=True
        )

        planets_sorted = sorted(
            self.db_data.planets,
            key=lambda pl: (pl.galaxy, pl.system, pl.position),
        )
        active_planets = [
            f"{(pl.name or 'Colony')} `{pl.coords_str()}`"
            f"{' 🌙' if pl.has_moon else ''}"
            for pl in planets_sorted
            if not pl.destroyed
        ]
        destroyed_planets = [
            f"`{pl.coords_str()}`" for pl in planets_sorted if pl.destroyed
        ]
        embed.add_field(
            name="Planets",
            value="\n".join(active_planets),
            inline=False,
        )
        if destroyed_planets:
            embed.add_field(
                name="Destroyed Planets",
                value="\n".join(destroyed_planets),
                inline=False,
            )
        return embed


def sync_planets(player_id, planets):
    seen_coords = set()

    # Ogame API updates weekly, expire any manual edits from before then.
    with data_models.Session() as session:
        for planet in planets:
            attrs = planet.get("@attributes", {})
            galaxy, system, position = parse_coords(attrs.get("coords"))
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


def get_player_info(player_id):
    details_url = "https://s256-us.ogame.gameforge.com/api/playerData.xml"

    resp = requests.get(
        details_url, params={"id": player_id, "toJson": 1}, timeout=10
    )
    resp.raise_for_status()
    player_data = resp.json()

    sync_planets(player_id, player_data["planets"]["planet"])
    with data_models.Session() as session:
        player_model = session.get(data_models.Player, player_id)
        _ = player_model.planets
        session.expunge(player_model)

    return OgamePlayer(
        db_data=player_model,
        rank=player_data.get("positions", {}).get("position", [])[0],
        alliance=player_data.get("alliance", {}).get("name", "None"),
    )
