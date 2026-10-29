"""Static configuration (from .env) and user-adjustable defaults (stored in the DB)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "betting.db"

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
ODDS_API_BASE = os.getenv("ODDS_API_BASE", "https://api.the-odds-api.com/v4")
# Demo mode generates synthetic markets so the dashboard works without a key.
DEMO_MODE = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes") or not ODDS_API_KEY

# Defaults for settings the user can change in the UI. "Conservative" risk profile.
DEFAULT_SETTINGS: dict = {
    "bankroll": 1000.0,
    "currency": "£",
    "kelly_fraction": 0.25,
    "max_stake_pct": 0.02,
    "min_edge": 0.03,
    # Price used for edge when bet365 isn't in the feed and you haven't typed one in:
    # "median" (typical soft-book price, closest to what bet365 usually offers) or "best".
    "proxy_price": "median",
    # Regions for the bulk odds call. "eu" carries Pinnacle + Betfair EU (the sharp core)
    # at 1 credit per market. Add "uk" for UK soft books (doubles the cost).
    "regions": "eu",
    "props_regions": "uk,eu",
    "markets_by_group": {
        "football_eng": ["h2h", "totals"],
        "nfl": ["h2h", "spreads", "totals"],
        "nba": ["h2h", "spreads", "totals"],
        "ncaaf": ["h2h", "spreads", "totals"],
        "ncaab": ["h2h", "spreads", "totals"],
        "other": ["h2h"],
    },
    # Model weight in the blend: when a sharp book prices the market vs when only soft books do.
    "model_weight_sharp": 0.15,
    "model_weight_soft": 0.40,
    # Credit budgeting (The Odds API free tier = 500/month).
    "monthly_credits": 500,
    "credit_reset_day": 1,
    "credit_reserve": 25,
    "auto_refresh": False,
    "refresh_interval_hours": 12,
    "refresh_groups": ["football_eng", "nfl", "nba"],
    "lookahead_hours": 72,
    # bet365 promotions — verify current terms on bet365, they change.
    "early_payout": True,
    "acca_boost": True,
    # legs -> boost on winnings (bet365 football Acca Boost table)
    "acca_boost_table": {"2": 0.05, "3": 0.10, "4": 0.15, "5": 0.20, "6": 0.25, "7": 0.30,
                         "8": 0.35, "9": 0.40, "10": 0.50, "11": 0.60, "12": 0.70, "13": 0.70, "14": 0.70},
    "acca_boost_min_leg_odds": 1.2,
    "acca_max_stake_pct": 0.01,
    "acca_default_legs": 3,
    # "Other" sports scanned by default (keys from The Odds API). Tennis keys are added
    # automatically for whichever tournaments are active.
    "other_sport_keys": [
        "soccer_spl", "soccer_germany_bundesliga", "soccer_spain_la_liga",
        "soccer_italy_serie_a", "soccer_france_ligue_one", "soccer_netherlands_eredivisie",
        "icehockey_nhl", "baseball_mlb", "rugbyleague_nrl", "rugbyunion_premiership",
    ],
    "include_active_tennis": True,
}
