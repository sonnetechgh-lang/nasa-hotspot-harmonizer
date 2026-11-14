# NASA Hotspot Harmonizer — Ghana

Burning activity calendar for Ghana built from harmonized MODIS and VIIRS active-fire hotspots
(NASA Space Apps 2026: *Harmonization of MODIS and VIIRS Hot Spots*). See [PLAN.md](PLAN.md).

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then add your FIRMS_MAP_KEY
```

## Build the data

```powershell
.\.venv\Scripts\python -m pipeline.download    # ~400 MB of raw CSVs into data/raw (safe to re-run)
.\.venv\Scripts\python -m pipeline.normalize   # -> data/processed/hotspots.parquet
.\.venv\Scripts\python -m pipeline.calibrate   # tests grid sizes + methods -> calibration.json
.\.venv\Scripts\python -m pipeline.grid        # -> grid_daily.parquet, monthly_harmonized.parquet
.\.venv\Scripts\python scripts\preview.py      # -> web/preview.html (open it in a browser)
.\.venv\Scripts\python -m pytest -q tests
```

## Run the API

```powershell
.\.venv\Scripts\uvicorn api.main:app --port 8000
```

Interactive docs: http://127.0.0.1:8000/docs

| Endpoint | What it returns |
|---|---|
| `GET /api/meta` | Data dates, regions, harmonization settings and accuracy |
| `GET /api/regions` | Ghana border + 16 regions as GeoJSON |
| `GET/POST /api/calendar` | Monthly activity with unusual-month flags, normals, fire-season dates, per-season summaries, last-30-days status, plain-language summary |
| `GET/POST /api/daily?year=` | Daily activity for one year next to the normal for each day |
| `GET/POST /api/hotspots?year=&month=` | Hotspots for the map (most intense first) |
| `GET/POST /api/export.csv` | Monthly table with normals and flags |

Area: `region=Northern` (or `Ghana`), `bbox=west,south,east,north`, or a GeoJSON `geometry` in a POST body.
Options: `sensor=harmonized|modis|viirs`, `ref=2011-2025` (years used for normals; default all full years).

## Data sources

| Period | Source |
|---|---|
| MODIS Nov 2000 – Dec 2025, VIIRS S-NPP Jan 2012 – Dec 2025 | NASA FIRMS yearly country files (standard product) |
| 2026 until standard data ends | NASA FIRMS area API, `MODIS_SP` / `VIIRS_SNPP_SP` |
| After that, until today | NASA FIRMS area API, `MODIS_NRT` / `VIIRS_SNPP_NRT` |
| Ghana border and 16 regions | geoBoundaries (gbOpen, ADM0 / ADM1) |

We acknowledge the use of data and imagery from NASA's Fire Information for Resource Management
System (FIRMS), part of NASA's Earth Science Data and Information System (ESDIS).

## Output columns (`hotspots.parquet`)

`lat, lon, time_utc, date, sensor (MODIS|VIIRS), satellite, product (SP|NRT), confidence_raw,
conf_class (low|nominal|high), frp (MW), brightness_k, scan, track, daynight, fire_type, use, source_file`

`use` = vegetation fire (`fire_type` 0, or missing in NRT data) with nominal or high confidence.

## Deploy

Docker container behind Caddy on the VPS, with a daily data refresh: see [deploy/DEPLOY.md](deploy/DEPLOY.md).

## License

Code: MIT (see [LICENSE](LICENSE)). Fire data: NASA FIRMS (MODIS C6.1, VIIRS S-NPP). Boundaries: geoBoundaries (CC BY 4.0).
