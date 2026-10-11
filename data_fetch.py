"""Pull full M1 history (bid OHLC + per-bar spread) for liquid pairs into data/*.parquet."""
import os, sys
import MetaTrader5 as mt5, pandas as pd
from dotenv import load_dotenv
load_dotenv()

SYMS = ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","NZDUSD","EURGBP","EURJPY","EURCHF","AUDJPY","GBPJPY","EURAUD"]

def connect():
    ok = mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("MT5_LOGIN")),
                        password=os.getenv("MT5_PASSWORD"), server=os.getenv("MT5_SERVER"))
    if not ok: raise SystemExit(f"MT5 init failed: {mt5.last_error()}")

if __name__ == "__main__":
    connect()
    os.makedirs("data", exist_ok=True)
    for s in SYMS:
        b = s + "m"
        mt5.symbol_select(b, True)
        r = mt5.copy_rates_from_pos(b, mt5.TIMEFRAME_M1, 0, 6_000_000)
        if r is None or len(r) == 0:
            print(s, "no data", mt5.last_error()); continue
        d = pd.DataFrame(r); d["time"] = pd.to_datetime(d["time"], unit="s")
        d = d.drop(columns=["real_volume"]).drop_duplicates("time").sort_values("time")
        d.to_parquet(f"data/{s}_M1.parquet", index=False)
        print(f"{s}: {len(d):>8} bars {d.time.iloc[0]} -> {d.time.iloc[-1]}")
    mt5.shutdown()
