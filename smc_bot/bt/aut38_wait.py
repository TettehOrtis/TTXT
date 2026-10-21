"""PREREG addendum 24: re-entry wait after the split exit. Usage: python aut38_wait.py STAGE VARIANT_INDEX | judge"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut35_shaperule as A
import aut36_splitbe as B
import aut37_combo as K
import common as C

WAITS = [0, 1, 2, 5, 10, 15, 30, 60, None]          # None = SLOT


def run(S, F, i, side, spec, shape, wait):
    fr, wts = B.SHAPES[shape] if shape else ((1.0,), (1.0,))
    be = shape is not None
    tpd, sld, hold = R.EXITS[spec["exit"]]
    cm = C.COMM[X.SYM]
    t1, o, s = S.t1, S.o, S.s
    tc = F["tc"][i]
    f_all = np.searchsorted(t1, tc, "left")
    rows, busy = [], np.datetime64("1970-01-01")
    for q in range(len(i)):
        d = int(side[q])
        if tc[q] < busy:
            continue
        f = int(f_all[q])
        if f >= S.n - 2:
            continue
        fill = o[f] + s[f] if d == 1 else o[f]
        stop = fill - d * (sld - cm) / 10.0
        tps = [fill + d * (x * tpd + cm) / 10.0 for x in fr]
        r = S.trade(f, d, fill, stop, tps, wts, None, be, hold)
        if r is None:
            continue
        usd, k, how = r
        slot_end = t1[k]
        if shape:
            rb = S.trade(f, d, fill, stop, [fill + d * (tpd + cm) / 10.0], (1.0,), None, False, hold)
            slot_end = t1[max(k, rb[1])]
        rel = slot_end if wait is None else min(t1[k] + np.timedelta64(int(wait), "m"), slot_end)
        busy = rel
        rows.append((tc[q], t1[k], rel, d, usd, how[-1], sum(h == "TP" for h in how)))
    return pd.DataFrame(rows, columns=["t_signal", "t_exit", "slot_end", "side", "usd_01", "last", "legs_tp"])


def pattern(r, name, stage):
    """tag each trade by how the PREVIOUS basket ended and the gap since it ended"""
    prev_usd = np.r_[np.nan, r.usd_01.values[:-1]]
    prev_tp = np.r_[-1, r.legs_tp.values[:-1]]
    gap = np.r_[np.inf, (r.t_signal.values[1:] - r.t_exit.values[:-1]) / np.timedelta64(1, "m")]
    kind = np.where(np.isnan(prev_usd), "first", np.where(prev_usd <= 0, "prev LOSS", np.where(prev_tp >= 3, "prev FULL target", "prev EARLY exit (small win)")))
    gb = pd.cut(gap, [-1, 2, 5, 15, 1e9], labels=["0-2 min", "2-5 min", "5-15 min", "15+ min"])
    d = pd.DataFrame(dict(kind=kind, gap=gb, usd=r.usd_01.values))
    g = d.groupby(["kind", "gap"], observed=True).usd.agg(n="size", win=lambda x: 100 * (x > 0).mean(), avg="mean", gp=lambda x: x[x > 0].sum(), gl=lambda x: -x[x <= 0].sum()).reset_index()
    g["pf"] = g.gp / g.gl
    g["variant"], g["stage"] = name, stage
    return g


if __name__ == "__main__":
    if sys.argv[1] == "judge":
        pass
    else:
        stage, vi = sys.argv[1], int(sys.argv[2])
        name = N.NAMES[vi]
        sp = N.spec_for(name)
        F = R.add_trends(X.build_stage(stage))
        i, side = A.signals_for(F, sp)
        S = A.Sim(F)
        base = run(S, F, i, side, sp, None, None)
        base_set = set(base.t_signal.values.astype("datetime64[ns]").astype(np.int64))
        rows, pat = [], None
        for w in WAITS:
            r = run(S, F, i, side, sp, "T3", w)
            ent = r.t_signal.values.astype("datetime64[ns]").astype(np.int64)
            same = float(np.mean([e in base_set for e in ent]) * 100)
            for sized in (False, True):
                st = K.stat(r, K.weights(r, sized), F["days"])
                st.update(variant=name, stage=stage, wait="SLOT" if w is None else w, sized=sized, same_as_plain_pct=round(same, 1),
                          cost_day=round(len(r) * 1.6 / F["days"], 2), early_exit_pct=round(100 * float(((r.legs_tp >= 1) & (r.legs_tp < 3)).mean()), 1))
                rows.append(st)
            print(name, stage, w, len(r), st["wr"], st["pf"], st["net_day"], flush=True)
            if w == 0:
                pat = pattern(r, name, stage)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"wait_{stage}_{vi}.csv"), index=False)
        pat.to_csv(os.path.join(X.OUTD, f"waitpat_{stage}_{vi}.csv"), index=False)
        print("done", flush=True)
        sys.exit()
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "wait_*_*.csv"))])
    d["wait"] = d["wait"].astype(str)
    d.to_csv(os.path.join(X.OUTD, "WAIT_ALL.csv"), index=False)
    order = ["EXPLORE", "VALIDATE", "STRESS"]
    pd.set_option("display.width", 250)
    for sized in (False, True):
        res = {}
        for w in [str(x) for x in WAITS if x is not None]:
            n = 0
            for v, g in d[d.sized == sized].groupby("variant"):
                ok = True
                for s_ in order:
                    x = g[(g.stage == s_) & (g["wait"] == w)].iloc[0]
                    b = g[(g.stage == s_) & (g["wait"] == "SLOT")].iloc[0]
                    ok &= x.pf > b.pf and x.net_day >= b.net_day and x.max_dd <= b.max_dd
                n += ok
            res[w] = n
        print("sized" if sized else "not sized", "- WAIT N beats SLOT for this many of 5 variants:", res)
        print(d[d.sized == sized].groupby(["stage", "wait"])[["per_day", "wr", "pf", "net_day", "net_unit", "worst_day", "max_dd", "cost_day", "same_as_plain_pct", "early_exit_pct"]].mean().round(2).to_string())
    p = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "waitpat_*_*.csv"))])
    p.to_csv(os.path.join(X.OUTD, "WAITPAT_ALL.csv"), index=False)
    t = p.groupby(["kind", "gap"]).apply(lambda g: pd.Series(dict(n=g.n.sum(), win=(g.win * g.n).sum() / g.n.sum(), avg_usd=(g.avg * g.n).sum() / g.n.sum(),
                                                                  pf=g.gp.sum() / g.gl.sum())))
    print(t.round(2).to_string())
