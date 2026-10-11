"""Timed trades (session seasonality). Enter at first bar >= HH:00 UTC, exit at close of bar before HH+H.
Grid: entry hour 0..23 x hold {1,2,3,4,6}h x side {+1,-1}. One trade per day -> daily-independent samples.
Costs: entry at ask (bid + bar spread*SPRM) + slip, exit at bid - slip (long); mirror for short.
Select on 2021-2024 only. Holdout 2025-2026 reported for ALL cells but only the pre-registered pick counts.
Drift-neutral: excess = trade return - (same-year mean return per hour * H) in the trade direction.
Usage: python b2_timed.py SYM [sprm] [slip_frac]"""
import sys
import numpy as np, pandas as pd
from scipy.stats import norm
sys.path.insert(0, "research")
from lib import load, pip_size

sym = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
SPRM = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
SLF = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25
d = load(sym)
p = pip_size(sym)
t = d.time
dow = t.dt.dayofweek.values
op, cl, spr = d.open.values, d.close.values, d.spr.values
slip = SLF * np.median(spr)
tv = t.values
date = t.dt.normalize().values
# bar index of first bar at/after each (day, hour)
key = pd.DataFrame({"date": date, "hr": t.dt.hour.values, "i": np.arange(len(d))})
first = key.groupby(["date", "hr"]).i.min()
# mean return per hour of each year (for drift neutralisation), in units
yr = t.dt.year.values
hours_per_year = pd.Series(1, index=pd.MultiIndex.from_frame(key[["date", "hr"]].drop_duplicates())).groupby(level=0).size().groupby(pd.to_datetime(first.index.get_level_values(0).unique()).year).sum()
yr_move = pd.Series(cl, index=t).groupby(yr).agg(lambda x: x.iloc[-1] - x.iloc[0]) / p
drift_hr = (yr_move / hours_per_year).to_dict()   # units per trading hour
pmid = pd.Series(cl).groupby(yr).mean().to_dict()

rows = []
alltr = {}
for H in (1, 2, 3, 4, 6):
    for h0 in range(24):
        try:
            ent = first.xs(h0, level="hr")
        except KeyError:
            continue
        ent_t = pd.to_datetime(ent.index) + pd.Timedelta(hours=h0)
        ex_t = ent_t + pd.Timedelta(hours=H)
        ei = ent.values
        xi = np.searchsorted(tv, ex_t.values) - 1          # last bar before exit time
        ok = (xi > ei) & (xi < len(d)) & ((tv[np.minimum(xi, len(d) - 1)] - ex_t.values) > -np.timedelta64(30, "m"))
        # skip if crossing a weekend (> H+1 h wall time between bars)
        ok &= (tv[np.minimum(xi, len(d) - 1)] - tv[ei]) < np.timedelta64(H * 60 + 5, "m")
        ei, xi = ei[ok], xi[ok]
        gross = (cl[xi] - op[ei]) / p
        yrs = yr[ei]
        for side in (1, -1):
            if side == 1:
                net = gross - spr[ei] * SPRM - 2 * slip
            else:
                net = -gross - spr[xi] * SPRM - 2 * slip
            drift = np.array([drift_hr[y] for y in yrs]) * H * side
            exc = net - drift
            tr = yrs <= 2024; te = yrs >= 2025
            def st(x):
                return x.mean(), x.mean() / (x.std(ddof=1) / np.sqrt(len(x))), len(x)
            m1, t1, n1 = st(net[tr]); m2, t2, n2 = st(net[te])
            e1, et1, _ = st(exc[tr]); e2, et2, _ = st(exc[te])
            peryr = pd.Series(net).groupby(yrs).mean()
            rows.append(dict(h0=h0, H=H, side=side, tr_n=n1, tr_net=m1, tr_t=t1, tr_exc=e1, tr_exc_t=et1,
                             yrs_pos_tr=int((peryr[peryr.index <= 2024] > 0).sum()),
                             te_n=n2, te_net=m2, te_t=t2, te_exc=e2, te_exc_t=et2,
                             cost=np.mean(spr[ei] * SPRM + 2 * slip)))
g = pd.DataFrame(rows)
ntr = len(g)
zb = norm.ppf(1 - 0.05 / 2 / ntr)
pd.set_option("display.width", 250)
print(f"{sym}: trials={ntr}, Bonferroni |t| threshold (5%, two-sided) = {zb:.2f}; slip={slip:.2f}u sprm={SPRM}")
print("Top 15 by TRAIN drift-neutral t (tr_exc_t):")
print(g.sort_values("tr_exc_t", ascending=False).head(15).round(2).to_string(index=False))
print("\nTop 10 by TRAIN raw net t:")
print(g.sort_values("tr_t", ascending=False).head(10).round(2).to_string(index=False))
print("\nFraction of cells with positive train net:", (g.tr_net > 0).mean().round(3), " positive holdout net:", (g.te_net > 0).mean().round(3))
print("Correlation train t vs holdout t across cells:", np.corrcoef(g.tr_t, g.te_t)[0, 1].round(3),
      " (drift-neutral):", np.corrcoef(g.tr_exc_t, g.te_exc_t)[0, 1].round(3))
g.to_csv(f"research/opus/b2_{sym}.csv", index=False)
