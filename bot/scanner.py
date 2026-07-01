"""
scanner.py — Kalshi market scanner using series-ticker targeting.

Instead of keyword-matching market titles (which returns sports parlays),
we directly query known financial/economic series and parse thresholds from
the ticker format: KXBTC-26MAY1700-T89799.99  =>  threshold = 89799.99, above
                   KXBTC-26MAY1700-B89750      =>  threshold = 89750, bracket/band
"""
import re
import math
import time
from datetime import datetime, timezone

import bot.database as db
import bot.kalshi_client as kalshi
import bot.binance_client as binance
import bot.fred_client as fred
import bot.eia_client as eia
from bot.risk import check_edge, validate_market
from bot.config import MIN_OPEN_INTEREST, MIN_HOURS_TO_EXPIRY, MAX_HOURS_TO_EXPIRY


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Ticker threshold parser ───────────────────────────────────────────

def _parse_ticker_threshold(ticker: str) -> tuple[float, str] | None:
    """
    Parse threshold and direction from Kalshi ticker suffix.
      -T89799.99  => (89799.99, "above")   "above threshold"
      -T-0.3      => (-0.3,     "above")   "above -0.3 (i.e. less negative)"
      -B89750     => (89750,    "band")    bracket/range market — skip for now
    Returns (threshold, direction) or None if unparseable.
    """
    # Match -T followed by optional minus and digits
    m = re.search(r'-T(-?[\d]+\.?[\d]*)$', ticker)
    if m:
        try:
            return float(m.group(1)), "above"
        except Exception:
            pass
    # Band market (B prefix = between two prices) — skip
    m = re.search(r'-B(-?[\d]+\.?[\d]*)$', ticker)
    if m:
        return None  # band markets not yet supported
    return None


def _log_skip(ticker, title, category, reason):
    db.log_opportunity({
        "market_id": ticker, "title": title, "category": category,
        "direction": "yes", "status": "skipped",
        "reason_skipped": reason, "scanned_at": _now_iso(),
    })


def _log_scanned(ticker, title, category, direction, edge, our_prob,
                 kalshi_implied, oi, close_time, qualifies):
    status = "opportunity" if qualifies else "scanned"
    reason = "" if qualifies else f"Edge {edge:.1%} below threshold"
    return {
        "market_id": ticker, "title": title, "category": category,
        "direction": direction, "edge_score": edge,
        "our_prob": round(our_prob, 4),
        "kalshi_implied": round(kalshi_implied, 4),
        "status": status, "reason_skipped": reason,
        "scanned_at": _now_iso(),
        "open_interest": oi,
        "close_time": close_time,
    }


def _process_market(market: dict, category: str, our_prob_fn) -> dict | None:
    """
    Generic market processor. our_prob_fn(threshold, direction) -> float|None.
    Returns opportunity dict if qualifies, else None.
    Logs everything to DB regardless.
    """
    ticker = market.get("ticker", "")
    title = market.get("title", "") or ticker
    close_time = market.get("close_time", "")
    oi = kalshi.market_open_interest(market)

    # Validate basic market criteria
    ok, reason = validate_market(market)
    if not ok:
        print(f"[SCANNER] SKIP {ticker[:40]}: {reason}")
        _log_skip(ticker, title, category, reason)
        return None

    # Enforce expiry window: must be between MIN and MAX hours from now
    if close_time:
        try:
            close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            hours_left = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_left < MIN_HOURS_TO_EXPIRY:
                reason = f"Expires too soon ({hours_left:.1f}h < {MIN_HOURS_TO_EXPIRY}h min)"
                print(f"[SCANNER] SKIP {ticker}: {reason}")
                _log_skip(ticker, title, category, reason)
                return None
            if hours_left > MAX_HOURS_TO_EXPIRY:
                reason = f"Expires too far out ({hours_left:.1f}h > {MAX_HOURS_TO_EXPIRY}h max)"
                _log_skip(ticker, title, category, reason)
                return None
        except Exception:
            pass

    # Need actual bid/ask to compute implied prob
    if not kalshi.market_has_quotes(market):
        _log_skip(ticker, title, category, "No bid/ask quotes — illiquid")
        return None

    # Parse threshold from ticker
    parsed = _parse_ticker_threshold(ticker)
    if not parsed:
        print(f"[SCANNER] SKIP {ticker[:40]}: non-standard ticker format")
        _log_skip(ticker, title, category, "Non-standard ticker format (band/unknown)")
        return None
    threshold, direction = parsed

    # Compute our model probability
    our_prob = our_prob_fn(threshold, direction)
    if our_prob is None:
        _log_skip(ticker, title, category, "Model data unavailable")
        return None

    # Get market-implied probability
    kalshi_implied = kalshi.market_mid_price(market)
    if kalshi_implied is None:
        _log_skip(ticker, title, category, "Cannot compute market implied prob")
        return None

    # Check edge
    edge, qualifies = check_edge(our_prob, kalshi_implied)

    # Also try the inverse: bet NO if we think it won't happen
    if not qualifies:
        inv_edge, inv_q = check_edge(1 - our_prob, 1 - kalshi_implied)
        if inv_q:
            edge, qualifies = inv_edge, True
            our_prob = 1 - our_prob
            direction = "no"
        else:
            direction = "yes"
            print(f"[KBOT] EDGE_FAIL {ticker[:40]}: our={our_prob:.2%} kalshi={kalshi_implied:.2%} edge={edge:.2%}")

    bet_dir = "yes" if direction != "no" else "no"
    opp = _log_scanned(ticker, title, category, bet_dir, edge, our_prob,
                       kalshi_implied, oi, close_time, qualifies)
    db.log_opportunity(opp)
    return opp if qualifies else None


