"""Time-of-day drift in gold: mean hourly return ($/oz) by UTC hour and by year. Exploratory (counts as trials when turned into rules)."""
import sys; sys.path.insert(0,"research")
import numpy as np, pandas as pd
from lib import load
sym = sys.argv[1] if len(sys.argv)>1 else "XAUUSD"
d = load(sym)
d = d.set_index("time")
h = d.close.resample("1h").last().dropna()
o = d.open.resample("1h").first().dropna()
r = (h - o).dropna()   # within-hour bid move, $/unit price
rr = pd.DataFrame({"r": r, "yr": r.index.year, "hr": r.index.hour})
# normalise by price level so years comparable (bp)
px = d.close.resample("1h").last().reindex(r.index)
rr["bp"] = r / px * 1e4
t = rr.pivot_table(index="hr", columns="yr", values="bp", aggfunc="mean").round(2)
t["tr21_24"] = rr[rr.yr<=2024].groupby("hr").bp.mean().round(2)
t["t_tr"] = (rr[rr.yr<=2024].groupby("hr").bp.mean()/(rr[rr.yr<=2024].groupby("hr").bp.std()/np.sqrt(rr[rr.yr<=2024].groupby("hr").size()))).round(1)
t["te25_26"] = rr[rr.yr>=2025].groupby("hr").bp.mean().round(2)
t["t_te"] = (rr[rr.yr>=2025].groupby("hr").bp.mean()/(rr[rr.yr>=2025].groupby("hr").bp.std()/np.sqrt(rr[rr.yr>=2025].groupby("hr").size()))).round(1)
pd.set_option("display.width",250)
print(sym, "mean within-hour return in bp by UTC hour")
print(t.to_string())
day = rr.groupby(rr.index.normalize()).bp.sum()
print("daily mean bp by yr", day.groupby(day.index.year).mean().round(2).to_dict())
