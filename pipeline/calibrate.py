"""Measure how MODIS relates to VIIRS in the years both were running.

Tries every combination of grid size and correction method, and scores each with
leave-one-year-out testing: fit on all overlap years but one, predict that year's
VIIRS activity from MODIS, and measure the error. The best combination wins
(a simpler method wins ties within 0.5 percentage points).

Correction methods (VIIRS cell-days = k x MODIS cell-days):
  ratio             one k for all of Ghana
  ratio_month       one k per calendar month (season)
  ratio_zone        one k per zone (south / middle / north)
  ratio_zone_month  one k per zone and month

Also prints the yearly VIIRS/MODIS ratio, overall and for Terra and Aqua separately,
to check whether the ageing MODIS satellites have drifted.

Output: data/processed/calibration.json
Run:    python -m pipeline.calibrate [--years 2012-2025]
"""
import argparse
import json

import numpy as np
import pandas as pd

from .config import PROCESSED, ZONES
from .grid import connect, monthly_cell_days, viirs_start

CELL_SIZES = [0.01, 0.025, 0.05, 0.1]
MODELS = ["ratio", "ratio_month", "ratio_zone", "ratio_zone_month"]  # simplest first
MIN_MODIS = 200          # cell-days a group needs to get its own factor
MIN_MONTH_SCORE = 500    # only score months with this much VIIRS activity
TIE_MARGIN = 0.005
ZONE_NAMES = [z for z, _ in ZONES]


SAT_WHO = "CASE WHEN sensor = 'VIIRS' THEN 'VIIRS' ELSE satellite END"
# Terra alone was running before Aqua's first fire data (July 2002). Its factor is calibrated
# on years before its orbit drift shows up in the yearly ratios (ratio jumps from 2023).
TERRA_YEARS = (2012, 2022)


def overlap_table(mc: pd.DataFrame, y0: int, y1: int, ref: str = "MODIS") -> pd.DataFrame:
    """Rows (y, m, z) with reference (M) and VIIRS (V) cell-days for the overlap years."""
    t = mc[mc.who.isin([ref, "VIIRS"]) & mc.y.between(y0, y1)]
    p = t.pivot_table(index=["y", "m", "z"], columns="who", values="cd",
                      aggfunc="sum", fill_value=0).reset_index()
    p = p.rename(columns={ref: "M", "VIIRS": "V"})
    return p[~((p.y == 2012) & (p.m == 1))]  # VIIRS only started 20 Jan 2012


def factor_table(train: pd.DataFrame, model: str) -> pd.DataFrame:
    tab = pd.DataFrame([(z, m) for z in ZONE_NAMES for m in range(1, 13)], columns=["z", "m"])
    k_all = train.V.sum() / train.M.sum()

    def ratios(keys, fallback):
        g = train.groupby(keys)[["M", "V"]].sum()
        k = (g.V / g.M).where(g.M >= MIN_MODIS)
        return k, fallback

    if model == "ratio":
        tab["k"] = k_all
    elif model == "ratio_month":
        k, _ = ratios("m", k_all)
        tab["k"] = tab.m.map(k).fillna(k_all)
    elif model == "ratio_zone":
        k, _ = ratios("z", k_all)
        tab["k"] = tab.z.map(k).fillna(k_all)
    elif model == "ratio_zone_month":
        kz, _ = ratios("z", k_all)
        kz = kz.fillna(k_all)
        kzm, _ = ratios(["z", "m"], None)
        tab["k"] = [kzm.get((z, m), np.nan) for z, m in zip(tab.z, tab.m)]
        tab["k"] = tab.k.fillna(tab.z.map(kz)).fillna(k_all)
    else:
        raise ValueError(model)
    return tab


