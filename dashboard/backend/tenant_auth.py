"""
Dashboard auth helpers (Step 2): resolve optional store session from headers.
"""
from __future__ import annotations

from typing import Optional, Tuple

from fastapi import Header, HTTPException

from dbconn import get_session


def extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


def resolve_auth_context(
    authorization: Optional[str] = None,
    x_store_token: Optional[str] = None,
):
    """Return AuthContext or None. Does not raise."""
    raw = x_store_token or extract_bearer(authorization)
    if not raw:
        return None
    from db.store_auth import authenticate_session

    session = get_session()
    try:
        return authenticate_session(session, raw)
    finally:
        session.close()


def require_store_auth(
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    """FastAPI dependency: require a valid store session."""
    raw = x_store_token or extract_bearer(authorization)
    if not raw:
        raise HTTPException(status_code=401, detail="Missing store session (Authorization Bearer or X-Store-Token)")

    from db.store_auth import authenticate_session

    session = get_session()
    try:
        ctx = authenticate_session(session, raw)
        if not ctx:
            raise HTTPException(status_code=401, detail="Invalid or expired store session")
        # Detach key fields so caller can use after session closes
        return {
            "user_id": ctx.user.user_id,
            "username": ctx.user.username,
            "store_id": ctx.store.store_id,
            "store_name": ctx.store.store_name,
            "subscription_id": ctx.subscription.subscription_id if ctx.subscription else None,
            "plan": ctx.subscription.plan if ctx.subscription else None,
            "raw_token": raw,
        }
    finally:
        session.close()


def scope_store_id(
    requested_store_id: Optional[str],
    authorization: Optional[str] = None,
    x_store_token: Optional[str] = None,
) -> Tuple[Optional[str], Optional[dict]]:
    """
    If a store session is present, force that store_id (isolation).
    Cross-store requests → 403. Without auth, return requested_store_id unchanged (demo mode).
    """
    raw = x_store_token or extract_bearer(authorization)
    if not raw:
        return requested_store_id, None

    from db.store_auth import authenticate_session

    session = get_session()
    try:
        ctx = authenticate_session(session, raw)
        if not ctx:
            raise HTTPException(status_code=401, detail="Invalid or expired store session")
        auth = {
            "user_id": ctx.user.user_id,
            "username": ctx.user.username,
            "store_id": ctx.store.store_id,
            "store_name": ctx.store.store_name,
            "plan": ctx.subscription.plan if ctx.subscription else None,
        }
        if requested_store_id and requested_store_id not in ("all", ctx.store.store_id):
            raise HTTPException(
                status_code=403,
                detail="Cannot access another store's data",
            )
        return ctx.store.store_id, auth
    finally:
        session.close()
