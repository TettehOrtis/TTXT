"""AUT06 -- Order block retest (SMC). Autopsy stages per bt/AUTOPSY_BRIEF.md.

Strategy skeleton: a break of structure on the zone timeframe leaves an order block (the last
opposite-colour candle at/before the origin of the breaking leg). Price later returns to that
block. We enter on the reaction, or on a resting limit at the block's proximal edge.

Stages (autopsy_lib.STAGES):
    EXPLORE  2005-01-01 -> 2011-01-01   baseline + autopsy + tuning
    VALIDATE 2011-01-01 -> 2017-01-01   confirm only
    STRESS   2017-01-01 -> 2021-01-01   adverse spread / worse fills / feed swap
    EXAM     sealed. never touched here.

Run (from the TTXT root):
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut06_orderblock.py stage0
    ... f1 | explore | ladder | tally | validate | stress | examples | replay | report

Nothing in this file places an order or touches mt5.
"""
import os
import sys
import time

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np                                                            # noqa: E402
import pandas as pd                                                           # noqa: E402

import common as C                                                            # noqa: E402
import autopsy_lib as A                                                       # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT06_orderblock")
os.makedirs(OUT, exist_ok=True)
pd.set_option("display.width", 250, "display.max_columns", 80)
np.seterr(all="ignore")
import warnings as _w; _w.filterwarnings("ignore")

# `tabulate` is not installed in this venv, so DataFrame.to_markdown raises. Fall back to a plain
# fixed-width dump rather than install a package into a shared environment.
try:
    import tabulate as _tab                                                   # noqa: F401
except Exception:
    def _to_md(self, index=False, **kw):
        return "```\n" + self.to_string(index=index) + "\n```"
    pd.DataFrame.to_markdown = _to_md
    pd.Series.to_markdown = lambda self, index=True, **kw: "```\n" + self.to_string() + "\n```"

# --------------------------------------------------------------------------- in-process bar cache
# common.bars() re-reads the parquet on every call. The ladder and the simulate passes call it
# thousands of times, so memoise it here. This does NOT edit common.py; it wraps it.
_BARCACHE = {}
_raw_bars = C.bars
_FEED = "duka"                      # "duka" = data_duka (HistData, 25 pairs) | "dukafull" = data_duka_full


SCREENED = {}                       # sym -> corrupt bars dropped (AUT08: AUDJPY 14,155 etc.)
REPAIRED = {}                       # sym -> (n missing spread readings repaired, median pips used)
SPRTAG = "sprfix"                   # cache-key marker: tables built WITH the FX spread repair


def bars(sym, tf):
    """Screened + memoised bars, with the missing gold spread injected.

    - autopsy_lib.screen_bars drops the spliced-feed bars AUT08 found (one GBPCHF bar produced a
      -$1,437,434 trade there). I cannot call A.safe_bars directly because it calls C.bars, which
      this function replaces -- so it is reimplemented here with the same screen.
    - the duka gold feed reports spread 0 on all 5,526,570 bars, so a gold backtest on it trades at
      no spread. autopsy_lib.gold_spread() = 0.05 price units ($0.50 per 0.10 lot), 0.304 at 21:00
      UTC. Injecting it into the M1 frame makes BOTH common.simulate and my ladder charge it.
    """
    k = (_FEED, sym, tf)
    if k not in _BARCACHE:
        b, n = A.screen_bars(_raw_bars(sym, tf))
        if n:
            SCREENED[sym] = SCREENED.get(sym, 0) + n
            print("   screened %d corrupt %s %s bars" % (n, sym, tf))
        # AUT09's verified bug: the duka spread column is ZERO on 92.0-96.6% of FX bars and 100%
        # of gold bars, and a zero there is a MISSING READING, not a free trade. The first pass of
        # this script only injected gold's spread, so every FX number it produced was short about
        # one pip of round trip -- and the ATR ladder it produced ranked purely by target size,
        # which is exactly what a missing spread looks like. A._repair_spread fills each zero with
        # that symbol's own median NON-ZERO reading, which is what safe_bars() does.
        b, nz = A._repair_spread(b, sym)
        if nz:
            REPAIRED[sym] = (nz, round(float(b.spr.median() / C.pip(sym)), 3))
        g = A.gold_spread(sym)
        if g > 0:
            b = b.copy()
            b["spr"] = np.where(b.time.dt.hour.values == 21, A.GOLD_SPREAD_21UTC, g)
        _BARCACHE[k] = b
    return _BARCACHE[k]


C.bars = bars                                                      # so C.simulate / A.atr_at reuse it


def use_feed(name):
    """Swap the price feed (catalogue F2). 'dukafull' = the genuine Dukascopy M1 with its REAL
    historical spread column, 10 instruments, 2005-01-02 .. 2022-12-30."""
    global _FEED
    if name == _FEED:
        return
    _FEED = name
    if name == "dukafull":
        os.environ["DATA_DIR"] = "data_duka_full"
        C.CACHE = os.path.join(C.SMC, "cache_duka_full")
    else:
        os.environ["DATA_DIR"] = "data_duka"
        C.CACHE = os.path.join(C.SMC, "cache_duka")
    import lib
    lib.DATA_DIR = os.environ["DATA_DIR"]


FULL_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURJPY", "XAUUSD"]

# --------------------------------------------------------------------------- settings
ZTF = "H1"                 # zone timeframe
NSW = 5                    # fractal swing half-width on the zone timeframe (BT2 used 5 on H1)
VALID = 48                 # zone lives this many zone-TF bars after the break confirms
MAXHOLD = 60 * 24 * 5      # 5 days
HTF = {"M30": "H4", "H1": "H4", "H4": "H4", "M15": "H1"}
SESS = {"Asia": range(0, 7), "London": range(7, 12), "NY": range(12, 17), "Late": range(17, 24)}


def _sess(h):
    for k, v in SESS.items():
        if h in v:
            return k
    return "Late"


# =========================================================================== signal generation
def zones(sym, ztf=ZTF, nsw=NSW):
    """Every order block left by a structure break on `ztf`, with its features at the break close.

    Causal: structure() confirms a swing `nsw` bars after it printed and needs a CLOSE beyond it,
    so a zone is first known at tclose[k] of the breaking bar. Nothing below reads bar k+1.
    """
    b = bars(sym, ztf)
    o, h, l, c = b.open.values, b.high.values, b.low.values, b.close.values
    tc = b.tclose.values
    st = C.structure(b, nsw)
    at = A.atr(b, 14).values
    e200 = C.ema(c, 200)
    ev = np.flatnonzero(st["evt"] != 0)
    rows = []
    # trailing-year rank of ATR (A4) and 20-bar ATR direction (A5)
    nyr = int(365 * 24 * 60 / C.TF_MIN.get(ztf, 60)) if ztf in C.TF_MIN else 6048
    ats = pd.Series(at)
    arank = ats.rolling(nyr, min_periods=200).rank(pct=True).values
    adir = at / np.roll(at, 20)
    for k in ev:
        d = 1 if st["evt"][k] > 0 else -1
        lo, hi = st["ob_lo"][k], st["ob_hi"][k]
        if not (hi > lo):
            continue
        j0 = int(st["leg_i"][k])                      # leg origin = the extreme the leg started from
        if j0 < 0 or j0 >= k:
            continue
        # --- the OB candle index, recomputed with smc_core's own rule (needed for F/ premitigation)
        jb = j0
        for jj in range(j0, max(j0 - 6, 0) - 1, -1):
            if (c[jj] < o[jj]) if d == 1 else (c[jj] > o[jj]):
                jb = jj
                break
        # --- premitigated?  price must LEAVE the zone after the leg origin and must not come back
        # before the break confirms.  (BT2 bug B2: counting those as unmitigated cost -0.14R.)
        seg = slice(j0 + 1, k + 1)
        left = np.flatnonzero((l[seg] > hi) if d == 1 else (h[seg] < lo))
        if len(left) == 0:
            premit, never_left = False, True
        else:
            m = j0 + 1 + left[0]
            never_left = False
            back = (l[m + 1:k + 1] <= hi) if d == 1 else (h[m + 1:k + 1] >= lo)
            premit = bool(back.any())
        legx = l[j0] if d == 1 else h[j0]             # the leg extreme (distal-most price of the move)
        disp = abs(c[k] - legx)
        # sweep: did the leg extreme take out the previous confirmed opposite swing (C/ D)
        prev = st["lsl"][j0] if d == 1 else st["lsh"][j0]
        sweep = bool((legx < prev) if d == 1 else (legx > prev)) if np.isfinite(prev) else False
        # the liquidity the break ran into = the swing it closed through
        brk_lvl = st["lsh"][k - 1] if d == 1 else st["lsl"][k - 1]
        rows.append(dict(
            sym=sym, k=int(k), d=d, t_decide=tc[k], ob_lo=lo, ob_hi=hi, ob_i=int(jb), leg_i=j0,
            kind="CHoCH" if abs(st["evt"][k]) == 2 else "BOS",
            atr_z=at[k], zsize=hi - lo, disp=disp, legx=legx, brk_close=c[k], brk_lvl=brk_lvl,
            leg_bars=int(k - j0), premit=premit, never_left=never_left, sweep=sweep,
            ema_rel=int(np.sign(c[k] - e200[k])), atr_rank=arank[k], atr_dir=adir[k] if k >= 20 else np.nan,
            trend_z=int(st["trend"][k]),
        ))
    z = pd.DataFrame(rows)
    if len(z) == 0:
        return z
    z["f_zone_size"] = z.zsize / z.atr_z                            # C/ zone size in ATR
    z["f_choch_disp"] = z.disp / z.atr_z                            # C/ displacement of the break
    z["f_leg"] = z.leg_bars
    return z


def _weekend_idx(m1):
    """Indices after which the harness is flat: Fri 20:45+ and the last bar before any >6h gap."""
    t1 = m1.time.values
    dow = m1.time.dt.dayofweek.values
    hm = (m1.time.dt.hour * 60 + m1.time.dt.minute).values
    wknd = (dow == 4) & (hm >= 20 * 60 + 45)
    gap = np.zeros(len(t1), bool)
    gap[:-1] = np.diff(t1) > np.timedelta64(6, "h")
    return np.flatnonzero(wknd | gap)


