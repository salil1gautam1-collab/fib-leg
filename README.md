# Fib-Leg Scanner & Semi-Auto Trader

Implements the strategy in [`../fib-leg-scanner-design.md`](../fib-leg-scanner-design.md):
hourly breakout → trailing fib leg (locked by a 0.382 reversal) → pullback to 0.5
→ 15m price-action entry, SL below 0.618. Scans Nifty 50/200 + index, alerts on
Telegram with an annotated chart, and (semi-auto) places a hedged future + option
trade via Fyers/Dhan. US = alert-only.

## Status (v0.1 — "make it exist")

| Layer | State |
|---|---|
| Adaptive ZigZag pivots (`strategy/pivots.py`) | ✅ real |
| Fib-leg state machine (`strategy/fib_leg.py`) | ✅ real |
| Dual timeframe: 1H setup + 15m trigger (`backtest/driver.py`) | ✅ real |
| 15m trigger (`strategy/trigger.py`) | ✅ real |
| Backtest + R-multiple report + sweep (`backtest/`, `run_sweep.py`) | ✅ real, runs offline |
| Tests (`tests/`) | ✅ real |
| Pine indicator (`pine/fib_leg.pine`) | ✅ real (logic parity target) |
| Data feeds | ✅ synthetic + yfinance + **Fyers** + **Dhan** (`data/dhan_feed.py`, free, needs token); resolver verified |
| Trade execution | ⏸️ shelved — alerts-only scope; hedge logic kept in design doc |
| Web + Mobile PWA dashboard (`docs/`) | ✅ real, verified rendering |
| GitHub Actions cron + scan job (`scan.py`, `.github/workflows/`) | ✅ real |
| Telegram push (`scan.py`) | ✅ basic (env-gated); rich chart = TODO |
| Brokers / orchestrator | ⏸️ shelved (alerts-only) |

The **core engine is pure stdlib** — no installs needed to run the backtest or tests.

## Run it

> Needs Python 3.10+. (Not currently installed on this machine — install from
> python.org or the Microsoft Store, then:)

```bash
cd fib-leg

# offline backtest on synthetic data — proves the engine end-to-end
python run_backtest.py

# try the entry/stop toggles
python run_backtest.py --entry 0.618 --sl 0.786

# real data (optional): pip install yfinance
python run_backtest.py --yf RELIANCE.NS INFY.NS

# dual timeframe — 1H setup + 15m trigger (your actual rule)
python run_backtest.py --yf RELIANCE.NS "^NSEI" --dual

# parameter sweep over real data (ranks entry/sl/threshold by total R)
python run_sweep.py --yf RELIANCE.NS INFY.NS TCS.NS "^NSEI"

# Dhan — free NSE history (one-time setup):
#   1. copy config/dhan.example.json -> ~/.fibleg/dhan.json, fill in client_id/access_token
#      (from web.dhan.co -> DhanHQ Trading API)
#   2. run with real history (scrip master auto-downloads/caches):
python run_backtest.py --dhan RELIANCE.NS INFY.NS "^NSEI" --dual --days 365

# Fyers (alternative, if account active): config/fyers.example.json -> ~/.fibleg/fyers.json,
#   then: python fyers_login.py ; python run_backtest.py --fyers RELIANCE.NS --dual

# tests — zero installs needed (stdlib runner)
python tests/run_tests.py
# …or with pytest if you have it:  python -m pytest tests/ -q
```

## Layout

```
fibleg/
  models.py            domain types (Bar, Pivot, FibLeg, Setup, Signal, Trade)
  config.py            StrategyConfig toggles (entry/sl/targets/threshold)
  indicators/atr.py    Wilder ATR (streaming + batch)
  strategy/
    pivots.py          adaptive threshold ZigZag  (design §1.5)
    fib_leg.py         per-symbol state machine    (design §1)
    trigger.py         15m price-action entry rule
  backtest/            replay engine + R-multiple report
  data/                universe + feeds (synthetic / yfinance / Fyers TODO)
  alerts/              telegram_bot.py + chart.py (stubs)
  execution/brokers.py paper + Fyers/Dhan + hedge builder (design §6.5)
  orchestrator.py      live sharded scan loop (skeleton)
pine/fib_leg.pine      TradingView indicator
tests/                 pivot + fib-leg unit tests
run_backtest.py        offline entry point
```

## Web + Mobile dashboard (free, no PC)

`docs/` is a static PWA (installable on phone). `scan.py` writes `docs/signals.json`;
the page reads it and shows live setups + a TradingView chart + recent signals.

**Deploy (all free, runs without your PC):**
1. Push this repo to GitHub.
2. **Settings → Pages** → Deploy from branch → `main` / `/docs`. Your app is live at
   `https://<user>.github.io/<repo>/`.
3. **Actions** tab → enable workflows. `.github/workflows/scan.yml` then runs every
   15 min during NSE hours, regenerates `signals.json`, commits it → the app updates.
4. (Optional) **Settings → Secrets**: add `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`
   (then set `SCAN_SOURCE: dhan` in the workflow) and `TELEGRAM_TOKEN` / `TELEGRAM_CHAT`
   for push alerts. Until then it runs on free yfinance data.

Local preview: `python -m http.server 5050 --directory docs` → open `localhost:5050`.

## Next build steps

1. Validate the edge: run the backtest on real Nifty data, tune
   `leg_reversal_thresh` / `entry_ratio` / `sl_ratio`.
2. Wire the Fyers feed (accurate NSE 1H/15m) + true 15m trigger timeframe.
3. Telegram alerter + mplfinance chart PNG.
4. Semi-auto confirm-to-trade (future + option) via Fyers/Dhan.
5. Pine↔Python parity fixture test.
