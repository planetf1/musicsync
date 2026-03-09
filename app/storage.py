from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.orm import Session as OrmSession

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "musicsync.db")
engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


class Token(Base):
    __tablename__ = "tokens"
    service: Mapped[str] = mapped_column(String(20), primary_key=True)  # 'spotify' | 'tidal' | 'apple'
    # For 'apple': data JSON contains {"developer_token": "...", "music_user_token": "...", ...}
    data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON blob
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class ArtistMap(Base):
    __tablename__ = "artist_map"
    # Composite primary key: (spotify_id, target_service)
    spotify_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_service: Mapped[str] = mapped_column(String(20), primary_key=True, default="tidal")  # 'tidal' | 'apple'
    spotify_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Legacy TIDAL-specific columns (kept for backwards compatibility, use target_* for new services)
    tidal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tidal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Generic target columns (use these for Apple Music and future services)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # JSON-encoded list of Spotify genres for the artist (simple list[str])
    genres_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class PendingResolution(Base):
    __tablename__ = "pending_resolution"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spotify_id: Mapped[str] = mapped_column(String(64), nullable=False)
    spotify_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_service: Mapped[str] = mapped_column(String(20), nullable=False, default="tidal")  # 'tidal' | 'apple'
    candidates_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of {id,name,score}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class RunLog(Base):
    __tablename__ = "run_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


# --- Tracks ---


class TrackMap(Base):
    __tablename__ = "track_map"
    # Composite primary key: (spotify_id, target_service)
    spotify_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_service: Mapped[str] = mapped_column(String(20), primary_key=True, default="tidal")  # 'tidal' | 'apple'
    spotify_title: Mapped[str] = mapped_column(String(512), nullable=False)
    spotify_artist: Mapped[str] = mapped_column(String(512), nullable=False)
    spotify_artist_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Legacy TIDAL-specific columns (kept for backwards compatibility)
    tidal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tidal_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tidal_artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tidal_artist_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tidal_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    # Generic target columns (use these for Apple Music and future services)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_artist_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    isrc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    spotify_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PendingTrackResolution(Base):
    __tablename__ = "pending_track_resolution"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spotify_id: Mapped[str] = mapped_column(String(64), nullable=False)
    spotify_title: Mapped[str] = mapped_column(String(512), nullable=False)
    spotify_artist: Mapped[str] = mapped_column(String(512), nullable=False)
    target_service: Mapped[str] = mapped_column(String(20), nullable=False, default="tidal")  # 'tidal' | 'apple'
    isrc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    candidates_json: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # JSON list of {id,title,artist,isrc,duration,score}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


# --- Sync Events (audit/backup) ---


class ArtistSyncEvent(Base):
    __tablename__ = "artist_sync_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spotify_id: Mapped[str] = mapped_column(String(64), nullable=False)
    spotify_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_service: Mapped[str] = mapped_column(String(20), nullable=False, default="tidal")  # 'tidal' | 'apple'
    # Legacy TIDAL columns
    tidal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tidal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Generic target columns
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # 'auto' | 'manual'
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class TrackSyncEvent(Base):
    __tablename__ = "track_sync_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spotify_id: Mapped[str] = mapped_column(String(64), nullable=False)
    spotify_title: Mapped[str] = mapped_column(String(512), nullable=False)
    spotify_artist: Mapped[str] = mapped_column(String(512), nullable=False)
    target_service: Mapped[str] = mapped_column(String(20), nullable=False, default="tidal")  # 'tidal' | 'apple'
    # Legacy TIDAL columns
    tidal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tidal_title: Mapped[str] = mapped_column(String(512), nullable=False)
    tidal_artist: Mapped[str] = mapped_column(String(512), nullable=False)
    # Generic target columns
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    isrc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # 'auto' | 'manual'
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


# --- Playlists ---


