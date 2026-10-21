"""AUT13 driver: grid of (k, hold) cells with every mandatory control, per target and stage.

    python smc_bot/bt/aut13_run.py grid XAUUSD EXPLORE
"""
import os, sys, json, time
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import aut13_leadlag as L
C, A = L.C, L.A


def flat(d, pre=""):
    o = {}
    for k, v in d.items():
        if isinstance(v, dict):
            o.update(flat(v, pre + k + "."))
        elif isinstance(v, (list, tuple)):
            continue
        else:
            o[pre + k] = v
    return o


def grid(target, stage, ks=L.KS, holds=L.HOLDS, with_luck=True):
    ev = L.events(target)
    evo = L.events(target, "own")
    rows, full = [], {}
    for k in ks:
        for h in holds:
            t0 = time.time()
            e = L.cell(ev, stage, k, h)
            if len(e) < 30:
                continue
            ctl, rule = L.controls(target, e, h, stage, with_luck=with_luck)
            eo = L.cell(evo, stage, k, h)
            own = L.book(L.run(target, eo, h), target)          # control: fade the target's OWN k-minute move, no dollar
            ctl["own_move_fade"] = own
            full["k%d_h%d" % (k, h)] = ctl
            r = flat(ctl)
            r.update(target=target, stage=stage, k=k, hold=h)
            rows.append(r)
            print(target, stage, "k=%d h=%d n=%d rule $%.3f rev $%.3f coin $%.3f own $%.3f luck $%s  (%.0fs)" % (
                k, h, ctl["rule"]["trades"], ctl["rule"]["usd_per_trade"], ctl["reversed_rule"]["usd_per_trade"],
                ctl["coin_flip"]["usd_per_trade"], own.get("usd_per_trade", np.nan),
                ctl.get("luck_bar", {}).get("usd_per_trade"), time.time() - t0), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(L.OUT, "grid_%s_%s.csv" % (target, stage)), index=False)
    with open(os.path.join(L.OUT, "grid_%s_%s.json" % (target, stage)), "w") as f:
        json.dump(full, f, indent=1, default=float)
    return df


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "grid":
        grid(sys.argv[2], sys.argv[3], with_luck=(len(sys.argv) < 5))
