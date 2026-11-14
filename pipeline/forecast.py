"""Outlook: expected burning for the next 1-12 months, from an area's own fire history.

The model is deliberately simple, so it can be explained in one sentence and checked:

  forecast for a month = half of  "the same month, averaged over the last 5 years"
                       + half of  "the 15-season trend line, spread over the year
                                   with the usual monthly pattern of the last 10 years"

It was picked by testing on Ghana and its 16 regions: forecasts made every month from
2011 to 2025, each compared with what then happened. It beat the long-term normal
(typical error on a 12-month total ~10% against ~15%) and the other simple options tried
(recent averages alone, trend alone, weighting by the last few months).

The likely range comes from the same test: how far past forecasts made at this time of
year, for the same months ahead, landed from the truth (10th to 90th percentile).
The model only knows past fires. Rainfall, policy and land-use changes are not in it.

All functions take a monthly series (pd.Series, month-start index, complete months only).
"""
import math

import numpy as np
import pandas as pd

RECENT_YEARS = 5         # years in the recent average
TREND_SEASONS = 15       # seasons in the trend line
SHAPE_YEARS = 10         # years used for the usual monthly pattern
HORIZON = 12             # months ahead
FIRST_ORIGIN = "2011-06-01"  # earliest month a past forecast is tested from (needs ~10 seasons before it)
NEAR_MONTHS = 1          # past forecasts made within ±1 calendar month of now count for the range
TREND_P = 0.05           # p-value below which a trend is called clear


