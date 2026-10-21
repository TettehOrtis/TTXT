"""PREREG addendum 29: the addendum 28 search on 10 markets. Usage: python aut44_htf10.py PAIR | judge   (Dukascopy feed, open windows only)"""
import os, sys, glob
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import common as C
import aut43_htf as H

PAIRS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURJPY"]
STAGES = ["EXPLORE", "VALIDATE", "STRESS"]


def run_pair(pair):
    X.SYM = pair
    rows = []
    for stage in STAGES:
        F = X.build_stage(stage)
        a, z = pd.Timestamp(F["a"]), pd.Timestamp(F["z"])
        rng = np.random.default_rng(11)
        for tf in H.TFS:
            df = C.bars(pair, tf).reset_index(drop=True)
            if len(df) < 400:
                continue
            for strat in H.STRATS:
                idx, side, atr_all = H.tf_signals(df, strat)
                tclose = df.tclose.values[idx]
                inw = (tclose >= np.datetime64(a)) & (tclose < np.datetime64(z))
                idx, side, tclose = idx[inw], side[inw], tclose[inw]
                if len(idx) == 0:
                    continue
                atr = atr_all[idx]
                rnd = np.where(rng.random(len(side)) < 0.5, 1, -1)
                for K in H.KS:
                    for RR in H.RRS:
                        for kind, sd in (("SIG", side), ("RND", rnd)):
                            o = H.build_orders(F, tclose, sd, atr, K, RR)
                            r = C.simulate(pair, o, one_at_a_time=True)
                            x = H.stat(r)
                            x.update(pair=pair, stage=stage, tf=tf, strat=strat, K=K, RR=RR, kind=kind)
                            rows.append(x)
        print(pair, stage, "done", flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"htf10_{pair}.csv"), index=False)


if __name__ == "__main__":
    if sys.argv[1] != "judge":
        run_pair(sys.argv[1])
        sys.exit()
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "htf10_*.csv"))])
    d.to_csv(os.path.join(X.OUTD, "HTF10_ALL.csv"), index=False)
    keys = ["tf", "strat", "K", "RR"]
    pd.set_option("display.width", 250)

    def works(g, rg):
        for s in STAGES:
            x, c = g[g.stage == s], rg[rg.stage == s]
            if len(x) == 0 or len(c) == 0:
                return False
            x, c = x.iloc[0], c.iloc[0]
            if not (x.trades >= 100 and x.pf >= 1.10 and x.exp_R > 0 and x.pf > c.pf):
                return False
        return True

    def pf_rule(g):
        for s in STAGES:
            x = g[g.stage == s]
            if len(x) == 0:
                return False
            x = x.iloc[0]
            if not (x.trades >= 100 and x.pf >= 1.10 and x.exp_R > 0):
                return False
        return True
    sig, rnd = d[d.kind == "SIG"], d[d.kind == "RND"]
    res = []
    chance = 0
    for (pair, *k), g in sig.groupby(["pair"] + keys):
        gr = rnd[(rnd.pair == pair) & (rnd.tf == k[0]) & (rnd.strat == k[1]) & (rnd.K == k[2]) & (rnd.RR == k[3])]
        res.append(dict(pair=pair, tf=k[0], strat=k[1], K=k[2], RR=k[3], works=works(g, gr)))
    for _, g in rnd.groupby(["pair"] + keys):
        chance += pf_rule(g)
    res = pd.DataFrame(res)
    broad = res.groupby(keys).works.sum().reset_index().rename(columns={"works": "markets_working"})
    print("markets:", sorted(d.pair.unique()), "| (market, config) cells:", len(res), "| cells that work:", int(res.works.sum()), "| random-control cells passing the profit-factor rule (chance):", int(chance))
    print("configurations working on >= 5 markets:", int((broad.markets_working >= 5).sum()), "| best:", broad.sort_values("markets_working", ascending=False).head(6).to_string(index=False))
    pivot = sig.groupby(["tf", "strat"]).apply(lambda g: pd.Series(dict(avg_pf=g.pf.mean(), avg_exp_R=g.exp_R.mean(), avg_win=g.wr.mean())))
    print(pivot.round(3).to_string())
    print(res[res.works].to_string(index=False))
