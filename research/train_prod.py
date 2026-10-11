"""Train the production gold model on ALL data up to now; thresholds from the pooled walk-forward OOS predictions."""
import sys, json
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import *

SYM, H, Q = "XAUUSD", 120, 1.0
params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=1000, feature_fraction=0.6,
              bagging_fraction=0.6, bagging_freq=1, lambda_l2=50.0, verbose=-1, num_threads=8)
d = load(SYM); f = features2(d, SYM); p = pip_size(SYM)
hm = d.time.dt.hour * 60 + d.time.dt.minute
act = active_mask(d) & (hm <= 18 * 60 + 30).values
idx = np.where(act & f.notna().all(axis=1).values)[0]
idx = idx[idx + 1 + H < len(d)]
e = idx + 1
y = np.clip((d.close.values[e + H] - d.open.values[e]) / p / f["atr60"].values[idx], -6, 6)
m = lgb.train(params, lgb.Dataset(f.values.astype(np.float32)[idx[::4]], y[::4]), num_boost_round=250)
m.save_model("models/gold_h120.txt")
oos = pd.read_parquet("research/gold_oos_preds.parquet").dropna()
# ROLLING thresholds: 1% tails of this model's own predictions over the last 60 days (re-run weekly to refresh)
recent = idx[d.time.values[idx] >= d.time.values[idx[-1]] - np.timedelta64(60, "D")]
ins = m.predict(f.values.astype(np.float32)[recent])
hi, lo = float(np.percentile(ins, 100 - Q / 2)), float(np.percentile(ins, Q / 2))
meta = dict(sym=SYM, H=H, q=Q, thr_hi=hi, thr_lo=lo, thr_mode="rolling60d",
            thr_hi_oos_pooled=float(np.percentile(oos.pred, 100 - Q / 2)), thr_lo_oos_pooled=float(np.percentile(oos.pred, Q / 2)), k_sl=5.0, features=list(f.columns), trained_until=str(d.time.iloc[-1]),
            oos_pred_sd=float(oos.pred.std()), prod_insample_sd_recent=float(ins.std()),
            note="demo forward-test candidate; walk-forward t~1 (NOT statistically significant)")
json.dump(meta, open("models/gold_h120.json", "w"), indent=1)
print(json.dumps({k: v for k, v in meta.items() if k != "features"}, indent=1))
