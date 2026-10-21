"""PREREG addendum 20: break-even and time exits. Usage: python aut36_manage.py STAGE VARIANT_INDEX"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut30_cooldown_vol as K
import common as C

BE = [0.4, 0.6, 0.8]
HOLD = [5, 15, 30, 60, 120]


def stats(s, days):
    u = s.usd_01.values
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    day = pd.Series(u).groupby(pd.DatetimeIndex(s.t_exit).normalize()).sum()
    cum = np.cumsum(u[np.argsort(pd.DatetimeIndex(s.t_exit).values, kind="stable")])
    return dict(trades=len(s), per_day=round(len(s) / days, 2), win_pct=round(100 * (u > 0).mean(), 2),
                stop_share=round(100 * (s.exit_reason == "SL").mean(), 1), pf=round(gp / gl, 3), usd_trade=round(u.mean(), 3),
                net=round(u.sum()), largest_loss=round(u.min(), 1), worst_day=round(day.min()), max_dd=round((np.maximum.accumulate(cum) - cum).max()))


def run(F, o, label, days, rows, name, stage):
    r = C.simulate(X.SYM, o, one_at_a_time=False)
    r = r[r.status == "filled"].sort_values("t_signal", kind="stable").reset_index(drop=True)
    ts = pd.DatetimeIndex(r.t_signal).values.astype("datetime64[ns]").astype(np.int64)
    te = pd.DatetimeIndex(r.t_exit).values.astype("datetime64[ns]").astype(np.int64)
    s = r[K.select(ts, te, r.usd_01.values, 0, 0, 0)]
    st = stats(s, days)
    st.update(variant=name, stage=stage, rule=label)
    rows.append(st)
    print(name, stage, label, st["pf"], st["usd_trade"], st["stop_share"], flush=True)


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
    base = R.orders(F, i, side, sp["exit"])
    rows = []
    run(F, base, "BASE", F["days"], rows, name, stage)
    risk = (base.entry.isna() * 0 + 1) * 0  # placeholder to keep dtype simple
    nxt = np.clip(i + 1, 0, F["n"] - 1)
    px = F["o"][nxt] + np.where(side == 1, F["m1"].spr.values[nxt], 0.0)
    rk = np.abs(px - base.sl.values)
    td = np.abs(base.tp.values - px)
    for fr in BE:
        o = base.copy()
        o["be_at_r"] = fr * td / rk
        run(F, o, f"BE{int(fr * 100)}", F["days"], rows, name, stage)
    for n in HOLD:
        o = base.copy()
        o["max_hold_min"] = float(n)
        run(F, o, f"HOLD{n}", F["days"], rows, name, stage)
    pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"manage_{stage}_{vi}.csv"), index=False)
    print("done", flush=True)