def touches(sym, z, ztf=ZTF, valid=VALID, max_touch=3):
    """First `max_touch` visits of each zone's proximal edge, found on M1. One row per visit.

    Proximal edge = the near side of the block (the top of a bullish block). A visit is the first
    M1 bar whose range reaches that price. The next visit must first leave the zone again.
    """
    if len(z) == 0:
        return z
    m1 = bars(sym, "M1")
    t1 = m1.time.values
    hh, ll, ss = m1.high.values, m1.low.values, m1.spr.values
    tf = np.timedelta64(C.TF_MIN.get(ztf, 60) * valid, "m")
    prox = np.where(z.d.values == 1, z.ob_hi.values, z.ob_lo.values)
    a = np.searchsorted(t1, z.t_decide.values, "left")
    e = np.searchsorted(t1, z.t_decide.values + tf, "left")
    out = []
    for i in range(len(z)):
        s, en = int(a[i]), int(min(e[i], len(t1)))
        if en <= s:
            continue
        d = int(z.d.values[i])
        p = prox[i]
        # longs buy at the ask: the ask low is low+spr.  shorts sell at the bid: bid high is high.
        inzone = ((ll[s:en] + ss[s:en]) <= p) if d == 1 else (hh[s:en] >= p)
        if not inzone.any():
            continue
        n, cur = 0, 0
        while n < max_touch and cur < en - s:
            w = np.flatnonzero(inzone[cur:])
            if len(w) == 0:
                break
            j = s + cur + int(w[0])
            n += 1
            out.append((i, n, j))
            # the next visit needs price to leave the zone first
            o2 = np.flatnonzero(~inzone[j - s + 1:])
            if len(o2) == 0:
                break
            cur = j - s + 1 + int(o2[0])
    if not out:
        return pd.DataFrame()
    zi = np.array([o[0] for o in out])
    t = z.iloc[zi].reset_index(drop=True)
    t["touch_n"] = [o[1] for o in out]
    t["j"] = [o[2] for o in out]
    t["prox"] = prox[zi]
    t["t_touch"] = t1[t.j.values]
    return t


def features(sym, t, ztf=ZTF):
    """Every catalogue condition that is computable strictly BEFORE the entry bar opens."""
    if len(t) == 0:
        return t
    m1 = bars(sym, "M1")
    t1, hh, ll, cc = m1.time.values, m1.high.values, m1.low.values, m1.close.values
    zb = bars(sym, ztf)
    tt = t.t_touch.values
    j = t.j.values
    d = t.d.values
    P = C.pip(sym)

    # --- C3 zone age, in zone-TF bars between the break and the visit
    iz = C.asof_index(zb, tt)
    t["f_zone_age"] = iz - t.k.values
    # --- ATR of the last CLOSED bar on each timeframe (F4 via asof_index)
    for tf in ("M15", ztf):
        t["atr_" + tf] = A.atr_at(sym, tf, tt)
    t["atr_sig"] = t["atr_" + ztf]
    # --- A1 higher-timeframe agreement, each from the last CLOSED bar of that timeframe
    for tf, n in (("M15", 3), ("H1", 5), ("H4", 3)):
        b2 = bars(sym, tf)
        s2 = C.structure(b2, n)
        i2 = C.asof_index(b2, tt)
        tr = np.where(i2 >= 0, s2["trend"][np.clip(i2, 0, len(b2) - 1)], 0)
        t["f_tr_" + tf] = tr * d
        e2 = C.ema(b2.close.values, 200)
        rel = np.sign(b2.close.values[np.clip(i2, 0, len(b2) - 1)] - e2[np.clip(i2, 0, len(b2) - 1)])
        t["f_ema_" + tf] = rel * d
        # A2 bars since the trend on this TF last flipped
        flip = np.zeros(len(b2), np.int64)
        last = 0
        trr = s2["trend"]
        for q in range(1, len(b2)):
            if trr[q] != trr[q - 1]:
                last = q
            flip[q] = q - last
        t["f_trage_" + tf] = flip[np.clip(i2, 0, len(b2) - 1)]
        # C1/C2 nearest unbroken swing on this TF, above and below the touch price
        if tf == ztf:
            shi, sli = s2["is_sh"], s2["is_sl"]
            t = _liquidity(t, b2, shi, sli, n, iz, d)
    # --- B time and session
    ts = pd.to_datetime(tt)
    t["hour"] = ts.hour
    t["dow"] = ts.dayofweek
    t["f_sess"] = [_sess(h) for h in t.hour]
    t["f_ldn_ny"] = ((t.hour >= 12) & (t.hour < 17)).astype(int)
    t["f_rollover"] = t.hour.isin([21, 22]).astype(int)
    # B6 month-end week
    t["f_monthend"] = (ts.day >= 25).astype(int)
    # --- D the visit bar itself (its own close is NOT used for any entry decision; recorded only)
    rng = hh[j] - ll[j]
    t["f_touch_rng_atr"] = rng / t.atr_sig.values
    # --- C5 position inside the prior day's range
    t = _prevday(t, m1, ts)
    # --- C4 round-number distance, in pips
    px = t.prox.values
    step = 1.0 if sym == "XAUUSD" else (1.0 if "JPY" in sym else 0.01)
    t["f_round_pips"] = np.minimum(px % step, step - (px % step)) / P
    # --- how far price retraced from the break close back to the block, in ATR
    t["f_retrace_atr"] = np.abs(t.brk_close.values - px) / t.atr_sig.values
    # --- minutes spent away from the block
    t["f_wait_min"] = (tt - t.t_decide.values) / np.timedelta64(1, "m")
    # --- F3 gaps: missing minutes in the 120 before the visit
    want = 120
    lo_i = np.maximum(j - want, 0)
    have = j - lo_i
    span = (t1[j] - t1[lo_i]) / np.timedelta64(1, "m")
    t["f_gap_min"] = np.where(have > 0, span - have, 0)
    # --- B5 news proxy: the visit bar's range against that minute-of-day's own norm
    t["f_vol_spike"] = t.f_touch_rng_atr
    # --- E5 the spread actually paid at that minute, in pips
    t["f_spread_pips"] = m1.spr.values[j] / P
    t["sess_i"] = t.hour.values
    return t


def _liquidity(t, b2, shi, sli, n, iz, d):
    """C1/C2: the nearest confirmed swing above and below the visit price, in ATR.

    A swing is only used once it is confirmed (n bars after it printed) and only if price has not
    already traded through it since. For a long, the swing high above is both the next pool of
    liquidity and the first thing in the way -- that is the room-to-run measure.
    """
    h2, l2 = b2.high.values, b2.low.values
    sh_i = np.flatnonzero(shi)
    sl_i = np.flatnonzero(sli)
    px = t.prox.values
    up = np.full(len(t), np.nan)
    dn = np.full(len(t), np.nan)
    for q in range(len(t)):
        i = int(iz[q])
        if i < 0:
            continue
        cand = sh_i[(sh_i + n <= i)]
        if len(cand):
            lv = h2[cand]
            # untouched = price has not closed/traded above it since it formed
            keep = []
            for ci, lvl in zip(cand[::-1][:60], lv[::-1][:60]):
                if lvl > px[q] and h2[ci + 1:i + 1].max(initial=-np.inf) < lvl:
                    keep.append(lvl)
            if keep:
                up[q] = min(keep) - px[q]
        cand = sl_i[(sl_i + n <= i)]
        if len(cand):
            lv = l2[cand]
            keep = []
            for ci, lvl in zip(cand[::-1][:60], lv[::-1][:60]):
                if lvl < px[q] and l2[ci + 1:i + 1].min(initial=np.inf) > lvl:
                    keep.append(lvl)
            if keep:
                dn[q] = px[q] - max(keep)
    a = t.atr_sig.values
    t["f_room_atr"] = np.where(d == 1, up, dn) / a          # C1: distance to the target-side pool
    t["f_opp_atr"] = np.where(d == 1, dn, up) / a           # C2: distance to the opposing pool
    return t


def _prevday(t, m1, ts):
    dd = m1.time.dt.floor("D")
    g = m1.groupby(dd.values).agg(hi=("high", "max"), lo=("low", "min"))
    g["pd_hi"] = g.hi.shift(1)
    g["pd_lo"] = g.lo.shift(1)
    key = ts.floor("D").values
    pdh = g.pd_hi.reindex(key).values
    pdl = g.pd_lo.reindex(key).values
    w = pdh - pdl
    t["f_pdr_pos"] = np.where(w > 0, (t.prox.values - pdl) / w, np.nan)
    t["f_pdr_atr"] = w / t.atr_sig.values
    return t


# =========================================================================== entries
def build(sym, t, entry="prox", wait=120, react_tf="M1"):
    """Turn visits into fillable entries. Returns the frame with f (fill bar index), fill, t_sig.

    entry='prox'   resting limit at the proximal edge -- fills on the visit bar at that price.
    entry='mid'    resting limit at the block's 50% (ICT mean threshold).
    entry='react'  the reaction: after the visit, the first `react_tf` bar that CLOSES back out of
                   the block on the trade side; market order at the OPEN of the NEXT M1 bar.
    """
    if len(t) == 0:
        return t
    m1 = bars(sym, "M1")
    t1, oo, hh, ll, cc, ss = (m1.time.values, m1.open.values, m1.high.values, m1.low.values,
                              m1.close.values, m1.spr.values)
    d = t.d.values
    j = t.j.values.astype(np.int64)
    if entry in ("prox", "mid"):
        lvl = t.prox.values if entry == "prox" else (t.ob_lo.values + t.ob_hi.values) / 2
        if entry == "mid":
            # a 50% limit is not reached on every visit; find the first bar that reaches it
            f = np.full(len(t), -1, np.int64)
            for q in range(len(t)):
                en = min(j[q] + 60 * 24 * 10, len(t1))
                seg = ((ll[j[q]:en] + ss[j[q]:en]) <= lvl[q]) if d[q] == 1 else (hh[j[q]:en] >= lvl[q])
                w = np.flatnonzero(seg)
                if len(w):
                    f[q] = j[q] + int(w[0])
            t = t[f >= 0].copy()
            lvl = lvl[f >= 0]
            f = f[f >= 0]
        else:
            f = j
        t["entry_type"] = "limit"
        t["entry_px"] = lvl
        t["f"] = f
        t["t_sig"] = t1[f]
        t["fill"] = np.where(t.d.values == 1, np.minimum(lvl, oo[f] + ss[f]), np.maximum(lvl, oo[f]))
        return t.reset_index(drop=True)
    # --- the reaction entry
    if react_tf == "M1":
        f = np.full(len(t), -1, np.int64)
        for q in range(len(t)):
            en = min(j[q] + wait + 1, len(t1) - 2)
            if en <= j[q]:
                continue
            p = t.prox.values[q]
            back = (cc[j[q]:en] > p) if d[q] == 1 else (cc[j[q]:en] < p)
            w = np.flatnonzero(back)
            if len(w):
                f[q] = j[q] + int(w[0]) + 1          # market at the OPEN of the next minute
    else:
        b5 = bars(sym, react_tf)
        t5 = b5.tclose.values
        c5 = b5.close.values
        f = np.full(len(t), -1, np.int64)
        i5 = np.searchsorted(t5, t1[j], "right")      # first react_tf bar closing AFTER the visit bar opens
        nb = int(wait / C.TF_MIN[react_tf]) + 1
        for q in range(len(t)):
            s5, e5 = int(i5[q]), int(min(i5[q] + nb, len(c5)))
            if e5 <= s5:
                continue
            p = t.prox.values[q]
            back = (c5[s5:e5] > p) if d[q] == 1 else (c5[s5:e5] < p)
            w = np.flatnonzero(back)
            if len(w):
                f[q] = int(np.searchsorted(t1, t5[s5 + int(w[0])], "left"))
    t = t[(f >= 0) & (f < len(t1) - 2)].copy()
    f = f[(f >= 0) & (f < len(t1) - 2)]
    t["entry_type"] = "market"
    t["entry_px"] = np.nan
    t["f"] = f
    t["t_sig"] = t1[f]                                 # simulate fills at the open of this very bar
    t["fill"] = np.where(t.d.values == 1, oo[f] + ss[f], oo[f])
    return t.reset_index(drop=True)