def predict(test: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    out = test.merge(factors, on=["z", "m"], how="left")
    out["P"] = out.M * out.k
    return out


def cross_validate(tab: pd.DataFrame, model: str) -> dict:
    annual, monthly = [], []
    for yr in sorted(tab.y.unique()):
        train, test = tab[tab.y != yr], tab[tab.y == yr]
        p = predict(test, factor_table(train, model))
        annual.append(abs(p.P.sum() - p.V.sum()) / p.V.sum())
        cm = p.groupby("m")[["P", "V"]].sum()
        cm = cm[cm.V >= MIN_MONTH_SCORE]
        monthly.extend((abs(cm.P - cm.V) / cm.V).tolist())
    return {"annual_err": float(np.mean(annual)), "annual_err_max": float(np.max(annual)),
            "month_err_median": float(np.median(monthly))}


def yearly_ratios(con, cell: float) -> pd.DataFrame:
    by_sat = monthly_cell_days(con, cell, SAT_WHO)
    both = monthly_cell_days(con, cell)
    t = pd.concat([both, by_sat[by_sat.who != "VIIRS"]])
    t = t[~((t.y == 2012) & (t.m == 1))]
    y = t.pivot_table(index="y", columns="who", values="cd", aggfunc="sum")
    y = y[y.index.isin(range(2012, 2026))]
    return pd.DataFrame({
        "viirs_per_modis": y.VIIRS / y.MODIS,
        "viirs_per_terra": y.VIIRS / y.Terra,
        "viirs_per_aqua": y.VIIRS / y.Aqua,
        "terra_per_aqua": y.Terra / y.Aqua,
    }).round(3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2012-2025", help="calibration years, e.g. 2012-2019")
    args = ap.parse_args()
    y0, y1 = (int(v) for v in args.years.split("-"))

    con = connect()
    results = []
    for cell in CELL_SIZES:
        tab = overlap_table(monthly_cell_days(con, cell), y0, y1)
        for model in MODELS:
            r = cross_validate(tab, model)
            results.append({"cell_deg": cell, "model": model, **r})
            print(f"  grid {cell:<5} {model:<17} annual error {r['annual_err']:6.1%} "
                  f"(worst {r['annual_err_max']:5.1%})  peak-month error {r['month_err_median']:6.1%}")
    res = pd.DataFrame(results)

    best_err = res.annual_err.min()
    order = {m: i for i, m in enumerate(MODELS)}
    ok = res[res.annual_err <= best_err + TIE_MARGIN].copy()
    ok["simplicity"] = ok.model.map(order)
    best = ok.sort_values(["simplicity", "annual_err"]).iloc[0]
    cell, model = float(best.cell_deg), best.model

    tab = overlap_table(monthly_cell_days(con, cell), y0, y1)
    factors = factor_table(tab, model)
    drift = yearly_ratios(con, cell)

    terra_tab = overlap_table(monthly_cell_days(con, cell, SAT_WHO), *TERRA_YEARS, ref="Terra")
    terra_factors = factor_table(terra_tab, model)
    terra_cv = cross_validate(terra_tab, model)
    aqua_start = con.sql("SELECT min(date) FROM hotspots WHERE satellite = 'Aqua'").fetchall()[0][0]

    cal = {
        "cell_deg": cell,
        "model": model,
        "calibration_years": [y0, y1],
        "viirs_start": str(viirs_start(con)),
        "aqua_start": str(aqua_start),
        "reference_sensor": "VIIRS (Suomi NPP)",
        "units": "VIIRS-equivalent active cell-days",
        "factors": factors.round(4).to_dict(orient="records"),
        "factors_terra_only": terra_factors.round(4).to_dict(orient="records"),
        "terra_only_years": list(TERRA_YEARS),
        "terra_only_cross_validation": {k: round(v, 4) for k, v in terra_cv.items()},
        "overall_ratio": round(float(tab.V.sum() / tab.M.sum()), 4),
        "cross_validation": res.round(4).to_dict(orient="records"),
        "yearly_ratios": drift.reset_index().rename(columns={"y": "year"}).to_dict(orient="records"),
    }
    (PROCESSED / "calibration.json").write_text(json.dumps(cal, indent=2), encoding="utf-8")

    print(f"\nChosen: grid {cell} deg, method {model}, "
          f"annual error {best.annual_err:.1%}, overall VIIRS/MODIS ratio {cal['overall_ratio']}")
    print("\nFactors (k) by zone and month:")
    print(factors.pivot(index="z", columns="m", values="k").round(2).to_string())
    print(f"\nTerra-only factors (used before Aqua started, {aqua_start}), "
          f"calibrated {TERRA_YEARS[0]}-{TERRA_YEARS[1]}, annual error {terra_cv['annual_err']:.1%}:")
    print(terra_factors.groupby("m").k.first().round(2).to_frame().T.to_string(index=False))
    print("\nYearly ratios (drift check):")
    print(drift.to_string())


if __name__ == "__main__":
    main()
