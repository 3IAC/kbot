# KBOT — Autonomous Kalshi Trading Bot

> Paper-safe, zero-prompt, autonomous edge-finding bot for [Kalshi](https://kalshi.com) prediction markets.
> Scans 4 market categories every 30 minutes. Runs on Railway. Dashboard on Vercel.

---

## Stack

| Layer | Tech |
|---|---|
| Bot engine | Python + APScheduler |
| Dashboard | Flask + Chart.js |
| Local DB | SQLite |
| Production DB | PostgreSQL (Railway) |
| Deployment | Railway (bot) + Vercel (dashboard) |

---

## Market Categories

| Category | Data Source | API |
|---|---|---|
| Weather | NOAA | `api.weather.gov` (no key) |
| Crypto | Binance | `api.binance.com` (no key) |
| Economic | FRED | `api.stlouisfed.org` (free key) |
| Energy/Gas | EIA | `api.eia.gov` (free key) |

---

## Quick Start (Windows)

```bash
# 1. Clone / enter directory
cd kbot

# 2. Copy and fill env file
copy .env.example .env
# Edit .env with your Kalshi key ID, FRED key, EIA key

# 3. Place your Kalshi RSA private key at:
#    ./private_key.pem  (or update KALSHI_PRIVATE_KEY_PATH in .env)

# 4. Double-click start.bat
#    — creates venv, installs deps, starts bot + dashboard
```

Dashboard opens at **http://localhost:5000**

---

## Manual Start

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

pip install -r requirements.txt
python bot/main.py
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `KALSHI_KEY_ID` | ✅ | Your Kalshi API key ID |
| `KALSHI_PRIVATE_KEY_PATH` | ✅ | Path to RSA private key .pem |
| `KALSHI_ENV` | ✅ | `demo` or `live` |
| `PAPER_TRADING` | ✅ | `true` = zero real orders |
| `PAPER_STARTING_BALANCE` | — | Starting simulated balance (default $100) |
| `PAPER_KILL_SWITCH_BALANCE` | — | Auto-halt balance (default $70) |
| `FRED_API_KEY` | — | For economic market scanning |
| `EIA_API_KEY` | — | For energy market scanning |
| `DATABASE_URL` | — | PostgreSQL URL (Railway sets automatically) |

---

## Risk Parameters

| Parameter | Value |
|---|---|
| Min edge to trade | 10% (after fees) |
| Fee buffer | 3% |
| Bet size | 3% of balance |
| Max open positions | 5 |
| Min open interest | 500 contracts |
| Min time to expiry | 2 hours |
| Kill switch | $70 (30% drawdown from $100) |

---

## Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and init
railway login
railway init

# Add PostgreSQL
railway add --plugin postgresql

# Set env vars
railway variables set KALSHI_KEY_ID=xxx
railway variables set KALSHI_PRIVATE_KEY_PATH=/app/private_key.pem
railway variables set KALSHI_ENV=demo
railway variables set PAPER_TRADING=true
railway variables set FRED_API_KEY=xxx
railway variables set EIA_API_KEY=xxx

# Deploy
railway up
```

---

## Deploy Dashboard to Vercel

```bash
# Install Vercel CLI
npm install -g vercel

# Deploy (from kbot/ root)
vercel

# Set env vars in Vercel dashboard or:
vercel env add PAPER_TRADING
```

---

## Switch to Live Trading

1. Set `KALSHI_ENV=live` in `.env`
2. Set `PAPER_TRADING=false`
3. Verify your Kalshi account has sufficient balance
4. The bot will use the live API: `https://trading-api.kalshi.com/trade-api/v2`

⚠️ **Start with demo mode and paper trading until you've verified performance.**

---

## Project Structure

```
kbot/
├── bot/
│   ├── main.py          # Scheduler, watchdog, entry point
│   ├── config.py        # All env vars and constants
│   ├── database.py      # SQLite/PostgreSQL abstraction
│   ├── kalshi_client.py # RSA-PSS signed API client
│   ├── noaa_client.py   # Weather data (50 US cities)
│   ├── binance_client.py# Crypto prices + threshold model
│   ├── fred_client.py   # Economic indicators
│   ├── eia_client.py    # Energy/gas prices
│   ├── scanner.py       # 4-category opportunity scanner
│   ├── trader.py        # Trade execution (paper + live)
│   ├── exit_monitor.py  # Position exit logic
│   └── risk.py          # Edge check, sizing, kill switch
├── dashboard/
│   ├── app.py           # Flask API server
│   └── templates/
│       └── index.html   # Dark finance UI
├── .env.example
├── .gitignore
├── requirements.txt
├── Procfile             # Railway: web: python bot/main.py
├── railway.json
├── vercel.json
├── start.bat            # Windows one-click launcher
└── README.md
```

---

## Getting API Keys

- **Kalshi**: [kalshi.com/account/profile/api](https://kalshi.com/account/profile/api) → generate RSA key pair
- **FRED**: [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html)
- **EIA**: [eia.gov/opendata/register.php](https://www.eia.gov/opendata/register.php)
- **NOAA**: No key needed
- **Binance**: No key needed (public market data only)
