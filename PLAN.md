# NASA Hotspot Harmonizer — Project Plan

**Challenge:** NASA Space Apps 2026: *Harmonization of MODIS and VIIRS Hot Spots*
**Team:** solo
**Registration deadline:** 15 November 2026
**Plan updated:** 4 November 2026 (after reading the full challenge description)

---

## 1. What the challenge asks for

> Build a **web application** that harmonizes MODIS and VIIRS fire hotspot records into a **burning activity calendar**, so responders, scientists and land managers can examine **historical fire patterns, unusual conditions and critical periods** within a **selected area of interest**.

| Requirement from the challenge | How we meet it |
|---|---|
| Web application | FastAPI backend + browser front end, hosted on your VPS |
| Harmonize records across sensors | Common grid + quality filters + calibration over the 2012–present overlap, giving one continuous record from 2000 to now (section 4) |
| Burning activity calendar | Heatmaps of year × month and year × day for the selected area |
| Historical fire patterns | 20+ years of data (MODIS since Nov 2000, VIIRS since 2012) |
| Unusual conditions | Each month/week compared with the long-term normal; flagged when above the 90th percentile |
| Critical periods | Fire-season start, peak and end worked out from the long-term average |
| Selected area of interest | Draw a box or polygon on a map, or pick a country/region |
| Users: early warning, scientists, land managers | Plain-language summary panel, CSV export, and an explanation of the method |

**Dropped from the earlier idea:** Telegram bot and air quality. The bot becomes an optional extra (section 9).

---

## 2. Why harmonization is needed

| | MODIS | VIIRS |
|---|---|---|
| Satellites | Terra, Aqua | Suomi NPP, NOAA-20, NOAA-21 |
| Record starts | Nov 2000 (Terra), Jul 2002 (Aqua) | Jan 2012 (Suomi NPP) |
| Pixel size | ~1 km | ~375 m |
| Confidence | number from 0 to 100 | low / nominal / high |
| Detects small fires | Fewer | Many more |

If you plot the raw counts, burning seems to jump in 2012. That's only because VIIRS started then and picks up more and smaller fires, not because more burning happened. The Terra and Aqua satellites (MODIS) are also near the end of their missions, so future data will be VIIRS only. A harmonized record keeps the long-term history comparable.

---

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.13** | Already installed. Has the best tools for geospatial and data work. |
| Data processing | **pandas + numpy** | Reading the CSV archives, cleaning, putting both sensors into one format, and the calibration math. |
| Storage / querying | **DuckDB + Parquet files** | 20+ years of detections means tens of millions of rows. DuckDB adds up totals for any area very quickly from one file, with no database server. |
| Geospatial | **shapely (+ geopandas for prep)** | Clips detections to the area the user draws and assigns grid cells. |
| Backend API | **FastAPI + uvicorn** | Small JSON API that serves calendar data to the front end. It's fast to write and generates its own docs. |
| Map | **Leaflet.js + Leaflet.draw** | Free, needs no API key. Users draw their area of interest. |
| Calendar & charts | **Apache ECharts** | Has a built-in calendar heatmap, plus line and band charts for the "normal vs. this year" view. One library covers all the charts. |
| Front end | **Plain HTML + JS (no build step)** | Quickest for one person and easy to host. Nothing to compile. |
| Analysis | **Jupyter Notebook + matplotlib** | Testing calibration settings and making figures for the slides. |
| Hosting | **Your VPS + nginx + systemd** | You already run apps there. nginx serves the site and forwards API calls to uvicorn. |
| HTTPS | **Let's Encrypt (certbot)** | Free certificate so judges get a secure link. |
| Version control | **Git + GitHub (public repo)** | Git is installed. Judges expect a public repo. |

---

## 4. How the harmonization works

| Step | What happens | Why |
|---|---|---|
| 1. Load archives | Standard (quality-checked) MODIS and VIIRS archives for the demo regions, plus near-real-time data for the latest days | Standard archives are the best data for history; near-real-time keeps "this year" current |
| 2. Remove non-fires | Drop volcanoes, static industrial heat sources and offshore points (`type` ≠ 0) | Gas flares and factories would distort the calendar |
| 3. Confidence filter | MODIS ≥ 30 (nominal+) and VIIRS nominal/high, both using one shared low/nominal/high scale | Same quality bar for both sensors |
| 4. Common grid | Snap each detection to a ~1 km grid cell (0.01°) and count **active cell-days** instead of raw pixels | VIIRS's smaller pixels pick up one fire several times. Counting cells instead of pixels removes most of the resolution difference. |
| 5. Calibrate in the overlap | For 2012–present, compare MODIS and VIIRS cell-day counts month by month, per region, and fit a scale factor (with R²) | Gives a measured conversion between the two sensors, not a guess |
| 6. Build a continuous record | Put the whole series in one unit ("VIIRS-equivalent" activity) from 2000 to now: MODIS × factor before VIIRS started, VIIRS after. Separate Terra-only factors before Aqua's data starts (July 2002). Both raw series stay available | One unbroken calendar. VIIRS is the reference because it continues after MODIS ends. |
| 7. Normals & anomalies | For every month/week: long-term mean, 10th and 90th percentiles; flag values above the 90th percentile | "Unusual conditions" |
| 8. Critical periods | Season start/peak/end = when the long-term average rises above or falls below a share of the yearly peak | "Critical periods" for planning |

