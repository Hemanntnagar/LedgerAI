"""
Step 4 — phone-based loyalty: earn, tier, redeem, lookup.

Identity = normalized phone → Customer → LoyaltyAccount (per store).
"""
from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import yaml
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import (
    Customer,
    FestivalEvent,
    Invoice,
    LoyaltyAccount,
    LoyaltyTransaction,
    Subscription,
    SubscriptionPlan,
)
from db.phone import InvalidPhoneError, normalize_phone

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")

TIER_ORDER = ("bronze", "silver", "gold")


@functools.lru_cache(maxsize=None)
def load_loyalty_config() -> dict:
    path = os.path.join(CONFIG_DIR, "loyalty.yaml")
    with open(path) as f:
        return yaml.safe_load(f) or {}


def clear_loyalty_config_cache() -> None:
    load_loyalty_config.cache_clear()


def plan_has_loyalty(session: Session, store_id: str) -> bool:
    """True when the store's plan features_json.loyalty is enabled (Pro)."""
    sub = session.query(Subscription).filter_by(store_id=store_id).first()
    if not sub or sub.status != "active":
        return False
    plan = session.query(SubscriptionPlan).filter_by(plan_code=sub.plan).first()
    features = (plan.features_json if plan else None) or {}
    if isinstance(features, dict) and features.get("loyalty"):
        return True
    # Fallback: pro plan always has loyalty even if catalog row is stale
    return sub.plan == "pro"


def festival_on_date(session: Session, day: date) -> Optional[FestivalEvent]:
    return (
        session.query(FestivalEvent)
        .filter(FestivalEvent.start_date <= day, FestivalEvent.end_date >= day)
        .order_by(FestivalEvent.start_date.asc())
        .first()
    )


def points_for_amount(
    amount: float,
    *,
    invoice_date: Optional[date] = None,
    session: Optional[Session] = None,
    config: Optional[dict] = None,
) -> Tuple[int, float, Optional[str]]:
    """
    Return (points, multiplier_applied, festival_name_or_None).
    """
    cfg = config or load_loyalty_config()
    pts_cfg = cfg.get("points") or {}
    rupees_per = float(pts_cfg.get("rupees_per_point") or 10)
    min_amt = float(pts_cfg.get("min_invoice_amount") or 0)
    festival_mult = float(pts_cfg.get("festival_multiplier") or 1.0)

    if amount is None or amount < min_amt or rupees_per <= 0:
        return 0, 1.0, None

    mult = 1.0
    fest_name = None
    if session is not None and invoice_date is not None and festival_mult > 1.0:
        fest = festival_on_date(session, invoice_date)
        if fest:
            mult = festival_mult
            fest_name = fest.name

    raw = (float(amount) / rupees_per) * mult
    return int(raw), mult, fest_name


def get_or_create_account(
    session: Session, store_id: str, customer_id: str
) -> LoyaltyAccount:
    acct = (
        session.query(LoyaltyAccount)
        .filter_by(store_id=store_id, customer_id=customer_id)
        .first()
    )
    if acct:
        return acct
    acct = LoyaltyAccount(
        store_id=store_id,
        customer_id=customer_id,
        points_balance=0,
        tier="bronze",
    )
    session.add(acct)
    session.flush()
    return acct


def customer_stats(
    session: Session, store_id: str, customer_id: str
) -> Tuple[float, int]:
    """Lifetime spend and visit count for this store×customer."""
    row = (
        session.query(
            func.coalesce(func.sum(Invoice.grand_total), 0),
            func.count(Invoice.invoice_id),
        )
        .filter(Invoice.store_id == store_id, Invoice.customer_id == customer_id)
        .one()
    )
    return float(row[0] or 0), int(row[1] or 0)


def compute_tier(lifetime_spend: float, visits: int, config: Optional[dict] = None) -> str:
    cfg = config or load_loyalty_config()
    tiers = cfg.get("tiers") or {}
    best = "bronze"
    best_rank = 0
    for name in TIER_ORDER:
        spec = tiers.get(name) or {}
        min_spend = float(spec.get("min_lifetime_spend") or 0)
        min_visits = int(spec.get("min_visits") or 0)
        if lifetime_spend >= min_spend or visits >= min_visits:
            rank = TIER_ORDER.index(name)
            if rank >= best_rank:
                best = name
                best_rank = rank
    return best


