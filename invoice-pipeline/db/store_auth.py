"""
Store dashboard login + session tokens (Step 2).

Passwords: PBKDF2-HMAC-SHA256 (stdlib). Session tokens: opaque, hashed at rest.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

from db.models import Store, StoreSession, StoreUser, Subscription, gen_uuid

SESSION_PREFIX = "st_"
SESSION_TTL_HOURS = 72
PBKDF2_ITERATIONS = 120_000


@dataclass
class AuthContext:
    user: StoreUser
    store: Store
    subscription: Optional[Subscription]
    session: StoreSession
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

    raw = SESSION_PREFIX + secrets.token_urlsafe(32)
    sess = StoreSession(
        session_id=gen_uuid(),
        user_id=user.user_id,
        token_hash=hash_token(raw),
        token_prefix=raw[:12],
        expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
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
            if not user or not user.is_active:
                return None
            store = session.query(Store).filter_by(store_id=user.store_id).first()
            if not store:
                return None
            sub = session.query(Subscription).filter_by(store_id=store.store_id).first()
            return AuthContext(user=user, store=store, subscription=sub, session=sess)
    return None


def logout(session, raw_token: str) -> bool:
    ctx = authenticate_session(session, raw_token)
    if not ctx:
        return False
    ctx.session.revoked_at = datetime.utcnow()
    session.flush()
    return True
