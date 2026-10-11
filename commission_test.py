"""Measure the REAL all-in round-trip cost on the ZERO demo account: open + immediately close 0.10 lot on each pair,
read commission/fee from the deals, and measure spread + slippage. Needs the FX market open.
Usage: python commission_test.py            (prefix from .env: Z_LOGIN / Z_PASSWORD / Z_SERVER)"""
import os, time, datetime as dt
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv(".env")
SYMS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "EURJPY", "USDCHF", "XAUUSD"]
LOT, MAGIC = 0.10, 770077
assert mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"),
                      server=os.getenv("Z_SERVER")), mt5.last_error()
a = mt5.account_info()
assert a.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO, "demo only"
print(f"account {a.login} {a.server} bal={a.balance}\n")


def send(req):
    r = mt5.order_send(req)
    return r


rows = []
for base in SYMS:
    s = base + "z"
    mt5.symbol_select(s, True)
    i = mt5.symbol_info(s)
    t = mt5.symbol_info_tick(s)
    if t is None or time.time() - t.time > 120:
        print(f"{s}: market closed / stale tick"); continue
    fm = mt5.ORDER_FILLING_IOC if i.filling_mode & 2 else mt5.ORDER_FILLING_FOK
    pip = 0.1 if base == "XAUUSD" else (0.01 if "JPY" in base else 0.0001)
    pip_usd_1lot = pip / i.trade_tick_size * i.trade_tick_value
    sp_open = (t.ask - t.bid) / pip
    r1 = send(dict(action=mt5.TRADE_ACTION_DEAL, symbol=s, volume=LOT, type=mt5.ORDER_TYPE_BUY, price=t.ask, deviation=20,
                   magic=MAGIC, comment="costtest", type_time=mt5.ORDER_TIME_GTC, type_filling=fm))
    if r1 is None or r1.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"{s}: open failed {r1.retcode if r1 else mt5.last_error()}"); continue
    time.sleep(1.0)
    pos = [p for p in mt5.positions_get(symbol=s) or [] if p.magic == MAGIC]
    t2 = mt5.symbol_info_tick(s)
    for p in pos:
        r2 = send(dict(action=mt5.TRADE_ACTION_DEAL, symbol=s, volume=p.volume, type=mt5.ORDER_TYPE_SELL, position=p.ticket,
                       price=t2.bid, deviation=20, magic=MAGIC, comment="costtest", type_time=mt5.ORDER_TIME_GTC, type_filling=fm))
    time.sleep(1.0)
    deals = [d for d in (mt5.history_deals_get(position=r1.order) or [])]
    if not deals:
        deals = [d for d in (mt5.history_deals_get(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5),
                                                   dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)) or [])
                 if d.symbol == s and d.magic == MAGIC]
    comm = sum(d.commission + d.fee for d in deals)
    profit = sum(d.profit for d in deals)
    comm_pips = -comm / (pip_usd_1lot * LOT)
    rows.append((base, sp_open, -comm / LOT / 2, comm_pips, profit, profit + comm))
    print(f"{s:8} spread@open={sp_open:.2f}p | commission round trip ${-comm:.2f} for {LOT} lot "
          f"= ${-comm/LOT/2:.2f}/lot/side = {comm_pips:.2f} pips | price P/L ${profit:+.2f} | net ${profit+comm:+.2f}")
print("\nSUMMARY: all-in round-trip cost in pips = spread + commission_pips (+ slippage in price P/L)")
for b, sp, cps, cp, pr, net in rows:
    print(f"  {b:7} commission {cp:.2f} pips + spread ~{sp:.2f} = {cp+sp:.2f} pips  (${cps:.2f}/lot/side)")
mt5.shutdown()
