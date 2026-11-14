"""Ghana Burning Activity Calendar: API + web files.

Every analysis endpoint takes an area in one of three ways (GET query or POST JSON body):
  region=Northern        a Ghana region, a district (e.g. "Saboba", "Tamale Metropolitan"), or "Ghana"
  bbox=w,s,e,n           a box in degrees
  geometry={GeoJSON}     a drawn polygon (POST only)
and sensor=harmonized|modis|viirs (default harmonized).

Run:  uvicorn api.main:app --port 8000      then open http://127.0.0.1:8000/docs
"""
import io
import math
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import climatology as clim
from pipeline import forecast as fc
from pipeline.config import ROOT

from .area import AreaError, AreaIndex
from .store import REF_EARLIEST, REF_YEARS, SENSORS, Store

UNITS = {"harmonized": "VIIRS-equivalent active cell-days",
         "modis": "MODIS active cell-days", "viirs": "VIIRS active cell-days"}
CHANGE_PERIODS = ((2003, 2011), (2021, 2025))
CACHE_SIZE = 32
MIN_SEASON_ACTIVITY = 10  # active cell-days in a normal season needed for season dates and flags

@asynccontextmanager
async def lifespan(_app):
    # Work out the slow region comparison in the background so the first visitor doesn't wait
    threading.Thread(target=compare_regions, daemon=True).start()
    yield


app = FastAPI(title="Ghana Burning Activity Calendar API", version="0.1", lifespan=lifespan,
              description="Harmonized MODIS + VIIRS active-fire activity for any area of Ghana.")
store = Store()
areas = AreaIndex(store.cal["cell_deg"])
_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()


class AreaBody(BaseModel):
    region: str | None = None
    bbox: str | list[float] | None = None
    geometry: dict | None = None
    sensor: str = "harmonized"
    ref: str | None = None
    year: int | None = None
    month: int | None = None
    start: str | None = None   # forecast period, "YYYY-MM"
    end: str | None = None


# ---------- helpers ----------

