import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# ── Kalshi ────────────────────────────────────────────────────────────
KALSHI_ENV = os.getenv("KALSHI_ENV", "demo")
KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./private_key.pem")

if KALSHI_ENV == "live":
    KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
else:
    KALSHI_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"

# ── Paper trading ─────────────────────────────────────────────────────
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
PAPER_STARTING_BALANCE = float(os.getenv("PAPER_STARTING_BALANCE", "100.00"))
PAPER_KILL_SWITCH_BALANCE = float(os.getenv("PAPER_KILL_SWITCH_BALANCE", "50.00"))

# ── External APIs ─────────────────────────────────────────────────────
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
EIA_API_KEY = os.getenv("EIA_API_KEY", "")

# ── Database ──────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///kbot.db")
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "kbot.db"))

# ── Risk parameters ───────────────────────────────────────────────────
MIN_EDGE = 0.05           # 5% minimum edge after fees
FEE_BUFFER = 0.01         # 1% fee buffer
BET_PCT = 0.03            # 3% of balance per trade
MAX_OPEN_POSITIONS = 5
MIN_OPEN_INTEREST = 0     # demo markets have no OI — keep at 0
MIN_HOURS_TO_EXPIRY = 0.5 # 30-min minimum (fast resolving bets)
MAX_HOURS_TO_EXPIRY = 24  # 24h max — only today's contracts

# ── Scheduler intervals ───────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = 30
EXIT_CHECK_INTERVAL_MINUTES = 10

# ── Dashboard ─────────────────────────────────────────────────────────
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5000"))
DASHBOARD_REFRESH_SECONDS = 20
