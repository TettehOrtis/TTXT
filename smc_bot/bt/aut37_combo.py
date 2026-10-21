"""PREREG addendum 23: size rule + T3 split shape + slot, together. Usage: python aut37_combo.py STAGE VARIANT_INDEX | judge"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut35_shaperule as A
import aut36_splitbe as B
import common as C

WEAK = 0.4
CHAIN_MIN = 2.0


def run(S, F, i, side, spec, shape, slot):
    """as aut36_splitbe.run, plus the time the slot is released (slot_end)."""
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
        kb = k
        if slot and shape:
            rb = S.trade(f, d, fill, stop, [fill + d * (tpd + cm) / 10.0], (1.0,), None, False, hold)
            kb = max(k, rb[1])
        busy = t1[kb]
        rows.append((tc[q], t1[k], t1[kb], d, usd, how[-1]))
    return pd.DataFrame(rows, columns=["t_signal", "t_exit", "slot_end", "side", "usd_01", "last"])


def weights(r, sized):
    if not sized:
        return np.ones(len(r))
    win = (r.usd_01.values > 0)
    gap = np.r_[np.inf, (r.t_signal.values[1:] - r.slot_end.values[:-1]) / np.timedelta64(1, "m")]
    full = np.r_[False, win[:-1]] & (gap <= CHAIN_MIN)
    return np.where(full, 1.0, WEAK)


def stat(r, w, days):
    u = r.usd_01.values * w
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    order = np.argsort(r.t_exit.values, kind="stable")
    cum = np.cumsum(u[order])
    day = pd.Series(u).groupby(pd.DatetimeIndex(r.t_exit).normalize()).sum()
    return dict(trades=len(r), per_day=round(len(r) / days, 2), wr=round(100.0 * (r.usd_01.values > 0).mean(), 2), pf=round(gp / gl, 3),
                net=round(u.sum()), net_day=round(u.sum() / days, 2), net_unit=round(u.sum() / w.sum(), 3), worst_day=round(day.min()),
                max_dd=round((np.maximum.accumulate(cum) - cum).max()), avg_size=round(w.mean(), 3), days=days)


def judge():
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "combo_*_*.csv"))])
    d.to_csv(os.path.join(X.OUTD, "COMBO_ALL.csv"), index=False)
    order = ["EXPLORE", "VALIDATE", "STRESS"]
    works, rows = 0, []
    for v, g in d.groupby("variant"):
        ok = True
        for s_ in order:
            x, b = (g[(g.stage == s_) & (g.rule == k)].iloc[0] for k in ("T3S_SIZE", "BASE"))
            t3, sz = g[(g.stage == s_) & (g.rule == "T3S")].iloc[0], g[(g.stage == s_) & (g.rule == "SIZE")].iloc[0]
            ok &= (x.per_day >= 0.98 * b.per_day and x.wr >= b.wr + 1 and x.pf > b.pf and x.pf > t3.pf and x.pf > sz.pf and x.net_unit >= b.net_unit
                   and x.max_dd <= b.max_dd and x.worst_day >= b.worst_day)
        works += ok
        rows.append((v, ok))
    print("T3S_SIZE works for", works, "of 5 variants")
    pd.set_option("display.width", 250)
    print(d.groupby(["stage", "rule"])[["per_day", "wr", "pf", "net_day", "net_unit", "worst_day", "max_dd", "avg_size"]].mean().round(2).to_string())


if __name__ == "__main__":
    if sys.argv[1] == "judge":
        judge()
        sys.exit()
    stage, vi = sys.argv[1], int(sys.argv[2])
    name = N.NAMES[vi]
    sp = N.spec_for(name)
    F = R.add_trends(X.build_stage(stage))
    i, side = A.signals_for(F, sp)
    S = A.Sim(F)
    rows = []
    plain = run(S, F, i, side, sp, None, False)
    t3 = run(S, F, i, side, sp, "T3", True)
    for rule, r, sized in (("BASE", plain, False), ("SIZE", plain, True), ("T3S", t3, False), ("T3S_SIZE", t3, True)):
        st = stat(r, weights(r, sized), F["days"])
        st.update(variant=name, stage=stage, rule=rule)
        rows.append(st)
        print(name, stage, rule, st["per_day"], st["wr"], st["pf"], st["net_day"], st["max_dd"], flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"combo_{stage}_{vi}.csv"), index=False)
    print("done", flush=True)
