# Apple Music Integration — Implementation Notes

Status: **Implemented** (playlists and liked tracks)
Owners: Contributors / Maintainers
Last updated: 2026-03-09

## Implementation Summary

Apple Music integration is now live in MusicSync. Key features:

- **Playlists**: Sync user-owned Spotify playlists to Apple Music (create/update with ordered track additions).
- **Liked tracks**: Add Spotify liked tracks to Apple Music library.
- **Matching**: ISRC-first with fuzzy fallback (title/artist/duration scoring).
- **Authentication**: Two-token approach (Developer Token JWT + Music User Token via MusicKit JS).
- **Storage**: Multi-service support in `playlist_map` and `track_map` tables with composite keys.
- **UI**: Connect Apple Music button, playlist sync, and likes sync with progress polling.

**Limitations** (by Apple Music API design):
- No programmatic artist following/favoriting.
- "Love/dislike" flags are read-only; we add tracks to library instead.
- Storefront/region availability varies.

See below for the original design proposal, which guided the implementation.

---

# Original Design Proposal

The following sections capture the design approach that informed the implementation. Most goals were achieved; see the Implementation Summary above for specifics.

## Goals

- Add Apple Music as a sync target alongside TIDAL, using Spotify as the source of truth.
- Support:
  - Playlists: create/update user playlists and add missing tracks in order.
  - Liked tracks: add to the user’s Apple Music library (best-effort parity with Spotify "Likes").
- Preserve project principles: idempotent background jobs with progress, ISRC-first matching, fuzzy fallback, local persistence of mappings and snapshots.

## Non-Goals (for initial version)

- Programmatic artist "follow/favorite" writes on Apple Music (not supported by public MusicKit APIs).
- Setting Apple "love/dislike" ratings (not reliably writable via API). We will treat "Like" as "add to library".
- Two-way sync (Apple → Spotify) or continuous bidirectional reconciliation.

---

## Architecture Overview

- New client: `app/apple_client.py`
  - Handles authentication (Developer Token and Music User Token), storefront detection, and Apple Music REST calls.
  - Exposes high-level methods used by background jobs (search by ISRC, search by text, create playlist, add tracks, add to library).
- Storage updates in `app/storage.py`
  - Persist Apple tokens and Apple-specific mappings (or generalize existing mapping tables by target service).
- Background jobs in `app/main.py`
  - New sync functions and start/status endpoints mirroring the TIDAL flow, with Apple-specific API calls.
- Matching reuse in `app/matching.py`
  - Keep normalization and fuzzy logic; add small heuristics for Apple (explicit/clean, remasters, storefront availability).
- UI updates in `app/templates/`
  - Add Connect Apple Music action (MusicKit login) and Apple sync buttons; show progress and unmatched queues.

---

## Authentication and Tokens

Apple requires two tokens:

1. Developer Token (server-side)
   - JWT (ES256) signed with a MusicKit private key (.p8).
   - Includes Team ID and Key ID; hours-to-months validity (max ~6 months). We will generate on-demand and cache.
2. Music User Token (client-side)
   - Obtained via MusicKit JS after the user signs in and authorizes the app.
   - Sent to backend and stored for making user-specific library/playlist writes.

Proposed env/config inputs (no secrets checked in):

- `APPLE_TEAM_ID`
- `APPLE_KEY_ID`
- `APPLE_PRIVATE_KEY_PATH` (absolute path to the .p8 key on the host)
- Optional metadata for MusicKit JS: `APPLE_MUSICKIT_APP_NAME`, `APPLE_MUSICKIT_APP_BUILD`

Security notes:
- Do not store the raw .p8 in the repository; read it from a secure path.
- Cache Developer Tokens in memory with expiry; rotate proactively.
- Store Music User Tokens encrypted at rest (if feasible) and scoped per user.

---

## Client Module: `app/apple_client.py` (new)

Responsibilities:
- Developer Token: build and cache JWT using `PyJWT` + `cryptography` (ES256).
- User Token management: validate presence and expiry; couple with storefront detection.
- REST helpers: signed requests to Apple Music API (v1) with `Authorization: Bearer <developer-token>` and `Music-User-Token: <user-token>`.
- Rate limiting: backoff and retry on 429/5xx; configurable max retries.

Key methods (interface sketch — not implementation):
- `get_storefront(user_token) -> str`
- `search_by_isrc(isrc: str, storefront: str) -> Optional[AppleTrack]`
- `search_track(title: str, artist: str, duration_s: int, storefront: str) -> list[AppleTrack]`
- `ensure_playlist(name: str, description: str | None) -> ApplePlaylist`
- `add_tracks_to_playlist(playlist_id: str, track_ids: list[str]) -> AddResult`
- `add_tracks_to_library(track_ids: list[str]) -> AddResult`

