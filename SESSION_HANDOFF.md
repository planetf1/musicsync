# Session Handoff - Apple Music Sync Implementation

**Date**: 2026-03-09
**Status**: Paused due to terminal pager issues
**Full Context**: See `/memories/session/apple-music-sync-session.md`

## ⚠️ IMMEDIATE ACTION REQUIRED

### Git State: Commits Ready But NOT PUSHED

Two commits are created locally and need to be pushed to remote:

1. **"feat: Make TIDAL integration optional"** (musicsync-jjd ✅)
2. **"feat(storage): Generalize schema for multi-service support"** (musicsync-0gr ✅)

### Push Workflow

```bash
# 1. Check current state
git status
git log --oneline -3

# 2. Handle any unstaged formatting changes
git add -A  # If there are formatting changes to app/storage.py
git commit --amend --no-edit  # Amend last commit with formatting

# 3. Push to remote
git pull --rebase  # In case remote has changes
bd sync  # Sync beads database
git push  # Push commits

# 4. Verify
git status  # Should show "up to date with origin"
```

## What Was Completed

### ✅ Task musicsync-jjd: TIDAL Optional Feature
- Environment variable `TIDAL_ENABLED` (default: true)
- Conditional logic throughout app/main.py
- UI conditionals in templates/index.html
- Documentation in README.md and .env.example

### ✅ Task musicsync-0gr: Storage Schema for Multi-Service
- Composite primary keys: (spotify_id, target_service)
- Generic target_* columns + legacy tidal_* columns
- Comprehensive migrations in init_db()
- All CRUD functions accept target_service parameter
- Token model supports 'apple' service

## Next Task: musicsync-hu2

**Title**: Implement Apple Music client module
**Status**: Ready to start (dependencies complete)
**Priority**: High (critical path)

### Implementation Checklist

1. **Create app/apple_client.py**:
   - [ ] Developer Token generation (ES256 JWT signing)
     - Requires: Team ID, Key ID, P8 private key file
     - Expiration: max 6 months, handle refresh
   - [ ] Music User Token management
     - Acquired via MusicKit JS (browser-based flow)
     - Store in Token table with service='apple'
   - [ ] Storefront detection (user's country)
   - [ ] REST API client class
     - Base URL: https://api.music.apple.com/v1/
     - Headers: Authorization (Bearer tokens)
     - Rate limiting with exponential backoff
   - [ ] Helper methods: search, add to library, create playlist, etc.

2. **Update pyproject.toml**:
   - [ ] Add `PyJWT` dependency
   - [ ] Add `cryptography` dependency

3. **Update .env.example**:
   - [ ] `APPLE_MUSIC_TEAM_ID`
   - [ ] `APPLE_MUSIC_KEY_ID`
   - [ ] `APPLE_MUSIC_PRIVATE_KEY_PATH` (path to .p8 file)

4. **Test**:
   ```bash
   uv sync  # Install new dependencies
   uv run python -c "import app.apple_client; print('✓ Module imports')"
   ```

### Reference Documentation
- Apple Music API: https://developer.apple.com/documentation/applemusicapi
- Developer Tokens: https://developer.apple.com/documentation/applemusicapi/generating_developer_tokens
- MusicKit JS: https://developer.apple.com/documentation/musickitjs

## Subsequent Tasks

- **musicsync-bt4**: Apple Music authentication flow (depends on hu2)
- **musicsync-ep8**: Playlist sync (depends on bt4)
- **musicsync-v7x**: Liked tracks sync (depends on bt4)
- **musicsync-6jn**: Research artist following (depends on bt4)
- **musicsync-qyo**: Update UI (depends on ep8+v7x)
- **musicsync-0zc**: Documentation (depends on ep8+v7x)
- **musicsync-w88**: Tests (depends on ep8+v7x)

## Technical Notes

- **ISRC matching**: Apple Music supports ISRC, use same ISRC-first approach as TIDAL
- **Fuzzy matching**: Fallback for items without ISRC (use existing matching.py helpers)
- **Artist following**: May not be supported by Apple Music API - research needed
- **Rate limits**: Apple is stricter than Spotify - implement exponential backoff
- **Playlist limits**: 100 playlists, 25,000 tracks per playlist (generous vs TIDAL)

## Beads Commands

```bash
# Claim next task
bd update musicsync-hu2 --claim --json

# When complete
bd close musicsync-hu2 --reason "Completed: Apple Music client with Developer Token, API helpers, and MusicKit integration" --json

# Check ready work
bd ready --json
```

---

**Remember**: Complete the git push FIRST before starting new development work!
