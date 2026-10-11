"""Exp1: random-entry baseline + LightGBM walk-forward on one pair.
Usage: python research/exp1.py EURUSD
Trials counter: every (tp,sl,maxhold) bracket evaluated is one 'trial' (tracked for multiple-testing honesty)."""
import sys, time
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import *

sym = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
SLIP = 0.2
d = load(sym)
f = features(d, sym)
act = active_mask(d)
valid = act & f.notna().all(axis=1).values
idx_all = np.where(valid)[0]
print(f"{sym}: {len(d)} bars since {d.time.iloc[0].date()}, {len(idx_all)} tradable bars, median spr {d.spr[act].median():.2f} pips")

# ---------- 1) random baseline: what does a no-edge strategy lose?
rs = np.random.default_rng(0)
sub = rs.choice(idx_all, 60000, replace=False)
for tp, sl in [(4, 4), (6, 4), (8, 6), (10, 8)]:
    for side in (1, -1):
        pnl, held, why = run_sim(d, sym, sub, side, tp, sl, 30, SLIP)
        print(f"  random side={side:+d} TP{tp}/SL{sl}: win%={np.mean(why==1)*100:5.1f} sl%={np.mean(why==-1)*100:5.1f} "
              f"timeout%={np.mean(why==0)*100:5.1f} avg net={np.nanmean(pnl):+.3f} pips")

# ---------- 2) walk-forward LightGBM
BR = [(4, 4, 30), (6, 4, 30), (6, 6, 40), (8, 6, 40), (10, 8, 60)]
Xall = f.values.astype(np.float32)
cols = list(f.columns)
times = d.time.values
qs = pd.date_range("2023-01-01", "2026-10-01", freq="QS")
params = dict(objective="binary", learning_rate=0.05, num_leaves=15, min_data_in_leaf=400,
              feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0,
              verbose=-1, num_threads=8)

results = {}
for tp, sl, mh in BR:
    t0 = time.time()
    oos = {}
    labs, pnls, helds = {}, {}, {}
    for side in (1, -1):
        pnl, held, why = run_sim(d, sym, idx_all, side, tp, sl, mh, SLIP)
        labs[side] = (pnl > 0).astype(int)
        pnls[side], helds[side] = pnl, held
        oos[side] = np.full(len(idx_all), np.nan)
    tarr = times[idx_all]
    for a, b in zip(qs[:-1], qs[1:]):
        te = np.where((tarr >= np.datetime64(a)) & (tarr < np.datetime64(b)))[0]
        tr = np.where(tarr < np.datetime64(a - pd.Timedelta(days=2)))[0][::3]
        if len(te) == 0 or len(tr) < 5000:
            continue
        for side in (1, -1):
            ok = ~np.isnan(pnls[side][tr])
            m = lgb.train(params, lgb.Dataset(Xall[idx_all[tr[ok]]], labs[side][tr[ok]]), num_boost_round=200)
            oos[side][te] = m.predict(Xall[idx_all[te]])
    results[(tp, sl, mh)] = (oos, pnls, helds)
    print(f"[TP{tp}/SL{sl}/H{mh}] wf done in {time.time()-t0:.0f}s")

    # evaluate thresholds on OOS
    pl, ps = oos[1], oos[-1]
    have = ~np.isnan(pl)
    be = (sl + 1.0) / (tp + sl)   # rough break-even winrate incl ~1 pip cost
    print(f"   rough break-even win% ~{be*100:.1f}")
    for thr in (0.50, 0.55, 0.60, 0.65, 0.70):
        best_side = np.where(pl >= ps, 1, -1)
        pbest = np.where(pl >= ps, pl, ps)
        sel = have & (pbest >= thr)
        ii = np.where(sel)[0]
        if len(ii) < 30:
            print(f"   thr {thr:.2f}: n={len(ii)}"); continue
        pnl = np.where(best_side[ii] == 1, pnls[1][ii], pnls[-1][ii])
        hd = np.where(best_side[ii] == 1, helds[1][ii], helds[-1][ii])
        # no-overlap filter (one trade at a time)
        keep, last_end = [], -1
        for k, i in enumerate(ii):
            gi = idx_all[i]
            if gi > last_end:
                keep.append(k); last_end = gi + hd[k]
        pnl = pnl[keep]; yrs = pd.to_datetime(tarr[ii[keep]]).year
        s = pd.Series(pnl).groupby(yrs).agg(["count", "mean", "sum"]).round(2)
        print(f"   thr {thr:.2f}: n={len(pnl):5d} win%={np.mean(pnl>0)*100:5.1f} avg={pnl.mean():+.3f} total={pnl.sum():+8.1f} pips | "
              + " ".join(f"{y}:{r['mean']:+.2f}({int(r['count'])})" for y, r in s.iterrows()))
