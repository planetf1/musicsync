# MusicSync

Sync your Spotify library to TIDAL and Apple Music with a friendly web UI,
idempotent operations, and clear progress. Focus areas: followed artists, liked
tracks, and user-owned playlists.

Looking for a quick visual tour? See the Screenshots & Feature Guide:

- [Screenshots & Feature Guide](docs/SCREENSHOTS.md)

## Features

- Connect accounts
  - Spotify OAuth (Spotipy)
  - TIDAL device login (tidalapi)
  - Apple Music authentication (MusicKit JS)
- Followed artists (Spotify → TIDAL Favorites)
  - Robust normalization (diacritics, punctuation, “The” stripping, parentheticals)
  - Fuzzy matching using rapidfuzz
  - Auto-match with high confidence; otherwise queue for manual resolution
  - Idempotent: checks TIDAL favorites to avoid duplicates
- Liked tracks (Spotify → TIDAL Favorites, Spotify → Apple Music Library)
  - ISRC-first matching with duration tolerance
  - Fallback fuzzy scoring for titles/artists
  - Manual resolution UI when needed
- Playlists (Spotify → TIDAL, Spotify → Apple Music)
  - Syncs user-owned playlists: creates or reuses on TIDAL/Apple Music
  - Maps tracks via TrackMap (or matching) and adds missing tracks
  - Stores an ordered per-playlist track snapshot locally for browsing
- Library browsing
  - Artists, Tracks, Playlists pages with search, sorting, pagination, and
    page-size presets (including “all”)
  - Direct links out to Spotify/TIDAL entities
  - Playlists list shows each playlist's track count and total runtime; detail
    page shows the per-playlist track list with durations and a summary
- Backups & Exports
  - Download database: `/backup/db`
  - Multi-format exports for artists, tracks, playlists: `/export/{kind}?format=json|csv|md&download=1`
  - JSON exports for Artists, Tracks, Playlists (legacy): `/backup/*`

## Prerequisites

- Python 3.13+
- A Spotify Developer App with a Redirect URI set to `http://localhost:8000/auth/spotify/callback`

Create a `.env` in the project root (see `.env.example` for a template):

```bash
SPOTIFY_CLIENT_ID=<your id>
SPOTIFY_CLIENT_SECRET=<your secret>
SPOTIFY_REDIRECT_URI=http://localhost:8000/auth/spotify/callback

# Optional: disable TIDAL integration (default: enabled)
# Set to 'false', '0', or 'no' to disable TIDAL sync
TIDAL_ENABLED=true

# Optional: disable Apple Music integration (default: enabled)
# Set to 'false', '0', or 'no' to disable Apple Music sync
APPLE_ENABLED=true

# Apple Music integration (requires Apple Developer account with MusicKit key)
APPLE_MUSIC_TEAM_ID=<your Apple Developer Team ID>
APPLE_MUSIC_KEY_ID=<your MusicKit key ID>
APPLE_MUSIC_PRIVATE_KEY_PATH=/absolute/path/to/AuthKey_XXXXXXXXXX.p8

# Optional: used by the musicsync CLI entrypoint
MUSICSYNC_HOST=127.0.0.1
MUSICSYNC_PORT=8000
MUSICSYNC_RELOAD=1
```

## Install

Option A — with uv (recommended)

```bash
# 1) Install uv (macOS)
brew install astral-sh/uv/uv || curl -LsSf https://astral.sh/uv/install.sh | sh

# 2) Run directly with uv (no venv needed). Note: invoke 'uvicorn' as the command.
uv run --with uvicorn uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Compatibility mode (avoids native uvloop/httptools)
uv run --with uvicorn \
  uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 \
  --loop asyncio --http h11

# (Alternative)
# uv run --with uvicorn \
#   python -m uvicorn app.main:app \
#   --host 127.0.0.1 --port 8000 --reload

# Or install the CLI tool from this repo via uv
uv tool install "git+https://github.com/planetf1/musicsync.git"
musicsync  # starts the server using uvicorn
# You can tune the loop/http with env vars, e.g.:
# MUSICSYNC_LOOP=asyncio MUSICSYNC_HTTP=h11 MUSICSYNC_RELOAD=0 musicsync
```

