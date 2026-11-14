"""Areas of interest: Ghana, its regions and districts, boxes or drawn polygons, turned into grid cells."""
import json
import math

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import box, mapping, shape

from pipeline.config import BBOX, COUNTRY_CODE, RAW

KM_PER_DEG = 111.32


class AreaError(ValueError):
    pass


def _load(path):
    gj = json.loads(path.read_text(encoding="utf-8"))
    return gj["features"]


class AreaIndex:
    def __init__(self, cell: float):
        self.cell = cell
        feats0 = _load(RAW / "boundaries" / f"{COUNTRY_CODE}_ADM0.geojson")
        self.border = shapely.make_valid(shapely.union_all([shape(f["geometry"]) for f in feats0]))
        self.regions = {}
        for f in _load(RAW / "boundaries" / f"{COUNTRY_CODE}_ADM1.geojson"):
            name = f["properties"]["shapeName"].removesuffix(" Region")
            self.regions[name] = shapely.make_valid(shape(f["geometry"]))
        # Districts (ADM2), each tagged with the region that holds it
        self.districts, self.district_region = {}, {}
        for f in _load(RAW / "boundaries" / f"{COUNTRY_CODE}_ADM2.geojson"):
            name = f["properties"]["shapeName"]
            geom = shapely.make_valid(shape(f["geometry"]))
            self.districts[name] = geom
            p = geom.representative_point()
            self.district_region[name] = next((r for r, g in self.regions.items() if g.contains(p)), None)
        self._district_key = {n.lower(): n for n in self.districts}

        # Every grid cell whose centre is inside Ghana
        w, s, e, n = BBOX
        ix = np.arange(math.floor(w / cell), math.floor(e / cell) + 1)
        iy = np.arange(math.floor(s / cell), math.floor(n / cell) + 1)
        gx, gy = np.meshgrid(ix, iy)
        cx, cy = (gx.ravel() + 0.5) * cell, (gy.ravel() + 0.5) * cell
        inside = shapely.contains_xy(self.border, cx, cy)
        self.ix, self.iy = gx.ravel()[inside], gy.ravel()[inside]
        self.cx, self.cy = cx[inside], cy[inside]

    # ----- parsing -----

    def region_names(self) -> list[str]:
        return sorted(self.regions)

    def district_names(self) -> list[str]:
        return sorted(self.districts)

    @staticmethod
    def district_label(name: str) -> str:
        """'Saboba' -> 'Saboba District'; 'Tamale Metropolitan' and 'Wa Municipal' stay as they are."""
        return name if name.endswith(("Municipal", "Metropolitan", "District")) else f"{name} District"

    def resolve(self, region: str | None = None, bbox: str | None = None,
                geometry: dict | None = None):
        """Return (name, shapely geometry clipped to Ghana, cache key)."""
        given = [v is not None and v != "" for v in (region, bbox, geometry)]
        if sum(given) > 1:
            raise AreaError("give only one of region, bbox or geometry")
        if region:
            key = region.strip().removesuffix(" Region").lower()
            if key == "ghana":
                return "Ghana", self.border, "region:ghana"
            match = next((n for n in self.regions if n.lower() == key), None)
            if match:
                return f"{match} Region", self.regions[match], f"region:{match.lower()}"
            # A district: region names never clash with district names, so one parameter serves both
            dkey = region.strip().lower()
            district = self._district_key.get(dkey) or self._district_key.get(dkey.removesuffix(" district"))
            if district:
                return self.district_label(district), self.districts[district], f"district:{district.lower()}"
            raise AreaError(f"unknown area '{region}'. Use Ghana, a region ({', '.join(self.region_names())}) "
                            "or a district (listed in /api/meta)")
        if bbox:
            try:
                w, s, e, n = (float(v) for v in (bbox.split(",") if isinstance(bbox, str) else bbox))
            except (ValueError, TypeError):
                raise AreaError("bbox must be west,south,east,north") from None
            if not (w < e and s < n):
                raise AreaError("bbox must be west,south,east,north with west < east and south < north")
            geom = box(w, s, e, n)
            name = f"Box {s:.2f}–{n:.2f}°N, {w:.2f}–{e:.2f}°E"
        elif geometry:
            try:
                geom = shapely.make_valid(shape(geometry.get("geometry", geometry)))
            except Exception:
                raise AreaError("geometry must be a GeoJSON Polygon or MultiPolygon") from None
            if geom.geom_type not in ("Polygon", "MultiPolygon", "GeometryCollection"):
                raise AreaError("geometry must be a polygon")
            name = "Drawn area"
        else:
            return "Ghana", self.border, "region:ghana"
        clipped = geom.intersection(self.border)
        if clipped.is_empty:
            raise AreaError("the area is outside Ghana")
        return name, clipped, f"wkb:{shapely.to_wkb(shapely.set_precision(clipped, 1e-5)).hex()}"

    # ----- cells -----

    def cells(self, geom) -> pd.DataFrame:
        inside = shapely.contains_xy(geom, self.cx, self.cy)
        if not inside.any():  # very small area: use the cell under its centre
            p = geom.representative_point()
            return pd.DataFrame({"ix": [math.floor(p.x / self.cell)], "iy": [math.floor(p.y / self.cell)]})
        return pd.DataFrame({"ix": self.ix[inside], "iy": self.iy[inside]})

    def area_km2(self, geom) -> float:
        lat = geom.centroid.y
        return float(geom.area * KM_PER_DEG ** 2 * math.cos(math.radians(lat)))

    def regions_geojson(self, tolerance: float = 0.005) -> dict:
        feats = [{"type": "Feature", "properties": {"name": f"{n} Region", "id": n},
                  "geometry": mapping(g.simplify(tolerance, preserve_topology=True))}
                 for n, g in sorted(self.regions.items())]
        return {"type": "FeatureCollection", "features": feats}

    def districts_geojson(self, tolerance: float = 0.003) -> dict:
        # Map outlines only (analysis uses the full shapes): simplified and rounded to ~10 m to keep the file small
        feats = [{"type": "Feature",
                  "properties": {"name": n, "label": self.district_label(n), "region": f"{self.district_region[n]} Region"},
                  "geometry": mapping(shapely.set_precision(g.simplify(tolerance, preserve_topology=True), 1e-4))}
                 for n, g in sorted(self.districts.items())]
        return {"type": "FeatureCollection", "features": feats}

    def border_geojson(self, tolerance: float = 0.005) -> dict:
        return mapping(self.border.simplify(tolerance, preserve_topology=True))