class PlaylistMap(Base):
    __tablename__ = "playlist_map"
    # Composite primary key: (spotify_id, target_service)
    spotify_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_service: Mapped[str] = mapped_column(String(20), primary_key=True, default="tidal")  # 'tidal' | 'apple'
    spotify_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # Legacy TIDAL-specific columns (kept for backwards compatibility)
    tidal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tidal_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Generic target columns (use these for Apple Music and future services)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PlaylistTrack(Base):
    __tablename__ = "playlist_track"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_spotify_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_service: Mapped[str] = mapped_column(
        String(20), nullable=False, default="tidal", index=True
    )  # 'tidal' | 'apple'
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based order from Spotify
    spotify_track_id: Mapped[str] = mapped_column(String(64), nullable=False)
    spotify_title: Mapped[str] = mapped_column(String(512), nullable=False)
    spotify_artist: Mapped[str] = mapped_column(String(512), nullable=False)
    # Legacy TIDAL-specific columns
    tidal_track_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tidal_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tidal_artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Generic target columns
    target_track_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    isrc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    spotify_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


def init_db() -> None:
    """Initialize database and run migrations for multi-service support."""
    Base.metadata.create_all(bind=engine)

    # Lightweight migrations for SQLite: add missing columns and migrate to multi-service schema
    try:
        with engine.connect() as conn:
            # === artist_map migrations ===
            res_art = conn.exec_driver_sql("PRAGMA table_info('artist_map')")
            art_cols = {row[1] for row in res_art.fetchall()}

            if 'genres_json' not in art_cols:
                conn.exec_driver_sql("ALTER TABLE artist_map ADD COLUMN genres_json TEXT")
            if 'target_service' not in art_cols:
                conn.exec_driver_sql("ALTER TABLE artist_map ADD COLUMN target_service VARCHAR(20) DEFAULT 'tidal'")
            if 'target_id' not in art_cols:
                conn.exec_driver_sql("ALTER TABLE artist_map ADD COLUMN target_id VARCHAR(64)")
            if 'target_name' not in art_cols:
                conn.exec_driver_sql("ALTER TABLE artist_map ADD COLUMN target_name VARCHAR(255)")

            # Backfill target_* columns from tidal_* for existing rows
            conn.exec_driver_sql(
                "UPDATE artist_map SET target_id = tidal_id, target_name = tidal_name "
                "WHERE target_service = 'tidal' AND target_id IS NULL AND tidal_id IS NOT NULL"
            )

            # === track_map migrations ===
            res_track = conn.exec_driver_sql("PRAGMA table_info('track_map')")
            track_cols = {row[1] for row in res_track.fetchall()}

            if 'spotify_artist_id' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN spotify_artist_id VARCHAR(64)")
            if 'tidal_artist_id' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN tidal_artist_id VARCHAR(64)")
            if 'spotify_duration' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN spotify_duration INTEGER")
            if 'tidal_duration' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN tidal_duration INTEGER")
            if 'target_service' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN target_service VARCHAR(20) DEFAULT 'tidal'")
            if 'target_id' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN target_id VARCHAR(64)")
            if 'target_title' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN target_title VARCHAR(512)")
            if 'target_artist' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN target_artist VARCHAR(512)")
            if 'target_artist_id' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN target_artist_id VARCHAR(64)")
            if 'target_duration' not in track_cols:
                conn.exec_driver_sql("ALTER TABLE track_map ADD COLUMN target_duration INTEGER")

            # Backfill target_* columns from tidal_* for existing rows
            conn.exec_driver_sql(
                "UPDATE track_map SET "
                "target_id = tidal_id, target_title = tidal_title, target_artist = tidal_artist, "
                "target_artist_id = tidal_artist_id, target_duration = tidal_duration "
                "WHERE target_service = 'tidal' AND target_id IS NULL AND tidal_id IS NOT NULL"
            )

            # === playlist_map migrations ===
            res_pl = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='playlist_map'")
            if not res_pl.fetchall():
                conn.exec_driver_sql(
                    "CREATE TABLE playlist_map ("
                    "spotify_id VARCHAR(64) NOT NULL, "
                    "target_service VARCHAR(20) NOT NULL DEFAULT 'tidal', "
                    "spotify_name VARCHAR(512) NOT NULL, "
                    "tidal_id VARCHAR(64), "
                    "tidal_name VARCHAR(512), "
                    "target_id VARCHAR(64), "
                    "target_name VARCHAR(512), "
                    "last_synced_at DATETIME, "
                    "PRIMARY KEY (spotify_id, target_service))"
                )
            else:
                res_plcols = conn.exec_driver_sql("PRAGMA table_info('playlist_map')")
                pl_cols = {row[1] for row in res_plcols.fetchall()}
                if 'target_service' not in pl_cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE playlist_map ADD COLUMN target_service VARCHAR(20) DEFAULT 'tidal'"
                    )
                if 'target_id' not in pl_cols:
                    conn.exec_driver_sql("ALTER TABLE playlist_map ADD COLUMN target_id VARCHAR(64)")
                if 'target_name' not in pl_cols:
                    conn.exec_driver_sql("ALTER TABLE playlist_map ADD COLUMN target_name VARCHAR(512)")

                # Backfill target_* columns from tidal_* for existing rows
                conn.exec_driver_sql(
                    "UPDATE playlist_map SET target_id = tidal_id, target_name = tidal_name "
                    "WHERE target_service = 'tidal' AND target_id IS NULL AND tidal_id IS NOT NULL"
                )

            # === playlist_track migrations ===
            res_pt = conn.exec_driver_sql("PRAGMA table_info('playlist_track')")
            pt_cols = {row[1] for row in res_pt.fetchall()}

            if 'spotify_duration' not in pt_cols:
                conn.exec_driver_sql("ALTER TABLE playlist_track ADD COLUMN spotify_duration INTEGER")
            if 'target_service' not in pt_cols:
                conn.exec_driver_sql("ALTER TABLE playlist_track ADD COLUMN target_service VARCHAR(20) DEFAULT 'tidal'")
            if 'target_track_id' not in pt_cols:
                conn.exec_driver_sql("ALTER TABLE playlist_track ADD COLUMN target_track_id VARCHAR(64)")
            if 'target_title' not in pt_cols:
                conn.exec_driver_sql("ALTER TABLE playlist_track ADD COLUMN target_title VARCHAR(512)")
            if 'target_artist' not in pt_cols:
                conn.exec_driver_sql("ALTER TABLE playlist_track ADD COLUMN target_artist VARCHAR(512)")

            # Backfill target_* columns from tidal_* for existing rows
            conn.exec_driver_sql(
                "UPDATE playlist_track SET "
                "target_track_id = tidal_track_id, target_title = tidal_title, target_artist = tidal_artist "
                "WHERE target_service = 'tidal' AND target_track_id IS NULL AND tidal_track_id IS NOT NULL"
            )

            # === pending_resolution migrations ===
            res_pend = conn.exec_driver_sql("PRAGMA table_info('pending_resolution')")
            pend_cols = {row[1] for row in res_pend.fetchall()}
            if 'target_service' not in pend_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE pending_resolution ADD COLUMN target_service VARCHAR(20) DEFAULT 'tidal'"
                )

            # === pending_track_resolution migrations ===
            res_ptrack = conn.exec_driver_sql("PRAGMA table_info('pending_track_resolution')")
            ptrack_cols = {row[1] for row in res_ptrack.fetchall()}
            if 'target_service' not in ptrack_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE pending_track_resolution ADD COLUMN target_service VARCHAR(20) DEFAULT 'tidal'"
                )

            # === artist_sync_event migrations ===
            res_ase = conn.exec_driver_sql("PRAGMA table_info('artist_sync_event')")
            ase_cols = {row[1] for row in res_ase.fetchall()}
            if 'target_service' not in ase_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE artist_sync_event ADD COLUMN target_service VARCHAR(20) DEFAULT 'tidal'"
                )
            if 'target_id' not in ase_cols:
                conn.exec_driver_sql("ALTER TABLE artist_sync_event ADD COLUMN target_id VARCHAR(64)")
            if 'target_name' not in ase_cols:
                conn.exec_driver_sql("ALTER TABLE artist_sync_event ADD COLUMN target_name VARCHAR(255)")

            # === track_sync_event migrations ===
            res_tse = conn.exec_driver_sql("PRAGMA table_info('track_sync_event')")
            tse_cols = {row[1] for row in res_tse.fetchall()}
            if 'target_service' not in tse_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE track_sync_event ADD COLUMN target_service VARCHAR(20) DEFAULT 'tidal'"
                )
            if 'target_id' not in tse_cols:
                conn.exec_driver_sql("ALTER TABLE track_sync_event ADD COLUMN target_id VARCHAR(64)")
            if 'target_title' not in tse_cols:
                conn.exec_driver_sql("ALTER TABLE track_sync_event ADD COLUMN target_title VARCHAR(512)")
            if 'target_artist' not in tse_cols:
                conn.exec_driver_sql("ALTER TABLE track_sync_event ADD COLUMN target_artist VARCHAR(512)")

            conn.commit()
    except Exception as e:
        # Best-effort; if migration fails, log it but allow app to continue
        import logging

        logging.getLogger("musicsync.storage").warning(f"Database migration warning: {e}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Helper functions


def save_token(db: OrmSession, service: str, data: dict[str, Any]) -> None:
    payload = json.dumps(data)
    existing = db.get(Token, service)
    if existing:
        existing.data = payload
        existing.updated_at = datetime.now(UTC)
    else:
        db.add(Token(service=service, data=payload))
    db.commit()


def load_token(db: OrmSession, service: str) -> dict[str, Any] | None:
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
    tidal_id: str | None,
    tidal_name: str | None,
    confidence: float,
    resolved: bool,
    target_service: str = "tidal",
) -> None:
    """Upsert artist mapping. For backwards compat, tidal_id/tidal_name params are kept.

    For TIDAL: pass tidal_id/tidal_name and they'll be stored in both legacy and target_ columns.
    For Apple Music: pass target_service='apple', set tidal_id=None, and use separate function or
    update target_id/target_name directly on the returned object.
    """
    m = (
        db.query(ArtistMap)
        .filter(ArtistMap.spotify_id == spotify_id, ArtistMap.target_service == target_service)
        .first()
    )
    if not m:
        m = ArtistMap(spotify_id=spotify_id, spotify_name=spotify_name, target_service=target_service)
        db.add(m)
    # Store in legacy TIDAL columns
    m.tidal_id = tidal_id
    m.tidal_name = tidal_name
    # Also store in generic target_ columns
    m.target_id = tidal_id if target_service == "tidal" else m.target_id
    m.target_name = tidal_name if target_service == "tidal" else m.target_name
    m.confidence = confidence
    m.resolved = resolved
    m.last_synced_at = datetime.now(UTC) if (tidal_id or m.target_id) else None
    db.commit()


