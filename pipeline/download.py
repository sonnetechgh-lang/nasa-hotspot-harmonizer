"""Download every MODIS and VIIRS (S-NPP) hotspot for Ghana, 2000 to today.

Sources, in order:
  1. FIRMS yearly country files (standard quality data) for every full year.
  2. FIRMS area API, standard product (SP), for the current year until SP ends.
  3. FIRMS area API, near-real-time product (NRT), from there until today.
  4. geoBoundaries outlines for Ghana (country + 16 regions).

Files already downloaded are skipped, so the script is safe to re-run.
Run:  python -m pipeline.download
"""
import csv
import io
import sys
from datetime import date, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import (API_SOURCES, BBOX, COUNTRY_CODE, COUNTRY_NAME, FIRMS_BASE,
                     RAW, SENSOR_START_YEAR, firms_key)

API_DAYS_PER_CALL = 5     # FIRMS area API window size
REFRESH_RECENT_DAYS = 10  # re-download API windows this recent (NRT data still arriving)


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=6, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "nasa-hotspot-harmonizer/0.1"
    return s


class Redactor:
    """Keeps the API key out of anything we print."""
    def __init__(self, key: str):
        self.key = key

    def __call__(self, text: str) -> str:
        return text.replace(self.key, "***") if self.key else text


def save_csv(text: str, path) -> int:
    """Check it is a hotspot CSV, write it atomically, return the row count."""
    if not text.startswith("latitude"):
        raise ValueError(f"not a hotspot CSV: {text[:120]!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)
    return max(text.count("\n") - 1, 0)


# ---------- 1. yearly country files ----------

def download_yearly(session, sensor: str) -> int:
    """Download all full-year files for a sensor. Returns the first year with no file."""
    year = SENSOR_START_YEAR[sensor]
    while True:
        name = f"{sensor}_{year}_{COUNTRY_NAME}.csv"
        path = RAW / "yearly" / name
        if path.exists() and path.stat().st_size > 0:
            print(f"  have  {name}")
            year += 1
            continue
        url = f"{FIRMS_BASE}/data/country/{sensor}/{year}/{name}"
        r = session.get(url, timeout=180)
        if r.status_code == 404:
            print(f"  none  {name} (not published yet)")
            return year
        r.raise_for_status()
        n = save_csv(r.text, path)
        print(f"  got   {name}: {n:,} rows")
        year += 1


# ---------- 2 + 3. FIRMS area API ----------

def data_availability(session, key, redact) -> dict:
    r = session.get(f"{FIRMS_BASE}/api/data_availability/csv/{key}/ALL", timeout=60)
    r.raise_for_status()
    rows = csv.DictReader(io.StringIO(r.text))
    out = {row["data_id"]: (date.fromisoformat(row["min_date"]), date.fromisoformat(row["max_date"]))
           for row in rows}
    if not out:
        raise RuntimeError("empty availability response: " + redact(r.text[:200]))
    return out


def download_api_range(session, key, redact, source: str, start: date, end: date) -> None:
    """Download [start, end] in API_DAYS_PER_CALL windows."""
    if start > end:
        return
    bbox = ",".join(str(v) for v in BBOX)
    today = date.today()
    total = 0
    d = start
    while d <= end:
        days = min(API_DAYS_PER_CALL, (end - d).days + 1)
        path = RAW / "api" / source / f"{d.isoformat()}_{days}d.csv"
        recent = (today - d).days <= REFRESH_RECENT_DAYS
        if path.exists() and not recent:
            d += timedelta(days=days)
            continue
        url = f"{FIRMS_BASE}/api/area/csv/{key}/{source}/{bbox}/{days}/{d.isoformat()}"
        r = session.get(url, timeout=180)
        if r.status_code != 200:
            raise RuntimeError(f"{source} {d}: HTTP {r.status_code} {redact(r.text[:200])}")
        try:
            total += save_csv(r.text, path)
        except ValueError as e:
            raise RuntimeError(f"{source} {d}: {redact(str(e))}") from None
        d += timedelta(days=days)
    print(f"  {source}: {start} to {end} done ({total:,} new rows in bounding box)")


# ---------- 4. boundaries ----------

def download_boundaries(session) -> None:
    for level in ("ADM0", "ADM1", "ADM2"):
        path = RAW / "boundaries" / f"{COUNTRY_CODE}_{level}.geojson"
        if path.exists():
            print(f"  have  {path.name}")
            continue
        meta = session.get(f"https://www.geoboundaries.org/api/current/gbOpen/{COUNTRY_CODE}/{level}/",
                           timeout=60)
        meta.raise_for_status()
        gj = session.get(meta.json()["gjDownloadURL"], timeout=180)
        gj.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gj.content)
        print(f"  got   {path.name}")


def main() -> None:
    key = firms_key()
    redact = Redactor(key)
    session = make_session()

    print("Boundaries")
    download_boundaries(session)

    avail = data_availability(session, key, redact)
    today = date.today()

    for sensor, sources in API_SOURCES.items():
        print(f"\n{sensor.upper()} yearly files")
        first_missing_year = download_yearly(session, sensor)

        sp_min, sp_max = avail[sources["SP"]]
        nrt_min, nrt_max = avail[sources["NRT"]]
        sp_start = max(date(first_missing_year, 1, 1), sp_min)
        print(f"{sensor.upper()} API")
        download_api_range(session, key, redact, sources["SP"], sp_start, sp_max)

        nrt_start = max(sp_max + timedelta(days=1), sp_start, nrt_min)
        if nrt_min > sp_max + timedelta(days=1):
            print(f"  warning: gap {sp_max + timedelta(days=1)} to {nrt_min - timedelta(days=1)} "
                  f"(no SP or NRT data)")
        download_api_range(session, key, redact, sources["NRT"], nrt_start, min(nrt_max, today))

    print("\nDownload complete.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
