from __future__ import annotations

from typing import List, Dict, Optional

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from .storage import init_db, SessionLocal, add_pending_resolution, delete_pending, get_pending, upsert_artist_map
from .spotify_client import get_authorize_url, exchange_code_for_token, get_spotify_client
from .tidal_client import (
    get_session as get_tidal_session,
    get_login_url_and_worker,
    is_logged_in as tidal_logged_in,
    get_login_state as tidal_login_state,
)
from .matching import score_artist_match

app = FastAPI(title="MusicSync")
init_db()

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


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
    try:
        tidal_ok = tidal_logged_in()
    except Exception:
        tidal_ok = False

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "spotify_ok": spotify_ok,
            "tidal_ok": tidal_ok,
        },
    )


@app.get("/auth/spotify/login")
async def spotify_login():
    url = get_authorize_url()
    return RedirectResponse(url)


@app.get("/auth/spotify/callback")
async def spotify_callback(code: Optional[str] = None, error: Optional[str] = None):
    if error:
        raise HTTPException(status_code=400, detail=error)
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    exchange_code_for_token(code)
    return RedirectResponse("/")


@app.get("/auth/tidal/start")
async def tidal_start():
    data = get_login_url_and_worker()
    # Return the details needed for UI
    return JSONResponse({
        "verification_uri_complete": data.get("verification_uri_complete"),
        "verification_uri": data.get("verification_uri"),
        "user_code": data.get("user_code"),
        "expires_in": data.get("expires_in"),
        "pending": data.get("pending"),
    })


@app.get("/auth/tidal/status")
async def tidal_status():
    state = tidal_login_state()
    return JSONResponse({
        "logged_in": tidal_logged_in(),
        "pending": state.get("pending", False),
        "connected": state.get("connected", False),
        "error": state.get("error"),
    })


# --- Sync Followed Artists ---

async def fetch_spotify_followed_artists() -> List[Dict[str, str]]:
    sp = get_spotify_client()
    artists: List[Dict[str, str]] = []
    after: Optional[str] = None
    while True:
        page = sp.current_user_followed_artists(limit=50, after=after) or {}
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
    return {a.id for a in favs}


def _tidal_search_artists(sess, name: str) -> List[Dict[str, str]]:
    res = sess.search("artists", name)
    out: List[Dict[str, str]] = []
    try:
        for a in res.artists:
            out.append({"id": str(a.id), "name": a.name})
    except Exception:
        pass
    return out


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
                add_pending_resolution(db, sp_id, name, ranked[:10])
                upsert_artist_map(db, sp_id, name, None, None, float(ranked[0]["score"]) if ranked else 0.0, False)
            pending += 1

    return JSONResponse({"artists": len(artists), "auto_matched": auto_matched, "pending": pending})


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
        delete_pending(db, pending_id)
    return JSONResponse({"status": "ok"})


@app.get("/healthz")
async def healthz():
    return {"ok": True}
