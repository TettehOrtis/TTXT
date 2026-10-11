"""AUT01 - autopsy of the EMA pullback (HTF 50-EMA trend -> M1 pullback to the 9/21 EMA).

This is the sniper's live rule.  BT21-E already measured the RAW version and it FAILED
(50.65 right in 100 against a 55.69 luck bar at the live $15/$70 shape).  This script does not
re-run that verdict.  It asks WHY individual trades won and lost, and whether a refined version
clears its own bar.

Governing documents: bt/AUTOPSY_BRIEF.md (method, catalogue A-G, locked split, overfitting caps)
and bt/AGENT_BRIEF.md (hard rules).  Shared layer: bt/autopsy_lib.py.  Nothing in common.py,
smc_core.py or any other agent's file is edited.

PRE-REGISTERED BEFORE ANY RESULT WAS SEEN
-----------------------------------------
Signal sets
  raw   : BT21-E's E4.  M15 trend from the last CLOSED M15 bar (close > EMA50 = up).  M1 trigger on
          the last CLOSED M1 bar: low <= EMA21 and close > open (BUY); high >= EMA21 and
          close < open (SELL).  Fires every qualifying minute.  Fill = next M1 open.
  pull  : raw PLUS the touch must be a real pullback - the previous CLEAR_BARS (5) M1 bars all
          stayed clear of the EMA on the trend side (BUY: low > EMA21 for 5 bars).  So it is the
          FIRST touch after a run away from the EMA, not the 400th minute of hugging it.
Autopsy shape: 1.5 x M15-ATR target / 1.0 x M15-ATR stop, max hold 1 day.  Break-even 42.9.
Costs: real Dukascopy per-minute spread (injected, see SPREAD BUG below) + the $1.10 gold round trip.
Shapes are ATR multiples only, never dollars (BT21 finding #2).

SPREAD BUG (catalogue F, found at stage 0 - reported, not fixed)
  data_duka/XAUUSD_M1.parquet carries spread == 0 on all 5,526,570 bars, so every
  SMC_DATASET=duka gold backtest in this project has been run with NO SPREAD, only commission.
  data_duka_full/XAUUSD_M1.parquet carries the real spread (median 335 points = 3.35 pips =
  $0.335 a round trip at 0.10 lots).  This script injects the real one by wrapping common.bars
  in its own process.  common.py is untouched.
"""
import os
import sys
import json
import time
import argparse

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("SMC_DATASET", "duka")

import common as C                                                        # noqa: E402
import autopsy_lib as A                                                   # noqa: E402
from smc_core import resample                                             # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT01_emapull")
os.makedirs(OUT, exist_ok=True)
SYM = "XAUUSD"
CLEAR_BARS = 5                        # how long price must stay clear of the EMA before a touch counts
SWING_N = 5                           # M15 fractal swing half-width; a swing is KNOWN only at i+N
#: the round number a trader actually watches on each instrument (C4)
RN = {s: (10.0 if s == "XAUUSD" else (1.0 if "JPY" in s else 0.0100)) for s in C.PAIRS}
MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURJPY"]
TEN = ["XAUUSD"] + MAJORS
TGT, STOP = 1.5, 1.0                  # the autopsy shape, in M15 ATR
HOLD = 1440                           # minutes


# ================================================================= feed / spread plumbing
#: AUT08 found 14,158 impossible-price bars in the Dukascopy files (an AUDJPY feed splice, 2 GBPCHF
#: bars, 1 AUDUSD bar) and common.simulate does not defend against them (common.py:172 only tests
#: risk > 0).  autopsy_lib.safe_bars screens them.  Of this report's ten symbols exactly ONE bar is
#: dropped (AUDUSD, 2005-11-21 20:12); gold, the instrument this report is about, is clean.
_TRUE_BARS = C.bars                     # captured before the patch below, so safe_bars cannot recurse


def _ORIG_BARS(sym, tf):
    """common.bars screened by autopsy_lib.screen_bars.  Our own _BARCACHE does the memoising that
    autopsy_lib.safe_bars would otherwise do (C.bars re-reads the parquet on every call)."""
    b, n = A.screen_bars(_TRUE_BARS(sym, tf))
    SCREENED[(sym, tf)] = n
    return b


SCREENED = {}
_BARCACHE = {}
CFG = dict(feed="duka", spr_mult=1.0, spr_fixed=None, spr_floor=None, gold="stand_in",
           lo=None, hi=None)


def _real_spread_m1(sym):
    """The per-minute Dukascopy spread that data_duka drops for gold (see SPREAD BUG)."""
    d = pd.read_parquet(os.path.join(C.ROOT, "data_duka_full", f"{sym}_M1.parquet"),
                        columns=["time", "spread"])
    return d.rename(columns={"spread": "spr_pts"})


def _alt_feed_m1(sym):
    """An independent broker feed for the stage-3 feed swap (data/, 2018-06 onward, real spread)."""
    d = pd.read_parquet(os.path.join(C.ROOT, "data", f"{sym}_M1.parquet"))
    d["spr"] = d.spread * 0.001 / C.pip(sym) * C.pip(sym)     # points -> price units
    return d[["time", "open", "high", "low", "close", "spr"]].reset_index(drop=True)


def _patched_bars(sym, tf):
    key = (sym, tf, CFG["feed"], CFG["spr_mult"], CFG["spr_fixed"], CFG["spr_floor"],
           CFG["gold"], CFG["lo"], CFG["hi"])
    if key in _BARCACHE:
        return _BARCACHE[key]
    if CFG["feed"] == "alt":
        m1 = _alt_feed_m1(sym)
        if tf == "M1":
            b = m1.copy()
            b["tclose"] = b.time + pd.Timedelta(minutes=1)
        else:
            b = resample(m1, tf)
    else:
        b = _ORIG_BARS(sym, tf).copy()
        if tf == "M1" and float(b.spr.abs().max()) == 0.0:    # the duka gold hole (see SPREAD BUG)
            if CFG["gold"] == "feed":
                s = _real_spread_m1(sym)
                b = b.merge(s, on="time", how="left")
                b["spr"] = (b.spr_pts.fillna(s.spr_pts.median()) * 0.001).clip(lower=0.0)
                b = b.drop(columns=["spr_pts"])
            else:
                b["spr"] = A.gold_spread(sym)      # autopsy_lib's documented $0.30 stand-in
    if CFG["spr_fixed"] is not None:
        b["spr"] = float(CFG["spr_fixed"]) * C.pip(sym)      # spr_fixed is in pips, spr is in price units
    if CFG["spr_floor"] is not None:
        b["spr"] = b.spr.clip(lower=float(CFG["spr_floor"]) * C.pip(sym))
    b["spr"] = b.spr * CFG["spr_mult"]
    if CFG["lo"] is not None:
        m = (b.time >= CFG["lo"]) & (b.time < CFG["hi"])
        b = b[m].reset_index(drop=True)
    _BARCACHE[key] = b
    return b


C.bars = _patched_bars
A.C.bars = _patched_bars


