"""AUT04 - Opening range breakout, London and New York. Autopsy agent 4.

Governing document: bt/AUTOPSY_BRIEF.md. Hard rules: bt/AGENT_BRIEF.md.
Writes ONLY results/AUT04_orb/. Edits nothing shared.

Run (from the TTXT root):
    set SMC_DATASET=duka
    .venv\\Scripts\\python.exe smc_bot/bt/aut04_orb.py audit        # stage 0, catalogue group F
    .venv\\Scripts\\python.exe smc_bot/bt/aut04_orb.py explore      # stage 1
    .venv\\Scripts\\python.exe smc_bot/bt/aut04_orb.py validate     # stage 2
    .venv\\Scripts\\python.exe smc_bot/bt/aut04_orb.py stress       # stage 3

Why a bars() wrapper exists (documented, not hidden):
  1. cache_duka/*_M1.parquet has `spr` identically 0.0 for EVERY symbol, gold included, so
     common.simulate() charges commission only and no spread at all. BT21R solved this by adding a
     flat 0.05 price units of gold spread; AUT04 does the same, by wrapping C.bars in THIS module
     (common.py is never edited). EXTRA_SPR is a module constant and is swept in the cost ladder.
  2. C.bars() re-reads a 5.5M-row parquet on every call, and simulate() calls it once per run. The
     wrapper memoises.
  3. FEED="dukafull" serves data_duka_full/, which has gold back to 2005-01-02 and the REAL
     per-minute Dukascopy spread. That is the feed swap for stage 3 (catalogue F2).
"""
import os
import sys
import time
import itertools

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                                                        # noqa: E402
import autopsy_lib as A                                                   # noqa: E402

SYM = "XAUUSD"
OUT = os.path.join(C.RESULTS, "AUT04_orb")
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- costs and feed
EXTRA_SPR = 0.05            # price units of gold spread injected into the (all-zero) duka spr column
FEED = "duka"               # "duka" = cache_duka (spread stripped) | "dukafull" = data_duka_full (real spread)

SESSIONS = {"LON": ("Europe/London", 8, 0), "NY": ("America/New_York", 9, 30)}
RNG_N = 15                  # opening range = first 15 M1 bars from the session-open bar, inclusive
BREAK_WIN = 120             # the range has 2 h to break
BUF_K = 0.25                # break buffer = 0.25 x ATR60 (M1 mean bar range over the 60 bars before the range ends)
RETEST_WIN = 30             # bars a retest limit order stays live
HOLD_MIN = 480              # baseline time stop, clock minutes from the fill

_ORIG_BARS = C.bars
_MEMO = {}


def bars(sym, tf):
    """C.bars with a memo, the gold spread filled in, and an optional real-spread feed swap."""
    key = (FEED, EXTRA_SPR, sym, tf)
    if key in _MEMO:
        return _MEMO[key]
    if FEED == "duka":
        b = _ORIG_BARS(sym, tf)
        b, _ndrop = A.screen_bars(b)            # AUT08 corrupt-bar screen; gold drops 0 of 5,526,570
        b = b.copy()
        if EXTRA_SPR:
            b["spr"] = b.spr.values + EXTRA_SPR
    elif FEED == "dukafull":
        f = os.path.join(C.ROOT, "data_duka_full", f"{sym}_M1.parquet")
        m1 = pd.read_parquet(f)
        point = 0.001 if sym == SYM else (0.001 if "JPY" in sym else 1e-05)
        m1 = m1[["time", "open", "high", "low", "close", "spread"]].rename(columns={"spread": "spr"})
        m1["spr"] = m1.spr.clip(lower=0).astype("float64") * point        # -> price units
        if tf == "M1":
            m1["tclose"] = m1.time + pd.Timedelta(minutes=1)
            b = m1
        else:
            b = C.resample(m1, tf)
    else:
        raise ValueError(FEED)
    for c in ("time", "tclose"):
        b[c] = b[c].astype("datetime64[ns]")
    _MEMO[key] = b
    return b


C.bars = bars                                                             # simulate/luck_bar/atr_at all follow


def set_feed(feed, extra_spr=None):
    global FEED, EXTRA_SPR
    FEED = feed
    if extra_spr is not None:
        EXTRA_SPR = extra_spr


# ---------------------------------------------------------------- session anchors (DST aware)
def session_starts(b, tzname, hh, mm, maxgap=3):
    """Bar index of each session open. Same algorithm as research/orb.py::session_starts."""
    t = b.time
    days = pd.date_range(t.iloc[0].normalize(), t.iloc[-1].normalize(), freq="D")
    days = days[days.dayofweek < 5]
    tz = ZoneInfo(tzname)
    loc = [pd.Timestamp(year=x.year, month=x.month, day=x.day, hour=hh, minute=mm, tz=tz) for x in days]
    utc = pd.DatetimeIndex([x.tz_convert("UTC").tz_localize(None) for x in loc])
    tv = t.values
    pos = np.searchsorted(tv, utc.values)
    ok = pos < len(tv) - 1
    pos, utc = pos[ok], utc[ok]
    gap = (tv[pos] - utc.values) / np.timedelta64(1, "m")
    keep = gap <= maxgap
    return pos[keep].astype(np.int64), utc[keep], gap[keep]


def m1_atr(b, w=60):
    """Mean M1 bar range over the w bars ENDING AT i-1 (strictly before bar i). Never peeks."""
    r = pd.Series(b.high.values - b.low.values)
    return r.rolling(w, min_periods=w).mean().shift(1).values.astype(np.float64)


# ---------------------------------------------------------------- the opening ranges
def ranges(b, sess, rng_n=RNG_N):
    """One row per session: the opening range, its context, and the bar indices around it."""
    tz, hh, mm = SESSIONS[sess]
    pos, utc, gap = session_starts(b, tz, hh, mm)
    t = b.time.values
    hi, lo, op, cl = b.high.values, b.low.values, b.open.values, b.close.values
    a60 = m1_atr(b, 60)
    rows = []
    for i0, t_open, g in zip(pos, utc, gap):
        ie = i0 + rng_n - 1
        if ie + BREAK_WIN + 2 >= len(b):
            continue
        # the range must be rng_n CONSECUTIVE clock minutes (no hole, no gap)
        span = (t[ie] - t[i0]) / np.timedelta64(1, "m")
        rh = hi[i0:ie + 1].max()
        rl = lo[i0:ie + 1].min()
        rows.append(dict(sess=sess, i_open=i0, i_end=ie, t_open=pd.Timestamp(t_open),
                         t_end=pd.Timestamp(t[ie]), anchor_gap_min=float(g), range_span_min=float(span),
                         rh=rh, rl=rl, w=rh - rl, atr60=a60[ie + 1], r_open=op[i0], r_close=cl[ie]))
    r = pd.DataFrame(rows)
    r["contiguous"] = r.range_span_min == rng_n - 1
    return r


