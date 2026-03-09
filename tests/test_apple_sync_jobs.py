"""Unit tests for Apple sync background jobs using stubbed clients (no real accounts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main
from app.apple_client import AddResult
from app.storage import Base

# Setup in-memory sqlite for tests
engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _run_likes_job(job_id: str) -> None:
    runner = getattr(main, "_run_sync_apple_likes_job")
    runner(job_id)


def _reset_job(job_id: str) -> None:
    jobs_lock = getattr(main, "_jobs_lock")
    jobs = getattr(main, "_jobs")
    with jobs_lock:
        jobs.pop(job_id, None)


def _job_status(job_id: str) -> dict[str, Any]:
    jobs_lock = getattr(main, "_jobs_lock")
    jobs = getattr(main, "_jobs")
    with jobs_lock:
        return dict(jobs.get(job_id) or {})


def _get_testing_session():
    return TestingSessionLocal()


class StubSpotifyPager:
    """Simple stub that returns predefined pages for Spotify calls."""

    def __init__(self, pages: list[dict[str, Any]]):
        self._pages = pages
        self._idx = 0

    def __call__(self, _callable):
        if self._idx < len(self._pages):
            page = self._pages[self._idx]
            self._idx += 1
            return page
        return {"items": [], "next": None}


@dataclass
class StubAppleClient:
    """Stub implementation for Apple sync tests."""

    search_by_isrc_map: dict[str, dict[str, Any]]
    existing_library_ids: set[str]

    def __post_init__(self):
        self.add_calls: list[list[str]] = []
        self.playlist_adds: list[tuple[str, list[str]]] = []

    def get_storefront(self):
        return "us"

    def list_library_song_ids(self):
        return set(self.existing_library_ids)

    def search_by_isrc(self, isrc: str, storefront: str | None = None):
        _ = storefront
        return self.search_by_isrc_map.get(isrc)

    def search_track(
        self,
        *,
        title: str,
        artist: str,
        duration_s: int | None = None,
        storefront: str | None = None,
        limit: int = 10,
    ):
        _ = (title, artist, duration_s, storefront, limit)
        return []

    def add_tracks_to_library(self, track_ids: list[str]):
        self.add_calls.append(list(track_ids))
        return AddResult(requested=len(track_ids), succeeded=len(track_ids), failed=0)

    def add_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]) -> AddResult:
        self.playlist_adds.append((playlist_id, list(track_ids)))
        return AddResult(requested=len(track_ids), succeeded=len(track_ids), failed=0)

    def ensure_playlist(self, name: str, description: str | None = None) -> dict[str, Any]:
        return {"id": "mock_playlist_id", "name": name}

    def list_library_playlists(self) -> list[dict[str, Any]]:
        return []


@pytest.mark.unit
class TestAppleLikesSyncJob:
    def _patch_common(self, monkeypatch: pytest.MonkeyPatch):
        def _session_local():
            return _get_testing_session()

        def _spotify_client():
            return object()

        def _cleanup(_db, target_service=None):
            _ = target_service
            return 0

        def _noop(*args, **kwargs):
            _ = (args, kwargs)
            return None

        monkeypatch.setattr(main, "SessionLocal", _session_local)
        monkeypatch.setattr(main, "get_spotify_client", _spotify_client)
        monkeypatch.setattr(main, "cleanup_pending_tracks_for_resolved", _cleanup)
        monkeypatch.setattr(main, "upsert_track_map", _noop)
        monkeypatch.setattr(main, "delete_pending_track_by_spotify_id", _noop)
        monkeypatch.setattr(main, "add_track_sync_event", _noop)

    def test_counts_already_in_library_and_only_adds_missing(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_common(monkeypatch)

        spotify_pages = [
            {
                "items": [
                    {
                        "track": {
                            "id": "sp1",
                            "name": "Song One",
                            "artists": [{"name": "Artist A", "id": "art1"}],
                            "duration_ms": 200000,
                            "external_ids": {"isrc": "ISRC0001"},
                        }
                    },
                    {
                        "track": {
                            "id": "sp2",
                            "name": "Song Two",
                            "artists": [{"name": "Artist B", "id": "art2"}],
                            "duration_ms": 210000,
                            "external_ids": {"isrc": "ISRC0002"},
                        }
                    },
                ],
                "next": None,
            }
        ]
        monkeypatch.setattr(main, "call_spotify", StubSpotifyPager(spotify_pages))

        apple = StubAppleClient(
            search_by_isrc_map={
                "ISRC0001": {"id": "am1", "name": "Song One", "artists": ["Artist A"], "duration_ms": 200000},
                "ISRC0002": {"id": "am2", "name": "Song Two", "artists": ["Artist B"], "duration_ms": 210000},
            },
            existing_library_ids={"am1"},
        )

        def _apple_client():
            return apple

        def _noop_pending(*args, **kwargs):
            _ = (args, kwargs)
            return None

        monkeypatch.setattr(main, "get_apple_client", _apple_client)
        monkeypatch.setattr(main, "add_pending_track_resolution", _noop_pending)

        job_id = "job-apple-likes-1"
        _reset_job(job_id)

        _run_likes_job(job_id)

        status = _job_status(job_id)

        assert status["state"] == "done"
        assert status["total"] == 2
        assert status["processed"] == 2
        assert status["added_to_library"] == 1
        assert status["already_in_library"] == 1
        assert status["pending_count"] == 0
        assert apple.add_calls == [["am2"]]

    def test_unmatched_track_goes_to_pending(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_common(monkeypatch)

        spotify_pages = [
            {
                "items": [
                    {
                        "track": {
                            "id": "sp3",
                            "name": "Unknown Song",
                            "artists": [{"name": "Unknown Artist", "id": "art3"}],
                            "duration_ms": 180000,
                            "external_ids": {},
                        }
                    }
                ],
                "next": None,
            }
        ]
        monkeypatch.setattr(main, "call_spotify", StubSpotifyPager(spotify_pages))

        apple = StubAppleClient(search_by_isrc_map={}, existing_library_ids=set())

        def _apple_client():
            return apple

        monkeypatch.setattr(main, "get_apple_client", _apple_client)

        pending_calls: list[tuple[Any, ...]] = []

        def _record_pending(*args, **kwargs):
            _ = kwargs
            pending_calls.append(args)

        monkeypatch.setattr(main, "add_pending_track_resolution", _record_pending)

        job_id = "job-apple-likes-2"
        _reset_job(job_id)

        _run_likes_job(job_id)

        status = _job_status(job_id)

        assert status["state"] == "done"
        assert status["total"] == 1
        assert status["processed"] == 1
        assert status["added_to_library"] == 0
        assert status["already_in_library"] == 0
        assert status["pending_count"] == 1
        assert pending_calls, "Expected unmatched track to be queued for pending resolution"
        assert apple.add_calls == []

    def test_unmatched_track_keeps_ranked_candidates_for_pending(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_common(monkeypatch)

        spotify_pages = [
            {
                "items": [
                    {
                        "track": {
                            "id": "sp4",
                            "name": "Almost Match",
                            "artists": [{"name": "Artist C", "id": "art4"}],
                            "duration_ms": 180000,
                            "external_ids": {},
                        }
                    }
                ],
                "next": None,
            }
        ]
        monkeypatch.setattr(main, "call_spotify", StubSpotifyPager(spotify_pages))

        apple = StubAppleClient(search_by_isrc_map={}, existing_library_ids=set())
        apple.search_track = lambda **kwargs: [  # type: ignore[method-assign]
            {
                "id": "am-candidate-1",
                "name": "Totally Different Song",
                "artists": ["Unrelated Artist"],
                "duration_ms": 120000,
                "isrc": None,
            }
        ]

        monkeypatch.setattr(main, "get_apple_client", lambda: apple)

        pending_payloads: list[list[dict[str, Any]]] = []

        def _record_pending(db, spotify_id, title, artist, isrc, candidates, target_service="tidal"):
            _ = (db, spotify_id, title, artist, isrc, target_service)
            pending_payloads.append(list(candidates))

        monkeypatch.setattr(main, "add_pending_track_resolution", _record_pending)

        job_id = "job-apple-likes-3"
        _reset_job(job_id)

        _run_likes_job(job_id)

        status = _job_status(job_id)
        assert status["state"] == "done"
        assert status["pending_count"] == 1
        assert pending_payloads
        assert pending_payloads[0]
        assert pending_payloads[0][0]["id"] == "am-candidate-1"
        assert float(pending_payloads[0][0].get("score", 0)) > 0


def _run_playlists_job(job_id: str) -> None:
    runner = getattr(main, "_run_sync_apple_playlists_job")
    runner(job_id)


@pytest.mark.unit
class TestApplePlaylistsSyncJob:
    def _patch_common(self, monkeypatch: pytest.MonkeyPatch):
        def _session_local():
            return _get_testing_session()

        def _spotify_client():
            return object()

        def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(main, "SessionLocal", _session_local)
        monkeypatch.setattr(main, "get_spotify_client", _spotify_client)

    def test_syncs_playlists_and_adds_missing_tracks(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_common(monkeypatch)

        # Mock SP me() and playlists and tracks
        me_page = {"id": "my_user"}
        playlists_pages = [
            {"items": [{"id": "pl1", "name": "My Playlist 1", "owner": {"id": "my_user"}}], "next": None}
        ]
        pl_tracks_pages = [
            {
                "items": [
                    {
                        "track": {
                            "id": "t1",
                            "name": "Track 1",
                            "artists": [{"name": "Artist 1", "id": "a1"}],
                            "external_ids": {"isrc": "ISRC1"},
                            "duration_ms": 200000,
                        }
                    }
                ],
                "next": None,
            }
        ]

        def _call_spotify(func):
            class DummySP:
                def me(self):
                    return me_page

                def current_user_playlists(self, limit, offset):
                    return playlists_pages[0]

                def playlist_tracks(self, pl_id, limit, offset):
                    return pl_tracks_pages[0]

            return func(DummySP())

        monkeypatch.setattr(main, "call_spotify", _call_spotify)

        apple = StubAppleClient(
            search_by_isrc_map={"ISRC1": {"id": "am1", "name": "Track 1", "artists": [{"name": "Artist 1"}]}},
            existing_library_ids=set(),
        )
        # Override ensure_playlist ID specifically for this test
        apple.ensure_playlist = lambda name, desc: {"id": "apl1", "name": name}  # type: ignore

        def _apple_client():
            return apple

        monkeypatch.setattr(main, "get_apple_client", _apple_client)

        job_id = "job-apple-playlists-1"
        _reset_job(job_id)

        _run_playlists_job(job_id)

        status = _job_status(job_id)

        assert status["state"] == "done"
        assert status["total"] == 1
        assert status["processed"] == 1
        assert status["created"] == 1
        assert status["updated"] == 0
        assert apple.playlist_adds == [("apl1", ["am1"])]


def _run_followed_artists_job(job_id: str, limit: int = 0, rebuild: bool = False) -> None:
    runner = getattr(main, "_run_sync_apple_followed_artists_playlist_job")
    runner(job_id, limit, rebuild)


@pytest.mark.unit
class TestAppleFollowedArtistsPlaylistSyncJob:
    def _patch_common(self, monkeypatch: pytest.MonkeyPatch):
        def _session_local():
            return _get_testing_session()

        def _spotify_client():
            return object()

        monkeypatch.setattr(main, "SessionLocal", _session_local)
        monkeypatch.setattr(main, "get_spotify_client", _spotify_client)

    def test_creates_fallback_playlist_with_top_tracks(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_common(monkeypatch)

        # Mock SP me() and followed artists and top tracks
        me_page = {"country": "US"}
        artists_pages = [{"artists": {"items": [{"id": "art1", "name": "Artist A"}], "cursors": {"after": None}}}]
        top_tracks_pages = [
            {
                "tracks": [
                    {
                        "id": "t1",
                        "name": "Top Track 1",
                        "artists": [{"name": "Artist A"}],
                        "external_ids": {"isrc": "ISRC2"},
                        "duration_ms": 200000,
                    }
                ]
            }
        ]

        def _call_spotify(func):
            class DummySP:
                def me(self):
                    return me_page

                def current_user_followed_artists(self, limit, after=None):
                    return artists_pages[0]

                def artist_top_tracks(self, artist_id, country):
                    return top_tracks_pages[0]

            return func(DummySP())

        monkeypatch.setattr(main, "call_spotify", _call_spotify)

        apple = StubAppleClient(
            search_by_isrc_map={"ISRC2": {"id": "am2", "name": "Top Track 1", "artists": [{"name": "Artist A"}]}},
            existing_library_ids=set(),
        )
        apple.ensure_playlist = lambda name, desc: {"id": "apl_fallback", "name": name}  # type: ignore

        def _apple_client():
            return apple

        monkeypatch.setattr(main, "get_apple_client", _apple_client)

        job_id = "job-apple-fallback-1"
        _reset_job(job_id)

        _run_followed_artists_job(job_id)

        status = _job_status(job_id)

        assert status["state"] == "done"
        assert status["total"] == 1
        assert status["processed"] == 1
        assert status["mapped"] == 1
        assert status["unavailable"] == 0
        assert status["added_to_apple"] == 1
        assert status["created"] == 1
        assert apple.playlist_adds == [("apl_fallback", ["am2"])]
