"""Plain-English reasoning for each instrument's detail page, built from the
model's own signals and the committee's latest check."""
from __future__ import annotations

from . import config as C
from .details import ETF_ABOUT

FACTOR_IDS = {f["id"] for f in C.FACTORS}
REGION_IDS = {r["id"] for r in C.REGIONS}
A_STOCK_IDS = {s["id"] for s in C.STOCKS}


def _p(v, signed=True):
    if v is None:
        return "n/a"
    return f"{v:+.1%}" if signed else f"{v:.1%}"


def _find(rows, i):
    return next((r for r in rows if r.get("id") == i), None)


def logic(i: str, st: dict) -> str:
    sig = st.get("signals") or {}
    A, B = sig.get("A") or {}, sig.get("B") or []
    ta, tb = st["pies"]["A"]["target"], st["pies"]["B"]["target"]
    excl = st.get("excluded", {}).get(i)
    regime = A.get("regime") or {}
    cash_note = ""
    if ta.get("CASH"):
        cash_note = f" Two market warning signs are on, so {ta['CASH']}% of pie A sits in cash, trimmed evenly from every holding."

    if i == C.CORE["id"]:
        return ("The fund's core, always held. Pie A starts with 45% here, and any style or region slot the "
                f"rules can't fill also falls back to it, so today it's {ta.get(i, 0)}% of pie A." + cash_note)

    if i == C.DEFENSIVE["gold"]["id"]:
        g = regime.get("gold_trend")
        return ("Only bought when two market warning signs are on and gold itself is in an uptrend. "
                f"Gold is {_p(g)} against its 200-day average, so " +
                ("it holds part of the defensive slice." if ta.get(i) else "the defensive slice goes to cash instead."))

    if i in FACTOR_IDS:
        r = _find(A.get("factors", []), i)
        if not r:
            return "Not enough price history to rank this style yet."
        held = ta.get(i, 0)
        verdict = (f"so it holds {held}% of pie A." if held else
                   "only the top three styles are held (12%, 8% and 5%), so it sits this one out.")
        return (f"Style ranked #{r['rank']} of {len(A.get('factors', []))}. Over the blended 6-to-12-month window it has "
                f"done {_p(r.get('mom'))} relative to the All-World index; the top three styles get 12%, 8% and 5%, "
                + verdict + (cash_note if held else ""))

    if i in REGION_IDS:
        r = _find(A.get("regions", []), i)
        if not r:
            return "Not enough price history to rank this region yet."
        held = ta.get(i, 0)
        trend = f"It is {_p(r.get('trend'))} against its 200-day average"
        if held:
            tail = f", one of the two strongest regions in an uptrend, so it gets {held}% of pie A." + cash_note
        elif not r.get("eligible"):
            tail = ", so it isn't in an uptrend and the model won't buy it; the slot stays in the All-World core."
        else:
            tail = ", which qualifies, but two stronger regions have the slots."
        return (f"Region ranked #{r['rank']} of {len(A.get('regions', []))} on risk-adjusted momentum "
                f"({_p(r.get('mom'))} blended 6-to-12-month return). " + trend + tail)

    if i in A_STOCK_IDS:
        r = _find(A.get("stocks", []), i)
        if not r:
            return "Ranked outside the top 12 of pie A's quality large caps."
        held = ta.get(i, 0)
        base = (f"Ranked #{r['rank']} of pie A's 30 quality large caps on risk-adjusted momentum "
                f"({_p(r.get('mom'))} blended 6-to-12-month return, {_p(r.get('vol'), False)} volatility). "
                f"It is {_p(r.get('trend'))} against its 200-day average. ")
        if excl:
            return base + f"The committee {excl['decision']}ed it until {excl['until']}: {excl['reason']}"
        if held:
            return base + f"The top three in an uptrend get 5% each, so it holds {held}% of pie A." + cash_note
        if not r.get("eligible"):
            return base + "New buys need an uptrend, so it waits."
        return base + "It qualifies, but three stronger names hold the slots; a holding is only replaced once it drops out of the top 8."

    if i.startswith("B_"):
        r = _find(B, i)
        if not r:
            return "Ranked outside pie B's top 40, so the model isn't considering it."
        held = tb.get(i, 0)
        base = (f"Ranked #{r['rank']} of pie B's ~158 stocks. The score blends 12-month (40%) and 6-month (20%) "
                f"risk-adjusted momentum, closeness to the 52-week high (25%) and low volatility (15%). Here: "
                f"12-month momentum {_p(r.get('mom12'))}, 6-month {_p(r.get('mom6'))}, {_p(r.get('high52'))} from its "
                f"52-week high, {_p(r.get('vol'), False)} volatility, {_p(r.get('trend'))} against its 200-day average. ")
        if r.get("pinned"):
            return base + "Skipped: its price is pinned by an agreed takeover, so there's little upside left."
        if excl or r.get("vetoed"):
            e = excl or {}
            return base + f"The committee {e.get('decision', 'vet')}ed it until {e.get('until', '?')}: {e.get('reason', '')}"
        if held:
            return base + (f"Held at {held}% of pie B. Steadier stocks get bigger slices (weights are inverse to "
                           "volatility, 4% to 15% each, at most 35% in one sector).")
        if not r.get("eligible"):
            return base + "New buys need an uptrend and positive momentum, so it waits."
        return base + "It qualifies but sits outside the 12 slots (or its sector is full). It's next in line if a holding drops out."

    return ETF_ABOUT.get(i, "")


def committee_check(i: str, st: dict) -> dict | None:
    for rv in reversed(st.get("reviews", [])):
        it = next((x for x in rv.get("items", []) if x.get("id") == i), None)
        if it:
            return {"date": rv["data_date"], "decision": it.get("decision"), "reason": it.get("reason", ""),
                    "sources": [u for u in it.get("sources", []) if str(u).startswith("http")][:3]}
    return None
