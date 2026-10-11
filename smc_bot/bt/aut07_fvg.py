"""AUT07 -- fair value gap fill (SMC).  Autopsy agent 7.  Governing doc: bt/AUTOPSY_BRIEF.md.

Skeleton: a three-candle imbalance leaves an unfilled gap (bull: low[k] > high[k-2], gap =
(high[k-2], low[k]), known only when candle 3 CLOSES).  Price returns to fill it; we enter on the
reaction inside the gap.

Run from the TTXT root on the Dukascopy feed:
    set SMC_DATASET=duka
    .venv\\Scripts\\python.exe smc_bot\\bt\\aut07_fvg.py stage0
    .venv\\Scripts\\python.exe smc_bot\\bt\\aut07_fvg.py explore
    .venv\\Scripts\\python.exe smc_bot\\bt\\aut07_fvg.py ladder
    .venv\\Scripts\\python.exe smc_bot\\bt\\aut07_fvg.py final       (VALIDATE + STRESS + examples)

Nothing here edits common.py / smc_core.py / autopsy_lib.py.  `C.bars` is wrapped at RUNTIME with
a memo cache and an optional synthetic spread (STRESS only); the files on disk are untouched.
EXAM (2021+) is never requested from A.gate / A.clip.
"""
import os
import sys
import time
import heapq
import argparse
import numpy as np
import pandas as pd

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common as C                      # noqa: E402
import autopsy_lib as A                 # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT07_fvg")
CACHE = os.path.join(OUT, "_cache")
os.makedirs(CACHE, exist_ok=True)
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 90)

TF = "M15"
STRUCT_N = 5
MAX_AGE = 48                 # a gap stays tradable 48 M15 bars (12 h) after candle 3 closes
MIN_ATR = 0.05               # generate wide; all size filtering is done on f_w_atr
GOLD = "XAUUSD"
CORE = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "XAUUSD"]
BASE = dict(depth=0.0, sign=1, tgt=1.5, stop=1.0, mode="react", stop_kind="atr", max_hold_h=24.0)

# --------------------------------------------------------------------------- bars (memo + stress)
# AUT07 correction 2026-10-08: the original draft of this file wrapped C.bars DIRECTLY, which meant
# it never went through autopsy_lib.safe_bars() and therefore (a) did not drop the verified corrupt
# Dukascopy bars and (b) traded gold at ZERO spread, because the duka gold spread column is zero on
# all 5,526,570 bars.  Everything now routes through safe_bars().  C.bars is restored around the
# call so safe_bars reads the real loader instead of recursing into this wrapper.
_RAW_BARS = C.bars
_MEMO = {}
EXTRA_SPREAD_PIPS = 0.0
SCREEN_LOG = {}            # sym -> dict(dropped=, spreads_repaired=)


def bars(sym, tf):
    key = (sym, tf, EXTRA_SPREAD_PIPS)
    if key not in _MEMO:
        C.bars = _RAW_BARS
        try:
            raw = _RAW_BARS(sym, tf)
            clean, ndrop = A.screen_bars(raw)
            b, nspr = A._repair_spread(clean, sym)
        finally:
            C.bars = bars
        lg = SCREEN_LOG.setdefault(sym, {})
        lg[tf] = dict(bars=len(raw), dropped=int(ndrop), spreads_repaired=int(nspr))
        if EXTRA_SPREAD_PIPS:
            b = b.copy()
            b["spr"] = b.spr.values + EXTRA_SPREAD_PIPS * C.pip(sym)
        _MEMO[key] = b.reset_index(drop=True)
    return _MEMO[key]


C.bars = bars


def set_costs(extra_spread_pips=0.0, stop_slip_pips=0.2, comm_mult=1.0):
    global EXTRA_SPREAD_PIPS
    EXTRA_SPREAD_PIPS = extra_spread_pips
    C.STOP_SLIP = stop_slip_pips
    if not hasattr(set_costs, "_c0"):
        set_costs._c0 = dict(C.COMM)
    for k, v in set_costs._c0.items():
        C.COMM[k] = round(v * comm_mult, 4)


# --------------------------------------------------------------------------- small helpers
def ns(x):
    return np.asarray(pd.to_datetime(x).values, dtype="datetime64[ns]")


def nearest_untouched(h, l, is_sw, N, up=True):
    """For each bar j: the nearest PRIOR swing level above (up=True) / below that price has not yet
    traded through.  A swing is usable only N bars after it printed; it dies the moment a bar's
    range reaches it.  One pass, monotone heap -- this is catalogue C1/C2 done exactly.
    """
    n = len(h)
    out = np.full(n, np.nan)
    heap = []
    for j in range(n):
        s = j - N
        if s >= 0 and is_sw[s]:
            heapq.heappush(heap, (h[s] if up else -l[s]))
        if up:
            while heap and heap[0] <= h[j]:
                heapq.heappop(heap)
        else:
            while heap and -heap[0] >= l[j]:
                heapq.heappop(heap)
        if heap:
            out[j] = heap[0] if up else -heap[0]
    return out


def first_touch_m1(m1, t_from, t_to, level, approach):
    """First M1 bar index in [t_from, t_to) whose range reaches `level`.  approach +1 = price comes
    DOWN onto the level (low <= level); -1 = comes UP (ask = high+spread >= level).  -1 if never."""
    t1 = m1.time.values
    lo, hi, sp = m1.low.values, m1.high.values, m1.spr.values
    a = np.searchsorted(t1, ns(t_from), "left")
    e = np.searchsorted(t1, ns(t_to), "left")
    out = np.full(len(level), -1, np.int64)
    for i in range(len(level)):
        s, en = int(a[i]), int(min(e[i], len(t1)))
        if en <= s:
            continue
        hit = (lo[s:en] <= level[i]) if approach[i] > 0 else ((hi[s:en] + sp[s:en]) >= level[i])
        if hit.any():
            out[i] = s + int(np.argmax(hit))
    return out


