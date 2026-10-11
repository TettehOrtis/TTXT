import os, datetime as dt, MetaTrader5 as mt5
from dotenv import load_dotenv; load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
a = mt5.account_info(); print("balance", a.balance, "equity", a.equity, "open positions", len(mt5.positions_get() or []))
now = dt.datetime.now(dt.timezone.utc)
for d in mt5.history_deals_get(now - dt.timedelta(hours=2), now + dt.timedelta(hours=1)) or []:
    if d.magic in (880088, 880100):
        print(dt.datetime.fromtimestamp(d.time, dt.timezone.utc).strftime("%H:%M:%S"), d.symbol, "IN " if d.entry == 0 else "OUT", d.volume,
              "price", d.price, "profit", d.profit, "comm", d.commission)
mt5.shutdown()
