from __future__ import annotations

import threading
from typing import Dict, Optional, Any

import tidalapi

from .storage import load_token, save_token, SessionLocal


_session_lock = threading.Lock()
_cached_session: Optional[Any] = None


def _create_session() -> Any:
    global _cached_session
    with _session_lock:
        if _cached_session and _cached_session.check_login():
            return _cached_session

        sess: Any = tidalapi.Session()  # type: ignore[attr-defined]
        # Try to load an existing session from DB
        with SessionLocal() as db:
            tok = load_token(db, "tidal")
        if tok and all(k in tok for k in ("session_id", "country_code", "user_id")):
            try:
                sess.load_session(tok["session_id"], tok["country_code"], tok["user_id"])  # type: ignore[arg-type]
                if sess.check_login():
                    _cached_session = sess
                    return sess
            except Exception:
                pass
        _cached_session = sess
        return sess


def is_logged_in() -> bool:
    sess: Any = _create_session()
    return bool(sess.check_login())  # type: ignore[attr-defined]


def get_login_url_and_worker() -> Dict[str, object]:
    """
    Start TIDAL device/remote login and return details needed for UI, then
    wait for completion in a background thread and persist the session.
    Returns keys: verification_uri_complete, verification_uri (optional), user_code (optional), expires_in.
    """
    sess = _create_session()

    login, future = sess.login_oauth()  # type: ignore[attr-defined]

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
                save_token(
                    db,
                    "tidal",
                    {
                        "session_id": sess.session_id,  # type: ignore[attr-defined]
                        "country_code": sess.country_code,  # type: ignore[attr-defined]
                        "user_id": getattr(sess.user, "id", None),  # type: ignore[attr-defined]
                    },
                )

    threading.Thread(target=waiter, daemon=True).start()

    return {
        "verification_uri_complete": verification_uri_complete,
        "verification_uri": verification_uri,
        "user_code": user_code,
        "expires_in": expires_in,
    }


def get_session() -> Any:
    sess: Any = _create_session()
    if not sess.check_login():  # type: ignore[attr-defined]
        raise RuntimeError("TIDAL not authorized. Visit /auth/tidal/start and follow instructions.")
    return sess
