"""Turn the raw MODIS and VIIRS files into one clean table.

What it does:
  - gives both sensors the same column names and units
  - puts confidence on one scale: low / nominal / high
  - converts date + time to one UTC timestamp
  - keeps only points inside Ghana's border (geoBoundaries outline)
  - removes duplicates where downloads overlap (standard data wins over near-real-time)
  - flags points to use: vegetation fires (type 0) with nominal or high confidence

Output: data/processed/hotspots.parquet and data/processed/summary_by_year.csv
Run:    python -m pipeline.normalize
"""
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape

from .config import COUNTRY_CODE, PROCESSED, RAW

# FIRMS guidance for MODIS: below 30 = low, 30-79 = nominal, 80+ = high
MODIS_NOMINAL_MIN = 30
MODIS_HIGH_MIN = 80

SATELLITE_NAMES = {
    "Terra": "Terra", "T": "Terra",
    "Aqua": "Aqua", "A": "Aqua",
    "N": "Suomi NPP", "NPP": "Suomi NPP",
    "N20": "NOAA-20", "1": "NOAA-20",
    "N21": "NOAA-21", "2": "NOAA-21",
}

COLUMNS = ["lat", "lon", "time_utc", "date", "sensor", "satellite", "product",
           "confidence_raw", "conf_class", "frp", "brightness_k", "scan", "track",
           "daynight", "fire_type", "use", "source_file"]


def detect_sensor(columns) -> str:
    if "bright_ti4" in columns:
        return "VIIRS"
    if "brightness" in columns:
        return "MODIS"
    raise ValueError(f"unknown hotspot file columns: {list(columns)}")


def confidence_class(sensor: str, conf: pd.Series) -> pd.Series:
    """Map each sensor's confidence onto low / nominal / high."""
    if sensor == "MODIS":
        c = pd.to_numeric(conf, errors="coerce")
        out = np.select([c < MODIS_NOMINAL_MIN, c < MODIS_HIGH_MIN], ["low", "nominal"], "high")
        return pd.Series(out, index=conf.index).where(c.notna())
    first = conf.astype(str).str.strip().str.lower().str[0]
    return first.map({"l": "low", "n": "nominal", "h": "high"})


def normalize_frame(df: pd.DataFrame, product: str, source_file: str) -> pd.DataFrame:
    sensor = detect_sensor(df.columns)
    hhmm = df["acq_time"].astype(str).str.strip().str.zfill(4)
    time_utc = pd.to_datetime(df["acq_date"].astype(str).str.strip() + " " + hhmm,
                              format="%Y-%m-%d %H%M", utc=True)
    if "type" in df.columns:
        fire_type = pd.to_numeric(df["type"], errors="coerce").astype("Int8")
    else:  # near-real-time files have no type column
        fire_type = pd.Series(pd.NA, index=df.index, dtype="Int8")
    sat = df["satellite"].astype(str).str.strip()

    out = pd.DataFrame({
        "lat": pd.to_numeric(df["latitude"], errors="coerce"),
        "lon": pd.to_numeric(df["longitude"], errors="coerce"),
        "time_utc": time_utc,
        "date": time_utc.dt.date,
        "sensor": sensor,
        "satellite": sat.map(SATELLITE_NAMES).fillna(sat),
        "product": product,
        "confidence_raw": df["confidence"].astype(str).str.strip(),
        "conf_class": confidence_class(sensor, df["confidence"]),
        "frp": pd.to_numeric(df["frp"], errors="coerce"),
        "brightness_k": pd.to_numeric(df["brightness" if sensor == "MODIS" else "bright_ti4"],
                                      errors="coerce"),
        "scan": pd.to_numeric(df["scan"], errors="coerce"),
        "track": pd.to_numeric(df["track"], errors="coerce"),
        "daynight": df["daynight"].astype(str).str.strip(),
        "fire_type": fire_type,
        "source_file": source_file,
    })
    out["use"] = (out["fire_type"].isna() | (out["fire_type"] == 0)).astype(bool) & \
        out["conf_class"].isin(["nominal", "high"])
    return out[COLUMNS]


def load_border(path: Path):
    gj = json.loads(path.read_text(encoding="utf-8"))
    feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
    geom = shapely.union_all([shape(f["geometry"]) for f in feats])
    shapely.prepare(geom)
    return geom


def clip_to_border(df: pd.DataFrame, border) -> pd.DataFrame:
    inside = shapely.contains_xy(border, df["lon"].to_numpy(), df["lat"].to_numpy())
    return df[inside]


def raw_files():
    """Yield (path, product) for every raw hotspot CSV."""
    for p in sorted((RAW / "yearly").glob("*.csv")):
        yield p, "SP"
    for p in sorted((RAW / "api").glob("*/*.csv")):
        yield p, "NRT" if p.parent.name.endswith("_NRT") else "SP"


def build() -> pd.DataFrame:
    frames = []
    for path, product in raw_files():
        df = pd.read_csv(path, dtype={"acq_time": str, "confidence": str, "acq_date": str})
        if df.empty:
            continue
        frames.append(normalize_frame(df, product, f"{path.parent.name}/{path.name}"))
    if not frames:
        raise SystemExit("No raw files found. Run: python -m pipeline.download")
    allpts = pd.concat(frames, ignore_index=True).dropna(subset=["lat", "lon"])

    border = load_border(RAW / "boundaries" / f"{COUNTRY_CODE}_ADM0.geojson")
    before = len(allpts)
    allpts = clip_to_border(allpts, border)
    print(f"Inside Ghana: {len(allpts):,} of {before:,} points")

    # Standard (SP) rows sort first, so they win over NRT duplicates
    allpts = allpts.sort_values(["product", "time_utc"], key=lambda s: s.map({"SP": 0, "NRT": 1})
                                if s.name == "product" else s)
    before = len(allpts)
    allpts = allpts.drop_duplicates(subset=["sensor", "satellite", "lat", "lon", "time_utc"])
    print(f"Duplicates removed: {before - len(allpts):,}")
    return allpts.sort_values("time_utc").reset_index(drop=True)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.assign(year=pd.to_datetime(df["date"]).dt.year)
    s = df.groupby(["year", "sensor"]).agg(points=("lat", "size"), used=("use", "sum")).unstack("sensor")
    s.columns = [f"{sensor.lower()}_{what}" for what, sensor in s.columns]
    return s.fillna(0).astype(int).reset_index()


def main() -> None:
    df = build()
    PROCESSED.mkdir(parents=True, exist_ok=True)
    # Temp file + rename, so the running API never reads a half-written file
    df.to_parquet(PROCESSED / "hotspots.parquet.tmp", index=False)
    os.replace(PROCESSED / "hotspots.parquet.tmp", PROCESSED / "hotspots.parquet")
    summary = summarize(df)
    summary.to_csv(PROCESSED / "summary_by_year.csv", index=False)

    print(f"\nSaved {len(df):,} hotspots to data/processed/hotspots.parquet")
    for (sensor, product), g in df.groupby(["sensor", "product"]):
        print(f"  {sensor:5} {product:3}  {len(g):>9,} points  {g['date'].min()} to {g['date'].max()}")
    print("\nPoints per year (all / used after filters):")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
