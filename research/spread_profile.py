import pandas as pd, numpy as np
PIP = {"JPY":0.01}
def pip(sym): return 0.01 if "JPY" in sym else 0.0001
pt = lambda sym: 0.001 if "JPY" in sym else 0.00001
syms = ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","NZDUSD","EURGBP","EURJPY","EURCHF","AUDJPY","GBPJPY","EURAUD"]
rows=[]
for s in syms:
    d = pd.read_parquet(f"data/{s}_M1.parquet")
    d["sp_pips"] = d.spread * pt(s) / pip(s)      # points -> pips
    d["yr"] = d.time.dt.year; d["hr"] = d.time.dt.hour
    rows.append((s, d.groupby("yr").sp_pips.median().round(2).to_dict()))
print("MEDIAN SPREAD (pips) BY YEAR")
for s, r in rows: print(f"{s:7}", r)
d = pd.read_parquet("data/EURUSD_M1.parquet"); d = d[d.time >= "2025-09-01"]
d["sp"] = d.spread*0.00001/0.0001; d["hr"]=d.time.dt.hour
d["rng"] = (d.high-d.low)/0.0001
g = d.groupby("hr").agg(sp_med=("sp","median"), sp_p90=("sp", lambda x: x.quantile(.9)), rng_mean=("rng","mean"), vol=("tick_volume","mean")).round(2)
print("\nEURUSD last-12mo by UTC hour (spread pips, M1 range pips, tick vol)"); print(g.to_string())
