"""Exp2: walk-forward LightGBM with ATR-scaled brackets, evaluated by score quantile.
Usage: python research/exp2.py XAUUSD [slip_units]"""
import sys, time
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import *

sym = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
SLIP = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
d = load(sym)
f = features(d, sym)
act = active_mask(d)
valid = act & f.notna().all(axis=1).values
idx_all = np.where(valid)[0]
atr = f["atr60"].values
print(f"{sym}: {len(d)} bars, {len(idx_all)} tradable, unit={pip_size(sym)}, median spr {d.spr[act].median():.2f} units, "
      f"median atr60 {np.median(atr[idx_all]):.1f} units, slip={SLIP}")

rs = np.random.default_rng(0)
sub = rs.choice(idx_all, 60000, replace=False)
BR = [(2.0, 1.5, 20), (3.0, 2.0, 30), (3.0, 3.0, 30), (4.0, 3.0, 45)]
for ktp, ksl, mh in BR[:2]:
    for side in (1, -1):
        pnl, held, why = run_sim(d, sym, sub, side, ktp*atr[sub], ksl*atr[sub], mh, SLIP)
        print(f"  random side={side:+d} TP{ktp}xATR/SL{ksl}xATR H{mh}: win%={np.mean(why==1)*100:5.1f} sl%={np.mean(why==-1)*100:5.1f} "
              f"to%={np.mean(why==0)*100:5.1f} avg net={np.nanmean(pnl):+.2f} units (tp~{np.mean(ktp*atr[sub]):.0f})")

Xall = f.values.astype(np.float32)
times = d.time.values
qs = pd.date_range("2023-01-01", "2026-10-01", freq="QS")
params = dict(objective="binary", learning_rate=0.05, num_leaves=15, min_data_in_leaf=400, feature_fraction=0.7,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0, verbose=-1, num_threads=8)
tarr = times[idx_all]
for ktp, ksl, mh in BR:
    t0 = time.time()
    oos, labs, pnls, helds = {}, {}, {}, {}
    for side in (1, -1):
        pnl, held, why = run_sim(d, sym, idx_all, side, ktp*atr[idx_all], ksl*atr[idx_all], mh, SLIP)
        labs[side] = (pnl > 0).astype(int); pnls[side] = pnl; helds[side] = held
        oos[side] = np.full(len(idx_all), np.nan)
    for a, b in zip(qs[:-1], qs[1:]):
        te = np.where((tarr >= np.datetime64(a)) & (tarr < np.datetime64(b)))[0]
        tr = np.where(tarr < np.datetime64(a - pd.Timedelta(days=2)))[0][::3]
        if len(te) == 0 or len(tr) < 5000: continue
        for side in (1, -1):
            ok = ~np.isnan(pnls[side][tr])
            m = lgb.train(params, lgb.Dataset(Xall[idx_all[tr[ok]]], labs[side][tr[ok]]), num_boost_round=200)
            oos[side][te] = m.predict(Xall[idx_all[te]])
    pl, ps = oos[1], oos[-1]
    have = ~np.isnan(pl)
    print(f"[TP{ktp}xATR/SL{ksl}xATR/H{mh}] wf {time.time()-t0:.0f}s | base win% long {labs[1][have].mean()*100:.1f} short {labs[-1][have].mean()*100:.1f} "
          f"| unconditional avg net long {np.nanmean(pnls[1][have]):+.2f} short {np.nanmean(pnls[-1][have]):+.2f}")
    best_side = np.where(pl >= ps, 1, -1); pbest = np.where(pl >= ps, pl, ps)
    for q in (0.5, 1, 2, 5, 10, 20):
        thr = np.nanpercentile(pbest[have], 100 - q)
        ii = np.where(have & (pbest >= thr))[0]
        pnl = np.where(best_side[ii] == 1, pnls[1][ii], pnls[-1][ii]); hd = np.where(best_side[ii] == 1, helds[1][ii], helds[-1][ii])
        keep, last_end = [], -1
        for k, i in enumerate(ii):
            gi = idx_all[i]
            if gi > last_end: keep.append(k); last_end = gi + hd[k]
        pnl = pnl[keep]; yrs = pd.to_datetime(tarr[ii[keep]]).year
        s = pd.Series(pnl).groupby(yrs).agg(["count", "mean"])
        print(f"   top{q:>4}% thr={thr:.3f} n={len(pnl):5d} win%={np.mean(pnl>0)*100:5.1f} avg={pnl.mean():+.2f} tot={pnl.sum():+9.0f} | "
              + " ".join(f"{y}:{r['mean']:+.1f}({int(r['count'])})" for y, r in s.iterrows()))
