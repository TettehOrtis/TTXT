"""Production USDJPY scalp model (Zero account), same protocol as research/wf_scalp.py:
train on everything before (last 6 months - 1 day), set thresholds = 0.25%/99.75% of the model's predictions on the last
6 months (unseen by the model), trade the 20-minute forward move. Re-run weekly/monthly to refresh."""
import os, sys, json
os.environ["DATA_DIR"] = "data_zero"
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import load, features2, pip_size

SYM, H, Q = "USDJPY", 20, 0.5
params = dict(objective="regression", learning_rate=0.05, num_leaves=31, min_data_in_leaf=2000, feature_fraction=0.6,
              bagging_fraction=0.6, bagging_freq=1, lambda_l2=50.0, verbose=-1, num_threads=6)
d = load(SYM, "2021-01-01")
f = features2(d, SYM).drop(columns=["spr", "spr_over_atr"]).astype(np.float32)
p = pip_size(SYM)
h = d.time.dt.hour
act = ((h >= 7) & (h < 20) & ~((d.time.dt.dayofweek == 4) & (h >= 19))).values
idx = np.where(act & f.notna().all(axis=1).values)[0]
idx = idx[idx + H + 2 < len(d)]
px = d.open.values + d.spr.values * p / 2
gross = (px[idx + 1 + H] - px[idx + 1]) / p
atr = f["atr60"].values[idx].astype(np.float64)
y = np.clip(gross / atr, -8, 8)
tt = d.time.values[idx]
cut = tt[-1] - np.timedelta64(182, "D")
tr = np.where(tt < cut - np.timedelta64(1, "D"))[0]; va = np.where(tt >= cut)[0]
trs = tr[:: max(1, len(tr) // 400000)]
m = lgb.train(params, lgb.Dataset(f.values[idx[trs]], y[trs]), num_boost_round=300)
pv = m.predict(f.values[idx[va]])
hi, lo = float(np.percentile(pv, 100 - Q / 2)), float(np.percentile(pv, Q / 2))
# how the chosen signals did on the (model-unseen) last 6 months, one trade at a time
s = np.where((pv >= hi) | (pv <= lo))[0]; side = np.where(pv[s] >= 0, 1, -1); g = side * gross[va[s]]
keep, last = [], -1
for j, gi in enumerate(idx[va[s]]):
    if gi > last: keep.append(j); last = gi + H
g = g[keep]; days = len(np.unique(tt[va].astype("datetime64[D]")))
os.makedirs("models", exist_ok=True)
m.save_model("models/usdjpy_h20.txt")
meta = dict(sym=SYM, H=H, q=Q, thr_hi=hi, thr_lo=lo, features=list(f.columns), train_until=str(pd.Timestamp(tt[tr[-1]])),
            thr_window=f"{pd.Timestamp(tt[va[0]])} -> {pd.Timestamp(tt[va[-1]])}", comm_rt_pips=0.79, sl_pips=20.0,
            last6m_trades=len(g), last6m_per_day=len(g) / days, last6m_gross_pips=float(g.mean()), last6m_net_pips=float(g.mean() - 0.79),
            note="FORWARD TEST ONLY. Walk-forward 2022-2026: +0.46 pip/trade after commission, 5/8 half-years positive; weak evidence.")
json.dump(meta, open("models/usdjpy_h20.json", "w"), indent=1)
print(json.dumps({k: v for k, v in meta.items() if k != "features"}, indent=1))