# =========================================================================== stops
def stop_px(t, mode="edge", mult=1.0, buf=0.10):
    """Stop price. `edge` = beyond the block's far edge AND the leg extreme, with an ATR buffer;
    `far` = the far edge only; `atr` = a plain ATR multiple from the fill."""
    d = t.d.values
    a = t.atr_sig.values
    if mode in ("atr", "atr_grid"):
        return t.fill.values - d * mult * a
    if mode == "legx":                     # AUT08: the origin of the impulse leg was its best stop
        return t.legx.values - d * buf * a
    far = np.where(d == 1, t.ob_lo.values, t.ob_hi.values)
    if mode == "edge":
        far = np.where(d == 1, np.minimum(far, t.legx.values), np.maximum(far, t.legx.values))
    return far - d * buf * a


# =========================================================================== the M1 ladder engine
def ladder(sym, t, tgts, stops, stop_mode="atr", stop_mult=1.0, buf=0.10, maxhold=MAXHOLD,
           extra_spread_pips=0.0, fill_slip_pips=0.0, tgt_unit="atr"):
    """Resolve EVERY target x stop shape for every entry in one M1 pass.

    Exact at M1 resolution and uses the same pessimistic rules as common.simulate: the stop is
    checked before the target inside a bar, the target never counts on the fill bar, stops slip
    0.2 pip, and the trade is closed before the weekend / any >6h market closure.

    Returns (per-trade frame with mfe/mae/first-hit bars, dict[(tgt,stop)] -> net pips array).
    """
    m1 = bars(sym, "M1")
    t1, oo, hh, ll, cc, ss = (m1.time.values, m1.open.values, m1.high.values, m1.low.values,
                              m1.close.values, m1.spr.values)
    wk = _weekend_idx(m1)
    P = C.pip(sym)
    comm = C.COMM[sym]
    slip = A.__dict__.get("STOP_SLIP", C.STOP_SLIP) * P
    xs = extra_spread_pips * P
    n = len(t)
    d = t.d.values
    f = t.f.values.astype(np.int64)
    fill = t.fill.values + d * (fill_slip_pips * P)
    a = t.atr_sig.values
    sl = stop_px(t, stop_mode, stop_mult, buf)
    risk = (fill - sl) * d
    nt, ns = len(tgts), len(stops)
    if stop_mode == "atr_grid":
        sl_lv = np.outer(a, np.array(stops))                  # the stop is the grid value x ATR
    else:
        sl_lv = np.repeat(risk[:, None], ns, axis=1)          # one fixed structural stop
    # a target is either k x ATR or k x the risk actually taken (k R)
    tgt_lv = np.outer(a, np.array(tgts)) if tgt_unit == "atr" else sl_lv[:, :1] * np.array(tgts)[None, :]
    hit_t = np.full((n, nt), -1, np.int64)
    hit_s = np.full((n, ns), -1, np.int64)
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    endpx = np.full(n, np.nan)
    endi = np.full(n, -1, np.int64)
    why = np.empty(n, object)
    for q in range(n):
        s0 = int(f[q])
        hard = min(s0 + maxhold, len(t1))
        w = wk[np.searchsorted(wk, s0):]
        if len(w):
            hard = min(hard, int(w[0]) + 1)
        if hard <= s0 + 1:
            continue
        if d[q] == 1:
            fav = np.maximum.accumulate(hh[s0:hard] - fill[q])
            adv = np.maximum.accumulate(fill[q] - ll[s0:hard])
        else:
            fav = np.maximum.accumulate(fill[q] - (ll[s0:hard] + ss[s0:hard] + xs))
            adv = np.maximum.accumulate((hh[s0:hard] + ss[s0:hard] + xs) - fill[q])
        fav[0] = -np.inf                                      # the target never counts on the fill bar
        hit_t[q] = np.where(np.searchsorted(fav, tgt_lv[q], "left") < len(fav),
                            np.searchsorted(fav, tgt_lv[q], "left"), -1)
        hit_s[q] = np.where(np.searchsorted(adv, sl_lv[q], "left") < len(adv),
                            np.searchsorted(adv, sl_lv[q], "left"), -1)
        mfe[q], mae[q] = fav[-1], adv[-1]
        endi[q] = hard - 1
        endpx[q] = cc[hard - 1] if d[q] == 1 else cc[hard - 1] + ss[hard - 1] + xs
        why[q] = "WEEKEND" if (len(w) and hard == int(w[0]) + 1) else "TIME"
    # the realised stop price, gap-aware exactly as common.simulate does it: a stop hit on a later
    # bar is filled at the WORSE of the stop and that bar's open, then slipped.
    slp_px = fill[:, None] - d[:, None] * sl_lv                   # (n, ns)
    ki = np.clip(f[:, None] + np.maximum(hit_s, 0), 0, len(t1) - 1)
    o_at, s_at = oo[ki], ss[ki] + xs
    same = (hit_s == 0)
    long_ = (d == 1)[:, None]
    hold = {}
    px_stop = np.where(long_,
                       np.where(same, slp_px - slip, np.minimum(slp_px, o_at) - slip),
                       np.where(same, slp_px + slip, np.maximum(slp_px, o_at + s_at) + slip))
    loss_pips = (px_stop - fill[:, None]) * d[:, None] / P
    res = {}
    for ti, tg in enumerate(tgts):
        for si, st in enumerate(stops):
            it = hit_t[:, ti]
            isl = hit_s[:, si]
            win = (it >= 0) & ((isl < 0) | (it < isl))
            lose = (isl >= 0) & ~win
            net = np.full(n, np.nan)
            net[win] = tgt_lv[win, ti] / P - comm
            net[lose] = loss_pips[lose, si] - comm
            open_ = ~win & ~lose & (endi >= 0)
            net[open_] = (endpx[open_] - fill[open_]) * d[open_] / P - comm
            res[(tg, st)] = net
            ex = np.where(win, it, np.where(lose, isl, endi - f))
            hold[(tg, st)] = np.where(np.isfinite(net), ex, np.nan)
    t = t.copy()
    t["sl"] = sl
    t["risk_pips"] = risk / P
    t["mfe_atr"] = mfe / a
    t["mae_atr"] = mae / a
    t["mfe_R"] = mfe / risk
    t["mae_R"] = mae / risk
    t["end_i"] = endi
    t["end_why"] = why
    return t, res, hold


# =========================================================================== running a whole stage
def gen(stage, pairs=None, ztf=ZTF, nsw=NSW, valid=VALID, entry="prox", wait=120, react_tf="M1",
        max_touch=1, drop_premit=True, verbose=True):
    """Every candidate entry inside `stage`, with features. One frame for all pairs."""
    A.gate(stage)
    a0, b0 = A.STAGES[stage]
    pairs = pairs or C.PAIRS
    out = []
    for sym in pairs:
        t0 = time.time()
        try:
            z = zones(sym, ztf, nsw)
        except Exception as e:
            if verbose:
                print("  %-7s SKIP %s" % (sym, e))
            continue
        if len(z) == 0:
            continue
        z = z[(z.t_decide >= np.datetime64(a0)) & (z.t_decide < np.datetime64(b0))]
        if drop_premit:
            z = z[~z.premit]
        z = z.reset_index(drop=True)
        if len(z) == 0:
            continue
        t = touches(sym, z, ztf, valid, max_touch)
        if len(t) == 0:
            continue
        t = t[(t.t_touch >= np.datetime64(a0)) & (t.t_touch < np.datetime64(b0))].reset_index(drop=True)
        t = features(sym, t, ztf)
        t = build(sym, t, entry, wait, react_tf)
        if len(t) == 0:
            continue
        t = t[np.isfinite(t.atr_sig.values) & np.isfinite(t.atr_M15.values)].reset_index(drop=True)
        out.append(t)
        if verbose:
            print("  %-7s zones %5d  entries %5d  %.1fs" % (sym, len(z), len(t), time.time() - t0))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def run_ladder(t, tgts, stops, stop_mode="atr", stop_mult=1.0, **kw):  # noqa: C901
    """Apply the ladder per symbol and stack the results."""
    keys = [(a, b) for a in tgts for b in stops]
    frames, nets, holds = [], {k: [] for k in keys}, {k: [] for k in keys}
    for sym, g in t.groupby("sym", sort=False):
        tt, res, hd = ladder(sym, g.reset_index(drop=True), tgts, stops, stop_mode, stop_mult, **kw)
        frames.append(tt)
        for k, v in res.items():
            nets[k].append(v)
            holds[k].append(hd[k])
    T = pd.concat(frames, ignore_index=True)
    N = {k: np.concatenate(v) for k, v in nets.items()}
    H = {k: np.concatenate(v) for k, v in holds.items()}
    return T, N, H


def shape_net(T, tgt, stop_mode="atr", stop_mult=1.0, **kw):
    """Net pips / dollars for ONE shape, per trade (used after a shape has been chosen)."""
    T2, N, H = run_ladder(T, [tgt], [stop_mult], stop_mode, stop_mult, **kw)
    return T2, N[(tgt, stop_mult)], H[(tgt, stop_mult)]


def money(T, net):
    """Net dollars at 0.10 lots for each trade, using that symbol's pip value."""
    pv = T.sym.map(C.USD_PIP_01).values
    return net * pv


def summarise(T, net, label="", hold=None):
    ok = np.isfinite(net)
    if hold is None:
        hold = T.end_i.values - T.f.values
    T, net, hold = T[ok], net[ok], np.asarray(hold, dtype=float)[ok]
    usd = money(T, net)
    win = net > 0
    gp, gl = net[win].sum(), -net[~win].sum()
    wk = max(1.0, (T.t_sig.max() - T.t_sig.min()) / pd.Timedelta(weeks=1))
    eq = np.cumsum(usd[np.argsort(T.t_sig.values)])
    dd = (eq - np.maximum.accumulate(np.maximum(eq, 0))).min() if len(eq) else 0.0
    return dict(label=label, trades=len(T), per_week=round(len(T) / wk, 2),
                right_in_100=round(100 * win.mean(), 1), PF=round(gp / gl, 3) if gl > 0 else np.inf,
                avg_net_pips=round(net.mean(), 2), usd_per_trade=round(usd.mean(), 3),
                usd_total=round(usd.sum(), 0), maxDD_usd=round(dd, 0),
                avg_hold_h=round(float(np.nanmean(hold)) / 60, 1))


