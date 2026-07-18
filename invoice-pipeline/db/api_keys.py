"""
Store API keys for live invoice ingest (Step 1).

Keys are stored hashed (SHA-256) on Subscription.api_key_hash.
The raw key is shown once at issue time and never persisted.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Optional, Tuple

from db.models import Store, Subscription, gen_uuid

API_KEY_PREFIX = "inv_"


@dataclass
class IssuedApiKey:
    store_id: str
    api_key: str  # raw — show once
    prefix: str
    plan: str


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> Tuple[str, str, str]:
    """Return (raw_key, key_hash, prefix)."""
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    prefix = raw[:12]
    return raw, hash_api_key(raw), prefix


def verify_api_key(raw_key: str, key_hash: Optional[str]) -> bool:
    if not raw_key or not key_hash:
        return False
    return hmac.compare_digest(hash_api_key(raw_key), key_hash)


def issue_api_key(
    session,
    *,
    store_id: Optional[str] = None,
    store_name: Optional[str] = None,
    plan: str = "basic",
) -> IssuedApiKey:
    """Create or rotate an API key for a store. Ensures a Subscription row exists."""
    from db.plans import apply_plan_to_subscription, get_plan, seed_subscription_plans
    from db.subscriptions import ensure_billing_period

    store = _resolve_store(session, store_id=store_id, store_name=store_name)
    raw, key_hash, prefix = generate_api_key()

    seed_subscription_plans(session)
    plan_row = get_plan(session, plan) if plan else None

    sub = session.query(Subscription).filter_by(store_id=store.store_id).first()
    if not sub:
        sub = Subscription(subscription_id=gen_uuid(), store_id=store.store_id)
        session.add(sub)
        store.subscription = sub

    sub.api_key_hash = key_hash
    sub.api_key_prefix = prefix
    if plan_row:
        apply_plan_to_subscription(sub, plan_row)
    elif plan:
        sub.plan = plan
    if sub.status != "active":
        sub.status = "active"
    ensure_billing_period(sub)
    session.flush()

    return IssuedApiKey(
        store_id=store.store_id,
        api_key=raw,
        prefix=prefix,
        plan=sub.plan,
    )


def authenticate_api_key(session, raw_key: str) -> Optional[Tuple[Store, Subscription]]:
    """Validate an API key; return (store, subscription) or None."""
    if not raw_key or not str(raw_key).strip():
        return None
    raw_key = str(raw_key).strip()
    prefix = raw_key[:12]

    candidates = (
        session.query(Subscription)
        .filter(Subscription.api_key_prefix == prefix)
        .filter(Subscription.status == "active")
        .all()
    )
    # Fallback if prefix column empty / older rows
    if not candidates:
        candidates = (
            session.query(Subscription)
            .filter(Subscription.status == "active")
            .filter(Subscription.api_key_hash.isnot(None))
            .all()
        )

    for sub in candidates:
        if verify_api_key(raw_key, sub.api_key_hash):
            store = session.query(Store).filter_by(store_id=sub.store_id).first()
            if store:
                return store, sub
    return None


def _resolve_store(
    session,
    *,
    store_id: Optional[str],
    store_name: Optional[str],
) -> Store:
    if store_id:
        store = session.query(Store).filter_by(store_id=store_id).first()
        if not store:
            raise ValueError(f"unknown store_id: {store_id}")
        return store
    if store_name and str(store_name).strip():
        name = store_name.strip()
        store = session.query(Store).filter_by(store_name=name).first()
        if not store:
            store = Store(store_name=name)
            session.add(store)
            session.flush()
        return store
    raise ValueError("store_id or store_name is required")