def update_artist_genres(
    db: OrmSession, spotify_id: str, genres: list[str] | None, target_service: str = "tidal"
) -> None:
    """Update stored genres for a Spotify artist.

    Genres are stored as a JSON-encoded list on ArtistMap.genres_json. This does not
    alter match status; it's safe to call independently of syncing.
    """
    m = (
        db.query(ArtistMap)
        .filter(ArtistMap.spotify_id == spotify_id, ArtistMap.target_service == target_service)
        .first()
    )
    if not m:
        return
    try:
        payload = json.dumps(list(genres or []))
    except Exception:
        payload = json.dumps([])
    m.genres_json = payload
    db.commit()


def add_pending_resolution(
    db: OrmSession,
    spotify_id: str,
    spotify_name: str,
    candidates: Sequence[dict[str, Any]],
    target_service: str = "tidal",
) -> None:
    # Remove any existing pending for this spotify_id+target_service to keep latest
    db.query(PendingResolution).filter(
        PendingResolution.spotify_id == spotify_id, PendingResolution.target_service == target_service
    ).delete()
    db.add(
        PendingResolution(
            spotify_id=spotify_id,
            spotify_name=spotify_name,
            target_service=target_service,
            candidates_json=json.dumps(candidates),
        )
    )
    db.commit()


