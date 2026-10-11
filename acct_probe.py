"""Read-only probe of a second account: type, symbol suffix, spreads. Usage: python acct_probe.py ZERO"""
import os, sys, collections, MetaTrader5 as mt5
from dotenv import load_dotenv
load_dotenv(); P = sys.argv[1]
ok = mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv(f"{P}_LOGIN")), password=os.getenv(f"{P}_PASSWORD"), server=os.getenv(f"{P}_SERVER"))
print("init", ok, mt5.last_error())
a = mt5.account_info(); print("login", a.login, "name", a.name, "server", a.server, "bal", a.balance, "trade_mode", a.trade_mode, "lev", a.leverage, "margin_mode", a.margin_mode)
syms = mt5.symbols_get(); c = collections.Counter(s.path.split("\\")[0] for s in syms); print(len(syms), "symbols, groups:", dict(c))
fx = [s.name for s in syms if s.name.upper().startswith(("EURUSD", "EURJPY", "USDJPY", "NZDUSD", "XAUUSD"))]; print("names:", fx)
for n in fx:
    mt5.symbol_select(n, True); i = mt5.symbol_info(n); t = mt5.symbol_info_tick(n)
    print(f"  {n:12} spread_pts={i.spread} float={i.spread_float} digits={i.digits} path={i.path}")
mt5.shutdown()