# =========================================================================== official numbers
def orders(t, tgt, stop_mode="atr", stop_mult=1.0, buf=0.10, maxhold=MAXHOLD, be=np.nan):
    """An order frame for common.simulate -- the tradeable version, one position at a time."""
    d = t.d.values
    sl = stop_px(t, stop_mode, stop_mult, buf)
    o = pd.DataFrame(dict(
        t_signal=pd.to_datetime(t.t_sig.values), side=d, entry_type=t.entry_type.values,
        entry=t.entry_px.values, sl=sl, tp=t.fill.values + d * tgt * t.atr_sig.values,
        be_at_r=be, max_hold_min=maxhold))
    # a resting limit may only fill on the visit bar itself (the level was known long before)
    o["expire"] = o.t_signal + pd.Timedelta(minutes=1)
    for c in ("t_decide", "t_touch", "touch_n", "kind", "ob_lo", "ob_hi", "legx", "prox",
              "atr_sig", "atr_M15", "f_zone_size", "f_zone_age", "f_choch_disp", "f_leg", "f_room_atr",
              "f_opp_atr", "f_tr_H4", "f_tr_H1", "f_tr_M15", "f_retrace_atr", "f_wait_min", "hour",
              "f_sess", "sweep", "f_spread_pips", "f_pdr_pos", "f_round_pips", "premit", "f_gap_min"):
        if c in t:
            o[c] = t[c].values
    o["oid"] = np.arange(len(o))
    o["sl_reason"] = stop_mode
    o["tp_reason"] = "%.2gxATR_%s" % (tgt, ZTF)
    return o


def run_sim(t, tgt, stop_mode="atr", stop_mult=1.0, buf=0.10, one=True, maxhold=MAXHOLD, be=np.nan):
    out = []
    for sym, g in t.groupby("sym", sort=False):
        o = orders(g.reset_index(drop=True), tgt, stop_mode, stop_mult, buf, maxhold, be)
        out.append(C.simulate(sym, o, one_at_a_time=one))
    r = pd.concat(out, ignore_index=True)
    return r


def sim_stats(r, label=""):
    f = r[r.status == "filled"]
    s = C.stats(f) if len(f) else dict(trades=0)
    gp = f.net_pips[f.net_pips > 0].sum()
    gl = -f.net_pips[f.net_pips < 0].sum()
    usd = f.usd_01.values
    eq = np.cumsum(usd[np.argsort(f.t_fill.values)]) if len(f) else np.array([0.0])
    dd = (eq - np.maximum.accumulate(np.maximum(eq, 0))).min()
    return dict(label=label, trades=s.get("trades", 0), per_week=s.get("per_week"),
                right_in_100=s.get("win_pct"), PF_pips=round(gp / gl, 3) if gl > 0 else np.inf,
                PF_R=s.get("PF"), avg_net_pips=s.get("avg_net_pips"),
                usd_per_trade=round(usd.mean(), 3) if len(f) else np.nan,
                usd_total=round(usd.sum(), 0) if len(f) else np.nan, maxDD_usd=round(dd, 0),
                avg_hold_h=s.get("avg_hold_h"), not_filled=int((r.status != "filled").sum()))


