# MusicSync

Sync your Spotify library to TIDAL with a friendly web UI, idempotent operations, and clear progress. Focus areas: followed artists, liked tracks, and user-owned playlists.

## Features

- Connect accounts
  - Spotify OAuth (Spotipy)
  - TIDAL device login (tidalapi)
- Followed artists (Spotify → TIDAL Favorites)
  - Robust normalization (diacritics, punctuation, “The” stripping, parentheticals)
  - Fuzzy matching using rapidfuzz
  - Auto-match with high confidence; otherwise queue for manual resolution
  - Idempotent: checks TIDAL favorites to avoid duplicates
- Liked tracks (Spotify → TIDAL Favorites)
  - ISRC-first matching with duration tolerance
  - Fallback fuzzy scoring for titles/artists
  - Manual resolution UI when needed
- Playlists (Spotify → TIDAL)
  - Syncs user-owned playlists: creates or reuses on TIDAL
  - Maps tracks via TrackMap (or matching) and adds missing tracks
  - Stores an ordered per-playlist track snapshot locally for browsing
- Library browsing
  - Artists, Tracks, Playlists pages with search, sorting, pagination, and page-size presets (including “all”)
  - Direct links out to Spotify/TIDAL entities
- Backups
  - JSON exports for Artists, Tracks, Playlists

## Prerequisites

- Python 3.10+
- A Spotify Developer App with a Redirect URI set to `http://localhost:8000/auth/spotify/callback`

Create a `.env` in the project root:

```bash
SPOTIFY_CLIENT_ID=<your id>
SPOTIFY_CLIENT_SECRET=<your secret>
SPOTIFY_REDIRECT_URI=http://localhost:8000/auth/spotify/callback
APP_HOST=127.0.0.1
APP_PORT=8000
```

## Install

Option A — with uv (recommended)

```bash
# 1) Install uv (macOS)
brew install astral-sh/uv/uv || curl -LsSf https://astral.sh/uv/install.sh | sh

# 2) Run directly with uv (no venv needed)
uv run --with uvicorn app.main:app -- --host 127.0.0.1 --port 8000 --reload

# Or install the CLI tool from this repo via uv
uv tool install "git+https://github.com/planetf1/musicsync.git"
musicsync  # starts the server using uvicorn
```

Option B — classic venv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open:

- App: <http://localhost:8000>
- Artists library: <http://localhost:8000/library/artists>
- Tracks library: <http://localhost:8000/library/tracks>
- Playlists library: <http://localhost:8000/library/playlists>

## Using the app

1) Connect accounts
   - Click “Connect Spotify” and complete the OAuth flow.
   - Click “Connect TIDAL” → follow the device login link and confirm.

2) Sync
   - “Sync Followed Artists” adds matches to TIDAL favorites.
   - “Sync Liked Tracks” adds songs to TIDAL favorites using ISRC-first mapping.
   - “Sync Playlists” creates/updates your user-owned Spotify playlists on TIDAL and records the ordered track list locally.

3) Review & browse
   - Pending matches: resolve from the Pending pages.
   - Library pages: browse synced Artists/Tracks/Playlists with search/sort/pagination.
   - Playlist details: click a playlist name in the Playlists library to view the locally stored track list (includes Spotify links and TIDAL links where mapped).

## Notes on TIDAL login

We use the `tidalapi` device login flow. When you press “Connect TIDAL,” the UI shows a link/code to authorize. After completing it, refresh the app; your session is cached for reuse.

## Idempotency & persistence

- SQLite DB: `musicsync.db` in the project root.
- Mappings and sync events are stored; favorites and playlist adds skip duplicates.
- You can re-run syncs safely; the app stores per-playlist track snapshots in DB for browsing.

## Limits and caveats

- TIDAL favorites: user reports indicate ~10k limit per category (e.g., albums). If you hit the limit, TIDAL returns errors.
- Playlist size: clients and APIs may behave differently for very large playlists; the app adds tracks in chunks and avoids duplicates.
- Matching: ISRCs are preferred; otherwise, robust normalized fuzzy matching is used, which may queue some items for manual review.

## Backup endpoints

- Artists: `/backup/artists`
- Tracks: `/backup/tracks`
- Playlists: `/backup/playlists`

## Contributing & design

- See AGENTS.md for an overview of the architecture, database schema, and background job design (especially helpful for automated tooling/agents).
- See docs/PLAYLISTS.md for a deeper dive into playlist sync logic and trade-offs.

## Troubleshooting

- “Connect TIDAL” shows pending forever: open the provided link, approve, then click “Check Status” or refresh.
- Spotify auth errors: ensure Redirect URI matches exactly.
- Missing rapidfuzz: `pip install -r requirements.txt` (it’s included).
- Unicode/diacritics oddities in matches: normalization strips accents and punctuation; use manual resolve when in doubt.

## License

MIT