# --------------------------------------------------------------------------- the gap table
def gap_table(sym, tf=TF, min_atr=MIN_ATR, max_age=MAX_AGE, use_cache=True):
    """Every FVG on `tf` with every condition computable at the CLOSE of candle 3.  Nothing in
    here reads a bar that has not closed by t_created."""
    f = os.path.join(CACHE, "gaps_%s_%s_%g_%d.parquet" % (sym, tf, min_atr, max_age))
    if use_cache and os.path.exists(f):
        return pd.read_parquet(f)
    b = bars(sym, tf)
    h, l, o, c = b.high.values, b.low.values, b.open.values, b.close.values
    tc = b.tclose.values
    bull, bear = C.fvgs(b, min_atr)
    kb, ks = np.where(bull)[0], np.where(bear)[0]
    k = np.concatenate([kb, ks])
    lo = np.concatenate([h[kb - 2], h[ks]])
    hi = np.concatenate([l[kb], l[ks - 2]])
    d = np.concatenate([np.ones(len(kb), np.int8), -np.ones(len(ks), np.int8)])
    srt = np.argsort(k, kind="stable")
    k, lo, hi, d = k[srt], lo[srt], hi[srt], d[srt]
    keep = k >= 25                                             # need room for the lookbacks
    k, lo, hi, d = k[keep], lo[keep], hi[keep], d[keep]

    at15 = A.atr(b, 14).values
    good = np.isfinite(at15[k]) & (at15[k] > 0) & (hi > lo)
    k, lo, hi, d = k[good], lo[good], hi[good], d[good]
    AT = at15[k]
    tf_min = C.TF_MIN.get(tf, 1)

    g = pd.DataFrame(dict(sym=sym, k=k, dir=d, lo=lo, hi=hi, atr=AT))
    g["t_created"] = pd.to_datetime(tc[k])
    g["t_expire"] = g.t_created + pd.Timedelta(minutes=tf_min * max_age)

    # ---- geometry: gap width, displacement leg, candle 3 (C / D group)
    g["f_w_atr"] = (hi - lo) / AT
    g["f_w_pips"] = (hi - lo) / C.pip(sym)
    leg_hi = pd.Series(h).rolling(3).max().values[k]
    leg_lo = pd.Series(l).rolling(3).min().values[k]
    g["f_leg_atr"] = (leg_hi - leg_lo) / AT
    rng3 = np.where(h[k] - l[k] > 0, h[k] - l[k], np.nan)
    g["f_body3_atr"] = np.abs(c[k] - o[k]) / AT
    g["f_clspos"] = np.where(d == 1, (c[k] - l[k]) / rng3, (h[k] - c[k]) / rng3)
    g["f_engulf"] = ((h[k] >= h[k - 1]) & (l[k] <= l[k - 1])).astype(np.int8)
    g["f_dirbody"] = (np.sign(c[k] - o[k]) == d).astype(np.int8)
    g["f_w_over_leg"] = g.f_w_atr.values / np.where(g.f_leg_atr.values > 0, g.f_leg_atr.values, np.nan)

    # ---- F3: was the gap printed across MISSING bars?  (a feed hole, not an imbalance)
    hole_min = (tc[k] - tc[k - 3]) / np.timedelta64(1, "m") - 3 * tf_min
    g["f_hole_min"] = hole_min
    g["f_hole"] = (hole_min > 1).astype(np.int8)

    # ---- B group: the hour the gap was BORN (the thin-liquidity question)
    th = g.t_created.dt
    g["f_born_h"] = th.hour.values.astype(np.int16)
    g["f_born_dow"] = th.dayofweek.values.astype(np.int16)
    g["f_born_thin"] = th.hour.isin([21, 22, 23, 0, 1, 2]).values.astype(np.int8)
    g["f_born_ldn"] = th.hour.isin([7, 8, 9, 10, 11]).values.astype(np.int8)
    g["f_born_ny"] = th.hour.isin([12, 13, 14, 15, 16]).values.astype(np.int8)
    g["f_born_ovl"] = th.hour.isin([13, 14, 15]).values.astype(np.int8)

    # ---- A group: volatility level / direction / stretch, all from closed M15 bars
    atr_long = pd.Series(at15).rolling(96 * 20, min_periods=96 * 5).median().values
    g["f_atr_rel"] = AT / atr_long[k]
    prev = at15[np.maximum(k - 20, 0)]
    g["f_atr_dir"] = AT / np.where(np.isfinite(prev) & (prev > 0), prev, np.nan)
    e50 = C.ema(c, 50)
    g["f_m15_ema_agree"] = (np.sign(c[k] - e50[k]) == d).astype(np.int8)
    g["f_m15_ema_dist"] = np.abs(c[k] - e50[k]) / AT

    # ---- A1 / A2 / C1 / C2: higher timeframes, strictly from the last CLOSED HTF bar
    asof_ok = True
    for htf in ("H1", "H4"):
        bh = bars(sym, htf)
        stt = C.structure(bh, STRUCT_N)
        j = C.asof_index(bh, g.t_created.values)
        ok = j >= 0
        jj = np.clip(j, 0, len(bh) - 1)
        asof_ok = asof_ok and bool(np.all(bh.tclose.values[jj[ok]] <= g.t_created.values[ok]))
        tr = np.where(ok, stt["trend"][jj], 0)
        g["f_%s_trend" % htf.lower()] = tr.astype(np.int8)
        g["f_%s_agree" % htf.lower()] = (tr == d).astype(np.int8)
        ch = bh.close.values
        eh = C.ema(ch, 50)
        g["f_%s_ema_agree" % htf.lower()] = np.where(ok, np.sign(ch[jj] - eh[jj]) == d, False).astype(np.int8)
        if htf == "H1":
            up = nearest_untouched(bh.high.values, bh.low.values, stt["is_sh"], STRUCT_N, True)
            dn = nearest_untouched(bh.high.values, bh.low.values, stt["is_sl"], STRUCT_N, False)
            g["_lvl_up"] = np.where(ok, up[jj], np.nan)
            g["_lvl_dn"] = np.where(ok, dn[jj], np.nan)
            ev = np.maximum.accumulate(np.where(stt["evt"] != 0, np.arange(len(bh)), -1))
            g["f_h1_evt_age"] = np.where(ok & (ev[jj] >= 0), jj - ev[jj], np.nan)
    g.attrs["asof_ok"] = asof_ok

    # ---- C5: where the gap sits inside yesterday's range
    day = b.time.dt.floor("D")
    dh = b.groupby(day).high.max().shift(1)
    dl = b.groupby(day).low.min().shift(1)
    pdh = dh.reindex(day.values).values[k]
    pdl = dl.reindex(day.values).values[k]
    mid = (lo + hi) / 2.0
    span = np.where(pdh - pdl > 0, pdh - pdl, np.nan)
    g["f_pdr_pos"] = (mid - pdl) / span
    g["f_pdh_atr"] = (pdh - mid) / AT
    g["f_pdl_atr"] = (mid - pdl) / AT
    g.to_parquet(f, index=False)
    return g


# --------------------------------------------------------------------------- order building
def entry_level(g, depth):
    """depth 0 = near (proximal) gap edge, 0.5 = half filled, 1.0 = gap fully filled."""
    lo, hi, d = g.lo.values, g.hi.values, g["dir"].values
    w = hi - lo
    return np.where(d == 1, hi - w * depth, lo + w * depth)


ORD_COLS = ["t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r",
            "max_hold_min", "k", "dir", "lo", "hi", "atr", "t_created", "lvl", "t_touch"]


def build_orders(g, depth=0.0, sign=1, tgt=1.5, stop=1.0, mode="react", stop_kind="atr",
                 max_hold_h=24.0, be_at_r=np.nan, confirm=0, buf=0.1):
    """One order per gap.

    mode 'react': MARKET at the close of the first M1 bar that trades to the entry level (the
        brief's "enter on the reaction inside the gap").  Symmetric, so fade is testable.
    mode 'touch': a resting LIMIT at the level; the touch IS the fill.  Better price, but it only
        fills when price keeps coming at you (the limit-entry selection trap, BT1/BT4).
    stop_kind 'atr': stop = stop x ATR from the entry level.
    stop_kind 'gap': stop beyond the FAR gap edge + buf x ATR (structural: if the gap is fully
        erased the imbalance is gone).  `stop` is then ignored.
    confirm: require the touching M1 bar to CLOSE back out of the gap (a reaction, not a pass).
    """
    sym = g.sym.iloc[0]
    d = (g["dir"].values * sign).astype(np.int8)
    lvl = entry_level(g, depth)
    at = g.atr.values
    far = np.where(g["dir"].values == 1, g.lo.values, g.hi.values)
    if stop_kind == "gap":
        sl = np.where(d == 1, np.minimum(lvl, far) - buf * at, np.maximum(lvl, far) + buf * at)
    else:
        sl = lvl - d * stop * at
    o = pd.DataFrame(dict(side=d, entry_type="market", entry=np.nan, sl=sl, tp=lvl + d * tgt * at,
                          be_at_r=be_at_r, max_hold_min=float(max_hold_h) * 60.0))
    for col in ("k", "dir", "lo", "hi", "atr", "t_created"):
        o[col] = g[col].values
    o["lvl"] = lvl
    if mode == "touch":
        o["entry_type"] = "limit"
        o["entry"] = lvl
        o["expire"] = g.t_expire.values
        o["t_signal"] = g.t_created.values
        o["t_touch"] = pd.NaT
        return o
    m1 = bars(sym, "M1")
    ti = first_touch_m1(m1, g.t_created.values, g.t_expire.values, lvl, g["dir"].values)
    ok = ti >= 0
    if confirm:
        cl = m1.close.values
        idx = np.clip(ti, 0, len(cl) - 1)
        ok = ok & np.where(g["dir"].values == 1, cl[idx] > lvl, cl[idx] < lvl)
    o = o[ok].reset_index(drop=True)
    ti = ti[ok]
    o["t_touch"] = pd.to_datetime(m1.time.values[ti])
    o["t_signal"] = pd.to_datetime(m1.tclose.values[ti])
    o["expire"] = o.t_signal + pd.Timedelta(minutes=1)
    return o


