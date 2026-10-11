"""Overnight test on the ZERO demo: 15 trades with server-side SL/TP (they run with the laptop closed).
   python overnight_trades.py open     |   python overnight_trades.py report   (history comes from the broker server)"""
import os, sys, time, datetime as dt, MetaTrader5 as mt5
from dotenv import load_dotenv
load_dotenv(".env")
MAGIC = 880300
SL = 20
# (pair, lot, tp_pips): 7 x 0.10 and 8 x 0.01; repeated pairs use different targets
PLAN = [("EURUSD", 0.10, 20), ("EURUSD", 0.10, 40), ("USDJPY", 0.10, 20), ("USDJPY", 0.10, 40),
        ("GBPUSD", 0.10, 20), ("GBPUSD", 0.10, 40), ("AUDUSD", 0.10, 20),
        ("EURUSD", 0.01, 20), ("EURUSD", 0.01, 40), ("USDJPY", 0.01, 20), ("USDJPY", 0.01, 40),
        ("GBPUSD", 0.01, 20), ("GBPUSD", 0.01, 40), ("AUDUSD", 0.01, 20), ("AUDUSD", 0.01, 40)]
assert mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
assert mt5.account_info().trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
pip = lambda b: 0.01 if "JPY" in b else 0.0001
UTC = dt.timezone.utc

if sys.argv[1] == "open":
    if [p for p in (mt5.positions_get() or []) if p.magic == MAGIC]:
        sys.exit("overnight trades already open")
    sides = {}
    for b in {x[0] for x in PLAN}:
        r = mt5.copy_rates_from_pos(b + "z", mt5.TIMEFRAME_M1, 1, 240)
        sides[b] = 1 if r["close"][-1] > r["close"][0] else -1
    print("start balance", mt5.account_info().balance)
    for k, (b, lot, tp) in enumerate(PLAN):
        s = b + "z"; mt5.symbol_select(s, True); i = mt5.symbol_info(s); side = sides[b]
        for attempt in range(4):
            t = mt5.symbol_info_tick(s); price = t.ask if side == 1 else t.bid
            req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=s, volume=lot, price=price, deviation=20, magic=MAGIC,
                       type=mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL,
                       sl=round(price - side * SL * pip(b), i.digits), tp=round(price + side * tp * pip(b), i.digits),
                       comment=f"ovn{k}_tp{tp}", type_time=mt5.ORDER_TIME_GTC,
                       type_filling=mt5.ORDER_FILLING_IOC if i.filling_mode & 2 else mt5.ORDER_FILLING_FOK)
            r = mt5.order_send(req)
            if r and r.retcode == mt5.TRADE_RETCODE_DONE: break
            time.sleep(3)
        print(f"{k:2} {b} {'BUY ' if side == 1 else 'SELL'} {lot:.2f} @ {r.price if r else '-'} SL {req['sl']} TP {req['tp']} "
              f"-> {'OK' if r and r.retcode == mt5.TRADE_RETCODE_DONE else 'FAILED ' + str(r.retcode if r else mt5.last_error())}")
    time.sleep(1)
    print("open now:", len([p for p in (mt5.positions_get() or []) if p.magic == MAGIC]), "| balance after commission", mt5.account_info().balance)

elif sys.argv[1] == "report":
    now = dt.datetime.now(UTC)
    deals = [d for d in (mt5.history_deals_get(dt.datetime(2026, 9, 26), dt.datetime(2026, 12, 31)) or []) if d.magic == MAGIC]
    pos = {}
    for d in deals: pos.setdefault(d.position_id, []).append(d)
    why = {mt5.DEAL_REASON_SL: "STOP LOSS", mt5.DEAL_REASON_TP: "TAKE PROFIT", mt5.DEAL_REASON_CLIENT: "manual", mt5.DEAL_REASON_EXPERT: "script"}
    tot = 0
    for pid, ds in sorted(pos.items(), key=lambda x: x[1][0].time):
        i_ = [d for d in ds if d.entry == 0][0]; o = [d for d in ds if d.entry == 1]
        net = sum(d.profit + d.commission + d.swap + d.fee for d in ds); tot += net
        if o:
            o = o[-1]; b = i_.symbol[:-1]
            pips = (o.price - i_.price) / pip(b) * (1 if i_.type == 0 else -1)
            print(f"{i_.symbol} {i_.volume:.2f} {'BUY ' if i_.type == 0 else 'SELL'} {i_.comment:12} opened {dt.datetime.fromtimestamp(i_.time, UTC):%H:%M} "
                  f"closed {dt.datetime.fromtimestamp(o.time, UTC):%a %H:%M} by {why.get(o.reason, o.reason):11} {pips:+6.1f} pips  net ${net:+.2f}")
        else:
            print(f"{i_.symbol} {i_.volume:.2f} {i_.comment:12} STILL OPEN (commission ${net:+.2f})")
    op = [p for p in (mt5.positions_get() or []) if p.magic == MAGIC]
    fl = sum(p.profit + p.swap for p in op)
    print(f"\nclosed+commission net ${tot:+.2f} | still open {len(op)} floating ${fl:+.2f} | balance {mt5.account_info().balance} equity {mt5.account_info().equity}")
mt5.shutdown()
