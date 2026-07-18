"""Plain-language Indian-English templates — no LLM."""
from __future__ import annotations

TEMPLATES: dict[str, dict[str, str]] = {
    "bundle": {
        "title": "Bundle {antecedents} + {consequents}",
        "body": (
            "{pct}% of customers who buy {antecedents} also buy {consequents} "
            "(lift {lift}×). Offer a combo — estimated +₹{impact}/month basket lift."
        ),
    },
    "stop_discount": {
        "title": "Stop deep-discounting {product}",
        "body": (
            "{product} is on a promo floor ({classification}). "
            "Pull back the discount — estimated ₹{impact}/month margin recovery."
        ),
    },
    "reorder": {
        "title": "Reorder {product} (demand surge)",
        "body": (
            "{product} is selling ~{recent} units/day. "
            "Deepen stock for the next {days} days — ~₹{impact} at risk if you stock out."
        ),
    },
    "clear_dead_stock": {
        "title": "Clear dead stock: {product}",
        "body": (
            "{product} has had no sales for {days} days. "
            "Run clearance or stop reordering — free up ~₹{impact} working capital."
        ),
    },
    "investigate_velocity": {
        "title": "Check demand drop: {product}",
        "body": (
            "{product} velocity fell to {recent}/day from baseline {baseline}/day. "
            "Check stock position, placement, or competitor pricing."
        ),
    },
    "win_back": {
        "title": "Win-back {count} dormant / at-risk customers",
        "body": (
            "{count} phone-linked customers look dormant or at risk. "
            "Send a loyalty offer (~{rate_pct}% reactivation) — ~₹{impact} potential."
        ),
    },
    "vip_nurture": {
        "title": "Nurture {count} VIP customers",
        "body": (
            "Protect {count} VIP buyers with early festival access or a thank-you reward "
            "(~₹{impact} retention value)."
        ),
    },
    "festival_prep": {
        "title": "{festival}: {action} {product}",
        "body": (
            "Prep for {festival} ({window_start} → {window_end}). "
            "{product} historically lifts vs baseline"
            "{lift_clause}. Estimated opportunity ~₹{impact}."
        ),
    },
    "anomaly_review": {
        "title": "Review {metric} anomaly: {product}",
        "body": (
            "On {date}, {product} {metric} was ₹{actual} vs expected ₹{expected} "
            "(z={z}). Investigate billing errors, stockouts, or one-off events."
        ),
    },
    "protect_a_sku": {
        "title": "Protect A-class SKU: {product}",
        "body": (
            "{product} is revenue rank #{rank} (class A). "
            "Keep it always in stock and avoid aggressive discounting."
        ),
    },
}


def render_action(row: dict) -> tuple[str, str]:
    """Return (title, body). Prefer ML suggestion_text as body when present."""
    atype = row["action_type"]
    tmpl = TEMPLATES.get(atype, {"title": atype, "body": ""})
    title_vars = dict(row.get("title_vars") or {})
    body_vars = dict(row.get("body_vars") or {})

    # Festival lift clause is optional
    if atype == "festival_prep":
        lift_pct = body_vars.get("lift_pct")
        body_vars["lift_clause"] = (
            f" (+{lift_pct}%)" if lift_pct is not None else ""
        )

    title = tmpl["title"].format_map(_Safe(title_vars))
    if row.get("suggestion_text"):
        body = row["suggestion_text"]
    else:
        body = tmpl["body"].format_map(_Safe(body_vars))
    return title, body


class _Safe(dict):
    def __missing__(self, key):
        return ""
