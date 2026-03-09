from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
from fastapi.testclient import TestClient

import app.main as main


class _NoOpThread:
    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        # Intentionally do nothing to keep route tests deterministic.
        return None


def _clear_job(job_id: str) -> None:
    jobs_lock = getattr(main, "_jobs_lock")
    jobs = getattr(main, "_jobs")
    with jobs_lock:
        jobs.pop(job_id, None)


class _DummyDB:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def query(self, *args, **kwargs):
        _ = (args, kwargs)
        return self

    def filter(self, *args, **kwargs):
        _ = (args, kwargs)
        return self

    def first(self):
        return None


def test_start_and_status_apple_likes_job_pending(monkeypatch):
    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    client = TestClient(main.app)
    resp = client.post("/sync/apple/likes/start?limit=12")

    assert resp.status_code == 200
    payload = resp.json()
    assert "job_id" in payload

    job_id = payload["job_id"]
    try:
        status = client.get(f"/sync/apple/likes/status?job_id={job_id}")
        assert status.status_code == 200
        body = status.json()
        assert body["state"] == "pending"
        assert body["limit"] == 12
    finally:
        _clear_job(job_id)


def test_start_and_status_apple_playlists_job_pending(monkeypatch):
    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    client = TestClient(main.app)
    resp = client.post("/sync/apple/playlists/start?limit=7")

    assert resp.status_code == 200
    payload = resp.json()
    job_id = payload["job_id"]

    try:
        status = client.get(f"/sync/apple/playlists/status?job_id={job_id}")
        assert status.status_code == 200
        body = status.json()
        assert body["state"] == "pending"
        assert body["limit"] == 7
    finally:
        _clear_job(job_id)


def test_start_and_status_apple_followed_artists_job_pending(monkeypatch):
    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    client = TestClient(main.app)
    resp = client.post("/sync/apple/followed-artists-playlist/start?limit=5&rebuild=true")

    assert resp.status_code == 200
    payload = resp.json()
    job_id = payload["job_id"]

    try:
        status = client.get(f"/sync/apple/followed-artists-playlist/status?job_id={job_id}")
        assert status.status_code == 200
        body = status.json()
        assert body["state"] == "pending"
        assert body["limit"] == 5
        assert body["rebuild"] is True
    finally:
        _clear_job(job_id)


def test_apple_status_endpoint_returns_404_for_unknown_job():
    client = TestClient(main.app)
    resp = client.get("/sync/apple/likes/status?job_id=missing-job")
    assert resp.status_code == 404


