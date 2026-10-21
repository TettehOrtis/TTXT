"""PREREG addendum 25: which previous result earns full size. Usage: python aut39_sizefull.py STAGE VARIANT_INDEX | judge"""
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

WEAK = 0.4


def run(S, F, i, side, spec):
    """T3 + slot, returning also the result of the plain trade of each slot and the legs that hit."""
    fr, wts = B.SHAPES["T3"]
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
        r = S.trade(f, d, fill, stop, tps, wts, None, True, hold)
        if r is None:
            continue
        usd, k, how = r
        rb = S.trade(f, d, fill, stop, [fill + d * (tpd + cm) / 10.0], (1.0,), None, False, hold)
        busy = t1[max(k, rb[1])]
        rows.append((tc[q], t1[k], busy, d, usd, how[-1], sum(h == "TP" for h in how), rb[0]))
    return pd.DataFrame(rows, columns=["t_signal", "t_exit", "slot_end", "side", "usd_01", "last", "legs_tp", "plain_usd"])


def weights(r, mode):
    gap = np.r_[np.inf, (r.t_signal.values[1:] - r.slot_end.values[:-1]) / np.timedelta64(1, "m")]
    near = gap <= K.CHAIN_MIN
    u = r.usd_01.values
    prev = lambda a: np.r_[False, a[:-1]]            # noqa: E731
    if mode == "ANY":
        return np.where(prev(u > 0) & near, 1.0, WEAK)
    if mode == "FULLTP":
        return np.where(prev(r.legs_tp.values >= 3) & near, 1.0, WEAK)
    if mode == "PLAIN":
        return np.where(prev(r.plain_usd.values > 0) & near, 1.0, WEAK)
    if mode == "TIER3":
        full, early = prev(r.legs_tp.values >= 3) & near, prev((r.legs_tp.values < 3) & (u > 0)) & near
        return np.where(full, 1.0, np.where(early, 0.7, WEAK))
    raise KeyError(mode)


MODES = ["ANY", "FULLTP", "PLAIN", "TIER3"]

if __name__ == "__main__":
    if sys.argv[1] != "judge":
        stage, vi = sys.argv[1], int(sys.argv[2])
        name = N.NAMES[vi]
        sp = N.spec_for(name)
        F = R.add_trends(X.build_stage(stage))
        i, side = A.signals_for(F, sp)
        r = run(A.Sim(F), F, i, side, sp)
        rows = []
        for m in MODES:
            st = K.stat(r, weights(r, m), F["days"])
            st.update(variant=name, stage=stage, rule=m)
            rows.append(st)
            print(name, stage, m, st["wr"], st["pf"], st["net_day"], st["max_dd"], flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"sizefull_{stage}_{vi}.csv"), index=False)
        sys.exit()
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "sizefull_*_*.csv"))])
    d.to_csv(os.path.join(X.OUTD, "SIZEFULL_ALL.csv"), index=False)
    order = ["EXPLORE", "VALIDATE", "STRESS"]
    res = {}
    for m in MODES[1:]:
        n = 0
        for v, g in d.groupby("variant"):
            ok = True
            for s_ in order:
                x, b = (g[(g.stage == s_) & (g.rule == k)].iloc[0] for k in (m, "ANY"))
                ok &= x.pf > b.pf and x.net_unit >= b.net_unit and x.max_dd <= b.max_dd and x.worst_day >= b.worst_day
            n += ok
        res[m] = int(n)
    print("beats ANY for this many of 5 variants:", res)
    pd.set_option("display.width", 250)
    print(d.groupby(["stage", "rule"])[["wr", "pf", "net_day", "net_unit", "worst_day", "max_dd", "avg_size"]].mean().round(2).to_string())