def run(sym, orders, stage, seq=False):
    A.gate(stage)
    if len(orders) == 0:
        return pd.DataFrame()
    o = A.clip(orders, stage, "t_signal")
    if len(o) == 0:
        return pd.DataFrame()
    o = o[[c for c in ORD_COLS if c in o.columns]].sort_values("t_signal", kind="stable").reset_index(drop=True)
    return C.simulate(sym, o, one_at_a_time=seq, cancel_limit_if_tp_first=False)


def trades(sym, stage, g=None, seq=False, **kw):
    """Gaps -> orders -> filled trades, with the gap features merged back on (sym, k)."""
    if g is None:
        g = gap_table(sym)
    o = build_orders(g, **kw)
    r = run(sym, o, stage, seq=seq)
    if len(r) == 0:
        return r
    r = r[r.status == "filled"].copy()
    if len(r) == 0:
        return r
    fcols = ["k"] + [c for c in g.columns if c.startswith("f_")] + ["_lvl_up", "_lvl_dn", "t_expire"]
    r = r.merge(g[fcols], on="k", how="left", suffixes=("", "_g"))
    # ---- features that only exist once the order has filled (still causal: known at the fill)
    r["f_age_bars"] = (r.t_fill - r.t_created) / pd.Timedelta(minutes=C.TF_MIN[TF])
    r["f_age_min"] = (r.t_fill - r.t_created) / pd.Timedelta(minutes=1)
    r["f_fill_h"] = r.t_fill.dt.hour.astype(np.int16)
    r["f_fill_dow"] = r.t_fill.dt.dayofweek.astype(np.int16)
    r["f_same_sess"] = (r.t_fill.dt.floor("D") == r.t_created.dt.floor("D")).astype(np.int8)
    fwd = np.where(r["dir"].values == 1, r._lvl_up.values - r.lvl.values, r.lvl.values - r._lvl_dn.values)
    back = np.where(r["dir"].values == 1, r.lvl.values - r._lvl_dn.values, r._lvl_up.values - r.lvl.values)
    r["f_room_atr"] = fwd / r.atr.values
    r["f_back_atr"] = back / r.atr.values
    r["f_stop_atr"] = r.risk_pips.values * C.pip(sym) / r.atr.values
    r["year"] = r.t_fill.dt.year
    return r


def pool(syms, stage, seq=False, **kw):
    out = []
    for s in syms:
        t = trades(s, stage, seq=seq, **kw)
        if len(t):
            out.append(t)
    return pd.concat(out, ignore_index=True, sort=False) if out else pd.DataFrame()


# --------------------------------------------------------------------------- reporting
def st(t, label=""):
    if len(t) == 0:
        return dict(group=label, trades=0)
    s = C.stats(t)
    gu = (t.gross_pips * t.sym.map(C.USD_PIP_01)).mean()
    return dict(group=label, trades=s["trades"], per_week=s["per_week"], win_pct=s["win_pct"],
                PF=s["PF"], avg_R=s["avg_R"], avg_net_pips=s["avg_net_pips"],
                usd_per_trade=round(float(t.usd_01.mean()), 3), gross_usd=round(float(gu), 3),
                usd_total=s["usd_per_01lot"], maxDD_R=s["maxDD_R"], avg_hold_h=s["avg_hold_h"])


def luck(syms, t, tgt, stop, stage, n=4000, seed=7):
    """A.luck_bar on the same stage, matched to the hours the real signal actually fired."""
    rows = []
    for s in syms:
        tt = t[t.sym == s] if "sym" in t else t
        if len(tt) == 0:
            continue
        hours = sorted(set(tt.t_fill.dt.hour.tolist()))
        try:
            lb = A.luck_bar(s, n, tgt, stop, stage, seed=seed, hours=hours)
        except Exception as e:                                   # pragma: no cover
            rows.append(dict(sym=s, err=str(e)[:40]))
            continue
        lb = lb[lb.status == "filled"]
        if len(lb) == 0:
            continue
        rows.append(dict(sym=s, n=len(lb), luck_win=round(100 * (lb.R > 0).mean(), 2),
                         luck_usd=round(float(lb.usd_01.mean()), 3)))
    d = pd.DataFrame(rows)
    return d


# ----------------------------------------------- the four mandatory controls (AUTOPSY_BRIEF)
def sides(t):
    """Control 3: buy and sell reported separately.  EXPLORE is one-directional, so this is not
    optional -- a pooled number can be a bull market wearing a strategy's clothes."""
    out = {}
    for lab, m in (("buy", t.side.values > 0), ("sell", t.side.values < 0)):
        x = t[m]
        out[lab] = dict(trades=int(len(x)),
                        right_in_100=round(100 * float((x.R > 0).mean()), 2) if len(x) else None,
                        usd_per_trade=round(float(x.usd_01.mean()), 3) if len(x) else None,
                        gross_usd_per_trade=round(float((x.gross_pips * x.sym.map(C.USD_PIP_01)).mean()), 3)
                        if len(x) else None)
    return out


def direction_controls(syms, stage, seed=3, **kw):
    """Control 2: always-long / always-short / coin flip AT THE RULE'S OWN MINUTES.

    The order set is the rule's own -- same gaps, same touch minutes, same ATR shape around the same
    entry level -- with ONLY the side replaced.  That isolates the direction call from the clock, the
    volatility and the sample, which is the whole point (AUT05's invention).
    """
    rng = np.random.default_rng(seed)
    out = {}
    for lab in ("rule", "always_long", "always_short", "coin_flip"):
        tt = []
        for s in syms:
            g = gap_table(s)
            o = build_orders(g, **kw)
            if len(o) == 0:
                continue
            if lab != "rule":
                d = (np.ones(len(o), np.int8) if lab == "always_long" else
                     -np.ones(len(o), np.int8) if lab == "always_short" else
                     rng.choice([1, -1], size=len(o)).astype(np.int8))
                lvl, at = o.lvl.values, o.atr.values
                o = o.copy()
                o["side"] = d
                o["sl"] = lvl - d * kw.get("stop", BASE["stop"]) * at
                o["tp"] = lvl + d * kw.get("tgt", BASE["tgt"]) * at
            r = run(s, o, stage)
            if len(r):
                r = r[r.status == "filled"]
                if len(r):
                    tt.append(r)
        if not tt:
            out[lab] = None
            continue
        t = pd.concat(tt, ignore_index=True, sort=False)
        out[lab] = dict(trades=int(len(t)), right_in_100=round(100 * float((t.R > 0).mean()), 2),
                        PF=round(float(C.stats(t)["PF"]), 3),
                        usd_per_trade=round(float(t.usd_01.mean()), 3))
    return out


