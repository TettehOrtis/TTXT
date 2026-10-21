import os, sys, pandas as pd
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "AUT13_leadlag")
pd.set_option("display.width", 250)
rows = []
for tg in ("XAUUSD", "EURUSD"):
    for st in ("EXPLORE", "VALIDATE", "STRESS"):
        d = pd.read_csv(os.path.join(OUT, "grid_%s_%s.csv" % (tg, st)))
        for _, r in d.iterrows():
            rows.append(dict(tg=tg, stage=st, k=r["k"], h=r["hold"], n=r["rule.trades"], usd=r["rule.usd_per_trade"],
                             rev=r["reversed_rule.usd_per_trade"], coin=r["coin_flip.usd_per_trade"],
                             own=r["own_move_fade.usd_per_trade"], buy=r["rule.buy_usd_per_trade"], sell=r["rule.sell_usd_per_trade"]))
g = pd.DataFrame(rows)
g.to_csv(os.path.join(OUT, "grid_all_stages.csv"), index=False)
p = g.pivot_table(index=["tg", "k", "h"], columns="stage", values=["usd", "own"])
print(p.round(2).to_string())
print(g.groupby(["tg", "stage"]).agg(best=("usd", "max"), mean_rule=("usd", "mean"), mean_own=("own", "mean"), mean_coin=("coin", "mean"), mean_rev=("rev", "mean")).round(3))
print("cells >0:", g[g.usd > 0].groupby(["tg", "stage"]).size())
