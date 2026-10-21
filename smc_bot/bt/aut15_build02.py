"""AUT15 rebuild: AUT02 M30 N=20 sweep-fade orders for all three stages (aut02_sweep imported read-only).
Calls sweep_signals / REFINE / reshape only (no C.save, so nothing is written into AUT02's folder).
Writes slim order files to results/AUT15_costcut/_work/orders_extra/."""
import os
import sys

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut02_sweep as S                                                   # noqa: E402
import autopsy_lib as A                                                   # noqa: E402
import common as C                                                        # noqa: E402

OUTD = os.path.join(C.RESULTS, "AUT15_costcut", "_work", "orders_extra")
os.makedirs(OUTD, exist_ok=True)
COLS = ["sym", "t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min"]

if __name__ == "__main__":
    S.SPREAD_PRICE = 0.05
    acc = {}
    for stage in ("EXPLORE", "VALIDATE", "STRESS"):
        S.set_window(stage)
        o0 = S.sweep_signals(S.SYM, "M30", 20, stage)
        for rn, fn in S.REFINE.items():
            m = fn(o0)
            o = o0[m].reset_index(drop=True)
            for sn, kw in S.SHAPES.items():
                oo = S.reshape(o, **kw)
                t = C.simulate(S.SYM, oo, one_at_a_time=False)
                t = t[t.status == "filled"]
                x = t[[c for c in COLS if c in t.columns] + ["usd_01"]].copy()
                x["stage"] = stage
                acc.setdefault("%s__%s" % (rn, sn), []).append(x)
                print(stage, rn, sn, len(x), round(x.usd_01.mean(), 3), flush=True)
    for k, v in acc.items():
        d = pd.concat(v, ignore_index=True)
        d["entry_type"] = d["entry_type"].fillna("market")
        d["expire"] = pd.to_datetime(d["expire"]).fillna(d["t_signal"])
        d["row"] = np.arange(len(d))
        d.to_parquet(os.path.join(OUTD, "AUT02__%s_M30N20.parquet" % k), index=False)
