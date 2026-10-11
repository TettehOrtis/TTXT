"""Test the overnight-style trades over 2021-2026 on ZERO history with REAL commission.
Entry: every 2 hours (all day), direction = 4-hour trend (close now vs 240 min ago); also random direction as control.
Exit: take-profit / stop-loss grid (pips), max hold 24h, SL-first if both hit inside one minute (pessimistic).
Select TP/SL on 2021-2023, check once on 2024-2026 (unseen). Money shown per 0.10 lot."""
import sys, os
import numpy as np, pandas as pd
os.environ["DATA_DIR"] = "data_zero"
sys.path.insert(0, "research")
from lib import load, run_sim, pip_size

COMM_RT_PIPS = dict(EURUSD=0.50, GBPUSD=0.63, USDJPY=0.79, AUDUSD=0.60)
USD_PER_PIP_01 = dict(EURUSD=1.0, GBPUSD=1.0, AUDUSD=1.0, USDJPY=0.64)     # per 0.10 lot
TPS, SLS = (3, 5, 8, 12, 20, 40), (5, 10, 20, 30)
SLIP = 0.2                                                                  # pips per fill (stop fills slipped 0.4 on Sunday)
rows = []
for sym in COMM_RT_PIPS:
    d = load(sym, "2021-01-01"); p = pip_size(sym)
    d["spr"] = d["spr"].clip(lower=0)
    idx = np.where((d.time.dt.minute == 0) & (d.time.dt.hour % 2 == 0) & (d.time.dt.dayofweek < 5) &
                   ~((d.time.dt.dayofweek == 4) & (d.time.dt.hour >= 18)))[0]
    idx = idx[(idx > 240) & (idx < len(d) - 1500)]
    trend = np.sign(d.close.values[idx] - d.close.values[idx - 240]).astype(int)
    keep = trend != 0; idx, trend = idx[keep], trend[keep]
    rnd = np.random.default_rng(7).choice([-1, 1], len(idx))
    yrs = d.time.dt.year.values[idx]
    for rule, side in (("trend", trend), ("random", rnd)):
        for tp in TPS:
            for sl in SLS:
                pnl = np.full(len(idx), np.nan)
                for s in (1, -1):
                    m = side == s
                    a, _, _ = run_sim(d, sym, idx[m], s, tp, sl, 1440, SLIP, 0.0)
                    pnl[m] = a
                net = pnl - COMM_RT_PIPS[sym]
                for per, msk in (("2021-23", yrs <= 2023), ("2024-26", yrs >= 2024)):
                    x = net[msk & ~np.isnan(net)]
                    rows.append(dict(sym=sym, rule=rule, tp=tp, sl=sl, period=per, n=len(x), win=np.mean(x > 0) * 100,
                                     usd_trade=x.mean() * USD_PER_PIP_01[sym], pips=x.mean()))
    print("done", sym, flush=True)
r = pd.DataFrame(rows)
r.to_csv("research/exit_grid.csv", index=False)
pd.set_option("display.width", 220)
tr = r[r.period == "2021-23"].set_index(["sym", "rule", "tp", "sl"]); te = r[r.period == "2024-26"].set_index(["sym", "rule", "tp", "sl"])
j = tr[["n", "win", "usd_trade"]].join(te[["n", "win", "usd_trade"]], lsuffix="_2123", rsuffix="_2426")
print("\nBEST TP/SL per pair+rule chosen on 2021-23 -> result on unseen 2024-26 ($ per trade at 0.10 lot, after commission):")
best = j.reset_index().sort_values("usd_trade_2123", ascending=False).groupby(["sym", "rule"]).head(1)
print(best.round(2).to_string(index=False))
print("\nAverage over ALL TP/SL settings ($/trade, 0.10 lot):")
print(j.groupby(level=["sym", "rule"])[["usd_trade_2123", "usd_trade_2426"]].mean().round(2))
print("\nEvery setting, trend rule, 2024-26 unseen ($/trade at 0.10 lot):")
print(te.xs("trend", level="rule")["usd_trade"].unstack("sl").round(2))
