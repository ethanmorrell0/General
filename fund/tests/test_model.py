"""Logic tests on synthetic random-walk prices (no network needed)."""
import json

import numpy as np
import pandas as pd
import pytest

from fund import backtest as B
from fund import config as C
from fund import model as M
from fund import run as R

IDS = [C.CORE["id"]] + [x["id"] for x in C.FACTORS + C.REGIONS + C.STOCKS + C.B_STOCKS] + [C.DEFENSIVE["gold"]["id"]]


def fake_prices(days=900, seed=1, drift=0.0003):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=days)
    data = {}
    for i, k in enumerate(IDS):
        mu = drift + rng.normal(0, 0.0003)
        r = rng.normal(mu, 0.012, days)
        data[k] = 100 * np.exp(np.cumsum(r))
    return pd.DataFrame(data, index=idx)


def test_allocation_is_whole_percent_and_sums_to_100():
    res = M.allocate(fake_prices())
    t = res["target"]
    assert sum(t.values()) == 100
    assert all(isinstance(v, int) and v > 0 for v in t.values())
    assert t[C.CORE["id"]] >= C.WEIGHTS["core"] * 0.85
    assert len(t) <= 11
    stocks = [k for k in t if k in {s["id"] for s in C.STOCKS}]
    assert len(stocks) <= 3


def test_crash_triggers_defensive_overlay():
    px = fake_prices()
    px.iloc[-80:] = px.iloc[-80:] * np.linspace(1, 0.7, 80)[:, None]
    res = M.allocate(px)
    assert res["regime"]["level"] == 2
    assert res["target"].get("CASH", 0) + res["target"].get("SGLN", 0) == C.DEFENSIVE_LEVELS[2]


def test_hold_when_nothing_changed():
    px = fake_prices()
    res = M.allocate(px)
    t = res["target"]
    go, reasons = M.decide(t, {k: float(v) for k, v in t.items()}, t, res["regime"]["level"], res["regime"]["level"])
    assert not go


def test_drift_triggers_rebalance():
    t = {"VWRP": 50, "IWFM": 50}
    go, reasons = M.decide(t, {"VWRP": 65.0, "IWFM": 35.0}, t, 0, 0)
    assert go and "well off target" in reasons[0]


def test_hysteresis_keeps_incumbent_stock():
    px = fake_prices()
    res = M.allocate(px)
    ranked = [s for s in res["stocks"] if s["eligible"]]
    if len(ranked) < 5:
        pytest.skip("not enough eligible names in this random sample")
    incumbent = ranked[4]  # rank 5: would not be picked fresh, but is inside keep_rank 8
    prev = dict(res["target"])
    prev.pop(ranked[0]["id"])  # we held ranks 2, 3 and 5 instead of 1, 2, 3
    prev[incumbent["id"]] = 5
    res2 = M.allocate(px, prev=prev)
    assert incumbent["id"] in res2["target"]
    assert ranked[0]["id"] not in res2["target"]


def test_backtest_runs_and_charges_costs():
    px = fake_prices(days=1000)
    sim = B.simulate(px, start="2021-03-01")
    m = B.metrics(sim)
    assert m["years"] > 1
    assert len(sim["rebalances"]) >= 1
    assert m["turnover_per_year"] < 10


def test_pie_b_allocation_respects_caps():
    from fund import model_b as MB
    res = MB.allocate_b(fake_prices())
    t = res["target"]
    assert sum(t.values()) == 100
    stocks = {k: v for k, v in t.items() if k != "CASH"}
    assert len(stocks) <= C.B["n"]
    assert all(C.B["min_w"] - 1 <= v <= C.B["max_w"] + 1 for v in stocks.values())
    sectors = {}
    for s in C.B_STOCKS:
        if s["id"] in stocks:
            sectors[s["sector"]] = sectors.get(s["sector"], 0) + stocks[s["id"]]
    assert max(sectors.values()) <= C.B["sector_cap"] + 15


def test_pie_b_goes_to_cash_in_a_crash():
    from fund import model_b as MB
    px = fake_prices()
    px.iloc[-120:] = px.iloc[-120:] * np.linspace(1, 0.5, 120)[:, None]
    assert MB.allocate_b(px)["target"].get("CASH", 0) >= 50


def test_daily_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(R, "DATA", tmp_path / "data.json")
    px = fake_prices()
    import fund.data as D
    monkeypatch.setattr(D, "download", lambda start="2014-01-01": (px.iloc[:-30], {"last_date": "x"}))
    e1 = R.daily("2023-06-01")
    assert e1["action"] == "REBALANCE"
    st = json.loads((tmp_path / "state.json").read_text())
    assert abs(st["nav"][-1]["A_val"] / (st["nav"][-1]["A_val"] + st["nav"][-1]["B_val"]) - 0.5) < 0.01
    assert R.daily("2023-06-02")["action"] == "NO_NEW_DATA"
    for i, n in enumerate(range(29, -1, -1)):
        monkeypatch.setattr(D, "download", lambda start="2014-01-01", n=n: (px.iloc[:len(px) - n], {"last_date": "y"}))
        e = R.daily(f"2023-07-{i + 1:02d}")
        assert e["action"] in {"HOLD", "REBALANCE"}
    R.annotate("Test note")
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["log"][-1]["note"] == "Test note"
    for p in ("A", "B"):
        assert sum(st["pies"][p]["target"].values()) == 100
    share = st["nav"][-1]["A_val"] / (st["nav"][-1]["A_val"] + st["nav"][-1]["B_val"])
    assert abs(share - 0.5) < 0.06
