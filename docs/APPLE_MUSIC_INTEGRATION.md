# Apple Music Integration — Design Proposal

Status: Proposal (no implementation yet)
Owners: Contributors / Maintainers
Last updated: 2025-10-26

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
