from sqlalchemy.exc import IntegrityError

from thoth import data_models, ogame_api


class MembershipError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def discord_to_ogame_id(discord_id):
    with data_models.Session() as session:
        discord_user_model = session.get(data_models.DiscordUser, discord_id)
        if discord_user_model and discord_user_model.player:
            return discord_user_model.player.ogame_id
        return None


def link(discord_id, ogame_name):
    ogame_id = ogame_api.get_player_id(ogame_name)
    if not ogame_id:
        raise MembershipError("player_not_found")

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
                raise MembershipError("discord_already_linked")
            if "ogame_id" in str(e.orig):
                raise MembershipError("player_already_linked")
            raise


def unlink(discord_id):
    with data_models.Session() as session:
        discord_model = session.get(data_models.DiscordUser, discord_id)
        if not discord_model:
            raise MembershipError("not_linked")
        session.delete(discord_model)
        session.commit()