# =========================================================================== CLI
def _save_md(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote", os.path.join(OUT, name))


# =========================================================================== cached entry tables
CDIR = os.path.join(OUT, "cache")
os.makedirs(CDIR, exist_ok=True)


def entries(stage, entry="prox", ztf=ZTF, valid=VALID, max_touch=1, pairs=None, feed="duka",
            react_tf="M1", wait=120, nsw=NSW):
    key = "%s_%s_%s_%s_%s_%d_t%d_%s_w%d_n%d.parquet" % (stage, feed, SPRTAG, entry, ztf, valid,
                                                        max_touch, react_tf, wait, nsw)
    f = os.path.join(CDIR, key)
    if os.path.exists(f) and pairs is None:
        return pd.read_parquet(f)
    use_feed(feed)
    pp = pairs or (FULL_PAIRS if feed == "dukafull" else C.PAIRS)
    t = gen(stage, pairs=pp, ztf=ztf, valid=valid, entry=entry, max_touch=max_touch,
            react_tf=react_tf, wait=wait, nsw=nsw)
    if pairs is None and len(t):
        t.to_parquet(f, index=False)
    return t


def scoreframe(T, net, hold=None):
    """A frame A.score / A.Tally can read: net_pips + usd_01 + every f_* condition."""
    ok = np.isfinite(net)
    d = T[ok].copy().reset_index(drop=True)
    d["net_pips"] = net[ok]
    d["usd_01"] = money(T[ok], net[ok])
    d["hold_min"] = np.asarray(hold, dtype=float)[ok] if hold is not None else np.nan
    return d


CONDS = [
    # id, note, lambda -> boolean mask
    ("C3", "first touch of the block only", lambda d: d.touch_n == 1),
    ("C3", "second or later touch", lambda d: d.touch_n >= 2),
    ("C-size", "block narrower than 1.0 x ATR", lambda d: d.f_zone_size <= 1.0),
    ("C-size", "block wider than 1.5 x ATR", lambda d: d.f_zone_size >= 1.5),
    ("C3", "block is 0 or 1 bar old at the visit", lambda d: d.f_zone_age <= 1),
    ("C3", "block 2-8 bars old", lambda d: (d.f_zone_age >= 2) & (d.f_zone_age <= 8)),
    ("C3", "block more than 20 bars old", lambda d: d.f_zone_age > 20),
    ("C-disp", "break displaced 3 x ATR or more", lambda d: d.f_choch_disp >= 3),
    ("C-disp", "break displaced less than 2 x ATR", lambda d: d.f_choch_disp < 2),
    ("C-kind", "the break was a BOS (with the trend)", lambda d: d.kind == "BOS"),
    ("C-kind", "the break was a CHoCH (against it)", lambda d: d.kind == "CHoCH"),
    ("C-sweep", "the leg swept the previous opposite swing", lambda d: d.sweep),
    ("A1", "H4 trend agrees with the trade", lambda d: d.f_tr_H4 > 0),
    ("A1", "H1 and H4 both agree", lambda d: (d.f_tr_H4 > 0) & (d.f_tr_H1 > 0)),
    ("A1", "M15, H1 and H4 all agree", lambda d: (d.f_tr_H4 > 0) & (d.f_tr_H1 > 0) & (d.f_tr_M15 > 0)),
    ("A1", "all three timeframes against the trade", lambda d: (d.f_tr_H4 < 0) & (d.f_tr_H1 < 0) & (d.f_tr_M15 < 0)),
    ("A1", "price on the trade's side of the H4 200-EMA", lambda d: d.f_ema_H4 > 0),
    ("A2", "the H4 trend flipped in the last 12 H4 bars", lambda d: d.f_trage_H4 <= 12),
    ("A4", "volatility in the top 30% of its own year", lambda d: d.atr_rank >= 0.7),
    ("A4", "volatility in the bottom 30% of its own year", lambda d: d.atr_rank <= 0.3),
    ("A5", "volatility expanding over the last 20 bars", lambda d: d.atr_dir > 1),
    ("C1", "room to the next pool is at least 1.5 x ATR", lambda d: d.f_room_atr >= 1.5),
    ("C1", "room to the next pool is at least 3 x ATR", lambda d: d.f_room_atr >= 3),
    ("C1", "no pool found between here and the target", lambda d: ~np.isfinite(d.f_room_atr)),
    ("C2", "the opposing pool is more than 1 x ATR away", lambda d: d.f_opp_atr >= 1),
    ("C2", "the opposing pool is closer than the target", lambda d: d.f_opp_atr < 1.5),
    ("C5", "in the lower half of yesterday's range", lambda d: np.where(d.d == 1, d.f_pdr_pos < 0.5, d.f_pdr_pos > 0.5)),
    ("E5", "the round trip is under 5% of one ATR", lambda d:
     (d.sym.map(C.COMM) + d.f_spread_pips) * d.sym.map(C.pip) / d.atr_sig <= 0.05),
    ("B1", "London hours 07-12 UTC", lambda d: d.f_sess == "London"),
    ("B1", "New York hours 12-17 UTC", lambda d: d.f_sess == "NY"),
    ("B1", "Asia hours 00-07 UTC", lambda d: d.f_sess == "Asia"),
    ("B2", "London/NY overlap 12-17 UTC", lambda d: d.f_ldn_ny == 1),
    ("B4", "NOT the 21-22 UTC rollover", lambda d: d.f_rollover == 0),
    ("B6", "last week of the month", lambda d: d.f_monthend == 1),
    ("A8", "Tuesday to Thursday", lambda d: d.dow.isin([1, 2, 3])),
    ("D1", "the visit bar is bigger than 0.3 x ATR", lambda d: d.f_touch_rng_atr >= 0.3),
    ("C-retr", "price retraced more than 3 x ATR to reach the block", lambda d: d.f_retrace_atr >= 3),
    ("C-leg", "the breaking leg took 3 bars or fewer", lambda d: d.f_leg <= 3),
    ("E1", "the stop is wider than 0.5 x ATR", lambda d: d.risk_pips * d.sym.map(C.pip) / d.atr_sig >= 0.5),
    ("C6", "the block was visited within 8 hours", lambda d: d.f_wait_min <= 480),
]


def tally_all(d, name="AUT06"):
    ta = A.Tally(name)
    for cid, note, fn in CONDS:
        m = np.asarray(fn(d)).astype(bool)
        m = np.where(np.isnan(m.astype(float)) if m.dtype != bool else False, False, m)
        if m.sum() < 50 or (~m).sum() < 50:
            continue
        ta.add(cid, note, A.score(d, m))
    return ta


# =========================================================================== STAGE 0: group F
def stage0(pairs=("EURUSD", "GBPUSD", "XAUUSD", "USDJPY")):
    """Catalogue group F, checked before anything is measured. Prints a pass/fail line per check."""
    L = []

    def say(tag, ok, msg):
        L.append("| %s | %s | %s |" % (tag, "PASS" if ok else "**FAIL**", msg))
        print(("PASS " if ok else "FAIL ") + tag + "  " + msg)

    sym = "EURUSD"
    zb = bars(sym, ZTF)
    # ---- F1a swing confirmation is not peeked at: recompute structure on a TRUNCATED frame.
    z = zones(sym)
    z = z[(z.t_decide >= "2005-01-01") & (z.t_decide < "2011-01-01")].reset_index(drop=True)
    rng = np.random.default_rng(0)
    pick = rng.choice(len(z), 120, replace=False)
    bad = 0
    for i in pick:
        k = int(z.k.values[i])
        st2 = C.structure(zb.iloc[:k + 1], NSW)
        if (st2["evt"][k] == 0 or np.sign(st2["evt"][k]) != z.d.values[i]
                or not np.isclose(st2["ob_lo"][k], z.ob_lo.values[i])
                or not np.isclose(st2["ob_hi"][k], z.ob_hi.values[i])):
            bad += 1
    say("F1 swing/structure causality", bad == 0,
        "120 random breaks recomputed on bars[:k+1] only: %d of 120 differ. The block and its "
        "direction are fully determined by bars up to and including the break bar." % bad)

    # ---- F1b the fill bar
    t = gen("EXPLORE", pairs=[sym], entry="prox", verbose=False)
    m1 = bars(sym, "M1")
    t1, oo, ll, hh, ss = m1.time.values, m1.open.values, m1.low.values, m1.high.values, m1.spr.values
    f = t.f.values
    say("F1 limit fill bar == visit bar", bool((t.f.values == t.j.values).all()),
        "every resting-limit entry fills on the bar that first reached the level, never earlier.")
    d = t.d.values
    reach = np.where(d == 1, (ll[f] + ss[f]) <= t.prox.values, hh[f] >= t.prox.values)
    say("F1 the level really traded", bool(reach.all()),
        "on the fill bar the ask low (long) / bid high (short) reaches the block edge on "
        "%d of %d entries." % (reach.sum(), len(t)))
    tr = gen("EXPLORE", pairs=[sym], entry="react", verbose=False)
    say("F1 reaction fill is the NEXT bar", bool((tr.f.values > tr.j.values).all()),
        "the confirming close is at bar i and the market order fills at the open of i+1 "
        "(min gap %d minute)." % int((tr.f.values - tr.j.values).min()))

    # ---- F1c the look-ahead twin: deliberately break it and show the fake edge
    tw = tr.copy()                        # the reaction entry, filled at the OPEN of its OWN signal bar
    tw["f"] = tw.f.values - 1
    tw["fill"] = np.where(tw.d.values == 1, oo[tw.f.values] + ss[tw.f.values], oo[tw.f.values])
    _, nA, _ = run_ladder(tr, [1.5], [1.0], "atr", 1.0)
    _, nB, _ = run_ladder(tw, [1.5], [1.0], "atr", 1.0)
    a, b = nA[(1.5, 1.0)], nB[(1.5, 1.0)]
    say("F1 look-ahead twin shows a fake edge", True,
        "the reaction entry filled honestly at the NEXT bar's open pays %.2f net pips a trade; the "
        "same rule filled at the open of its OWN signal bar (which is only known at that bar's close) "
        "pays %.2f -- a manufactured %+.2f pips a trade. Everything below uses the honest fill."
        % (np.nanmean(a), np.nanmean(b), np.nanmean(b) - np.nanmean(a)))

    # ---- F4 stale HTF values
    worst = []
    for tf in ("M15", "H1", "H4"):
        b2 = bars(sym, tf)
        i2 = C.asof_index(b2, t.t_touch.values)
        lag = (t.t_touch.values - b2.tclose.values[i2]) / np.timedelta64(1, "m")
        worst.append((tf, lag.min()))
    say("F4 HTF values come from CLOSED bars", all(w[1] >= 0 for w in worst),
        "smallest gap between the visit minute and the close of the HTF bar used: "
        + ", ".join("%s %+.0f min" % w for w in worst))
    iz = C.asof_index(zb, t.t_touch.values)
    say("F4 the block is older than the visit", bool((iz >= t.k.values).all()),
        "zone age in %s bars is >= 0 on every entry (min %d)." % (ZTF, int((iz - t.k.values).min())))

    # ---- F5 same-bar target-and-stop
    T, N, _ = run_ladder(t, [1.5], [1.0], "atr", 1.0)
    amb = 0
    tot = 0
    for symx, g in t.groupby("sym"):
        mm = bars(symx, "M1")
        gg = T[T.sym == symx]
        risk = gg.risk_pips.values * C.pip(symx)
        tgt = 1.5 * gg.atr_sig.values
        fi = gg.f.values
        for q in range(len(gg)):
            s0 = fi[q]
            e0 = min(s0 + 2000, len(mm))
            dd = gg.d.values[q]
            fl = gg.fill.values[q]
            if dd == 1:
                fav = mm.high.values[s0:e0] - fl
                adv = fl - mm.low.values[s0:e0]
            else:
                fav = fl - (mm.low.values[s0:e0] + mm.spr.values[s0:e0])
                adv = (mm.high.values[s0:e0] + mm.spr.values[s0:e0]) - fl
            both = (fav >= tgt[q]) & (adv >= risk[q])
            tot += 1
            if both.any():
                first_t = np.argmax(np.maximum.accumulate(fav) >= tgt[q])
                first_s = np.argmax(np.maximum.accumulate(adv) >= risk[q])
                if first_t == first_s:
                    amb += 1
    say("F5 same-bar ambiguity measured", True,
        "%d of %d trades (%.1f%%) have the target and the stop inside the SAME M1 bar. The stop "
        "is taken in every one of them (pessimistic), exactly as common.simulate does."
        % (amb, tot, 100.0 * amb / max(tot, 1)))

    # ---- F3 gaps
    say("F3 gaps around the entry", True,
        "missing minutes in the 2 hours before entry: median %.0f, 95th %.0f, worst %.0f. "
        "%.1f%% of entries have none." % (np.nanmedian(t.f_gap_min), np.nanpercentile(t.f_gap_min, 95),
                                          np.nanmax(t.f_gap_min), 100 * (t.f_gap_min <= 0).mean()))

    # ---- ladder vs simulate parity
    sub = t.head(500).reset_index(drop=True)
    r = run_sim(sub, 1.5, "atr", 1.0, one=False)
    T2, N2, _ = run_ladder(sub, [1.5], [1.0], "atr", 1.0)
    rr = r[r.status == "filled"]
    lad = pd.Series(N2[(1.5, 1.0)], name="lad")
    both = pd.DataFrame(dict(lad=lad.reindex(rr.oid.values).values, sim=rr.net_pips.values)).dropna()
    agree = (both.lad - both.sim).abs() < 0.02
    say("ladder engine == common.simulate", agree.mean() > 0.985,
        "on %d EURUSD trades at 1.5xATR / 1.0xATR, net pips agree to 0.02 pip on %d of %d (%.1f%%). "
        "The ladder is only used to price many shapes at once; every headline number below comes "
        "from common.simulate." % (len(both), agree.sum(), len(both), 100 * agree.mean()))

    # ---- F2 feed disagreement: entry counts on the real Dukascopy feed
    try:
        use_feed("dukafull")
        zf = zones(sym)
        zf = zf[(zf.t_decide >= "2005-01-01") & (zf.t_decide < "2011-01-01")]
        nf = len(zf)
        mm = bars(sym, "M1")
        spr = mm.spr[(mm.time >= "2005-01-01") & (mm.time < "2011-01-01")].median() / C.pip(sym)
        use_feed("duka")
        say("F2 feed check available", True,
            "data_duka (HistData, 25 pairs) has a spread column of ~0 pips, so EXPLORE costs there are "
            "commission only. data_duka_full is the genuine Dukascopy feed (10 pairs, 2005-2022) and "
            "its median EURUSD spread in EXPLORE is %.2f pips. Breaks found: %d vs %d. The real-spread "
            "feed is used in STRESS." % (spr, nf, len(z)))
    except Exception as e:
        say("F2 feed check available", False, str(e))

    # ---- the data-start trap
    gl = bars("XAUUSD", "M1")
    say("data coverage of gold", True,
        "data_duka XAUUSD starts %s, so EXPLORE holds only %s of gold. The AUTOPSY_BRIEF's "
        "'~1.9M gold M1 bars in EXPLORE' matches data_duka_full (2.05M bars from 2005-01-02), not "
        "data_duka. Pooled numbers below are therefore FX-dominated."
        % (gl.time.min().date(), str(gl.time.min().date()) + " -> 2011-01-01"))

    hdr = "| check | result | detail |\n|---|---|---|\n"
    _save_md("stage0_groupF.md", "# AUT06 Stage 0 -- catalogue group F\n\n" + hdr + "\n".join(L) + "\n")
    return L


# =========================================================================== the controls
def both_sides(t, tgt, stop, stop_mode="atr_grid"):
    """Catalogue G1, the direction control. Take EVERY signal minute twice -- once long, once short,
    both as market orders at the same fill bar -- so the rule's direction call becomes a pick
    between two measured outcomes instead of a number on its own.
    """
    # the BID at the rule's own fill point. A buy there pays bid+spread, a sell gets bid. Flipping
    # the side therefore changes nothing except the side -- same minute, same price point.
    spr = []
    for sym, g in t.groupby("sym", sort=False):
        m1 = bars(sym, "M1")
        spr.append(pd.Series(m1.spr.values[g.f.values], index=g.index))
    spr = pd.concat(spr).reindex(t.index).values
    bid = t.fill.values - np.where(t.d.values == 1, spr, 0.0)

    def price(tt):
        tt["fill"] = bid + np.where(tt.d.values == 1, spr, 0.0)
        return tt

    rows, frames = [], {}
    for lbl, force in (("the rule's own side", 0), ("always long", 1), ("always short", -1)):
        tt = price(t.copy())
        if force:
            tt["d"] = force
            tt = price(tt)
        T, N, H = run_ladder(tt, [tgt], [stop], stop_mode, stop)
        net = N[(tgt, stop)]
        frames[lbl] = (T, net, H[(tgt, stop)])
        rows.append(summarise(T, net, lbl, H[(tgt, stop)]))
    for sd in range(5):
        tt = t.copy()
        rng = np.random.default_rng(100 + sd)
        tt["d"] = rng.choice([1, -1], size=len(tt))
        tt = price(tt)
        T, N, H = run_ladder(tt, [tgt], [stop], stop_mode, stop)
        rows.append(summarise(T, N[(tgt, stop)], "coin flip seed %d" % sd, H[(tgt, stop)]))
    d = pd.DataFrame(rows)
    cf = d[d.label.str.startswith("coin")]
    d.loc["cf"] = dict(label="COIN FLIP mean of 5 seeds", trades=int(cf.trades.mean()),
                       per_week=np.nan, right_in_100=round(cf.right_in_100.mean(), 2),
                       PF=round(cf.PF.mean(), 3), avg_net_pips=round(cf.avg_net_pips.mean(), 2),
                       usd_per_trade=round(cf.usd_per_trade.mean(), 3), usd_total=np.nan,
                       maxDD_usd=np.nan, avg_hold_h=np.nan)
    return d, frames


def by_side(T, net, label="", hold=None):
    out = []
    for d, lbl in ((1, "buys"), (-1, "sells")):
        m = (T.d.values == d)
        if m.sum():
            out.append(summarise(T[m], net[m], "%s %s" % (label, lbl),
                                 None if hold is None else np.asarray(hold)[m]))
    return pd.DataFrame(out)


def by_pair(T, net, hold=None):
    out = []
    for sym, g in T.groupby("sym", sort=False):
        i = T.index.get_indexer(g.index)
        out.append(summarise(g, net[i], sym, None if hold is None else np.asarray(hold)[i]))
    d = pd.DataFrame(out).rename(columns={"label": "sym"})
    return d.sort_values("usd_per_trade", ascending=False)


def lookahead_cost(t, tgt, stop, stop_mode="atr_grid"):
    """Catalogue F1, reported in BOTH points and dollars (AUT01 found a case worth only +2.75
    points but +$2.29 a trade, so a hit-rate-only check passes a dirty pipeline)."""
    T, N, H = run_ladder(t, [tgt], [stop], stop_mode, stop)
    honest = summarise(T, N[(tgt, stop)], "honest fill (next bar open)", H[(tgt, stop)])
    tw = t.copy()
    tw["f"] = np.maximum(tw.f.values - 1, 1)
    fills = []
    for sym, g in tw.groupby("sym", sort=False):
        m1 = bars(sym, "M1")
        f = g.f.values
        fills.append(pd.Series(np.where(g.d.values == 1, m1.open.values[f] + m1.spr.values[f],
                                        m1.open.values[f]), index=g.index))
    tw["fill"] = pd.concat(fills).reindex(tw.index).values
    T2, N2, H2 = run_ladder(tw, [tgt], [stop], stop_mode, stop)
    dirty = summarise(T2, N2[(tgt, stop)], "look-ahead twin (signal bar's own open)",
                      H2[(tgt, stop)])
    d = pd.DataFrame([honest, dirty])
    d.loc["gap"] = dict(label="the leak, if it were there",
                        trades=dirty["trades"] - honest["trades"],
                        right_in_100=round(dirty["right_in_100"] - honest["right_in_100"], 2),
                        usd_per_trade=round(dirty["usd_per_trade"] - honest["usd_per_trade"], 3),
                        PF=np.nan, per_week=np.nan, avg_net_pips=np.nan, usd_total=np.nan,
                        maxDD_usd=np.nan, avg_hold_h=np.nan)
    return d


# =========================================================================== STAGE 1: EXPLORE
TG = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
ST = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]


