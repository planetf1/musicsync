from __future__ import annotations

import json
import logging
import os
import re
import threading
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from tidalapi import artist as tidal_artist

from .apple_client import get_apple_client
from .matching import ArtistCandidate, score_artist_match
from .spotify_client import call_spotify, exchange_code_for_token, get_authorize_url, get_spotify_client
from .storage import (
    DB_PATH,
    ArtistMap,
    SessionLocal,
    TrackMap,
    add_artist_sync_event,
    add_pending_resolution,
    add_pending_track_resolution,
    add_track_sync_event,
    cleanup_pending_for_resolved,
    cleanup_pending_tracks_for_resolved,
    delete_pending,
    delete_pending_by_spotify_id,
    delete_pending_track,
    delete_pending_track_by_spotify_id,
    engine,
    export_artists,
    export_playlists,
    export_tracks,
    get_pending,
    get_pending_tracks,
    get_playlist_map,
    get_playlist_stats,
    init_db,
    list_genre_counts,
    list_playlist_tracks,
    list_synced_artists,
    list_synced_playlists,
    list_synced_tracks,
    replace_playlist_tracks,
    update_artist_genres,
    upsert_artist_map,
    upsert_playlist_map,
    upsert_track_map,
)
from .tidal_client import (
    get_login_state as tidal_login_state,
)
from .tidal_client import (
    get_login_url_and_worker,
)
from .tidal_client import (
    get_session as get_tidal_session,
)
from .tidal_client import (
    is_logged_in as tidal_logged_in,
)

app = FastAPI(title="MusicSync")
init_db()

# Configuration: TIDAL can be disabled via environment variable
TIDAL_ENABLED = os.getenv("TIDAL_ENABLED", "true").lower() in ("true", "1", "yes")
# Configuration: Apple Music can be disabled via environment variable
APPLE_ENABLED = os.getenv("APPLE_ENABLED", "true").lower() in ("true", "1", "yes")
APPLE_FOLLOWED_ARTISTS_PLAYLIST_NAME = "Followed Artists from Spotify"

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# --- Simple in-memory job tracking for background syncs ---
_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, object]] = {}
_log = logging.getLogger("musicsync")
# Ensure our app logger emits to console even under uvicorn's logging config
if not _log.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    _handler.setFormatter(_formatter)
    _log.addHandler(_handler)
_log.setLevel(logging.DEBUG)

# Also bump root logger to INFO if it's lower, so our messages aren't dropped
root_logger = logging.getLogger()
if root_logger.level > logging.INFO:
    root_logger.setLevel(logging.INFO)


def _safe_int_ids(ids: list[str]) -> list[int]:
    out: list[int] = []
    for x in ids:
        try:
            out.append(int(x))
        except Exception:
            # ignore ids that cannot be parsed to int
            pass
    return out


def _tidal_playlist_add_with_fallbacks(playlist_obj: Any, ids: list[str]) -> int:
    """Try multiple tidalapi Playlist.add call signatures; return count successfully added.

    Attempts with int IDs and string IDs, with/without position. If all chunk attempts fail,
    falls back to per-item adds with the same strategy. Returns the number of attempted items
    that did not raise an exception (may overcount if API silently de-duplicates).
    """
    add_method = getattr(playlist_obj, "add", None)
    if not callable(add_method):
        return 0
    pos_raw = getattr(playlist_obj, "num_tracks", None)
    pos = pos_raw if isinstance(pos_raw, int) else -1
    ids_int = _safe_int_ids(ids)
    strategies: list[tuple[list[Any], bool]] = []
    if ids_int:
        strategies.append((ids_int, True))
        strategies.append((ids_int, False))
    strategies.append((ids, True))
    strategies.append((ids, False))
    # Try chunk calls
    for payload, with_pos in strategies:
        try:
            if with_pos and pos is not None and pos >= 0:
                add_method(payload, position=pos)
            else:
                add_method(payload)
            return len(ids)
        except Exception:
            continue
    # Per-item fallback
    added = 0
    for single in ids:
        single_int = _safe_int_ids([single])
        attempts: list[tuple[list[Any], bool]] = []
        if single_int:
            attempts.append((single_int, True))
            attempts.append((single_int, False))
        attempts.append(([single], True))
        attempts.append(([single], False))
        success = False
        for payload, with_pos in attempts:
            try:
                if with_pos and pos is not None and pos >= 0:
                    add_method(payload, position=pos)
                else:
                    add_method(payload)
                success = True
                break
            except Exception:
                continue
        if success:
            added += 1
    return added


def _norm_artist_name(s: str) -> str:
    """Normalize artist names for comparison: strip diacritics, casefold, replace symbols, collapse spaces."""
    if not s:
        return ""
    # Unicode normalize and strip accents
    # Remove parenthetical/bracketed suffixes like "(OMD)", "[Official]"
    s = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]", " ", s)
    s_norm = unicodedata.normalize("NFKD", s)
    s_ascii = s_norm.encode("ascii", "ignore").decode("ascii")
    s_ascii = s_ascii.replace("&", " and ")
    # Remove punctuation except spaces and alnum
    s_ascii = re.sub(r"[^A-Za-z0-9\s]", " ", s_ascii)
    # Collapse whitespace and lower
    s_ascii = re.sub(r"\s+", " ", s_ascii).strip().lower()
    # Remove leading 'the '
    if s_ascii.startswith("the "):
        s_ascii = s_ascii[4:]
    return s_ascii


def _norm_track_title(s: str) -> str:
    """Normalize track titles: strip diacritics, remove version/parentheticals, collapse spaces, lowercase."""
    if not s:
        return ""
    # remove bracketed content and common suffixes like - remaster, remastered, version
    s = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]", " ", s)
    s = re.sub(r"\s*-\s*(single|album)?\s*version.*$", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(remaster(?:ed)?|live|edit|radio edit)\b", " ", s, flags=re.IGNORECASE)
    s_norm = unicodedata.normalize("NFKD", s)
    s_ascii = s_norm.encode("ascii", "ignore").decode("ascii")
    s_ascii = re.sub(r"[^A-Za-z0-9\s]", " ", s_ascii)
    s_ascii = re.sub(r"\s+", " ", s_ascii).strip().lower()
    return s_ascii


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # Determine connection status
    spotify_ok = False
    try:
        _ = get_spotify_client()
        spotify_ok = True
    except Exception:
        spotify_ok = False

    tidal_ok = False
    if TIDAL_ENABLED:
        try:
            tidal_ok = tidal_logged_in()
        except Exception:
            tidal_ok = False

    apple_ok = False
    if APPLE_ENABLED:
        try:
            client = get_apple_client()
            token = client.get_music_user_token()
            if token:
                apple_ok = True
        except Exception:
            apple_ok = False

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "spotify_ok": spotify_ok,
            "tidal_ok": tidal_ok,
            "tidal_enabled": TIDAL_ENABLED,
            "apple_ok": apple_ok,
            "apple_enabled": APPLE_ENABLED,
        },
    )


@app.get("/auth/spotify/login")
async def spotify_login():
    url = get_authorize_url()
    return RedirectResponse(url)


