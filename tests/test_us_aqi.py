"""Tests for the US AQI calculation (EPA breakpoints, units, averaging, rounding)."""
import numpy as np
import pandas as pd

from agentweather.features.us_aqi import (
    aqi_from_pollutants,
    aqi_from_subindices,
    add_us_aqi,
    normalize_unit,
    sub_index,
    to_ugm3,
)


def test_pm25_breakpoints():
    # EPA PM2.5 24h breakpoints, concentrations in µg/m³
    assert sub_index("pm25", 0.0) == 0.0
    assert sub_index("pm25", 12.0) == 50.0
    assert sub_index("pm25", 12.1) == 51.0
    assert sub_index("pm25", 35.4) == 100.0
    assert sub_index("pm25", 55.5) == 151.0  # 55.5 starts the 151-200 band
    assert sub_index("pm25", 500.4) == 500.0


def test_truncation_before_formula():
    # 12.05 µg/m³ truncates to 12.0 -> AQI 50, not the 12.1 band.
    assert sub_index("pm25", 12.05) == 50.0
    # PM10 truncates to integer: 54.9 -> 54 -> AQI 50.
    assert sub_index("pm10", 54.9) == 50.0


def test_above_top_breakpoint_caps_at_500():
    assert sub_index("pm25", 9999.0) == 500.0


def test_gas_units_ppm_and_ppb():
    # CO breakpoints are in ppm: 0 ppm -> 0, 4.4 ppm -> 50, 50.4+ -> 500.
    assert sub_index("co", 0.0, "ppm") == 0.0
    assert sub_index("co", 4.4, "ppm") == 50.0
    # 1 ppm CO = 28.01/24.45 mg/m³ = ~1146 µg/m³
    assert to_ugm3("co", 1.0, "ppm") == 1.0 * 28.0101 / 24.45 * 1000.0
    # NO2 breakpoints are in ppb.
    assert sub_index("no2", 53.0, "ppb") == 50.0
    assert sub_index("no2", 54.0, "ppb") == 51.0


def test_ugm3_to_ppm_conversion_for_o3():
    # O3: 100 µg/m³ -> ppm = 100*24.45/47.9982/1000 = 0.0509 -> AQI ~47
    idx = sub_index("o3", 100.0, "ug/m3")
    assert idx is not None and 40 < idx < 55


def test_unknown_unit_and_bad_values():
    assert sub_index("pm25", 10.0, "furlongs") is None
    assert sub_index("pm25", float("nan")) is None
    assert sub_index("nonsense", 10.0) is None
    assert normalize_unit("µg/m³") == "ugm3"
    assert normalize_unit("PPM") == "ppm"


def test_aqi_from_pollutants_picks_max():
    aqi, dominant = aqi_from_pollutants({
        "pm25": (35.0, "ugm3"),    # sub-index ~99
        "pm10": (400.0, "ugm3"),   # sub-index ~266 -> dominant
        "co": (1.0, "ppm"),
    })
    assert dominant == "pm10"
    assert aqi is not None and 260 <= aqi <= 270


def test_aqi_from_subindices():
    aqi, dominant = aqi_from_subindices({"pm25": 80.0, "pm10": 164.0, "o3": None})
    assert (aqi, dominant) == (164, "pm10")
    assert aqi_from_subindices({"pm25": None}) == (None, None)


def _hourly_frame(values_by_pollutant, hours=30):
    idx = pd.date_range("2024-01-01", periods=hours, freq="h", tz="UTC")
    data = {"timestamp_utc": idx}
    for name, values in values_by_pollutant.items():
        data[name] = values
    return pd.DataFrame(data)


def test_add_us_aqi_uses_24h_average_for_pm():
    # Constant PM2.5 of 100 µg/m³ -> 24h average AQI ~174 once the window fills.
    df = _hourly_frame({"pm25": [100.0] * 30})
    out = add_us_aqi(df)
    assert out["aqi_pollutant"].dropna().eq("pm25").all()
    # First 17 hours lack 75% of a 24h window -> missing, never fabricated.
    assert out["us_aqi"].iloc[:17].isna().all()
    assert out["us_aqi"].iloc[17:].notna().all()
    assert int(out["us_aqi"].dropna().iloc[0]) == 174


def test_add_us_aqi_1h_pollutants_need_no_window():
    # SO2 1h averaging: valid from the first row.
    df = _hourly_frame({"so2": [80.0] * 5}, hours=5)
    out = add_us_aqi(df)
    assert out["us_aqi"].notna().all()
    assert out["aqi_pollutant"].eq("so2").all()


def test_add_us_aqi_missing_pollutants_stay_missing():
    df = _hourly_frame({"pm25": [np.nan] * 30})
    out = add_us_aqi(df)
    assert out["us_aqi"].isna().all()
