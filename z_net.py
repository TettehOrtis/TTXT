import os, MetaTrader5 as mt5, pandas as pd, glob
from dotenv import load_dotenv; load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
print("open positions on Zero:", len(mt5.positions_get() or []), "| balance", mt5.account_info().balance)
mt5.shutdown()
# real round-trip commission in pips (active-hours spread ~0) + 0.1 pip slippage allowance
COST = dict(EURUSD=0.50, GBPUSD=0.63, USDJPY=0.79, AUDUSD=0.60, USDCAD=0.99, NZDUSD=0.90, EURGBP=0.83, EURJPY=1.10, USDCHF=0.87)
SLIP = 0.10
rows = []
for f in sorted(glob.glob("research/zero/*_mid.csv")):
    d = pd.read_csv(f); sym = d.sym.iloc[0]; c = COST[sym] + SLIP
    g = d[d.C == -1].dropna(subset=["test_gross"])
    for (H, q), x in g.groupby(["H", "q"]):
        net = x.test_gross - c
        rows.append(dict(sym=sym, H=H, q=q, cost=c, gross=(x.test_gross*x.test_n).sum()/x.test_n.sum(),
                         net=((x.test_gross-c)*x.test_n).sum()/x.test_n.sum(), per_day=x.test_per_day.mean(),
                         blocks_net_pos=f"{int((net>0).sum())}/{len(x)}",
                         per_block=" ".join(f"{r.test}:{r.test_gross-c:+.2f}" for r in x.itertuples())))
r = pd.DataFrame(rows)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 200)
print("\nAll settings with POSITIVE net after REAL Zero commission + 0.1 pip slippage (unseen test blocks):")
print(r[r.net > 0].sort_values("net", ascending=False).round(2).to_string(index=False))
print("\nBest net per pair:")
print(r.sort_values("net", ascending=False).groupby("sym").head(1)[["sym","H","q","cost","gross","net","per_day","blocks_net_pos"]].round(2).to_string(index=False))