def luck_band(sym, t, tgt, stop, stage, n=20000, seeds=(1, 2, 3, 4, 5)):
    """Control 1: luck bar shape-matched, hour-matched AND hold-matched, n>=20,000, five seeds.

    `hours` comes from the real trades' FILL hour and `max_hold_min` from the rule's own time stop,
    because an unmatched five-day hold reads about a point low (HANDOFF 5.4).
    """
    if len(t) == 0:
        return None
    hours = sorted(set(t.f_fill_h.tolist()))
    hold = float(t.max_hold_min.median())
    w, u, nn = [], [], []
    for sd in seeds:
        lb = A.luck_bar(sym, n, tgt, stop, stage, seed=sd, hours=hours, max_hold_min=hold)
        lb = lb[lb.status == "filled"]
        if len(lb) == 0:
            continue
        w.append(100 * float((lb.R > 0).mean()))
        u.append(float(lb.usd_01.mean()))
        nn.append(len(lb))
    if not w:
        return None
    return dict(n_requested=n, n_filled_per_seed=nn, seeds=list(seeds), hours_matched=True,
                shape_matched=True, hold_matched_min=hold,
                right_in_100_mean=round(float(np.mean(w)), 2),
                right_in_100_lo=round(float(np.min(w)), 2), right_in_100_hi=round(float(np.max(w)), 2),
                seed_band_points=round(float(np.max(w) - np.min(w)), 2),
                usd_per_trade_mean=round(float(np.mean(u)), 3),
                usd_per_trade_lo=round(float(np.min(u)), 3), usd_per_trade_hi=round(float(np.max(u)), 3))


def lookahead_twin(syms, stage, **kw):
    """Control 4: the look-ahead twin in BOTH points and dollars.  Two deliberate cheats:
    knowing the gap at the OPEN of candle 3, and filling at the signal bar's own open."""
    rows = {}
    for lab, shift in (("honest", 0), ("cheat_gap_known_at_candle3_open", 1),
                       ("cheat_gap_known_at_candle2_close", 2)):
        tt = []
        for s in syms:
            g = gap_table(s)
            if shift:
                b = bars(s, TF)
                g = g.copy()
                g["t_created"] = pd.to_datetime(b.time.values[g.k.values] if shift == 1
                                                else b.tclose.values[g.k.values - 1])
                g["t_expire"] = g.t_created + pd.Timedelta(minutes=C.TF_MIN[TF] * MAX_AGE)
            t = trades(s, stage, g=g, **kw)
            if len(t):
                tt.append(t)
        t = pd.concat(tt, ignore_index=True, sort=False) if tt else pd.DataFrame()
        rows[lab] = dict(trades=int(len(t)),
                         right_in_100=round(100 * float((t.R > 0).mean()), 2) if len(t) else None,
                         usd_per_trade=round(float(t.usd_01.mean()), 3) if len(t) else None)
    h = rows["honest"]
    for lab in rows:
        if lab != "honest" and rows[lab]["trades"]:
            rows[lab]["cheat_worth_points"] = round(rows[lab]["right_in_100"] - h["right_in_100"], 2)
            rows[lab]["cheat_worth_usd"] = round(rows[lab]["usd_per_trade"] - h["usd_per_trade"], 3)
    return rows


def wline(fh, s=""):
    fh.write(s + "\n")


def tbl(fh, df, title=None, floatfmt=".3f"):
    if title:
        wline(fh, "\n**%s**\n" % title)
    if isinstance(df, dict):
        df = pd.DataFrame([df])
    try:
        wline(fh, df.to_markdown(index=False, floatfmt=floatfmt))
    except ImportError:                      # `tabulate` is not installed in this venv
        wline(fh, "```")
        wline(fh, df.to_string(index=False))
        wline(fh, "```")
    wline(fh)


# =========================================================================== STAGE 0 (group F)
def stage0():
    fh = open(os.path.join(OUT, "stage0_causality.md"), "w", encoding="utf-8")
    wline(fh, "# AUT07 Stage 0 -- catalogue group F, checked before anything else\n")
    syms = [GOLD, "EURUSD", "GBPUSD"]
    rows = []
    for sym in syms:
        b = bars(sym, TF)
        g = gap_table(sym, use_cache=False)
        # F1: the gap is known only at the close of candle 3
        assert (g.t_created.values == b.tclose.values[g.k.values]).all(), "t_created != close of candle 3"
        kk = g.k.values
        d = g["dir"].values
        okdef = np.where(d == 1, b.low.values[kk] > b.high.values[kk - 2],
                         b.low.values[kk - 2] > b.high.values[kk])
        assert okdef.all(), "a gap row does not satisfy the FVG inequality"
        o = build_orders(g, **BASE)
        assert (o.t_signal.values >= o.t_created.values).all(), "signal before the gap existed"
        assert (o.t_touch.values >= o.t_created.values).all(), "touch before the gap existed"
        r = run(sym, o, "EXPLORE")
        f = r[r.status == "filled"]
        assert (f.t_fill.values > f.t_signal.values - np.timedelta64(1, "ns")).all()
        assert (f.t_fill.values >= f.t_created.values).all()
        # the fill minute must be strictly AFTER the touch minute
        assert (f.t_fill.values > f.t_touch.values).all(), "filled on the touch bar itself"
        rows.append(dict(sym=sym, gaps=len(g), asof_ok=g.attrs.get("asof_ok", "cached"),
                         orders=len(o), filled=len(f),
                         min_fill_lag_min=float(((f.t_fill - f.t_touch) / pd.Timedelta(minutes=1)).min()),
                         gap_def_ok=True, t_created_ok=True))
    tbl(fh, pd.DataFrame(rows), "F1/F4 causality asserts (all passed)")

    # ---- F1 lookahead twin: pretend the gap is known at the OPEN of candle 3
    wline(fh, "\n## F1 -- the look-ahead twin (deliberate bug, to prove the real run is clean)\n")
    rows = []
    for sym in syms:
        g = gap_table(sym)
        b = bars(sym, TF)
        for name, shift in (("honest (candle 3 close)", 0), ("TWIN: candle 3 OPEN", 1),
                            ("TWIN: candle 2 close", 2)):
            gg = g.copy()
            if shift:
                gg["t_created"] = pd.to_datetime(b.time.values[gg.k.values] if shift == 1
                                                 else b.tclose.values[gg.k.values - 1])
                gg["t_expire"] = gg.t_created + pd.Timedelta(minutes=C.TF_MIN[TF] * MAX_AGE)
            t = trades(sym, "EXPLORE", g=gg, **BASE)
            rows.append(dict(sym=sym, version=name, **{k: v for k, v in st(t).items() if k != "group"}))
    tw = pd.DataFrame(rows)
    tbl(fh, tw)
    wline(fh, "If the twins look the same as the honest run, the rule is not reading the future "
              "through the gap definition.  If a twin looks better, that is the size of the leak "
              "we avoided.\n")

    # ---- F5: same-bar target-and-stop ambiguity, resolved from M1
    wline(fh, "\n## F5 -- same-M1-bar stop AND target (the simulator resolves stop-first)\n")
    rows = []
    for sym in syms:
        t = trades(sym, "EXPLORE", **BASE)
        m1 = bars(sym, "M1")
        t1 = m1.time.values
        i = np.searchsorted(t1, ns(t.t_exit.values), "left")
        i = np.clip(i, 0, len(t1) - 1)
        hi, lo = m1.high.values[i], m1.low.values[i]
        amb = ((t.exit_reason == "SL") & np.where(t.side.values == 1, hi >= t.tp.values,
                                                  lo <= t.tp.values))
        optimistic = t.R.values.copy()
        optimistic[amb.values] = np.abs((t.tp.values - t.fill.values) / (t.fill.values - t.sl.values))[amb.values]
        rows.append(dict(sym=sym, trades=len(t), sl_exits=int((t.exit_reason == "SL").sum()),
                         ambiguous=int(amb.sum()), amb_pct=round(100 * amb.mean(), 2),
                         avg_R_pessimistic=round(t.R.mean(), 4),
                         avg_R_if_all_tp_first=round(float(np.mean(optimistic)), 4)))
    tbl(fh, pd.DataFrame(rows), floatfmt=".4f")

    # ---- F3: feed holes
    wline(fh, "\n## F3 -- holes in the feed\n")
    rows = []
    for sym in C.PAIRS:
        b = bars(sym, "M1")
        dt = np.diff(b.time.values) / np.timedelta64(1, "m")
        m = (b.time.values[:-1] >= np.datetime64("2005-01-01")) & (b.time.values[:-1] < np.datetime64("2021-01-01"))
        g = gap_table(sym)
        ge = g[(g.t_created >= "2005-01-01") & (g.t_created < "2011-01-01")]
        rows.append(dict(sym=sym, first_bar=str(b.time.min())[:10], m1_bars=len(b),
                         holes_gt_6h=int(((dt > 360) & m).sum()), holes_60_360m=int(((dt > 60) & (dt <= 360) & m).sum()),
                         explore_gaps=len(ge), gaps_across_a_hole=int(ge.f_hole.sum()),
                         pct_across_hole=round(100 * ge.f_hole.mean(), 2) if len(ge) else np.nan))
    hol = pd.DataFrame(rows)
    tbl(fh, hol, floatfmt=".2f")

    # ---- F2: feed swap is impossible before 2021
    wline(fh, "\n## F2 -- feed disagreement: NOT TESTABLE on EXPLORE/VALIDATE/STRESS\n")
    try:
        import importlib
        p = os.path.join(C.ROOT, "data_zero")
        zf = sorted(os.listdir(p))[:3] if os.path.isdir(p) else []
        importlib.import_module  # noqa
    except Exception:
        zf = []
    wline(fh, "The second feed (Exness ZERO, `data_zero`) starts 2021-01-01, which is the SEALED EXAM "
              "window.  A Dukascopy-vs-Exness swap therefore cannot be run on any stage I am allowed "
              "to look at.  STRESS substitutes adverse spread + adverse slippage instead.  "
              "Sample of data_zero files: %s\n" % (zf,))

    # ---- E5 / F2 side-finding: the Dukascopy spread column
    wline(fh, "\n## E5 -- what the Dukascopy feed actually charges (this is a finding, see report)\n")
    rows = []
    for sym in C.PAIRS:
        b = bars(sym, "M1")
        sp = b.spr.values / C.pip(sym)
        rows.append(dict(sym=sym, spr_zero_pct=round(100 * float((sp <= 0).mean()), 2),
                         spr_mean_pips=round(float(sp.mean()), 4), spr_p99=round(float(np.quantile(sp, 0.99)), 3),
                         comm_pips=C.COMM[sym]))
    tbl(fh, pd.DataFrame(rows), floatfmt=".4f")

    # ---- F6: era drift
    wline(fh, "\n## F6 -- era drift: is one ATR the same thing in 2005 and 2020?\n")
    rows = []
    for sym in (GOLD, "EURUSD"):
        b = bars(sym, TF)
        at = A.atr(b, 14).values
        d = pd.DataFrame(dict(y=b.time.dt.year, atr=at, px=b.close.values)).dropna()
        d = d[(d.y >= 2005) & (d.y < 2021)].groupby("y").agg(atr_price=("atr", "median"), px=("px", "median"))
        d["atr_pct_of_price"] = 100 * d.atr_price / d.px
        d["sym"] = sym
        rows.append(d.reset_index())
    tbl(fh, pd.concat(rows), floatfmt=".4f")
    fh.close()
    print("stage0 written")


