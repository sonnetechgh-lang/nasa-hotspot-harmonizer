"""Data access: daily activity for a set of grid cells, and raw hotspots for the map."""
import json
import threading

import duckdb
import pandas as pd
import shapely

from pipeline.config import DUCKDB_MEMORY_LIMIT, PROCESSED
from pipeline.grid import harmonized_weight_sql

CELL_KEY = 100_000  # cell id = ix * CELL_KEY + iy


def cell_key_sql(ix: str, iy: str) -> str:
    return f"({ix}::BIGINT * {CELL_KEY} + {iy})"


SENSORS = ("harmonized", "modis", "viirs")
# Default reference years for normals: every full year each record covers well
REF_YEARS = {"harmonized": (2001, 2025), "modis": (2003, 2025), "viirs": (2013, 2025)}
# Earliest year a custom reference period may start, per record
REF_EARLIEST = {"harmonized": 2001, "modis": 2001, "viirs": 2013}


class Store:
    def __init__(self):
        self.cal = json.loads((PROCESSED / "calibration.json").read_text(encoding="utf-8"))
        self.lock = threading.Lock()
        self.con = duckdb.connect()
        self.con.execute(f"SET memory_limit='{DUCKDB_MEMORY_LIMIT}'")
        self.con.execute("SET threads=2")
        grid = (PROCESSED / "grid_daily.parquet").as_posix()
        self.hotspots_path = (PROCESSED / "hotspots.parquet").as_posix()
        self.con.register("factors", pd.DataFrame(self.cal["factors"]))
        self.con.register("factors_terra", pd.DataFrame(self.cal["factors_terra_only"]))
        # Load the grid into memory once (~40 MB), with each row's harmonized weight
        # already worked out, so area queries are a single join on one key.
        self.con.execute(f"""
            CREATE TABLE grid AS
            SELECT gr.date, {cell_key_sql('gr.ix', 'gr.iy')} AS cell,
                   gr.ix::SMALLINT AS ix, gr.iy::SMALLINT AS iy,
                   (gr.sensor = 'MODIS')::TINYINT AS is_modis,
                   (gr.sensor = 'VIIRS')::TINYINT AS is_viirs,
                   ({harmonized_weight_sql(self.cal, 'gr')})::REAL AS w
            FROM '{grid}' gr
            JOIN factors f ON f.z = gr.zone AND f.m = month(gr.date)
            JOIN factors_terra t ON t.z = gr.zone AND t.m = month(gr.date)""")
        first, last = self.con.sql("SELECT min(date), max(date) FROM grid").fetchall()[0]
        self.first, self.last = pd.Timestamp(first), pd.Timestamp(last)

    def daily(self, cells: pd.DataFrame) -> pd.DataFrame:
        """Daily MODIS, VIIRS and harmonized activity for the given cells, zeros filled in."""
        keys = pd.DataFrame({"cell": cells.ix.astype("int64") * CELL_KEY + cells.iy.astype("int64")})
        with self.lock:
            self.con.register("aoi", keys)
            df = self.con.sql("""
                SELECT date, sum(is_modis) AS modis, sum(is_viirs) AS viirs, sum(w) AS harmonized
                FROM grid SEMI JOIN aoi USING (cell)
                GROUP BY date ORDER BY date""").df()
            self.con.unregister("aoi")
        df["date"] = pd.to_datetime(df["date"])
        full = pd.date_range(self.first, self.last, freq="D")
        return df.set_index("date").reindex(full, fill_value=0).astype(float)

    def weekly_coarse(self, coarse_deg: float, fy_month: int, fy_day: int,
                      start_years: tuple[int, int]) -> pd.DataFrame:
        """Harmonized activity per coarse cell (cx, cy) and week of the fire year, summed over
        the fire years that start in start_years[0]..start_years[1]."""
        f = int(round(coarse_deg / self.cal["cell_deg"]))   # fine cells per coarse cell side
        m, d = fy_month, fy_day
        with self.lock:
            return self.con.sql(f"""
                WITH t AS (
                  SELECT CAST(floor(ix / {f}) AS INTEGER) AS cx, CAST(floor(iy / {f}) AS INTEGER) AS cy,
                         date, w,
                         CASE WHEN month(date) > {m} OR (month(date) = {m} AND day(date) >= {d})
                              THEN year(date) ELSE year(date) - 1 END AS fy
                  FROM grid)
                SELECT cx, cy,
                       LEAST(CAST(floor(datediff('day', make_date(fy, {m}, {d}), date) / 7) AS INTEGER), 51) AS week,
                       sum(w) AS v
                FROM t WHERE fy BETWEEN {start_years[0]} AND {start_years[1]}
                GROUP BY ALL""").df()

    def hotspots(self, geom, start, end, limit: int = 5000) -> tuple[pd.DataFrame, int]:
        """Usable hotspots in the area and date range; the most intense first."""
        w, s, e, n = geom.bounds
        with self.lock:
            df = self.con.sql(f"""
                SELECT lat, lon, time_utc, sensor, satellite, conf_class, frp
                FROM '{self.hotspots_path}'
                WHERE use AND date BETWEEN DATE '{start}' AND DATE '{end}'
                  AND lat BETWEEN {s} AND {n} AND lon BETWEEN {w} AND {e}""").df()
        df = df[shapely.contains_xy(geom, df.lon.to_numpy(), df.lat.to_numpy())]
        total = len(df)
        return df.sort_values("frp", ascending=False).head(limit), total