def get_pending(db: OrmSession) -> list[dict[str, Any]]:
    rows = db.query(PendingResolution).order_by(PendingResolution.created_at.asc()).all()
    out: list[dict[str, Any]] = []
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


def delete_pending_by_spotify_id(db: OrmSession, spotify_id: str, target_service: str | None = None) -> None:
    """Delete pending artist resolution rows for a Spotify artist id.

    When target_service is provided, deletion is scoped to that service only.
    """
    q = db.query(PendingResolution).filter(PendingResolution.spotify_id == spotify_id)
    if target_service:
        q = q.filter(PendingResolution.target_service == target_service)
    q.delete()
    db.commit()


def cleanup_pending_for_resolved(db: OrmSession, target_service: str | None = None) -> int:
    """Remove pending rows whose artists are already resolved in ArtistMap.

    Returns the number of rows deleted (best-effort estimate).
    """
    subq = db.query(ArtistMap.spotify_id).filter(ArtistMap.resolved)
    q = db.query(PendingResolution).filter(PendingResolution.spotify_id.in_(subq))
    if target_service:
        subq = subq.filter(ArtistMap.target_service == target_service)
        q = q.filter(PendingResolution.target_service == target_service)
    deleted = q.delete(synchronize_session=False)
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
    spotify_artist_id: str | None,
    tidal_id: str | None,
    tidal_title: str | None,
    tidal_artist: str | None,
    tidal_artist_id: str | None,
    isrc: str | None,
    spotify_duration: int | None,
    tidal_duration: int | None,
    confidence: float,
    resolved: bool,
    target_service: str = "tidal",
) -> None:
    """Upsert track mapping. For backwards compat, tidal_* params are kept."""
    m = db.query(TrackMap).filter(TrackMap.spotify_id == spotify_id, TrackMap.target_service == target_service).first()
    if not m:
        m = TrackMap(
            spotify_id=spotify_id,
            spotify_title=spotify_title,
            spotify_artist=spotify_artist,
            target_service=target_service,
        )
        db.add(m)
    # Store in legacy TIDAL columns
    m.tidal_id = tidal_id
    m.tidal_title = tidal_title
    m.tidal_artist = tidal_artist
    m.tidal_artist_id = tidal_artist_id
    m.tidal_duration = tidal_duration
    # Always store in generic target_ columns (tidal_* params are used generically)
    m.target_id = tidal_id
    m.target_title = tidal_title
    m.target_artist = tidal_artist
    m.target_artist_id = tidal_artist_id
    m.target_duration = tidal_duration
    m.spotify_artist_id = spotify_artist_id
    m.isrc = isrc
    m.spotify_duration = spotify_duration
    m.confidence = confidence
    m.resolved = resolved
    m.last_synced_at = datetime.now(UTC) if (tidal_id or m.target_id) else None
    db.commit()


