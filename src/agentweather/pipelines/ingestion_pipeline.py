"""Ingestion pipeline: fetch data from the APIs and save it through the store.

Run it from the project root:

    python -m agentweather.pipelines.ingestion_pipeline --mode current
    python -m agentweather.pipelines.ingestion_pipeline --mode historical --days 30
    python -m agentweather.pipelines.ingestion_pipeline --mode historical --days 1826

Historical mode collects, per source:
  - OpenWeather air pollution history (free, since 27 Nov 2020)
  - OpenAQ v3 pollutant measurements near Lahore, converted to hourly µg/m³
    and US AQI (0-500) — coverage depends on what the stations actually report
  - OpenWeather weather "timemachine" history (One Call 3.0 subscription,
    one API call per timestamp, so long ranges need many days of quota)

Each source runs as its own job. If one fails (for example AQICN is down), the others still
run and save their data. The exit code is 0 only if every job succeeded, so a GitHub Actions
run turns red when something went wrong.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence

from agentweather.config.settings import Settings, load_settings
from agentweather.ingestion.aqicn_client import AQICNClient
from agentweather.ingestion.openaq_client import OpenAQClient
from agentweather.ingestion.openweather_client import OpenWeatherClient
from agentweather.ingestion.schemas import (
    AQICN_AIR,
    AQICN_FORECAST,
    OPENAQ_AIR,
    OPENWEATHER_AIR,
    OPENWEATHER_WEATHER,
)
from agentweather.store import DataStore, get_store
from agentweather.utils.exceptions import AgentWeatherError
from agentweather.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


@dataclass
class JobResult:
    name: str
    ok: bool
    new_rows: int = 0
    error: str = ""


def _run_jobs(jobs: Sequence[tuple]) -> List[JobResult]:
    """Run each (name, function) job. A failure in one job never stops the others."""
    results: List[JobResult] = []
    for name, job in jobs:
        try:
            results.append(JobResult(name, True, new_rows=job()))
        except AgentWeatherError as exc:
            logger.error("Job '%s' failed: %s", name, exc)
            results.append(JobResult(name, False, error=str(exc)))
        except Exception as exc:  # unexpected bug: log the traceback, keep going
            logger.exception("Job '%s' crashed unexpectedly", name)
            results.append(JobResult(name, False, error=f"unexpected error: {exc!r}"))
    return results


def _openweather_client(settings: Settings) -> OpenWeatherClient:
    return OpenWeatherClient(settings.require_openweather_key(), settings.city, settings.openweather)


def _aqicn_client(settings: Settings) -> AQICNClient:
    return AQICNClient(settings.require_aqicn_token(), settings.city, settings.aqicn)


def _openaq_client(settings: Settings) -> OpenAQClient:
    return OpenAQClient(settings.require_openaq_key(), settings.city, settings.openaq)


def run_current(settings: Settings, store: DataStore) -> List[JobResult]:
    """Collect the latest reading from every source."""

    def weather() -> int:
        df = _openweather_client(settings).fetch_current_weather()
        return store.save(OPENWEATHER_WEATHER.name, df, OPENWEATHER_WEATHER.key_columns)

    def ow_air() -> int:
        df = _openweather_client(settings).fetch_current_air_pollution()
        return store.save(OPENWEATHER_AIR.name, df, OPENWEATHER_AIR.key_columns)

    def aqicn() -> int:
        current, forecast = _aqicn_client(settings).fetch_feed()
        added = store.save(AQICN_AIR.name, current, AQICN_AIR.key_columns)
        added += store.save(AQICN_FORECAST.name, forecast, AQICN_FORECAST.key_columns)
        return added

    return _run_jobs([
        ("openweather_weather", weather),
        ("openweather_air", ow_air),
        ("aqicn_air_and_forecast", aqicn),
    ])


def run_historical(
    settings: Settings,
    store: DataStore,
    days: int,
    include_weather_history: bool = False,
    step_hours: int = 6,
    max_calls: int = 100,
) -> List[JobResult]:
    """Backfill history where the API plans allow it."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    def ow_air_history() -> int:
        df = _openweather_client(settings).fetch_air_pollution_history(start, end)
        return store.save(OPENWEATHER_AIR.name, df, OPENWEATHER_AIR.key_columns)

    def openaq_history() -> int:
        df = _openaq_client(settings).fetch_history(start, end)
        return store.save(OPENAQ_AIR.name, df, OPENAQ_AIR.key_columns)

    jobs = [
        ("openweather_air_history", ow_air_history),
        ("openaq_air_history", openaq_history),
    ]

    if include_weather_history:
        def weather_history() -> int:
            stamps = []
            cursor = start
            while cursor < end:
                stamps.append(cursor)
                cursor += timedelta(hours=step_hours)
            df = _openweather_client(settings).fetch_weather_history(stamps, max_calls=max_calls)
            return store.save(OPENWEATHER_WEATHER.name, df, OPENWEATHER_WEATHER.key_columns)

        jobs.append(("openweather_weather_history", weather_history))

    logger.info("AQICN has no history through its API; run --mode current on a schedule to build it.")
    return _run_jobs(jobs)


def _summarize(results: List[JobResult]) -> int:
    logger.info("---- Summary ----")
    for r in results:
        if r.ok:
            logger.info("OK     %-30s %d new row(s)", r.name, r.new_rows)
        else:
            logger.error("FAILED %-30s %s", r.name, r.error)
    return 0 if all(r.ok for r in results) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AgentWeather data ingestion")
    parser.add_argument("--mode", choices=("current", "historical"), default="current")
    parser.add_argument("--days", type=int, default=30, help="historical mode: how many days back")
    parser.add_argument(
        "--include-weather-history", action="store_true",
        help="historical mode: also call OpenWeather timemachine (One Call 3.0 subscription needed)",
    )
    parser.add_argument("--step-hours", type=int, default=6, help="hours between timemachine samples")
    parser.add_argument("--max-calls", type=int, default=100, help="maximum timemachine API calls")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings.log_level, settings.project_root / "logs")
    logger.info("Starting ingestion: mode=%s city=%s", args.mode, settings.city.name)

    store = get_store(settings)
    if args.mode == "current":
        results = run_current(settings, store)
    else:
        results = run_historical(
            settings, store, args.days, args.include_weather_history, args.step_hours, args.max_calls
        )
    return _summarize(results)


if __name__ == "__main__":
    sys.exit(main())
