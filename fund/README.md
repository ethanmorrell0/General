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

Dashboard: https://claude.ai/artifact/SYQuqVLu1G7sEACFyiJg3W

The dashboard is also where the fund's state lives: it publishes `state.json`
and `backtest.json` next to the page, and every run reads them back from there.
That way a scheduled or on-demand run needs no push access to this repo. The
Sync button calls the Claude Code Remote connector's `fire_trigger` for the
daily task (`trig_01PC31FgzjK37vrhh5kAgWyL`), which runs the routine below.

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

## Daily routine (what the 10:00 UK scheduled run and the Sync button do)

1. Get the code (read access is enough): `git fetch origin claude/active-equity-fund && git checkout claude/active-equity-fund && git pull origin claude/active-equity-fund`
2. `pip install -q yfinance pandas numpy`
3. Load the fund's state from the dashboard (if it can't be read, stop and report it;
   the repo holds no copy): `Artifact` action `read` with
   `url` https://claude.ai/artifact/SYQuqVLu1G7sEACFyiJg3W, then `read` again with
   `paths: ["state.json", "backtest.json"]` and copy the saved files over
   `fund/state/state.json` and `fund/state/backtest.json`. Never start from the
   repo's copy when the dashboard's can be read: the repo's is older.
4. `python -m fund.run daily`. If prices can't be downloaded, run
   `python -m fund.run unavailable "<one-line reason>"` instead. Runs use only
   completed trading days, so a 10am run decides on the previous close.
5. On the first run of a calendar month (or if `backtest.json` is missing), run
   `python -m fund.backtest`.
6. Read `fund/state/data.json`. Write a plain-English note of 2–6 short sentences
   covering both pies: each pie's decision and the signal behind it, any money to
   move between the pies, and anything worth watching. No hype, no predictions.
   Save it with `python -m fund.run annotate "<note>"`. Skip on a NO_DATA day.
   On a NO_NEW_DATA run (e.g. a second sync on the same day) write no note: the
   latest decision and its note stay as they are.
7. `python -m fund.export` (writes `fund/site/data.json`).
8. Publish: `Artifact` publish with `url` above, `file_path: fund/site/index.html`,
   `files: {"data.json": "fund/site/data.json", "state.json": "fund/state/state.json",
   "backtest.json": "fund/state/backtest.json"}`. Leave `capabilities` out (it keeps
   the Sync button's connector access). If the publish is refused because the
   dashboard changed since step 3, another run finished first: start again from
   step 3 once.
9. No git push is needed. Code changes are committed by hand from an interactive session.

## Backtest (2016 to 2026-10, after costs)

| Version | Vs All-World a year | Worst fall (index −25.0%) |
|---|---|---|
| Whole fund, fair test (stocks from the 30 largest companies of 2015) | +0.9% | −21.4% |
| Whole fund, as built (today's lists: hindsight, upper bound) | +4.7% | −24.0% |
| Pie A, fair test | +0.6% | −22.4% |
| Pie B on 2015 large caps | +1.1% | −20.9% |
| Pie B, as built | +6.8% | −26.7% |
| Pie A, ETFs only | −0.6% | −23.3% |