# ---------------------------------------------------------------- the break
def breaks(b, r, conv="close", win=BREAK_WIN, buf_k=BUF_K):
    """First break of the opening range in the window after it, by one of three conventions.

    conv='close'  a bar whose CLOSE is beyond the edge by >= buffer. The decision is that bar's
                  close, so the earliest possible fill is the NEXT bar's open. Cannot be ambiguous:
                  one close cannot be on both sides. This is the only convention used for a result.
    conv='touch'  the bar's HIGH/LOW reaches the edge+buffer. What a resting stop order does. A bar
                  can reach both edges -> ambiguous, counted and resolved from the bar's open.
                  AUDIT ONLY: common.simulate() has no stop-order entry type (see the report).
    """
    hi, lo, op, cl = b.high.values, b.low.values, b.open.values, b.close.values
    spr = b.spr.values
    out = []
    for q in r.itertuples(index=False):
        if not q.contiguous or not np.isfinite(q.atr60) or q.w <= 0:
            out.append((-1, 0, np.nan, 0)); continue
        buf = buf_k * q.atr60
        up_l, dn_l = q.rh + buf, q.rl - buf
        s, e = q.i_end + 1, min(q.i_end + 1 + win, len(b) - 2)
        j, side, amb = -1, 0, 0
        for k in range(s, e):
            if conv == "close":
                u, d = cl[k] > up_l, cl[k] < dn_l
            else:
                u, d = hi[k] + spr[k] >= up_l, lo[k] <= dn_l
            if u and d:
                amb = 1
                # resolve from the bar's open: the nearer edge is reached first
                u, d = (abs(op[k] - up_l) <= abs(op[k] - dn_l)), not (abs(op[k] - up_l) <= abs(op[k] - dn_l))
            if u:
                j, side = k, 1; break
            if d:
                j, side = k, -1; break
        out.append((j, side, (up_l if side == 1 else dn_l) if side else np.nan, amb))
    d = pd.DataFrame(out, columns=["i_brk", "side", "level", "ambig"])
    return pd.concat([r.reset_index(drop=True), d], axis=1)


# ---------------------------------------------------------------- features (catalogue conditions)
def features(b, ev):
    """Every f_* column. All of them are computable from data closed STRICTLY BEFORE the fill bar."""
    b15, bh1, bh4 = bars(SYM, "M15"), bars(SYM, "H1"), bars(SYM, "H4")
    t = b.time.values
    hi, lo, op, cl, spr = b.high.values, b.low.values, b.open.values, b.close.values, b.spr.values
    P = C.pip(SYM)
    ev = ev[ev.side != 0].reset_index(drop=True).copy()
    ib, ie = ev.i_brk.values.astype(int), ev.i_end.values.astype(int)
    sd = ev.side.values.astype(float)

    ev["t_signal"] = pd.to_datetime(t[ib]) + pd.Timedelta(minutes=1)      # close time of the break bar
    ev["t_fill_exp"] = pd.to_datetime(t[ib + 1])
    ev["ref"] = cl[ib]                                                    # price known at the decision
    ev["brk_open"] = op[ib]
    ev["brk_high"] = hi[ib]
    ev["brk_low"] = lo[ib]
    ev["nxt_open"] = op[ib + 1]
    ev["spr_fill"] = spr[ib + 1]

    # --- E/A: volatility units
    at15 = A.atr_at(SYM, "M15", ev.t_signal.values)
    ev["f_atr15"] = at15
    ev["f_w_atr"] = ev.w.values / at15
    ev["f_w_pips"] = ev.w.values / P
    ev["f_atr60"] = ev.atr60.values

    # A4 volatility level: ATR15 rank inside its own trailing year (closed M15 bars only)
    a15 = A.atr(b15, 14)
    rank = a15.rolling(365 * 96, min_periods=96 * 20).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True) \
        if False else None                                                # too slow; use a cheap rank below
    i15 = C.asof_index(b15, ev.t_signal.values)
    av = a15.values
    med = pd.Series(av).rolling(96 * 60, min_periods=96 * 5).median().values
    q80 = pd.Series(av).rolling(96 * 60, min_periods=96 * 5).quantile(0.8).values
    j15 = np.clip(i15, 0, len(av) - 1)
    ev["f_atr_vs_med"] = av[j15] / med[j15]
    ev["f_atr_hi"] = (av[j15] > q80[j15]).astype(float)                    # A4
    back = np.clip(i15 - 60, 0, len(av) - 1)
    ev["f_atr_dir"] = av[j15] / av[back]                                   # A5 expanding / contracting

    # A4/A6 the ORB-specific one: this range's width against the same session's own recent normal
    ev["f_w_vs_med20"] = np.nan
    ev["f_w_pct60"] = np.nan
    for s, g in ev.groupby("sess"):
        w = g.w.values
        m20 = pd.Series(w).shift(1).rolling(20, min_periods=10).median().values
        rk = np.full(len(w), np.nan)
        for k in range(len(w)):
            lo_k = max(0, k - 60)
            if k - lo_k >= 20:
                rk[k] = (w[lo_k:k] < w[k]).mean()
        ev.loc[g.index, "f_w_vs_med20"] = w / m20
        ev.loc[g.index, "f_w_pct60"] = rk

    # B3 minutes from the end of the range to the break
    ev["f_min_to_break"] = (ib - ie).astype(float)
    ev["f_min_from_open"] = ((t[ib] - t[ev.i_open.values.astype(int)]) / np.timedelta64(1, "m")).astype(float)
    # B1/B2 session clock
    hr = pd.to_datetime(t[ib + 1]).hour
    ev["f_hour"] = hr
    ev["f_overlap"] = ((hr >= 12) & (hr < 16)).astype(float)               # B2 London/NY overlap
    ev["f_dow"] = pd.to_datetime(t[ib + 1]).dayofweek
    dd = pd.to_datetime(t[ib + 1])
    ev["f_monthend"] = ((dd.day >= 27) | (dd.day <= 2)).astype(float)      # B6

    # A1 higher timeframe agreement, strictly from the last CLOSED HTF bar (catalogue F4)
    for tf, bb in (("h1", bh1), ("h4", bh4)):
        e = np.asarray(C.ema(bb.close, 50))
        k = np.clip(C.asof_index(bb, ev.t_signal.values), 0, len(e) - 1)
        rel = np.sign(cl[ib] - e[k])
        ev["f_%s_agree" % tf] = (rel == sd).astype(float)
        ev["f_%s_dist_atr" % tf] = (cl[ib] - e[k]) * sd / at15
    # A1 overnight trend: move from the prior 21:00 UTC close to the end of the opening range
    on = _overnight(b, ev)
    ev = pd.concat([ev, on], axis=1)
    ev["f_on_agree"] = (np.sign(ev.r_close.values - ev.on_ref.values) == sd).astype(float)

    # C1 room to run: untouched prior-day / overnight extreme ahead of the entry, in ATR
    ahead_pd = np.where(sd > 0, ev.pdh.values - cl[ib], cl[ib] - ev.pdl.values)
    ahead_on = np.where(sd > 0, ev.onh.values - cl[ib], cl[ib] - ev.onl.values)
    ev["f_room_pd_atr"] = ahead_pd / at15
    ev["f_room_on_atr"] = ahead_on / at15
    ev["f_room_pd_w"] = ahead_pd / ev.w.values
    ev["f_untouched_pd"] = (ahead_pd > 0).astype(float)                    # the level has not been taken yet
    ev["f_untouched_on"] = (ahead_on > 0).astype(float)
    # C2 nearest OPPOSING level: the prior-day extreme behind us
    beh = np.where(sd > 0, cl[ib] - ev.pdl.values, ev.pdh.values - cl[ib])
    ev["f_opp_pd_atr"] = beh / at15
    # C5 where the range sits inside the prior day's range
    pdr = (ev.pdh - ev.pdl).replace(0, np.nan)
    ev["f_pd_pos"] = ((ev.r_close - ev.pdl) / pdr).clip(-1, 2)
    ev["f_on_w_atr"] = (ev.onh - ev.onl) / at15

    # D the trigger candle itself
    rngb = (hi[ib] - lo[ib])
    ev["f_body_atr"] = np.abs(cl[ib] - op[ib]) / at15
    ev["f_body_frac"] = np.where(rngb > 0, np.abs(cl[ib] - op[ib]) / rngb, np.nan)
    ev["f_close_pos"] = np.where(rngb > 0, (cl[ib] - lo[ib]) / rngb, np.nan)   # D2, 1 = closed on the high
    ev["f_close_pos_dir"] = np.where(sd > 0, ev.f_close_pos, 1 - ev.f_close_pos)
    ev["f_disp_atr"] = (cl[ib] - ev.level.values) * sd / at15              # how far past the level it closed
    ev["f_disp_w"] = (cl[ib] - ev.level.values) * sd / ev.w.values
    ev["f_engulf"] = ((hi[ib] >= hi[ib - 1]) & (lo[ib] <= lo[ib - 1])).astype(float)   # D5
    # C3 freshness: was the edge already wicked before the break bar
    pre = np.zeros(len(ev))
    for k in range(len(ev)):
        s0, s1 = ie[k] + 1, ib[k]
        if s1 > s0:
            pre[k] = (hi[s0:s1] > ev.rh.values[k]).sum() if sd[k] > 0 else (lo[s0:s1] < ev.rl.values[k]).sum()
    ev["f_edge_pretouch"] = pre

    # F3 holes and gaps anywhere from the session open to the break
    gapm = np.zeros(len(ev))
    for k in range(len(ev)):
        seg = np.diff(t[ev.i_open.values[k]:ib[k] + 2]) / np.timedelta64(1, "m")
        gapm[k] = seg.max() if len(seg) else 1.0
    ev["f_max_hole_min"] = gapm
    ev["f_fill_is_next_minute"] = ((t[ib + 1] - t[ib]) == np.timedelta64(1, "m")).astype(float)
    return ev


