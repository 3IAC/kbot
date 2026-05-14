import re
import time
from datetime import datetime, timezone

import bot.database as db
import bot.kalshi_client as kalshi
import bot.noaa_client as noaa
import bot.binance_client as binance
import bot.fred_client as fred
import bot.eia_client as eia
from bot.risk import check_edge, validate_market


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Weather scanner ───────────────────────────────────────────────────

def _parse_weather_market(market: dict) -> dict | None:
    """Try to extract city name and weather type from a Kalshi market title."""
    title = (market.get("title") or "").lower()
    ticker = (market.get("ticker") or "")

    city_match = None
    for city in noaa.US_CITIES:
        if city["name"].lower() in title:
            city_match = city
            break

    if not city_match:
        for part in ["weather", "rain", "temperature", "precip", "snow", "storm"]:
            if part in title:
                city_match = noaa.US_CITIES[0]
                break

    return {"city": city_match, "title": market.get("title", ""), "ticker": ticker} if city_match else None


def scan_weather() -> list[dict]:
    opportunities = []
    try:
        markets = kalshi.get_markets(limit=200, status="open")
        weather_markets = [
            m for m in markets
            if any(kw in (m.get("title","") + m.get("ticker","")).lower()
                   for kw in ["rain", "precip", "temperature", "weather", "snow", "storm", "high temp", "low temp"])
        ]

        for market in weather_markets[:20]:
            try:
                ok, reason = validate_market(market)
                ticker = market.get("ticker","")
                title = market.get("title","")

                info = _parse_weather_market(market)
                if not info or not info["city"]:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "WEATHER",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": "No city matched", "scanned_at": _now_iso()
                    })
                    continue

                if not ok:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "WEATHER",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": reason, "scanned_at": _now_iso()
                    })
                    continue

                kalshi_implied = kalshi.get_implied_prob(ticker, "yes")
                if kalshi_implied is None:
                    continue

                city = info["city"]
                our_prob = noaa.get_rain_prob(city["name"])
                if our_prob is None:
                    data = noaa.get_city_weather(city)
                    our_prob = data["precip_prob"] / 100.0 if data else None
                if our_prob is None:
                    continue

                edge, qualifies = check_edge(our_prob, kalshi_implied)
                direction = "yes"

                if not qualifies:
                    inverted_edge, inv_qualifies = check_edge(1 - our_prob, 1 - kalshi_implied)
                    if inv_qualifies:
                        edge = inverted_edge
                        qualifies = True
                        direction = "no"
                        our_prob = 1 - our_prob

                status = "opportunity" if qualifies else "scanned"
                reason = "" if qualifies else f"Edge {edge:.1%} below threshold"

                opp = {
                    "market_id": ticker, "title": title, "category": "WEATHER",
                    "direction": direction, "edge_score": edge,
                    "our_prob": round(our_prob, 4), "kalshi_implied": round(kalshi_implied, 4),
                    "status": status, "reason_skipped": reason, "scanned_at": _now_iso(),
                    "open_interest": market.get("open_interest", 0),
                    "close_time": market.get("close_time",""),
                }
                db.log_opportunity(opp)
                if qualifies:
                    opportunities.append(opp)

                time.sleep(0.3)
            except Exception as e:
                db.log_error("scanner.weather", f"Market {market.get('ticker','')} error: {e}")

    except Exception as e:
        db.log_error("scanner.weather", f"Weather scan failed: {e}")

    return opportunities


# ── Crypto scanner ────────────────────────────────────────────────────

def _parse_crypto_threshold(title: str) -> tuple[str, float, str] | None:
    """Extract symbol, threshold price, direction from market title."""
    title_lower = title.lower()
    symbol = None
    if "bitcoin" in title_lower or "btc" in title_lower:
        symbol = "BTCUSDT"
    elif "ethereum" in title_lower or "eth" in title_lower:
        symbol = "ETHUSDT"
    if not symbol:
        return None

    numbers = re.findall(r"[\$]?([\d,]+(?:\.\d+)?)[kK]?", title)
    threshold = None
    for n in numbers:
        try:
            val = float(n.replace(",", ""))
            if "k" in title[title.lower().find(n):title.lower().find(n)+10].lower():
                val *= 1000
            if 1000 < val < 500000:
                threshold = val
                break
        except Exception:
            pass
    if not threshold:
        return None

    direction = "above"
    if any(w in title_lower for w in ["below", "under", "less than", "drop"]):
        direction = "below"

    return symbol, threshold, direction


