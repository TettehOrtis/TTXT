"""Fetch M1 history (bid OHLC + per-bar spread) from the ZERO account into data_zero/, and profile its spreads."""
import os, MetaTrader5 as mt5, pandas as pd, numpy as np
from dotenv import load_dotenv
load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
SYMS = ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","NZDUSD","EURGBP","EURJPY","XAUUSD"]
pip = lambda s: 0.1 if s == "XAUUSD" else (0.01 if "JPY" in s else 0.0001)
rows = []
for s in SYMS:
    z = s + "z"; mt5.symbol_select(z, True); i = mt5.symbol_info(z)
    r = mt5.copy_rates_from_pos(z, mt5.TIMEFRAME_M1, 0, 6_000_000)
    if r is None: print(s, "no data", mt5.last_error()); continue
    d = pd.DataFrame(r); d["time"] = pd.to_datetime(d["time"], unit="s")
    d = d.drop(columns=["real_volume"]).drop_duplicates("time").sort_values("time")
    d.to_parquet(f"data_zero/{s}_M1.parquet", index=False)
    d["sp"] = d.spread * i.point / pip(s)
    h = d.time.dt.hour; act = d[(h >= 7) & (h < 20) & (d.time >= "2025-01-01")]
    yr = d[d.time >= "2021-01-01"].groupby(d.time.dt.year).sp.mean().round(2).to_dict()
    rows.append(dict(sym=s, bars=len(d), first=str(d.time.iloc[0].date()), zero_pct_0720=round((act.sp == 0).mean()*100, 1),
                     mean_sp_0720=round(act.sp.mean(), 3), p90=round(act.sp.quantile(.9), 2), mean_by_year=yr))
mt5.shutdown()
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 120)
print("ZERO account spreads (pips; gold in $0.10 units), 07-20 UTC since 2025 (bar spread = min in the minute):")
print(pd.DataFrame(rows).to_string(index=False))
