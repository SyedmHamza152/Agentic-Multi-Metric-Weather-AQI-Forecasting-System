# AgentWeather

Weather and air quality forecasting agents for **Lahore, Pakistan**
(10Pearls SHINE Internship, Cohort 10).

**Current status: Phase 1 - project structure and data collection.**
Later phases add feature engineering, models, Hopsworks, LangGraph agents, Streamlit, and GitHub Actions.

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

## Run

```powershell
# Latest readings from both APIs -> data/raw/*.csv
python -m agentweather.pipelines.ingestion_pipeline --mode current

# Backfill history (OpenWeather air pollution, free plan)
python -m agentweather.pipelines.ingestion_pipeline --mode historical --days 30

# Also backfill weather + UV history (needs the One Call 3.0 subscription)
python -m agentweather.pipelines.ingestion_pipeline --mode historical --days 5 --include-weather-history
```

## Test

```powershell
pytest
```

Tests use fake API responses, so they need no keys and no internet.

## Output files (`data/raw/`)

| File | Source | Content |
|---|---|---|
| `openweather_weather.csv` | OpenWeather | temperature, humidity, wind, rain, UV, pressure, clouds |
| `openweather_air.csv` | OpenWeather | pollutants and a 1-5 air quality index |
| `aqicn_air.csv` | AQICN | AQI (our AQI target), pollutants, station info |
| `aqicn_forecast.csv` | AQICN | daily forecast per pollutant (including UV) |

## Data limits

- **AQICN** has no history through its API. History builds up by running `--mode current` on a schedule.
- **OpenWeather air pollution history** is free and starts 27 Nov 2020.
- **UV index and weather history** need the One Call 3.0 subscription (1,000 free calls/day). Historical UV goes back 5 days only.
- AQICN uses the station nearest to the coordinates. Check `station_name` in `aqicn_air.csv`, and pin a station with `aqicn.station_id` in `config/settings.yaml` if needed.
