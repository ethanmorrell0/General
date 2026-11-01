"""Fund mandate and investable universe.

Every instrument lists Yahoo Finance tickers in order of preference. The engine
uses the first ticker that returns enough price history, so a delisted or
renamed line falls back to an alternative instead of breaking the run.
"""

BENCHMARK = {"id": "VWRP", "name": "Vanguard FTSE All-World (Acc)", "tickers": ["VWRP.L", "VWRL.L"]}

# Core holding: the benchmark itself. Everything else is a tilt away from it.
CORE = {"id": "VWRP", "name": "Vanguard FTSE All-World (Acc)", "tickers": ["VWRP.L", "VWRL.L"]}

# Style tilts. Ranked by how they have done relative to the All-World index.
FACTORS = [
    {"id": "IWFM", "name": "iShares MSCI World Momentum Factor", "style": "Momentum", "tickers": ["IWFM.L", "IWMO.L"]},
    {"id": "IWFQ", "name": "iShares MSCI World Quality Factor", "style": "Quality", "tickers": ["IWFQ.L", "IWQU.L"]},
    {"id": "IWFV", "name": "iShares MSCI World Value Factor", "style": "Value", "tickers": ["IWFV.L", "IWVL.L"]},
    {"id": "IWFS", "name": "iShares MSCI World Size Factor", "style": "Small-cap", "tickers": ["IWFS.L", "IWSZ.L"]},
]

# Regional tilts. The strongest trending regions get an overweight.
REGIONS = [
    {"id": "VUAG", "name": "Vanguard S&P 500 (Acc)", "region": "US", "tickers": ["VUAG.L", "VUSA.L"]},
    {"id": "VERG", "name": "Vanguard FTSE Dev. Europe ex UK (Acc)", "region": "Europe ex UK", "tickers": ["VERG.L", "VERX.L"]},
    {"id": "VUKG", "name": "Vanguard FTSE 100 (Acc)", "region": "UK", "tickers": ["VUKG.L", "VUKE.L"]},
    {"id": "VJPN", "name": "Vanguard FTSE Japan", "region": "Japan", "tickers": ["VJPB.L", "VJPN.L"]},
    {"id": "VFEG", "name": "Vanguard FTSE Emerging Markets (Acc)", "region": "Emerging markets", "tickers": ["VFEG.L", "VFEM.L"]},
    {"id": "VAPX", "name": "Vanguard FTSE Dev. Asia Pacific ex Japan", "region": "Asia Pacific ex Japan", "tickers": ["VAPX.L"]},
]

# Stock sleeve candidates: large, profitable, durable businesses (the quality
# screen). The engine picks among them on momentum and trend each day.
# ccy: currency the Yahoo price is quoted in. GBp = pence.
STOCKS = [
    {"id": "MSFT", "name": "Microsoft", "tickers": ["MSFT"], "ccy": "USD"},
    {"id": "AAPL", "name": "Apple", "tickers": ["AAPL"], "ccy": "USD"},
    {"id": "NVDA", "name": "Nvidia", "tickers": ["NVDA"], "ccy": "USD"},
    {"id": "GOOGL", "name": "Alphabet", "tickers": ["GOOGL"], "ccy": "USD"},
    {"id": "AMZN", "name": "Amazon", "tickers": ["AMZN"], "ccy": "USD"},
    {"id": "META", "name": "Meta Platforms", "tickers": ["META"], "ccy": "USD"},
    {"id": "AVGO", "name": "Broadcom", "tickers": ["AVGO"], "ccy": "USD"},
    {"id": "TSM", "name": "Taiwan Semiconductor (ADR)", "tickers": ["TSM"], "ccy": "USD"},
    {"id": "V", "name": "Visa", "tickers": ["V"], "ccy": "USD"},
    {"id": "MA", "name": "Mastercard", "tickers": ["MA"], "ccy": "USD"},
    {"id": "LLY", "name": "Eli Lilly", "tickers": ["LLY"], "ccy": "USD"},
    {"id": "COST", "name": "Costco", "tickers": ["COST"], "ccy": "USD"},
    {"id": "JPM", "name": "JPMorgan Chase", "tickers": ["JPM"], "ccy": "USD"},
    {"id": "BRK-B", "name": "Berkshire Hathaway B", "tickers": ["BRK-B"], "ccy": "USD"},
    {"id": "UNH", "name": "UnitedHealth", "tickers": ["UNH"], "ccy": "USD"},
    {"id": "ISRG", "name": "Intuitive Surgical", "tickers": ["ISRG"], "ccy": "USD"},
    {"id": "SPGI", "name": "S&P Global", "tickers": ["SPGI"], "ccy": "USD"},
    {"id": "NFLX", "name": "Netflix", "tickers": ["NFLX"], "ccy": "USD"},
    {"id": "ASML", "name": "ASML", "tickers": ["ASML.AS", "ASML"], "ccy": "EUR"},
    {"id": "SAP", "name": "SAP", "tickers": ["SAP.DE", "SAP"], "ccy": "EUR"},
    {"id": "MC", "name": "LVMH", "tickers": ["MC.PA"], "ccy": "EUR"},
    {"id": "RMS", "name": "Hermes", "tickers": ["RMS.PA"], "ccy": "EUR"},
    {"id": "SU", "name": "Schneider Electric", "tickers": ["SU.PA"], "ccy": "EUR"},
    {"id": "NOVO", "name": "Novo Nordisk (ADR)", "tickers": ["NVO"], "ccy": "USD"},
    {"id": "AZN", "name": "AstraZeneca", "tickers": ["AZN.L"], "ccy": "GBp"},
    {"id": "RELX", "name": "RELX", "tickers": ["REL.L"], "ccy": "GBp"},
    {"id": "LSEG", "name": "London Stock Exchange Group", "tickers": ["LSEG.L"], "ccy": "GBp"},
    {"id": "ULVR", "name": "Unilever", "tickers": ["ULVR.L"], "ccy": "GBp"},
    {"id": "RR", "name": "Rolls-Royce", "tickers": ["RR.L"], "ccy": "GBp"},
    {"id": "BA", "name": "BAE Systems", "tickers": ["BA.L"], "ccy": "GBp"},
]

