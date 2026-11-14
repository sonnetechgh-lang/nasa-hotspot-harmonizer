"""Shared settings: paths, study area and secrets from .env."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"

COUNTRY_CODE = "GHA"
COUNTRY_NAME = "Ghana"
# west, south, east, north - a little wider than the border; points are clipped later
BBOX = (-3.30, 4.70, 1.25, 11.20)

FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov"

# Rough ecological zones by latitude: (name, upper latitude bound); last zone has no bound.
# South = forest, middle = forest-savanna transition, north = Guinea/Sudan savanna.
ZONES = [("south", 7.0), ("middle", 8.0), ("north", None)]

DUCKDB_MEMORY_LIMIT = "1GB"

# First year each sensor's record exists
SENSOR_START_YEAR = {"modis": 2000, "viirs-snpp": 2012}
# FIRMS API source names for each sensor
API_SOURCES = {
    "modis": {"SP": "MODIS_SP", "NRT": "MODIS_NRT"},
    "viirs-snpp": {"SP": "VIIRS_SNPP_SP", "NRT": "VIIRS_SNPP_NRT"},
}


def load_env() -> dict:
    """Read KEY=VALUE lines from .env without printing anything."""
    path = ROOT / ".env"
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def firms_key() -> str:
    key = load_env().get("FIRMS_MAP_KEY", "")
    if not key or key.startswith("your_"):
        raise SystemExit("FIRMS_MAP_KEY is missing from .env")
    return key
