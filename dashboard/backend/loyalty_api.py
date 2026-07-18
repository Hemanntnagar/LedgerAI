"""
Loyalty APIs (Step 4): lookup by phone, redeem rewards, list catalog.

Auth: store session (dashboard) or X-API-Key (POS checkout).
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from dbconn import get_session

router = APIRouter(prefix="/api/v1/loyalty", tags=["loyalty"])


class RedeemRequest(BaseModel):
    phone: str = Field(..., min_length=8)
    reward_code: str = Field(..., min_length=1)


def _extract_api_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key and str(x_api_key).strip():
        return str(x_api_key).strip()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        # Store sessions start with st_; API keys with inv_
        if token.startswith("inv_"):
            return token
    return None


def _resolve_store(
    authorization: Optional[str],
    x_store_token: Optional[str],
    x_api_key: Optional[str],
) -> Dict[str, Any]:
    """
    Accept either a store dashboard session or a POS API key.
    Returns {store_id, store_name, plan, auth_via}.
    """
    from tenant_auth import extract_bearer
    from db.store_auth import authenticate_session
    from db.api_keys import authenticate_api_key

    session = get_session()
    try:
        raw_session = x_store_token or extract_bearer(authorization)
        if raw_session and raw_session.startswith("st_"):
            ctx = authenticate_session(session, raw_session)
            if not ctx:
                raise HTTPException(status_code=401, detail="Invalid or expired store session")
            return {
                "store_id": ctx.store.store_id,
                "store_name": ctx.store.store_name,
                "plan": ctx.subscription.plan if ctx.subscription else None,
                "auth_via": "session",
            }

        raw_key = _extract_api_key(x_api_key, authorization)
        if raw_key:
            auth = authenticate_api_key(session, raw_key)
            if not auth:
                raise HTTPException(status_code=401, detail="Invalid API key")
            store, sub = auth
            return {
                "store_id": store.store_id,
                "store_name": store.store_name,
                "plan": sub.plan if sub else None,
                "auth_via": "api_key",
            }

        raise HTTPException(
            status_code=401,
            detail="Missing auth (Authorization Bearer st_… / X-Store-Token, or X-API-Key)",
        )
    finally:
        session.close()


def _require_loyalty_feature(store_id: str) -> None:
    from db.loyalty import plan_has_loyalty

    session = get_session()
    try:
        if not plan_has_loyalty(session, store_id):
            raise HTTPException(
                status_code=403,
                detail="Loyalty requires an active Pro plan",
            )
    finally:
        session.close()


@router.get("/lookup")
def loyalty_lookup(
    phone: str = Query(..., min_length=8),
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Checkout / CRM lookup: balance, tier, lifetime stats by phone."""
    auth = _resolve_store(authorization, x_store_token, x_api_key)
    _require_loyalty_feature(auth["store_id"])

    from db.loyalty import lookup_by_phone

    session = get_session()
    try:
        result = lookup_by_phone(session, auth["store_id"], phone)
        session.commit()
        if not result.found and not result.customer_id:
            raise HTTPException(status_code=404, detail=result.detail or "not found")
        return {
            "found": result.found,
            "store_id": auth["store_id"],
            "phone_normalized": result.phone_normalized,
            "customer_id": result.customer_id,
            "customer_name": result.customer_name,
            "loyalty_account_id": result.loyalty_account_id,
            "points_balance": result.points_balance,
            "tier": result.tier,
            "lifetime_spend": result.lifetime_spend,
            "visits": result.visits,
            "detail": result.detail,
        }
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.get("/rewards")
def loyalty_rewards(
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Reward catalog (discount %, free add-on, festival coupon)."""
    auth = _resolve_store(authorization, x_store_token, x_api_key)
    _require_loyalty_feature(auth["store_id"])

    from db.loyalty import list_rewards

    return {"store_id": auth["store_id"], "rewards": list_rewards()}


@router.post("/redeem")
def loyalty_redeem(
    body: RedeemRequest,
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Redeem a catalog reward; deducts points and returns the reward payload."""
    auth = _resolve_store(authorization, x_store_token, x_api_key)
    _require_loyalty_feature(auth["store_id"])

    from db.loyalty import redeem_reward

    session = get_session()
    try:
        result = redeem_reward(
            session,
            store_id=auth["store_id"],
            phone=body.phone,
            reward_code=body.reward_code,
            require_loyalty_plan=True,
        )
        if result.status != "redeemed":
            code = 404 if "no customer" in (result.detail or "") else 400
            if "insufficient" in (result.detail or ""):
                code = 402
            raise HTTPException(status_code=code, detail=result.detail or "redeem failed")
        session.commit()
        return {
            "status": "redeemed",
            "store_id": auth["store_id"],
            "points_delta": result.points_delta,
            "points_balance": result.points_balance,
            "tier": result.tier,
            "reward": result.reward,
        }
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()
