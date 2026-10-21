"""AUT13: replicate the UNREFINED base cell (k=5, hold=15) on the other five USD pairs, every stage, split by pair inside each stage."""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import aut13_autopsy as U
L = U.L

if __name__ == "__main__":
    out = {}
    for tg in sys.argv[1:]:
        ev = L.events(tg)
        for st in ("EXPLORE", "VALIDATE", "STRESS"):
            t0 = time.time()
            e = L.cell(ev, st, L.BASE_K, L.BASE_H)
            ctl, tr = L.controls(tg, e, L.BASE_H, st, with_luck=True, n_luck=20000, seeds=(0, 1))
            out["%s|%s" % (tg, st)] = ctl
            tr.to_csv(os.path.join(L.OUT, "trades_%s_BASE_%s.csv" % (tg, st)), index=False)
            print(tg, st, ctl["rule"]["trades"], ctl["rule"]["usd_per_trade"], "luck", ctl.get("luck_bar", {}).get("usd_per_trade"),
                  "rev", ctl["reversed_rule"]["usd_per_trade"], "rt", ctl["round_trip_usd"], "%.0fs" % (time.time() - t0), flush=True)
            json.dump(out, open(os.path.join(L.OUT, "replicate_%s.json" % "_".join(sys.argv[1:])), "w"), indent=1, default=float)
