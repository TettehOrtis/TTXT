import os, numpy as np, pandas as pd, MetaTrader5 as mt5
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("MT5_LOGIN")), password=os.getenv("MT5_PASSWORD"), server=os.getenv("MT5_SERVER"))
for s in ["XAUUSDm","US30m","EURUSDm"]:
    pt = mt5.symbol_info(s).point
    for a,b in [((2026,9,1),(2026,9,25)),((2025,3,3),(2025,3,8))]:
        t = mt5.copy_ticks_range(s, datetime(*a,tzinfo=timezone.utc), datetime(*b,tzinfo=timezone.utc), mt5.COPY_TICKS_ALL)
        if t is None or len(t)==0: print(s,a,"no ticks"); continue
        t = pd.DataFrame(t); t["time"]=pd.to_datetime(t.time_msc, unit="ms"); t["sp"]=(t.ask-t.bid)/pt
        t["m"]=t.time.dt.floor("min"); h=t.time.dt.hour
        g=t.groupby("m").agg(mn=("sp","min"),me=("sp","mean"),first=("sp","first"))
        tw = t.sp.mean()  # tick weighted
        act=(h>=7)&(h<20)
        print(f"{s} {a} pt={pt} ticks={len(t)} bar-min spread mean={g.mn.mean():.1f} per-minute mean={g.me.mean():.1f} first-tick-of-min mean={g['first'].mean():.1f} tick-wtd={tw:.1f}  | active-hrs tick-wtd {t.sp[act].mean():.1f} med {t.sp[act].median():.1f}")
mt5.shutdown()