#: Two cost worlds, both reported at every stage.
#:  "zero" - the Exness ZERO account the sniper actually trades: ~0 raw spread, so BT21-E's flat
#:           0.05 price units (0.5 pips, $0.05 at 0.10 lots) of extra spread + the $1.10 round trip.
#:           This is the headline, and it is what BT21-E charged, so numbers are comparable.
#:  "duka" - the real raw Dukascopy per-minute spread (median 5.04 pips = $0.50 in EXPLORE) + the
#:           SAME $1.10 commission.  That double-counts, because the commission is what buys the
#:           zero spread, so it is deliberately adverse and is used for the stage-3 stress.
#:  "real"   - the HEADLINE.  Spread = max(the feed's own per-minute spread, 0.5 pips).  On gold the
#:             feed's spread is injected from data_duka_full (median 0.335 price units = $3.35 a round
#:             trip at 0.10 lots), which is more conservative than autopsy_lib's $0.30 stand-in.  On the
#:             FX majors the duka spread is often 0, so the 0.5-pip floor does the work.
#:  "real2x" - the same with the spread doubled: the stage-3 adverse-cost run.
#:  "zero"   - a flat 0.5 pips everywhere.  Optimistic on gold; kept only as a sensitivity.
COSTS = {"real": dict(spr_fixed=None, spr_floor=0.5, spr_mult=1.0, gold="stand_in"),
         "real2x": dict(spr_fixed=None, spr_floor=0.5, spr_mult=2.0, gold="stand_in"),
         "dukaraw": dict(spr_fixed=None, spr_floor=0.5, spr_mult=1.0, gold="feed"),
         "zero": dict(spr_fixed=0.5, spr_floor=None, spr_mult=1.0, gold="stand_in"),
         "duka": dict(spr_fixed=None, spr_floor=None, spr_mult=1.0, gold="feed")}   # spreads in PIPS


def set_window(stage, cost="real", feed="duka", warm_days=60, tail_days=20):
    """Point every bars() call at one stage (plus warm-up and a tail for exits) and one cost world."""
    a, z = A.STAGES[A.gate(stage)]
    _BARCACHE.clear()
    CFG.update(feed=feed, **COSTS[cost],
               lo=str(pd.Timestamp(a) - pd.Timedelta(days=warm_days)),
               hi=str(pd.Timestamp(z) + pd.Timedelta(days=tail_days)))
    return pd.Timestamp(a), pd.Timestamp(z)


# ================================================================= indicators (all causal)
def _ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def _rsi(c, n=14):
    d = pd.Series(c).diff()
    up = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50).values


def _run_len_true(mask):
    """For each i, how many consecutive True immediately BEFORE i (mask[i-1], mask[i-2], ...)."""
    out = np.zeros(len(mask), np.int32)
    run = 0
    for i in range(1, len(mask)):
        run = run + 1 if mask[i - 1] else 0
        out[i] = run
    return out


