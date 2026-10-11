"""Read-only: swap terms for candidate symbols."""
import os, MetaTrader5 as mt5
from dotenv import load_dotenv
load_dotenv()
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("MT5_LOGIN")), password=os.getenv("MT5_PASSWORD"), server=os.getenv("MT5_SERVER"))
for s in ["XAUUSDm","US30m","USTECm","EURUSDm"]:
    i = mt5.symbol_info(s)
    print(s, "swap_mode", i.swap_mode, "long", i.swap_long, "short", i.swap_short, "3day", i.swap_rollover3days, "stops_level", i.trade_stops_level, "freeze", i.trade_freeze_level, "exec", i.trade_exemode, "bid", i.bid)
a = mt5.account_info(); print("leverage", a.leverage, "margin_so_so", a.margin_so_so, "currency", a.currency)
mt5.shutdown()
