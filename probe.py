"""Connectivity + market-cost probe. Run first to see what the broker charges."""
import os
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()
ok = mt5.initialize(
    path=os.getenv("MT5_PATH"),
    login=int(os.getenv("MT5_LOGIN")),
    password=os.getenv("MT5_PASSWORD"),
    server=os.getenv("MT5_SERVER"),
)
print("initialize:", ok, mt5.last_error())
if not ok:
    raise SystemExit(1)

ai = mt5.account_info()
print(f"\nACCOUNT {ai.login} {ai.server} | {ai.name}")
print(f"  balance={ai.balance} equity={ai.equity} currency={ai.currency} leverage=1:{ai.leverage}")
print(f"  trade_mode={ai.trade_mode} (0=demo) margin_mode={ai.margin_mode} trade_allowed={ai.trade_allowed}")
ti = mt5.terminal_info()
print(f"  terminal: connected={ti.connected} trade_allowed={ti.trade_allowed} algo={getattr(ti, 'tradeapi_disabled', None)}")

syms = mt5.symbols_get()
print(f"\n{len(syms)} symbols available")
wanted = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
          "EURJPY", "GBPJPY", "EURGBP", "XAUUSD", "AUDJPY", "EURAUD", "EURCHF"]
names = {s.name for s in syms}
# Exness may add suffixes (m, z, c, etc.)
found = {}
for w in wanted:
    for cand in (w, w + "m", w + "z", w + "c", w + "_"):
        if cand in names:
            found[w] = cand
            break
print("resolved:", found)

print("\nSYMBOL SPECS")
print(f"{'sym':10}{'spread(pts)':>12}{'digits':>7}{'pt':>10}{'tickval':>9}{'minlot':>8}{'stops':>6}{'fill':>6}{'exec':>6}  calc")
for w, s in found.items():
    mt5.symbol_select(s, True)
    i = mt5.symbol_info(s)
    t = mt5.symbol_info_tick(s)
    print(f"{s:10}{i.spread:>12}{i.digits:>7}{i.point:>10}{i.trade_tick_value:>9.4f}{i.volume_min:>8}{i.trade_stops_level:>6}"
          f"{i.filling_mode:>6}{i.trade_exemode:>6}  tick={t.bid if t else None}/{t.ask if t else None} "
          f"mode={i.trade_mode} swap_l={i.swap_long} swap_s={i.swap_short}")
mt5.shutdown()
