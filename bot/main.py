"""
KBOT — Autonomous Kalshi trading bot.
Runs scanner every 30 minutes and exit monitor every 10 minutes.
Zero user prompts. All errors logged silently.
"""
import sys
import time
import signal
import threading
from datetime import datetime, timezone

# Force UTF-8 output on Windows so Unicode characters in logs never crash the bot
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor

import bot.database as db
from bot.scanner import run_full_scan
from bot.trader import process_opportunities
from bot.exit_monitor import check_positions
from bot.risk import check_kill_switch
from bot.config import SCAN_INTERVAL_MINUTES, EXIT_CHECK_INTERVAL_MINUTES, PAPER_TRADING, DASHBOARD_PORT


_shutdown_event = threading.Event()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def scan_job():
    try:
        print(f"\n{'='*60}")
        print(f"[KBOT] SCAN JOB starting at {_now()}")
        if db.is_halted():
            print("[KBOT] Trading halted — skipping scan")
            return
        opportunities = run_full_scan()
        if opportunities:
            placed = process_opportunities(opportunities)
            print(f"[KBOT] Placed {placed} trades from {len(opportunities)} opportunities")
        else:
            print("[KBOT] No actionable opportunities found")
        check_kill_switch()
    except Exception as e:
        db.log_error("main.scan_job", str(e))


def exit_job():
    try:
        if db.is_halted():
            return
        check_positions()
        check_kill_switch()
    except Exception as e:
        db.log_error("main.exit_job", str(e))


def start_dashboard():
    """Start Flask dashboard in a background thread."""
    try:
        from dashboard.app import app
        print(f"[KBOT] Dashboard starting on http://localhost:{DASHBOARD_PORT}")
        app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)
    except Exception as e:
        db.log_error("main.dashboard", f"Dashboard failed to start: {e}")


def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║                 KBOT — Kalshi Trading Bot                ║
║            Autonomous. Zero-prompt. Paper-safe.          ║
╚══════════════════════════════════════════════════════════╝
""")

    mode = "PAPER TRADING ($100 simulated)" if PAPER_TRADING else "⚠️  LIVE TRADING"
    print(f"Mode: {mode}")
    print(f"Scan interval: every {SCAN_INTERVAL_MINUTES} minutes")
    print(f"Exit monitor: every {EXIT_CHECK_INTERVAL_MINUTES} minutes")
    print()

    db.init_db()
    acc = db.get_account()
    print(f"Account balance: ${acc['balance']:.2f}")
    print(f"Total trades: {acc['total_trades']}")
    print()

    executors = {"default": ThreadPoolExecutor(4)}
    scheduler = BackgroundScheduler(executors=executors, timezone="UTC")

    scheduler.add_job(scan_job, "interval", minutes=SCAN_INTERVAL_MINUTES, id="scanner",
                      next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(exit_job, "interval", minutes=EXIT_CHECK_INTERVAL_MINUTES, id="exit_monitor")

    scheduler.start()
    print(f"[KBOT] Scheduler started. First scan running now...")

    dash_thread = threading.Thread(target=start_dashboard, daemon=True)
    dash_thread.start()

    def _handle_signal(sig, frame):
        print(f"\n[KBOT] Shutdown signal received")
        _shutdown_event.set()
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not _shutdown_event.is_set():
        time.sleep(5)


if __name__ == "__main__":
    main()