def add_pending_track_resolution(
    db: OrmSession,
    spotify_id: str,
    title: str,
    artist: str,
    isrc: str | None,
    candidates: Sequence[dict[str, Any]],
    target_service: str = "tidal",
) -> None:
    db.query(PendingTrackResolution).filter(
        PendingTrackResolution.spotify_id == spotify_id, PendingTrackResolution.target_service == target_service
    ).delete()
    db.add(
        PendingTrackResolution(
            spotify_id=spotify_id,
            spotify_title=title,
            spotify_artist=artist,
            target_service=target_service,
            isrc=isrc,
            candidates_json=json.dumps(candidates),
        )
    )
    db.commit()


def get_pending_tracks(db: OrmSession) -> list[dict[str, Any]]:
    rows = db.query(PendingTrackResolution).order_by(PendingTrackResolution.created_at.asc()).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "spotify_id": r.spotify_id,
                "spotify_title": r.spotify_title,
                "spotify_artist": r.spotify_artist,
                "isrc": r.isrc,
                "target_service": r.target_service,
                "candidates": json.loads(str(r.candidates_json)),
                "created_at": r.created_at.isoformat(),
            }
        )
    return out


def delete_pending_track(db: OrmSession, pending_id: int) -> None:
    db.query(PendingTrackResolution).filter(PendingTrackResolution.id == pending_id).delete()
    db.commit()


def delete_pending_track_by_spotify_id(db: OrmSession, spotify_id: str, target_service: str | None = None) -> None:
    q = db.query(PendingTrackResolution).filter(PendingTrackResolution.spotify_id == spotify_id)
    if target_service:
        q = q.filter(PendingTrackResolution.target_service == target_service)
    q.delete()
    db.commit()


