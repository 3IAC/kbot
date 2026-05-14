import requests
import math
from bot.config import FRED_API_KEY
import bot.database as db

FRED_BASE = "https://api.stlouisfed.org/fred"


def _get(path: str, params: dict = None) -> dict | None:
    if not FRED_API_KEY:
        return None
    p = {"api_key": FRED_API_KEY, "file_type": "json"}
    if params:
        p.update(params)
    try:
        r = requests.get(FRED_BASE + path, params=p, timeout=15)
        if r.status_code == 200:
            return r.json()
        db.log_error("fred_client", f"GET {path} → {r.status_code}: {r.text[:200]}")
    except Exception as e:
        db.log_error("fred_client", f"GET {path} exception: {e}")
    return None


def get_series_latest(series_id: str) -> float | None:
    data = _get("/series/observations", {
        "series_id": series_id,
        "sort_order": "desc",
        "limit": 1,
    })
    if data:
        obs = data.get("observations", [])
        if obs:
            try:
                val = obs[0].get("value", ".")
                if val != ".":
                    return float(val)
            except Exception:
                pass
    return None


def get_series_history(series_id: str, limit: int = 12) -> list[float]:
    data = _get("/series/observations", {
        "series_id": series_id,
        "sort_order": "desc",
        "limit": limit,
    })
    if data:
        results = []
        for obs in data.get("observations", []):
            try:
                val = obs.get("value", ".")
                if val != ".":
                    results.append(float(val))
            except Exception:
                pass
        return results
    return []


def _trend_direction(history: list[float]) -> float:
    """Returns -1 to 1 trend signal from recent observations (newest first)."""
    if len(history) < 2:
        return 0.0
    recent = history[:3]
    older = history[3:6] if len(history) >= 6 else history[-3:]
    if not older:
        return 0.0
    recent_avg = sum(recent) / len(recent)
    older_avg = sum(older) / len(older)
    if older_avg == 0:
        return 0.0
    change = (recent_avg - older_avg) / abs(older_avg)
    return max(-1.0, min(1.0, change * 10))


def get_cpi() -> dict | None:
    """CPI All Urban Consumers (CPIAUCSL), monthly."""
    history = get_series_history("CPIAUCSL", 12)
    if not history:
        return None
    latest = history[0]
    prev = history[1] if len(history) > 1 else latest
    yoy_change = None
    if len(history) >= 12:
        yoy_change = round(((latest - history[11]) / history[11]) * 100, 2)
    return {
        "series": "CPIAUCSL",
        "name": "CPI (Urban)",
        "latest": latest,
        "prev": prev,
        "mom_change": round(((latest - prev) / prev) * 100, 3) if prev else None,
        "yoy_change": yoy_change,
        "trend": _trend_direction(history),
    }


def get_unemployment() -> dict | None:
    """Unemployment rate (UNRATE), monthly."""
    history = get_series_history("UNRATE", 12)
    if not history:
        return None
    latest = history[0]
    prev = history[1] if len(history) > 1 else latest
    return {
        "series": "UNRATE",
        "name": "Unemployment Rate",
        "latest": latest,
        "prev": prev,
        "mom_change": round(latest - prev, 2),
        "trend": _trend_direction(history),
    }


def get_gdp_growth() -> dict | None:
    """Real GDP growth rate (A191RL1Q225SBEA), quarterly."""
    history = get_series_history("A191RL1Q225SBEA", 8)
    if not history:
        return None
    latest = history[0]
    return {
        "series": "A191RL1Q225SBEA",
        "name": "Real GDP Growth (QoQ %)",
        "latest": latest,
        "trend": _trend_direction(history),
    }


def model_econ_prob(indicator: str, threshold: float, direction: str = "above") -> float | None:
    """
    Simple threshold probability model for economic indicators.
    indicator: 'cpi_yoy', 'unemployment', 'gdp_growth'
    """
    if indicator == "cpi_yoy":
        data = get_cpi()
        val = data["yoy_change"] if data else None
    elif indicator == "unemployment":
        data = get_unemployment()
        val = data["latest"] if data else None
    elif indicator == "gdp_growth":
        data = get_gdp_growth()
        val = data["latest"] if data else None
    else:
        return None

    if val is None:
        return None

    distance = (val - threshold)
    signal = distance / max(abs(threshold) * 0.1, 0.1)
    prob = 1.0 / (1.0 + math.exp(-signal))

    if direction == "below":
        prob = 1.0 - prob

    return round(max(0.05, min(0.95, prob)), 4)