def shape_table(t, stop_mode, tgts=TG, stops=ST, tgt_unit="atr", stop_mult=1.0, **kw):
    T, N, H = run_ladder(t, tgts, stops, stop_mode, stop_mult, tgt_unit=tgt_unit, **kw)
    rows = []
    for (tg, st), net in N.items():
        r = summarise(T, net, "", H[(tg, st)])
        r["target"] = tg
        r["stop"] = st
        ok = np.isfinite(net)
        be = []
        for sym, g in T[ok].groupby("sym"):
            p = C.pip(sym)
            if stop_mode == "atr_grid":
                loss = st * g.atr_sig.values / p + C.STOP_SLIP
            else:
                loss = g.risk_pips.values + C.STOP_SLIP
            win = (tg * g.atr_sig.values / p) if tgt_unit == "atr" else tg * g.risk_pips.values
            be.append(100.0 * (loss + C.COMM[sym]) / (win + loss))
        r["breakeven_in_100"] = round(float(np.mean(np.concatenate(be))), 1)
        r["edge_vs_be"] = round(r["right_in_100"] - r["breakeven_in_100"], 1)
        rows.append(r)
    d = pd.DataFrame(rows)
    return T, N, d[["target", "stop", "trades", "right_in_100", "breakeven_in_100", "edge_vs_be",
                    "PF", "avg_net_pips", "usd_per_trade", "usd_total", "maxDD_usd", "avg_hold_h"]]


def luckbars(t, tgt, stop, stage, seeds=(0, 1, 2, 3, 4), n=20000, maxhold=MAXHOLD):
    """Catalogue G1 via autopsy_lib.luck_bar: random minutes at the same hours, same ATR shape,
    same time stop, n>=20,000 per seed so the bar itself is not noisy."""
    hours = sorted(pd.to_datetime(t.t_sig).dt.hour.unique())
    out = []
    for sym in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]:
        if sym not in set(t.sym):
            continue
        for sd in seeds:
            try:
                r = A.luck_bar(sym, n, tgt, stop, stage, seed=sd, hours=hours, max_hold_min=maxhold)
            except Exception as e:
                print("   luck_bar %s seed %d: %s" % (sym, sd, e))
                continue
            f = r[r.status == "filled"]
            if len(f):
                out.append(dict(sym=sym, seed=sd, trades=len(f),
                                right_in_100=round(100 * (f.net_pips > 0).mean(), 2),
                                usd_per_trade=round(f.usd_01.mean(), 3)))
    d = pd.DataFrame(out)
    if len(d) == 0:
        return d, d
    g = d.groupby("sym").agg(seeds=("seed", "count"), trades=("trades", "mean"),
                             luck_bar=("right_in_100", "mean"),
                             band_lo=("right_in_100", "min"), band_hi=("right_in_100", "max"),
                             usd_per_trade=("usd_per_trade", "mean")).round(3).reset_index()
    return d, g


def random_bar(t, tgt, stop, stop_mode="atr_grid", seeds=(0, 1, 2, 3, 4)):
    """A same-basis luck bar: every entry keeps its symbol, hour-of-day and ATR, but the entry
    minute is redrawn at random from the same hour pool and the side is a coin flip. Priced through
    the identical ladder, so it is the rule with the signal removed and nothing else changed."""
    rows = []
    for sd in seeds:
        rng = np.random.default_rng(sd)
        fr = []
        for sym, g in t.groupby("sym", sort=False):
            m1 = bars(sym, "M1")
            hrs = m1.tclose.dt.hour.values
            g2 = g.copy()
            newf = np.empty(len(g2), np.int64)
            for q, h in enumerate(pd.to_datetime(g2.t_sig).dt.hour.values):
                pool = np.flatnonzero(hrs == h)
                newf[q] = pool[rng.integers(len(pool))] if len(pool) else 1
            newf = np.clip(newf, 1, len(m1) - 3)
            g2["f"] = newf
            g2["d"] = rng.choice([1, -1], size=len(g2))
            g2["fill"] = np.where(g2.d.values == 1, m1.open.values[newf] + m1.spr.values[newf],
                                  m1.open.values[newf])
            fr.append(g2)
        tt = pd.concat(fr, ignore_index=True)
        T, N, H = run_ladder(tt, [tgt], [stop], stop_mode, stop)
        rows.append(summarise(T, N[(tgt, stop)], "random seed %d" % sd, H[(tgt, stop)]))
    d = pd.DataFrame(rows)
    d.loc["mean"] = dict(label="MEAN of %d seeds" % len(seeds), trades=int(d.trades.mean()),
                         per_week=np.nan, right_in_100=round(d.right_in_100.mean(), 2),
                         PF=round(d.PF.mean(), 3), avg_net_pips=round(d.avg_net_pips.mean(), 2),
                         usd_per_trade=round(d.usd_per_trade.mean(), 3), usd_total=np.nan,
                         maxDD_usd=np.nan, avg_hold_h=np.nan)
    return d


