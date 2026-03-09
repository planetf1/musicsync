"""Negative tests for Apple sync background jobs to verify robust error handling."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

import app.main as main
from tests.test_apple_sync_jobs import (
    StubAppleClient,
    StubSpotifyPager,
    _get_testing_session,
    _job_status,
    _reset_job,
    _run_likes_job,
)


@pytest.mark.unit
class TestAppleSyncNegativeScenarios:
    def _patch_common(self, monkeypatch: pytest.MonkeyPatch):
        def _session_local():
            return _get_testing_session()

        def _spotify_client():
            return object()

        monkeypatch.setattr(main, "SessionLocal", _session_local)
        monkeypatch.setattr(main, "get_spotify_client", _spotify_client)

    def test_job_handles_401_unauthorized(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_common(monkeypatch)

        # Mock Spotify to return some tracks
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
                    }
                ],
                "next": None,
            }
        ]
        monkeypatch.setattr(main, "call_spotify", StubSpotifyPager(spotify_pages))

        # Mock Apple client to raise 401
        def _raise_401(*args, **kwargs):
            raise httpx.HTTPStatusError("Unauthorized", request=MagicMock(), response=MagicMock(status_code=401))

        apple = StubAppleClient(search_by_isrc_map={}, existing_library_ids=set())
        monkeypatch.setattr(apple, "search_by_isrc", _raise_401)
        monkeypatch.setattr(main, "get_apple_client", lambda: apple)

        job_id = "job-apple-negative-401"
        _reset_job(job_id)

        # Run the job - it should catch the exception and mark the job as error
        _run_likes_job(job_id)

        status = _job_status(job_id)
        assert status["state"] == "error"
        assert "Unauthorized" in status["error"]

    def test_job_handles_500_server_error(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_common(monkeypatch)

        spotify_pages = [
            {
                "items": [
                    {
                        "track": {
                            "id": "sp2",
                            "name": "Song Two",
                            "artists": [{"name": "Artist B", "id": "art2"}],
                            "duration_ms": 210000,
                            "external_ids": {"isrc": "ISRC0002"},
                        }
                    }
                ],
                "next": None,
            }
        ]
        monkeypatch.setattr(main, "call_spotify", StubSpotifyPager(spotify_pages))

        # Mock Apple client to raise 500
        def _raise_500(*args, **kwargs):
            raise httpx.HTTPStatusError("Server Error", request=MagicMock(), response=MagicMock(status_code=500))

        apple = StubAppleClient(search_by_isrc_map={}, existing_library_ids=set())
        monkeypatch.setattr(apple, "search_by_isrc", _raise_500)
        monkeypatch.setattr(main, "get_apple_client", lambda: apple)

        job_id = "job-apple-negative-500"
        _reset_job(job_id)

        _run_likes_job(job_id)

        status = _job_status(job_id)
        assert status["state"] == "error"
        assert "Server Error" in status["error"]
