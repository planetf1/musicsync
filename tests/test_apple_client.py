"""Unit tests for Apple Music client.

Tests cover token management, API requests, search functionality, playlist operations,
library additions, and error handling.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import httpx
import pytest

from app.apple_client import (
    APPLE_BASE_URL,
    AppleMusicClient,
    _as_int,
    _env_first,
    _normalize_song,
    _now_ts,
)


@pytest.mark.unit
class TestHelperFunctions:
    """Test module-level helper functions."""

    def test_now_ts_returns_integer_timestamp(self):
        result = _now_ts()
        assert isinstance(result, int)
        assert result > 1700000000  # Sanity check (after 2023)

    def test_as_int_converts_valid_integers(self):
        assert _as_int("42") == 42
        assert _as_int(123) == 123
        assert _as_int("0") == 0

    def test_as_int_returns_default_on_invalid_input(self):
        assert _as_int("abc") == 0
        assert _as_int("abc", default=99) == 99
        assert _as_int(None) == 0

    def test_env_first_returns_first_set_variable(self):
        with patch.dict(os.environ, {"VAR1": "", "VAR2": "value2"}, clear=True):
            assert _env_first("VAR1", "VAR2") == "value2"

    def test_env_first_returns_none_when_all_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _env_first("VAR1", "VAR2") is None

    def test_normalize_song_extracts_required_fields(self):
        song = {
            "id": "12345",
            "attributes": {
                "name": "Test Song",
                "artistName": "Test Artist",
                "durationInMillis": 240000,
                "isrc": "USUM71234567",
                "contentRating": "explicit",
            },
        }
        result = _normalize_song(song)
        assert result["id"] == "12345"
        assert result["name"] == "Test Song"
        assert result["artists"] == ["Test Artist"]
        assert result["duration_ms"] == 240000
        assert result["isrc"] == "USUM71234567"
        assert result["is_explicit"] is True

    def test_normalize_song_handles_missing_fields(self):
        song = {"id": "999"}
        result = _normalize_song(song)
        assert result["id"] == "999"
        assert result["name"] == ""
        assert result["artists"] == []
        assert result["duration_ms"] is None
        assert result["isrc"] is None
        assert result["is_explicit"] is False


@pytest.mark.unit
class TestAppleMusicClientInit:
    """Test client initialization and configuration."""

    def test_init_with_default_values(self):
        client = AppleMusicClient()
        assert client.timeout_seconds == 20.0
        assert client.max_retries == 4
        assert client.backoff_seconds == 1.0

    def test_init_with_custom_values(self):
        client = AppleMusicClient(
            timeout_seconds=30.0,
            max_retries=2,
            backoff_seconds=2.5,
        )
        assert client.timeout_seconds == 30.0
        assert client.max_retries == 2
        assert client.backoff_seconds == 2.5

    def test_init_loads_environment_variables(self):
        with patch.dict(
            os.environ,
            {
                "APPLE_MUSIC_TEAM_ID": "ABC123",
                "APPLE_MUSIC_KEY_ID": "XYZ456",
                "APPLE_MUSIC_PRIVATE_KEY_PATH": "/path/to/key.p8",
            },
        ):
            client = AppleMusicClient()
            assert client.team_id == "ABC123"
            assert client.key_id == "XYZ456"
            assert client.private_key_path == "/path/to/key.p8"


@pytest.mark.unit
class TestDeveloperTokenGeneration:
    """Test developer token (JWT) generation and caching."""

    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    @patch("app.apple_client.AppleMusicClient._save_state")
    @patch("app.apple_client.AppleMusicClient._read_private_key")
    @patch("jwt.encode")
    def test_get_developer_token_generates_valid_jwt(
        self, mock_jwt_encode, mock_read_key, mock_save_state, mock_get_state
    ):
        mock_get_state.return_value = {}
        mock_read_key.return_value = "fake_private_key_content"
        mock_jwt_encode.return_value = "generated_jwt_token"

        with patch.dict(
            os.environ,
            {
                "APPLE_MUSIC_TEAM_ID": "TEAM123",
                "APPLE_MUSIC_KEY_ID": "KEY789",
                "APPLE_MUSIC_PRIVATE_KEY_PATH": "/fake/path.p8",
            },
        ):
            client = AppleMusicClient()
            token = client.get_developer_token()

        assert token == "generated_jwt_token"

        # Verify JWT was encoded with correct parameters
        mock_jwt_encode.assert_called_once()
        call_kwargs = mock_jwt_encode.call_args.kwargs
        assert call_kwargs["algorithm"] == "ES256"
        assert call_kwargs["payload"]["iss"] == "TEAM123"
        assert "iat" in call_kwargs["payload"]
        assert "exp" in call_kwargs["payload"]
        assert call_kwargs["headers"]["kid"] == "KEY789"
        assert call_kwargs["headers"]["alg"] == "ES256"

        # Verify token was cached
        mock_save_state.assert_called_once()

    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    def test_get_developer_token_returns_cached_token(self, mock_get_state):
        future_ts = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
        mock_get_state.return_value = {
            "developer_token": "cached_token_xyz",
            "developer_token_expires_at": future_ts,
        }

        client = AppleMusicClient()
        token = client.get_developer_token()

        assert token == "cached_token_xyz"

    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    @patch("app.apple_client.AppleMusicClient._save_state")
    @patch("app.apple_client.AppleMusicClient._read_private_key")
    @patch("jwt.encode")
    def test_get_developer_token_refreshes_expired_token(
        self, mock_jwt_encode, mock_read_key, mock_save_state, mock_get_state
    ):
        expired_ts = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
        mock_get_state.return_value = {
            "developer_token": "expired_token",
            "developer_token_expires_at": expired_ts,
        }
        mock_read_key.return_value = "fake_private_key_content"
        mock_jwt_encode.return_value = "new_jwt_token"

        with patch.dict(
            os.environ,
            {
                "APPLE_MUSIC_TEAM_ID": "TEAM123",
                "APPLE_MUSIC_KEY_ID": "KEY789",
                "APPLE_MUSIC_PRIVATE_KEY_PATH": "/fake/path.p8",
            },
        ):
            client = AppleMusicClient()
            token = client.get_developer_token()

        assert token == "new_jwt_token"
        assert token != "expired_token"

    def test_require_developer_config_raises_without_team_id(self):
        with patch.dict(os.environ, {}, clear=True):
            client = AppleMusicClient()
            with pytest.raises(RuntimeError, match="Missing Apple Music configuration"):
                client._require_developer_config()


@pytest.mark.unit
class TestUserTokenManagement:
    """Test Music User Token storage and retrieval."""

    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    @patch("app.apple_client.AppleMusicClient._save_state")
    def test_set_music_user_token_stores_token(self, mock_save_state, mock_get_state):
        mock_get_state.return_value = {}
        client = AppleMusicClient()
        client.set_music_user_token("user_token_abc123")

        mock_save_state.assert_called_once()
        saved_state = mock_save_state.call_args[0][0]
        assert saved_state["music_user_token"] == "user_token_abc123"

    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    @patch("app.apple_client.AppleMusicClient._save_state")
    def test_set_music_user_token_with_storefront(self, mock_save_state, mock_get_state):
        mock_get_state.return_value = {}
        client = AppleMusicClient()
        client.set_music_user_token("user_token_abc123", storefront="us")

        saved_state = mock_save_state.call_args[0][0]
        assert saved_state["music_user_token"] == "user_token_abc123"
        assert saved_state["storefront"] == "us"

    def test_set_music_user_token_rejects_empty_token(self):
        client = AppleMusicClient()
        with pytest.raises(ValueError, match="cannot be empty"):
            client.set_music_user_token("")

    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    def test_get_music_user_token_returns_stored_token(self, mock_get_state):
        mock_get_state.return_value = {"music_user_token": "stored_token_xyz"}
        client = AppleMusicClient()
        token = client.get_music_user_token()
        assert token == "stored_token_xyz"

    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    def test_get_music_user_token_raises_when_missing(self, mock_get_state):
        mock_get_state.return_value = {}
        client = AppleMusicClient()
        with pytest.raises(RuntimeError, match="user token missing"):
            client.get_music_user_token()

    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    @patch("app.apple_client.AppleMusicClient._save_state")
    def test_clear_music_user_token_removes_token(self, mock_save_state, mock_get_state):
        mock_get_state.return_value = {
            "music_user_token": "token_to_clear",
            "storefront": "us",
        }
        client = AppleMusicClient()
        client.clear_music_user_token()

        saved_state = mock_save_state.call_args[0][0]
        assert "music_user_token" not in saved_state
        assert "storefront" not in saved_state


@pytest.mark.unit
class TestAPIRequests:
    """Test HTTP request handling with retries and error handling."""

    @patch("httpx.request")
    @patch("app.apple_client.AppleMusicClient.get_developer_token")
    def test_request_makes_successful_api_call(self, mock_get_token, mock_httpx):
        mock_get_token.return_value = "dev_token_123"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'{"data": []}'
        mock_response.json.return_value = {"data": []}
        mock_httpx.return_value = mock_response

        client = AppleMusicClient()
        result = client._request("GET", "/test")

        assert result == {"data": []}
        mock_httpx.assert_called_once()
        call_kwargs = mock_httpx.call_args.kwargs
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["url"] == f"{APPLE_BASE_URL}/test"
        assert "Authorization" in call_kwargs["headers"]

    @patch("httpx.request")
    @patch("app.apple_client.AppleMusicClient.get_developer_token")
    @patch("app.apple_client.AppleMusicClient.get_music_user_token")
    def test_request_includes_user_token_when_required(self, mock_user_token, mock_dev_token, mock_httpx):
        mock_dev_token.return_value = "dev_token_123"
        mock_user_token.return_value = "user_token_456"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"{}"
        mock_response.json.return_value = {}
        mock_httpx.return_value = mock_response

        client = AppleMusicClient()
        client._request("GET", "/me/storefront", require_user_token=True)

        call_kwargs = mock_httpx.call_args.kwargs
        assert call_kwargs["headers"]["Music-User-Token"] == "user_token_456"

    @patch("httpx.request")
    @patch("app.apple_client.AppleMusicClient.get_developer_token")
    @patch("time.sleep")
    def test_request_retries_on_rate_limit(self, mock_sleep, mock_get_token, mock_httpx):
        mock_get_token.return_value = "dev_token_123"

        # First call returns 429, second call succeeds
        response_429 = Mock()
        response_429.status_code = 429
        response_429.headers = {}

        response_200 = Mock()
        response_200.status_code = 200
        response_200.content = b"{}"
        response_200.json.return_value = {}

        mock_httpx.side_effect = [response_429, response_200]

        client = AppleMusicClient(backoff_seconds=0.1)
        result = client._request("GET", "/test")

        assert result == {}
        assert mock_httpx.call_count == 2
        mock_sleep.assert_called_once()

    @patch("httpx.request")
    @patch("app.apple_client.AppleMusicClient.get_developer_token")
    def test_request_refreshes_token_on_401(self, mock_get_token, mock_httpx):
        # First token returns 401, refreshed token succeeds
        mock_get_token.side_effect = ["old_token", "new_token"]

        response_401 = Mock()
        response_401.status_code = 401

        response_200 = Mock()
        response_200.status_code = 200
        response_200.content = b"{}"
        response_200.json.return_value = {}

        mock_httpx.side_effect = [response_401, response_200]

        client = AppleMusicClient()
        result = client._request("GET", "/test")

        assert result == {}
        assert mock_httpx.call_count == 2
        # Verify token was refreshed (force_refresh=True on second call)
        assert mock_get_token.call_count == 2

    @patch("httpx.request")
    @patch("app.apple_client.AppleMusicClient.get_developer_token")
    def test_request_raises_on_http_error(self, mock_get_token, mock_httpx):
        mock_get_token.return_value = "dev_token_123"
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=Mock(), response=mock_response
        )
        mock_httpx.return_value = mock_response

        client = AppleMusicClient()
        with pytest.raises(httpx.HTTPStatusError):
            client._request("GET", "/nonexistent")


@pytest.mark.unit
class TestStorefrontDetection:
    """Test storefront detection and caching."""

    @patch("app.apple_client.AppleMusicClient._request")
    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    @patch("app.apple_client.AppleMusicClient._save_state")
    def test_get_storefront_fetches_and_caches(self, mock_save, mock_get_state, mock_request):
        mock_get_state.return_value = {}
        mock_request.return_value = {"data": [{"id": "us"}]}

        client = AppleMusicClient()
        storefront = client.get_storefront()

        assert storefront == "us"
        mock_request.assert_called_once_with("GET", "/me/storefront", require_user_token=True)
        mock_save.assert_called_once()

    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    def test_get_storefront_returns_cached_value(self, mock_get_state):
        mock_get_state.return_value = {"storefront": "gb"}

        client = AppleMusicClient()
        storefront = client.get_storefront()

        assert storefront == "gb"

    @patch("app.apple_client.AppleMusicClient._request")
    @patch("app.apple_client.AppleMusicClient._get_cached_state")
    def test_get_storefront_raises_on_empty_response(self, mock_get_state, mock_request):
        mock_get_state.return_value = {}
        mock_request.return_value = {"data": []}

        client = AppleMusicClient()
        with pytest.raises(RuntimeError, match="Unable to detect"):
            client.get_storefront()


@pytest.mark.unit
class TestSearchByISRC:
    """Test ISRC-based track search."""

    @patch("app.apple_client.AppleMusicClient._request")
    @patch("app.apple_client.AppleMusicClient.get_storefront")
    def test_search_by_isrc_returns_exact_match(self, mock_storefront, mock_request):
        mock_storefront.return_value = "us"
        mock_request.return_value = {
            "data": [
                {
                    "id": "123",
                    "attributes": {
                        "name": "Test Song",
                        "artistName": "Test Artist",
                        "isrc": "USUM71234567",
                        "durationInMillis": 200000,
                    },
                }
            ]
        }

        client = AppleMusicClient()
        result = client.search_by_isrc("USUM71234567")

        assert result is not None
        assert result["id"] == "123"
        assert result["name"] == "Test Song"
        assert result["isrc"] == "USUM71234567"

    @patch("app.apple_client.AppleMusicClient._request")
    @patch("app.apple_client.AppleMusicClient.get_storefront")
    def test_search_by_isrc_returns_none_when_not_found(self, mock_storefront, mock_request):
        mock_storefront.return_value = "us"
        mock_request.return_value = {"data": []}

        client = AppleMusicClient()
        result = client.search_by_isrc("NONEXISTENT")

        assert result is None

    def test_search_by_isrc_returns_none_for_empty_isrc(self):
        client = AppleMusicClient()
        result = client.search_by_isrc("")
        assert result is None


@pytest.mark.unit
class TestTrackSearch:
    """Test text-based track search."""

    @patch("app.apple_client.AppleMusicClient._request")
    @patch("app.apple_client.AppleMusicClient.get_storefront")
    def test_search_track_returns_results(self, mock_storefront, mock_request):
        mock_storefront.return_value = "us"
        mock_request.return_value = {
            "results": {
                "songs": {
                    "data": [
                        {
                            "id": "456",
                            "attributes": {
                                "name": "Search Result",
                                "artistName": "Artist Name",
                                "durationInMillis": 180000,
                            },
                        }
                    ]
                }
            }
        }

        client = AppleMusicClient()
        results = client.search_track(title="Search Result", artist="Artist Name")

        assert len(results) == 1
        assert results[0]["id"] == "456"
        assert results[0]["name"] == "Search Result"

    @patch("app.apple_client.AppleMusicClient._request")
    @patch("app.apple_client.AppleMusicClient.get_storefront")
    def test_search_track_sorts_by_duration(self, mock_storefront, mock_request):
        mock_storefront.return_value = "us"
        mock_request.return_value = {
            "results": {
                "songs": {
                    "data": [
                        {
                            "id": "1",
                            "attributes": {
                                "name": "Song",
                                "artistName": "Artist",
                                "durationInMillis": 300000,
                            },
                        },
                        {
                            "id": "2",
                            "attributes": {
                                "name": "Song",
                                "artistName": "Artist",
                                "durationInMillis": 180000,
                            },
                        },
                    ]
                }
            }
        }

        client = AppleMusicClient()
        results = client.search_track(title="Song", artist="Artist", duration_s=190)

        # Result with duration closest to 190s (190000ms) should be first
        assert results[0]["id"] == "2"


@pytest.mark.integration
class TestPlaylistOperations:
    """Test playlist listing, creation, and track additions."""

    @patch("app.apple_client.AppleMusicClient._request")
    def test_list_library_playlists_returns_all_playlists(self, mock_request):
        mock_request.return_value = {
            "data": [
                {"id": "pl1", "attributes": {"name": "Playlist 1"}},
                {"id": "pl2", "attributes": {"name": "Playlist 2"}},
            ],
            "next": None,
        }

        client = AppleMusicClient()
        playlists = client.list_library_playlists()

        assert len(playlists) == 2
        assert playlists[0] == {"id": "pl1", "name": "Playlist 1"}
        assert playlists[1] == {"id": "pl2", "name": "Playlist 2"}

    @patch("app.apple_client.AppleMusicClient._request")
    def test_list_library_playlists_handles_pagination(self, mock_request):
        mock_request.side_effect = [
            {
                "data": [{"id": "pl1", "attributes": {"name": "Playlist 1"}}],
                "next": f"{APPLE_BASE_URL}/me/library/playlists?offset=1",
            },
            {
                "data": [{"id": "pl2", "attributes": {"name": "Playlist 2"}}],
                "next": None,
            },
        ]

        client = AppleMusicClient()
        playlists = client.list_library_playlists()

        assert len(playlists) == 2
        assert mock_request.call_count == 2

    @patch("app.apple_client.AppleMusicClient._request")
    @patch("app.apple_client.AppleMusicClient.list_library_playlists")
    def test_ensure_playlist_creates_new_playlist(self, mock_list, mock_request):
        mock_list.return_value = []
        mock_request.return_value = {"data": [{"id": "new_pl", "attributes": {"name": "New Playlist"}}]}

        client = AppleMusicClient()
        playlist = client.ensure_playlist("New Playlist", description="Test description")

        assert playlist["id"] == "new_pl"
        assert playlist["name"] == "New Playlist"
        mock_request.assert_called_once()

    @patch("app.apple_client.AppleMusicClient.list_library_playlists")
    def test_ensure_playlist_returns_existing_playlist(self, mock_list):
        mock_list.return_value = [
            {"id": "existing_pl", "name": "Existing Playlist"},
        ]

        client = AppleMusicClient()
        playlist = client.ensure_playlist("Existing Playlist")

        assert playlist["id"] == "existing_pl"

    @patch("app.apple_client.AppleMusicClient._request")
    def test_add_tracks_to_playlist_chunks_requests(self, mock_request):
        mock_request.return_value = {}
        track_ids = [f"track_{i}" for i in range(250)]

        client = AppleMusicClient()
        result = client.add_tracks_to_playlist("pl123", track_ids)

        # 250 tracks should be chunked into 3 requests (100, 100, 50)
        assert mock_request.call_count == 3
        assert result.requested == 250
        assert result.succeeded == 250
        assert result.failed == 0

    def test_add_tracks_to_playlist_handles_empty_list(self):
        client = AppleMusicClient()
        result = client.add_tracks_to_playlist("pl123", [])

        assert result.requested == 0
        assert result.succeeded == 0
        assert result.failed == 0

    @patch("app.apple_client.AppleMusicClient._request")
    def test_get_playlist_tracks_returns_all_tracks(self, mock_request):
        mock_request.return_value = {
            "data": [
                {"id": "track1", "type": "library-songs"},
                {"id": "track2", "type": "library-songs"},
            ],
            "next": None,
        }

        client = AppleMusicClient()
        tracks = client.get_playlist_tracks("pl123")

        assert len(tracks) == 2
        assert tracks[0]["id"] == "track1"
        assert tracks[1]["id"] == "track2"

    @patch("app.apple_client.AppleMusicClient._request")
    def test_get_playlist_tracks_handles_pagination(self, mock_request):
        mock_request.side_effect = [
            {
                "data": [{"id": "track1", "type": "library-songs"}],
                "next": f"{APPLE_BASE_URL}/me/library/playlists/pl123/tracks?offset=1",
            },
            {
                "data": [{"id": "track2", "type": "library-songs"}],
                "next": None,
            },
        ]

        client = AppleMusicClient()
        tracks = client.get_playlist_tracks("pl123")

        assert len(tracks) == 2
        assert mock_request.call_count == 2

    @patch("app.apple_client.AppleMusicClient.get_playlist_tracks")
    @patch("app.apple_client.AppleMusicClient._request")
    def test_clear_playlist_tracks_removes_all_tracks(self, mock_request, mock_get_tracks):
        mock_get_tracks.return_value = [
            {"id": "track1", "type": "library-songs"},
            {"id": "track2", "type": "library-songs"},
            {"id": "track3", "type": "library-songs"},
        ]
        mock_request.return_value = {}

        client = AppleMusicClient()
        removed = client.clear_playlist_tracks("pl123")

        assert removed == 3
        mock_get_tracks.assert_called_once_with("pl123")
        mock_request.assert_called_once()

    @patch("app.apple_client.AppleMusicClient.get_playlist_tracks")
    @patch("app.apple_client.AppleMusicClient._request")
    def test_clear_playlist_tracks_chunks_deletes(self, mock_request, mock_get_tracks):
        # Create 250 tracks to test chunking
        mock_get_tracks.return_value = [{"id": f"track{i}", "type": "library-songs"} for i in range(250)]
        mock_request.return_value = {}

        client = AppleMusicClient()
        removed = client.clear_playlist_tracks("pl123")

        # 250 tracks should be chunked into 3 DELETE requests (100, 100, 50)
        assert removed == 250
        assert mock_request.call_count == 3

    @patch("app.apple_client.AppleMusicClient.get_playlist_tracks")
    def test_clear_playlist_tracks_handles_empty_playlist(self, mock_get_tracks):
        mock_get_tracks.return_value = []

        client = AppleMusicClient()
        removed = client.clear_playlist_tracks("pl123")

        assert removed == 0


@pytest.mark.integration
class TestLibraryOperations:
    """Test library track additions."""

    @patch("app.apple_client.AppleMusicClient._request")
    def test_add_tracks_to_library_chunks_requests(self, mock_request):
        mock_request.return_value = {}
        track_ids = [f"track_{i}" for i in range(250)]

        client = AppleMusicClient()
        result = client.add_tracks_to_library(track_ids)

        # 250 tracks should be chunked into 3 requests (100, 100, 50)
        assert mock_request.call_count == 3
        assert result.requested == 250
        assert result.succeeded == 250
        assert result.failed == 0

    def test_add_tracks_to_library_handles_empty_list(self):
        client = AppleMusicClient()
        result = client.add_tracks_to_library([])

        assert result.requested == 0
        assert result.succeeded == 0
        assert result.failed == 0


@pytest.mark.integration
class TestErrorHandling:
    """Test error handling for various failure scenarios."""

    @patch("app.apple_client.AppleMusicClient._read_private_key")
    def test_missing_private_key_file_raises_error(self, mock_read_key):
        mock_read_key.side_effect = FileNotFoundError("Key file not found")

        with patch.dict(
            os.environ,
            {
                "APPLE_MUSIC_TEAM_ID": "TEAM123",
                "APPLE_MUSIC_KEY_ID": "KEY789",
                "APPLE_MUSIC_PRIVATE_KEY_PATH": "/nonexistent/key.p8",
            },
        ):
            client = AppleMusicClient()
            with pytest.raises(FileNotFoundError):
                client.get_developer_token()

    @patch("httpx.request")
    @patch("app.apple_client.AppleMusicClient.get_developer_token")
    @patch("time.sleep")
    def test_request_fails_after_max_retries(self, mock_sleep, mock_get_token, mock_httpx):
        mock_get_token.return_value = "dev_token_123"

        # All requests return 500
        response_500 = Mock()
        response_500.status_code = 500
        response_500.headers = {}
        response_500.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Internal Server Error", request=Mock(), response=response_500
        )
        mock_httpx.return_value = response_500

        client = AppleMusicClient(max_retries=2)
        with pytest.raises(httpx.HTTPStatusError):
            client._request("GET", "/test")

        # Should attempt: initial + 2 retries = 3 total
        assert mock_httpx.call_count == 3

    @patch("app.apple_client.AppleMusicClient._request")
    def test_ensure_playlist_raises_on_creation_failure(self, mock_request):
        # list_library_playlists returns empty (playlist doesn't exist)
        # create returns no data
        mock_request.side_effect = [
            {"data": []},  # list_library_playlists pagination
            {"data": []},  # create playlist returns empty
        ]

        with patch.object(AppleMusicClient, "list_library_playlists", return_value=[]):
            client = AppleMusicClient()
            with pytest.raises(RuntimeError, match="returned no data"):
                client.ensure_playlist("New Playlist")
