"""AUT16 addendum 3: hour / weekday filters. Rules: results/AUT16_layers/PREREG.md. Open windows only. Usage: python aut19_hours.py STAGE"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

MODES = ["BASE", "EVE20_22", "EVE20_24", "EVE+FRI17", "EVE+NOFRI", "EVE+FRI17+H16"]


def keep_mask(mode, hr, dow):
    k = np.ones(len(hr), bool)
    if mode == "BASE":
        return k
    if mode == "EVE20_22":
        return ~((hr >= 20) & (hr < 22))
    k &= ~((hr >= 20) & (hr < 24))
    if mode in ("EVE+FRI17", "EVE+FRI17+H16"):
        k &= ~((dow == 4) & (hr >= 17))
    if mode == "EVE+NOFRI":
        k &= ~(dow == 4)
    if mode == "EVE+FRI17+H16":
        k &= ~((hr >= 16) & (hr < 17))
    return k


if __name__ == "__main__":
    stage = sys.argv[1]
    F = R.add_trends(X.build_stage(stage))
    ix = pd.DatetimeIndex(F["tc"])
    hr_all, dow_all = ix.hour.values, ix.dayofweek.values
    rows = []
    for n in N.NAMES:
        sp = N.spec_for(n)
        i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        for mode in MODES:
            k = keep_mask(mode, hr_all[i0], dow_all[i0])
            i, side = i0[k], s0[k]
            r = C.simulate(X.SYM, R.orders(F, i, side, sp["exit"]), one_at_a_time=True)
            st = N.stats2(r, F["days"])
            f = r[r.status == "filled"]
            st["weekend_closes"] = int((f.exit_reason == "WEEKEND").sum())
            st["weekend_close_usd"] = round(f[f.exit_reason == "WEEKEND"].usd_01.sum(), 0)
            st.update(variant=n, mode=mode, stage=stage, signals=len(i))
            rows.append(st)
            print(n, mode, st.get("fills"), st.get("usd_trade"), flush=True)
            pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"hours_{stage}.csv"), index=False)
    print("done", flush=True)
