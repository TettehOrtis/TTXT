"""AUT15 rebuild: AUT04 opening-range-breakout orders for all three stages (aut04_orb imported read-only).
Uses its cached event table and its own orders() builder; the simulate call is only for the reference net dollars.
Writes slim order files to results/AUT15_costcut/_work/orders_extra/."""
import os
import sys

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut04_orb as O                                                     # noqa: E402
import autopsy_lib as A                                                   # noqa: E402
import common as C                                                        # noqa: E402

OUTD = os.path.join(C.RESULTS, "AUT15_costcut", "_work", "orders_extra")
os.makedirs(OUTD, exist_ok=True)
COLS = ["sym", "t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min"]
SHAPES = {"autopsy_1p5x1p0": ("atr", 1.5, 1.0), "best_4x4": ("atr", 4.0, 4.0)}

if __name__ == "__main__":
    ev = O.load_events("close")
    print(len(ev), flush=True)
    REF = {
        "R0_all": lambda e: np.ones(len(e), bool),
        "R1_london": lambda e: (e.sess == "LON").values,
        "R2_brokelate": lambda e: (e.f_min_to_break > 10.0).values,
        "R3_london_late": lambda e: ((e.sess == "LON") & (e.f_min_to_break > 10.0)).values,
    }
    acc = {}
    for stage in ("EXPLORE", "VALIDATE", "STRESS"):
        e0 = A.clip(ev, stage)
        for rn, fn in REF.items():
            e = e0[fn(e0)]
            for sn, shp in SHAPES.items():
                o = O.orders(e, shp)
                t = C.simulate(O.SYM, o, one_at_a_time=False)
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
        d.to_parquet(os.path.join(OUTD, "AUT04__%s.parquet" % k), index=False)
