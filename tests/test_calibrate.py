import pandas as pd
import pytest

from pipeline.calibrate import MIN_MODIS, cross_validate, factor_table, overlap_table, predict


def synthetic(k_by_month=None, years=range(2013, 2018)):
    """VIIRS = k x MODIS exactly, with plenty of activity in every zone and month."""
    k_by_month = k_by_month or {m: 3.0 for m in range(1, 13)}
    rows = []
    for y in years:
        for m in range(1, 13):
            for z in ("south", "middle", "north"):
                M = MIN_MODIS + 100
                rows.append({"y": y, "m": m, "z": z, "M": M, "V": M * k_by_month[m]})
    return pd.DataFrame(rows)


def test_single_ratio_recovers_constant_factor():
    f = factor_table(synthetic(), "ratio")
    assert f.k.round(6).eq(3.0).all()
    assert len(f) == 36  # 3 zones x 12 months


def test_month_factors_recover_seasonal_pattern():
    k = {m: 2.0 if m in (11, 12, 1, 2) else 6.0 for m in range(1, 13)}
    f = factor_table(synthetic(k), "ratio_month")
    assert f.groupby("m").k.first().round(6).to_dict() == pytest.approx(k)


def test_sparse_groups_fall_back_to_overall_ratio():
    tab = synthetic()
    tab.loc[tab.m == 7, ["M", "V"]] = [1, 50]   # tiny MODIS activity in July
    f = factor_table(tab, "ratio_month")
    july = f[f.m == 7].k.iloc[0]
    overall = tab.V.sum() / tab.M.sum()
    assert july == pytest.approx(overall)


def test_cross_validation_is_exact_for_exact_data():
    r = cross_validate(synthetic(), "ratio_month")
    assert r["annual_err"] == pytest.approx(0, abs=1e-9)


def test_predict_multiplies_modis_by_factor():
    tab = synthetic()
    p = predict(tab, factor_table(tab, "ratio"))
    assert (p.P - p.V).abs().max() == pytest.approx(0, abs=1e-6)


def test_overlap_table_drops_partial_january_2012():
    mc = pd.DataFrame([
        {"who": "MODIS", "y": 2012, "m": 1, "z": "north", "cd": 10},
        {"who": "VIIRS", "y": 2012, "m": 1, "z": "north", "cd": 5},
        {"who": "MODIS", "y": 2012, "m": 2, "z": "north", "cd": 10},
        {"who": "VIIRS", "y": 2012, "m": 2, "z": "north", "cd": 30},
        {"who": "Terra", "y": 2012, "m": 2, "z": "north", "cd": 4},
    ])
    t = overlap_table(mc, 2012, 2012)
    assert t[["m", "M", "V"]].values.tolist() == [[2, 10, 30]]
    terra = overlap_table(mc, 2012, 2012, ref="Terra")
    assert terra[["m", "M", "V"]].values.tolist() == [[2, 4, 30]]
