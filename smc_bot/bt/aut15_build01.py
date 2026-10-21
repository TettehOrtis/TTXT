"""AUT15 rebuild: AUT01 'live as shipped' ($15 target / $70 stop = 1.5 / 7.0 price units from the signal close, 1-day hold) for STRESS.
Uses aut01_livecfg.build_light / signal_sets / subsample (imported read-only). EXPLORE and VALIDATE are rebuilt too, only to
prove the rebuild reproduces the saved corrected file before STRESS is trusted."""
import os
import sys

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut01_livecfg as L                                                 # noqa: E402
import common as C                                                        # noqa: E402

OUTD = os.path.join(C.RESULTS, "AUT15_costcut", "_work", "orders_extra3")
COLS = ["sym", "t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min"]

if __name__ == "__main__":
    ref = pd.read_csv(os.path.join(C.RESULTS, "AUT01_emapull", "trades_live_as_shipped_CORRECTED_ALLSTAGES.csv"),
                      usecols=["t_signal", "side", "sl", "tp", "usd_01", "max_hold_min"])
    ref["t_signal"] = pd.to_datetime(ref.t_signal)
    acc = []
    for stage in ("EXPLORE", "STRESS"):
        sets = L.signal_sets(stage)
        sel, pop = L.subsample(sets["LIVE"])
        o = sel.copy()
        b = C.bars("XAUUSD", "M1")
        i = np.clip(np.searchsorted(b.time.values, pd.to_datetime(o.t_signal).values, "left"), 0, len(b) - 1)
        px = b.open.values[i] + np.where(o.side.values == 1, b.spr.values[i], 0.0)       # the price a market order fills at
        o["sl"] = px - o.side * 6.89                                                    # net $70 after the $1.10 commission
        o["tp"] = px + o.side * 1.61                                                    # net $15 after the $1.10 commission
        if stage == "EXPLORE":
            k = o.merge(ref, on=["t_signal", "side"], suffixes=("", "_ref"))
            print("EXPLORE match with saved corrected file:", len(k), "of", len(o), "| sl equal", np.allclose(k.sl, k.sl_ref), "| tp equal", np.allclose(k.tp, k.tp_ref), flush=True)
        r = C.simulate("XAUUSD", o.drop(columns=["sym"]), one_at_a_time=False)
        r = r[r.status == "filled"]
        x = r[[c for c in COLS if c in r.columns] + ["usd_01"]].copy()
        x["sym"] = "XAUUSD"
        x["stage"] = stage
        print(stage, "signals", pop, "sampled", len(o), "filled", len(x), "usd/trade", round(x.usd_01.mean(), 3), flush=True)
        if stage == "STRESS":
            acc.append(x)
    d = pd.concat(acc, ignore_index=True)
    d["row"] = np.arange(len(d))
    d["entry_type"] = d["entry_type"].fillna("market")
    d["expire"] = pd.to_datetime(d["expire"]).fillna(pd.to_datetime(d["t_signal"]))
    d.to_parquet(os.path.join(OUTD, "AUT01__LIVE_AS_SHIPPED_STRESS.parquet"), index=False)
