# EdgeFinder: value betting dashboard

A local web app that finds bets where **bet365's price beats the true probability**, across English
football, NFL, NBA, college football, college basketball and other sports. It also builds the
**most profitable accumulator** for the filters you choose.

```bash
./run.sh            # first run creates .venv and .env, then open http://localhost:8000
```

With no API key it runs in **demo mode** on synthetic (but realistically priced) markets, so you can try
everything first. To go live:

1. Get a free key at [the-odds-api.com](https://the-odds-api.com) (500 credits/month).
2. Put it in `.env` as `ODDS_API_KEY=...` and restart.

## How it decides a bet is profitable

A bet is +EV when `fair probability × bet365 odds > 1`. The work is in getting the fair probability:

1. **Sharp market.** Pinnacle and the betting exchanges (Betfair, Matchbook, Smarkets) are the most
   accurate prices available. Their margin is removed with the **power method**, which strips more
   margin from longshots, as real books load it there.
2. **Consensus.** With no sharp book, the average de-vigged price of every available book is used.
3. **Models**, blended in by a weight you control (default 15% next to sharp prices, 40% next to soft
   ones, scaled down for teams with little data):
   - **Football:** a Dixon-Coles Poisson model with time-decayed attack/defence ratings. It's fitted on
     free [football-data.co.uk](https://www.football-data.co.uk) results for the whole English pyramid
     (Premier League → National League) together, so promoted/relegated sides link the divisions and cup
     ties are priced on one scale. Scottish and major European leagues are fitted the same way.
   - **NFL / NBA / NCAAF / NCAAB:** ridge-regularised offence/defence point ratings from ESPN results,
     with normal margin/total distributions for moneylines, spreads and totals.
   - Tennis, NHL, MLB, rugby and so on use market prices only.

### Where the bet365 price comes from

The free odds feed usually **doesn't carry bet365**, so each bet shows:

| Column | Meaning |
|---|---|
| **Fair** | Fair odds and probability |
| **bet365 price** | From the feed if available, otherwise **type in what bet365 shows**. The edge and stake then recalculate and the price is marked confirmed. Until you do, the edge uses an *estimate* (median of other UK/EU books, or best price; set this in Settings). |
| **Need ≥** | The minimum bet365 price for the bet to clear your minimum edge. This is the number to check. |
| **Stake** | Fractional Kelly stake, capped per bet |

### bet365 promotions

- **2 Goals Ahead Early Payout:** for match-result bets in eligible leagues, the dashboard calculates
  P(team goes 2 up but doesn't win) with a minute-by-minute goal model and adds it to the win chance.
  It's worth most on underdogs.
- **Acca Boost:** the bonus on winnings for football accas, using a table by number of legs (editable in
  Settings). **bet365 changes terms. Check the current table, eligible leagues and markets.**

## Acca builder

Choose the number of legs, sports, markets, odds range per leg, kick-off window, and whether to count
Acca Boost / 2-Up. A beam search over the priced selections (one leg per event) returns the three
accas with the highest **expected value**, with win chance, boosted payout and a Kelly stake (capped at 1%
of bankroll by default). If no +EV combination exists, it says so instead of pretending.

> Max-EV accas tend to be high variance: low win probability, big payouts. Fractional-Kelly staking
> keeps stakes small to match. Fewer legs means less variance.

## Free-tier credit budgeting

The Odds API charges `markets × regions` credits per sport, per refresh. The app:

- checks each competition's schedule first (free) and **skips sports with no games** in your look-ahead
  window;
- shows the cost of a refresh before you run it, and refuses to dip into a reserve (default 25);
- uses region `eu` only by default (it has Pinnacle and Betfair, so 1 credit per market). Add `uk` for
  UK books at double the cost;
- auto-refresh (off by default) spreads the remaining credits evenly until your monthly reset;
- loads player props and BTTS only **on demand per game** (Value bets → *Player props & BTTS*).

A full scan of English football + NFL + NBA + college is roughly 25 credits on default settings, so
the free tier supports about 18 full scans a month. That's one every other day, or daily scans of
just the sports you care about. The paid 20K plan ($30/month) removes the constraint. Update
*Monthly credits* in Settings if you upgrade.

## Bet tracker

Log bets from the value table or the acca builder (enter the odds you actually got), settle them
(won/lost/void/half-won/half-lost) and track profit, ROI and expected profit. It also tracks
**closing-line value (CLV)**: your price compared with the fair price at the last refresh before kick-off.
Consistently positive CLV is the best early sign that you're beating the market. Profit takes hundreds
of bets to tell you that.

## Project layout

```
app/
  main.py          FastAPI routes + auto-refresh loop
  service.py       refresh orchestration, analysis cache, props
  pricing.py       de-vig, model blend, bet365 price, edge, stake
  acca.py          accumulator optimiser
  maths.py         de-vig methods, Kelly, Asian-line settlement
  budget.py        Odds API credit budgeting
  odds_api.py      The Odds API client
  tracker.py       P&L, ROI, CLV
  demo.py          synthetic markets for demo mode
  models/          football (Dixon-Coles), ratings (US sports), early payout, hub
static/            dashboard (vanilla JS, no build step)
tests/             pytest suite, including the live path against mocked APIs
```

Run tests with `.venv/bin/python -m pytest`.

## Caveats

- No model or method guarantees profit. Edges of 2–5% are normal, and variance is large.
- bet365 limits accounts that consistently beat the closing line. Mixing in normal bets and sensible
  stake sizes helps.
- The 2-Up eligibility list and the Acca Boost table are hard-coded from bet365's published terms at the
  time of writing (`app/sports.py`, Settings). Keep them current.
- Please gamble responsibly. If it stops being fun, [BeGambleAware](https://www.begambleaware.org) and
  GamStop can help.