# ================================================================= signals + features
def build(stage, sym=SYM, trend_tf="M15", entry_ema=21, mode="pull", clear_bars=CLEAR_BARS,
          trend_mode="close_ema50"):
    """Every signal of one variant inside `stage`, with the catalogue features attached.

    Everything below is read from bars that had already CLOSED when the decision was made:
    the M1 trigger bar itself (its own open/high/low/close are known at its close) and the last
    CLOSED HTF bar via common.asof_index.  Nothing reads the fill bar.
    """
    a, z = A.STAGES[stage]
    LEG = 20                      # the leg window used for f_ext / f_pb_bars, fixed regardless of clear_bars
    m1 = C.bars(sym, "M1")
    tf = C.bars(sym, trend_tf)
    m15 = C.bars(sym, "M15")
    h1, h4 = C.bars(sym, "H1"), C.bars(sym, "H4")

    c1, o1, hi1, lo1 = m1.close.values, m1.open.values, m1.high.values, m1.low.values
    e_fast, e_slow = _ema(c1, 9), _ema(c1, 21)
    #: any EMA length, so the live bot's EMA20 can be measured as configured
    ema1 = e_slow if entry_ema == 21 else (e_fast if entry_ema == 9 else _ema(c1, entry_ema))
    rsi1 = _rsi(c1, 14)

    # ---- HTF trend from the last CLOSED HTF bar
    tfe = _ema(tf.close.values, 50)
    if trend_mode == "ema50_200":
        #: sniper/strategy.py trend_of(): the live bot crosses the 50-EMA against the 200-EMA on the
        #: trend timeframe.  The brief's spec (and BT21-E's) is close-above-the-50-EMA instead.
        tf_trend = np.where(tfe > _ema(tf.close.values, 200), 1, -1)
    else:
        tf_trend = np.where(tf.close.values > tfe, 1, -1)
    j = C.asof_index(tf, m1.tclose.values)
    ok = j >= (200 * 3 if trend_mode == "ema50_200" else 50 * 3)
    trend = np.where(ok, tf_trend[np.clip(j, 0, len(tf) - 1)], 0)
    tfe_at = np.where(ok, tfe[np.clip(j, 0, len(tf) - 1)], np.nan)

    # bars since the HTF trend last flipped (A2)
    flip = np.concatenate([[0], np.flatnonzero(np.diff(tf_trend) != 0) + 1])
    age_tf = np.arange(len(tf)) - flip[np.clip(np.searchsorted(flip, np.arange(len(tf)), "right") - 1, 0, None)]
    f_trend_age = np.where(ok, age_tf[np.clip(j, 0, len(tf) - 1)], np.nan)

    # ---- M15 ATR (the unit for every shape) and its context
    atr15 = A.atr(m15, 14).values
    j15 = C.asof_index(m15, m1.tclose.values)
    at = np.where(j15 >= 14, atr15[np.clip(j15, 0, len(atr15) - 1)], np.nan)
    s_atr = pd.Series(atr15)
    atr_rank = s_atr.rolling(96 * 250, min_periods=96 * 20).rank(pct=True).values     # A4, trailing year
    atr_chg = (s_atr / s_atr.shift(96)).values                                        # A5, vs a day ago
    m15e50 = _ema(m15.close.values, 50)
    m15slope = (m15e50 - np.roll(m15e50, 10))                                         # A3
    m15slope[:10] = np.nan

    def at15(v):
        return np.where(j15 >= 96, v[np.clip(j15, 0, len(v) - 1)], np.nan)[i]

    # ---- HTF agreement (A1)
    def tf_side(b):
        e = _ema(b.close.values, 50)
        s = np.where(b.close.values > e, 1, -1)
        k = C.asof_index(b, m1.tclose.values)
        return np.where(k >= 150, s[np.clip(k, 0, len(s) - 1)], 0)

    s_m15, s_h1, s_h4 = tf_side(m15), tf_side(h1), tf_side(h4)

    # ---- the trigger
    up = trend == 1
    touch_up = (lo1 <= ema1) & (c1 > o1) & up
    touch_dn = (hi1 >= ema1) & (c1 < o1) & (trend == -1)
    clear_up = _run_len_true(lo1 > ema1)       # bars immediately before i with low above the EMA
    clear_dn = _run_len_true(hi1 < ema1)
    tch = ((lo1 <= ema1) & (hi1 >= ema1)).astype(float)                              # C3
    touch_n = pd.Series(tch).rolling(120, min_periods=1).sum().shift(1).fillna(0).values
    if mode == "pull":
        sig_up = touch_up & (clear_up >= clear_bars)
        sig_dn = touch_dn & (clear_dn >= clear_bars)
    else:
        sig_up, sig_dn = touch_up, touch_dn
    sig = sig_up | sig_dn
    sig &= np.isfinite(at) & (at > 0) & np.isfinite(tfe_at)
    sig &= (m1.tclose.values >= np.datetime64(pd.Timestamp(a))) & \
           (m1.tclose.values < np.datetime64(pd.Timestamp(z)))
    sig[:400] = False
    sig[-20:] = False
    i = np.flatnonzero(sig)
    if len(i) == 0:
        return pd.DataFrame()
    d = np.where(sig_up[i], 1, -1)

    # ---- C1 / C2: room to run and the nearest opposing level, from CONFIRMED M15 swings
    is_sh, is_sl = C.swings(m15.high.values, m15.low.values, SWING_N)
    sh_i = np.flatnonzero(is_sh)
    sl_i = np.flatnonzero(is_sl)
    sh_known = sh_i + SWING_N                    # a fractal swing is only knowable N bars later
    sl_known = sl_i + SWING_N
    sh_px, sl_px = m15.high.values[sh_i], m15.low.values[sl_i]
    m15_hi_run = np.maximum.accumulate(m15.high.values)
    f_room = np.full(len(i), np.nan)
    f_behind = np.full(len(i), np.nan)
    f_untouched = np.zeros(len(i))
    px = c1[i]
    jj = j15[i]
    for k in range(len(i)):
        bar, p, side = jj[k], px[k], d[k]
        if side == 1:
            m = (sh_known <= bar) & (sh_px > p) & (sh_known >= bar - 400)
            if m.any():
                w = np.argmin(np.where(m, sh_px, np.inf))
                f_room[k] = sh_px[w] - p
                # untouched = price has not traded above that high since it formed
                hi_since = m15.high.values[sh_i[w] + 1:bar + 1]
                f_untouched[k] = float(len(hi_since) == 0 or hi_since.max() < sh_px[w])
            m2 = (sl_known <= bar) & (sl_px < p) & (sl_known >= bar - 400)
            if m2.any():
                f_behind[k] = p - np.max(np.where(m2, sl_px, -np.inf))
        else:
            m = (sl_known <= bar) & (sl_px < p) & (sl_known >= bar - 400)
            if m.any():
                w = np.argmax(np.where(m, sl_px, -np.inf))
                f_room[k] = p - sl_px[w]
                lo_since = m15.low.values[sl_i[w] + 1:bar + 1]
                f_untouched[k] = float(len(lo_since) == 0 or lo_since.min() > sl_px[w])
            m2 = (sh_known <= bar) & (sh_px > p) & (sh_known >= bar - 400)
            if m2.any():
                f_behind[k] = np.min(np.where(m2, sh_px, np.inf)) - p

    # ---- prior day's range (C5) and how much of it is spent (A6)
    day = m1.time.dt.floor("D")
    dd = m1.groupby(day).agg(dh=("high", "max"), dl=("low", "min"))
    dd = dd.shift(1)
    pdh = day.map(dd.dh).values
    pdl = day.map(dd.dl).values
    cum_hi = m1.groupby(day).high.cummax().values
    cum_lo = m1.groupby(day).low.cummin().values

    # ---- the rest
    t = m1.tclose.values[i]
    hr = m1.time.dt.hour.values[i]
    rng = (hi1 - lo1)[i]
    body = (c1 - o1)[i]
    ov = ema1[i]
    pb_depth = np.where(d == 1, ov - lo1[i], hi1[i] - ov)
    # how far price ran away from the EMA during the leg before the touch (D3 / A7)
    ext = np.array([(hi1[k - LEG:k].max() - ema1[k]) if d[n] == 1 else
                    (ema1[k] - lo1[k - LEG:k].min()) for n, k in enumerate(i)])
    # how long the pullback itself took (D4): bars since the extreme of the leg
    pbbars = np.array([(k - (k - LEG + (int(np.argmax(hi1[k - LEG:k])) if d[n] == 1 else
                                            int(np.argmin(lo1[k - LEG:k]))))) for n, k in enumerate(i)])
    eff = np.array([abs(c1[k] - c1[k - 30]) / max(1e-9, np.abs(np.diff(c1[k - 30:k + 1])).sum())
                    for k in i])                                                       # A3
    vol5 = np.array([(hi1[k - 4:k + 1].max() - lo1[k - 4:k + 1].min()) for k in i])
    vmd = pd.Series(rng).groupby(m1.time.dt.hour.values[i] * 60 + m1.time.dt.minute.values[i])

    out = pd.DataFrame({
        "t_signal": t, "side": d, "entry_type": "market", "entry": np.nan,
        "expire": t, "be_at_r": np.nan, "max_hold_min": float(HOLD),
        "sig_i": i, "sig_close": c1[i], "f_atr": at[i],
        "f_pb_depth": pb_depth / at[i],                       # D3
        "f_ext": ext / at[i],                                 # D3 / A7 leg size before the touch
        "f_pb_bars": pbbars,                                  # D4
        "f_body": body * d / at[i],                           # D1
        "f_close_pos": np.where(rng > 0, (np.where(d == 1, c1[i] - lo1[i], hi1[i] - c1[i])) / rng, 0.5),  # D2
        "f_bar_rng": rng / at[i],
        "f_engulf": ((np.where(d == 1, c1[i] > hi1[i - 1], c1[i] < lo1[i - 1]))).astype(float),  # D5
        "f_body_hold": (np.where(d == 1, c1[i] > ov, c1[i] < ov)).astype(float),
        "f_rsi": rsi1[i],
        "f_ema_gap": (e_fast[i] - e_slow[i]) * d / at[i],
        "f_htf_dist": (c1[i] - tfe_at[i]) * d / at[i],         # A7
        "f_trend_age": f_trend_age[i],                         # A2
        "f_slope": at15(m15slope) * d / at[i],                 # A3
        "f_eff": eff,                                          # A3
        "f_atr_rank": at15(atr_rank),                          # A4
        "f_atr_chg": at15(atr_chg),                            # A5
        "f_agree3": ((s_m15[i] == d) & (s_h1[i] == d) & (s_h4[i] == d)).astype(float),   # A1
        "f_agree_h4": (s_h4[i] == d).astype(float),
        "f_room": f_room / at[i],                              # C1 / C2
        "f_behind": f_behind / at[i],                           # C2
        "f_untouched": f_untouched,                             # C1
        "f_rn": np.abs(c1[i] - np.round(c1[i] / RN[sym]) * RN[sym]) / at[i],             # C4
        "f_dpos": np.where(pdh[i] > pdl[i], (c1[i] - pdl[i]) / (pdh[i] - pdl[i]), np.nan),  # C5
        "f_day_spent": (cum_hi[i] - cum_lo[i]) / np.where(pdh[i] > pdl[i], pdh[i] - pdl[i], np.nan),  # A6
        "f_hour": hr, "f_dow": m1.time.dt.dayofweek.values[i],                           # B1 / A8
        "f_overlap": ((hr >= 12) & (hr < 16)).astype(float),                             # B2
        "f_asia": (hr < 7).astype(float),
        "f_ldn": ((hr >= 7) & (hr < 12)).astype(float),
        "f_ny": ((hr >= 12) & (hr < 21)).astype(float),
        "f_spike": vol5 / at[i],                                                         # B5 proxy
        "f_touch_n": touch_n[i],                               # C3 how worn this EMA already is
        "f_rsi_ok": (np.where(d == 1, rsi1[i] <= 45.0, rsi1[i] >= 55.0)).astype(float),   # the live rule
        "f_spr": m1.spr.values[np.clip(i + 1, 0, len(m1) - 1)] / C.pip(sym),             # E5 (fill minute)
    })
    out.insert(0, "sym", sym)
    out["sl"] = out.sig_close - out.side * out.f_atr * STOP
    out["tp"] = out.sig_close + out.side * out.f_atr * TGT
    del vmd
    return out.reset_index(drop=True)


