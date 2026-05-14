from bot.config import (
    MIN_EDGE, FEE_BUFFER, BET_PCT, MAX_OPEN_POSITIONS,
    MIN_OPEN_INTEREST, MIN_HOURS_TO_EXPIRY, PAPER_KILL_SWITCH_BALANCE
)
import bot.database as db
from datetime import datetime, timezone


def check_edge(our_prob: float, kalshi_implied: float) -> tuple[float, bool]:
    """Returns (edge, qualifies). Edge = our_prob - kalshi_implied - fee_buffer."""
    edge = our_prob - kalshi_implied - FEE_BUFFER
    return round(edge, 4), edge >= MIN_EDGE


def bet_size(balance: float) -> float:
    return round(balance * BET_PCT, 2)


def can_trade() -> tuple[bool, str]:
    if db.is_halted():
        return False, "Kill switch triggered — trading halted"

    acc = db.get_account()
    if not acc:
        return False, "No account record"

    if acc["balance"] <= PAPER_KILL_SWITCH_BALANCE:
        db.halt_trading()
        return False, f"Balance ${acc['balance']:.2f} hit kill switch ${PAPER_KILL_SWITCH_BALANCE:.2f}"

    open_count = db.count_open_positions()
    if open_count >= MAX_OPEN_POSITIONS:
        return False, f"Max positions reached ({open_count}/{MAX_OPEN_POSITIONS})"

    return True, "ok"


def validate_market(market: dict) -> tuple[bool, str]:
    oi = market.get("open_interest", 0) or 0
    if oi < MIN_OPEN_INTEREST:
        return False, f"Open interest {oi} < {MIN_OPEN_INTEREST}"

    expiry_str = market.get("close_time") or market.get("expiration_time") or ""
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours_left = (expiry - now).total_seconds() / 3600
            if hours_left < MIN_HOURS_TO_EXPIRY:
                return False, f"Only {hours_left:.1f}h to expiry"
        except Exception:
            pass

    return True, "ok"


def check_kill_switch():
    acc = db.get_account()
    if acc and acc["balance"] <= PAPER_KILL_SWITCH_BALANCE and not acc["halted"]:
        db.halt_trading()
        db.log_error("risk", f"Kill switch triggered at ${acc['balance']:.2f}")
        return True
    return False
