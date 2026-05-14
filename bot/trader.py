from datetime import datetime, timezone
import bot.database as db
import bot.kalshi_client as kalshi
from bot.risk import can_trade, bet_size, check_kill_switch
from bot.config import PAPER_TRADING


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def execute_opportunity(opp: dict) -> bool:
    """
    Attempt to trade an opportunity. Returns True if trade was placed.
    All decisions are made here — no user prompts ever.
    """
    market_id = opp.get("market_id", "")
    title = opp.get("title", "")
    category = opp.get("category", "")
    direction = opp.get("direction", "yes")
    edge = opp.get("edge_score", 0)

    check_kill_switch()

    tradeable, reason = can_trade()
    if not tradeable:
        print(f"[TRADER] Skipping {market_id}: {reason}")
        return False

    acc = db.get_account()
    balance = acc["balance"]
    stake = bet_size(balance)

    if stake < 1.00:
        print(f"[TRADER] Stake ${stake:.2f} too small, skipping")
        return False

    entry_price = opp.get("kalshi_implied", 0.5)

    if PAPER_TRADING:
        return _paper_trade(opp, stake, entry_price, balance)
    else:
        return _live_trade(opp, stake, entry_price)


def _paper_trade(opp: dict, stake: float, entry_price: float, balance: float) -> bool:
    market_id = opp["market_id"]
    direction = opp.get("direction", "yes")
    contracts = max(1, int(stake / max(entry_price, 0.01)))

    pos = {
        "market_id": market_id,
        "market_title": opp.get("title", ""),
        "category": opp.get("category", ""),
        "direction": direction,
        "stake": stake,
        "entry_price": entry_price,
        "contracts": contracts,
        "noaa_prob_at_entry": opp.get("our_prob"),
        "kalshi_implied_at_entry": opp.get("kalshi_implied"),
        "edge_at_entry": opp.get("edge_score"),
        "entry_time": _now_iso(),
    }

    try:
        db.add_open_position(pos)
        new_balance = balance - stake
        db.update_balance(new_balance)
        print(f"[PAPER] Opened {direction.upper()} on {market_id} | stake=${stake:.2f} | entry={entry_price:.4f} | edge={opp.get('edge_score',0):.1%}")
        return True
    except Exception as e:
        db.log_error("trader", f"Paper trade failed for {market_id}: {e}")
        return False


def _live_trade(opp: dict, stake: float, entry_price: float) -> bool:
    market_id = opp["market_id"]
    direction = opp.get("direction", "yes")
    contracts = max(1, int(stake / max(entry_price, 0.01)))

    try:
        result = kalshi.place_order(
            ticker=market_id,
            side=direction,
            count=contracts,
            price=entry_price,
        )
        if not result:
            db.log_error("trader", f"Live order failed for {market_id}")
            return False

        pos = {
            "market_id": market_id,
            "market_title": opp.get("title", ""),
            "category": opp.get("category", ""),
            "direction": direction,
            "stake": stake,
            "entry_price": entry_price,
            "contracts": contracts,
            "noaa_prob_at_entry": opp.get("our_prob"),
            "kalshi_implied_at_entry": opp.get("kalshi_implied"),
            "edge_at_entry": opp.get("edge_score"),
            "entry_time": _now_iso(),
        }
        db.add_open_position(pos)
        print(f"[LIVE] Opened {direction.upper()} on {market_id} | stake=${stake:.2f} | contracts={contracts}")
        return True
    except Exception as e:
        db.log_error("trader", f"Live trade error for {market_id}: {e}")
        return False


def process_opportunities(opportunities: list[dict]) -> int:
    """Process a ranked list of opportunities. Returns number of trades placed."""
    placed = 0
    for opp in opportunities:
        check_kill_switch()
        tradeable, _ = can_trade()
        if not tradeable:
            break
        if execute_opportunity(opp):
            placed += 1
    return placed
