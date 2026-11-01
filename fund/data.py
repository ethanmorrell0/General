"""Price download. Returns one table of daily total-return prices in GBP,
one column per instrument id, on London trading days."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


class DataUnavailable(RuntimeError):
    pass


def _instruments(extra=()):
    items = [C.CORE] + C.FACTORS + C.REGIONS + C.STOCKS + [C.DEFENSIVE["gold"]] + list(extra)
    seen, out = set(), []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return out


def fix_pence(s: pd.Series) -> pd.Series:
    """Yahoo sometimes flips an LSE line between pence and pounds for a few days.
    Detect 100x jumps and put those days back on the series' usual scale."""
    s = s.dropna().copy()
    if s.empty:
        return s
    med = s.rolling(21, min_periods=1, center=True).median()
    ratio = s / med
    s[ratio > 30] = s[ratio > 30] / 100
    s[ratio < 1 / 30] = s[ratio < 1 / 30] * 100
    return s


def download(start: str = "2014-01-01", extra=()) -> tuple[pd.DataFrame, dict]:
    import yfinance as yf

    insts = _instruments(extra)
    all_tickers = sorted({t for it in insts for t in it["tickers"]} | {t for v in C.FX.values() for t in v})
    raw = yf.download(all_tickers, start=start, auto_adjust=True, progress=False, threads=True)
    if raw is None or raw.empty:
        raise DataUnavailable("Yahoo Finance returned no data")
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()

    def series(ticker):
        if ticker not in close.columns:
            return None
        s = close[ticker].dropna()
        return s if len(s) > 30 else None

    fx = {}
    for ccy, ts in C.FX.items():
        for t in ts:
            s = series(t)
            if s is not None:
                fx[ccy] = s
                break

    # Trading calendar: the longest history among the core's tickers
    # (VWRL goes back to 2012; VWRP only to 2019).
    core_s = None
    for t in C.CORE["tickers"]:
        s_ = series(t)
        if s_ is not None and (core_s is None or len(s_) > len(core_s)):
            core_s = s_
    if core_s is None:
        raise DataUnavailable("No price history for the All-World core holding")
    cal = core_s.index  # London trading days

    cols, used, missing = {}, {}, []
    for it in insts:
        # Pick the ticker with the longest history among the candidates.
        best = None
        for t in it["tickers"]:
            s = series(t)
            if s is not None and (best is None or s.index[0] < best[1].index[0] - pd.Timedelta(days=365)):
                best = (t, s)
        if best is None:
            missing.append(it["id"])
            continue
        t, s = best
        ccy = it.get("ccy", "GBp" if t.endswith(".L") else "USD")
        if t.endswith(".L"):
            s = fix_pence(s) / 100.0
        elif ccy in fx:
            s = s / fx[ccy].reindex(s.index).ffill()
        cols[it["id"]] = s
        used[it["id"]] = t

    # Other exchanges have different holidays: carry the last price forward a
    # few days, then keep London trading days only.
    px = pd.DataFrame(cols).sort_index().ffill(limit=5).reindex(cal)
    meta = {"tickers_used": used, "missing": missing, "last_date": str(px.index[-1].date())}
    return px, meta
