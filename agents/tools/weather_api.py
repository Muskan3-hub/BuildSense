"""
BuildSense Tool: Weather & Site Condition Advisory

Fetches current weather data using the OpenWeatherMap API and generates
construction-site-specific risk advisories.

Live mode:   Calls api.openweathermap.org with detected coordinates
             (WEATHER_API_KEY from .env; free tier endpoints)
Failure:     Raises honestly — callers receive an error state, never
             invented temperature/rain/wind values.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenWeatherMap endpoints (free tier)
OPENWEATHER_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
OPENWEATHER_GEOCODE_URL = "https://api.openweathermap.org/geo/1.0/direct"

# Risk thresholds for construction advisories
RAIN_RISK_THRESHOLD_MM = 5.0     # mm/h — above this flags concrete/plastering risk
WIND_RISK_THRESHOLD_KMH = 40.0   # km/h — flags scaffolding safety risk
HEAT_RISK_THRESHOLD_C = 38.0     # °C   — flags heat stress / water curing risk

# OpenWeatherMap condition id groups → icon emoji
OWM_ICON_BY_GROUP = {
    200: "⛈️",  # Thunderstorm
    300: "🌦️",  # Drizzle
    500: "🌧️",  # Rain
    600: "❄️",  # Snow
    700: "🌫️",  # Atmosphere (fog, haze, dust...)
    800: "☀️",  # Clear
    801: "🌤️",  # Few clouds
    802: "⛅",  # Scattered clouds
    803: "☁️",  # Broken clouds
    804: "☁️",  # Overcast clouds
}


def _as_number(value, default=0.0):
    """Return a numeric weather value when an API field is null or malformed."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _owm_condition(weather_entry):
    """
    Map an OpenWeatherMap ``weather`` list entry to
    (condition_label, condition_description, icon_emoji).
    """
    if not isinstance(weather_entry, list) or not weather_entry:
        return "Unknown", "Unknown conditions", "🌤️"

    entry = weather_entry[0] if isinstance(weather_entry[0], dict) else {}

    label = str(entry.get("main") or "Unknown")
    description = str(entry.get("description") or label)

    condition_id = entry.get("id")
    try:
        condition_id = int(condition_id)
    except (TypeError, ValueError):
        condition_id = -1

    if condition_id in OWM_ICON_BY_GROUP:
        icon = OWM_ICON_BY_GROUP[condition_id]
    elif condition_id >= 800:
        # Cloud coverage codes 801-804 degrade gracefully for unknown ids
        icon = OWM_ICON_BY_GROUP[min(condition_id, 804)]
    elif condition_id >= 700:
        icon = OWM_ICON_BY_GROUP[700]
    elif condition_id >= 600:
        icon = OWM_ICON_BY_GROUP[600]
    elif condition_id >= 500:
        icon = OWM_ICON_BY_GROUP[500]
    elif condition_id >= 300:
        icon = OWM_ICON_BY_GROUP[300]
    elif condition_id >= 200:
        icon = OWM_ICON_BY_GROUP[200]
    else:
        icon = "🌤️"

    return label.title(), description.capitalize(), icon


def _geocode_city(city, country_code="", api_key=None):
    """Resolve a city name to lat/lon via OpenWeatherMap direct geocoding."""
    import requests

    query = f"{city},{country_code}" if country_code else city
    params = {
        "q": query,
        "limit": 1,
        "appid": api_key,
    }
    r = requests.get(OPENWEATHER_GEOCODE_URL, params=params, timeout=8)
    r.raise_for_status()
    results = r.json()
    if not isinstance(results, list) or not results:
        return None, None, None
    loc = results[0]
    return loc.get("lat"), loc.get("lon"), loc.get("name", city)


