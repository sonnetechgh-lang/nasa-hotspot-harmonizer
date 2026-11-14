# CLAUDE.md

NASA Space Apps 2026 entry, challenge *Harmonization of MODIS and VIIRS Hot Spots*.
Solo entrant. Study area: Ghana only. Registration deadline 15 Nov 2026.
Goal: a web app with a **burning activity calendar** for any area of Ghana, built from
harmonized MODIS + VIIRS active-fire hotspots (2000 to today). Full plan and timeline: `PLAN.md`.

## Status (as of 4 Nov 2026)

- Day 1 ✅ data download + normalize → `data/processed/hotspots.parquet` (4.2M hotspots)
- Day 2 ✅ grid + calibration → 0.025° grid, one factor per month, VIIRS as reference, 3.3% yearly error
- Day 3 ✅ FastAPI backend (`api/`), 32 tests passing
- Day 4 ✅ front end in `web/` (index.html, app.js, styles.css, method.html), tested in browser
  desktop + phone + dark mode. Map tiles: OpenStreetMap (CARTO now needs an API key).
- Day 5 ✅ `/api/compare` + "Regions compared" card, `scripts/findings.py` → `reports/findings.md`,
  low-activity rule (< 10 cell-days a normal season → no season dates or flags), 34 tests.
- Frontend upgrade ✅ (5 Nov, judge-focused, plain HTML/JS, clean neutral look): headline strip
  of national findings, sticky area picker + section menu, "how to use" hint, "One record from two
  sensors" chart (`yearly` in /api/calendar), map modes Regions / Burning / Change / ▶ Play season
  (`/api/animation`, 0.2° weekly grid), save-as-image on every chart, copy link, toast, spinners.
  38 tests pass.
- Outlook ✅ (5 Nov): `pipeline/forecast.py` + `/api/forecast` (start/end = "YYYY-MM", next 12 months,
  harmonized only) + "Outlook" card in `web/` and method.html §5. Model = ½ last-5-year same-month mean
  + ½ 15-season Theil-Sen trend line; range = 10–90% of backtest misses at this time of year; trend
  "clear" if Mann-Kendall p < 0.05. Backtest 2011–25, Ghana + 16 regions: 12-month error ~10% vs ~15%
  for the normal. Missed 2025/26 (forecast ~82k, actual ~61k). 47 tests pass.
- UI redesign ✅ (9 Nov): Apple-style (Inter/SF type, #f5f5f7 page, white 22px cards, frosted sticky nav),
  little text (ⓘ popover sheets replace fine print), CSS-only sliding segmented controls (`.seg`, equal widths,
  thumb via `:has`), icon buttons for table/save on every chart (every chart has a table view), own map tools
  (zoom/fit/draw rectangle/polygon/clear via `L.Draw.*` handlers, no Leaflet.draw toolbar), toasts with a
  "View" action, scroll-reveal motion (off under reduced motion). Order: Overview, Calendar, Seasons, Outlook,
  Regions, Sensors. Tested desktop + 390 px phone + dark mode; every control clicked through.
- Districts ✅ (9 Nov): 260 districts (geoBoundaries ADM2, `pipeline/download.py` fetches it) usable anywhere a
  region is, through the same `region=` parameter (names never clash). `/api/meta` lists them with their region,
  `/api/districts` serves simplified outlines. Picker groups districts under each region; map shows the selected
  region's districts (drill down), all of them at zoom ≥ 9.5. Regions compare/animation stay region-level. 49 tests.
  User rejected a topic colour scheme + coloured icon tiles: keep the neutral look.
- GitHub ✅ (14 Nov): public repo https://github.com/sonnetechgh-lang/nasa-hotspot-harmonizer (MIT, data/ not committed).
- Deploy files ✅ (14 Nov): VPS (Ubuntu 26.04, 2 cores, 7.7 GB, root@187.7.21.43) already runs other apps in
  Docker behind a shared Caddy container (network `proxy`, `/opt/apps/proxy/Caddyfile`, auto HTTPS, DNS at
  Spaceship). So: `Dockerfile` + `compose.yml` (container `hotspots_app`, in `/opt/apps/hotspots`),
  `deploy/Caddyfile.snippet`, host timer `deploy/hotspots-refresh.*` (docker exec refresh.sh, then restart).
  Pipeline writes are atomic (tmp + os.replace). Never disturb the other apps or their containers.
- **Next: Day 6** run the deploy on the user's VPS (nginx + systemd + HTTPS + daily data refresh:
  download → normalize → grid; recalibrate only on purpose), then slides, demo video.
- The user's own server often runs on port 8000 (started from VS Code). Don't kill it; test on 8001.
- New findings: 2025/26 was the lowest season on record (−38%), below normal in all 16 regions;
  season moves north → south (Upper East peak ~23 Nov, Central ~18 Apr); Savannah, Northern,
  Bono East and Bono = 68% of the national drop.
- The user asked to always use the caveman MCP tools in this project: compress large tool output
  with `caveman_compress`, recover trimmed output with `caveman_retrieve` instead of re-running.

## Commands (PowerShell, from the project folder)

```powershell
.\.venv\Scripts\python -m pipeline.download    # safe to re-run; refreshes last 10 days
.\.venv\Scripts\python -m pipeline.normalize
.\.venv\Scripts\python -m pipeline.calibrate
.\.venv\Scripts\python -m pipeline.grid
.\.venv\Scripts\python -m pytest -q -p no:warnings tests
.\.venv\Scripts\uvicorn api.main:app --port 8000   # docs at /docs, web files served at /
.\.venv\Scripts\python scripts\preview.py          # web/preview.html (Day 2 check page)
```

## Key design decisions

- Count **active cell-days** (grid cell with ≥1 usable hotspot on a day), not raw points.
- Harmonized = MODIS × monthly factor before VIIRS start (2012-01-20), VIIRS S-NPP after.
  Terra-only factors before Aqua start (2002-07-19); those months are marked `confidence: lower`.
- Usable hotspot = `fire_type` 0 (or missing in NRT) and nominal/high confidence.
- Normals default to all full years (harmonized 2001–2025); `ref=` lets users pick e.g. 2011–2025.
- Fire year starts at the quietest point of the year (~1 Jul for Ghana).
- Findings so far: Ghana season ~27 Nov – 15 Mar, peak ~4 Jan; 2021–25 is 28% below 2003–11;
  Greater Accra shows no decline (+1%); last full season 2025/26 was 38% below normal.

## Environment notes

- Windows; PC is often low on memory. DuckDB is capped at 1 GB (`DUCKDB_MEMORY_LIMIT`).
  Don't leave background servers running.
- DuckDB 2.0: relations have no `fetchone()`; use `.fetchall()[0]`.
- pandas 3: "no flag" values come back as NaN, not None (API `clean()` converts).
- PowerShell mangles inline Python with `*` or quotes; use Bash heredocs for ad-hoc scripts.
- A "caveman" hook trims long command output ("sections elided (caveman)"); print short output.
- Keys live in `.env` (FIRMS_MAP_KEY, Earthdata login). Never print or commit them.
- Charts follow the dataviz guidance: categorical MODIS blue / VIIRS orange / Harmonized aqua,
  single-hue blue ramp for the calendar, light + dark themes, table view for every chart.