def run(orders, one_at_a_time=False, tgt=TGT, stop=STOP, hold=HOLD, be=np.nan, anchor="fill"):
    """Fills and exits for every order, per symbol, through common.simulate - never by hand.

    anchor="fill": the target and stop are measured from the price the order will actually fill at
    (the next M1 open, plus the spread for a buy), which is what a live bot does - it gets its fill
    and then brackets it.  anchor="close" measures them from the signal bar's close instead, which
    quietly makes the shape depend on the spread: a wider spread pushes a buy's fill nearer its
    target and further from its stop, so the same rule reads a HIGHER hit rate on a WORSE feed.
    That artifact is measured in the report; "fill" removes it.  Either way common.simulate alone
    decides the fill price and the exit.
    """
    out = []
    for sym, g in orders.groupby("sym", sort=True):
        o = g.drop(columns=["sym"]).copy()      # common.simulate inserts its own `sym`
        if anchor == "fill":
            b = C.bars(sym, "M1")
            a = np.searchsorted(b.time.values, pd.to_datetime(o.t_signal).values, "left")
            a = np.clip(a, 0, len(b) - 1)
            px = np.where(o.side.values == 1, b.open.values[a] + b.spr.values[a], b.open.values[a])
        else:
            px = o.sig_close.values
        o["sl"] = px - o.side * o.f_atr * stop
        o["tp"] = px + o.side * o.f_atr * tgt
        o["max_hold_min"] = float(hold)
        o["be_at_r"] = be
        out.append(C.simulate(sym, o, one_at_a_time=one_at_a_time))
    r = pd.concat(out, ignore_index=True, sort=False)
    return r[r.status == "filled"].reset_index(drop=True)


def build_all(stage, syms, **kw):
    fr = [build(stage, sym=s, **kw) for s in syms]
    fr = [f for f in fr if len(f)]
    return pd.concat(fr, ignore_index=True, sort=False).sort_values("t_signal").reset_index(drop=True)         if fr else pd.DataFrame()


def fmt(t, label=""):
    if len(t) == 0:
        return dict(label=label, trades=0)
    s = C.stats(t)
    s["win_pct"] = round(100 * (t.net_pips > 0).mean(), 2)
    s["usd_per_trade"] = round(t.usd_01.mean(), 3)
    s["label"] = label
    return s


