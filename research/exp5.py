"""Exp5: is the gold H=120 top-1% result real or a lucky pick?
 (a) robustness surface over neighbouring quantiles/horizons, (b) long/short split vs an always-long baseline,
 (c) placebo on other instruments with the identical pipeline.
Usage: python research/exp5.py gold | placebo"""
import sys
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import *

mode = sys.argv[1]
params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=1000, feature_fraction=0.6,
              bagging_fraction=0.6, bagging_freq=1, lambda_l2=50.0, verbose=-1, num_threads=8)
qs = pd.date_range("2023-01-01", "2026-10-01", freq="QS")


def analyse(sym, Hs, quants, slip_frac=0.25, verbose_split=False):
    d = load(sym); f = features2(d, sym); p = pip_size(sym)
    act = active_mask(d)
    X = f.values.astype(np.float32)
    idx_all = np.where(act & f.notna().all(axis=1).values)[0]
    atr = f["atr60"].values; cl = d.close.values; op = d.open.values; spr = d.spr.values
    tarr = d.time.values[idx_all]
    slip = slip_frac * float(np.median(spr[idx_all]))
    out = []
    for H in Hs:
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
        # drift baseline: always-long, one trade at a time, same window as evaluated folds
        for q in quants:
            sel = np.zeros(len(idx_all), bool)
            for k in np.unique(fold[fold >= 0]):
                past = pred[(fold >= 0) & (fold < k)]
                if len(past) < 20000: continue
                hi, lo = np.percentile(past, 100 - q / 2), np.percentile(past, q / 2)
                sel |= (fold == k) & ((pred >= hi) | (pred <= lo))
            ii = np.where(sel)[0]
            sgn = np.where(pred[ii] >= 0, 1, -1)
            gross = sgn * ret[ii]
            net = gross - (spr[idx_all[ii] + 1] + 2 * slip)
            keep, last = [], -1
            for j, i in enumerate(ii):
                if idx_all[i] > last: keep.append(j); last = idx_all[i] + H
            net, sg, gr, tt = net[keep], sgn[keep], gross[keep], pd.to_datetime(tarr[ii[keep]])
            daily = pd.Series(net, index=tt).groupby(tt.floor("D")).sum()
            t = daily.mean() / (daily.std() / np.sqrt(len(daily))) if len(daily) > 5 else np.nan
            L, S = sg == 1, sg == -1
            out.append(dict(sym=sym, H=H, q=q, n=len(net), net_u=net.mean(), usd_oz=net.mean() * p, win=np.mean(net > 0) * 100, t=t,
                            long_n=L.sum(), long_net=net[L].mean() if L.any() else np.nan,
                            short_n=S.sum(), short_net=net[S].mean() if S.any() else np.nan))
        # unconditional drift over the same evaluated rows (all bars, direction long) in units
        have = fold >= 0
        out.append(dict(sym=sym, H=H, q=-1, n=have.sum(), net_u=np.nanmean(ret[have]) - (spr[idx_all[have] + 1].mean() + 2 * slip),
                        usd_oz=np.nan, win=np.nan, t=np.nan, long_n=0, long_net=np.nan, short_n=0, short_net=np.nan))
    return pd.DataFrame(out)


pd.set_option("display.width", 220)
if mode == "gold":
    r = analyse("XAUUSD", (90, 120, 150, 180, 240), (0.5, 1, 2, 3, 5))
    print(r.round(2).to_string(index=False))
    print("\n(rows with q=-1: unconditional always-LONG net per trade over the same evaluated window = the drift baseline)")
elif mode.isupper():
    r = analyse(mode, (60, 90, 120, 150, 180), (0.5, 1, 2, 3, 5))
    print(r.round(2).to_string(index=False))
else:
    rows = []
    for s in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "US30", "USTEC", "DE30", "USOIL", "BTCUSD"):
        rows.append(analyse(s, (120,), (1,)))
    r = pd.concat(rows)
    print("PLACEBO: identical pipeline, H=120, top1% on instruments with no signal found earlier")
    print(r[r.q == 1][["sym", "n", "net_u", "win", "t", "long_n", "long_net", "short_n", "short_net"]].round(2).to_string(index=False))
