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
        db.log_error("kalshi_client", f"GET {path} → {r.status_code}: {r.text[:300]}")
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
        db.log_error("kalshi_client", f"POST {path} → {r.status_code}: {r.text[:300]}")
        return None
    except Exception as e:
        db.log_error("kalshi_client", f"POST {path} exception: {e}")
        return None


# ── Public endpoints ──────────────────────────────────────────────────

def get_markets(limit=200, status="open", series_ticker=None, category=None) -> list:
    params = {"limit": limit, "status": status}
    if series_ticker:
        params["series_ticker"] = series_ticker
    if category:
        params["category"] = category
    data = _get("/markets", params=params)
    if data:
        return data.get("markets", [])
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

def get_implied_prob(ticker: str, side: str = "yes") -> float | None:
    """Returns mid-market implied probability for a side (0.0–1.0)."""
    ob = get_orderbook(ticker)
    if not ob:
        return None
    try:
        yes_bids = ob.get("orderbook", {}).get("yes", [])
        yes_asks = ob.get("orderbook", {}).get("no", [])
        if side == "yes" and yes_bids:
            best_bid = float(yes_bids[0][0]) / 100.0
            return best_bid
        if side == "no" and yes_asks:
            best_ask = float(yes_asks[0][0]) / 100.0
            return 1.0 - best_ask
    except Exception:
        pass
    mkt = get_market(ticker)
    if mkt:
        try:
            lp = mkt.get("last_price") or mkt.get("yes_ask") or "0.5"
            return float(lp)
        except Exception:
            pass
    return None


def get_markets_by_category(category: str) -> list:
    """Fetch open markets filtered by category keyword."""
    all_markets = get_markets(limit=200, status="open")
    category_lower = category.lower()
    return [m for m in all_markets if category_lower in (m.get("category","") or "").lower()
            or category_lower in (m.get("series_ticker","") or "").lower()
            or category_lower in (m.get("title","") or "").lower()]
