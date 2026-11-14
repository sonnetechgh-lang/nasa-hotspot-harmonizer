import numpy as np
import pandas as pd
import pytest

from pipeline import climatology as clim


def seasonal_series(start="2001-01-01", end="2010-12-31", scale_by_year=None):
    """Daily series with one bell-shaped fire season peaking on 1 January."""
    idx = pd.date_range(start, end, freq="D")
    b = clim.doy365(idx)
    dist = np.minimum(b, 365 - b)                    # days from 1 Jan
    v = 100 * np.exp(-0.5 * (dist / 20.0) ** 2)
    s = pd.Series(v, index=idx)
    if scale_by_year:
        s = s * np.array([scale_by_year.get(y, 1.0) for y in idx.year])
    return s


def test_doy365_merges_leap_day():
    idx = pd.DatetimeIndex(["2020-02-28", "2020-02-29", "2020-03-01", "2021-03-01", "2020-12-31"])
    assert clim.doy365(idx).tolist() == [58, 59, 59, 59, 364]


def test_season_finds_peak_and_symmetric_window():
    s = seasonal_series()
    season = clim.season(clim.daily_normal(s, (2001, 2010)))
    assert season["peak"] == "1 Jan"
    assert season["fire_year_start"] in ("1 Jul", "2 Jul", "3 Jul")
    start, end = season["bins"]["start"], season["bins"]["end"]
    # 10% and 90% points sit about the same distance either side of 1 January
    assert abs((365 - start) - end) <= 2


def test_season_none_when_no_fire():
    assert clim.season(np.zeros(365)) is None


def test_monthly_table_marks_partial_months():
    s = pd.Series(1.0, index=pd.date_range("2000-11-02", "2001-02-10"))
    m = clim.monthly_table(s)
    assert m.loc[m.partial, ["year", "month"]].values.tolist() == [[2000, 11], [2001, 2]]


def test_flags_above_and_ignores_quiet_months():
    s = seasonal_series(scale_by_year={2008: 3.0, 2009: 0.2})
    s["2005-07-15"] = 50.0                         # a blip in a month that is normally ~0
    mon = clim.monthly_table(s)
    normals = clim.monthly_normals(mon, (2001, 2010))
    flagged = clim.flag_months(mon, normals, (2001, 2010))
    dec = flagged[flagged.month == 12].set_index("year").flag
    assert dec[2008] == "above" and dec[2009] == "below" and pd.isna(dec[2003])
    july = flagged[(flagged.year == 2005) & (flagged.month == 7)].flag.iloc[0]
    assert pd.isna(july)


def test_fire_years_compare_with_normal():
    s = seasonal_series(start="2001-01-01", end="2012-12-31",
                        scale_by_year={2008: 2.0, 2009: 2.0})
    quiet = clim.season(clim.daily_normal(s, (2001, 2011)))["bins"]["fire_year_start"]
    fy, normal = clim.fire_years(s, quiet, (2001, 2011))
    row = fy.set_index("label").loc["2008/09"]
    assert row.complete
    assert row.vs_normal > 0.5                     # doubled season
    assert row.peak.month in (12, 1)
    assert not fy.iloc[0].complete                 # data starts mid-season


def test_fire_year_in_progress_compares_to_date():
    s = seasonal_series(start="2001-01-01", end="2011-11-15")
    quiet = clim.season(clim.daily_normal(s, (2001, 2010)))["bins"]["fire_year_start"]
    fy, _ = clim.fire_years(s, quiet, (2001, 2010))
    cur = fy.iloc[-1]
    assert not cur.complete
    assert cur.vs_normal_to_date == pytest.approx(0, abs=0.01)


def test_recent_status_quiet_and_above():
    s = seasonal_series(end="2010-07-31")
    assert clim.recent_status(s, (2001, 2009))["status"] == "quiet season"
    s2 = seasonal_series(end="2011-01-10")
    s2["2010-12-20":"2011-01-10"] *= 3
    r = clim.recent_status(s2, (2001, 2009))
    assert r["status"] == "above normal" and r["value"] > r["p90"]


def test_period_change():
    s = seasonal_series(end="2010-12-31", scale_by_year={y: 0.5 for y in range(2006, 2011)})
    change = clim.period_change(clim.monthly_table(s), (2001, 2005), (2006, 2010))
    assert change == pytest.approx(-0.5, abs=0.01)
