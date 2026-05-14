import requests
import math
import bot.database as db

BINANCE_BASE = "https://api.binance.com/api/v3"


def _get(path: str, params: dict = None) -> dict | list | None:
    try:
        r = requests.get(BINANCE_BASE + path, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        db.log_error("binance_client", f"GET {path} → {r.status_code}: {r.text[:200]}")
    except Exception as e:
        db.log_error("binance_client", f"GET {path} exception: {e}")
    return None


def get_ticker_24h(symbol: str) -> dict | None:
    data = _get("/ticker/24hr", params={"symbol": symbol})
    if isinstance(data, dict):
        return data
    return None


def get_price(symbol: str) -> float | None:
    data = _get("/ticker/price", params={"symbol": symbol})
    if data:
        try:
            return float(data["price"])
        except Exception:
            pass
    return None


def get_klines(symbol: str, interval: str = "1h", limit: int = 24) -> list:
    data = _get("/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
    return data if isinstance(data, list) else []


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def model_threshold_prob(symbol: str, threshold: float, direction: str = "above") -> float | None:
    """
    Model probability that price will be above/below threshold at expiry.
    Uses current price, 24h change, and recent momentum as signal.
    Returns 0.0–1.0.
    """
    ticker = get_ticker_24h(symbol)
    if not ticker:
        return None

    try:
        price = float(ticker["lastPrice"])
        high_24h = float(ticker["highPrice"])
        low_24h = float(ticker["lowPrice"])
        change_pct = float(ticker["priceChangePercent"]) / 100.0
    except Exception:
        return None

    if price <= 0 or threshold <= 0:
        return None

    price_range = high_24h - low_24h
    if price_range <= 0:
        price_range = price * 0.02

    distance = (price - threshold) / price_range
    momentum_boost = change_pct * 2.0
    signal = distance + momentum_boost

    prob = _sigmoid(signal * 2.0)

    if direction == "below":
        prob = 1.0 - prob

    return round(max(0.05, min(0.95, prob)), 4)


def get_btc_summary() -> dict | None:
    ticker = get_ticker_24h("BTCUSDT")
    if not ticker:
        return None
    return {
        "symbol": "BTC",
        "price": float(ticker["lastPrice"]),
        "change_24h_pct": float(ticker["priceChangePercent"]),
        "volume_24h": float(ticker["volume"]),
        "high_24h": float(ticker["highPrice"]),
        "low_24h": float(ticker["lowPrice"]),
    }


def get_eth_summary() -> dict | None:
    ticker = get_ticker_24h("ETHUSDT")
    if not ticker:
        return None
    return {
        "symbol": "ETH",
        "price": float(ticker["lastPrice"]),
        "change_24h_pct": float(ticker["priceChangePercent"]),
        "volume_24h": float(ticker["volume"]),
        "high_24h": float(ticker["highPrice"]),
        "low_24h": float(ticker["lowPrice"]),
    }
