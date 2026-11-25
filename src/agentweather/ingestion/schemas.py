"""The shape of each dataset: column names, valid ranges, and which columns identify a unique row.

Both the API clients (validation) and the storage layer (de-duplication) read from here,
so a column is defined in exactly one place. The same definitions can later be used to
create Hopsworks feature groups.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
DATE_FMT = "%Y-%m-%d"


@dataclass(frozen=True)
class DatasetSpec:
    name: str                                   # also the CSV file name (without .csv)
    columns: Tuple[str, ...]                    # final column order
    critical: Tuple[str, ...]                   # rows missing any of these are dropped
    key_columns: Tuple[str, ...]                # these together identify one unique row
    ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)   # valid (min, max)
    time_columns: Dict[str, str] = field(default_factory=dict)             # column -> output format


# ---- OpenWeather: weather (current and historical "timemachine") ----
OPENWEATHER_WEATHER = DatasetSpec(
    name="openweather_weather",
    columns=(
        "timestamp_utc", "city", "latitude", "longitude",
        "temperature_c", "humidity_pct", "wind_speed_ms", "wind_deg",
        "precipitation_mm", "uv_index", "pressure_hpa", "cloud_pct",
        "weather_description", "source", "ingested_at_utc",
    ),
    critical=("timestamp_utc", "temperature_c", "humidity_pct", "wind_speed_ms"),
    key_columns=("timestamp_utc", "city"),
    ranges={
        "temperature_c": (-50, 60),
        "humidity_pct": (0, 100),
        "wind_speed_ms": (0, 120),
        "wind_deg": (0, 360),
        "precipitation_mm": (0, 500),
        "uv_index": (0, 20),
        "pressure_hpa": (850, 1100),
        "cloud_pct": (0, 100),
    },
    time_columns={"timestamp_utc": ISO_FMT, "ingested_at_utc": ISO_FMT},
)

# ---- OpenWeather: air pollution (current and history). ow_aqi is a 1-5 scale, not the US AQI. ----
OPENWEATHER_AIR = DatasetSpec(
    name="openweather_air",
    columns=(
        "timestamp_utc", "city", "latitude", "longitude", "ow_aqi",
        "co", "no", "no2", "o3", "so2", "pm2_5", "pm10", "nh3",
        "source", "ingested_at_utc",
    ),
    critical=("timestamp_utc", "ow_aqi"),
    key_columns=("timestamp_utc", "city"),
    ranges={
        "ow_aqi": (1, 5),
        "co": (0, 100000), "no": (0, 100000), "no2": (0, 100000), "o3": (0, 100000),
        "so2": (0, 100000), "pm2_5": (0, 100000), "pm10": (0, 100000), "nh3": (0, 100000),
    },
    time_columns={"timestamp_utc": ISO_FMT, "ingested_at_utc": ISO_FMT},
)

# ---- AQICN: current air quality. "aqi" is always the US AQI (0-500); we compute it
# from the station's per-pollutant sub-indices when possible and store the raw
# reported value in "reported_aqi" for auditing. ----
AQICN_AIR = DatasetSpec(
    name="aqicn_air",
    columns=(
        "timestamp_utc", "city", "latitude", "longitude",
        "station_id", "station_name", "station_lat", "station_lon",
        "aqi", "reported_aqi", "aqi_pollutant", "dominant_pollutant",
        "pm25", "pm10", "o3", "no2", "so2", "co",
        "temperature_c", "humidity_pct", "pressure_hpa", "wind_speed_ms",
        "source", "ingested_at_utc",
    ),
    critical=("timestamp_utc", "station_id", "aqi"),
    key_columns=("timestamp_utc", "station_id"),
    ranges={
        "aqi": (0, 1000),
        "reported_aqi": (0, 1000),
        "pm25": (0, 1000), "pm10": (0, 1000), "o3": (0, 1000),
        "no2": (0, 1000), "so2": (0, 1000), "co": (0, 1000),
        "temperature_c": (-50, 60),
        "humidity_pct": (0, 100),
        "pressure_hpa": (850, 1100),
        "wind_speed_ms": (0, 120),
    },
    time_columns={"timestamp_utc": ISO_FMT, "ingested_at_utc": ISO_FMT},
)

# ---- OpenAQ v3: historical air quality. Pollutants are stored in µg/m³ and us_aqi is
# the EPA US AQI (0-500) computed from them with proper averaging periods. ----
OPENAQ_AIR = DatasetSpec(
    name="openaq_air",
    columns=(
        "timestamp_utc", "city", "latitude", "longitude",
        "location_id", "location_name",
        "pm25", "pm10", "no2", "so2", "co", "o3",
        "us_aqi", "aqi_pollutant",
        "source", "ingested_at_utc",
    ),
    critical=("timestamp_utc", "location_id"),
    key_columns=("timestamp_utc", "location_id"),
    ranges={
        "pm25": (0, 1000000), "pm10": (0, 1000000), "no2": (0, 1000000),
        "so2": (0, 1000000), "co": (0, 1000000), "o3": (0, 1000000),
        "us_aqi": (0, 500),
    },
    time_columns={"timestamp_utc": ISO_FMT, "ingested_at_utc": ISO_FMT},
)

# ---- AQICN: daily forecast (long format: one row per day and pollutant) ----
AQICN_FORECAST = DatasetSpec(
    name="aqicn_forecast",
    columns=(
        "issued_at_utc", "forecast_date", "city", "station_id", "pollutant",
        "avg", "min", "max", "ingested_at_utc",
    ),
    critical=("issued_at_utc", "forecast_date", "station_id", "pollutant"),
    key_columns=("issued_at_utc", "forecast_date", "station_id", "pollutant"),
    ranges={"avg": (0, 1000), "min": (0, 1000), "max": (0, 1000)},
    time_columns={
        "issued_at_utc": ISO_FMT,
        "forecast_date": DATE_FMT,
        "ingested_at_utc": ISO_FMT,
    },
)
