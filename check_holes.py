import os, sys, time, pandas as pd, MetaTrader5 as mt5
from dotenv import load_dotenv; load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
for s in ["USDJPY", "EURUSD"]:
    z = s + "z"; mt5.symbol_select(z, True)
    for attempt in range(3):                      # let the terminal finish syncing history
        r = mt5.copy_rates_from_pos(z, mt5.TIMEFRAME_M1, 0, 6_000_000); time.sleep(3)
    d = pd.DataFrame(r); d["time"] = pd.to_datetime(d["time"], unit="s")
    d = d.drop(columns=["real_volume"]).drop_duplicates("time").sort_values("time")
    d.to_parquet(f"data_zero/{s}_M1.parquet", index=False)
    per = d.groupby(d.time.dt.floor("D")).size()
    wd = per[(per.index.dayofweek < 4) & (per.index >= "2021-01-04")]            # Mon-Thu should be ~1435 bars
    all_days = pd.bdate_range("2021-01-04", d.time.iloc[-1].normalize() - pd.Timedelta(days=1))
    all_days = all_days[all_days.dayofweek < 4]
    missing = [x.date() for x in all_days if x not in per.index]
    thin = wd[wd < 1300]
    print(f"{s}: last bar {d.time.iloc[-1]} | Mon-Thu days missing entirely: {len(missing)} {missing[:8]} | days with <1300 bars: {len(thin)}")
    print("   thin examples:", {str(k.date()): int(v) for k, v in thin.head(10).items()})
mt5.shutdown()
