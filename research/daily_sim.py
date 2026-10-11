"""What does a 'daily profit target then stop' rule do to daily outcomes when entries have ~zero edge?
Zero-edge baseline: random side, entries only in liquid hours, ATR-scaled bracket, REAL bar spreads + slippage,
last 12 months of gold, real min-lot granularity. Usage: python research/daily_sim.py"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "research")
from lib import *

sym = "XAUUSD"
d = load(sym, "2025-09-01")
f = features(d, sym)
p = pip_size(sym)                       # 0.10 = $0.10 per unit
atr = f["atr60"].values
act = active_mask(d) & f.notna().all(axis=1).values
day = d.time.dt.floor("D").values
SLIP = 0.25 * float(np.median(d.spr.values[act]))
K = 1.5                                 # TP = SL = K * ATR60
idx = np.where(act)[0][::5]             # candidate signal every 5 minutes
rs = np.random.default_rng(1)
side = rs.choice([-1, 1], len(idx))
sl_units = K * atr[idx]
pnl_u = np.empty(len(idx)); held = np.empty(len(idx), int)
for s in (1, -1):
    m = side == s
    a, b, _ = run_sim(d, sym, idx[m], s, sl_units[m], sl_units[m], 30, SLIP)
    pnl_u[m], held[m] = a, b
ok = ~np.isnan(pnl_u)
idx, side, sl_units, pnl_u, held = idx[ok], side[ok], sl_units[ok], pnl_u[ok], held[ok]
print(f"{len(idx)} candidate trades | median SL = {np.median(sl_units):.0f} units = ${np.median(sl_units)*p:.2f}/oz | "
      f"zero-edge avg net/trade = {pnl_u.mean():+.2f} units (${pnl_u.mean()*p:+.3f}/oz) | win% {np.mean(pnl_u>0)*100:.1f}")

days = day[idx]


def run_days(bal, risk_pct, tgt_pct, loss_pct, max_trades=12, min_mult=2.5, tgt_min=2.0):
    tgt = max(tgt_min, bal * tgt_pct / 100); lim = bal * loss_pct / 100
    out = []
    for dd in np.unique(days):
        ii = np.where(days == dd)[0]
        pnl = 0.0; n = 0; last_end = -1; peak = 0.0
        for k in ii:
            gi = idx[k]
            if gi <= last_end:
                continue
            per_oz_risk = sl_units[k] * p                           # $ risk per 1 oz (0.01 lot)
            risk_usd = max(0.5, bal * risk_pct / 100)
            lots01 = int(risk_usd / per_oz_risk)                    # multiples of 0.01 lot (1 oz each)
            if lots01 < 1:
                if per_oz_risk <= risk_usd * min_mult:
                    lots01 = 1
                else:
                    continue                                        # min-lot too risky for this account
            t = pnl_u[k] * p * lots01
            pnl += t; n += 1; last_end = gi + held[k]
            peak = max(peak, pnl)
            if pnl >= tgt or pnl <= -lim or n >= max_trades:
                break
        out.append((pnl, n))
    return pd.DataFrame(out, columns=["pnl", "n"])


rows = []
for bal in (100, 250, 500):
    for risk in (1.0, 3.0):
        for tgt, loss in ((2.0, 3.0), (1.0, 3.0), (3.0, 3.0)):
            r = run_days(bal, risk, tgt, loss)
            tr = r[r.n > 0]
            if len(tr) == 0:
                rows.append((bal, risk, tgt, loss, 0, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan)); continue
            rows.append((bal, risk, tgt, loss, len(tr), (tr.pnl > 0).mean() * 100, tr.pnl[tr.pnl > 0].mean() if (tr.pnl > 0).any() else 0,
                         tr.pnl[tr.pnl <= 0].mean() if (tr.pnl <= 0).any() else 0, tr.pnl.mean(), tr.pnl.min(), tr.n.mean()))
g = pd.DataFrame(rows, columns=["bal$", "risk%", "tgt%", "loss%", "days", "green%", "avg_green$", "avg_red$", "mean/day$", "worst$", "trades/day"])
pd.set_option("display.width", 200)
print("\nZERO-EDGE gold scalper, real 2025-09..2026-09 costs, daily target->stop, loss limit, max 12 trades")
print(g.round(2).to_string(index=False))
print("\n(rows with days=0: the 0.01-lot minimum risks more than the account can afford at that risk%)")
