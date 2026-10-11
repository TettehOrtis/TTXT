import os, sys, pandas as pd
os.environ["DATA_DIR"] = "data_zero"
from scalper.broker import Broker
sys.path.insert(0, "research")
from lib import load
b = Broker(suffix="z", magic=660066, env="Z"); b.connect()
live = b.rates("USDJPYz", 6000).iloc[:-1].tail(4500)
ref = load("USDJPY", "2026-09-20")
for name, d in (("live", live), ("research", ref)):
    g = d.groupby(d.time.dt.floor("D")).agg(n=("close", "size"), h=("high", "max"), l=("low", "min"), c=("close", "last"), first=("time", "min"), last=("time", "max"))
    print(name); print(g.tail(6).to_string())
b.shutdown()
