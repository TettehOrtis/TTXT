"""Exp3: direct forward-return predictability. LightGBM regression on vol-normalised forward return,
walk-forward quarterly. Reports net (after spread+2*slip) mean of extreme-score buckets by year.
Usage: python research/exp3.py XAUUSD slip"""
import sys, time
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
sys.path.insert(0, "research")
from lib import *

sym = sys.argv[1]
d = load(sym); f = features2(d, sym); p = pip_size(sym)
act = active_mask(d)
SLIP = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25 * float(np.median(d.spr.values[act]))
X = f.values.astype(np.float32)
valid = act & f.notna().all(axis=1).values
idx_all = np.where(valid)[0]
atr = f["atr60"].values
cl = d.close.values; spr = d.spr.values
tarr = d.time.values[idx_all]
print(f"{sym}: {len(idx_all)} rows, {X.shape[1]} features, slip={SLIP}, med spr={np.median(spr[idx_all]):.2f}u atr60={np.median(atr[idx_all]):.1f}u")
qs = pd.date_range("2023-01-01", "2026-10-01", freq="QS")
params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=1000, feature_fraction=0.6,
              bagging_fraction=0.6, bagging_freq=1, lambda_l2=50.0, verbose=-1, num_threads=8)
for H in (30, 60, 120, 240):
    e = idx_all + 1
    xe = np.minimum(e + H, len(cl) - 1)
    ret = (cl[xe] - d.open.values[e]) / p                     # bid-to-bid forward move (units)
    cost = spr[e] + 2 * SLIP                                   # spread + entry/exit slippage
    y = ret / atr[idx_all]                                      # vol-normalised target
    y = np.clip(y, -6, 6)
    pred = np.full(len(idx_all), np.nan)
    t0 = time.time()
    for a, b in zip(qs[:-1], qs[1:]):
        te = np.where((tarr >= np.datetime64(a)) & (tarr < np.datetime64(b)))[0]
        tr = np.where(tarr < np.datetime64(a - pd.Timedelta(days=2)))[0][::4]
        if len(te) == 0 or len(tr) < 5000: continue
        m = lgb.train(params, lgb.Dataset(X[idx_all[tr]], y[tr]), num_boost_round=250)
        pred[te] = m.predict(X[idx_all[te]])
    have = ~np.isnan(pred)
    ic = spearmanr(pred[have], y[have]).correlation
    print(f"\nH={H} bars | wf {time.time()-t0:.0f}s | OOS Spearman IC={ic:+.4f} | mean cost={cost[have].mean():.2f}u")
    for q in (1, 3, 10):
        hi = np.nanpercentile(pred[have], 100 - q / 2 * 1.0); lo = np.nanpercentile(pred[have], q / 2)
        sel = have & ((pred >= hi) | (pred <= lo))
        sgn = np.where(pred[sel] >= 0, 1, -1)
        net = sgn * ret[sel] - cost[sel]
        yrs = pd.to_datetime(tarr[sel]).year
        L = sgn == 1
        print(f"      [q={q}%] long n={L.sum()} gross={np.mean(ret[sel][L]):+.2f} | short n={(~L).sum()} gross={np.mean(-ret[sel][~L]):+.2f} | all-bars drift (long) {np.nanmean(ret[have]):+.2f}u")
        s = pd.Series(net).groupby(yrs).agg(["count", "mean"])
        print(f"   extreme {q:>2}% (both tails) n={sel.sum():6d} gross={np.mean(sgn*ret[sel]):+.2f} net={net.mean():+.2f}u win%={np.mean(net>0)*100:.1f} | "
              + " ".join(f"{y}:{r['mean']:+.1f}" for y, r in s.iterrows()))
