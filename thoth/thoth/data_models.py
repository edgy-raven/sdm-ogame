from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    create_engine,
    BigInteger,
    Boolean,
    Column,
    Integer,
    String,
    ForeignKey,
    DateTime,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker


Base = declarative_base()


class Player(Base):
    __tablename__ = "players"

    ogame_id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    planets = relationship("Planet", back_populates="player")
    report_api_keys = relationship("ReportAPIKey")


class CoordinatesMixin:
    galaxy = Column(Integer)
    system = Column(Integer)
    position = Column(Integer)

    def set_coords(self, coord_str):
        self.galaxy, self.system, self.position = (
            int(x) for x in coord_str.split(":")
        )

    def coord_str(self):
        return f"{self.galaxy}:{self.system}:{self.position}"


class Planet(Base, CoordinatesMixin):
    __tablename__ = "planets"

    ogame_id = Column(Integer, ForeignKey("players.ogame_id"), primary_key=True)
    galaxy = Column(Integer, primary_key=True)
    system = Column(Integer, primary_key=True)
    position = Column(Integer, primary_key=True)

    name = Column(String, nullable=True)
    has_moon = Column(Boolean, default=False)
    destroyed = Column(Boolean, default=False)
    manual_edit = Column(DateTime, nullable=True)

    player = relationship("Player", back_populates="planets")

    def has_recent_manual_edit(self) -> bool:
        return self.manual_edit is not None and self.manual_edit >= (
            datetime.utcnow() - timedelta(days=7)
        )


class DiscordUser(Base):
    __tablename__ = "discord_users"

    discord_id = Column(BigInteger, primary_key=True)
    ogame_id = Column(
        Integer,
        ForeignKey("players.ogame_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    player = relationship("Player", uselist=False)


class ReportAPIKey(Base, CoordinatesMixin):
    __tablename__ = "report_api_keys"

    report_api_key = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    ogame_id = Column(Integer, ForeignKey("players.ogame_id"), nullable=False)
    source = Column(String, nullable=False)
    from_moon = Column(Boolean, default=False)

    ships = relationship("FleetShip", cascade="all, delete-orphan")
    techs = relationship("FleetTech", cascade="all, delete-orphan")

    planet = relationship(
        "Planet",
        primaryjoin=(
            "and_("
            "foreign(ReportAPIKey.ogame_id) == Planet.ogame_id, "
            "foreign(ReportAPIKey.galaxy) == Planet.galaxy, "
            "foreign(ReportAPIKey.system) == Planet.system, "
            "foreign(ReportAPIKey.position) == Planet.position"
            ")"
        ),
        viewonly=True,
        uselist=False,
    )
    ships = relationship("FleetShip", cascade="all, delete-orphan")
    techs = relationship("FleetTech", cascade="all, delete-orphan")

    def fleet_and_tech_dict(self):
        return {
            "ships": {ship.ship_type: ship.count for ship in self.ships},
            "techs": {tech.tech_type: tech.level for tech in self.techs},
        }

    def timestamp_display_text(self):
        created_at_utc = self.created_at.replace(tzinfo=timezone.utc)
        seconds = int(
            (datetime.now(timezone.utc) - created_at_utc).total_seconds()
        )

        if seconds < 60:
            ago = f"{seconds} seconds ago"
        elif seconds < 3600:
            minutes = seconds // 60
            ago = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif seconds < 86400:
            hours = seconds // 3600
            ago = f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            if hours:
                ago = f"{days} day{'s' if days != 1 else ''} "
                f"{hours} hour{'s' if hours != 1 else ''} ago"
            else:
                ago = f"{days} day{'s' if days != 1 else ''} ago"

        return f"{created_at_utc:%Y-%m-%d %H:%M:%S UTC} ({ago})"


class FleetShip(Base):
    __tablename__ = "fleet_ships"

    report_api_key = Column(
        String, ForeignKey("report_api_keys.report_api_key"), primary_key=True
    )
    ship_type = Column(Integer, primary_key=True)
    count = Column(Integer, nullable=False)


class FleetTech(Base):
    __tablename__ = "fleet_techs"

    report_api_key = Column(
        String, ForeignKey("report_api_keys.report_api_key"), primary_key=True
    )
    tech_type = Column(Integer, primary_key=True)
    level = Column(Integer, nullable=False)


engine = create_engine("sqlite:///ogame_players.db")
Base.metadata.create_all(engine)

Session = sessionmaker(bind=engine)
