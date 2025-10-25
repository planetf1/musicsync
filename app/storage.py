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
    # JSON-encoded list of Spotify genres for the artist (simple list[str])
    genres_json = Column(Text, nullable=True)


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
    spotify_artist_id = Column(String(64), nullable=True)
    tidal_id = Column(String(64), nullable=True)
    tidal_title = Column(String(512), nullable=True)
    tidal_artist = Column(String(512), nullable=True)
    tidal_artist_id = Column(String(64), nullable=True)
    isrc = Column(String(32), nullable=True)
    spotify_duration = Column(Integer, nullable=True)  # seconds
    tidal_duration = Column(Integer, nullable=True)    # seconds
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


# --- Playlists ---

class PlaylistMap(Base):
    __tablename__ = "playlist_map"
    spotify_id = Column(String(64), primary_key=True)
    spotify_name = Column(String(512), nullable=False)
    tidal_id = Column(String(64), nullable=True)
    tidal_name = Column(String(512), nullable=True)
    last_synced_at = Column(DateTime, nullable=True)


class PlaylistTrack(Base):
    __tablename__ = "playlist_track"
    id = Column(Integer, primary_key=True, autoincrement=True)
    playlist_spotify_id = Column(String(64), nullable=False, index=True)
    position = Column(Integer, nullable=False)  # 1-based order from Spotify
    spotify_track_id = Column(String(64), nullable=False)
    spotify_title = Column(String(512), nullable=False)
    spotify_artist = Column(String(512), nullable=False)
    tidal_track_id = Column(String(64), nullable=True)
    tidal_title = Column(String(512), nullable=True)
    tidal_artist = Column(String(512), nullable=True)
    isrc = Column(String(32), nullable=True)
    spotify_duration = Column(Integer, nullable=True)  # seconds
    last_synced_at = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # Lightweight migrations for SQLite: add missing columns to track_map
    try:
        with engine.connect() as conn:
            res = conn.exec_driver_sql("PRAGMA table_info('track_map')")
            cols = {row[1] for row in res.fetchall()}  # type: ignore[index]
            if 'spotify_artist_id' not in cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN spotify_artist_id VARCHAR(64)")
            if 'tidal_artist_id' not in cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN tidal_artist_id VARCHAR(64)")
            if 'spotify_duration' not in cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN spotify_duration INTEGER")
            if 'tidal_duration' not in cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN tidal_duration INTEGER")
            # Ensure artist_map has genres_json column
            res_art = conn.exec_driver_sql("PRAGMA table_info('artist_map')")
            art_cols = {row[1] for row in res_art.fetchall()}  # type: ignore[index]
            if 'genres_json' not in art_cols:
                try:
                    conn.exec_driver_sql("ALTER TABLE artist_map ADD COLUMN genres_json TEXT")
                except Exception:
                    pass
            # Ensure playlist_map table exists
            res2 = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='playlist_map'")
            if not res2.fetchall():
                conn.exec_driver_sql(
                    "CREATE TABLE playlist_map (spotify_id VARCHAR(64) PRIMARY KEY, spotify_name VARCHAR(512) NOT NULL, tidal_id VARCHAR(64), tidal_name VARCHAR(512), last_synced_at DATETIME)"
                )
            # Ensure playlist_track table has spotify_duration
            res3 = conn.exec_driver_sql("PRAGMA table_info('playlist_track')")
            pcols = {row[1] for row in res3.fetchall()}  # type: ignore[index]
            if 'spotify_duration' not in pcols:
                try:
                    conn.exec_driver_sql("ALTER TABLE playlist_track ADD COLUMN spotify_duration INTEGER")
                except Exception:
                    pass
    except Exception:
        # Best-effort; if migration fails, app still runs without new columns
        pass


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


