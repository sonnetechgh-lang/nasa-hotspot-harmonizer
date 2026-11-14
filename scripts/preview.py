"""Build a local preview page of the harmonized record.

Writes web/preview.html with the data embedded.
Run:  python scripts/preview.py   (after pipeline.calibrate and pipeline.grid)
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
OUT = ROOT / "web" / "preview.html"
TEMPLATE = ROOT / "web" / "preview.template.html"


def main() -> None:
    monthly = pd.read_parquet(PROCESSED / "monthly_harmonized.parquet")
    m = monthly.groupby(["year", "month"])[["modis_cd", "viirs_cd", "harmonized"]].sum().reset_index()
    data = [{"year": int(r.year), "month": int(r.month), "MODIS": int(r.modis_cd),
             "VIIRS": int(r.viirs_cd), "Harmonized": round(float(r.harmonized), 1)}
            for r in m.itertuples()]
    cal = json.loads((PROCESSED / "calibration.json").read_text(encoding="utf-8"))
    best = next(r for r in cal["cross_validation"]
                if r["cell_deg"] == cal["cell_deg"] and r["model"] == cal["model"])
    meta = {
        "cell_deg": cal["cell_deg"], "model": cal["model"], "ratio": cal["overall_ratio"],
        "annual_err": best["annual_err"], "viirs_start": cal["viirs_start"],
        "aqua_start": cal["aqua_start"],
        "factors": {r["m"]: r["k"] for r in cal["factors"]},
    }
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("/*DATA*/null", json.dumps(data)).replace("/*META*/null", json.dumps(meta))
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT} ({len(data)} months)")


if __name__ == "__main__":
    main()
