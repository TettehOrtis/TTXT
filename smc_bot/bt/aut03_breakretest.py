"""AUT03 - break and retest of a horizontal micro support/resistance. Gold M1, Dukascopy.

Governing document: bt/AUTOPSY_BRIEF.md. Hard rules: bt/AGENT_BRIEF.md.
Shared layer: bt/autopsy_lib.py (stage split, ATR, luck bar, score, Tally). Never re-implemented here.

THE QUESTION THIS FILE EXISTS TO ANSWER
---------------------------------------
BT21 concluded "the retest requirement is a cost, not a filter". That conclusion was reached by
comparing an ORB **with** a retest (268-1,084 trades an era) against the same ORB taken immediately,
and it was measured as *direction skill* at a fixed $15/$70 dollar shape through BT20's basket engine.
BT21's micro S/R family (R1/R2) had **no immediate-break control at all**, so the claim was never
actually tested on the setup this agent owns.

AUT03 builds the missing control: the SAME micro S/R levels, the SAME break definition, two arms --
IMMEDIATE (enter on the break bar's close) and RETEST (enter on the rejection candle) -- measured as
win rate / profit factor / net dollars through `common.simulate`, in ATR-denominated shapes, on the
owner's locked stage split. Then every condition in the catalogue is scored on both arms, so the
question becomes: is there a condition under which the retest arm beats the immediate arm?

    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut03_breakretest.py stage0
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut03_breakretest.py explore
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut03_breakretest.py validate
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut03_breakretest.py stress
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut03_breakretest.py report
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from numba import njit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C                                                     # noqa: E402
import autopsy_lib as A                                                # noqa: E402
from smc_core import swings, ema                                       # noqa: E402

GOLD = "XAUUSD"
NAME = "AUT03_breakretest"
OUT = os.path.join(C.RESULTS, NAME)
PARTS = os.path.join(OUT, "parts")
os.makedirs(PARTS, exist_ok=True)

# ---------------------------------------------------------------- costs (see stage 0, finding F-a)
# data_duka/XAUUSD_M1.parquet has `spread` == 0 on all 5,526,570 rows, so `common.simulate` would
# charge NO spread at all on duka gold. We inject it instead of pretending it is zero.
# The duka gold feed reports NO spread at all, so one is injected. SPREAD_BASE is set just below to
# autopsy_lib.gold_spread("XAUUSD") = $0.30, the project's documented conservative stand-in.
# STRESS then re-runs at +50% and +100% on top of it, because a feed swap is impossible here (duka
# and Exness Zero overlap only inside the sealed EXAM).
SPREAD_MID = 0.10          # STRESS: 2x the measured spread
SPREAD_ADVERSE = 0.20      # STRESS: 4x the measured spread, plus 5x the stop slippage
SLIP_BASE = 0.2             # gold-pips on a stop (common.py default)
SLIP_ADVERSE = 1.0          # STRESS: 5x worse stop fills

# ---------------------------------------------------------------- the rule's constants
N_SWING = 5                 # fractal swing, N bars each side, usable once bar i+N has closed (BT21's R1)
BREAK_MAX = 120             # bars a level may wait for its break before it is forgotten
RETEST_MAX = 30             # bars after the break in which the retest must happen (BT21's number)
BUFK = 0.25                 # break buffer = BUFK * ATR60 (mean M1 bar range over the 60 bars ending k-1)
ATR_W = 60
GAP_H = 6                   # a jump bigger than this is a market closure; no pattern may span one
TOUCH_TOL = 0.10            # a "touch" of the level = within TOUCH_TOL * ATR60 of it
MAX_HOLD_MIN = 480          # 8 hours. Measured in stage 0: 96%+ of baseline trades resolve inside it.

SPREAD_BASE = A.gold_spread(GOLD)          # 0.05 price units = $0.50 per 0.10 lot (ERRORS.md #34, #44)
SPREAD_21UTC = A.gold_spread(GOLD, hour=21)   # 0.304 -- that hour is effectively untradeable

TGT, STOP = 1.5, 1.0        # the baseline ATR shape from AUTOPSY_BRIEF (break-even 43.3 on EXPLORE)
LOT = 0.10

NEVER = np.int64(1 << 40)


# ================================================================ bar-level helpers
def segments(b):
    tv = b.time.values.astype("datetime64[ns]").astype(np.int64)
    gap = np.zeros(len(tv), bool)
    gap[1:] = np.diff(tv) > GAP_H * 3600 * 1_000_000_000
    return np.cumsum(gap).astype(np.int64)


def atr_causal(b, w=ATR_W):
    """Mean bar range over the w bars ENDING AT k-1, price units. Value at k uses only closed bars."""
    r = pd.Series(b.high.values - b.low.values)
    return r.rolling(w, min_periods=w).mean().shift(1).values.astype(np.float64)


_BARS = {}
_ORIG_BARS = C.bars


def gbars(sym, tf, spread=None):
    """Memoised bars with a real spread injected (the duka gold spread column is identically 0)."""
    key = (sym, tf, spread)
    if key not in _BARS:
        b = _ORIG_BARS(sym, tf).copy()
        if spread is not None and sym == GOLD:
            b["spr"] = float(spread)
        _BARS[key] = b
    return _BARS[key]


def patch_costs(spread, slip):
    """Make common.simulate see a real gold spread and the chosen stop slippage. common.py untouched."""
    C.bars = lambda sym, tf: gbars(sym, tf, spread)
    C.STOP_SLIP = slip
    _CUR_SPREAD[0] = spread


# ================================================================ the scanner
@njit(cache=True)
def _walls(h, l, is_sh, is_sl, N):
    """For every bar k: the nearest INTACT swing high above and swing low below (catalogue C1/C2).

    "Intact" = confirmed before k and never traded through since it formed, i.e. untouched liquidity.
    Two monotone stacks. A level is popped the moment a bar's high (low) reaches it.
    Levels on each stack are strictly monotone, because forming a new swing high above an older intact
    one would itself have pierced the older one.
    """
    n = len(h)
    wu = np.full(n, np.nan)
    wd = np.full(n, np.nan)
    su = np.empty(n, np.float64)
    sd = np.empty(n, np.float64)
    nu = 0
    nd = 0
    for k in range(n):
        while nu > 0 and su[nu - 1] <= h[k]:
            nu -= 1
        while nd > 0 and sd[nd - 1] >= l[k]:
            nd -= 1
        q = k - N
        if q >= 0:
            if is_sh[q]:
                lv = h[q]
                mx = -1e18
                for j in range(q + 1, k + 1):
                    if h[j] > mx:
                        mx = h[j]
                if lv > mx:
                    su[nu] = lv
                    nu += 1
            if is_sl[q]:
                lv = l[q]
                mn = 1e18
                for j in range(q + 1, k + 1):
                    if l[j] < mn:
                        mn = l[j]
                if lv < mn:
                    sd[nd] = lv
                    nd += 1
        if nu > 0:
            wu[k] = su[nu - 1]
        if nd > 0:
            wd[k] = sd[nd - 1]
    return wu, wd


@njit(cache=True)
def _scan(h, l, c, o, seg, sh_idx, sl_idx, atr, N, bmax, rmax, bufk, tol):
    """Every confirmed M1 swing level -> its break -> its first return.

    One record per level that broke. `rsig` is the retest bar (-1 if no return inside rmax);
    `held` is 1 when that return closed back on the break side (BT21's R1 trigger), 0 when it closed
    through. The break bar is recorded for every broken level, so the IMMEDIATE arm is the same
    population minus the retest requirement -- which is the control BT21 never built.
    """
    n = len(h)
    nsh = len(sh_idx)
    m = nsh + len(sl_idx)
    swg = np.full(m, -1, np.int64)
    brk = np.full(m, -1, np.int64)
    rsig = np.full(m, -1, np.int64)
    held = np.zeros(m, np.int8)
    side = np.zeros(m, np.int8)
    lvl = np.zeros(m, np.float64)
    touch = np.zeros(m, np.int32)
    depth = np.full(m, np.nan)              # how far the retest wick went back THROUGH the level, in atr
    ret_cl = np.full(m, np.nan)             # retest bar close
    k = 0
    for q in range(m):
        if q < nsh:
            i = sh_idx[q]
            up = True
        else:
            i = sl_idx[q - nsh]
            up = False
        lv = h[i] if up else l[i]
        j0 = i + N
        bj = -1
        ntouch = 0
        touching = False
        j = j0
        while j < n and j < j0 + bmax:
            if seg[j] != seg[i]:
                break
            a = atr[j]
            if not (a > 0.0):
                j += 1
                continue
            buf = bufk * a
            if up:
                if c[j] > lv + buf:
                    bj = j
                    break
                near = h[j] >= lv - tol * a
            else:
                if c[j] < lv - buf:
                    bj = j
                    break
                near = l[j] <= lv + tol * a
            if near and not touching:
                ntouch += 1
            touching = near
            j += 1
        if bj < 0:
            continue
        a = atr[bj]
        r = bj + 1
        rr = -1
        hd = 0
        dp = np.nan
        while r < n and r <= bj + rmax:
            if seg[r] != seg[bj]:
                break
            if up:
                if l[r] <= lv:
                    rr = r
                    dp = (lv - l[r]) / a
                    hd = 1 if c[r] > lv else 0
                    break
            else:
                if h[r] >= lv:
                    rr = r
                    dp = (h[r] - lv) / a
                    hd = 1 if c[r] < lv else 0
                    break
            r += 1
        swg[k] = i
        brk[k] = bj
        rsig[k] = rr
        held[k] = hd
        side[k] = 1 if up else -1
        lvl[k] = lv
        touch[k] = ntouch
        depth[k] = dp
        if rr >= 0:
            ret_cl[k] = c[rr]
        k += 1
    return (swg[:k], brk[:k], rsig[:k], held[:k], side[:k], lvl[:k], touch[:k], depth[:k], ret_cl[:k])


def build_levels():
    """One pass over the whole duka gold history. Cached to parts/levels.npz."""
    f = os.path.join(PARTS, "levels.npz")
    if os.path.exists(f):
        z = np.load(f)
        return {k: z[k] for k in z.files}
    b = gbars(GOLD, "M1", SPREAD_BASE)
    h, l, c, o = (b.high.values.astype(np.float64), b.low.values.astype(np.float64),
                  b.close.values.astype(np.float64), b.open.values.astype(np.float64))
    seg = segments(b)
    at = atr_causal(b)
    is_sh, is_sl = swings(h, l, N_SWING)
    t0 = time.time()
    wu, wd = _walls(h, l, is_sh, is_sl, N_SWING)
    print("  walls in %.0fs" % (time.time() - t0))
    t0 = time.time()
    swg, brk, rsig, held, side, lvl, touch, depth, ret_cl = _scan(
        h, l, c, o, seg, np.flatnonzero(is_sh).astype(np.int64), np.flatnonzero(is_sl).astype(np.int64),
        at, N_SWING, BREAK_MAX, RETEST_MAX, BUFK, TOUCH_TOL)
    print("  scan in %.0fs -> %d broken levels" % (time.time() - t0, len(swg)))
    d = dict(swg=swg, brk=brk, rsig=rsig, held=held, side=side, lvl=lvl, touch=touch,
             depth=depth, ret_cl=ret_cl, wu=wu.astype(np.float32), wd=wd.astype(np.float32),
             atr60=at.astype(np.float32))
    np.savez_compressed(f + ".tmp.npz", **d)
    os.replace(f + ".tmp.npz", f)
    return d


# ================================================================ features (all strictly pre-entry)
def htf_frame(tf):
    b = gbars(GOLD, tf, SPREAD_BASE)
    out = pd.DataFrame({"tclose": b.tclose, "close": b.close})
    out["atr"] = A.atr(b, 14).values
    out["ema50"] = ema(b.close.values, 50)
    out["ema200"] = ema(b.close.values, 200)
    return out


def feature_table(L, arm):
    """One row per signal of `arm` in ('immediate', 'retest'). Decision bar = `sig`; fill at sig+1.

    Every column is computed from bars with index <= sig. Nothing reads the fill bar.
    """
    b = gbars(GOLD, "M1", SPREAD_BASE)
    h, l, c, o = b.high.values, b.low.values, b.close.values, b.open.values
    n = len(b)
    flip = 1
    if arm == "immediate":
        m = L["brk"] >= 0
        sig = L["brk"][m]
    elif arm == "retest":
        m = (L["rsig"] >= 0) & (L["held"] == 1)
        sig = L["rsig"][m]
    elif arm == "retest_failed_fade":        # the return closed back THROUGH the level: the break failed
        m = (L["rsig"] >= 0) & (L["held"] == 0)
        sig = L["rsig"][m]
        flip = -1
    elif arm == "immediate_matched":
        # DIAGNOSTIC ONLY, NOT TRADEABLE: the break bars of exactly those levels whose retest later
        # held. It separates the retest's SELECTION effect from its TIMING effect. You cannot know at
        # the break that a retest is coming, so this is never a variant -- only an explanation.
        m = (L["rsig"] >= 0) & (L["held"] == 1)
        sig = L["brk"][m]
    else:
        raise ValueError(arm)
    keep = (sig > 0) & (sig < n - 2)
    m = np.flatnonzero(m)[keep]
    sig = sig[keep]
    # one trade per decision bar: several micro levels can break on the same minute. Keep the one whose
    # swing formed LAST (the closest, most micro level), as BT21's R1 deduplicated on the signal bar.
    o_ = np.lexsort((-L["swg"][m], sig))
    m, sig = m[o_], sig[o_]
    first = np.r_[True, np.diff(sig) > 0]
    n_raw = len(sig)
    m, sig = m[first], sig[first]
    # the fill bar must be the very next CLOCK minute, never a row across a market closure (F1/F3)
    nextmin = (b.time.values[sig + 1] - b.time.values[sig]) == np.timedelta64(1, "m")
    n_gapfill = int((~nextmin).sum())
    m, sig = m[nextmin], sig[nextmin]
    side = flip * L["side"][m].astype(np.int64)
    bdir = L["side"][m].astype(np.int64)                 # the BREAK's direction, which the fade arm opposes
    lvl = L["lvl"][m]
    brk = L["brk"][m]
    swg = L["swg"][m]
    atr60 = L["atr60"][brk].astype(np.float64)

    d = pd.DataFrame(dict(sig=sig, side=side, level=lvl, brk=brk, swing=swg))
    d["t_signal"] = b.tclose.values[sig]
    d["entry_px"] = np.where(side == 1, o[sig + 1] + b.spr.values[sig + 1], o[sig + 1])

    # --- ATR unit for every shape: M15 ATR(14) of the last CLOSED M15 bar (catalogue F4)
    d["atr15"] = A.atr_at(GOLD, "M15", d.t_signal.values, 14)
    at = d.atr15.values

    # --- C1 / C2  room to run and the opposing wall
    wu = L["wu"][sig].astype(np.float64)
    wd = L["wd"][sig].astype(np.float64)
    ahead = np.where(side == 1, wu, wd)
    behind = np.where(side == 1, wd, wu)
    d["f_room_atr"] = np.clip(np.where(np.isnan(ahead), 99.0,
                                       side * (ahead - d.entry_px.values) / at), 0.0, 99.0)
    d["f_back_atr"] = np.clip(np.where(np.isnan(behind), 99.0,
                                       side * (d.entry_px.values - behind) / at), 0.0, 99.0)

    # --- C3 / C6  level quality
    d["f_touches"] = L["touch"][m]
    d["f_level_age"] = (brk - (swg + N_SWING)).astype(float)
    d["f_level_dist_atr"] = np.abs(d.entry_px.values - lvl) / at
    d["f_same_day"] = (b.time.values[swg].astype("datetime64[D]")
                       == b.time.values[sig].astype("datetime64[D]")).astype(float)
    # C4 round number: distance of the LEVEL to the nearest $1 and $10 mark, in gold-pips
    d["f_rn1"] = np.minimum(lvl % 1.0, 1.0 - lvl % 1.0) / 0.1
    d["f_rn10"] = np.minimum(lvl % 10.0, 10.0 - lvl % 10.0) / 0.1

    # --- D  the break bar and the trigger candle
    d["f_brk_disp_atr"] = bdir * (c[brk] - lvl) / at
    rng_b = (h[brk] - l[brk])
    d["f_brk_body"] = np.abs(c[brk] - o[brk]) / np.where(rng_b > 0, rng_b, np.nan)
    d["f_brk_rng_atr"] = rng_b / at
    d["f_bars_retest"] = (sig - brk).astype(float)                 # 0 for the immediate arm (D4)
    # f_depth_atr is the RETEST bar's own wick depth through the level. On the retest arms that bar IS
    # the signal bar, so it is causal. On the immediate/immediate_matched arms the signal bar is the
    # BREAK bar and the retest has not happened yet -- reading it there is look-ahead (catalogue F1),
    # so it is left undefined. The first EXPLORE pass did fill it in, and it read 52.11 right in 100
    # against a 37.32 baseline: the single biggest "edge" in the whole run was the one peeking column.
    if arm in ("retest", "retest_failed_fade"):
        d["f_depth_atr"] = L["depth"][m]
    else:
        d["f_depth_atr"] = np.nan
    rng_s = (h[sig] - l[sig])
    d["f_sig_body_atr"] = np.abs(c[sig] - o[sig]) / at
    d["f_sig_rng_atr"] = rng_s / at
    d["f_sig_body_frac"] = np.abs(c[sig] - o[sig]) / np.where(rng_s > 0, rng_s, np.nan)
    d["f_sig_close_pos"] = np.where(side == 1, (c[sig] - l[sig]), (h[sig] - c[sig])) / np.where(rng_s > 0, rng_s, np.nan)
    prev = np.maximum(sig - 1, 0)
    d["f_engulf"] = ((np.minimum(o[sig], c[sig]) <= np.minimum(o[prev], c[prev]))
                     & (np.maximum(o[sig], c[sig]) >= np.maximum(o[prev], c[prev]))).astype(float)

    # --- A  regime, all via asof_index on CLOSED HTF bars
    for tf, tag in (("M15", "m15"), ("H1", "h1"), ("H4", "h4")):
        f = htf_frame(tf)
        i = C.asof_index(f, d.t_signal.values)
        i = np.clip(i, 0, len(f) - 1)
        cl = f.close.values[i]
        e50 = f.ema50.values[i]
        e200 = f.ema200.values[i]
        aa = f.atr.values[i]
        d["f_%s_trend" % tag] = np.sign(e50 - e200)
        d["f_%s_agree" % tag] = (np.sign(e50 - e200) == side).astype(float)
        d["f_%s_ext_atr" % tag] = np.abs(cl - e50) / np.where(aa > 0, aa, np.nan)
        if tag == "m15":
            d["f_m15_sep_atr"] = np.abs(e50 - e200) / np.where(aa > 0, aa, np.nan)
            # A4 volatility rank of atr15 against its own trailing year (365*96 M15 bars), causal
            s = pd.Series(f.atr.values)
            rk = s.rolling(96 * 250, min_periods=96 * 20).rank(pct=True).values
            d["f_atr_rank"] = rk[i]
            # A5 volatility direction: atr now vs atr 20 M15 bars ago
            prev20 = np.clip(i - 20, 0, len(f) - 1)
            d["f_atr_expand"] = (f.atr.values[i] > f.atr.values[prev20]).astype(float)

    # --- B  clock
    tt = pd.to_datetime(d.t_signal)
    d["hour"] = tt.dt.hour
    d["dow"] = tt.dt.dayofweek
    d["f_ldn"] = tt.dt.hour.between(7, 11).astype(float)
    d["f_ny"] = tt.dt.hour.between(13, 17).astype(float)
    d["f_asia"] = tt.dt.hour.between(0, 6).astype(float)
    d["f_thin"] = (~tt.dt.hour.between(7, 20)).astype(float)

    # --- A6 / C5  where we are inside the day and inside yesterday's range
    day = b.time.values.astype("datetime64[D]")
    dh = pd.Series(h).groupby(day).cummax().values
    dl = pd.Series(l).groupby(day).cummin().values
    g = pd.DataFrame(dict(day=day, h=h, l=l)).groupby("day").agg(hi=("h", "max"), lo=("l", "min"))
    pv = g.shift(1)
    didx = pd.Index(g.index).get_indexer(day[sig])
    pdh, pdl = pv.hi.values[didx], pv.lo.values[didx]
    d["f_day_spent_atr"] = (dh[sig] - dl[sig]) / at
    d["f_pdr_pos"] = np.where(pdh > pdl, (d.entry_px.values - pdl) / (pdh - pdl), np.nan)
    d["f_above_pdh"] = (d.entry_px.values > pdh).astype(float)

    d["arm"] = arm
    d["stage"] = A.stage_of(d.t_signal)
    d.attrs["n_raw"] = n_raw
    d.attrs["n_dropped_same_bar"] = n_raw - len(d) - n_gapfill
    d.attrs["n_dropped_gap_fill"] = n_gapfill
    return d


# ================================================================ a fast replica of common.simulate
@njit(cache=True)
def _sim(o, h, l, c, spr, f_idx, side, sl, tp, hard, slip, comm, pipv):
    """Exactly common.simulate's exit logic, vectorised over trades. Validated to the cent in stage 0.

    SL is checked before TP inside a bar (pessimistic); TP never counts on the fill bar; the stop
    slips; a short's stop/target are read off the ask (high+spr / low+spr).
    """
    n = len(f_idx)
    fill = np.zeros(n)
    exit_px = np.zeros(n)
    k_ex = np.zeros(n, np.int64)
    reason = np.zeros(n, np.int8)                 # 1 TP, -1 SL, 0 TIME/END
    mfe = np.zeros(n)
    mae = np.zeros(n)
    for q in range(n):
        f = f_idx[q]
        d = side[q]
        fl = o[f] + spr[f] if d == 1 else o[f]
        fill[q] = fl
        risk = (fl - sl[q]) * d
        hi = hard[q]
        kk = -1
        px = 0.0
        rs = 0
        for k in range(f, hi):
            if d == 1:
                sh = l[k] <= sl[q]
                th = h[k] >= tp[q]
            else:
                sh = h[k] + spr[k] >= sl[q]
                th = l[k] + spr[k] <= tp[q]
            if k == f:
                th = False
            if sh:
                if d == 1:
                    px = (sl[q] if k == f else min(sl[q], o[k])) - slip
                else:
                    px = (sl[q] if k == f else max(sl[q], o[k] + spr[k])) + slip
                kk = k
                rs = -1
                break
            if th:
                px = tp[q]
                kk = k
                rs = 1
                break
        if kk < 0:
            kk = hi - 1
            px = c[kk] if d == 1 else c[kk] + spr[kk]
            rs = 0
        mx = -1e18                                # max high (bid) / max ask over the life of the trade
        mn = 1e18                                 # min low (bid) / min ask
        for k in range(f, kk + 1):
            if d == 1:
                if h[k] > mx:
                    mx = h[k]
                if l[k] < mn:
                    mn = l[k]
            else:
                if h[k] + spr[k] > mx:
                    mx = h[k] + spr[k]
                if l[k] + spr[k] < mn:
                    mn = l[k] + spr[k]
        if d == 1:
            mfe[q] = (mx - fl) / risk
            mae[q] = (fl - mn) / risk
        else:
            mfe[q] = (fl - mn) / risk
            mae[q] = (mx - fl) / risk
        exit_px[q] = px
        k_ex[q] = kk
        reason[q] = rs
    return fill, exit_px, k_ex, reason, mfe, mae


def hard_end(b, f_idx, max_hold_min, max_days=10):
    """Exclusive end bar for each trade: max_days, the clock-minute hold cap, and the weekend/gap flat."""
    t1 = b.time.values
    n = len(t1)
    dow = b.time.dt.dayofweek.values
    hm = (b.time.dt.hour * 60 + b.time.dt.minute).values
    weekend = (dow == 4) & (hm >= 20 * 60 + 45)
    gap_before = np.zeros(n, bool)
    gap_before[:-1] = np.diff(t1) > np.timedelta64(6, "h")
    wk = np.flatnonzero(weekend | gap_before)
    end = np.minimum(f_idx + max_days * 1440, n)
    end = np.minimum(end, np.searchsorted(t1, t1[f_idx] + np.timedelta64(int(max_hold_min), "m"), "right"))
    j = np.searchsorted(wk, f_idx)
    wfirst = np.where(j < len(wk), wk[np.clip(j, 0, len(wk) - 1)] + 1, n)
    return np.minimum(end, wfirst)


def run_shape(d, tgt, stop, spread=SPREAD_BASE, slip=SLIP_BASE, max_hold=MAX_HOLD_MIN):
    """Add outcome columns for one ATR shape, using the njit replica. Returns a copy of d."""
    b = gbars(GOLD, "M1", spread)
    o, h, l, c, spr = (b.open.values.astype(np.float64), b.high.values.astype(np.float64),
                       b.low.values.astype(np.float64), b.close.values.astype(np.float64),
                       b.spr.values.astype(np.float64))
    d = d.dropna(subset=["atr15"]).copy()
    f_idx = (d.sig.values + 1).astype(np.int64)
    side = d.side.values.astype(np.int64)
    at = d.atr15.values
    px = np.where(side == 1, o[f_idx] + spr[f_idx], o[f_idx])
    sl = px - side * at * stop
    tp = px + side * at * tgt
    hard = hard_end(b, f_idx, max_hold)
    ok = hard > f_idx
    d, f_idx, side, sl, tp, hard, at = (d[ok], f_idx[ok], side[ok], sl[ok], tp[ok], hard[ok], at[ok])
    P = C.pip(GOLD)
    fill, xpx, kex, rs, mfe, mae = _sim(o, h, l, c, spr, f_idx, side, sl, tp, hard,
                                       slip * P, C.COMM[GOLD], C.USD_PIP_01[GOLD])
    d = d.copy()
    d["fill"] = fill
    d["sl"] = sl
    d["tp"] = tp
    d["exit"] = xpx
    d["t_fill"] = b.time.values[f_idx]
    d["t_exit"] = b.time.values[kex]
    d["exit_reason"] = pd.Series(rs).map({1: "TP", -1: "SL", 0: "TIME"}).values
    d["hold_h"] = (b.time.values[kex] - b.time.values[f_idx]) / np.timedelta64(1, "h")
    d["risk_pips"] = (fill - sl) * side / P
    d["gross_pips"] = (xpx - fill) * side / P
    d["net_pips"] = d.gross_pips - C.COMM[GOLD]
    d["R"] = d.net_pips / d.risk_pips
    d["usd_01"] = d.net_pips * C.USD_PIP_01[GOLD] * (LOT / 0.10)
    d["mfe_R"] = mfe
    d["mae_R"] = mae
    d["tgt_mult"] = tgt
    d["stop_mult"] = stop
    d["period"] = C.period_of(d.t_signal)
    d["status"] = "filled"
    return d


# ================================================================ scoring
def pf(t):
    g = t.net_pips
    w, lo = g[g > 0].sum(), -g[g < 0].sum()
    return float(w / lo) if lo > 0 else np.inf


def block_t(t, col="usd_01"):
    """Week-block t statistic on mean dollars per trade. Trades overlap, so weeks are the unit."""
    if len(t) < 20:
        return np.nan
    g = t.groupby(pd.to_datetime(t.t_fill).dt.to_period("W"))[col].mean()
    if len(g) < 5 or g.std(ddof=1) == 0:
        return np.nan
    return float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g))))


def summ(t, label=""):
    if len(t) == 0:
        return dict(label=label, trades=0)
    tt = t.sort_values("t_exit", kind="stable")
    R = tt.R.values
    eq = np.cumsum(R)
    dd = (eq - np.maximum.accumulate(np.maximum(eq, 0))).min()
    weeks = max(1.0, (tt.t_fill.max() - tt.t_fill.min()) / pd.Timedelta(weeks=1))
    return dict(label=label, trades=len(t), per_week=round(len(t) / weeks, 2),
                win_pct=round(100 * float((t.net_pips > 0).mean()), 2),
                PF=round(pf(t), 3), avg_net_pips=round(float(t.net_pips.mean()), 3),
                usd_per_trade=round(float(t.usd_01.mean()), 3),
                usd_total=round(float(t.usd_01.sum()), 0),
                avg_hold_h=round(float(t.hold_h.mean()), 2),
                maxDD_R=round(float(dd), 1), avg_R=round(float(R.mean()), 4),
                tp_pct=round(100 * float((t.exit_reason == "TP").mean()), 1),
                time_pct=round(100 * float((t.exit_reason == "TIME").mean()), 1),
                wk_t=round(block_t(t), 2))


def breakeven_for(t, spread=None):
    """Break-even win rate for this exact set of trades (its own ATR, its own costs, its own spread).
    autopsy_lib.breakeven_rate, applied trade by trade and averaged."""
    sp = SPREAD_BASE if spread is None else spread
    return float(np.mean([A.breakeven_rate(GOLD, a, tg, st, spread=sp)
                          for a, tg, st in zip(t.atr15.values, t.tgt_mult.values, t.stop_mult.values)]))


_LUCK = {}
_CUR_SPREAD = [None]


def luck(stage, hours, n=4000, tgt=TGT, stop=STOP, seed=0):
    key = (stage, tuple(sorted(set(hours))), n, tgt, stop, seed, C.STOP_SLIP,
           id(_BARS.get((GOLD, "M1", SPREAD_BASE))), _CUR_SPREAD[0])
    if key not in _LUCK:
        r = A.luck_bar(GOLD, n, tgt, stop, stage, seed=seed, hours=sorted(set(hours)))
        r = r[r.status == "filled"]
        _LUCK[key] = (round(100 * float((r.net_pips > 0).mean()), 2), len(r),
                      round(float(r.usd_01.mean()), 3), round(pf(r), 3))
    return _LUCK[key]


def four_numbers(t, stage):
    """The four numbers the brief demands beside every hit rate."""
    if len(t) == 0:
        return dict(trades=0)
    lb, ln, lusd, lpf = luck(stage, t.hour.tolist(), tgt=float(t.tgt_mult.iloc[0]),
                             stop=float(t.stop_mult.iloc[0]))
    return dict(trades=len(t), right_in_100=round(100 * float((t.net_pips > 0).mean()), 2),
                luck_bar=lb, beats=round(100 * float((t.net_pips > 0).mean()) - lb, 2),
                usd_per_trade=round(float(t.usd_01.mean()), 3), PF=round(pf(t), 3),
                breakeven=round(breakeven_for(t), 2))


# ================================================================ STAGE 0 — catalogue group F
def stage0():
    print("=" * 100)
    print("STAGE 0 - catalogue group F. The boring killers, checked before anything else.")
    print("=" * 100)
    rep = {}
    b = gbars(GOLD, "M1", SPREAD_BASE)
    raw = _ORIG_BARS(GOLD, "M1")

    # F3 / coverage
    rep["F3_data"] = dict(bars=len(raw), first=str(raw.time.min()), last=str(raw.time.max()))
    for st in ("EXPLORE", "VALIDATE", "STRESS"):
        a, z = A.STAGES[st]
        m = (raw.time >= a) & (raw.time < z)
        gaps = np.diff(raw.time.values[m.values]) / np.timedelta64(1, "m")
        rep["F3_" + st] = dict(bars=int(m.sum()), days=int(pd.Series(raw.time[m].dt.date).nunique()),
                               gaps_gt_6h=int((gaps > 360).sum()), gaps_gt_1h=int((gaps > 60).sum()),
                               pct_contiguous=round(100 * float((gaps == 1).mean()), 2))
    print("\nF3 coverage (duka gold starts 2009-03-15, NOT 2005-01-01):")
    for k, v in rep.items():
        print("   ", k, v)

    # F-a the spread column
    rep["Fa_spread"] = dict(nonzero_rows=int((raw.spr != 0).sum()), rows=len(raw),
                            injected=SPREAD_BASE, comm=C.COMM[GOLD])
    print("\nF-a spread column: %d of %d rows non-zero. Injected spread = %.2f price units."
          % (rep["Fa_spread"]["nonzero_rows"], len(raw), SPREAD_BASE))

    L = build_levels()
    for arm in ("immediate", "retest"):
        d = feature_table(L, arm)
        e = A.clip(d, "EXPLORE")
        # F1 the fill bar
        bad = int((e.sig.values + 1 <= e.sig.values).sum())
        nxt = b.time.values[e.sig.values + 1] - b.time.values[e.sig.values]
        rep["F1_" + arm] = dict(signals=len(e), fill_is_next_row=int(bad == 0),
                               fill_bar_is_next_clock_minute_pct=round(
                                   100 * float((nxt == np.timedelta64(1, "m")).mean()), 3),
                               decision_before_fill_pct=round(100 * float(
                                   (pd.to_datetime(e.t_signal).values <= b.time.values[e.sig.values + 1]).mean()), 2))
        # F4 HTF staleness
        f15 = htf_frame("M15")
        i = C.asof_index(f15, e.t_signal.values)
        rep["F4_" + arm] = dict(min_lag_min=float(((pd.to_datetime(e.t_signal).values
                                                    - f15.tclose.values[i]) / np.timedelta64(1, "m")).min()),
                                negative_lags=int((((pd.to_datetime(e.t_signal).values
                                                     - f15.tclose.values[i]) / np.timedelta64(1, "m")) < 0).sum()))
        # F1 lookahead twin: fill at the signal bar's OWN open
        t_ok = run_shape(e, TGT, STOP)
        tw = e.copy()
        tw["sig"] = tw.sig - 1                           # fill at the signal bar itself
        t_tw = run_shape(tw, TGT, STOP)
        rep["F1twin_" + arm] = dict(
            causal_right_in_100=round(100 * float((t_ok.net_pips > 0).mean()), 2),
            twin_right_in_100=round(100 * float((t_tw.net_pips > 0).mean()), 2),
            delta_points=round(100 * float((t_tw.net_pips > 0).mean()) - 100 * float((t_ok.net_pips > 0).mean()), 2),
            causal_usd=round(float(t_ok.usd_01.mean()), 3), twin_usd=round(float(t_tw.usd_01.mean()), 3),
            delta_usd=round(float(t_tw.usd_01.mean() - t_ok.usd_01.mean()), 3))
        # F5 same-bar ambiguity, measured not assumed
        both = ((t_ok.mae_R >= t_ok.risk_pips * 0 + 1.0) & (t_ok.mfe_R >= t_ok.tgt_mult / t_ok.stop_mult))
        rep["F5_" + arm] = dict(resolved_from_M1_pct=100.0,
                                both_barriers_same_bar_pct=round(100 * float(
                                    ((t_ok.exit_reason == "SL") & (t_ok.mfe_R >= TGT / STOP)).mean()), 3),
                                pessimistic_SL_first=True,
                                tp_on_fill_bar_allowed=False,
                                unresolved_TIME_pct=round(100 * float((t_ok.exit_reason == "TIME").mean()), 2))
        # replica vs common.simulate, to the cent
        smp = e.sample(min(1500, len(e)), random_state=3).sort_values("sig")
        patch_costs(SPREAD_BASE, SLIP_BASE)
        at = smp.atr15.values
        f_idx = smp.sig.values + 1
        pxx = np.where(smp.side.values == 1, b.open.values[f_idx] + b.spr.values[f_idx], b.open.values[f_idx])
        orders = pd.DataFrame(dict(t_signal=pd.to_datetime(smp.t_signal), side=smp.side.values,
                                  entry_type="market", entry=np.nan,
                                  sl=pxx - smp.side.values * at * STOP,
                                  tp=pxx + smp.side.values * at * TGT,
                                  expire=pd.to_datetime(smp.t_signal), be_at_r=np.nan,
                                  max_hold_min=MAX_HOLD_MIN, sigbar=smp.sig.values))
        ref = C.simulate(GOLD, orders, one_at_a_time=False)
        ref = ref[ref.status == "filled"]
        mine = run_shape(smp, TGT, STOP).set_index("sig")
        j = ref.set_index("sigbar").join(mine[["net_pips", "exit_reason", "exit"]], rsuffix="_m", how="inner")
        rmap = {"TP": "TP", "SL": "SL", "TIME": "TIME", "WEEKEND": "TIME", "END": "TIME", "BE": "SL"}
        rep["F_replica_" + arm] = dict(
            n=len(j), reason_agree_pct=round(100 * float(
                (j.exit_reason.map(rmap) == j.exit_reason_m).mean()), 3),
            reason_agree_literal_pct=round(100 * float((j.exit_reason == j.exit_reason_m).mean()), 3),
            max_abs_net_pips_diff=round(float((j.net_pips - j.net_pips_m).abs().max()), 6),
            max_abs_exit_diff=round(float((j.exit - j.exit_m).abs().max()), 8))
        print("\n%s arm:" % arm.upper())
        for k in ("F1_", "F4_", "F1twin_", "F5_", "F_replica_"):
            print("   ", k + arm, rep[k + arm])
    # F-a, continued: how much the spread assumption alone is worth at this shape
    sens = []
    for arm in ("immediate", "retest"):
        d = A.clip(feature_table(L, arm), "EXPLORE")
        for sp in (0.0, SPREAD_BASE, SPREAD_MID, SPREAD_ADVERSE):
            t = run_shape(d, TGT, STOP, spread=sp)
            sens.append(dict(arm=arm, spread=sp, trades=len(t),
                             right_in_100=round(100 * float((t.net_pips > 0).mean()), 2),
                             breakeven=round(breakeven_for(t), 2), PF=round(pf(t), 3),
                             usd_per_trade=round(float(t.usd_01.mean()), 3)))
    rep["Fa_spread_sensitivity"] = sens
    print("\nF-a spread sensitivity at %g ATR target / %g ATR stop:" % (TGT, STOP))
    print(pd.DataFrame(sens).to_string(index=False))
    with open(os.path.join(PARTS, "stage0.json"), "w") as f:
        json.dump(rep, f, indent=1, default=str)
    return rep


# ================================================================ STAGE 1 — EXPLORE
COND = [
    # (id, catalogue, one mechanical sentence, lambda)
    ("c1_room15", "C1", "room to the next untouched level ahead >= 1.5 ATR (the target distance)",
     lambda d: d.f_room_atr >= 1.5),
    ("c1_room30", "C1", "room ahead >= 3.0 ATR", lambda d: d.f_room_atr >= 3.0),
    ("c1_blocked", "C1", "room ahead < 1.0 ATR: the break walks straight into a level",
     lambda d: d.f_room_atr < 1.0),
    ("c1_noceiling", "C1", "no untouched level ahead at all inside the stack (open air)",
     lambda d: d.f_room_atr >= 98.0),
    ("c2_back15", "C2", "the opposing level behind the entry is >= 1.5 ATR away",
     lambda d: d.f_back_atr >= 1.5),
    ("c2_back_lt_stop", "C2", "an opposing level sits inside the stop (< 1.0 ATR behind)",
     lambda d: d.f_back_atr < 1.0),
    ("c1c2_asym", "C1+C2", "more room ahead than behind", lambda d: d.f_room_atr > d.f_back_atr),
    ("d4_fast3", "D4", "the retest arrived within 3 bars of the break", lambda d: d.f_bars_retest <= 3),
    ("d4_fast10", "D4", "the retest arrived within 10 bars", lambda d: d.f_bars_retest <= 10),
    ("d4_slow", "D4", "the retest took more than 10 bars", lambda d: d.f_bars_retest > 10),
    ("d3_shallow10", "D3", "the retest wick went back less than 0.10 ATR through the level",
     lambda d: d.f_depth_atr <= 0.10),
    ("d3_shallow25", "D3", "retest depth <= 0.25 ATR", lambda d: d.f_depth_atr <= 0.25),
    ("d3_deep", "D3", "retest depth > 0.50 ATR", lambda d: d.f_depth_atr > 0.50),
    ("c3_fresh", "C3", "the level was never touched between forming and breaking",
     lambda d: d.f_touches == 0),
    ("c3_tested", "C3", "the level was touched at least twice before it broke", lambda d: d.f_touches >= 2),
    ("c3_age60", "C3", "the level waited more than 60 bars to break", lambda d: d.f_level_age > 60),
    ("c6_sameday", "C6", "the level was formed on the same UTC day as the entry",
     lambda d: d.f_same_day == 1),
    ("c4_rn1", "C4", "the level sits within 1 gold-pip of a whole dollar", lambda d: d.f_rn1 <= 1.0),
    ("c4_rn10", "C4", "the level sits within 10 gold-pips of a $10 mark", lambda d: d.f_rn10 <= 10.0),
    ("d1_disp", "D1", "the break bar closed >= 0.5 ATR beyond the level", lambda d: d.f_brk_disp_atr >= 0.5),
    ("d1_brkbody", "D1", "the break bar's body is >= 60% of its range", lambda d: d.f_brk_body >= 0.6),
    ("d1_brkrng", "D1", "the break bar's range is >= 0.5 ATR", lambda d: d.f_brk_rng_atr >= 0.5),
    ("d2_sigbody", "D2", "the trigger candle's body is >= 0.25 ATR", lambda d: d.f_sig_body_atr >= 0.25),
    ("d2_closepos", "D2", "the trigger closes in the far 70% of its own range",
     lambda d: d.f_sig_close_pos >= 0.7),
    ("d5_engulf", "D5", "the trigger candle engulfs the bar before it", lambda d: d.f_engulf == 1),
    ("a1_m15", "A1", "the M15 50/200 EMA trend agrees with the trade", lambda d: d.f_m15_agree == 1),
    ("a1_h1", "A1", "the H1 50/200 EMA trend agrees", lambda d: d.f_h1_agree == 1),
    ("a1_both", "A1", "M15 and H1 both agree", lambda d: (d.f_m15_agree == 1) & (d.f_h1_agree == 1)),
    ("a1_all3", "A1", "M15, H1 and H4 all agree", lambda d: (d.f_m15_agree == 1) & (d.f_h1_agree == 1) & (d.f_h4_agree == 1)),
    ("a3_sep", "A3", "the M15 EMA50/200 separation is >= 1 ATR (trend, not chop)",
     lambda d: d.f_m15_sep_atr >= 1.0),
    ("a4_volhi", "A4", "ATR is in the top 30% of its own trailing year", lambda d: d.f_atr_rank >= 0.70),
    ("a4_vollo", "A4", "ATR is in the bottom 30% of its own trailing year", lambda d: d.f_atr_rank <= 0.30),
    ("a5_expand", "A5", "ATR is higher than 20 M15 bars ago", lambda d: d.f_atr_expand == 1),
    ("a7_near", "A7", "price is within 1 ATR of the M15 50-EMA", lambda d: d.f_m15_ext_atr <= 1.0),
    ("a6_quietday", "A6", "less than 2 ATR of today's range is spent", lambda d: d.f_day_spent_atr <= 2.0),
    ("c5_pdr", "C5", "entry is outside yesterday's range on the trade's side",
     lambda d: ((d.side == 1) & (d.f_pdr_pos > 1)) | ((d.side == -1) & (d.f_pdr_pos < 0))),
    ("b1_ldn", "B1", "entry falls in the London hours 07-11 UTC", lambda d: d.f_ldn == 1),
    ("b1_ny", "B1", "entry falls in the New York hours 13-17 UTC", lambda d: d.f_ny == 1),
    ("b1_asia", "B1", "entry falls in the Asia hours 00-06 UTC", lambda d: d.f_asia == 1),
    ("b4_thin", "B4", "entry falls outside 07-20 UTC (thin hours)", lambda d: d.f_thin == 1),
]


def explore():
    patch_costs(SPREAD_BASE, SLIP_BASE)
    L = build_levels()
    out = {}
    tabs = {}
    print("=" * 100)
    print("STAGE 1 - EXPLORE  %s -> %s" % A.STAGES["EXPLORE"])
    print("=" * 100)

    # ---- baselines: both arms, same levels, same break, the only difference is the retest
    base = {}
    ARMS = ("immediate", "retest", "immediate_matched", "retest_failed_fade")
    for arm in ARMS:
        d = A.clip(feature_table(L, arm), "EXPLORE")
        t = run_shape(d, TGT, STOP)
        base[arm] = t
        tabs[arm] = d
        print("\n%-20s %s" % (arm, summ(t, arm)))
        print("%-20s %s" % ("", four_numbers(t, "EXPLORE")))
    out["baseline"] = {a: summ(t, a) for a, t in base.items()}
    out["baseline_four"] = {a: four_numbers(t, "EXPLORE") for a, t in base.items()}

    # ---- the decomposition BT21 never did: selection effect vs timing effect
    wi = lambda t: 100 * float((t.net_pips > 0).mean())                      # noqa: E731
    out["decomposition"] = dict(
        all_breaks_win=round(wi(base["immediate"]), 2),
        retested_breaks_at_break_win=round(wi(base["immediate_matched"]), 2),
        retested_breaks_at_retest_win=round(wi(base["retest"]), 2),
        selection_points=round(wi(base["immediate_matched"]) - wi(base["immediate"]), 2),
        timing_points=round(wi(base["retest"]) - wi(base["immediate_matched"]), 2),
        all_breaks_usd=round(float(base["immediate"].usd_01.mean()), 3),
        retested_at_break_usd=round(float(base["immediate_matched"].usd_01.mean()), 3),
        retested_at_retest_usd=round(float(base["retest"].usd_01.mean()), 3),
        selection_usd=round(float(base["immediate_matched"].usd_01.mean()
                                  - base["immediate"].usd_01.mean()), 3),
        timing_usd=round(float(base["retest"].usd_01.mean()
                               - base["immediate_matched"].usd_01.mean()), 3))
    print("\nDECOMPOSITION (selection vs timing):", out["decomposition"])

    # the population arithmetic BT21 reported: how many breaks get a retest that holds
    allb = L["brk"] >= 0
    e_mask = allb & (A.stage_of(gbars(GOLD, "M1", SPREAD_BASE).tclose.values[np.maximum(L["brk"], 0)]) == "EXPLORE")
    out["population"] = dict(
        broken_levels=int(e_mask.sum()),
        returned_inside_30=int((e_mask & (L["rsig"] >= 0)).sum()),
        retest_held=int((e_mask & (L["rsig"] >= 0) & (L["held"] == 1)).sum()),
        pct_returned=round(100 * float((e_mask & (L["rsig"] >= 0)).sum() / max(1, e_mask.sum())), 2),
        pct_held_of_breaks=round(100 * float((e_mask & (L["rsig"] >= 0) & (L["held"] == 1)).sum()
                                             / max(1, e_mask.sum())), 2))
    print("\npopulation:", out["population"])

    # ---- the ATR ladder on both arms (the SHAPE question)
    lad = []
    for tg, st in A.ATR_LADDER:
        for arm in ARMS:
            t = run_shape(tabs[arm], tg, st)
            lad.append(dict(arm=arm, tgt=tg, stop=st, **summ(t, "%s %g/%g" % (arm, tg, st)),
                            breakeven=round(breakeven_for(t), 2)))
    lad = pd.DataFrame(lad)
    lad["beats_be"] = (lad.win_pct - lad.breakeven).round(2)
    lad.to_csv(os.path.join(PARTS, "ladder_EXPLORE.csv"), index=False)
    out["ladder_best"] = lad.sort_values("usd_per_trade", ascending=False).head(12).to_dict("records")
    print("\nladder, top 10 by $/trade:")
    print(lad.sort_values("usd_per_trade", ascending=False)
          .head(10)[["arm", "tgt", "stop", "trades", "win_pct", "breakeven", "beats_be", "PF",
                     "usd_per_trade", "avg_hold_h", "time_pct"]].to_string(index=False))

    # ---- the Tally: every condition, on both arms
    tally = A.Tally(NAME)
    rows = []
    for cid, cat, note, fn in COND:
        for arm in ARMS:
            t = base[arm]
            try:
                m = np.asarray(fn(t).fillna(False), dtype=bool)
            except Exception as ex:
                print("   !! %s %s: %s" % (cid, arm, ex))
                continue
            tab = A.score(t, m)
            if arm == "retest":
                tally.add("%s/%s" % (cat, cid), note, tab)
            sub = t[m]
            rows.append(dict(cond=cid, cat=cat, arm=arm, note=note, n=int(m.sum()),
                             kept_pct=round(100 * float(m.mean()), 1),
                             win_pct=round(100 * float((sub.net_pips > 0).mean()), 2) if len(sub) else np.nan,
                             win_all=round(100 * float((t.net_pips > 0).mean()), 2),
                             lift=round((100 * float((sub.net_pips > 0).mean())
                                         - 100 * float((t.net_pips > 0).mean())), 2) if len(sub) else np.nan,
                             PF=round(pf(sub), 3) if len(sub) else np.nan,
                             usd=round(float(sub.usd_01.mean()), 3) if len(sub) else np.nan,
                             usd_all=round(float(t.usd_01.mean()), 3),
                             wk_t=round(block_t(sub), 2) if len(sub) else np.nan))
    T = pd.DataFrame(rows)
    T.to_csv(os.path.join(PARTS, "tally_EXPLORE.csv"), index=False)
    tally.frame().to_csv(os.path.join(PARTS, "tally_lib_EXPLORE.csv"), index=False)
    out["conditions_tried"] = len(COND)
    print("\nconditions tried: %d (cap 40). Retest arm, sorted by lift:" % len(COND))
    print(T[T.arm == "retest"].sort_values("lift", ascending=False)
          [["cond", "cat", "n", "kept_pct", "win_pct", "lift", "PF", "usd", "wk_t"]].to_string(index=False))
    print("\nIMMEDIATE arm, same conditions, sorted by lift:")
    print(T[T.arm == "immediate"].sort_values("lift", ascending=False)
          [["cond", "cat", "n", "kept_pct", "win_pct", "lift", "PF", "usd", "wk_t"]].to_string(index=False))

    # ---- the head-to-head the whole job turns on: retest minus immediate, inside each condition
    h2h = []
    for cid, cat, note, fn in COND:
        a = T[(T.cond == cid) & (T.arm == "immediate")]
        r = T[(T.cond == cid) & (T.arm == "retest")]
        if len(a) and len(r):
            h2h.append(dict(cond=cid, cat=cat, note=note, n_imm=int(a.n.iloc[0]), n_ret=int(r.n.iloc[0]),
                            win_imm=a.win_pct.iloc[0], win_ret=r.win_pct.iloc[0],
                            win_delta=round(r.win_pct.iloc[0] - a.win_pct.iloc[0], 2),
                            usd_imm=a.usd.iloc[0], usd_ret=r.usd.iloc[0],
                            usd_delta=round(r.usd.iloc[0] - a.usd.iloc[0], 3),
                            pf_imm=a.PF.iloc[0], pf_ret=r.PF.iloc[0]))
    H = pd.DataFrame(h2h).sort_values("usd_delta", ascending=False)
    H.to_csv(os.path.join(PARTS, "h2h_EXPLORE.csv"), index=False)
    print("\nRETEST minus IMMEDIATE inside each condition (positive = the retest helped there):")
    print(H[["cond", "n_imm", "n_ret", "win_imm", "win_ret", "win_delta", "usd_imm", "usd_ret",
             "usd_delta"]].to_string(index=False))
    out["h2h_overall"] = dict(
        win_delta=round(100 * float((base["retest"].net_pips > 0).mean())
                        - 100 * float((base["immediate"].net_pips > 0).mean()), 2),
        usd_delta=round(float(base["retest"].usd_01.mean() - base["immediate"].usd_01.mean()), 3),
        trades_pct=round(100 * (len(base["retest"]) / len(base["immediate"]) - 1), 1),
        pf_imm=round(pf(base["immediate"]), 3), pf_ret=round(pf(base["retest"]), 3))

    for a, t in base.items():
        C.save(NAME, "EXPLORE_base_" + a, t)
    with open(os.path.join(PARTS, "explore.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    return out, base, tabs, T, H, lad


# ---------------------------------------------------------------- the refinements
# Fixed after EXPLORE, 2026-10-07, before VALIDATE was read. Three, the cap. Never re-tuned.
#
# V1 and V2 are a matched pair on purpose: the SAME filter on the retest arm and on the immediate
# arm. Together they answer the assigned question -- if the retest does any work, V1 should beat V2.
# V3 is the only cell in the 576-cell EXPLORE probe that made money with a usable trade count.
REFS = {
    "V1_retest_room": dict(
        arm="retest", filt="room15", tgt=1.5, stop=1.0,
        rule="a micro level breaks, price returns inside 30 bars and closes back on the break side; "
             "take it only if the nearest untouched level ahead is at least 1.5 ATR away"),
    "V2_immediate_room": dict(
        arm="immediate", filt="room15", tgt=1.5, stop=1.0,
        rule="CONTROL: the same levels and the same room filter, entered on the break bar instead of "
             "waiting for a retest"),
    "V3_fade_failed_quiet": dict(
        arm="retest_failed_fade", filt="quietday", tgt=4.0, stop=3.0,
        rule="a micro level breaks, price returns and closes back THROUGH it (the break failed): trade "
             "the other way, but only while less than 2 ATR of the day's range has been spent"),
}
# the four unfiltered arms, carried through every stage so the retest-vs-immediate comparison is
# confirmed out of sample too, not only on EXPLORE
BASES = {"BASE_" + a: dict(arm=a, filt="none", tgt=TGT, stop=STOP, rule="unfiltered baseline")
         for a in ("immediate", "retest", "immediate_matched", "retest_failed_fade")}


def stage_run(stage, spread=SPREAD_BASE, slip=SLIP_BASE, tag=None):
    patch_costs(spread, slip)
    L = build_levels()
    tag = tag or stage
    specs = {**BASES, **REFS}
    tabs = {}
    rows, saves = [], {}
    for nm, s in specs.items():
        if s["arm"] not in tabs:
            tabs[s["arm"]] = A.clip(feature_table(L, s["arm"]), stage)
        d = tabs[s["arm"]]
        m = np.asarray(FNS[s["filt"]](d).fillna(False), dtype=bool)
        t = run_shape(d[m], s["tgt"], s["stop"], spread=spread, slip=slip)
        saves[nm] = t
        # EXPLORE is 1.8 years of a 866 -> 1429 uptrend, so buys and sells are reported apart.
        for sd, tt in (("all", t), ("buy", t[t.side == 1]), ("sell", t[t.side == -1])):
            if len(tt) < 30:
                continue
            row = dict(variant=nm, side=sd, stage=stage, tag=tag, arm=s["arm"], filt=s["filt"],
                       tgt=s["tgt"], stop=s["stop"], spread=spread, slip=slip,
                       **summ(tt, nm), breakeven=round(breakeven_for(tt, spread), 2),
                       **{("x_" + k): v for k, v in four_numbers(tt, stage).items()})
            rows.append(row)
            if sd == "all":
                print("%-22s %s" % (nm, {k: v for k, v in row.items() if k not in ("rule", "label")}))
    df = pd.DataFrame(rows)
    df["rule"] = [specs[n]["rule"] for n in df.variant]
    df.to_csv(os.path.join(PARTS, "variants_%s.csv" % tag), index=False)
    for nm, t in saves.items():
        C.save(NAME, "%s_%s" % (tag, nm), t)
    print("saved parts/variants_%s.csv" % tag)
    return df, saves


FNS = {
    "none": lambda d: pd.Series(True, index=d.index),
    "room15": lambda d: d.f_room_atr >= 1.5,
    "quietday": lambda d: d.f_day_spent_atr <= 2.0,
    "quietday_room15": lambda d: (d.f_day_spent_atr <= 2.0) & (d.f_room_atr >= 1.5),
    "quiet_vollo": lambda d: (d.f_day_spent_atr <= 2.0) & (d.f_atr_rank <= 0.30),
    "asia": lambda d: d.f_asia == 1,
}


def both_ways(d, tgt, stop, spread=None, slip=SLIP_BASE):
    """AUT05's control. Run EVERY signal minute twice -- once all-long, once all-short -- so the
    rule's own direction call can be scored against the coin flip on its own exact minutes.
    Returns (long leg, short leg, summary dict)."""
    spread = SPREAD_BASE if spread is None else spread
    dl = d.copy(); dl["side"] = 1
    ds = d.copy(); ds["side"] = -1
    tl = run_shape(dl, tgt, stop, spread=spread, slip=slip)
    ts = run_shape(ds, tgt, stop, spread=spread, slip=slip)
    tr = run_shape(d, tgt, stop, spread=spread, slip=slip)
    k = min(len(tl), len(ts))
    assert len(tl) == len(ts), (len(tl), len(ts))
    chose_long = np.asarray(d.side.values[:len(tl)] == 1)
    better = tl.usd_01.values > ts.usd_01.values
    tie = tl.usd_01.values == ts.usd_01.values
    picked_better = np.where(tie, 0.5, (chose_long == better).astype(float))
    coin = 0.5 * (tl.usd_01.values + ts.usd_01.values)
    return tl, ts, dict(
        n=int(k), picked_the_better_side_in_100=round(100 * float(picked_better.mean()), 2),
        ties_pct=round(100 * float(tie.mean()), 2),
        rule_usd=round(float(tr.usd_01.mean()), 3), coin_usd=round(float(coin.mean()), 3),
        all_long_usd=round(float(tl.usd_01.mean()), 3), all_short_usd=round(float(ts.usd_01.mean()), 3),
        all_long_win=round(100 * float((tl.net_pips > 0).mean()), 2),
        all_short_win=round(100 * float((ts.net_pips > 0).mean()), 2),
        rule_minus_coin_usd=round(float(tr.usd_01.mean() - coin.mean()), 3))


def control():
    """The both-ways control on every arm, every stage that may be read."""
    patch_costs(SPREAD_BASE, SLIP_BASE)
    L = build_levels()
    rows = []
    for stage in ("EXPLORE", "VALIDATE", "STRESS"):
        for arm in ("immediate", "retest", "retest_failed_fade"):
            d = A.clip(feature_table(L, arm), stage)
            for tg, st in ((TGT, STOP), (3.0, 1.0)):
                _tl, _ts, r = both_ways(d, tg, st)
                rows.append(dict(stage=stage, arm=arm, tgt=tg, stop=st, **r))
                print(rows[-1])
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(PARTS, "control_bothways.csv"), index=False)
    return df


def probe():
    """EXPLORE only. Cross the surviving conditions with the whole ATR ladder, to pick <=3 refinements.
    This is the SHAPE question applied to the FILTER question; no new condition is invented here."""
    patch_costs(SPREAD_BASE, SLIP_BASE)
    L = build_levels()
    tabs = {a: A.clip(feature_table(L, a), "EXPLORE")
            for a in ("immediate", "retest", "retest_failed_fade")}
    rows = []
    for arm, d in tabs.items():
        for fname in ("none", "room15", "quietday", "quietday_room15", "quiet_vollo", "asia"):
            m = np.asarray(FNS[fname](d).fillna(False), dtype=bool)
            if m.sum() < 200:
                continue
            dd = d[m]
            for tg, st in A.ATR_LADDER:
                t = run_shape(dd, tg, st)
                if len(t) < 200:
                    continue
                rows.append(dict(arm=arm, filt=fname, tgt=tg, stop=st,
                                 **summ(t, "%s|%s|%g/%g" % (arm, fname, tg, st)),
                                 breakeven=round(breakeven_for(t), 2)))
    P = pd.DataFrame(rows)
    P["beats_be"] = (P.win_pct - P.breakeven).round(2)
    P.to_csv(os.path.join(PARTS, "probe_EXPLORE.csv"), index=False)
    cols = ["arm", "filt", "tgt", "stop", "trades", "per_week", "win_pct", "breakeven", "PF",
            "usd_per_trade", "avg_hold_h", "time_pct", "wk_t", "maxDD_R"]
    print("cells:", len(P), " PF>1:", int((P.PF > 1).sum()), " $>0:", int((P.usd_per_trade > 0).sum()))
    print("\nTop 25 by $/trade:")
    print(P.sort_values("usd_per_trade", ascending=False).head(25)[cols].to_string(index=False))
    print("\nTop 15 by PF:")
    print(P.sort_values("PF", ascending=False).head(15)[cols].to_string(index=False))
    return P


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stage0"
    if cmd == "stage0":
        stage0()
    elif cmd == "explore":
        explore()
    elif cmd == "probe":
        probe()
    elif cmd == "control":
        control()
    elif cmd == "validate":
        stage_run("VALIDATE")
    elif cmd == "stress":
        stage_run("STRESS")
        stage_run("STRESS", spread=SPREAD_MID, slip=SLIP_ADVERSE, tag="STRESS_mid")
        stage_run("STRESS", spread=SPREAD_ADVERSE, slip=SLIP_ADVERSE, tag="STRESS_adverse")
    elif cmd == "explore_variants":
        stage_run("EXPLORE")
    else:
        raise SystemExit("unknown command %r" % cmd)