Data models (lightweight dicts or pydantic models) should capture: `id`, `name`, `artists`, `duration_ms`, `is_explicit`, and store Apple catalog IDs (not library IDs) for playlist additions.

---

## Storage Model Changes (`app/storage.py`)

Tokens:
- Table `tokens` already exists; extend to store Apple tokens keyed by service and user:
  - `service` (e.g., `apple`), `token_type` (`developer`, `user`), `value`, `expires_at`, `created_at`, `updated_at`.

Mappings:
- Option A (generalize): Add a `service` column to `track_map`, `playlist_map` to support multiple targets (TIDAL, Apple).
- Option B (parallel tables): `apple_track_map`, `apple_playlist_map` mirroring existing schemas.

Recommendation: Prefer Option A to reduce duplication and keep listing/export logic consistent. Use a composite key on `(source, target_service)`, where `source` identifies Spotify entities (artist/track/playlist IDs). Store Apple catalog IDs in mapping rows, confidence scores, and `resolved` flags.

Snapshots:
- Reuse `playlist_track` for ordered Spotify track snapshots (service-agnostic). No schema change required.

Migrations:
- Use SQLAlchemy metadata updates; avoid destructive migrations. Provide lightweight raw-SQL migrations for older DBs, following project conventions.

---

## Background Jobs and Endpoints (`app/main.py`)

Endpoints (sketch):
- `GET /apple/connect` — serves a page embedding MusicKit JS to acquire Music User Token.
- `POST /apple/token` — receives the Music User Token, stores it in `tokens` table.
- `POST /sync/apple/playlists/start` — starts playlist sync job.
- `GET /sync/apple/playlists/status` — returns job status and counters.
- Optional: `POST /sync/apple/likes/start` and `/status` — adds Spotify liked tracks to Apple library.

Playlist sync job outline:
1. Preflight: ensure Apple tokens available; resolve user storefront once and cache.
2. Build ordered Spotify track list for each eligible playlist (existing logic).
3. Map tracks to Apple catalog IDs:
   - ISRC-first via `filter[isrc]=`.
   - Fallback to normalized text search with duration tolerance and explicit/clean parity when possible.
   - Persist mapping; queue ambiguous candidates into `pending_track_resolution` with `service=apple`.
4. Ensure Apple playlist exists (create if needed).
5. Diff: compute `to_add` vs existing tracks (fetch once, cache).
6. Add in chunks (50–100) with backoff.
7. Snapshot: keep `playlist_track` updated for local introspection.
8. Counters: `created`, `updated`, `mapped`, `added_to_apple`, `existing_on_apple`, `pending_count`, `skipped_unavailable`.

Liked tracks job (optional initial release):
- Collect Spotify liked tracks; map as above; call `add_tracks_to_library` in chunks; counters for `added_to_library`, `already_in_library`.

Error handling:
- On 401 from Apple, re-create Developer Token; if Music User Token invalid/expired, set job state to `error` with actionable message to reconnect.

---

## Matching Strategy (`app/matching.py`)

- Keep ISRC-first as a hard match.
- Fallback: normalized title + primary artist fuzzy score with duration tolerance (e.g., ±2s to ±5s), selecting the best candidate by:
  1) minimum duration delta, 2) explicit parity with source, 3) popularity/relevance when available, 4) album version preference (studio over live/remaster unless the source hints otherwise).
- Storefront-aware: run searches against the user’s storefront to maximize availability.

---

## UI and Templates (`app/templates/`)

- Index enhancements:
  - "Connect Apple Music" button that launches MusicKit JS login and posts token to backend.
  - "Sync Playlists to Apple Music" action following existing pattern with progress polling.
- Pending resolution views:
  - Extend existing Pending pages to include Apple scope, or create parallel `/pending-apple` if clarity is needed.
- Clear copy on limits:
  - Explain that artist favorites and love/dislike are not synced; we add tracks to library and sync playlists.

---

## Rate Limits and Performance

- Chunk write operations to 50–100 items per request (tune after measurement).
- Exponential backoff on 429/5xx with jitter. Cap retries and surface partial progress.
- Cache storefront and playlist contents to minimize reads.

---

## Configuration and Ops

Environment variables to document (no implementation yet):
- `APPLE_TEAM_ID`
- `APPLE_KEY_ID`
- `APPLE_PRIVATE_KEY_PATH`
- `APPLE_MUSICKIT_APP_NAME` (optional; used in MusicKit JS config)
- `APPLE_MUSICKIT_APP_BUILD` (optional)