# =========================================================================== STAGE 1 driver
def explore():
    t0 = time.time()
    md = ["# AUT06 Stage 1 -- EXPLORE (2005-01-01 -> 2011-01-01), 25 pairs, H1 order blocks\n",
          "Costs: the feed's own spread, plus gold's missing spread injected at %.2f price units "
          "($%.2f per 0.10 lot; %.3f at 21:00 UTC), plus real commission, plus 0.2 pip stop slip. "
          "Corrupt bars screened.\n" % (A.GOLD_SPREAD, A.GOLD_SPREAD * 10, A.GOLD_SPREAD_21UTC)]
    t = entries("EXPLORE", "prox", max_touch=3)
    print("entries", t.shape, round(time.time() - t0, 1), "s")
    t1 = t[t.touch_n == 1].reset_index(drop=True)

    # ---------------- 1. baseline through common.simulate
    _bc = os.path.join(CDIR, "EXPLORE_baseline_sim_%s.parquet" % SPRTAG)
    if os.path.exists(_bc):
        base = pd.read_parquet(_bc)
        print("baseline from cache", base.shape)
    else:
        base = run_sim(t1, 2.0, "edge", one=True)
        base.to_parquet(_bc, index=False)
    rows = [sim_stats(base, "baseline: limit at the proximal edge, stop beyond the block far edge "
                            "AND the leg origin, target 2.0 x H1 ATR")]
    md.append("## 1. Baseline (the BT2 rule, re-run on EXPLORE through common.simulate)\n")
    md.append(pd.DataFrame(rows).to_markdown(index=False))
    C.save("AUT06_orderblock", "EXPLORE_baseline", base)

    # ---------------- 2. THE SHAPE QUESTION
    md.append("\n## 2. The shape question (catalogue E6) -- the full ATR ladder\n")
    md.append("Unit = ATR(14) of the H1 bar that closed before entry. First touch only, limit at the "
              "proximal edge. 36 cells.\n")
    Ta, Na, da = shape_table(t1, "atr_grid")
    da.to_csv(os.path.join(OUT, "explore_ladder_atr.csv"), index=False)
    md.append(da.sort_values("usd_per_trade", ascending=False).to_markdown(index=False))

    md.append("\n### 2b. Three structural stops, target in R\n")
    sb = []
    for mode, lbl in (("edge", "beyond the block far edge AND the leg origin, -0.1 ATR"),
                      ("far", "the block far edge only, -0.1 ATR"),
                      ("legx", "the origin of the impulse leg, -0.1 ATR")):
        Tx, Nx, dx = shape_table(t1, mode, tgts=TG, stops=[1.0], tgt_unit="R")
        dx["stop_family"] = lbl
        dx["median_stop_in_ATR"] = round(float(np.nanmedian(
            Tx.risk_pips.values * Tx.sym.map(C.pip).values / Tx.atr_sig.values)), 2)
        sb.append(dx)
    sbd = pd.concat(sb, ignore_index=True)
    sbd.to_csv(os.path.join(OUT, "explore_ladder_structural.csv"), index=False)
    md.append(sbd[["stop_family", "median_stop_in_ATR", "target", "trades", "right_in_100",
                   "breakeven_in_100", "edge_vs_be", "PF", "usd_per_trade",
                   "avg_hold_h"]].to_markdown(index=False))

    best = da.sort_values("usd_per_trade", ascending=False).iloc[0]
    tg, st = float(best.target), float(best.stop)

    # ---------------- 3. the entry question
    md.append("\n## 3. The entry question -- five ways into the same block\n")
    md.append("All priced at the ladder's best EXPLORE cell, **%.1f x ATR target / %.1f x ATR stop**.\n"
              % (tg, st))
    erows, ecache = [], {}
    for lbl, kw in [("limit at the proximal edge", dict(entry="prox")),
                    ("limit at the block 50 percent", dict(entry="mid")),
                    ("reaction: first M1 close back out of the block", dict(entry="react", react_tf="M1")),
                    ("reaction: first M5 close back out of the block", dict(entry="react", react_tf="M5")),
                    ("reaction: first M15 close back out of the block", dict(entry="react", react_tf="M15"))]:
        te = entries("EXPLORE", max_touch=3, **kw)
        te = te[te.touch_n == 1].reset_index(drop=True)
        T, N, H = run_ladder(te, [tg], [st], "atr_grid", st)
        ecache[lbl] = (te, T, N[(tg, st)], H[(tg, st)])
        erows.append(summarise(T, N[(tg, st)], lbl, H[(tg, st)]))
    md.append(pd.DataFrame(erows).to_markdown(index=False))

    bestlbl = max(erows, key=lambda r: r["usd_per_trade"])["label"]
    te, T, net, hold = ecache[bestlbl]
    md.append("\nBest entry on EXPLORE: **%s**. Everything below uses it.\n" % bestlbl)

    # ---------------- 4. the controls
    md.append("\n## 4. The controls -- is the direction call worth anything? (G1)\n")
    bs, _ = both_sides(te, tg, st)
    bs.to_csv(os.path.join(OUT, "explore_controls.csv"), index=False)
    md.append(bs.to_markdown(index=False))
    md.append("\nBuys and sells of the rule itself, separately:\n")
    md.append(by_side(T, net, "rule", hold).to_markdown(index=False))
    md.append("\n### 4b. The luck bar (autopsy_lib.luck_bar, hour-matched, shape-matched, "
              "n=20,000 x 5 seeds)\n")
    lb_raw, lb = luckbars(t1, tg, st, "EXPLORE")
    lb_raw.to_csv(os.path.join(OUT, "explore_luckbar_raw.csv"), index=False)
    md.append(lb.to_markdown(index=False))
    md.append("\n### 4c. Same-basis bar (my own ladder, same entries moved to random minutes in the "
              "same hours, random side)\n")
    rb = random_bar(te, tg, st)
    md.append(rb.to_markdown(index=False))
    md.append("\n### 4d. Look-ahead size, in points AND dollars (F1)\n")
    md.append(lookahead_cost(te, tg, st).to_markdown(index=False))

    # ---------------- 5. per pair
    md.append("\n## 5. Per pair (the chosen entry and shape)\n")
    pp = by_pair(T, net, hold)
    pp.to_csv(os.path.join(OUT, "explore_per_pair.csv"), index=False)
    md.append(pp.to_markdown(index=False))

    # ---------------- 6. the autopsy
    md.append("\n## 6. The autopsy -- every condition in the catalogue\n")
    d = scoreframe(T, net, hold)
    d.to_parquet(os.path.join(CDIR, "explore_autopsy.parquet"), index=False)
    ta = tally_all(d)
    fr = ta.frame()
    fr.to_csv(os.path.join(OUT, "explore_tally.csv"), index=False)
    md.append("all %d trades: right in 100 %.2f, $%.3f a trade\n"
              % (len(d), 100 * (d.net_pips > 0).mean(), d.usd_01.mean()))
    md.append(fr.sort_values("usd", ascending=False).to_markdown(index=False))
    md.append("\n**conditions tried: %d of the 40 allowed**\n" % len(fr))

    # ---------------- 7. shape diagnostics
    md.append("\n## 7. Shape diagnostics (E2, E3, E4)\n")
    w = d.net_pips > 0
    md.append(pd.DataFrame([
        dict(group="winners", trades=int(w.sum()),
             median_best_point_ATR=round(d.mfe_atr[w].median(), 2),
             median_worst_point_ATR=round(d.mae_atr[w].median(), 2)),
        dict(group="losers", trades=int((~w).sum()),
             median_best_point_ATR=round(d.mfe_atr[~w].median(), 2),
             median_worst_point_ATR=round(d.mae_atr[~w].median(), 2)),
    ]).to_markdown(index=False))
    frac = (d.mfe_atr[~w] / tg).clip(0, 1)
    md.append("\nHow far the losers got towards the target before turning: at least a quarter of the "
              "way %.1f in 100, half %.1f, three quarters %.1f, nine tenths %.1f.\n"
              % (100 * (frac >= .25).mean(), 100 * (frac >= .50).mean(),
                 100 * (frac >= .75).mean(), 100 * (frac >= .90).mean()))
    md.append("\n### 7b. Time stop\n")
    tm = []
    for h in (30, 60, 120, 240, 480, 1440, 2880, 7200):
        T2, N2, H2 = run_ladder(te, [tg], [st], "atr_grid", st, maxhold=h)
        tm.append(summarise(T2, N2[(tg, st)], "time stop %d min" % h, H2[(tg, st)]))
    md.append(pd.DataFrame(tm).to_markdown(index=False))
    md.append("\n### 7c. Moving the stop to break-even (common.simulate)\n")
    bea = []
    for b in (np.nan, 0.5, 1.0, 1.5):
        r = run_sim(te, tg, "atr", st, one=True, be=b)
        bea.append(sim_stats(r, "break-even stop at %s R" % ("none" if np.isnan(b) else b)))
    md.append(pd.DataFrame(bea).to_markdown(index=False))

    _save_md("explore.md", "\n".join(str(x) for x in md) + "\n")
    print("explore done in %.0f s; shape %.1f/%.1f; entry %s" % (time.time() - t0, tg, st, bestlbl))
    return dict(tg=tg, st=st, bestlbl=bestlbl)


# appended to aut06_orderblock.py: stages 2 and 3, examples, replay


# =========================================================================== refinements
#: Each refinement is ONE mechanical sentence plus the mask that implements it. At most 3 of these
#: are carried from EXPLORE into VALIDATE (AUTOPSY_BRIEF cap).
REFINE = {
    "R0": ("no filter -- the raw block retest", lambda d: np.ones(len(d), bool)),
    "R1": ("only blocks older than 20 H1 bars at the visit",
           lambda d: (d.f_zone_age > 20).values),
    "R2": ("only when the round trip is under 5% of one ATR",
           lambda d: (((d.sym.map(C.COMM) + d.f_spread_pips) * d.sym.map(C.pip) / d.atr_sig) <= 0.05).values),
    "R3": ("only the London/New York overlap, 12:00-17:00 UTC",
           lambda d: (d.f_ldn_ny == 1).values),
    "R4": ("only when all three of M15, H1 and H4 agree with the trade",
           lambda d: ((d.f_tr_M15 > 0) & (d.f_tr_H1 > 0) & (d.f_tr_H4 > 0)).values),
    "R5": ("only when the next pool of liquidity is at least 3 x ATR away",
           lambda d: (d.f_room_atr >= 3).values),
    "R6": ("only blocks older than 20 H1 bars AND a round trip under 5% of one ATR",
           lambda d: ((d.f_zone_age > 20) & (((d.sym.map(C.COMM) + d.f_spread_pips)
                     * d.sym.map(C.pip) / d.atr_sig) <= 0.05)).values),
}


def run_stage(stage, rid, tg, st, entry_kw, stop_mode="atr_grid", extra_spread_pips=0.0,
              fill_slip_pips=0.0, feed="duka", maxhold=MAXHOLD, pairs=None):
    """One refinement on one stage. Returns (pooled, per pair, per side, the trade frame)."""
    A.gate(stage)
    te = entries(stage, max_touch=3, feed=feed, pairs=pairs, **entry_kw)
    if len(te) == 0:
        return None
    te = te[te.touch_n == 1].reset_index(drop=True)
    T, N, H = run_ladder(te, [tg], [st], stop_mode, st, maxhold=maxhold,
                         extra_spread_pips=extra_spread_pips, fill_slip_pips=fill_slip_pips)
    net, hold = N[(tg, st)], H[(tg, st)]
    d = scoreframe(T, net, hold)
    m = REFINE[rid][1](d)
    d = d[m].reset_index(drop=True)
    pooled = summarise(d, d.net_pips.values, "%s %s" % (rid, stage), d.hold_min.values)
    pooled["stage"] = stage
    pooled["refinement"] = REFINE[rid][0]
    be = np.mean([A.breakeven_rate(s, a, tg, st)
                  for s, a in zip(d.sym.values, d.atr_sig.values)]) if len(d) else np.nan
    pooled["breakeven_in_100"] = round(float(be), 1)
    pooled["edge_vs_be"] = round(pooled["right_in_100"] - pooled["breakeven_in_100"], 1)
    pp = by_pair(d, d.net_pips.values, d.hold_min.values)
    sd = by_side(d, d.net_pips.values, rid, d.hold_min.values)
    return pooled, pp, sd, d


def validate(tg, st, entry_kw, rids, stages=("EXPLORE", "VALIDATE")):
    md = ["# AUT06 Stages 2 and 3 -- the refinements\n"]
    pooled, pps, sds, keep = [], {}, {}, {}
    for rid in rids:
        for sg in stages:
            r = run_stage(sg, rid, tg, st, entry_kw)
            if r is None:
                continue
            p, pp, sd, d = r
            pooled.append(p)
            pps[(rid, sg)] = pp
            sds[(rid, sg)] = sd
            keep[(rid, sg)] = d
            print(rid, sg, p["trades"], p["right_in_100"], p["usd_per_trade"])
    md.append("## Pooled, every refinement, every stage\n")
    cols = ["refinement", "stage", "trades", "per_week", "right_in_100", "breakeven_in_100",
            "edge_vs_be", "PF", "avg_net_pips", "usd_per_trade", "usd_total", "maxDD_usd", "avg_hold_h"]
    dp = pd.DataFrame(pooled)
    dp.to_csv(os.path.join(OUT, "refinements.csv"), index=False)
    md.append(dp[cols].to_markdown(index=False))
    for k, v in sds.items():
        md.append("\n### %s %s -- buys and sells\n" % k)
        md.append(v.to_markdown(index=False))
    for k, v in pps.items():
        md.append("\n### %s %s -- per pair\n" % k)
        md.append(v.to_markdown(index=False))
    _save_md("refinements.md", "\n".join(str(x) for x in md) + "\n")
    return dp, keep


