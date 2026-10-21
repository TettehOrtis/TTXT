"""PREREG addendum 26: stop smaller than target. Usage: python aut41_rr.py STAGE VARIANT_INDEX | judge"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut30_cooldown_vol as K
import common as C

COMM = X.COMM
GRID = [(6.89, 5.11)] + [(s, t) for s in (2, 3, 4, 5) for t in (5, 8, 12, 20)]


def orders(F, i, side, S, T):
    m1 = F["m1"]
    nxt = np.clip(i + 1, 0, F["n"] - 1)
    px = F["o"][nxt] + np.where(side == 1, m1.spr.values[nxt], 0.0)
    sl = px - side * (S * 10 - COMM) / 10.0 if S != 6.89 else px - side * 6.89
    tp = px + side * (T * 10 + COMM) / 10.0 if T != 5.11 else px + side * 5.11
    return pd.DataFrame({"t_signal": F["tc"][i], "side": side, "entry_type": "market", "entry": np.nan, "expire": F["tc"][i],
                         "sl": sl, "tp": tp, "be_at_r": np.nan, "max_hold_min": 1440.0})


def st(s, days):
    u = s.usd_01.values
    tp, sl = (s.exit_reason == "TP").sum(), (s.exit_reason == "SL").sum()
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    cum = np.cumsum(u[np.argsort(pd.DatetimeIndex(s.t_exit).values, kind="stable")])
    w, l = s[s.exit_reason == "TP"], s[s.exit_reason == "SL"]
    return dict(trades=len(s), per_day=round(len(s) / days, 2), wr=round(100.0 * tp / max(1, tp + sl), 1), pf=round(gp / gl, 3) if gl else np.nan,
                usd_trade=round(u.mean(), 3), avg_win=round(w.usd_01.mean(), 1) if len(w) else np.nan, avg_loss=round(l.usd_01.mean(), 1) if len(l) else np.nan,
                net=round(u.sum()), max_dd=round((np.maximum.accumulate(cum) - cum).max()),
                min_win=round(w.hold_h.median() * 60, 0) if len(w) else np.nan, min_loss=round(l.hold_h.median() * 60, 0) if len(l) else np.nan)


if __name__ == "__main__":
    if sys.argv[1] != "judge":
        stage, vi = sys.argv[1], int(sys.argv[2])
        name = N.NAMES[vi]
        sp = N.spec_for(name)
        F = R.add_trends(X.build_stage(stage))
        ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
        ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
        i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        k = ok[i0] & (i0 < F["n"] - 3)
        i, side = i0[k], s0[k]
        rows = []
        for S, T in GRID:
            r = C.simulate(X.SYM, orders(F, i, side, S, T), one_at_a_time=False)
            r = r[r.status == "filled"].sort_values("t_signal", kind="stable").reset_index(drop=True)
            ts = pd.DatetimeIndex(r.t_signal).values.astype("datetime64[ns]").astype(np.int64)
            te = pd.DatetimeIndex(r.t_exit).values.astype("datetime64[ns]").astype(np.int64)
            s = r[K.select(ts, te, r.usd_01.values, 0, 0, 0)]
            x = st(s, F["days"])
            x.update(variant=name, stage=stage, stop=S, target=T)
            rows.append(x)
            print(name, stage, S, T, x["wr"], x["pf"], x["usd_trade"], x["avg_win"], x["avg_loss"], flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"rr_{stage}_{vi}.csv"), index=False)
        sys.exit()
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "rr_*_*.csv"))])
    d.to_csv(os.path.join(X.OUTD, "RR_ALL.csv"), index=False)
    order = ["EXPLORE", "VALIDATE", "STRESS"]
    pd.set_option("display.width", 250)
    res = {}
    for (S, T), _ in d[(d.stop != 6.89)].groupby(["stop", "target"]):
        n = 0
        for v, g in d.groupby("variant"):
            ok = True
            for s_ in order:
                x = g[(g.stage == s_) & (g.stop == S) & (g.target == T)].iloc[0]
                b = g[(g.stage == s_) & (g.stop == 6.89)].iloc[0]
                ok &= (x.pf > b.pf and x.usd_trade >= b.usd_trade and abs(x.avg_loss) < x.avg_win and x.max_dd <= b.max_dd)
            n += ok
        res[(S, T)] = int(n)
    print("pairs that work, variants of 5:", {k: v for k, v in res.items()})
    t = d.groupby(["stop", "target", "stage"])[["wr", "pf", "usd_trade", "avg_win", "avg_loss", "per_day", "max_dd", "min_win", "min_loss"]].mean().round(2)
    print(t.to_string())
