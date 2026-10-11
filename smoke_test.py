"""Execution smoke test on the DEMO account: min-lot round trips on BTCUSDm (24/7) + order_check on gold.
Measures REAL spread, slippage, latency and net cost after commission/swap."""
import time, sys, logging
import MetaTrader5 as mt5
from scalper.broker import Broker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
b = Broker()
b.connect()
S = "BTCUSDm"
sp = b.spec(S)
print(f"{S}: digits={sp.digits} tick_size={sp.trade_tick_size} tick_value={sp.trade_tick_value} vmin={sp.volume_min} "
      f"stops_lvl={sp.trade_stops_level} freeze={sp.trade_freeze_level} fillmask={sp.filling_mode} exe={sp.trade_exemode}")
print("is_open:", b.is_open(S))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3
rows = []
for k in range(N):
    t = b.tick(S)
    spread = t.ask - t.bid
    side = 1 if k % 2 == 0 else -1
    dist = 400.0                     # wide protective stop/target so they never interfere in this test
    sl = t.ask - dist if side == 1 else t.bid + dist
    tp = t.ask + dist if side == 1 else t.bid - dist
    f = b.open(S, side, sp.volume_min, sl=sl, tp=tp, comment="smoke")
    print(f"[{k}] OPEN side={side:+d} ok={f.ok} ret={f.retcode} '{f.comment}' spread={spread:.2f} intended={f.intended} "
          f"filled={f.price} slip={f.slippage:+.2f} lat={f.latency_ms:.0f}ms")
    if not f.ok:
        continue
    time.sleep(2.5)
    pos = b.positions(S)
    print(f"     open positions: {[(p.ticket, p.volume, p.price_open, p.sl, p.tp, round(p.profit, 2)) for p in pos]}")
    for p in pos:
        c = b.close(p)
        print(f"     CLOSE ok={c.ok} ret={c.retcode} filled={c.price} slip={c.slippage:+.2f} lat={c.latency_ms:.0f}ms")
    time.sleep(1)
    deals = [d for d in b.deals_since(time.time() - 600) if d.symbol == S]
    net = sum(d.profit + d.commission + d.swap + d.fee for d in deals[-2:])
    rows.append(net)
    print(f"     net P/L of this round trip (profit+comm+swap+fee) = {net:+.4f} USD")
print("round-trip nets:", [round(x, 4) for x in rows])

# closed-market path check on gold (expect 'market closed' on a Saturday)
G = "XAUUSDm"
g = b.spec(G)
t = b.tick(G)
print(f"\n{G}: is_open={b.is_open(G)} tick_age={time.time() - t.time:.0f}s")
f = b.open(G, 1, g.volume_min, sl=t.ask - 5, tp=t.ask + 5, comment="chk")
print("gold order_check path ->", f.ok, f.retcode, f.comment)
print("account after:", b.account().balance, b.account().equity)
b.shutdown()
