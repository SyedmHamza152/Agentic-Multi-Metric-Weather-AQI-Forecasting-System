"""OpenAQ v3 client: historical air quality measurements.

What it collects
  - Monitoring locations near the city (/v3/locations), scored by how many of the
    six US AQI pollutants they measure and how far back their data goes. The best
    few (openaq.max_locations in settings.yaml) are kept — Lahore has dozens of
    low-cost PM2.5 sensors but only a handful measure other pollutants.
  - Historical measurements per sensor (/v3/sensors/{id}/measurements), fetched
    in monthly chunks and paged through, then averaged to hourly values.

Output
  - One row per hour per location with pollutant concentrations in µg/m³ and the
    US AQI (0-500) calculated by agentweather.features.us_aqi using EPA
    averaging periods. Each location is its own series (location_id is part of
    the row key), so stations are never blended into a fabricated composite.
    Pollutants a station does not measure stay empty — we never invent data.

Auth: the API key is sent as the X-API-Key header (v3 requirement).
Docs: https://docs.openaq.org
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd

from agentweather.config.settings import CityConfig, OpenAQConfig
from agentweather.features.us_aqi import AQI_POLLUTANTS, add_us_aqi, to_ugm3
from agentweather.ingestion.schemas import ISO_FMT, OPENAQ_AIR
from agentweather.ingestion.validators import safe_float, validate_dataframe
from agentweather.utils.exceptions import (
    ConfigError,
    DataValidationError,
)
from agentweather.utils.http import build_session, get_json
from agentweather.utils.logger import get_logger

logger = get_logger(__name__)

MAX_PAGES_PER_CHUNK = 200  # safety stop so a runaway pagination cannot loop forever


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FMT)


def _results(payload: Any, service: str) -> List[dict]:
    """Return the results list from a v3 list response."""
    if not isinstance(payload, Mapping):
        raise DataValidationError(f"{service}: response is not a JSON object")
    results = payload.get("results")
    if not isinstance(results, list):
        raise DataValidationError(f"{service}: response has no 'results' list")
    return results


def _parameter_info(sensor: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Extract (parameter name, unit) from a v3 sensor object."""
    param = sensor.get("parameter")
    if isinstance(param, Mapping):
        return (
            str(param.get("name") or "").lower() or None,
            param.get("units") or param.get("unit"),
        )
    name = sensor.get("parameter_name") or sensor.get("name")
    return (str(name).lower() if name else None), sensor.get("units") or sensor.get("unit")


def _coverage_days(location: Mapping[str, Any]) -> float:
    """Days between datetimeFirst and datetimeLast, 0 when unknown."""
    try:
        first = pd.Timestamp((location.get("datetimeFirst") or {}).get("utc"))
        last = pd.Timestamp((location.get("datetimeLast") or {}).get("utc"))
        return max(0.0, (last - first).total_seconds() / 86400.0)
    except (TypeError, ValueError):
        return 0.0


def parse_locations(payload: Any, city: CityConfig) -> List[dict]:
    """All candidate locations from a /v3/locations response, best first.

    Score = (number of distinct US AQI pollutants) * 1e6 + coverage days, so a
    station measuring more pollutants always beats one measuring fewer, and
    coverage breaks ties.
    """
    candidates = []
    for loc in _results(payload, "OpenAQ locations"):
        coords = loc.get("coordinates") or {}
        sensors = loc.get("sensors") or []
        pollutants = {
            name for name, _ in (_parameter_info(s) for s in sensors)
            if name in AQI_POLLUTANTS
        }
        if loc.get("id") is None or not pollutants:
            continue
        candidates.append({
            "location_id": str(loc["id"]),
            "location_name": loc.get("name"),
            "latitude": safe_float(coords.get("latitude")),
            "longitude": safe_float(coords.get("longitude")),
            "datetime_first": (loc.get("datetimeFirst") or {}).get("utc"),
            "datetime_last": (loc.get("datetimeLast") or {}).get("utc"),
            "coverage_days": _coverage_days(loc),
            "pollutants": pollutants,
            "sensors": sensors,
        })
    if not candidates:
        raise DataValidationError(
            f"OpenAQ: no monitoring locations with AQI pollutants near {city.name}"
        )
    candidates.sort(key=lambda c: (len(c["pollutants"]), c["coverage_days"]), reverse=True)
    return candidates


