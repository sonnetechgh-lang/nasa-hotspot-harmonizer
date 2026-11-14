import numpy as np
import pandas as pd
import pytest

from pipeline import forecast as fc


def monthly(years=range(2001, 2026), level=None, fy_month=7):
    """Monthly series with a fixed seasonal shape (peak in January), scaled per fire year."""
    idx = pd.date_range(f"{min(years)}-01-01", f"{max(years)}-12-01", freq="MS")
    shape = np.array([30, 20, 8, 3, 1, 0, 0, 0, 0, 1, 10, 25], float)   # Jan..Dec
    fy = np.where(idx.month >= fy_month, idx.year, idx.year - 1)
    lv = np.array([level(y) if level else 1.0 for y in fy])
    return pd.Series(shape[idx.month - 1] * 10 * lv, index=idx)


def test_theil_sen_ignores_one_outlier():
    x = np.arange(10.0)
    y = 2 * x + 1
    y[5] = 100
    b, a = fc.theil_sen(x, y)
    assert b == pytest.approx(2) and a == pytest.approx(1)


def test_mann_kendall_detects_trend_and_not_noise():
    assert fc.mann_kendall_p(np.arange(15.0)) < 0.001
    assert fc.mann_kendall_p(np.array([5, 3, 6, 2, 7, 4, 5, 3, 6, 4, 5, 3, 6, 4, 5.0])) > 0.3


def test_lag_mean_matches_same_months_in_previous_years():
    s = monthly()
    m = fc.Monthly(s)
    origin = m.t("2020-10-01")
    got = m.recent_mean(origin, 3)
    targets = pd.date_range("2020-11-01", periods=12, freq="MS")
    want = [np.mean([s[t - pd.DateOffset(years=k)] for k in (1, 2, 3)]) for t in targets]
    assert got == pytest.approx(want)


def test_flat_history_predicts_the_same_again():
    s = monthly()
    m = fc.Monthly(s)
    origin = m.t("2020-10-01")
    p = fc.predict(m, origin)
    assert p == pytest.approx(s["2020-11-01":"2021-10-01"].to_numpy(), rel=1e-6)


def test_falling_history_predicts_below_recent_average():
    s = monthly(level=lambda y: 0.95 ** (y - 2000))
    m = fc.Monthly(s)
    origin = m.t("2020-10-01")
    p = fc.predict(m, origin)
    recent = m.recent_mean(origin, fc.RECENT_YEARS)
    assert p.sum() < recent.sum()
    t = fc.trend_summary(m.season_totals(origin), 15)
    assert t["direction"] == "downward" and t["per_year"] == pytest.approx(-0.05, abs=0.005)


def test_season_totals_only_complete_fire_years():
    m = fc.Monthly(monthly(years=range(2001, 2011)))
    tot = m.season_totals(m.t("2010-06-01"))
    assert tot.index.tolist() == list(range(2001, 2010))     # 2001/02 ... 2009/10
    assert m.season_totals(m.t("2010-05-01")).index[-1] == 2008


def test_backtest_uses_only_the_past_and_range_covers_truth():
    rng = np.random.default_rng(1)
    s = monthly() * rng.lognormal(0, 0.2, size=len(monthly()))
    m = fc.Monthly(s)
    origin = len(s) - 1
    bt = fc.backtest(m, origin)
    assert bt["origins"].max() + fc.HORIZON <= origin                 # every tested month is known
    o = bt["origins"][0]
    assert bt["pred"][0] == pytest.approx(fc.predict(fc.Monthly(s.iloc[:o + 1]), o))  # same with future cut off
    sk = fc.period_skill(m, bt, origin, 1, 6)
    assert sk["low_factor"] < 0 < sk["high_factor"]
    assert 0.6 <= sk["inside"] <= 0.95
    assert sk["past"] and all(p["actual"] >= 0 for p in sk["past"])
