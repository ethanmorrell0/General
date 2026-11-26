"""Investment committee: a news, data and risk check on every buy before it reaches you.

The rules still choose the trades. The committee (three analyst agents, a reviewer and
a CIO, run by the scheduled session; see fund/committee/README.md) can only approve,
delay (7 days) or veto (30 days) a buy. It never picks stocks and never blocks a sale.

    python -m fund.committee needed          # prints "yes" when today's decision has unreviewed buys
    python -m fund.committee packet          # writes fund/state/review_packet.json for the analysts
    python -m fund.committee apply FILE      # applies the CIO's verdict (JSON) to the fund state
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as C
from .run import NAMES, SLEEVE, load_state, save, _value

PACKET = Path(__file__).parent / "state" / "review_packet.json"
DAYS = {"delay": 7, "veto": 30}

_A_ITEMS = {it["id"]: it for it in [C.CORE] + C.FACTORS + C.REGIONS + C.STOCKS + [C.DEFENSIVE["gold"]]}
_B_ITEMS = {s["id"]: s for s in C.B_STOCKS}


def _country(ticker: str, ccy: str) -> str:
    if ticker.endswith(".L"):
        return "UK"
    if ticker.endswith(".SW"):
        return "Switzerland"
    if any(ticker.endswith(x) for x in (".DE", ".PA", ".AS", ".MI", ".MC")):
        return "Europe"
    if ticker in ("TSM", "BABA", "PDD", "NU", "BIDU", "INFY", "SONY", "TM", "NVO", "SE", "MELI", "SHOP"):
        return "Non-US (US listing)"
    return "US"


def info(i: str) -> dict:
    """Everything an analyst needs to identify an instrument."""
    if i in _B_ITEMS:
        s = _B_ITEMS[i]
        return {"id": i, "pie": "B", "label": s["ticker"].split(".")[0], "yahoo": s["ticker"], "name": s["name"],
                "kind": "stock", "group": s["sector"], "currency": s["ccy"], "country": _country(s["ticker"], s["ccy"])}
    it = _A_ITEMS.get(i, {"id": i, "name": NAMES.get(i, i), "tickers": [i]})
    stock = any(s["id"] == i for s in C.STOCKS)
    t = it["tickers"][0]
    return {"id": i, "pie": "A", "label": i, "yahoo": t, "name": C.SHORT_NAMES.get(i) or it["name"],
            "full_name": it["name"], "kind": "stock" if stock else "ETF",
            "group": next((b["sector"] for b in C.B_STOCKS if b["ticker"] == t), SLEEVE.get(i, "")) if stock else SLEEVE.get(i, ""),
            "currency": it.get("ccy", "GBP" if t.endswith(".L") else "USD"),
            "country": _country(t, it.get("ccy", "")) if stock else "fund"}


def _latest(state):
    return state["log"][-1] if state["log"] else None


def _buys(entry) -> list[tuple[str, str, float]]:
    out = []
    for p in ("A", "B"):
        for k, v in (entry.get("pies", {}).get(p, {}).get("trades") or {}).items():
            if v > 0 and k != "CASH":
                out.append((p, k, v))
    return out


def needed() -> bool:
    st = load_state()
    e = _latest(st)
    if not e or e.get("action") != "REBALANCE" or not _buys(e):
        return False
    return not any(r.get("data_date") == e["data_date"] for r in st.get("reviews", []))


def _reserves(st, n=3) -> list[str]:
    tgt = st["pies"]["B"]["target"]
    excl = st.get("excluded", {})
    out = []
    for r in (st.get("signals") or {}).get("B", []):
        if r["id"] in tgt or r["id"] in excl or r.get("pinned") or not r.get("eligible"):
            continue
        out.append(r["id"])
        if len(out) >= n:
            break
    return out


def _data_checks(items: list[dict]) -> dict:
    """Sanity-check the price history behind each name (glitches, gaps, splits)."""
    try:
        import yfinance as yf
        tick = sorted({x["yahoo"] for x in items})
        raw = yf.download(tick, period="3mo", auto_adjust=True, progress=False)["Close"]
    except Exception as e:  # noqa: BLE001 - report, don't fail the run
        return {"error": f"price download failed: {e}"}
    out = {}
    for x in items:
        t = x["yahoo"]
        if t not in raw:
            out[x["id"]] = {"error": "no prices returned"}
            continue
        s = raw[t].dropna()
        s.index = s.index.tz_localize(None) if getattr(s.index, "tz", None) else s.index
        now = dt.datetime.now(ZoneInfo("Europe/London"))
        if len(s) and s.index[-1].date() >= now.date() and now.hour * 60 + now.minute < 21 * 60 + 30:
            s = s.iloc[:-1]  # today's row is intraday until the US close
        if s.empty:
            out[x["id"]] = {"error": "no prices returned"}
            continue
        r = s.pct_change().dropna()
        jumps = r[r.abs() > 0.15]
        same = int((s.diff() == 0).astype(int).groupby((s.diff() != 0).cumsum()).sum().max() or 0)
        out[x["id"]] = {
            "last_date": str(s.index[-1].date()), "days": int(len(s)), "last_price": round(float(s.iloc[-1]), 4),
            "max_daily_move": round(float(r.abs().max()), 4) if len(r) else None,
            "moves_over_15pct": {str(d.date()): round(float(v), 4) for d, v in jumps.items()},
            "possible_100x_glitch": bool(((s / s.rolling(5, min_periods=1).median()) > 30).any()),
            "longest_unchanged_run": same,
        }
    return out


def packet() -> Path:
    st = load_state()
    e = _latest(st)
    buys = _buys(e) if e else []
    sigA = (st.get("signals") or {}).get("A", {})
    sigB = {r["id"]: r for r in (st.get("signals") or {}).get("B", [])}
    sigA_all = {r["id"]: r for g in ("factors", "regions", "stocks") for r in sigA.get(g, [])}
    items = []
    for p, k, pts in buys:
        x = info(k)
        x.update({"pie": p, "role": "buy", "target_pct_of_pie": st["pies"][p]["target"].get(k),
                  "trade_pts_of_pie": pts, "signal": sigB.get(k) or sigA_all.get(k)})
        items.append(x)
    reserves = []
    for k in _reserves(st):
        x = info(k)
        x.update({"role": "reserve", "signal": sigB.get(k),
                  "note": "Next in line for pie B if a buy is vetoed. Review it like a buy."})
        reserves.append(x)
    # Whole-fund look at concentration, counting a company held in both pies once.
    weights = {}
    for p in ("A", "B"):
        for k, w in st["pies"][p]["target"].items():
            x = info(k) if k != "CASH" else {"name": "Cash", "kind": "cash", "group": "Cash", "country": "-"}
            key = x["name"]
            weights.setdefault(key, {"name": key, "kind": x["kind"], "group": x["group"], "country": x["country"], "pct_of_fund": 0.0})
            weights[key]["pct_of_fund"] += w * C.SPLIT[p] / 100
    sectors, countries = {}, {}
    for w in weights.values():
        if w["kind"] == "stock":
            sectors[w["group"]] = sectors.get(w["group"], 0) + w["pct_of_fund"]
            countries[w["country"]] = countries.get(w["country"], 0) + w["pct_of_fund"]
    pk = {
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_date": e["data_date"] if e else None,
        "decision": e.get("action") if e else None,
        "market": sigA.get("regime"),
        "buys": items,
        "reserves": reserves,
        "fund_holdings": sorted(weights.values(), key=lambda w: -w["pct_of_fund"]),
        "stock_sector_pct_of_fund": {k: round(v, 2) for k, v in sorted(sectors.items(), key=lambda x: -x[1])},
        "stock_country_pct_of_fund": {k: round(v, 2) for k, v in sorted(countries.items(), key=lambda x: -x[1])},
        "data_checks": _data_checks(items + reserves),
        "currently_excluded": st.get("excluded", {}),
    }
    PACKET.write_text(json.dumps(pk, indent=1, default=float))
    return PACKET


def _add_days(d: str, n: int) -> str:
    return (dt.date.fromisoformat(d) + dt.timedelta(days=n)).isoformat()


def apply(path: str) -> dict:
    """Apply the CIO's verdict. Vetoed or delayed buys are swapped: pie A's go to
    its All-World core, pie B's to the first approved reserve (else cash)."""
    v = json.loads(Path(path).read_text())
    st = load_state()
    e = _latest(st)
    if not e or v.get("data_date") != e.get("data_date"):
        raise SystemExit(f"Verdict is for {v.get('data_date')}, latest decision is {e and e.get('data_date')}")
    decisions = {it["id"]: it for it in v.get("items", [])}
    prices = st.get("last_prices", {})
    used, changes = set(), []
    st.setdefault("excluded", {})
    for p, k, _ in _buys(e):
        d = decisions.get(k, {}).get("decision", "approve")
        if d not in DAYS:
            continue
        pie = st["pies"][p]
        if k not in pie["units"] or k not in prices:
            continue
        w = pie["target"].pop(k, 0)
        value = pie["units"].pop(k) * prices[k]
        dest = "CASH"
        if p == "A":
            dest = C.CORE["id"]
        else:
            for r in _reserves(st, 10):
                if r in used or decisions.get(r, {}).get("decision") != "approve" or r not in prices:
                    continue
                dest = r
                break
        used.add(dest)
        pie["target"][dest] = pie["target"].get(dest, 0) + w
        price = 1.0 if dest == "CASH" else prices[dest]
        pie["units"][dest] = pie["units"].get(dest, 0) + value / price
        tr = e["pies"][p]["trades"]
        tr[dest] = round(tr.pop(k, 0) + tr.get(dest, 0), 2)
        st["excluded"][k] = {"decision": d, "until": _add_days(e["data_date"], DAYS[d]),
                             "reason": decisions[k].get("reason", "")}
        changes.append({"pie": p, "out": k, "out_name": info(k)["name"], "in": dest,
                        "in_name": "Cash" if dest == "CASH" else info(dest)["name"], "pct_of_pie": w, "decision": d})
    # Vetoed or delayed reserves (and anything else not swapped above) are excluded too,
    # so the rules can't pick them up on a later day.
    for k, it in decisions.items():
        d = it.get("decision")
        if d in DAYS and k not in st["excluded"]:
            st["excluded"][k] = {"decision": d, "until": _add_days(e["data_date"], DAYS[d]), "reason": it.get("reason", "")}
    for p in ("A", "B"):
        pie = st["pies"][p]
        total = _value(pie["units"], {**prices, "CASH": 1.0}) if pie["units"] else 0
        if total:
            pie["current"] = {k: round(100 * u * (1.0 if k == "CASH" else prices[k]) / total, 2) for k, u in pie["units"].items()}
    review = {
        "data_date": e["data_date"], "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": v.get("summary", ""), "items": v.get("items", []), "flags": v.get("flags", []), "changes": changes,
    }
    st["reviews"] = (st.get("reviews", []) + [review])[-50:]
    e["committee"] = {"approved": sum(1 for it in v.get("items", []) if it.get("decision") == "approve"),
                      "changes": changes, "summary": review["summary"]}
    save(st)
    return review


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "needed":
        print("yes" if needed() else "no")
    elif cmd == "packet":
        print(packet())
    elif cmd == "apply" and len(sys.argv) > 2:
        print(json.dumps(apply(sys.argv[2]), indent=1))
    else:
        raise SystemExit(__doc__)