@app.get("/auth/spotify/callback")
async def spotify_callback(code: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(status_code=400, detail=error)
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    exchange_code_for_token(code)
    return RedirectResponse("/")


@app.get("/auth/tidal/start")
async def tidal_start():
    if not TIDAL_ENABLED:
        raise HTTPException(status_code=400, detail="TIDAL integration is disabled")
    data = get_login_url_and_worker()
    # Return the details needed for UI
    return JSONResponse(
        {
            "verification_uri_complete": data.get("verification_uri_complete"),
            "verification_uri": data.get("verification_uri"),
            "user_code": data.get("user_code"),
            "expires_in": data.get("expires_in"),
            "pending": data.get("pending"),
        }
    )


@app.get("/auth/tidal/status")
async def tidal_status():
    if not TIDAL_ENABLED:
        raise HTTPException(status_code=400, detail="TIDAL integration is disabled")
    state = tidal_login_state()
    return JSONResponse(
        {
            "logged_in": tidal_logged_in(),
            "pending": state.get("pending", False),
            "connected": state.get("connected", False),
            "error": state.get("error"),
        }
    )


# --- Apple Music Authentication ---


@app.get("/auth/apple/connect", response_class=HTMLResponse)
async def apple_connect(request: Request):
    if not APPLE_ENABLED:
        raise HTTPException(status_code=400, detail="Apple Music integration is disabled")

    app_name = os.getenv("APPLE_MUSICKIT_APP_NAME", "MusicSync")
    app_build = os.getenv("APPLE_MUSICKIT_APP_BUILD", "1.0.0")

    return templates.TemplateResponse(
        "apple_connect.html",
        {
            "request": request,
            "app_name": app_name,
            "app_build": app_build,
        },
    )


@app.get("/auth/apple/developer-token")
async def apple_developer_token():
    if not APPLE_ENABLED:
        raise HTTPException(status_code=400, detail="Apple Music integration is disabled")

    try:
        client = get_apple_client()
        token = client.get_developer_token()
        return JSONResponse({"token": token})
    except Exception as e:
        logging.exception("Failed to generate Apple Music developer token")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/apple/token")
async def apple_token(request: Request):
    if not APPLE_ENABLED:
        raise HTTPException(status_code=400, detail="Apple Music integration is disabled")

    try:
        body = await request.json()
        music_user_token = body.get("music_user_token")

        if not music_user_token:
            raise HTTPException(status_code=400, detail="Missing music_user_token")

        client = get_apple_client()
        # Set the token and try to fetch storefront to validate it
        storefront = None
        try:
            client.set_music_user_token(music_user_token)
            storefront = client.get_storefront()
        except Exception as e:
            logging.exception("Failed to validate Apple Music user token")
            raise HTTPException(status_code=400, detail=f"Invalid token: {e}")

        return JSONResponse(
            {
                "status": "success",
                "storefront": storefront,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("Failed to store Apple Music user token")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/apple/status")
async def apple_status():
    if not APPLE_ENABLED:
        raise HTTPException(status_code=400, detail="Apple Music integration is disabled")

    try:
        client = get_apple_client()
        user_token = client.get_music_user_token()
        connected = bool(user_token)
        storefront = None
        if connected:
            try:
                storefront = client.get_storefront()
            except Exception:
                connected = False

        return JSONResponse(
            {
                "connected": connected,
                "storefront": storefront,
            }
        )
    except Exception:
        return JSONResponse(
            {
                "connected": False,
                "storefront": None,
            }
        )


# --- Sync Followed Artists ---


async def fetch_spotify_followed_artists() -> list[dict[str, str]]:
    artists: list[dict[str, str]] = []
    after: str | None = None
    while True:
        page = call_spotify(lambda sp: sp.current_user_followed_artists(limit=50, after=after)) or {}
        artists_block = page.get("artists") or {}
        items = artists_block.get("items") or []
        for a in items:
            artists.append({"id": a["id"], "name": a["name"]})
        cursors = artists_block.get("cursors") or {}
        after = cursors.get("after")
        if not after:
            break
    return artists


def _tidal_favorite_artists_set(sess) -> set:
    user = sess.user
    favs = user.favorites.artists()
    # Normalize to str for consistent comparisons
    return {str(a.id) for a in favs}


def _tidal_search_artists(sess: Any, name: str) -> list[dict[str, str]]:
    """Search for artists on TIDAL with multiple fallbacks and robust parsing.

    Handles differing tidalapi return shapes (dict or list) and logs issues.
    """

    def extract_artists(res: Any) -> list[dict[str, str]]:
        try:
            if not res:
                return []
            candidates: list[Any] = []
            if isinstance(res, dict):
                # Try common keys
                for k in ("artists", "Artists", "artist", "Artist"):
                    if k in res and isinstance(res[k], list):
                        candidates = res[k]
                        break
                # Some tidalapi versions may return list directly, but keep dict fallback
                if not candidates:
                    artists_list = res.get("artists")
                    if isinstance(artists_list, list):
                        candidates = artists_list
            elif isinstance(res, list):
                candidates = res
            out: list[dict[str, str]] = []
            for a in candidates:
                try:
                    aid = getattr(a, "id", None) if not isinstance(a, dict) else a.get("id")
                    aname = getattr(a, "name", None) if not isinstance(a, dict) else a.get("name")
                    if aid and aname:
                        out.append({"id": str(aid), "name": str(aname)})
                except Exception:
                    continue
            return out
        except Exception as e:  # pragma: no cover
            _log.debug(f"extract_artists parse error for '{name}': {e}")
            return []

    try:
        # Primary: search for artists explicitly
        res = sess.search(name, [tidal_artist.Artist], limit=25)
        _log.debug(f"TIDAL search artists raw type={type(res)} for '{name}'")
        out = extract_artists(res)
        if out:
            return out
    except Exception as e:
        _log.warning(f"TIDAL search (artists) failed for '{name}': {e}")

    try:
        # Fallback 1: search across all models and filter artists
        res2 = sess.search(name, None, limit=25)
        _log.debug(f"TIDAL search all-models raw type={type(res2)} for '{name}'")
        out = extract_artists(res2)
        if out:
            return out
    except Exception as e:
        _log.warning(f"TIDAL search (all models) failed for '{name}': {e}")

    try:
        # Fallback 2: normalize name slightly (strip The, replace ampersand)
        q = name.strip()
        if q.lower().startswith("the "):
            q = q[4:]
        q = q.replace(" & ", " and ")
        if q != name:
            res3 = sess.search(q, [tidal_artist.Artist], limit=25)
            _log.debug(f"TIDAL search normalized raw type={type(res3)} for '{name}' -> '{q}'")
            out = extract_artists(res3)
            if out:
                return out
    except Exception as e:
        _log.warning(f"TIDAL search (normalized) failed for '{name}': {e}")

    _log.debug(f"TIDAL search returned 0 candidates for '{name}'")
    return []


def _tidal_search_tracks(sess: Any, title: str, artist: str | None = None) -> list[dict[str, Any]]:
    """Search for tracks on TIDAL and extract comparable fields."""

    def extract_tracks(res: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            items: list[Any] = []
            if isinstance(res, dict):
                for k in ("tracks", "Tracks", "track", "Track"):
                    if k in res and isinstance(res[k], list):
                        items = res[k]
                        break
            elif isinstance(res, list):
                items = res
            for t in items:
                try:
                    tid = getattr(t, "id", None) if not isinstance(t, dict) else t.get("id")
                    name = getattr(t, "name", None) if not isinstance(t, dict) else t.get("name")
                    duration = getattr(t, "duration", None) if not isinstance(t, dict) else t.get("duration")
                    isrc = getattr(t, "isrc", None) if not isinstance(t, dict) else t.get("isrc")
                    # artists may be list on attr .artists, include id+name if available
                    arts: list[dict[str, Any]] = []
                    if not isinstance(t, dict):
                        try:
                            arts = [
                                {"id": getattr(a, "id", None), "name": getattr(a, "name", "")}
                                for a in (getattr(t, "artists", []) or [])
                                if getattr(a, "name", None)
                            ]
                        except Exception:
                            arts = []
                    else:
                        aobj = t.get("artists")
                        if isinstance(aobj, list):
                            arts = [
                                {"id": a.get("id"), "name": str(a.get("name"))}
                                for a in aobj
                                if isinstance(a, dict) and a.get("name")
                            ]
                    if tid and name:
                        out.append(
                            {
                                "id": str(tid),
                                "title": str(name),
                                "artists": arts,
                                "isrc": isrc if isrc else None,
                                "duration": int(duration) if duration is not None else None,
                            }
                        )
                except Exception:
                    continue
        except Exception as e:
            _log.debug(f"extract_tracks parse error for '{title}': {e}")
        return out

    q = title if not artist else f"{title} {artist}"
    try:
        # Some tidalapi versions require passing None to get tracks
        res = sess.search(q, None, limit=25)
        _log.debug(f"TIDAL search tracks raw type={type(res)} for '{q}'")
        out = extract_tracks(res)
        if out:
            return out
    except Exception as e:
        _log.warning(f"TIDAL track search failed for '{q}': {e}")
    try:
        # fallback all models
        res2 = sess.search(q, None, limit=25)
        _log.debug(f"TIDAL search all-models raw type={type(res2)} for '{q}'")
        out = extract_tracks(res2)
        if out:
            return out
    except Exception:
        pass
    # Try normalized title
    nq = _norm_track_title(title)
    if nq and nq != title:
        try:
            res3 = sess.search(nq, None, limit=25)
            out = extract_tracks(res3)
            if out:
                return out
        except Exception:
            pass
    _log.debug(f"TIDAL track search returned 0 candidates for '{q}'")
    return []


@app.post("/sync/artists")
async def sync_artists():
    # Check auth
    try:
        get_spotify_client()
    except Exception:
        raise HTTPException(status_code=400, detail="Spotify not authorized yet")
    try:
        sess = get_tidal_session()
    except Exception:
        raise HTTPException(status_code=400, detail="TIDAL not authorized yet")

    # Fetch data
    artists = await fetch_spotify_followed_artists()

    favorites = _tidal_favorite_artists_set(sess)

    auto_matched = 0
    pending = 0

    for art in artists:
        name = art["name"]
        sp_id = art["id"]
        candidates = _tidal_search_artists(sess, name)
        ranked = score_artist_match(name, candidates)
        if ranked and ranked[0]["score"] >= 90.0:
            top = ranked[0]
            tid = str(top["id"])  # ensure str
            if tid not in favorites:
                # Add favorite, idempotent because TIDAL ignores duplicates
                try:
                    sess.user.favorites.add_artist(int(tid))
                except Exception:
                    # Some versions expect int id
                    pass
                favorites.add(tid)
            with SessionLocal() as db:
                upsert_artist_map(db, sp_id, name, tid, str(top["name"]), float(top["score"]), True)
            auto_matched += 1
        else:
            # Record pending resolution for UI
            with SessionLocal() as db:
                add_pending_resolution(db, sp_id, name, [dict(x) for x in ranked[:10]])
                upsert_artist_map(db, sp_id, name, None, None, float(ranked[0]["score"]) if ranked else 0.0, False)
            pending += 1

    return JSONResponse({"artists": len(artists), "auto_matched": auto_matched, "pending": pending})


# ---- Background job based sync with progress -----


def _job_set(job_id: str, **kwargs) -> None:
    with _jobs_lock:
        _jobs.setdefault(job_id, {})
        _jobs[job_id].update(kwargs)


def _run_sync_artists_job(job_id: str, limit: int = 0) -> None:
    _log.info(f"[job {job_id}] Sync followed artists: start")
    _job_set(job_id, state="running", started_at=datetime.now(UTC).isoformat())
    try:
        # Check auth
        try:
            get_spotify_client()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            _log.exception(f"[job {job_id}] Spotify not authorized")
            return

        if not TIDAL_ENABLED:
            _job_set(job_id, state="error", error="TIDAL integration is disabled")
            _log.error(f"[job {job_id}] TIDAL integration is disabled")
            return

        try:
            sess = get_tidal_session()
            # Log some session diagnostics
            try:
                uid = getattr(getattr(sess, "user", None), "id", None)
                ccode = getattr(sess, "country_code", None)
                _log.info(f"[job {job_id}] TIDAL session active. user={uid} country={ccode}")
            except Exception:
                pass
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            _log.exception(f"[job {job_id}] TIDAL not authorized")
            return

        # Fetch data
        artists: list[dict[str, str]] = []
        after: str | None = None
        while True:
            page = call_spotify(lambda sp, after=after: sp.current_user_followed_artists(limit=50, after=after)) or {}
            artists_block = page.get("artists") or {}
            items = artists_block.get("items") or []
            for a in items:
                artists.append({"id": a["id"], "name": a["name"]})
            cursors = artists_block.get("cursors") or {}
            after = cursors.get("after")
            if not after:
                break

        if limit and limit > 0:
            artists = artists[:limit]
        total = len(artists)
        _job_set(job_id, total=total, processed=0, auto_matched=0, pending_count=0)
        _log.info(f"[job {job_id}] Loaded {total} followed artists from Spotify")

        favorites = _tidal_favorite_artists_set(sess)

        # Prefetch Spotify genres in batches (best-effort)
        sp_genres: dict[str, list[str]] = {}
        try:
            ids = [a["id"] for a in artists]
            i = 0
            while i < len(ids):
                chunk = ids[i : i + 50]
                try:
                    res = call_spotify(lambda sp, chunk=chunk: sp.artists(chunk)) or {}
                    arts = res.get("artists") or []
                    for ar in arts:
                        gid = ar.get("id")
                        gens = ar.get("genres") or []
                        if gid:
                            sp_genres[str(gid)] = [str(g) for g in gens if isinstance(g, str)]
                except Exception:
                    pass
                i += 50
        except Exception:
            sp_genres = {}

        # Proactively clear any stale pending entries that are already resolved
        with SessionLocal() as db:
            removed = cleanup_pending_for_resolved(db)
            if removed:
                _log.info(f"[job {job_id}] Cleared {removed} stale pending entries for already-resolved artists")

        auto_matched = 0
        pending = 0
        processed = 0

        for art in artists:
            name = art["name"]
            sp_id = art["id"]
            try:
                candidates = _tidal_search_artists(sess, name)
                if not candidates:
                    _log.debug(f"[job {job_id}] No TIDAL candidates for '{name}'")
                else:
                    _log.info(f"[job {job_id}] Candidates for '{name}': {len(candidates)}")
                ranked: list[ArtistCandidate] = score_artist_match(name, candidates)
                # Prefer exact-normalized matches if any
                norm_q = _norm_artist_name(name)
                exact_candidate = next((c for c in candidates if _norm_artist_name(c["name"]) == norm_q), None)
                # Also compute normalized fuzzy scores and take the better of raw vs normalized
                top_raw: ArtistCandidate | None = ranked[0] if ranked else None
                top_norm: ArtistCandidate | None = None
                try:
                    norm_candidates = [{"id": c["id"], "name": _norm_artist_name(c["name"])} for c in candidates]
                    norm_ranked: list[ArtistCandidate] = score_artist_match(norm_q, norm_candidates)
                    if norm_ranked:
                        # map back to original name
                        best_id = norm_ranked[0]["id"]
                        best_score = float(norm_ranked[0]["score"])
                        orig_name = next((c["name"] for c in candidates if c["id"] == best_id), None)
                        if orig_name:
                            top_norm = {"id": best_id, "name": orig_name, "score": best_score}
                except Exception:
                    pass

                top: ArtistCandidate | None
                if exact_candidate:
                    top = {"id": exact_candidate["id"], "name": exact_candidate["name"], "score": 100.0}
                else:
                    # Choose max of raw vs normalized
                    if top_raw and top_norm:
                        top = top_raw if float(top_raw["score"]) >= float(top_norm["score"]) else top_norm
                    else:
                        top = top_raw or top_norm
                if top and float(top["score"]) >= 85.0:
                    tid = str(top["id"])
                    if tid not in favorites:
                        try:
                            sess.user.favorites.add_artist(int(tid))
                        except Exception:
                            pass
                        favorites.add(tid)
                    with SessionLocal() as db:
                        upsert_artist_map(db, sp_id, name, tid, str(top["name"]), float(top["score"]), True)
                        # Update genres if known
                        if sp_genres.get(sp_id):
                            update_artist_genres(db, sp_id, sp_genres.get(sp_id))
                        # Remove any stale pending entries for this artist now that it's resolved
                        delete_pending_by_spotify_id(db, sp_id)
                        add_artist_sync_event(db, sp_id, name, tid, str(top["name"]), "auto")
                    _log.info(f"[job {job_id}] Auto-matched '{name}' -> '{top['name']}' (score {top['score']:.0f})")
                    auto_matched += 1
                else:
                    with SessionLocal() as db:
                        add_pending_resolution(db, sp_id, name, [dict(x) for x in ranked[:10]])
                        upsert_artist_map(
                            db, sp_id, name, None, None, float(ranked[0]["score"]) if ranked else 0.0, False
                        )
                        # Still update genres even if pending; useful for browsing
                        if sp_genres.get(sp_id):
                            update_artist_genres(db, sp_id, sp_genres.get(sp_id))
                    pending += 1
            except Exception as e:
                _log.warning(f"[job {job_id}] Error processing artist '{name}': {e}")
                with SessionLocal() as db:
                    add_pending_resolution(db, sp_id, name, [])
                pending += 1
            finally:
                processed += 1
                if processed % 5 == 0 or processed == total:
                    _job_set(job_id, processed=processed, auto_matched=auto_matched, pending_count=pending)

        _job_set(
            job_id,
            state="done",
            finished_at=datetime.now(UTC).isoformat(),
            processed=processed,
            auto_matched=auto_matched,
            pending_count=pending,
        )
        _log.info(f"[job {job_id}] Done. total={total} auto_matched={auto_matched} pending={pending}")
    except Exception as e:
        _job_set(job_id, state="error", error=str(e))
        _log.exception(f"[job {job_id}] Fatal error")


@app.post("/sync/artists/start")
async def start_sync_artists(request: Request):
    # optional debug limit via query param: /sync/artists/start?limit=25
    limit_param = request.query_params.get("limit")
    try:
        limit = int(limit_param) if limit_param else 0
    except ValueError:
        limit = 0
    job_id = str(uuid.uuid4())
    _job_set(job_id, state="pending", limit=limit)
    threading.Thread(target=_run_sync_artists_job, args=(job_id, limit), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/sync/artists/status")
async def sync_artists_status(job_id: str):
    with _jobs_lock:
        data = _jobs.get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(data)


@app.get("/pending", response_class=HTMLResponse)
async def list_pending(request: Request):
    with SessionLocal() as db:
        items = get_pending(db)
    return templates.TemplateResponse("pending.html", {"request": request, "items": items})


@app.post("/resolve/{pending_id}")
async def resolve_pending(pending_id: int, tidal_id: str = Form(...), tidal_name: str = Form(...)):
    sess = get_tidal_session()
    with SessionLocal() as db:
        # Find the spotify info from pending list
        items = {p["id"]: p for p in get_pending(db)}
        if pending_id not in items:
            raise HTTPException(status_code=404, detail="Pending item not found")
        p = items[pending_id]
        sp_id = p["spotify_id"]
        sp_name = p["spotify_name"]
        # Add favorite if needed
        favs = _tidal_favorite_artists_set(sess)
        if tidal_id not in favs:
            try:
                sess.user.favorites.add_artist(int(tidal_id))
            except Exception:
                pass
        upsert_artist_map(db, sp_id, sp_name, tidal_id, tidal_name, 0.0, True)
        add_artist_sync_event(db, sp_id, sp_name, tidal_id, tidal_name, "manual")
        delete_pending(db, pending_id)
    return JSONResponse({"status": "ok"})


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# ---- Tracks sync: Spotify liked tracks -> TIDAL favorites ----


def _tidal_favorite_tracks_set(sess) -> set:
    try:
        favs = sess.user.favorites.tracks()
        return {str(t.id) for t in favs}
    except Exception:
        return set()


def _score_track_candidate(
    sp_title: str, sp_artist: str, sp_isrc: str | None, sp_dur: int | None, cand: dict[str, Any]
) -> float:
    # ISRC exact match wins
    if sp_isrc and cand.get("isrc") and str(cand.get("isrc")).upper() == sp_isrc.upper():
        return 1000.0
    # Compute normalized scores
    qt = _norm_track_title(sp_title)
    qa = _norm_artist_name(sp_artist)
    ct = _norm_track_title(cand.get("title") or "")
    cand_artist_name = ""
    arts = cand.get("artists")
    if isinstance(arts, list) and arts:
        first = arts[0]
        if isinstance(first, dict):
            cand_artist_name = str(first.get("name") or "")
        else:
            cand_artist_name = str(first)
    else:
        cand_artist_name = str(cand.get("artist") or "")
    ca = _norm_artist_name(cand_artist_name)
    from rapidfuzz import fuzz

    s_title = float(fuzz.WRatio(qt, ct))
    s_artist = float(fuzz.WRatio(qa, ca))
    s = 0.7 * s_title + 0.3 * s_artist
    # duration bonus if within 3 seconds
    try:
        _d = cand.get("duration")
        cdur = int(_d) if isinstance(_d, int | float | str) else None
        if sp_dur and cdur and abs(int(sp_dur) - cdur) <= 3:
            s += 10.0
    except Exception:
        pass
    return s


def _run_sync_tracks_job(job_id: str, limit: int = 0) -> None:
    _job_set(job_id, state="running", started_at=datetime.now(UTC).isoformat())
    try:
        try:
            get_spotify_client()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        if not TIDAL_ENABLED:
            _job_set(job_id, state="error", error="TIDAL integration is disabled")
            return

        try:
            sess = get_tidal_session()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        # Fetch liked tracks from Spotify
        tracks: list[dict[str, Any]] = []
        offset = 0
        page_size = 50
        while True:
            page = (
                call_spotify(
                    lambda sp, offset=offset, page_size=page_size: sp.current_user_saved_tracks(
                        limit=page_size, offset=offset
                    )
                )
                or {}
            )
            items = page.get("items") or []
            for it in items:
                t = it.get("track") or {}
                if not t:
                    continue
                tid = t.get("id")
                name = t.get("name")
                artists = t.get("artists") or []
                artist_name = artists[0]["name"] if artists else ""
                artist_id = artists[0].get("id") if artists and isinstance(artists[0], dict) else None
                duration_ms = t.get("duration_ms")
                isrc = None
                ext = t.get("external_ids") or {}
                if ext.get("isrc"):
                    isrc = ext.get("isrc")
                dur = int(duration_ms / 1000) if duration_ms else None
                if tid and name:
                    tracks.append(
                        {
                            "id": tid,
                            "title": name,
                            "artist": artist_name,
                            "artist_id": artist_id,
                            "isrc": isrc,
                            "duration": dur,
                        }
                    )
            offset += len(items)
            if limit and len(tracks) >= limit:
                tracks = tracks[:limit]
                break
            if not page.get("next"):
                break

        total = len(tracks)
        _job_set(job_id, total=total, processed=0, auto_matched=0, pending_count=0)

        # Cleanup stale pending
        with SessionLocal() as db:
            removed = cleanup_pending_tracks_for_resolved(db)
            if removed:
                _log.info("[job %s] Cleared %d stale pending tracks", job_id, removed)

        favorites = _tidal_favorite_tracks_set(sess)
        processed = 0
        auto_matched = 0
        pending = 0
        for tr in tracks:
            spid = tr["id"]
            title = tr["title"]
            artist = tr["artist"]
            s_artist_id = tr.get("artist_id")
            isrc = tr.get("isrc")
            dur = tr.get("duration")
            try:
                cands = _tidal_search_tracks(sess, title, artist)
                # Evaluate candidates
                ranked: list[dict[str, Any]] = []
                for c in cands:
                    score = _score_track_candidate(title, artist, isrc, dur, c)
                    c2 = dict(c)
                    c2["score"] = float(score)
                    ranked.append(c2)
                ranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
                top = ranked[0] if ranked else None
                accept = False
                if top:
                    if float(top.get("score", 0.0)) >= 95.0:
                        accept = True
                    # ISRC winner yields score >= 1000
                    if float(top.get("score", 0.0)) >= 900.0:
                        accept = True
                if accept and top:
                    tid = str(top["id"])
                    # Extract primary TIDAL artist info
                    t_artist_name = None
                    t_artist_id = None
                    t_arts = top.get("artists")
                    if isinstance(t_arts, list) and t_arts:
                        first = t_arts[0]
                        if isinstance(first, dict):
                            t_artist_name = first.get("name")
                            t_artist_id = first.get("id")
                        else:
                            t_artist_name = str(first)
                    if tid not in favorites:
                        try:
                            sess.user.favorites.add_track(int(tid))
                        except Exception:
                            pass
                        favorites.add(tid)
                    with SessionLocal() as db:
                        upsert_track_map(
                            db,
                            spid,
                            title,
                            artist,
                            s_artist_id,
                            tid,
                            top.get("title"),
                            t_artist_name,
                            t_artist_id,
                            isrc,
                            dur,
                            (top.get("duration") if isinstance(top.get("duration"), int | float) else None),
                            float(top.get("score", 0.0)),
                            True,
                        )
                        delete_pending_track_by_spotify_id(db, spid)
                        add_track_sync_event(
                            db,
                            spid,
                            title,
                            artist,
                            tid,
                            str(top.get("title") or ""),
                            str(t_artist_name or ""),
                            isrc,
                            "auto",
                        )
                    auto_matched += 1
                else:
                    with SessionLocal() as db:
                        add_pending_track_resolution(db, spid, title, artist, isrc, ranked[:10])
                        upsert_track_map(
                            db,
                            spid,
                            title,
                            artist,
                            s_artist_id,
                            None,
                            None,
                            None,
                            None,
                            isrc,
                            dur,
                            None,
                            float(ranked[0]["score"]) if ranked else 0.0,
                            False,
                        )
                    pending += 1
            except Exception:
                with SessionLocal() as db:
                    add_pending_track_resolution(db, spid, title, artist, isrc, [])
                pending += 1
            finally:
                processed += 1
                if processed % 5 == 0 or processed == total:
                    _job_set(job_id, processed=processed, auto_matched=auto_matched, pending_count=pending)

        _job_set(
            job_id,
            state="done",
            finished_at=datetime.now(UTC).isoformat(),
            processed=processed,
            auto_matched=auto_matched,
            pending_count=pending,
        )
    except Exception as e:
        _job_set(job_id, state="error", error=str(e))


def _run_sync_playlists_job(job_id: str, limit: int = 0) -> None:
    _job_set(job_id, state="running", started_at=datetime.now(UTC).isoformat())
    try:
        try:
            get_spotify_client()
            me = call_spotify(lambda sp: sp.me()) or {}
            my_spotify_user_id = (me.get("id") or "").strip()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        if not TIDAL_ENABLED:
            _job_set(job_id, state="error", error="TIDAL integration is disabled")
            return

        try:
            sess = get_tidal_session()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        # Fetch user-owned Spotify playlists
        playlists: list[dict[str, Any]] = []
        offset = 0
        page_size = 50
        while True:
            page = (
                call_spotify(
                    lambda sp, offset=offset, page_size=page_size: sp.current_user_playlists(
                        limit=page_size, offset=offset
                    )
                )
                or {}
            )
            items = page.get("items") or []
            for pl in items:
                owner = pl.get("owner") or {}
                if my_spotify_user_id and (owner.get("id") or "").strip() != my_spotify_user_id:
                    continue  # only manually created (owned) playlists
                pid = pl.get("id")
                name = pl.get("name")
                if pid and name:
                    playlists.append({"id": pid, "name": name})
            offset += len(items)
            if limit and len(playlists) >= limit:
                playlists = playlists[:limit]
                break
            if not page.get("next"):
                break

        total = len(playlists)
        processed = 0
        created = 0
        updated = 0
        _job_set(job_id, total=total, processed=processed, created=created, updated=updated)
        for pl in playlists:
            sp_pl_id = pl["id"]
            sp_pl_name = pl["name"]
            try:
                # Ensure we have a TIDAL playlist mapped/created
                tidal_pl_id: str | None = None
                with SessionLocal() as db:
                    m = get_playlist_map(db, sp_pl_id)
                    if m and m.get("tidal_id"):
                        tidal_pl_id = str(m["tidal_id"])
                tidal_playlist_obj = None
                if tidal_pl_id:
                    from tidalapi.playlist import Playlist as TidalPlaylist

                    try:
                        tidal_playlist_obj = TidalPlaylist(sess, tidal_pl_id).factory()
                    except Exception:
                        tidal_playlist_obj = None
                # Track whether this playlist already existed on TIDAL and whether we actually modify it
                preexisting = tidal_playlist_obj is not None
                did_modify = False
                if tidal_playlist_obj is None:
                    # Create new TIDAL playlist
                    t_pl = sess.user.create_playlist(sp_pl_name, f"Synced from Spotify: {sp_pl_id}")
                    tidal_pl_id = str(t_pl.id)
                    tidal_playlist_obj = t_pl
                    created += 1

                # Build set of existing TIDAL track IDs in the playlist
                existing_ids: set[str] = set()
                try:
                    off = 0
                    while True:
                        chunk = tidal_playlist_obj.tracks(limit=100, offset=off)
                        if not chunk:
                            break
                        for tr in chunk:
                            existing_ids.add(str(tr.id))
                        if len(chunk) < 100:
                            break
                        off += len(chunk)
                except Exception:
                    pass

                # Fetch Spotify playlist tracks in order
                sp_track_ids: list[str] = []
                sp_tracks_meta: dict[str, dict[str, Any]] = {}
                off2 = 0
                while True:
                    page = (
                        call_spotify(
                            lambda sp, sp_pl_id=sp_pl_id, off2=off2: sp.playlist_tracks(
                                sp_pl_id, limit=100, offset=off2
                            )
                        )
                        or {}
                    )
                    items = page.get("items") or []
                    for it in items:
                        t = it.get("track") or {}
                        tid = t.get("id")
                        if not tid:
                            continue
                        sp_track_ids.append(tid)
                        name = t.get("name")
                        artists = t.get("artists") or []
                        artist_name = artists[0]["name"] if artists else ""
                        artist_id = artists[0].get("id") if artists and isinstance(artists[0], dict) else None
                        duration_ms = t.get("duration_ms")
                        dur = int(duration_ms / 1000) if duration_ms else None
                        isrc = (t.get("external_ids") or {}).get("isrc")
                        sp_tracks_meta[tid] = {
                            "title": name,
                            "artist": artist_name,
                            "artist_id": artist_id,
                            "duration": dur,
                            "isrc": isrc,
                        }
                    off2 += len(items)
                    if not page.get("next"):
                        break

                # Map to TIDAL track IDs, using existing TrackMap or fallback matching
                tidal_to_add_ordered: list[str] = []
                sid_to_meta: dict[str, dict[str, Any]] = {}
                with SessionLocal() as db:
                    for sid in sp_track_ids:
                        tm = db.get(TrackMap, sid)
                        if (tm is not None) and (getattr(tm, "tidal_id", None)):
                            tidal_to_add_ordered.append(str(tm.tidal_id))
                            sid_to_meta[sid] = {
                                "tidal_id": str(tm.tidal_id),
                                "tidal_title": tm.tidal_title,
                                "tidal_artist": tm.tidal_artist,
                            }
                            continue
                        meta = sp_tracks_meta.get(sid) or {}
                        cands = _tidal_search_tracks(sess, meta.get("title") or "", meta.get("artist") or "")
                        ranked: list[dict[str, Any]] = []
                        for c in cands:
                            score = _score_track_candidate(
                                meta.get("title") or "",
                                meta.get("artist") or "",
                                meta.get("isrc"),
                                meta.get("duration"),
                                c,
                            )
                            c2 = dict(c)
                            c2["score"] = float(score)
                            ranked.append(c2)
                        ranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
                        top = ranked[0] if ranked else None
                        accept = False
                        if top:
                            if float(top.get("score", 0.0)) >= 95.0:
                                accept = True
                            if float(top.get("score", 0.0)) >= 900.0:
                                accept = True
                        if accept and top:
                            tid = str(top["id"])
                            t_artist_name = None
                            t_artist_id = None
                            t_arts = top.get("artists")
                            if isinstance(t_arts, list) and t_arts:
                                first = t_arts[0]
                                if isinstance(first, dict):
                                    t_artist_name = first.get("name")
                                    t_artist_id = first.get("id")
                                else:
                                    t_artist_name = str(first)
                            upsert_track_map(
                                db,
                                sid,
                                meta.get("title") or "",
                                meta.get("artist") or "",
                                meta.get("artist_id"),
                                tid,
                                top.get("title"),
                                t_artist_name,
                                t_artist_id,
                                meta.get("isrc"),
                                meta.get("duration"),
                                (top.get("duration") if isinstance(top.get("duration"), int | float) else None),
                                float(top.get("score", 0.0)),
                                True,
                            )
                            tidal_to_add_ordered.append(tid)
                            sid_to_meta[sid] = {
                                "tidal_id": tid,
                                "tidal_title": top.get("title"),
                                "tidal_artist": t_artist_name,
                            }
                        else:
                            # leave unmapped; could add pending resolution
                            add_pending_track_resolution(
                                db,
                                sid,
                                meta.get("title") or "",
                                meta.get("artist") or "",
                                meta.get("isrc"),
                                ranked[:10],
                            )

                # Add any missing to the playlist, skipping duplicates
                to_add_final: list[str] = [tid for tid in tidal_to_add_ordered if tid not in existing_ids]
                _log.info(
                    "[job %s] Playlist '%s' (%s): mapped=%d existing_on_tidal=%d to_add=%d",
                    job_id,
                    sp_pl_name,
                    tidal_pl_id,
                    len(tidal_to_add_ordered),
                    len(existing_ids),
                    len(to_add_final),
                )

                added_count = 0
                if to_add_final:
                    # chunk in 100s
                    i = 0
                    while i < len(to_add_final):
                        chunk = to_add_final[i : i + 100]
                        added_count += _tidal_playlist_add_with_fallbacks(tidal_playlist_obj, chunk)
                        i += len(chunk)
                _log.info(
                    "[job %s] Playlist '%s' (%s): added_to_tidal=%d",
                    job_id,
                    sp_pl_name,
                    tidal_pl_id,
                    added_count,
                )
                if added_count > 0:
                    did_modify = True

                # Second-pass reconciliation: re-fetch playlist tracks and retry missing ones once
                try:
                    refreshed_existing: set[str] = set()
                    off_chk = 0
                    while True:
                        chunk_chk = tidal_playlist_obj.tracks(limit=100, offset=off_chk)
                        if not chunk_chk:
                            break
                        for tr in chunk_chk:
                            refreshed_existing.add(str(getattr(tr, "id", "")))
                        if len(chunk_chk) < 100:
                            break
                        off_chk += len(chunk_chk)
                except Exception:
                    refreshed_existing = existing_ids

                missing_after: list[str] = [tid for tid in tidal_to_add_ordered if tid not in refreshed_existing]
                if missing_after:
                    _log.info(
                        "[job %s] Playlist '%s' (%s): retrying missing=%d",
                        job_id,
                        sp_pl_name,
                        tidal_pl_id,
                        len(missing_after),
                    )
                    j = 0
                    retry_added = 0
                    while j < len(missing_after):
                        chunk2 = missing_after[j : j + 100]
                        retry_added += _tidal_playlist_add_with_fallbacks(tidal_playlist_obj, chunk2)
                        j += len(chunk2)
                    if retry_added > 0:
                        did_modify = True

                # Count as updated only if a pre-existing TIDAL playlist actually changed
                if preexisting and did_modify:
                    updated += 1

                # Update mapping record
                with SessionLocal() as db:
                    upsert_playlist_map(
                        db, sp_pl_id, sp_pl_name, tidal_pl_id, getattr(tidal_playlist_obj, "name", None)
                    )
                    # Replace playlist tracks snapshot with current Spotify order
                    entries: list[dict[str, Any]] = []
                    for idx, sid in enumerate(sp_track_ids, start=1):
                        meta = sp_tracks_meta.get(sid) or {}
                        tmeta = sid_to_meta.get(sid) or {}
                        entries.append(
                            {
                                "position": idx,
                                "spotify_track_id": sid,
                                "spotify_title": meta.get("title") or "",
                                "spotify_artist": meta.get("artist") or "",
                                "spotify_duration": meta.get("duration"),
                                "isrc": meta.get("isrc"),
                                "tidal_track_id": tmeta.get("tidal_id"),
                                "tidal_title": tmeta.get("tidal_title"),
                                "tidal_artist": tmeta.get("tidal_artist"),
                            }
                        )
                    replace_playlist_tracks(db, sp_pl_id, entries)

            except Exception:
                pass
            finally:
                processed += 1
                _job_set(job_id, processed=processed, created=created, updated=updated)

        _job_set(
            job_id,
            state="done",
            finished_at=datetime.now(UTC).isoformat(),
            processed=processed,
            created=created,
            updated=updated,
        )
    except Exception as e:
        _job_set(job_id, state="error", error=str(e))


def _run_sync_apple_likes_job(job_id: str, limit: int = 0) -> None:
    """Sync Spotify liked tracks to Apple Music library."""
    _job_set(job_id, state="running", started_at=datetime.now(UTC).isoformat())
    try:
        # Check Spotify auth
        try:
            get_spotify_client()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        # Check Apple Music auth
        try:
            apple_client = get_apple_client()
            storefront = apple_client.get_storefront()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        # Fetch liked tracks from Spotify
        tracks: list[dict[str, Any]] = []
        offset = 0
        page_size = 50
        while True:
            page = (
                call_spotify(
                    lambda sp, offset=offset, page_size=page_size: sp.current_user_saved_tracks(
                        limit=page_size, offset=offset
                    )
                )
                or {}
            )
            items = page.get("items") or []
            for it in items:
                t = it.get("track") or {}
                if not t:
                    continue
                tid = t.get("id")
                name = t.get("name")
                artists = t.get("artists") or []
                artist_name = artists[0]["name"] if artists else ""
                artist_id = artists[0].get("id") if artists and isinstance(artists[0], dict) else None
                duration_ms = t.get("duration_ms")
                isrc = None
                ext = t.get("external_ids") or {}
                if ext.get("isrc"):
                    isrc = ext.get("isrc")
                dur = int(duration_ms / 1000) if duration_ms else None
                if tid and name:
                    tracks.append(
                        {
                            "id": tid,
                            "title": name,
                            "artist": artist_name,
                            "artist_id": artist_id,
                            "isrc": isrc,
                            "duration": dur,
                        }
                    )
            offset += len(items)
            if limit and len(tracks) >= limit:
                tracks = tracks[:limit]
                break
            if not page.get("next"):
                break

        total = len(tracks)
        _job_set(job_id, total=total, processed=0, added_to_library=0, already_in_library=0, pending_count=0)

        # Cleanup stale pending
        with SessionLocal() as db:
            removed = cleanup_pending_tracks_for_resolved(db)
            if removed:
                _log.info("[job %s] Cleared %d stale pending tracks", job_id, removed)

        processed = 0
        added_to_library = 0
        already_in_library = 0
        pending = 0

        # Best-effort preload of existing Apple library IDs for accurate counters
        existing_library_ids: set[str] = set()
        try:
            existing_library_ids = apple_client.list_library_song_ids()
        except Exception:
            existing_library_ids = set()

        # Batch tracks for efficient library adds
        apple_tracks_to_add: list[str] = []

        for tr in tracks:
            title = tr["title"]
            artist = tr["artist"]
            isrc = tr.get("isrc")
            dur = tr.get("duration")
            try:
                apple_track: dict[str, Any] | None = None

                # Try ISRC-first match
                if isrc:
                    try:
                        apple_track = apple_client.search_by_isrc(isrc, storefront)
                    except Exception:
                        pass

                # Fallback to fuzzy search
                if not apple_track:
                    try:
                        candidates = apple_client.search_track(
                            title=title,
                            artist=artist,
                            duration_s=dur,
                            storefront=storefront,
                            limit=10,
                        )
                        if candidates:
                            # Score candidates using existing helper
                            ranked: list[dict[str, Any]] = []
                            for c in candidates:
                                score = _score_track_candidate(
                                    title,
                                    artist,
                                    isrc,
                                    dur,
                                    {
                                        "id": c["id"],
                                        "title": c["name"],
                                        "artists": c["artists"],
                                        "duration": (int(c["duration_ms"] / 1000) if c.get("duration_ms") else None),
                                        "isrc": c.get("isrc"),
                                    },
                                )
                                c2 = dict(c)
                                c2["score"] = float(score)
                                ranked.append(c2)
                            ranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
                            top = ranked[0] if ranked else None

                            # Accept high confidence matches (>= 95 or ISRC match >= 900)
                            if top and (float(top.get("score", 0.0)) >= 95.0 or float(top.get("score", 0.0)) >= 900.0):
                                apple_track = top
                    except Exception:
                        pass

                if apple_track:
                    # Extract Apple Music track details
                    apple_id = apple_track["id"]
                    apple_title = apple_track.get("name", "")
                    apple_artists = apple_track.get("artists", [])
                    apple_artist_name = apple_artists[0] if apple_artists else None
                    apple_duration_ms = apple_track.get("duration_ms")
                    apple_duration = int(apple_duration_ms / 1000) if apple_duration_ms else None
                    confidence = float(apple_track.get("score", 0.0))

                    # Persist track mapping
                    with SessionLocal() as db:
                        upsert_track_map(
                            db,
                            tr["id"],
                            title,
                            artist,
                            tr.get("artist_id"),
                            apple_id,
                            apple_title,
                            apple_artist_name,
                            None,  # apple_artist_id not available
                            isrc,
                            dur,
                            apple_duration,
                            confidence,
                            True,
                            target_service="apple",
                        )
                        delete_pending_track_by_spotify_id(db, tr["id"])
                        add_track_sync_event(
                            db,
                            tr["id"],
                            title,
                            artist,
                            apple_id,
                            apple_title,
                            apple_artist_name or "",
                            isrc,
                            "auto",
                            target_service="apple",
                        )

                    # Queue for batch add to library only if not already present
                    if apple_id in existing_library_ids:
                        already_in_library += 1
                    else:
                        apple_tracks_to_add.append(apple_id)
                        # Avoid duplicate add attempts during the same run
                        existing_library_ids.add(apple_id)

                    # Batch add in chunks of 100
                    if len(apple_tracks_to_add) >= 100:
                        try:
                            result = apple_client.add_tracks_to_library(apple_tracks_to_add)
                            added_to_library += result.succeeded
                        except Exception:
                            pass
                        apple_tracks_to_add = []
                else:
                    # No match found, add to pending
                    with SessionLocal() as db:
                        # Store unmatched track in pending resolution queue
                        if isinstance(apple_track, dict) and "score" in apple_track:
                            # We have candidates but none met threshold
                            add_pending_track_resolution(
                                db, tr["id"], title, artist, isrc, [apple_track], target_service="apple"
                            )
                            confidence_score = float(apple_track.get("score", 0.0))
                        else:
                            # No candidates at all
                            add_pending_track_resolution(db, tr["id"], title, artist, isrc, [], target_service="apple")
                            confidence_score = 0.0

                        # Store partial mapping (unresolved)
                        upsert_track_map(
                            db,
                            tr["id"],
                            title,
                            artist,
                            tr.get("artist_id"),
                            None,
                            None,
                            None,
                            None,
                            isrc,
                            dur,
                            None,
                            confidence_score,
                            False,
                            target_service="apple",
                        )
                        pending += 1
            except Exception:
                pending += 1
            finally:
                processed += 1
                if processed % 10 == 0 or processed == total:
                    _job_set(
                        job_id,
                        processed=processed,
                        added_to_library=added_to_library,
                        already_in_library=already_in_library,
                        pending_count=pending,
                    )

        # Add remaining tracks
        if apple_tracks_to_add:
            try:
                result = apple_client.add_tracks_to_library(apple_tracks_to_add)
                added_to_library += result.succeeded
            except Exception:
                pass

        _job_set(
            job_id,
            state="done",
            finished_at=datetime.now(UTC).isoformat(),
            processed=processed,
            added_to_library=added_to_library,
            already_in_library=already_in_library,
            pending_count=pending,
        )
    except Exception as e:
        _job_set(job_id, state="error", error=str(e))


def _run_sync_apple_playlists_job(job_id: str, limit: int = 0) -> None:
    """Sync user-owned Spotify playlists to Apple Music."""
    _job_set(job_id, state="running", started_at=datetime.now(UTC).isoformat())
    try:
        # Check Spotify auth
        try:
            get_spotify_client()
            me = call_spotify(lambda sp: sp.me()) or {}
            my_spotify_user_id = (me.get("id") or "").strip()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        # Check Apple Music auth
        try:
            apple_client = get_apple_client()
            storefront = apple_client.get_storefront()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        # Fetch user-owned Spotify playlists
        playlists: list[dict[str, Any]] = []
        offset = 0
        page_size = 50
        while True:
            page = (
                call_spotify(
                    lambda sp, offset=offset, page_size=page_size: sp.current_user_playlists(
                        limit=page_size, offset=offset
                    )
                )
                or {}
            )
            items = page.get("items") or []
            for pl in items:
                owner = pl.get("owner") or {}
                if my_spotify_user_id and (owner.get("id") or "").strip() != my_spotify_user_id:
                    continue  # only manually created (owned) playlists
                pid = pl.get("id")
                name = pl.get("name")
                if pid and name:
                    playlists.append({"id": pid, "name": name})
            offset += len(items)
            if limit and len(playlists) >= limit:
                playlists = playlists[:limit]
                break
            if not page.get("next"):
                break

        total = len(playlists)
        processed = 0
        created = 0
        updated = 0
        _job_set(job_id, total=total, processed=processed, created=created, updated=updated)

        for pl in playlists:
            sp_pl_id = pl["id"]
            sp_pl_name = pl["name"]
            try:
                # Ensure we have an Apple Music playlist mapped/created
                apple_pl_id: str | None = None
                with SessionLocal() as db:
                    m = get_playlist_map(db, sp_pl_id, target_service="apple")
                    if m and m.get("target_id"):
                        apple_pl_id = str(m["target_id"])

                preexisting = apple_pl_id is not None
                did_modify = False

                if not apple_pl_id:
                    # Create new Apple Music playlist
                    apple_pl = apple_client.ensure_playlist(sp_pl_name, f"Synced from Spotify: {sp_pl_id}")
                    apple_pl_id = apple_pl["id"]
                    created += 1

                # Existing Apple playlist tracks are not fetched yet; rely on
                # Apple Music duplicate handling for now.
                # For now, we'll rely on Apple Music's duplicate handling
                existing_ids: set[str] = set()

                # Fetch Spotify playlist tracks in order
                sp_track_ids: list[str] = []
                sp_tracks_meta: dict[str, dict[str, Any]] = {}
                off2 = 0
                while True:
                    page = (
                        call_spotify(
                            lambda sp, sp_pl_id=sp_pl_id, off2=off2: sp.playlist_tracks(
                                sp_pl_id, limit=100, offset=off2
                            )
                        )
                        or {}
                    )
                    items = page.get("items") or []
                    for it in items:
                        t = it.get("track") or {}
                        tid = t.get("id")
                        if not tid:
                            continue
                        sp_track_ids.append(tid)
                        name = t.get("name")
                        artists = t.get("artists") or []
                        artist_name = artists[0]["name"] if artists else ""
                        artist_id = artists[0].get("id") if artists and isinstance(artists[0], dict) else None
                        duration_ms = t.get("duration_ms")
                        dur = int(duration_ms / 1000) if duration_ms else None
                        isrc = (t.get("external_ids") or {}).get("isrc")
                        sp_tracks_meta[tid] = {
                            "title": name,
                            "artist": artist_name,
                            "artist_id": artist_id,
                            "duration": dur,
                            "isrc": isrc,
                        }
                    off2 += len(items)
                    if not page.get("next"):
                        break

                # Map Spotify tracks to Apple Music catalog IDs
                apple_to_add_ordered: list[str] = []
                sid_to_meta: dict[str, dict[str, Any]] = {}

                for sid in sp_track_ids:
                    meta = sp_tracks_meta.get(sid) or {}
                    apple_track: dict[str, Any] | None = None

                    # Try ISRC-first match
                    if meta.get("isrc"):
                        try:
                            apple_track = apple_client.search_by_isrc(meta["isrc"], storefront)
                        except Exception:
                            pass

                    # Fallback to fuzzy search
                    if not apple_track:
                        try:
                            candidates = apple_client.search_track(
                                title=meta.get("title") or "",
                                artist=meta.get("artist") or "",
                                duration_s=meta.get("duration"),
                                storefront=storefront,
                                limit=10,
                            )
                            if candidates:
                                # Score candidates using existing helper
                                ranked: list[dict[str, Any]] = []
                                for c in candidates:
                                    score = _score_track_candidate(
                                        meta.get("title") or "",
                                        meta.get("artist") or "",
                                        meta.get("isrc"),
                                        meta.get("duration"),
                                        {
                                            "id": c["id"],
                                            "title": c["name"],
                                            "artists": c["artists"],
                                            "duration": (
                                                int(c["duration_ms"] / 1000) if c.get("duration_ms") else None
                                            ),
                                            "isrc": c.get("isrc"),
                                        },
                                    )
                                    c2 = dict(c)
                                    c2["score"] = float(score)
                                    ranked.append(c2)
                                ranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
                                top = ranked[0] if ranked else None
                                if top and float(top.get("score", 0.0)) >= 80.0:
                                    apple_track = top
                        except Exception:
                            pass

                    if apple_track:
                        apple_to_add_ordered.append(apple_track["id"])
                        sid_to_meta[sid] = {
                            "apple_id": apple_track["id"],
                            "apple_title": apple_track.get("name"),
                            "apple_artist": (apple_track["artists"][0] if apple_track.get("artists") else None),
                        }

                # Add tracks to Apple Music playlist
                to_add_final: list[str] = [aid for aid in apple_to_add_ordered if aid not in existing_ids]
                _log.info(
                    "[job %s] Playlist '%s' (%s): mapped=%d existing=%d to_add=%d",
                    job_id,
                    sp_pl_name,
                    apple_pl_id or "unknown",
                    len(apple_to_add_ordered),
                    len(existing_ids),
                    len(to_add_final),
                )

                added_count = 0
                if to_add_final and apple_pl_id:
                    try:
                        result = apple_client.add_tracks_to_playlist(apple_pl_id, to_add_final)
                        added_count = result.succeeded
                        did_modify = added_count > 0
                    except Exception:
                        pass

                _log.info(
                    "[job %s] Playlist '%s' (%s): added_to_apple=%d",
                    job_id,
                    sp_pl_name,
                    apple_pl_id or "unknown",
                    added_count,
                )

                if preexisting and did_modify:
                    updated += 1

                # Update mapping record
                with SessionLocal() as db:
                    upsert_playlist_map(
                        db,
                        sp_pl_id,
                        sp_pl_name,
                        None,
                        None,
                        target_service="apple",
                        target_id=apple_pl_id,
                        target_name=sp_pl_name,
                    )
                    # Replace playlist tracks snapshot
                    entries: list[dict[str, Any]] = []
                    for idx, sid in enumerate(sp_track_ids, start=1):
                        meta = sp_tracks_meta.get(sid) or {}
                        ameta = sid_to_meta.get(sid) or {}
                        entries.append(
                            {
                                "position": idx,
                                "spotify_track_id": sid,
                                "spotify_title": meta.get("title") or "",
                                "spotify_artist": meta.get("artist") or "",
                                "spotify_duration": meta.get("duration"),
                                "isrc": meta.get("isrc"),
                                "target_track_id": ameta.get("apple_id"),
                                "target_title": ameta.get("apple_title"),
                                "target_artist": ameta.get("apple_artist"),
                            }
                        )
                    replace_playlist_tracks(db, sp_pl_id, entries, target_service="apple")

            except Exception:
                pass
            finally:
                processed += 1
                _job_set(job_id, processed=processed, created=created, updated=updated)

        _job_set(
            job_id,
            state="done",
            finished_at=datetime.now(UTC).isoformat(),
            processed=processed,
            created=created,
            updated=updated,
        )
    except Exception as e:
        _job_set(job_id, state="error", error=str(e))


def _run_sync_apple_followed_artists_playlist_job(job_id: str, limit: int = 0, rebuild: bool = False) -> None:
    """Create/update an Apple Music playlist representing Spotify followed artists.

    Since Apple Music API doesn't expose artist follow/favorite write endpoints,
    this job builds a deterministic fallback playlist with one representative
    track per followed artist.

    Args:
        job_id: Unique job identifier for progress tracking
        limit: Optional limit on number of artists to process (0 = all)
        rebuild: If True, clear existing playlist tracks before repopulating (strict rebuild mode)
    """
    _job_set(job_id, state="running", started_at=datetime.now(UTC).isoformat())
    try:
        # Check Spotify auth
        try:
            get_spotify_client()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        # Check Apple Music auth
        try:
            apple_client = get_apple_client()
            storefront = apple_client.get_storefront()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        # Spotify market for top tracks (fallback to US)
        market = "US"
        try:
            me = call_spotify(lambda sp: sp.me()) or {}
            market = str(me.get("country") or "US").upper()
        except Exception:
            market = "US"

        # Fetch followed artists
        artists: list[dict[str, str]] = []
        after: str | None = None
        while True:
            page = call_spotify(lambda sp, after=after: sp.current_user_followed_artists(limit=50, after=after)) or {}
            artists_block = page.get("artists") or {}
            items = artists_block.get("items") or []
            for a in items:
                aid = a.get("id")
                aname = a.get("name")
                if aid and aname:
                    artists.append({"id": str(aid), "name": str(aname)})
            cursors = artists_block.get("cursors") or {}
            after = cursors.get("after")
            if not after:
                break

        if limit and limit > 0:
            artists = artists[:limit]

        total = len(artists)
        _job_set(
            job_id,
            total=total,
            processed=0,
            mapped=0,
            unavailable=0,
            added_to_apple=0,
            playlist_name=APPLE_FOLLOWED_ARTISTS_PLAYLIST_NAME,
        )

        # Ensure playlist exists
        preexisting = False
        apple_playlist: dict[str, Any] | None = None
        try:
            existing = apple_client.list_library_playlists()
            for p in existing:
                if str(p.get("name") or "").strip().lower() == APPLE_FOLLOWED_ARTISTS_PLAYLIST_NAME.lower():
                    apple_playlist = p
                    preexisting = True
                    break
        except Exception:
            apple_playlist = None

        if not apple_playlist:
            apple_playlist = apple_client.ensure_playlist(
                APPLE_FOLLOWED_ARTISTS_PLAYLIST_NAME,
                "Auto-generated fallback for Spotify followed artists",
            )

        apple_playlist_id = str(apple_playlist.get("id") or "")
        if not apple_playlist_id:
            _job_set(job_id, state="error", error="Failed to create or load Apple fallback playlist")
            return

        # Clear existing tracks if rebuild mode is enabled
        tracks_removed = 0
        if rebuild and preexisting:
            try:
                _log.info(f"[job {job_id}] Rebuild mode: clearing existing tracks from playlist")
                tracks_removed = apple_client.clear_playlist_tracks(apple_playlist_id)
                _log.info(f"[job {job_id}] Removed {tracks_removed} tracks from playlist")
            except Exception as e:
                _log.warning(f"[job {job_id}] Failed to clear playlist tracks: {e}")
                # Continue with sync even if clear fails

        processed = 0
        mapped = 0
        unavailable = 0
        apple_track_ids: list[str] = []

        for art in artists:
            sp_artist_id = art["id"]
            sp_artist_name = art["name"]
            try:
                representative: dict[str, Any] | None = None

                # Prefer Spotify top tracks for representative song
                try:
                    top = (
                        call_spotify(
                            lambda sp, sp_artist_id=sp_artist_id, market=market: sp.artist_top_tracks(
                                sp_artist_id, country=market
                            )
                        )
                        or {}
                    )
                    top_tracks = top.get("tracks") or []
                    for t in top_tracks:
                        tid = t.get("id")
                        tname = t.get("name")
                        if not tid or not tname:
                            continue
                        t_artists = t.get("artists") or []
                        t_artist_name = (
                            t_artists[0].get("name") if t_artists and isinstance(t_artists[0], dict) else sp_artist_name
                        )
                        t_duration_ms = t.get("duration_ms")
                        t_isrc = (t.get("external_ids") or {}).get("isrc")
                        representative = {
                            "id": str(tid),
                            "title": str(tname),
                            "artist": str(t_artist_name or sp_artist_name),
                            "duration": int(t_duration_ms / 1000) if t_duration_ms else None,
                            "isrc": t_isrc,
                        }
                        break
                except Exception:
                    representative = None

                if not representative:
                    unavailable += 1
                    continue

                apple_track: dict[str, Any] | None = None

                # ISRC-first match
                if representative.get("isrc"):
                    try:
                        apple_track = apple_client.search_by_isrc(representative["isrc"], storefront)
                    except Exception:
                        apple_track = None

                # Fuzzy fallback
                if not apple_track:
                    try:
                        candidates = apple_client.search_track(
                            title=representative["title"],
                            artist=representative["artist"],
                            duration_s=representative.get("duration"),
                            storefront=storefront,
                            limit=10,
                        )
                        ranked: list[dict[str, Any]] = []
                        for c in candidates:
                            score = _score_track_candidate(
                                representative["title"],
                                representative["artist"],
                                representative.get("isrc"),
                                representative.get("duration"),
                                {
                                    "id": c.get("id"),
                                    "title": c.get("name"),
                                    "artists": c.get("artists") or [],
                                    "duration": int(c["duration_ms"] / 1000) if c.get("duration_ms") else None,
                                    "isrc": c.get("isrc"),
                                },
                            )
                            c2 = dict(c)
                            c2["score"] = float(score)
                            ranked.append(c2)
                        ranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
                        top_match = ranked[0] if ranked else None
                        if top_match and (
                            float(top_match.get("score", 0.0)) >= 80.0 or float(top_match.get("score", 0.0)) >= 900.0
                        ):
                            apple_track = top_match
                    except Exception:
                        apple_track = None

                if apple_track and apple_track.get("id"):
                    apple_track_ids.append(str(apple_track["id"]))
                    mapped += 1
                else:
                    unavailable += 1
            except Exception:
                unavailable += 1
            finally:
                processed += 1
                if processed % 10 == 0 or processed == total:
                    _job_set(job_id, processed=processed, mapped=mapped, unavailable=unavailable)

        # De-duplicate while preserving order
        deduped_ids = list(dict.fromkeys(apple_track_ids))
        added_to_apple = 0
        if deduped_ids:
            try:
                result = apple_client.add_tracks_to_playlist(apple_playlist_id, deduped_ids)
                added_to_apple = int(result.succeeded)
            except Exception:
                added_to_apple = 0

        _job_set(
            job_id,
            state="done",
            finished_at=datetime.now(UTC).isoformat(),
            processed=processed,
            total=total,
            mapped=mapped,
            unavailable=unavailable,
            added_to_apple=added_to_apple,
            playlist_id=apple_playlist_id,
            playlist_name=APPLE_FOLLOWED_ARTISTS_PLAYLIST_NAME,
            created=(0 if preexisting else 1),
            updated=(1 if preexisting and added_to_apple > 0 else 0),
            rebuild=rebuild,
            tracks_removed=tracks_removed,
        )
    except Exception as e:
        _job_set(job_id, state="error", error=str(e))


@app.post("/sync/tracks/start")
async def start_sync_tracks(request: Request):
    limit_param = request.query_params.get("limit")
    try:
        limit = int(limit_param) if limit_param else 0
    except ValueError:
        limit = 0
    job_id = str(uuid.uuid4())
    _job_set(job_id, state="pending", limit=limit)
    threading.Thread(target=_run_sync_tracks_job, args=(job_id, limit), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/sync/tracks/status")
async def sync_tracks_status(job_id: str):
    with _jobs_lock:
        data = _jobs.get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(data)


@app.post("/sync/playlists/start")
async def start_sync_playlists(request: Request):
    limit_param = request.query_params.get("limit")
    try:
        limit = int(limit_param) if limit_param else 0
    except ValueError:
        limit = 0
    job_id = str(uuid.uuid4())
    _job_set(job_id, state="pending", limit=limit)
    threading.Thread(target=_run_sync_playlists_job, args=(job_id, limit), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/sync/playlists/status")
async def sync_playlists_status(job_id: str):
    with _jobs_lock:
        data = _jobs.get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(data)


@app.post("/sync/apple/likes/start")
async def start_sync_apple_likes(request: Request):
    """Start syncing Spotify liked tracks to Apple Music library."""
    limit_param = request.query_params.get("limit")
    try:
        limit = int(limit_param) if limit_param else 0
    except ValueError:
        limit = 0
    job_id = str(uuid.uuid4())
    _job_set(job_id, state="pending", limit=limit)
    threading.Thread(target=_run_sync_apple_likes_job, args=(job_id, limit), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/sync/apple/likes/status")
async def sync_apple_likes_status(job_id: str):
    """Get status of Apple Music likes sync job."""
    with _jobs_lock:
        data = _jobs.get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(data)


@app.post("/sync/apple/playlists/start")
async def start_sync_apple_playlists(request: Request):
    """Start syncing user-owned Spotify playlists to Apple Music."""
    limit_param = request.query_params.get("limit")
    try:
        limit = int(limit_param) if limit_param else 0
    except ValueError:
        limit = 0
    job_id = str(uuid.uuid4())
    _job_set(job_id, state="pending", limit=limit)
    threading.Thread(target=_run_sync_apple_playlists_job, args=(job_id, limit), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/sync/apple/playlists/status")
async def sync_apple_playlists_status(job_id: str):
    """Get status of Apple Music playlist sync job."""
    with _jobs_lock:
        data = _jobs.get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(data)


@app.post("/sync/apple/followed-artists-playlist/start")
async def start_sync_apple_followed_artists_playlist(request: Request):
    """Start creating/updating fallback Apple playlist for Spotify followed artists.

    Query parameters:
        limit: Optional limit on number of artists to process (0 = all)
        rebuild: If 'true', clear existing playlist tracks before repopulating (strict rebuild mode)
    """
    limit_param = request.query_params.get("limit")
    rebuild_param = request.query_params.get("rebuild", "").lower()
    try:
        limit = int(limit_param) if limit_param else 0
    except ValueError:
        limit = 0
    rebuild = rebuild_param in ("true", "1", "yes")
    job_id = str(uuid.uuid4())
    _job_set(job_id, state="pending", limit=limit, rebuild=rebuild)
    threading.Thread(
        target=_run_sync_apple_followed_artists_playlist_job,
        args=(job_id, limit, rebuild),
        daemon=True,
    ).start()
    return JSONResponse({"job_id": job_id})


@app.get("/sync/apple/followed-artists-playlist/status")
async def sync_apple_followed_artists_playlist_status(job_id: str):
    """Get status of fallback Apple followed-artists playlist sync job."""
    with _jobs_lock:
        data = _jobs.get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(data)


@app.get("/pending-tracks", response_class=HTMLResponse)
async def list_pending_tracks(request: Request):
    with SessionLocal() as db:
        items = get_pending_tracks(db)
    return templates.TemplateResponse("pending_tracks.html", {"request": request, "items": items})


@app.post("/resolve-track/{pending_id}")
async def resolve_track(
    pending_id: int,
    target_service: str = Form(...),
    target_id: str = Form(...),
    target_title: str = Form(...),
    target_artist: str = Form(""),
    target_artist_id: str = Form(""),
):
    """Resolve pending track match for TIDAL or Apple Music."""
    with SessionLocal() as db:
        items = {p["id"]: p for p in get_pending_tracks(db)}
        if pending_id not in items:
            raise HTTPException(status_code=404, detail="Pending item not found")
        p = items[pending_id]
        spid = p["spotify_id"]
        sptitle = p["spotify_title"]
        spart = p["spotify_artist"]
        isrc = p.get("isrc")

        # Dispatch based on target service
        if target_service == "tidal":
            # TIDAL resolution: add to favorites
            sess = get_tidal_session()
            favs = _tidal_favorite_tracks_set(sess)
            if target_id not in favs:
                try:
                    sess.user.favorites.add_track(int(target_id))
                except Exception:
                    pass
        elif target_service == "apple":
            # Apple Music resolution: add to library
            try:
                apple_client = get_apple_client()
                apple_client.add_tracks_to_library([target_id])
            except Exception:
                pass
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported service: {target_service}")

        # Preserve spotify artist id if known
        existing_map = (
            db.query(TrackMap).filter(TrackMap.spotify_id == spid, TrackMap.target_service == target_service).first()
        )
        s_artist_id = getattr(existing_map, "spotify_artist_id", None) if existing_map else None
        s_dur = getattr(existing_map, "spotify_duration", None) if existing_map else None
        t_dur = getattr(existing_map, "target_duration", None) if existing_map else None
        t_artist_id = target_artist_id or None

        upsert_track_map(
            db,
            spid,
            sptitle,
            spart,
            s_artist_id,
            target_id,
            target_title,
            target_artist,
            t_artist_id,
            isrc,
            s_dur,
            t_dur,
            0.0,
            True,
            target_service=target_service,
        )
        add_track_sync_event(
            db,
            spid,
            sptitle,
            spart,
            target_id,
            target_title,
            target_artist,
            isrc,
            "manual",
            target_service=target_service,
        )
        delete_pending_track(db, pending_id)
    return JSONResponse({"status": "ok"})


@app.get("/backup/artists")
async def backup_artists():
    with SessionLocal() as db:
        data = export_artists(db)
    return JSONResponse(data)


@app.get("/backup/tracks")
async def backup_tracks():
    with SessionLocal() as db:
        data = export_tracks(db)
    return JSONResponse(data)


@app.get("/backup/playlists")
async def backup_playlists():
    with SessionLocal() as db:
        data = export_playlists(db)
    return JSONResponse(data)


# ---- Database backup/restore ----


@app.get("/backup/db")
async def backup_db():
    try:
        fh = open(DB_PATH, "rb")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="database not found")
    resp = StreamingResponse(fh, media_type="application/octet-stream")
    resp.headers["Content-Disposition"] = "attachment; filename=musicsync.db"
    return resp


@app.post("/restore/db")
async def restore_db(file: UploadFile = File(...)):
    import shutil
    import time

    # Read small header to sanity check
    head = await file.read(16)
    await file.seek(0)
    if head[:15] != b"SQLite format 3":
        # Allow anyway but warn
        _log.warning("Uploaded restore file does not look like SQLite. Proceeding anyway.")
    # Backup existing DB if present
    try:
        engine.dispose()
    except Exception:
        pass
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_path = f"{DB_PATH}.bak.{ts}" if os.path.exists(DB_PATH) else None
    try:
        if backup_path:
            shutil.copy2(DB_PATH, backup_path)
        # Replace DB
        with open(DB_PATH, "wb") as out:
            shutil.copyfileobj(file.file, out)
        # Re-init in case migrations are needed
        init_db()
        return JSONResponse({"status": "ok", "backup": backup_path})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"restore failed: {e}")


# ---- Multi-format exports ----


def _to_csv(rows: list[dict[str, Any]], headers: list[str]) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) for k in headers})
    return buf.getvalue()


def _to_markdown(rows: list[dict[str, Any]], headers: list[str]) -> str:
    # Simple GitHub-flavored table
    line1 = "| " + " | ".join(headers) + " |"
    line2 = "| " + " | ".join(["---"] * len(headers)) + " |"
    lines = [line1, line2]
    for r in rows:
        vals = [str(r.get(h, "") or "") for h in headers]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


@app.get("/export/{kind}")
async def export_any(kind: str, format: str = "json", download: bool = False):
    kind = kind.lower()
    format = format.lower()
    if kind not in ("artists", "tracks", "playlists"):
        raise HTTPException(status_code=400, detail="invalid kind")
    with SessionLocal() as db:
        if kind == "artists":
            rows = export_artists(db)
            headers = [
                "spotify_id",
                "spotify_name",
                "tidal_id",
                "tidal_name",
                "genres",
                "confidence",
                "resolved",
                "last_synced_at",
            ]
        elif kind == "tracks":
            rows = export_tracks(db)
            headers = [
                "spotify_id",
                "spotify_title",
                "spotify_artist",
                "spotify_artist_id",
                "tidal_id",
                "tidal_title",
                "tidal_artist",
                "tidal_artist_id",
                "isrc",
                "spotify_duration",
                "tidal_duration",
                "confidence",
                "resolved",
                "last_synced_at",
            ]
        else:
            rows = export_playlists(db)
            headers = ["spotify_id", "spotify_name", "tidal_id", "tidal_name", "last_synced_at"]

    if format == "json":
        return JSONResponse(rows)
    elif format in ("csv", "tsv"):
        text = _to_csv(rows, headers)
        media = "text/csv"
        filename = f"{kind}.csv"
        resp = PlainTextResponse(text, media_type=media)
        if download:
            resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
        return resp
    elif format in ("md", "markdown"):
        text = _to_markdown(rows, headers)
        resp = PlainTextResponse(text, media_type="text/markdown")
        if download:
            resp.headers["Content-Disposition"] = f"attachment; filename={kind}.md"
        return resp
    else:
        raise HTTPException(status_code=400, detail="invalid format; use json,csv,md")


# ---- Library views (synced items) ----


@app.get("/library/artists", response_class=HTMLResponse)
async def library_artists(request: Request):
    # Parse query params
    q = request.query_params.get("q") or None
    genre = request.query_params.get("genre") or None
    sort = request.query_params.get("sort") or "last_synced_at"
    order = request.query_params.get("order") or "desc"
    try:
        page = int(request.query_params.get("page") or 1)
    except ValueError:
        page = 1
    ps_raw = request.query_params.get("page_size")
    if ps_raw is None:
        page_size = 25
    elif ps_raw == "all":
        page_size = 0
    else:
        try:
            page_size = int(ps_raw)
        except ValueError:
            page_size = 25

    with SessionLocal() as db:
        items, total = list_synced_artists(
            db,
            search=q,
            genre=genre,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
        # Compute top genres (respecting search filter only)
        top_genres = list_genre_counts(db, search=q)[:20]
    pages = (total + page_size - 1) // page_size if page_size else 1
    if page < 1:
        page = 1
    if pages and page > pages:
        page = pages
    return templates.TemplateResponse(
        "library_artists.html",
        {
            "request": request,
            "items": items,
            "count": len(items),
            "total": total,
            "page": page,
            "pages": pages,
            "page_size": page_size,
            "sort": sort,
            "order": order,
            "q": q or "",
            "genre": genre or "",
            "genre_counts": top_genres,
        },
    )


# ---- Refresh genres job (Spotify) ----


def _run_refresh_genres_job(job_id: str, missing_only: bool = True) -> None:
    _log.info(f"[job {job_id}] Refresh genres: start (missing_only={missing_only})")
    _job_set(job_id, state="running", started_at=datetime.now(UTC).isoformat())
    try:
        try:
            get_spotify_client()
        except Exception as e:
            _job_set(job_id, state="error", error=str(e))
            return

        with SessionLocal() as db:
            rows = db.query(ArtistMap).all()
            # Build list of artist ids to refresh
            target_ids: list[str] = []
            for r in rows:
                try:
                    current = getattr(r, "genres_json", None) or "[]"
                    parsed = []
                    try:
                        parsed = json.loads(str(current))
                    except Exception:
                        parsed = []
                    if missing_only:
                        if not parsed:
                            target_ids.append(str(r.spotify_id))
                    else:
                        target_ids.append(str(r.spotify_id))
                except Exception:
                    continue

            total = len(target_ids)
            processed = 0
            updated = 0
            skipped = 0
            _job_set(job_id, total=total, processed=processed, updated=updated, skipped=skipped)

            i = 0
            while i < len(target_ids):
                chunk = target_ids[i : i + 50]
                try:
                    res = call_spotify(lambda sp: sp.artists(chunk)) or {}
                    arts = res.get("artists") or []
                except Exception:
                    arts = []
                # Map id->genres
                id_to_genres: dict[str, list[str]] = {}
                for ar in arts:
                    gid = str(ar.get("id")) if ar.get("id") else None
                    gens = ar.get("genres") or []
                    if gid:
                        try:
                            id_to_genres[gid] = [str(g) for g in gens if isinstance(g, str)]
                        except Exception:
                            id_to_genres[gid] = []
                # Apply updates in a batch
                for gid in chunk:
                    gens = id_to_genres.get(gid)
                    if gens is None:
                        # No data returned; count as skipped
                        skipped += 1
                        processed += 1
                        continue
                    m = db.get(ArtistMap, gid)
                    if not m:
                        skipped += 1
                        processed += 1
                        continue
                    try:
                        m.genres_json = json.dumps(gens)
                        updated += 1
                    except Exception:
                        skipped += 1
                    processed += 1
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                _job_set(job_id, processed=processed, updated=updated, skipped=skipped)
                i += 50

        _job_set(
            job_id,
            state="done",
            finished_at=datetime.now(UTC).isoformat(),
            processed=processed,
            updated=updated,
            skipped=skipped,
        )
        _log.info(f"[job {job_id}] Refresh genres done. total={total} updated={updated} skipped={skipped}")
    except Exception as e:
        _job_set(job_id, state="error", error=str(e))


@app.post("/genres/refresh/start")
async def start_refresh_genres(request: Request):
    missing_only_raw = request.query_params.get("missing_only")
    missing_only = True
    if missing_only_raw is not None:
        missing_only = missing_only_raw not in ("0", "false", "False")
    job_id = str(uuid.uuid4())
    _job_set(job_id, state="pending", missing_only=missing_only)
    threading.Thread(target=_run_refresh_genres_job, args=(job_id, missing_only), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/genres/refresh/status")
async def refresh_genres_status(job_id: str):
    with _jobs_lock:
        data = _jobs.get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(data)


@app.get("/library/tracks", response_class=HTMLResponse)
async def library_tracks(request: Request):
    q = request.query_params.get("q") or None
    sort = request.query_params.get("sort") or "last_synced_at"
    order = request.query_params.get("order") or "desc"
    try:
        page = int(request.query_params.get("page") or 1)
    except ValueError:
        page = 1
    ps_raw = request.query_params.get("page_size")
    if ps_raw is None:
        page_size = 25
    elif ps_raw == "all":
        page_size = 0
    else:
        try:
            page_size = int(ps_raw)
        except ValueError:
            page_size = 25
    with SessionLocal() as db:
        items, total = list_synced_tracks(
            db,
            search=q,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
    pages = (total + page_size - 1) // page_size if page_size else 1
    if page < 1:
        page = 1
    if pages and page > pages:
        page = pages
    return templates.TemplateResponse(
        "library_tracks.html",
        {
            "request": request,
            "items": items,
            "count": len(items),
            "total": total,
            "page": page,
            "pages": pages,
            "page_size": page_size,
            "sort": sort,
            "order": order,
            "q": q or "",
        },
    )


@app.get("/library/playlists", response_class=HTMLResponse)
async def library_playlists(request: Request):
    q = request.query_params.get("q") or None
    sort = request.query_params.get("sort") or "last_synced_at"
    order = request.query_params.get("order") or "desc"
    try:
        page = int(request.query_params.get("page") or 1)
    except ValueError:
        page = 1
    ps_raw = request.query_params.get("page_size")
    if ps_raw is None:
        page_size = 25
    elif ps_raw == "all":
        page_size = 0
    else:
        try:
            page_size = int(ps_raw)
        except ValueError:
            page_size = 25

    with SessionLocal() as db:
        # For sort by computed stats (tracks or duration), fetch all, sort in memory, then paginate
        if sort in ("tracks", "duration"):
            all_items, total = list_synced_playlists(
                db,
                search=q,
                sort="last_synced_at",  # base sort doesn't matter; we'll sort in memory
                order="desc",
                page=1,
                page_size=0,
            )
            # attach stats for all to enable proper sorting
            for it in all_items:
                try:
                    it["stats"] = get_playlist_stats(db, it["spotify_id"])
                except Exception:
                    it["stats"] = {"count": 0, "total_seconds": 0}
            reverse = order.lower() == "desc"
            if sort == "tracks":
                all_items.sort(key=lambda x: int((x.get("stats") or {}).get("count", 0)), reverse=reverse)
            else:  # duration
                all_items.sort(key=lambda x: int((x.get("stats") or {}).get("total_seconds", 0)), reverse=reverse)
            # paginate
            total = len(all_items)
            if page_size and page_size > 0:
                pages = (total + page_size - 1) // page_size
                if page < 1:
                    page = 1
                if pages and page > pages:
                    page = pages
                start = (page - 1) * page_size
                end = start + page_size
                items = all_items[start:end]
            else:
                pages = 1
                items = all_items
        else:
            items, total = list_synced_playlists(
                db,
                search=q,
                sort=sort,
                order=order,
                page=page,
                page_size=page_size,
            )
            # Attach stats for visible items only
            for it in items:
                try:
                    it["stats"] = get_playlist_stats(db, it["spotify_id"])
                except Exception:
                    it["stats"] = {"count": 0, "total_seconds": 0}
            pages = (total + page_size - 1) // page_size if page_size else 1
            if page < 1:
                page = 1
            if pages and page > pages:
                page = pages
    return templates.TemplateResponse(
        "library_playlists.html",
        {
            "request": request,
            "items": items,
            "count": len(items),
            "total": total,
            "page": page,
            "pages": pages,
            "page_size": page_size,
            "sort": sort,
            "order": order,
            "q": q or "",
        },
    )


@app.get("/library/playlists/{spotify_id}", response_class=HTMLResponse)
async def library_playlist_detail(spotify_id: str, request: Request):
    q = request.query_params.get("q") or None
    sort = request.query_params.get("sort") or "position"
    order = request.query_params.get("order") or "asc"
    try:
        page = int(request.query_params.get("page") or 1)
    except ValueError:
        page = 1
    ps_raw = request.query_params.get("page_size")
    if ps_raw is None:
        page_size = 25
    elif ps_raw == "all":
        page_size = 0
    else:
        try:
            page_size = int(ps_raw)
        except ValueError:
            page_size = 25

    with SessionLocal() as db:
        pl_tidal = get_playlist_map(db, spotify_id, target_service="tidal")
        pl_apple = get_playlist_map(db, spotify_id, target_service="apple")
        # Use whichever exists, prefer TIDAL for backwards compatibility
        pl = pl_tidal or pl_apple
        if not pl:
            raise HTTPException(status_code=404, detail="Playlist not found")
        # Merge both service mappings into one dict for template
        playlist_info = dict(pl)
        if pl_apple:
            playlist_info["apple_id"] = pl_apple.get("target_id")
            playlist_info["apple_name"] = pl_apple.get("target_name")
        items, total = list_playlist_tracks(
            db,
            spotify_id,
            search=q,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
        stats = get_playlist_stats(db, spotify_id)
    pages = (total + page_size - 1) // page_size if page_size else 1
    if page < 1:
        page = 1
    if pages and page > pages:
        page = pages
    return templates.TemplateResponse(
        "library_playlist_detail.html",
        {
            "request": request,
            "playlist": playlist_info,
            "items": items,
            "count": len(items),
            "total": total,
            "stats": stats,
            "page": page,
            "pages": pages,
            "page_size": page_size,
            "sort": sort,
            "order": order,
            "q": q or "",
        },
    )


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    import platform
    import sys

    # Connection status
    spotify_ok = False
    try:
        _ = get_spotify_client()
        spotify_ok = True
    except Exception:
        spotify_ok = False

    try:
        tidal_ok = tidal_logged_in()
    except Exception:
        tidal_ok = False

    # Basic env/runtime info
    pyver = sys.version.splitlines()[0]
    plat = platform.platform()

    # DB info and counts
    with SessionLocal() as db:
        try:
            artists, _ = list_synced_artists(db, page_size=0)
            tracks, _ = list_synced_tracks(db, page_size=0)
            playlists, _ = list_synced_playlists(db, page_size=0)
        except Exception:
            artists, tracks, playlists = [], [], []
        try:
            pend_arts = get_pending(db)
        except Exception:
            pend_arts = []
        try:
            pend_trks = get_pending_tracks(db)
        except Exception:
            pend_trks = []

    try:
        db_size = os.path.getsize(DB_PATH)
    except Exception:
        db_size = 0

    # Summarize jobs (best-effort)
    with _jobs_lock:
        jobs_snapshot = [{"id": jid, **data} for jid, data in _jobs.items()]

    env_flags = {
        "MUSICSYNC_LOOP": os.environ.get("MUSICSYNC_LOOP", "asyncio"),
        "MUSICSYNC_HTTP": os.environ.get("MUSICSYNC_HTTP", "h11"),
        "MUSICSYNC_RELOAD": os.environ.get("MUSICSYNC_RELOAD", "1"),
        "MUSICSYNC_ACCESS_LOG": os.environ.get("MUSICSYNC_ACCESS_LOG", "1"),
    }

    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "python_version": pyver,
            "platform": plat,
            "db_path": DB_PATH,
            "db_size": db_size,
            "spotify_ok": spotify_ok,
            "tidal_ok": tidal_ok,
            "artists_count": len(artists),
            "tracks_count": len(tracks),
            "playlists_count": len(playlists),
            "pending_artists_count": len(pend_arts),
            "pending_tracks_count": len(pend_trks),
            "jobs": jobs_snapshot,
            "env_flags": env_flags,
            "now": datetime.now(UTC).isoformat(),
        },
    )


# ---- CLI entry point for uv/pipx installed tool ----


def run() -> None:
    """Start the MusicSync FastAPI server.

    Environment variables:
    - MUSICSYNC_HOST (default 127.0.0.1)
    - MUSICSYNC_PORT (default 8000)
    - MUSICSYNC_RELOAD (default 1 if in development)
    - MUSICSYNC_LOOP (default 'asyncio'; set 'uvloop' to enable uvloop if installed)
    - MUSICSYNC_HTTP (default 'h11'; set 'httptools' to enable httptools if installed)
    - MUSICSYNC_ACCESS_LOG (default 1; set 0 to disable access logs)
    """
    import uvicorn

    host = os.environ.get("MUSICSYNC_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("MUSICSYNC_PORT", "8000"))
    except ValueError:
        port = 8000
    reload_flag = os.environ.get("MUSICSYNC_RELOAD", "1") not in ("0", "false", "False")
    loop_impl = os.environ.get("MUSICSYNC_LOOP", "asyncio")
    http_impl = os.environ.get("MUSICSYNC_HTTP", "h11")
    access_log = os.environ.get("MUSICSYNC_ACCESS_LOG", "1") not in ("0", "false", "False")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload_flag,
        loop=loop_impl,
        http=http_impl,
        access_log=access_log,
    )