def _overnight(b, ev):
    """Prior-day high/low, prior 21:00 UTC reference close, and the overnight range before the open."""
    t = b.time.values
    hi, lo, cl = b.high.values, b.low.values, b.close.values
    day = pd.to_datetime(t).normalize().values
    i0 = ev.i_open.values.astype(int)
    rows = []
    for k in range(len(ev)):
        s = i0[k]
        d0 = day[s]
        # prior day = the last calendar day with bars strictly before d0
        a = np.searchsorted(day, d0, "left")
        pb = np.searchsorted(day, day[max(a - 1, 0)], "left")
        pe = a
        pdh = hi[pb:pe].max() if pe > pb else np.nan
        pdl = lo[pb:pe].min() if pe > pb else np.nan
        on_s = max(pe - 1, 0)
        onh = hi[on_s:s].max() if s > on_s else np.nan
        onl = lo[on_s:s].min() if s > on_s else np.nan
        rows.append((pdh, pdl, cl[max(pe - 1, 0)], onh, onl))
    return pd.DataFrame(rows, columns=["pdh", "pdl", "on_ref", "onh", "onl"], index=ev.index)


# ---------------------------------------------------------------- orders -> simulate
def orders(ev, shape, fade=False, retest=False, hold=HOLD_MIN, retest_win=RETEST_WIN, anchor=None):
    """shape = ('atr', t, s) | ('w', t, s) | ('struct', r) ; one order per event.

    anchor = the price the stop and target are measured FROM. It must be known at the decision.
      'ref'   the break bar's close (default for a market entry)
      'level' the range edge itself (default for a retest limit and for a resting stop entry)
    """
    e = ev[np.isfinite(ev.f_atr15) & (ev.w > 0)].copy()
    sd = e.side.values * (-1.0 if fade else 1.0)
    if anchor is None:
        anchor = "level" if retest else "ref"
    ref = e.level.values if anchor == "level" else e.ref.values
    kind = shape[0]
    if kind == "atr":
        u = e.f_atr15.values
        tdist, sdist = shape[1] * u, shape[2] * u
    elif kind == "w":
        u = e.w.values
        tdist, sdist = shape[1] * u, shape[2] * u
    elif kind == "struct":                                                # stop on the far side of the range
        sdist = np.where(sd > 0, ref - e.rl.values, e.rh.values - ref)
        sdist = np.where(sdist > 0, sdist, e.w.values)
        tdist = shape[1] * sdist
    else:
        raise ValueError(kind)
    o = pd.DataFrame({
        "t_signal": e.t_signal.values,
        "side": sd.astype(int),
        "entry_type": "limit" if retest else "market",
        "entry": e.level.values if retest else np.nan,
        "expire": e.t_signal.values + pd.Timedelta(minutes=retest_win),
        "sl": ref - sd * sdist,
        "tp": ref + sd * tdist,
        "be_at_r": np.nan,
        "max_hold_min": float(hold),
        "anchor_px": ref,
    })
    keep = [c for c in e.columns if c.startswith(("f_", "sess", "t_open", "t_end", "rh", "rl", "w",
                                                 "level", "ref", "ambig", "i_brk", "i_end", "i_open",
                                                 "nxt_open", "spr_fill", "pdh", "pdl", "onh", "onl"))]
    for c in keep:
        if c not in o.columns:
            o[c] = e[c].values
    o["shape"] = "%s_%s" % (kind, "_".join(str(x) for x in shape[1:]))
    return o.sort_values("t_signal", kind="stable").reset_index(drop=True)


