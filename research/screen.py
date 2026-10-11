"""Fetch candidate instruments and rank by (typical 15-min move) / (spread)."""
import os, json, numpy as np, pandas as pd, MetaTrader5 as mt5
from dotenv import load_dotenv
load_dotenv()
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("MT5_LOGIN")), password=os.getenv("MT5_PASSWORD"), server=os.getenv("MT5_SERVER"))
CAND = ["XAUUSD","XAGUSD","BTCUSD","ETHUSD","USOIL","UKOIL","US30","USTEC","US500","DE30","JP225","UK100","EURUSD","GBPUSD","USDJPY","GBPJPY","XRPUSD","SOLUSD","LTCUSD","BNBUSD"]
meta = {}
rows = []
for s in CAND:
    b = s + "m"
    if not mt5.symbol_select(b, True): print("skip", b); continue
    i = mt5.symbol_info(b)
    meta[s] = dict(point=i.point, digits=i.digits, contract=i.trade_contract_size, tick_size=i.trade_tick_size,
                   tick_value=i.trade_tick_value, vmin=i.volume_min, vstep=i.volume_step, vmax=i.volume_max, calc=i.trade_calc_mode)
    r = mt5.copy_rates_from_pos(b, mt5.TIMEFRAME_M1, 0, 700_000)   # ~2 years+
    if r is None or len(r) < 1000: print("nodata", b); continue
    d = pd.DataFrame(r); d["time"] = pd.to_datetime(d["time"], unit="s")
    d = d.drop(columns=["real_volume"]).drop_duplicates("time").sort_values("time")
    d.to_parquet(f"data/{s}_M1.parquet", index=False)
    d = d[d.time >= "2025-06-01"]
    d["spr"] = d.spread * i.point
    h = d.time.dt.hour
    act = d[(h >= 7) & (h < 20)]
    mv15 = (d.close.shift(-15) - d.close).abs()[act.index].median()
    mv15m = (d.close.shift(-15) - d.close).abs()[act.index].mean()
    sp = act.spr.median()
    rows.append((s, len(d), round(sp,5), round(mv15m,5), round(mv15m/sp,1), round(2*sp/mv15m*100,1), meta[s]["contract"], meta[s]["vmin"]))
mt5.shutdown()
json.dump(meta, open("data/meta.json","w"), indent=1)
df = pd.DataFrame(rows, columns=["sym","bars","med_spread","mean|15m move|","move/spread","cost%ofmove(2xspr)","contract","vmin"]).sort_values("move/spread", ascending=False)
print(df.to_string(index=False))
