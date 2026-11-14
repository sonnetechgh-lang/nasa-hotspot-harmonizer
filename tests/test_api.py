"""API tests against the real processed data (skipped if it hasn't been built)."""
import warnings

import pytest

from pipeline.config import PROCESSED

pytestmark = pytest.mark.skipif(not (PROCESSED / "calibration.json").exists(),
                                reason="processed data not built")


@pytest.fixture(scope="module")
def client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
        from api.main import app
    return TestClient(app)


def test_calendar_for_ghana(client):
    d = client.get("/api/calendar?region=Ghana").json()
    assert d["area"]["name"] == "Ghana"
    assert d["season"]["peak"].endswith(("Dec", "Jan"))
    assert len(d["normals"]) == 12
    assert {"value", "flag", "confidence"} <= set(d["monthly"][0])
    assert d["monthly"][0]["confidence"] == "lower"      # Nov 2000: Terra only
    assert d["summary"]


def test_region_names_are_flexible(client):
    a = client.get("/api/calendar?region=upper east").json()
    b = client.get("/api/calendar?region=Upper East Region").json()
    assert a["area"]["name"] == b["area"]["name"] == "Upper East Region"


def test_reference_period_changes_normals(client):
    full = client.get("/api/calendar?region=Ghana").json()
    recent = client.get("/api/calendar?region=Ghana&ref=2011-2025").json()
    assert recent["reference_years"] == [2011, 2025]
    dec = lambda d: next(n for n in d["normals"] if n["month"] == 12)["mean"]
    assert dec(recent) != dec(full)


@pytest.mark.parametrize("query, status", [
    ("region=Atlantis", 400),
    ("bbox=10,10,11,11", 400),          # outside Ghana
    ("bbox=1,2,3", 400),
    ("region=Ghana&sensor=goes", 400),
    ("region=Ghana&ref=2020-2022", 400),  # too short
    ("region=Ghana&sensor=viirs&ref=2005-2015", 400),  # before VIIRS
])
def test_bad_requests(client, query, status):
    assert client.get(f"/api/calendar?{query}").status_code == status


def test_drawn_polygon(client):
    poly = {"type": "Polygon", "coordinates": [[[-1, 9], [0, 9], [0, 10], [-1, 10], [-1, 9]]]}
    r = client.post("/api/calendar", json={"geometry": poly})
    assert r.status_code == 200
    assert r.json()["area"]["cells"] == 1600          # 1 deg x 1 deg at 0.025 deg


def test_tiny_area_still_gets_a_cell(client):
    r = client.get("/api/calendar?bbox=-0.801,9.401,-0.800,9.402")
    assert r.status_code == 200 and r.json()["area"]["cells"] == 1


def test_tiny_area_is_marked_low_activity(client):
    d = client.get("/api/calendar?bbox=-2.60,5.90,-2.59,5.91").json()
    assert d["low_activity"] is True
    assert all(m["flag"] is None for m in d["monthly"])
    assert "very little fire" in d["summary"][0]
    assert client.get("/api/calendar?region=Ghana").json()["low_activity"] is False


def test_compare_regions(client):
    d = client.get("/api/compare").json()
    assert len(d["regions"]) == 16
    assert sum(r["share"] for r in d["regions"]) == pytest.approx(1, abs=0.01)
    assert sum(r["share_of_change"] for r in d["regions"]) == pytest.approx(1, abs=0.01)
    raw = client.get("/api/compare?sensor=modis").json()
    assert all(r["long_term_change"] is None for r in raw["regions"])


def test_yearly_records_for_the_sensor_chart(client):
    y = {r["year"]: r for r in client.get("/api/calendar?region=Ghana").json()["yearly"]}
    assert y[2005]["viirs"] is None and y[2005]["modis"] > 0       # no VIIRS before 2012
    assert y[2015]["harmonized"] == pytest.approx(y[2015]["viirs"], rel=1e-6)  # VIIRS is the reference
    assert y[2000]["partial"] and not y[2015]["partial"]


@pytest.mark.parametrize("season", ["normal", "2025"])
def test_season_animation(client, season):
    a = client.get(f"/api/animation?season={season}").json()
    assert len(a["weeks"]) == 52 and 0 <= a["play_from"] < a["play_to"] <= 51
    assert a["cells"] and all(0 <= i < len(a["cells"]) for _, i, _ in a["values"])
    peak_week = max(range(52), key=lambda w: a["national"][w])
    assert a["weeks"][peak_week].split()[1] in ("Nov", "Dec", "Jan", "Feb")


def test_season_animation_rejects_bad_season(client):
    assert client.get("/api/animation?season=abc").status_code == 400
    assert client.get("/api/animation?season=1990").status_code == 400


def test_daily_hotspots_export(client):
    d = client.get("/api/daily?region=Northern&year=2025").json()
    assert len(d["days"]) == 365 and {"date", "value", "normal"} <= set(d["days"][0])
    h = client.get("/api/hotspots?region=Northern&year=2025&month=12&limit=10").json()
    assert h["shown"] <= 10 and h["total"] >= h["shown"]
    e = client.get("/api/export.csv?region=Volta")
    assert e.status_code == 200 and e.text.startswith("# Volta Region")


def test_forecast_for_a_region_and_period(client):
    meta = client.get("/api/forecast?region=Ashanti").json()
    first, last = meta["window"]["first"], meta["window"]["last"]
    assert len(meta["months"]) == 12 and meta["period"]["months"] == 12
    d = client.get(f"/api/forecast?region=Ashanti&start={first}&end={first}").json()
    t = d["total"]
    assert d["period"]["months"] == 1 and sum(m["in_period"] for m in d["months"]) == 1
    assert t["low"] <= t["forecast"] <= t["high"]
    assert d["trend"]["recent"]["direction"] in ("upward", "downward", "no clear trend")
    assert d["skill"]["typical_error"] is not None and d["past"]
    assert d["summary"][0].startswith("Expected burning in Ashanti Region")
    assert client.get(f"/api/forecast?region=Ashanti&start={last}&end={first}").status_code == 400
    assert client.get("/api/forecast?region=Ashanti&start=2001-01").status_code == 400


def test_forecast_for_drawn_area(client):
    body = {"geometry": {"type": "Polygon", "coordinates": [[[-1, 9], [-0.8, 9], [-0.8, 9.2], [-1, 9.2], [-1, 9]]]}}
    d = client.post("/api/forecast", json=body).json()
    assert d["summary"][0].startswith("Expected burning in this area")


def test_districts(client):
    meta = client.get("/api/meta").json()
    assert len(meta["districts"]) == 260
    assert all(d["region"].endswith(" Region") for d in meta["districts"])
    a = client.get("/api/calendar?region=Saboba").json()
    b = client.get("/api/calendar?region=saboba district").json()
    assert a["area"]["name"] == b["area"]["name"] == "Saboba District"
    assert a["monthly"] == b["monthly"]
    assert a["summary"][0].startswith("In Saboba District")   # named like a region, not "this area"
    # A district is part of its region
    northern = client.get("/api/calendar?region=Northern").json()
    assert a["area"]["km2"] < northern["area"]["km2"]
    gj = client.get("/api/districts").json()
    assert len(gj["features"]) == 260 and {"name", "label", "region"} <= set(gj["features"][0]["properties"])


def test_urban_district_is_low_activity(client):
    assert client.get("/api/calendar?region=Ledzokuku Municipal").json()["low_activity"] is True
