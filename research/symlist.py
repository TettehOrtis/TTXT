import os, MetaTrader5 as mt5, collections
from dotenv import load_dotenv
load_dotenv()
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("MT5_LOGIN")), password=os.getenv("MT5_PASSWORD"), server=os.getenv("MT5_SERVER"))
g = collections.defaultdict(list)
for s in mt5.symbols_get():
    g[s.path.split("\\")[0]].append(s.name)
for k, v in g.items(): print(k, len(v), v[:40])
mt5.shutdown()
