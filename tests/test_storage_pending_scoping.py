from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.storage import (
    ArtistMap,
    Base,
    PendingResolution,
    PendingTrackResolution,
    TrackMap,
    cleanup_pending_for_resolved,
    cleanup_pending_tracks_for_resolved,
    delete_pending_by_spotify_id,
    delete_pending_track_by_spotify_id,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def test_cleanup_pending_tracks_scoped_by_service():
    db = _make_session()
    try:
        db.add(
            TrackMap(
                spotify_id="sp_track_1",
                target_service="tidal",
                spotify_title="Song",
                spotify_artist="Artist",
                resolved=True,
            )
        )
        db.add(
            PendingTrackResolution(
                spotify_id="sp_track_1",
                spotify_title="Song",
                spotify_artist="Artist",
                target_service="tidal",
                isrc=None,
                candidates_json="[]",
            )
        )
        db.add(
            PendingTrackResolution(
                spotify_id="sp_track_1",
                spotify_title="Song",
                spotify_artist="Artist",
                target_service="apple",
                isrc=None,
                candidates_json="[]",
            )
        )
        db.commit()

        removed = cleanup_pending_tracks_for_resolved(db, target_service="tidal")
        assert removed == 1

        remaining = db.query(PendingTrackResolution).all()
        assert len(remaining) == 1
        assert remaining[0].target_service == "apple"
    finally:
        db.close()


def test_delete_pending_tracks_scoped_by_service():
    db = _make_session()
    try:
        db.add(
            PendingTrackResolution(
                spotify_id="sp_track_2",
                spotify_title="Song",
                spotify_artist="Artist",
                target_service="tidal",
                isrc=None,
                candidates_json="[]",
            )
        )
        db.add(
            PendingTrackResolution(
                spotify_id="sp_track_2",
                spotify_title="Song",
                spotify_artist="Artist",
                target_service="apple",
                isrc=None,
                candidates_json="[]",
            )
        )
        db.commit()

        delete_pending_track_by_spotify_id(db, "sp_track_2", target_service="apple")

        remaining = db.query(PendingTrackResolution).all()
        assert len(remaining) == 1
        assert remaining[0].target_service == "tidal"
    finally:
        db.close()


def test_cleanup_pending_artists_scoped_by_service():
    db = _make_session()
    try:
        db.add(
            ArtistMap(
                spotify_id="sp_artist_1",
                target_service="tidal",
                spotify_name="Artist",
                resolved=True,
            )
        )
        db.add(
            PendingResolution(
                spotify_id="sp_artist_1",
                spotify_name="Artist",
                target_service="tidal",
                candidates_json="[]",
            )
        )
        db.add(
            PendingResolution(
                spotify_id="sp_artist_1",
                spotify_name="Artist",
                target_service="apple",
                candidates_json="[]",
            )
        )
        db.commit()

        removed = cleanup_pending_for_resolved(db, target_service="tidal")
        assert removed == 1

        remaining = db.query(PendingResolution).all()
        assert len(remaining) == 1
        assert remaining[0].target_service == "apple"
    finally:
        db.close()


def test_delete_pending_artists_scoped_by_service():
    db = _make_session()
    try:
        db.add(
            PendingResolution(
                spotify_id="sp_artist_2",
                spotify_name="Artist",
                target_service="tidal",
                candidates_json="[]",
            )
        )
        db.add(
            PendingResolution(
                spotify_id="sp_artist_2",
                spotify_name="Artist",
                target_service="apple",
                candidates_json="[]",
            )
        )
        db.commit()

        delete_pending_by_spotify_id(db, "sp_artist_2", target_service="apple")

        remaining = db.query(PendingResolution).all()
        assert len(remaining) == 1
        assert remaining[0].target_service == "tidal"
    finally:
        db.close()
