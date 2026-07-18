# Insight Engine (Step 6)

Deterministic ranked-action generator. Reads ML result tables (+ customer
segments) and writes scored rows into the shared `insights` table.

**No external LLM.** Same DB snapshot + `config/rules.yaml` → same ranked list.

## Setup

```bash
cd insight-engine
pip install -r requirements.txt
export INVOICE_PIPELINE_ROOT=../invoice-pipeline
export ML_MODELS_ROOT=../ml-models
export INVOICE_DB_URL=sqlite:///../invoice-pipeline/invoice_pipeline.db
```

Run ML first (or rely on report generate which already calls `run-all`):

```bash
cd ../ml-models
python3 cli.py run-all --store-name "Corner Kirana"
```

Then:

```bash
cd ../insight-engine
python3 cli.py run --store-name "Corner Kirana"
python3 cli.py run --store-name "Corner Kirana" --dry-run
```

## Pipeline

1. **Collectors** — association rules, elasticity, inventory, RFM segments,
   festival insights, anomalies, ABC, forecasts
2. **Rules** — eligibility thresholds in `config/rules.yaml`
3. **Scorer** — `score = impact × confidence × urgency × weight`
4. **Conflicts** — product-level mutex (e.g. reorder vs clear_dead_stock)
5. **Templates** — Indian-English titles/bodies (or ML `suggestion_text`)
6. **Persist** — replaces `status=new` insights; keeps accepted/dismissed/done
7. **Learning (Step 7)** — feedback multipliers on rule weights

## Action types

| Type | Source |
|---|---|
| `bundle` | association_rules |
| `stop_discount` | price_elasticity (promo floor) |
| `reorder` | inventory velocity_surge (+ forecast) |
| `clear_dead_stock` | inventory dead_stock |
| `investigate_velocity` | inventory velocity_drop |
| `win_back` | customer_segments dormant/at_risk |
| `vip_nurture` | customer_segments vip |
| `festival_prep` | festival_insights |
| `anomaly_review` | anomalies |
| `protect_a_sku` | abc_classification class A |

## Dashboard wiring

`POST /api/v1/reports` runs ML → **insight engine** → snapshot with `top_actions`.
`GET /api/v1/insights` lists the ranked actions for the authenticated store.
`PATCH /api/v1/insights/{id}/feedback` records useful / not_useful.

## Step 7 — Feedback learning

Historical `useful` / `not_useful` marks on insights adjust rule
weights via a Beta-style prior (`config/rules.yaml` → `learning:`).
Neutral prior → multiplier `1.0`; more useful → up to `1.5`; more
not_useful → down to `0.5`. Same DB feedback + rules → same ranking
(still deterministic, still no LLM).

## Step 10 — Pilot (backtest & tune)

```bash
python3 cli.py pilot-outcomes --store-id <uuid>
python3 cli.py backtest --store-id <uuid>
python3 cli.py tune --store-id <uuid> --rule bundle --param min_lift --values 1.1,1.2,1.3
```

- `backtest.py` — correlate accepted months with next-month revenue lift
- `tuning.py` — simulate threshold grids against the current ML snapshot
- Window defaults / festival suggestions:
  `../invoice-pipeline/config/pilot.yaml`

## Tests

```bash
python3 -m pytest tests/ -v
```
