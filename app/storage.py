from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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


# --- Tracks ---

class TrackMap(Base):
    __tablename__ = "track_map"
    spotify_id = Column(String(64), primary_key=True)
    spotify_title = Column(String(512), nullable=False)
    spotify_artist = Column(String(512), nullable=False)
    tidal_id = Column(String(64), nullable=True)
    tidal_title = Column(String(512), nullable=True)
    tidal_artist = Column(String(512), nullable=True)
    isrc = Column(String(32), nullable=True)
    confidence = Column(Float, default=0.0)
    resolved = Column(Boolean, default=False)
    last_synced_at = Column(DateTime, nullable=True)


class PendingTrackResolution(Base):
    __tablename__ = "pending_track_resolution"
    id = Column(Integer, primary_key=True, autoincrement=True)
    spotify_id = Column(String(64), nullable=False)
    spotify_title = Column(String(512), nullable=False)
    spotify_artist = Column(String(512), nullable=False)
    isrc = Column(String(32), nullable=True)
    candidates_json = Column(Text, nullable=False)  # JSON list of {id,title,artist,isrc,duration,score}
    created_at = Column(DateTime, default=datetime.utcnow)


# --- Sync Events (audit/backup) ---

class ArtistSyncEvent(Base):
    __tablename__ = "artist_sync_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    spotify_id = Column(String(64), nullable=False)
    spotify_name = Column(String(255), nullable=False)
    tidal_id = Column(String(64), nullable=False)
    tidal_name = Column(String(255), nullable=False)
    source = Column(String(16), nullable=False)  # 'auto' | 'manual'
    synced_at = Column(DateTime, default=datetime.utcnow)


class TrackSyncEvent(Base):
    __tablename__ = "track_sync_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    spotify_id = Column(String(64), nullable=False)
    spotify_title = Column(String(512), nullable=False)
    spotify_artist = Column(String(512), nullable=False)
    tidal_id = Column(String(64), nullable=False)
    tidal_title = Column(String(512), nullable=False)
    tidal_artist = Column(String(512), nullable=False)
    isrc = Column(String(32), nullable=True)
    source = Column(String(16), nullable=False)  # 'auto' | 'manual'
    synced_at = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
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
        existing.data = payload  # type: ignore[assignment]
        existing.updated_at = datetime.utcnow()  # type: ignore[assignment]
    else:
        db.add(Token(service=service, data=payload))
    db.commit()


def load_token(db: OrmSession, service: str) -> Optional[Dict[str, Any]]:
    tok = db.get(Token, service)
    if not tok:
        return None
    try:
        return json.loads(str(tok.data))
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
    m.tidal_id = tidal_id  # type: ignore[assignment]
    m.tidal_name = tidal_name  # type: ignore[assignment]
    m.confidence = confidence  # type: ignore[assignment]
    m.resolved = resolved  # type: ignore[assignment]
    m.last_synced_at = datetime.utcnow() if tidal_id else None  # type: ignore[assignment]
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
                "candidates": json.loads(str(r.candidates_json)),
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


# Track helpers

def upsert_track_map(
    db: OrmSession,
    spotify_id: str,
    spotify_title: str,
    spotify_artist: str,
    tidal_id: Optional[str],
    tidal_title: Optional[str],
    tidal_artist: Optional[str],
    isrc: Optional[str],
    confidence: float,
    resolved: bool,
) -> None:
    m = db.get(TrackMap, spotify_id)
    if not m:
        m = TrackMap(spotify_id=spotify_id, spotify_title=spotify_title, spotify_artist=spotify_artist)
        db.add(m)
    m.tidal_id = tidal_id  # type: ignore[assignment]
    m.tidal_title = tidal_title  # type: ignore[assignment]
    m.tidal_artist = tidal_artist  # type: ignore[assignment]
    m.isrc = isrc  # type: ignore[assignment]
    m.confidence = confidence  # type: ignore[assignment]
    m.resolved = resolved  # type: ignore[assignment]
    m.last_synced_at = datetime.utcnow() if tidal_id else None  # type: ignore[assignment]
    db.commit()


