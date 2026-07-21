"""
Store dashboard login + session tokens (Step 2).

Passwords: PBKDF2-HMAC-SHA256 (stdlib).

Session tokens are HMAC-signed so they survive Vercel cold starts that
reset the ephemeral SQLite copy under /tmp. A StoreSession row is still
written when possible (local revoke / audit); authentication does not
require that row to still exist.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from db.models import Store, StoreSession, StoreUser, Subscription, gen_uuid

SESSION_PREFIX = "st_"
SIGNED_PREFIX = "st_s."
SESSION_TTL_HOURS = 72
PBKDF2_ITERATIONS = 120_000
# Demo-stable default so every serverless instance can verify the same tokens.
# Override with LEDGER_SESSION_SECRET in production.
_DEFAULT_SESSION_SECRET = "ledger-demo-session-secret-v1"


@dataclass
class AuthContext:
    user: StoreUser
    store: Store
    subscription: Optional[Subscription]
    session: Optional[StoreSession]
    raw_token: Optional[str] = None  # only set on login/create


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Return `salt$hexdigest` suitable for StoreUser.password_hash."""
    if not password or len(password) < 6:
        raise ValueError("password must be at least 6 characters")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    if not password or not stored or "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt=salt), stored)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _session_secret() -> bytes:
    secret = (os.environ.get("LEDGER_SESSION_SECRET") or "").strip() or _DEFAULT_SESSION_SECRET
    return secret.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def mint_signed_token(
    *,
    user_id: str,
    session_id: str,
    expires_at: datetime,
) -> str:
    """Build `st_s.<payload>.<sig>` — verifiable without a DB session row."""
    payload = {
        "u": user_id,
        "j": session_id,
        "e": int(_aware_utc(expires_at).timestamp()),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url_encode(
        hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{SIGNED_PREFIX}{body}.{sig}"


def parse_signed_token(raw_token: str) -> Optional[dict]:
    """Return payload dict if signature + expiry are valid, else None."""
    if not raw_token or not raw_token.startswith(SIGNED_PREFIX):
        return None
    try:
        rest = raw_token[len(SIGNED_PREFIX) :]
        body, sig = rest.rsplit(".", 1)
    except ValueError:
        return None
    expected = _b64url_encode(
        hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    try:
        exp = int(payload.get("e", 0))
        user_id = str(payload.get("u") or "")
        session_id = str(payload.get("j") or "")
    except (TypeError, ValueError):
        return None
    if not user_id or not session_id:
        return None
    if exp < int(time.time()):
        return None
    return {"user_id": user_id, "session_id": session_id, "exp": exp}


def create_store_user(
    session,
    *,
    store_id: str,
    username: str,
    password: str,
) -> StoreUser:
    username = (username or "").strip().lower()
    if not username:
        raise ValueError("username is required")
    store = session.query(Store).filter_by(store_id=store_id).first()
    if not store:
        raise ValueError(f"unknown store_id: {store_id}")
    existing = session.query(StoreUser).filter_by(username=username).first()
    if existing:
        raise ValueError(f"username already taken: {username}")

    user = StoreUser(
        user_id=gen_uuid(),
        store_id=store_id,
        username=username,
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def set_store_password(
    session,
    *,
    store_id: Optional[str] = None,
    store_name: Optional[str] = None,
    username: str,
    password: str,
) -> StoreUser:
    """Create or update a store login. Creates the store if store_name is new."""
    from db.api_keys import _resolve_store

    store = _resolve_store(session, store_id=store_id, store_name=store_name)
    username = (username or "").strip().lower()
    user = session.query(StoreUser).filter_by(username=username).first()
    if user:
        if user.store_id != store.store_id:
            raise ValueError("username belongs to a different store")
        user.password_hash = hash_password(password)
        user.is_active = True
    else:
        user = create_store_user(
            session, store_id=store.store_id, username=username, password=password
        )
    # Ensure subscription exists so report gating works
    sub = session.query(Subscription).filter_by(store_id=store.store_id).first()
    if not sub:
        from db.plans import PLAN_BASIC, apply_plan_to_subscription, get_plan, seed_subscription_plans

        seed_subscription_plans(session)
        plan = get_plan(session, PLAN_BASIC)
        sub = Subscription(subscription_id=gen_uuid(), store_id=store.store_id)
        if plan:
            apply_plan_to_subscription(sub, plan)
        else:
            sub.plan = PLAN_BASIC
        session.add(sub)
    session.flush()
    return user


def _auth_from_user(session, user: StoreUser, sess: Optional[StoreSession] = None) -> Optional[AuthContext]:
    if not user or not user.is_active:
        return None
    store = session.query(Store).filter_by(store_id=user.store_id).first()
    if not store:
        return None
    sub = session.query(Subscription).filter_by(store_id=store.store_id).first()
    return AuthContext(user=user, store=store, subscription=sub, session=sess)


def login(
    session,
    *,
    username: str,
    password: str,
    ttl_hours: int = SESSION_TTL_HOURS,
) -> AuthContext:
    username = (username or "").strip().lower()
    user = session.query(StoreUser).filter_by(username=username).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise PermissionError("invalid username or password")

    store = session.query(Store).filter_by(store_id=user.store_id).first()
    if not store:
        raise PermissionError("store missing for user")

    expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
    session_id = gen_uuid()
    raw = mint_signed_token(
        user_id=user.user_id,
        session_id=session_id,
        expires_at=expires_at,
    )
    sess = StoreSession(
        session_id=session_id,
        user_id=user.user_id,
        token_hash=hash_token(raw),
        token_prefix=raw[:12],
        expires_at=expires_at,
    )
    session.add(sess)
    session.flush()

    sub = session.query(Subscription).filter_by(store_id=store.store_id).first()
    return AuthContext(user=user, store=store, subscription=sub, session=sess, raw_token=raw)


def authenticate_session(session, raw_token: str) -> Optional[AuthContext]:
    if not raw_token or not str(raw_token).strip():
        return None
    raw_token = str(raw_token).strip()
    if not raw_token.startswith(SESSION_PREFIX):
        return None

    # Prefer signed tokens — work even after /tmp SQLite is recopied on cold start.
    payload = parse_signed_token(raw_token)
    if payload:
        sess = (
            session.query(StoreSession)
            .filter_by(session_id=payload["session_id"])
            .first()
        )
        if sess is not None and sess.revoked_at is not None:
            return None
        user = session.query(StoreUser).filter_by(user_id=payload["user_id"]).first()
        return _auth_from_user(session, user, sess)

    # Legacy opaque tokens (pre-signed): require a live StoreSession row.
    prefix = raw_token[:12]
    candidates = (
        session.query(StoreSession)
        .filter(StoreSession.token_prefix == prefix)
        .filter(StoreSession.revoked_at.is_(None))
        .all()
    )
    now = datetime.utcnow()
    for sess in candidates:
        if sess.expires_at < now:
            continue
        if hmac.compare_digest(hash_token(raw_token), sess.token_hash):
            user = session.query(StoreUser).filter_by(user_id=sess.user_id).first()
            return _auth_from_user(session, user, sess)
    return None


def logout(session, raw_token: str) -> bool:
    raw_token = (raw_token or "").strip()
    if not raw_token:
        return False

    payload = parse_signed_token(raw_token)
    if payload:
        sess = (
            session.query(StoreSession)
            .filter_by(session_id=payload["session_id"])
            .first()
        )
        if sess is not None and sess.revoked_at is None:
            sess.revoked_at = datetime.utcnow()
            session.flush()
        # Valid signature ⇒ treat logout as success even if the row was wiped.
        return True

    ctx = authenticate_session(session, raw_token)
    if not ctx or not ctx.session:
        return False
    ctx.session.revoked_at = datetime.utcnow()
    session.flush()
    return True
