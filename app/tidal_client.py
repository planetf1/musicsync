from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import tidalapi

from .storage import SessionLocal, load_token, save_token

_session_lock = threading.Lock()
_cached_session: Any | None = None
_login_state: dict[str, Any] = {"pending": False, "connected": False, "error": None}
_login_in_progress: bool = False


def _create_session() -> Any:
    global _cached_session
    with _session_lock:
        if _cached_session:
            # If already cached, try to reuse it. If not logged in and no login is in progress,
            # attempt to load credentials into the same object to maintain references.
            try:
                if _cached_session.check_login():  # type: ignore[attr-defined]
                    return _cached_session
            except Exception:
                pass

            if not _login_in_progress:
                with SessionLocal() as db:
                    tok = load_token(db, "tidal")
                if tok:
                    try:
                        if all(k in tok for k in ("token_type", "access_token", "expiry_time")):
                            # OAuth tokens
                            token_type = str(tok.get("token_type") or "")
                            access_token = str(tok.get("access_token") or "")
                            refresh_token = tok.get("refresh_token")
                            expiry_val = tok.get("expiry_time")
                            expiry_ts = int(expiry_val) if expiry_val is not None else 0
                            expiry_time = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
                            _cached_session.load_oauth_session(
                                token_type,
                                access_token,
                                refresh_token,
                                expiry_time,
                            )  # type: ignore[attr-defined]
                            if _cached_session.check_login():  # type: ignore[attr-defined]
                                return _cached_session
                        elif all(k in tok for k in ("session_id", "country_code", "user_id")):
                            # Legacy session method (fallback)
                            _cached_session.load_session(tok["session_id"], tok["country_code"], tok["user_id"])  # type: ignore[arg-type]
                            if _cached_session.check_login():  # type: ignore[attr-defined]
                                return _cached_session
                    except Exception:
                        pass
            return _cached_session

        sess: Any = tidalapi.Session()  # type: ignore[attr-defined]
        # Try to load an existing session from DB
        with SessionLocal() as db:
            tok = load_token(db, "tidal")
        if tok:
            try:
                if all(k in tok for k in ("token_type", "access_token", "expiry_time")):
                    token_type = str(tok.get("token_type") or "")
                    access_token = str(tok.get("access_token") or "")
                    refresh_token = tok.get("refresh_token")
                    expiry_val = tok.get("expiry_time")
                    expiry_ts = int(expiry_val) if expiry_val is not None else 0
                    expiry_time = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
                    sess.load_oauth_session(
                        token_type,
                        access_token,
                        refresh_token,
                        expiry_time,
                    )  # type: ignore[attr-defined]
                    if sess.check_login():  # type: ignore[attr-defined]
                        _cached_session = sess
                        return sess
                elif all(k in tok for k in ("session_id", "country_code", "user_id")):
                    sess.load_session(tok["session_id"], tok["country_code"], tok["user_id"])  # type: ignore[arg-type]
                    if sess.check_login():  # type: ignore[attr-defined]
                        _cached_session = sess
                        return sess
            except Exception:
                pass
        _cached_session = sess
        return sess


def is_logged_in() -> bool:
    if _login_state.get("connected"):
        return True
    sess: Any = _create_session()
    try:
        return bool(sess.check_login())  # type: ignore[attr-defined]
    except Exception:
        return False


def get_login_url_and_worker() -> dict[str, object]:
    """
    Start TIDAL device/remote login and return details needed for UI, then
    wait for completion in a background thread and persist the session.
    Returns keys: verification_uri_complete, verification_uri (optional), user_code (optional), expires_in.
    """
    global _login_state, _login_in_progress, _cached_session
    sess = _create_session()

    if _login_in_progress:
        # A login is already in progress; avoid starting another
        return {
            "error": "A TIDAL login is already pending. Please finish in the browser and try again.",
            "pending": True,
        }

    login, future = sess.login_oauth()  # type: ignore[attr-defined]
    _login_state = {"pending": True, "connected": False, "error": None}
    _login_in_progress = True

    # Extract fields defensively (attributes vary by version)
    verification_uri_complete = getattr(login, "verification_uri_complete", None)
    verification_uri = getattr(login, "verification_uri", None)
    user_code = getattr(login, "user_code", None) or getattr(login, "device_code", None)
    expires_in = getattr(login, "expires_in", None)

    def waiter():
        ok = False
        try:
            future.result()  # blocks until login completes or expires
            ok = sess.check_login()  # type: ignore[attr-defined]
        except Exception:
            ok = False
        if ok:
            with SessionLocal() as db:
                # Persist OAuth tokens for future launches
                # Normalize expiry_time to epoch seconds for JSON serialization
                exp = getattr(sess, "expiry_time", None)
                if isinstance(exp, datetime):
                    expiry_ts = int(exp.timestamp())
                else:
                    try:
                        expiry_ts = int(exp) if exp is not None else 0
                    except Exception:
                        expiry_ts = 0
                save_token(
                    db,
                    "tidal",
                    {
                        "token_type": getattr(sess, "token_type", None),  # type: ignore[attr-defined]
                        "access_token": getattr(sess, "access_token", None),  # type: ignore[attr-defined]
                        "refresh_token": getattr(sess, "refresh_token", None),  # type: ignore[attr-defined]
                        "expiry_time": expiry_ts,
                    },
                )
            _login_state.update({"pending": False, "connected": True, "error": None})
        else:
            _login_state.update(
                {"pending": False, "connected": False, "error": "Authorization not confirmed or expired."}
            )
        # No matter what, login attempt is finished
        global _login_in_progress
        _login_in_progress = False

    threading.Thread(target=waiter, daemon=True).start()

    return {
        "verification_uri_complete": verification_uri_complete,
        "verification_uri": verification_uri,
        "user_code": user_code,
        "expires_in": expires_in,
        "pending": _login_state["pending"],
    }


def get_session() -> Any:
    sess: Any = _create_session()
    if not sess.check_login():  # type: ignore[attr-defined]
        raise RuntimeError("TIDAL not authorized. Visit /auth/tidal/start and follow instructions.")
    return sess


def get_login_state() -> dict[str, Any]:
    """Return current login flow state for diagnostics/UI."""
    # Return a shallow copy to prevent external mutation
    return dict(_login_state)
