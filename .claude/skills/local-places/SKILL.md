---
name: local-places
description: >
  Search for nearby places (restaurants, cafes, gyms, etc.) via a local Google Places API
  proxy server running on localhost. Returns structured results with ratings, addresses,
  open status, price levels, and place details.
  USE when the user asks about nearby places, wants to find restaurants or businesses,
  asks "what's open near me", "find a coffee shop", "restaurants nearby", "best rated
  places in [area]", "where can I get [food type]", or any local discovery task.
  Requires the local server to be running first.
homepage: https://github.com/Hyaxia/local_places
metadata:
  {
    "openclaw":
      {
        "emoji": "📍",
        "requires": { "bins": ["uv"], "env": ["GOOGLE_PLACES_API_KEY"] },
        "primaryEnv": "GOOGLE_PLACES_API_KEY",
      },
  }
---

# 📍 Local Places

Search for nearby places using a local Google Places API proxy. Two-step flow: resolve location → search places.

---

## Server Setup

```bash
cd {baseDir}
echo "GOOGLE_PLACES_API_KEY=your-key" > .env
uv venv && uv pip install -e ".[dev]"
uv run --env-file .env uvicorn local_places.main:app --host 127.0.0.1 --port 8000
```

**Always ping the server first:**

```bash
curl http://127.0.0.1:8000/ping
# Expected: {"message": "pong"}
```

If the ping fails, the server is not running. Do not attempt API calls — inform the user and provide the startup command above.

---

## Conversation Flow

1. **Check server** with `/ping` before any other call
2. **Resolve location** if user says "near me" or gives a vague area → `POST /locations/resolve`
3. **Show numbered list** if multiple location matches → ask user to pick one
4. **Collect preferences** — type, open now, price level, minimum rating
5. **Search** with `location_bias` from the chosen location → `POST /places/search`
6. **Present results** — name, rating, address, open status, price level
7. **Offer to fetch details** (`GET /places/{place_id}`) or refine search

---

## Endpoint Reference

### `POST /locations/resolve`

Convert a free-text location string to `lat/lng` coordinates.

**Request:**

```json
{ "location_text": "Soho, London", "limit": 5 }
```

| Field | Type | Required | Default | Constraints |
|-------|------|----------|---------|-------------|
| `location_text` | string | ✅ | — | min length 1 |
| `limit` | int | | 5 | 1–10 |

**Response:**

```json
{
  "results": [
    {
      "place_id": "ChIJ...",
      "name": "Soho",
      "address": "Soho, London, UK",
      "location": { "lat": 51.5137, "lng": -0.1366 },
      "types": ["neighborhood", "political"]
    }
  ]
}
```

---

### `POST /places/search`

Search for places near a location with optional filters.

**Request:**

```json
{
  "query": "coffee shop",
  "location_bias": { "lat": 51.5137, "lng": -0.1366, "radius_m": 1000 },
  "filters": { "open_now": true, "min_rating": 4.0, "price_levels": [1, 2] },
  "limit": 10
}
```

| Field | Type | Required | Default | Constraints |
|-------|------|----------|---------|-------------|
| `query` | string | ✅ | — | min length 1 |
| `location_bias.lat` | float | | — | -90 to 90 |
| `location_bias.lng` | float | | — | -180 to 180 |
| `location_bias.radius_m` | float | | — | must be > 0 |
| `filters.types` | list[str] | | — | **MAX 1 type** (e.g. `["restaurant"]`) |
| `filters.open_now` | bool | | — | true/false |
| `filters.min_rating` | float | | — | 0–5 in **0.5 increments** |
| `filters.price_levels` | list[int] | | — | integers 0–4 (0=free, 4=very expensive) |
| `filters.keyword` | string | | — | additional keyword filter |
| `limit` | int | | 10 | 1–20 |
| `page_token` | string | | — | from previous `next_page_token` for pagination |

**Response:**

```json
{
  "results": [
    {
      "place_id": "ChIJ...",
      "name": "Monmouth Coffee",
      "address": "27 Monmouth St, London",
      "location": { "lat": 51.514, "lng": -0.127 },
      "rating": 4.7,
      "price_level": 2,
      "types": ["cafe", "food"],
      "open_now": true
    }
  ],
  "next_page_token": "Aap_uEA..."
}
```

Use `next_page_token` as `page_token` in the next request for more results.

---

### `GET /places/{place_id}`

Fetch full details for a specific place (after search).

**Response — additional fields vs search:**

```json
{
  "place_id": "ChIJ...",
  "name": "Monmouth Coffee",
  "address": "27 Monmouth St, London",
  "location": { "lat": 51.514, "lng": -0.127 },
  "rating": 4.7,
  "price_level": 2,
  "types": ["cafe", "food"],
  "open_now": true,
  "phone": "+44 20 7232 3010",
  "website": "https://monmouthcoffee.co.uk",
  "hours": [
    "Monday: 8:00 AM – 6:30 PM",
    "Tuesday: 8:00 AM – 6:30 PM"
  ]
}
```

Details adds `phone`, `website`, and `hours` not available in search results.

---

## Error Handling

| HTTP Code | Meaning | Action |
|-----------|---------|--------|
| `422 Unprocessable Entity` | Validation error — check `detail` field | Fix the invalid parameter (check constraints above) |
| Connection refused | Server not running | Start the server, then retry |
| `200` with empty `results` | No places found | Broaden query, reduce `min_rating`, increase `radius_m`, or remove filters |

**Common validation errors:**

- `types` has more than 1 element → use only one type per search
- `min_rating` not in 0.5 increments (e.g. 4.3 is invalid; use 4.0 or 4.5)
- `price_levels` contains a value outside 0–4
- `radius_m` is 0 or negative

---

## Price Level Guide

| Level | Meaning |
|-------|---------|
| 0 | Free |
| 1 | Inexpensive ($) |
| 2 | Moderate ($$) |
| 3 | Expensive ($$$) |
| 4 | Very expensive ($$$$) |