def scan_crypto() -> list[dict]:
    opportunities = []
    try:
        markets = kalshi.get_markets(limit=200, status="open")
        crypto_markets = [
            m for m in markets
            if any(kw in (m.get("title","") + m.get("ticker","")).lower()
                   for kw in ["bitcoin", "btc", "ethereum", "eth", "crypto"])
        ]

        for market in crypto_markets[:20]:
            try:
                ok, reason = validate_market(market)
                ticker = market.get("ticker","")
                title = market.get("title","")

                parsed = _parse_crypto_threshold(title)
                if not parsed:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "CRYPTO",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": "No threshold parsed", "scanned_at": _now_iso()
                    })
                    continue

                if not ok:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "CRYPTO",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": reason, "scanned_at": _now_iso()
                    })
                    continue

                symbol, threshold, direction = parsed
                our_prob = binance.model_threshold_prob(symbol, threshold, direction)
                if our_prob is None:
                    continue

                kalshi_implied = kalshi.get_implied_prob(ticker, "yes")
                if kalshi_implied is None:
                    continue

                if direction == "no":
                    edge, qualifies = check_edge(our_prob, 1 - kalshi_implied)
                else:
                    edge, qualifies = check_edge(our_prob, kalshi_implied)

                bet_direction = "yes" if direction == "above" else "no"
                status = "opportunity" if qualifies else "scanned"
                skip_reason = "" if qualifies else f"Edge {edge:.1%} below threshold"

                opp = {
                    "market_id": ticker, "title": title, "category": "CRYPTO",
                    "direction": bet_direction, "edge_score": edge,
                    "our_prob": round(our_prob, 4), "kalshi_implied": round(kalshi_implied, 4),
                    "status": status, "reason_skipped": skip_reason, "scanned_at": _now_iso(),
                    "open_interest": market.get("open_interest", 0),
                    "close_time": market.get("close_time",""),
                }
                db.log_opportunity(opp)
                if qualifies:
                    opportunities.append(opp)

                time.sleep(0.2)
            except Exception as e:
                db.log_error("scanner.crypto", f"Market {market.get('ticker','')} error: {e}")

    except Exception as e:
        db.log_error("scanner.crypto", f"Crypto scan failed: {e}")

    return opportunities


# ── Economic scanner ──────────────────────────────────────────────────

_ECON_KEYWORDS = ["cpi", "inflation", "unemployment", "gdp", "federal funds", "fed rate", "jobs", "payroll"]
_ECON_SERIES_MAP = {
    "cpi": ("cpi_yoy", None),
    "inflation": ("cpi_yoy", None),
    "unemployment": ("unemployment", None),
    "jobs": ("unemployment", None),
    "gdp": ("gdp_growth", None),
}


def _parse_econ_market(title: str) -> tuple[str, float, str] | None:
    title_lower = title.lower()
    indicator = None
    for kw, (series, _) in _ECON_SERIES_MAP.items():
        if kw in title_lower:
            indicator = series
            break
    if not indicator:
        return None

    numbers = re.findall(r"([\d]+\.?[\d]*)\s*%?", title)
    threshold = None
    for n in numbers:
        try:
            val = float(n)
            if 0 < val < 100:
                threshold = val
                break
        except Exception:
            pass
    if not threshold:
        return None

    direction = "above"
    if any(w in title_lower for w in ["below", "under", "less than", "fall"]):
        direction = "below"

    return indicator, threshold, direction


def scan_economic() -> list[dict]:
    opportunities = []
    try:
        markets = kalshi.get_markets(limit=200, status="open")
        econ_markets = [
            m for m in markets
            if any(kw in (m.get("title","") + m.get("ticker","")).lower()
                   for kw in _ECON_KEYWORDS)
        ]

        for market in econ_markets[:20]:
            try:
                ok, reason = validate_market(market)
                ticker = market.get("ticker","")
                title = market.get("title","")

                parsed = _parse_econ_market(title)
                if not parsed:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "ECONOMIC",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": "No indicator/threshold parsed", "scanned_at": _now_iso()
                    })
                    continue

                if not ok:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "ECONOMIC",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": reason, "scanned_at": _now_iso()
                    })
                    continue

                indicator, threshold, direction = parsed
                our_prob = fred.model_econ_prob(indicator, threshold, direction)
                if our_prob is None:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "ECONOMIC",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": "FRED data unavailable (check FRED_API_KEY)", "scanned_at": _now_iso()
                    })
                    continue

                kalshi_implied = kalshi.get_implied_prob(ticker, "yes")
                if kalshi_implied is None:
                    continue

                edge, qualifies = check_edge(our_prob, kalshi_implied)
                status = "opportunity" if qualifies else "scanned"
                skip_reason = "" if qualifies else f"Edge {edge:.1%} below threshold"

                opp = {
                    "market_id": ticker, "title": title, "category": "ECONOMIC",
                    "direction": "yes", "edge_score": edge,
                    "our_prob": round(our_prob, 4), "kalshi_implied": round(kalshi_implied, 4),
                    "status": status, "reason_skipped": skip_reason, "scanned_at": _now_iso(),
                    "open_interest": market.get("open_interest", 0),
                    "close_time": market.get("close_time",""),
                }
                db.log_opportunity(opp)
                if qualifies:
                    opportunities.append(opp)

                time.sleep(0.2)
            except Exception as e:
                db.log_error("scanner.economic", f"Market {market.get('ticker','')} error: {e}")

    except Exception as e:
        db.log_error("scanner.economic", f"Economic scan failed: {e}")

    return opportunities


