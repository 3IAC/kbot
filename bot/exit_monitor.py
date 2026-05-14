from datetime import datetime, timezone, timedelta
import bot.database as db
import bot.kalshi_client as kalshi
from bot.risk import check_kill_switch
from bot.config import PAPER_TRADING


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _settle_paper_position(pos: dict, exit_price: float, exit_reason: str):
    """Settle a paper trade: calculate P&L, update account, archive to trades table."""
    stake = pos.get("stake", 0)
    contracts = pos.get("contracts", 0) or 1
    entry_price = pos.get("entry_price", 0.5)
    direction = pos.get("direction", "yes")

    if direction == "yes":
        pnl = (exit_price - entry_price) * contracts
    else:
        pnl = ((1.0 - exit_price) - (1.0 - entry_price)) * contracts

    won = pnl > 0
    outcome = "win" if won else "loss"

    trade = {
        "market_id": pos["market_id"],
        "market_title": pos.get("market_title",""),
        "category": pos.get("category",""),
        "direction": direction,
        "stake": stake,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "noaa_prob_at_entry": pos.get("noaa_prob_at_entry"),
        "kalshi_implied_at_entry": pos.get("kalshi_implied_at_entry"),
        "edge_at_entry": pos.get("edge_at_entry"),
        "outcome": outcome,
        "pnl_usd": round(pnl, 4),
        "entry_time": pos.get("entry_time",""),
        "exit_time": _now_iso(),
        "exit_reason": exit_reason,
    }

    db.log_trade(trade)
    db.remove_open_position(pos["market_id"])

    acc = db.get_account()
    new_balance = acc["balance"] + stake + pnl
    db.update_balance(new_balance, pnl_delta=pnl, trade_won=won)

    print(f"[EXIT] {pos['market_id']} | {outcome.upper()} | P&L: ${pnl:+.2f} | reason: {exit_reason}")
    check_kill_switch()


def _should_exit_market(market: dict) -> tuple[bool, str]:
    """Determine if a market has resolved or is close enough to exit."""
    status = (market.get("status") or "").lower()
    if status in ("settled", "finalized", "resolved"):
        return True, "market_settled"

    close_time_str = market.get("close_time") or market.get("expiration_time") or ""
    if close_time_str:
        try:
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if now >= close_time:
                return True, "market_expired"
            if (close_time - now) < timedelta(minutes=30):
                return True, "near_expiry_exit"
        except Exception:
            pass

    return False, ""


def _get_exit_price(market: dict, pos: dict) -> float:
    """Get current market price for exit simulation."""
    result = market.get("result")
    direction = pos.get("direction","yes")

    if result == "yes":
        return 1.0 if direction == "yes" else 0.0
    elif result == "no":
        return 0.0 if direction == "yes" else 1.0

    ticker = pos["market_id"]
    current_implied = kalshi.get_implied_prob(ticker, direction)
    if current_implied is not None:
        return current_implied

    return pos.get("entry_price", 0.5)


def check_positions():
    """Main exit monitor loop — checks all open positions."""
    positions = db.get_open_positions()
    if not positions:
        return

    print(f"[EXIT MONITOR] Checking {len(positions)} open positions")

    for pos in positions:
        try:
            market_id = pos["market_id"]
            market = kalshi.get_market(market_id)

            if not market:
                entry_str = pos.get("entry_time","")
                if entry_str:
                    try:
                        entry_dt = datetime.fromisoformat(entry_str.replace("Z","+00:00"))
                        age_hours = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
                        if age_hours > 48:
                            _settle_paper_position(pos, pos.get("entry_price",0.5), "market_not_found_timeout")
                    except Exception:
                        pass
                continue

            should_exit, reason = _should_exit_market(market)

            if should_exit:
                exit_price = _get_exit_price(market, pos)
                if PAPER_TRADING:
                    _settle_paper_position(pos, exit_price, reason)
                else:
                    _settle_paper_position(pos, exit_price, reason)
            else:
                current_implied = kalshi.get_implied_prob(market_id, pos.get("direction","yes"))
                if current_implied is not None:
                    entry_price = pos.get("entry_price", 0.5)
                    unrealized_pnl = (current_implied - entry_price) * (pos.get("contracts",1))
                    print(f"[EXIT MONITOR] {market_id} | current={current_implied:.3f} | unrealized P&L: ${unrealized_pnl:+.2f}")

        except Exception as e:
            db.log_error("exit_monitor", f"Position check error for {pos.get('market_id','?')}: {e}")
