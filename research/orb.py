"""Opening-range breakout with trailing stop, cost-realistic, DST-aware session opens.
Usage: python research/orb.py            (runs the grid, prints per-config per-year expectancy)

Trial accounting: every grid cell is a trial. Select on 2021-2024, confirm on 2025-2026 (never tuned on).
"""
import sys, itertools
import numpy as np, pandas as pd
from numba import njit
from zoneinfo import ZoneInfo
sys.path.insert(0, "research")
from lib import load, pip_size

SESSIONS = {  # name: (tz, hh, mm)
    "NY": ("America/New_York", 9, 30),
    "LON": ("Europe/London", 8, 0),
    "FRA": ("Europe/Berlin", 9, 0),
}


def session_starts(d, tzname, hh, mm):
    days = pd.date_range(d.time.iloc[0].normalize(), d.time.iloc[-1].normalize(), freq="D")
    days = days[days.dayofweek < 5]
    tz = ZoneInfo(tzname)
    loc = [pd.Timestamp(year=x.year, month=x.month, day=x.day, hour=hh, minute=mm, tz=tz) for x in days]
    utc = pd.DatetimeIndex([t.tz_convert("UTC").tz_localize(None) for t in loc])
    tv = d.time.values
    pos = np.searchsorted(tv, utc.values)
    ok = (pos < len(tv) - 1)
    pos = pos[ok]; utc = utc[ok]
    gap = (tv[pos] - utc.values) / np.timedelta64(1, "m")
    keep = gap <= 3
    return pos[keep], utc[keep]


@njit(cache=True)
def orb_sim(op, hi, lo, cl, spr, starts, rng_n, win, stop_mode, kstop, trail_start, trail_dist, tp_r, buf, slip, pip, atr):
    """One trade per event. Returns (pnl_units, r_mult, side, ok)."""
    n = len(starts)
    pnl = np.full(n, np.nan); rm = np.full(n, np.nan); sd = np.zeros(n, np.int8)
    nb = len(op)
    for k in range(n):
        s = starts[k]
        if s + rng_n + win + 2 >= nb:
            continue
        rh = -1e18; rl = 1e18
        for j in range(s, s + rng_n):
            if hi[j] > rh: rh = hi[j]
            if lo[j] < rl: rl = lo[j]
        rsz = (rh - rl)
        a = atr[s + rng_n - 1] * pip
        if rsz <= 0 or a <= 0:
            continue
        side = 0
        entry = 0.0
        j = s + rng_n
        end = s + rng_n + win
        ej = -1
        while j < end:
            up = hi[j] + spr[j] * pip >= rh + buf * pip
            dn = lo[j] <= rl - buf * pip
            if up and dn:  # ambiguous bar -> skip event
                break
            if up:
                lvl = rh + buf * pip
                entry = max(lvl, op[j] + spr[j] * pip) + slip * pip
                side = 1; ej = j; break
            if dn:
                lvl = rl - buf * pip
                entry = min(lvl, op[j]) - slip * pip
                side = -1; ej = j; break
            j += 1
        if side == 0:
            continue
        # risk distance
        if stop_mode == 0:
            risk = kstop * a
        else:
            risk = max(min(rsz, kstop * a * 2), 0.5 * a)
        stop = entry - risk if side == 1 else entry + risk
        best = entry
        ex = 0.0
        done = False
        # entry bar: only adverse check (pessimistic) using its own extremes after fill
        j = ej
        while j < end and not done:
            if side == 1:
                if lo[j] <= stop:
                    ex = min(stop, op[j]) - slip * pip; done = True; break
                if tp_r > 0 and hi[j] >= entry + tp_r * risk:
                    ex = entry + tp_r * risk; done = True; break
                if hi[j] > best: best = hi[j]
                if best - entry >= trail_start * risk:
                    ns = best - trail_dist * risk
                    if ns > stop: stop = ns
            else:
                if hi[j] + spr[j] * pip >= stop:
                    ex = max(stop, op[j] + spr[j] * pip) + slip * pip; done = True; break
                if tp_r > 0 and lo[j] + spr[j] * pip <= entry - tp_r * risk:
                    ex = entry - tp_r * risk; done = True; break
                if lo[j] < best: best = lo[j]
                if entry - best >= trail_start * risk:
                    ns = best + trail_dist * risk + 0.0
                    # short stop is on the ask side: stop level compared to ask (=bid+spr)
                    if ns < stop: stop = ns
            j += 1
        if not done:
            j = end - 1
            ex = cl[j] - slip * pip if side == 1 else cl[j] + spr[j] * pip + slip * pip
        p = (ex - entry) if side == 1 else (entry - ex)
        pnl[k] = p / pip; rm[k] = p / risk; sd[k] = side
    return pnl, rm, sd


def run(sym, sess, rng_n, win, stop_mode, kstop, trail_start, trail_dist, tp_r, buf_k, slip_frac=0.25, start="2021-01-01"):
    d = load(sym, start)
    p = pip_size(sym)
    rng = (d.high - d.low) / p
    atr = rng.rolling(60).mean().values
    tz, hh, mm = SESSIONS[sess]
    st, utc = session_starts(d, tz, hh, mm)
    slip = slip_frac * float(np.median(d.spr.values))
    buf = buf_k * float(np.median(d.spr.values))
    pnl, rm, sd = orb_sim(d.open.values, d.high.values, d.low.values, d.close.values, d.spr.values,
                          st.astype(np.int64), rng_n, win, stop_mode, kstop, trail_start, trail_dist, tp_r, buf, slip, p, atr)
    df = pd.DataFrame({"t": utc, "pnl": pnl, "r": rm, "side": sd}).dropna()
    return df, p


def summarize(df):
    y = df.t.dt.year
    g = df.groupby(y).agg(n=("pnl", "size"), avg=("pnl", "mean"), win=("pnl", lambda x: (x > 0).mean() * 100), avgR=("r", "mean"))
    return g


if __name__ == "__main__":
    grid = []
    for sym, sess in [("XAUUSD", "NY"), ("XAUUSD", "LON"), ("US30", "NY"), ("USTEC", "NY"), ("DE30", "FRA")]:
        for rng_n, win, stop_mode, kstop, ts, td, tp_r in itertools.product(
                (5, 15, 30), (120, 240), (0, 1), (1.5, 3.0), (1.0,), (1.0,), (0.0, 2.0)):
            df, p = run(sym, sess, rng_n, win, stop_mode, kstop, ts, td, tp_r, 0.5)
            if len(df) < 200:
                continue
            tr = df[df.t.dt.year <= 2024]; te = df[df.t.dt.year >= 2025]
            grid.append(dict(sym=sym, sess=sess, rng=rng_n, win=win, smode=stop_mode, kstop=kstop, tp_r=tp_r,
                             n=len(df), tr_avgR=tr.r.mean(), tr_n=len(tr), te_avgR=te.r.mean(), te_n=len(te),
                             tr_win=(tr.pnl > 0).mean() * 100, te_win=(te.pnl > 0).mean() * 100, all_avg=df.pnl.mean()))
    g = pd.DataFrame(grid)
    pd.set_option("display.width", 250)
    print(f"{len(g)} configs tested (trials)")
    print("\nTOP 15 by train (2021-24) avg R  -> shown with untouched 2025-26 result:")
    print(g.sort_values("tr_avgR", ascending=False).head(15).round(3).to_string(index=False))
    print("\nBy instrument/session: mean of train avgR and test avgR across ALL configs (base rate):")
    print(g.groupby(["sym", "sess"])[["tr_avgR", "te_avgR"]].mean().round(3))
    g.to_csv("research/orb_grid.csv", index=False)
