from datetime import datetime, timedelta

from sqlalchemy import (
    create_engine,
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

    # OGame player ID is the primary key
    ogame_id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    planets = relationship("Planet", back_populates="player")


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


engine = create_engine("sqlite:///ogame_players.db")
Base.metadata.create_all(engine)

Session = sessionmaker(bind=engine)
