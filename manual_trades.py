"""Manual trades on the ZERO demo. open: trend-following (close now vs 240 M1 bars ago), 0.01 lot, 30-pip safety SL.
   python manual_trades.py open | status | close"""
import os, sys, time, MetaTrader5 as mt5
from dotenv import load_dotenv
load_dotenv(".env")
PAIRS, SL_PIPS = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD"], 30
LOT = float(sys.argv[2]) if len(sys.argv) > 2 else 0.01
MAGICS = {0.01: 880088, 0.1: 880100}
MAGIC = MAGICS.get(LOT, 880199)
assert mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
assert mt5.account_info().trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
pip = lambda b: 0.01 if "JPY" in b else 0.0001
mine = lambda: [p for p in (mt5.positions_get() or []) if p.magic in set(MAGICS.values()) | {880199}]
mine_lot = lambda: [p for p in mine() if p.magic == MAGIC]
fill = lambda i: mt5.ORDER_FILLING_IOC if i.filling_mode & 2 else mt5.ORDER_FILLING_FOK
cmd = sys.argv[1]
if cmd == "open":
    if mine_lot(): sys.exit(f"already {len(mine_lot())} manual positions open at lot {LOT}")
    for b in PAIRS:
        s = b + "z"; mt5.symbol_select(s, True); i = mt5.symbol_info(s); t = mt5.symbol_info_tick(s)
        r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M1, 1, 240)
        move = (r["close"][-1] - r["close"][0]) / pip(b)
        side = 1 if move > 0 else -1
        price = t.ask if side == 1 else t.bid
        sl = price - side * SL_PIPS * pip(b)
        res = mt5.order_send(dict(action=mt5.TRADE_ACTION_DEAL, symbol=s, volume=LOT, type=mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL,
                                  price=price, sl=round(sl, i.digits), deviation=20, magic=MAGIC, comment="manual-trend",
                                  type_time=mt5.ORDER_TIME_GTC, type_filling=fill(i)))
        print(f"{b}: 4h move {move:+.1f} pips -> {'BUY ' if side == 1 else 'SELL'} {LOT} @ {res.price if res else None} "
              f"SL {sl:.{i.digits}f} | spread {(t.ask-t.bid)/pip(b):.1f}p | {res.retcode if res else mt5.last_error()} {res.comment if res else ''}")
elif cmd == "status":
    tot = 0
    for p in mine():
        b = p.symbol[:-1]; t = mt5.symbol_info_tick(p.symbol); cur = t.bid if p.type == 0 else t.ask
        pips = (cur - p.price_open) / pip(b) * (1 if p.type == 0 else -1)
        tot += p.profit
        print(f"{b} {p.volume:.2f} {'BUY ' if p.type == 0 else 'SELL'} open {p.price_open} now {cur} | {pips:+.1f} pips | P/L ${p.profit:+.2f} (before close commission)")
    print(f"TOTAL floating ${tot:+.2f} | positions {len(mine())}")
elif cmd == "close":
    for p in mine():
        t = mt5.symbol_info_tick(p.symbol); i = mt5.symbol_info(p.symbol)
        r = mt5.order_send(dict(action=mt5.TRADE_ACTION_DEAL, symbol=p.symbol, volume=p.volume, position=p.ticket,
                                type=mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY, price=t.bid if p.type == 0 else t.ask,
                                deviation=20, magic=MAGIC, comment="manual-close", type_time=mt5.ORDER_TIME_GTC, type_filling=fill(i)))
        print(p.symbol, "closed" if r and r.retcode == mt5.TRADE_RETCODE_DONE else f"FAILED {r.retcode if r else mt5.last_error()}")
    time.sleep(1)
    import datetime as dt
    ds = mt5.history_deals_get(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3), dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)) or []
    ds = [d for d in ds if d.magic in set(MAGICS.values()) | {880199}]
    net = sum(d.profit + d.commission + d.swap + d.fee for d in ds)
    for sym, mg in sorted({(d.symbol, d.magic) for d in ds}):
        x = [d for d in ds if d.symbol == sym and d.magic == mg]
        print(f"  {sym} lot {x[0].volume}: price ${sum(d.profit for d in x):+.2f} commission ${sum(d.commission + d.fee for d in x):+.2f} swap ${sum(d.swap for d in x):+.2f}")
    print(f"NET RESULT all manual trades: ${net:+.2f} | balance {mt5.account_info().balance}")
mt5.shutdown()
