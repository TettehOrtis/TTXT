"""Are re-entries right after a close, or entries delayed by a minute, worse? Usage: python aut33_stale.py STAGE VARIANT_INDEX
Open windows only. Normal = entry at the next bar open. Delayed = same signal, entry one bar later (60 s stale)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut30_cooldown_vol as K
import common as C


def sim(F, i, side, ex):
    r = C.simulate(X.SYM, R.orders(F, i, side, ex), one_at_a_time=False)
    return r[r.status == "filled"].sort_values("t_signal", kind="stable").reset_index(drop=True)


def stat(r):
    u = r.usd_01.values
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    tp, sl = (r.exit_reason == "TP").sum(), (r.exit_reason == "SL").sum()
    return dict(trades=len(r), wr=round(100.0 * tp / max(1, tp + sl), 2), pf=round(gp / gl, 3) if gl > 0 else np.nan, usd_trade=round(u.mean(), 3))


if __name__ == "__main__":
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
    for label, shift in (("NORMAL", 0), ("DELAY60S", 1)):
        r = sim(F, i + shift, side, sp["exit"])
        ts = pd.DatetimeIndex(r.t_signal).values.astype("datetime64[ns]").astype(np.int64)
        te = pd.DatetimeIndex(r.t_exit).values.astype("datetime64[ns]").astype(np.int64)
        keep = K.select(ts, te, r.usd_01.values, 0, 0, 0)
        s = r[keep].reset_index(drop=True)
        ts_k, te_k = ts[keep], te[keep]
        gap = np.r_[np.inf, (ts_k[1:] - te_k[:-1]) / 60e9]          # minutes between the previous exit and this signal
        prev_win = np.r_[False, s.usd_01.values[:-1] > 0]
        groups = {"ALL": np.ones(len(s), bool), "CHAIN(<=2min after prev exit)": gap <= 2, "CHAIN after a WIN": (gap <= 2) & prev_win,
                  "CHAIN after a LOSS": (gap <= 2) & ~prev_win, "FRESH(>2min)": gap > 2}
        for g, m in groups.items():
            st = stat(s[m]); st.update(variant=name, stage=stage, entry=label, group=g)
            rows.append(st)
    pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"stale_{stage}_{vi}.csv"), index=False)
    print(pd.DataFrame(rows).to_string(index=False), flush=True)