def update_artist_genres(db: OrmSession, spotify_id: str, genres: Optional[List[str]]) -> None:
    """Update stored genres for a Spotify artist.

    Genres are stored as a JSON-encoded list on ArtistMap.genres_json. This does not
    alter match status; it's safe to call independently of syncing.
    """
    m = db.get(ArtistMap, spotify_id)
    if not m:
        return
    try:
        payload = json.dumps(list(genres or []))
    except Exception:
        payload = json.dumps([])
    m.genres_json = payload  # type: ignore[assignment]
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
    spotify_artist_id: Optional[str],
    tidal_id: Optional[str],
    tidal_title: Optional[str],
    tidal_artist: Optional[str],
    tidal_artist_id: Optional[str],
    isrc: Optional[str],
    spotify_duration: Optional[int],
    tidal_duration: Optional[int],
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
    m.spotify_artist_id = spotify_artist_id  # type: ignore[assignment]
    m.tidal_artist_id = tidal_artist_id  # type: ignore[assignment]
    m.isrc = isrc  # type: ignore[assignment]
    m.spotify_duration = spotify_duration  # type: ignore[assignment]
    m.tidal_duration = tidal_duration  # type: ignore[assignment]
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


# Playlist helpers

def upsert_playlist_map(
    db: OrmSession,
    spotify_id: str,
    spotify_name: str,
    tidal_id: str | None,
    tidal_name: str | None,
) -> None:
    m = db.get(PlaylistMap, spotify_id)
    if not m:
        m = PlaylistMap(spotify_id=spotify_id, spotify_name=spotify_name)
        db.add(m)
    m.tidal_id = tidal_id  # type: ignore[assignment]
    m.tidal_name = tidal_name  # type: ignore[assignment]
    m.last_synced_at = datetime.utcnow() if tidal_id else None  # type: ignore[assignment]
    db.commit()


def get_playlist_map(db: OrmSession, spotify_id: str) -> Optional[Dict[str, Any]]:
    m = db.get(PlaylistMap, spotify_id)
    if not m:
        return None
    return {
        "spotify_id": m.spotify_id,
        "spotify_name": m.spotify_name,
        "tidal_id": m.tidal_id,
        "tidal_name": m.tidal_name,
        "last_synced_at": m.last_synced_at.isoformat() if getattr(m, "last_synced_at", None) else None,
    }


def replace_playlist_tracks(db: OrmSession, playlist_spotify_id: str, entries: List[Dict[str, Any]]) -> None:
    """Replace the stored track snapshot for a playlist with the given ordered entries.

    Each entry should include: position (int, 1-based), spotify_track_id, spotify_title, spotify_artist,
    and optional tidal_track_id, tidal_title, tidal_artist, isrc.
    """
    from sqlalchemy import delete
    db.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_spotify_id == playlist_spotify_id))
    now = datetime.utcnow()
    for e in entries:
        db.add(
            PlaylistTrack(
                playlist_spotify_id=playlist_spotify_id,
                position=int(e.get("position", 0) or 0),
                spotify_track_id=str(e.get("spotify_track_id")),
                spotify_title=str(e.get("spotify_title") or ""),
                spotify_artist=str(e.get("spotify_artist") or ""),
                tidal_track_id=(str(e.get("tidal_track_id")) if e.get("tidal_track_id") else None),
                tidal_title=(str(e.get("tidal_title")) if e.get("tidal_title") else None),
                tidal_artist=(str(e.get("tidal_artist")) if e.get("tidal_artist") else None),
                isrc=(str(e.get("isrc")) if e.get("isrc") else None),
                spotify_duration=(int(e.get("spotify_duration")) if e.get("spotify_duration") is not None else None),  # type: ignore[arg-type]
                last_synced_at=now,
            )
        )
    db.commit()


