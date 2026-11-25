"""Load settings from config/settings.yaml and secrets from environment variables (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple, Union

import yaml
from dotenv import load_dotenv

from agentweather.utils.exceptions import ConfigError

# Project root = the folder that contains config/, data/, src/ ...
# AGENTWEATHER_HOME can override this (useful in CI or when the package is not installed with -e).
PROJECT_ROOT = Path(
    os.environ.get("AGENTWEATHER_HOME", Path(__file__).resolve().parents[3])
).resolve()

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"


@dataclass(frozen=True)
class CityConfig:
    name: str
    country: str
    latitude: float
    longitude: float
    timezone: str


@dataclass(frozen=True)
class OpenWeatherConfig:
    base_url: str
    timeout_seconds: float
    max_retries: int
    units: str
    use_onecall: bool


@dataclass(frozen=True)
class AQICNConfig:
    base_url: str
    timeout_seconds: float
    max_retries: int
    station_id: Optional[str]


@dataclass(frozen=True)
class OpenAQConfig:
    base_url: str
    timeout_seconds: float
    max_retries: int
    search_radius_m: int
    page_limit: int
    chunk_days: int
    max_locations: int


@dataclass(frozen=True)
class StorageConfig:
    backend: str
    raw_dir: Path
    processed_dir: Path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    city: CityConfig
    openweather: OpenWeatherConfig
    aqicn: AQICNConfig
    openaq: OpenAQConfig
    storage: StorageConfig
    targets: Tuple[str, ...]
    log_level: str
    # repr=False keeps secrets out of logs if a Settings object is ever printed.
    openweather_api_key: Optional[str] = field(default=None, repr=False)
    aqicn_api_token: Optional[str] = field(default=None, repr=False)
    openaq_api_key: Optional[str] = field(default=None, repr=False)

    def require_openweather_key(self) -> str:
        if not self.openweather_api_key:
            raise ConfigError("OPENWEATHER_API_KEY is not set. Add it to your .env file.")
        return self.openweather_api_key

    def require_aqicn_token(self) -> str:
        if not self.aqicn_api_token:
            raise ConfigError("AQICN_API_TOKEN is not set. Add it to your .env file.")
        return self.aqicn_api_token

    def require_openaq_key(self) -> str:
        if not self.openaq_api_key:
            raise ConfigError("OPENAQ_API_KEY is not set. Add it to your .env file.")
        return self.openaq_api_key


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ConfigError(f"settings.yaml: missing or invalid section '{key}'")
    return value


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_settings(
    config_path: Optional[Union[str, Path]] = None,
    env_file: Optional[Union[str, Path]] = None,
) -> Settings:
    """Read settings.yaml and the .env file, validate them, and return a Settings object."""
    load_dotenv(env_file or PROJECT_ROOT / ".env")  # does nothing if the file does not exist

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file is not valid YAML: {exc}") from None

    try:
        city_raw = _section(raw, "city")
        ow_raw = _section(raw, "openweather")
        aq_raw = _section(raw, "aqicn")
        oa_raw = _section(raw, "openaq")
        st_raw = _section(raw, "storage")

        city = CityConfig(
            name=str(city_raw["name"]),
            country=str(city_raw["country"]),
            latitude=float(city_raw["latitude"]),
            longitude=float(city_raw["longitude"]),
            timezone=str(city_raw.get("timezone", "UTC")),
        )
        if not -90 <= city.latitude <= 90 or not -180 <= city.longitude <= 180:
            raise ConfigError("settings.yaml: latitude/longitude out of range")

        station = aq_raw.get("station_id")
        settings = Settings(
            project_root=PROJECT_ROOT,
            city=city,
            openweather=OpenWeatherConfig(
                base_url=str(ow_raw["base_url"]),
                timeout_seconds=float(ow_raw.get("timeout_seconds", 10)),
                max_retries=int(ow_raw.get("max_retries", 3)),
                units=str(ow_raw.get("units", "metric")),
                use_onecall=bool(ow_raw.get("use_onecall", True)),
            ),
            aqicn=AQICNConfig(
                base_url=str(aq_raw["base_url"]),
                timeout_seconds=float(aq_raw.get("timeout_seconds", 10)),
                max_retries=int(aq_raw.get("max_retries", 3)),
                station_id=str(station) if station not in (None, "") else None,
            ),
            openaq=OpenAQConfig(
                base_url=str(oa_raw["base_url"]),
                timeout_seconds=float(oa_raw.get("timeout_seconds", 20)),
                max_retries=int(oa_raw.get("max_retries", 3)),
                search_radius_m=int(oa_raw.get("search_radius_m", 50000)),
                page_limit=int(oa_raw.get("page_limit", 1000)),
                chunk_days=int(oa_raw.get("chunk_days", 31)),
                max_locations=int(oa_raw.get("max_locations", 3)),
            ),
            storage=StorageConfig(
                backend=str(st_raw.get("backend", "csv")).lower(),
                raw_dir=_resolve(PROJECT_ROOT, str(st_raw.get("raw_dir", "data/raw"))),
                processed_dir=_resolve(
                    PROJECT_ROOT, str(st_raw.get("processed_dir", "data/processed"))
                ),
            ),
            targets=tuple(raw.get("targets", [])),
            log_level=os.environ.get("LOG_LEVEL", str(raw.get("log_level", "INFO"))).upper(),
            openweather_api_key=os.environ.get("OPENWEATHER_API_KEY") or None,
            aqicn_api_token=os.environ.get("AQICN_API_TOKEN") or None,
            openaq_api_key=os.environ.get("OPENAQ_API_KEY") or None,
        )
    except KeyError as exc:
        raise ConfigError(f"settings.yaml: missing required key {exc}") from None
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"settings.yaml: invalid value ({exc})") from None

    return settings