# =========================================================================== STAGE 1 EXPLORE
CONDS = []


def cond(cid, note, fn):
    CONDS.append((cid, note, fn))


def _register_conditions():
    CONDS.clear()
    q = lambda t, c, v: t[c].values >= v          # noqa: E731
    cond("C-w1", "gap width >= 0.25 ATR", lambda t: t.f_w_atr.values >= 0.25)
    cond("C-w2", "gap width >= 0.50 ATR", lambda t: t.f_w_atr.values >= 0.50)
    cond("C-w3", "gap width >= 1.00 ATR", lambda t: t.f_w_atr.values >= 1.00)
    cond("C-w4", "gap width < 0.15 ATR (micro gaps)", lambda t: t.f_w_atr.values < 0.15)
    cond("D1a", "candle-3 body >= 0.75 ATR (real displacement)", lambda t: t.f_body3_atr.values >= 0.75)
    cond("D1b", "3-candle leg >= 2.0 ATR", lambda t: t.f_leg_atr.values >= 2.0)
    cond("D2", "candle 3 closes in the top/bottom third of its range", lambda t: t.f_clspos.values >= 0.667)
    cond("D5", "candle 3 engulfs candle 2", lambda t: t.f_engulf.values == 1)
    cond("D-gl", "gap is >= 35% of the whole leg (clean imbalance)", lambda t: t.f_w_over_leg.values >= 0.35)
    cond("B4a", "gap BORN in a thin hour (21-02 UTC)", lambda t: t.f_born_thin.values == 1)
    cond("B4b", "gap BORN in London hours (07-11 UTC)", lambda t: t.f_born_ldn.values == 1)
    cond("B4c", "gap BORN in New York hours (12-16 UTC)", lambda t: t.f_born_ny.values == 1)
    cond("B2", "gap BORN in the London/NY overlap (13-15 UTC)", lambda t: t.f_born_ovl.values == 1)
    cond("B1", "FILLED in London or NY (07-16 UTC)", lambda t: (t.f_fill_h.values >= 7) & (t.f_fill_h.values <= 16))
    cond("B1b", "FILLED in a thin hour (21-02 UTC)", lambda t: np.isin(t.f_fill_h.values, [21, 22, 23, 0, 1, 2]))
    cond("B6", "Monday or Friday", lambda t: np.isin(t.f_fill_dow.values, [0, 4]))
    cond("A1a", "H1 structure trend agrees with the gap", lambda t: t.f_h1_agree.values == 1)
    cond("A1b", "H4 structure trend agrees with the gap", lambda t: t.f_h4_agree.values == 1)
    cond("A1c", "H1 AND H4 both agree", lambda t: (t.f_h1_agree.values == 1) & (t.f_h4_agree.values == 1))
    cond("A1d", "H1 close vs H1 EMA50 agrees", lambda t: t.f_h1_ema_agree.values == 1)
    cond("A1e", "M15 close vs M15 EMA50 agrees", lambda t: t.f_m15_ema_agree.values == 1)
    cond("A2", "H1 structure event is fresh (<= 12 H1 bars old)", lambda t: t.f_h1_evt_age.values <= 12)
    cond("A4a", "ATR above its own 20-day median", lambda t: t.f_atr_rel.values >= 1.0)
    cond("A4b", "ATR well above its 20-day median (>= 1.5x)", lambda t: t.f_atr_rel.values >= 1.5)
    cond("A5", "ATR expanding (ATR now >= 1.2x ATR 20 bars ago)", lambda t: t.f_atr_dir.values >= 1.2)
    cond("A7", "price not stretched from M15 EMA50 (< 2 ATR)", lambda t: t.f_m15_ema_dist.values < 2.0)
    cond("C1", "room to run: nearest untouched H1 level >= 1.5 ATR away", lambda t: t.f_room_atr.values >= 1.5)
    cond("C1b", "room to run >= 3 ATR", lambda t: t.f_room_atr.values >= 3.0)
    cond("C2", "opposing level behind the entry is close (< 1 ATR)", lambda t: t.f_back_atr.values < 1.0)
    cond("C5", "gap in the discount/premium half it should be in", lambda t:
         np.where(t["dir"].values == 1, t.f_pdr_pos.values <= 0.5, t.f_pdr_pos.values >= 0.5))
    cond("C6", "gap filled the same day it was born", lambda t: t.f_same_sess.values == 1)
    cond("C3a", "gap filled fast (<= 4 M15 bars old)", lambda t: t.f_age_bars.values <= 4)
    cond("C3b", "gap filled late (>= 16 M15 bars old)", lambda t: t.f_age_bars.values >= 16)
    cond("F3", "gap printed ACROSS A HOLE in the feed", lambda t: t.f_hole.values == 1)
    cond("E1", "stop is wide enough to sit outside the noise (>= 1 ATR)", lambda t: t.f_stop_atr.values >= 1.0)


