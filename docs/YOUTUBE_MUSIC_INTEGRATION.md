# YouTube Music Integration — Design Proposal

Status: Proposal (no implementation yet)
Owners: Contributors / Maintainers
Last updated: 2025-10-26

## Goals

- Add YouTube Music (YTM) as a sync target alongside TIDAL (source remains Spotify).
- Support:
  - Playlists: create/update user playlists and add missing tracks in order.
  - Liked tracks: either add to YTM "Liked songs" (when available) or create a dedicated playlist ("Liked from Spotify").
- Preserve project principles: idempotent background jobs with progress, robust matching, local persistence of mappings and snapshots.

## Non-Goals (initial release)

- Two-way sync (YTM → Spotify) or bidirectional reconciliation.
- Full parity for artist follows. We may support artist subscriptions if feasible, but it's optional.

---

## Context: API Options

- There is no official public YouTube Music API. The community library `ytmusicapi` provides high-level access via reverse-engineered endpoints and now supports OAuth-based authentication.
- Risk: reverse-engineered endpoints can change; breaking changes are possible.
- Alternative: YouTube Data API v3 exists, but it targets YouTube (not YouTube Music) and does not provide usable coverage for YTM library/playlist features.

Conclusion: Use `ytmusicapi` with OAuth, document risks clearly, feature-flag the integration, and implement robust error handling.

---

## Architecture Overview

- New client: `app/youtube_music_client.py`
  - Wraps `ytmusicapi` session creation (OAuth) and exposes minimal, stable methods for jobs.
  - Handles search, playlist creation, track additions, and likes where available.
- Storage updates in `app/storage.py`
  - Persist OAuth credentials/refresh tokens and YouTube Music mappings (or generalize existing mapping tables by target service).
- Background jobs in `app/main.py`
  - New sync functions and start/status endpoints mirroring TIDAL/Apple patterns.
- Matching reuse in `app/matching.py`
  - Keep normalization and fuzzy logic; tune for YTM content specifics (song vs video, duration, artist channel).
- UI updates in `app/templates/`
  - Add "Connect YouTube Music" OAuth flow and YTM sync buttons; show progress and unmatched queues.

---

## Authentication

Preferred: OAuth via `ytmusicapi` OAuth helpers (browser-based consent).

- Google Cloud project required with OAuth Client (Web application). Configure redirect URI pointing to FastAPI (e.g., `/oauth2/ytmusic/callback`).
- Scopes: the library typically uses YouTube scopes (e.g., `youtube` or `youtube.force-ssl`). We will request the minimal scopes required for playlists and library management.

Backend token handling:
- Store access/refresh tokens in `tokens` table with `service=ytmusic`, `token_type=oauth`.
- Handle token refresh automatically via `ytmusicapi` and persist updates.

Environment/config:
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_OAUTH_REDIRECT_URI` (e.g., `https://yourapp.example.com/oauth2/ytmusic/callback`)
- Optionally a path to an OAuth client secrets JSON (if using Google client libraries in addition to ytmusicapi).

Security:
- Do not log tokens; encrypt at rest if feasible.
- Use HTTPS in production and restrict OAuth consent to intended domains.

---

## Client Module: `app/youtube_music_client.py` (new)

Responsibilities:
- Manage an authenticated `ytmusicapi.YTMusic` instance per user.
- Provide stable wrappers with retry/backoff for transient failures.

Key methods (interface sketch — not implementation):
- `get_user_context() -> UserContext` (e.g., locale/region if exposed)
- `search_track(title: str, artist: str, duration_s: int) -> list[YTMTrack]`
- `ensure_playlist(name: str, description: str | None) -> YTMPlaylist`
- `get_playlist_items(playlist_id: str) -> list[YTMTrack]`
- `add_to_playlist(playlist_id: str, video_ids: list[str]) -> AddResult`
- `rate_song_like(video_id: str) -> bool` (optional; fallback to dedicated playlist if not reliable)

Data models should capture: `videoId`, `title`, `artists`, `duration_s`, `album`, `is_song` (vs `is_video`), `is_explicit` (if available).

---

## Storage Model Changes (`app/storage.py`)

Tokens:
- Extend `tokens` to store YTM OAuth tokens: `service=ytmusic`, `token_type=oauth`, `value` (JSON), `expires_at`.

Mappings:
- Prefer generalization: add `service` column to `track_map` and `playlist_map` (if not already present) to store YTM mappings (e.g., `videoId` for tracks, playlist ID for playlists).
- Persist confidence and `resolved` flags; reuse `pending_track_resolution` for ambiguous results with `service=ytmusic`.

Snapshots:
- Reuse `playlist_track` for ordered Spotify track snapshots.

Migrations:
- Follow project’s non-destructive pattern, with light raw-SQL migrations for older DBs.

---

## Background Jobs and Endpoints (`app/main.py`)

Endpoints (sketch):
- `GET /ytmusic/connect` — starts OAuth auth-code flow (redirect to Google consent).
- `GET /oauth2/ytmusic/callback` — handles Google redirect, exchanges code, stores tokens.
- `POST /sync/ytmusic/playlists/start` — starts playlist sync job.
- `GET /sync/ytmusic/playlists/status` — returns job status and counters.
- Optional: `POST /sync/ytmusic/likes/start` and `/status` — adds Spotify liked tracks to YTM (either via "like" or via a dedicated playlist).

