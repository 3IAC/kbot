import requests
import bot.database as db

NOAA_BASE = "https://api.weather.gov"
HEADERS = {"User-Agent": "KBOT/1.0 (itszaya31@gmail.com)", "Accept": "application/geo+json"}

US_CITIES = [
    {"name": "New York", "lat": 40.7128, "lon": -74.0060},
    {"name": "Los Angeles", "lat": 34.0522, "lon": -118.2437},
    {"name": "Chicago", "lat": 41.8781, "lon": -87.6298},
    {"name": "Houston", "lat": 29.7604, "lon": -95.3698},
    {"name": "Phoenix", "lat": 33.4484, "lon": -112.0740},
    {"name": "Philadelphia", "lat": 39.9526, "lon": -75.1652},
    {"name": "San Antonio", "lat": 29.4241, "lon": -98.4936},
    {"name": "San Diego", "lat": 32.7157, "lon": -117.1611},
    {"name": "Dallas", "lat": 32.7767, "lon": -96.7970},
    {"name": "San Jose", "lat": 37.3382, "lon": -121.8863},
    {"name": "Austin", "lat": 30.2672, "lon": -97.7431},
    {"name": "Jacksonville", "lat": 30.3322, "lon": -81.6557},
    {"name": "Fort Worth", "lat": 32.7555, "lon": -97.3308},
    {"name": "Columbus", "lat": 39.9612, "lon": -82.9988},
    {"name": "Charlotte", "lat": 35.2271, "lon": -80.8431},
    {"name": "Indianapolis", "lat": 39.7684, "lon": -86.1581},
    {"name": "San Francisco", "lat": 37.7749, "lon": -122.4194},
    {"name": "Seattle", "lat": 47.6062, "lon": -122.3321},
    {"name": "Denver", "lat": 39.7392, "lon": -104.9903},
    {"name": "Nashville", "lat": 36.1627, "lon": -86.7816},
    {"name": "Oklahoma City", "lat": 35.4676, "lon": -97.5164},
    {"name": "El Paso", "lat": 31.7619, "lon": -106.4850},
    {"name": "Washington DC", "lat": 38.9072, "lon": -77.0369},
    {"name": "Las Vegas", "lat": 36.1699, "lon": -115.1398},
    {"name": "Louisville", "lat": 38.2527, "lon": -85.7585},
    {"name": "Memphis", "lat": 35.1495, "lon": -90.0490},
    {"name": "Portland", "lat": 45.5051, "lon": -122.6750},
    {"name": "Baltimore", "lat": 39.2904, "lon": -76.6122},
    {"name": "Milwaukee", "lat": 43.0389, "lon": -87.9065},
    {"name": "Albuquerque", "lat": 35.0844, "lon": -106.6504},
    {"name": "Tucson", "lat": 32.2226, "lon": -110.9747},
    {"name": "Fresno", "lat": 36.7378, "lon": -119.7871},
    {"name": "Sacramento", "lat": 38.5816, "lon": -121.4944},
    {"name": "Kansas City", "lat": 39.0997, "lon": -94.5786},
    {"name": "Mesa", "lat": 33.4152, "lon": -111.8315},
    {"name": "Atlanta", "lat": 33.7490, "lon": -84.3880},
    {"name": "Omaha", "lat": 41.2565, "lon": -95.9345},
    {"name": "Colorado Springs", "lat": 38.8339, "lon": -104.8214},
    {"name": "Raleigh", "lat": 35.7796, "lon": -78.6382},
    {"name": "Long Beach", "lat": 33.7701, "lon": -118.1937},
    {"name": "Virginia Beach", "lat": 36.8529, "lon": -75.9780},
    {"name": "Minneapolis", "lat": 44.9778, "lon": -93.2650},
    {"name": "Tampa", "lat": 27.9506, "lon": -82.4572},
    {"name": "New Orleans", "lat": 29.9511, "lon": -90.0715},
    {"name": "Cleveland", "lat": 41.4993, "lon": -81.6944},
    {"name": "Bakersfield", "lat": 35.3733, "lon": -119.0187},
    {"name": "Aurora", "lat": 39.7294, "lon": -104.8319},
    {"name": "Anaheim", "lat": 33.8366, "lon": -117.9143},
    {"name": "Honolulu", "lat": 21.3069, "lon": -157.8583},
    {"name": "Anchorage", "lat": 61.2181, "lon": -149.9003},
]

_grid_cache: dict = {}


def _get_grid(lat: float, lon: float) -> dict | None:
    key = f"{lat},{lon}"
    if key in _grid_cache:
        return _grid_cache[key]
    try:
        r = requests.get(f"{NOAA_BASE}/points/{lat},{lon}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            props = r.json().get("properties", {})
            result = {
                "forecast_hourly": props.get("forecastHourly"),
                "office": props.get("cwa"),
            }
            _grid_cache[key] = result
            return result
    except Exception as e:
        db.log_error("noaa_client", f"Grid lookup {key} failed: {e}")
    return None


def get_hourly_forecast(lat: float, lon: float) -> list:
    grid = _get_grid(lat, lon)
    if not grid or not grid.get("forecast_hourly"):
        return []
    try:
        r = requests.get(grid["forecast_hourly"], headers=HEADERS, timeout=10)
        if r.status_code == 200:
            periods = r.json().get("properties", {}).get("periods", [])
            return periods[:24]
    except Exception as e:
        db.log_error("noaa_client", f"Hourly forecast failed: {e}")
    return []


def get_city_weather(city: dict) -> dict | None:
    """Returns weather summary for a city: precip_prob, temp_f, conditions."""
    periods = get_hourly_forecast(city["lat"], city["lon"])
    if not periods:
        return None
    p = periods[0]
    return {
        "city": city["name"],
        "lat": city["lat"],
        "lon": city["lon"],
        "temp_f": p.get("temperature", 0),
        "precip_prob": (p.get("probabilityOfPrecipitation", {}) or {}).get("value", 0) or 0,
        "wind_speed": p.get("windSpeed", "0 mph"),
        "short_forecast": p.get("shortForecast", ""),
        "is_daytime": p.get("isDaytime", True),
    }


def get_rain_prob(city_name: str) -> float | None:
    """Returns 0–1 precipitation probability for a city."""
    city = next((c for c in US_CITIES if c["name"].lower() == city_name.lower()), None)
    if not city:
        return None
    data = get_city_weather(city)
    if data:
        return data["precip_prob"] / 100.0
    return None


def get_all_cities_summary(sample_size: int = 10) -> list:
    """Returns weather data for a sample of cities (rate-limit friendly)."""
    results = []
    for city in US_CITIES[:sample_size]:
        data = get_city_weather(city)
        if data:
            results.append(data)
    return results
