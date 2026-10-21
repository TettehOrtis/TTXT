"""AUT15 - AUT11 daily-trend variants replayed at cost factors (uses aut11_trend read-only; its own walker).
Usage: python aut15_aut11.py <factors comma list> <out parquet name>
Commission and spread are both multiplied by the factor (a.comm and smult). Stop/trail anchored on the signal close."""
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

OUTW = os.path.join(C.RESULTS, "AUT15_costcut", "_work")
SPECS = {s["id"]: s for s in json.load(open(os.path.join(C.RESULTS, "AUT11_trend", "refinements_chosen.json")))}
STAGES = ["EXPLORE", "VALIDATE", "STRESS"]


def variants():
    v = {}
    for rule in T.RULES:
        for mode in T.EXITS:
            v["%s_%s" % (rule, mode)] = dict(rule=rule, mode=mode, spec=None)
    for sid, sp in SPECS.items():
        for rule in T.RULES:
            v["%s_%s_OPP" % (sid, rule)] = dict(rule=rule, mode="OPP", spec=sp)
    return v


def set_comm(f):
    for s in T.SYMS:
        a = T.load(s)
        a.comm = C.COMM[s] * f


def run(fac, key, stage, v):
    set_comm(fac)
    eo = None
    if v["spec"] is not None:
        sp = v["spec"]
        eo = T.entry_ok_from(sp["feature"], sp["op"], sp["thr_by_skeleton"].get(v["rule"], 0.5))
    t = T.run_rule(v["rule"], v["mode"], stage, entry_ok=eo, smult=fac)
    set_comm(1.0)
    return t


if __name__ == "__main__":
    facs = [float(x) for x in sys.argv[1].split(",")]
    outname = sys.argv[2]
    rows = []
    t0 = time.time()
    V = variants()
    for key, v in V.items():
        for stage in STAGES:
            for fac in facs:
                t = run(fac, key, stage, v)
                if len(t) == 0:
                    continue
                d = t[["sym", "t_signal", "side", "usd_01", "cost_usd", "nights", "risk_pips"]].copy()
                d["variant"], d["stage"], d["fac"] = key, stage, fac
                rows.append(d)
        print(key, "%.0fs" % (time.time() - t0), flush=True)
    pd.concat(rows, ignore_index=True).to_parquet(os.path.join(OUTW, outname), index=False)