Option B — classic venv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# For low-memory environments (avoid reload watchers)
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Pure-Python compatibility (no uvloop/httptools)
uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop asyncio --http h11
```

Then open:

- App: <http://localhost:8000>
- Status: <http://localhost:8000/status>
- Artists library: <http://localhost:8000/library/artists>
- Tracks library: <http://localhost:8000/library/tracks>
- Playlists library: <http://localhost:8000/library/playlists>

## Using the app

### Connect accounts

- Click “Connect Spotify” and complete the OAuth flow.
- Click “Connect TIDAL” → follow the device login link and confirm.- Click "Connect Apple Music" → authenticate with your Apple ID via MusicKit
  (browser popup). Requires Apple Music subscription.
### Sync

- “Sync Followed Artists” adds matches to TIDAL favorites.
- “Sync Liked Tracks” adds songs to TIDAL favorites using ISRC-first mapping.
- “Sync Playlists” creates/updates your user-owned Spotify playlists on TIDAL
  and records the ordered track list locally.- Apple Music sync:
  - "Sync Playlists to Apple Music" creates/updates your Spotify playlists on
    Apple Music with ISRC-first matching.
  - "Sync Liked Tracks to Apple Music" adds your Spotify liked tracks to your
    Apple Music library.
  - Note: Apple Music does not support artist following via API.
### Review & browse

- Pending matches: resolve from the Pending pages.
- Library pages: browse synced Artists/Tracks/Playlists with search, sort
  (including Genres), pagination, and page-size presets.
- Playlist details: click a playlist name in the Playlists library to view the
  locally stored track list (includes Spotify links and TIDAL links where
  mapped). A summary card shows total tracks and total duration (H:MM:SS).

### Genres (optional)

- The Artists page shows a Top Genres widget and supports filtering and
  sorting by genres.
- Use the “Refresh Genres” button to fetch or update genres for your artists
  from Spotify. Enable “Missing only” for a quick top-up.

## Notes on TIDAL login

We use the `tidalapi` device login flow. When you press “Connect TIDAL,” the
UI shows a link/code to authorize. After completing it, refresh the app; your
session is cached for reuse.
## Notes on Apple Music setup

Apple Music integration requires an Apple Developer account with a MusicKit
identifier and private key:

1. **Apple Developer Program**: Enroll at <https://developer.apple.com/programs/>
   (costs $99/year for individual accounts).
2. **MusicKit Identifier**: Create a MusicKit identifier in your Apple
   Developer account → Certificates, Identifiers & Profiles → Identifiers →
   MusicKit.
3. **Private Key**: Generate a MusicKit private key (`.p8` file) with MusicKit
   enabled. Download and save it securely (you can only download it once).
4. **Environment Variables**: Configure in `.env`:
   - `APPLE_MUSIC_TEAM_ID`: Your 10-character Apple Developer Team ID
   - `APPLE_MUSIC_KEY_ID`: The Key ID from your MusicKit private key (10
     characters, e.g., `ABCD123456`)
   - `APPLE_MUSIC_PRIVATE_KEY_PATH`: Absolute path to your `.p8` file

When you click "Connect Apple Music" in the UI, a popup opens for Apple ID
authentication via MusicKit JS. The app generates a JWT developer token
(ES256) and obtains a music user token for API access. Tokens are cached in
the database for reuse.
## Idempotency & persistence

- SQLite DB: `musicsync.db` in the project root.
- Mappings and sync events are stored; favorites and playlist adds skip duplicates.
- You can re-run syncs safely; the app stores per-playlist track snapshots in
  DB for browsing.

## Limits and caveats

- TIDAL favorites: user reports indicate ~10k limit per category (e.g.,
  albums). If you hit the limit, TIDAL returns errors.
- Playlist size: clients and APIs may behave differently for very large
  playlists; the app adds tracks in chunks and avoids duplicates.
- Matching: ISRCs are preferred; otherwise, robust normalized fuzzy matching is
  used, which may queue some items for manual review.

## Backup endpoints

- Download SQLite DB: `/backup/db`
- JSON (legacy): `/backup/artists`, `/backup/tracks`, `/backup/playlists`
- Multi-format exports: `/export/{artists|tracks|playlists}?format=json|csv|md&download=1`

## Contributing & design

- See [AGENTS.md](AGENTS.md) for an overview of the architecture, database
  schema, and background job design (especially helpful for automated
  tooling/agents).
- See [docs/PLAYLISTS.md](docs/PLAYLISTS.md) for a deeper dive into playlist
  sync logic and trade-offs.

## Service coverage and roadmap

This project treats Spotify as the source of truth and syncs to other services.
Capabilities differ by target service due to API surface area and reliability.

- Spotify (source)
  - Rich official Web API and OAuth.
  - Followed artists, liked tracks, and playlists are fully supported as a
    source for sync.

- TIDAL (current target)
  - Supported today for artists (favorites), liked tracks (favorites), and
    playlists (create/update, ordered adds). Uses device login via `tidalapi`.
  - Notes: additions are chunked to avoid rate limits; user-reported favorites
    limits around ~10k per category may apply.

- Apple Music (current target)
  - Implemented: playlists (create/update), liked tracks (add to library).
    Uses JWT authentication (ES256) with MusicKit Developer Token + Music User
    Token (via MusicKit JS).
  - Matching: ISRC-first, then fuzzy title/artist/duration scoring.
  - Limitations: Apple Music API does not support programmatic artist
    following/favoriting; "love/dislike" flags are read-only.
  - Notes: storefront/region matters for search and availability; additions
    are chunked (100 tracks per batch).
  - Implementation notes: [docs/APPLE_MUSIC_INTEGRATION.md](docs/APPLE_MUSIC_INTEGRATION.md)

- YouTube Music (planned target)
  - No official public API; integration relies on `ytmusicapi` (reverse-
    engineered, now with OAuth). Feasible for playlists.
  - Liked tracks are best represented as a dedicated playlist (e.g., "Liked
    from Spotify") for determinism; direct "like" operations may not be
    reliable across accounts.
  - Matching is primarily text + duration based; prefer song entities over
    general videos and bias toward official artist channels.
  - Design doc: [docs/YOUTUBE_MUSIC_INTEGRATION.md](docs/YOUTUBE_MUSIC_INTEGRATION.md)

We expose per-service capabilities clearly in the UI and keep an unmatched
review flow for ambiguous mappings. Planned targets are feature-flagged and
will roll out gradually.

## Troubleshooting

- “Connect TIDAL” shows pending forever: open the provided link, approve, then
  click “Check Status” or refresh.- "Connect Apple Music" fails or shows errors:
  - Verify `APPLE_MUSIC_TEAM_ID`, `APPLE_MUSIC_KEY_ID`, and
    `APPLE_MUSIC_PRIVATE_KEY_PATH` are set correctly in `.env`.
  - Ensure the `.p8` private key file exists at the specified path.
  - Check that your MusicKit identifier is enabled and active in your Apple
    Developer account.
  - Ensure you have an active Apple Music subscription (required for API access).
  - Check browser console for MusicKit JS errors (popup blocker, CORS issues).
- Apple Music sync shows "track not found": storefront/region availability
  varies; some tracks may not be available in your Apple Music region.- Spotify auth errors: ensure Redirect URI matches exactly.
- Missing rapidfuzz: `pip install -r requirements.txt` (it’s included).
- Unicode/diacritics oddities in matches: normalization strips accents and
  punctuation; use manual resolve when in doubt.
- Server doesn’t start (curl returns 000/7): ensure uvicorn is running and no
  firewall is blocking 127.0.0.1:8000.
- Process killed (exit 137) on start: try running without `--reload` to reduce
  memory use. Example:

   ```bash
   uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

  If using uv, this variant also avoids reload overhead:

   ```bash
   uv run --with uvicorn uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

  You can also reduce logging load with `--no-access-log`.

   ```bash
   uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
   ```

  If you installed the CLI via uv or pipx (`musicsync` command), you can
  control server internals with env vars for a pure-Python stack:


  ```bash
  MUSICSYNC_LOOP=asyncio \
  MUSICSYNC_HTTP=h11 \
  MUSICSYNC_RELOAD=0 \
  MUSICSYNC_ACCESS_LOG=0 \
  musicsync
  ```

  Then check the diagnostics page at <http://localhost:8000/status> to confirm
  the server is up and accounts are connected.

## License

MIT

---

## Developer Setup (optional)

Install dev tools and pre-commit hooks:

```bash
# Using uv
uv pip install -e .[dev]
pre-commit install

# Or with a venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pre-commit install
```

Run linters/type checks locally:

```bash
ruff check --fix . && ruff format .
mypy .
```

Run all hooks and secrets scan manually:

```bash
pre-commit run --all-files
gitleaks detect --no-git --source . --no-banner
```
