"""Walk-forward SCALPING study on FX (the user's protocol):
  split the whole history (2018-07 -> 2026-09) into 6-month blocks; for each fold
     TRAIN      = everything before the validation block (expanding window)
     VALIDATE   = the 6 months before the test block  -> choose horizon H and signal threshold q (per cost level)
     TEST       = the next 6 months, never seen by training or selection
  then roll forward 6 months and repeat.
Costs are NOT taken from the Standard-account spread column. Trades are simulated on BID prices both sides (= zero
spread) and an all-in round-trip cost C (pips: spread + commission + slippage) is subtracted, so the same run tells us
what an account with cost C would earn. Break-even cost = gross pips per trade.
Usage: python research/wf_scalp.py EURUSD
"""
import sys, os, time
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, "research")
from lib import load, features2, pip_size

SYM = sys.argv[1]
MID = "--mid" in sys.argv          # price the trade at MID (bid + spread/2) to remove bid-bounce artifacts
HS = (5, 10, 20)                       # holding time in minutes (scalps)
QS = (0.5, 1.0, 2.0, 5.0)              # % of most extreme scores traded (both tails)
COSTS = (0.0, 0.2, 0.4, 0.6, 0.8) if sys.argv[1] != "XAUUSD" else (0.0, 0.5, 1.0, 2.0, 3.0)   # all-in round trip (pips; gold: $0.10 units)
params = dict(objective="regression", learning_rate=0.05, num_leaves=31, min_data_in_leaf=2000, feature_fraction=0.6,
              bagging_fraction=0.6, bagging_freq=1, lambda_l2=50.0, verbose=-1, num_threads=3)

t0 = time.time()
d = load(SYM, "2018-07-01")
f = features2(d, SYM).drop(columns=["spr", "spr_over_atr"]).astype(np.float32)
p = pip_size(SYM)
h = d.time.dt.hour
act = ((h >= 7) & (h < 20) & ~((d.time.dt.dayofweek == 4) & (h >= 19))).values
valid = act & f.notna().all(axis=1).values
idx = np.where(valid)[0]
idx = idx[idx + max(HS) + 2 < len(d)]
X = f.values[idx]
tt = d.time.values[idx]
op = d.open.values
atr = f["atr60"].values[idx].astype(np.float64)
px = op + (d.spr.values * p / 2 if MID else 0.0)
gross = {H: (px[idx + 1 + H] - px[idx + 1]) / p for H in HS}          # enter next bar open, exit H minutes later (pips)
print(f"{SYM}: {len(idx)} tradable bars, features {X.shape[1]}, prep {time.time()-t0:.0f}s", flush=True)

blocks = pd.date_range("2021-07-01", "2026-10-01", freq="6MS")        # validation/test block starts
rows = []
for i in range(1, len(blocks) - 1):
    v0, v1, t1 = blocks[i - 1], blocks[i], blocks[i + 1]
    tr = np.where(tt < np.datetime64(v0 - pd.Timedelta(days=1)))[0]
    va = np.where((tt >= np.datetime64(v0)) & (tt < np.datetime64(v1)))[0]
    te = np.where((tt >= np.datetime64(v1)) & (tt < np.datetime64(t1)))[0]
    if len(tr) < 60000 or len(va) == 0 or len(te) == 0:
        continue
    trs = tr[:: max(1, len(tr) // 400000)]
    res = {}
    for H in HS:
        y = np.clip(gross[H] / atr, -8, 8)
        m = lgb.train(params, lgb.Dataset(X[trs], y[trs]), num_boost_round=300)
        pv, pt = m.predict(X[va]), m.predict(X[te])
        for q in QS:
            hi, lo = np.percentile(pv, 100 - q / 2), np.percentile(pv, q / 2)   # thresholds from VALIDATION only
            for name, sel_idx, pr in (("val", va, pv), ("test", te, pt)):
                s = np.where((pr >= hi) | (pr <= lo))[0]
                side = np.where(pr[s] >= 0, 1, -1)
                g = side * gross[H][sel_idx[s]]
                keep, last = [], -1                     # one position at a time
                gi = idx[sel_idx[s]]
                for j in range(len(s)):
                    if gi[j] > last:
                        keep.append(j); last = gi[j] + H
                g = g[keep]
                days = max(1, len(np.unique(tt[sel_idx].astype("datetime64[D]"))))
                res[(name, H, q)] = (len(g), len(g) / days, g.mean() if len(g) else np.nan)
    # selection on VALIDATION for each cost level, then read the untouched TEST result
    for C in COSTS:
        best = max(((H, q) for H in HS for q in QS),
                   key=lambda k: (res[("val",) + k][2] - C) * res[("val",) + k][0] if res[("val",) + k][0] >= 30 else -1e9)
        vn, vpd, vg = res[("val",) + best]
        tn, tpd, tg = res[("test",) + best]
        rows.append(dict(sym=SYM, test=f"{v1:%Y-%m}", C=C, H=best[0], q=best[1], val_net=vg - C, test_n=tn,
                         test_per_day=tpd, test_gross=tg, test_net=tg - C))
    # also log the full test grid (gross) for this fold, for the break-even analysis
    for H in HS:
        for q in QS:
            n_, pd_, g_ = res[("test", H, q)]
            rows.append(dict(sym=SYM, test=f"{v1:%Y-%m}", C=-1, H=H, q=q, test_n=n_, test_per_day=pd_, test_gross=g_))
    print(f"  fold test {v1:%Y-%m}: train {len(tr)} rows | " + " ".join(
        f"H{H}q{q}:{res[('test', H, q)][2]:+.2f}" for H in HS for q in (0.5, 2.0)) + f" | {time.time()-t0:.0f}s", flush=True)

out = pd.DataFrame(rows)
TAG = "zero" if os.environ.get("DATA_DIR") == "data_zero" else "wf"
os.makedirs(f"research/{TAG}", exist_ok=True)
out.to_csv(f"research/{TAG}/{SYM}{'_mid' if MID else ''}.csv", index=False)
sel = out[out.C >= 0]
pd.set_option("display.width", 200)
print(f"\n=== {SYM}: selected-on-validation, result on UNSEEN test blocks ===")
for C in COSTS:
    s = sel[sel.C == C]
    tot = (s.test_net * s.test_n).sum() / s.test_n.sum()
    print(f"cost {C:.1f} pip: folds positive {int((s.test_net > 0).sum())}/{len(s)} | net/trade (trade-weighted) {tot:+.3f} pips | "
          f"trades/day {s.test_per_day.mean():.1f} | per fold: " + " ".join(f"{r.test}:{r.test_net:+.2f}" for r in s.itertuples()))
g = out[out.C == -1].groupby(["H", "q"]).apply(lambda x: pd.Series({
    "gross_pips": (x.test_gross * x.test_n).sum() / x.test_n.sum(), "trades_day": x.test_per_day.mean(),
    "folds_gross_pos": f"{int((x.test_gross > 0).sum())}/{len(x)}"}), include_groups=False)
print("\nGROSS edge on unseen test blocks (= break-even round-trip cost in pips):")
print(g.round(3).to_string())
