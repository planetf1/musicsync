from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from dotenv import load_dotenv

from .storage import SessionLocal, load_token, save_token

_log = logging.getLogger(__name__)

APPLE_SERVICE = "apple"
APPLE_BASE_URL = "https://api.music.apple.com/v1"


def _now_ts() -> int:
    return int(time.time())


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _normalize_song(song: dict[str, Any]) -> dict[str, Any]:
    attrs = song.get("attributes") or {}
    artists: list[str] = []
    artist_name = attrs.get("artistName")
    if artist_name:
        artists.append(str(artist_name))
    return {
        "id": str(song.get("id") or ""),
        "name": str(attrs.get("name") or ""),
        "artists": artists,
        "duration_ms": _as_int(attrs.get("durationInMillis"), 0) or None,
        "isrc": attrs.get("isrc"),
        "is_explicit": bool(attrs.get("contentRating") == "explicit"),
        "raw": song,
    }


@dataclass
class AddResult:
    requested: int
    succeeded: int
    failed: int


class AppleMusicClient:
    """Apple Music API client.

    Manages developer token generation/caching, user token persistence, storefront
    lookup, and common read/write helpers used by sync jobs.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_retries: int = 4,
        backoff_seconds: float = 1.0,
    ) -> None:
        load_dotenv()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

        self.team_id = _env_first("APPLE_MUSIC_TEAM_ID", "APPLE_TEAM_ID")
        self.key_id = _env_first("APPLE_MUSIC_KEY_ID", "APPLE_KEY_ID")
        self.private_key_path = _env_first("APPLE_MUSIC_PRIVATE_KEY_PATH", "APPLE_PRIVATE_KEY_PATH")

    def _require_developer_config(self) -> tuple[str, str, str]:
        if not self.team_id or not self.key_id or not self.private_key_path:
            raise RuntimeError(
                "Missing Apple Music configuration. Set APPLE_MUSIC_TEAM_ID/APPLE_TEAM_ID, "
                "APPLE_MUSIC_KEY_ID/APPLE_KEY_ID, and APPLE_MUSIC_PRIVATE_KEY_PATH/APPLE_PRIVATE_KEY_PATH."
            )
        return self.team_id, self.key_id, self.private_key_path

    def _read_private_key(self) -> str:
        _, _, key_path = self._require_developer_config()
        with open(key_path, encoding="utf-8") as fp:
            return fp.read()

    def _get_cached_state(self) -> dict[str, Any]:
        with SessionLocal() as db:
            return load_token(db, APPLE_SERVICE) or {}

    def _save_state(self, state: dict[str, Any]) -> None:
        with SessionLocal() as db:
            save_token(db, APPLE_SERVICE, state)

    def get_music_user_token(self) -> str:
        state = self._get_cached_state()
        token = state.get("music_user_token")
        if not token:
            raise RuntimeError("Apple Music user token missing. Connect Apple Music first.")
        return str(token)

    def set_music_user_token(self, user_token: str, storefront: str | None = None) -> None:
        token = (user_token or "").strip()
        if not token:
            raise ValueError("Apple Music user token cannot be empty")
        state = self._get_cached_state()
        state["music_user_token"] = token
        if storefront:
            state["storefront"] = storefront
        self._save_state(state)

    def clear_music_user_token(self) -> None:
        state = self._get_cached_state()
        state.pop("music_user_token", None)
        state.pop("storefront", None)
        self._save_state(state)

    def get_developer_token(self, *, force_refresh: bool = False) -> str:
        state = self._get_cached_state()
        token = state.get("developer_token")
        exp_ts = _as_int(state.get("developer_token_expires_at"), 0)

        if not force_refresh and token and exp_ts > (_now_ts() + 60):
            return str(token)

        team_id, key_id, _ = self._require_developer_config()
        private_key = self._read_private_key()

        now = datetime.now(UTC)
        # Apple permits max 6 months; we refresh earlier to stay safe.
        exp = now + timedelta(days=150)
        payload = {
            "iss": team_id,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        headers = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
        dev_token = jwt.encode(payload=payload, key=private_key, algorithm="ES256", headers=headers)

        state["developer_token"] = dev_token
        state["developer_token_expires_at"] = int(exp.timestamp())
        self._save_state(state)
        return str(dev_token)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        require_user_token: bool = False,
    ) -> dict[str, Any]:
        developer_refreshed = False

        for attempt in range(self.max_retries + 1):
            headers = {
                "Authorization": f"Bearer {self.get_developer_token(force_refresh=developer_refreshed)}",
                "Accept": "application/json",
            }
            if require_user_token:
                headers["Music-User-Token"] = self.get_music_user_token()

            response = httpx.request(
                method=method,
                url=f"{APPLE_BASE_URL}{path}",
                params=params,
                json=json_body,
                headers=headers,
                timeout=self.timeout_seconds,
            )

            if response.status_code == 401 and not developer_refreshed:
                # Developer token may be expired/revoked. Refresh once and retry.
                developer_refreshed = True
                continue

            # Log rate limit headers for observability
            rate_limit = response.headers.get("X-RateLimit-Limit")
            rate_remaining = response.headers.get("X-RateLimit-Remaining")
            rate_reset = response.headers.get("X-RateLimit-Reset")
            if rate_limit or rate_remaining or rate_reset:
                _log.debug(
                    "Apple Music API rate limit: limit=%s, remaining=%s, reset=%s",
                    rate_limit,
                    rate_remaining,
                    rate_reset,
                )

            if response.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                delay = self.backoff_seconds * (2**attempt)
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    delay = max(delay, float(_as_int(retry_after, 0) or 0))
                _log.warning(
                    "Apple Music API %s %s: HTTP %d, retrying after %.1fs (attempt %d/%d)",
                    method,
                    path,
                    response.status_code,
                    delay,
                    attempt + 1,
                    self.max_retries,
                )
                time.sleep(delay)
                continue

            response.raise_for_status()
            if not response.content:
                return {}
            data = response.json()
            if isinstance(data, dict):
                return data
            return {"data": data}

        raise RuntimeError("Apple Music API request failed after retries")

    def get_storefront(self, *, force_refresh: bool = False) -> str:
        state = self._get_cached_state()
        cached = state.get("storefront")
        if cached and not force_refresh:
            return str(cached)

        payload = self._request("GET", "/me/storefront", require_user_token=True)
        data = payload.get("data") or []
        if not data:
            raise RuntimeError("Unable to detect Apple Music storefront")
        storefront = str(data[0].get("id") or "")
        if not storefront:
            raise RuntimeError("Apple Music storefront response missing id")
        state["storefront"] = storefront
        self._save_state(state)
        return storefront

    def search_by_isrc(self, isrc: str, storefront: str | None = None) -> dict[str, Any] | None:
        needle = (isrc or "").strip().upper()
        if not needle:
            return None
        sf = storefront or self.get_storefront()
        payload = self._request(
            "GET",
            f"/catalog/{sf}/songs",
            params={"filter[isrc]": needle, "limit": 5},
            require_user_token=True,
        )
        songs = payload.get("data") or []
        for song in songs:
            attrs = song.get("attributes") or {}
            if str(attrs.get("isrc") or "").upper() == needle:
                return _normalize_song(song)
        if songs:
            return _normalize_song(songs[0])
        return None

    def search_track(
        self,
        *,
        title: str,
        artist: str,
        duration_s: int | None = None,
        storefront: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        sf = storefront or self.get_storefront()
        term = " ".join(p for p in [title.strip(), artist.strip()] if p)
        payload = self._request(
            "GET",
            f"/catalog/{sf}/search",
            params={"term": term, "types": "songs", "limit": limit},
            require_user_token=True,
        )
        songs = (((payload.get("results") or {}).get("songs") or {}).get("data")) or []
        out = [_normalize_song(song) for song in songs]

        if duration_s is not None:
            duration_ms = int(duration_s) * 1000
            out.sort(key=lambda s: abs(_as_int(s.get("duration_ms"), duration_ms) - duration_ms))
        return out

    def list_library_playlists(self) -> list[dict[str, Any]]:
        """Return all library playlists (id + name)."""
        playlists: list[dict[str, Any]] = []
        next_url: str | None = "/me/library/playlists"

        while next_url:
            payload = self._request("GET", next_url, params={"limit": 100}, require_user_token=True)
            for item in payload.get("data") or []:
                attrs = item.get("attributes") or {}
                playlists.append({"id": str(item.get("id") or ""), "name": str(attrs.get("name") or "")})
            next_raw = payload.get("next")
            next_url = str(next_raw) if next_raw else None
            if next_url and next_url.startswith("https://api.music.apple.com/v1"):
                next_url = next_url.replace("https://api.music.apple.com/v1", "", 1)

        return playlists

    def list_library_song_ids(self) -> set[str]:
        """Return all song IDs currently present in the user's Apple Music library."""
        song_ids: set[str] = set()
        next_url: str | None = "/me/library/songs"

        while next_url:
            payload = self._request("GET", next_url, params={"limit": 100}, require_user_token=True)
            for item in payload.get("data") or []:
                song_id = item.get("id")
                if song_id:
                    song_ids.add(str(song_id))

            next_raw = payload.get("next")
            next_url = str(next_raw) if next_raw else None
            if next_url and next_url.startswith("https://api.music.apple.com/v1"):
                next_url = next_url.replace("https://api.music.apple.com/v1", "", 1)

        return song_ids

    def ensure_playlist(self, name: str, description: str | None = None) -> dict[str, Any]:
        target_name = (name or "").strip()
        if not target_name:
            raise ValueError("Playlist name cannot be empty")

        for pl in self.list_library_playlists():
            if pl["name"].strip().lower() == target_name.lower():
                return pl

        body: dict[str, Any] = {
            "attributes": {
                "name": target_name,
            }
        }
        if description:
            body["attributes"]["description"] = description

        payload = self._request("POST", "/me/library/playlists", json_body=body, require_user_token=True)
        data = payload.get("data") or []
        if not data:
            raise RuntimeError("Apple Music playlist creation returned no data")
        playlist = data[0]
        attrs = playlist.get("attributes") or {}
        return {"id": str(playlist.get("id") or ""), "name": str(attrs.get("name") or target_name)}

    def add_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]) -> AddResult:
        ids = [str(t).strip() for t in track_ids if str(t).strip()]
        if not ids:
            return AddResult(requested=0, succeeded=0, failed=0)

        chunk_size = 100
        succeeded = 0

        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            body = {
                "data": [{"id": track_id, "type": "songs"} for track_id in chunk],
            }
            self._request(
                "POST",
                f"/me/library/playlists/{playlist_id}/tracks",
                json_body=body,
                require_user_token=True,
            )
            succeeded += len(chunk)

        return AddResult(requested=len(ids), succeeded=succeeded, failed=len(ids) - succeeded)

    def get_playlist_tracks(self, playlist_id: str) -> list[dict[str, Any]]:
        """Get all tracks in a playlist."""
        tracks: list[dict[str, Any]] = []
        next_url: str | None = f"/me/library/playlists/{playlist_id}/tracks"

        while next_url:
            payload = self._request("GET", next_url, params={"limit": 100}, require_user_token=True)
            for item in payload.get("data") or []:
                tracks.append(item)
            next_raw = payload.get("next")
            next_url = str(next_raw) if next_raw else None
            if next_url and next_url.startswith("https://api.music.apple.com/v1"):
                next_url = next_url.replace("https://api.music.apple.com/v1", "", 1)

        return tracks

    def clear_playlist_tracks(self, playlist_id: str) -> int:
        """Remove all tracks from a playlist. Returns count of tracks removed."""
        tracks = self.get_playlist_tracks(playlist_id)
        if not tracks:
            return 0

        track_ids = [str(t.get("id") or "") for t in tracks if t.get("id")]
        if not track_ids:
            return 0

        # Delete in chunks
        chunk_size = 100
        removed = 0

        for i in range(0, len(track_ids), chunk_size):
            chunk = track_ids[i : i + chunk_size]
            # Use comma-separated track IDs as query parameter
            track_ids_param = ",".join(chunk)
            self._request(
                "DELETE",
                f"/me/library/playlists/{playlist_id}/tracks",
                params={"ids[library-songs]": track_ids_param},
                require_user_token=True,
            )
            removed += len(chunk)

        return removed

    def add_tracks_to_library(self, track_ids: list[str]) -> AddResult:
        ids = [str(t).strip() for t in track_ids if str(t).strip()]
        if not ids:
            return AddResult(requested=0, succeeded=0, failed=0)

        chunk_size = 100
        succeeded = 0

        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            # Apple library add endpoint accepts ids[songs] as a comma-separated query parameter.
            self._request(
                "POST",
                "/me/library",
                params={"ids[songs]": ",".join(chunk)},
                require_user_token=True,
            )
            succeeded += len(chunk)

        return AddResult(requested=len(ids), succeeded=succeeded, failed=len(ids) - succeeded)


def get_apple_client() -> AppleMusicClient:
    return AppleMusicClient()
