"""AUT16 addendum 5: second look at the sealed data, five variants, BASE / EVE / FINAL. Needs AUTOPSY_EXAM_UNLOCK and SMC_DATASET (duka|zero)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

if __name__ == "__main__":
    feed = os.environ.get("SMC_DATASET", "zero")
    F = R.add_trends(X.build_stage("EXAM"))
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    outd = os.path.join(X.OUTD, "exam2"); os.makedirs(outd, exist_ok=True)
    pool_all = np.flatnonzero(F["in"]); pool_all = pool_all[(pool_all > X.WARM) & (pool_all < F["n"] - 20)]
    allowed = {"BASE": np.ones(F["n"], bool),
               "EVE": ~((hr >= 20) & (hr < 24)),
               "FINAL": ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))}
    rows = []
    for n in N.NAMES:
        sp = N.spec_for(n)
        i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        for mode in ("BASE", "EVE", "FINAL"):
            k = allowed[mode][i0]
            i, side = i0[k], s0[k]
            r = C.simulate(X.SYM, R.orders(F, i, side, sp["exit"]), one_at_a_time=True)
            st = N.stats2(r, F["days"])
            f = r[r.status == "filled"]
            st.update(variant=n, mode=mode, feed=feed, signals=len(i), weekend_closes=int((f.exit_reason == "WEEKEND").sum()))
            if mode == "FINAL":
                f.to_csv(os.path.join(outd, f"{n}__FINAL__{feed}.csv"), index=False)
                d = F["tr"][tuple(sp["trend"])]
                pool = pool_all[(d[pool_all] != 0) & allowed["FINAL"][pool_all]]
                cs = []
                for sd in range(5):
                    rng = np.random.default_rng(sd)
                    pick = np.sort(rng.choice(pool, size=min(len(i), len(pool)), replace=False))
                    rc = C.simulate(X.SYM, R.orders(F, pick, d[pick], sp["exit"]), one_at_a_time=True)
                    cs.append(X.stats(rc, F["days"]).get("usd_trade"))
                st["ctl_mean"], st["ctl_max"] = round(float(np.mean(cs)), 2), round(float(np.max(cs)), 2)
            rows.append(st); print(n, mode, st.get("fills"), st.get("usd_trade"), flush=True)
            pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"exam2_{feed}.csv"), index=False)
    print("done", flush=True)
