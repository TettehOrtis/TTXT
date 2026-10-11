"""ML filter for SMC setups, walk-forward (the user's protocol):
6-month blocks; for each test block:  TRAIN = all setups before the validation block,
VALIDATE = the 6 months before the test block (choose the score threshold), TEST = the next 6 months (never seen).
All pairs are pooled (more data -> the model learns what makes a good SMC setup anywhere).
Reports rules-only vs rules+ML, in R and net pips, per block and per pair.
Usage: python smc_bot/ml_walkforward.py
"""
import os, glob
import numpy as np
import pandas as pd
import lightgbm as lgb

HERE = os.path.dirname(os.path.abspath(__file__))
FEATS = ["dir", "hour", "dow", "zone_size_atr", "zone_age_h", "pos_in_h1_range", "discount_ok", "swept", "bars_to_choch", "rr",
         "risk_pips", "risk_atr5", "tp_atr_h1", "m5_ema_ok", "h1_ema_ok", "m30_ema_ok", "h1_ema_dist", "m30_ema_dist", "m15_agree",
         "atr5_pips", "atr_h1_pips", "zone_kind_c", "entry_kind_c", "sym_c"]
params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=60, feature_fraction=0.7,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, verbose=-1, num_threads=6)


def load_all():
    df = pd.concat([pd.read_parquet(f) for f in glob.glob(os.path.join(HERE, "out", "setups_*.parquet"))], ignore_index=True)
    df = df[df.status == "traded"].copy()
    df["zone_kind_c"] = (df.zone_kind == "OB").astype(int)
    df["entry_kind_c"] = (df.entry_kind == "OB").astype(int)
    df["sym_c"] = df.sym.astype("category").cat.codes
    for c in ("discount_ok", "swept", "m5_ema_ok", "h1_ema_ok", "m30_ema_ok"):
        df[c] = df[c].astype(float)
    df["r_clip"] = df.r_net.clip(-2, 6)
    return df.sort_values("t_setup").reset_index(drop=True)


def main():
    df = load_all()
    t = df.t_setup.values
    blocks = pd.date_range("2022-01-01", "2027-01-01", freq="6MS")
    rows, test_parts = [], []
    for i in range(1, len(blocks) - 1):
        v0, v1, t1 = blocks[i - 1], blocks[i], blocks[i + 1]
        tr = df[t < np.datetime64(v0)]; va = df[(t >= np.datetime64(v0)) & (t < np.datetime64(v1))]
        te = df[(t >= np.datetime64(v1)) & (t < np.datetime64(t1))]
        if len(tr) < 300 or len(va) < 30 or len(te) == 0:
            continue
        m = lgb.train(params, lgb.Dataset(tr[FEATS].values.astype(np.float32), tr.r_clip.values), num_boost_round=300)
        pv = m.predict(va[FEATS].values.astype(np.float32)); pt = m.predict(te[FEATS].values.astype(np.float32))
        # threshold chosen on VALIDATION: best mean R among score quantiles that keep >= 25% of setups
        best_q, best_r = 0.0, va.r_net.mean()
        for q in (0.25, 0.5, 0.75):
            thr = np.quantile(pv, q)
            r = va.r_net.values[pv >= thr].mean()
            if r > best_r:
                best_q, best_r = q, r
        thr = np.quantile(pv, best_q) if best_q > 0 else -np.inf
        sel = te[pt >= thr].assign(score=pt[pt >= thr])
        test_parts.append(sel.assign(block=f"{v1:%Y-%m}"))
        rows.append(dict(test=f"{v1:%Y-%m}", n_rules=len(te), R_rules=te.r_net.mean(), win_rules=(te.r_net > 0).mean() * 100,
                         kept_q=best_q, n_ml=len(sel), R_ml=sel.r_net.mean() if len(sel) else np.nan,
                         win_ml=(sel.r_net > 0).mean() * 100 if len(sel) else np.nan, pips_ml=sel.net_pips.mean() if len(sel) else np.nan))
    res = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print("UNSEEN test blocks - rules only vs rules + ML filter (R = net result / risk, after real Zero commission):")
    print(res.round(3).to_string(index=False))
    allt = pd.concat(test_parts) if test_parts else pd.DataFrame()
    te_all = df[df.t_setup >= pd.Timestamp(res.test.iloc[0] + "-01")] if len(res) else df
    print(f"\nRULES ONLY  : n={len(te_all)} avgR={te_all.r_net.mean():+.3f} win%={(te_all.r_net > 0).mean() * 100:.1f} "
          f"pips/trade={te_all.net_pips.mean():+.2f}")
    if len(allt):
        print(f"RULES + ML  : n={len(allt)} avgR={allt.r_net.mean():+.3f} win%={(allt.r_net > 0).mean() * 100:.1f} "
              f"pips/trade={allt.net_pips.mean():+.2f} | blocks positive {int((res.R_ml > 0).sum())}/{len(res)}")
        print("\nper pair (rules+ML, unseen):")
        print(allt.groupby("sym").agg(n=("r_net", "size"), avgR=("r_net", "mean"), win=("r_net", lambda x: (x > 0).mean() * 100),
                                      pips=("net_pips", "mean")).round(3).to_string())
        allt.to_parquet(os.path.join(HERE, "out", "ml_test_trades.parquet"), index=False)
    # feature importance of a model on everything (what the ML learned)
    m = lgb.train(params, lgb.Dataset(df[FEATS].values.astype(np.float32), df.r_clip.values), num_boost_round=300)
    imp = pd.Series(m.feature_importance("gain"), index=FEATS).sort_values(ascending=False)
    print("\nwhat matters most (gain share):", ", ".join(f"{k} {v / imp.sum() * 100:.0f}%" for k, v in imp.head(10).items()))


if __name__ == "__main__":
    main()
