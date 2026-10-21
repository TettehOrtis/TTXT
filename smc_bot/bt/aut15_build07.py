"""AUT15 rebuild: AUT07 FVG_FILL baseline (B0) for VALIDATE and STRESS, through aut07's own pool() with its BASE spec.
aut07_fvg imported read-only. Slim orders go to _work/orders_extra3."""
import os
import sys

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut07_fvg as F                                                     # noqa: E402
import common as C                                                        # noqa: E402

OUTD = os.path.join(C.RESULTS, "AUT15_costcut", "_work", "orders_extra3")
os.makedirs(OUTD, exist_ok=True)
COLS = ["sym", "t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min"]

if __name__ == "__main__":
    F.set_costs()
    stage = sys.argv[1]
    t = F.pool(C.PAIRS, stage, **F.BASE)
    x = t[[c for c in COLS if c in t.columns] + ["usd_01"]].copy()
    del t
    x["stage"] = stage
    print(stage, len(x), round(x.usd_01.mean(), 3), flush=True)
    x["row"] = np.arange(len(x))
    x["entry_type"] = x["entry_type"].fillna("market")
    x["expire"] = pd.to_datetime(x["expire"]).fillna(pd.to_datetime(x["t_signal"]))
    x.to_parquet(os.path.join(OUTD, "AUT07__FVG_FILL_base_%s.parquet" % stage), index=False)