def refresh_tier(session: Session, account: LoyaltyAccount) -> str:
    spend, visits = customer_stats(session, account.store_id, account.customer_id)
    new_tier = compute_tier(spend, visits)
    if account.tier != new_tier:
        account.tier = new_tier
        account.updated_at = datetime.utcnow()
    return account.tier


def already_earned_for_invoice(session: Session, invoice_id: str) -> bool:
    return (
        session.query(LoyaltyTransaction)
        .filter_by(invoice_id=invoice_id, reason="earn")
        .first()
        is not None
    )


@dataclass
class EarnResult:
    status: str  # earned | skipped | duplicate
    points_delta: int = 0
    points_balance: int = 0
    tier: Optional[str] = None
    multiplier: float = 1.0
    festival: Optional[str] = None
    detail: Optional[str] = None
    loyalty_account_id: Optional[str] = None


def earn_on_invoice(
    session: Session,
    invoice: Invoice,
    *,
    require_loyalty_plan: bool = True,
) -> EarnResult:
    """
    Accrue points for an invoice with a linked customer.
    Idempotent on invoice_id (one earn txn per invoice).
    """
    if not invoice.customer_id:
        return EarnResult(status="skipped", detail="no customer on invoice")
    if require_loyalty_plan and not plan_has_loyalty(session, invoice.store_id):
        return EarnResult(status="skipped", detail="plan does not include loyalty")
    if already_earned_for_invoice(session, invoice.invoice_id):
        acct = (
            session.query(LoyaltyAccount)
            .filter_by(store_id=invoice.store_id, customer_id=invoice.customer_id)
            .first()
        )
        return EarnResult(
            status="duplicate",
            points_balance=int(acct.points_balance) if acct else 0,
            tier=acct.tier if acct else None,
            loyalty_account_id=acct.loyalty_account_id if acct else None,
            detail="already earned for this invoice",
        )

    amount = float(invoice.grand_total or 0)
    points, mult, fest = points_for_amount(
        amount, invoice_date=invoice.invoice_date, session=session
    )
    acct = get_or_create_account(session, invoice.store_id, invoice.customer_id)

    if points <= 0:
        refresh_tier(session, acct)
        return EarnResult(
            status="skipped",
            points_balance=int(acct.points_balance),
            tier=acct.tier,
            loyalty_account_id=acct.loyalty_account_id,
            detail="zero points for amount",
        )

    note = f"₹{amount:.2f}"
    if fest:
        note = f"{note} ×{mult:g} ({fest})"

    session.add(
        LoyaltyTransaction(
            loyalty_account_id=acct.loyalty_account_id,
            invoice_id=invoice.invoice_id,
            points_delta=points,
            reason="earn",
            note=note,
        )
    )
    acct.points_balance = int(acct.points_balance or 0) + points
    acct.updated_at = datetime.utcnow()
    tier = refresh_tier(session, acct)
    session.flush()

    return EarnResult(
        status="earned",
        points_delta=points,
        points_balance=int(acct.points_balance),
        tier=tier,
        multiplier=mult,
        festival=fest,
        loyalty_account_id=acct.loyalty_account_id,
    )


def backfill_loyalty(
    session: Session,
    *,
    store_id: Optional[str] = None,
) -> Dict[str, int]:
    """Earn for historical invoices that have a customer and no earn txn yet."""
    q = session.query(Invoice).filter(Invoice.customer_id.isnot(None))
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    earned = skipped = duplicate = 0
    for inv in q.order_by(Invoice.invoice_date.asc()).all():
        result = earn_on_invoice(session, inv, require_loyalty_plan=True)
        if result.status == "earned":
            earned += 1
        elif result.status == "duplicate":
            duplicate += 1
        else:
            skipped += 1
    return {"earned": earned, "skipped": skipped, "duplicate": duplicate}


@dataclass
class LookupResult:
    found: bool
    phone_normalized: Optional[str] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    loyalty_account_id: Optional[str] = None
    points_balance: int = 0
    tier: str = "bronze"
    lifetime_spend: float = 0.0
    visits: int = 0
    detail: Optional[str] = None


