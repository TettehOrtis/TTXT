import os, time, MetaTrader5 as mt5
from dotenv import load_dotenv; load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
pip = lambda b: 0.01 if "JPY" in b else 0.0001
for p in sorted([p for p in mt5.positions_get() or [] if p.magic == 880300], key=lambda p: p.comment):
    b = p.symbol[:-1]; t = mt5.symbol_info_tick(p.symbol); cur = t.bid if p.type == 0 else t.ask
    pips = (cur - p.price_open) / pip(b) * (1 if p.type == 0 else -1)
    to_sl = abs(cur - p.sl) / pip(b); to_tp = abs(p.tp - cur) / pip(b)
    print(f"{p.symbol} {p.volume:.2f} {'BUY ' if p.type == 0 else 'SELL'} {p.comment:10} {pips:+5.1f} pips  ${p.profit:+6.2f} | {to_tp:4.1f} pips to TP, {to_sl:4.1f} to SL | tick age {time.time()-t.time:.0f}s")
mt5.shutdown()
