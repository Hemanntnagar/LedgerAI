"""
Live POS invoice ingest (Step 1).

Accepts a POS-style JSON payload (InvoiceContract), reuses the same
cleaning / product matching / dedup path as file ingest, and upserts
customers by normalized phone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any, Dict, Optional

from adapters.base import RawInvoiceData, RawLineItem
from contracts.invoice_contract import InvoiceContract, validate_invoice_contract
from db.models import Invoice, Store, Subscription
from pipeline.config_loader import load_thresholds
from pipeline.ingest import ingest_invoice


@dataclass
class LiveIngestResult:
    status: str  # loaded | duplicate | failed_validation
    invoice_id: Optional[str] = None
    customer_id: Optional[str] = None
    store_id: Optional[str] = None
    phone_normalized: Optional[str] = None
    detail: Optional[str] = None
    # Step 4 loyalty (populated when points accrue on load)
    loyalty_points_earned: Optional[int] = None
    loyalty_points_balance: Optional[int] = None
    loyalty_tier: Optional[str] = None


def _num_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def contract_to_raw(inv: InvoiceContract, *, store_name: str) -> RawInvoiceData:
    """Map validated contract → RawInvoiceData for the shared ingest path."""
    line_items = []
    for li in inv.line_items:
        line_items.append(
            RawLineItem(
                product_name=li.product_name,
                product_id=li.product_id,
                sku=li.sku,
                category=li.category,
                quantity=_num_str(li.quantity),
                unit_price=_num_str(li.unit_price),
                discount_pct=_num_str(li.discount_pct) if li.discount_pct is not None else "0",
                tax_pct=_num_str(li.tax_pct),
                line_total=_num_str(li.line_total),
                unit_cost=_num_str(li.unit_cost),
            )
        )

    inv_date = inv.invoice_date
    if isinstance(inv_date, date):
        date_str = inv_date.isoformat()
    else:
        date_str = str(inv_date)

    time_str = None
    if inv.invoice_time is not None:
        if isinstance(inv.invoice_time, time):
            time_str = inv.invoice_time.strftime("%H:%M:%S")
        else:
            time_str = str(inv.invoice_time)

    return RawInvoiceData(
        store_name=store_name,
        invoice_number=inv.invoice_number,
        invoice_date=date_str,
        invoice_time=time_str,
        customer_name=inv.customer_name,
        customer_phone=inv.customer_phone,
        payment_method=inv.payment_method,
        offer_codes=list(inv.offer_codes or []),
        line_items=line_items,
        source_type="api",
        source_file_path=f"api://{store_name}/{inv.invoice_number}",
        confidence=1.0,
    )


def _assert_store_matches(inv: InvoiceContract, store: Store) -> None:
    """Reject payloads that try to attribute the sale to another store."""
    if inv.store_id and inv.store_id != store.store_id:
        raise PermissionError(
            f"payload store_id {inv.store_id} does not match authenticated store"
        )
    if inv.store_name and inv.store_name.strip() and inv.store_name.strip() != store.store_name:
        raise PermissionError(
            f"payload store_name {inv.store_name!r} does not match authenticated store"
        )


def ingest_live_invoice(
    session,
    payload: Dict[str, Any],
    *,
    store: Store,
    subscription: Optional[Subscription] = None,
) -> LiveIngestResult:
    """Validate POS JSON, run shared ingest, return ids for the API response."""
    require_phone = bool(subscription and subscription.require_customer_phone)

    # Store identity comes from the API key; clients may omit store fields.
    body = dict(payload)
    body.setdefault("store_id", store.store_id)
    body.setdefault("store_name", store.store_name)

    try:
        inv = validate_invoice_contract(body, require_phone=require_phone)
    except Exception as exc:
        return LiveIngestResult(status="failed_validation", detail=str(exc), store_id=store.store_id)

    try:
        _assert_store_matches(inv, store)
    except PermissionError as exc:
        return LiveIngestResult(status="failed_validation", detail=str(exc), store_id=store.store_id)

    # Force authenticated store identity onto the payload path
    raw = contract_to_raw(inv, store_name=store.store_name)
    thresholds = load_thresholds()
    outcome = ingest_invoice(session, raw, thresholds)

    result = LiveIngestResult(
        status=outcome,
        store_id=store.store_id,
        phone_normalized=inv.phone_normalized,
    )

    if outcome in ("loaded", "duplicate"):
        parsed_date = inv.invoice_date if isinstance(inv.invoice_date, date) else None
        q = session.query(Invoice).filter_by(
            store_id=store.store_id,
            invoice_number=inv.invoice_number,
        )
        if parsed_date is not None:
            q = q.filter_by(invoice_date=parsed_date)
        row = q.order_by(Invoice.ingested_at.desc()).first()
        if row:
            result.invoice_id = row.invoice_id
            result.customer_id = row.customer_id
            # Step 4: accrue loyalty on first successful load (idempotent)
            if outcome == "loaded" and row.customer_id:
                from db.loyalty import earn_on_invoice

                earn = earn_on_invoice(session, row, require_loyalty_plan=True)
                if earn.status == "earned":
                    result.loyalty_points_earned = earn.points_delta
                    result.loyalty_points_balance = earn.points_balance
                    result.loyalty_tier = earn.tier
                elif earn.points_balance is not None and earn.loyalty_account_id:
                    result.loyalty_points_balance = earn.points_balance
                    result.loyalty_tier = earn.tier

    if outcome == "failed_validation":
        result.detail = "invoice failed pipeline validation (see ingestion_errors)"

    return result
