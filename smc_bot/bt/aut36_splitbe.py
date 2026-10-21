"""PREREG addendum 22: repair attempts for the split-then-move-stop-to-entry rule. Reuses the aut35 simulator.
Usage: python aut36_splitbe.py STAGE VARIANT_INDEX   |   python aut36_splitbe.py judge
Open windows only."""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut35_shaperule as A
import common as C

# shape -> (target fractions, weights); every shape moves the stop of the other legs to entry+commission after the first leg hits
SHAPES = {"S5BE": ((.2, .4, .6, .8, 1.0), (.2,) * 5), "H2_40": ((.4, 1.0), (.5, .5)), "H2_60": ((.6, 1.0), (.5, .5)),
          "H2_80": ((.8, 1.0), (.5, .5)), "H5_40": ((.4, .55, .7, .85, 1.0), (.2,) * 5), "H5_60": ((.6, .7, .8, .9, 1.0), (.2,) * 5),
          "T3": ((.5, .75, 1.0), (.2, .3, .5))}


def run(S, F, i, side, spec, shape, slot):
    fr, wts = SHAPES[shape] if shape else ((1.0,), (1.0,))
    be = shape is not None
    tpd, sld, hold = R.EXITS[spec["exit"]]
    cm = C.COMM[X.SYM]
    t1, o, s = S.t1, S.o, S.s
    tc = F["tc"][i]
    f_all = np.searchsorted(t1, tc, "left")
    rows = []
    busy = np.datetime64("1970-01-01")
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
        if slot and shape:                                           # the plain trade would still be open: keep the slot closed
            rb = S.trade(f, d, fill, stop, [fill + d * (tpd + cm) / 10.0], (1.0,), None, False, hold)
            kb = max(k, rb[1])
        busy = t1[kb]
        rows.append((tc[q], t1[k], d, usd, how[-1], sum(h == "TP" for h in how), (t1[k] - t1[f]) / np.timedelta64(1, "m")))
    return pd.DataFrame(rows, columns=["t_signal", "t_exit", "side", "usd_01", "last", "legs_tp", "hold_min"])


def judge():
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "splitbe_*_*.csv"))])
    d["net_day"] = d.net / d.days
    d.to_csv(os.path.join(X.OUTD, "SPLITBE_ALL.csv"), index=False)
    order = ["EXPLORE", "VALIDATE", "STRESS"]
    res = []
    for (v, rule), g in d[d.rule != "BASE"].groupby(["variant", "rule"]):
        ok_all, bits = True, {}
        for s_ in order:
            x = g[g.stage == s_].iloc[0]
            b = d[(d.variant == v) & (d.rule == "BASE") & (d.stage == s_)].iloc[0]
            t = (x.per_day >= 0.98 * b.per_day, x.wr >= b.wr + 1.0, x.pf > b.pf, x.net_day >= b.net_day, x.max_dd <= b.max_dd)
            ok_all &= all(t)
            bits[s_] = f"trades {100 * x.per_day / b.per_day:.0f}% wr {x.wr - b.wr:+.1f} pf {x.pf - b.pf:+.3f} $/day {x.net_day - b.net_day:+.2f} dd {x.max_dd - b.max_dd:+.0f}"
        res.append(dict(variant=v, rule=rule, works=bool(ok_all), **bits))
    r = pd.DataFrame(res)
    r.to_csv(os.path.join(X.OUTD, "SPLITBE_JUDGED.csv"), index=False)
    print("variants helped (of 5) per rule:\n", r.groupby("rule").works.sum().to_string())
    rows = []
    for rule in ["BASE"] + sorted(set(d.rule) - {"BASE"}):
        out = {"rule": rule}
        for s_ in order:
            a = d[(d.rule == rule) & (d.stage == s_)].mean(numeric_only=True)
            out[s_] = f"trades/day {a.per_day:.1f} win {a.wr:.1f}% $/trade {a.usd_trade:+.2f} $/day {a.net_day:+.2f} drop {a.max_dd:.0f}"
        rows.append(out)
    pd.set_option("display.width", 250, "display.max_colwidth", 100)
    print(pd.DataFrame(rows).to_string(index=False))


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
    jobs = [("BASE", None, False)] + [(f"{sh}_{m}", sh, m == "SLOT") for sh in SHAPES for m in ("FREE", "SLOT")]
    for rule, shape, slot in jobs:
        r = run(S, F, i, side, sp, shape, slot)
        st = A.stat(r, F["days"])
        st.update(variant=name, stage=stage, rule=rule, days=F["days"])
        rows.append(st)
        print(name, stage, rule, st["trades"], st["wr"], st["pf"], st["usd_trade"], st["max_dd"], flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"splitbe_{stage}_{vi}.csv"), index=False)
    print("done", flush=True)
