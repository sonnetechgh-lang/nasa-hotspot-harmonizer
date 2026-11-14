"""Normals, unusual months, fire seasons and recent status for one area's daily series.

All functions take a daily activity series (pd.Series indexed by date, zeros filled in)
and return plain tables or dicts, so the API and tests can use them directly.

Terms:
  normal          the reference-years average for that month / day of year
  usual range     10th to 90th percentile of the reference years
  unusual month   above the 90th or below the 10th percentile, in a month that normally
                  holds at least 1% of the year's burning (so quiet months don't trigger flags)
  fire year       12 months starting at the quietest point of the year, so one dry season
                  is never split in two (for Ghana this starts in mid-year)
  season dates    when 10% / 25% / 75% / 90% of a fire year's burning has happened
"""
import numpy as np
import pandas as pd

FLAG_MIN_SHARE = 0.01
SMOOTH_DAYS = 15
QUIET_WINDOW_DAYS = 61
BASE = pd.Timestamp("2001-01-01")  # a non-leap year, used to label day-of-year bins


# ---------- helpers ----------

def doy365(idx: pd.DatetimeIndex) -> np.ndarray:
    """Day-of-year bin 0..364. In leap years 29 Feb shares a bin with 1 Mar."""
    d = idx.dayofyear.to_numpy() - 1
    after_feb = np.asarray(idx.is_leap_year & (idx.month > 2))
    return np.where(after_feb, d - 1, d)


def bin_date(b: int) -> pd.Timestamp:
    return BASE + pd.Timedelta(days=int(b) % 365)


def bin_label(b: int) -> str:
    t = bin_date(b)
    return f"{t.day} {t.strftime('%b')}"


def circular_smooth(x: np.ndarray, window: int) -> np.ndarray:
    k = window // 2
    ext = np.concatenate([x[-k:], x, x[:k]])
    return np.convolve(ext, np.ones(2 * k + 1) / (2 * k + 1), mode="valid")


def fill_daily(s: pd.Series, start, end) -> pd.Series:
    s = s.copy()
    s.index = pd.DatetimeIndex(s.index)
    return s.reindex(pd.date_range(start, end, freq="D"), fill_value=0.0).astype(float)


# ---------- monthly ----------

def monthly_table(s: pd.Series) -> pd.DataFrame:
    m = s.resample("MS").sum()
    out = pd.DataFrame({"year": m.index.year, "month": m.index.month, "value": m.to_numpy()})
    first, last = s.index.min(), s.index.max()
    out["partial"] = False
    if first.day != 1:
        out.loc[(out.year == first.year) & (out.month == first.month), "partial"] = True
    if last != last + pd.offsets.MonthEnd(0):
        out.loc[(out.year == last.year) & (out.month == last.month), "partial"] = True
    return out


def monthly_normals(mon: pd.DataFrame, ref: tuple) -> pd.DataFrame:
    r = mon[mon.year.between(*ref) & ~mon.partial]
    g = r.groupby("month").value
    n = pd.DataFrame({"mean": g.mean(), "p10": g.quantile(0.1), "p50": g.quantile(0.5),
                      "p90": g.quantile(0.9)}).reindex(range(1, 13), fill_value=0.0)
    total = n["mean"].sum()
    n["share"] = n["mean"] / total if total > 0 else 0.0
    return n.rename_axis("month").reset_index()


def flag_months(mon: pd.DataFrame, normals: pd.DataFrame, ref: tuple) -> pd.DataFrame:
    """Add percentile rank, change vs median, and an above/below flag to each month."""
    out = mon.merge(normals, on="month", how="left")
    refvals = {m: g.value.to_numpy() for m, g in
               mon[mon.year.between(*ref) & ~mon.partial].groupby("month")}
    out["pct_rank"] = [float((refvals.get(m, np.array([])) <= v).mean()) if m in refvals else None
                       for m, v in zip(out.month, out.value)]
    out["vs_median"] = np.where(out.p50 > 0, out.value / out.p50.where(out.p50 > 0) - 1, np.nan)
    eligible = out.share >= FLAG_MIN_SHARE
    above = eligible & (out.value > out.p90)
    below = eligible & ~out.partial & (out.value < out.p10)
    out["flag"] = np.where(above, "above", np.where(below, "below", None))
    return out


# ---------- daily climatology and season ----------

def daily_normal(s: pd.Series, ref: tuple) -> np.ndarray:
    r = s[(s.index.year >= ref[0]) & (s.index.year <= ref[1])]
    bins = doy365(r.index)
    sums = np.bincount(bins, weights=r.to_numpy(), minlength=365)
    days = np.bincount(bins, minlength=365)
    return sums / np.maximum(days, 1)


