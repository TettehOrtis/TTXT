"""AUT16 addendum 4: Monday start. Usage: python aut20_monday.py STAGE (open windows only)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

if __name__ == "__main__":
    stage = sys.argv[1]
    F = R.add_trends(X.build_stage(stage))
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    rows = []
    for n in N.NAMES:
        sp = N.spec_for(n)
        i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        base = ~((hr[i0] >= 20) & (hr[i0] < 24))
        for mode, cut in (("MON00", 0), ("MON07", 7), ("MON10", 10)):
            k = base & ~((dow[i0] == 0) & (hr[i0] < cut))
            i, side = i0[k], s0[k]
            r = C.simulate(X.SYM, R.orders(F, i, side, sp["exit"]), one_at_a_time=True)
            st = N.stats2(r, F["days"])
            f = r[r.status == "filled"]
            mon = f[pd.DatetimeIndex(f.t_signal).dayofweek == 0]
            st["monday_trades"] = len(mon); st["monday_usd_trade"] = round(mon.usd_01.mean(), 2) if len(mon) else np.nan
            st.update(variant=n, mode=mode, stage=stage, signals=len(i))
            rows.append(st); print(n, mode, st.get("fills"), st.get("usd_trade"), flush=True)
            pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"monday_{stage}.csv"), index=False)
    print("done", flush=True)
