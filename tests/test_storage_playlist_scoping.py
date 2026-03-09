"""Regression tests for service-scoped playlist snapshot reads/stats."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.storage import Base, get_playlist_stats, list_playlist_tracks, replace_playlist_tracks


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def test_list_playlist_tracks_filters_by_target_service():
    db = _make_session()
    try:
        spotify_id = "sp_pl_1"

        replace_playlist_tracks(
            db,
            spotify_id,
            [
                {
                    "position": 1,
                    "spotify_track_id": "sp_t_1",
                    "spotify_title": "Song A",
                    "spotify_artist": "Artist A",
                    "spotify_duration": 210,
                    "tidal_track_id": "td_1",
                    "tidal_title": "Song A",
                    "tidal_artist": "Artist A",
                }
            ],
            target_service="tidal",
        )

        replace_playlist_tracks(
            db,
            spotify_id,
            [
                {
                    "position": 1,
                    "spotify_track_id": "sp_t_1",
                    "spotify_title": "Song A",
                    "spotify_artist": "Artist A",
                    "spotify_duration": 210,
                    "target_track_id": "am_1",
                    "target_title": "Song A",
                    "target_artist": "Artist A",
                },
                {
                    "position": 2,
                    "spotify_track_id": "sp_t_2",
                    "spotify_title": "Song B",
                    "spotify_artist": "Artist B",
                    "spotify_duration": 180,
                    "target_track_id": "am_2",
                    "target_title": "Song B",
                    "target_artist": "Artist B",
                },
            ],
            target_service="apple",
        )

        tidal_rows, tidal_total = list_playlist_tracks(db, spotify_id, target_service="tidal", page_size=0)
        apple_rows, apple_total = list_playlist_tracks(db, spotify_id, target_service="apple", page_size=0)

        assert tidal_total == 1
        assert len(tidal_rows) == 1
        assert tidal_rows[0]["target_service"] == "tidal"

        assert apple_total == 2
        assert len(apple_rows) == 2
        assert all(r["target_service"] == "apple" for r in apple_rows)
    finally:
        db.close()


def test_get_playlist_stats_filters_by_target_service():
    db = _make_session()
    try:
        spotify_id = "sp_pl_2"

        replace_playlist_tracks(
            db,
            spotify_id,
            [
                {
                    "position": 1,
                    "spotify_track_id": "sp_t_1",
                    "spotify_title": "Song A",
                    "spotify_artist": "Artist A",
                    "spotify_duration": 100,
                    "tidal_track_id": "td_1",
                },
                {
                    "position": 2,
                    "spotify_track_id": "sp_t_2",
                    "spotify_title": "Song B",
                    "spotify_artist": "Artist B",
                    "spotify_duration": 120,
                    "tidal_track_id": "td_2",
                },
            ],
            target_service="tidal",
        )

        replace_playlist_tracks(
            db,
            spotify_id,
            [
                {
                    "position": 1,
                    "spotify_track_id": "sp_t_1",
                    "spotify_title": "Song A",
                    "spotify_artist": "Artist A",
                    "spotify_duration": 100,
                    "target_track_id": "am_1",
                }
            ],
            target_service="apple",
        )

        tidal_stats = get_playlist_stats(db, spotify_id, target_service="tidal")
        apple_stats = get_playlist_stats(db, spotify_id, target_service="apple")

        assert tidal_stats == {"count": 2, "total_seconds": 220}
        assert apple_stats == {"count": 1, "total_seconds": 100}
    finally:
        db.close()
