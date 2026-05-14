import sqlite3
import os
import sys
import threading
from datetime import datetime, timezone
from bot.config import DB_PATH, DATABASE_URL, PAPER_STARTING_BALANCE

_local = threading.local()


def _is_postgres():
    return DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")


def get_conn():
    if _is_postgres():
        import psycopg2
        import psycopg2.extras
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn
    else:
        if not hasattr(_local, "conn") or _local.conn is None:
            _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _local.conn.row_factory = sqlite3.Row
            _local.conn.execute("PRAGMA journal_mode=WAL")
        return _local.conn


def _ph():
    return "%s" if _is_postgres() else "?"


def init_db():
    ph = _ph()
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            market_title TEXT,
            category TEXT,
            direction TEXT,
            stake REAL,
            entry_price REAL,
            exit_price REAL,
            noaa_prob_at_entry REAL,
            kalshi_implied_at_entry REAL,
            edge_at_entry REAL,
            outcome TEXT,
            pnl_usd REAL,
            entry_time TEXT,
            exit_time TEXT,
            exit_reason TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            title TEXT,
            category TEXT,
            direction TEXT,
            edge_score REAL,
            our_prob REAL,
            kalshi_implied REAL,
            status TEXT,
            reason_skipped TEXT,
            scanned_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT,
            message TEXT,
            timestamp TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_account (
            id INTEGER PRIMARY KEY,
            balance REAL,
            total_pnl REAL,
            total_trades INTEGER,
            win_count INTEGER,
            halted INTEGER,
            updated_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS open_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT UNIQUE NOT NULL,
            market_title TEXT,
            category TEXT,
            direction TEXT,
            stake REAL,
            entry_price REAL,
            contracts INTEGER,
            noaa_prob_at_entry REAL,
            kalshi_implied_at_entry REAL,
            edge_at_entry REAL,
            entry_time TEXT
        )
    """)

    conn.commit()

    row = _fetchone("SELECT id FROM paper_account WHERE id = 1")
    if row is None:
        now = _now()
        conn2 = get_conn()
        c2 = conn2.cursor()
        c2.execute(
            f"INSERT INTO paper_account (id, balance, total_pnl, total_trades, win_count, halted, updated_at) VALUES (1, {ph}, 0.0, 0, 0, 0, {ph})",
            (PAPER_STARTING_BALANCE, now)
        )
        conn2.commit()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fetchone(sql, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(sql, params)
    row = c.fetchone()
    return dict(row) if row else None


def _fetchall(sql, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(sql, params)
    rows = c.fetchall()
    return [dict(r) for r in rows]


def _execute(sql, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(sql, params)
    conn.commit()
    return c.lastrowid


# ── Paper account ─────────────────────────────────────────────────────

def get_account():
    return _fetchone("SELECT * FROM paper_account WHERE id = 1")


def update_balance(balance, pnl_delta=0.0, trade_won=None):
    acc = get_account()
    new_pnl = acc["total_pnl"] + pnl_delta
    new_trades = acc["total_trades"] + (1 if trade_won is not None else 0)
    new_wins = acc["win_count"] + (1 if trade_won else 0)
    ph = _ph()
    _execute(
        f"UPDATE paper_account SET balance={ph}, total_pnl={ph}, total_trades={ph}, win_count={ph}, updated_at={ph} WHERE id=1",
        (balance, new_pnl, new_trades, new_wins, _now())
    )


def halt_trading():
    _execute(f"UPDATE paper_account SET halted=1, updated_at={_ph()} WHERE id=1", (_now(),))


def is_halted():
    acc = get_account()
    return bool(acc and acc["halted"])


# ── Open positions ────────────────────────────────────────────────────

def get_open_positions():
    return _fetchall("SELECT * FROM open_positions ORDER BY entry_time DESC")


def count_open_positions():
    row = _fetchone("SELECT COUNT(*) as cnt FROM open_positions")
    return row["cnt"] if row else 0


def add_open_position(pos: dict):
    ph = _ph()
    _execute(
        f"""INSERT OR REPLACE INTO open_positions
            (market_id, market_title, category, direction, stake, entry_price, contracts,
             noaa_prob_at_entry, kalshi_implied_at_entry, edge_at_entry, entry_time)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
        (pos["market_id"], pos.get("market_title",""), pos.get("category",""),
         pos.get("direction",""), pos.get("stake",0), pos.get("entry_price",0),
         pos.get("contracts",0), pos.get("noaa_prob_at_entry"), pos.get("kalshi_implied_at_entry"),
         pos.get("edge_at_entry"), pos.get("entry_time", _now()))
    )


def remove_open_position(market_id: str):
    _execute(f"DELETE FROM open_positions WHERE market_id={_ph()}", (market_id,))


# ── Trades ────────────────────────────────────────────────────────────

def log_trade(trade: dict):
    ph = _ph()
    _execute(
        f"""INSERT INTO trades
            (market_id, market_title, category, direction, stake, entry_price, exit_price,
             noaa_prob_at_entry, kalshi_implied_at_entry, edge_at_entry, outcome, pnl_usd,
             entry_time, exit_time, exit_reason)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
        (trade.get("market_id",""), trade.get("market_title",""), trade.get("category",""),
         trade.get("direction",""), trade.get("stake",0), trade.get("entry_price",0),
         trade.get("exit_price"), trade.get("noaa_prob_at_entry"), trade.get("kalshi_implied_at_entry"),
         trade.get("edge_at_entry"), trade.get("outcome",""), trade.get("pnl_usd",0),
         trade.get("entry_time",""), trade.get("exit_time", _now()), trade.get("exit_reason",""))
    )


def get_recent_trades(limit=50):
    return _fetchall(
        f"SELECT * FROM trades ORDER BY entry_time DESC LIMIT {_ph()}", (limit,)
    )


def get_today_pnl():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = _fetchone(
        f"SELECT COALESCE(SUM(pnl_usd),0) as pnl FROM trades WHERE entry_time LIKE {_ph()}",
        (f"{today}%",)
    )
    return row["pnl"] if row else 0.0


# ── Opportunities ─────────────────────────────────────────────────────

def log_opportunity(opp: dict):
    ph = _ph()
    _execute(
        f"""INSERT INTO opportunities
            (market_id, title, category, direction, edge_score, our_prob, kalshi_implied,
             status, reason_skipped, scanned_at)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
        (opp.get("market_id",""), opp.get("title",""), opp.get("category",""),
         opp.get("direction",""), opp.get("edge_score",0), opp.get("our_prob"),
         opp.get("kalshi_implied"), opp.get("status","scanned"),
         opp.get("reason_skipped",""), opp.get("scanned_at", _now()))
    )


def get_recent_opportunities(limit=100):
    return _fetchall(
        f"SELECT * FROM opportunities ORDER BY scanned_at DESC LIMIT {_ph()}", (limit,)
    )


def get_markets_scanned_today():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = _fetchone(
        f"SELECT COUNT(*) as cnt FROM opportunities WHERE scanned_at LIKE {_ph()}",
        (f"{today}%",)
    )
    return row["cnt"] if row else 0


# ── Errors ────────────────────────────────────────────────────────────

def log_error(module: str, message: str):
    ph = _ph()
    _execute(
        f"INSERT INTO errors (module, message, timestamp) VALUES ({ph},{ph},{ph})",
        (module, str(message)[:2000], _now())
    )
    try:
        print(f"[ERROR] {module}: {message}")
    except UnicodeEncodeError:
        safe = str(message).encode(sys.stdout.encoding or "ascii", errors="replace").decode(sys.stdout.encoding or "ascii")
        print(f"[ERROR] {module}: {safe}")