DEFENSIVE = {
    "gold": {"id": "SGLN", "name": "iShares Physical Gold", "tickers": ["SGLN.L", "SGLP.L"]},
    # Cash sits uninvested in the ISA, where Trading 212 pays interest on it.
    "cash": {"id": "CASH", "name": "Cash (uninvested, earns T212 interest)"},
}

FX = {"USD": ["GBPUSD=X"], "EUR": ["GBPEUR=X"], "CHF": ["GBPCHF=X"], "JPY": ["GBPJPY=X"]}

# Target weights in whole percent, so they can be typed straight into a T212 pie.
WEIGHTS = {
    "core": 45,
    "factors": [12, 8, 5],      # top 3 styles, by rank; 4th style gets nothing
    "regions": [8, 7],          # top 2 trending regions
    "stocks": [5, 5, 5],        # top 3 stocks, equal weight
}

# Defensive overlay on the All-World index (in GBP).
# Number of warning signs -> % out of equities. One sign alone is usually a
# false alarm (in the 2016-2026 backtest it cost about 0.15%/yr), so only
# both together move money out.
DEFENSIVE_LEVELS = {0: 0, 1: 0, 2: 15}
GOLD_SHARE_WHEN_TRENDING = 2 / 3         # rest of the defensive slice goes to cash

# Hysteresis: a holding stays until it drops below these ranks, so the fund
# does not churn on small changes in the rankings.
KEEP_RANK = {"regions": 4, "stocks": 8}

# Buffers that stop the fund trading on noise.
REGIME_BAND = {"sma": 0.02, "ret": 0.03}   # warnings switch on/off only past these margins
HOLD_SLACK = {"trend": 0.03, "mom": 0.02}  # holdings may dip this far below the entry rules
FACTOR_STICKINESS = 0.05                   # score bonus for the current top style (scaled by weight)

MIN_DAYS_BETWEEN = 10  # trading days between rebalances, unless market risk rises

# Daily rebalance triggers.
TRIGGER = {
    "position_gap_pp": 3.0,   # a holding is 3+ percentage points from its target...
    "position_gap_rel": 0.25, # ...and at least 25% of its own size away
    "total_gap_pp": 8.0,      # or the sum of all gaps is 8+ points (4% of the fund one way)
}

# Trading costs used in the backtest (one-way, as a fraction of the amount traded).
COSTS = {"etf": 0.0010, "usd_eur_stock": 0.0025, "uk_stock": 0.0060}

LOOKBACK = {"mom_long": 252, "skip": 21, "mom_short": 126, "vol": 63, "sma": 200, "regime_ret": 63}
SMOOTH_LAGS = [0, 5, 10, 15, 20]  # scores are averaged over these day-lags to cut noise

# Backtest only: roughly the 30 largest listed companies at the end of 2015,
# including later laggards (GE, Exxon, Intel, HSBC...). Picking from this list
# avoids the hindsight in today's STOCKS list. Delisted names are left out.
BACKTEST_STOCKS_2015 = [
    {"id": f"PIT_{t}", "name": n, "tickers": [t], "ccy": c} for t, n, c in [
        ("AAPL", "Apple", "USD"), ("GOOGL", "Alphabet", "USD"), ("MSFT", "Microsoft", "USD"),
        ("BRK-B", "Berkshire Hathaway", "USD"), ("XOM", "Exxon Mobil", "USD"), ("AMZN", "Amazon", "USD"),
        ("META", "Meta", "USD"), ("JNJ", "Johnson & Johnson", "USD"), ("GE", "General Electric", "USD"),
        ("WFC", "Wells Fargo", "USD"), ("JPM", "JPMorgan", "USD"), ("PG", "Procter & Gamble", "USD"),
        ("WMT", "Walmart", "USD"), ("VZ", "Verizon", "USD"), ("T", "AT&T", "USD"), ("PFE", "Pfizer", "USD"),
        ("KO", "Coca-Cola", "USD"), ("CVX", "Chevron", "USD"), ("INTC", "Intel", "USD"), ("ORCL", "Oracle", "USD"),
        ("BABA", "Alibaba", "USD"), ("TSM", "TSMC", "USD"), ("NESN.SW", "Nestle", "CHF"), ("NOVN.SW", "Novartis", "CHF"),
        ("7203.T", "Toyota", "JPY"), ("HSBA.L", "HSBC", "GBp"), ("BP.L", "BP", "GBp"), ("SHEL.L", "Shell", "GBp"),
        ("MC.PA", "LVMH", "EUR"),
    ]
]