# ── Probability models ────────────────────────────────────────────────

def _crypto_prob_btc(threshold: float, direction: str) -> float | None:
    """
    Estimate P(BTC > threshold at expiry) using current price and a
    simple log-normal model with 5% annualised daily vol (short-dated).
    """
    price = binance.get_price("BTCUSDT")
    if price is None:
        return None
    return _price_above_prob(price, threshold, direction, daily_vol_pct=0.04)


def _crypto_prob_eth(threshold: float, direction: str) -> float | None:
    price = binance.get_price("ETHUSDT")
    if price is None:
        return None
    return _price_above_prob(price, threshold, direction, daily_vol_pct=0.055)


def _price_above_prob(current: float, threshold: float, direction: str,
                      daily_vol_pct: float = 0.04) -> float:
    """
    P(price > threshold) assuming log-normal price with given daily vol.
    Uses complementary CDF of the standard normal.
    For very short-dated markets (< 3 days), assume ~current price.
    """
    if current <= 0 or threshold <= 0:
        return 0.5
    log_ratio = math.log(threshold / current)
    # Sigma ~ daily_vol; assume 1-day horizon
    sigma = daily_vol_pct
    # z-score: how many sigmas above current is the threshold?
    z = log_ratio / sigma  # positive = threshold above current
    # P(price > threshold) = P(Z < -z) using standard normal CDF approx
    prob_above = _norm_cdf(-z)
    if direction == "above":
        return max(0.01, min(0.99, prob_above))
    else:  # below
        return max(0.01, min(0.99, 1.0 - prob_above))


def _norm_cdf(x: float) -> float:
    """Approximation of the standard normal CDF."""
    # Abramowitz & Stegun approximation
    if x < 0:
        return 1.0 - _norm_cdf(-x)
    k = 1.0 / (1.0 + 0.2316419 * x)
    poly = k * (0.319381530 + k * (-0.356563782 + k * (1.781477937 +
                k * (-1.821255978 + k * 1.330274429))))
    return 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly


def _cpi_prob(threshold: float, direction: str) -> float | None:
    """
    Compare FRED CPI month-over-month change to KXCPI threshold.
    KXCPI threshold is the monthly % change (e.g., 0.3 means +0.3% MoM).
    Uses cpi_mom directly; falls back to cpi_yoy / 12 if needed.
    """
    data = fred.get_economic_data()
    if not data:
        return None
    # Prefer direct MoM figure
    cpi_mom = data.get("cpi_mom")
    if cpi_mom is None:
        cpi_yoy = data.get("cpi_yoy")
        if cpi_yoy is None:
            return None
        cpi_mom = cpi_yoy / 12.0
    # Uncertainty: ±0.15pp (monthly CPI is noisy)
    return _value_above_prob(cpi_mom, threshold, direction, uncertainty=0.15)


def _fed_prob(threshold: float, direction: str) -> float | None:
    """
    KXFED threshold is fed funds rate upper bound (e.g., 4.25).
    Use FRED fed rate data.
    """
    data = fred.get_economic_data()
    if not data:
        return None
    fed_rate = data.get("fed_rate")
    if fed_rate is None:
        return None
    return _value_above_prob(fed_rate, threshold, direction, uncertainty=0.25)


