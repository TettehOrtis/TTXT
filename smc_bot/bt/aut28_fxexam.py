"""AUT16 addendum 10: the five variants on the 9 FX majors. Sealed test. Usage: python aut28_fxexam.py EXAM"""
import os, sys, json
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

PAIRS = ["EURUSD", "GBPUSD", "EURJPY"]
K = json.load(open(os.path.join(X.OUTD, "fx_k.json")))["k"]


def fx_orders(F, i, side, ex):
    kk = K["E50_70" if ex == "E50_70" else "E30_70"]
    nxt = np.clip(i + 1, 0, F["n"] - 1)
    px = F["o"][nxt] + np.where(side == 1, F["m1"].spr.values[nxt], 0.0)
    at = F["atr15"][i]
    sl, tp = px - side * kk["ks"] * at, px + side * kk["kt"] * at
    o = pd.DataFrame({"t_signal": F["tc"][i], "side": side, "entry_type": "market", "entry": np.nan, "expire": F["tc"][i],
                      "sl": sl, "tp": tp, "be_at_r": np.nan, "max_hold_min": 1440.0})
    return o[np.isfinite(o.sl) & np.isfinite(o.tp)].reset_index(drop=True)


if __name__ == "__main__":
    stage = sys.argv[1]
    rows = []
    for pair in PAIRS:
        X.SYM = pair
        F = R.add_trends(X.build_stage(stage))
        ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
        ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
        pool_all = np.flatnonzero(F["in"]); pool_all = pool_all[(pool_all > X.WARM) & (pool_all < F["n"] - 20)]
        for n in N.NAMES:
            sp = N.spec_for(n)
            i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
            for mode in ("BASE", "FINAL"):
                kk = np.ones(len(i0), bool) if mode == "BASE" else ok[i0]
                i, side = i0[kk], s0[kk]
                if len(i) < 50:
                    continue
                r = C.simulate(pair, fx_orders(F, i, side, sp["exit"]), one_at_a_time=True)
                st = N.stats2(r, F["days"])
                st.update(pair=pair, variant=n, mode=mode, stage=stage, signals=len(i))
                if mode == "FINAL":
                    d = F["tr"][tuple(sp["trend"])]
                    pool = pool_all[(d[pool_all] != 0) & ok[pool_all]]
                    cs = []
                    for sd in range(3):
                        rng = np.random.default_rng(sd)
                        pick = np.sort(rng.choice(pool, size=min(len(i), len(pool)), replace=False))
                        rc = C.simulate(pair, fx_orders(F, pick, d[pick], sp["exit"]), one_at_a_time=True)
                        cs.append(X.stats(rc, F["days"]).get("usd_trade", np.nan))
                    st["ctl_max"] = round(float(np.nanmax(cs)), 2)
                rows.append(st)
            print(pair, stage, n, flush=True)
            pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, "fxexam_" + os.environ.get("SMC_DATASET", "zero") + ".csv"), index=False)
    print("done", flush=True)