def list_playlist_tracks(
    db: OrmSession,
    playlist_spotify_id: str,
    *,
    search: Optional[str] = None,
    sort: str = "position",
    order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Dict[str, Any]], int]:
    from sqlalchemy import func, or_

    q = db.query(PlaylistTrack).filter(PlaylistTrack.playlist_spotify_id == playlist_spotify_id)
    if search:
        s = f"%{search.lower()}%"
        q = q.filter(
            or_(
                func.lower(PlaylistTrack.spotify_title).like(s),
                func.lower(PlaylistTrack.spotify_artist).like(s),
                func.lower(PlaylistTrack.tidal_title).like(s),
                func.lower(PlaylistTrack.tidal_artist).like(s),
                func.lower(PlaylistTrack.spotify_track_id).like(s),
                func.lower(PlaylistTrack.tidal_track_id).like(s),
            )
        )

    total = q.count()

    sort_map = {
        "position": PlaylistTrack.position,
        "spotify_title": PlaylistTrack.spotify_title,
        "spotify_artist": PlaylistTrack.spotify_artist,
        "tidal_title": PlaylistTrack.tidal_title,
        "tidal_artist": PlaylistTrack.tidal_artist,
        "last_synced_at": PlaylistTrack.last_synced_at,
    }
    col = sort_map.get(sort, PlaylistTrack.position)
    if order.lower() == "asc":
        q = q.order_by(col.asc().nullslast())
    else:
        q = q.order_by(col.desc().nullslast())

    if page_size != 0:
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 25
        offset = (page - 1) * page_size
        q = q.offset(offset).limit(page_size)

    rows = q.all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "position": int(getattr(r, "position", 0) or 0),
                "spotify_track_id": r.spotify_track_id,
                "spotify_title": r.spotify_title,
                "spotify_artist": r.spotify_artist,
                "tidal_track_id": r.tidal_track_id,  # type: ignore[attr-defined]
                "tidal_title": r.tidal_title,  # type: ignore[attr-defined]
                "tidal_artist": r.tidal_artist,  # type: ignore[attr-defined]
                "isrc": r.isrc,  # type: ignore[attr-defined]
                "duration": int(getattr(r, "spotify_duration", 0) or 0),
                "last_synced_at": r.last_synced_at.isoformat() if getattr(r, "last_synced_at", None) else None,  # type: ignore[union-attr]
            }
        )
    return out, total


# Export helpers (for backup)

def export_artists(db: OrmSession) -> List[Dict[str, Any]]:
    rows = db.query(ArtistMap).all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            genres = json.loads(str(getattr(r, "genres_json", "") or "[]"))
        except Exception:
            genres = []
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "tidal_id": r.tidal_id,  # type: ignore[attr-defined]
                "tidal_name": r.tidal_name,  # type: ignore[attr-defined]
                "genres": genres,
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
                "spotify_artist_id": r.spotify_artist_id,  # type: ignore[attr-defined]
                "tidal_id": r.tidal_id,  # type: ignore[attr-defined]
                "tidal_title": r.tidal_title,  # type: ignore[attr-defined]
                "tidal_artist": r.tidal_artist,  # type: ignore[attr-defined]
                "tidal_artist_id": r.tidal_artist_id,  # type: ignore[attr-defined]
                "isrc": r.isrc,  # type: ignore[attr-defined]
                "spotify_duration": r.spotify_duration,  # type: ignore[attr-defined]
                "tidal_duration": r.tidal_duration,  # type: ignore[attr-defined]
                "confidence": float(r.confidence),  # type: ignore[arg-type]
                "resolved": bool(r.resolved),  # type: ignore[arg-type]
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,  # type: ignore[union-attr]
            }
        )
    return out


def export_playlists(db: OrmSession) -> List[Dict[str, Any]]:
    rows = db.query(PlaylistMap).all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "tidal_id": r.tidal_id,  # type: ignore[attr-defined]
                "tidal_name": r.tidal_name,  # type: ignore[attr-defined]
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,  # type: ignore[union-attr]
            }
        )
    return out


# Library listing helpers (for UI)

