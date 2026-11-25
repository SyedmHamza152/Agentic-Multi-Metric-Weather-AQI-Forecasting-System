"""US AQI (0-500) calculation following US EPA rules.

This is the single AQI standard for the whole project: every dataset that carries an
AQI value stores a US AQI, computed here.

How it works (EPA "Air Quality Index" technical documentation):
  1. Each pollutant gets a sub-index from its concentration using EPA breakpoints.
  2. The overall AQI is the highest valid sub-index, and that pollutant is "dominant".
  3. Averaging periods differ per pollutant: PM2.5/PM10 use a 24-hour average,
     O3 and CO use an 8-hour average, NO2 and SO2 use a 1-hour average.
  4. Concentrations are truncated (not rounded) to the pollutant's specified
     precision before the formula is applied; the result is rounded to an integer.
  5. Concentrations are converted to the units the EPA breakpoints expect
     (PM in µg/m³, O3/CO in ppm, NO2/SO2 in ppb) using the molar volume at
     25 °C and 1 atm (24.45 L/mol).

Functions
  sub_index(pollutant, value, unit)      one concentration -> one sub-index
  aqi_from_pollutants(values)             {pollutant: (value, unit)} -> (aqi, dominant)
  add_us_aqi(df)                          hourly µg/m³ frame -> df with us_aqi columns
"""
from __future__ import annotations

import math
from typing import Dict, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

# Molar masses (g/mol) used to convert µg/m³ <-> ppm/ppb at 25 C, 1 atm.
MOLAR_MASS = {"o3": 47.9982, "no2": 46.0055, "so2": 64.066, "co": 28.0101}
MOLAR_VOLUME_L = 24.45

