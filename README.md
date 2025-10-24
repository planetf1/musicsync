# MusicSync

Sync your Spotify profile to TIDAL.

Focus order:

1. Followed artists (end-to-end implemented)
2. Favorite (liked) tracks
3. Playlists
4. Library extras (albums, etc.)

## What it does now

- Authenticate with Spotify (OAuth) and TIDAL (OAuth simple flow)
- Fetch your Spotify followed artists
- Find the best matching artists on TIDAL (fuzzy name match)
- Auto-match when confidence is high; otherwise ask you to choose a match
- Add matched artists to your TIDAL favorites
- Idempotent: won’t add duplicates; safe to re-run
- Simple web UI to connect accounts, start sync, view progress, and resolve ambiguous matches

## Prerequisites

- Python 3.10+
- A Spotify developer app with Redirect URI set to `http://localhost:8000/auth/spotify/callback`

Create `.env` in the project root:

```bash
SPOTIFY_CLIENT_ID=<your id>
SPOTIFY_CLIENT_SECRET=<your secret>
SPOTIFY_REDIRECT_URI=http://localhost:8000/auth/spotify/callback
APP_HOST=127.0.0.1
APP_PORT=8000
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open <http://localhost:8000> in your browser.

## Notes on TIDAL login

The app uses TIDAL’s OAuth simple flow via the `tidalapi` package. When you click “Connect TIDAL,” you’ll see a link to open and authorize. After completing the login in the browser, return to this app; the session is saved and reused.

## Idempotency

- Mappings from Spotify → TIDAL artist IDs are stored in a local SQLite DB `musicsync.db`.
- Before adding a favorite on TIDAL, we check if it’s already present.
- You can re-run sync safely.

## Next steps (scoped)

- Favorite tracks sync (liked songs) with robust matching
- Playlist sync (create/update playlists on TIDAL)
- Progress persistence and resumable sessions

 