def theil_sen(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Median-of-slopes line: robust to a single odd year. Returns (slope, intercept)."""
    i, j = np.triu_indices(len(x), 1)
    slope = float(np.median((y[j] - y[i]) / (x[j] - x[i])))
    return slope, float(np.median(y - slope * x))


def mann_kendall_p(y: np.ndarray) -> float:
    """Two-sided p-value of the Mann-Kendall test for a monotonic trend (normal approximation)."""
    n = len(y)
    if n < 4:
        return 1.0
    i, j = np.triu_indices(n, 1)
    s = float(np.sign(y[j] - y[i]).sum())
    _, counts = np.unique(y, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - (counts * (counts - 1) * (2 * counts + 5)).sum()) / 18
    if var <= 0:
        return 1.0
    z = (s - np.sign(s)) / math.sqrt(var)
    return float(math.erfc(abs(z) / math.sqrt(2)))


class Monthly:
    """A monthly series as a flat array, with index 0 = its first month."""

    def __init__(self, mon: pd.Series, fy_month: int = 7):
        self.start = pd.Timestamp(mon.index[0])
        self.v = mon.to_numpy(float)
        self.fy_month = fy_month
        self._lag: dict[int, np.ndarray] = {}
        first = self.t(pd.Timestamp(self.start.year, fy_month, 1))
        self._fy_starts = np.arange(first + 12 if first < 0 else first, len(self.v) - 11, 12)
        self._fy_totals = np.array([self.v[a:a + 12].sum() for a in self._fy_starts])
        self._fy_years = np.array([self.fire_year(a) for a in self._fy_starts], dtype=float)

    def t(self, ts) -> int:
        ts = pd.Timestamp(ts)
        return (ts.year - self.start.year) * 12 + ts.month - self.start.month

    def ts(self, t: int) -> pd.Timestamp:
        return self.start + pd.DateOffset(months=int(t))

    def fire_year(self, t: int) -> int:
        d = self.ts(t)
        return d.year if d.month >= self.fy_month else d.year - 1

    def lag_mean(self, n: int) -> np.ndarray:
        """For every month t (also up to 12 months past the data): the mean of the same calendar
        month in the n years before it, v[t-12] ... v[t-12n]. Within 12 months of an origin these
        are all already known at that origin."""
        if n not in self._lag:
            size = len(self.v) + HORIZON
            total, count = np.zeros(size), np.zeros(size)
            for k in range(1, n + 1):
                if 12 * k >= len(self.v) + HORIZON:
                    break
                src = self.v[:max(0, min(len(self.v), size - 12 * k))]
                total[12 * k:12 * k + len(src)] += src
                count[12 * k:12 * k + len(src)] += 1
            self._lag[n] = np.where(count > 0, total / np.maximum(count, 1), 0.0)
        return self._lag[n]

    def recent_mean(self, origin: int, n: int) -> np.ndarray:
        return self.lag_mean(n)[origin + 1:origin + 1 + HORIZON]

    def season_totals(self, origin: int) -> pd.Series:
        """Total of every fire year complete by the end of month `origin`, indexed by start year."""
        done = self._fy_starts + 11 <= origin
        return pd.Series(self._fy_totals[done], index=self._fy_years[done].astype(int), dtype=float)


def trend_line(totals: pd.Series, n: int = TREND_SEASONS) -> tuple[float, float] | None:
    """Theil-Sen line through log season totals of the last n seasons."""
    tot = totals.iloc[-n:]
    if len(tot) < 5:
        return None
    return theil_sen(tot.index.to_numpy(float), np.log(tot.to_numpy() + 1))


def predict(m: Monthly, origin: int) -> np.ndarray:
    """Forecast for the 12 months after month `origin`, using only data up to it."""
    recent = m.recent_mean(origin, RECENT_YEARS)
    shape = m.recent_mean(origin, SHAPE_YEARS)            # 12 consecutive months = one full year
    line = trend_line(m.season_totals(origin))
    if line is None or shape.sum() <= 0:
        return recent
    b, a = line
    level = np.array([math.exp(a + b * m.fire_year(t)) - 1 for t in range(origin + 1, origin + 1 + HORIZON)])
    trended = shape / shape.sum() * np.maximum(level, 0)
    return 0.5 * recent + 0.5 * trended


def normal_forecast(m: Monthly, origin: int) -> np.ndarray:
    """The plain long-term normal (all years so far), used as the yardstick in the test."""
    return m.recent_mean(origin, 999)


def backtest(m: Monthly, last_origin: int) -> dict:
    """Past forecasts from every month since FIRST_ORIGIN whose 12 months ahead are all known.
    Returns arrays: origins (n), pred / normal / actual (n x 12)."""
    first = max(m.t(FIRST_ORIGIN), 12 * 8)
    origins = np.arange(first, last_origin - HORIZON + 1)
    pred = np.array([predict(m, o) for o in origins]).reshape(-1, HORIZON)
    norm = np.array([normal_forecast(m, o) for o in origins]).reshape(-1, HORIZON)
    actual = np.array([m.v[o + 1:o + 1 + HORIZON] for o in origins]).reshape(-1, HORIZON)
    return {"origins": origins, "pred": pred, "normal": norm, "actual": actual}


def _log_ratio(actual, pred):
    return np.log((actual + 1) / (pred + 1))


def period_skill(m: Monthly, bt: dict, origin: int, h0: int, h1: int) -> dict:
    """How past forecasts did for months h0..h1 ahead (1-based), made at this time of year.
    Gives the likely range (as factors on the forecast) and the typical error."""
    months_now = m.ts(origin).month
    om = np.array([m.ts(o).month for o in bt["origins"]])
    gap = np.abs((om - months_now + 6) % 12 - 6)
    near = gap <= NEAR_MONTHS
    if near.sum() < 6:
        near = np.ones_like(near, dtype=bool)
    sl = slice(h0 - 1, h1)
    p = bt["pred"][near, sl].sum(1)
    a = bt["actual"][near, sl].sum(1)
    nm = bt["normal"][near, sl].sum(1)
    if len(a) == 0:
        return {"n": 0}
    r = _log_ratio(a, p)
    lo, hi = np.quantile(r, [0.1, 0.9])
    # Typical error as a share of what happened, on months that saw real burning
    big = a >= 5
    err = float(np.median(np.abs(p[big] - a[big]) / a[big])) if big.any() else None
    err_n = float(np.median(np.abs(nm[big] - a[big]) / a[big])) if big.any() else None
    inside = float(np.mean((r >= lo) & (r <= hi)))
    # One past forecast per year: those made in the same calendar month as now
    same = np.flatnonzero(om == months_now)
    past = []
    for k in same:
        o = bt["origins"][k]
        pv, av = float(bt["pred"][k, sl].sum()), float(bt["actual"][k, sl].sum())
        past.append({"made": m.ts(o) + pd.offsets.MonthEnd(0), "from": m.ts(o + h0), "to": m.ts(o + h1),
                     "forecast": pv, "low": (pv + 1) * math.exp(lo) - 1, "high": (pv + 1) * math.exp(hi) - 1,
                     "actual": av})
    return {"n": int(near.sum()), "low_factor": float(lo), "high_factor": float(hi), "typical_error": err,
            "normal_error": err_n, "inside": inside, "past": past}


def trend_summary(totals: pd.Series, n: int | None = None) -> dict | None:
    """Direction of change in season totals: % per year (Theil-Sen) and whether it is clear (Mann-Kendall)."""
    tot = totals.iloc[-n:] if n else totals
    if len(tot) < 5 or tot.sum() <= 0:
        return None
    b, _ = theil_sen(tot.index.to_numpy(float), np.log(tot.to_numpy() + 1))
    p = mann_kendall_p(tot.to_numpy())
    per_year = math.exp(b) - 1
    clear = p < TREND_P
    direction = ("downward" if per_year < 0 else "upward") if clear else "no clear trend"
    return {"from": int(tot.index[0]), "to": int(tot.index[-1]), "seasons": len(tot),
            "per_year": per_year, "p": p, "clear": clear, "direction": direction}
