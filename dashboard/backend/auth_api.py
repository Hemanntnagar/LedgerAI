"""
Store login / logout / me (Step 2).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from dbconn import get_session
from tenant_auth import extract_bearer, require_store_auth

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str = Field(min_length=6)


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    store_id: str
    store_name: str
    username: str
    plan: Optional[str] = None
    expires_at: Optional[str] = None


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    from db.store_auth import login as do_login

    session = get_session()
    try:
        try:
            ctx = do_login(session, username=body.username, password=body.password)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        session.commit()
        expires = None
        if ctx.session is not None and ctx.session.expires_at is not None:
            expires = ctx.session.expires_at.isoformat() + "Z"
        return LoginResponse(
            token=ctx.raw_token,
            store_id=ctx.store.store_id,
            store_name=ctx.store.store_name,
            username=ctx.user.username,
            plan=ctx.subscription.plan if ctx.subscription else None,
            expires_at=expires,
        )
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()


@router.post("/logout")
def logout(
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    from db.store_auth import logout as do_logout

    raw = x_store_token or extract_bearer(authorization)
    if not raw:
        raise HTTPException(status_code=401, detail="Missing store session")
    session = get_session()
    try:
        ok = do_logout(session, raw)
        session.commit()
        if not ok:
            raise HTTPException(status_code=401, detail="Invalid session")
        return {"status": "logged_out"}
    finally:
        session.close()


@router.get("/me")
def auth_me(auth: dict = Depends(require_store_auth)):
    return {
        "user_id": auth["user_id"],
        "username": auth["username"],
        "store_id": auth["store_id"],
        "store_name": auth["store_name"],
        "plan": auth.get("plan"),
    }
