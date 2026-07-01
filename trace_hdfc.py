"""Trace the HDFCBANK 2H short: actual entry fill, actual SL used, and the exact 5m
candle that hit the stop — to see whether price really reached the 0.786 SL."""
import scan
base, dual, base_min = scan._fetch("yf", ["HDFCBANK.NS"], 60)
bars = base["HDFCBANK.NS"]
c = scan._conf_cfg("full")            # matches app default: full exit
eng, _ = scan._run_tf({"HDFCBANK.NS": bars}, True, 120, c, "book382", base_min, 5)
e = eng["HDFCBANK.NS"]

for t in e.trades:
    lg = t.leg
    print(f"TRADE {t.side.value} leg {round(lg.start_price,2)}->{round(lg.end_price,2)}")
    print(f"  entry_fill = {round(t.entry,2)}   SL(actual) = {round(t.sl,2)}   "
          f"exit = {t.exit_reason}   pts = {t.realized_points}")
    print(f"  0.786 of THIS leg = {round(lg.retracement(0.786),2)}   entry_ts={t.entry_ts}")
    # find the 5m candle that hit the stop (short: close >= sl)
    if t.exit_reason == "sl" and t.entry_ts is not None:
        after = [b for b in bars if b.ts >= t.entry_ts]
        for b in after[:400]:
            hit = b.close >= t.sl if t.side.value == "short" else b.close <= t.sl
            if hit:
                print(f"  >>> STOP candle {b.ts}  O={b.open} H={b.high} L={b.low} C={b.close}  "
                      f"(close {'>=' if t.side.value=='short' else '<='} SL {round(t.sl,2)})")
                break
    print()
