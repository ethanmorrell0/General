"""Sport catalogue: maps The Odds API sport keys to the dashboard's groups."""
from __future__ import annotations

GROUPS = {
    "football_eng": "Football (England)",
    "nfl": "NFL",
    "nba": "NBA",
    "ncaaf": "College Football",
    "ncaab": "College Basketball",
    "other": "Other",
}

FIXED_GROUP = {
    "americanfootball_nfl": "nfl",
    "basketball_nba": "nba",
    "americanfootball_ncaaf": "ncaaf",
    "basketball_ncaab": "ncaab",
}

# Default English keys, used when the live sports list hasn't been fetched yet.
ENGLISH_KEYS = [
    "soccer_epl", "soccer_efl_champ", "soccer_england_league1", "soccer_england_league2",
    "soccer_fa_cup", "soccer_england_efl_cup", "soccer_england_national_league",
]

# Competitions where bet365's "2 Goals Ahead Early Payout" applies (pre-match full-time result).
# Check bet365's current list — it changes.
EARLY_PAYOUT_KEYS = {
    "soccer_epl", "soccer_efl_champ", "soccer_england_league1", "soccer_england_league2",
    "soccer_spl", "soccer_germany_bundesliga", "soccer_spain_la_liga", "soccer_italy_serie_a",
    "soccer_france_ligue_one", "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_fa_cup", "soccer_england_efl_cup",
}


def is_english_football(key: str) -> bool:
    return key in ENGLISH_KEYS or key.startswith("soccer_england") or key in ("soccer_fa_cup", "soccer_efl_champ", "soccer_epl")


def group_for(key: str) -> str:
    if key in FIXED_GROUP:
        return FIXED_GROUP[key]
    if is_english_football(key):
        return "football_eng"
    return "other"


def is_football(key: str) -> bool:
    return key.startswith("soccer_")


def three_way(key: str) -> bool:
    """Soccer h2h has a draw outcome."""
    return is_football(key)


def scan_keys(group: str, settings: dict, live_sports: list[dict] | None) -> list[str]:
    """Sport keys to fetch for a group, filtered to in-season keys when the live list is known."""
    active = None
    if live_sports:
        active = {s["key"] for s in live_sports if s.get("active") and not s.get("has_outrights")}
    if group in ("nfl", "nba", "ncaaf", "ncaab"):
        keys = [k for k, g in FIXED_GROUP.items() if g == group]
    elif group == "football_eng":
        keys = list(ENGLISH_KEYS)
        if active:
            keys = sorted({k for k in active if is_english_football(k)} | (set(keys) & active))
    else:
        keys = list(settings.get("other_sport_keys", []))
        if active and settings.get("include_active_tennis", True):
            keys += sorted(k for k in active if k.startswith("tennis_"))
    if active is not None:
        keys = [k for k in keys if k in active]
    return keys
