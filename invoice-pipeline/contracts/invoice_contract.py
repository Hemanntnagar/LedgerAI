"""
Invoice data contract (Step 0).

Defines what every invoice must carry so loyalty, reports, and ML work.
Adapters and the future live API should map into these shapes before load.

Required
  - store_id (or store_name resolvable to store_id)
  - invoice_number, invoice_date (+ optional time)
  - line items: product identity, quantity, unit_price, optional discount
  - grand_total

Required for loyalty / customer ML
  - customer phone (normalized to E.164 / Indian 10-digit)

Optional but valuable
  - offer/promo codes, payment method, unit cost / margin, stock qty
"""
from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import List, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from db.phone import InvalidPhoneError, normalize_phone


Number = Union[Decimal, float, int, str]


class LineItemContract(BaseModel):
    product_name: Optional[str] = None
    product_id: Optional[str] = None
    sku: Optional[str] = None
    category: Optional[str] = None
    quantity: Number
    unit_price: Number
    discount_pct: Optional[Number] = 0
    tax_pct: Optional[Number] = None
    line_total: Optional[Number] = None
    offer_code: Optional[str] = None
    unit_cost: Optional[Number] = None  # margin / elasticity inputs

    @model_validator(mode="after")
    def _need_product_identity(self) -> "LineItemContract":
        if not (self.product_id or self.sku or (self.product_name and self.product_name.strip())):
            raise ValueError("each line item needs product_id, sku, or product_name")
        return self


class InvoiceContract(BaseModel):
    """Canonical invoice payload for ingest (file adapters or live API)."""

    store_id: Optional[str] = None
    store_name: Optional[str] = None
    invoice_number: str
    invoice_date: date
    invoice_time: Optional[time] = None
    line_items: List[LineItemContract] = Field(min_length=1)
    grand_total: Number

    # Loyalty / customer ML
    customer_phone: Optional[str] = None
    customer_name: Optional[str] = None
    customer_id: Optional[str] = None

    # Optional enrichments
    payment_method: Optional[str] = None
    offer_codes: List[str] = Field(default_factory=list)
    subtotal: Optional[Number] = None
    tax_total: Optional[Number] = None
    discount_total: Optional[Number] = None
    source_type: str = "api"  # 'api' | 'csv' | 'json' | 'pdf' | 'ocr_image'

    phone_normalized: Optional[str] = None  # filled by validator

    @model_validator(mode="after")
    def _require_store(self) -> "InvoiceContract":
        if not self.store_id and not (self.store_name and self.store_name.strip()):
            raise ValueError("store_id or store_name is required")
        return self

    @field_validator("invoice_number")
    @classmethod
    def _nonempty_invoice_number(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("invoice_number is required")
        return str(v).strip()

    @model_validator(mode="after")
    def _normalize_phone(self) -> "InvoiceContract":
        if self.customer_phone is None or not str(self.customer_phone).strip():
            self.phone_normalized = None
            return self
        try:
            self.phone_normalized = normalize_phone(self.customer_phone)
        except InvalidPhoneError as exc:
            raise ValueError(str(exc)) from exc
        return self


def validate_invoice_contract(payload: dict, *, require_phone: bool = False) -> InvoiceContract:
    """Parse and validate a raw dict against the invoice contract.

    Set require_phone=True for Pro/loyalty plans (enforced in Step 1/2).
    """
    inv = InvoiceContract.model_validate(payload)
    if require_phone and not inv.phone_normalized:
        raise ValueError("customer_phone is required for loyalty-enabled ingest")
    return inv
