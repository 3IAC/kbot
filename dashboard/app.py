import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, jsonify, render_template
import bot.database as db
from bot.config import PAPER_TRADING, DASHBOARD_REFRESH_SECONDS

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


@app.route("/")
def index():
    return render_template("index.html",
                           paper_trading=PAPER_TRADING,
                           refresh_seconds=DASHBOARD_REFRESH_SECONDS)


@app.route("/api/status")
def api_status():
    try:
        db.init_db()
        acc = db.get_account()
        today_pnl = db.get_today_pnl()
        open_positions = db.get_open_positions()
        markets_scanned = db.get_markets_scanned_today()

        win_rate = 0.0
        if acc and acc["total_trades"] > 0:
            win_rate = round(acc["win_count"] / acc["total_trades"] * 100, 1)

        return jsonify({
            "ok": True,
            "balance": round(acc["balance"], 2) if acc else 100.00,
            "today_pnl": round(today_pnl, 2),
            "all_time_pnl": round(acc["total_pnl"], 2) if acc else 0.0,
            "win_rate": win_rate,
            "total_trades": acc["total_trades"] if acc else 0,
            "win_count": acc["win_count"] if acc else 0,
            "open_positions": len(open_positions),
            "markets_scanned": markets_scanned,
            "halted": bool(acc["halted"]) if acc else False,
            "paper_trading": PAPER_TRADING,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/opportunities")
def api_opportunities():
    try:
        db.init_db()
        opps = db.get_recent_opportunities(100)
        return jsonify({"ok": True, "opportunities": opps})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/positions")
def api_positions():
    try:
        db.init_db()
        positions = db.get_open_positions()
        return jsonify({"ok": True, "positions": positions})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trades")
def api_trades():
    try:
        db.init_db()
        trades = db.get_recent_trades(50)
        return jsonify({"ok": True, "trades": trades})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/pnl_series")
def api_pnl_series():
    """Return cumulative P&L series for sparkline chart."""
    try:
        db.init_db()
        trades = db.get_recent_trades(100)
        trades_reversed = list(reversed(trades))
        cumulative = 0.0
        series = []
        for t in trades_reversed:
            cumulative += t.get("pnl_usd", 0) or 0
            series.append(round(cumulative, 2))
        return jsonify({"ok": True, "series": series})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
