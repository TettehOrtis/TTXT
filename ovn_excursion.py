"""Max favourable / adverse excursion of each overnight trade from broker TICKS (open -> close or now)."""
import os, datetime as dt, numpy as np, MetaTrader5 as mt5
from dotenv import load_dotenv; load_dotenv(".env")
mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"), server=os.getenv("Z_SERVER"))
UTC = dt.timezone.utc; MAGIC = 880300
pip = lambda b: 0.01 if "JPY" in b else 0.0001
now = dt.datetime.now(UTC)
deals = [d for d in (mt5.history_deals_get(now - dt.timedelta(days=3), now + dt.timedelta(days=1)) or []) if d.magic == MAGIC]
openpos = {p.ticket: p for p in (mt5.positions_get() or []) if p.magic == MAGIC}
by = {}
for d in deals: by.setdefault(d.position_id, []).append(d)
seen = {}
rows = []
for pid, ds in sorted(by.items(), key=lambda x: x[1][0].time):
    i_ = [d for d in ds if d.entry == 0][0]; o = [d for d in ds if d.entry == 1]
    b = i_.symbol[:-1]; side = 1 if i_.type == 0 else -1
    t0 = dt.datetime.fromtimestamp(i_.time, UTC)
    t1 = dt.datetime.fromtimestamp(o[-1].time, UTC) if o else now
    key = (i_.symbol, t0.replace(second=0), t1.replace(second=0))
    if key not in seen:
        tk = mt5.copy_ticks_range(i_.symbol, t0, t1 + dt.timedelta(seconds=1), mt5.COPY_TICKS_ALL)
        px = tk["bid"] if side == 1 else tk["ask"]          # exit side
        prof = (px - i_.price) * side / pip(b)
        k_max, k_min = int(np.argmax(prof)), int(np.argmin(prof))
        seen[key] = (prof[k_max], dt.datetime.fromtimestamp(tk["time"][k_max], UTC), prof[k_min], dt.datetime.fromtimestamp(tk["time"][k_min], UTC), prof[-1], len(tk))
    best, tb, worst, tw, last, n = seen[key]
    pv = mt5.symbol_info(i_.symbol); usd_per_pip = pip(b) / pv.trade_tick_size * pv.trade_tick_value * i_.volume
    status = f"closed {t1:%a %H:%M} ({'SL' if o and o[-1].reason == mt5.DEAL_REASON_SL else 'TP' if o and o[-1].reason == mt5.DEAL_REASON_TP else 'other'})" if o else "OPEN now"
    rows.append((b, i_.volume, 'BUY ' if side == 1 else 'SELL', i_.comment, best, tb, worst, tw, last, usd_per_pip, status))
print(f"{'pair':7}{'lot':>5} {'side':5}{'trade':12}{'MAX PROFIT':>22}{'MAX LOSS':>24}{'now/exit':>10}   status")
for b, v, s, c, best, tb, worst, tw, last, u, st in rows:
    print(f"{b:7}{v:5.2f} {s:5}{c:12}{best:+6.1f}p ${best*u:+7.2f} @{tb:%H:%M}{worst:+7.1f}p ${worst*u:+7.2f} @{tw:%H:%M}{last:+8.1f}p   {st}")
print("\n(times UTC; profit measured at the price you could actually close at; commission not included)")
mt5.shutdown()