Users can switch the calendar between **MODIS / VIIRS / harmonized**, so judges see the jump in raw data and the fix side by side.

---

## 5. App features

| Feature | Description | Priority |
|---|---|---|
| Area of interest | Draw a box/polygon or pick a demo region | Must |
| Calendar heatmap | Year × month grid, colour = burning activity | Must |
| Daily calendar | GitHub-style day grid for a chosen year | Must |
| Sensor toggle | MODIS / VIIRS / harmonized | Must |
| Normal vs. selected year | Seasonal curve with a 10–90% band and the selected year overlaid | Must |
| Unusual-condition flags | Calendar cells outlined when above the 90th percentile | Must |
| Critical periods summary | e.g. "Fire season: Nov–Mar, peak in Jan" | Must |
| Drill-down map | Click a calendar cell to see its hotspots on the map | Should |
| CSV export | Download the calendar data | Should |
| Method page | Plain-language explanation of the harmonization, calibration numbers and data credits | Must (for judging) |
| Current status | Last 7 days vs. normal for this time of year (early warning) | Could |
| Telegram alerts | Alert when the area goes above normal | Optional extra |

---

## 6. Data sources

| Source | What it gives | Why we need it | Access |
|---|---|---|---|
| **FIRMS MODIS C6.1 standard archive** | Quality-checked MODIS hotspots, 2000–present | The long history | FIRMS archive download (yearly files by country, or custom request) |
| **FIRMS VIIRS (S-NPP) 375 m standard archive** | Quality-checked VIIRS hotspots, 2012–present | Overlap period for calibration and the recent record | FIRMS archive download |
| **FIRMS NRT feeds** (`MODIS_NRT`, `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`) | Last few days | Keeps the current year up to date | FIRMS area API (MAP_KEY) |
| **FIRMS documentation (attribute descriptions)** | Meaning of `confidence`, `type`, `scan`, `track` | So our filters match NASA's definitions | Public |
| **Natural Earth country/region boundaries** | Outlines | Pick-a-region option | Free download |

API pattern for NRT:
`https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{DAYS}`

**Study area: Ghana (all data).**

