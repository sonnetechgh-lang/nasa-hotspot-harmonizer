"""Snap hotspots to a regular grid and build the harmonized record.

Active cell-day = a grid cell with at least one usable hotspot from a sensor on one day.
Counting cells instead of points removes most of the pixel-size difference
(several 375 m VIIRS pixels on one fire collapse into one cell).

Harmonized record (VIIRS-equivalent units):
  - before VIIRS started (20 Jan 2012): MODIS cell-days x calibration factor
  - from then on: VIIRS cell-days
VIIRS is the reference because it is the sensor that continues after MODIS ends.

Outputs:
  data/processed/grid_daily.parquet         one row per sensor, day and active cell
  data/processed/monthly_harmonized.parquet one row per year, month and zone
Run:  python -m pipeline.grid   (after python -m pipeline.calibrate)
"""
import json

import duckdb
import pandas as pd

from .config import DUCKDB_MEMORY_LIMIT, PROCESSED, ZONES

HOTSPOTS = (PROCESSED / "hotspots.parquet").as_posix()
CALIBRATION = PROCESSED / "calibration.json"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{DUCKDB_MEMORY_LIMIT}'")
    con.execute(f"CREATE VIEW hotspots AS SELECT * FROM '{HOTSPOTS}' WHERE use")
    return con


def zone_sql(lat_expr: str) -> str:
    whens = " ".join(f"WHEN {lat_expr} < {hi} THEN '{name}'" for name, hi in ZONES if hi is not None)
    return f"CASE {whens} ELSE '{ZONES[-1][0]}' END"


def cell_days_sql(cell: float, who: str = "sensor") -> str:
    """SQL giving one row per (who, date, grid cell) with at least one usable hotspot."""
    return f"""
        SELECT {who} AS who, date,
               CAST(floor(lon / {cell}) AS INTEGER) AS ix,
               CAST(floor(lat / {cell}) AS INTEGER) AS iy,
               count(*) AS n, sum(frp) AS frp_sum, max(frp) AS frp_max
        FROM hotspots GROUP BY ALL"""


def monthly_cell_days(con, cell: float, who: str = "sensor") -> pd.DataFrame:
    """Active cell-days per (who, year, month, zone)."""
    lat = f"((iy + 0.5) * {cell})"
    return con.sql(f"""
        SELECT who, year(date) AS y, month(date) AS m, {zone_sql(lat)} AS z, count(*) AS cd
        FROM ({cell_days_sql(cell, who)}) GROUP BY ALL""").df()


def viirs_start(con):
    return con.sql("SELECT min(date) FROM hotspots WHERE sensor = 'VIIRS'").fetchall()[0][0]


def harmonized_weight_sql(cal: dict, g: str) -> str:
    """Weight of one grid cell-day row in the harmonized record.
    Needs joined factor tables aliased f (MODIS) and t (Terra only)."""
    return f"""CASE
        WHEN {g}.date >= DATE '{cal['viirs_start']}' THEN CASE WHEN {g}.sensor = 'VIIRS' THEN 1 ELSE 0 END
        WHEN {g}.sensor <> 'MODIS' THEN 0
        WHEN {g}.date < DATE '{cal['aqua_start']}' THEN t.k
        ELSE f.k END"""


def build(con, cal: dict) -> pd.DataFrame:
    cell = cal["cell_deg"]
    PROCESSED.mkdir(parents=True, exist_ok=True)
    grid_path = (PROCESSED / "grid_daily.parquet").as_posix()
    con.execute(f"""
        COPY (
          SELECT who AS sensor, date, ix, iy,
                 round((iy + 0.5) * {cell}, 4) AS lat_c, round((ix + 0.5) * {cell}, 4) AS lon_c,
                 {zone_sql(f'((iy + 0.5) * {cell})')} AS zone, n, frp_sum, frp_max
          FROM ({cell_days_sql(cell)}) ORDER BY date
        ) TO '{grid_path}' (FORMAT parquet)""")

    con.register("factors", pd.DataFrame(cal["factors"]))               # columns z, m, k
    con.register("factors_terra", pd.DataFrame(cal["factors_terra_only"]))
    monthly = con.sql(f"""
        SELECT year(g.date) AS year, month(g.date) AS month, g.zone,
               count(*) FILTER (WHERE g.sensor = 'MODIS') AS modis_cd,
               count(*) FILTER (WHERE g.sensor = 'VIIRS') AS viirs_cd,
               sum({harmonized_weight_sql(cal, 'g')}) AS harmonized
        FROM '{grid_path}' g
        JOIN factors f ON f.z = g.zone AND f.m = month(g.date)
        JOIN factors_terra t ON t.z = g.zone AND t.m = month(g.date)
        GROUP BY ALL ORDER BY ALL""").df()
    monthly.to_parquet(PROCESSED / "monthly_harmonized.parquet", index=False)
    return monthly


def main() -> None:
    if not CALIBRATION.exists():
        raise SystemExit("Run python -m pipeline.calibrate first")
    cal = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    con = connect()
    monthly = build(con, cal)
    yearly = monthly.groupby("year")[["modis_cd", "viirs_cd", "harmonized"]].sum().round().astype(int)
    print(f"Grid {cal['cell_deg']} deg, model {cal['model']}. Active cell-days per year:")
    print(yearly.to_string())


if __name__ == "__main__":
    main()
