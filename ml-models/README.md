# Phase 3 — Core ML Models

Five ML/analytical modules that read from the Phase 1 invoice-pipeline
database and write structured results into their own result tables. Two
modules from the original spec (customer segmentation, churn prediction)
are **not built** here because the connected dataset has no `customer_id`
data populated — building them would mean fabricating a signal that
doesn't exist. Add them once customer-linked invoice data is available.

## Setup

```bash
pip install -r requirements.txt

export INVOICE_PIPELINE_ROOT=/path/to/invoice-pipeline
export INVOICE_DB_URL="sqlite:////path/to/invoice-pipeline/invoice_pipeline.db"
```

## Running the jobs

```bash
python3 cli.py run-abc-classification
python3 cli.py run-basket-analysis
python3 cli.py run-elasticity
python3 cli.py run-anomaly-detection
python3 cli.py run-forecast --horizon 30
python3 cli.py run-all                     # runs everything in sequence
python3 cli.py backtest-forecast --cutoff-date 2026-06-01 --horizon 14
```

Each job is idempotent — deletes its own result rows for the run before
re-inserting, so re-running doesn't duplicate data.

## Honest results against the connected dataset

This matters more than the code: I ran every module against the real
130-day, 2-store, 13-product dataset from Phase 1/2, not just synthetic
unit-test fixtures. Here's what actually came back, including the results
that reveal limits of the current data rather than flattering it.

### Forecasting: MAPE ~74% — genuinely mediocre, and here's why

Backtested against a 14-day holdout, Prophet ran successfully on all 26
(store, product) series with "high confidence," but **average MAPE across
products was 73.9%** — not a good result. I checked the root cause:
daily unit sales per product average ~11.7 units/day with a standard
deviation of ~6.3 — a coefficient of variation over 0.5. At this
transaction volume, day-level demand for a single SKU in a single store
is inherently noisy; there just isn't a strong enough signal for
day-granularity forecasting to work well yet.

**What this means practically**: either aggregate to weekly forecasts
(smooths the noise), forecast at a higher level (category or store total,
not per-SKU), or wait for more transaction volume before trusting
per-product daily forecasts for inventory decisions. The `confidence_level`
field is currently always "high" whenever there's enough history for
Prophet to run at all — it does NOT currently reflect backtested accuracy.
That's a real gap: **confidence_level should be derived from backtest MAPE,
not just from "did Prophet run."** Recommended follow-up before this
feeds inventory decisions: wire the backtest MAPE into the confidence
flag (e.g., MAPE > 40% → downgrade to "low" regardless of history length).

### Basket analysis: rules found, but weak (lift ~1.3, support ~1%)

36 rules found for one store, 20 for the other, 2 global. The top rules
by lift only reach ~1.3 with ~1% support — a real but weak signal. This
is consistent with the connected dataset: products were assigned to
baskets independent of each other (no built-in purchase affinity), so
found rules are closer to statistical noise than real "buy X get Y"
behavior. **On real POS data with actual customer purchase habits,
expect much stronger, more actionable rules** (lift 2+, support 5%+) —
don't calibrate expectations for "real" data off this run.

### ABC classification: worked as expected

5 of 13 products account for 70% of revenue (Class A), 4 more reach 90%
(Class B), the remaining 4 are Class C. This is deterministic ranking
math, so it's correct by construction — no data-quality caveat here.

### Price elasticity: mostly "unreliable_estimate" (22 of 26) — correctly so

Only 1 product came back "elastic," 3 "inelastic," and the rest flagged
unreliable. This is the model doing its job: the connected dataset's
price variation was randomized noise uncorrelated with demand, so there
genuinely isn't a price-demand relationship to detect. The model
correctly refuses to report a fabricated elasticity rather than forcing
a number. **On real data with actual promotional history, expect far
fewer "unreliable" flags** — this result is a feature of the test data,
not a flaw in the method.

### Anomaly detection: 65 product-level + 2 store-level flags

Ran cleanly. Given the input data's random daily noise, some of these are
likely false positives rather than genuine business anomalies — worth
spot-checking a few against your own judgment before trusting the flagged
list wholesale on any dataset, synthetic or real.

## Result tables

| Table | Written by |
|---|---|
| `forecast_results` | `forecasting/job.py` |
| `association_rules` | `basket_analysis/job.py` |
| `abc_classification` | `abc_classification/job.py` |
| `price_elasticity` | `elasticity/job.py` |
| `anomalies` | `anomaly_detection/job.py` |

All use the same SQLAlchemy `Base`/engine as the Phase 1 pipeline, so they
live in the same database.

## Configuration

All thresholds live in `config/model_config.yaml` — forecast horizon,
basket-analysis support/lift minimums, ABC cutoff percentages, elasticity
significance thresholds, anomaly z-score threshold. Change these without
touching code.

## Testing

```bash
python3 -m pytest tests/ -v
```

18 tests, all against synthetic fixtures with known/derivable correct
answers (not live DB) — e.g. a strong-vs-weak basket association test
that deliberately includes baseline variation, since a naive "always
paired" fixture actually produces lift == 1.0 (not > 1), which caught a
real test-design bug during development, not a code bug.

## Design notes worth knowing

- **Forecasting** falls back to a naive trailing-14-day-average projection
  (wide ±50% band, flagged `low` confidence) when history is under 30 days
  — never fits Prophet on insufficient data.
- **Elasticity** requires at least 4 distinct historical price points and a
  statistically significant regression coefficient (p < 0.1) before
  classifying as elastic/inelastic; otherwise flags `unreliable_estimate`.
- **Anomaly detection** uses simple rolling z-score (threshold 2.5,
  configurable) rather than IsolationForest for this phase — swap in
  `anomaly_detection/model.py` if z-score proves too noisy on your real data.
- **Basket analysis and ABC classification** both run per-store AND
  globally (all-store combined), since patterns can differ by location.

## What to confirm / do before trusting this in production

1. **Wire backtest MAPE into `confidence_level`** for forecasting — right
   now it only reflects "did Prophet get enough history," not "is this
   forecast actually accurate." This is the single most important fix
   before forecasts drive real inventory decisions.
2. **Re-run all modules once real transaction data (not synthetic) is
   flowing** — several results above (basket rules, elasticity) are
   artifacts of synthetic data's lack of real behavioral correlation and
   should look meaningfully different, likely better, on real data.
3. **Add customer segmentation/churn once `customer_id` is populated** —
   these were intentionally skipped, not forgotten.
4. **Spot-check flagged anomalies** manually before wiring them into
   automated alerts — z-score flags statistical outliers, which isn't
   always the same as a "real" business anomaly worth acting on.
