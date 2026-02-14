#!/usr/bin/env python3
"""CLI wrapper for OpenWeather current + forecast for any location.

Uses:
- OpenWeather Geocoding API (direct / zip)
- Current weather: /data/2.5/weather
- Forecast: /data/2.5/forecast (5-day / 3-hour)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lib import cache, dotenv, openweather

DEFAULT_UNITS = "imperial"
DEFAULT_LANG = None
CACHE_TTL_S = 600  # 10 minutes


def _api_key() -> Optional[str]:
    return os.environ.get("OPENWEATHER_API_KEY")

_US_STATE_CODES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA",
    "ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK",
    "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC",
}

_COUNTRY_SYNONYMS = {
    # OpenWeather expects ISO 3166-1 alpha-2 codes (e.g. GB, JP). "UK" is a common user input.
    "UK": "GB",
    "U.K.": "GB",
    "UNITED KINGDOM": "GB",
    "GREAT BRITAIN": "GB",
    "ENGLAND": "GB",
    "SCOTLAND": "GB",
    "WALES": "GB",
    "JAPAN": "JP",
    "UNITED STATES": "US",
    "USA": "US",
    "U.S.": "US",
}


def _normalize_country_token(token: str) -> str:
    cleaned = (token or "").strip()
    if not cleaned:
        return ""
    upper = cleaned.upper()
    return _COUNTRY_SYNONYMS.get(upper, upper)


def _geocode_candidates(q: str, default_country: str) -> list[str]:
    """Generate likely OpenWeather geocode query variants.

    OpenWeather geocoding expects: "city[,state][,country_code]" where country_code is ISO alpha-2.
    Users often provide "London, UK" which does not resolve ("UK" isn't accepted); we normalize to GB.
    """
    q = (q or "").strip()
    if not q:
        return []

    candidates = [q]
    if q.count(",") != 1:
        return candidates

    parts = [p.strip() for p in q.split(",")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return candidates

    city, region = parts[0], parts[1]
    region_upper = region.upper()

    # If the second token looks like a US state code, append the default country.
    if region_upper in _US_STATE_CODES:
        dc = _normalize_country_token(default_country)
        if dc:
            candidates.append(f"{city},{region_upper},{dc}")
        return candidates

    # Otherwise, treat the second token as a country-ish token and normalize.
    cc = _normalize_country_token(region)
    if cc and len(cc) == 2:
        candidates.append(f"{city},{cc}")
    # Also try "city,region,cc" for some cases (e.g. "London,England,GB").
    if cc:
        candidates.append(f"{city},{region},{cc}")
    return list(dict.fromkeys(candidates))


def _print_json_error(*, error: str, resolved_location: Optional[dict] = None) -> None:
    payload: dict[str, Any] = {"successful": False, "error": error}
    if resolved_location is not None:
        payload["resolved_location"] = resolved_location
    print(json.dumps(payload, indent=2, ensure_ascii=True))


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OpenWeather current + forecast for any location.")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        loc = sp.add_mutually_exclusive_group(required=False)
        loc.add_argument("--location", help="Free-form location (e.g., 'Austin, TX', 'London, UK').")
        loc.add_argument("--zip", dest="zip_code", help="Postal/zip code (use with --country).")
        # Lat/lon pair is validated below.
        sp.add_argument("--lat", type=float, help="Latitude.")
        sp.add_argument("--lon", type=float, help="Longitude.")

        sp.add_argument("--country", help="Country code for --zip (e.g., US).", default="US")
        sp.add_argument("--units", choices=["standard", "metric", "imperial"], default=DEFAULT_UNITS)
        sp.add_argument("--lang", default=DEFAULT_LANG, help="Language code (optional).")
        sp.add_argument("--json", action="store_true", help="Output JSON only.")
        sp.add_argument("--no-cache", action="store_true", help="Disable local cache.")

    cur = sub.add_parser("current", help="Current weather conditions.")
    add_common(cur)

    fc = sub.add_parser("forecast", help="Forecast (5-day / 3-hour).")
    add_common(fc)
    fc.add_argument("--hours", type=int, default=36, help="How many hours ahead to print (default: 36).")
    fc.add_argument("--daily", action="store_true", help="Summarize by day (min/max temps) instead of 3-hour periods.")

    return p.parse_args(argv)


def _ensure_latlon(args: argparse.Namespace) -> Tuple[float, float, Dict[str, Any]]:
    """Resolve to (lat, lon, resolved_location_metadata)."""
    # Explicit coordinates win.
    if args.lat is not None or args.lon is not None:
        if args.lat is None or args.lon is None:
            raise ValueError("Both --lat and --lon are required if specifying coordinates.")
        return float(args.lat), float(args.lon), {"source": "coords", "lat": float(args.lat), "lon": float(args.lon)}

    api_key = _api_key()
    if not api_key:
        raise ValueError("Missing OPENWEATHER_API_KEY.")

    if args.zip_code:
        geo = openweather.geocode_zip(api_key=api_key, zip_code=args.zip_code, country=args.country)
        if not geo or "lat" not in geo or "lon" not in geo:
            raise ValueError(f"Could not geocode zip {args.zip_code} {args.country}.")
        return float(geo["lat"]), float(geo["lon"]), {
            "source": "zip",
            "zip": args.zip_code,
            "country": args.country,
            "name": geo.get("name"),
            "lat": float(geo["lat"]),
            "lon": float(geo["lon"]),
        }

    if args.location:
        q = args.location
        matches: List[Dict[str, Any]] = []
        for cand in _geocode_candidates(str(q), str(args.country or "")):
            matches = openweather.geocode_direct(api_key=api_key, q=cand, limit=5)
            if matches:
                break
        if not matches:
            raise ValueError(
                f"Could not geocode location: {args.location} (try ISO country code, e.g. 'London, GB')"
            )
        best = matches[0]
        return float(best["lat"]), float(best["lon"]), {
            "source": "direct",
            "query": args.location,
            "name": best.get("name"),
            "state": best.get("state"),
            "country": best.get("country"),
            "lat": float(best["lat"]),
            "lon": float(best["lon"]),
        }

    raise ValueError("Provide --location, --zip (with --country), or --lat/--lon.")


def _unit_suffix(units: str) -> str:
    if units == "metric":
        return "C"
    if units == "imperial":
        return "F"
    return "K"


def _wind_units(units: str) -> str:
    # OpenWeather: metric = m/s, imperial = miles/hour, standard = m/s
    return "mph" if units == "imperial" else "m/s"


def _cache_key(prefix: str, lat: float, lon: float, units: str, lang: Optional[str]) -> str:
    return f"{prefix}|lat={lat:.4f}|lon={lon:.4f}|units={units}|lang={lang or ''}"


def _print_current_human(resolved: Dict[str, Any], data: Dict[str, Any], units: str) -> None:
    name = data.get("name") or resolved.get("name") or resolved.get("query") or "Unknown"
    w = (data.get("weather") or [{}])[0] if isinstance(data.get("weather"), list) else {}
    main = data.get("main") if isinstance(data.get("main"), dict) else {}
    wind = data.get("wind") if isinstance(data.get("wind"), dict) else {}

    temp = main.get("temp")
    feels = main.get("feels_like")
    humid = main.get("humidity")
    desc = w.get("description") or w.get("main")
    wind_spd = wind.get("speed")
    suffix = _unit_suffix(units)

    print(f"# Current Weather: {name}")
    print()
    print(f"- Location: {resolved}")
    if desc:
        print(f"- Conditions: {desc}")
    if temp is not None:
        print(f"- Temp: {temp} {suffix}")
    if feels is not None:
        print(f"- Feels like: {feels} {suffix}")
    if humid is not None:
        print(f"- Humidity: {humid}%")
    if wind_spd is not None:
        print(f"- Wind: {wind_spd} {_wind_units(units)}")


def _forecast_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = data.get("list")
    return items if isinstance(items, list) else []


def _print_forecast_human(resolved: Dict[str, Any], data: Dict[str, Any], units: str, hours: int, daily: bool) -> None:
    city = data.get("city") if isinstance(data.get("city"), dict) else {}
    name = city.get("name") or resolved.get("name") or resolved.get("query") or "Unknown"
    suffix = _unit_suffix(units)

    items = _forecast_items(data)
    print(f"# Forecast (5-day / 3-hour): {name}")
    print()
    print(f"- Location: {resolved}")
    print(f"- Items returned: {len(items)}")
    print()

    # Filter window.
    utc = getattr(dt, "UTC", dt.timezone.utc)
    now = dt.datetime.now(tz=utc).replace(tzinfo=None)
    cutoff = now + dt.timedelta(hours=max(1, int(hours)))

    def parse_dt(it: Dict[str, Any]) -> Optional[dt.datetime]:
        s = it.get("dt_txt")
        if isinstance(s, str):
            try:
                return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
        ts = it.get("dt")
        if isinstance(ts, (int, float)):
            try:
                return dt.datetime.utcfromtimestamp(int(ts))
            except Exception:
                return None
        return None

    window = []
    for it in items:
        if not isinstance(it, dict):
            continue
        when = parse_dt(it)
        if not when:
            continue
        if when <= cutoff:
            window.append((when, it))

    if not window:
        print("No forecast items in requested window.")
        return

    if not daily:
        print("## Periods")
        for when, it in window:
            main = it.get("main") if isinstance(it.get("main"), dict) else {}
            w = (it.get("weather") or [{}])[0] if isinstance(it.get("weather"), list) else {}
            desc = w.get("description") or w.get("main") or ""
            temp = main.get("temp")
            print(f"- {when.strftime('%Y-%m-%d %H:%M UTC')}: {temp}{suffix} {desc}".rstrip())
        return

    # Daily summary.
    by_day: Dict[str, List[Tuple[dt.datetime, Dict[str, Any]]]] = {}
    for when, it in window:
        key = when.date().isoformat()
        by_day.setdefault(key, []).append((when, it))

    print("## Daily Summary")
    for day in sorted(by_day.keys()):
        temps = []
        descs: Dict[str, int] = {}
        for _, it in by_day[day]:
            main = it.get("main") if isinstance(it.get("main"), dict) else {}
            t = main.get("temp")
            if isinstance(t, (int, float)):
                temps.append(float(t))
            w = (it.get("weather") or [{}])[0] if isinstance(it.get("weather"), list) else {}
            d = (w.get("description") or w.get("main") or "").strip()
            if d:
                descs[d] = descs.get(d, 0) + 1
        if temps:
            lo, hi = min(temps), max(temps)
            top_desc = max(descs.items(), key=lambda kv: kv[1])[0] if descs else ""
            extra = f" ({top_desc})" if top_desc else ""
            print(f"- {day}: {lo:.0f}{suffix} to {hi:.0f}{suffix}{extra}")


def main(argv: List[str]) -> int:
    args = _parse_args(argv)

    # Load repo .env so OPENWEATHER_API_KEY is available for any agent run context.
    dotenv.load_repo_dotenv(Path(__file__))

    api_key = _api_key()
    if not api_key:
        msg = "missing OPENWEATHER_API_KEY (set it in .env)"
        if args.json:
            _print_json_error(error=msg)
            return 0
        sys.stderr.write(f"error: {msg}\n")
        return 2

    try:
        lat, lon, resolved = _ensure_latlon(args)
    except ValueError as e:
        if args.json:
            _print_json_error(error=str(e))
            return 0
        sys.stderr.write(f"error: {e}\n")
        return 2

    units = args.units
    lang = args.lang

    if args.cmd == "current":
        key = _cache_key("current", lat, lon, units, lang)
        cached = None if args.no_cache else cache.get(key, CACHE_TTL_S)
        if cached is None:
            data = openweather.current_weather(api_key=api_key, lat=lat, lon=lon, units=units, lang=lang)
            if not args.no_cache and isinstance(data, dict):
                cache.set(key, data)
            source = {"cache": "miss", "endpoint": "/data/2.5/weather"}
        else:
            data = cached
            source = {"cache": "hit", "endpoint": "/data/2.5/weather"}

        if args.json:
            out = {"resolved_location": resolved, "current": data, "source": source}
            print(json.dumps(out, indent=2, ensure_ascii=True))
            return 0
        _print_current_human(resolved, data, units)
        return 0

    if args.cmd == "forecast":
        key = _cache_key("forecast", lat, lon, units, lang)
        cached = None if args.no_cache else cache.get(key, CACHE_TTL_S)
        if cached is None:
            data = openweather.forecast_5d_3h(api_key=api_key, lat=lat, lon=lon, units=units, lang=lang)
            if not args.no_cache and isinstance(data, dict):
                cache.set(key, data)
            source = {"cache": "miss", "endpoint": "/data/2.5/forecast"}
        else:
            data = cached
            source = {"cache": "hit", "endpoint": "/data/2.5/forecast"}

        if args.json:
            out = {"resolved_location": resolved, "forecast": data, "source": source}
            print(json.dumps(out, indent=2, ensure_ascii=True))
            return 0

        _print_forecast_human(resolved, data, units, hours=args.hours, daily=bool(args.daily))
        return 0

    sys.stderr.write("error: unknown command\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