Playlist sync job outline:
1. Preflight: ensure OAuth token; initialize `ytmusicapi` client.
2. For each eligible Spotify playlist:
   - Build ordered Spotify track list (existing logic).
   - Map to YTM video IDs:
     - Prefer results with `is_song` over `is_video` when searching.
     - Fuzzy match normalized title + primary artist with duration tolerance (±2–5s).
     - Optionally check album match to break ties.
     - Persist mapping; queue ambiguous cases to pending resolution.
   - Ensure YTM playlist exists (create if missing).
   - Diff: fetch existing playlist items once; compute `to_add`.
   - Add in chunks (e.g., 50) preserving order.
   - Counters: `created`, `updated`, `mapped`, `added_to_ytmusic`, `existing_on_ytmusic`, `pending_count`, `skipped_unavailable`.

Likes job (optional initial release):
- Two strategies:
  1) Use `rate_song_like(video_id)` where available and reliable.
  2) Create/maintain a playlist "Liked from Spotify" and add tracks there (more predictable and reviewable).

Error handling:
- On auth errors, attempt token refresh; if not recoverable, mark job `error` and instruct user to reconnect YTM.
- Backoff/retry on 5xx and throttling; surface partial progress.

---

## Matching Strategy (`app/matching.py`)

- YouTube Music rarely surfaces ISRC consistently; plan for text-based matching as primary.
- Use filters to bias toward songs (not general YouTube videos). Prefer official artist channel recordings.
- Scoring:
  - Title+artist normalized fuzzy score (e.g., rapidfuzz) with duration tolerance.
  - Tie-break by: smallest duration delta, presence of album info, `is_song` flag, and channel metadata indicating official/verified.
  - Consider excluding "live", "karaoke", "cover", "remix" unless indicated by Spotify title.

---

## UI and Templates (`app/templates/`)

- Index enhancements:
  - "Connect YouTube Music" button that kicks off OAuth.
  - "Sync Playlists to YouTube Music" action, progress polling, and counters mirroring existing UX.
- Pending review:
  - Integrate with existing Pending pages with `service=ytmusic` filter.
- Copy:
  - Clarify that some items may map to equivalent videos when song entities aren’t found.

---

## Rate Limits and Performance

- `ytmusicapi` relies on private endpoints; implement conservative request pacing.
- Chunk adds (e.g., 50 items) and cache playlist contents to minimize reads.
- Use exponential backoff with jitter; cap retries and continue with partials.

---

## Configuration and Ops

Environment variables (no implementation yet):
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_OAUTH_REDIRECT_URI`
- Optional: `YTMUSIC_OAUTH_SECRETS_PATH` if using a saved client secret JSON.

Operational notes:
- Ensure OAuth consent screen is configured and the app is published (or add test users).
- In local dev, use `http://127.0.0.1:<port>/oauth2/ytmusic/callback` as an authorized redirect.

---

## Testing Plan

Unit tests:
- OAuth token storage/refresh and client bootstrap.
- Matching: varied cases (explicit vs clean, live/remaster filtering, duration deltas) with recorded fixtures.

Integration tests (env-guarded):
- Create a test playlist and add tracks; verify idempotency and order.
- Likes flow: rate like vs playlist-based fallback; confirm outcomes.

Manual validation:
- Connect YTM; sync selected playlists; review unmatched items and re-map as needed.

Mocking:
- Define a client interface and a fake implementation to isolate tests from live endpoints.

---

## Security and Privacy

- Treat OAuth tokens as secrets; avoid logging; encrypt at rest if possible.
- Use HTTPS in production and verify OAuth redirect origins.
- Clearly communicate that YTM support relies on a reverse-engineered library and may break.

---

## Limitations and Known Behaviors

- Lack of consistent ISRC means text-based matching drives most mappings.
- Some matches may resolve to video versions if song entities are unavailable.
- Reverse-engineered endpoints can change without notice; feature flag and graceful degradation are required.

---

## Rollout Plan

1. Feature flag YTM integration in UI.
2. Ship OAuth connect flow and token storage; verify in dev.
3. Implement playlist sync path; beta release.
4. Add liked-tracks flow (choose rate-like or playlist fallback based on reliability).
5. Improve matching heuristics and pending review UX.

---

## Effort Estimate (rough)

- YTM client + OAuth + tokens: 2–3 days (OAuth setup can vary)
- Playlist sync job + counters: 1–2 days
- Liked-tracks flow: 0.5–1 day
- Schema updates + migrations + UI buttons: 0.5–1 day
- Tests + fixtures + docs: 1–2 days

Total: ~2–3.5 weeks elapsed including OAuth setup, reviews, and polish due to added uncertainty around reverse-engineered endpoints.

---

## Open Questions

- Should we default likes to a separate playlist to ensure deterministic behavior, or attempt rate-like first and fall back automatically?
- What deletion semantics should playlist sync use by default (strict mirror vs additive)?
- Do we expose a user option to accept video matches when song entities are unavailable?
