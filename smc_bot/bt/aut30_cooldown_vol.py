"""AUT16 addendum 13: cooldown after a loss and volatility-sized exits. Usage: python aut30_cooldown_vol.py STAGE VARIANT_INDEX (0..4)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

KT = {"E30_70": 2.122, "E50_70": 3.486}
KS = 4.7
POLICIES = [("CD0", 0, 0, 0), ("CD1", 1, 0, 0), ("CD2", 2, 0, 0), ("CD3", 3, 0, 0), ("CD5", 5, 0, 0), ("CD10", 10, 0, 0), ("CD15", 15, 0, 0),
            ("S2W10", 0, 2, 10), ("S2W30", 0, 2, 30)]      # name, cooldown after any loss, streak length, wait after streak
NS = 60_000_000_000


def atr_orders(F, i, side, kt, ks):
    nxt = np.clip(i + 1, 0, F["n"] - 1)
    px = F["o"][nxt] + np.where(side == 1, F["m1"].spr.values[nxt], 0.0)
    at = F["atr15"][i]
    o = pd.DataFrame({"t_signal": F["tc"][i], "side": side, "entry_type": "market", "entry": np.nan, "expire": F["tc"][i],
                      "sl": px - side * ks * at, "tp": px + side * kt * at, "be_at_r": np.nan, "max_hold_min": 1440.0})
    return o[np.isfinite(o.sl) & np.isfinite(o.tp)].reset_index(drop=True)


def select(ts, te, u, cd, streak_k, streak_wait):
    keep = np.zeros(len(ts), bool)
    busy = np.iinfo(np.int64).min
    cool = busy
    streak = 0
    for k in range(len(ts)):
        t = ts[k]
        if t < busy or t < cool:
            continue
        keep[k] = True
        busy = te[k]
        if u[k] < 0:
            streak += 1
            if cd:
                cool = max(cool, te[k] + cd * NS)
            if streak_k and streak >= streak_k:
                cool = max(cool, te[k] + streak_wait * NS)
        else:
            streak = 0
    return keep


def stats(t_exit, u, days):
    d = pd.Series(u).groupby(pd.DatetimeIndex(t_exit).normalize()).agg(["sum", "size"])
    cum = np.cumsum(u[np.argsort(t_exit)])
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    return dict(trades=len(u), per_day=round(len(u) / days, 2), win_pct=round(100 * (u > 0).mean(), 1), pf=round(gp / gl, 2) if gl > 0 else np.nan,
                net=round(u.sum()), usd_trade=round(u.mean(), 3), largest_loss=round(u.min(), 1), worst_day=round(d["sum"].min()),
                busiest_day_trades=int(d["size"].max()), max_dd=round((np.maximum.accumulate(cum) - cum).max()))


if __name__ == "__main__":
    stage, vi = sys.argv[1], int(sys.argv[2])
    name = N.NAMES[vi]
    sp = N.spec_for(name)
    F = R.add_trends(X.build_stage(stage))
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
    k = ok[i0]
    i, side = i0[k], s0[k]
    kt0 = KT[sp["exit"]]
    exits = [("FIXED", R.orders(F, i, side, sp["exit"])), ("A_cal", atr_orders(F, i, side, kt0, KS)), ("A_tight", atr_orders(F, i, side, kt0, 3.5)),
             ("A_wide", atr_orders(F, i, side, kt0, 6.0)), ("A_bigT", atr_orders(F, i, side, kt0 * 1.5, KS))]
    rows = []
    for ename, o in exits:
        r = C.simulate(X.SYM, o, one_at_a_time=False)
        r = r[r.status == "filled"].sort_values("t_signal", kind="stable")
        ts = pd.DatetimeIndex(r.t_signal).values.astype("datetime64[ns]").astype(np.int64)
        te = pd.DatetimeIndex(r.t_exit).values.astype("datetime64[ns]").astype(np.int64)
        u = r.usd_01.values
        for pname, cd, sk, sw in POLICIES:
            keep = select(ts, te, u, cd, sk, sw)
            st = stats(r.t_exit.values[keep], u[keep], F["days"])
            st.update(variant=name, stage=stage, exit=ename, policy=pname, simulated=len(r))
            rows.append(st)
        print(name, stage, ename, "base", rows[-len(POLICIES)]["trades"], rows[-len(POLICIES)]["usd_trade"], flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"cdvol_{stage}_{vi}.csv"), index=False)
    print("done", flush=True)
