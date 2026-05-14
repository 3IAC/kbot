import requests
import math
import bot.database as db

# Binance US for US-based users; Binance global as secondary; CoinGecko as final fallback
BINANCE_US_BASE = "https://api.binance.us/api/v3"
BINANCE_GLOBAL_BASE = "https://api.binance.com/api/v3"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

_working_base = None  # cached after first successful call


def _detect_base() -> str:
    global _working_base
    if _working_base:
        return _working_base
    for base in [BINANCE_US_BASE, BINANCE_GLOBAL_BASE]:
        try:
            r = requests.get(f"{base}/ping", timeout=5)
            if r.status_code == 200:
                _working_base = base
                return base
        except Exception:
            pass
    _working_base = None  # will use CoinGecko path
    return None


def _get_binance(path: str, params: dict = None) -> dict | list | None:
    base = _detect_base()
    if not base:
        return None
    try:
        r = requests.get(base + path, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        db.log_error("binance_client", f"GET {path} -> {r.status_code}: {r.text[:200]}")
        if r.status_code in (451, 403):
            global _working_base
            _working_base = None  # reset so next call re-detects
    except Exception as e:
        db.log_error("binance_client", f"GET {path} exception: {e}")
    return None


def _get_coingecko(coin_id: str) -> dict | None:
    """CoinGecko simple price endpoint — no key needed, works globally."""
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd",
                    "include_24hr_change": "true", "include_24hr_vol": "true"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get(coin_id)
        db.log_error("binance_client", f"CoinGecko {coin_id} -> {r.status_code}")
    except Exception as e:
        db.log_error("binance_client", f"CoinGecko exception: {e}")
    return None


def get_ticker_24h(symbol: str) -> dict | None:
    data = _get_binance("/ticker/24hr", params={"symbol": symbol})
    if isinstance(data, dict):
        return data
    return None


def get_price(symbol: str) -> float | None:
    data = _get_binance("/ticker/price", params={"symbol": symbol})
    if data:
        try:
            return float(data["price"])
        except Exception:
            pass
    return None


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def model_threshold_prob(symbol: str, threshold: float, direction: str = "above") -> float | None:
    """
    Model probability that price will be above/below threshold at expiry.
    Uses Binance US or CoinGecko as data source.
    Returns 0.0–1.0.
    """
    # Try Binance first
    ticker = get_ticker_24h(symbol)
    if ticker:
        try:
            price = float(ticker["lastPrice"])
            high_24h = float(ticker["highPrice"])
            low_24h = float(ticker["lowPrice"])
            change_pct = float(ticker["priceChangePercent"]) / 100.0
        except Exception:
            ticker = None

    # Fall back to CoinGecko
    if not ticker:
        coin_id = "bitcoin" if "BTC" in symbol else "ethereum"
        cg = _get_coingecko(coin_id)
        if not cg:
            return None
        price = cg.get("usd", 0)
        change_pct = (cg.get("usd_24h_change") or 0) / 100.0
        high_24h = price * (1 + abs(change_pct))
        low_24h = price * (1 - abs(change_pct))

    if not price or price <= 0 or threshold <= 0:
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
    if ticker:
        return {
            "symbol": "BTC",
            "source": "binance",
            "price": float(ticker["lastPrice"]),
            "change_24h_pct": float(ticker["priceChangePercent"]),
            "volume_24h": float(ticker["volume"]),
            "high_24h": float(ticker["highPrice"]),
            "low_24h": float(ticker["lowPrice"]),
        }
    # CoinGecko fallback
    cg = _get_coingecko("bitcoin")
    if cg:
        price = cg.get("usd", 0)
        chg = cg.get("usd_24h_change") or 0
        return {
            "symbol": "BTC",
            "source": "coingecko",
            "price": price,
            "change_24h_pct": chg,
            "volume_24h": cg.get("usd_24h_vol") or 0,
            "high_24h": price * 1.02,
            "low_24h": price * 0.98,
        }
    return None


def get_eth_summary() -> dict | None:
    ticker = get_ticker_24h("ETHUSDT")
    if ticker:
        return {
            "symbol": "ETH",
            "source": "binance",
            "price": float(ticker["lastPrice"]),
            "change_24h_pct": float(ticker["priceChangePercent"]),
            "volume_24h": float(ticker["volume"]),
            "high_24h": float(ticker["highPrice"]),
            "low_24h": float(ticker["lowPrice"]),
        }
    cg = _get_coingecko("ethereum")
    if cg:
        price = cg.get("usd", 0)
        chg = cg.get("usd_24h_change") or 0
        return {
            "symbol": "ETH",
            "source": "coingecko",
            "price": price,
            "change_24h_pct": chg,
            "volume_24h": cg.get("usd_24h_vol") or 0,
            "high_24h": price * 1.02,
            "low_24h": price * 0.98,
        }
    return None