# EPA breakpoints: (C_low, C_high, I_low, I_high). Concentration units per pollutant:
#   pm25/pm10 -> µg/m³, o3/co -> ppm, no2/so2 -> ppb.
BREAKPOINTS = {
    "pm25": [
        (0.0, 12.0, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
        (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300), (250.5, 350.4, 301, 400),
        (350.5, 500.4, 401, 500),
    ],
    "pm10": [
        (0.0, 54.0, 0, 50), (55.0, 154.0, 51, 100), (155.0, 254.0, 101, 150),
        (255.0, 354.0, 151, 200), (355.0, 424.0, 201, 300), (425.0, 504.0, 301, 400),
        (505.0, 604.0, 401, 500),
    ],
    "o3": [
        (0.0, 0.054, 0, 50), (0.055, 0.070, 51, 100), (0.071, 0.085, 101, 150),
        (0.086, 0.105, 151, 200), (0.106, 0.125, 201, 300), (0.126, 0.374, 301, 400),
        (0.375, 0.604, 401, 500),
    ],
    "no2": [
        (0.0, 53.0, 0, 50), (54.0, 100.0, 51, 100), (101.0, 360.0, 101, 150),
        (361.0, 649.0, 151, 200), (650.0, 1249.0, 201, 300), (1250.0, 1649.0, 301, 400),
        (1650.0, 2049.0, 401, 500),
    ],
    "so2": [
        (0.0, 35.0, 0, 50), (36.0, 75.0, 51, 100), (76.0, 185.0, 101, 150),
        (186.0, 304.0, 151, 200), (305.0, 604.0, 201, 300), (605.0, 804.0, 301, 400),
        (805.0, 1004.0, 401, 500),
    ],
    "co": [
        (0.0, 4.4, 0, 50), (4.5, 9.4, 51, 100), (9.5, 12.4, 101, 150),
        (12.5, 15.4, 151, 200), (15.5, 30.4, 201, 300), (30.5, 40.4, 301, 400),
        (40.5, 50.4, 401, 500),
    ],
}

# EPA-required averaging period (hours) and truncation precision per pollutant.
AVERAGING_HOURS = {"pm25": 24, "pm10": 24, "o3": 8, "co": 8, "no2": 1, "so2": 1}
TRUNCATE_DECIMALS = {"pm25": 1, "pm10": 0, "o3": 3, "no2": 0, "so2": 0, "co": 1}
AQI_POLLUTANTS = tuple(BREAKPOINTS)

# A rolling average is accepted when at least this fraction of the window has data
# (EPA uses 75% completeness for 24h/8h averages).
MIN_COMPLETENESS = 0.75

# The unit each pollutant's EPA breakpoints are expressed in.
EPA_UNIT = {"pm25": "ugm3", "pm10": "ugm3", "o3": "ppm", "co": "ppm",
            "no2": "ppb", "so2": "ppb"}

_UNIT_ALIASES = {
    "µg/m³": "ugm3", "ug/m³": "ugm3", "ug/m3": "ugm3", "μg/m³": "ugm3",
    "μg/m3": "ugm3", "µg/m3": "ugm3", "ugm3": "ugm3", "um003": "ugm3",
    "mg/m³": "mgm3", "mg/m3": "mgm3", "mgm3": "mgm3",
    "ppm": "ppm", "ppb": "ppb",
}


def normalize_unit(unit: Optional[str]) -> Optional[str]:
    """Map common unit spellings to one of: ugm3, mgm3, ppm, ppb."""
    if unit is None:
        return None
    return _UNIT_ALIASES.get(str(unit).strip().lower())


def to_ugm3(pollutant: str, value: float, unit: Optional[str]) -> Optional[float]:
    """Public wrapper: convert any supported unit to µg/m³ (None if impossible)."""
    normalized = normalize_unit(unit)
    if normalized is None or not math.isfinite(value):
        return None
    return _to_ugm3(pollutant, value, normalized)


def _to_ugm3(pollutant: str, value: float, unit: str) -> Optional[float]:
    """Convert a concentration to µg/m³. Returns None for gases without a molar mass."""
    if unit == "ugm3":
        return value
    if unit == "mgm3":
        return value * 1000.0
    if unit in ("ppm", "ppb"):
        mw = MOLAR_MASS.get(pollutant)
        if mw is None:
            return None
        ppm = value if unit == "ppm" else value / 1000.0
        return ppm * mw / MOLAR_VOLUME_L * 1000.0  # ppm -> mg/m3 -> µg/m³
    return None


def _to_epa_units(pollutant: str, value_ugm3: float) -> float:
    """Convert µg/m³ to the units the EPA breakpoints for this pollutant expect."""
    if pollutant in ("pm25", "pm10"):
        return value_ugm3
    mw = MOLAR_MASS[pollutant]
    ppm = value_ugm3 * MOLAR_VOLUME_L / mw / 1000.0
    return ppm if pollutant in ("o3", "co") else ppm * 1000.0


def _truncate(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.trunc(value * factor) / factor


def sub_index(pollutant: str, concentration: float, unit: str = "ugm3") -> Optional[float]:
    """One pollutant concentration -> its AQI sub-index (float, before rounding).

    Returns None when the pollutant is unknown, the unit cannot be converted,
    or the concentration is not a finite number.
    """
    if pollutant not in BREAKPOINTS or not math.isfinite(concentration):
        return None
    unit = normalize_unit(unit)
    if unit is None:
        return None
    if unit == EPA_UNIT[pollutant]:
        c = _truncate(concentration, TRUNCATE_DECIMALS[pollutant])
    else:
        ugm3 = _to_ugm3(pollutant, concentration, unit)
        if ugm3 is None or ugm3 < 0:
            return None
        c = _truncate(_to_epa_units(pollutant, ugm3), TRUNCATE_DECIMALS[pollutant])
    if c < 0:
        return None
    for c_low, c_high, i_low, i_high in BREAKPOINTS[pollutant]:
        if c_low <= c <= c_high:
            return (i_high - i_low) / (c_high - c_low) * (c - c_low) + i_low
    # Above the top breakpoint the AQI is capped at 500 ("Hazardous").
    return 500.0 if c > BREAKPOINTS[pollutant][-1][1] else 0.0


def aqi_from_pollutants(
    values: Mapping[str, Tuple[float, str]],
) -> Tuple[Optional[int], Optional[str]]:
    """{pollutant: (concentration, unit)} -> (overall US AQI, dominant pollutant).

    The overall AQI is the highest sub-index, rounded to the nearest integer.
    """
    best_aqi: Optional[float] = None
    best_pollutant: Optional[str] = None
    for pollutant, pair in values.items():
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        idx = sub_index(pollutant, pair[0], pair[1])
        if idx is None:
            continue
        if best_aqi is None or idx > best_aqi:
            best_aqi, best_pollutant = idx, pollutant
    if best_aqi is None:
        return None, None
    return int(round(best_aqi)), best_pollutant


def aqi_from_subindices(sub_indices: Mapping[str, float]) -> Tuple[Optional[int], Optional[str]]:
    """AQI from already-computed pollutant sub-indices (e.g. AQICN's iaqi values,
    which are per-pollutant AQI values on the station's scale).

    Returns the highest valid sub-index as the US AQI and its pollutant.
    """
    best: Optional[float] = None
    best_pollutant: Optional[str] = None
    for pollutant, value in sub_indices.items():
        if pollutant not in AQI_POLLUTANTS or value is None:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v) or v < 0:
            continue
        if best is None or v > best:
            best, best_pollutant = v, pollutant
    if best is None:
        return None, None
    return min(500, int(round(best))), best_pollutant


def _sub_index_series(pollutant: str, hourly_ugm3: pd.Series) -> pd.Series:
    """Vectorized sub-index for an hourly µg/m³ series (values already averaged)."""
    window = AVERAGING_HOURS[pollutant]
    if window > 1:
        min_periods = max(1, int(math.ceil(window * MIN_COMPLETENESS)))
        averaged = hourly_ugm3.rolling(window, min_periods=min_periods).mean()
    else:
        averaged = hourly_ugm3
    epa = averaged.map(lambda v: _to_epa_units(pollutant, v) if pd.notna(v) else np.nan)
    truncated = epa.map(lambda v: _truncate(v, TRUNCATE_DECIMALS[pollutant]) if pd.notna(v) else np.nan)

    out = pd.Series(np.nan, index=hourly_ugm3.index, dtype=float)
    for c_low, c_high, i_low, i_high in BREAKPOINTS[pollutant]:
        mask = (truncated >= c_low) & (truncated <= c_high)
        out = out.mask(mask, (i_high - i_low) / (c_high - c_low) * (truncated - c_low) + i_low)
    out = out.mask(truncated > BREAKPOINTS[pollutant][-1][1], 500.0)
    return out


def add_us_aqi(df: pd.DataFrame, pollutants: Tuple[str, ...] = AQI_POLLUTANTS) -> pd.DataFrame:
    """Add `us_aqi` and `aqi_pollutant` columns to an hourly DataFrame.

    Expects one column per pollutant in µg/m³ and a DatetimeIndex (or a
    'timestamp_utc' column, which is set as the index). Rows where no pollutant
    has enough data for its averaging period get a missing us_aqi — we never
    fabricate an AQI.
    """
    if df.empty:
        return df.assign(us_aqi=np.nan, aqi_pollutant=np.nan)

    work = df.copy()
    if "timestamp_utc" in work.columns:
        work["timestamp_utc"] = pd.to_datetime(work["timestamp_utc"], utc=True)
        work = work.set_index("timestamp_utc").sort_index()
    elif not isinstance(work.index, pd.DatetimeIndex):
        raise ValueError("add_us_aqi needs a DatetimeIndex or a timestamp_utc column")

    sub_frames = {}
    for pollutant in pollutants:
        if pollutant not in work.columns:
            continue
        series = pd.to_numeric(work[pollutant], errors="coerce")
        sub_frames[pollutant] = _sub_index_series(pollutant, series)

    if not sub_frames:
        return df.assign(us_aqi=np.nan, aqi_pollutant=np.nan)

    subs = pd.DataFrame(sub_frames)
    # Row-wise max sub-index = US AQI; argmax = dominant pollutant. Rows with no
    # valid sub-index stay missing — we never fabricate an AQI. (numpy is used
    # because pandas' idxmax raises on all-NaN rows.)
    values = subs.to_numpy(dtype=float)
    valid = ~np.isnan(values)
    filled = np.where(valid, values, -np.inf)
    has_any = valid.any(axis=1)
    best_pos = filled.argmax(axis=1)
    names = np.array(list(subs.columns), dtype=object)

    us_aqi = pd.Series(
        np.where(has_any, filled[np.arange(len(filled)), best_pos], np.nan),
        index=subs.index,
    )
    dominant = pd.Series(
        np.where(has_any, names[best_pos], np.nan), index=subs.index
    )

    result = work.reset_index()
    # us_aqi/dominant are indexed by timestamp; align them positionally.
    result["us_aqi"] = pd.array(us_aqi.round(), dtype="Int64")
    result["aqi_pollutant"] = dominant.to_numpy(dtype=object)
    return result
