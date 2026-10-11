"""Final candidate: gold, LightGBM forward-return model (H=120), trade only extreme scores.
Evaluates the EXACT live exit rules (protective stop k*ATR60 + 120-bar time exit, entries 07:00-18:30 UTC so exit
never crosses rollover), with expanding vs rolling-4-quarter thresholds, in $ for 0.01 lot. Saves OOS preds.
Usage: python research/final_gold.py"""
import sys
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import *

SYM, H = "XAUUSD", 120
params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=1000, feature_fraction=0.6,
              bagging_fraction=0.6, bagging_freq=1, lambda_l2=50.0, verbose=-1, num_threads=8)
d = load(SYM); f = features2(d, SYM); p = pip_size(SYM)
act = active_mask(d)
hm = d.time.dt.hour * 60 + d.time.dt.minute
act = act & (hm <= 18 * 60 + 30).values           # exit (t+120) stays before 20:30 UTC
X = f.values.astype(np.float32)
idx_all = np.where(act & f.notna().all(axis=1).values)[0]
atr = f["atr60"].values; cl = d.close.values; op = d.open.values
tarr = d.time.values[idx_all]
e = idx_all + 1; xe = np.minimum(e + H, len(cl) - 1)
y = np.clip((cl[xe] - op[e]) / p / atr[idx_all], -6, 6)
qs = pd.date_range("2023-01-01", "2026-10-01", freq="QS")
pred = np.full(len(idx_all), np.nan); fold = np.full(len(idx_all), -1)
for k, (a, b) in enumerate(zip(qs[:-1], qs[1:])):
    te = np.where((tarr >= np.datetime64(a)) & (tarr < np.datetime64(b)))[0]
    tr = np.where(tarr < np.datetime64(a - pd.Timedelta(days=2)))[0][::4]
    if len(te) == 0 or len(tr) < 5000: continue
    m = lgb.train(params, lgb.Dataset(X[idx_all[tr]], y[tr]), num_boost_round=250)
    pred[te] = m.predict(X[idx_all[te]]); fold[te] = k
pd.DataFrame({"time": tarr, "pred": pred, "fold": fold}).to_parquet("research/gold_oos_preds.parquet", index=False)

SLIP = 0.25 * float(np.median(d.spr.values[idx_all]))
USD_PER_UNIT_001 = p * 100 * 0.01          # 0.01 lot = 1 oz -> $0.10 per 0.10 move


def select(q, mode):
    sel = np.zeros(len(idx_all), bool)
    for k in np.unique(fold[fold >= 0]):
        pm = (fold >= 0) & (fold < k) if mode == "expanding" else (fold >= max(0, k - 4)) & (fold < k)
        past = pred[pm]
        if len(past) < 20000: continue
        hi, lo = np.percentile(past, 100 - q / 2), np.percentile(past, q / 2)
        sel |= (fold == k) & ((pred >= hi) | (pred <= lo))
    return np.where(sel)[0]


rows = []
for mode in ("expanding", "rolling4q"):
    for q in (0.5, 1.0, 1.5):
        ii = select(q, mode)
        side = np.where(pred[ii] >= 0, 1, -1).astype(np.int8)
        for k_sl in (2.0, 3.0, 5.0, 99.0):
            sl = k_sl * atr[idx_all[ii]]
            pnl = np.full(len(ii), np.nan); held = np.zeros(len(ii), int)
            for s in (1, -1):
                mm = side == s
                a_, b_, _ = run_sim(d, SYM, idx_all[ii[mm]], s, np.full(mm.sum(), 1e6), sl[mm], H, SLIP, 0.1)
                pnl[mm], held[mm] = a_, b_
            keep, last = [], -1
            for j, i in enumerate(ii):
                gi = idx_all[i]
                if gi > last and not np.isnan(pnl[j]):
                    keep.append(j); last = gi + held[j]
            usd = pnl[keep] * USD_PER_UNIT_001
            tt = pd.to_datetime(tarr[ii[keep]])
            day = pd.Series(usd, index=tt).groupby(tt.floor("D")).sum()
            wk = pd.Series(usd, index=tt).groupby(tt.to_period("W")).sum()
            eq = np.cumsum(usd); dd = (eq - np.maximum.accumulate(eq)).min()
            n_days = np.busday_count(tt.min().date(), tt.max().date())
            yr = pd.Series(usd).groupby(tt.year).agg(["count", "sum"])
            rows.append(dict(mode=mode, q=q, k_sl=k_sl, n=len(usd), per_day=len(usd) / n_days, usd_trade=usd.mean(),
                             sd_trade=usd.std(), win=np.mean(usd > 0) * 100, t_week=wk.mean() / (wk.std() / np.sqrt(len(wk))),
                             worst_trade=usd.min(), worst_day=day.min(), maxDD=dd, total=usd.sum(),
                             by_year=" ".join(f"{y_}:{r['sum']:+.0f}({int(r['count'])})" for y_, r in yr.iterrows())))
pd.set_option("display.width", 260); pd.set_option("display.max_colwidth", 80)
print(f"slip={SLIP:.2f} units/fill | $ figures are for 0.01 lot (1 oz); scale linearly with lot size")
print(pd.DataFrame(rows).round(2).to_string(index=False))
