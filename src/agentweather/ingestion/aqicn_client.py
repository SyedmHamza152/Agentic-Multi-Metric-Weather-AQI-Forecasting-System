"""AQICN (World Air Quality Index) client.

What it collects
  - The current air quality reading from the monitoring station nearest to the city
    (or a station you pin with `station_id` in settings.yaml): AQI, pollutants, and
    a few weather values.
  - The daily forecast that comes in the same response (pm25, pm10, o3, uvi ...).

Limits you should know
  - The AQICN API gives current data and a short forecast only. Long historical data is
    published on the AQICN "data platform" website, not through this API.
    To build history, run the current-data pipeline on a schedule (GitHub Actions, later).
  - "Nearest station" can be outside the city. The station name and location are stored
    with every row so you can check this.

This file only talks to AQICN. OpenWeather is in openweather_client.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Tuple

import pandas as pd

from agentweather.config.settings import AQICNConfig, CityConfig
from agentweather.features.us_aqi import aqi_from_subindices
from agentweather.ingestion.schemas import AQICN_AIR, AQICN_FORECAST, ISO_FMT
from agentweather.ingestion.validators import safe_float, validate_dataframe
from agentweather.utils.exceptions import (
    APIError,
    AuthenticationError,
    ConfigError,
    DataValidationError,
    RateLimitError,
)
from agentweather.utils.http import build_session, get_json
from agentweather.utils.logger import get_logger

logger = get_logger(__name__)

POLLUTANT_KEYS = ("pm25", "pm10", "o3", "no2", "so2", "co")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FMT)


def check_status(payload: Any) -> Mapping[str, Any]:
    """AQICN answers HTTP 200 even for errors; the real status is inside the JSON."""
    if not isinstance(payload, Mapping):
        raise DataValidationError("AQICN: response is not a JSON object")
    if payload.get("status") == "ok":
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise DataValidationError("AQICN: 'ok' response has no data object")
        return data

    detail = str(payload.get("data") or payload.get("message") or "unknown error")
    lowered = detail.lower()
    if "invalid key" in lowered or "invalid token" in lowered:
        raise AuthenticationError("AQICN: token rejected (Invalid key)")
    if "quota" in lowered:
        raise RateLimitError(f"AQICN: quota exceeded ({detail})")
    raise APIError(f"AQICN: API returned an error: {detail}")


def _reading_time(data: Mapping[str, Any]) -> str:
    """Time of the measurement as UTC text. Prefers the epoch value, falls back to ISO text."""
    time_info = data.get("time") or {}
    if time_info.get("v"):
        return datetime.fromtimestamp(int(time_info["v"]), tz=timezone.utc).strftime(ISO_FMT)
    if time_info.get("iso"):
        return pd.to_datetime(time_info["iso"], utc=True).strftime(ISO_FMT)
    raise DataValidationError("AQICN: reading has no timestamp")


def parse_current(data: Mapping[str, Any], city: CityConfig) -> dict:
    """Parse the 'data' object of a feed response into one row."""
    try:
        iaqi = data.get("iaqi") or {}
        station = data.get("city") or {}
        geo = station.get("geo") or [None, None]

        def value(key: str) -> Optional[float]:
            return safe_float((iaqi.get(key) or {}).get("v"))

        reported_aqi = safe_float(data.get("aqi"))  # AQICN sends "-" when a station has no value

        # AQICN's per-pollutant iaqi values are sub-indices on the station's AQI
        # scale. WAQI documents its headline 'aqi' as the US AQI; we verify that
        # by recomputing the AQI as the highest sub-index and comparing.
        sub_indices = {key: value(key) for key in POLLUTANT_KEYS}
        us_aqi, us_aqi_pollutant = aqi_from_subindices(sub_indices)

        if us_aqi is not None:
            aqi = us_aqi
            if reported_aqi is not None and abs(us_aqi - reported_aqi) > max(
                10, 0.15 * reported_aqi
            ):
                logger.warning(
                    "AQICN station %s: reported AQI %s differs from computed US AQI %s; "
                    "the station may use a non-US scale, storing the computed value",
                    data.get("idx"), reported_aqi, us_aqi,
                )
        else:
            # No pollutant sub-indices to compute from; the reported value is the
            # documented US AQI for WAQI stations.
            aqi = reported_aqi
            us_aqi_pollutant = None

        row = {
            "timestamp_utc": _reading_time(data),
            "city": city.name,
            "latitude": city.latitude,
            "longitude": city.longitude,
            "station_id": str(data["idx"]) if data.get("idx") is not None else None,
            "station_name": station.get("name"),
            "station_lat": safe_float(geo[0]),
            "station_lon": safe_float(geo[1]),
            "aqi": aqi,
            "reported_aqi": reported_aqi,
            "aqi_pollutant": us_aqi_pollutant,
            "dominant_pollutant": data.get("dominentpol"),  # (sic) AQICN's spelling
            "temperature_c": value("t"),
            "humidity_pct": value("h"),
            "pressure_hpa": value("p"),
            "wind_speed_ms": value("w"),
            "source": "aqicn_feed",
            "ingested_at_utc": _now_iso(),
        }
        for key in POLLUTANT_KEYS:
            row[key] = value(key)
        return row
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise DataValidationError(f"AQICN: unexpected response shape ({exc!r})") from None


def parse_forecast(data: Mapping[str, Any], city: CityConfig, issued_at: str, station_id: str) -> List[dict]:
    """Parse forecast.daily into long-format rows (one row per day and pollutant)."""
    daily = ((data.get("forecast") or {}).get("daily")) or {}
    rows: List[dict] = []
    for pollutant, days in daily.items():
        for item in days or []:
            rows.append({
                "issued_at_utc": issued_at,
                "forecast_date": item.get("day"),
                "city": city.name,
                "station_id": station_id,
                "pollutant": pollutant,
                "avg": safe_float(item.get("avg")),
                "min": safe_float(item.get("min")),
                "max": safe_float(item.get("max")),
                "ingested_at_utc": _now_iso(),
            })
    return rows


class AQICNClient:
    SERVICE = "AQICN"

    def __init__(self, token: str, city: CityConfig, config: AQICNConfig, session=None) -> None:
        if not token:
            raise ConfigError("AQICNClient needs an API token")
        self._token = token
        self.city = city
        self.config = config
        self._session = session or build_session(max_retries=config.max_retries)

    def _feed_url(self) -> str:
        base = self.config.base_url.rstrip("/")
        if self.config.station_id:
            return f"{base}/feed/@{self.config.station_id}/"
        return f"{base}/feed/geo:{self.city.latitude};{self.city.longitude}/"

    def fetch_feed(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """One API call -> (current reading DataFrame, forecast DataFrame).

        The forecast DataFrame may be empty if the station has no forecast.
        """
        payload = get_json(
            self._session,
            self._feed_url(),
            {"token": self._token},
            self.config.timeout_seconds,
            self.SERVICE,
        )
        data = check_status(payload)

        row = parse_current(data, self.city)
        current = validate_dataframe(pd.DataFrame([row]), AQICN_AIR)

        logger.info(
            "AQICN station: %s (id %s), AQI %s",
            row["station_name"], row["station_id"], row["aqi"],
        )

        forecast_rows = parse_forecast(data, self.city, row["timestamp_utc"], row["station_id"])
        if forecast_rows:
            forecast = validate_dataframe(pd.DataFrame(forecast_rows), AQICN_FORECAST)
        else:
            logger.info("AQICN: no forecast in response")
            forecast = pd.DataFrame(columns=list(AQICN_FORECAST.columns))
        return current, forecast
