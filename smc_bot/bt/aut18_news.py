"""AUT16 addendum: news pause / flatten-before-news / evening block on the five variants. Rules: results/AUT16_layers/PREREG.md (addendum).
Usage: python aut18_news.py STAGE   (open windows only)"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import common as C

NAMES = ["R4_M30PX100_T_E20SIDE_E50_70", "R4_M15PX200_T_M5E20SIDE_E50_70", "R4_M15PX200_T_E20SIDE_E30_70",
         "R4_M30PX50_T_M5E20SIDE_E50_70", "R3_E_E30_70"]
NEWS = (510, 840)                       # 08:30 and 14:00 New York, in minutes after midnight
MODES = ["BASE", "PAUSE", "FLATTEN", "EVE", "PAUSE+EVE", "FLATTEN+EVE"]


def spec_for(n):
    if n == "R3_E_E30_70":
        return dict(name=n, trend=("M15", "PX200"), trig="G0_BODY", hours="ALL", side=0, exit="E30_70")
    return {s["name"]: s for s in json.load(open(os.path.join(X.OUTD, "round4_specs.json")))}[n]


def stats2(r, days):
    st = X.stats(r, days)
    f = r[r.status == "filled"].sort_values("t_exit")
    if len(f) == 0:
        return st
    cum = f.usd_01.cumsum().values
    st["largest_loss"] = round(f.usd_01.min(), 1)
    st["max_dd"] = round((np.maximum.accumulate(cum) - cum).max(), 0)
    st["n_over80"] = int((f.usd_01 < -80).sum())
    return st


if __name__ == "__main__":
    stage = sys.argv[1]
    F = R.add_trends(X.build_stage(stage))
    ny = pd.DatetimeIndex(F["tc"]).tz_localize("UTC").tz_convert("America/New_York")
    md_ny = (ny.hour * 60 + ny.minute).values
    hr_utc = pd.DatetimeIndex(F["tc"]).hour.values
    rows = []
    for n in NAMES:
        sp = spec_for(n)
        i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        for mode in MODES:
            keep = np.ones(len(i0), bool)
            md = md_ny[i0]
            flat = mode.startswith("FLATTEN")
            pre = 20 if flat else 5
            if mode.startswith("PAUSE") or flat:
                for t in NEWS:
                    keep &= ~((md >= t - pre) & (md <= t + 30))
            if mode.endswith("EVE"):
                keep &= ~((hr_utc[i0] >= 20) & (hr_utc[i0] < 24))
            i, side = i0[keep], s0[keep]
            o = R.orders(F, i, side, sp["exit"])
            if flat:
                m = md_ny[i]
                until = np.min([(t - 5 - m) % 1440 for t in NEWS], axis=0)
                o["max_hold_min"] = np.minimum(o["max_hold_min"].values, np.maximum(until, 1)).astype(float)
            r = C.simulate(X.SYM, o, one_at_a_time=True)
            st = stats2(r, F["days"])
            st.update(variant=n, mode=mode, stage=stage, signals=len(i))
            rows.append(st)
            print(n, mode, st.get("fills"), st.get("usd_trade"), st.get("largest_loss"), flush=True)
            pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"news_{stage}.csv"), index=False)
    print("done", flush=True)
