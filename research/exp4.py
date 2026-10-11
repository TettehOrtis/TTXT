"""Exp4: leak-free re-test of the gold ML signal. Expanding-window thresholds (only past OOS scores),
one trade at a time, spread-only vs spread+slip costs, day-clustered t-stats. Usage: python research/exp4.py [XAUUSD]"""
import sys
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import *

sym = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
d = load(sym); f = features2(d, sym); p = pip_size(sym)
act = active_mask(d)
X = f.values.astype(np.float32)
idx_all = np.where(act & f.notna().all(axis=1).values)[0]
atr = f["atr60"].values; cl = d.close.values; op = d.open.values; spr = d.spr.values
tarr = d.time.values[idx_all]
qs = pd.date_range("2023-01-01", "2026-10-01", freq="QS")
params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=1000, feature_fraction=0.6,
              bagging_fraction=0.6, bagging_freq=1, lambda_l2=50.0, verbose=-1, num_threads=8)
usd_per_unit = p * float(pd.read_json("data/meta.json", typ="series")[sym]["contract"]) * 0.01 if sym == "XAUUSD" else None

for H in (30, 60, 120):
    e = idx_all + 1; xe = np.minimum(e + H, len(cl) - 1)
    ret = (cl[xe] - op[e]) / p
    y = np.clip(ret / atr[idx_all], -6, 6)
    pred = np.full(len(idx_all), np.nan); fold = np.full(len(idx_all), -1)
    for k, (a, b) in enumerate(zip(qs[:-1], qs[1:])):
        te = np.where((tarr >= np.datetime64(a)) & (tarr < np.datetime64(b)))[0]
        tr = np.where(tarr < np.datetime64(a - pd.Timedelta(days=2)))[0][::4]
        if len(te) == 0 or len(tr) < 5000: continue
        m = lgb.train(params, lgb.Dataset(X[idx_all[tr]], y[tr]), num_boost_round=250)
        pred[te] = m.predict(X[idx_all[te]]); fold[te] = k
    for q in (1, 3):
        for slip_frac in (0.0, 0.25):
            slip = slip_frac * float(np.median(spr[idx_all]))
            sel = np.zeros(len(idx_all), bool)
            for k in np.unique(fold[fold >= 0]):
                past = pred[(fold >= 0) & (fold < k)]
                if len(past) < 20000: continue                    # need history for the threshold
                hi, lo = np.percentile(past, 100 - q / 2), np.percentile(past, q / 2)
                cur = fold == k
                sel |= cur & ((pred >= hi) | (pred <= lo))
            ii = np.where(sel)[0]
            sgn = np.where(pred[ii] >= 0, 1, -1)
            net = sgn * ret[ii] - (spr[idx_all[ii] + 1] + 2 * slip)
            # one trade at a time
            keep, last = [], -1
            for j, i in enumerate(ii):
                if idx_all[i] > last: keep.append(j); last = idx_all[i] + H
            net = net[keep]; tt = pd.to_datetime(tarr[ii[keep]])
            daily = pd.Series(net, index=tt).groupby(tt.floor("D")).sum()
            t = daily.mean() / (daily.std() / np.sqrt(len(daily))) if len(daily) > 5 else np.nan
            yr = pd.Series(net).groupby(tt.year).agg(["count", "mean"])
            print(f"H={H:3d} top{q}% slip={slip_frac:.2f}sp | trades={len(net):5d} ({len(net)/max(len(daily),1):.1f}/active day) "
                  f"net/trade={net.mean():+.2f}u (${net.mean()*p:+.3f}/oz) win%={np.mean(net>0)*100:.1f} | day-clustered t={t:+.2f} n_days={len(daily)} | "
                  + " ".join(f"{y_}:{r['mean']:+.1f}({int(r['count'])})" for y_, r in yr.iterrows()))