def add_pending_track_resolution(
    db: OrmSession, spotify_id: str, title: str, artist: str, isrc: Optional[str], candidates: List[Dict[str, Any]]
) -> None:
    db.query(PendingTrackResolution).filter(PendingTrackResolution.spotify_id == spotify_id).delete()
    db.add(
        PendingTrackResolution(
            spotify_id=spotify_id,
            spotify_title=title,
            spotify_artist=artist,
            isrc=isrc,
            candidates_json=json.dumps(candidates),
        )
    )
    db.commit()


def get_pending_tracks(db: OrmSession) -> List[Dict[str, Any]]:
    rows = db.query(PendingTrackResolution).order_by(PendingTrackResolution.created_at.asc()).all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "spotify_id": r.spotify_id,
                "spotify_title": r.spotify_title,
                "spotify_artist": r.spotify_artist,
                "isrc": r.isrc,
                "candidates": json.loads(str(r.candidates_json)),
                "created_at": r.created_at.isoformat(),
            }
        )
    return out


def delete_pending_track(db: OrmSession, pending_id: int) -> None:
    db.query(PendingTrackResolution).filter(PendingTrackResolution.id == pending_id).delete()
    db.commit()


def delete_pending_track_by_spotify_id(db: OrmSession, spotify_id: str) -> None:
    db.query(PendingTrackResolution).filter(PendingTrackResolution.spotify_id == spotify_id).delete()
    db.commit()


def cleanup_pending_tracks_for_resolved(db: OrmSession) -> int:
    subq = db.query(TrackMap.spotify_id).filter(TrackMap.resolved == True)
    deleted = db.query(PendingTrackResolution).filter(PendingTrackResolution.spotify_id.in_(subq)).delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)


# Event helpers

def add_artist_sync_event(
    db: OrmSession,
    spotify_id: str,
    spotify_name: str,
    tidal_id: str,
    tidal_name: str,
    source: str,
) -> None:
    db.add(
        ArtistSyncEvent(
            spotify_id=spotify_id,
            spotify_name=spotify_name,
            tidal_id=tidal_id,
            tidal_name=tidal_name,
            source=source,
        )
    )
    db.commit()


def add_track_sync_event(
    db: OrmSession,
    spotify_id: str,
    spotify_title: str,
    spotify_artist: str,
    tidal_id: str,
    tidal_title: str,
    tidal_artist: str,
    isrc: Optional[str],
    source: str,
) -> None:
    db.add(
        TrackSyncEvent(
            spotify_id=spotify_id,
            spotify_title=spotify_title,
            spotify_artist=spotify_artist,
            tidal_id=tidal_id,
            tidal_title=tidal_title,
            tidal_artist=tidal_artist,
            isrc=isrc,
            source=source,
        )
    )
    db.commit()


# Export helpers (for backup)

def export_artists(db: OrmSession) -> List[Dict[str, Any]]:
    rows = db.query(ArtistMap).all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "tidal_id": r.tidal_id,  # type: ignore[attr-defined]
                "tidal_name": r.tidal_name,  # type: ignore[attr-defined]
                "confidence": float(r.confidence),  # type: ignore[arg-type]
                "resolved": bool(r.resolved),  # type: ignore[arg-type]
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,  # type: ignore[union-attr]
            }
        )
    return out


def export_tracks(db: OrmSession) -> List[Dict[str, Any]]:
    rows = db.query(TrackMap).all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_title": r.spotify_title,
                "spotify_artist": r.spotify_artist,
                "tidal_id": r.tidal_id,  # type: ignore[attr-defined]
                "tidal_title": r.tidal_title,  # type: ignore[attr-defined]
                "tidal_artist": r.tidal_artist,  # type: ignore[attr-defined]
                "isrc": r.isrc,  # type: ignore[attr-defined]
                "confidence": float(r.confidence),  # type: ignore[arg-type]
                "resolved": bool(r.resolved),  # type: ignore[arg-type]
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,  # type: ignore[union-attr]
            }
        )
    return out


