"""AUT16 export: one trades CSV + one JSON + one readable MD per variant, for the owner to read.

  python aut17_export.py trades STAGE    -> results/AUT16_layers/variants/_parts/<variant>__<STAGE>.parquet
  python aut17_export.py reports         -> merges the three windows per variant, writes trades CSV, runs plain_report.py
Every variant is exported with ITS OWN exit (rounds 1-2: the live +$15/-$70; rounds 3-4: the exit named in the variant).
Fixed 0.10 lot, one trade at a time, cost paid once inside common.simulate.
"""
import os
import sys
import json
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut16_layers as X                                                  # noqa: E402
import aut17_round3 as R                                                  # noqa: E402
import common as C                                                        # noqa: E402

VD = os.path.join(X.OUTD, "variants")
PARTS = os.path.join(VD, "_parts")
os.makedirs(PARTS, exist_ok=True)


def all_specs():
    specs = []
    for name, trend, pull, trig in X.phase1_list():
        specs.append(dict(kind="L", name=name, trend=tuple(trend), pull=pull, trig=trig, exit="LIVE", round="1"))
    for name, trend, pull, trig in json.load(open(os.path.join(X.OUTD, "phase2_variants.json"))):
        specs.append(dict(kind="L", name=name, trend=tuple(trend), pull=pull, trig=trig, exit="LIVE", round="2"))
    for s in R.round3_list():
        specs.append(dict(kind="R", round="3", **s))
    f4 = os.path.join(X.OUTD, "round4_specs.json")
    if os.path.exists(f4):
        for s in json.load(open(f4)):
            specs.append(dict(kind="R", round="4", **s))
    seen, out = set(), []
    for s in specs:
        if s["name"] not in seen:
            seen.add(s["name"])
            out.append(s)
    return out


def trades(stage):
    F = R.add_trends(X.build_stage(stage))
    for sp in all_specs():
        f = os.path.join(PARTS, f"{sp['name']}__{stage}.parquet")
        if os.path.exists(f):
            continue
        if sp["kind"] == "L":
            i, side = X.make_signals(F, sp["trend"], sp["pull"], sp["trig"])
            if len(i) == 0:
                continue
            r = C.simulate(X.SYM, X.make_orders(F, i, side, "LIVE"), one_at_a_time=True)
        else:
            i, side = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
            if len(i) == 0:
                continue
            r = C.simulate(X.SYM, R.orders(F, i, side, sp["exit"]), one_at_a_time=True)
        r = r[r.status == "filled"].copy()
        r["window"] = stage
        r.to_parquet(f, index=False)
        print(sp["name"], stage, len(r), flush=True)
    print("done", flush=True)


def describe(sp):
    if sp["kind"] == "L":
        ex = "live +$15 / -$70, 1-day limit"
        return (f"trend {sp['trend'][0]} {sp['trend'][1]} | pullback {sp['pull']} | trigger {sp['trig']} | exit {ex}")
    ex = R.EXITS[sp["exit"]]
    return (f"trend {sp['trend'][0]} {sp['trend'][1]} | no pullback | trigger {sp.get('trig', 'G0_BODY')} | "
            f"hours {sp.get('hours', 'ALL')} | side { {0: 'both', 1: 'buys only', -1: 'sells only'}[sp.get('side', 0)] } | "
            f"exit +${ex[0]} / -${ex[1]}, hold limit {ex[2]} min")


def reports():
    rows = []
    for sp in all_specs():
        fs = [os.path.join(PARTS, f"{sp['name']}__{s}.parquet") for s in ("EXPLORE", "VALIDATE", "STRESS")]
        fs = [f for f in fs if os.path.exists(f)]
        if not fs:
            continue
        t = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
        csv = os.path.join(VD, f"{sp['name']}.csv")
        t.to_csv(csv, index=False)
        subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "plain_report.py"),
                        csv, "--name", f"{sp['name']} (all three windows)", "--risk", "0", "--lot", "0.10", "--balance", "1000"],
                       check=True, capture_output=True)
        # per-window one-liners for the index
        w = {}
        for s in ("EXPLORE", "VALIDATE", "STRESS"):
            x = t[t.window == s]
            if len(x) == 0:
                w[s] = None
                continue
            u = x.usd_01.values
            gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
            w[s] = dict(trades=len(x), win=round(100 * (u > 0).mean(), 1), pf=round(gp / gl, 2) if gl > 0 else None,
                        net=round(u.sum()), per_trade=round(u.mean(), 2))
        rows.append(dict(name=sp["name"], round=sp["round"], rule=describe(sp), windows=w))
        # prepend the rule + three-window table to the variant's MD
        mdf = os.path.join(VD, f"{sp['name']}_SUMMARY.md")
        body = open(mdf, encoding="utf-8").read()
        head = [f"# {sp['name']}", "", f"**Rule:** {describe(sp)}", "", "**Per window** (0.10 lot, cost paid once):", "",
                "| window | trades | win % | profit factor | net $ | $ per trade |", "|---|---|---|---|---|---|"]
        for s, lab in (("EXPLORE", "W1 2009-10"), ("VALIDATE", "W2 2011-16"), ("STRESS", "W3 2017-20")):
            v = w[s]
            head.append(f"| {lab} | " + (" | ".join(str(v[k]) for k in ("trades", "win", "pf", "net", "per_trade")) if v else "- | - | - | - | -") + " |")
        open(mdf, "w", encoding="utf-8").write("\n".join(head) + "\n\n---\n\n" + body)
    json.dump(rows, open(os.path.join(VD, "INDEX.json"), "w"), indent=1)
    print("exported", len(rows))


if __name__ == "__main__":
    if sys.argv[1] == "trades":
        trades(sys.argv[2])
    else:
        reports()
