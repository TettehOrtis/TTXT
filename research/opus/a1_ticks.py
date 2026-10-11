"""Read-only: compare MT5 bar 'spread' with tick-level spreads (no trading calls)."""
import os, numpy as np, pandas as pd, MetaTrader5 as mt5
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
ok = mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("MT5_LOGIN")), password=os.getenv("MT5_PASSWORD"), server=os.getenv("MT5_SERVER"))
print("init", ok)
for s, pt in [("XAUUSDm",0.001),("US30m",0.01),("EURUSDm",0.00001)]:
    t = mt5.copy_ticks_range(s, datetime(2026,9,22,tzinfo=timezone.utc), datetime(2026,9,25,tzinfo=timezone.utc), mt5.COPY_TICKS_ALL)
    if t is None or len(t)==0: print(s,"no ticks", mt5.last_error()); continue
    t = pd.DataFrame(t); t["time"]=pd.to_datetime(t.time_msc, unit="ms"); t["sp"]=(t.ask-t.bid)/pt
    t["m"]=t.time.dt.floor("min")
    g=t.groupby("m").agg(sp_min=("sp","min"),sp_mean=("sp","mean"),sp_max=("sp","max"),sp_open=("sp","first"),sp_last=("sp","last"),n=("sp","size"),bid_hi=("bid","max"),bid_lo=("bid","min"),bid_o=("bid","first"),bid_c=("bid","last"),ask_hi=("ask","max"),ask_lo=("ask","min"))
    r = mt5.copy_rates_range(s, mt5.TIMEFRAME_M1, datetime(2026,9,22,tzinfo=timezone.utc), datetime(2026,9,25,tzinfo=timezone.utc))
    r = pd.DataFrame(r); r["m"]=pd.to_datetime(r.time,unit="s")
    j = g.join(r.set_index("m")[["open","high","low","close","spread"]], how="inner")
    print(s, len(t),"ticks", len(j),"bars")
    for c in ["sp_min","sp_mean","sp_max","sp_open","sp_last"]:
        print(f"  bar.spread == {c}: {(np.abs(j.spread-j[c].round())<=0.5).mean():.3f}  corr {np.corrcoef(j.spread,j[c])[0,1]:.3f}  med {j[c].median():.1f}")
    print("  bar.spread median", j.spread.median(), " high==bid_hi", np.isclose(j.high,j.bid_hi).mean().round(3), " low==bid_lo", np.isclose(j.low,j.bid_lo).mean().round(3))
    # ask_high vs bid_high+bar spread
    print("  (ask_hi - (bid_hi + spread*pt))/pt median, p90:", ((j.ask_hi-(j.high+j.spread*pt))/pt).median().round(2), ((j.ask_hi-(j.high+j.spread*pt))/pt).quantile(.9).round(2))
    print("  ((ask_lo) - (bid_lo + spread*pt))/pt median, p90:", ((j.ask_lo-(j.low+j.spread*pt))/pt).median().round(2), ((j.ask_lo-(j.low+j.spread*pt))/pt).quantile(.9).round(2))
mt5.shutdown()