def lookup_by_phone(
    session: Session, store_id: str, phone: str
) -> LookupResult:
    try:
        normalized = normalize_phone(phone)
    except InvalidPhoneError as exc:
        return LookupResult(found=False, detail=str(exc))
    if not normalized:
        return LookupResult(found=False, detail="empty phone")

    customer = (
        session.query(Customer).filter_by(phone_normalized=normalized).first()
    )
    if not customer:
        return LookupResult(
            found=False,
            phone_normalized=normalized,
            detail="no customer for this phone",
        )

    acct = (
        session.query(LoyaltyAccount)
        .filter_by(store_id=store_id, customer_id=customer.customer_id)
        .first()
    )
    spend, visits = customer_stats(session, store_id, customer.customer_id)
    if not acct:
        return LookupResult(
            found=True,
            phone_normalized=normalized,
            customer_id=customer.customer_id,
            customer_name=customer.customer_name,
            points_balance=0,
            tier=compute_tier(spend, visits),
            lifetime_spend=spend,
            visits=visits,
            detail="no loyalty account yet (will create on next earn)",
        )

    # Keep tier in sync with spend/visits on lookup
    refresh_tier(session, acct)
    return LookupResult(
        found=True,
        phone_normalized=normalized,
        customer_id=customer.customer_id,
        customer_name=customer.customer_name,
        loyalty_account_id=acct.loyalty_account_id,
        points_balance=int(acct.points_balance or 0),
        tier=acct.tier,
        lifetime_spend=spend,
        visits=visits,
    )


def list_rewards(config: Optional[dict] = None) -> List[Dict[str, Any]]:
    cfg = config or load_loyalty_config()
    rewards = cfg.get("rewards") or []
    return [dict(r) for r in rewards]


def get_reward(code: str, config: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    code_u = code.strip().upper()
    for r in list_rewards(config):
        if str(r.get("code", "")).upper() == code_u:
            return r
    return None


@dataclass
class RedeemResult:
    status: str  # redeemed | failed
    points_delta: int = 0
    points_balance: int = 0
    tier: Optional[str] = None
    reward: Optional[Dict[str, Any]] = None
    detail: Optional[str] = None


def redeem_reward(
    session: Session,
    *,
    store_id: str,
    phone: str,
    reward_code: str,
    require_loyalty_plan: bool = True,
) -> RedeemResult:
    if require_loyalty_plan and not plan_has_loyalty(session, store_id):
        return RedeemResult(status="failed", detail="plan does not include loyalty")

    reward = get_reward(reward_code)
    if not reward:
        return RedeemResult(status="failed", detail=f"unknown reward: {reward_code}")

    cost = int(reward.get("points_cost") or 0)
    if cost <= 0:
        return RedeemResult(status="failed", detail="reward has invalid points_cost")

    try:
        normalized = normalize_phone(phone)
    except InvalidPhoneError as exc:
        return RedeemResult(status="failed", detail=str(exc))
    if not normalized:
        return RedeemResult(status="failed", detail="empty phone")

    customer = session.query(Customer).filter_by(phone_normalized=normalized).first()
    if not customer:
        return RedeemResult(status="failed", detail="no customer for this phone")

    acct = (
        session.query(LoyaltyAccount)
        .filter_by(store_id=store_id, customer_id=customer.customer_id)
        .first()
    )
    if not acct:
        return RedeemResult(status="failed", detail="no loyalty account")

    balance = int(acct.points_balance or 0)
    if balance < cost:
        return RedeemResult(
            status="failed",
            points_balance=balance,
            tier=acct.tier,
            detail=f"insufficient points (have {balance}, need {cost})",
        )

    delta = -cost
    session.add(
        LoyaltyTransaction(
            loyalty_account_id=acct.loyalty_account_id,
            invoice_id=None,
            points_delta=delta,
            reason="redeem",
            note=f"reward:{reward.get('code')}",
        )
    )
    acct.points_balance = balance + delta
    acct.updated_at = datetime.utcnow()
    session.flush()

    return RedeemResult(
        status="redeemed",
        points_delta=delta,
        points_balance=int(acct.points_balance),
        tier=acct.tier,
        reward=reward,
    )