def _gdp_prob(threshold: float, direction: str) -> float | None:
    """
    KXGDP threshold is quarterly GDP growth % (e.g., 4.0 = 4.0% annualized).
    Use FRED GDP data.
    """
    data = fred.get_economic_data()
    if not data:
        return None
    gdp = data.get("gdp_growth")
    if gdp is None:
        return None
    return _value_above_prob(gdp, threshold, direction, uncertainty=1.0)


def _wti_prob(threshold: float, direction: str) -> float | None:
    """
    KXWTI threshold is WTI crude oil price in USD/bbl.
    Fetches current WTI spot price from EIA and builds a log-normal model.
    """
    wti = eia.get_wti_price()
    if wti and wti > 0:
        return _price_above_prob(wti, threshold, direction, daily_vol_pct=0.025)
    return None


def _value_above_prob(current: float, threshold: float, direction: str,
                      uncertainty: float = 0.5) -> float:
    """
    P(value > threshold) using a logistic function around the current value.
    uncertainty = 1-sigma uncertainty in the current value.
    """
    if uncertainty <= 0:
        return 1.0 if current > threshold else 0.0
    z = (current - threshold) / uncertainty
    # Logistic function as CDF approximation
    prob = 1.0 / (1.0 + math.exp(-z * 1.7))  # 1.7 scales logistic to ~normal
    if direction == "above":
        return max(0.01, min(0.99, prob))
    else:
        return max(0.01, min(0.99, 1.0 - prob))


# ── Category scanners ─────────────────────────────────────────────────

def scan_crypto() -> list[dict]:
    """Scan KXBTC and KXETH series for price-range opportunities."""
    opportunities = []
    print("[SCANNER] Crypto: fetching KXBTC / KXETH markets...")

    series_map = {
        "KXBTC": _crypto_prob_btc,
        "KXETH": _crypto_prob_eth,
    }

    for series, prob_fn in series_map.items():
        try:
            markets = kalshi.get_markets(limit=200, series_ticker=series, status="open")
            print(f"[SCANNER] {series}: {len(markets)} markets")
            liquid = [m for m in markets if kalshi.market_has_quotes(m)]
            print(f"[SCANNER] {series}: {len(liquid)} with bid/ask quotes ({MIN_HOURS_TO_EXPIRY}h-{MAX_HOURS_TO_EXPIRY}h expiry window)")
            for market in liquid[:50]:
                opp = _process_market(market, "CRYPTO", prob_fn)
                if opp:
                    opportunities.append(opp)
                time.sleep(0.3)
            print(f"[SCANNER] {series}: {len(opportunities)} opportunities so far")
        except Exception as e:
            db.log_error("scanner.crypto", f"{series} error: {e}")
        time.sleep(0.5)

    return opportunities


def scan_economic() -> list[dict]:
    """Scan KXCPI, KXFED, KXGDP for economic threshold opportunities."""
    opportunities = []
    print("[SCANNER] Economic: fetching KXCPI / KXFED / KXGDP markets...")

    series_map = {
        "KXCPI": _cpi_prob,
        "KXFED": _fed_prob,
        "KXGDP": _gdp_prob,
    }

    for series, prob_fn in series_map.items():
        try:
            markets = kalshi.get_markets(limit=100, series_ticker=series, status="open")
            print(f"[SCANNER] {series}: {len(markets)} markets")
            liquid = [m for m in markets if kalshi.market_has_quotes(m)]
            print(f"[SCANNER] {series}: {len(liquid)} with bid/ask quotes")
            for market in liquid[:20]:
                opp = _process_market(market, "ECONOMIC", prob_fn)
                if opp:
                    opportunities.append(opp)
                time.sleep(0.3)
        except Exception as e:
            db.log_error("scanner.economic", f"{series} error: {e}")
        time.sleep(0.5)

    return opportunities


def scan_energy() -> list[dict]:
    """Scan KXWTI crude oil markets."""
    opportunities = []
    print("[SCANNER] Energy: fetching KXWTI markets...")

    try:
        markets = kalshi.get_markets(limit=100, series_ticker="KXWTI", status="open")
        print(f"[SCANNER] KXWTI: {len(markets)} markets")
        liquid = [m for m in markets if kalshi.market_has_quotes(m)]
        print(f"[SCANNER] KXWTI: {len(liquid)} with bid/ask quotes")
        for market in liquid[:20]:
            opp = _process_market(market, "ENERGY", _wti_prob)
            if opp:
                opportunities.append(opp)
            time.sleep(0.3)
    except Exception as e:
        db.log_error("scanner.energy", f"KXWTI error: {e}")

    return opportunities