def run(ev, shape, fade=False, retest=False, hold=HOLD_MIN, one=False, anchor=None):
    o = orders(ev, shape, fade=fade, retest=retest, hold=hold, anchor=anchor)
    return C.simulate(SYM, o, one_at_a_time=one)


def filled(tr):
    return tr[tr.status == "filled"].copy()


def book(t):
    """The five numbers the brief demands, plus the rest of the owner's list."""
    t = t[t.status == "filled"] if "status" in t else t
    if len(t) == 0:
        return dict(trades=0)
    s = C.stats(t)
    gp = t.net_pips[t.net_pips > 0].sum()
    gl = -t.net_pips[t.net_pips < 0].sum()
    return dict(trades=s["trades"], per_week=s["per_week"],
                right_in_100=round(100 * (t.net_pips > 0).mean(), 2),
                PF=round(gp / gl, 3) if gl > 0 else np.inf,
                avg_net_pips=s["avg_net_pips"], usd_per_trade=round(t.usd_01.mean(), 3),
                usd_total=s["usd_per_01lot"], avg_hold_h=s["avg_hold_h"],
                maxDD_R=s["maxDD_R"], avg_R=s["avg_R"])


# ================================================================ STAGE 0 - catalogue group F
# common.simulate() has no stop-order entry type (it has 'market' and a LIMIT that fills when price
# comes back to it). A real opening-range breakout is traded with a resting BUY/SELL STOP at the range
# edge. `walk()` below is the only way to price that entry. It copies simulate()'s conventions exactly
# -- SL before TP inside a bar, never TP on the fill bar, 0.2 pip stop slippage, ask = bid + spr,
# flat before the weekend and before any gap > 6 h -- and it is checked against simulate() on the
# baseline variant (see audit() "walk vs simulate").

def walk(sym, o, fill_mode="next_open", entry_slip=0.0, tp_first=False, max_days=10, anchor_fill=False):
    """Own bar walker. fill_mode:
         'next_open'  market at the open of the bar AFTER the break bar      (= simulate baseline)
         'sig_open'   market at the break bar's OWN open  -> the known look-ahead twin
         'stop'       resting stop at `o.level`: fill = max(level, open+spr) on the break bar
       entry_slip in pips is added adversely to the fill (stop orders really do slip).
       tp_first=True flips the inside-bar tie from SL to TP, to size catalogue F5.
    """
    b = bars(sym, "M1")
    t1 = b.time.values
    op, hi, lo, cl, sp = b.open.values, b.high.values, b.low.values, b.close.values, b.spr.values
    dow = b.time.dt.dayofweek.values
    hm = (b.time.dt.hour * 60 + b.time.dt.minute).values
    weekend = (dow == 4) & (hm >= 20 * 60 + 45)
    gap_before = np.zeros(len(t1), bool)
    gap_before[:-1] = np.diff(t1) > np.timedelta64(6, "h")
    wk = np.where(weekend | gap_before)[0]
    P = C.pip(sym)
    comm = C.COMM[sym]
    slip = C.STOP_SLIP * P
    es = entry_slip * P
    res = []
    for r in o.itertuples(index=False):
        row = r._asdict()
        d = int(r.side)
        ib = int(r.i_brk)
        if fill_mode == "sig_open":
            f, px = ib, op[ib] + (sp[ib] if d == 1 else 0.0)
        elif fill_mode == "next_open":
            f, px = ib + 1, op[ib + 1] + (sp[ib + 1] if d == 1 else 0.0)
        elif fill_mode == "stop":
            f = ib
            lvl = float(r.level)
            px = max(lvl + sp[f], op[f] + sp[f]) if d == 1 else min(lvl, op[f])
        else:
            raise ValueError(fill_mode)
        px = px + d * es
        sl, tp = float(r.sl), float(r.tp)
        if anchor_fill:                   # barriers measured from the FILL, as BT21R's $15/$70 shape was
            tp = px + (float(r.tp) - float(r.anchor_px)) 
            sl = px + (float(r.sl) - float(r.anchor_px))
        risk = (px - sl) * d
        if not risk > 0 or (px - tp) * d >= 0:
            res.append({**row, "status": "invalid"})
            continue
        end = min(f + max_days * 1440, len(t1))
        if np.isfinite(r.max_hold_min):
            end = min(end, int(np.searchsorted(t1, t1[f] + np.timedelta64(int(r.max_hold_min), "m"), "right")))
        wi = wk[np.searchsorted(wk, f):]
        hard = min(end, wi[0] + 1) if len(wi) else end
        k = f
        ex = None
        while k < hard:
            if d == 1:
                sl_hit, tp_hit = lo[k] <= sl, hi[k] >= tp
            else:
                sl_hit, tp_hit = hi[k] + sp[k] >= sl, lo[k] + sp[k] <= tp
            if k == f:
                tp_hit = False                                        # never TP on the fill bar
            if sl_hit and tp_hit:
                if tp_first:
                    ex = (k, tp, "TP")
                else:
                    sl_hit, tp_hit = True, False
            if ex is None and sl_hit:
                if d == 1:
                    q = (min(sl, op[k]) - slip) if k > f else (sl - slip)
                else:
                    q = (max(sl, op[k] + sp[k]) + slip) if k > f else (sl + slip)
                ex = (k, q, "SL")
            elif ex is None and tp_hit:
                ex = (k, tp, "TP")
            if ex is not None:
                break
            k += 1
        if ex is None:
            k = hard - 1
            q = cl[k] if d == 1 else cl[k] + sp[k]
            why = "WEEKEND" if len(wi) and hard == wi[0] + 1 else ("TIME" if hard == end else "END")
            ex = (k, q, why)
        k, q, why = ex
        sh, sl_ = hi[f:k + 1], lo[f:k + 1]
        mfe = ((sh.max() - px) if d == 1 else (px - (sl_ + sp[f:k + 1]).min())) / risk
        mae = ((px - sl_.min()) if d == 1 else ((sh + sp[f:k + 1]).max() - px)) / risk
        both = 0
        if why in ("SL", "TP"):
            if d == 1:
                both = int(lo[k] <= sl and hi[k] >= tp)
            else:
                both = int(hi[k] + sp[k] >= sl and lo[k] + sp[k] <= tp)
        gross = (q - px) * d / P
        net = gross - comm
        res.append({**row, "status": "filled", "t_fill": pd.Timestamp(t1[f]), "fill": px,
                    "t_exit": pd.Timestamp(t1[k]), "exit": q, "exit_reason": why,
                    "hold_h": (t1[k] - t1[f]) / np.timedelta64(1, "h"), "risk_pips": risk / P,
                    "gross_pips": gross, "net_pips": net, "R": net / (risk / P),
                    "usd_01": net * C.USD_PIP_01[sym], "mfe_R": mfe, "mae_R": mae,
                    "both_in_exit_bar": both})
    out = pd.DataFrame(res)
    out.insert(0, "sym", sym)
    out["period"] = C.period_of(out.t_signal) if len(out) else []
    return out

