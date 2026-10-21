"""AUT15 Part A summary from the replayed orders. Writes results/AUT15_costcut/partA_*.csv"""
import os
import sys
import glob
import json

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import common as C                                                        # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT15_costcut")
WORK = os.path.join(OUT, "_work")


def load_results(adir):
    fs = glob.glob(os.path.join(WORK, adir, "*.parquet"))
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)


def summarize(adir="A", ordsub="orders", tag="partA", regfile="registry.json"):
    res = load_results(adir)
    reg = json.load(open(os.path.join(WORK, regfile)))
    rows, bysym = [], []
    for e in reg:
        key = e["key"]
        o = pd.read_parquet(os.path.join(WORK, ordsub, key + ".parquet"))
        r = res[res.key == key].set_index("row")
        o = o.set_index("row").join(r[["usd_1", "usd_0", "st_1", "st_0"]], how="left")
        o["ok"] = o.st_0.fillna(False).astype(bool) & o.st_1.fillna(False).astype(bool)
        for vname, syms in e["views"]:
            v = o if syms is None else o[o.sym.isin(syms)]
            for st in ("EXPLORE", "VALIDATE", "STRESS"):
                g = v[(v.stage == st)]
                n_all = len(g)
                g = g[g.ok]
                if n_all == 0:
                    rows.append(dict(key=key, study=e["study"], label=e["label"], view=vname, stage=st, n=0, n_saved=0))
                    continue
                buy, sell = g[g.side == 1], g[g.side == -1]
                rows.append(dict(key=key, study=e["study"], label=e["label"], view=vname, stage=st, n=len(g), n_saved=n_all,
                                 edge0=g.usd_0.mean(), net1=g.usd_1.mean(), saved_net=g.usd_01.mean(),
                                 paid_by_study=g.usd_0.mean() - g.usd_01.mean(), paid_my_env=g.usd_0.mean() - g.usd_1.mean(),
                                 n_buy=len(buy), n_sell=len(sell), buy0=buy.usd_0.mean() if len(buy) else np.nan,
                                 sell0=sell.usd_0.mean() if len(sell) else np.nan,
                                 buy1=buy.usd_1.mean() if len(buy) else np.nan, sell1=sell.usd_1.mean() if len(sell) else np.nan,
                                 repro_gap=(g.usd_1 - g.usd_01).abs().mean()))
                if vname == "ALL" and g.sym.nunique() > 1:
                    for s, gs in g.groupby("sym"):
                        bysym.append(dict(key=key, view=vname, stage=st, sym=s, n=len(gs), edge0=gs.usd_0.mean(), net1=gs.usd_1.mean()))
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(OUT, tag + "_stage_table.csv"), index=False)
    pd.DataFrame(bysym).to_csv(os.path.join(OUT, tag + "_by_symbol.csv"), index=False)
    return d


if __name__ == "__main__":
    d = summarize()
    d2 = summarize("A2", "orders_extra", "partA_extra", "registry_extra.json")
    d3 = summarize("A3", "orders_extra2", "partA_extra2", "registry_extra2.json")
    parts = [d, d2, d3]
    for adir, ordsub, tag, reg in (("A4", "orders_extra3", "partA_extra3", "registry_extra3.json"),
                                    ("A5", "orders_extra4", "partA_extra4", "registry_extra4.json")):
        if os.path.exists(os.path.join(WORK, reg)) and os.path.isdir(os.path.join(WORK, adir)):
            parts.append(summarize(adir, ordsub, tag, reg))
    d = pd.concat(parts, ignore_index=True)
    d.to_csv(os.path.join(OUT, "partA_stage_table_all.csv"), index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    p = d.pivot_table(index=["key", "view"], columns="stage", values=["n", "edge0", "net1", "saved_net"], aggfunc="first")
    print(p.round(2).to_string())
