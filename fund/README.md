# Active Equity Fund

A rules-based equity fund for a Trading 212 Stocks & Shares ISA, held as two
pies at 50/50:

- **Pie A** aims to beat the FTSE All-World index (VWRP, GBP) by 2 points a year.
- **Pie B** is pure stock selection for maximum long-term return, risk-capped:
  12 stocks from 160 global large, mid and small caps.

A daily run re-scores both pies on the previous close and suggests a rebalance
only when it is worth trading. Trades are worked out on the whole fund (netting
stocks both pies hold) and shown per pie. Money moves between the pies when
either drifts to 45% or 55%.

Dashboard: https://claude.ai/artifact/1Afb2GiezND8wugv4RnzGh

## Pie A mandate

| Sleeve | Weight | Rule |
|---|---|---|
| Core | 45% | VWRP. Spare slots from other sleeves fall back here. |
| Styles | 25% | Top 3 of Momentum / Quality / Value / Small-cap by 6–12m performance vs VWRP: 12 / 8 / 5%. |
| Regions | 15% | Top 2 of US / Europe / UK / Japan / EM / Asia-Pac by risk-adjusted momentum, above 200-day average: 8 / 7%. |
| Stocks | 15% | Top 3 of 30 quality large caps by risk-adjusted momentum, in an uptrend: 5% each. |
| Defensive | 0 or 15% | 15% out of equities only when both warning signs are on for VWRP (below 200-day average and down over 3 months). Gold (SGLN) if gold is trending, else cash. |

Weights are whole percentages so they go straight into T212 pie slices.

## Pie B mandate

- Score = 40% 12-1 month risk-adjusted momentum + 20% 6-month risk-adjusted
  momentum + 25% closeness to the 52-week high − 15% volatility (all ranked
  across the universe).
- New picks must be above their 200-day average with positive momentum; holdings
  stay while in the top 40 and no more than 10% below their 200-day average.
- Inverse-volatility weights, 4–15% per stock, at most 35% per sector.
- Unfilled slots (fewer than 12 qualifying stocks) are held as cash.
- Stocks whose recent volatility has collapsed to under 40% of normal (an agreed
  takeover pinning the price) are skipped.
- Trades less often than pie A: drift ≥ half a position's size or ≥15pp total,
  and 20 trading days between rebalances.

Buffers against over-trading: smoothed scores, incumbent bonuses, keep-ranks
(stocks top 8, regions top 4), slack on trend filters for holdings, a ±2% band
on the market warning signs, and 10 trading days between rebalances unless
market risk rises. Triggers: a holding enters/leaves, the warning level changes,
a position is ≥3pp and ≥25% of its size off target, or total drift ≥8pp.

## Code

- `config.py` — universe, weights, buffers, costs
- `data.py` — Yahoo Finance download, GBP conversion, pence fixes
- `model.py` — signals, allocation, triggers, costs
- `run.py` — daily run and state (`state/state.json`)
- `export.py` — dashboard data files (`state/dash/*.json`)
- `backtest.py` — walk-forward backtest of the same rules (`state/backtest.json`)
- `dashboard/index.html` — the dashboard page (stored in the artifact)
- `tests/` — logic tests on synthetic prices: `python -m pytest -q fund/tests`

## Daily routine (what the 07:00 scheduled session does)

1. `git fetch origin claude/active-equity-fund && git checkout claude/active-equity-fund && git pull origin claude/active-equity-fund`
2. `pip install -q yfinance pandas numpy`
3. `python -m fund.run daily`. If it fails because prices can't be downloaded,
   run `python -m fund.run unavailable "<one-line reason>"` instead.
4. On the first run of a calendar month (or if `fund/state/backtest.json` is missing
   and data is available), run `python -m fund.backtest`.
5. Read `fund/state/data.json` (latest entry, holdings, signals). Write a plain-English
   note of 2–5 short sentences: what the decision is and why, in terms a non-specialist
   follows (which holding changed and what signal moved it, or why nothing changed),
   and anything worth watching. No hype, no predictions. Save it with
   `python -m fund.run annotate "<note>"`.
6. `python -m fund.export`
7. Update the dashboard (artifact `https://claude.ai/artifact/1Afb2GiezND8wugv4RnzGh`):
   - `Artifact` action `read` on the url first.
   - For each of `summary`, `holdings`, `stats`, `signals`, `log`, `nav` (plus `bt_metrics`,
     `bt_curve` if the backtest ran): upload `fund/state/dash/<id>.json` with
     `Artifact` publish, `url`, `asset: true`, `file_path` (one call per file).
   - `ArtifactData` `get` each `datasets/<id>` for its version, then one `batch` of
     `update`s setting `source: {kind: "file", url: "<new /_blob/... url>", name: "<id>.json"}`
     and `updated: {at: <ISO now>, by: "Claude (fund engine)"}`, each with `if_version`.
   - Delete the previous assets listed in `fund/state/assets.json` (`Artifact` action
     `delete`, `url`, `path` = old id) and write the new ids into that file.
8. `git add fund/state && git commit -m "Fund run <date>: <decision>" && git push -u origin claude/active-equity-fund`

## Backtest (2016 to 2026-10, after costs)

| Version | Vs All-World a year | Worst fall (index −25.0%) |
|---|---|---|
| Whole fund, fair test (stocks from the 30 largest companies of 2015) | +0.9% | −21.4% |
| Whole fund, as built (today's lists: hindsight, upper bound) | +4.7% | −24.0% |
| Pie A, fair test | +0.6% | −22.4% |
| Pie B on 2015 large caps | +1.1% | −20.9% |
| Pie B, as built | +6.8% | −26.7% |
| Pie A, ETFs only | −0.6% | −23.3% |
