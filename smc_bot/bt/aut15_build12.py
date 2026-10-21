"""AUT15 rebuild: AUT12 event family E_LFIX_AM (follow 60-min pre-move, hold 120) on all three stages.
aut12_clock imported read-only; cache=False so nothing is written into AUT12's folder."""
import os
import sys

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut12_clock as K                                                   # noqa: E402
import common as C                                                        # noqa: E402

OUTD = os.path.join(C.RESULTS, "AUT15_costcut", "_work", "orders_extra")

if __name__ == "__main__":
    ev = K.events_all()
    acc = []
    for stage in ("EXPLORE", "VALIDATE", "STRESS"):
        es = ev[ev.stage == stage]
        sim = K.sim_events(ev, stage, K.BASE_SHAPE, hold=120, cache=False)
        tr = K.pick(es, sim, 60, "follow", events=["LFIX_AM"])
        x = tr[["sym", "t_signal", "side", "sl", "tp", "usd_01"]].copy()
        x["entry_type"] = "market"
        x["entry"] = np.nan
        x["expire"] = x.t_signal
        x["be_at_r"] = np.nan
        x["max_hold_min"] = 120.0
        x["stage"] = stage
        acc.append(x)
        print(stage, len(x), round(x.usd_01.mean(), 3), flush=True)
    d = pd.concat(acc, ignore_index=True)
    d["row"] = np.arange(len(d))
    d.to_parquet(os.path.join(OUTD, "AUT12__E_LFIX_AM_all3.parquet"), index=False)
