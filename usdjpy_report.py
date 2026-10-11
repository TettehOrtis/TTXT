"""Results of the USDJPY forward test, straight from the broker's history (works even after restarts)."""
import os, datetime as dt, MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
UTC = dt.timezone.utc
deals = [d for d in (mt5.history_deals_get(dt.datetime(2026, 9, 29), dt.datetime(2027, 12, 31)) or []) if d.magic == 660066]
by = {}
for d in deals:
    by.setdefault(d.position_id, []).append(d)
why = {mt5.DEAL_REASON_SL: "stop loss", mt5.DEAL_REASON_TP: "take profit", mt5.DEAL_REASON_EXPERT: "20-min exit", mt5.DEAL_REASON_CLIENT: "manual"}
tot, wins, n, days = 0.0, 0, 0, {}
for pid, ds in sorted(by.items(), key=lambda x: x[1][0].time):
    i_ = [d for d in ds if d.entry == 0]; o = [d for d in ds if d.entry == 1]
    if not i_ or not o:
        continue
    i_, o = i_[0], o[-1]
    net = sum(d.profit + d.commission + d.swap + d.fee for d in ds)
    pips = (o.price - i_.price) / 0.01 * (1 if i_.type == 0 else -1)
    t = dt.datetime.fromtimestamp(i_.time, UTC)
    tot += net; n += 1; wins += net > 0; days[t.date()] = days.get(t.date(), 0) + net
    print(f"{t:%a %d %b %H:%M} {'BUY ' if i_.type == 0 else 'SELL'} {i_.volume:.2f} {pips:+6.1f} pips  net ${net:+7.2f}  ({why.get(o.reason, o.reason)})")
op = [p for p in (mt5.positions_get() or []) if p.magic == 660066]
print(f"\ntrades {n} | wins {wins} ({wins / n * 100 if n else 0:.0f}%) | NET ${tot:+.2f} | avg ${tot / n if n else 0:+.2f}/trade | open now {len(op)}")
for dday, v in sorted(days.items()):
    print(f"  {dday}: ${v:+.2f}")
print("balance", mt5.account_info().balance)
mt5.shutdown()
