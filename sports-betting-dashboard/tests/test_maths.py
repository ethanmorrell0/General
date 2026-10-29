import pytest

from app import maths


def test_devig_power_sums_to_one_and_shades_longshot():
    prices = [1.50, 4.20, 6.50]
    fair = maths.devig_power(prices)
    assert sum(fair) == pytest.approx(1.0)
    mult = maths.devig_multiplicative(prices)
    # Power method takes relatively more margin off the longshot.
    assert fair[2] < mult[2]
    assert fair[0] > mult[0]


def test_devig_no_margin_is_identity():
    assert maths.devig_power([2.0, 2.0]) == pytest.approx([0.5, 0.5])


def test_kelly_and_stake():
    assert maths.kelly_fraction(0.5, 2.2) == pytest.approx(0.1 / 1.2)
    assert maths.kelly_fraction(0.4, 2.0) == 0.0
    # quarter Kelly of 1000 = 20.83 -> capped at 2% = 20
    assert maths.stake(0.5, 2.2, 1000, 0.25, 0.02) == 20.0
    assert maths.stake(0.5, 2.2, 1000, 0.25, 0.5) == 20.5


def test_min_price_for_edge():
    assert maths.min_price_for_edge(0.5, 0.03) == pytest.approx(2.06)


def test_quarter_line_split():
    assert maths.split_quarter(-0.75) == [-1.0, -0.5]
    assert maths.split_quarter(0.25) == [0.0, 0.5]
    assert maths.split_quarter(-1.5) == [-1.5]


def test_asian_handicap_settlement():
    dist = {-1: 0.3, 0: 0.3, 1: 0.25, 2: 0.15}
    # -0.25: win if margin>=1, half-loss on draw, loss otherwise
    w, hw, push, hl, l = maths.settle_margin(dist, -0.25)
    assert (w, hw, push, hl, l) == pytest.approx((0.4, 0.0, 0.0, 0.3, 0.3))
    # -1.0: win by 2+, push on 1, lose otherwise
    w, hw, push, hl, l = maths.settle_margin(dist, -1.0)
    assert (w, push, l) == pytest.approx((0.15, 0.25, 0.6))


def test_effective_prob_breakeven():
    # Win 40%, push 20%, lose 40% -> break-even at evens -> p_eff 0.5
    assert maths.effective_prob(0.4, 0, 0.2, 0, 0.4) == pytest.approx(0.5)