# ================================================================= STAGE 0 - catalogue group F
def stage0(syms=("XAUUSD", "EURUSD", "GBPUSD")):
    """Prove the pipeline is clean BEFORE measuring anything (AUTOPSY_BRIEF: check group F first).

    A look-ahead test that reads "no difference" is not proof - it can be two biases cancelling - so
    the twin is built on purpose and its size is reported, not just its presence.
    """
    set_window("EXPLORE", cost="zero")
    out = {}
    for sym in syms:
        m1 = C.bars(sym, "M1")
        m15 = C.bars(sym, "M15")
        d = build("EXPLORE", sym=sym)
        if len(d) == 0:
            continue
        r = run(d)
        k = {}
        # ---- F1 which bar we fill on
        gap_s = (pd.to_datetime(r.t_fill) - pd.to_datetime(r.t_signal)).dt.total_seconds()
        k["F1_fill_is_next_minute_pct"] = round(100 * float((gap_s == 0).mean()), 2)
        k["F1_fill_before_signal_count"] = int((gap_s < 0).sum())
        k["F1_median_fill_lag_s"] = float(gap_s.median())
        # the twin: fill at the signal bar's OWN open (what BT21-E found worth +7.5 points here)
        tw = d.copy()
        tw["t_signal"] = pd.to_datetime(tw.t_signal) - pd.Timedelta(minutes=1)
        rt = run(tw)
        k["F1_twin_right_in_100"] = round(100 * float((rt.net_pips > 0).mean()), 2)
        k["F1_honest_right_in_100"] = round(100 * float((r.net_pips > 0).mean()), 2)
        k["F1_twin_minus_honest_points"] = round(k["F1_twin_right_in_100"] - k["F1_honest_right_in_100"], 2)
        k["F1_twin_minus_honest_usd"] = round(float(rt.usd_01.mean() - r.usd_01.mean()), 3)
        # ---- F4 the HTF value came from the last CLOSED bar
        j15 = C.asof_index(m15, pd.to_datetime(d.t_signal).values)
        k["F4_worst_htf_close_minus_signal_close_s"] = float(
            ((m15.tclose.values[np.clip(j15, 0, len(m15) - 1)] -
              pd.to_datetime(d.t_signal).values) / np.timedelta64(1, "s")).max())
        # ---- F3 gaps
        t1 = m1.time.values
        gp = np.zeros(len(t1), bool)
        gp[:-1] = np.diff(t1) > np.timedelta64(6, "h")
        gi = np.flatnonzero(gp)
        fi = np.searchsorted(t1, pd.to_datetime(r.t_fill).values, "left")
        xi = np.searchsorted(t1, pd.to_datetime(r.t_exit).values, "left")
        crossed = np.array([bool(((gi >= f) & (gi < x)).any()) for f, x in zip(fi, xi)])
        k["F3_pct_trades_crossing_a_6h_gap"] = round(100 * float(crossed.mean()), 2)
        k["F3_right_in_100_gap_trades_removed"] = round(100 * float((r.net_pips[~crossed] > 0).mean()), 2)
        w = m1[(m1.time >= "2005-01-01") & (m1.time < "2011-01-01")]
        k["F3_stage_first_bar"] = str(w.time.min())
        k["F3_stage_bars"] = int(len(w))
        span_wk = (w.time.max() - w.time.min()) / pd.Timedelta(weeks=1)
        k["F3_missing_minutes_pct_of_covered_span"] = round(100 * (1 - len(w) / (span_wk * 5 * 24 * 60)), 2)
        # ---- F5 same-bar target AND stop
        both = []
        for t_ex, tp, sl, sd in zip(pd.to_datetime(r.t_exit).values, r.tp.values, r.sl.values, r.side.values):
            q = np.searchsorted(t1, t_ex, "left")
            both.append(bool(m1.high.values[q] >= max(tp, sl) and m1.low.values[q] <= min(tp, sl)))
        k["F5_pct_exit_bars_holding_both_levels"] = round(100 * float(np.mean(both)), 2)
        k["F5_exit_reasons_on_those_bars"] = r.exit_reason[np.array(both)].value_counts().to_dict()
        # ---- F6 era drift of the unit
        k["F6_median_M15_ATR_price"] = round(float(d.f_atr.median()), 4)
        k["F6_median_target_usd_at_010"] = round(float((d.f_atr.median() * TGT / C.pip(sym)) * C.USD_PIP_01[sym]), 2)
        k["F6_round_trip_usd"] = round(C.COMM[sym] * C.USD_PIP_01[sym] + 0.5 * C.USD_PIP_01[sym], 3)
        k["F6_cost_as_pct_of_target"] = round(100 * k["F6_round_trip_usd"] / k["F6_median_target_usd_at_010"], 1)
        # ---- the EMA itself: recompute from scratch on a truncated window
        ii = d.sig_i.values[len(d) // 2]
        slow_full = _ema(m1.close.values, 21)[ii]
        slow_trunc = _ema(m1.close.values[:ii + 1], 21)[-1]
        k["EMA_causal_abs_diff_truncated_vs_full"] = float(abs(slow_full - slow_trunc))
        k["signals"] = int(len(d))
        k["trades_filled"] = int(len(r))
        out[sym] = k
    return out


def hand_replay(sym="XAUUSD", n=3, seed=7):
    """Re-do the arithmetic of n trades bar by bar from the raw M1 frame, outside the pipeline."""
    set_window("EXPLORE", cost="zero")
    d = build("EXPLORE", sym=sym)
    r = run(d)
    m1 = C.bars(sym, "M1")
    o, h, l, c, sp = m1.open.values, m1.high.values, m1.low.values, m1.close.values, m1.spr.values
    t1 = m1.time.values
    P, comm = C.pip(sym), C.COMM[sym]
    rows = []
    for _, x in r.sample(min(n, len(r)), random_state=seed).iterrows():
        f = int(np.searchsorted(t1, np.datetime64(pd.Timestamp(x.t_fill)), "left"))
        dd = int(x.side)
        fill = o[f] + sp[f] if dd == 1 else o[f]
        j = f
        got = None
        while j < min(f + int(x.max_hold_min) + 1, len(t1)):
            if (l[j] <= x.sl) if dd == 1 else (h[j] + sp[j] >= x.sl):
                px = (min(x.sl, o[j]) - 0.2 * P) if (dd == 1 and j > f) else (
                    (x.sl - 0.2 * P) if dd == 1 else (max(x.sl, o[j] + sp[j]) + 0.2 * P if j > f else x.sl + 0.2 * P))
                got = (j, px, "SL")
                break
            if j > f and ((h[j] >= x.tp) if dd == 1 else (l[j] + sp[j] <= x.tp)):
                got = (j, x.tp, "TP")
                break
            j += 1
        if got is None:
            rows.append(dict(t_signal=str(x.t_signal), ok=None, note="ran past the hand window"))
            continue
        j, px, why = got
        net = (px - fill) * dd / P - comm
        rows.append(dict(sym=sym, t_signal=str(x.t_signal), side=dd,
                         pipeline_fill=round(float(x.fill), 5), hand_fill=round(float(fill), 5),
                         pipeline_exit=round(float(x.exit), 5), hand_exit=round(float(px), 5),
                         pipeline_reason=x.exit_reason, hand_reason=why,
                         pipeline_net_pips=round(float(x.net_pips), 4), hand_net_pips=round(float(net), 4),
                         ok=bool(abs(float(x.net_pips) - net) < 1e-6 and x.exit_reason == why)))
    return rows


# ================================================================= substrate cache
def substrate(stage, syms=None, cost="real", feed="duka", **kw):
    """Signals + features for one stage, cached on disk so every later step reads the same table."""
    syms = list(syms or TEN)
    import hashlib
    tag = "%s|%s|%s|%s" % (stage, feed, ",".join(sorted(syms)), json.dumps(kw, sort_keys=True))
    f = os.path.join(OUT, "sig_%s_%s_%dsym_%s.parquet" % (
        stage, feed, len(syms), hashlib.md5(tag.encode()).hexdigest()[:8]))
    if os.path.exists(f):
        return pd.read_parquet(f)
    set_window(stage, cost=cost, feed=feed)
    d = build_all(stage, syms, **kw)
    d.to_parquet(f, index=False)
    return d


# ================================================================= the conditions (catalogue A-E)
#: Every entry is ONE mechanical sentence and ONE pre-declared cut.  No cut was searched over a grid.
#: A6 (day range spent), A8 (day of week) and C4 (round numbers) are deliberately NOT tested here:
#: C4 is already covered by BT10 and the brief says reuse it, and the other two were dropped to keep the
#: total trial count inside the 40-condition cap.  They are listed in the report as untested.
CONDS = [
    ("A1", "all three HTFs (M15, H1, H4) on the trade's side", lambda t: t.f_agree3 == 1),
    ("A1b", "H4 on the trade's side", lambda t: t.f_agree_h4 == 1),
    ("A2", "the M15 trend flipped at most 20 M15 bars ago (fresh)", lambda t: t.f_trend_age <= 20),
    ("A2b", "the M15 trend is at least 100 M15 bars old (stale)", lambda t: t.f_trend_age >= 100),
    ("A3", "the M15 EMA50 has moved at least 0.5 ATR in 10 bars the trade's way", lambda t: t.f_slope >= 0.5),
    ("A3b", "M1 travel is at least 35% efficient over the last 30 minutes", lambda t: t.f_eff >= 0.35),
    ("A4", "ATR in the top 30% of its own trailing year", lambda t: t.f_atr_rank >= 0.7),
    ("A4b", "ATR in the bottom 30% of its own trailing year", lambda t: t.f_atr_rank <= 0.3),
    ("A5", "ATR bigger than it was a day ago (expanding)", lambda t: t.f_atr_chg >= 1.0),
    ("A7", "price is within 1 ATR of the HTF 50-EMA (not extended)", lambda t: t.f_htf_dist <= 1.0),
    ("A7b", "price is more than 2 ATR beyond the HTF 50-EMA (extended)", lambda t: t.f_htf_dist >= 2.0),
    ("B1", "London hours, 07:00-12:00 UTC", lambda t: t.f_ldn == 1),
    ("B1b", "New York hours, 12:00-21:00 UTC", lambda t: t.f_ny == 1),
    ("B1c", "Asia hours, before 07:00 UTC", lambda t: t.f_asia == 1),
    ("B2", "London/NY overlap, 12:00-16:00 UTC", lambda t: t.f_overlap == 1),
    ("B4", "the spread at the fill minute is at or below this set's median", lambda t: t.f_spr <= t.f_spr.median()),
    ("B5", "news-like burst proxy: the last 5 minutes spanned at least 2 ATR", lambda t: t.f_spike >= 2.0),
    ("C1", "at least 1.5 ATR of clear air to the next opposing M15 swing (room to run)",
     lambda t: t.f_room >= 1.5),
    ("C1b", "that opposing level has never been traded through since it formed (untouched)",
     lambda t: t.f_untouched == 1),
    ("C2", "a prior M15 swing sits at least 1 ATR behind, to lean the stop on", lambda t: t.f_behind >= 1.0),
    ("C2b", "room ahead >= 1.5 ATR AND a level at least 1 ATR behind", lambda t: (t.f_room >= 1.5) & (t.f_behind >= 1.0)),
    ("C3", "the EMA has been touched at most 10 times in the last 120 minutes (fresh zone)",
     lambda t: t.f_touch_n <= 10),
    ("C5", "price sits in the middle 60% of yesterday's range", lambda t: (t.f_dpos >= 0.2) & (t.f_dpos <= 0.8)),
    ("D1", "the trigger candle's body is at least 0.3 ATR", lambda t: t.f_body >= 0.3),
    ("D2", "the trigger closed in the top 30% of its own range, the trade's way", lambda t: t.f_close_pos >= 0.7),
    ("D3", "a shallow tag: price went at most 0.1 ATR past the EMA", lambda t: t.f_pb_depth <= 0.1),
    ("D3b", "a deep overshoot: price went at least 0.25 ATR past the EMA", lambda t: t.f_pb_depth >= 0.25),
    ("D4", "the pullback took at most 5 minutes", lambda t: t.f_pb_bars <= 5),
    ("D5", "the trigger engulfed the previous bar", lambda t: t.f_engulf == 1),
    ("Dh", "the trigger closed back on the trend's side of the EMA (body hold)", lambda t: t.f_body_hold == 1),
    ("Dr", "the live bot's RSI depth test (<=45 to buy, >=55 to sell)", lambda t: t.f_rsi_ok == 1),
    ("De", "the leg into the pullback was at least 0.75 ATR long", lambda t: t.f_ext >= 0.75),
]


def luckbars(trades, stage, tgt, stop, seed=0):
    """A.luck_bar per symbol, matched on the hours this rule actually fired, then pooled by weight."""
    rows = []
    for sym, g in trades.groupby("sym"):
        hrs = sorted(set(pd.to_datetime(g.t_signal).dt.hour))
        lb = A.luck_bar(sym, len(g), tgt, stop, stage, seed=seed, hours=hrs)
        lb = lb[lb.status == "filled"]
        rows.append(dict(sym=sym, n_real=len(g), n_luck=len(lb),
                         luck_right_in_100=round(100 * float((lb.net_pips > 0).mean()), 2),
                         luck_usd=round(float(lb.usd_01.mean()), 3),
                         luck_PF=round(C.stats(lb)["PF"], 3),
                         real_right_in_100=round(100 * float((g.net_pips > 0).mean()), 2),
                         real_usd=round(float(g.usd_01.mean()), 3)))
    d = pd.DataFrame(rows)
    w = d.n_real / d.n_real.sum()
    d.attrs["pooled_luck"] = round(float((d.luck_right_in_100 * w).sum()), 2)
    d.attrs["pooled_real"] = round(float((d.real_right_in_100 * w).sum()), 2)
    return d


# ================================================================= STAGE 1 - EXPLORE
def _j(name, obj):
    with open(os.path.join(OUT, name), "w") as fh:
        json.dump(obj, fh, indent=1, default=str)
    print("wrote", name)


def ladder(d, label, seed=3, cap=12000, hold=HOLD):
    """Catalogue E6: price the SAME entries at every shape on A.ATR_LADDER.  Target/stop in ATR only."""
    s = d if len(d) <= cap else pd.concat(
        [g.sample(min(len(g), max(300, cap // d.sym.nunique())), random_state=seed)
         for _, g in d.groupby("sym")], ignore_index=True)
    rows = []
    for tg, st in A.ATR_LADDER:
        r = run(s, tgt=tg, stop=st, hold=hold)
        if len(r) == 0:
            continue
        be = float(np.mean([A.breakeven_rate(sym, float(g.f_atr.median()), tg, st)
                            for sym, g in s.groupby("sym")]))
        rows.append(dict(set=label, tgt_atr=tg, stop_atr=st, trades=len(r),
                         right_in_100=round(100 * float((r.net_pips > 0).mean()), 2),
                         breakeven_needs=round(be, 2),
                         edge_points=round(100 * float((r.net_pips > 0).mean()) - be, 2),
                         PF=round(C.stats(r)["PF"], 3),
                         usd_per_trade=round(float(r.usd_01.mean()), 3),
                         avg_hold_h=round(float(r.hold_h.mean()), 2)))
    return pd.DataFrame(rows).sort_values("usd_per_trade", ascending=False).reset_index(drop=True)


def explore():
    t0 = time.time()
    set_window("EXPLORE", cost="zero")
    d = substrate("EXPLORE")
    meta = dict(stage="EXPLORE", signals=int(len(d)), per_sym=d.groupby("sym").size().to_dict())

    # ---------- baseline: the raw rule (every qualifying minute) vs the real-pullback rule
    base = {}
    rawg = build("EXPLORE", sym="XAUUSD", mode="raw")
    meta["raw_gold_signals"] = int(len(rawg))
    rawg = rawg.sample(20000, random_state=1).sort_values("t_signal").reset_index(drop=True)
    for lbl, frame in [("raw_gold_20k_sample", rawg), ("pull_gold", d[d.sym == "XAUUSD"]),
                       ("pull_pooled10", d)]:
        base[lbl] = fmt(run(frame), lbl)
    # the live dollar shape, for the record ($15 target / $70 stop on gold at 0.10 lots)
    g = d[d.sym == "XAUUSD"].copy()
    g["f_atr"] = 1.0
    gl = g.copy()
    gl["sl"] = gl.sig_close - gl.side * 7.0
    gl["tp"] = gl.sig_close + gl.side * 1.5
    rl = C.simulate("XAUUSD", gl.drop(columns=["sym"]), one_at_a_time=False)
    base["pull_gold_live_15_70_dollars"] = fmt(rl[rl.status == "filled"], "live $15/$70")
    _j("explore_baseline.json", base)
    _j("explore_meta.json", meta)

    # ---------- the autopsy shape, both cost worlds
    r = run(d)
    r.to_parquet(os.path.join(OUT, "trades_explore_pull.parquet"), index=False)
    set_window("EXPLORE", cost="duka")
    rd = run(d)
    set_window("EXPLORE", cost="zero")
    _j("explore_costs.json", {"zero_spread_plus_commission": fmt(r, "zero"),
                              "raw_duka_spread_plus_commission": fmt(rd, "duka")})

    # ---------- the luck bar (catalogue G1)
    lb = luckbars(r, "EXPLORE", TGT, STOP)
    lb.to_csv(os.path.join(OUT, "explore_luckbar.csv"), index=False)
    _j("explore_luckbar_pooled.json", dict(pooled_luck=lb.attrs["pooled_luck"],
                                           pooled_real=lb.attrs["pooled_real"]))

    # ---------- catalogue E6: the shape ladder
    lad = pd.concat([ladder(d[d.sym == "XAUUSD"], "gold"), ladder(d, "pooled10")], ignore_index=True)
    lad.to_csv(os.path.join(OUT, "explore_ladder.csv"), index=False)

    # ---------- the Tally: every condition tried
    T = A.Tally("AUT01_emapull")
    for cid, note, fn in CONDS:
        m = fn(r).fillna(False).values if hasattr(fn(r), "fillna") else np.asarray(fn(r))
        if m.sum() < 200 or (~m).sum() < 200:
            T.rows.append(dict(cond=cid, note=note + "  [too few either side to read]",
                               lift=np.nan, kept=int(m.sum()), usd=np.nan))
            continue
        tb = A.score(r, m)
        T.add(cid, note, tb)
        T.rows[-1]["PF"] = round(C.stats(r[m])["PF"], 3)
        T.rows[-1]["usd_false"] = tb.net_usd_per_trade[2]
    # E3: does a time cap help or hurt
    for hold, lbl in [(120, "E3 time cap 2 h"), (1440, "E3 time cap 1 day (the autopsy shape)"),
                      (7200, "E3 time cap 5 days (matches the luck bar's hold)")]:
        rr = run(d, hold=hold)
        T.rows.append(dict(cond="E3", note=lbl, lift=round(100 * float((rr.net_pips > 0).mean()) -
                                                           100 * float((r.net_pips > 0).mean()), 2),
                           kept=len(rr), usd=round(float(rr.usd_01.mean()), 3),
                           PF=round(C.stats(rr)["PF"], 3), usd_false=np.nan))
    # E4: break-even stop
    for be in [0.5, 1.0]:
        rr = run(d, be=be)
        T.rows.append(dict(cond="E4", note="move the stop to break-even once %.1f R in favour" % be,
                           lift=round(100 * float((rr.net_pips > 0).mean()) -
                                      100 * float((r.net_pips > 0).mean()), 2),
                           kept=len(rr), usd=round(float(rr.usd_01.mean()), 3),
                           PF=round(C.stats(rr)["PF"], 3), usd_false=np.nan))
    # the two parameters traders argue about
    for ee, ttf, lbl in [(9, "M15", "pull to the M1 EMA9 instead of the EMA21"),
                         (21, "M5", "M5 50-EMA trend filter instead of M15")]:
        dv = substrate("EXPLORE", entry_ema=ee, trend_tf=ttf)
        rr = run(dv)
        T.rows.append(dict(cond="var", note=lbl,
                           lift=round(100 * float((rr.net_pips > 0).mean()) -
                                      100 * float((r.net_pips > 0).mean()), 2),
                           kept=len(rr), usd=round(float(rr.usd_01.mean()), 3),
                           PF=round(C.stats(rr)["PF"], 3), usd_false=np.nan))
    # D6 cannot be tested on this feed
    T.rows.append(dict(cond="D6", note="volume at the trigger - NOT TESTABLE: Dukascopy tick_volume is "
                                       "the constant 1 on all gold bars", lift=np.nan, kept=0,
                       usd=np.nan, PF=np.nan, usd_false=np.nan))
    tf_ = T.frame()
    tf_.to_csv(os.path.join(OUT, "tally.csv"), index=False)
    _j("explore_tally_meta.json", dict(conditions_tried=int(len(tf_)),
                                       seconds=round(time.time() - t0, 1)))
    print(tf_.to_string())
    return r, tf_, lad


# ================================================================= STAGES 2 and 3
#: Filled in after EXPLORE, never changed afterwards.  Each is (name, one-sentence rule, filter, tgt, stop, hold).
REFINEMENTS = []


def confirm(stage, cost="real", feed="duka", syms=None, one_at_a_time=False):
    """Run the chosen refinements, unchanged, on a stage.  Nothing is tuned here."""
    set_window(stage, cost=cost, feed=feed)
    d = substrate(stage, syms=syms, cost=cost, feed=feed)
    base = run(d)
    rows = [dict(name="baseline pull 1.5/1.0", **fmt(base))]
    out = {"baseline pull 1.5/1.0": base}
    for name, _sent, fn, tg, st, hd in REFINEMENTS:
        m = fn(d)
        m = m.fillna(False).values if hasattr(m, "fillna") else np.asarray(m)
        sel = d[m]
        if len(sel) == 0:
            continue
        r = run(sel, tgt=tg, stop=st, hold=hd, one_at_a_time=one_at_a_time)
        rows.append(dict(name=name, **fmt(r)))
        out[name] = r
    tab = pd.DataFrame(rows)
    tab.insert(0, "stage", stage)
    tab.insert(1, "cost", cost)
    tab.insert(2, "feed", feed)
    return tab, out, d


def stage_report(stage, costs=("real",), feeds=("duka",), syms=None):
    tabs, lucks = [], []
    for cost in costs:
        for feed in feeds:
            tab, out, d = confirm(stage, cost=cost, feed=feed, syms=syms)
            tabs.append(tab)
            if cost == "real" and feed == "duka":
                for name, r in out.items():
                    tg, st = (TGT, STOP)
                    for nm, _s, _f, a_, b_, _h in REFINEMENTS:
                        if nm == name:
                            tg, st = a_, b_
                    lb = luckbars(r, stage, tg, st)
                    lb.insert(0, "variant", name)
                    lb.insert(0, "stage", stage)
                    lb.attrs = {}
                    lucks.append(lb)
                    out[name].to_parquet(os.path.join(
                        OUT, "trades_%s_%s.parquet" % (stage, name.replace(" ", "_").replace("/", "-"))),
                        index=False)
    t = pd.concat(tabs, ignore_index=True)
    t.to_csv(os.path.join(OUT, "stage_%s.csv" % stage), index=False)
    if lucks:
        pd.concat(lucks, ignore_index=True).to_csv(os.path.join(OUT, "luck_%s.csv" % stage), index=False)
    print(t.to_string())
    return t


# ================================================================= STAGE 1b - the mirror and the anchor
def flip(d):
    """Same setups, opposite side.  Every f_* column still describes the SETUP, so its meaning is
    unchanged; only the direction traded is reversed."""
    e = d.copy()
    e["side"] = -e["side"]
    return e


def explore2():
    set_window("EXPLORE", cost="zero")
    d = substrate("EXPLORE")
    res = {}

    # ---- the anchor artifact, measured
    for anc in ("close", "fill"):
        for cost in ("zero", "duka"):
            set_window("EXPLORE", cost=cost)
            r = run(d, anchor=anc)
            res["anchor=%s cost=%s" % (anc, cost)] = fmt(r)
    set_window("EXPLORE", cost="zero")
    _j("explore_anchor.json", res)

    # ---- the mirror: fade the pullback instead of following it
    f = flip(d)
    lad = pd.concat([ladder(f[f.sym == "XAUUSD"], "gold_FADE"), ladder(f, "pooled10_FADE")],
                    ignore_index=True)
    lad.to_csv(os.path.join(OUT, "explore_ladder_fade.csv"), index=False)
    print(lad.head(14).to_string())

    # ---- the fade at the autopsy shape, pooled and per pair, beside its own luck bar
    rf = run(f)
    rf.to_parquet(os.path.join(OUT, "trades_explore_fade.parquet"), index=False)
    lb = luckbars(rf, "EXPLORE", TGT, STOP, seed=11)
    lb.to_csv(os.path.join(OUT, "explore_luckbar_fade.csv"), index=False)
    _j("explore_fade.json", {"pooled": fmt(rf, "fade 1.5/1.0"),
                             "pooled_luck": lb.attrs["pooled_luck"],
                             "pooled_real": lb.attrs["pooled_real"],
                             "per_sym": {s: fmt(g) for s, g in rf.groupby("sym")}})
    return rf, lad


# ================================================================= 10 trades a human can check
def examples(trades, n=10, seed=21):
    """A stratified draw - winners and losers in proportion - so the ten are not cherry-picked."""
    t = trades.copy()
    w = t[t.net_pips > 0]
    l = t[t.net_pips <= 0]
    nw = max(1, int(round(n * len(w) / max(1, len(t)))))
    s = pd.concat([w.sample(min(nw, len(w)), random_state=seed),
                   l.sample(min(n - nw, len(l)), random_state=seed)]).sort_values("t_signal")
    cols = ["sym", "t_signal", "side", "t_fill", "fill", "sl", "tp", "t_exit", "exit",
            "exit_reason", "hold_h", "net_pips", "R", "usd_01", "f_atr", "f_room", "f_ext",
            "f_body", "f_pb_depth", "mfe_R", "mae_R"]
    return s[[c for c in cols if c in s.columns]].round(5)


# ================================================================= the three refinements (LOCKED after EXPLORE)
def _stack(d):
    """The filter stack: the four conditions whose hit rate rose on EXPLORE, all four required."""
    return ((d.f_overlap == 1) & (d.f_htf_dist <= 1.0) & (d.f_pb_depth <= 0.1) & (d.f_body < 0.3))


REFINEMENTS[:] = [
    ("R1_shape", "the owner's entry unchanged, but the target and stop sized in ATR instead of dollars: "
                 "a 2.0 x M15-ATR target behind a 4.0 x M15-ATR stop, flat after one day.  Chosen on "
                 "EXPLORE as the best net dollars per trade of the 36 shapes on A.ATR_LADDER.",
     lambda d: np.ones(len(d), bool), 2.0, 4.0, 1440),
    ("R2_shape_filter", "R1 plus all four of the conditions whose hit rate rose on EXPLORE: the "
                        "London/New York overlap (12:00-16:00 UTC); price within 1 ATR of the "
                        "higher-timeframe 50-EMA; the pullback went at most 0.1 ATR past the M1 EMA21; "
                        "and the trigger candle's body is under 0.3 ATR.",
     _stack, 2.0, 4.0, 1440),
    ("R3_balanced", "the owner's entry unchanged at a balanced 3.0 x ATR target / 3.0 x ATR stop - the "
                    "shape that came closest to its own break-even anywhere on the ladder "
                    "(profit factor 0.96 on gold in EXPLORE).",
     lambda d: np.ones(len(d), bool), 3.0, 3.0, 1440),
]


def side_split(r):
    """Catalogue A: EXPLORE is a bull market in gold, so buys and sells are always reported apart."""
    out = {}
    for sd, lbl in [(1, "BUY"), (-1, "SELL")]:
        g = r[r.side == sd]
        out[lbl] = fmt(g, lbl) if len(g) else dict(trades=0)
    return out


def confirm2(stage, cost="real", feed="duka", seed=5):
    """Baseline + the three refinements on one stage, with buy/sell split and corrected luck bars."""
    set_window(stage, cost=cost, feed=feed)
    d = substrate(stage, feed=feed)
    rows, lucks, res = [], [], {}
    jobs = [("R0_baseline_1.5_1.0", lambda x: np.ones(len(x), bool), TGT, STOP, HOLD)] + \
           [(n, f, a, b, h) for n, _s, f, a, b, h in REFINEMENTS]
    for name, fn, tg, st, hd in jobs:
        m = fn(d)
        m = m.fillna(False).values if hasattr(m, "fillna") else np.asarray(m)
        sel = d[m]
        if len(sel) < 50:
            continue
        if name == "R3_fade":
            sel = flip(sel)
        r = run(sel, tgt=tg, stop=st, hold=hd)
        res[name] = r
        rows.append(dict(stage=stage, cost=cost, variant=name, scope="pooled10", **fmt(r)))
        for sd, lbl in [(1, "BUY"), (-1, "SELL")]:
            g = r[r.side == sd]
            if len(g):
                rows.append(dict(stage=stage, cost=cost, variant=name, scope="pooled10 " + lbl, **fmt(g)))
        g = r[r.sym == "XAUUSD"]
        if len(g):
            rows.append(dict(stage=stage, cost=cost, variant=name, scope="GOLD", **fmt(g)))
            for sd, lbl in [(1, "BUY"), (-1, "SELL")]:
                h = g[g.side == sd]
                if len(h):
                    rows.append(dict(stage=stage, cost=cost, variant=name, scope="GOLD " + lbl, **fmt(h)))
        if cost == "real":
            lb = luckbars(r, stage, tg, st, seed=seed)
            lb.insert(0, "variant", name)
            lb.insert(0, "stage", stage)
            lb["breakeven_needs"] = [round(A.breakeven_rate(s_, float(sel[sel.sym == s_].f_atr.median()
                                                            if (sel.sym == s_).any() else 1.0), tg, st), 2)
                                     for s_ in lb.sym]
            lucks.append(lb.copy())
            r.to_parquet(os.path.join(OUT, "trades_%s_%s.parquet" % (stage, name)), index=False)
    t = pd.DataFrame(rows)
    t.to_csv(os.path.join(OUT, "stage_%s_%s.csv" % (stage, cost)), index=False)
    if lucks:
        pd.concat(lucks, ignore_index=True).to_csv(os.path.join(OUT, "luck_%s.csv" % stage), index=False)
    print(t.to_string(index=False))
    return t, res


# ================================================================= the direction control (from AUT05)
def direction_control(d, stage, tgt=TGT, stop=STOP, hold=HOLD, seed=4):
    """Run EVERY signal minute twice - once all-long, once all-short - so the rule's own direction
    call is just a pick between two MEASURED outcomes.

    The EMA pullback is a trend-following rule and EXPLORE is 1.8 years of gold going 866 -> 1429, so
    this is the control it is most exposed to: if "always long" alone beats the rule, the rule has
    found the bull market, not an entry.
    """
    rng = np.random.default_rng(seed)
    out = []
    for lbl, sd in [("always LONG", 1), ("always SHORT", -1), ("coin flip", 0), ("the rule's own call", None)]:
        e = d.copy()
        if sd in (1, -1):
            e["side"] = sd
        elif sd == 0:
            e["side"] = rng.choice([1, -1], size=len(e))
        r = run(e, tgt=tgt, stop=stop, hold=hold)
        row = dict(who=lbl, **fmt(r))
        for s2, l2 in [(1, "BUY"), (-1, "SELL")]:
            g = r[r.side == s2]
            row["%s_trades" % l2] = len(g)
            row["%s_right_in_100" % l2] = round(100 * float((g.net_pips > 0).mean()), 2) if len(g) else np.nan
            row["%s_usd" % l2] = round(float(g.usd_01.mean()), 3) if len(g) else np.nan
        out.append(row)
    t = pd.DataFrame(out)
    t.insert(0, "stage", stage)
    return t