def clean(obj):
    """Make numpy / pandas values JSON-friendly (NaN -> None, dates -> ISO strings)."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return clean(obj.to_dict(orient="records"))
    if isinstance(obj, (pd.Timestamp, date)):
        return obj.isoformat()[:10]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return None if math.isnan(obj) else round(float(obj), 3)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if obj is pd.NA or obj is pd.NaT:
        return None
    return obj


def check_sensor(sensor: str) -> str:
    s = sensor.lower()
    if s not in SENSORS:
        raise HTTPException(400, f"sensor must be one of {', '.join(SENSORS)}")
    return s


def parse_ref(ref: str | None, sensor: str) -> tuple[int, int]:
    """Reference years for normals, e.g. '2011-2025'. Default: every full year of the record."""
    if not ref:
        return REF_YEARS[sensor]
    try:
        a, b = (int(v) for v in ref.split("-"))
    except ValueError:
        raise HTTPException(400, "ref must look like 2011-2025") from None
    lo, hi = REF_EARLIEST[sensor], store.last.year - 1
    if not (lo <= a < b <= hi) or b - a < 4:
        raise HTTPException(400, f"ref for {sensor} must be at least 5 full years between {lo} and {hi}")
    return a, b


def resolve(region=None, bbox=None, geometry=None):
    try:
        return areas.resolve(region, bbox, geometry)
    except AreaError as e:
        raise HTTPException(400, str(e)) from None


def daily_for(key: str, geom) -> pd.DataFrame:
    """Daily series for an area, kept in a small cache (shared with the warm-up thread)."""
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    df = store.daily(areas.cells(geom))
    with _cache_lock:
        _cache[key] = df
        if len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return df


def pct(x: float) -> str:
    return f"{abs(x):.0%} {'above' if x > 0 else 'below'}"


def summarize(name, season, fy, recent, change, months) -> list[str]:
    out = []
    if season:
        out.append(f"In {name}, the fire season usually runs from {season['start']} to {season['end']}, "
                   f"peaking around {season['peak']}. Half of the year's burning happens between "
                   f"{season['core_start']} and {season['core_end']}.")
    else:
        out.append(f"No burning was detected in {name}.")
        return out
    r = recent
    window = f"{pd.Timestamp(r['from']):%d %b} – {pd.Timestamp(r['to']):%d %b %Y}"
    if r["status"] == "quiet season":
        out.append(f"Last {r['days']} days ({window}): quiet, as usual for this time of year.")
    else:
        out.append(f"Last {r['days']} days ({window}): {r['status']}, {r['value']:,.0f} against a normal "
                   f"of {r['normal']:,.0f} for this time of year.")
    done = fy[fy.complete & fy.vs_normal.notna()]
    if not done.empty:
        last = done.iloc[-1]
        out.append(f"Last full season ({last.label}): {pct(last.vs_normal)} normal.")
    cur = fy.iloc[-1]
    if not cur.complete and pd.notna(cur.vs_normal_to_date):
        out.append(f"Season {cur.label} so far: {pct(cur.vs_normal_to_date)} normal for this point in the season.")
    recent_flags = months[(months.year >= months.year.max() - 4) & months.flag.notna()]
    if not recent_flags.empty:
        a, b = (recent_flags.flag == "above").sum(), (recent_flags.flag == "below").sum()
        out.append(f"In the last 5 years, {a} month(s) were unusually high and {b} unusually low.")
    if change is not None:
        (a0, a1), (b0, b1) = CHANGE_PERIODS
        out.append(f"Long-term: burning in {b0}–{str(b1)[-2:]} averaged {abs(change):.0%} "
                   f"{'more' if change > 0 else 'less'} than in {a0}–{str(a1)[-2:]}.")
    return out


def analyse(region=None, bbox=None, geometry=None, sensor="harmonized", ref=None) -> dict:
    sensor = check_sensor(sensor)
    ref = parse_ref(ref, sensor)
    name, geom, key = resolve(region, bbox, geometry)
    daily = daily_for(key, geom)
    s = daily[sensor]
    if sensor == "viirs":
        s = s[s.index >= pd.Timestamp(store.cal["viirs_start"])]

    mon = clim.monthly_table(s)
    normals = clim.monthly_normals(mon, ref)
    months = clim.flag_months(mon, normals, ref)
    # Before Aqua's data starts only Terra was flying: those months use the less
    # accurate Terra-only correction
    month_start = pd.to_datetime(dict(year=months.year, month=months.month, day=1))
    lower = (sensor == "harmonized") & (month_start < pd.Timestamp(store.cal["aqua_start"]))
    months["confidence"] = np.where(lower, "lower", "standard")
    climd = clim.daily_normal(s, ref)
    season = clim.season(climd)
    fy, fy_normal = (clim.fire_years(s, season["bins"]["fire_year_start"], ref)
                     if season else (pd.DataFrame(), float("nan")))
    recent = clim.recent_status(s, ref)
    change = clim.period_change(mon, *CHANGE_PERIODS) if sensor == "harmonized" else None

    # A few stray fires give "season dates" that look precise but mean nothing
    low_activity = bool(season) and not (fy_normal >= MIN_SEASON_ACTIVITY)
    if low_activity:
        months["flag"] = None
    place = name if not key.startswith("wkb:") else "this area"   # drawn shapes and boxes
    if not season:
        summary = [f"No burning was detected in {place}."]
    elif low_activity:
        summary = [f"There is very little fire in {place} (about {fy_normal:,.1f} active cell-days in a normal "
                   "season), too little for reliable season dates or unusual-month flags. Try a larger area."]
    else:
        summary = summarize(place, season, fy, recent, change, months)

    # All three records per calendar year, for the "one record from two sensors" chart
    yearly = daily.groupby(daily.index.year)[["modis", "viirs", "harmonized"]].sum()
    yearly.loc[yearly.index < pd.Timestamp(store.cal["viirs_start"]).year, "viirs"] = np.nan
    yearly = yearly.rename_axis("year").reset_index()
    yearly["partial"] = (yearly.year == daily.index.min().year) | (yearly.year == daily.index.max().year)

    cells = areas.cells(geom)
    return {
        "area": {"name": name, "km2": areas.area_km2(geom), "cells": len(cells),
                 "bounds": list(geom.bounds)},
        "sensor": sensor, "units": UNITS[sensor], "reference_years": list(ref),
        "data": {"first": s.index.min(), "last": s.index.max()},
        "monthly": months[["year", "month", "value", "partial", "flag", "pct_rank", "vs_median",
                           "confidence"]],
        "normals": normals,
        "season": {k: v for k, v in season.items() if k != "bins"} if season else None,
        "fire_years": fy, "fire_year_normal": fy_normal,
        "recent": recent,
        "long_term_change": {"from": CHANGE_PERIODS[0], "to": CHANGE_PERIODS[1], "change": change}
        if change is not None else None,
        "low_activity": low_activity,
        "yearly": yearly,
        "summary": summary,
    }


def daily_view(region, bbox, geometry, sensor, year, ref=None) -> dict:
    sensor = check_sensor(sensor)
    ref = parse_ref(ref, sensor)
    name, geom, key = resolve(region, bbox, geometry)
    daily = daily_for(key, geom)
    s = daily[sensor]
    year = year or s.index.max().year
    normal = clim.circular_smooth(clim.daily_normal(s, ref), clim.SMOOTH_DAYS)
    part = s[str(year)]
    if part.empty:
        raise HTTPException(404, f"no data for {year}")
    bins = clim.doy365(part.index)
    return {"area": name, "sensor": sensor, "units": UNITS[sensor], "year": year,
            "days": [{"date": d, "value": v, "normal": normal[b]}
                     for d, v, b in zip(part.index, part.to_numpy(), bins)]}


def hotspot_view(region, bbox, geometry, year, month, limit) -> dict:
    name, geom, _ = resolve(region, bbox, geometry)
    if year is None:
        raise HTTPException(400, "year is required")
    if month:
        start = date(year, month, 1)
        end = (pd.Timestamp(start) + pd.offsets.MonthEnd(0)).date()
    else:
        start, end = date(year, 1, 1), date(year, 12, 31)
    df, total = store.hotspots(geom, start, end, limit)
    df = df.assign(time_utc=df.time_utc.dt.strftime("%Y-%m-%d %H:%M"))
    return {"area": name, "from": start, "to": end, "total": total, "shown": len(df),
            "hotspots": df[["lat", "lon", "time_utc", "sensor", "satellite", "conf_class", "frp"]]}


def export_csv(region, bbox, geometry, sensor, ref=None) -> Response:
    res = analyse(region, bbox, geometry, sensor, ref)
    mon = res["monthly"].merge(res["normals"][["month", "mean", "p10", "p90"]], on="month")
    mon = mon.rename(columns={"mean": "normal_mean", "p10": "normal_p10", "p90": "normal_p90"})
    buf = io.StringIO()
    buf.write(f"# {res['area']['name']} | {res['units']} | normals {res['reference_years'][0]}-"
              f"{res['reference_years'][1]} | source: NASA FIRMS MODIS + VIIRS, harmonized\n")
    mon.round(3).to_csv(buf, index=False)
    fname = res["area"]["name"].lower().replace(" ", "_").replace("°", "")[:40]
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}_{res["sensor"]}.csv"'})


# ---------- routes ----------

@app.get("/api/health")
def health():
    return {"ok": True, "data_last_day": store.last.date().isoformat()}


@app.get("/api/meta")
def meta():
    c = store.cal
    best = next(r for r in c["cross_validation"] if r["cell_deg"] == c["cell_deg"] and r["model"] == c["model"])
    return clean({
        "country": "Ghana", "data": {"first": store.first, "last": store.last},
        "sensors": SENSORS, "units": UNITS, "reference_years": REF_YEARS,
        "reference_presets": {"all": REF_YEARS["harmonized"],
                              "recent_15": (store.last.year - 15, store.last.year - 1)},
        "regions": ["Ghana"] + [f"{n} Region" for n in areas.region_names()],
        "districts": [{"name": n, "label": areas.district_label(n), "region": f"{areas.district_region[n]} Region"}
                      for n in areas.district_names()],
        "harmonization": {
            "grid_deg": c["cell_deg"], "method": c["model"], "reference_sensor": c["reference_sensor"],
            "calibration_years": c["calibration_years"], "viirs_start": c["viirs_start"],
            "aqua_start": c["aqua_start"], "overall_ratio": c["overall_ratio"],
            "annual_error": best["annual_err"], "peak_month_error": best["month_err_median"],
            "factors_by_month": {r["m"]: r["k"] for r in c["factors"] if r["z"] == "north"},
            "terra_only_factors_by_month": {r["m"]: r["k"] for r in c["factors_terra_only"] if r["z"] == "north"},
            "terra_only_annual_error": c["terra_only_cross_validation"]["annual_err"],
        },
    })


@app.get("/api/regions")
def regions():
    return {"border": areas.border_geojson(), "regions": areas.regions_geojson()}


@app.get("/api/districts")
def districts():
    return areas.districts_geojson()


@app.get("/api/calendar")
def calendar_get(region: str | None = None, bbox: str | None = None, sensor: str = "harmonized",
                 ref: str | None = None):
    return clean(analyse(region, bbox, None, sensor, ref))


@app.post("/api/calendar")
def calendar_post(body: AreaBody):
    return clean(analyse(body.region, body.bbox, body.geometry, body.sensor, body.ref))


@app.get("/api/daily")
def daily_get(region: str | None = None, bbox: str | None = None, sensor: str = "harmonized",
              year: int | None = None, ref: str | None = None):
    return clean(daily_view(region, bbox, None, sensor, year, ref))


@app.post("/api/daily")
def daily_post(body: AreaBody):
    return clean(daily_view(body.region, body.bbox, body.geometry, body.sensor, body.year, body.ref))


@app.get("/api/hotspots")
def hotspots_get(year: int, month: int | None = Query(None, ge=1, le=12), region: str | None = None,
                 bbox: str | None = None, limit: int = Query(5000, ge=1, le=20000)):
    return clean(hotspot_view(region, bbox, None, year, month, limit))


@app.post("/api/hotspots")
def hotspots_post(body: AreaBody, limit: int = Query(5000, ge=1, le=20000)):
    return clean(hotspot_view(body.region, body.bbox, body.geometry, body.year, body.month, limit))


_compare_cache: dict = {}
_compare_lock = threading.Lock()


def period_mean(monthly: pd.DataFrame, years: tuple) -> float:
    yearly = monthly[~monthly.partial].groupby("year").value.sum()
    return float(yearly.loc[years[0]:years[1]].mean())


def compare_regions(sensor: str = "harmonized", ref: str | None = None) -> dict:
    """Season dates, size of the fire season and change over time for every region.
    Takes ~10 s the first time, so the server warms it up at startup."""
    sensor = check_sensor(sensor)
    ref_years = parse_ref(ref, sensor)
    key = (sensor, ref_years)
    with _compare_lock:
        if key in _compare_cache:
            return _compare_cache[key]
        rows = []
        for name in areas.region_names():
            res = analyse(region=name, sensor=sensor, ref=f"{ref_years[0]}-{ref_years[1]}" if ref else None)
            fy, s = res["fire_years"], res["season"]
            done = fy[fy.complete & fy.vs_normal.notna()] if not fy.empty else fy
            rows.append({
                "region": res["area"]["name"], "km2": res["area"]["km2"],
                "season_normal": res["fire_year_normal"],
                "density": res["fire_year_normal"] / res["area"]["km2"] * 100 if res["area"]["km2"] else None,
                "start": s and s["start"], "peak": s and s["peak"], "end": s and s["end"],
                "last_season": done.iloc[-1].label if not done.empty else None,
                "last_season_vs_normal": float(done.iloc[-1].vs_normal) if not done.empty else None,
                "long_term_change": res["long_term_change"]["change"] if res["long_term_change"] else None,
                "early_mean": period_mean(res["monthly"], CHANGE_PERIODS[0]),
                "late_mean": period_mean(res["monthly"], CHANGE_PERIODS[1]),
            })
        df = pd.DataFrame(rows)
        total = df.season_normal.sum()
        df["share"] = df.season_normal / total if total > 0 else np.nan
        # Share of the national change each region accounts for (harmonized record only)
        drop = (df.early_mean - df.late_mean).sum()
        df["share_of_change"] = ((df.early_mean - df.late_mean) / drop
                                 if sensor == "harmonized" and drop else np.nan)
        out = clean({"sensor": sensor, "units": UNITS[sensor], "reference_years": list(ref_years),
                     "change_periods": CHANGE_PERIODS if sensor == "harmonized" else None,
                     "regions": df.sort_values("season_normal", ascending=False)})
        _compare_cache[key] = out
        return out


# ---------- outlook (forecast for the next 1-12 months) ----------

_bt_cache: OrderedDict = OrderedDict()


def month_label(ts) -> str:
    return f"{pd.Timestamp(ts):%b %Y}"


def period_label(a, b) -> str:
    return month_label(a) if pd.Timestamp(a) == pd.Timestamp(b) else f"{month_label(a)} – {month_label(b)}"


def parse_month(s: str, name: str) -> pd.Timestamp:
    try:
        return pd.Timestamp(f"{s[:7]}-01")
    except (ValueError, TypeError):
        raise HTTPException(400, f"{name} must look like 2026-12") from None


def season_label(y: int, fy_month: int) -> str:
    return str(y) if fy_month == 1 else f"{y}/{str(y + 1)[-2:]}"


def outlook(region=None, bbox=None, geometry=None, start=None, end=None, ref=None) -> dict:
    """Expected burning (harmonized record) for a chosen period in the next 12 months."""
    res = analyse(region, bbox, geometry, "harmonized", ref)
    name, _, key = resolve(region, bbox, geometry)
    mon = res["monthly"]
    full = mon[~mon.partial & (mon.year >= 2001)]
    series = pd.Series(full.value.to_numpy(float),
                       index=pd.to_datetime(dict(year=full.year, month=full.month, day=1)))
    # Fire year starts in the month holding the area's quietest point (next month if late in it)
    fy_month = 7
    if res["season"]:
        q = pd.Timestamp(f"{res['season']['fire_year_start']} 2001")
        fy_month = q.month if q.day <= 15 else q.month % 12 + 1
    m = fc.Monthly(series, fy_month)
    origin = len(series) - 1
    bkey = (key, fy_month, series.index[-1], float(series.iloc[-1]))
    with _cache_lock:
        bt = _bt_cache.get(bkey)
    if bt is None:
        bt = fc.backtest(m, origin)
        with _cache_lock:
            _bt_cache[bkey] = bt
            if len(_bt_cache) > CACHE_SIZE:
                _bt_cache.popitem(last=False)

    window = [m.ts(origin + h) for h in range(1, fc.HORIZON + 1)]
    a = parse_month(start, "start") if start else window[0]
    b = parse_month(end, "end") if end else window[-1]
    if a not in window or b not in window or b < a:
        raise HTTPException(400, f"pick months from {window[0]:%Y-%m} to {window[-1]:%Y-%m}, start before end")
    h0, h1 = window.index(a) + 1, window.index(b) + 1

    pred = fc.predict(m, origin)
    normals = res["normals"].set_index("month")["mean"]
    by_month = {(r.year, r.month): r for r in mon.itertuples()}

    def rng(p, sk):
        if not sk.get("n"):
            return None, None
        return max(0.0, (p + 1) * math.exp(sk["low_factor"]) - 1), (p + 1) * math.exp(sk["high_factor"]) - 1

    months = []
    for h, ts in enumerate(window, start=1):
        lo, hi = rng(pred[h - 1], fc.period_skill(m, bt, origin, h, h))
        prev = by_month.get((ts.year - 1, ts.month))
        now = by_month.get((ts.year, ts.month))
        months.append({"month": f"{ts:%Y-%m}", "label": month_label(ts), "forecast": pred[h - 1],
                       "low": lo, "high": hi, "normal": float(normals.get(ts.month, 0.0)),
                       "last_year": float(prev.value) if prev is not None else None,
                       "observed_so_far": float(now.value) if now is not None and now.partial else None,
                       "in_period": h0 <= h <= h1})
    sel = [x for x in months if x["in_period"]]
    sk = fc.period_skill(m, bt, origin, h0, h1)
    total = sum(x["forecast"] for x in sel)
    lo, hi = rng(total, sk)
    normal = sum(x["normal"] for x in sel)
    last_year = sum(x["last_year"] or 0 for x in sel)
    history = [{"month": f"{ts:%Y-%m}", "label": month_label(ts), "value": float(v),
                "normal": float(normals.get(ts.month, 0.0))}
               for ts, v in series.iloc[-24:].items()]

    totals = m.season_totals(origin)
    trend_recent = fc.trend_summary(totals, fc.TREND_SEASONS)
    trend_all = fc.trend_summary(totals)
    if trend_recent:
        line = fc.trend_line(totals)
        trend_recent["line"] = [{"season": season_label(y, fy_month), "value": math.exp(line[1] + line[0] * y) - 1}
                                for y in totals.index[-fc.TREND_SEASONS:]]
    seasons = [{"season": season_label(y, fy_month), "total": v} for y, v in totals.items()]
    past = [{**p, "label": f"{p['from']:%b %Y}" if h0 == h1 else f"{p['from']:%b %Y} – {p['to']:%b %Y}",
             "made": p["made"], "inside": p["low"] <= p["actual"] <= p["high"]} for p in sk.get("past", [])]
    if sk.get("n"):
        sk = {k: v for k, v in sk.items() if k != "past"}

    place = name if not key.startswith("wkb:") else "this area"
    plabel = period_label(a, b)
    vs_normal = total / normal - 1 if normal > 0 else None
    vs_last = total / last_year - 1 if last_year > 0 else None
    summary = []
    if res["low_activity"] or total + normal < 1:
        summary.append(f"There is very little fire in {place}, so this outlook is rough. Try a larger area.")
    summary.append(f"Expected burning in {place}, {plabel}: about {total:,.0f} active cell-days"
                   + (f" (likely {lo:,.0f} to {hi:,.0f})." if lo is not None else "."))
    comp = []
    if vs_normal is not None:
        comp.append(f"{pct(vs_normal)} normal")
    if vs_last is not None:
        comp.append(f"{abs(vs_last):.0%} {'more' if vs_last > 0 else 'less'} than the same months a year earlier")
    if comp:
        summary.append("That is " + " and ".join(comp) + ".")
    if trend_recent:
        t = trend_recent
        if t["clear"]:
            summary.append(f"Burning has a {t['direction']} trend: about {abs(t['per_year']):.1%} "
                           f"{'less' if t['per_year'] < 0 else 'more'} each season over the last {t['seasons']} seasons.")
        else:
            summary.append(f"Over the last {t['seasons']} seasons there is no clear upward or downward trend "
                           f"(it changes about {abs(t['per_year']):.1%} a season, within normal ups and downs).")
    if sk.get("typical_error") is not None:
        summary.append(f"Tested on past years, forecasts like this were typically {sk['typical_error']:.0%} off "
                       f"(using the normal alone: {sk['normal_error']:.0%}).")

    return {
        "area": {"name": name}, "units": UNITS["harmonized"], "reference_years": res["reference_years"],
        "made_from": f"{series.index[-1]:%Y-%m}", "data_last": res["data"]["last"],
        "window": {"first": f"{window[0]:%Y-%m}", "last": f"{window[-1]:%Y-%m}"},
        "period": {"start": f"{a:%Y-%m}", "end": f"{b:%Y-%m}", "label": plabel, "months": h1 - h0 + 1},
        "total": {"forecast": total, "low": lo, "high": hi, "normal": normal, "last_year": last_year,
                  "vs_normal": vs_normal, "vs_last_year": vs_last},
        "months": months, "history": history,
        "trend": {"recent": trend_recent, "all": trend_all, "seasons": seasons},
        "skill": sk, "past": past,
        "low_activity": res["low_activity"],
        "method": ("Half the recent 5-year average for each month, half the 15-season trend line. "
                   "Likely range: 10th–90th percentile of past forecasts made at this time of year."),
        "summary": summary,
    }


@app.get("/api/forecast")
def forecast_get(region: str | None = None, bbox: str | None = None, start: str | None = None,
                 end: str | None = None, ref: str | None = None):
    return clean(outlook(region, bbox, None, start, end, ref))


@app.post("/api/forecast")
def forecast_post(body: AreaBody):
    return clean(outlook(body.region, body.bbox, body.geometry, body.start, body.end, body.ref))


ANIM_CELL_DEG = 0.2          # coarse grid for the season playback (~22 km cells)
_anim_cache: dict = {}


def season_animation(season: str = "normal", ref: str | None = None) -> dict:
    """Week-by-week harmonized activity on a coarse grid over Ghana, for one fire season
    (season = its start year, e.g. "2025") or the normal season (average of reference years)."""
    ref_years = parse_ref(ref, "harmonized")
    key = (season, ref_years)
    if key in _anim_cache:
        return _anim_cache[key]
    ghana = analyse(region="Ghana")
    fy0 = pd.Timestamp(ghana["fire_years"].iloc[0]["from"])        # national fire-year start, e.g. 1 Jul
    if season == "normal":
        years = (ref_years[0], ref_years[1] - 1)                    # complete fire years inside the reference
        label = f"Normal season ({ref_years[0]}–{ref_years[1]})"
    else:
        try:
            y = int(season[:4])
        except ValueError:
            raise HTTPException(400, "season must be 'normal' or a start year like 2025") from None
        if not (store.first.year - 1 <= y <= store.last.year):
            raise HTTPException(400, f"no data for the {y} season")
        years = (y, y)
        label = f"{y}/{str(y + 1)[-2:]} season"
    df = store.weekly_coarse(ANIM_CELL_DEG, fy0.month, fy0.day, years)
    df["v"] = df.v / (years[1] - years[0] + 1)
    df = df[df.v >= 0.05]
    cells = df[["cx", "cy"]].drop_duplicates().sort_values(["cy", "cx"]).reset_index(drop=True)
    cells["i"] = cells.index
    df = df.merge(cells, on=["cx", "cy"])
    national = df.groupby("week").v.sum().reindex(range(52), fill_value=0)
    active = national[national >= 0.01 * national.max()].index if national.max() > 0 else pd.Index([0, 51])
    start = pd.Timestamp(2001 if fy0.month > 1 else 2002, fy0.month, fy0.day)
    c = ANIM_CELL_DEG
    out = clean({
        "label": label, "cell_deg": c, "units": UNITS["harmonized"],
        "weeks": [(start + pd.Timedelta(weeks=w)).strftime("%d %b").lstrip("0") for w in range(52)],
        "play_from": int(active.min()), "play_to": int(active.max()),
        "national": national.round(1).tolist(),
        "cells": [[round(cy * c, 4), round(cx * c, 4)] for cx, cy in zip(cells.cx, cells.cy)],  # south-west corner
        "values": df[["week", "i", "v"]].round({"v": 1}).values.tolist(),
        "max": float(df.v.quantile(0.98)) if len(df) else 0,
    })
    _anim_cache[key] = out
    return out


@app.get("/api/animation")
def animation_get(season: str = "normal", ref: str | None = None):
    return season_animation(season, ref)


@app.get("/api/compare")
def compare_get(sensor: str = "harmonized", ref: str | None = None):
    return compare_regions(sensor, ref)


@app.get("/api/export.csv")
def export_get(region: str | None = None, bbox: str | None = None, sensor: str = "harmonized",
               ref: str | None = None):
    return export_csv(region, bbox, None, sensor, ref)


@app.post("/api/export.csv")
def export_post(body: AreaBody):
    return export_csv(body.region, body.bbox, body.geometry, body.sensor, body.ref)


# Web files last, so /api routes win
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
