"""AUT15 rebuild: AUT06 order-block orders for all three stages via aut06's own cached entry tables and orders() builder.
(aut06_orderblock imported read-only; nothing is saved into AUT06's folder.)"""
import os
import sys

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut06_orderblock as B                                              # noqa: E402
import common as C                                                        # noqa: E402

OUTD = os.path.join(C.RESULTS, "AUT15_costcut", "_work", "orders_extra")
COLS = ["sym", "t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min"]
VARS = {"BASELINE_edge_2xATR": (2.0, "edge", 1.0), "R0_best_0p5x2p0": (0.5, "atr_grid", 2.0)}

if __name__ == "__main__":
    acc = {}
    for stage in ("EXPLORE", "VALIDATE", "STRESS"):
        te = B.entries(stage, "prox", max_touch=3)
        t1 = te[te.touch_n == 1].reset_index(drop=True)
        print(stage, "entries", len(te), "touch1", len(t1), flush=True)
        for vn, (tg, mode, mult) in VARS.items():
            r = B.run_sim(t1, tg, mode, stop_mult=mult, one=True)
            r = r[r.status == "filled"]
            x = r[[c for c in COLS if c in r.columns] + ["usd_01"]].copy()
            x["stage"] = stage
            acc.setdefault(vn, []).append(x)
            print(stage, vn, len(x), round(x.usd_01.mean(), 3), flush=True)
    for k, v in acc.items():
        d = pd.concat(v, ignore_index=True)
        d["entry_type"] = d["entry_type"].fillna("market")
        d["expire"] = pd.to_datetime(d["expire"]).fillna(d["t_signal"])
        d["row"] = np.arange(len(d))
        d.to_parquet(os.path.join(OUTD, "AUT06__%s.parquet" % k), index=False)