| Item | Value |
|---|---|
| Country code (FIRMS) | `GHA` |
| Bounding box | lon −3.26 to 1.20, lat 4.73 to 11.17 |
| Yearly archive files | FIRMS country downloads, e.g. `modis_2020_Ghana.csv`, `viirs-snpp_2020_Ghana.csv` (https://firms.modaps.eosdis.nasa.gov/country/) |
| Recent data | FIRMS country API: `/api/country/csv/{MAP_KEY}/{SOURCE}/GHA/{DAYS}` |
| Regions for "pick a region" | Ghana's 16 administrative regions (geoBoundaries ADM1, https://www.geoboundaries.org/). Natural Earth still shows the old 10 regions. |
| Expected pattern | Dry-season burning, roughly Nov–Mar, peaking Dec–Feb, strongest in the northern savanna (Northern, Savannah, Upper East/West, Bono East). Much less in the southern forest zone. |
| Why it's a good demo | Strong, regular fire seasons make the calendar easy to read, and comparing north and south shows the area-of-interest feature well |

---

## 7. Accounts and keys needed

| Item | Why | Where | Cost |
|---|---|---|---|
| **NASA FIRMS MAP_KEY** | NRT API calls and archive requests | https://firms.modaps.eosdis.nasa.gov/api/map_key/ | Free |
| **NASA Earthdata login** | Some archive downloads need it | https://urs.earthdata.nasa.gov/ | Free |
| **GitHub account + public repo** | Submission link | https://github.com | Free |
| **VPS access** | Hosts the live web app | Your existing server | Already paid |
| **Domain or subdomain** (optional) | Cleaner link for judges plus HTTPS | Any domain you own | Optional |
| **Space Apps registration + team page** | Required to submit | spaceappschallenge.org | Free; deadline 15 Nov |
| ~~Telegram bot token~~ | Only if we build the optional alerts | `@BotFather` | Free |

Keys go in a `.env` file that is **never committed** to Git.

---

## 8. Tools on your PC

| Tool | Why | Status |
|---|---|---|
| Python 3.13 + pip | Runs everything | Installed |
| Git | Version control | Installed |
| VS Code | Editing the code | Install if missing |
| Jupyter (`pip install notebook`) | Calibration testing + figures | To install |
| Free disk on D: | Raw archive CSVs before converting them to Parquet (Ghana needs < 1 GB) | ✅ 24.7 GB free |
| QGIS (optional) | Checking hotspots visually | Optional |
| OBS Studio / Windows screen recorder | Demo video | To install if needed |

---

## 9. Folder layout

```
nasa-hotspot-harmonizer/
├── PLAN.md
├── README.md
├── .env.example            # FIRMS_MAP_KEY placeholder
├── requirements.txt
├── data/                   # not committed to Git
│   ├── raw/                # downloaded archive CSVs
│   └── processed/          # hotspots.parquet, grid_daily.parquet
├── pipeline/
│   ├── download.py         # archive + NRT fetch
│   ├── normalize.py        # shared format, type/confidence filters
│   ├── grid.py             # snap to 0.01° grid, cell-day counts
│   ├── calibrate.py        # overlap regression → scale factors
│   └── climatology.py      # normals, percentiles, critical periods
├── api/
│   └── main.py             # FastAPI: /calendar /climatology /hotspots /export
├── web/
│   ├── index.html          # map + calendar + charts
│   ├── app.js
│   └── method.html         # how it works + data credits
├── notebooks/
│   └── calibration.ipynb   # checks + slide figures
├── deploy/
│   ├── nginx.conf
│   └── harmonizer.service  # systemd unit
└── tests/
    └── test_pipeline.py
```

---

## 10. Timeline

| When | Task | Done when |
|---|---|---|
| **Before 15 Nov** | Register, create the team page. ~~FIRMS key~~ ✅ ~~Earthdata login~~ ✅ ~~Study area~~ ✅ Ghana | Key in `.env`, registration confirmed |
| Day 1 ✅ | `download.py` + `normalize.py`; load all Ghana archives (MODIS 2000–now, VIIRS 2012–now) | Done 4 Nov: 4,209,766 hotspots in `hotspots.parquet` (79 MB), Nov 2000 to 4 Nov 2026 |
| Day 2 ✅ | `grid.py` + `calibrate.py` + preview check | Done 4 Nov: 0.025° grid, one factor per month (chosen by leave-one-year-out testing over 16 grid/method combinations), 3.3% average yearly error; no 2012 jump |
| Day 3 ✅ | `climatology.py` + FastAPI endpoints | Done 4 Nov: calendar, normals, unusual months, season dates, fire-year summaries, last-30-days status, hotspots, CSV export for any region / box / polygon; < 1 s per request; 32 tests pass |
| Day 4 ✅ | Front end: map, area drawing, calendar heatmap, sensor toggle | Done 5 Nov: `web/index.html` + `app.js` + `styles.css` + `method.html`; region picker, map click, polygon/box drawing, fire-year calendar with unusual-month outlines, season vs normal, season totals, daily calendar, hotspot drill-down, CSV export, shareable links, light/dark, phone layout |
| Day 5 ✅ | Regions compared panel, findings report, edge cases | Done 5 Nov: `/api/compare` (warmed up at startup), "Regions compared" card (season timeline + change by region + table), `reports/findings.md` via `scripts/findings.py`, low-activity rule for tiny areas; 34 tests pass |
| Day 6 | Deploy to VPS with HTTPS, README, slides, 30-second video | Live link + submission package |
| Event weekend | Final polish and submit | Submitted on spaceappschallenge.org |

Event dates still need to be confirmed on the Space Apps site. Move the days to fit.

---

## 11. What we submit

| Item | Purpose |
|---|---|
| Live web app link | Judges try the calendar for an area themselves |
| Public GitHub repo | Code + README with setup steps |
| 30-second demo video | What judges see first |
| Slides (5–7) | Problem (the 2012 jump) → method → calendar demo → findings → who benefits |
| Method page + data credits | Shows the harmonization is sound and credits the NASA data |

---

## 12. Risks

| Risk | Plan |
|---|---|
| Archive data is large | Ghana only keeps it small (well under 1 GB); convert to Parquet; pre-aggregate grid cell-days |
| Archive download is slow or needs a request form | Start downloads before the event; use yearly country files where available |
| Calibration differs by region or season | Fit per region (and by season if needed); show R² openly on the method page |
| The drawn area goes outside Ghana | Clip it to Ghana's border and tell the user clearly |
| Calibration differs between north (savanna) and south (forest) | Fit separate scale factors for each ecological zone if one country-wide factor fits poorly |
| Little time as a solo entrant | Must-have features first; drill-down, export and alerts can be cut |
| Space Apps site certificate error seen on this PC | Check in your browser before entering registration details |
