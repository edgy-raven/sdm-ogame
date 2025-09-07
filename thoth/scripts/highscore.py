import requests
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from thoth.data_models import HighScore, Session
from thoth.ogame_api import bulk_update_players

SCORE_TYPES = {
    0: ("total_pt", "total_rk"),
    3: ("mil_pt", "mil_rk"),
    5: ("mil_built_pt", None),
}


def snapshot():
    results = {}
    timestamp = None

    for score_type, (pt_field, rk_field) in SCORE_TYPES.items():
        resp = requests.get(
            "https://s256-us.ogame.gameforge.com/api/highscore.xml",
            params={"toJson": 1, "category": 1, "type": score_type},
        )
        resp.raise_for_status()
        data = resp.json()

        timestamp = datetime.fromtimestamp(
            int(data["@attributes"]["timestamp"]), timezone.utc
        )

        for player in data["player"]:
            attrs = player["@attributes"]
            ogame_id = int(attrs["id"])
            score = int(attrs["score"])
            pos = int(attrs["position"])

            if ogame_id not in results:
                results[ogame_id] = {}
            results[ogame_id][pt_field] = score
            if rk_field:
                results[ogame_id][rk_field] = pos

    with Session() as session:
        max_db_ts = session.query(func.max(HighScore.created_at)).scalar()
        if max_db_ts is not None:
            max_db_ts = max_db_ts.replace(tzinfo=timezone.utc)
            if timestamp <= max_db_ts or (timestamp - max_db_ts) < timedelta(
                minutes=5
            ):
                print("Already updated. Skipping.")
                return

    bulk_update_players()
    with Session() as session:
        for ogame_id, fields in results.items():
            hs = HighScore(ogame_id=ogame_id, created_at=timestamp, **fields)
            session.add(hs)
        session.commit()


if __name__ == "__main__":
    snapshot()
