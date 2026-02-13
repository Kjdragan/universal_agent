---
name: openweather
description: |
  Fetch current weather and forecasts for any location using the OpenWeather API.
  Use when an agent needs current conditions or a short-term forecast for a city/address/zip or coordinates, and the API key is available in `.env` as OPENWEATHER_API_KEY.
metadata:
  clawdbot:
    requires:
      bins: ["python3", "uv"]
---

# OpenWeather API Skill

This skill provides a small, reliable script wrapper around OpenWeather:

- Current conditions: `/data/2.5/weather`
- 5-day / 3-hour forecast: `/data/2.5/forecast`
- Geocoding: `/geo/1.0/direct` and `/geo/1.0/zip`

The script reads `OPENWEATHER_API_KEY` from the repo `.env` (and falls back to the process environment).

## Notes (Important)

- OpenWeather recommends calling no more than once per 10 minutes per location. This skill implements a 10-minute local cache by default.
- “Forecast” here is the 5-day/3-hour endpoint (not a long-range daily forecast).

## Usage

```bash
# Current weather by free-form location (geocoded)
uv run .claude/skills/openweather/scripts/openweather_weather.py current --location "Austin, TX"

# Forecast by free-form location
uv run .claude/skills/openweather/scripts/openweather_weather.py forecast --location "Austin, TX" --hours 36

# Current weather by coordinates
uv run .claude/skills/openweather/scripts/openweather_weather.py current --lat 30.2672 --lon -97.7431

# Forecast by zip (US example)
uv run .claude/skills/openweather/scripts/openweather_weather.py forecast --zip 78701 --country US

# JSON output (for downstream processing)
uv run .claude/skills/openweather/scripts/openweather_weather.py current --location "London, UK" --json
```

## Output Contract (Script)

- Human mode: prints a concise Markdown-like summary.
- JSON mode (`--json`): prints a single JSON object with:
  - `resolved_location` (name/state/country/lat/lon when available)
  - `current` or `forecast`
  - `source` metadata (cache hit/miss, endpoint)

