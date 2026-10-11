import sys; sys.path.insert(0,"research")
import numpy as np, pandas as pd
from lib import load, pip_size
for s in ["XAUUSD","US30","EURUSD"]:
    d=load(s,"2021-01-01")
    d["yr"]=d.time.dt.year
    print(s, len(d), d.time.iloc[0], d.time.iloc[-1])
    print(" median spr units by yr", d.groupby("yr").spr.median().round(2).to_dict())
    print(" mean spr by yr", d.groupby("yr").spr.mean().round(2).to_dict())
    rng=(d.high-d.low)/pip_size(s)
    print(" median M1 range by yr", rng.groupby(d.yr).median().round(2).to_dict())
    g=d[d.time>="2025-01-01"].groupby(d.time.dt.hour).agg(tv=("tick_volume","mean"),spr=("spr","median"))
    print(g.T.round(1).to_string())
    # gaps
    dt=d.time.diff().dt.total_seconds()/60
    print(" gaps>1min:",(dt>1).mean().round(4), " dup:",d.time.duplicated().sum())
    # open vs prev close
    oc=(d.open-d.close.shift())/pip_size(s)
    print(" |open-prevclose| median, p99:", oc.abs().median(), oc.abs().quantile(.99))
