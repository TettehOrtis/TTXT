"""AUT16 addendum 7: daily profit target / loss limit replay. Usage: python aut23_daily.py STAGE (open windows only)"""
import os, sys, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

XS, YS = [None, 100, 200, 300], [None, 100, 200, 300]


def replay(t, x, y):
    t = t.sort_values("t_fill").reset_index(drop=True)
    day = pd.to_datetime(t.t_fill).dt.date.values
    u = t.usd_01.values
    keep = np.zeros(len(t), bool)
    cur, pnl = None, 0.0
    for k in range(len(t)):
        if day[k] != cur:
            cur, pnl = day[k], 0.0
        if (x is not None and pnl >= x) or (y is not None and pnl <= -y):
            continue
        keep[k] = True
        pnl += u[k]
    return t[keep]


def summarize(t, days):
    d = t.groupby(pd.to_datetime(t.t_exit).dt.date).usd_01.sum()
    d.index = pd.to_datetime(d.index)
    w = d.resample("W").sum()
    w = w[w != 0]
    cum = t.sort_values("t_exit").usd_01.cumsum().values
    return dict(trades=len(t), per_day=round(len(t) / days, 2), win_days=round(100 * (d > 0).mean(), 1), avg_day=round(d.mean(), 1),
                worst_day=round(d.min(), 0), worst_week=round(w.min(), 0), net=round(t.usd_01.sum(), 0),
                max_dd=round((np.maximum.accumulate(cum) - cum).max(), 0))


if __name__ == "__main__":
    stage = sys.argv[1]
    F = R.add_trends(X.build_stage(stage))
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    rows = []
    for n in N.NAMES:
        sp = N.spec_for(n)
        i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        k = ok[i0]
        r = C.simulate(X.SYM, R.orders(F, i0[k], s0[k], sp["exit"]), one_at_a_time=True)
        f = r[r.status == "filled"].copy()
        for x, y in itertools.product(XS, YS):
            st = summarize(replay(f, x, y), F["days"])
            st.update(variant=n, stage=stage, target=x if x else 0, limit=y if y else 0)
            rows.append(st)
        print(n, len(f), flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"daily_{stage}.csv"), index=False)
    print("done", flush=True)
