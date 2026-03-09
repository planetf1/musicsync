# AGENTS guide

This document is for AI agents and contributors to understand the architecture,
data model, and conventions of this repository. It should help you implement
new features safely and consistently.

## Overview

MusicSync is a FastAPI web app that syncs a user's Spotify library to TIDAL. It
focuses on:

- Followed artists → TIDAL favorites
- Liked tracks → TIDAL favorites
- User-owned playlists → TIDAL playlists

Core goals:

- Idempotent operations (safe to re-run)
- Background jobs with progress polling
- Robust matching (ISRC-first, otherwise normalized fuzzy matching)
- Local persistence of mappings and per-playlist track snapshots

## Project layout

- `app/main.py`: FastAPI app, routes, and background job logic
- `app/storage.py`: SQLAlchemy models, DB session and helpers (CRUD, listings, exports)
- `app/spotify_client.py`: Spotify auth + Spotipy client factory
- `app/tidal_client.py`: TIDAL device login via tidalapi and session management
- `app/matching.py`: Fuzzy scoring and normalization helpers for artists/tracks
- `app/templates/`: Jinja2 templates for UI pages
- `app/static/`: CSS and assets
- `musicsync.db`: SQLite database (created in repo root)

## Database schema (high level)

- `tokens` — store serialized tokens and session blobs per service.
- `artist_map` — Spotify artist → TIDAL artist mapping (with confidence,
  resolved flag, last_synced_at)
- `pending_resolution` — artist-level pending candidates for manual resolution
- `track_map` — Spotify track → TIDAL track mapping (artist ids, ISRC,
  confidence, resolved, last_synced_at)
- `pending_track_resolution` — track-level pending candidates for manual resolution
- `artist_sync_event` / `track_sync_event` — audit of actions (auto/manual)
- `playlist_map` — Spotify playlist → TIDAL playlist mapping
- `playlist_track` — snapshot of tracks in a Spotify playlist (ordered), with
  optional mapped TIDAL track ids

SQLite tables are created via SQLAlchemy `Base.metadata.create_all()` and a few
light raw-SQL migrations for older tables. Avoid destructive migrations.

## Background jobs

Jobs run in a thread and report progress via `/sync/.../status` endpoints. A
simple in-memory `_jobs` dict tracks state:

- `state`: `pending` | `running` | `done` | `error`
- `total`, `processed`, and operation-specific counters (e.g., `auto_matched`,
  `created`, `updated`, `pending_count`)

Long-running syncs:

- Artists: fetch followed artists, match to TIDAL, update favorites, upsert
  mapping, queue pending when ambiguous.
- Tracks: fetch liked tracks, try ISRC-first, then fuzzy match; add favorites;
  upsert mapping; queue pending when ambiguous.
- Playlists: list user-owned Spotify playlists; ensure a TIDAL playlist; build
  ordered Spotify track list; map to TIDAL; add missing tracks; snapshot the
  ordered results into `playlist_track`.

## Matching rules

- Artists: normalization of names (diacritics, punctuation, leading "The",
  parentheticals) followed by fuzzy matching; prefer exact normalized match when
  available.
- Tracks: ISRC exact match is a hard win; otherwise normalized fuzzy
  title+artist with duration tolerance.

## UI routes

- `/` — index; connect accounts; start syncs; show progress
- `/pending`, `/pending-tracks` — manual resolution queues
- `/library/artists`, `/library/tracks`, `/library/playlists` — library views
  with search/sort/pagination
- `/library/playlists/{spotify_id}` — per-playlist track listing from local DB snapshot
- `/backup/*` — JSON exports

## Adding features safely

- Follow the background job pattern for long operations. Always report
  incremental progress.
- Persist mappings and snapshots; assume re-runs; avoid destructive updates
  outside the intended scope.
- Use the storage helpers or add new ones for query patterns. Keep listing
  helpers consistent (search/sort/pagination, `page_size=0` means "all").
- When touching matching, preserve the ISRC-first logic for tracks and
  high-confidence autos for artists. Log or queue ambiguous items.
- Keep external API calls chunked (e.g., playlist track adds) to avoid rate
  limits and partial failures. Retry cautiously where safe.

## Coding conventions

- Python 3.13+; FastAPI; SQLAlchemy ORM; Spotipy; tidalapi; rapidfuzz.
- Logging: we currently use f-strings in the codebase; feel free to convert to
  lazy `%` formatting in future cleanups, but keep messages informative and
  sparse at INFO.
- Avoid broad `except Exception:` unless you are at job/task boundaries and you
  log/record state appropriately.

## Common extension points

- New library pages: add a storage `list_*` helper, a route under `/library/*`,
  and a Jinja template mirroring existing patterns (search, sort, pagination,
  page-size including `all`).
- New sync types: add a background job function with counters; start/status
  endpoints; a button and polling script on the index page.
- New exports: add `export_*` helper and map to `/backup/*` JSON endpoints.

## Tests and manual validation

- Quick smoke (within env): `uv run python -c "import app.main; print('OK')"`
- Manual flows: `uv run --with uvicorn uvicorn app.main:app --reload` then connect
  Spotify/TIDAL, trigger syncs, review Pending, browse Library pages.

## Runtime notes for agents

- Spotify token refresh
  - Use `app.spotify_client.call_spotify(lambda sp: ...)` for Spotipy calls.
  - The helper retries once on Spotify 401 by refreshing the token.
  - Avoid creating long-lived clients that outlive token expiry.

- Playlist adds on TIDAL
  - Do not assume `allow_duplicates` kwarg exists.
  - Prefer int track IDs when calling tidalapi.
  - The code tries multiple `Playlist.add` signatures and retries per-item as
    needed, then re-fetches and does a second pass for any missing items.

- Counters semantics (playlists)
  - `created` increments when a new TIDAL playlist is created.
  - `updated` increments only when a pre-existing TIDAL playlist actually had
    tracks added during this run.

- Skipping logic
  - Skips are based on what is already present in the TIDAL playlist, not on
    local DB mappings. DB is used for mapping only.

- Logs
  - Per-playlist logs include: `mapped`, `existing_on_tidal`, `to_add`,
    `added_to_tidal`, and when applicable `retrying missing`.

## Low-resource runs

- Favor pure-Python IO stack to avoid large native deps.

```bash
uv run --with uvicorn uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --loop asyncio --http h11 --no-access-log
```

## Known limits and behaviors

- TIDAL favorites have practical limits (~10k per category) based on public reports.
- Large playlists can vary by client/API; additions are chunked and duplicates
  are skipped.
- The per-playlist snapshot reflects Spotify order at the time of sync;
  subsequent edits on TIDAL are not back-propagated.

If you need a deep dive into playlist specifics, see [docs/PLAYLISTS.md](docs/PLAYLISTS.md).

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

<!-- BEGIN BEADS INTEGRATION -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Git-friendly: Dolt-powered version control with native sync
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update <id> --claim --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Auto-Sync

bd automatically syncs via Dolt:

- Each write auto-commits to Dolt history
- Use `bd dolt push`/`bd dolt pull` for remote sync
- No manual export/import needed!

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and docs/QUICKSTART.md.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

<!-- END BEADS INTEGRATION -->