def explore():
    _register_conditions()
    fh = open(os.path.join(OUT, "explore.md"), "w", encoding="utf-8")
    wline(fh, "# AUT07 Stage 1 -- EXPLORE 2005-01-01 to 2011-01-01\n")
    set_costs()
    t0 = time.time()

    # ---------------- baseline, all 25 pairs
    base = pool(C.PAIRS, "EXPLORE", **BASE)
    base.to_parquet(os.path.join(CACHE, "base_explore.parquet"), index=False)
    wline(fh, "Baseline B0: M15 FVG, min width 0.05 ATR, entry = MARKET on the close of the first "
              "M1 bar that touches the near gap edge, stop 1.0 ATR, target 1.5 ATR, 24 h time stop, "
              "gap valid 48 M15 bars.  %d trades, %.0f s\n" % (len(base), time.time() - t0))
    tbl(fh, pd.DataFrame([st(base, "pooled 25 pairs")]), "B0 pooled")
    per = pd.DataFrame([st(base[base.sym == s], s) for s in C.PAIRS])
    tbl(fh, per, "B0 per pair")

    # ---------------- entry mechanism / direction / depth grid
    rows = []
    for mode in ("react", "touch"):
        for depth, dn in ((0.0, "edge"), (0.5, "50% fill"), (1.0, "full fill")):
            for sign, sn in ((1, "follow"), (-1, "fade")):
                if mode == "touch" and sign == -1:
                    continue                 # a limit cannot be a fade: see report
                kw = dict(BASE, mode=mode, depth=depth, sign=sign)
                t = pool(CORE, "EXPLORE", **kw)
                rows.append(dict(mode=mode, entry=dn, dirn=sn, **{k: v for k, v in st(t).items() if k != "group"}))
    ent = pd.DataFrame(rows)
    tbl(fh, ent, "Entry mechanism x depth x direction (6 core pairs, 1.5 ATR / 1.0 ATR shape)")

    # ---------------- stop style
    rows = []
    for sk, buf, name in (("atr", 0, "1.0 ATR stop"), ("gap", 0.1, "beyond the far gap edge + 0.1 ATR"),
                          ("gap", 0.5, "beyond the far gap edge + 0.5 ATR")):
        for depth, dn in ((0.0, "edge"), (1.0, "full fill")):
            t = pool(CORE, "EXPLORE", **dict(BASE, stop_kind=sk, buf=buf, depth=depth))
            rows.append(dict(stop=name, entry=dn, **{k: v for k, v in st(t).items() if k != "group"}))
    tbl(fh, pd.DataFrame(rows), "Stop placement (6 core pairs, 1.5 ATR target)")

    # ---------------- the autopsy: every condition, three ways + the luck bar
    T = A.Tally("AUT07_fvg")
    rows = []
    for cid, note, fn in CONDS:
        c = np.nan_to_num(fn(base).astype(float), nan=0.0).astype(bool)
        tabl = A.score(base, c)
        T.add(cid, note, tabl)
        rows.append(dict(cond=cid, note=note, n_true=int(tabl.trades[1]), n_false=int(tabl.trades[2]),
                         win_true=tabl.right_in_100[1], win_false=tabl.right_in_100[2],
                         usd_true=tabl.net_usd_per_trade[1], usd_false=tabl.net_usd_per_trade[2],
                         lift=round(tabl.right_in_100[1] - tabl.right_in_100[0], 2)))
    tally = pd.DataFrame(rows).sort_values("usd_true", ascending=False)
    tbl(fh, tally, "THE TALLY -- %d conditions tried, pooled 25 pairs, B0 shape" % len(CONDS))
    tally.to_csv(os.path.join(OUT, "tally_explore.csv"), index=False)

    # gold on its own
    gold = base[base.sym == GOLD]
    rows = []
    for cid, note, fn in CONDS:
        c = np.nan_to_num(fn(gold).astype(float), nan=0.0).astype(bool)
        tb = A.score(gold, c)
        rows.append(dict(cond=cid, note=note, n_true=int(tb.trades[1]), win_true=tb.right_in_100[1],
                         usd_true=tb.net_usd_per_trade[1], win_false=tb.right_in_100[2],
                         usd_false=tb.net_usd_per_trade[2]))
    tbl(fh, pd.DataFrame(rows).sort_values("usd_true", ascending=False), "The same tally, GOLD only")

    # ---------------- born-hour detail (the question the brief asked for by name)
    byh = base.groupby("f_born_h").agg(trades=("R", "size"), win=("R", lambda x: round(100 * (x > 0).mean(), 2)),
                                       usd=("usd_01", "mean"), w_atr=("f_w_atr", "median"),
                                       hole=("f_hole", "mean")).round(3).reset_index()
    tbl(fh, byh, "Gap BIRTH hour (UTC) -- pooled 25 pairs")
    byh2 = gold.groupby("f_born_h").agg(trades=("R", "size"), win=("R", lambda x: round(100 * (x > 0).mean(), 2)),
                                        usd=("usd_01", "mean"), w_atr=("f_w_atr", "median")).round(3).reset_index()
    tbl(fh, byh2, "Gap BIRTH hour (UTC) -- gold only")

    # ---------------- E2: what the trades actually reached (the shape question)
    rows = []
    for name, t in (("pooled", base), ("gold", gold)):
        for lab, m in (("winners", t.R > 0), ("losers", t.R <= 0)):
            x = t[m]
            rows.append(dict(set=name, group=lab, n=len(x), mfe_R=round(x.mfe_R.median(), 3),
                             mae_R=round(x.mae_R.median(), 3), hold_h=round(x.hold_h.median(), 2)))
        rows.append(dict(set=name, group="losers that got >=80% to target",
                         n=int(((t.R <= 0) & (t.mfe_R >= 0.8 * 1.5)).sum()),
                         mfe_R=np.nan, mae_R=np.nan, hold_h=np.nan))
    tbl(fh, pd.DataFrame(rows), "E2 -- how far trades travelled before resolving (R = 1.0 ATR stop)")

    # ---------------- G1: the luck bar
    lb = luck(C.PAIRS, base, BASE["tgt"], BASE["stop"], "EXPLORE")
    lb = lb.merge(pd.DataFrame([dict(sym=s, real_win=st(base[base.sym == s]).get("win_pct"),
                                     real_usd=st(base[base.sym == s]).get("usd_per_trade"),
                                     real_n=st(base[base.sym == s]).get("trades")) for s in C.PAIRS]), on="sym")
    lb["beat_luck"] = (lb.real_win - lb.luck_win).round(2)
    tbl(fh, lb, "G1 -- the luck bar (random entries, same stage, same hours, same shape, same costs)")
    wline(fh, "Pooled: real %.2f right in 100 vs luck bar %.2f; real $%.3f vs luck $%.3f a trade.\n"
              % (base.R.gt(0).mean() * 100, lb.luck_win.mean(), base.usd_01.mean(), lb.luck_usd.mean()))
    fh.close()
    print("explore written, %.0f s" % (time.time() - t0))