Apple Developer setup:
- Enroll in the Apple Developer Program.
- Create a MusicKit key (obtain Key ID, Team ID, and download the .p8).
- Configure allowed domains for MusicKit JS; use HTTPS in production.

---

## Testing Plan

Unit tests:
- Developer Token JWT creation (claims, headers, signature) and caching.
- Storefront detection from the user token.
- Matching: ISRC-first and fuzzy fallback using recorded Apple payload fixtures.

Integration tests (env-guarded):
- Create playlist and add tracks end-to-end with a test Apple account; verify idempotency and ordering.
- Library add flow for liked tracks.

Manual validation:
- Run FastAPI locally; connect Spotify + Apple; sync selected playlists; review Pending and Library pages.

Mocking:
- Add an Apple API adapter interface and a fake implementation for tests.

---

## Security and Privacy

- Never log full tokens or the private key path.
- Encrypt at rest where feasible; otherwise minimize token scope and lifetime.
- Use HTTPS for any MusicKit JS flows in production.
- Respect Apple Music API terms regarding data storage and caching.

---

## Limitations and Known Behaviors

- Cannot programmatically favorite/follow artists in Apple Music via public APIs.
- "Likes" parity maps to library additions; we do not set "love" flags.
- Regional/storefront mismatches can cause unavailability; log and queue for manual review.
- Library size limits may apply; warn on very large migrations.

### Research update (2026-03-09): artist follow/favorite capability

Result: **not supported** for programmatic write operations in Apple Music API.

Evidence from Apple documentation:

- `Artists` API lists only retrieval operations for catalog/library artists and
  relationship fetches (no create/update/delete/follow endpoints):
  <https://developer.apple.com/documentation/applemusicapi/artists-api>
- `Get a Library Artist` and `Get All Library Artists` are explicitly `GET`
  endpoints and return resource data only:
  <https://developer.apple.com/documentation/applemusicapi/get-a-library-artist>
  <https://developer.apple.com/documentation/applemusicapi/get-all-library-artists>
- User write capabilities documented by Apple focus on playlists and library
  resources, e.g.:
  - Create playlist: <https://developer.apple.com/documentation/applemusicapi/create-a-new-library-playlist>
  - Add tracks to playlist: <https://developer.apple.com/documentation/applemusicapi/add-tracks-to-a-library-playlist>
  - Add resource to library: <https://developer.apple.com/documentation/applemusicapi/add-a-resource-to-a-library>
- Ratings writes exist for songs/albums/playlists/videos/stations, but not
  artists:
  <https://developer.apple.com/documentation/applemusicapi/ratings-api>

Decision for MusicSync:

- Keep "Followed artists" sync as **Spotify → TIDAL only**.
- For Apple Music, continue syncing liked tracks and playlists.
- Optional future fallback: generate an Apple playlist (for example
  "Followed Artists from Spotify") populated with representative tracks.

---

## Rollout Plan

1. Feature flag for Apple integration in UI.
2. Ship auth + token storage behind the flag; validate token acquisition in dev.
3. Implement playlist sync path; release as beta.
4. Add liked-tracks → library sync; expand Pending review UI.
5. Iterate on matching heuristics and performance.

---

## Effort Estimate (rough)

- Apple client + tokens: 1–2 days
- MusicKit JS login flow + backend persistence: 1–2 days
- Playlist sync job + counters: 1–2 days
- Liked tracks to library: 0.5–1 day
- Schema updates + migrations + UI buttons: 0.5–1 day
- Tests + fixtures + docs: 1–2 days

Total: ~1.5–2.5 weeks elapsed including setup and review.

---

## Open Questions

- Mapping storage: generalize existing tables vs dedicated Apple tables — prefer generalization; verify impact on existing queries.
- Deletion semantics: should Apple playlist sync strictly mirror (remove extras) or be additive by default? Consider a user setting.
- Popularity signals: does Apple API surface enough ranking metadata to aid tie-breaks consistently across storefronts?

---

## Implementation Details (Actual Build)

### What Was Built

