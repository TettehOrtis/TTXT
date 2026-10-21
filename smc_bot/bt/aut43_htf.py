"""PREREG addendum 28: higher-timeframe search for reward > risk. Usage: python aut43_htf.py STAGE | judge  (open windows only; BT20-fixed simulator)"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import common as C

TFS = ["M15", "H1", "H4"]
STRATS = ["DON", "PULL", "RSI", "MOM"]
KS = [1.0, 1.5, 2.0]
RRS = [1.5, 2.0, 3.0, 5.0]


def tf_signals(df, strat):
    c, o, h, l = df.close.values, df.open.values, df.high.values, df.low.values
    e20, e50, e200 = X.M._ema(c, 20), X.M._ema(c, 50), X.M._ema(c, 200)
    atr = X.A.atr(df, 14).values
    rsi = X.M._rsi(c, 14)
    up, dn = (e50 > e200) & (c > e200), (e50 < e200) & (c < e200)
    hh = pd.Series(h).shift(1).rolling(20).max().values
    ll = pd.Series(l).shift(1).rolling(20).min().values
    if strat == "DON":
        lg, sh = c > hh, c < ll
    elif strat == "PULL":
        lg, sh = up & (l <= e20) & (c > e20), dn & (h >= e20) & (c < e20)
    elif strat == "RSI":
        lg, sh = up & (rsi < 30), dn & (rsi > 70)
    else:
        body = np.abs(c - o)
        lg, sh = up & (c > o) & (body >= atr), dn & (c < o) & (body >= atr)
    ok = np.isfinite(atr) & (np.arange(len(c)) > 260)
    lg, sh = lg & ok, sh & ok
    both = lg & sh
    lg, sh = lg & ~both, sh & ~both
    idx = np.flatnonzero(lg | sh)
    return idx, np.where(lg[idx], 1, -1), atr


def build_orders(F, tclose, side, atr, K, RR):
    m1 = F["m1"]
    t1 = m1.time.values
    a = np.clip(np.searchsorted(t1, tclose), 0, len(t1) - 1)
    px = F["o"][a] + np.where(side == 1, m1.spr.values[a], 0.0)
    dist = K * atr
    o = pd.DataFrame({"t_signal": tclose, "side": side, "entry_type": "market", "entry": np.nan, "expire": tclose,
                      "sl": px - side * dist, "tp": px + side * RR * dist, "be_at_r": np.nan, "max_hold_min": 7200.0})
    return o[np.isfinite(o.sl) & (dist > 0)].reset_index(drop=True)


def stat(r):
    r = r[r.status == "filled"]
    if len(r) == 0:
        return dict(trades=0)
    x = r.R.values
    w, l = x[x > 0], x[x <= 0]
    return dict(trades=len(r), wr=round(100.0 * len(w) / len(x), 1), avg_win_R=round(w.mean(), 2) if len(w) else np.nan,
                avg_loss_R=round(l.mean(), 2) if len(l) else np.nan, pf=round(w.sum() / -l.sum(), 3) if len(l) and l.sum() < 0 else np.nan,
                exp_R=round(x.mean(), 3), usd_trade=round(r.usd_01.mean(), 2))


if __name__ == "__main__":
    if sys.argv[1] != "judge":
        stage = sys.argv[1]
        F = X.build_stage(stage)
        a, z = pd.Timestamp(F["a"]), pd.Timestamp(F["z"])
        rng = np.random.default_rng(11)
        rows = []
        for tf in TFS:
            df = C.bars(X.SYM, tf).reset_index(drop=True)
            for strat in STRATS:
                idx, side, atr_all = tf_signals(df, strat)
                tclose = df.tclose.values[idx]
                inw = (tclose >= np.datetime64(a)) & (tclose < np.datetime64(z))
                idx, side, tclose = idx[inw], side[inw], tclose[inw]
                atr = atr_all[idx]
                rnd = np.where(rng.random(len(side)) < 0.5, 1, -1)
                for K in KS:
                    for RR in RRS:
                        for kind, sd in (("SIG", side), ("RND", rnd)):
                            if len(sd) == 0:
                                continue
                            o = build_orders(F, tclose, sd, atr, K, RR)
                            r = C.simulate(X.SYM, o, one_at_a_time=True)
                            x = stat(r)
                            x.update(stage=stage, tf=tf, strat=strat, K=K, RR=RR, kind=kind)
                            rows.append(x)
                print(stage, tf, strat, len(idx), flush=True)
                pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"htf_{stage}.csv"), index=False)
        print("done", flush=True)
        sys.exit()
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "htf_*.csv"))])
    d.to_csv(os.path.join(X.OUTD, "HTF_ALL.csv"), index=False)
    order = ["EXPLORE", "VALIDATE", "STRESS"]
    pd.set_option("display.width", 250)

    def passes(g):
        ok = True
        for s in order:
            x = g[g.stage == s]
            if len(x) == 0:
                return False
            x = x.iloc[0]
            ok &= (x.trades >= 150) and (x.pf >= 1.10) and (x.exp_R > 0)
        return ok
    keys = ["tf", "strat", "K", "RR"]
    sig = d[d.kind == "SIG"]; rnd = d[d.kind == "RND"]
    res = []
    for k, g in sig.groupby(keys):
        gr = rnd[(rnd.tf == k[0]) & (rnd.strat == k[1]) & (rnd.K == k[2]) & (rnd.RR == k[3])]
        beat = all((g[g.stage == s].iloc[0].pf > gr[gr.stage == s].iloc[0].pf) for s in order if len(g[g.stage == s]) and len(gr[gr.stage == s]))
        res.append(dict(zip(keys, k), passes=passes(g), beats_random=beat, passes_all=bool(passes(g) and beat)))
    res = pd.DataFrame(res)
    chance = sum(passes(g) for _, g in rnd.groupby(keys))
    print("configurations", len(res), "| SIGNAL configs passing the PF/expectancy rule:", int(res.passes.sum()), "| also beating random:", int(res.passes_all.sum()),
          "| RANDOM-direction configs passing the same PF rule (chance rate):", int(chance))
    print(res[res.passes].to_string(index=False))
    t = sig.groupby(["tf", "strat", "stage"])[["wr", "avg_win_R", "avg_loss_R", "pf", "exp_R"]].mean().round(2)
    print(t.to_string())
