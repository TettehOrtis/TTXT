import os, datetime as dt, MetaTrader5 as mt5
from dotenv import load_dotenv; load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
print(mt5.account_info().login)
ds = mt5.history_deals_get(dt.datetime(2026, 9, 26), dt.datetime(2026, 10, 1)) or []
print("deals", len(ds))
why = {mt5.DEAL_REASON_SL: "SL", mt5.DEAL_REASON_TP: "TP", mt5.DEAL_REASON_CLIENT: "manual-terminal", mt5.DEAL_REASON_EXPERT: "script", mt5.DEAL_REASON_MOBILE: "mobile", mt5.DEAL_REASON_WEB: "web"}
for d in ds:
    if d.magic == 880300 and d.entry == 1 or (d.entry == 1 and d.time > 1790500000):
        print(dt.datetime.fromtimestamp(d.time, dt.timezone.utc).strftime("%a %H:%M"), d.symbol, d.volume, d.comment, why.get(d.reason, d.reason), "profit", d.profit, "swap", d.swap, "magic", d.magic)
mt5.shutdown()
