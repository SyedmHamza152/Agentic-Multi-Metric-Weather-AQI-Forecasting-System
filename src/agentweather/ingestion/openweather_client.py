"""OpenWeather client.

What it collects
  - Current weather (free API 2.5): temperature, humidity, wind, rain, pressure, clouds.
  - UV index (One Call 3.0): needs the separate "One Call by Call" subscription.
    If it is not available, the weather row is still saved, with uv_index left empty.
  - Current air pollution (free API 2.5): gases and particles.
  - Air pollution history (free API 2.5): hourly data since 27 Nov 2020.
  - Weather history via One Call 3.0 "timemachine": one API call per timestamp.
    Its UV index only goes back 5 days.

This file only talks to OpenWeather. AQICN is in aqicn_client.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List, Mapping, Optional

import pandas as pd

from agentweather.config.settings import CityConfig, OpenWeatherConfig
from agentweather.ingestion.schemas import (
    ISO_FMT,
    OPENWEATHER_AIR,
    OPENWEATHER_WEATHER,
)
from agentweather.ingestion.validators import safe_float, validate_dataframe
from agentweather.utils.exceptions import (
    AgentWeatherError,
    AuthenticationError,
    ConfigError,
    DataValidationError,
    FeatureUnavailableError,
)
from agentweather.utils.http import build_session, get_json
from agentweather.utils.logger import get_logger

logger = get_logger(__name__)

AIR_HISTORY_START = datetime(2020, 11, 27, tzinfo=timezone.utc)  # earliest air pollution history
AIR_HISTORY_CHUNK_DAYS = 30  # ask for history in 30-day pieces to keep responses small


def _iso_from_epoch(epoch: Any) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime(ISO_FMT)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FMT)


# --------------------------------------------------------------------------------------
# Parsing functions: turn raw API JSON into plain dict rows. No network here, easy to test.
# --------------------------------------------------------------------------------------

def parse_current_weather(payload: Mapping[str, Any], city: CityConfig) -> dict:
    """Parse the /data/2.5/weather response into one row."""
    try:
        main = payload["main"]
        wind = payload.get("wind") or {}
        rain = safe_float((payload.get("rain") or {}).get("1h")) or 0.0
        snow = safe_float((payload.get("snow") or {}).get("1h")) or 0.0
        weather = (payload.get("weather") or [{}])[0]
        return {
            "timestamp_utc": _iso_from_epoch(payload["dt"]),
            "city": city.name,
            "latitude": city.latitude,
            "longitude": city.longitude,
            "temperature_c": safe_float(main["temp"]),
            "humidity_pct": safe_float(main["humidity"]),
            "wind_speed_ms": safe_float(wind.get("speed")),
            "wind_deg": safe_float(wind.get("deg")),
            "precipitation_mm": rain + snow,
            "uv_index": None,  # filled in separately from One Call
            "pressure_hpa": safe_float(main.get("pressure")),
            "cloud_pct": safe_float((payload.get("clouds") or {}).get("all")),
            "weather_description": weather.get("description"),
            "source": "openweather_current",
            "ingested_at_utc": _now_iso(),
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DataValidationError(
            f"OpenWeather current weather: unexpected response shape ({exc!r})"
        ) from None


def parse_timemachine(payload: Mapping[str, Any], city: CityConfig) -> List[dict]:
    """Parse a One Call 3.0 /timemachine response into rows."""
    try:
        rows = []
        for item in payload["data"]:
            rows.append({
                "timestamp_utc": _iso_from_epoch(item["dt"]),
                "city": city.name,
                "latitude": city.latitude,
                "longitude": city.longitude,
                "temperature_c": safe_float(item["temp"]),
                "humidity_pct": safe_float(item["humidity"]),
                "wind_speed_ms": safe_float(item.get("wind_speed")),
                "wind_deg": safe_float(item.get("wind_deg")),
                "precipitation_mm": (safe_float((item.get("rain") or {}).get("1h")) or 0.0)
                + (safe_float((item.get("snow") or {}).get("1h")) or 0.0),
                "uv_index": safe_float(item.get("uvi")),
                "pressure_hpa": safe_float(item.get("pressure")),
                "cloud_pct": safe_float(item.get("clouds")),
                "weather_description": ((item.get("weather") or [{}])[0]).get("description"),
                "source": "openweather_timemachine",
                "ingested_at_utc": _now_iso(),
            })
        return rows
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DataValidationError(
            f"OpenWeather timemachine: unexpected response shape ({exc!r})"
        ) from None


def parse_air_pollution(payload: Mapping[str, Any], city: CityConfig, source: str) -> List[dict]:
    """Parse an /air_pollution (current, forecast or history) response into rows."""
    try:
        rows = []
        for item in payload["list"]:
            comp = item.get("components") or {}
            rows.append({
                "timestamp_utc": _iso_from_epoch(item["dt"]),
                "city": city.name,
                "latitude": city.latitude,
                "longitude": city.longitude,
                "ow_aqi": safe_float(item["main"]["aqi"]),
                "co": safe_float(comp.get("co")),
                "no": safe_float(comp.get("no")),
                "no2": safe_float(comp.get("no2")),
                "o3": safe_float(comp.get("o3")),
                "so2": safe_float(comp.get("so2")),
                "pm2_5": safe_float(comp.get("pm2_5")),
                "pm10": safe_float(comp.get("pm10")),
                "nh3": safe_float(comp.get("nh3")),
                "source": source,
                "ingested_at_utc": _now_iso(),
            })
        return rows
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DataValidationError(
            f"OpenWeather air pollution: unexpected response shape ({exc!r})"
        ) from None


# --------------------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------------------

class OpenWeatherClient:
    SERVICE = "OpenWeather"

    def __init__(
        self,
        api_key: str,
        city: CityConfig,
        config: OpenWeatherConfig,
        session=None,
    ) -> None:
        if not api_key:
            raise ConfigError("OpenWeatherClient needs an API key")
        self._api_key = api_key
        self.city = city
        self.config = config
        self._session = session or build_session(max_retries=config.max_retries)

    # ---- internal helper ----
    def _get(self, path: str, **params: Any) -> Any:
        query = {
            "lat": self.city.latitude,
            "lon": self.city.longitude,
            "appid": self._api_key,
            **params,
        }
        url = f"{self.config.base_url.rstrip('/')}{path}"
        return get_json(self._session, url, query, self.config.timeout_seconds, self.SERVICE)

    # ---- current data ----
    def fetch_current_uv(self) -> float:
        """Current UV index from One Call 3.0."""
        try:
            payload = self._get(
                "/data/3.0/onecall",
                exclude="minutely,hourly,daily,alerts",
                units=self.config.units,
            )
        except AuthenticationError as exc:
            raise FeatureUnavailableError(
                "OpenWeather: One Call 3.0 is not enabled for this key "
                "(it needs the 'One Call by Call' subscription)"
            ) from exc
        uvi = safe_float((payload.get("current") or {}).get("uvi"))
        if uvi is None:
            raise DataValidationError("OpenWeather One Call: no current UV index in response")
        return uvi

    def fetch_current_weather(self) -> pd.DataFrame:
        """Current weather as a one-row DataFrame, including UV index when available."""
        payload = self._get("/data/2.5/weather", units=self.config.units)
        row = parse_current_weather(payload, self.city)

        if self.config.use_onecall:
            try:
                row["uv_index"] = self.fetch_current_uv()
            except AgentWeatherError as exc:
                # UV is optional here: keep the rest of the weather data.
                logger.warning("UV index unavailable, saving weather without it: %s", exc)

        return validate_dataframe(pd.DataFrame([row]), OPENWEATHER_WEATHER)

    def fetch_current_air_pollution(self) -> pd.DataFrame:
        """Current OpenWeather air pollution as a one-row DataFrame."""
        payload = self._get("/data/2.5/air_pollution")
        rows = parse_air_pollution(payload, self.city, source="openweather_current")
        return validate_dataframe(pd.DataFrame(rows), OPENWEATHER_AIR)

    # ---- historical data ----
    def fetch_air_pollution_history(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Hourly air pollution between two datetimes (free plan, history since 27 Nov 2020)."""
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        if start < AIR_HISTORY_START:
            logger.warning("Air pollution history starts %s; moving start date.", AIR_HISTORY_START.date())
            start = AIR_HISTORY_START
        if end <= start:
            raise DataValidationError("Air pollution history: end must be after start")

        rows: List[dict] = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=AIR_HISTORY_CHUNK_DAYS), end)
            logger.info("Fetching air pollution history %s -> %s", cursor.date(), chunk_end.date())
            payload = self._get(
                "/data/2.5/air_pollution/history",
                start=int(cursor.timestamp()),
                end=int(chunk_end.timestamp()),
            )
            rows.extend(parse_air_pollution(payload, self.city, source="openweather_history"))
            cursor = chunk_end

        df = pd.DataFrame(rows).drop_duplicates(subset=["timestamp_utc"]) if rows else pd.DataFrame()
        return validate_dataframe(df, OPENWEATHER_AIR)

    def fetch_weather_history(
        self, timestamps: Iterable[datetime], max_calls: int = 100
    ) -> pd.DataFrame:
        """Historical weather from One Call 3.0 timemachine. One API call per timestamp.

        max_calls protects your daily quota (1,000 free calls per day on One Call 3.0).
        If more timestamps are given, only the most recent ones are fetched.
        """
        stamps = sorted(ts.astimezone(timezone.utc) for ts in timestamps)
        if len(stamps) > max_calls:
            logger.warning("%d timestamps requested; limiting to the latest %d", len(stamps), max_calls)
            stamps = stamps[-max_calls:]

        rows: List[dict] = []
        failures = 0
        for ts in stamps:
            try:
                payload = self._get(
                    "/data/3.0/onecall/timemachine",
                    dt=int(ts.timestamp()),
                    units=self.config.units,
                )
                rows.extend(parse_timemachine(payload, self.city))
            except AuthenticationError as exc:
                raise FeatureUnavailableError(
                    "OpenWeather: One Call 3.0 timemachine is not enabled for this key"
                ) from exc
            except AgentWeatherError as exc:
                failures += 1
                logger.warning("Skipping %s: %s", ts.strftime(ISO_FMT), exc)

        if failures:
            logger.warning("%d of %d timemachine calls failed", failures, len(stamps))
        df = pd.DataFrame(rows).drop_duplicates(subset=["timestamp_utc"]) if rows else pd.DataFrame()
        return validate_dataframe(df, OPENWEATHER_WEATHER)
