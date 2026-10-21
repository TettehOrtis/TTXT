"""AUT13 stages 1c-3: pick each carried refinement's cell on EXPLORE, then run base + 3 refinements through
EXPLORE, VALIDATE (touched once) and STRESS (+ adverse costs), on gold and EURUSD, with every mandatory control.

    python aut13_refine.py pick                 # EXPLORE only: choose the cell of each refinement
    python aut13_refine.py full XAUUSD          # all variants x all stages for one target
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import aut13_autopsy as U
L, A, C = U.L, U.A, U.C

SEL = json.load(open(os.path.join(L.OUT, "selection_EXPLORE.json")))
TH = SEL["thresholds"]
CARRIED = SEL["carried"]                      # e.g. ['T4', 'E2', 'L8']
NAMES = {c[0]: c[2] for c in U.CONDS(TH["XAUUSD"])}
STAGES = ("EXPLORE", "VALIDATE", "STRESS")
# adverse extra spread, price units: gold +0.05 / +0.10 (round trip $2.10 / $2.60); FX +0.5 / +1.0 pip
ADVERSE = {"XAUUSD": (0.05, 0.10), "EURUSD": (0.00005, 0.0001)}


def variant_events(target, rid):
    ev = U.feat(target)
    return ev if rid is None else U.filt(ev, TH[target], rid)


def pick():
    rows = []
    for rid in CARRIED:
        for tg in U.TARGETS:
            ev = variant_events(tg, rid)
            for k in L.KS:
                for h in L.HOLDS:
                    e = L.cell(ev, "EXPLORE", k, h)
                    t = L.filled(L.run(tg, e, h))
                    rows.append(dict(rid=rid, target=tg, k=k, hold=h, trades=len(t), usd=float(t.usd_01.mean()),
                                     right=float(100 * (t.net_pips > 0).mean())))
                    print(rows[-1], flush=True)
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(L.OUT, "refine_cells_EXPLORE.csv"), index=False)
    cells = {}
    for rid in CARRIED:
        p = d[d.rid == rid].pivot_table(index=["k", "hold"], columns="target", values=["usd", "trades"])
        ok = (p["trades"] >= 100).all(axis=1)
        sc = p["usd"].mean(axis=1).where(ok)
        k, h = sc.idxmax()
        cells[rid] = dict(k=int(k), hold=int(h), explore_usd_mean=float(sc.max()))
        print(rid, cells[rid])
    json.dump(cells, open(os.path.join(L.OUT, "refine_cells.json"), "w"), indent=1)


def variants():
    cells = json.load(open(os.path.join(L.OUT, "refine_cells.json")))
    out = [("BASE", None, L.BASE_K, L.BASE_H)]
    out += [("R%d_%s" % (i + 1, rid), rid, cells[rid]["k"], cells[rid]["hold"]) for i, rid in enumerate(CARRIED)]
    return out


def full(target):
    res = {}
    allt = {}
    for vname, rid, k, h in variants():
        ev = variant_events(target, rid)
        for st in STAGES:
            t0 = time.time()
            e = L.cell(ev, st, k, h)
            ctl, tr = L.controls(target, e, h, st, with_luck=True)
            ctl["k"], ctl["hold"], ctl["rid"] = k, h, rid
            ctl["cooldown_events"] = int(len(e))
            ctl["invalid_orders"] = int(0)
            if st == "STRESS":
                adv = {}
                normal = ctl["rule"]
                for a in ADVERSE[target]:
                    L.set_stress(target, a)
                    ta = L.run(target, e, h)
                    L.set_stress(target, 0)
                    b = L.book(ta, target)
                    adv["extra_spread_%g" % a] = b
                # a stress row that equals the normal row has no power: refuse to continue
                for a, b in adv.items():
                    assert abs(b["usd_per_trade"] - normal["usd_per_trade"]) > 1e-6 or b["trades"] == 0, "stress has no power"
                ctl["adverse"] = adv
            res["%s|%s" % (vname, st)] = ctl
            tr = tr.copy()
            tr["period"] = st
            allt.setdefault(vname, []).append(tr)
            tr.to_csv(os.path.join(L.OUT, "trades_%s_%s_%s.csv" % (target, vname, st)), index=False)
            print(target, vname, st, "k=%d h=%d n=%d rule $%.3f luck $%s rev $%.3f coin $%.3f (%.0fs)" % (
                k, h, ctl["rule"]["trades"], ctl["rule"]["usd_per_trade"], ctl.get("luck_bar", {}).get("usd_per_trade"),
                ctl["reversed_rule"]["usd_per_trade"], ctl["coin_flip"]["usd_per_trade"], time.time() - t0), flush=True)
            json.dump(res, open(os.path.join(L.OUT, "full_%s.json" % target), "w"), indent=1, default=float)
        pd.concat(allt[vname], ignore_index=True).to_csv(
            os.path.join(L.OUT, "trades_%s_%s_ALLSTAGES.csv" % (target, vname)), index=False)
    return res


if __name__ == "__main__":
    if sys.argv[1] == "pick":
        pick()
    elif sys.argv[1] == "full":
        full(sys.argv[2])