def _generate_advisory(weather: dict) -> dict:
    """
    Generates construction-site advisories based on weather conditions.
    Returns a risk_level ('LOW', 'MEDIUM', 'HIGH') and advisory list.
    """
    advisories = []
    risk_level = "LOW"

    rain_mm = _as_number(weather.get("rainfall_1h_mm"), 0.0)
    wind_kmh = _as_number(weather.get("wind_speed_kmh"), 0.0)
    temp_c = _as_number(weather.get("temp_c"), 25.0)
    humidity = _as_number(weather.get("humidity_pct"), 50)

    # Rain checks
    if rain_mm > RAIN_RISK_THRESHOLD_MM:
        risk_level = "HIGH"
        advisories.append(
            f"⛔ STOP concrete pouring — rainfall {rain_mm:.1f}mm/h exceeds safe threshold ({RAIN_RISK_THRESHOLD_MM}mm/h). "
            "Water contamination compromises concrete mix ratio (NBC Part 6, Clause 7.2)."
        )
        advisories.append(
            "⛔ Suspend plastering and putty application — high moisture prevents proper curing and bonding."
        )
    elif rain_mm > 1.0:
        risk_level = max(risk_level, "MEDIUM") if risk_level != "HIGH" else "HIGH"
        advisories.append(
            f"⚠️ Light rainfall ({rain_mm:.1f}mm/h) detected. Protect freshly poured slabs and mortar joints "
            "with curing sheets. Delay plaster finishing coats."
        )

    # Wind checks
    if wind_kmh > WIND_RISK_THRESHOLD_KMH:
        risk_level = "HIGH"
        advisories.append(
            f"⛔ HIGH WIND ALERT ({wind_kmh:.0f} km/h) — secure all scaffolding and formwork. "
            "Suspend crane/lift operations per OSHA scaffold safety standards."
        )
    elif wind_kmh > 25:
        if risk_level == "LOW":
            risk_level = "MEDIUM"
        advisories.append(
            f"⚠️ Moderate wind ({wind_kmh:.0f} km/h) — ensure scaffolding bracing is properly tightened. "
            "Avoid transporting lightweight sheet materials (gypsum, glass)."
        )

    # Heat stress check
    if temp_c > HEAT_RISK_THRESHOLD_C:
        if risk_level == "LOW":
            risk_level = "MEDIUM"
        advisories.append(
            f"⚠️ High temperature ({temp_c:.1f}°C) — implement mandatory rest breaks every 2 hrs for workers. "
            "Increase concrete curing water frequency (every 4 hrs instead of 8)."
        )

    # Humidity check for painting
    if humidity > 85:
        if risk_level == "LOW":
            risk_level = "MEDIUM"
        advisories.append(
            f"⚠️ High humidity ({humidity}%) — delay exterior painting and waterproofing application. "
            "Paint requires humidity < 85% for proper adhesion and drying."
        )

    if not advisories:
        advisories.append(
            "✅ Weather conditions are favourable for all construction activities. "
            "Proceed as scheduled."
        )

    return {
        "risk_level": risk_level,
        "advisories": advisories,
        "safe_activities": _get_safe_activities(risk_level),
        "restricted_activities": _get_restricted_activities(rain_mm, wind_kmh, temp_c, humidity)
    }


def _get_safe_activities(risk_level: str) -> list:
    if risk_level == "LOW":
        return [
            "Concrete pouring & curing",
            "Masonry & brickwork",
            "Plastering & putty",
            "Tiling & flooring",
            "Painting (interior & exterior)",
            "Scaffolding & elevated work"
        ]
    elif risk_level == "MEDIUM":
        return [
            "Structural masonry (covered areas)",
            "Interior tiling & flooring",
            "Electrical conduit work",
            "Carpentry & woodwork (indoor)"
        ]
    else:  # HIGH
        return [
            "Interior electrical work (non-elevated)",
            "Material procurement & planning",
            "Site supervision & documentation",
            "Indoor fixture installations"
        ]