def cleanup_pending_tracks_for_resolved(db: OrmSession, target_service: str | None = None) -> int:
    subq = db.query(TrackMap.spotify_id).filter(TrackMap.resolved)
    q = db.query(PendingTrackResolution).filter(PendingTrackResolution.spotify_id.in_(subq))
    if target_service:
        subq = subq.filter(TrackMap.target_service == target_service)
        q = q.filter(PendingTrackResolution.target_service == target_service)
    deleted = q.delete(synchronize_session=False)
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
    isrc: str | None,
    source: str,
    target_service: str = "tidal",
) -> None:
    """Add track sync event. tidal_* params are used generically for any service."""
    db.add(
        TrackSyncEvent(
            spotify_id=spotify_id,
            spotify_title=spotify_title,
            spotify_artist=spotify_artist,
            target_service=target_service,
            tidal_id=tidal_id,
            tidal_title=tidal_title,
            tidal_artist=tidal_artist,
            target_id=tidal_id,
            target_title=tidal_title,
            target_artist=tidal_artist,
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
    target_service: str = "tidal",
    target_id: str | None = None,
    target_name: str | None = None,
) -> None:
    """Upsert playlist mapping. For backwards compat, tidal_* params are kept."""
    m = (
        db.query(PlaylistMap)
        .filter(PlaylistMap.spotify_id == spotify_id, PlaylistMap.target_service == target_service)
        .first()
    )
    if not m:
        m = PlaylistMap(spotify_id=spotify_id, spotify_name=spotify_name, target_service=target_service)
        db.add(m)
    # Store in legacy TIDAL columns if TIDAL service
    if target_service == "tidal":
        m.tidal_id = tidal_id
        m.tidal_name = tidal_name
        m.target_id = tidal_id
        m.target_name = tidal_name
    else:
        # For non-TIDAL services (Apple), use target_* params
        m.target_id = target_id
        m.target_name = target_name
    m.last_synced_at = datetime.now(UTC) if (m.tidal_id or m.target_id) else None
    db.commit()


def get_playlist_map(db: OrmSession, spotify_id: str, target_service: str = "tidal") -> dict[str, Any] | None:
    m = (
        db.query(PlaylistMap)
        .filter(PlaylistMap.spotify_id == spotify_id, PlaylistMap.target_service == target_service)
        .first()
    )
    if not m:
        return None
    return {
        "spotify_id": m.spotify_id,
        "spotify_name": m.spotify_name,
        "tidal_id": m.tidal_id,
        "tidal_name": m.tidal_name,
        "target_id": m.target_id,
        "target_name": m.target_name,
        "target_service": m.target_service,
        "last_synced_at": m.last_synced_at.isoformat() if m.last_synced_at else None,
    }


def replace_playlist_tracks(
    db: OrmSession,
    playlist_spotify_id: str,
    entries: list[dict[str, Any]],
    target_service: str = "tidal",
) -> None:
    """Replace the stored track snapshot for a playlist with the given ordered entries.

    Each entry should include: position (int, 1-based), spotify_track_id, spotify_title, spotify_artist,
    and optional tidal_track_id, tidal_title, tidal_artist, target_track_id, target_title, target_artist, isrc.
    """
    from sqlalchemy import delete

    db.execute(
        delete(PlaylistTrack).where(
            PlaylistTrack.playlist_spotify_id == playlist_spotify_id,
            PlaylistTrack.target_service == target_service,
        )
    )
    now = datetime.now(UTC)
    for e in entries:
        dur_raw = e.get("spotify_duration")
        sd: int | None = int(dur_raw) if dur_raw is not None else None
        db.add(
            PlaylistTrack(
                playlist_spotify_id=playlist_spotify_id,
                target_service=target_service,
                position=int(e.get("position", 0) or 0),
                spotify_track_id=str(e.get("spotify_track_id")),
                spotify_title=str(e.get("spotify_title") or ""),
                spotify_artist=str(e.get("spotify_artist") or ""),
                tidal_track_id=(str(e.get("tidal_track_id")) if e.get("tidal_track_id") else None),
                tidal_title=(str(e.get("tidal_title")) if e.get("tidal_title") else None),
                tidal_artist=(str(e.get("tidal_artist")) if e.get("tidal_artist") else None),
                target_track_id=(str(e.get("target_track_id")) if e.get("target_track_id") else None),
                target_title=(str(e.get("target_title")) if e.get("target_title") else None),
                target_artist=(str(e.get("target_artist")) if e.get("target_artist") else None),
                isrc=(str(e.get("isrc")) if e.get("isrc") else None),
                spotify_duration=sd,
                last_synced_at=now,
            )
        )
    db.commit()


def list_playlist_tracks(
    db: OrmSession,
    playlist_spotify_id: str,
    *,
    target_service: str = "tidal",
    search: str | None = None,
    sort: str = "position",
    order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    from sqlalchemy import func, or_

    q = db.query(PlaylistTrack).filter(
        PlaylistTrack.playlist_spotify_id == playlist_spotify_id,
        PlaylistTrack.target_service == target_service,
    )
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
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "position": int(getattr(r, "position", 0) or 0),
                "spotify_track_id": r.spotify_track_id,
                "spotify_title": r.spotify_title,
                "spotify_artist": r.spotify_artist,
                "target_service": r.target_service,
                "target_track_id": r.target_track_id,
                "target_title": r.target_title,
                "target_artist": r.target_artist,
                "tidal_track_id": r.tidal_track_id,
                "tidal_title": r.tidal_title,
                "tidal_artist": r.tidal_artist,
                "isrc": r.isrc,
                "duration": int(getattr(r, "spotify_duration", 0) or 0),
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
            }
        )
    return out, total


