"""Cost model on Nifty-50 sample, 2H. For each trade: gross_R and risk_frac
(=|entry-sl|/entry). Costs are charged as a % of NOTIONAL, converted to R via
cost_R = cost_pct / risk_frac. NSE F&O rates (2025):
  txn (futures round trip): brokerage ~0.012 + STT 0.02(sell) + exch 0.0038 +
      SEBI 0.0002 + stamp 0.002 + GST ~0.003  ≈ 0.042% of notional
  slippage (liquid futures, incl stop gaps): ~0.06% round trip
  => hedge-OFF cost ≈ 0.10% of notional
  hedge-ON adds the protective option premium (paid every trade, lost on winners):
      tested at 0.3% / 0.6% / 1.0% of notional.
Compares exit ladders incl. extended fib/AB=CD targets."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
N50 = {"ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK","BAJAJ-AUTO","BAJFINANCE",
  "BAJAJFINSV","BEL","BHARTIARTL","BPCL","BRITANNIA","CIPLA","COALINDIA","DRREDDY","EICHERMOT",
  "GRASIM","HCLTECH","HDFCBANK","HDFCLIFE","HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK",
  "INDUSINDBK","INFY","ITC","JSWSTEEL","KOTAKBANK","LT","LTIM","MM","MARUTI","NESTLEIND","NTPC",
  "ONGC","POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN","SUNPHARMA","TATACONSUM","TATASTEEL",
  "TCS","TECHM","TITAN","TRENT","ULTRACEMCO","WIPRO","JIOFIN","TMPV","TMCV","HAL","VBL"}
avail = set(feeds.csv_dir_symbols(DIR))
tickers = sorted(N50 & avail)
print(f"Nifty-50 sample: {len(tickers)} of {len(N50)} available. 2H, book382, pin+close respect.\n", flush=True)

def base():
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry = True; c.nested_entry = True
    c.zone_respect = True; c.zone_frac = 0.05; c.zone_pin_respect = True
    return c
def L(tgts, fr, trail=True, entry_dep=False):
    c = base(); c.targets=tuple(tgts); c.target_fractions=tuple(fr)
    c.move_sl_to_be_after_tp1=True; c.trail_sl_after_targets=trail
    c.entry_dependent_targets=entry_dep; return c
EXITS = {
 "square@0.95        ": L((0.95,), (1.0,), False),
 "3T .95/1.27/1.62   ": L((0.95,1.272,1.618),(1/3,)*3),
 "harmonic 1.27/1.62/2.62/3.62": L((1.272,1.618,2.618,3.618),(0.25,)*4),
 "entry-dep 1/d (harmonic)": L((0.95,1.272,1.618),(1/3,)*3, entry_dep=True),
}
rows = {k: [] for k in EXITS}
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars = feeds.csv_dir_series(DIR, tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    for k,c in EXITS.items():
        eng,_ = scan._run_tf({tk:bars}, True, 120, c, "book382", 1, 5)
        e=eng[tk]
        for t in e.trades:
            if t.leg is None or not t.entry: continue
            orig_sl = t.leg.retracement(0.786)          # ORIGINAL risk (t.sl may be moved to BE/trail)
            rf = abs(t.entry - orig_sl)/t.entry
            if rf>0: rows[k].append((t.realized_r, rf))
    if (i+1)%15==0 or i+1==len(tickers): print(f"  [{i+1}/{len(tickers)}] ({time.time()-t0:.0f}s)",flush=True)

def net(R, cost_pct):
    tot=0.0
    for gr,rf in R: tot += gr - (cost_pct/100.0)/rf   # cost_pct is %, rf is a fraction
    return tot
HEDGE_OFF=0.10
print(f"\n{'exit ladder':26s} {'trades':>6s} {'win%':>5s} {'avgRiskFrac':>11s} {'GROSS':>9s} "
      f"{'net(nohedge)':>12s} {'hedge0.3%':>10s} {'hedge0.6%':>10s} {'hedge1.0%':>10s}")
for k,R in rows.items():
    if not R: print(f"{k:26s} none"); continue
    gr=sum(r[0] for r in R); w=sum(1 for r in R if r[0]>0); arf=sum(r[1] for r in R)/len(R)
    print(f"{k:26s} {len(R):6d} {round(100*w/len(R)):4d}% {arf*100:10.2f}% {gr:+9.1f} "
          f"{net(R,HEDGE_OFF):+12.1f} {net(R,HEDGE_OFF+0.3):+10.1f} {net(R,HEDGE_OFF+0.6):+10.1f} {net(R,HEDGE_OFF+1.0):+10.1f}")
print("\n(net columns = R after costs. hedgeX% adds option premium X% of notional per trade.)")