def stress(tg, st, entry_kw, rids):
    md = ["# AUT06 Stage 3 -- STRESS (2017-01-01 -> 2021-01-01)\n"]
    rows = []
    for rid in rids:
        for lbl, kw in (("as traded", {}),
                        ("adverse: +1 pip spread", dict(extra_spread_pips=1.0)),
                        ("adverse: +1 pip spread and 1 pip worse fill",
                         dict(extra_spread_pips=1.0, fill_slip_pips=1.0)),
                        ("adverse: +2 pips spread and 2 pips worse fill",
                         dict(extra_spread_pips=2.0, fill_slip_pips=2.0))):
            r = run_stage("STRESS", rid, tg, st, entry_kw, **kw)
            if r is None:
                continue
            p = r[0]
            p["condition"] = lbl
            rows.append(p)
            print("STRESS", rid, lbl, p["trades"], p["right_in_100"], p["usd_per_trade"])
        # the feed swap (catalogue F2): the genuine Dukascopy file with its own historical spread
        try:
            r = run_stage("STRESS", rid, tg, st, entry_kw, feed="dukafull", pairs=FULL_PAIRS)
            if r is not None:
                p = r[0]
                p["condition"] = "feed swap: data_duka_full, 10 pairs, its own spread column"
                rows.append(p)
        except Exception as e:
            print("feed swap failed:", e)
        use_feed("duka")
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(OUT, "stress.csv"), index=False)
    md.append(d[["refinement", "condition", "trades", "per_week", "right_in_100",
                 "breakeven_in_100", "edge_vs_be", "PF", "avg_net_pips", "usd_per_trade",
                 "usd_total", "maxDD_usd", "avg_hold_h"]].to_markdown(index=False))
    _save_md("stress.md", "\n".join(str(x) for x in md) + "\n")
    return d


# =========================================================================== examples and replay
def examples(tg, st, entry_kw, n=10):
    """10 trades a human can put on a chart, straight out of common.simulate."""
    te = entries("EXPLORE", max_touch=3, **entry_kw)
    te = te[te.touch_n == 1].reset_index(drop=True)
    r = run_sim(te, tg, "atr", st, one=True)
    f = r[r.status == "filled"].copy()
    f.to_csv(os.path.join(OUT, "trades_EXPLORE_chosen.csv"), index=False)
    out = []
    for sym, g in f.groupby("sym"):
        g = g[g.t_fill < "2008-01-01"]
        if len(g):
            out.append(g.iloc[len(g) // 2])
    e = pd.DataFrame(out).head(n)
    cols = ["sym", "t_decide", "ob_lo", "ob_hi", "t_touch", "side", "fill", "sl", "tp",
            "t_exit", "exit", "exit_reason", "risk_pips", "net_pips", "usd_01", "f_zone_age", "kind"]
    e = e[[c for c in cols if c in e.columns]]
    e.to_csv(os.path.join(OUT, "examples.csv"), index=False)
    return e


def replay(tg, st, entry_kw, which=(0, 1, 2)):
    """Independent minute-by-minute replay of a few trades -- no common.simulate, no ladder."""
    te = entries("EXPLORE", max_touch=3, **entry_kw)
    te = te[te.touch_n == 1].reset_index(drop=True)
    r = run_sim(te, tg, "atr", st, one=True)
    f = r[r.status == "filled"].reset_index(drop=True)
    lines = []
    for q in which:
        row = f.iloc[q * 137 % len(f)]
        sym = row.sym
        m1 = bars(sym, "M1")
        P = C.pip(sym)
        i = int(np.searchsorted(m1.time.values, np.datetime64(pd.Timestamp(row.t_fill)), "left"))
        d = int(row.side)
        fill = float(row.fill)
        sl, tp = float(row.sl), float(row.tp)
        hit = None
        for k in range(i, min(i + 20000, len(m1))):
            lo, hi, sp = m1.low.values[k], m1.high.values[k], m1.spr.values[k]
            if d == 1:
                if lo <= sl:
                    hit = (k, (min(sl, m1.open.values[k]) if k > i else sl) - C.STOP_SLIP * P, "SL")
                    break
                if k > i and hi >= tp:
                    hit = (k, tp, "TP")
                    break
            else:
                if hi + sp >= sl:
                    hit = (k, (max(sl, m1.open.values[k] + sp) if k > i else sl) + C.STOP_SLIP * P, "SL")
                    break
                if k > i and lo + sp <= tp:
                    hit = (k, tp, "TP")
                    break
        if hit is None:
            lines.append("| %s | %s | no exit inside the replay window |" % (sym, row.t_fill))
            continue
        k, px, why = hit
        net = (px - fill) * d / P - C.COMM[sym]
        lines.append("| %s | %s | %s | %.5f | %.5f | %.5f | %s | %s | %s | %.2f | %.2f | %s |"
                     % (sym, row.t_decide, row.t_fill, fill, sl, tp, m1.time.values[k],
                        pd.Timestamp(row.t_exit), why + "/" + row.exit_reason, net, row.net_pips,
                        "MATCH" if abs(net - row.net_pips) < 0.05 else "**DIFFERS**"))
    hdr = ("| pair | break confirmed | filled | fill | stop | target | my exit minute | simulate exit "
           "minute | my reason / simulate reason | my net pips | simulate net pips | agree |\n"
           + "|---|" * 12 + "\n")
    return hdr.replace("|---|" * 12, "|" + "---|" * 12) + "\n".join(lines)


# =========================================================================== per-stage controls
def stage_controls(stage, tg, st, entry_kw, rid="R0", csv=None):
    """All four mandatory controls, on ONE stage, for ONE refinement.

    (1) luck bar: hour-matched, shape-matched, hold-matched, n=20,000 x 5 seeds, band quoted.
    (2) always-long / always-short / coin flip at the rule's own minutes.
    (3) buys and sells separately.
    (4) the look-ahead twin in BOTH points and dollars.
    """
    A.gate(stage)
    te = entries(stage, max_touch=3, **entry_kw)
    te = te[te.touch_n == 1].reset_index(drop=True)
    T0, N0, H0 = run_ladder(te, [tg], [st], "atr_grid", st)
    d0 = scoreframe(T0, N0[(tg, st)], H0[(tg, st)])
    m = REFINE[rid][1](d0)
    te = te.loc[te.index[np.flatnonzero(m)]].reset_index(drop=True) if rid != "R0" else te
    out = {}
    bs, _ = both_sides(te, tg, st)
    out["both_sides"] = bs
    T, N, H = run_ladder(te, [tg], [st], "atr_grid", st)
    net, hold = N[(tg, st)], H[(tg, st)]
    out["pooled"] = pd.DataFrame([summarise(T, net, "%s %s" % (rid, stage), hold)])
    out["by_side"] = by_side(T, net, rid, hold)
    out["by_pair"] = by_pair(T, net, hold)
    out["lookahead"] = lookahead_cost(te, tg, st)
    out["random_bar"] = random_bar(te, tg, st)
    lb_raw, lb = luckbars(te, tg, st, stage)
    out["luck_bar"] = lb
    out["luck_bar_raw"] = lb_raw
    pref = csv or ("controls_%s_%s" % (stage, rid))
    for k, v in out.items():
        if isinstance(v, pd.DataFrame) and len(v):
            v.to_csv(os.path.join(OUT, "%s_%s.csv" % (pref, k)), index=False)
    for k, v in out.items():
        print("\n----- %s %s %s -----" % (stage, rid, k))
        print(v.to_string() if isinstance(v, pd.DataFrame) else v)
    return out


def flip_rule(stage, tg, st, entry_kw, rid="R0"):
    """The same entries, traded the OTHER way: price breaks THROUGH the block instead of bouncing.

    Settled round finding: an untouched level ahead behaves as a magnet, not a wall. If that is
    also true of an order block, the bounce trade is the wrong side of the same signal and this
    flip should be the mirror of it (cost is paid twice, so the mirror is NOT symmetric in dollars).
    """
    A.gate(stage)
    te = entries(stage, max_touch=3, **entry_kw)
    te = te[te.touch_n == 1].reset_index(drop=True)
    rows = []
    for lbl, sgn in (("bounce (the SMC rule)", 1), ("through (the flip)", -1)):
        tt = te.copy()
        if sgn < 0:
            # re-price at the same minute on the other side of the book
            spr = []
            for sym, g in tt.groupby("sym", sort=False):
                m1 = bars(sym, "M1")
                spr.append(pd.Series(m1.spr.values[g.f.values], index=g.index))
            spr = pd.concat(spr).reindex(tt.index).values
            bid = tt.fill.values - np.where(tt.d.values == 1, spr, 0.0)
            tt["d"] = -tt.d.values
            tt["fill"] = bid + np.where(tt.d.values == 1, spr, 0.0)
        T, N, H = run_ladder(tt, [tg], [st], "atr_grid", st)
        rows.append(summarise(T, N[(tg, st)], "%s %s" % (stage, lbl), H[(tg, st)]))
        rows.extend(by_side(T, N[(tg, st)], lbl, H[(tg, st)]).to_dict("records"))
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(OUT, "flip_%s.csv" % stage), index=False)
    print(d.to_string())
    return d


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "controls":
        stage_controls(sys.argv[2], float(sys.argv[3]), float(sys.argv[4]),
                       dict(entry=sys.argv[5]) if sys.argv[5] == "prox"
                       else dict(entry="react", react_tf=sys.argv[5]),
                       sys.argv[6] if len(sys.argv) > 6 else "R0")
        sys.exit()
    if cmd == "flip":
        flip_rule(sys.argv[2], float(sys.argv[3]), float(sys.argv[4]),
                  dict(entry=sys.argv[5]) if sys.argv[5] == "prox"
                  else dict(entry="react", react_tf=sys.argv[5]))
        sys.exit()
    if cmd == "stage0":
        stage0()
        sys.exit()
    if cmd == "explore":
        explore()
        sys.exit()
    if cmd == "stages":
        # the shape, the entry and the <=3 refinements chosen on EXPLORE are passed in, so that
        # nothing here is re-chosen after VALIDATE has been read.
        tg, st = float(sys.argv[2]), float(sys.argv[3])
        ekw = dict(entry=sys.argv[4]) if sys.argv[4] == "prox" else dict(entry="react", react_tf=sys.argv[4])
        rids = sys.argv[5].split(",")
        validate(tg, st, ekw, rids)
        stress(tg, st, ekw, rids)
        e = examples(tg, st, ekw)
        _save_md("examples.md", "# AUT06 -- 10 example trades (EXPLORE)\n\n"
                 + e.to_markdown(index=False) + "\n")
        _save_md("replay.md", "# AUT06 -- three trades replayed by hand on M1\n\n"
                 + replay(tg, st, ekw) + "\n")
        sys.exit()
    t = gen("EXPLORE", pairs=["EURUSD", "XAUUSD"])
    print(t.shape)
