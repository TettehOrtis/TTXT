"""Opus research simulator: bracket + trailing + time exit on M1 BID bars, with switchable assumptions.

amb: ambiguous bar (both SL and TP touched)  0=SL first (lib default), 1=TP first, 2=OHLC path heuristic
     (bar travels O->nearer extreme->other extreme->C).
sprm/spra: effective spread = spr*sprm + spra (units). Bar spread in MT5 data is the MINIMUM spread in the bar.
Decision at close of bar i -> fill at open of bar i+1.
Units: everything in 'units' (pip_size). Returns net units per trade.
"""
import sys
import numpy as np
from numba import njit
sys.path.insert(0, "research")
from lib import load, pip_size  # noqa


@njit(cache=True)
def sim(op, hi, lo, cl, spr, idx, side, tp, sl, maxhold, slip, tp_through, pip,
        amb, sprm, spra, trail_start, trail_dist, entry_lag):
    n = len(idx); nb = len(op)
    pnl = np.full(n, np.nan); held = np.zeros(n, np.int32); why = np.full(n, 9, np.int8)
    for k in range(n):
        e = idx[k] + 1 + entry_lag
        if e >= nb - 1:
            continue
        end = min(e + maxhold[k], nb)
        s = side[k]
        sp_e = spr[e] * sprm + spra
        if s == 1:
            entry = op[e] + (sp_e + slip) * pip
        else:
            entry = op[e] - slip * pip
        tpd = tp[k] * pip; sld = sl[k] * pip
        if s == 1:
            tpp = entry + tpd; slp = entry - sld
        else:
            tpp = entry - tpd; slp = entry + sld
        best = entry
        r = 0; ex = 0.0; j = e
        while j < end:
            spj = (spr[j] * sprm + spra) * pip
            if s == 1:
                hit_sl = lo[j] <= slp
                hit_tp = tp[k] > 0 and hi[j] >= tpp + tp_through * pip
            else:
                hit_sl = hi[j] + spj >= slp
                hit_tp = tp[k] > 0 and lo[j] + spj <= tpp - tp_through * pip
            if hit_sl and hit_tp:
                if amb == 0:
                    hit_tp = False
                elif amb == 1:
                    hit_sl = False
                else:
                    # path heuristic: which extreme is nearer the open is visited first
                    lo_first = (op[j] - lo[j]) < (hi[j] - op[j])
                    if s == 1:
                        if lo_first:
                            hit_tp = False
                        else:
                            hit_sl = False
                    else:
                        if lo_first:
                            hit_sl = False
                        else:
                            hit_tp = False
            if hit_sl:
                if s == 1:
                    ex = min(slp, op[j]) - slip * pip
                else:
                    ex = max(slp, op[j] + spj) + slip * pip
                r = -1; break
            if hit_tp:
                ex = tpp; r = 1; break
            # trailing update at bar end (no intrabar look-ahead)
            if trail_dist > 0:
                if s == 1:
                    if hi[j] > best: best = hi[j]
                    if best - entry >= trail_start * sld:
                        ns = best - trail_dist * sld
                        if ns > slp: slp = ns
                else:
                    if lo[j] + spj < best: best = lo[j] + spj
                    if entry - best >= trail_start * sld:
                        ns = best + trail_dist * sld
                        if ns < slp: slp = ns
            j += 1
        if r == 0:
            j = end - 1
            if s == 1:
                ex = cl[j] - slip * pip
            else:
                ex = cl[j] + (spr[j] * sprm + spra) * pip + slip * pip
        pnl[k] = (ex - entry) / pip if s == 1 else (entry - ex) / pip
        held[k] = j - e + 1; why[k] = r
    return pnl, held, why


def run(d, sym, idx, side, tp, sl, maxhold=30, slip=0.0, tp_through=0.0, amb=0, sprm=1.0, spra=0.0,
        trail_start=0.0, trail_dist=0.0, entry_lag=0):
    idx = np.asarray(idx, dtype=np.int64)
    side = np.broadcast_to(np.asarray(side), idx.shape).astype(np.int8).copy()
    tp = np.broadcast_to(np.asarray(tp, dtype=np.float64), idx.shape).copy()
    sl = np.broadcast_to(np.asarray(sl, dtype=np.float64), idx.shape).copy()
    mh = np.broadcast_to(np.asarray(maxhold, dtype=np.int64), idx.shape).copy()
    return sim(d.open.values, d.high.values, d.low.values, d.close.values, d.spr.values, idx, side, tp, sl,
               mh, float(slip), float(tp_through), pip_size(sym), int(amb), float(sprm), float(spra),
               float(trail_start), float(trail_dist), int(entry_lag))


@njit(cache=True)
def nonoverlap(idx, held):
    keep = np.zeros(len(idx), np.bool_)
    last = -1
    for k in range(len(idx)):
        if idx[k] > last and held[k] > 0:
            keep[k] = True
            last = idx[k] + 1 + held[k]
    return keep
