"""AUT16 controls for the no-pullback entries. Same exit, same one-at-a-time rule, same number of signals.

  TRENDRAND  random minutes, side = the trend's side (no pullback, no trigger candle). Isolates the trigger candle.
  RANDSIDE   the real signal minutes, random side.   (coin flip)
  ALLLONG / ALLSHORT  the real signal minutes, one side only.
  RANDRAND   random minutes, random side.
Five seeds each. Usage: python aut16_controls.py STAGE
"""
import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aut16_layers as X                                                  # noqa: E402
import common as C                                                        # noqa: E402

CASES = [("X_P10_M15_PX200", ("M15", "PX200")), ("X_P10_M30_PX200", ("M30", "PX200")),
         ("X_P10_M15_RSI", ("M15", "RSI")), ("X_P10_M15_EMA", ("M15", "EMA"))]


def one(F, i, side, days):
    o = X.make_orders(F, i, side, "LIVE")
    return X.stats(C.simulate(X.SYM, o, one_at_a_time=True), days)


if __name__ == "__main__":
    stage = sys.argv[1]
    F = X.build_stage(stage)
    rows = []
    pool_all = np.flatnonzero(F["in"])
    pool_all = pool_all[(pool_all > X.WARM) & (pool_all < F["n"] - 20)]
    for name, tr in CASES:
        i, side = X.make_signals(F, tr, "P10_NONE", "G0_BODY")
        d = F["tr"][tr]
        pool = pool_all[d[pool_all] != 0]
        real = one(F, i, side, F["days"])
        rows.append(dict(case=name, control="REAL", stage=stage, seed=-1, **real))
        rows.append(dict(case=name, control="ALLLONG", stage=stage, seed=-1, **one(F, i, np.ones(len(i), int), F["days"])))
        rows.append(dict(case=name, control="ALLSHORT", stage=stage, seed=-1, **one(F, i, -np.ones(len(i), int), F["days"])))
        for sd in range(5):
            rng = np.random.default_rng(sd)
            cs = rng.choice([1, -1], size=len(i))
            rows.append(dict(case=name, control="RANDSIDE", stage=stage, seed=sd, **one(F, i, cs, F["days"])))
            pick = np.sort(rng.choice(pool, size=min(len(i), len(pool)), replace=False))
            rows.append(dict(case=name, control="TRENDRAND", stage=stage, seed=sd, **one(F, pick, d[pick], F["days"])))
            pick2 = np.sort(rng.choice(pool_all, size=min(len(i), len(pool_all)), replace=False))
            rows.append(dict(case=name, control="RANDRAND", stage=stage, seed=sd, **one(F, pick2, rng.choice([1, -1], size=len(pick2)), F["days"])))
        print(name, stage, "real", real.get("usd_trade"), flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"controls_{stage}.csv"), index=False)
    print("done", flush=True)
