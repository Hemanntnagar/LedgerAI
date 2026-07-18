"""
Live invoice ingest API (Step 1).

POST /api/v1/invoices — POS pushes a bill; auth via store API key.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from dbconn import get_session

router = APIRouter(prefix="/api/v1", tags=["live-ingest"])


class LiveInvoiceResponse(BaseModel):
    status: str
    invoice_id: Optional[str] = None
    customer_id: Optional[str] = None
    store_id: Optional[str] = None
    phone_normalized: Optional[str] = None
    detail: Optional[str] = None
    loyalty_points_earned: Optional[int] = None
    loyalty_points_balance: Optional[int] = None
    loyalty_tier: Optional[str] = None


@router.post("/invoices", response_model=LiveInvoiceResponse, status_code=201)
def create_invoice(
    payload: Dict[str, Any],
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
):
    """Ingest one POS-style invoice JSON. Auth: X-API-Key or Bearer token."""
    raw_key = _extract_api_key(x_api_key, authorization)
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API key (X-API-Key header)")

    # Import from invoice-pipeline (on sys.path via dbconn)
    from db.api_keys import authenticate_api_key
    from pipeline.live_ingest import ingest_live_invoice

    session = get_session()
    try:
        auth = authenticate_api_key(session, raw_key)
        if not auth:
            raise HTTPException(status_code=401, detail="Invalid API key")
        store, subscription = auth

        result = ingest_live_invoice(
            session,
            payload,
            store=store,
            subscription=subscription,
        )

        if result.status == "failed_validation":
            session.commit()  # persist ingestion_errors if any
            raise HTTPException(
                status_code=422,
                detail=result.detail or "invoice validation failed",
            )

        session.commit()
        return LiveInvoiceResponse(
            status=result.status,
            invoice_id=result.invoice_id,
            customer_id=result.customer_id,
            store_id=result.store_id,
            phone_normalized=result.phone_normalized,
            detail=result.detail,
            loyalty_points_earned=result.loyalty_points_earned,
            loyalty_points_balance=result.loyalty_points_balance,
            loyalty_tier=result.loyalty_tier,
        )
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()


def _extract_api_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key and str(x_api_key).strip():
        return str(x_api_key).strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None