def sensors_by_pollutant(location: Mapping[str, Any]) -> Dict[str, List[dict]]:
    """Group one location's embedded sensors by pollutant name."""
    grouped: Dict[str, List[dict]] = {}
    for sensor in location.get("sensors") or []:
        name, unit = _parameter_info(sensor)
        if name in AQI_POLLUTANTS and sensor.get("id") is not None:
            grouped.setdefault(name, []).append({"id": str(sensor["id"]), "unit": unit})
    return grouped


def parse_measurements(payload: Any) -> List[dict]:
    """Flatten one page of sensor measurements into {ts, value} rows.

    v3 returns period.datetimeFrom.utc as the interval start (data is already
    aggregated to the sensor's reporting interval, usually hourly).
    """
    rows = []
    for item in _results(payload, "OpenAQ measurements"):
        period = item.get("period") or {}
        dt_from = period.get("datetimeFrom") or {}
        ts = dt_from.get("utc") or (item.get("datetime") or {}).get("utc")
        value = safe_float(item.get("value"))
        if ts is None or value is None:
            continue
        rows.append({"timestamp_utc": ts, "value": value})
    return rows


def hourly_ugm3_frame(
    series_by_pollutant: Mapping[str, pd.DataFrame],
    location: Mapping[str, Any],
    city: CityConfig,
) -> pd.DataFrame:
    """Combine per-pollutant measurement frames into one hourly µg/m³ DataFrame."""
    hourly = {}
    for pollutant, df in series_by_pollutant.items():
        if df.empty:
            continue
        work = df.copy()
        work["timestamp_utc"] = pd.to_datetime(work["timestamp_utc"], utc=True)
        work["hour"] = work["timestamp_utc"].dt.floor("h")
        hourly[pollutant] = work.groupby("hour")["value"].mean()

    if not hourly:
        raise DataValidationError("OpenAQ: no usable measurements for any pollutant")

    wide = pd.DataFrame(hourly).sort_index()
    wide.index.name = "timestamp_utc"
    wide = wide.reset_index()
    wide["city"] = city.name
    wide["latitude"] = city.latitude
    wide["longitude"] = city.longitude
    wide["location_id"] = location["location_id"]
    wide["location_name"] = location["location_name"]
    return wide