# Library listing helpers (for UI)

def list_synced_artists(
    db: OrmSession,
    *,
    search: Optional[str] = None,
    sort: str = "last_synced_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Dict[str, Any]], int]:
    from sqlalchemy import func, or_

    q = db.query(ArtistMap).filter(ArtistMap.tidal_id.isnot(None))
    if search:
        s = f"%{search.lower()}%"
        q = q.filter(
            or_(
                func.lower(ArtistMap.spotify_name).like(s),
                func.lower(ArtistMap.tidal_name).like(s),
                func.lower(ArtistMap.spotify_id).like(s),
                func.lower(ArtistMap.tidal_id).like(s),
            )
        )

    total = q.count()

    # Sorting
    sort_map = {
        "last_synced_at": ArtistMap.last_synced_at,
        "spotify_name": ArtistMap.spotify_name,
        "tidal_name": ArtistMap.tidal_name,
        "confidence": ArtistMap.confidence,
    }
    col = sort_map.get(sort, ArtistMap.last_synced_at)
    if order.lower() == "asc":
        q = q.order_by(col.asc().nullslast())
    else:
        q = q.order_by(col.desc().nullslast())

    # Pagination
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 50
    offset = (page - 1) * page_size
    q = q.offset(offset).limit(page_size)

    rows = q.all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "tidal_id": r.tidal_id,  # type: ignore[attr-defined]
                "tidal_name": r.tidal_name,  # type: ignore[attr-defined]
                "confidence": float(r.confidence),  # type: ignore[arg-type]
                "resolved": bool(r.resolved),  # type: ignore[arg-type]
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,  # type: ignore[union-attr]
            }
        )
    return out, total


def list_synced_tracks(
    db: OrmSession,
    *,
    search: Optional[str] = None,
    sort: str = "last_synced_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Dict[str, Any]], int]:
    from sqlalchemy import func, or_

    q = db.query(TrackMap).filter(TrackMap.tidal_id.isnot(None))
    if search:
        s = f"%{search.lower()}%"
        q = q.filter(
            or_(
                func.lower(TrackMap.spotify_title).like(s),
                func.lower(TrackMap.spotify_artist).like(s),
                func.lower(TrackMap.tidal_title).like(s),
                func.lower(TrackMap.tidal_artist).like(s),
                func.lower(TrackMap.isrc).like(s),
                func.lower(TrackMap.spotify_id).like(s),
                func.lower(TrackMap.tidal_id).like(s),
            )
        )

    total = q.count()

    sort_map = {
        "last_synced_at": TrackMap.last_synced_at,
        "spotify_title": TrackMap.spotify_title,
        "spotify_artist": TrackMap.spotify_artist,
        "tidal_title": TrackMap.tidal_title,
        "tidal_artist": TrackMap.tidal_artist,
        "confidence": TrackMap.confidence,
    }
    col = sort_map.get(sort, TrackMap.last_synced_at)
    if order.lower() == "asc":
        q = q.order_by(col.asc().nullslast())
    else:
        q = q.order_by(col.desc().nullslast())

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 50
    offset = (page - 1) * page_size
    q = q.offset(offset).limit(page_size)

    rows = q.all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_title": r.spotify_title,
                "spotify_artist": r.spotify_artist,
                "tidal_id": r.tidal_id,  # type: ignore[attr-defined]
                "tidal_title": r.tidal_title,  # type: ignore[attr-defined]
                "tidal_artist": r.tidal_artist,  # type: ignore[attr-defined]
                "isrc": r.isrc,  # type: ignore[attr-defined]
                "confidence": float(r.confidence),  # type: ignore[arg-type]
                "resolved": bool(r.resolved),  # type: ignore[arg-type]
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,  # type: ignore[union-attr]
            }
        )
    return out, total
