from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from thoth import data_models
from thoth.ogame_api import parse_ogame_sr, parse_battlesim_api
import thoth.ogame_api

# --- Setup engine and session ---
engine = create_engine("sqlite:///ogame_players.db")
data_models.Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# --- Step 1: Rebuild report_api_keys table ---
with engine.begin() as conn:
    for column_def in [
        "galaxy INTEGER",
        "system INTEGER",
        "position INTEGER",
        "from_moon BOOLEAN DEFAULT 0",
    ]:
        try:
            conn.execute(
                text(f"ALTER TABLE report_api_keys ADD COLUMN {column_def}")
            )
        except Exception:
            pass  # column probably already exists


# --- Step 2: Monkey-patch save_report_with_coords ---
def _migration_save_report(*, coord_str, **kwargs):
    with Session() as session:
        report_api_key_str = kwargs.get("report_api_key_str")
        player_id = kwargs.get("player_id")
        source = kwargs.get("source")
        created_at = kwargs.get("created_at")

        report = session.query(data_models.ReportAPIKey).get(report_api_key_str)
        if not report:
            report = data_models.ReportAPIKey(
                report_api_key=report_api_key_str,
                ogame_id=player_id,
                source=source,
                created_at=created_at,
            )
            session.add(report)

        # Pass the extracted coordinate string to set_coords
        report.set_coords(coord_str)

        session.commit()


thoth.ogame_api.save_report_with_coords = _migration_save_report

# --- Step 3: Run parsers to populate coordinates and from_moon ---
with Session() as session:
    for report in session.query(data_models.ReportAPIKey).all():
        api_key = report.report_api_key
        if report.source == "Ogame":
            parse_ogame_sr(api_key)
        elif report.source == "BattleSim":
            parse_battlesim_api(api_key, report.ogame_id)
