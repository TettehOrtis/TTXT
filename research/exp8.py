"""Exp8: CORRECTED accounting (long pays entry spread, short pays EXIT spread, +slippage both ends, no trade may cross
the 20:30-22:30 UTC rollover, one trade at a time, expanding-window thresholds). Robustness surface for gold and a placebo
set of instruments under the identical rules. t-stats clustered by WEEK (regime-robust). Usage: python research/exp8.py gold|placebo"""
import sys
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import *

params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=1000, feature_fraction=0.6,
              bagging_fraction=0.6, bagging_freq=1, lambda_l2=50.0, verbose=-1, num_threads=8)
qs = pd.date_range("2023-01-01", "2026-10-01", freq="QS")


def surface(sym, Hs, quants, slip_frac=0.25):
    d = load(sym); f = features2(d, sym); p = pip_size(sym)
    act = active_mask(d); X = f.values.astype(np.float32)
    idx_all = np.where(act & f.notna().all(axis=1).values)[0]
    atr = f["atr60"].values; cl = d.close.values; op = d.open.values; spr = d.spr.values
    tv = d.time.values; tarr = tv[idx_all]; slip = slip_frac * float(np.median(spr[idx_all]))
    res = []
    for H in Hs:
        e = idx_all + 1; xe = np.minimum(e + H, len(cl) - 1)
        ret = (cl[xe] - op[e]) / p
        y = np.clip(ret / atr[idx_all], -6, 6)
        et, st = pd.to_datetime(tv[xe]), pd.to_datetime(tv[e])
        no_roll = np.asarray((et.normalize() == st.normalize()) & ((et.hour * 60 + et.minute) <= 20 * 60 + 30))
        pred = np.full(len(idx_all), np.nan); fold = np.full(len(idx_all), -1)
        for k, (a, b) in enumerate(zip(qs[:-1], qs[1:])):
            te = np.where((tarr >= np.datetime64(a)) & (tarr < np.datetime64(b)))[0]
            tr = np.where(tarr < np.datetime64(a - pd.Timedelta(days=2)))[0][::4]
            if len(te) == 0 or len(tr) < 5000: continue
            m = lgb.train(params, lgb.Dataset(X[idx_all[tr]], y[tr]), num_boost_round=250)
            pred[te] = m.predict(X[idx_all[te]]); fold[te] = k
        for q in quants:
            sel = np.zeros(len(idx_all), bool)
            for k in np.unique(fold[fold >= 0]):
                past = pred[(fold >= 0) & (fold < k)]
                if len(past) < 20000: continue
                hi, lo = np.percentile(past, 100 - q / 2), np.percentile(past, q / 2)
                sel |= (fold == k) & ((pred >= hi) | (pred <= lo)) & no_roll
            ii = np.where(sel)[0]
            sgn = np.where(pred[ii] >= 0, 1, -1)
            sp = np.where(sgn == 1, spr[idx_all[ii] + 1], spr[xe[ii]])
            net = sgn * ret[ii] - (sp + 2 * slip)
            keep, last = [], -1
            for j, i in enumerate(ii):
                if idx_all[i] > last: keep.append(j); last = idx_all[i] + H
            net, sg, tt = net[keep], sgn[keep], pd.to_datetime(tarr[ii[keep]])
            if len(net) < 20:
                res.append(dict(sym=sym, H=H, q=q, n=len(net))); continue
            wk = pd.Series(net, index=tt).groupby(tt.to_period("W")).sum()
            t_w = wk.mean() / (wk.std() / np.sqrt(len(wk))) if len(wk) > 5 else np.nan
            L, S = sg == 1, sg == -1
            yr = pd.Series(net).groupby(tt.year).mean()
            res.append(dict(sym=sym, H=H, q=q, n=len(net), net_u=net.mean(), win=np.mean(net > 0) * 100, t_week=t_w,
                            long_n=L.sum(), long_net=net[L].mean() if L.any() else np.nan, short_n=S.sum(),
                            short_net=net[S].mean() if S.any() else np.nan,
                            yrs_pos=f"{int((yr > 0).sum())}/{len(yr)}"))
    return pd.DataFrame(res)


if __name__ == "__main__":
    pd.set_option("display.width", 230)
    if sys.argv[1] == "gold":
        print(surface("XAUUSD", (60, 90, 120, 150, 180), (0.5, 1, 2, 3, 5)).round(2).to_string(index=False))
    else:
        rows = [surface(s, (120,), (1, 2)) for s in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "US30", "USTEC", "DE30", "USOIL")]
        print("PLACEBO under identical corrected rules (H=120)")
        print(pd.concat(rows).round(2).to_string(index=False))
