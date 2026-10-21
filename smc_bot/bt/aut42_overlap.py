"""PREREG addendum 27: does the edge survive when the next entry cannot start in the bar where the previous trade exits? Usage: python aut42_overlap.py STAGE VARIANT_INDEX | judge"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

NS = 60_000_000_000


def select(ts, te, pad):
    keep = np.zeros(len(ts), bool)
    busy = np.iinfo(np.int64).min
    for k in range(len(ts)):
        if ts[k] < busy:
            continue
        keep[k] = True
        busy = te[k] + pad
    return keep


def st(s, days):
    u = s.usd_01.values
    tp, sl = (s.exit_reason == "TP").sum(), (s.exit_reason == "SL").sum()
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    return dict(trades=len(s), per_day=round(len(s) / days, 2), wr=round(100.0 * tp / max(1, tp + sl), 1), pf=round(gp / gl, 3) if gl else np.nan,
                usd_trade=round(u.mean(), 3), net=round(u.sum()))


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
        r = C.simulate(X.SYM, R.orders(F, i, side, sp["exit"]), one_at_a_time=False)
        r = r[r.status == "filled"].sort_values("t_signal", kind="stable").reset_index(drop=True)
        ts = pd.DatetimeIndex(r.t_signal).values.astype("datetime64[ns]").astype(np.int64)
        te = pd.DatetimeIndex(r.t_exit).values.astype("datetime64[ns]").astype(np.int64)
        rows = []
        for label, pad in (("A_current", 0), ("B_fixed", NS)):
            x = st(r[select(ts, te, pad)], F["days"])
            x.update(variant=name, stage=stage, rule=label)
            rows.append(x)
            print(name, stage, label, x["trades"], x["wr"], x["pf"], x["usd_trade"], flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"overlap_{stage}_{vi}.csv"), index=False)
        sys.exit()
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "overlap_*_*.csv"))])
    d.to_csv(os.path.join(X.OUTD, "OVERLAP_ALL.csv"), index=False)
    pd.set_option("display.width", 200)
    print(d.groupby(["stage", "rule"])[["per_day", "wr", "pf", "usd_trade"]].mean().round(2).to_string())
    n = 0
    for v, g in d.groupby("variant"):
        n += all((g[(g.stage == s) & (g.rule == "B_fixed")].iloc[0].pf > 1.0) and (g[(g.stage == s) & (g.rule == "B_fixed")].iloc[0].usd_trade >= 0) for s in ("EXPLORE", "VALIDATE", "STRESS"))
    print("corrected rule keeps PF>1 and $/trade>=0 in all 3 windows for", n, "of 5 variants")
    print(d[d.rule == "B_fixed"].pivot_table(index="variant", columns="stage", values="pf").round(2).to_string())
