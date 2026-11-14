import pandas as pd
import shapely
from shapely.geometry import box

from pipeline.normalize import clip_to_border, confidence_class, normalize_frame


def modis_rows():
    return pd.DataFrame({
        "latitude": [8.5, 9.0, 10.0], "longitude": [-0.3, -1.0, -2.0],
        "brightness": [301.5, 310.0, 330.0], "scan": [2.5, 1.0, 1.1], "track": [1.5, 1.0, 1.0],
        "acq_date": ["2020-01-01"] * 3, "acq_time": ["0113", "1035", "45"],
        "satellite": ["Aqua", "Terra", "T"], "instrument": ["MODIS"] * 3,
        "confidence": ["29", "30", "80"], "version": ["6.03"] * 3,
        "bright_t31": [288.7, 290.0, 295.0], "frp": [21.8, 5.0, 60.0],
        "daynight": ["N", "D", "D"], "type": [0, 2, 0],
    })


def viirs_rows():
    return pd.DataFrame({
        "latitude": [8.0, 8.1], "longitude": [-1.0, -1.1],
        "bright_ti4": [330.0, 340.0], "scan": [0.4, 0.5], "track": [0.4, 0.4],
        "acq_date": ["2020-01-02"] * 2, "acq_time": ["1300", "0130"],
        "satellite": ["N", "N"], "instrument": ["VIIRS"] * 2,
        "confidence": ["h", "low"], "version": ["2.0NRT"] * 2,
        "bright_ti5": [290.0, 291.0], "frp": [3.2, 1.1], "daynight": ["D", "N"],
    })


def test_modis_confidence_thresholds():
    got = confidence_class("MODIS", pd.Series(["0", "29", "30", "79", "80", "100", "x"]))
    assert got.tolist()[:6] == ["low", "low", "nominal", "nominal", "high", "high"]
    assert pd.isna(got.iloc[6])


def test_viirs_confidence_letters_and_words():
    got = confidence_class("VIIRS", pd.Series(["l", "n", "h", "nominal", "High"]))
    assert got.tolist() == ["low", "nominal", "high", "nominal", "high"]


def test_modis_frame():
    out = normalize_frame(modis_rows(), "SP", "test.csv")
    assert (out["sensor"] == "MODIS").all()
    assert out["satellite"].tolist() == ["Aqua", "Terra", "Terra"]
    assert str(out["time_utc"].iloc[2]) == "2020-01-01 00:45:00+00:00"   # "45" -> 00:45
    # low confidence, non-vegetation type, and a good point
    assert out["use"].tolist() == [False, False, True]


def test_viirs_frame_without_type_column():
    out = normalize_frame(viirs_rows(), "NRT", "test.csv")
    assert (out["sensor"] == "VIIRS").all()
    assert out["satellite"].tolist() == ["Suomi NPP", "Suomi NPP"]
    assert out["fire_type"].isna().all()
    assert out["use"].tolist() == [True, False]
    assert out["brightness_k"].tolist() == [330.0, 340.0]


def test_clip_to_border():
    border = box(-1.05, 7.0, 0.0, 9.0)
    shapely.prepare(border)
    out = normalize_frame(viirs_rows(), "NRT", "test.csv")
    assert clip_to_border(out, border)["lon"].tolist() == [-1.0]
