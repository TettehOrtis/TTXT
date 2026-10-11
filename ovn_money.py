import os, datetime as dt, numpy as np, MetaTrader5 as mt5
from dotenv import load_dotenv; load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
UTC = dt.timezone.utc; pip = lambda b: 0.01 if "JPY" in b else 0.0001
now = dt.datetime.now(UTC)
deals = [d for d in (mt5.history_deals_get(now - dt.timedelta(days=3), now + dt.timedelta(days=1)) or []) if d.magic == 880300]
by = {}
for d in deals: by.setdefault(d.position_id, []).append(d)
tot_best = tot_now = tot_comm = 0
for pid, ds in sorted(by.items(), key=lambda x: (x[1][0].symbol, -x[1][0].volume, x[1][0].time)):
    i_ = [d for d in ds if d.entry == 0][0]; o = [d for d in ds if d.entry == 1]
    b = i_.symbol[:-1]; side = 1 if i_.type == 0 else -1
    comm = -sum(d.commission + d.fee for d in ds)
    t0 = dt.datetime.fromtimestamp(i_.time, UTC); t1 = dt.datetime.fromtimestamp(o[-1].time, UTC) if o else now
    tk = mt5.copy_ticks_range(i_.symbol, t0, t1 + dt.timedelta(seconds=1), mt5.COPY_TICKS_ALL)
    px = tk["bid"] if side == 1 else tk["ask"]
    pv = mt5.symbol_info(i_.symbol); usd_pip = pip(b) / pv.trade_tick_size * pv.trade_tick_value * i_.volume
    usd = (px - i_.price) * side / pip(b) * usd_pip           # price P/L in $
    net = usd - comm                                           # after commission
    cover = np.where(net > 0)[0]; red = np.where(usd <= -comm - 1e-9)[0]   # red = losing at least the commission again
    first_green = dt.datetime.fromtimestamp(tk["time"][cover[0]], UTC) if len(cover) else None
    first_red = dt.datetime.fromtimestamp(tk["time"][red[0]], UTC) if len(red) else None
    order = "PROFIT first" if first_green and (not first_red or first_green <= first_red) else "LOSS first"
    now_net = sum(d.profit for d in o) - comm if o else net[-1]
    tot_best += net.max(); tot_now += now_net; tot_comm += comm
    print(f"{b} {i_.volume:.2f} {'BUY ' if side==1 else 'SELL'} | commission ${comm:.2f} | best after commission ${net.max():+6.2f} | "
          f"covered commission: {'YES at ' + format(first_green, '%H:%M') if first_green else 'NO '} | {order:12} | "
          f"{'closed' if o else 'now'} ${now_net:+6.2f}")
print(f"\nTOTAL commission ${tot_comm:.2f} | if each had closed at its best: ${tot_best:+.2f} | actual/now: ${tot_now:+.2f}")
mt5.shutdown()
