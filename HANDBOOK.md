# Fib-Leg Scanner — Owner's Handbook

*The complete reference: what this system is, the method behind it, the evidence, how to operate it, and its nuances. Written to be handed to someone who has never seen it.*

**Live app:** https://salil1gautam1-collab.github.io/fib-leg/
**Repo:** https://github.com/salil1gautam1-collab/fib-leg (public)

---

## 1. What this is (and is not)

A **swing-trading scanner and paper-trading agent** for NSE stocks, built on the Fibonacci
impulse-leg method from the owner's book *TradeWisely*. It:

- scans the market on a cloud schedule, draws impulse legs, and detects pullback entries;
- filters setups through a validated market-context gate (⭐ Best);
- sends alerts and tracks a **paper** ledger sized to your capital and risk plan;
- lets you slice an 11-year backtest interactively to see why the defaults are what they are.

**Hard rules (by the owner's design):**
- **Alerts and paper trading only. It never places real orders or moves money.**
- The owner holds start / pause / stop at all times.
- Broker credentials live only on the owner's machine (`~/.fibleg/*.json`) or in GitHub's
  encrypted secrets — never in code, chat, or the site.
- Real execution, if ever, is a separate deliberate decision after a long paper record
  (see §10 Roadmap).

---

## 2. The trading method

### 2.1 The impulse leg (the foundation)

Everything starts with drawing the **leg** correctly — an impulse move from a trend-change
origin to its extreme. The engine follows the book's rules:

1. **A leg is born on a break of structure.** A down-impulse isn't valid until price closes
   below the previous swing low (mirror for up). You don't fade a high just because it
   retraced; you wait for structure to actually break.
2. **The extreme drags with price.** While the move keeps making new highs (up-leg), the
   leg's end extends with it.
3. **A 0.382 close against the move marks a *provisional* top** — the leg pauses but may
   still extend if price resumes and makes a new extreme.
4. **The leg truly ENDS only when a candle closes beyond 0.618** of the whole leg
   (origin→extreme). This is law. Shallower pullbacks extend the same leg rather than
   re-anchoring the origin (prevents the "ratcheting origin" error).
5. **Fib convention:** leg START = 1.0, leg END = 0.0, for both directions. Retracement
   levels (0.382 / 0.5 / 0.618 / 0.786) are measured from the extreme back toward the origin.

*Detection method:* `Book 0.382` with the 0.618 re-anchor rule (`book_reanchor_ratio=0.618`)
— the configuration the owner visually validated leg-by-leg and that won the 11-year test.

### 2.2 The trade

- **Entry zone: the 0.5–0.618 retracement** of the leg (the book calls 0.618 the most
  important level). Not a limit order at a line — a *zone*.
- **Zone respect (required):** a candle on the detection timeframe (2H) must CLOSE inside
  the zone and a later candle CLOSE back out — or print a strong rejection **pin bar**
  through the zone. Price gapping over the zone = no trade (no chasing).
- **Nested entry:** once the zone is respected, the actual fill comes from a **5-minute
  nested Fibonacci** of the bounce — a precise, smaller-timeframe trigger.
- **Stop: a 5-minute CLOSE beyond the 0.786** retracement (not an intrabar wick).
- **Targets: entry-dependent harmonic projection.** The deeper the entry retracement `d`,
  the nearer the projected D-target (`1/d`): an entry at 0.5 projects 2.0, at 0.618 → 1.618,
  at 0.786 → 1.272. Scale-out rungs at the strong extension levels (1.13 / 1.272 / 1.618 /
  2.618) capped by that projection. This is the book's AB=CD idea mapped onto the leg.
- **Exit style: "Let it run + lock B" (validated best).** Scale out ⅓ at T1 (0.95 of the
  leg, just under the prior extreme), then move the stop **to the T1 price** (point B) —
  a failed continuation keeps the banked B-level profit instead of giving it back — and
  ratchet the stop up as further targets hit.
- **Reversal readiness:** an **M** (double top) ends an up-move and starts the down-impulse
  we short; a **W** (double bottom) starts the up-impulse we buy. A clean **pin bar** at the
  origin counts the same way. These are *confidence signals of a trend end* — the reason a
  fresh leg is trustworthy.
- **A+ confluence:** a prior swing (support/resistance) sitting near the 0.5–0.618 zone =
  "double confidence" (fib + level). Shown as a badge; deliberately NOT a hard requirement
  (testing showed requiring it removes good trades).

### 2.2b A validated side-play: the 0.618 option scalp (discretionary)

Tested on 11 years of 1-minute data (July 2026): when price makes a CLEAN first approach
into the zone of a valid 2H leg, a quick option scalp at the **0.618** has real edge —
but only in this exact form:

- **Entry at the 0.618 touch** (never 0.5 — the geometry there never offers 2R and it
  earns ~zero; the owner's ≥2R structural gate separates the two perfectly).
- **Stop ~10 bps below the zone floor**, triggered off the underlying (never the option
  quote). **Vehicle: slightly-ITM option** (delta 0.6–0.7, 10+ days to expiry, spread
  ≤1–2% of premium, no result/event days) — flat option costs are what make the
  arithmetic work; the same trade in futures dies on the toll.
- **Target the structural 0.5 / 0.382 levels; hard 15–30 min time cap.**
- Measured: ~+0.5–0.8R gross per attempt (net positive after realistic option costs),
  out-of-sample positive but weaker recently. **Win rate only 25–31%** — 7 of 10 attempts
  stop out small; the rare travelers pay for all of it. ~18 clean chances/yr across the
  universe → a discretionary side-arm, not an engine. Paper-trade it live before sizing.

### 2.3 The ⭐ Best context gate (the biggest consistency win)

Measured on ~5,100 trades over 11 years, four *ex-ante* market-context checks separate the
profitable conditions from the bleed:

| Gate | Rule | Why (measured) |
|---|---|---|
| Volatility | India VIX **below** its 20-day average | calm markets carry nearly all the profit (+0.077R vs +0.029R per trade) |
| Sector | stock's sector index trending **with** the trade | with-sector +0.087R vs against −0.016R |
| Market regime | Nifty **not** in the ADX 20–25 "whipsaw" zone | whipsaw is the only losing regime (−0.007R); sideways is actually the BEST (+0.104R) |
| Reward:risk | projected R:R to the 1.272 target **≥ 1** | the bottom R:R quintile is worthless (+0.005R) |

A setup passing all four is ⭐ **Best** — the app's default filter, the alert trigger, and
what the paper agent trades. Counter-intuitive but proven: this is a **pullback system that
thrives in sideways markets** — it does not need a trending market, it needs a *non-whipsaw*
one with calm volatility.

---

## 3. The evidence (11 years × 115 stocks, walk-forward)

All numbers net of 0.15% round-trip costs unless noted. Train (2015–20) vs test (2021–26)
split was used throughout — the edge is stronger out-of-sample than in-sample, the opposite
of curve-fitting.

### 3.1 Why these defaults

| Choice | Evidence |
|---|---|
| **2H detection** | only clearly profitable TF: 45m −319R (dead), 1H −55R, 2H **+183R** (⭐), 3H +124R, 4H +97R |
| **0.618 leg-end rule** | +105R → **+169R** on identical trade count — the single biggest improvement found; it is also the book's law |
| **lock-B exit** | beats breakeven and beats "square all at T1" (which *loses* money after costs: −75R) |
| **⭐ context gate** | +122R → **+183R** on a *third* of the trades; drawdown −47% → −27%; 10 of 12 years non-negative (2016: −26R→+28R) |
| **filters as badges, not gates** | with correct legs, M/W / pin / A+ hard-gates change little (±5R); the edge is the LEG + zone + context, not indicator stacking |

### 3.2 What was tested and rejected (so nobody re-treads it)

- **45m / 1H trading, any config** — loses after costs, always.
- **EMA-trend / ADX-on-the-stock / volume filters** — looked great 2021-26, *negative*
  2015-20 = regime-overfit. Rejected.
- **Requiring A+ (strict broken-level version)** — removes good trades. Badge only.
- **Naked options (ATM/ITM/deep-ITM), incl. quick fixed-% exits** — killed by IV crush
  (−444 to −706R under realistic vol assumptions). The strategy needs to HOLD runners;
  options punish holding.
- **Naked futures** — the edge is real but one gap produced a −13.8R single trade.
  Unbounded tail = not acceptable.

### 3.3 The instrument: Future + DOTM hedge

**Chosen: stock future + deep-OTM protective option** (put for longs, call for shorts),
strike about **one ATR beyond the 0.786 stop** (~20–30 delta), sold back on exit.
It caps the disaster tail (worst trade −13.8R → about −1.5R) for ~0.08–0.12% cost.
Note the honest finding: hedging *increases* average drawdown slightly (cost bleeds every
trade) — you hedge to survive gaps, not to smooth the curve.

### 3.4 Money math

- **R** = the rupees you agreed to lose if the trade fails (entry→stop distance × size).
  Everything is measured in that unit. See the ❓ explainer in the app's History tab.
- **Cost is the whole game:** gross edge ≈ +197R; at 0.10% costs +143R; at 0.15% +122R;
  **break-even ≈ 0.29%** round-trip (post-⭐-gate). Liquid futures ≈ 0.05–0.10% → viable.
  Delivery equity (0.2% STT) → dead. Full ATM option hedging (0.3–1%) → dead.
- **Sizing:** risk **1% of equity per trade** (1R = 1% of account). Backtest at 1%:
  ~5.7× over 11 yrs (~16%/yr) with ~−27% worst dip on the ⭐ set. 2% ≈ faster with ~−48%
  dips. 5%+ = ruin.
- **Capital reality (fixed-lot simulation):** ₹2–3L = ruin. ₹5L survives but earns ~3%
  (FD-level, casino stress). **₹8–12L = bare minimum** (one lot, ~8–9%/yr, deep dips).
  **₹25–30L+ = the real machine** — enough to hold several positions at fractional risk,
  which is where the compounding actually comes from.
- **Expectation discipline:** ~150 ⭐ trades/yr on the full 115-stock universe (~3/week);
  a **few per month** on the current 8-symbol rehearsal feed. The system's essence is
  *waiting* — a quiet week is the strategy working, not failing.

**⚠ Past tests are not future promises.** Markets change; an 11-year edge can degrade.
Paper-trade first, size small, never trade money you can't lose.

---

## 4. System architecture (how it runs itself)

```
GitHub Actions (cloud, free)                    Your devices
┌────────────────────────────────┐   fetch    ┌───────────────────────┐
│ session loop: every ~10 min    │  ────────► │ PWA (GitHub Pages)    │
│ 09:05–15:35 IST, Mon–Fri:      │            │ - Live/Agent/History/ │
│  yfinance data → engine →      │            │   Legs tabs           │
│  context gates → publish:      │            │ - paper agent (local  │
│   docs/signals.json (feed)     │            │   + Google Drive sync)│
│   docs/paper_log.json (ledger, │            └───────────────────────┘
│    append-only, never forgets) │              agent state lives in
│  → Telegram alert (⭐ only)    │              YOUR Drive app-folder
│  → nudge the page deploy       │              (private, 2FA'd)
└────────────────────────────────┘
```

Key properties:
- **No server of ours.** Static site + scheduled cloud jobs + your own Google Drive.
  Nothing personal or financial is on the public site.
- **Runs while you're offline.** The cloud records every completed ⭐ trade to the
  permanent log; your ledger recomputes from it whenever you open the app.
- **Data source today: yfinance (≈15-min delayed, 8 symbols)** — the rehearsal feed.
  Fyers (real-time, wide universe) replaces it on activation.
- **Market status is data-driven:** clock (IST, with pre-open state) cross-checked against
  data freshness — an exchange holiday or halt shows as such without any holiday calendar.
- Timestamps: feed stamped in UTC (tz-aware); the app displays ages correctly on any
  device; chart axes render **IST** like TradingView.

---

## 5. Operating the app

### 5.1 Tabs

- **📡 Live** — current setups. Badges per card: side, W/M (reversal at origin), A+
  (S/R confluence), **⭐ R:R** (passed the context gate + its reward:risk), HTF ✓
  (higher-timeframe agreement). "⚠ no mountain/valley" = plain-zone entry.
- **🤖 Agent** — the paper trader (below).
- **📜 History** — three sub-tabs: **Paper trades** (the agent's ledger), **Real trades**
  (future, locked), **Backtest** (the interactive 11-yr explorer) + the ❓ R explainer.
- **✅ Legs** — every scanned symbol's current leg; tap to inspect the fib on a chart;
  ✏️ lets you correct a leg (corrections export from Settings for tuning).
- **⚙ Settings** — timeframe/method/exit/trigger selectors (defaults are the validated
  ones), setup-filter modes, indices toggle, "Clear cache & update" (forces the newest
  app version), leg-correction export.
- The **chart** opens from any symbol tap (candles + zigzag + fib levels, IST axis),
  collapses (▾/✕), and doesn't follow you across tabs.

### 5.2 The paper agent

1. Enter **Capital (₹)** and pick a **Risk/trade** plan — each button shows its backtest
   expectation *and* consequence (0.5% ≈ 8%/yr, dips ~−13% … 2% ≈ 30%/yr, dips ~−44%).
2. **▶ Start.** The status pill (top of the panel) flips to *running*. From then on every
   ⭐ Best trade that **enters and closes after this moment** books to your ledger,
   compounding your equity (DOTM cap and costs modelled).
3. **⏸ Pause** freezes accrual; **⏹ Stop & reset** clears the run; **＋ Add** injects funds.
4. **☁️ Google sync** — one tap per device, same Google account everywhere: the agent
   (start date, capital, risk, funds) follows you automatically (stored in a hidden
   app-folder of *your* Drive). Only real actions (▶⏸⏹/funds/risk) create a new "newest"
   state — a device merely connecting can never overwrite another.
5. The report line shows the market's mood (regime + VIX), equity, trade count, worst dip,
   and the **pipeline** (how many ⭐ setups are being watched / in trade right now).

### 5.3 The backtest explorer (History → 🧪 Backtest)

Pick **Timeframe × Exit × Filter × Year-range** and read: net R, trades, win%, compound
multiple at 1% risk, CAGR, worst dip, and a per-year table. **↺ Default** returns to the
validated combo. Explorer choices are sandboxed — they never change what the scanner or
agent actually does. Use it to *see* the evidence (e.g., select 45m and watch it lose).

### 5.4 Reading freshness

- Header: `updated Xm ago` — during market hours ≤ ~20m = healthy. The ⟳ button re-fetches
  immediately (the app also auto-fetches every 60s). Data itself is ~15-min delayed
  (yfinance) until Fyers.
- Market line: `● Market open · sideways ✓ · VIX calm ✓` — the live context verdict.

### 5.5 Alerts

Telegram alerts fire **only for ⭐ Best setups**. To enable: create a bot (BotFather),
add `TELEGRAM_TOKEN` and `TELEGRAM_CHAT` as GitHub repo secrets (Settings → Secrets →
Actions). No code changes needed.

---

## 6. Nuances someone will eventually ask about

- **"Why so few trades?"** By design. 2H legs form over days; the ⭐ gate rejects ~⅔ of
  candidates. Waiting IS the edge. (§3.4 expectations.)
- **"Recent scanner trades" vs the paper ledger:** the former is a rolling ~60-day replay
  (it slides and forgets); the ledger is permanent and starts at your ▶.
- **"Live setups look unchanged"** — setups persist for hours/days while price approaches
  the zone; that's normal. New ⭐ setups appear when a leg completes and pulls back.
- **The watchlist R:R chip reads 2.7 on most cards** — that's the standard geometry of a
  0.5-entry/0.786-stop/1.272-target; per-trade R:R differentiates once actual 5m fills happen.
- **Sync rule of thumb:** press buttons on any device; every other device catches up within
  ~a minute (open) or on next open. If the pill says *tap ☁️ to reconnect*, tap it (browsers
  require a tap for the Google window).
- **"Clear cache & update"** refreshes the app's *code* (and resets settings to validated
  defaults); it does not touch your agent or ledger.
- **Backtest files** (`docs/backtest_*.json`) are pre-baked from the private 11-year
  dataset. To regenerate (new data, rule changes): double-click **`generate_backtests.bat`**
  on the data PC (~2–3h, publishes automatically).
- **The repo is public; the wallet is not.** Signals and backtests are public files; your
  capital, ledger and settings live only in your browser + your Drive.

---

## 7. Runbook (maintenance)

| Task | How |
|---|---|
| Force a scan now | GitHub → Actions → "fib-leg scan" → Run workflow (or `gh workflow run "fib-leg scan"`) |
| Check scanner health | GitHub → Actions: one session-loop run should be `in_progress` during market hours |
| Feed stuck > 30m in-session | Re-kick deploy: `gh api -X POST repos/salil1gautam1-collab/fib-leg/pages/builds` |
| Regenerate backtests | `generate_backtests.bat` (repo root, on the PC holding `fibleg/data/Stocks_data`) |
| Regenerate signals locally | `python scan.py --source yf` |
| Update the app | edit `docs/` → bump `?v=` in `index.html` + `sw.js` (cache-bust) → commit → push |
| Full validation suite | `wf_*.py` harnesses (each `python wf_X.py "fibleg/data/Stocks_data"`) |

**Repo map:** `fibleg/` engine (leg logic in `strategy/book_impulse.py` + `strategy/fib_leg.py`,
context gates in `context.py`) · `scan.py` cloud job · `docs/` the PWA + published data ·
`gen_backtest_all.py` + `generate_backtests.bat` backtest baker · `wf_*.py` research
harnesses · `.github/workflows/scan.yml` the session loop.

---

## 8. Security model

- Site + repo: public, contains no personal data.
- Agent data: your browser + your Google Drive app-folder (Google password/2FA).
- Broker/API keys: `~/.fibleg/*.json` on the owner's PC or GitHub encrypted secrets. Never
  in chat, code, or the site.
- The app **cannot** trade or move money — there is no order code in it.

---

## 9. Fyers activation checklist (next milestone)

1. Save credentials to `~/.fibleg/fyers.json` (owner's PC — never share in chat).
2. Wire `scan.py --source fyers` (live 5m feed) — the paper rehearsal becomes a true
   forward test on the wide universe.
3. Probe intraday history depth (if deeper than 2015, regenerate backtests — the 15-yr
   button auto-extends).
4. Verify real DOTM option spreads (the one cost assumption not yet market-checked).

## 10. Roadmap (owner-gated)

**Paper (now)** → 6–12 months of live-feed rehearsal matching the backtest →
**Semi-auto**: alert with a Confirm button, order only on the owner's tap →
**Full-auto** (only after the record earns it): kill-switches, position caps, daily-loss
stop, SEBI retail-algo compliance via the broker API. Each rung is a deliberate owner
decision; nothing self-upgrades.

---

*Generated from the validated state of the system (July 2026). The backtest evidence and
design decisions above are reproducible from the `wf_*.py` harnesses in this repo.*
