"""Trace the RELIANCE 4H long (leg 1310->1473) to see EXACTLY what armed it:
the arm candle's close vs the zone top, and the 5m closes around it."""
import scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

base, dual, base_min = scan._fetch("yf", ["RELIANCE.NS"], 60)
bars = base["RELIANCE.NS"]
print(f"RELIANCE 5m bars: {len(bars)}  {bars[0].ts}  ->  {bars[-1].ts}\n")

c = scan._conf_cfg("full")   # zone_entry + zone_respect + nested, book382, 0.786 SL
eng, _ = scan._run_tf({"RELIANCE.NS": bars}, True, 240, c, "book382", base_min, 5)
e = eng["RELIANCE.NS"]

print("ARM / FILL events (zone_respect on):")
for row in e._dbg:
    if row[0] == "ARM":
        _, ts, close, zhi, zlo, mtn = row
        print(f"  ARM  {ts}  close={close}  z_hi={zhi} z_lo={zlo} mtn={mtn}  "
              f"-> closed above zone top? {close > zhi}")
    else:
        _, ts, trig, ls, le = row
        print(f"  FILL {ts}  entry={trig}  leg {ls}->{le}")

print("\nTrades:")
for t in e.trades:
    lg = t.leg
    print(f"  {t.side.value} leg {round(lg.start_price,1)}->{round(lg.end_price,1)} "
          f"entry_fill={round(t.entry,2)} sl={round(t.sl,2)} exit={t.exit_reason} "
          f"pts={t.realized_points} entry_ts={t.entry_ts}")