**Files Created/Modified:**
- `app/apple_client.py`: Complete Apple Music client with JWT generation (ES256), storefront detection, ISRC + fuzzy search, playlist operations, and library additions.
- `app/main.py`: Background jobs for Apple playlist sync and liked tracks sync with progress tracking (`_run_sync_apple_playlists_job`, `_run_sync_apple_likes_job`). Endpoints: `/apple/connect`, `/apple/token`, `/sync/apple/playlists/start|status`, `/sync/apple/likes/start|status`.
- `app/storage.py`: Extended `tokens` table for Apple tokens; generalized `playlist_map` and `track_map` with composite keys `(spotify_id, service)` to support multi-service mappings. Added `list_synced_playlists()` grouping by `spotify_id` to return TIDAL and Apple mappings together.
- `app/templates/`: Updated `index.html` (Apple connect button + sync UI with JavaScript polling), `library_playlists.html` (Apple column), `library_playlist_detail.html` (Apple service indicator), `apple_connect.html` (MusicKit JS integration).

**Key Design Decisions:**
- **Mapping storage**: Chose Option A (generalize existing tables) by adding service discrimination to `playlist_map` and `track_map`. This avoids duplication and keeps listing/export logic consistent.
- **Authentication**: Two-token approach implemented as designed. Developer Token generated on-demand with PyJWT + cryptography (ES256). Music User Token obtained via MusicKit JS popup and stored in `tokens` table.
- **Matching**: ISRC-first is a hard win (score 1000); fallback to fuzzy text search with normalized title/artist and duration tolerance. High confidence threshold (≥95 overall or ISRC match ≥900) for auto-match; otherwise queued to `pending_track_resolution`.
- **Chunking**: Playlist adds are chunked at 100 tracks per batch; library adds also at 100 tracks per batch. Retry logic on partial failures with per-item fallback.
- **Progress tracking**: Uses existing `_job_set()` pattern with counters: `created`, `updated`, `mapped`, `added_to_apple`, `existing_on_apple`, `skipped`, `pending_count`.
- **Deletion semantics**: Additive by default (no removal of extra tracks from Apple playlists). Tracks are skipped if already present; no strict mirroring implemented initially.

**Environment Variables (as designed):**
- `APPLE_MUSIC_TEAM_ID`
- `APPLE_MUSIC_KEY_ID`
- `APPLE_MUSIC_PRIVATE_KEY_PATH`
- `APPLE_ENABLED` (feature flag, defaults to `true`)

**Testing:**
- Manual validation completed: Spotify + Apple Music connection, playlist sync, liked tracks sync, library browsing, pending resolution UI.
- Pre-commit hooks (ruff, mypy) enforced code quality.
- Unit tests for Apple client are tracked as future work (issue `musicsync-w88`).

**Known Deviations from Proposal:**
- Effort estimate was ~1.5–2.5 weeks; actual implementation was ~2 days focused work (benefiting from existing patterns).
- No dedicated `/pending-apple` pages; reused existing Pending resolution UI with service context.
- No feature flag toggle in UI yet; relies on `APPLE_ENABLED` env var.

### Setup Instructions

1. **Apple Developer Account**: Enroll at <https://developer.apple.com/programs/> ($99/year).
2. **Create MusicKit Identifier**: Apple Developer → Certificates, Identifiers & Profiles → Identifiers → MusicKit.
3. **Generate Private Key**: Create a key with MusicKit enabled, download `.p8` file (keep it secure).
4. **Configure `.env`**:
   ```bash
   APPLE_ENABLED=true
   APPLE_MUSIC_TEAM_ID=ABCD123456
   APPLE_MUSIC_KEY_ID=XYZ1234567
   APPLE_MUSIC_PRIVATE_KEY_PATH=/absolute/path/to/AuthKey_XYZ1234567.p8
   ```
5. **Connect in UI**: Navigate to <http://localhost:8000>, click "Connect Apple Music", authenticate via popup.
6. **Run Syncs**: Use "Sync Playlists to Apple Music" or "Sync Liked Tracks to Apple Music" buttons.

### Troubleshooting

- **"Connect Apple Music" fails**: Verify Team ID, Key ID, and private key path. Ensure the .p8 file is accessible and the MusicKit identifier is active. Check that you have an active Apple Music subscription.
- **"Track not found" during sync**: Storefront/region availability varies. Check that tracks are available in your Apple Music region.
- **Browser popup blocked**: Ensure browser allows popups for `localhost:8000`. Check browser console for MusicKit JS errors.
- **`401 Unauthorized` from Apple**: Developer Token expired or invalid. App regenerates automatically; if issue persists, verify key configuration.

### Future Enhancements

- Add integration tests for end-to-end Apple sync flows (live-account,
  environment-guarded).
- Optional: implement a configurable fallback playlist for Spotify followed
  artists on Apple Music (representative-track strategy).
- Explore strict mirroring mode for playlists (remove extras on Apple that aren't in Spotify).
- UI refinements: feature flag toggle, improved pending resolution filtering.