def get_playlist_stats(db: OrmSession, playlist_spotify_id: str, *, target_service: str = "tidal") -> dict[str, Any]:
    """Return aggregate stats for a playlist: track count and total duration (seconds)."""
    from sqlalchemy import func

    q = db.query(
        func.count(PlaylistTrack.id),
        func.coalesce(func.sum(PlaylistTrack.spotify_duration), 0),
    ).filter(
        PlaylistTrack.playlist_spotify_id == playlist_spotify_id,
        PlaylistTrack.target_service == target_service,
    )
    count_val, total_dur = q.one()
    return {"count": int(count_val or 0), "total_seconds": int(total_dur or 0)}


# Export helpers (for backup)


def export_artists(db: OrmSession) -> list[dict[str, Any]]:
    rows = db.query(ArtistMap).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            genres = json.loads(str(getattr(r, "genres_json", "") or "[]"))
        except Exception:
            genres = []
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "tidal_id": r.tidal_id,
                "tidal_name": r.tidal_name,
                "genres": genres,
                "confidence": float(r.confidence),
                "resolved": bool(r.resolved),
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
            }
        )
    return out


def export_tracks(db: OrmSession) -> list[dict[str, Any]]:
    rows = db.query(TrackMap).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_title": r.spotify_title,
                "spotify_artist": r.spotify_artist,
                "spotify_artist_id": r.spotify_artist_id,
                "tidal_id": r.tidal_id,
                "tidal_title": r.tidal_title,
                "tidal_artist": r.tidal_artist,
                "tidal_artist_id": r.tidal_artist_id,
                "isrc": r.isrc,
                "spotify_duration": r.spotify_duration,
                "tidal_duration": r.tidal_duration,
                "confidence": float(r.confidence),
                "resolved": bool(r.resolved),
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
            }
        )
    return out


def export_playlists(db: OrmSession) -> list[dict[str, Any]]:
    rows = db.query(PlaylistMap).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "tidal_id": r.tidal_id,
                "tidal_name": r.tidal_name,
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
            }
        )
    return out


# Library listing helpers (for UI)


