import time, logging, MetaTrader5 as mt5
from scalper.broker import Broker
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
t0=time.time(); b = Broker(); b.connect(); print(f"connect {time.time()-t0:.1f}s", flush=True)
for p in b.positions():
    t=time.time(); c = b.close(p)
    print(f"close {p.ticket} {p.symbol}: ok={c.ok} ret={c.retcode} '{c.comment}' filled={c.price} slip={c.slippage:+.2f} took={time.time()-t:.1f}s", flush=True)
time.sleep(1)
print("left open:", len(b.positions()), "| equity", b.account().equity, flush=True)
deals = b.deals_since(time.time()-3600)
for d in deals: print(" deal", d.ticket, d.symbol, "entry", d.entry, "type", d.type, "vol", d.volume, "price", d.price, "profit", d.profit, "comm", d.commission, "swap", d.swap, "fee", d.fee, flush=True)
b.shutdown()
