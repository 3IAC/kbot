import base64
import time
import json
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from bot.config import KALSHI_BASE_URL, KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH
import bot.database as db

_private_key = None


def _load_key():
    global _private_key
    if _private_key is not None:
        return _private_key
    try:
        with open(KALSHI_PRIVATE_KEY_PATH, "rb") as f:
            _private_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
        return _private_key
    except Exception as e:
        db.log_error("kalshi_client", f"Failed to load private key: {e}")
        return None


def _sign(method: str, path: str) -> dict:
    key = _load_key()
    if key is None:
        return {}
    ts = str(int(time.time() * 1000))
    msg = (ts + method.upper() + path).encode("utf-8")
    sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
    sig_b64 = base64.b64encode(sig).decode("utf-8")
    return {
        "KALSHI-ACCESS-KEY": KALSHI_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "Content-Type": "application/json",
    }


def _get(path: str, params: dict = None) -> dict | None:
    headers = _sign("GET", path)
    if not headers:
        return None
    try:
        r = requests.get(KALSHI_BASE_URL + path, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        db.log_error("kalshi_client", f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return None
    except Exception as e:
        db.log_error("kalshi_client", f"GET {path} exception: {e}")
        return None


def _post(path: str, body: dict) -> dict | None:
    headers = _sign("POST", path)
    if not headers:
        return None
    try:
        r = requests.post(KALSHI_BASE_URL + path, headers=headers, json=body, timeout=15)
        if r.status_code in (200, 201):
            return r.json()
        db.log_error("kalshi_client", f"POST {path} -> {r.status_code}: {r.text[:300]}")
        return None
    except Exception as e:
        db.log_error("kalshi_client", f"POST {path} exception: {e}")
        return None


# ── Public endpoints ──────────────────────────────────────────────────

def get_markets(limit=200, status="open", series_ticker=None, category=None,
                cursor=None) -> list:
    params = {"limit": limit, "status": status}
    if series_ticker:
        params["series_ticker"] = series_ticker
    if category:
        params["category"] = category
    if cursor:
        params["cursor"] = cursor
    data = _get("/markets", params=params)
    if data:
        return data.get("markets", [])
    return []


def get_markets_paged(series_ticker: str, limit_per_page=200) -> list:
    """Fetch ALL pages for a series_ticker. Returns flat list."""
    all_markets = []
    cursor = None
    while True:
        params = {"limit": limit_per_page, "series_ticker": series_ticker, "status": "open"}
        if cursor:
            params["cursor"] = cursor
        data = _get("/markets", params=params)
        if not data:
            break
        markets = data.get("markets", [])
        all_markets.extend(markets)
        cursor = data.get("cursor", "")
        if not cursor or not markets:
            break
        time.sleep(0.3)
    return all_markets


def get_events(limit=100, status="open", with_nested_markets=False) -> list:
    params = {"limit": limit, "status": status}
    if with_nested_markets:
        params["with_nested_markets"] = "true"
    data = _get("/events", params=params)
    if data:
        return data.get("events", [])
    return []


def get_market(ticker: str) -> dict | None:
    data = _get(f"/markets/{ticker}")
    return data.get("market") if data else None


def get_orderbook(ticker: str) -> dict | None:
    data = _get(f"/markets/{ticker}/orderbook")
    return data if data else None


def get_balance() -> float:
    data = _get("/portfolio/balance")
    if data:
        bal_str = data.get("balance", "0")
        try:
            return float(bal_str)
        except Exception:
            return 0.0
    return 0.0


def get_positions() -> list:
    data = _get("/portfolio/positions")
    if data:
        return data.get("market_positions", [])
    return []


def place_order(ticker: str, side: str, count: int, price: float, order_type="limit") -> dict | None:
    body = {
        "ticker": ticker,
        "action": "buy",
        "side": side,
        "type": order_type,
        "count_fp": f"{count:.2f}",
        "yes_price": f"{price:.4f}" if side == "yes" else None,
        "no_price": f"{price:.4f}" if side == "no" else None,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return _post("/portfolio/orders", body)


# ── Derived helpers ───────────────────────────────────────────────────

def market_open_interest(market: dict) -> float:
    """Get open interest from market dict. Handles both legacy int and new _fp string."""
    # New API: open_interest_fp is a string like "31153.95"
    oi_fp = market.get("open_interest_fp")
    if oi_fp is not None:
        try:
            return float(oi_fp)
        except Exception:
            pass
    # Legacy fallback
    oi = market.get("open_interest", 0) or 0
    return float(oi)


def market_mid_price(market: dict) -> float | None:
    """
    Return the mid-market price (0.0–1.0) for the YES side, using the new
    *_dollars fields from the March 2026 API format.
    Returns None if no quotes are available.
    """
    bid_str = market.get("yes_bid_dollars", "") or ""
    ask_str = market.get("yes_ask_dollars", "") or ""

    bid, ask = None, None
    try:
        if bid_str:
            bid = float(bid_str)
    except Exception:
        pass
    try:
        if ask_str:
            ask = float(ask_str)
    except Exception:
        pass

    if bid is not None and ask is not None and ask > 0:
        return (bid + ask) / 2.0
    if bid is not None and bid > 0:
        return bid
    if ask is not None and ask > 0:
        return ask

    # Last-price fallback
    lp_str = market.get("last_price_dollars", "") or ""
    try:
        lp = float(lp_str)
        if lp > 0:
            return lp
    except Exception:
        pass

    return None


def market_has_quotes(market: dict) -> bool:
    """True if market has actual bid or ask quotes."""
    for field in ("yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars"):
        val = market.get(field, "") or ""
        try:
            if float(val) > 0:
                return True
        except Exception:
            pass
    return False


def get_implied_prob(ticker: str, side: str = "yes") -> float | None:
    """
    Returns mid-market implied probability for the given side (0.0-1.0).
    First tries market data (fast), then falls back to orderbook (slow).
    """
    # Fast path: fetch market directly for fresh bid/ask
    mkt = get_market(ticker)
    if mkt:
        mid = market_mid_price(mkt)
        if mid is not None:
            return mid if side == "yes" else 1.0 - mid

    # Slow path: parse orderbook_fp (new format)
    ob = get_orderbook(ticker)
    if ob:
        try:
            ob_fp = ob.get("orderbook_fp", {})
            if side == "yes":
                yes_levels = ob_fp.get("yes_dollars", [])
                if yes_levels:
                    best_bid = float(yes_levels[0][0])
                    return best_bid
            else:
                no_levels = ob_fp.get("no_dollars", [])
                if no_levels:
                    best_bid = float(no_levels[0][0])
                    return best_bid
        except Exception:
            pass

    return None


def get_markets_by_category(category: str) -> list:
    """Fetch open markets filtered by category keyword in title/ticker."""
    all_markets = get_markets(limit=200, status="open")
    category_lower = category.lower()
    return [m for m in all_markets if category_lower in (m.get("category", "") or "").lower()
            or category_lower in (m.get("series_ticker", "") or "").lower()
            or category_lower in (m.get("title", "") or "").lower()]
