"""AUT15 rebuild: AUT08 break-of-structure baseline variants (other timeframes / swing sizes) from its cached event tables.
aut08_bos imported read-only. Args: stage list (default EXPLORE). Writes slim orders to _work/orders_extra."""
import os
import sys

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut08_bos as B                                                     # noqa: E402
import common as C                                                        # noqa: E402

OUTD = os.path.join(C.RESULTS, "AUT15_costcut", "_work", "orders_extra")
COLS = ["sym", "t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min"]
VARS = [("M15", 3), ("M15", 5), ("H1", 3), ("H4", 3), ("H4", 5)]

if __name__ == "__main__":
    stages = sys.argv[1].split(",") if len(sys.argv) > 1 else ["EXPLORE"]
    only = [tuple(x.split("_N")) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else None
    for tf, N in VARS:
        if only and (tf, str(N)) not in only:
            continue
        acc = []
        for stage in stages:
            ev = B.events_for(stage, tf, N)
            t = B.run_shape(ev)
            x = t[[c for c in COLS if c in t.columns] + ["usd_01"]].copy()
            x["stage"] = stage
            acc.append(x)
            print(tf, N, stage, len(x), round(x.usd_01.mean(), 3), flush=True)
        d = pd.concat(acc, ignore_index=True)
        d["row"] = np.arange(len(d))
        d.to_parquet(os.path.join(OUTD, "AUT08__%s_N%d_%s.parquet" % (tf, N, "-".join(s[:1] for s in stages))), index=False)
