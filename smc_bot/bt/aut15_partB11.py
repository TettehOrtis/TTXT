"""AUT15 Part B for an AUT11 daily-trend variant (its own daily walker, aut11_trend imported read-only).
Usage: python aut15_partB11.py TS20 TRAIL      (rule, exit mode).  24 FX pairs; gold is not part of the headline view.
Commission and spread are both multiplied by the cost factor (a.comm and a.spr of the cached daily arrays).
Stops are anchored on the signal-day close, so the stop price does not move with the cost."""
import os
import sys
import json
import time

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut11_trend as T                                                   # noqa: E402
import common as C                                                        # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT15_costcut")
FACS = [1.0, 0.75, 0.5, 0.3125, 0.15625, 0.0]
STG = ["EXPLORE", "VALIDATE", "STRESS"]
_SPR0 = {}


def set_cost(f):
    for s in T.SYMS:
        a = T.load(s)
        if s not in _SPR0:
            _SPR0[s] = a.spr.copy()
        a.comm = C.COMM[s] * f
        a.spr = _SPR0[s] * f


def gt(x):
    x = np.asarray(x, float)
    return float(1.96 * x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else np.nan


def cell(u):
    u = np.asarray(u, float)
    return dict(n=int(len(u)), usd=float(u.mean()) if len(u) else np.nan, gt=gt(u), win=float(100 * (u > 0).mean()) if len(u) else np.nan)


def main(rule, mode):
    t0 = time.time()
    name = "AUT11_%s_%s_FX24" % (rule, mode)
    out = dict(name=name, key="AUT11__%s_%s" % (rule, mode), view="FX24", cost_menu_factors=FACS)
    res = {}
    for f in FACS:
        set_cost(f)
        for st in STG:
            res[(f, st)] = T.run_rule(rule, mode, st, syms=T.FX)
        print("  menu f=%g %.0fs" % (f, time.time() - t0), flush=True)
    set_cost(1.0)
    # all-stage trades at today's cost
    base = pd.concat([res[(1.0, st)].assign(period=st) for st in STG], ignore_index=True)
    for f in FACS:
        base["usd_menu_%g" % f] = np.concatenate([res[(f, st)].usd_01.values for st in STG])
    base.to_csv(os.path.join(OUT, "trades_%s_ALLSTAGES.csv" % name), index=False)
    rows = []
    for f in FACS:
        for st in STG:
            g = res[(f, st)]
            for nm, sd in (("all", None), ("buy", 1), ("sell", -1)):
                h = g if sd is None else g[g.side == sd]
                rows.append(dict(factor=f, stage=st, side=nm, **cell(h.usd_01.values), pips=float(h.net_pips.mean()) if len(h) else np.nan,
                                 nights=float(h.nights.mean()) if len(h) else np.nan))
            for s2, h in g.groupby("sym"):
                rows.append(dict(factor=f, stage=st, side="sym:" + s2, **cell(h.usd_01.values), pips=float(h.net_pips.mean()), nights=float(h.nights.mean())))
    menu = pd.DataFrame(rows)
    menu.to_csv(os.path.join(OUT, "partB_%s_menu.csv" % name), index=False)
    paid = {st: float(res[(1.0, st)].cost_usd.mean()) for st in STG}
    out["cost_paid_today_usd"] = paid
    out["avg_nights"] = {st: float(res[(1.0, st)].nights.mean()) for st in STG}
    be = {}
    for st in STG:
        g = menu[(menu.stage == st) & (menu.side == "all")].set_index("factor")
        u = g.usd.reindex(FACS).values
        slope, icpt = np.polyfit(np.array(FACS), u, 1)
        fstar = -icpt / slope if slope < 0 else np.nan
        cross = None
        for i in range(len(FACS) - 1):
            ua, ub = u[i], u[i + 1]
            if ua <= 0 < ub or ua < 0 <= ub:
                cross = float(FACS[i] + (0 - ua) * (FACS[i + 1] - FACS[i]) / (ub - ua))
                break
        be[st] = dict(edge_before_cost_usd=float(g.usd[0.0]), net_today_usd=float(g.usd[1.0]), cost_paid_today=paid[st],
                      breakeven_factor=float(fstar), breakeven_cost_usd=float(fstar * paid[st]) if np.isfinite(fstar) else np.nan,
                      edge_gt=float(g["gt"][0.0]), line_slope=float(slope), breakeven_factor_from_rows=cross,
                      breakeven_swap_usd_per_night_today=float(g.usd[1.0] / res[(1.0, st)].nights.sum() * g.n[1.0]) if res[(1.0, st)].nights.sum() > 0 else None,
                      usd_by_factor={("%g" % f): float(v) for f, v in zip(FACS, u)})
    out["breakeven"] = be
    out["power_check_usd_differs_across_rows"] = {st: bool(len(set(np.round(list(be[st]["usd_by_factor"].values()), 4))) == len(FACS)) for st in STG}
    out["worst_stage_breakeven_cost_usd"] = float(min(be[st]["breakeven_cost_usd"] for st in STG))
    out["worst_stage"] = min(STG, key=lambda st: be[st]["breakeven_cost_usd"])
    json.dump(out, open(os.path.join(OUT, "partB_%s.json" % name), "w"), indent=1, default=float)
    print(" menu done %.0fs" % (time.time() - t0), flush=True)
    # controls, luck bar, look-ahead twin: at today's cost and at zero cost
    ctr, lk, tw = {}, {}, {}
    for f in (0.0, 1.0):
        set_cost(f)
        ctr["%g" % f], lk["%g" % f], tw["%g" % f] = {}, {}, {}
        for st in STG:
            t = res[(f, st)].reset_index(drop=True)
            ctr["%g" % f][st] = T.controls(t, rule, mode, st)
            print("  controls", f, st, "%.0fs" % (time.time() - t0), flush=True)
            lk["%g" % f][st] = T.luck(t, rule, mode, st, n=20000)
            print("  luck", f, st, "%.0fs" % (time.time() - t0), flush=True)
            tw["%g" % f][st] = T.lookahead(rule, mode, st, syms=T.FX)
    set_cost(1.0)
    out["controls"], out["luck_bar"], out["lookahead_twin"] = ctr, lk, tw
    json.dump(out, open(os.path.join(OUT, "partB_%s.json" % name), "w"), indent=1, default=float)
    print(" ALL DONE", name, "%.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