# =========================================================================== the shape ladder
def ladder(syms=None, tag="gold", pairs_label=None):
    syms = syms or [GOLD]
    fh = open(os.path.join(OUT, "ladder_%s.md" % tag), "w", encoding="utf-8")
    wline(fh, "# AUT07 -- the ATR shape ladder, EXPLORE only (%s)\n" % (pairs_label or ",".join(syms)))
    set_costs()
    gs = {s: gap_table(s) for s in syms}
    for depth, dn in ((0.0, "edge"), (1.0, "full fill")):
        rows = []
        for tgt, stp in A.ATR_LADDER:
            tt = []
            for s in syms:
                t = trades(s, "EXPLORE", g=gs[s], **dict(BASE, depth=depth, tgt=tgt, stop=stp))
                if len(t):
                    tt.append(t)
            if not tt:
                continue
            t = pd.concat(tt, ignore_index=True, sort=False)
            med = float(np.median(t.atr.values))
            be = A.breakeven_rate(syms[0], med, tgt, stp)
            s0 = st(t)
            rows.append(dict(tgt_atr=tgt, stop_atr=stp, need_right_in_100=round(be, 1),
                             got_right_in_100=s0["win_pct"], edge_pts=round(s0["win_pct"] - be, 1),
                             trades=s0["trades"], PF=s0["PF"], usd_per_trade=s0["usd_per_trade"],
                             gross_usd=s0["gross_usd"], avg_hold_h=s0["avg_hold_h"]))
        d = pd.DataFrame(rows).sort_values("usd_per_trade", ascending=False)
        tbl(fh, d, "Entry at the %s: 36 shapes, best dollars first" % dn)
        d.to_csv(os.path.join(OUT, "ladder_%s_%s.csv" % (tag, dn.replace(" ", "_"))), index=False)

    # time stop / break-even stop, on the best two shapes
    rows = []
    for tgt, stp in ((1.0, 2.0), (1.5, 1.0), (1.0, 1.0), (2.0, 3.0)):
        for hold in (2, 6, 24, 72):
            for be in (np.nan, 0.5, 1.0):
                tt = [trades(s, "EXPLORE", g=gs[s], **dict(BASE, tgt=tgt, stop=stp, max_hold_h=hold,
                                                           be_at_r=be)) for s in syms]
                tt = [x for x in tt if len(x)]
                if not tt:
                    continue
                t = pd.concat(tt, ignore_index=True, sort=False)
                s0 = st(t)
                rows.append(dict(shape="%.1fT/%.1fS" % (tgt, stp), hold_h=hold,
                                 be_at_r=("none" if np.isnan(be) else be), trades=s0["trades"],
                                 win=s0["win_pct"], PF=s0["PF"], usd=s0["usd_per_trade"],
                                 gross_usd=s0["gross_usd"], hold=s0["avg_hold_h"]))
    tbl(fh, pd.DataFrame(rows).sort_values("usd", ascending=False), "E3/E4 -- time stop and break-even stop")
    fh.close()
    print("ladder_%s written" % tag)


# =========================================================================== refinements
# Chosen AFTER the EXPLORE tally (results/AUT07_fvg/explore.md) and the two 36-shape ATR ladders
# (ladder_gold.md, ladder_core.md), BEFORE VALIDATE was ever opened.  Three findings drove the
# choice, in order of size:
#   1. Depth beats everything else in the entry-mechanism grid: full-fill entry (wait for the WHOLE
#      gap to be traded through, not just the near edge) beats edge-entry at every shape tried, by
#      $0.3-0.4 a trade (core-pairs ladder, matched shape).  "touch" (resting limit) and "fade" both
#      lose to react+follow.  So all three refinements below use depth=1.0.
#   2. Shape: on 6 core pairs (gold + 5 FX, full fill), 1.0xATR target / 4.0xATR stop is the single
#      best-dollar cell of the 36-shape ladder (-$0.753/trade, PF 0.86, only 1.3 points short of its
#      own break-even bar -- the closest any AUT0x cell has come).  On GOLD ALONE the same cell is
#      the only POSITIVE EXPLORE number this whole project has produced (+$0.131/trade, PF 0.95,
#      7,237 trades) -- flagged, not trusted, because EXPLORE gold is 1.8 one-directional bull-market
#      years and the positive reading does not survive pooling with the other 5 pairs.
#   3. Catalogue filters: of 35 conditions tried, D1a (candle-3 body >= 0.75 ATR, "real displacement")
#      had the best lift-to-sample tradeoff (+2.98 points keeping 17.6% of trades) and F3 (gap printed
#      across a feed hole) was the single worst bucket (-5.88 lift) -- cheap hygiene, costs ~2.5% of
#      trades.  B4c (gap born in New York hours, 12-16 UTC) was the best session filter (+1.94 points,
#      keeps 20%); hour-by-hour, 14:00 and 15:00 UTC are the two best hours in the day on both win
#      rate and dollars.
def refinements():
    """The <=3 rules carried out of EXPLORE.  Filled in from the explore tables (see report)."""
    shape = dict(BASE, depth=1.0, tgt=1.0, stop=4.0, max_hold_h=24.0)
    return {
        "R1_shape_fullfill": dict(kw=shape, filt=None,
                         why="shape + depth only, no catalogue filter: full-fill entry, 1.0xATR "
                             "target / 4.0xATR stop -- the best-dollar cell of the 36-shape ladder "
                             "on 6 core pairs, and the only EXPLORE cell anywhere in this project "
                             "that reads positive on gold alone"),
        "R2_displacement_nohole": dict(kw=shape,
                               filt=lambda t: (t.f_body3_atr.values >= 0.75) & (t.f_hole.values == 0),
                               why="R1 plus: the candle that completed the gap must have a real "
                                   "displacement body (>= 0.75 ATR, catalogue D1a) and the gap must "
                                   "not have been printed across a feed hole (catalogue F3)"),
        "R3_ny_session": dict(kw=shape,
                             filt=lambda t: t.f_born_ny.values == 1,
                             why="R1 plus: the gap must have been BORN in New York hours, 12:00-16:00 "
                                 "UTC (catalogue B4c) -- the best session filter in the tally and the "
                                 "two single best hours of the day (14:00, 15:00 UTC)"),
    }


REF_OVERRIDE = {}


def stage_run(stage, name, spec, syms=None, seq=False):
    syms = syms or C.PAIRS
    t = pool(syms, stage, **spec["kw"])
    if len(t) == 0:
        return t
    if spec["filt"] is not None:
        t = t[np.nan_to_num(spec["filt"](t).astype(float), nan=0.0).astype(bool)].copy()
    return t