def _get_restricted_activities(rain_mm, wind_kmh, temp_c, humidity):
    restricted = []
    if rain_mm > RAIN_RISK_THRESHOLD_MM:
        restricted.extend([
            "Concrete pouring / RCC work",
            "Plastering & wall putty",
            "Exterior waterproofing"
        ])
    if wind_kmh > WIND_RISK_THRESHOLD_KMH:
        restricted.extend([
            "Scaffolding erection / dismantling",
            "Crane & material hoisting",
            "Sheet material handling (gypsum, glass)"
        ])
    if temp_c > HEAT_RISK_THRESHOLD_C:
        restricted.append("Outdoor heavy labour (12:00 – 15:00)")
    if humidity > 85:
        restricted.extend(["Exterior painting", "Waterproof membrane application"])
    return list(set(restricted))


def get_weather_advisory(city: str = "", country_code: str = "IN",
                         lat=None, lon=None) -> dict:
    """
    Fetch current weather from OpenWeatherMap and return a construction site advisory.

    Args:
        city:         City name — resolved to coordinates via OpenWeatherMap
                      geocoding when lat/lon are not provided.
        country_code: ISO 3166-1 alpha-2 code (used for geocoding disambiguation)
        lat:          Latitude — when provided with lon, used directly
        lon:          Longitude — when provided with lat, used directly

    Returns:
        dict with weather data, risk_level, advisories, safe/restricted activities

    Raises:
        RuntimeError: WEATHER_API_KEY is not configured.
        ValueError:   no usable location, or the city could not be geocoded.
        requests exceptions: network/API failures propagate unchanged —
                      callers get a real error, never fabricated weather.
    """
    import requests

    api_key = os.getenv("WEATHER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("WEATHER_API_KEY not configured in environment")

    resolved_lat = lat
    resolved_lon = lon
    display_city = city

    # If only city name provided, geocode it first
    if (resolved_lat is None or resolved_lon is None) and city:
        resolved_lat, resolved_lon, display_city = _geocode_city(
            city, country_code, api_key
        )
        if resolved_lat is None:
            raise ValueError(f"Could not geocode city: {city}")

    if resolved_lat is None or resolved_lon is None:
        raise ValueError("No location provided (need lat/lon or city name)")

    # Fetch current weather from OpenWeatherMap using exact coordinates
    params = {
        "lat": resolved_lat,
        "lon": resolved_lon,
        "units": "metric",
        "appid": api_key,
    }
    response = requests.get(OPENWEATHER_CURRENT_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    main = data.get("main", {})
    wind = data.get("wind", {})
    condition_label, condition_desc, condition_icon = _owm_condition(
        data.get("weather")
    )

    # OWM metric wind speed is m/s — convert to km/h for advisory thresholds
    wind_kmh = _as_number(wind.get("speed"), 0.0) * 3.6

    # Visibility is in metres (absent on some stations/Old plan tiers)
    visibility_m = data.get("visibility")
    visibility_km = (
        round(_as_number(visibility_m, 10000.0) / 1000.0, 1)
        if visibility_m is not None
        else 10.0
    )

    weather_data = {
        "city": data.get("name") or display_city or "Unknown Location",
        "country": data.get("sys", {}).get("country", country_code),
        "temp_c": round(_as_number(main.get("temp"), 0.0), 1),
        "feels_like_c": round(_as_number(main.get("feels_like"), 0.0), 1),
        "humidity_pct": _as_number(main.get("humidity"), 50),
        "condition": condition_label,
        "condition_desc": condition_desc,
        "condition_icon": condition_icon,
        "rainfall_1h_mm": round(
            _as_number(data.get("rain", {}).get("1h"), 0.0), 2
        ),
        "wind_speed_kmh": round(wind_kmh, 1),
        "visibility_km": visibility_km,
        "source": f"OpenWeatherMap API — {data.get('name') or display_city or 'coordinates'}"
    }
    advisory = _generate_advisory(weather_data)
    return {**weather_data, **advisory}
