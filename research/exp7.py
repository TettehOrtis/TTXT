"""Exp6: dissect the USDJPY tail signal: by year/quarter, concentration (drop best months), time-of-day, feature importance,
and the same pipeline on JPY crosses. Usage: python research/exp6.py"""
import sys
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import *

params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=1000, feature_fraction=0.6,
              bagging_fraction=0.6, bagging_freq=1, lambda_l2=50.0, verbose=-1, num_threads=8)
qs = pd.date_range("2023-01-01", "2026-10-01", freq="QS")


def run(sym, H, q, slip_frac=0.25, breakdown=True, no_roll=False, exit_spread=True):
    d = load(sym); f = features2(d, sym); p = pip_size(sym)
    act = active_mask(d); X = f.values.astype(np.float32)
    idx_all = np.where(act & f.notna().all(axis=1).values)[0]
    atr = f["atr60"].values; cl = d.close.values; op = d.open.values; spr = d.spr.values
    tarr = d.time.values[idx_all]; slip = slip_frac * float(np.median(spr[idx_all]))
    e = idx_all + 1; xe = np.minimum(e + H, len(cl) - 1)
    ret = (cl[xe] - op[e]) / p
    y = np.clip(ret / atr[idx_all], -6, 6)
    pred = np.full(len(idx_all), np.nan); fold = np.full(len(idx_all), -1); imp = np.zeros(X.shape[1])
    for k, (a, b) in enumerate(zip(qs[:-1], qs[1:])):
        te = np.where((tarr >= np.datetime64(a)) & (tarr < np.datetime64(b)))[0]
        tr = np.where(tarr < np.datetime64(a - pd.Timedelta(days=2)))[0][::4]
        if len(te) == 0 or len(tr) < 5000: continue
        m = lgb.train(params, lgb.Dataset(X[idx_all[tr]], y[tr]), num_boost_round=250)
        pred[te] = m.predict(X[idx_all[te]]); fold[te] = k; imp += m.feature_importance("gain")
    sel = np.zeros(len(idx_all), bool)
    for k in np.unique(fold[fold >= 0]):
        past = pred[(fold >= 0) & (fold < k)]
        if len(past) < 20000: continue
        hi, lo = np.percentile(past, 100 - q / 2), np.percentile(past, q / 2)
        sel |= (fold == k) & ((pred >= hi) | (pred <= lo))
    ii = np.where(sel)[0]
    sgn = np.where(pred[ii] >= 0, 1, -1)
    # spread accounting: long pays spread at ENTRY (ask in, bid out); short pays spread at EXIT (bid in, ask out)
    sp_e, sp_x = spr[idx_all[ii] + 1], spr[xe[ii]]
    sp_used = np.where(sgn == 1, sp_e, sp_x) if exit_spread else sp_e
    net = sgn * ret[ii] - (sp_used + 2 * slip)
    if no_roll:
        et = pd.to_datetime(d.time.values[xe[ii]]); st = pd.to_datetime(d.time.values[e[ii]])
        ok = (et.normalize() == st.normalize()) & ((et.hour * 60 + et.minute) <= 20 * 60 + 30)
        ii, sgn, net = ii[ok], sgn[ok], net[ok]
    keep, last = [], -1
    for j, i in enumerate(ii):
        if idx_all[i] > last: keep.append(j); last = idx_all[i] + H
    net, sg, tt = net[keep], sgn[keep], pd.to_datetime(tarr[ii[keep]])
    df = pd.DataFrame({"t": tt, "net": net, "side": sg})
    print(f"\n=== {sym} H={H} top{q}% : n={len(df)} net/trade={df.net.mean():+.2f}u win%={np.mean(df.net>0)*100:.1f} "
          f"shorts={np.mean(df.side==-1)*100:.0f}%")
    if breakdown:
        print("by year:   ", " ".join(f"{y_}:{g.net.mean():+.1f}({len(g)})" for y_, g in df.groupby(df.t.dt.year)))
        qtr = df.groupby(df.t.dt.to_period("Q")).agg(n=("net", "size"), mean=("net", "mean"))
        print("by quarter:", " ".join(f"{k}:{r['mean']:+.1f}({int(r['n'])})" for k, r in qtr.iterrows()))
        mo = df.groupby(df.t.dt.to_period("M")).net.sum().sort_values(ascending=False)
        tot = df.net.sum()
        print(f"total {tot:+.0f}u | top-3 months = {mo.head(3).sum()/tot*100:.0f}% of total ({', '.join(str(x) for x in mo.head(3).index)}) "
              f"| net/trade excl. top-3 months: {df[~df.t.dt.to_period('M').isin(mo.head(3).index)].net.mean():+.2f}u")
        print("by hour:   ", " ".join(f"{h}:{g.net.mean():+.1f}({len(g)})" for h, g in df.groupby(df.t.dt.hour)))
        top = pd.Series(imp, index=f.columns).sort_values(ascending=False)
        print("top features by gain:", ", ".join(f"{k}({v/top.sum()*100:.0f}%)" for k, v in top.head(8).items()))
    return df


if __name__ == "__main__":
    print("USDJPY H=180 top2%: [1] original accounting  [2] short pays EXIT spread  [3] + forbid rollover crossing (exit <= 20:30 UTC same day)")
    print("--- [1]"); run("USDJPY", 180, 2, breakdown=False, exit_spread=False)
    print("--- [2]"); run("USDJPY", 180, 2, breakdown=False, exit_spread=True)
    print("--- [3]"); run("USDJPY", 180, 2, breakdown=True, no_roll=True)
    print("GOLD H=120 top1%: [1] original  [2] exit spread  [3] exit spread + no rollover")
    print("--- [1]"); run("XAUUSD", 120, 1, breakdown=False, exit_spread=False)
    print("--- [2]"); run("XAUUSD", 120, 1, breakdown=False, exit_spread=True)
    print("--- [3]"); run("XAUUSD", 120, 1, breakdown=True, no_roll=True)
