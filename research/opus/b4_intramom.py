"""Intraday momentum (Gao-Han-Li-Zhou 2018): the first half-hour return (prev 16:00 ET close -> 10:00 ET) and the
second-to-last half-hour return predict the last half-hour (15:30->16:00 ET). DST-aware via America/New_York.
Trials: symbols x signal variant {r1, r12, r1&r12 agree} x side-split. Select on 2021-24, holdout 2025-26.
Costs: bar spread at entry/exit (bar spread ~= mean tick spread on Exness, verified) + 0.25*median spread slip per fill."""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "research")
from lib import load, pip_size

rows = []
for sym in ["US30", "USTEC", "US500", "DE30", "XAUUSD"]:
    try:
        d = load(sym)
    except Exception as e:
        print(sym, e); continue
    p = pip_size(sym) if sym != "US500" else 0.1
    et = d.time.dt.tz_localize("UTC").dt.tz_convert("America/New_York")
    d["et_date"] = et.dt.date.values
    d["etm"] = (et.dt.hour * 60 + et.dt.minute).values
    op, cl, spr = d.open.values, d.close.values, d.spr.values * (pip_size(sym) / p if sym == "US500" else 1)
    slip = 0.25 * np.median(spr)
    def at(m):  # index of first bar at/after ET minute m per date (must be within 3 min)
        x = d[(d.etm >= m) & (d.etm <= m + 3)].groupby("et_date").head(1)
        return pd.Series(x.index.values, index=x.et_date.values)
    i0930, i1000, i1530, i1600 = at(9 * 60 + 30), at(10 * 60), at(15 * 60 + 30), at(15 * 60 + 58)
    i1500 = at(15 * 60)
    close16 = i1600  # bar starting 15:58 -> its close ~ 15:59
    df = pd.DataFrame({"a": i0930, "b": i1000, "c": i1500, "e": i1530, "x": i1600}).dropna().astype(int)
    df["prev_close_i"] = df.x.shift(1)
    df = df.dropna().astype(int)
    r1 = (op[df.b.values] - cl[df.prev_close_i.values]) / p
    r12 = (op[df.e.values] - op[df.c.values]) / p
    rl = (cl[df.x.values] - op[df.e.values]) / p
    yr = pd.to_datetime(df.index).year
    for var in ("r1", "r12", "both"):
        if var == "r1": s = np.sign(r1)
        elif var == "r12": s = np.sign(r12)
        else: s = np.where(np.sign(r1) == np.sign(r12), np.sign(r1), 0)
        for side in (1, -1):
            m = s == side
            cost = np.where(side == 1, spr[df.e.values], spr[df.x.values]) + 2 * slip
            net = side * rl - cost
            for per, pm in (("tr", yr <= 2024), ("te", yr >= 2025)):
                k = m & pm
                x = net[k]
                rows.append(dict(sym=sym, var=var, side=side, per=per, n=k.sum(), net=x.mean(), gross=(side * rl[k]).mean(),
                                 cost=cost[k].mean(), t=x.mean() / (x.std(ddof=1) / np.sqrt(len(x))),
                                 uncond_gross=(side * rl[pm]).mean()))
g = pd.DataFrame(rows)
w = g.pivot_table(index=["sym", "var", "side"], columns="per", values=["n", "gross", "cost", "net", "t", "uncond_gross"])
pd.set_option("display.width", 250)
print("trials:", len(w), "(units: index points / gold $0.10)")
print(w.round(2).to_string())