def final():
    refs = REF_OVERRIDE or refinements()
    fh = open(os.path.join(OUT, "final.md"), "w", encoding="utf-8")
    wline(fh, "# AUT07 -- the chosen refinements through EXPLORE, VALIDATE, STRESS\n")
    allrows = []
    keep = {}
    for stage, costs, lab in (("EXPLORE", dict(), "EXPLORE 2005-2011"),
                              ("VALIDATE", dict(), "VALIDATE 2011-2017"),
                              ("STRESS", dict(), "STRESS 2017-2021 (normal costs)"),
                              ("STRESS", dict(extra_spread_pips=None, stop_slip_pips=1.0, comm_mult=1.5),
                               "STRESS 2017-2021 (adverse: +spread, 5x slip, 1.5x commission)")):
        if costs:
            set_costs(extra_spread_pips=0.0, stop_slip_pips=costs["stop_slip_pips"],
                      comm_mult=costs["comm_mult"])
            global EXTRA_SPREAD_PIPS
            EXTRA_SPREAD_PIPS = 0.0
            _MEMO.clear()
            set_costs(extra_spread_pips=0.8, stop_slip_pips=1.0, comm_mult=1.5)
        else:
            _MEMO.clear()
            set_costs()
        for name, spec in refs.items():
            t = stage_run(stage, name, spec)
            allrows.append(dict(rule=name, stage=lab, **{k: v for k, v in st(t).items() if k != "group"}))
            if lab.startswith("EXPLORE") or lab.startswith("VALIDATE") or lab.startswith("STRESS 2017-2021 (normal"):
                keep[(name, stage)] = t
                A.gate(stage)
                try:
                    C.save("AUT07_fvg", "%s_%s" % (name, stage), t)
                except Exception as e:
                    print("save warn", e)
    set_costs()
    _MEMO.clear()
    d = pd.DataFrame(allrows)
    tbl(fh, d, "The three refinements, pooled 25 pairs")
    d.to_csv(os.path.join(OUT, "refinements_stages.csv"), index=False)

    for name in refs:
        rows = []
        for stage in ("EXPLORE", "VALIDATE", "STRESS"):
            t = keep.get((name, stage))
            if t is None or len(t) == 0:
                continue
            for s in C.PAIRS:
                x = t[t.sym == s]
                if len(x):
                    rows.append(dict(stage=stage, sym=s, **{k: v for k, v in st(x).items() if k != "group"}))
        if rows:
            tbl(fh, pd.DataFrame(rows), "%s -- per pair" % name)

    # luck bars for the chosen shape (all three refinements share the same shape/depth)
    for rname in refs:
        t = keep.get((rname, "EXPLORE"))
        if t is None or len(t) == 0:
            continue
        for stage in ("EXPLORE", "VALIDATE", "STRESS"):
            tt = keep.get((rname, stage))
            if tt is None or len(tt) == 0:
                continue
            lb = luck(C.PAIRS, tt, refs[rname]["kw"]["tgt"], refs[rname]["kw"]["stop"], stage)
            lb["stage"] = stage
            tbl(fh, lb, "Luck bar, %s, %s" % (rname, stage))

    # ---------------- gold on its own: controls + shape-matched luck band (the standout pair)
    wline(fh, "\n## Gold on its own -- the pair that stayed near breakeven at every stage\n")
    set_costs()
    for rname in refs:
        spec = refs[rname]
        ctl_kw = {k: v for k, v in spec["kw"].items() if k not in ("tgt", "stop")}
        for stage in ("EXPLORE", "VALIDATE", "STRESS"):
            try:
                dc = direction_controls([GOLD], stage, tgt=spec["kw"]["tgt"], stop=spec["kw"]["stop"], **ctl_kw)
            except Exception as e:
                wline(fh, "controls failed for %s %s: %s" % (rname, stage, e))
                continue
            wline(fh, "\n**%s controls, GOLD only, %s**\n" % (rname, stage))
            tbl(fh, pd.DataFrame([dict(variant=k, **(v or {})) for k, v in dc.items()]))
    t = keep.get(("R1_shape_fullfill", "EXPLORE"))
    gold_t = {}
    for rname in refs:
        for stage in ("EXPLORE", "VALIDATE", "STRESS"):
            tt = keep.get((rname, stage))
            if tt is None or len(tt) == 0:
                continue
            gt = tt[tt.sym == GOLD]
            if len(gt) == 0:
                continue
            lb = luck_band(GOLD, gt, refs[rname]["kw"]["tgt"], refs[rname]["kw"]["stop"], stage, n=20000)
            if lb is None:
                continue
            lb["rule"], lb["stage"] = rname, stage
            lb["real_right_in_100"] = round(100 * float((gt.R > 0).mean()), 2)
            lb["real_usd_per_trade"] = round(float(gt.usd_01.mean()), 3)
            lb["real_n"] = len(gt)
            tbl(fh, pd.DataFrame([lb]), "Gold shape-matched luck band, %s, %s" % (rname, stage))
    fh.close()
    print("final written")


# =========================================================================== examples + replay
def examples(n=10, rule="R3_ny_session"):
    refs = REF_OVERRIDE or refinements()
    set_costs()
    spec = refs[rule]
    t = stage_run("EXPLORE", rule, spec)
    t = t.sort_values("t_fill").reset_index(drop=True)
    rng = np.random.default_rng(11)
    pick = t.iloc[np.sort(rng.choice(len(t), size=min(n, len(t)), replace=False))]
    cols = ["sym", "dir", "t_created", "lo", "hi", "f_w_atr", "atr", "t_touch", "t_fill", "fill",
            "sl", "tp", "t_exit", "exit", "exit_reason", "hold_h", "R", "usd_01", "mfe_R", "mae_R"]
    pick[cols].to_csv(os.path.join(OUT, "examples.csv"), index=False)
    print(pick[cols].to_string())
    return pick


def replay(n=3, rule="R3_ny_session"):
    """Hand replay on raw M1, no simulator: which level was touched first and when."""
    refs = REF_OVERRIDE or refinements()
    set_costs()
    t = stage_run("EXPLORE", rule, refs[rule])
    t = t.sort_values("t_fill").reset_index(drop=True)
    rng = np.random.default_rng(5)
    pick = t.iloc[np.sort(rng.choice(len(t), size=n, replace=False))]
    rows = []
    for r in pick.itertuples():
        m1 = bars(r.sym, "M1")
        t1 = m1.time.values
        a = int(np.searchsorted(t1, np.datetime64(pd.Timestamp(r.t_fill)), "left"))
        d = int(r.side)
        P = C.pip(r.sym)
        fill = m1.open.values[a] + (m1.spr.values[a] if d == 1 else 0.0)
        end = min(a + int(r.max_hold_min), len(t1))
        why, px, kk = "TIME", None, end - 1
        for i in range(a, end):
            hi, lo, sp = m1.high.values[i], m1.low.values[i], m1.spr.values[i]
            sl_hit = (lo <= r.sl) if d == 1 else (hi + sp >= r.sl)
            tp_hit = (hi >= r.tp) if d == 1 else (lo + sp <= r.tp)
            if i == a:
                tp_hit = False
            if sl_hit:
                why, px, kk = "SL", r.sl - d * C.STOP_SLIP * P, i
                break
            if tp_hit:
                why, px, kk = "TP", r.tp, i
                break
        if px is None:
            px = m1.close.values[kk] + (0 if d == 1 else m1.spr.values[kk])
        net = (px - fill) * d / P - C.COMM[r.sym]
        rows.append(dict(sym=r.sym, t_fill=r.t_fill, side=d, sim_fill=round(r.fill, 5),
                         hand_fill=round(float(fill), 5), sim_exit=r.exit_reason, hand_exit=why,
                         sim_net_pips=round(r.net_pips, 3), hand_net_pips=round(float(net), 3),
                         sim_minutes=round(r.hold_h * 60), hand_minutes=int((t1[kk] - t1[a]) / np.timedelta64(1, "m")),
                         match=(why == r.exit_reason) and abs(net - r.net_pips) < 0.05))
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(OUT, "replay.csv"), index=False)
    print(d.to_string())
    return d


# =========================================================================== CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["stage0", "explore", "ladder", "ladder_core", "final",
                                    "examples", "replay", "all"])
    a = ap.parse_args()
    if a.cmd in ("stage0", "all"):
        stage0()
    if a.cmd in ("explore", "all"):
        explore()
    if a.cmd in ("ladder", "all"):
        ladder([GOLD], "gold")
    if a.cmd in ("ladder_core", "all"):
        ladder(CORE, "core", "6 core pairs")
    if a.cmd in ("final", "all"):
        final()
    if a.cmd in ("examples", "all"):
        examples()
    if a.cmd in ("replay", "all"):
        replay()


if __name__ == "__main__":
    main()
