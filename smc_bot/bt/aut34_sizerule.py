"""PREREG addendum 18: full size after a win, smaller size otherwise. Usage: python aut34_sizerule.py STAGE VARIANT_INDEX"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut30_cooldown_vol as K
import aut33_stale as S
import common as C

RULES = [("FULL", 1.0), ("W3of5", 0.6), ("W2of5", 0.4), ("W1of5", 0.2)]


def stats(s, w, days):
    u = s.usd_01.values * w
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    day = pd.Series(u).groupby(pd.DatetimeIndex(s.t_exit).normalize()).sum()
    cum = np.cumsum(u[np.argsort(pd.DatetimeIndex(s.t_exit).values, kind="stable")])
    tp, sl = (s.exit_reason == "TP").sum(), (s.exit_reason == "SL").sum()
    return dict(trades=len(s), wr_count=round(100.0 * tp / max(1, tp + sl), 2), pf=round(gp / gl, 3), net=round(u.sum()),
                net_per_unit=round(u.sum() / w.sum(), 3), worst_day=round(day.min()), max_dd=round((np.maximum.accumulate(cum) - cum).max()),
                largest_loss=round(u.min(), 1), avg_size=round(w.mean(), 3), per_day=round(len(s) / days, 2))


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
    r = S.sim(F, i, side, sp["exit"])
    ts = pd.DatetimeIndex(r.t_signal).values.astype("datetime64[ns]").astype(np.int64)
    te = pd.DatetimeIndex(r.t_exit).values.astype("datetime64[ns]").astype(np.int64)
    keep = K.select(ts, te, r.usd_01.values, 0, 0, 0)
    s = r[keep].reset_index(drop=True)
    tk, ek = ts[keep], te[keep]
    gap = np.r_[np.inf, (tk[1:] - ek[:-1]) / 60e9]
    prev_win = np.r_[False, s.usd_01.values[:-1] > 0]
    full = (gap <= 2) & prev_win
    rows = []
    for rn, wk in RULES:
        w = np.where(full, 1.0, wk)
        st = stats(s, w, F["days"]); st.update(variant=name, stage=stage, rule=rn, full_share_pct=round(100 * full.mean(), 1))
        rows.append(st)
    pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"sizerule_{stage}_{vi}.csv"), index=False)
    print(pd.DataFrame(rows).to_string(index=False), flush=True)
