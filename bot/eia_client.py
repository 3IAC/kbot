import requests
import math
from bot.config import EIA_API_KEY
import bot.database as db

EIA_BASE = "https://api.eia.gov/v2"


def _get(path: str, params: dict = None) -> dict | None:
    if not EIA_API_KEY:
        return None
    p = {"api_key": EIA_API_KEY}
    if params:
        p.update(params)
    try:
        r = requests.get(EIA_BASE + path, params=p, timeout=15)
        if r.status_code == 200:
            return r.json()
        db.log_error("eia_client", f"GET {path} -> {r.status_code}: {r.text[:200]}")
    except Exception as e:
        db.log_error("eia_client", f"GET {path} exception: {e}")
    return None


def get_gas_prices() -> dict | None:
    """
    Weekly US national average retail gasoline price (Regular, all grades).
    EIA series: PET.EMM_EPMR_PTE_NUS_DPG.W
    """
    data = _get("/petroleum/pri/gnd/data/", {
        "frequency": "weekly",
        "data[0]": "value",
        "facets[product][]": "EPM0",
        "facets[area][]": "US",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 8,
    })
    if not data:
        data = _get("/seriesid/PET.EMM_EPMR_PTE_NUS_DPG.W", {"out": "json"})

    if data:
        response = data.get("response") or data
        records = response.get("data", []) if isinstance(response, dict) else []
        if records:
            latest_val = None
            history = []
            for rec in records:
                try:
                    v = float(rec.get("value", 0))
                    history.append(v)
                    if latest_val is None:
                        latest_val = v
                except Exception:
                    pass
            if latest_val:
                prev = history[1] if len(history) > 1 else latest_val
                return {
                    "name": "US Avg Gas Price ($/gal)",
                    "latest": latest_val,
                    "prev": prev,
                    "wow_change": round(latest_val - prev, 3),
                    "trend": _trend_direction(history),
                    "history": history,
                }
    return None


def get_crude_inventory() -> dict | None:
    """
    Weekly US crude oil inventory (thousand barrels).
    """
    data = _get("/petroleum/stoc/wstk/data/", {
        "frequency": "weekly",
        "data[0]": "value",
        "facets[product][]": "EPC0",
        "facets[area][]": "NUS",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 8,
    })
    if data:
        response = data.get("response", {})
        records = response.get("data", [])
        if records:
            history = []
            for rec in records:
                try:
                    history.append(float(rec.get("value", 0)))
                except Exception:
                    pass
            if history:
                latest = history[0]
                prev = history[1] if len(history) > 1 else latest
                return {
                    "name": "US Crude Inventory (Mbbl)",
                    "latest": latest,
                    "prev": prev,
                    "wow_change": round(latest - prev, 0),
                    "trend": _trend_direction(history),
                }
    return None


def _trend_direction(history: list[float]) -> float:
    if len(history) < 2:
        return 0.0
    recent = history[:2]
    older = history[2:5] if len(history) >= 5 else history[-2:]
    if not older:
        return 0.0
    recent_avg = sum(recent) / len(recent)
    older_avg = sum(older) / len(older)
    if older_avg == 0:
        return 0.0
    change = (recent_avg - older_avg) / abs(older_avg)
    return max(-1.0, min(1.0, change * 20))


def model_gas_price_prob(threshold: float, direction: str = "above") -> float | None:
    """Model probability gas price will be above/below threshold."""
    gas = get_gas_prices()
    if not gas:
        return None
    price = gas["latest"]
    trend = gas["trend"]
    distance = (price - threshold) / max(abs(threshold) * 0.05, 0.05)
    signal = distance + trend * 0.5
    prob = 1.0 / (1.0 + math.exp(-signal * 2.0))
    if direction == "below":
        prob = 1.0 - prob
    return round(max(0.05, min(0.95, prob)), 4)


def model_crude_inventory_prob(threshold: float, direction: str = "above") -> float | None:
    """Model probability crude inventory will be above/below threshold."""
    inv = get_crude_inventory()
    if not inv:
        return None
    val = inv["latest"]
    trend = inv["trend"]
    distance = (val - threshold) / max(abs(threshold) * 0.02, 1000)
    signal = distance + trend * 0.3
    prob = 1.0 / (1.0 + math.exp(-signal))
    if direction == "below":
        prob = 1.0 - prob
    return round(max(0.05, min(0.95, prob)), 4)
