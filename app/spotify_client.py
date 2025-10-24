from __future__ import annotations

import os
import time
from typing import Dict

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

from .storage import load_token, save_token, SessionLocal

SCOPES = [
    "user-follow-read",
    "user-library-read",
    "playlist-read-private",
]


def _get_oauth() -> SpotifyOAuth:
    load_dotenv()
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8000/auth/spotify/callback")
    if not client_id or not client_secret:
        raise RuntimeError("Missing SPOTIFY_CLIENT_ID/SECRET in .env")
    return SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope=SCOPES)


def get_authorize_url() -> str:
    oauth = _get_oauth()
    return oauth.get_authorize_url()


def exchange_code_for_token(code: str) -> Dict:
    oauth = _get_oauth()
    token_info = oauth.get_access_token(code, as_dict=True)
    with SessionLocal() as db:
        save_token(db, "spotify", token_info)
    return token_info


def _refresh_if_needed(token_info: Dict) -> Dict:
    # spotipy token_info contains 'expires_at' (epoch seconds)
    if token_info.get("expires_at", 0) - int(time.time()) < 60:
        oauth = _get_oauth()
        refreshed = oauth.refresh_access_token(token_info["refresh_token"])
        with SessionLocal() as db:
            save_token(db, "spotify", refreshed)
        return refreshed
    return token_info


def get_spotify_client() -> spotipy.Spotify:
    with SessionLocal() as db:
        token_info = load_token(db, "spotify")
    if not token_info:
        raise RuntimeError("Spotify not authorized. Visit /auth/spotify/login")
    token_info = _refresh_if_needed(token_info)
    return spotipy.Spotify(auth=token_info["access_token"])  # type: ignore
