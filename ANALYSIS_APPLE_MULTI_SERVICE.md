# Analysis: Generalizing UI and Endpoints for Apple Music Support

## Executive Summary

The **database schema** already has comprehensive multi-service support via `target_service` fields and composite primary keys. Apple Music sync jobs correctly use `target_service="apple"` when storing records. However, **UI components and several endpoints remain TIDAL-only**, creating a disconnect where Apple Music data exists in the database but isn't accessible or resolvable through the web interface.

---

## ✅ What's Already Working

### Database Schema (Multi-Service Ready)
- ✅ `PendingTrackResolution` has `target_service` field (defaults to "tidal")
- ✅ `TrackMap` has composite PK `(spotify_id, target_service)`
- ✅ `PlaylistMap` has composite PK `(spotify_id, target_service)`
- ✅ `PlaylistTrack` has `target_service` field
- ✅ All sync event tables have `target_service` field
- ✅ All models have both legacy `tidal_*` and generic `target_*` columns

### Backend Sync Jobs
- ✅ `_run_sync_apple_likes_job` correctly passes `target_service="apple"` to:
  - `add_pending_track_resolution()`
  - `upsert_track_map()`
  - `add_track_sync_event()`
- ✅ Apple Music tracks are being matched and stored in the database
- ✅ Pending Apple Music tracks are being created with correct `target_service`

---

## ❌ What Needs Fixing

### 1. **Backend: `/resolve-track/{pending_id}` Endpoint** (app/main.py:2351)

**Current Issues:**
- ❌ Hardcoded to TIDAL only (`get_tidal_session()`)
- ❌ Only adds to TIDAL favorites
- ❌ Form parameters are TIDAL-specific (`tidal_id`, `tidal_title`, etc.)
- ❌ Calls `upsert_track_map()` without `target_service` (defaults to "tidal")
- ❌ No logic to dispatch to Apple Music client

**Required Changes:**
1. Accept `target_service` parameter (from hidden form field)
2. Dispatch resolution logic based on `target_service`:
   - For TIDAL: use `get_tidal_session()` + `add_track()`
   - For Apple Music: use `get_apple_client()` + `add_tracks_to_library()`
3. Update form parameter names to be generic (or service-specific routing)
4. Pass `target_service` to `upsert_track_map()`
5. Ensure database queries respect `target_service`

**Critical Note from Review:**
> The user needs to have a valid Apple Music "Music User Token" for `add_tracks_to_library()` to work during resolution.

---

### 2. **Backend: `get_pending_tracks()` Helper** (app/storage.py:598)

**Current Issues:**
- ❌ **Does NOT include `target_service` in returned dict**
- ❌ Templates have no way to distinguish TIDAL vs Apple Music pending items

**Required Changes:**
```python
# Current output dict is missing this field:
{
    "id": r.id,
    "spotify_id": r.spotify_id,
    "spotify_title": r.spotify_title,
    "spotify_artist": r.spotify_artist,
    "isrc": r.isrc,
    "candidates": json.loads(str(r.candidates_json)),
    "created_at": r.created_at.isoformat(),
    # MISSING:
    "target_service": r.target_service,  # ← ADD THIS
}
```

---

### 3. **UI: `pending_tracks.html` Template**

**Current Issues:**
- ❌ All labels say "Choose TIDAL match"
- ❌ Form field names hardcoded to `tidal_id`, `tidal_title`, `tidal_artist`, `tidal_artist_id`
- ❌ Search link only points to TIDAL
- ❌ No display/indication of target service
- ❌ No hidden `target_service` form field

**Required Changes:**
1. Display target_service for each pending item (e.g., badge showing "TIDAL" or "Apple Music")
2. Make labels service-aware:
   - "Choose TIDAL match" → "Choose [service] match"
3. Add hidden `target_service` field to form submission
4. Adapt candidate display for Apple Music vs TIDAL differences
5. Service-specific search links (TIDAL vs Apple Music web)

**Design Pattern Suggestion:**
```jinja2
{% if p.target_service == 'tidal' %}
  <label>Choose TIDAL match:</label>
  <select name="tidal_id">...</select>
{% elif p.target_service == 'apple' %}
  <label>Choose Apple Music match:</label>
  <select name="apple_id">...</select>
{% endif %}
<input type="hidden" name="target_service" value="{{ p.target_service }}" />
```

---

### 4. **UI: `library_playlist_detail.html` Template**

**Current Issues:**
- ✅ Shows Apple Music playlist ID exists (checkmark) ← Good
- ❌ Track table only shows TIDAL columns (`tidal_title`, `tidal_artist`)
- ❌ No Apple Music track match status columns
- ❌ Cannot see which tracks were matched to Apple Music

**Required Changes:**
1. Add Apple Music columns to the track table:
   - `apple_track_id` (or use `target_track_id` depending on sync approach)
   - `apple_title`
   - `apple_artist`
2. Display both TIDAL and Apple Music matches side-by-side OR
3. Make it service-aware (show TIDAL columns for TIDAL playlists, Apple for Apple playlists)

**Note:** `PlaylistTrack` model has `target_service` field but `list_playlist_tracks()` only returns TIDAL-specific fields. Need to either:
- Query and show entries for both services
- OR filter by a selected service
- OR show generic `target_*` fields with service indicator

---

### 5. **UI: `library_tracks.html` Template**

**Current Issues:**
- ❌ Only shows TIDAL title/artist columns
- ❌ No Apple Music columns
- ❌ No indication of which service a track mapping is for
- ❌ Cannot distinguish TIDAL-synced tracks from Apple-synced tracks

**Required Changes:**
1. Add service indicator column
2. Show both TIDAL and Apple Music mappings OR
3. Add service filter dropdown
4. Display appropriate target columns based on service

