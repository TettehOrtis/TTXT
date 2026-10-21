"""AUT16 addendum 8: account test. Usage: python aut24_account.py STAGE (open windows only)"""
import os, sys, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

BALS, LOTS = [100, 500], [0.01, 0.02, 0.03, 0.05]

if __name__ == "__main__":
    stage = sys.argv[1]
    F = R.add_trends(X.build_stage(stage))
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    rows = []
    for n in N.NAMES:
        sp = N.spec_for(n)
        i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        k = ok[i0]
        r = C.simulate(X.SYM, R.orders(F, i0[k], s0[k], sp["exit"]), one_at_a_time=True)
        f = r[r.status == "filled"].sort_values("t_exit").reset_index(drop=True)
        u0, te = f.usd_01.values, pd.to_datetime(f.t_exit).values
        for bal, lot in itertools.product(BALS, LOTS):
            u = u0 * (lot / 0.10)
            bal_path = bal + np.cumsum(u)
            dead = np.flatnonzero(bal_path <= 0)
            blown = len(dead) > 0
            end = dead[0] + 1 if blown else len(u)
            path = np.concatenate([[bal], bal_path[:end]])
            peak = np.maximum.accumulate(path)
            rows.append(dict(variant=n, stage=stage, start=bal, lot=lot, blown=blown,
                             blown_trade=int(dead[0] + 1) if blown else 0,
                             blown_date=str(pd.Timestamp(te[dead[0]]).date()) if blown else "",
                             trades_total=len(u), final=round(float(path[-1]), 0), lowest=round(float(path.min()), 0),
                             max_drop_pct=round(float(((peak - path) / peak).max() * 100), 0),
                             biggest_losing_streak_usd=round(float((peak - path).max()), 0),
                             days=F["days"]))
        print(n, len(f), flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"accountsmall_{stage}.csv"), index=False)
    print("done", flush=True)
