import json, scan
from fibleg.config import StrategyConfig
from fibleg.data import feeds
DIR="C:/Salil Claude/fib-leg/fibleg/data/Stocks_data"
def cfg():
    c=StrategyConfig(); c.entry_ratio,c.sl_ratio=0.5,0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.require_mw=True; c.entry_dependent_targets=True; c.trail_sl_after_targets=True; c.sl_lock_at_t1=True
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3); return c
stocks=["TCS","HDFCBANK","RELIANCE","INFY","ICICIBANK","SBIN"]
out=[]
for tk in stocks:
    b1=feeds.csv_dir_series(DIR,tk); b2=feeds.resample(b1,120)
    eng,_=scan._run_tf({tk:b1},True,120,cfg(),"book382",1,5); e=eng[tk]
    tr=e.trades
    if not tr: continue
    for frac in (0.12,0.35,0.58,0.82):     # 4 trades spread across the 11 years
        t=tr[min(len(tr)-1,int(frac*len(tr)))]; lg=t.leg
        s=max(0,lg.start_index-8); en=min(len(b2),lg.end_index+28)
        bars=[{"time":int(b2[i].ts.timestamp()),"open":round(b2[i].open,2),"high":round(b2[i].high,2),
               "low":round(b2[i].low,2),"close":round(b2[i].close,2)} for i in range(s,en)]
        out.append({"title":f"{tk} {t.side.value.upper()}  {lg.start_ts.date()}",
          "bars":bars,
          "legStart":{"time":int(b2[lg.start_index].ts.timestamp()),"price":round(lg.start_price,2)},
          "legEnd":{"time":int(b2[lg.end_index].ts.timestamp()),"price":round(lg.end_price,2)},
          "zoneHi":round(lg.retracement(0.5),2),"zoneLo":round(lg.retracement(0.618),2),
          "sl":round(lg.retracement(0.786),2),"entry":round(t.entry,2),"side":t.side.value})
open("legchart.json","w").write(json.dumps(out))
print("wrote",len(out),"charts")
