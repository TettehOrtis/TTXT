"""AUT16 addendum 9: account test on sealed data (third look). Needs AUTOPSY_EXAM_UNLOCK, SMC_DATASET=duka|zero."""
import os, sys, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

BALS, LOTS = [100, 500, 1000], [0.01, 0.02, 0.03, 0.05, 0.10]


def run_acct(u, bal):
    p = bal + np.cumsum(u)
    dead = np.flatnonzero(p <= 0)
    end = dead[0] + 1 if len(dead) else len(u)
    path = np.concatenate([[bal], p[:end]])
    peak = np.maximum.accumulate(path)
    return bool(len(dead)), float(path[-1]), float(path.min()), float(((peak - path) / peak).max() * 100), (int(dead[0]) if len(dead) else -1)


if __name__ == "__main__":
    feed = os.environ.get("SMC_DATASET", "zero")
    F = R.add_trends(X.build_stage("EXAM"))
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    cont, roll = [], []
    for n in N.NAMES:
        sp = N.spec_for(n)
        i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        k = ok[i0]
        r = C.simulate(X.SYM, R.orders(F, i0[k], s0[k], sp["exit"]), one_at_a_time=True)
        f = r[r.status == "filled"].sort_values("t_exit").reset_index(drop=True)
        te = pd.to_datetime(f.t_exit); tf = pd.to_datetime(f.t_fill)
        starts = pd.date_range(tf.min().normalize().replace(day=1), tf.max(), freq="MS")
        for bal, lot in itertools.product(BALS, LOTS):
            u = f.usd_01.values * (lot / 0.10)
            b, fin, low, drop, k0 = run_acct(u, bal)
            cont.append(dict(variant=n, feed=feed, start=bal, lot=lot, blown=b, blown_date=str(te[k0].date()) if b else "", final=round(fin), lowest=round(low), max_drop_pct=round(drop)))
            res = []
            for s in starts:
                e = s + pd.DateOffset(months=12)
                if e > tf.max():
                    continue
                m = ((tf >= s) & (tf < e)).values
                if m.sum() < 50:
                    continue
                bl, fn, lw, dr, _ = run_acct(u[m], bal)
                res.append((bl, fn, lw))
            if res:
                a = np.array(res, float)
                roll.append(dict(variant=n, feed=feed, start=bal, lot=lot, windows=len(res), pct_blown=round(100 * a[:, 0].mean()),
                                 median_final=round(float(np.median(a[:, 1]))), median_lowest=round(float(np.median(a[:, 2]))),
                                 worst_final=round(float(a[:, 1].min())), pct_ended_above_start=round(100 * (a[:, 1] > bal).mean())))
        print(n, len(f), flush=True)
        pd.DataFrame(cont).to_csv(os.path.join(X.OUTD, f"acctsealed_cont_{feed}.csv"), index=False)
        pd.DataFrame(roll).to_csv(os.path.join(X.OUTD, f"acctsealed_roll_{feed}.csv"), index=False)
    print("done", flush=True)