class OpenAQClient:
    SERVICE = "OpenAQ"

    def __init__(
        self,
        api_key: str,
        city: CityConfig,
        config: OpenAQConfig,
        session=None,
    ) -> None:
        if not api_key:
            raise ConfigError("OpenAQClient needs an API key")
        self._api_key = api_key
        self.city = city
        self.config = config
        self._session = session or build_session(max_retries=config.max_retries)
        # v3 authenticates with the X-API-Key header.
        headers = getattr(self._session, "headers", None)
        if headers is not None:
            headers["X-API-Key"] = api_key

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        return get_json(self._session, url, params, self.config.timeout_seconds, self.SERVICE)

    # ---- discovery ----
    def find_locations(self) -> List[dict]:
        """Candidate locations near the city, best coverage first."""
        payload = self._get(
            "/locations",
            coordinates=f"{self.city.latitude},{self.city.longitude}",
            radius=self.config.search_radius_m,
            limit=100,
        )
        candidates = parse_locations(payload, self.city)
        for loc in candidates[: self.config.max_locations]:
            logger.info(
                "OpenAQ candidate: %s (id %s), pollutants %s, coverage %s -> %s",
                loc["location_name"], loc["location_id"],
                sorted(loc["pollutants"]), loc["datetime_first"], loc["datetime_last"],
            )
        return candidates[: self.config.max_locations]

    # ---- measurements ----
    def _fetch_sensor_chunk(self, sensor_id: str, start: datetime, end: datetime) -> List[dict]:
        """Page through all measurements of one sensor inside [start, end)."""
        rows: List[dict] = []
        page = 1
        while True:
            payload = self._get(
                f"/sensors/{sensor_id}/measurements",
                datetime_from=start.strftime(ISO_FMT),
                datetime_to=end.strftime(ISO_FMT),
                limit=self.config.page_limit,
                page=page,
            )
            batch = _results(payload, "OpenAQ measurements")
            rows.extend(parse_measurements({"results": batch}))
            # v3's meta.found is a string like ">10000" when truncated, so a
            # short page is the reliable end-of-data signal.
            if len(batch) < self.config.page_limit or page >= MAX_PAGES_PER_CHUNK:
                break
            page += 1
        return rows

    def fetch_pollutant_history(
        self,
        pollutant: str,
        sensors: List[dict],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """All measurements for one pollutant, converted to µg/m³.

        Tries each candidate sensor until one returns data. Coverage (first/last
        timestamp) is logged so gaps are visible instead of silently filled.
        """
        for sensor in sensors:
            unit = sensor.get("unit")
            rows: List[dict] = []
            cursor = start
            while cursor < end:
                chunk_end = min(cursor + timedelta(days=self.config.chunk_days), end)
                rows.extend(self._fetch_sensor_chunk(sensor["id"], cursor, chunk_end))
                cursor = chunk_end
            if not rows:
                logger.info("OpenAQ %s sensor %s: no measurements in range", pollutant, sensor["id"])
                continue

            df = pd.DataFrame(rows)
            df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
            df["value"] = [to_ugm3(pollutant, v, unit) for v in df["value"]]
            df = df.dropna(subset=["value"])
            if df.empty:
                logger.warning(
                    "OpenAQ %s sensor %s: all values had unusable units (%r)",
                    pollutant, sensor["id"], unit,
                )
                continue

            logger.info(
                "OpenAQ %s sensor %s: %d measurement(s), %s -> %s, unit %r -> µg/m³",
                pollutant, sensor["id"], len(df),
                df["timestamp_utc"].min().strftime(ISO_FMT),
                df["timestamp_utc"].max().strftime(ISO_FMT),
                unit,
            )
            return df[["timestamp_utc", "value"]]

        return pd.DataFrame(columns=["timestamp_utc", "value"])

    def _location_history(self, location: dict, start: datetime, end: datetime) -> pd.DataFrame:
        """Hourly pollutant frame + US AQI for one location."""
        grouped = sensors_by_pollutant(location)
        series_by_pollutant = {}
        for pollutant in AQI_POLLUTANTS:
            candidates = grouped.get(pollutant) or []
            if not candidates:
                continue
            frame = self.fetch_pollutant_history(pollutant, candidates, start, end)
            if not frame.empty:
                series_by_pollutant[pollutant] = frame

        wide = hourly_ugm3_frame(series_by_pollutant, location, self.city)
        wide = add_us_aqi(wide)
        wide["source"] = "openaq_v3"
        wide["ingested_at_utc"] = _now_iso()
        return wide

    def fetch_history(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Hourly pollutant concentrations (µg/m³) + US AQI for [start, end).

        Fetches the best-covered locations near the city. Only data the API
        actually returns is stored; missing pollutants or periods stay empty.
        """
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        if end <= start:
            raise DataValidationError("OpenAQ history: end must be after start")

        frames = []
        for location in self.find_locations():
            try:
                frames.append(self._location_history(location, start, end))
            except DataValidationError as exc:
                logger.warning("OpenAQ location %s skipped: %s", location["location_id"], exc)

        if not frames:
            raise DataValidationError("OpenAQ: no location returned any usable measurements")

        return validate_dataframe(pd.concat(frames, ignore_index=True), OPENAQ_AIR)
