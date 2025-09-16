import requests

from thoth import reports

TEAM_KEY = None  # Set by initialize_connection()
TOOL = "thoth"
COUNTRY = "us"
UNIVERSE = "256"


def get_planet_infos(player_id):
    galaxy_infos_url = "https://ptre.chez.gg/scripts/api_galaxy_get_infos.php"
    resp = requests.get(
        galaxy_infos_url,
        params={
            "tool": TOOL,
            "country": COUNTRY,
            "universe": UNIVERSE,
            "team_key": TEAM_KEY,
            "player_id": player_id,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["galaxy_array"]


def sync_ptre_sr(player_id):
    resp = requests.get(
        "https://ptre.chez.gg/scripts/oglight_get_player_infos.php",
        params={
            "tool": TOOL,
            "country": COUNTRY,
            "univers": UNIVERSE,
            "team_key": TEAM_KEY,
            "player_id": player_id,
            "noacti": "yes",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data["top_sr_link"]:
        return

    sr_resp = requests.get(
        "https://ptre.chez.gg/scripts/api_get_report.php",
        params={
            "iid": data["top_sr_link"].split("?iid=")[1],
            "tool": TOOL,
            "team_key": TEAM_KEY,
        },
        timeout=10,
    )
    sr_resp.raise_for_status()
    ogame_sr_id = sr_resp.json()["report"]["RESULT_DATA"]["generic"]["sr_id"]
    try:
        reports.parse_ogame_sr(f"sr-us-256-{ogame_sr_id}")
    except (reports.ReportAPIException, requests.exceptions.HTTPError):
        return


def initialize_connection(team_key):
    global TEAM_KEY  # pylint: disable=global-statement
    TEAM_KEY = team_key
