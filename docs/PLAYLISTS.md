# Playlist sync design

This document explains how playlist sync works in MusicSync and highlights key design choices and trade-offs.

## Scope

- Source: user-owned Spotify playlists
- Target: TIDAL playlists under the authenticated TIDAL user
- Direction: one-way (Spotify → TIDAL)

## High-level flow

1. Enumerate Spotify playlists owned by the current user.
2. For each playlist:
   - Ensure a corresponding TIDAL playlist exists (create if missing, reuse if mapped).
   - Fetch the ordered list of Spotify tracks.
   - Map each track to a TIDAL track id:
     - Prefer an existing `TrackMap` entry.
     - Otherwise, try ISRC-first; else fuzzy match with normalization and duration tolerance.
   - Add any missing TIDAL track ids to the target playlist in chunks, skipping duplicates.
   - Persist a local snapshot of the playlist: ordered entries with Spotify ids/titles/artists and the mapped TIDAL ids/titles/artists when known.

## Data model

- `playlist_map` — maps Spotify playlist id → TIDAL playlist id, plus names and `last_synced_at`.
- `playlist_track` — ordered snapshot of a playlist’s content at sync time:
  - `playlist_spotify_id`, `position` (1-based), `spotify_track_id`, `spotify_title`, `spotify_artist`
  - Optional `tidal_track_id`, `tidal_title`, `tidal_artist`, `isrc`
  - `last_synced_at`

## Ordering

- The `playlist_track` snapshot uses the Spotify track order at the moment of sync.
- When the TIDAL playlist already exists, we do a set-like addition of missing tracks; absolute ordering on TIDAL is not normalized by this job.
- The snapshot allows local browsing of a consistent order (from Spotify) even if the TIDAL playlist was previously edited.

## Idempotency

- Additions to TIDAL playlists are chunked and deduplicated.
- Re-running playlist sync updates the snapshot and adds any new tracks; existing tracks are not re-added.

## Matching details

- Track mapping uses the same pipeline as liked-tracks:
  - ISRC exact match → accept
  - Else normalized fuzzy scoring across title+artist with duration bonus
  - Thresholds tuned for high precision; low-confidence candidates are not added and can be queued for manual review.

## Limits and cautions

- TIDAL favorites and possibly playlist sizes have practical limits (public reports suggest ~10k for favorites, playlist limits vary by client/API).
- Adding very large playlists can take time; the job reports progress.
- TIDAL operations can fail transiently; additions are done in chunks with best-effort fallbacks.

## UI

- Playlists Library shows mapped playlists and provides direct links to Spotify/TIDAL.
- Clicking a playlist name opens a detail page listing the locally stored ordered tracks with external links where available.

## Future improvements

- “Replace vs append” policy toggle for playlists.
- Include collaborative/followed playlists as optional sources.
- TIDAL → Spotify or bidirectional sync.
- Per-playlist include/exclude and filters.
