"""SMC + EMA multi-timeframe engine (shared by backtest and live scanner, so both see identical logic).

Everything is CAUSAL: a bar is used only after it has closed, a swing only after it is confirmed (N bars later),
a higher-timeframe value only after that higher-timeframe bar has closed.

Pipeline per pair (smc-strategy.md, section 5.7):
  1H bias (structure + EMA200)  ->  30M agrees  ->  30M POI (unmitigated OB/FVG)  ->  price reaches POI (first touch)
  ->  5M CHoCH in bias direction within `wait_choch` bars  ->  limit entry on retest of the 5M FVG/OB of that move
  ->  SL beyond zone / sweep extreme  ->  TP = next unbroken 1H swing (liquidity), RR >= min_rr
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "research"))

TF_MIN = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}


# ------------------------------------------------------------------ bars
def resample(m1, tf):
    """m1: DataFrame(time, open, high, low, close, spr). Returns TF bars with open-time `time` and close-time `tclose`."""
    rule = f"{TF_MIN[tf]}min"
    g = m1.set_index("time").resample(rule, label="left", closed="left")
    b = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(),
                      "spr": g.spr.mean()}).dropna().reset_index()
    b["tclose"] = b.time + pd.Timedelta(minutes=TF_MIN[tf])
    return b


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def atr(b, n=14):
    return (b.high - b.low).rolling(n).mean().values


# ------------------------------------------------------------------ swings / structure
def swings(h, l, N):
    """Fractal swings. is_sh[i] True if high[i] is the max of i-N..i+N (strictly above the left side). Known at i+N."""
    hs, ls = pd.Series(h), pd.Series(l)
    left_h = hs.rolling(N).max().shift(1).values
    right_h = hs[::-1].rolling(N).max().shift(1)[::-1].values
    left_l = ls.rolling(N).min().shift(1).values
    right_l = ls[::-1].rolling(N).min().shift(1)[::-1].values
    return (h > left_h) & (h >= right_h), (l < left_l) & (l <= right_l)


def structure(b, N):
    """Causal market structure on closed bars.
    Returns dict of arrays (len = bars):
      trend   +1/-1/0 after bar k closed
      evt     +2 bull CHoCH, +1 bull BOS, -1 bear BOS, -2 bear CHoCH, 0 none   (breaks need a CLOSE beyond the swing)
      ob_lo/ob_hi   order block of the impulsive leg that made the break
      leg_i   index of the leg origin (lowest low for bull break / highest high for bear break)
      lsh/lsl last confirmed swing high/low levels, lsh_i/lsl_i their bar index
    """
    o, h, l, c = b.open.values, b.high.values, b.low.values, b.close.values
    n = len(b)
    is_sh, is_sl = swings(h, l, N)
    trend = np.zeros(n, np.int8); evt = np.zeros(n, np.int8)
    ob_lo = np.full(n, np.nan); ob_hi = np.full(n, np.nan); leg_i = np.full(n, -1, np.int64)
    lsh_a = np.full(n, np.nan); lsl_a = np.full(n, np.nan); lsh_ia = np.full(n, -1, np.int64); lsl_ia = np.full(n, -1, np.int64)
    tr, lsh, lsl, lsh_i, lsl_i, sh_ok, sl_ok = 0, np.nan, np.nan, -1, -1, False, False
    for k in range(n):
        s = k - N
        if s >= 0 and is_sh[s]:
            lsh, lsh_i, sh_ok = h[s], s, True
        if s >= 0 and is_sl[s]:
            lsl, lsl_i, sl_ok = l[s], s, True
        if sh_ok and c[k] > lsh:
            kind = 2 if tr == -1 else 1
            tr, sh_ok = 1, False
            j0 = lsh_i + 1 + int(np.argmin(l[lsh_i + 1:k + 1]))
            j = j0
            for jj in range(j0, max(j0 - 6, lsh_i), -1):
                if c[jj] < o[jj]:
                    j = jj; break
            evt[k], ob_lo[k], ob_hi[k], leg_i[k] = kind, l[j], h[j], j0
        elif sl_ok and c[k] < lsl:
            kind = 2 if tr == 1 else 1
            tr, sl_ok = -1, False
            j0 = lsl_i + 1 + int(np.argmax(h[lsl_i + 1:k + 1]))
            j = j0
            for jj in range(j0, max(j0 - 6, lsl_i), -1):
                if c[jj] > o[jj]:
                    j = jj; break
            evt[k], ob_lo[k], ob_hi[k], leg_i[k] = -kind, l[j], h[j], j0
        trend[k] = tr
        lsh_a[k], lsl_a[k], lsh_ia[k], lsl_ia[k] = lsh, lsl, lsh_i, lsl_i
    return dict(trend=trend, evt=evt, ob_lo=ob_lo, ob_hi=ob_hi, leg_i=leg_i, lsh=lsh_a, lsl=lsl_a, lsh_i=lsh_ia, lsl_i=lsl_ia,
                is_sh=is_sh, is_sl=is_sl)


def fvgs(b, min_atr=0.1):
    """Bullish FVG at k: low[k] > high[k-2]  -> zone (high[k-2], low[k]).  Bearish mirror. Known at close of k."""
    h, l = b.high.values, b.low.values
    a = atr(b)
    bull = np.zeros(len(b), bool); bear = np.zeros(len(b), bool)
    bull[2:] = (l[2:] - h[:-2]) >= min_atr * np.nan_to_num(a[2:], nan=np.inf)
    bear[2:] = (l[:-2] - h[2:]) >= min_atr * np.nan_to_num(a[2:], nan=np.inf)
    return bull, bear


# ------------------------------------------------------------------ POI zones on the zone timeframe
def build_zones(b, st, max_age_bars, fvg_min_atr=0.1):
    """All 30M POIs: OBs from structure breaks + FVGs. Each zone: dir, lo, hi, kind, t_created (bar close), t_expire."""
    bull, bear = fvgs(b, fvg_min_atr)
    h, l = b.high.values, b.low.values
    tc = b.tclose.values
    tf = tc[1] - b.time.values[1]
    rows = []
    for k in np.where(st["evt"] != 0)[0]:
        d = 1 if st["evt"][k] > 0 else -1
        rows.append((d, st["ob_lo"][k], st["ob_hi"][k], "OB", k))
    for k in np.where(bull)[0]:
        rows.append((1, h[k - 2], l[k], "FVG", k))
    for k in np.where(bear)[0]:
        rows.append((-1, h[k], l[k - 2], "FVG", k))
    z = pd.DataFrame(rows, columns=["dir", "lo", "hi", "kind", "k"])
    z["t_created"] = tc[z.k.values]
    z["t_expire"] = z.t_created + max_age_bars * tf
    return z.sort_values("t_created").reset_index(drop=True)


def first_touch(z, m5):
    """For each zone, first M5 bar (after creation, before expiry) whose range trades into the zone. -1 if none.
    That touch mitigates the zone (only the first touch is tradable)."""
    t5 = m5.time.values
    hi5, lo5 = m5.high.values, m5.low.values
    out = np.full(len(z), -1, np.int64)
    a = np.searchsorted(t5, z.t_created.values, "left")        # first M5 bar opening at/after zone creation
    e = np.searchsorted(t5, z.t_expire.values, "left")
    for i in range(len(z)):
        s, en = a[i], e[i]
        if en <= s:
            continue
        hit = np.where((lo5[s:en] <= z.hi.values[i]) & (hi5[s:en] >= z.lo.values[i]))[0]
        if len(hit):
            out[i] = s + hit[0]
    return out
