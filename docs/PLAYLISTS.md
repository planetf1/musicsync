# Playlist sync design

This document explains how playlist sync works in MusicSync and highlights key
design choices and trade-offs.

## Scope

- Source: user-owned Spotify playlists
- Targets: TIDAL playlists and Apple Music playlists under the authenticated user
- Direction: one-way (Spotify → target service)

## High-level flow

1. Enumerate Spotify playlists owned by the current user.
2. For each playlist:
   - Ensure a corresponding target playlist exists (create if missing, reuse if mapped).
   - Fetch the ordered list of Spotify tracks.
   - Map each track to a target track id:
     - Prefer an existing `TrackMap` entry.
     - Otherwise, try ISRC-first; else fuzzy match with normalization and
       duration tolerance.
   - Add any missing target track ids to the target playlist in chunks, skipping duplicates.
     - Addition uses multiple safe call variants for compatibility with different
       APIs/client versions (int vs string IDs, with/without position, chunk vs
       per-item) and then re-checks the playlist.
   - Persist a local snapshot of the playlist: ordered entries with Spotify
     ids/titles/artists and the mapped target ids/titles/artists when known.

## Data model

- `playlist_map` — maps Spotify playlist id → target playlist id per `target_service`, plus names and `last_synced_at`.
- `playlist_track` — ordered snapshot of a playlist’s content at sync time:
  - `target_service` indicates which service snapshot row belongs to (`tidal` or `apple`)
  - `playlist_spotify_id`, `position` (1-based), `spotify_track_id`,
    `spotify_title`, `spotify_artist`
  - Optional service-specific target ids/titles/artists and `isrc`
  - `last_synced_at`

## Ordering

- The `playlist_track` snapshot uses the Spotify track order at the moment of sync.
- When the target playlist already exists, we do a set-like addition of missing
  tracks; absolute target ordering is not normalized by this job.
- The snapshot allows local browsing of a consistent order (from Spotify) even
  if the TIDAL playlist was previously edited.

## Idempotency

- Additions to target playlists are chunked and deduplicated.
- Re-running playlist sync updates the snapshot and adds any new tracks;
  existing tracks are not re-added. Skipping is based only on the live contents
  of the target playlist, not on local DB mappings.

## Implementation notes (robust adds)

- We prefer to pass integer track IDs to TIDAL. If string IDs are present we
  attempt to cast to int.
- We try multiple `Playlist.add(...)` signatures to accommodate tidalapi differences:
  1) int IDs with position
  2) int IDs without position
  3) string IDs with position
  4) string IDs without position
- If a chunked add fails, we retry per-item with the same strategies.
- After attempting to add, we re-fetch the playlist and retry a second pass for
  any tracks still missing. This reconciles cases where the API accepted the
  call but the UI/server lagged in reflecting the change.

## Updated counter semantics

- A playlist is counted as "updated" only if it already existed on TIDAL and at
  least one track add actually succeeded.
- Newly created TIDAL playlists are counted under "created".

## Debug logging

During sync, logs include per-playlist summaries, for example:

- `mapped=X existing_on_tidal=Y to_add=Z`
- `added_to_tidal=N`
- `retrying missing=M`

These help diagnose whether mapped tracks were attempted and how many additions succeeded.

## Matching details

- Track mapping uses the same pipeline as liked-tracks:
  - ISRC exact match → accept
  - Else normalized fuzzy scoring across title+artist with duration bonus
  - Thresholds tuned for high precision; low-confidence candidates are not added
    and can be queued for manual review.

## Limits and cautions

- Service limits vary by provider (TIDAL/Apple Music) and client/API behavior.
- Adding very large playlists can take time; the job reports progress.
- TIDAL operations can fail transiently; additions are done in chunks with
  best-effort fallbacks.

## UI

- Playlists Library shows mapped playlists and provides direct links to Spotify/TIDAL, plus Apple Music mapping status.
- Clicking a playlist name opens a detail page listing the locally stored
  ordered tracks with external links where available.

## Future improvements

- “Replace vs append” policy toggle for playlists.
- Include collaborative/followed playlists as optional sources.
- TIDAL → Spotify or bidirectional sync.
- Per-playlist include/exclude and filters.