def season(clim: np.ndarray) -> dict | None:
    total = clim.sum()
    if total <= 0:
        return None
    quiet = int(np.argmin(circular_smooth(clim, QUIET_WINDOW_DAYS)))
    cum = np.cumsum(np.roll(clim, -quiet)) / total

    def at(q):
        return (int(np.searchsorted(cum, q)) + quiet) % 365

    peak = int(np.argmax(circular_smooth(clim, SMOOTH_DAYS)))
    bins = {"fire_year_start": quiet, "start": at(0.10), "core_start": at(0.25),
            "peak": peak, "core_end": at(0.75), "end": at(0.90)}
    return {**{k: bin_label(v) for k, v in bins.items()}, "bins": bins}


def fire_years(s: pd.Series, quiet_bin: int, ref: tuple) -> tuple[pd.DataFrame, float]:
    """One row per fire year: total, start/peak/end dates, and change vs normal."""
    data_start, data_end = s.index.min(), s.index.max()
    t0 = bin_date(quiet_bin)
    rows = []
    for y in range(data_start.year - 1, data_end.year + 1):
        fs = pd.Timestamp(y, t0.month, t0.day)
        fe = pd.Timestamp(y + 1, t0.month, t0.day) - pd.Timedelta(days=1)
        if fe < data_start or fs > data_end:
            continue
        part = s[fs:fe]
        total = float(part.sum())
        row = {"label": f"{y}/{str(y + 1)[-2:]}", "start_year": y, "from": fs.date(), "to": fe.date(),
               "complete": bool(fs >= data_start and fe <= data_end), "total": total,
               "start": None, "peak": None, "end": None}
        if total > 0:
            cum = (part.cumsum() / total).to_numpy()
            row["start"] = part.index[np.searchsorted(cum, 0.10)].date()
            row["end"] = part.index[min(np.searchsorted(cum, 0.90), len(part) - 1)].date()
            row["peak"] = part.rolling(7, center=True, min_periods=1).sum().idxmax().date()
        rows.append(row)
    df = pd.DataFrame(rows)
    is_ref = df.complete & df.start_year.between(ref[0], ref[1] - 1)
    normal = float(df.loc[is_ref, "total"].mean()) if is_ref.any() else float("nan")
    df["vs_normal"] = np.where(df.complete & (normal > 0), df.total / normal - 1, np.nan)

    # The fire year in progress: compare with the normal amount by this point in the year
    df["normal_to_date"] = np.nan
    df["vs_normal_to_date"] = np.nan
    last = df.iloc[-1]
    if not last.complete and pd.Timestamp(last["from"]) >= data_start:
        n_days = (data_end - pd.Timestamp(last["from"])).days + 1
        so_far = [s[pd.Timestamp(r["from"]):].iloc[:n_days].sum() for _, r in df[is_ref].iterrows()]
        if so_far:
            ntd = float(np.mean(so_far))
            df.loc[df.index[-1], "normal_to_date"] = ntd
            if ntd > 0:
                df.loc[df.index[-1], "vs_normal_to_date"] = last.total / ntd - 1
    return df, normal


# ---------- recent status (early warning) ----------

def recent_status(s: pd.Series, ref: tuple, days: int = 30) -> dict:
    end = s.index.max()
    start = end - pd.Timedelta(days=days - 1)
    value = float(s[start:end].sum())
    vals = []
    for y in range(ref[0], ref[1] + 1):
        e = end - pd.DateOffset(years=end.year - y)
        st = e - pd.Timedelta(days=days - 1)
        if st >= s.index.min():
            vals.append(float(s[st:e].sum()))
    arr = np.array(vals) if vals else np.array([0.0])
    mean, p10, p90 = float(arr.mean()), float(np.quantile(arr, 0.1)), float(np.quantile(arr, 0.9))
    if p90 < 1:
        status = "quiet season"
    elif value > p90:
        status = "above normal"
    elif value < p10:
        status = "below normal"
    else:
        status = "normal"
    return {"from": start.date(), "to": end.date(), "days": days, "value": value,
            "normal": mean, "p10": p10, "p90": p90, "status": status,
            "pct_rank": float((arr <= value).mean())}


def period_change(mon: pd.DataFrame, a: tuple, b: tuple) -> float | None:
    yearly = mon[~mon.partial].groupby("year").value.sum()
    ma, mb = yearly.loc[a[0]:a[1]].mean(), yearly.loc[b[0]:b[1]].mean()
    return float(mb / ma - 1) if ma and ma > 0 else None
