from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session as OrmSession

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "musicsync.db")
engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


class Token(Base):
    __tablename__ = "tokens"
    service = Column(String(20), primary_key=True)  # 'spotify' | 'tidal'
    data = Column(Text, nullable=False)  # JSON blob
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ArtistMap(Base):
    __tablename__ = "artist_map"
    spotify_id = Column(String(64), primary_key=True)
    spotify_name = Column(String(255), nullable=False)
    tidal_id = Column(String(64), nullable=True)
    tidal_name = Column(String(255), nullable=True)
    confidence = Column(Float, default=0.0)
    resolved = Column(Boolean, default=False)
    last_synced_at = Column(DateTime, nullable=True)


class PendingResolution(Base):
    __tablename__ = "pending_resolution"
    id = Column(Integer, primary_key=True, autoincrement=True)
    spotify_id = Column(String(64), nullable=False)
    spotify_name = Column(String(255), nullable=False)
    candidates_json = Column(Text, nullable=False)  # JSON list of {id,name,score}
    created_at = Column(DateTime, default=datetime.utcnow)


class RunLog(Base):
    __tablename__ = "run_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    phase = Column(String(64), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> OrmSession:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Helper functions

def save_token(db: OrmSession, service: str, data: Dict[str, Any]) -> None:
    payload = json.dumps(data)
    existing = db.get(Token, service)
    if existing:
        existing.data = payload
        existing.updated_at = datetime.utcnow()
    else:
        db.add(Token(service=service, data=payload))
    db.commit()


def load_token(db: OrmSession, service: str) -> Optional[Dict[str, Any]]:
    tok = db.get(Token, service)
    if not tok:
        return None
    try:
        return json.loads(tok.data)
    except json.JSONDecodeError:
        return None


def upsert_artist_map(
    db: OrmSession,
    spotify_id: str,
    spotify_name: str,
    tidal_id: Optional[str],
    tidal_name: Optional[str],
    confidence: float,
    resolved: bool,
) -> None:
    m = db.get(ArtistMap, spotify_id)
    if not m:
        m = ArtistMap(spotify_id=spotify_id, spotify_name=spotify_name)
        db.add(m)
    m.tidal_id = tidal_id
    m.tidal_name = tidal_name
    m.confidence = confidence
    m.resolved = resolved
    m.last_synced_at = datetime.utcnow() if tidal_id else None
    db.commit()


def add_pending_resolution(
    db: OrmSession, spotify_id: str, spotify_name: str, candidates: List[Dict[str, Any]]
) -> None:
    # Remove any existing pending for this spotify_id to keep latest
    db.query(PendingResolution).filter(PendingResolution.spotify_id == spotify_id).delete()
    db.add(
        PendingResolution(
            spotify_id=spotify_id, spotify_name=spotify_name, candidates_json=json.dumps(candidates)
        )
    )
    db.commit()


def get_pending(db: OrmSession) -> List[Dict[str, Any]]:
    rows = db.query(PendingResolution).order_by(PendingResolution.created_at.asc()).all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "candidates": json.loads(r.candidates_json),
                "created_at": r.created_at.isoformat(),
            }
        )
    return out


def delete_pending(db: OrmSession, pending_id: int) -> None:
    db.query(PendingResolution).filter(PendingResolution.id == pending_id).delete()
    db.commit()


def delete_pending_by_spotify_id(db: OrmSession, spotify_id: str) -> None:
    """Delete any pending resolution rows for the given Spotify artist id."""
    db.query(PendingResolution).filter(PendingResolution.spotify_id == spotify_id).delete()
    db.commit()


def cleanup_pending_for_resolved(db: OrmSession) -> int:
    """Remove pending rows whose artists are already resolved in ArtistMap.

    Returns the number of rows deleted (best-effort estimate).
    """
    subq = db.query(ArtistMap.spotify_id).filter(ArtistMap.resolved == True)
    deleted = db.query(PendingResolution).filter(PendingResolution.spotify_id.in_(subq)).delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)


def log(db: OrmSession, phase: str, message: str) -> None:
    db.add(RunLog(phase=phase, message=message))
    db.commit()