def build(conv="close", rng_n=RNG_N):
    """All ORB events on the current feed, both sessions, with every f_* column."""
    b = bars(SYM, "M1")
    evs = [breaks(b, ranges(b, s, rng_n), conv) for s in SESSIONS]
    ev = pd.concat(evs, ignore_index=True)
    st = {s: dict(sessions=int((ev.sess == s).sum()),
                  contiguous=int(((ev.sess == s) & ev.contiguous).sum()),
                  broke=int(((ev.sess == s) & (ev.side != 0)).sum()),
                  ambiguous=int(((ev.sess == s) & (ev.ambig == 1)).sum())) for s in SESSIONS}
    return features(b, ev), st


def md(df, path=None, flt="%.3f"):
    d = df.copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda x: "" if pd.isna(x) else (flt % x))
    d = d.astype(str)
    cols = list(d.columns)
    s = "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    s += "".join("| " + " | ".join(r) + " |\n" for r in d.itertuples(index=False))
    s = s.rstrip("\n")
    if path:
        open(path, "w", encoding="utf-8").write(s + "\n")
    return s


SHAPE_BASE = ("atr", 1.5, 1.0)


def audit(stage="EXPLORE"):
    """STAGE 0. Catalogue group F, in depth, before any edge claim is made."""
    t0 = time.time()
    lines = []

    def say(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        lines.append(s)

    say("# AUT04 stage 0 - catalogue group F audit   feed=%s extra_spr=%.3f" % (FEED, EXTRA_SPR))

    evC, statC = build("close")
    evT, statT = build("touch")
    say("")
    say("## sessions -> breaks (whole feed)")
    say(md(pd.DataFrame([{"conv": "close", "sess": k, **v} for k, v in statC.items()]
                        + [{"conv": "touch", "sess": k, **v} for k, v in statT.items()])))

    EC, ET = A.clip(evC, stage), A.clip(evT, stage)
    say("")
    say("%s events: close-conv %d, touch-conv %d" % (stage, len(EC), len(ET)))

    # ---- F4: higher-timeframe values must come from a bar that CLOSED before the signal
    bh1, bh4 = bars(SYM, "H1"), bars(SYM, "H4")
    bad = 0
    for bb in (bh1, bh4):
        k = C.asof_index(bb, EC.t_signal.values)
        bad += int((bb.tclose.values[np.clip(k, 0, len(bb) - 1)] > EC.t_signal.values).sum())
    say("")
    say("## F4 stale-HTF check: HTF bars used whose close is AFTER the signal: %d (must be 0)" % bad)
    b15 = bars(SYM, "M15")
    k15 = C.asof_index(b15, EC.t_signal.values)
    say("   M15 ATR bar close <= signal on %d / %d rows"
        % (int((b15.tclose.values[np.clip(k15, 0, len(b15) - 1)] <= EC.t_signal.values).sum()), len(EC)))

    # ---- F3: holes and gaps
    say("")
    say("## F3 gaps and holes (close conv, %s)" % stage)
    say("   fill bar is the next clock minute: %.2f%%" % (100 * EC.f_fill_is_next_minute.mean()))
    say("   worst hole between session open and fill, minutes: p50 %.0f p99 %.0f max %.0f"
        % tuple(np.nanpercentile(EC.f_max_hole_min, [50, 99, 100])))
    say("   events with a hole > 6 h inside them: %d" % int((EC.f_max_hole_min > 360).sum()))
    say("   anchor gap (minutes from the true session open to the first bar): p50 %.0f max %.0f"
        % (EC.anchor_gap_min.median(), EC.anchor_gap_min.max()))

    # ---- F1: the fill bar
    say("")
    say("## F1 THE FILL BAR - the single highest-risk item for this strategy")
    oC = orders(EC, SHAPE_BASE)
    oT = orders(ET, SHAPE_BASE)
    sim = C.simulate(SYM, oC, one_at_a_time=False)
    w_next = walk(SYM, oC, "next_open")
    w_sig = walk(SYM, oC, "sig_open")
    oTL = orders(ET, SHAPE_BASE, anchor="level")
    w_stopT = walk(SYM, oTL, "stop", entry_slip=C.STOP_SLIP)
    w_stopT0 = walk(SYM, oTL, "stop", entry_slip=0.0)
    w_sigT = walk(SYM, oT, "sig_open")
    w_sigF = walk(SYM, oC, "sig_open", anchor_fill=True)
    w_nextF = walk(SYM, oC, "next_open", anchor_fill=True)
    rows = []
    for nm, t, note in [("simulate next_open (BASELINE)", sim, "the only convention used for a result"),
                        ("walk next_open", w_next, "walk re-implementation of the same thing"),
                        ("walk sig_open (LOOK-AHEAD TWIN)", w_sig, "fills at the break bar's own open"),
                        ("walk touch+stop order (level-anchored)", w_stopT, "resting stop at the edge, 0.2 pip slip"),
                        ("walk touch+stop order, no slip", w_stopT0, "same, slippage removed"),
                        ("walk touch+sig_open (double fake)", w_sigT, "touch break AND its own open"),
                        ("walk next_open, barriers from FILL", w_nextF, "clean, BT21R-style anchoring"),
                        ("walk sig_open, barriers from FILL", w_sigF, "the BT21R twin: bug shows in accuracy")]:
        rows.append({"convention": nm, **book(t), "note": note})
    tab = pd.DataFrame(rows)
    say(md(tab[["convention", "trades", "right_in_100", "PF", "avg_net_pips", "usd_per_trade", "avg_hold_h", "note"]]))
    base = tab.iloc[0]
    say("")
    for i in range(1, len(tab)):
        say("   %-38s  accuracy %+6.2f pts   money %+7.3f $/trade  vs the baseline"
            % (tab.convention[i], tab.right_in_100[i] - base.right_in_100, tab.usd_per_trade[i] - base.usd_per_trade))

    j = sim[sim.status == "filled"][["t_signal", "side", "exit_reason", "net_pips"]].merge(
        w_next[w_next.status == "filled"][["t_signal", "side", "exit_reason", "net_pips"]],
        on=["t_signal", "side"], suffixes=("_sim", "_walk"))
    say("")
    say("   walk vs simulate: %d matched, same exit reason on %.2f%%, max |net pips| difference %.4f"
        % (len(j), 100 * (j.exit_reason_sim == j.exit_reason_walk).mean(),
           float((j.net_pips_sim - j.net_pips_walk).abs().max())))

    say("")
    say("### the look-ahead twin, split out (close conv). dp = twin minus baseline")
    rows = []
    bn = w_next[w_next.status == "filled"].copy()
    bs = w_sig[w_sig.status == "filled"].copy()

    def pair(g, label):
        h = bs.merge(g[["t_signal", "side"]], on=["t_signal", "side"])
        return dict(split=label, trades=len(g), acc=round(100 * (g.net_pips > 0).mean(), 2),
                    acc_twin=round(100 * (h.net_pips > 0).mean(), 2),
                    dp_acc=round(100 * ((h.net_pips > 0).mean() - (g.net_pips > 0).mean()), 2),
                    usd=round(g.usd_01.mean(), 3), usd_twin=round(h.usd_01.mean(), 3),
                    dp_usd=round(h.usd_01.mean() - g.usd_01.mean(), 3))

    for key, g in bn.groupby("sess"):
        rows.append(pair(g, "sess=" + str(key)))
    for key, g in bn.groupby(bn.t_signal.dt.year):
        rows.append(pair(g, "year=%d" % key))
    q = pd.qcut(bn.f_body_atr, 4, labels=["body q1 calmest", "body q2", "body q3", "body q4 most violent"])
    for key, g in bn.groupby(q, observed=True):
        rows.append(pair(g, str(key)))
    say(md(pd.DataFrame(rows)))

    f = EC.copy()
    adv = (f.nxt_open.values - f.brk_open.values) * f.side.values / C.pip(SYM)
    say("")
    say("   free head start the twin gets, in gold pips of $0.10: mean %.2f p50 %.2f p90 %.2f  (= $%.2f a trade at 0.10 lots)"
        % (np.nanmean(adv), np.nanmedian(adv), np.nanpercentile(adv, 90), np.nanmean(adv) * C.USD_PIP_01[SYM]))
    advs = (f.nxt_open.values - f.level.values) * f.side.values / C.pip(SYM)
    say("   next-open fill minus the range edge itself, pips: mean %.2f p50 %.2f  (what a resting stop order saves)"
        % (np.nanmean(advs), np.nanmedian(advs)))

    # ---- F5
    say("")
    say("## F5 same-bar target AND stop (resolved from M1; this project has no tick data)")
    rows = []
    for nm, o_, fm in [("close conv, next_open", oC, "next_open"), ("touch conv, stop order", oT, "stop")]:
        es = C.STOP_SLIP if fm == "stop" else 0.0
        p = walk(SYM, o_, fm, entry_slip=es)
        q2 = walk(SYM, o_, fm, entry_slip=es, tp_first=True)
        pf, qf = p[p.status == "filled"], q2[q2.status == "filled"]
        rows.append(dict(variant=nm, trades=len(pf), both_barriers_same_bar=int(pf.both_in_exit_bar.sum()),
                         pct=round(100 * pf.both_in_exit_bar.mean(), 2),
                         acc_SL_first=round(100 * (pf.net_pips > 0).mean(), 2),
                         acc_TP_first=round(100 * (qf.net_pips > 0).mean(), 2),
                         usd_SL_first=round(pf.usd_01.mean(), 3), usd_TP_first=round(qf.usd_01.mean(), 3)))
    say(md(pd.DataFrame(rows)))
    say("   SL-first is the pessimistic convention and is the one used everywhere below.")

    # ---- F2 feed swap
    say("")
    say("## F2 feed disagreement")
    old = (FEED, EXTRA_SPR)
    try:
        set_feed("dukafull", 0.0)
        _MEMO.clear()
        ev2, st2 = build("close")
        E2 = A.clip(ev2, stage)
        s2 = C.simulate(SYM, orders(E2, SHAPE_BASE), one_at_a_time=False)
        b2 = bars(SYM, "M1")
        say("   data_duka_full gold: %d M1 bars, %s -> %s, median spread %.3f price units (= $%.2f round trip at 0.10 lots)"
            % (len(b2), b2.time.min().date(), b2.time.max().date(), b2.spr.median(), b2.spr.median() / C.pip(SYM)))
        say(md(pd.DataFrame([{"feed": "duka (+%.2f flat spread)" % old[1], **book(sim)},
                             {"feed": "duka_full (real per-minute spread)", **book(s2)}])))
        shared = EC[["t_signal", "side"]].merge(E2[["t_signal", "side"]], on=["t_signal", "side"])
        say("   identical (t_signal, side) on both feeds: %d of %d duka events (%.1f%%); duka_full has %d events in %s"
            % (len(shared), len(EC), 100 * len(shared) / max(len(EC), 1), len(E2), stage))
        say("   duka_full spread by year (price units, median):")
        g2 = ev2[ev2.side != 0].copy()
        say(md(pd.DataFrame({"year": g2.t_signal.dt.year, "spr_fill": g2.spr_fill})
               .groupby("year").spr_fill.median().round(3).reset_index()))
    finally:
        set_feed(*old)
        _MEMO.clear()

    # ---- F6 era drift
    say("")
    say("## F6 era drift - does the opening range mean the same thing across the years")
    g = evC[evC.side != 0].copy()
    g["year"] = g.t_signal.dt.year
    t = g.groupby("year").agg(events=("w", "size"), gold_px=("ref", "median"), width_usd=("w", "median"),
                              width_pips=("f_w_pips", "median"), width_over_atr=("f_w_atr", "median"),
                              atr15=("f_atr15", "median"), min_to_break=("f_min_to_break", "median")).round(3)
    say(md(t.reset_index()))
    say("")
    say("(stage 0 took %.0f s)" % (time.time() - t0))
    open(os.path.join(OUT, "stage0_audit.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    evC.to_parquet(os.path.join(OUT, "events_close.parquet"), index=False)
    evT.to_parquet(os.path.join(OUT, "events_touch.parquet"), index=False)
    return evC, evT



# ================================================================ STAGES 1-3
# Everything below is new work (stage 0 / group F was already on disk before this run).
# Design notes, so nothing is hidden:
#   * Every result uses the SAME convention stage 0 blessed: conv="close" breaks, a market order at
#     the open of the NEXT M1 bar, barriers anchored on the break bar's CLOSE ("ref"), SL resolved
#     before TP inside a bar. anchor="level" and walk(fill_mode="stop") are used only where the
#     text says so, and the look-ahead twin is always walk(fill_mode="sig_open") against
#     walk(fill_mode="next_open") on the SAME order book, so the only thing that differs is the bar.
#   * Bars come from the module wrapper, which is C.bars screened by A.screen_bars with the gold
#     spread injected; A.safe_bars("XAUUSD","M1") returns an identical frame (checked).

CACHE = {}


def load_events(conv="close"):
    """The cached event tables from stage 0. Rebuilt only if the parquet is missing."""
    if conv in CACHE:
        return CACHE[conv]
    f = os.path.join(OUT, "events_%s.parquet" % conv)
    if os.path.exists(f):
        ev = pd.read_parquet(f)
    else:
        ev, _ = build(conv)
        ev.to_parquet(f, index=False)
    CACHE[conv] = ev
    return ev


def _force(ev, side):
    e = ev.copy()
    e["side"] = int(side)
    return e


def _coin(ev, seed=7):
    e = ev.copy()
    r = np.random.default_rng(seed)
    e["side"] = r.choice([1, -1], size=len(e))
    return e


def split(tr):
    """book() plus the mandatory buy/sell split."""
    t = filled(tr)
    d = dict(book(t))
    for nm, sd in (("buy", 1), ("sell", -1)):
        g = t[t.side == sd]
        d["%s_trades" % nm] = len(g)
        if len(g):
            gp = g.net_pips[g.net_pips > 0].sum()
            gl = -g.net_pips[g.net_pips < 0].sum()
            d["%s_right_in_100" % nm] = round(100 * (g.net_pips > 0).mean(), 2)
            d["%s_profit_factor" % nm] = round(gp / gl, 3) if gl > 0 else None
            d["%s_usd_per_trade" % nm] = round(g.usd_01.mean(), 3)
        else:
            d["%s_right_in_100" % nm] = None
            d["%s_profit_factor" % nm] = None
            d["%s_usd_per_trade" % nm] = None
    return d


def hours_of(ev):
    return sorted(int(h) for h in pd.to_datetime(ev.t_signal).dt.hour.unique())


def luck(ev, stage, shape, hold=HOLD_MIN, n=20000, seeds=(0, 1, 2, 3, 4)):
    """Mandatory control 1: shape-matched, hour-matched AND hold-matched luck bar, 5 seeds."""
    hrs = hours_of(ev)
    acc, usd = [], []
    for sd in seeds:
        lb = filled(A.luck_bar(SYM, n, shape[1], shape[2], stage, seed=sd, hours=hrs, max_hold_min=hold))
        acc.append(100 * (lb.net_pips > 0).mean())
        usd.append(lb.usd_01.mean())
    return dict(n=n, seeds=list(seeds), hours=hrs, max_hold_min=hold,
                right_in_100=round(float(np.mean(acc)), 2),
                seed_band_points=round(float(max(acc) - min(acc)), 2),
                seed_values=[round(x, 2) for x in acc],
                usd_per_trade=round(float(np.mean(usd)), 3),
                shape_matched=True, hour_matched=True, hold_matched=True)


def both_ways(ev, stage, shape, hold=HOLD_MIN, fade=False, anchor=None, coin_seeds=(7, 8, 9)):
    """Mandatory control 2: always-long / always-short / coin flip at the rule's own minutes."""
    base = filled(run(ev, shape, fade=fade, hold=hold, anchor=anchor))
    L = filled(run(_force(ev, 1), shape, hold=hold, anchor=anchor))
    S = filled(run(_force(ev, -1), shape, hold=hold, anchor=anchor))
    cu, ca = [], []
    for sd in coin_seeds:
        c = filled(run(_coin(ev, sd), shape, hold=hold, anchor=anchor))
        cu.append(c.usd_01.mean())
        ca.append(100 * (c.net_pips > 0).mean())
    j = L[["t_signal", "usd_01"]].merge(S[["t_signal", "usd_01"]], on="t_signal", suffixes=("_L", "_S"))
    j = j.merge(base[["t_signal", "side"]], on="t_signal")
    better = np.where(j.usd_01_L > j.usd_01_S, 1, np.where(j.usd_01_S > j.usd_01_L, -1, 0))
    tie = better == 0
    picked = (j.side.values == better) & ~tie
    return dict(trades=len(base),
                rule_right_in_100=round(100 * (base.net_pips > 0).mean(), 2),
                rule_usd=round(base.usd_01.mean(), 3),
                all_long_right_in_100=round(100 * (L.net_pips > 0).mean(), 2),
                all_long_usd=round(L.usd_01.mean(), 3),
                all_short_right_in_100=round(100 * (S.net_pips > 0).mean(), 2),
                all_short_usd=round(S.usd_01.mean(), 3),
                coin_right_in_100=round(float(np.mean(ca)), 2),
                coin_usd=round(float(np.mean(cu)), 3),
                coin_seeds=list(coin_seeds),
                picks_better_side_in_100=round(100 * picked.sum() / max((~tie).sum(), 1), 2),
                ties_pct=round(100 * tie.mean(), 2),
                rule_minus_coin_usd=round(base.usd_01.mean() - float(np.mean(cu)), 3),
                rule_minus_coin_points=round(100 * (base.net_pips > 0).mean() - float(np.mean(ca)), 2))


def lookahead(ev, shape, hold=HOLD_MIN, fade=False, anchor=None):
    """Mandatory control 4: the look-ahead twin, in BOTH points and dollars, with its sign."""
    o = orders(ev, shape, fade=fade, hold=hold, anchor=anchor)
    cl = filled(walk(SYM, o, "next_open"))
    tw = filled(walk(SYM, o, "sig_open"))
    clF = filled(walk(SYM, o, "next_open", anchor_fill=True))
    twF = filled(walk(SYM, o, "sig_open", anchor_fill=True))
    f = ev[np.isfinite(ev.f_atr15) & (ev.w > 0)]
    head = (f.nxt_open.values - f.brk_open.values) * f.side.values / C.pip(SYM)
    return dict(
        clean_fill_mode="next_open", clean_anchor=("level" if anchor == "level" else "ref"),
        clean_right_in_100=round(100 * (cl.net_pips > 0).mean(), 2),
        clean_usd_per_trade=round(cl.usd_01.mean(), 3),
        twin_fill_mode="sig_open",
        twin_right_in_100=round(100 * (tw.net_pips > 0).mean(), 2),
        twin_usd_per_trade=round(tw.usd_01.mean(), 3),
        lookahead_points=round(100 * (tw.net_pips > 0).mean() - 100 * (cl.net_pips > 0).mean(), 2),
        lookahead_usd=round(tw.usd_01.mean() - cl.usd_01.mean(), 3),
        twin_barriers_from_fill_points=round(100 * (twF.net_pips > 0).mean() - 100 * (clF.net_pips > 0).mean(), 2),
        twin_barriers_from_fill_usd=round(twF.usd_01.mean() - clF.usd_01.mean(), 3),
        free_head_start_pips_mean=round(float(np.nanmean(head)), 2),
        free_head_start_usd_mean=round(float(np.nanmean(head)) * C.USD_PIP_01[SYM], 2))


def measure(ev, stage, shape=SHAPE_BASE, fade=False, retest=False, hold=HOLD_MIN,
            with_luck=True, luck_n=20000, luck_seeds=(0, 1, 2, 3, 4)):
    A.gate(stage)
    e = A.clip(ev, stage)
    tr = run(e, shape, fade=fade, retest=retest, hold=hold)
    d = split(tr)
    d["stage"] = stage
    if shape[0] == "atr":
        d["breakeven_needed"] = round(A.breakeven_rate(SYM, float(np.nanmedian(e.f_atr15)),
                                                       shape[1], shape[2]), 2)
    else:
        d["breakeven_needed"] = None
    if with_luck and len(filled(tr)):
        sh = shape if shape[0] == "atr" else ("atr", 1.5, 1.0)
        lb = luck(e, stage, sh, hold=hold, n=luck_n, seeds=luck_seeds)
        d["luck_bar"] = lb["right_in_100"]
        d["luck_bar_usd"] = lb["usd_per_trade"]
        d["luck_bar_seed_band_points"] = lb["seed_band_points"]
        d["vs_luck_bar_points"] = round(d["right_in_100"] - lb["right_in_100"], 2)
        d["vs_luck_bar_usd"] = round(d["usd_per_trade"] - lb["usd_per_trade"], 3)
        d["_luck"] = lb
    return d, tr


# ---------------------------------------------------------------- the condition scan
TALLY = []


def cond_scan(tr, conds, label=""):
    """Score each named condition three ways (brief method step 2). Appends to TALLY."""
    t = filled(tr)
    rows = []
    for cid, name, expr in conds:
        try:
            m = np.asarray(expr(t), dtype=bool)
        except Exception as e:                                            # noqa: BLE001
            rows.append(dict(cat=cid, cond=name, trades=0, note="ERR %s" % e))
            continue
        if m.sum() < 25 or (~m).sum() < 25:
            rows.append(dict(cat=cid, cond=name, trades=int(m.sum()), note="too few"))
            continue
        on, off = t[m], t[~m]
        rows.append(dict(cat=cid, cond=name, trades=int(m.sum()),
                         acc_on=round(100 * (on.net_pips > 0).mean(), 2),
                         acc_off=round(100 * (off.net_pips > 0).mean(), 2),
                         usd_on=round(on.usd_01.mean(), 3), usd_off=round(off.usd_01.mean(), 3),
                         d_acc=round(100 * ((on.net_pips > 0).mean() - (off.net_pips > 0).mean()), 2),
                         d_usd=round(on.usd_01.mean() - off.usd_01.mean(), 3),
                         kept_pct=round(100 * m.mean(), 1)))
        TALLY.append((label, cid, name))
    return pd.DataFrame(rows)


def CONDS():
    """The catalogue conditions this agent spends its budget on. Median split unless boolean."""
    def hi(c):
        return lambda t: t[c].values > np.nanmedian(t[c].values)

    def tr_(c):
        return lambda t: t[c].values > 0.5
    return [
        ("A1", "H1 50-EMA agrees with the break", tr_("f_h1_agree")),
        ("A1", "H4 50-EMA agrees with the break", tr_("f_h4_agree")),
        ("A1", "overnight drift agrees with the break", tr_("f_on_agree")),
        ("A3", "far from the H1 50-EMA (top half)", hi("f_h1_dist_atr")),
        ("A3", "far from the H4 50-EMA (top half)", hi("f_h4_dist_atr")),
        ("A4", "ATR15 in the top fifth of its own 60-day history", tr_("f_atr_hi")),
        ("A4", "ATR15 above its own 60-day median (top half)", hi("f_atr_vs_med")),
        ("A5", "ATR expanding over the last 15 h", hi("f_atr_dir")),
        ("A6", "opening range WIDE in ATR terms (top half)", hi("f_w_atr")),
        ("A6", "range wide vs this session's own last 20 (top half)", hi("f_w_vs_med20")),
        ("A6", "range in the top half of its own 60-session rank", hi("f_w_pct60")),
        ("B1", "session = New York (vs London)", lambda t: t.sess.values == "NY"),
        ("B2", "London/NY overlap 12:00-16:00 UTC", tr_("f_overlap")),
        ("B3", "broke LATE (minutes to break, top half)", hi("f_min_to_break")),
        ("B3", "late in the session (minutes from open, top half)", hi("f_min_from_open")),
        ("B4", "entry hour in AUT09's blocked list",
         lambda t: pd.to_datetime(t.t_signal).dt.hour.isin(A.BAD_HOURS_UTC).values),
        ("B6", "month-end window", tr_("f_monthend")),
        ("C1", "room to run to the prior-day extreme (top half)", hi("f_room_pd_atr")),
        ("C1", "prior-day extreme ahead still untouched", tr_("f_untouched_pd")),
        ("C1", "room to the overnight extreme (top half)", hi("f_room_on_atr")),
        ("C2", "opposing prior-day level far behind (top half)", hi("f_opp_pd_atr")),
        ("C3", "the range edge was already wicked before the break",
         lambda t: t.f_edge_pretouch.values > 0),
        ("C5", "upper half of the prior day's range", lambda t: t.f_pd_pos.values > 0.5),
        ("D1", "break-bar body large in ATR (top half)", hi("f_body_atr")),
        ("D2", "break bar closed near its own extreme (top half)", hi("f_close_pos_dir")),
        ("D5", "break bar engulfed the one before", tr_("f_engulf")),
        ("D-x", "closed far past the level, in ATR (top half)", hi("f_disp_atr")),
        ("D-x", "closed far past the level, in range widths (top half)", hi("f_disp_w")),
    ]


# ---------------------------------------------------------------- the shape question
def ladder(ev, stage, hold=HOLD_MIN, fade=False, unit="atr"):
    A.gate(stage)
    e = A.clip(ev, stage)
    rows = []
    for t_, s_ in A.ATR_LADDER:
        tr = filled(run(e, (unit, t_, s_), fade=fade, hold=hold))
        if len(tr) == 0:
            continue
        gp = tr.net_pips[tr.net_pips > 0].sum()
        gl = -tr.net_pips[tr.net_pips < 0].sum()
        rows.append(dict(unit=unit, tgt=t_, stop=s_, trades=len(tr),
                         right_in_100=round(100 * (tr.net_pips > 0).mean(), 2),
                         PF=round(gp / gl, 3) if gl > 0 else None,
                         usd_per_trade=round(tr.usd_01.mean(), 3),
                         avg_hold_h=round(tr.hold_h.mean(), 2)))
    return pd.DataFrame(rows).sort_values("usd_per_trade", ascending=False).reset_index(drop=True)


def jdump(name, obj):
    import json

    def enc(x):
        if isinstance(x, (np.floating, np.integer)):
            return float(x)
        return str(x)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, default=enc)
    print("wrote", name)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if cmd == "audit":
        audit()
