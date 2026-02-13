"""OpenWeather API client (stdlib only)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import http

OWM_BASE = "https://api.openweathermap.org"


def geocode_direct(*, api_key: str, q: str, limit: int = 5) -> List[Dict[str, Any]]:
    url = f"{OWM_BASE}/geo/1.0/direct"
    data = http.get_json(url, {"q": q, "limit": limit, "appid": api_key}, timeout_s=20)
    return data if isinstance(data, list) else []


def geocode_zip(*, api_key: str, zip_code: str, country: str) -> Dict[str, Any]:
    url = f"{OWM_BASE}/geo/1.0/zip"
    data = http.get_json(url, {"zip": f"{zip_code},{country}", "appid": api_key}, timeout_s=20)
    return data if isinstance(data, dict) else {}


def current_weather(
    *,
    api_key: str,
    lat: float,
    lon: float,
    units: str,
    lang: Optional[str] = None,
) -> Dict[str, Any]:
    url = f"{OWM_BASE}/data/2.5/weather"
    return http.get_json(
        url,
        {"lat": lat, "lon": lon, "units": units, "lang": lang, "appid": api_key},
        timeout_s=20,
    )


def forecast_5d_3h(
    *,
    api_key: str,
    lat: float,
    lon: float,
    units: str,
    lang: Optional[str] = None,
) -> Dict[str, Any]:
    url = f"{OWM_BASE}/data/2.5/forecast"
    return http.get_json(
        url,
        {"lat": lat, "lon": lon, "units": units, "lang": lang, "appid": api_key},
        timeout_s=30,
    )

