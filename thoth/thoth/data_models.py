from datetime import datetime, timedelta

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
    latest_report_api_key = relationship(
        "ReportAPIKey",
        uselist=False,
        primaryjoin="Player.ogame_id==ReportAPIKey.ogame_id",
        order_by="ReportAPIKey.created_at.desc()",
        viewonly=True,
    )


class Planet(Base):
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

    def coords_str(self) -> str:
        return f"{self.galaxy}:{self.system}:{self.position}"

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


class ReportAPIKey(Base):
    __tablename__ = "report_api_keys"

    report_api_key = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    ogame_id = Column(Integer, ForeignKey("players.ogame_id"), nullable=False)


engine = create_engine("sqlite:///ogame_players.db")
Base.metadata.create_all(engine)

Session = sessionmaker(bind=engine)