# ── Energy scanner ────────────────────────────────────────────────────

_ENERGY_KEYWORDS = ["gas price", "gasoline", "crude oil", "petroleum", "energy", "oil inventory", "barrel"]


def _parse_energy_market(title: str) -> tuple[str, float, str] | None:
    title_lower = title.lower()
    if any(kw in title_lower for kw in ["gas", "gasoline"]):
        indicator = "gas_price"
    elif any(kw in title_lower for kw in ["crude", "oil", "barrel", "inventory"]):
        indicator = "crude_inventory"
    else:
        return None

    numbers = re.findall(r"\$?([\d]+\.?[\d]*)", title)
    threshold = None
    for n in numbers:
        try:
            val = float(n)
            if indicator == "gas_price" and 1 < val < 20:
                threshold = val
                break
            elif indicator == "crude_inventory" and val > 100:
                threshold = val
                break
        except Exception:
            pass
    if not threshold:
        return None

    direction = "above"
    if any(w in title_lower for w in ["below", "under", "less than", "drop", "fall"]):
        direction = "below"

    return indicator, threshold, direction


def scan_energy() -> list[dict]:
    opportunities = []
    try:
        markets = kalshi.get_markets(limit=200, status="open")
        energy_markets = [
            m for m in markets
            if any(kw in (m.get("title","") + m.get("ticker","")).lower()
                   for kw in _ENERGY_KEYWORDS)
        ]

        for market in energy_markets[:20]:
            try:
                ok, reason = validate_market(market)
                ticker = market.get("ticker","")
                title = market.get("title","")

                parsed = _parse_energy_market(title)
                if not parsed:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "ENERGY",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": "No energy threshold parsed", "scanned_at": _now_iso()
                    })
                    continue

                if not ok:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "ENERGY",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": reason, "scanned_at": _now_iso()
                    })
                    continue

                indicator, threshold, direction = parsed
                if indicator == "gas_price":
                    our_prob = eia.model_gas_price_prob(threshold, direction)
                else:
                    our_prob = eia.model_crude_inventory_prob(threshold, direction)

                if our_prob is None:
                    db.log_opportunity({
                        "market_id": ticker, "title": title, "category": "ENERGY",
                        "direction": "yes", "status": "skipped",
                        "reason_skipped": "EIA data unavailable (check EIA_API_KEY)", "scanned_at": _now_iso()
                    })
                    continue

                kalshi_implied = kalshi.get_implied_prob(ticker, "yes")
                if kalshi_implied is None:
                    continue

                edge, qualifies = check_edge(our_prob, kalshi_implied)
                status = "opportunity" if qualifies else "scanned"
                skip_reason = "" if qualifies else f"Edge {edge:.1%} below threshold"

                opp = {
                    "market_id": ticker, "title": title, "category": "ENERGY",
                    "direction": "yes", "edge_score": edge,
                    "our_prob": round(our_prob, 4), "kalshi_implied": round(kalshi_implied, 4),
                    "status": status, "reason_skipped": skip_reason, "scanned_at": _now_iso(),
                    "open_interest": market.get("open_interest", 0),
                    "close_time": market.get("close_time",""),
                }
                db.log_opportunity(opp)
                if qualifies:
                    opportunities.append(opp)

                time.sleep(0.2)
            except Exception as e:
                db.log_error("scanner.energy", f"Market {market.get('ticker','')} error: {e}")

    except Exception as e:
        db.log_error("scanner.energy", f"Energy scan failed: {e}")

    return opportunities


# ── Main scan ─────────────────────────────────────────────────────────

def run_full_scan() -> list[dict]:
    """Run all 4 category scans. Returns list of actionable opportunities."""
    print(f"[SCANNER] Starting full scan at {_now_iso()}")
    all_opportunities = []

    for label, fn in [
        ("WEATHER", scan_weather),
        ("CRYPTO", scan_crypto),
        ("ECONOMIC", scan_economic),
        ("ENERGY", scan_energy),
    ]:
        try:
            opps = fn()
            print(f"[SCANNER] {label}: {len(opps)} opportunities found")
            all_opportunities.extend(opps)
        except Exception as e:
            db.log_error("scanner", f"{label} scan error: {e}")

    all_opportunities.sort(key=lambda x: x.get("edge_score", 0), reverse=True)
    print(f"[SCANNER] Scan complete — {len(all_opportunities)} total opportunities")
    return all_opportunities