def list_synced_artists(
    db: OrmSession,
    *,
    search: str | None = None,
    genre: str | None = None,
    sort: str = "last_synced_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
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
    out: list[dict[str, Any]] = []
    # Build output with genres parsed
    enriched: list[dict[str, Any]] = []
    for r in rows:
        try:
            genres = json.loads(str(getattr(r, "genres_json", "") or "[]"))
        except Exception:
            genres = []
        enriched.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "tidal_id": r.tidal_id,
                "tidal_name": r.tidal_name,
                "genres": genres,
                "confidence": float(r.confidence),
                "resolved": bool(r.resolved),
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
            }
        )
    # If in-memory sort by genre requested, apply now and paginate
    if in_memory_sort:

        def gkey(item: dict[str, Any]) -> str:
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
    search: str | None = None,
) -> list[tuple[str, int]]:
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
    counts: dict[str, int] = {}
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
    search: str | None = None,
    sort: str = "last_synced_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    from sqlalchemy import func, or_

    q = db.query(TrackMap).filter(TrackMap.target_id.isnot(None))
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
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "spotify_id": r.spotify_id,
                "spotify_title": r.spotify_title,
                "spotify_artist": r.spotify_artist,
                "spotify_artist_id": r.spotify_artist_id,
                "target_service": r.target_service,
                "target_id": r.target_id,
                "target_title": r.target_title,
                "target_artist": r.target_artist,
                "target_artist_id": r.target_artist_id,
                "target_duration": r.target_duration,
                "tidal_id": r.tidal_id,
                "tidal_title": r.tidal_title,
                "tidal_artist": r.tidal_artist,
                "tidal_artist_id": r.tidal_artist_id,
                "isrc": r.isrc,
                "spotify_duration": r.spotify_duration,
                "tidal_duration": r.tidal_duration,
                "confidence": float(r.confidence),
                "resolved": bool(r.resolved),
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
            }
        )
    return out, total


def list_synced_playlists(
    db: OrmSession,
    *,
    search: str | None = None,
    sort: str = "last_synced_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    from sqlalchemy import func, or_

    # Build subquery to get all playlist mappings grouped by spotify_id
    q = db.query(PlaylistMap)
    if search:
        s = f"%{search.lower()}%"
        q = q.filter(
            or_(
                func.lower(PlaylistMap.spotify_name).like(s),
                func.lower(PlaylistMap.tidal_name).like(s),
                func.lower(PlaylistMap.target_name).like(s),
                func.lower(PlaylistMap.spotify_id).like(s),
                func.lower(PlaylistMap.tidal_id).like(s),
                func.lower(PlaylistMap.target_id).like(s),
            )
        )

    # Get all rows first, then group by spotify_id
    all_rows = q.all()

    # Group by spotify_id
    grouped: dict[str, dict[str, Any]] = {}
    for r in all_rows:
        if r.spotify_id not in grouped:
            grouped[r.spotify_id] = {
                "spotify_id": r.spotify_id,
                "spotify_name": r.spotify_name,
                "tidal_id": None,
                "tidal_name": None,
                "apple_id": None,
                "apple_name": None,
                "last_synced_at": None,
            }

        item = grouped[r.spotify_id]
        # Update last_synced_at to latest
        if r.last_synced_at:
            if not item["last_synced_at"] or r.last_synced_at > item["last_synced_at"]:
                item["last_synced_at"] = r.last_synced_at

        # Populate service-specific fields
        if r.target_service == "tidal":
            item["tidal_id"] = r.tidal_id or r.target_id
            item["tidal_name"] = r.tidal_name or r.target_name
        elif r.target_service == "apple":
            item["apple_id"] = r.target_id
            item["apple_name"] = r.target_name

    # Convert to list
    items = list(grouped.values())
    total = len(items)

    # Sort
    sort_key_map = {
        "last_synced_at": lambda x: x.get("last_synced_at") or datetime.min,
        "spotify_name": lambda x: (x.get("spotify_name") or "").lower(),
        "tidal_name": lambda x: (x.get("tidal_name") or "").lower(),
    }
    sort_key = sort_key_map.get(sort, sort_key_map["last_synced_at"])
    items.sort(key=sort_key, reverse=(order.lower() == "desc"))

    # Paginate
    if page_size == 0:
        pass
    else:
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 25
        offset = (page - 1) * page_size
        items = items[offset : offset + page_size]

    # Format last_synced_at as ISO strings
    for item in items:
        if item.get("last_synced_at"):
            item["last_synced_at"] = item["last_synced_at"].isoformat()

    return items, total
