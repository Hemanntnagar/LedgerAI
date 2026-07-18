"""
Step 8 — Store admin: subscription glance + POS API key status / rotate.
"""
from fastapi import APIRouter, Depends, HTTPException

from dbconn import get_session
from tenant_auth import require_store_auth

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/api-key")
def get_api_key_status(auth: dict = Depends(require_store_auth)):
    """Return whether a POS key exists (prefix only — raw key never stored)."""
    from db.models import Subscription

    session = get_session()
    try:
        sub = session.query(Subscription).filter_by(store_id=auth["store_id"]).first()
        if not sub:
            raise HTTPException(status_code=404, detail="No subscription for this store")
        return {
            "store_id": auth["store_id"],
            "has_key": bool(sub.api_key_hash),
            "prefix": sub.api_key_prefix,
            "plan": sub.plan,
            "status": sub.status,
        }
    finally:
        session.close()


@router.post("/api-key")
def rotate_api_key(auth: dict = Depends(require_store_auth)):
    """
    Issue or rotate the store POS API key.
    Raw key is returned once; only the hash is persisted.
    """
    from db.api_keys import issue_api_key
    from db.models import Subscription

    session = get_session()
    try:
        sub = session.query(Subscription).filter_by(store_id=auth["store_id"]).first()
        plan = sub.plan if sub else "basic"
        issued = issue_api_key(
            session,
            store_id=auth["store_id"],
            plan=plan,
        )
        session.commit()
        return {
            "store_id": issued.store_id,
            "api_key": issued.api_key,
            "prefix": issued.prefix,
            "plan": issued.plan,
            "warning": "Copy this key now — it will not be shown again.",
        }
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()
