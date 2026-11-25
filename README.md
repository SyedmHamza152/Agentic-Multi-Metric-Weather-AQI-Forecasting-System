# AgentWeather

Weather and air quality forecasting agents for **Lahore, Pakistan**
(10Pearls SHINE Internship, Cohort 10).

**US AQI (0–500) is the single AQI standard across the whole project.** It is
calculated from raw pollutant concentrations using official US EPA breakpoints
and averaging-period rules (`src/agentweather/features/us_aqi.py`).

**Current status: Phase 1 - data collection.** Later phases add feature
engineering, models (SARIMAX, Prophet), Hopsworks, LangGraph agents,
Streamlit, and GitHub Actions.

## What it collects

| Dataset | Source | Coverage | AQI |
|---|---|---|---|
| `openweather_weather.csv` | OpenWeather 2.5 | current only (history needs One Call 3.0) | - |
| `openweather_air.csv` | OpenWeather 2.5 | hourly since 27 Nov 2020 (free) | `ow_aqi` is a 1–5 scale, **not** US AQI |
| `openaq_air.csv` | OpenAQ v3 | hourly PM2.5/PM10 per Lahore station (varies by station, see below) | `us_aqi` = real US AQI |
| `aqicn_air.csv` | AQICN/WAQI | snapshots only; build history by scheduling `--mode current` | `aqi` = verified US AQI, raw value in `reported_aqi` |
| `aqicn_forecast.csv` | AQICN/WAQI | 5-day pollutant forecast | - |

Lahore's OpenAQ stations mostly measure **PM2.5/PM10 only** — there is no
historical NO₂/SO₂/CO/O₃ data, so the US AQI is driven by particulate matter.
Never assume 5 years of data exist; each station's real coverage is logged
during the backfill and stored per `location_id`.

## Setup (Windows PowerShell; on Mac/Linux use `source .venv/bin/activate`)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e ".[dev]"
copy .env.example .env
```

Open `.env` and fill in:

- `OPENWEATHER_API_KEY` from https://home.openweathermap.org/api_keys
- `AQICN_API_TOKEN` from https://aqicn.org/data-platform/token/
- `OPENAQ_API_KEY` from https://docs.openaq.org (free v3 API key)

## Run

```powershell
# Latest readings from OpenWeather + AQICN -> data/raw/*.csv
python -m agentweather.pipelines.ingestion_pipeline --mode current

# Backfill history (OpenWeather air pollution + OpenAQ pollutants + US AQI)
python -m agentweather.pipelines.ingestion_pipeline --mode historical --days 1826

# Also backfill weather history (needs the One Call 3.0 subscription;
# one API call per timestamp, so use --step-hours/--max-calls to stay in quota)
python -m agentweather.pipelines.ingestion_pipeline --mode historical --days 365 --include-weather-history
```

Historical mode runs each source as an independent job — one failure does not
stop the others, and the store deduplicates on the dataset's key columns, so
re-running backfills is safe.

## Test

```powershell
pytest
```

Tests use fake API responses, so they need no keys and no internet.

## US AQI calculation

`agentweather.features.us_aqi` implements the EPA rules:

- Sub-index per pollutant from EPA breakpoints; overall AQI = highest valid
  sub-index (that pollutant becomes `aqi_pollutant`).
- Averaging periods: PM2.5/PM10 → 24 h, O₃/CO → 8 h, NO₂/SO₂ → 1 h.
  Rolling averages need ≥75% of the window present, otherwise AQI is missing —
  gaps are never filled with fabricated values.
- Units: everything is normalized to µg/m³ for storage; conversion to the
  EPA units (ppm/ppb) uses the molar volume at 25 °C.
- Truncation (not rounding) is applied before the AQI formula; the result is
  rounded to an integer and capped at 500.

## Data notes

- **AQICN** has no history API — schedule `--mode current` to accumulate it.
  Its `aqi` is the US AQI; the raw reported value is kept in `reported_aqi`
  and the code warns if it disagrees with the computed value (possible
  non-US scale).
- **OpenWeather air pollution history** is free since 27 Nov 2020.
- **Weather history** (temperature, wind, UV, ...) needs the paid One Call 3.0
  "timemachine" endpoint: 1 call per timestamp.
- **OpenAQ** coverage is whatever the stations actually report; the pipeline
  logs each sensor's date range and skips empty ones.
