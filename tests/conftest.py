"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_apple_song():
    """Sample Apple Music song data structure."""
    return {
        "id": "123456789",
        "type": "songs",
        "attributes": {
            "name": "Test Song",
            "artistName": "Test Artist",
            "albumName": "Test Album",
            "durationInMillis": 240000,
            "isrc": "USUM71234567",
            "contentRating": "clean",
            "playParams": {"id": "123456789", "kind": "song"},
        },
    }


@pytest.fixture
def sample_apple_playlist():
    """Sample Apple Music playlist data structure."""
    return {
        "id": "p.ABC123XYZ",
        "type": "library-playlists",
        "attributes": {
            "name": "My Playlist",
            "description": {"standard": "Test playlist description"},
            "canEdit": True,
        },
    }