def scan_weather() -> list[dict]:
    """
    Weather markets (NOAA-based). Currently no active Kalshi weather series
    with near-term price ranges was found. Scan returns 0 until a valid series
    appears. Extend series list below as Kalshi adds new weather markets.
    """
    opportunities = []
    WEATHER_SERIES = []  # Add e.g. "KXTEMP", "KXRAIN" when Kalshi activates them
    print(f"[SCANNER] Weather: {len(WEATHER_SERIES)} active series configured")

    for series in WEATHER_SERIES:
        try:
            markets = kalshi.get_markets(limit=50, series_ticker=series, status="open")
            liquid = [m for m in markets if kalshi.market_has_quotes(m)]
            for market in liquid[:10]:
                # Simple model: 50/50 with slight skew toward NOAA precip forecast
                def _weather_prob(threshold, direction):
                    return 0.5  # placeholder until NOAA model integrated
                opp = _process_market(market, "WEATHER", _weather_prob)
                if opp:
                    opportunities.append(opp)
                time.sleep(0.3)
        except Exception as e:
            db.log_error("scanner.weather", f"{series} error: {e}")

    return opportunities


def scan_soccer() -> list[dict]:
    """
    Scan FIFA / World Cup / Soccer markets by title keyword search.
    Uses a simple 50/50 model with slight confidence adjustment based on
    the Kalshi implied probability — no external data source needed.
    """
    opportunities = []
    SOCCER_KEYWORDS = ["FIFA", "World Cup", "SOCCER", "FOOTBALL"]
    print("[SCANNER] Soccer: searching FIFA/World Cup/Soccer markets...")

    seen = set()
    for keyword in SOCCER_KEYWORDS:
        try:
            markets = kalshi.get_markets_by_category(keyword)
            print(f"[SCANNER] Soccer '{keyword}': {len(markets)} markets")
            for market in markets:
                ticker = market.get("ticker", "")
                if ticker in seen:
                    continue
                seen.add(ticker)
                if not kalshi.market_has_quotes(market):
                    continue
                # For binary soccer outcomes (win/lose/draw) use a flat model:
                # our probability = market implied ± small adjustment to find edge
                def _soccer_prob(threshold, direction, _market=market):
                    implied = kalshi.market_mid_price(_market)
                    if implied is None:
                        return None
                    # Slight fade toward 50/50 to find mispriced markets
                    return max(0.05, min(0.95, implied * 0.9 + 0.05))
                opp = _process_market(market, "SOCCER", _soccer_prob)
                if opp:
                    opportunities.append(opp)
                time.sleep(0.2)
        except Exception as e:
            db.log_error("scanner.soccer", f"'{keyword}' error: {e}")
        time.sleep(0.3)

    print(f"[SCANNER] Soccer: {len(opportunities)} opportunities found")
    return opportunities


# ── Main scan ─────────────────────────────────────────────────────────

def run_full_scan() -> list[dict]:
    """Run all 4 category scans. Returns list of actionable opportunities."""
    print(f"[SCANNER] Starting full scan at {_now_iso()}")
    all_opportunities = []

    for label, fn in [
        ("CRYPTO",   scan_crypto),
        ("ECONOMIC", scan_economic),
        ("ENERGY",   scan_energy),
        ("WEATHER",  scan_weather),
        ("SOCCER",   scan_soccer),
    ]:
        try:
            opps = fn()
            print(f"[SCANNER] {label}: {len(opps)} opportunities found")
            all_opportunities.extend(opps)
        except Exception as e:
            db.log_error("scanner", f"{label} scan error: {e}")

    # Sort by soonest expiry first (fast payouts), break ties by edge score
    def _sort_key(x):
        close_time = x.get("close_time", "")
        try:
            close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            hours_left = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
        except Exception:
            hours_left = 999
        return (hours_left, -x.get("edge_score", 0))

    all_opportunities.sort(key=_sort_key)
    print(f"[SCANNER] Scan complete — {len(all_opportunities)} total opportunities")
    return all_opportunities