def list_synced_artists(
    db: OrmSession,
    *,
    search: Optional[str] = None,
    genre: Optional[str] = None,
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
    if genre:
        # crude filter using LIKE on the JSON text
        g = f"%{genre.lower()}%"
        q = q.filter(func.lower(ArtistMap.genres_json).like(g))

    total = q.count()

    # Sorting
    sort_map = {
        "last_synced_at": ArtistMap.last_synced_at,
        "spotify_name": ArtistMap.spotify_name,
        "tidal_name": ArtistMap.tidal_name,
        "confidence": ArtistMap.confidence,
    }
    in_memory_sort = False
    if sort == "genre":
        # We'll sort in memory by first genre (case-insensitive)
        in_memory_sort = True
    else:
        col = sort_map.get(sort, ArtistMap.last_synced_at)
        if order.lower() == "asc":
            q = q.order_by(col.asc().nullslast())
        else:
            q = q.order_by(col.desc().nullslast())

    # Pagination and fetching
    if in_memory_sort:
        rows = q.all()
    else:
        if page_size == 0:
            rows = q.all()
        else:
            if page < 1:
                page = 1
            if page_size < 1:
                page_size = 25
            offset = (page - 1) * page_size
            rows = q.offset(offset).limit(page_size).all()
    out: List[Dict[str, Any]] = []
    # Build output with genres parsed
    enriched: List[Dict[str, Any]] = []
    for r in rows:
        try:
            genres = json.loads(str(getattr(r, "genres_json", "") or "[]"))
        except Exception:
            genres = []
        enriched.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "tidal_id": r.tidal_id,  # type: ignore[attr-defined]
                "tidal_name": r.tidal_name,  # type: ignore[attr-defined]
                "genres": genres,
                "confidence": float(r.confidence),  # type: ignore[arg-type]
                "resolved": bool(r.resolved),  # type: ignore[arg-type]
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,  # type: ignore[union-attr]
            }
        )
    # If in-memory sort by genre requested, apply now and paginate
    if in_memory_sort:
        def gkey(item: Dict[str, Any]) -> str:
            if item.get("genres"):
                return str(item["genres"][0]).lower()
            return "~"  # tilde sorts after letters
        enriched.sort(key=gkey, reverse=(order.lower() == "desc"))
        if page_size != 0:
            if page < 1:
                page = 1
            if page_size < 1:
                page_size = 25
            start = (page - 1) * page_size
            end = start + page_size
            enriched = enriched[start:end]
    out = enriched
    return out, total


def list_genre_counts(
    db: OrmSession,
    *,
    search: Optional[str] = None,
) -> List[Tuple[str, int]]:
    """Compute aggregated genre counts across synced artists.

    Applies the same text search filter as list_synced_artists when provided.
    Returns a list of (genre, count) sorted by count desc then genre asc.
    """
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
    rows = q.all()
    counts: Dict[str, int] = {}
    for r in rows:
        try:
            genres = json.loads(str(getattr(r, "genres_json", "") or "[]"))
        except Exception:
            genres = []
        for g in genres:
            try:
                key = str(g).strip()
            except Exception:
                continue
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
    # Sort by count desc, then name asc
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return items


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

    if page_size == 0:
        # 'all' requested: do not apply pagination
        pass
    else:
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 25
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
                "spotify_artist_id": r.spotify_artist_id,  # type: ignore[attr-defined]
                "tidal_id": r.tidal_id,  # type: ignore[attr-defined]
                "tidal_title": r.tidal_title,  # type: ignore[attr-defined]
                "tidal_artist": r.tidal_artist,  # type: ignore[attr-defined]
                "tidal_artist_id": r.tidal_artist_id,  # type: ignore[attr-defined]
                "isrc": r.isrc,  # type: ignore[attr-defined]
                "spotify_duration": r.spotify_duration,  # type: ignore[attr-defined]
                "tidal_duration": r.tidal_duration,  # type: ignore[attr-defined]
                "confidence": float(r.confidence),  # type: ignore[arg-type]
                "resolved": bool(r.resolved),  # type: ignore[arg-type]
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,  # type: ignore[union-attr]
            }
        )
    return out, total


def list_synced_playlists(
    db: OrmSession,
    *,
    search: Optional[str] = None,
    sort: str = "last_synced_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Dict[str, Any]], int]:
    from sqlalchemy import func, or_

    q = db.query(PlaylistMap).filter(PlaylistMap.tidal_id.isnot(None))
    if search:
        s = f"%{search.lower()}%"
        q = q.filter(
            or_(
                func.lower(PlaylistMap.spotify_name).like(s),
                func.lower(PlaylistMap.tidal_name).like(s),
                func.lower(PlaylistMap.spotify_id).like(s),
                func.lower(PlaylistMap.tidal_id).like(s),
            )
        )

    total = q.count()

    sort_map = {
        "last_synced_at": PlaylistMap.last_synced_at,
        "spotify_name": PlaylistMap.spotify_name,
        "tidal_name": PlaylistMap.tidal_name,
    }
    col = sort_map.get(sort, PlaylistMap.last_synced_at)
    if order.lower() == "asc":
        q = q.order_by(col.asc().nullslast())
    else:
        q = q.order_by(col.desc().nullslast())

    if page_size == 0:
        pass
    else:
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 25
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
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,  # type: ignore[union-attr]
            }
        )
    return out, total