def test_resolve_track_apple_path_adds_to_library_and_persists(monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", _DummyDB)

    pending_item = {
        "id": 123,
        "spotify_id": "sp_track_1",
        "spotify_title": "Track One",
        "spotify_artist": "Artist One",
        "isrc": "ISRC123",
        "target_service": "apple",
    }
    monkeypatch.setattr(main, "get_pending_tracks", lambda db: [pending_item])

    class _AppleClient:
        def __init__(self):
            self.calls: list[list[str]] = []

        def add_tracks_to_library(self, ids: list[str]):
            self.calls.append(list(ids))

    apple_client = _AppleClient()
    monkeypatch.setattr(main, "get_apple_client", lambda: apple_client)

    upsert_calls: list[dict[str, Any]] = []
    event_calls: list[dict[str, Any]] = []
    delete_calls: list[int] = []

    def _upsert(*args, **kwargs):
        _ = args
        upsert_calls.append(kwargs)

    def _event(*args, **kwargs):
        _ = args
        event_calls.append(kwargs)

    def _delete(*args, **kwargs):
        _ = kwargs
        delete_calls.append(args[1])

    monkeypatch.setattr(main, "upsert_track_map", _upsert)
    monkeypatch.setattr(main, "add_track_sync_event", _event)
    monkeypatch.setattr(main, "delete_pending_track", _delete)

    client = TestClient(main.app)
    resp = client.post(
        "/resolve-track/123",
        data={
            "target_service": "apple",
            "target_id": "am_track_1",
            "target_title": "Apple Track One",
            "target_artist": "Apple Artist One",
            "target_artist_id": "",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert apple_client.calls == [["am_track_1"]]
    assert upsert_calls and upsert_calls[0]["target_service"] == "apple"
    assert event_calls and event_calls[0]["target_service"] == "apple"
    assert delete_calls == [123]


def test_resolve_track_rejects_unsupported_service(monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", _DummyDB)

    pending_item = {
        "id": 55,
        "spotify_id": "sp_track_2",
        "spotify_title": "Track Two",
        "spotify_artist": "Artist Two",
        "isrc": None,
        "target_service": "unsupported",
    }
    monkeypatch.setattr(main, "get_pending_tracks", lambda db: [pending_item])

    client = TestClient(main.app)
    resp = client.post(
        "/resolve-track/55",
        data={
            "target_service": "unsupported",
            "target_id": "x",
            "target_title": "x",
            "target_artist": "x",
        },
    )

    assert resp.status_code == 400
    assert "Unsupported service" in resp.json()["detail"]


def test_auth_apple_developer_token_success(monkeypatch):
    monkeypatch.setattr(main, "APPLE_ENABLED", True)

    class _AppleClient:
        def get_developer_token(self):
            return "dev_token_123"

    def _get_apple_client():
        return _AppleClient()

    monkeypatch.setattr(main, "get_apple_client", _get_apple_client)

    client = TestClient(main.app)
    resp = client.get("/auth/apple/developer-token")

    assert resp.status_code == 200
    assert resp.json()["token"] == "dev_token_123"


def test_auth_apple_token_success(monkeypatch):
    monkeypatch.setattr(main, "APPLE_ENABLED", True)

    class _AppleClient:
        def __init__(self):
            self.saved_token: str | None = None

        def set_music_user_token(self, token: str):
            self.saved_token = token

        def get_storefront(self):
            return "us"

    apple_client = _AppleClient()
    monkeypatch.setattr(main, "get_apple_client", lambda: apple_client)

    client = TestClient(main.app)
    resp = client.post("/auth/apple/token", json={"music_user_token": "user_tok_abc"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["storefront"] == "us"
    assert apple_client.saved_token == "user_tok_abc"


def test_auth_apple_token_missing_token_returns_400(monkeypatch):
    monkeypatch.setattr(main, "APPLE_ENABLED", True)

    def _get_apple_client():
        return object()

    monkeypatch.setattr(main, "get_apple_client", _get_apple_client)

    client = TestClient(main.app)
    resp = client.post("/auth/apple/token", json={})

    assert resp.status_code == 400
    assert "Missing music_user_token" in resp.json()["detail"]


def test_pending_tracks_page_shows_apple_candidate_metadata(monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", _DummyDB)

    pending_item = {
        "id": 77,
        "spotify_id": "sp_track_ui",
        "spotify_title": "My Song",
        "spotify_artist": "My Artist",
        "isrc": "USABC1234567",
        "target_service": "apple",
        "candidates": [
            {
                "id": "am_track_77",
                "title": "My Song",
                "artists": ["My Artist"],
                "duration": 201,
                "score": 97.0,
            }
        ],
    }
    monkeypatch.setattr(main, "get_pending_tracks", lambda db: [pending_item])

    client = TestClient(main.app)
    resp = client.get("/pending-tracks")

    assert resp.status_code == 200
    assert "Apple Music" in resp.text
    assert "ISRC: USABC1234567" in resp.text
    assert "Candidates: 1" in resp.text
    assert "Best score: 97" in resp.text
    assert "Apply Match" in resp.text


def test_auth_apple_status_reports_connected(monkeypatch):
    monkeypatch.setattr(main, "APPLE_ENABLED", True)

    class _AppleClient:
        def get_music_user_token(self):
            return "tok"

        def get_storefront(self):
            return "us"

    def _get_apple_client():
        return _AppleClient()

    monkeypatch.setattr(main, "get_apple_client", _get_apple_client)

    client = TestClient(main.app)
    resp = client.get("/auth/apple/status")

    assert resp.status_code == 200
    assert resp.json() == {"connected": True, "storefront": "us"}


def test_auth_apple_status_reports_disconnected_on_exception(monkeypatch):
    monkeypatch.setattr(main, "APPLE_ENABLED", True)

    class _AppleClient:
        def get_music_user_token(self):
            raise RuntimeError("no token")

    def _get_apple_client():
        return _AppleClient()

    monkeypatch.setattr(main, "get_apple_client", _get_apple_client)

    client = TestClient(main.app)
    resp = client.get("/auth/apple/status")

    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "storefront": None}


def test_resolve_track_tidal_path_adds_favorite_and_persists(monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", _DummyDB)

    pending_item = {
        "id": 88,
        "spotify_id": "sp_track_tidal",
        "spotify_title": "Tidal Track",
        "spotify_artist": "Tidal Artist",
        "isrc": "TIDALISRC",
        "target_service": "tidal",
    }
    monkeypatch.setattr(main, "get_pending_tracks", lambda db: [pending_item])

    add_calls: list[int] = []

    class _Favorites:
        def add_track(self, track_id: int):
            add_calls.append(track_id)

    class _User:
        def __init__(self):
            self.favorites = _Favorites()

    class _Session:
        def __init__(self):
            self.user = _User()

    def _get_tidal_session():
        return _Session()

    monkeypatch.setattr(main, "get_tidal_session", _get_tidal_session)
    monkeypatch.setattr(main, "_tidal_favorite_tracks_set", lambda sess: set())

    upsert_calls: list[dict[str, Any]] = []
    event_calls: list[dict[str, Any]] = []
    delete_calls: list[int] = []

    monkeypatch.setattr(main, "upsert_track_map", lambda *args, **kwargs: upsert_calls.append(kwargs))
    monkeypatch.setattr(main, "add_track_sync_event", lambda *args, **kwargs: event_calls.append(kwargs))
    monkeypatch.setattr(main, "delete_pending_track", lambda *args, **kwargs: delete_calls.append(args[1]))

    client = TestClient(main.app)
    resp = client.post(
        "/resolve-track/88",
        data={
            "target_service": "tidal",
            "target_id": "12345",
            "target_title": "TIDAL Track",
            "target_artist": "TIDAL Artist",
            "target_artist_id": "",
        },
    )

    assert resp.status_code == 200
    assert add_calls == [12345]
    assert upsert_calls and upsert_calls[0]["target_service"] == "tidal"
    assert event_calls and event_calls[0]["target_service"] == "tidal"
    assert delete_calls == [88]


def test_followed_artists_job_errors_when_apple_auth_fails(monkeypatch):
    def _get_spotify_client():
        return object()

    monkeypatch.setattr(main, "get_spotify_client", _get_spotify_client)

    def _get_apple_client():
        raise RuntimeError("apple auth failed")

    monkeypatch.setattr(main, "get_apple_client", _get_apple_client)

    job_id = "job-followed-artists-auth-fail"
    _clear_job(job_id)
    try:
        runner = getattr(main, "_run_sync_apple_followed_artists_playlist_job")
        runner(job_id)
        jobs = getattr(main, "_jobs")
        status = dict(jobs.get(job_id) or {})
        assert status["state"] == "error"
        assert "apple auth failed" in status.get("error", "")
    finally:
        _clear_job(job_id)


def test_followed_artists_job_errors_on_critical_http_status(monkeypatch):
    def _get_spotify_client():
        return object()

    monkeypatch.setattr(main, "get_spotify_client", _get_spotify_client)

    class _AppleClient:
        def get_storefront(self):
            return "us"

        def list_library_playlists(self):
            return []

        def ensure_playlist(self, name: str, description: str | None = None):
            _ = (name, description)
            return {"id": "apl_err", "name": "x"}

        def search_by_isrc(self, isrc: str, storefront: str | None = None):
            _ = (isrc, storefront)
            raise httpx.HTTPStatusError(
                "rate limited",
                request=MagicMock(),
                response=MagicMock(status_code=429),
            )

    def _get_apple_client():
        return _AppleClient()

    monkeypatch.setattr(main, "get_apple_client", _get_apple_client)

    def _call_spotify(func):
        class _SP:
            def me(self):
                return {"country": "US"}

            def current_user_followed_artists(self, limit, after=None):
                _ = (limit, after)
                return {"artists": {"items": [{"id": "a1", "name": "Artist One"}], "cursors": {"after": None}}}

            def artist_top_tracks(self, artist_id, country):
                _ = (artist_id, country)
                return {
                    "tracks": [
                        {
                            "id": "sp_t1",
                            "name": "Track One",
                            "artists": [{"name": "Artist One"}],
                            "external_ids": {"isrc": "ISRC-FAIL"},
                            "duration_ms": 200000,
                        }
                    ]
                }

        return func(_SP())

    monkeypatch.setattr(main, "call_spotify", _call_spotify)

    job_id = "job-followed-artists-http-fail"
    _clear_job(job_id)
    try:
        runner = getattr(main, "_run_sync_apple_followed_artists_playlist_job")
        runner(job_id)
        jobs = getattr(main, "_jobs")
        status = dict(jobs.get(job_id) or {})
        assert status["state"] == "error"
        assert "rate limited" in status.get("error", "")
    finally:
        _clear_job(job_id)
