import os, sys, numpy as np, pandas as pd, MetaTrader5 as mt5
from dotenv import load_dotenv
load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("MT5_LOGIN")), password=os.getenv("MT5_PASSWORD"), server=os.getenv("MT5_SERVER"))
for s in sys.argv[1:]:
    b = s + "m"; mt5.symbol_select(b, True); i = mt5.symbol_info(b)
    r = mt5.copy_rates_from_pos(b, mt5.TIMEFRAME_M1, 0, 6_000_000)
    d = pd.DataFrame(r); d["time"] = pd.to_datetime(d["time"], unit="s")
    d = d.drop(columns=["real_volume"]).drop_duplicates("time").sort_values("time")
    d.to_parquet(f"data/{s}_M1.parquet", index=False)
    d["sp"] = d.spread * i.point; d["yr"] = d.time.dt.year
    print(f"{s}: {len(d)} bars {d.time.iloc[0]} -> {d.time.iloc[-1]} | median spread by yr:", d.groupby("yr").sp.median().round(3).to_dict())
mt5.shutdown()
