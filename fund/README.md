# Active Equity Fund

A rules-based global equity portfolio for a Trading 212 Stocks & Shares ISA,
aiming to beat the FTSE All-World index (VWRP, GBP) by 2 points a year over the
long run. A daily run re-scores everything on the previous close and suggests a
rebalance only when it is worth trading.

Dashboard: https://claude.ai/artifact/1Afb2GiezND8wugv4RnzGh

## Mandate

| Sleeve | Weight | Rule |
|---|---|---|
| Core | 45% | VWRP. Spare slots from other sleeves fall back here. |
| Styles | 25% | Top 3 of Momentum / Quality / Value / Small-cap by 6–12m performance vs VWRP: 12 / 8 / 5%. |
| Regions | 15% | Top 2 of US / Europe / UK / Japan / EM / Asia-Pac by risk-adjusted momentum, above 200-day average: 8 / 7%. |
| Stocks | 15% | Top 3 of 30 quality large caps by risk-adjusted momentum, in an uptrend: 5% each. |
| Defensive | 0–15% | 0 / 7 / 15% out of equities for 0 / 1 / 2 warning signs on VWRP (below 200-day average; down over 3 months). Gold (SGLN) if gold is trending, else cash. |

Weights are whole percentages so they go straight into T212 pie slices.

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
   - For each of `summary`, `holdings`, `signals`, `log`, `nav` (plus `bt_metrics`,
     `bt_curve` if the backtest ran): upload `fund/state/dash/<id>.json` with
     `Artifact` publish, `url`, `asset: true`, `file_path` (one call per file).
   - `ArtifactData` `get` each `datasets/<id>` for its version, then one `batch` of
     `update`s setting `source: {kind: "file", url: "<new /_blob/... url>", name: "<id>.json"}`
     and `updated: {at: <ISO now>, by: "Claude (fund engine)"}`, each with `if_version`.
   - Delete the previous assets listed in `fund/state/assets.json` (`Artifact` action
     `delete`, `url`, `path` = old id) and write the new ids into that file.
8. `git add fund/state && git commit -m "Fund run <date>: <decision>" && git push -u origin claude/active-equity-fund`
