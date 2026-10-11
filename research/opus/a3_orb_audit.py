"""ORB audit: copy of research/orb.py orb_sim with switches for each pessimistic assumption."""
import sys, itertools
import numpy as np, pandas as pd
from numba import njit
sys.path.insert(0, "research")
from lib import load, pip_size
from orb import session_starts, SESSIONS, orb_sim


@njit(cache=True)
def orb2(op, hi, lo, cl, spr, starts, rng_n, win, stop_mode, kstop, trail_start, trail_dist, tp_r, buf, slip, pip, atr,
         entry_bar_check, amb_take):
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
        rsz = rh - rl
        a = atr[s + rng_n - 1] * pip
        if rsz <= 0 or a <= 0:
            continue
        side = 0; entry = 0.0; j = s + rng_n; end = s + rng_n + win; ej = -1
        while j < end:
            up = hi[j] + spr[j] * pip >= rh + buf * pip
            dn = lo[j] <= rl - buf * pip
            if up and dn:
                if not amb_take:
                    break
                # take the side nearer the open (first touched under path heuristic)
                if (rh + buf * pip - (op[j] + spr[j] * pip)) <= (op[j] - (rl - buf * pip)):
                    dn = False
                else:
                    up = False
            if up:
                entry = max(rh + buf * pip, op[j] + spr[j] * pip) + slip * pip; side = 1; ej = j; break
            if dn:
                entry = min(rl - buf * pip, op[j]) - slip * pip; side = -1; ej = j; break
            j += 1
        if side == 0:
            continue
        if stop_mode == 0:
            risk = kstop * a
        else:
            risk = max(min(rsz, kstop * a * 2), 0.5 * a)
        stop = entry - risk if side == 1 else entry + risk
        best = entry; ex = 0.0; done = False
        j = ej
        while j < end and not done:
            chk = entry_bar_check or j > ej
            if side == 1:
                if chk and lo[j] <= stop:
                    ex = min(stop, op[j]) - slip * pip; done = True; break
                if tp_r > 0 and hi[j] >= entry + tp_r * risk:
                    ex = entry + tp_r * risk; done = True; break
                if hi[j] > best: best = hi[j]
                if best - entry >= trail_start * risk:
                    ns = best - trail_dist * risk
                    if ns > stop: stop = ns
            else:
                if chk and hi[j] + spr[j] * pip >= stop:
                    ex = max(stop, op[j] + spr[j] * pip) + slip * pip; done = True; break
                if tp_r > 0 and lo[j] + spr[j] * pip <= entry - tp_r * risk:
                    ex = entry - tp_r * risk; done = True; break
                if lo[j] < best: best = lo[j]
                if entry - best >= trail_start * risk:
                    ns = best + trail_dist * risk
                    if ns < stop: stop = ns
            j += 1
        if not done:
            j = end - 1
            ex = cl[j] - slip * pip if side == 1 else cl[j] + spr[j] * pip + slip * pip
        pp = (ex - entry) if side == 1 else (entry - ex)
        pnl[k] = pp / pip; rm[k] = pp / risk; sd[k] = side
    return pnl, rm, sd


cache = {}
def go(sym, sess, rng_n, win, smode, kstop, tp_r, ebc=True, amb=False, slipf=0.25, bufk=0.5):
    if sym not in cache:
        d = load(sym); p = pip_size(sym)
        atr = ((d.high - d.low) / p).rolling(60).mean().values
        cache[sym] = (d, p, atr)
    d, p, atr = cache[sym]
    tz, hh, mm = SESSIONS[sess]
    st, utc = session_starts(d, tz, hh, mm)
    med = float(np.median(d.spr.values))
    args = (d.open.values, d.high.values, d.low.values, d.close.values, d.spr.values, st.astype(np.int64), rng_n, win,
            smode, kstop, 1.0, 1.0, tp_r, bufk * med, slipf * med, p, atr)
    pn, rm, sd = orb2(*args, ebc, amb)
    df = pd.DataFrame({"t": utc, "r": rm, "pnl": pn}).dropna()
    tr = df[df.t.dt.year <= 2024]; te = df[df.t.dt.year >= 2025]
    return len(df), tr.r.mean(), te.r.mean()


# sanity: identical to orb_sim with default switches
d = load("XAUUSD"); p = pip_size("XAUUSD"); atr = ((d.high - d.low) / p).rolling(60).mean().values
st, utc = session_starts(d, "America/New_York", 9, 30); med = float(np.median(d.spr.values))
a = orb_sim(d.open.values, d.high.values, d.low.values, d.close.values, d.spr.values, st.astype(np.int64), 30, 120, 1, 3.0, 1.0, 1.0, 2.0, 0.5 * med, 0.25 * med, p, atr)[1]
b = orb2(d.open.values, d.high.values, d.low.values, d.close.values, d.spr.values, st.astype(np.int64), 30, 120, 1, 3.0, 1.0, 1.0, 2.0, 0.5 * med, 0.25 * med, p, atr, True, False)[1]
print("orb2 reproduces orb_sim:", np.allclose(a, b, equal_nan=True))

rows = []
cfgs = [("XAUUSD", "NY", 30, 120, 1, 3.0, 2.0), ("XAUUSD", "NY", 15, 240, 0, 1.5, 0.0), ("US30", "NY", 15, 120, 0, 1.5, 2.0),
        ("USTEC", "NY", 30, 240, 1, 3.0, 0.0), ("DE30", "FRA", 15, 120, 0, 1.5, 0.0)]
for c in cfgs:
    for name, kw in [("orig", {}), ("no entry-bar stop check", dict(ebc=False)), ("take ambiguous breakout", dict(amb=True)),
                     ("slip=0", dict(slipf=0.0)), ("buf=0", dict(bufk=0.0)), ("all relaxed", dict(ebc=False, amb=True, slipf=0.0))]:
        n, tr, te = go(*c, **kw)
        rows.append(dict(cfg="/".join(map(str, c)), variant=name, n=n, trR=tr, teR=te))
pd.set_option("display.width", 200)
print(pd.DataFrame(rows).round(3).to_string(index=False))
