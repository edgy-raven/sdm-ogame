from sqlalchemy.exc import IntegrityError

from thoth import data_models, ogame_api


class MembershipError(Exception):
    def __init__(self, reason, **kwargs):
        super().__init__(reason)
        self.reason, self.details = reason, kwargs


def discord_to_ogame_id(discord_id):
    with data_models.Session() as session:
        return session.get(data_models.DiscordUser, discord_id).ogame_id


def link(discord_id, ogame_name):
    ogame_id = ogame_api.get_player_id(ogame_name)
    if not ogame_id:
        raise MembershipError("player_not_found", player_name=ogame_name)

    with data_models.Session() as session:
        try:
            session.add(
                data_models.DiscordUser(
                    discord_id=discord_id, ogame_id=ogame_id
                )
            )
            session.commit()
        except IntegrityError as e:
            session.rollback()
            if "discord_id" in str(e.orig):
                raise MembershipError(
                    "discord_already_linked", discord_id=discord_id
                )
            if "ogame_id" in str(e.orig):
                raise MembershipError(
                    "player_already_linked", ogame_name=ogame_name
                )
            raise


def unlink(discord_id):
    with data_models.Session() as session:
        discord_model = session.get(data_models.DiscordUser, discord_id)
        if not discord_model:
            raise MembershipError("not_linked", discord_id=discord_id)
        session.delete(discord_model)
        session.commit()
