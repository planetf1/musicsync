from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

import app.main as main


class _DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_library_playlists_next_link_uses_incremented_page(monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", lambda: _DummySession())

    def _list_synced_playlists(db, **kwargs):
        _ = (db, kwargs)
        return (
            [
                {
                    "spotify_id": "sp_pl_1",
                    "spotify_name": "My Playlist",
                    "tidal_id": "td_pl_1",
                    "tidal_name": "My Playlist",
                    "apple_id": None,
                    "apple_name": None,
                    "last_synced_at": "2026-03-09T00:00:00+00:00",
                }
            ],
            30,
        )

    monkeypatch.setattr(main, "list_synced_playlists", _list_synced_playlists)
    monkeypatch.setattr(
        main,
        "get_playlist_stats",
        lambda db, spotify_id, target_service: {"count": 1, "total_seconds": 60},
    )

    client = TestClient(main.app)
    resp = client.get("/library/playlists?page=1&page_size=25")

    assert resp.status_code == 200
    assert 'href="/library/playlists?page=2' in resp.text


def test_library_playlist_detail_honors_service_query_param(monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", lambda: _DummySession())

    def _get_playlist_map(db, spotify_id, target_service="tidal"):
        _ = (db, spotify_id)
        if target_service == "tidal":
            return {
                "spotify_id": "sp_pl_1",
                "spotify_name": "My Playlist",
                "target_service": "tidal",
                "tidal_id": "td_pl_1",
                "tidal_name": "My Playlist",
                "target_id": "td_pl_1",
                "target_name": "My Playlist",
            }
        if target_service == "apple":
            return {
                "spotify_id": "sp_pl_1",
                "spotify_name": "My Playlist",
                "target_service": "apple",
                "target_id": "am_pl_1",
                "target_name": "My Playlist",
            }
        return None

    seen_services: list[str] = []

    def _list_playlist_tracks(
        db: Any,
        spotify_id: str,
        *,
        target_service: str = "tidal",
        search: str | None = None,
        sort: str = "position",
        order: str = "asc",
        page: int = 1,
        page_size: int = 50,
    ):
        _ = (db, spotify_id, search, sort, order, page, page_size)
        seen_services.append(target_service)
        return [], 0

    monkeypatch.setattr(main, "get_playlist_map", _get_playlist_map)
    monkeypatch.setattr(main, "list_playlist_tracks", _list_playlist_tracks)
    monkeypatch.setattr(
        main,
        "get_playlist_stats",
        lambda db, spotify_id, target_service: {"count": 0, "total_seconds": 0},
    )

    client = TestClient(main.app)
    resp = client.get("/library/playlists/sp_pl_1?service=apple")

    assert resp.status_code == 200
    assert seen_services == ["apple"]
    assert "Service view:" in resp.text
    assert "service=apple" in resp.text
