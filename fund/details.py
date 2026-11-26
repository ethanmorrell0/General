"""Per-instrument detail for the app's holding pages: returns over several periods,
risk stats, a one-year price line and a short company profile.

Stats come from the same GBP total-return prices the model uses. Profiles (business
summary, market value, P/E, dividend yield, beta) come from Yahoo Finance, cached for
30 days so a run fetches only a few at a time. Everything here is best effort: a
failure leaves the previous detail in place and never stops a run.
"""
from __future__ import annotations

import datetime as dt
import math
import re

import numpy as np
import pandas as pd

from . import config as C

# Plain descriptions for the funds in pie A (Yahoo's profiles for ETFs are thin).
ETF_ABOUT = {
    "VWRP": "Vanguard FTSE All-World: one fund holding about 4,000 large and mid-sized companies across developed and emerging markets, weighted by size. It's the fund's core and its benchmark.",
    "IWFV": "iShares MSCI World Value Factor: developed-market companies that look cheap against their earnings, sales and book value.",
    "IWFQ": "iShares MSCI World Quality Factor: developed-market companies with high profitability, stable earnings and low debt.",
    "IWFM": "iShares MSCI World Momentum Factor: developed-market companies whose shares have risen most strongly over the past 6 to 12 months.",
    "IWFS": "iShares MSCI World Mid-Cap Equal Weight (formerly the Size Factor fund): mid-sized developed-market companies, each held at an equal weight so the giants don't dominate.",
    "VUAG": "Vanguard S&P 500: the 500 largest US companies, weighted by size.",
    "VERG": "Vanguard FTSE Developed Europe ex UK: large and mid-sized companies across continental Europe.",
    "VUKG": "Vanguard FTSE 100: the 100 largest companies listed in London.",
    "VJPN": "Vanguard FTSE Japan: large and mid-sized Japanese companies.",
    "VFEG": "Vanguard FTSE Emerging Markets: large and mid-sized companies in China, India, Taiwan, Brazil and other emerging economies.",
    "VAPX": "Vanguard FTSE Developed Asia Pacific ex Japan: companies in Australia, Hong Kong, Singapore, South Korea and New Zealand.",
    "SGLN": "iShares Physical Gold: tracks the gold price, backed by gold bars held in vaults. Used only as a defensive holding when markets turn down.",
}

PERIODS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252}


def instrument_ids(state: dict) -> list[str]:
    """Everything the app can open: pie A's universe, held names, pie B's top candidates."""
    ids = [C.CORE["id"], C.DEFENSIVE["gold"]["id"]] + [x["id"] for x in C.FACTORS + C.REGIONS + C.STOCKS]
    for p in ("A", "B"):
        ids += [k for k in state.get("pies", {}).get(p, {}).get("target", {}) if k != "CASH"]
    ids += [r["id"] for r in (state.get("signals") or {}).get("B", [])[:40]]
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _stats(s: pd.Series) -> dict | None:
    s = s.dropna()
    if len(s) < 30:
        return None
    a = s.to_numpy(dtype=float)
    last = a[-1]
    out = {"price": float(last), "as_of": str(s.index[-1].date())}
    for k, n in PERIODS.items():
        out["r_" + k] = float(last / a[-1 - n] - 1) if len(a) > n else None
    ytd = s[s.index.year == s.index[-1].year]
    prev = s[s.index.year < s.index[-1].year]
    out["r_YTD"] = float(last / prev.iloc[-1] - 1) if len(prev) and len(ytd) else None
    out["r_3Y_pa"] = float((last / a[-757]) ** (1 / 3) - 1) if len(a) > 756 else None
    yr = a[-253:] if len(a) > 252 else a
    rets = np.diff(np.log(yr))
    out["vol_1y"] = float(np.std(rets) * math.sqrt(252)) if len(rets) > 20 else None
    peak = np.maximum.accumulate(yr)
    out["maxdd_1y"] = float((yr / peak - 1).min())
    out["from_high"] = float(last / yr.max() - 1)
    out["trend_200"] = float(last / a[-200:].mean() - 1) if len(a) >= 200 else None
    line = yr[::5].tolist() + ([yr[-1]] if (len(yr) - 1) % 5 else [])
    out["spark"] = [round(100 * v / line[0], 2) for v in line]
    return out


def compute(px: pd.DataFrame, ids: list[str]) -> dict:
    out = {}
    for i in ids:
        if i in px:
            st = _stats(px[i])
            if st:
                out[i] = st
    return out


def _yahoo_ticker(i: str) -> str | None:
    for s in C.B_STOCKS:
        if s["id"] == i:
            return s["ticker"]
    for s in C.STOCKS:
        if s["id"] == i:
            return s["tickers"][0]
    return None


def _short(text: str, limit: int = 360) -> str:
    sents = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    out = ""
    for x in sents:
        if len(out) + len(x) > limit and out:
            break
        out = (out + " " + x).strip()
    return out


def refresh_profiles(state: dict, ids: list[str], max_fetch: int = 25) -> None:
    """Fill state['profiles'] for stocks, oldest first, a few per run."""
    prof = state.setdefault("profiles", {})
    today = dt.date.today()
    held = {k for p in ("A", "B") for k in state.get("pies", {}).get(p, {}).get("target", {})}
    todo = []
    for i in ids:
        t = _yahoo_ticker(i)
        if not t:
            continue
        got = prof.get(t)
        if not got or (today - dt.date.fromisoformat(got.get("fetched", "2000-01-01"))).days > 30:
            todo.append((0 if i in held else 1, got.get("fetched", "") if got else "", t))
    todo.sort()
    try:
        import yfinance as yf
    except Exception:  # noqa: BLE001
        return
    for _, _, t in todo[:max_fetch]:
        try:
            info = yf.Ticker(t).info or {}
        except Exception:  # noqa: BLE001 - rate limits etc.: keep the old profile
            continue
        if not info:
            continue
        dy = info.get("trailingAnnualDividendYield")
        prof[t] = {
            "fetched": today.isoformat(),
            "about": _short(info.get("longBusinessSummary", "")),
            "industry": info.get("industry"),
            "country": info.get("country"),
            "market_cap": info.get("marketCap"),
            "currency": info.get("currency"),
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "div_yield": dy if isinstance(dy, (int, float)) and dy < 0.5 else None,
            "beta": info.get("beta"),
            "website": info.get("website"),
        }


def update(state: dict, px: pd.DataFrame, fetch_profiles: bool = True) -> None:
    """Called by every run that downloaded prices."""
    try:
        ids = instrument_ids(state)
        state["details"] = compute(px, ids)
        if fetch_profiles:
            refresh_profiles(state, ids)
    except Exception as e:  # noqa: BLE001 - detail pages are a nice-to-have
        state.setdefault("meta", {})["details_error"] = str(e)