---

### 6. **Backend: `list_synced_tracks()` Helper** (app/storage.py:1090)

**Current Issues:**
- ❌ Filters only `TrackMap.tidal_id.isnot(None)` - **ignores Apple Music tracks!**
- ❌ Returns only TIDAL-specific fields (`tidal_id`, `tidal_title`, etc.)
- ❌ No `target_service` in output
- ❌ Apple Music tracks in database are invisible in the UI

**Required Changes:**
1. Filter by `TrackMap.target_id.isnot(None)` (generic column) OR show both services
2. Add `target_service` parameter to function signature
3. Include `target_service` in output dict
4. Return both legacy `tidal_*` and generic `target_*` fields OR service-specific fields

**Impact:**
This is a **critical blocker** - Apple Music track mappings exist in the database but are completely hidden from the `/library/tracks` page.

---

### 7. **Backend: `list_playlist_tracks()` Helper** (app/storage.py:793)

**Current Issues:**
- ⚠️ Only returns TIDAL-specific fields in output dict:
  ```python
  {
      "tidal_track_id": r.tidal_track_id,
      "tidal_title": r.tidal_title,
      "tidal_artist": r.tidal_artist,
      # Missing: target_track_id, target_title, target_artist, target_service
  }
  ```
- ❌ No `target_service` field returned
- ❌ Apple Music playlist track mappings invisible

**Required Changes:**
Similar to `list_synced_tracks()` - add `target_service` output and generic target fields.

---

## 📊 Summary Table

| Component | Status | Apple Music Support | Changes Required |
|-----------|--------|---------------------|------------------|
| Database schema | ✅ Complete | Full multi-service | None |
| Sync jobs (backend) | ✅ Complete | Working | None |
| `resolve_track` endpoint | ❌ TIDAL-only | None | Add service dispatch |
| `get_pending_tracks()` | ❌ Missing field | Partial (no service) | Add `target_service` to output |
| `pending_tracks.html` | ❌ TIDAL-only | None | Service-aware UI |
| `list_synced_tracks()` | ❌ TIDAL-only | None (hidden) | Multi-service query |
| `library_tracks.html` | ❌ TIDAL-only | None | Service columns |
| `list_playlist_tracks()` | ❌ TIDAL-only | None (hidden) | Multi-service output |
| `library_playlist_detail.html` | 🟡 Partial | Playlist ID only | Track match columns |

---

## 🎯 Recommended Implementation Order

### Phase 1: Make Apple Music Data Visible (Read-Only)
1. Fix `get_pending_tracks()` to include `target_service`
2. Fix `list_synced_tracks()` to show Apple Music tracks
3. Fix `list_playlist_tracks()` to show Apple Music mappings
4. Update `library_tracks.html` to display service indicator and target columns
5. Update `library_playlist_detail.html` to show Apple Music track mappings
6. Update `pending_tracks.html` to display target service (read-only for now)

### Phase 2: Enable Apple Music Resolution (Read-Write)
1. Update `resolve_track` endpoint to accept `target_service`
2. Add Apple Music resolution logic (`add_tracks_to_library()`)
3. Make `pending_tracks.html` fully interactive for Apple Music
4. Add service-specific search links

### Phase 3: UI Polish
1. Add service filter dropdowns to library pages
2. Add pagination for multi-service views
3. Add batch resolution capabilities
4. Add status indicators (synced to TIDAL, Apple, both, or neither)

---

## 🔍 Verification Checklist (from Review)

### Automated Tests
- [ ] Run existing tests: `pytest tests/test_apple_sync_jobs.py`
- [ ] Add test for Apple Music track resolution logic
- [ ] Test multi-service pending item handling

### Manual Testing
1. [ ] Start Apple Music sync, trigger pending match (via limit or mock)
2. [ ] Visit `/pending-tracks` page
3. [ ] Verify Apple Music items display with correct service indicator
4. [ ] Resolve an Apple Music track
5. [ ] Verify database update with `target_service='apple'`
6. [ ] Verify track added to Apple Music library
7. [ ] Visit `/library/tracks` and see Apple Music mappings
8. [ ] Visit playlist detail page and see both TIDAL and Apple Music matches
9. [ ] Check that TIDAL resolution still works (no regression)

---

## 🚨 Critical Blockers

1. **`list_synced_tracks()` TIDAL-only filter** - Apple Music tracks exist in DB but are invisible
2. **`resolve_track` endpoint** - Cannot resolve Apple Music pending items
3. **`get_pending_tracks()` missing service** - UI cannot distinguish services

---

## 💡 Design Decisions Needed

### Option A: Side-by-Side Multi-Service
Show both TIDAL and Apple Music columns in all views. Allows seeing Spotify track mapped to both services simultaneously.

**Pros:**
- Comprehensive view
- No filtering needed
**Cons:**
- Wide tables, horizontal scrolling
- Cluttered UI

### Option B: Service Filter/Tabs
Add a service selector (dropdown or tabs) to switch between TIDAL, Apple Music, or "All".

**Pros:**
- Cleaner UI
- Focused workflow
**Cons:**
- Extra click to switch services
- Requires filter persistence

### Option C: Unified Target Columns
Show only generic `target_id`, `target_title`, `target_artist` with a service badge.

**Pros:**
- Simplest schema usage
- Scalable to more services
**Cons:**
- Cannot see both services for same Spotify track
- Legacy TIDAL columns become redundant

---

## 📝 Notes

- The review correctly identifies that Apple Music resolution requires a valid Music User Token
- Database migrations are NOT needed - schema already supports multi-service
- Backend sync jobs are working correctly - this is purely a UI/endpoint gap
- Consider deprecating legacy `tidal_*` columns once multi-service UI is stable
