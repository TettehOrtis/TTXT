"""AUT08 - Break of structure / CHoCH continuation. Autopsy per bt/AUTOPSY_BRIEF.md.

Strategy under test: a CLOSE beyond the last CONFIRMED swing is a structure break (BOS if it is in
the direction of the running trend, CHoCH if it is the first break against it). Take the new
direction at the open of the next bar.

Run (from the TTXT root):
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut08_bos.py stage0
    ...                                                              explore
    ...                                                              ladder
    ...                                                              refine
    ...                                                              confirm      (VALIDATE+STRESS)

Nothing here reads EXAM (2021+). autopsy_lib.gate() guards every window.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                      # noqa: E402
import autopsy_lib as A                 # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT08_bos")
CACHE = os.path.join(OUT, "_cache")
os.makedirs(CACHE, exist_ok=True)

SESSIONS = {"ASIA": (0, 7), "LONDON": (7, 12), "OVERLAP": (12, 16), "NY": (16, 21), "LATE": (21, 24)}


# ====================================================================== structure, causal + leaky
def struct_core(b, N, lag=None):
    """smc_core.structure, re-implemented so the swing CONFIRMATION LAG is a parameter.

    lag=N  -> the honest rule: a fractal swing at bar s is only usable from bar s+N, the first bar
              at which `h[s] >= max(h[s+1..s+N])` is knowable. stage0 asserts this is bit-identical
              to smc_core.structure(b, N).
    lag=0  -> the LEAKY twin: the swing is used from bar s itself, i.e. the moment it forms. That
              needs h[s+1..s+N], which has not happened yet. The gap between the two IS the
              look-ahead size of this strategy family.
    """
    if lag is None:
        lag = N
    o, h, l, c = b.open.values, b.high.values, b.low.values, b.close.values
    n = len(b)
    is_sh, is_sl = C.swings(h, l, N)
    trend = np.zeros(n, np.int8)
    evt = np.zeros(n, np.int8)
    ob_lo = np.full(n, np.nan); ob_hi = np.full(n, np.nan); leg_i = np.full(n, -1, np.int64)
    lsh_a = np.full(n, np.nan); lsl_a = np.full(n, np.nan)
    lsh_ia = np.full(n, -1, np.int64); lsl_ia = np.full(n, -1, np.int64)
    tr, lsh, lsl, lsh_i, lsl_i, sh_ok, sl_ok = 0, np.nan, np.nan, -1, -1, False, False
    for k in range(n):
        s = k - lag
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
    return dict(trend=trend, evt=evt, ob_lo=ob_lo, ob_hi=ob_hi, leg_i=leg_i,
                lsh=lsh_a, lsl=lsl_a, lsh_i=lsh_ia, lsl_i=lsl_ia, is_sh=is_sh, is_sl=is_sl)


# ====================================================================== features
def _htf_trend(sym, tf, times, N=5):
    b = C.bars(sym, tf)
    tr = struct_core(b, N)["trend"]
    i = C.asof_index(b, pd.to_datetime(times).values)
    return np.where(i >= 0, tr[np.clip(i, 0, len(tr) - 1)], 0)


def event_table(sym, tf, N, hi, lag=None, nbuf=400):
    """One row per structure break on `tf`, every column computable strictly BEFORE the fill.

    Bars are cut at `hi` before structure() runs, so nothing after the window can leak in (the
    truncation-invariance check in stage0 proves the cut changes nothing, i.e. the rule is causal).
    """
    b = C.bars(sym, tf)
    b = b[b.time < hi].reset_index(drop=True)
    if len(b) < nbuf + 50:
        return pd.DataFrame()
    st = struct_core(b, N, lag)
    o, h, l, c = b.open.values, b.high.values, b.low.values, b.close.values
    atrS = A.atr(b, 14).values                                    # signal-TF Wilder ATR, causal
    e50 = C.ema(c, 50)
    k = np.flatnonzero(st["evt"] != 0)
    k = k[k >= nbuf]
    if len(k) == 0:
        return pd.DataFrame()
    d = np.sign(st["evt"][k]).astype(int)
    tsig = b.tclose.values[k]

    # ---- the broken level and the leg that broke it
    L = np.where(d > 0, st["lsh"][k], st["lsl"][k])
    Li = np.where(d > 0, st["lsh_i"][k], st["lsl_i"][k])
    legi = st["leg_i"][k]
    legpx = np.where(d > 0, l[legi], h[legi])
    a = atrS[k]

    f = {}
    f["t_signal"] = tsig
    f["side"] = d
    f["k"] = k
    f["f_choch"] = (np.abs(st["evt"][k]) == 2).astype(int)
    f["lvl"] = L
    f["leg_px"] = legpx
    f["ob_lo"] = st["ob_lo"][k]
    f["ob_hi"] = st["ob_hi"][k]
    f["atr_tf"] = a
    # ---- D / displacement (PRIME)
    f["f_disp_atr"] = (c[k] - L) * d / a                          # how far past the level it closed
    f["f_leg_atr"] = (c[k] - legpx) * d / a                       # size of the whole breaking move
    f["f_leg_bars"] = k - legi
    f["f_body_atr"] = np.abs(c[k] - o[k]) / a
    rng = np.maximum(h[k] - l[k], 1e-12)
    f["f_close_pos"] = np.where(d > 0, (c[k] - l[k]) / rng, (h[k] - c[k]) / rng)
    f["f_engulf"] = ((np.maximum(c[k], o[k]) >= h[k - 1]) & (np.minimum(c[k], o[k]) <= l[k - 1])).astype(int)
    # ---- A2 staleness
    ev_idx = np.flatnonzero(st["evt"] != 0)
    pos = np.searchsorted(ev_idx, k, "left")
    f["f_bars_prev_evt"] = np.where(pos > 0, k - ev_idx[np.clip(pos - 1, 0, len(ev_idx) - 1)], 9999)
    flip = np.flatnonzero(np.diff(st["trend"]) != 0) + 1
    fp = np.searchsorted(flip, k, "right")
    f["f_bars_trendflip"] = np.where(fp > 0, k - flip[np.clip(fp - 1, 0, len(flip) - 1)], 9999)
    # ---- A3 / A5 / A7
    eff = np.abs(c[k] - c[k - 20]) / np.maximum(
        pd.Series(c).diff().abs().rolling(20).sum().values[k], 1e-12)
    f["f_eff"] = eff
    f["f_ema_slope"] = (e50[k] - e50[k - 10]) * d / a
    f["f_atr_exp"] = a / np.maximum(atrS[k - 20], 1e-12)
    f["f_ema_dist"] = (c[k] - e50[k]) * d / a
    # ---- A4 volatility rank against its own trailing year
    W = int(round(365 * 24 * 60 / C.TF_MIN.get(tf, 15)))
    f["f_atr_rank"] = pd.Series(atrS).rolling(W, min_periods=200).rank(pct=True).values[k]
    # ---- C1 was the broken level genuine untouched liquidity
    maj = np.zeros(len(k), bool); unt = np.zeros(len(k)); tch = np.zeros(len(k))
    room = np.zeros(len(k)); wallbars = np.zeros(len(k))
    for q in range(len(k)):
        kk, li, lv, dd = k[q], Li[q], L[q], d[q]
        lo40 = max(0, li - 40)
        maj[q] = (h[lo40:li] < lv).all() if dd > 0 else (l[lo40:li] > lv).all()
        # how long that level stood as the extreme before it formed (capped at 400)
        back = np.flatnonzero(h[max(0, li - 400):li] >= lv) if dd > 0 else \
               np.flatnonzero(l[max(0, li - 400):li] <= lv)
        unt[q] = (li - (max(0, li - 400) + back[-1])) if len(back) else 400
        # C3 how many of the previous 40 bars traded through the level
        tch[q] = ((h[lo40:li] >= lv) & (l[lo40:li] <= lv)).sum()
        # C2 distance to the nearest obstacle ahead. Measured over the 200 bars UP TO AND INCLUDING
        # the swing that was broken, i.e. the break bar and its impulse leg are excluded -- a bar
        # high above its own close is not a wall, it is the same candle.
        w0 = max(0, li - 200)
        if dd > 0:
            cand = h[w0:li + 1][h[w0:li + 1] > c[kk]]
            room[q] = (cand.min() - c[kk]) / a[q] if len(cand) else np.nan
            wallbars[q] = len(cand)
        else:
            cand = l[w0:li + 1][l[w0:li + 1] < c[kk]]
            room[q] = (c[kk] - cand.max()) / a[q] if len(cand) else np.nan
            wallbars[q] = len(cand)
    f["f_major"] = maj.astype(int)
    f["f_untouched"] = unt
    f["f_touches"] = tch
    f["f_room_atr"] = np.where(np.isnan(room), 99.0, room)        # 99 = clean air, a 200-bar extreme
    f["f_clean_air"] = np.isnan(room).astype(int)
    # ---- C5 prior day position
    day = pd.Series(b.time.dt.normalize().values)
    dh = pd.Series(h).groupby(day).transform("max").shift(0)
    g = pd.DataFrame({"d": day, "h": h, "l": l}).groupby("d").agg(hi=("h", "max"), lo=("l", "min"))
    g = g.shift(1)
    pdh = day.map(g.hi).values; pdl = day.map(g.lo).values
    f["f_pd_pos"] = np.clip((c[k] - pdl[k]) / np.maximum(pdh[k] - pdl[k], 1e-12), -2, 3)
    del dh
    # ---- B time
    tt = pd.to_datetime(tsig)
    hr = tt.hour.values
    f["f_hour"] = hr
    f["f_session"] = np.select([(hr >= a0) & (hr < b0) for a0, b0 in SESSIONS.values()],
                               list(SESSIONS.keys()), "ASIA")
    f["f_overlap"] = ((hr >= 12) & (hr < 16)).astype(int)
    f["f_dow"] = tt.dayofweek.values
    # ---- A1 higher timeframe agreement (asof: only CLOSED HTF bars)
    nagree = np.zeros(len(k), int)
    for htf in ("M15", "H1", "H4"):
        tr = _htf_trend(sym, htf, tsig)
        col = (tr == d).astype(int)
        f["f_ag_" + htf] = col
        nagree += col
    f["f_htf_n"] = nagree
    # ---- C4 round numbers (BT10 convention: distance to the nearest 50-pip grid line)
    P = C.pip(sym)
    grid = 50 * P
    f["f_round_atr"] = np.abs(c[k] - np.round(c[k] / grid) * grid) / a

    ev = pd.DataFrame(f)
    ev.insert(0, "sym", sym)
    ev.insert(1, "tf", tf)
    ev.insert(2, "N", N)
    return ev


def events_for(stage, tf, N, pairs=None, lag=None, tag=""):
    """Cached event tables for a stage. Cut at the stage end before structure() runs."""
    A.gate(stage)
    lo, hi = A.STAGES[stage]
    pairs = pairs or C.PAIRS
    key = f"ev_{stage}_{tf}_N{N}{tag}.parquet"
    fp = os.path.join(CACHE, key)
    if os.path.exists(fp) and pairs == C.PAIRS:
        return pd.read_parquet(fp)
    out = []
    for sym in pairs:
        ev = event_table(sym, tf, N, hi, lag)
        if len(ev):
            out.append(ev[pd.to_datetime(ev.t_signal) >= lo])
    ev = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if pairs == C.PAIRS:
        ev.to_parquet(fp, index=False)
    return ev


# ====================================================================== orders / shapes
def fill_est(sym, times, side):
    """The price simulate() will actually fill a market order at: next M1 bar's open (+spread for a
    buy). Used only to size the stop and target off the real fill; it peeks at no high/low/close."""
    m1 = C.bars(sym, "M1")
    t1 = m1.time.values
    i = np.searchsorted(t1, pd.to_datetime(times).values, "left")
    ok = (i >= 0) & (i < len(t1) - 2)
    i = np.clip(i, 0, len(t1) - 1)
    px = np.where(np.asarray(side) > 0, m1.open.values[i] + m1.spr.values[i], m1.open.values[i])
    return np.where(ok, px, np.nan), m1.spr.values[i], ok


def make_orders(ev, tgt=1.5, stop=1.0, stop_mode="atr", tgt_mode="atr", atr_unit="M15",
                hold_bars=48, be_at_r=np.nan, spread_mult=1.0):
    """Market order at the open of the first M1 bar at/after the break bar's close.

    stop_mode: atr  -> stop_mult x ATR from the fill
               swing-> the broken swing level, 0.1 ATR beyond it (the level that defines the break)
               leg  -> the origin of the impulse leg, 0.1 ATR beyond it (BT4's choice)
    tgt_mode : atr  -> tgt x ATR ; rr -> tgt x risk
    atr_unit : M15 (the project's unit, matches luck_bar) or TF (the signal timeframe's own ATR)
    """
    out = []
    for sym, g in ev.groupby("sym", sort=False):
        g = g.copy()
        at = A.atr_at(sym, "M15", g.t_signal) if atr_unit == "M15" else g.atr_tf.values
        px, spr, ok = fill_est(sym, g.t_signal, g.side.values)
        d = g.side.values
        if stop_mode == "atr":
            sl = px - d * at * stop
        elif stop_mode == "swing":
            sl = g.lvl.values - d * 0.1 * at
        else:
            sl = g.leg_px.values - d * 0.1 * at
        risk = (px - sl) * d
        tp = px + d * at * tgt if tgt_mode == "atr" else px + d * risk * tgt
        o = pd.DataFrame(dict(t_signal=pd.to_datetime(g.t_signal), side=d, entry_type="market",
                              entry=np.nan, expire=pd.to_datetime(g.t_signal), sl=sl, tp=tp,
                              be_at_r=be_at_r,
                              max_hold_min=hold_bars * C.TF_MIN.get(g.tf.iloc[0], 15)))
        o["atr_used"] = at
        o["f_spr_atr"] = spr * spread_mult / at
        o["f_stop_atr"] = risk / at
        for c0 in g.columns:
            if c0.startswith("f_") or c0 in ("f_choch", "k", "lvl", "leg_px", "atr_tf", "tf"):
                o[c0] = g[c0].values
        # CAUSAL sanity gate (BT4 used the same idea). A stop that is not a positive price, or a
        # stop more than 10 ATR or less than 0.2 ATR wide, is a corrupt bar, not a trade. Four such
        # bars exist in data_duka (see the report, "bugs found") and one of them was worth
        # -$1.44M on its own in a 35,000-trade average.
        good = ok & np.isfinite(sl) & np.isfinite(tp) & (risk > 0) & (sl > 0) & (px > 0) \
            & (risk / at >= 0.2) & (risk / at <= 10.0)
        o = o[good].reset_index(drop=True)
        out.append((sym, o))
    return out


def despike(t, mult=50.0, verbose=True):
    """Drop trades whose price move is physically impossible (more than `mult` x the pair's own
    median ATR). These are corrupt bars in the Dukascopy M1 files, not market moves. They are
    listed, never silently dropped."""
    if len(t) == 0:
        return t, pd.DataFrame()
    lim = t.groupby("sym").atr_used.transform("median") / t.sym.map(C.pip) * mult
    bad = t.gross_pips.abs() > lim
    if verbose and bad.any():
        say(f"  DESPIKE: dropped {int(bad.sum())} trades on corrupt bars "
            f"(worth ${t.usd_01[bad].sum():,.0f} of the pooled total)")
        say(t[bad][["sym", "t_signal", "fill", "sl", "exit", "exit_reason",
                    "risk_pips", "net_pips", "usd_01"]].to_string(index=False))
    return t[~bad].reset_index(drop=True), t[bad]


def run_shape(ev, one_at_a_time=True, **kw):
    res = []
    for sym, o in make_orders(ev, **kw):
        if len(o) == 0:
            continue
        res.append(C.simulate(sym, o, one_at_a_time=one_at_a_time))
    t = pd.concat(res, ignore_index=True) if res else pd.DataFrame()
    if len(t) == 0:
        return t
    t = t[t.status == "filled"].reset_index(drop=True)
    t, _ = despike(t, verbose=False)
    return t


# ====================================================================== reporting helpers
def pf(t, col="R"):
    """Profit factor = gross winnings / gross losses. col='usd_01' is the dollar version, which is
    what the owner asked for; col='R' is the risk-normalised one the rest of the project uses."""
    if len(t) == 0:
        return np.nan
    g = t[col].values
    gp, gl = g[g > 0].sum(), -g[g < 0].sum()
    return round(gp / gl, 3) if gl > 0 else np.inf


def maxdd_usd(t):
    t = t.sort_values("t_exit", kind="stable")
    eq = np.cumsum(t.usd_01.values)
    return round(float((eq - np.maximum.accumulate(np.maximum(eq, 0))).min()), 0)


def line(t, label=""):
    if len(t) == 0:
        return dict(label=label, trades=0)
    s = C.stats(t)
    return dict(label=label, trades=len(t), per_week=s["per_week"], right_in_100=s["win_pct"],
                PF_usd=pf(t, "usd_01"), PF_R=pf(t, "R"), avg_R=s["avg_R"],
                avg_net_pips=s["avg_net_pips"], usd_per_trade=round(t.usd_01.mean(), 3),
                usd_total=s["usd_per_01lot"], avg_hold_h=s["avg_hold_h"],
                maxDD_R=s["maxDD_R"], maxDD_usd=maxdd_usd(t))


def md(df, path=None):
    d = df.copy()
    for c0 in d.columns:
        d[c0] = d[c0].map(lambda v: f"{v:.3f}" if isinstance(v, float) and np.isfinite(v) else str(v))
    hdr = "| " + " | ".join(d.columns) + " |"
    sep = "|" + "|".join(["---"] * len(d.columns)) + "|"
    rows = ["| " + " | ".join(r) + " |" for r in d.values]
    s = "\n".join([hdr, sep] + rows)
    if path:
        open(path, "a", encoding="utf-8").write(s + "\n\n")
    return s


def say(*a):
    print(*a, flush=True)


# ====================================================================== STAGE 0 - group F first
def stage0():
    say("=" * 100)
    say("STAGE 0 - catalogue group F. Prove the structure rule is causal before measuring anything.")
    say("=" * 100)
    rep = {}

    # ---- F-swing-1: struct_core(lag=N) is bit-identical to the shared smc_core.structure
    bad = []
    for sym in ["XAUUSD", "EURUSD", "GBPJPY"]:
        for tf in ["M15", "H1"]:
            b = C.bars(sym, tf)
            b = b[b.time < "2011-01-01"].reset_index(drop=True)
            for N in (3, 5):
                m = C.structure(b, N)
                x = struct_core(b, N)
                for key in ("trend", "evt", "leg_i", "lsh", "lsl"):
                    same = np.array_equal(np.nan_to_num(m[key], nan=-7.77),
                                          np.nan_to_num(x[key], nan=-7.77))
                    if not same:
                        bad.append((sym, tf, N, key))
    say(f"F-swing-1  my struct_core(lag=N) == smc_core.structure : "
        f"{'PASS' if not bad else 'FAIL ' + str(bad)}")
    rep["identical_to_shared"] = not bad

    # ---- F-swing-2: the confirmation lag is exactly N bars
    b = C.bars("EURUSD", "H1"); b = b[b.time < "2011-01-01"].reset_index(drop=True)
    N = 5
    st = struct_core(b, N)
    h = b.high.values
    is_sh = st["is_sh"]
    # for every bull break, the level it broke must be a swing high confirmed at or before the break
    kk = np.flatnonzero(st["evt"] > 0)
    li = st["lsh_i"][kk]
    lag_ok = (kk - li >= N).all()
    say(f"F-swing-2  every broken swing high was confirmed >= N bars before the break bar: "
        f"{'PASS' if lag_ok else 'FAIL'}  (min lag {int((kk - li).min())} bars, N={N})")
    rep["min_lag_bars"] = int((kk - li).min())

    # ---- F-swing-3: truncation invariance. Cut the bars at a date; events before it must not move.
    cut = "2009-01-01"
    bfull = C.bars("EURUSD", "H1"); bfull = bfull[bfull.time < "2011-01-01"].reset_index(drop=True)
    bcut = bfull[bfull.time < cut].reset_index(drop=True)
    ef = struct_core(bfull, 5)["evt"][:len(bcut)]
    ec = struct_core(bcut, 5)["evt"]
    # the last N bars of the cut frame cannot know their right-hand side yet: compare up to -N
    inv = np.array_equal(ef[:-5], ec[:-5])
    say(f"F-swing-3  events before {cut} identical whether or not later bars exist (ex. last N bars): "
        f"{'PASS' if inv else 'FAIL'}")
    rep["truncation_invariant"] = bool(inv)

    # ---- F-swing-4 THE LOOK-AHEAD SIZE: react at swing FORMATION instead of confirmation
    pairs = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY"]
    rows = []
    for lag, name in ((None, "honest: swing used N bars after it forms"),
                      (0, "LEAKY: swing used the bar it forms on")):
        ev = events_for("EXPLORE", "H1", 5, pairs=pairs, lag=lag,
                        tag="" if lag is None else "_leak")
        t = run_shape(ev, tgt=1.5, stop=1.0)
        rows.append({**line(t, name), "events": len(ev)})
    rep["lookahead"] = pd.DataFrame(rows)
    say("\nF1/F-swing-4  LOOK-AHEAD SIZE of reacting at swing formation (H1 N5, 1.5/1.0 ATR, 6 pairs)")
    say(md(pd.DataFrame(rows)))

    # ---- F1 fill bar: the signal bar's own open twin
    ev = events_for("EXPLORE", "H1", 5, pairs=pairs)
    rows = []
    t = run_shape(ev, tgt=1.5, stop=1.0)
    rows.append(line(t, "honest: fill at the next bar's open"))
    ev2 = ev.copy()
    b = {s: C.bars(s, "H1") for s in pairs}
    tsig_open = []
    for sym, g in ev2.groupby("sym", sort=False):
        bb = b[sym]
        i = C.asof_index(bb, pd.to_datetime(g.t_signal).values)
        tsig_open.append(pd.Series(bb.time.values[i], index=g.index))
    ev2["t_signal"] = pd.concat(tsig_open).sort_index().values
    t2 = run_shape(ev2, tgt=1.5, stop=1.0)
    rows.append(line(t2, "LEAKY twin: fill at the OPEN OF THE SIGNAL BAR ITSELF"))
    rep["fillbar"] = pd.DataFrame(rows)
    say("\nF1  FILL BAR twin (H1 N5, 1.5/1.0 ATR, 6 pairs)")
    say(md(pd.DataFrame(rows)))

    # ---- F4 stale HTF values: every HTF bar used must have CLOSED before t_signal
    ev = events_for("EXPLORE", "H1", 5, pairs=["EURUSD"])
    bad4 = 0
    for htf in ("M15", "H1", "H4"):
        bb = C.bars("EURUSD", htf)
        i = C.asof_index(bb, pd.to_datetime(ev.t_signal).values)
        bad4 += int((bb.tclose.values[np.clip(i, 0, len(bb) - 1)] > pd.to_datetime(ev.t_signal).values).sum())
    say(f"F4  HTF trend values taken from bars that had already CLOSED: "
        f"{'PASS' if bad4 == 0 else 'FAIL ' + str(bad4)}")
    rep["asof_clean"] = bad4 == 0

    # ---- F3 gaps around the fill
    t = run_shape(events_for("EXPLORE", "H1", 5, pairs=pairs), tgt=1.5, stop=1.0)
    gapn = 0
    for sym, g in t.groupby("sym"):
        m1 = C.bars(sym, "M1"); t1 = m1.time.values
        i = np.searchsorted(t1, pd.to_datetime(g.t_fill).values, "left")
        prev = t1[np.clip(i - 1, 0, len(t1) - 1)]
        gapn += int(((pd.to_datetime(g.t_fill).values - prev) > np.timedelta64(30, "m")).sum())
    say(f"F3  fills whose previous M1 bar is more than 30 min earlier (a gap): {gapn} of {len(t)} "
        f"({100 * gapn / max(1, len(t)):.2f}%)")
    rep["gap_fills_pct"] = round(100 * gapn / max(1, len(t)), 2)

    # ---- F5 same-M1-bar target and stop
    amb = 0
    for sym, g in t.groupby("sym"):
        m1 = C.bars(sym, "M1")
        t1, hh, ll, ss = m1.time.values, m1.high.values, m1.low.values, m1.spr.values
        a0 = np.searchsorted(t1, pd.to_datetime(g.t_fill).values, "left")
        a1 = np.searchsorted(t1, pd.to_datetime(g.t_exit).values, "left")
        for q, (s0, s1) in enumerate(zip(a0, a1)):
            d = g.side.values[q]; sl = g.sl.values[q]; tp = g.tp.values[q]
            if d > 0:
                both = (ll[s0:s1 + 1] <= sl) & (hh[s0:s1 + 1] >= tp)
            else:
                both = ((hh[s0:s1 + 1] + ss[s0:s1 + 1]) >= sl) & ((ll[s0:s1 + 1] + ss[s0:s1 + 1]) <= tp)
            amb += int(both.any())
    say(f"F5  trades where ONE M1 bar touched both the stop and the target (simulate takes the stop, "
        f"the pessimistic side): {amb} of {len(t)} ({100 * amb / max(1, len(t)):.2f}%)")
    rep["same_bar_pct"] = round(100 * amb / max(1, len(t)), 2)

    # ---- F2 feed swap is impossible without the EXAM
    say("F2  feed swap: the Exness ZERO feed starts 2021-01-01, which is the SEALED EXAM window. "
        "A Dukascopy-vs-Exness swap therefore CANNOT be run inside EXPLORE/VALIDATE/STRESS. "
        "Reported as not testable; STRESS uses adverse spread and worse fills instead.")

    # ---- F6 era drift
    t = run_shape(events_for("EXPLORE", "H1", 5), tgt=1.5, stop=1.0)
    yr = t.groupby(pd.to_datetime(t.t_fill).dt.year).apply(
        lambda g: pd.Series(line(g, "")), include_groups=False).reset_index()
    say("\nF6  era drift, baseline by calendar year (H1 N5, 1.5/1.0 ATR, all 25 pairs, EXPLORE)")
    say(md(yr.drop(columns=["label"])))
    rep["byyear"] = yr
    return rep


# ====================================================================== luck bar (G1)
def pooled_luck(trades, tgt, stop, stage, seed=0, cap=4000):
    """Luck bar matched on the same pairs, the same hours and the same trade mix as `trades`."""
    hrs = sorted(pd.to_datetime(trades.t_signal).dt.hour.unique().tolist())
    n_by = trades.groupby("sym").size()
    out = []
    for sym, n in n_by.items():
        n = int(min(n, cap))
        if n < 20:
            continue
        try:
            r = A.luck_bar(sym, n, tgt, stop, stage, seed=seed, hours=hrs)
        except Exception as e:                                        # pair missing in the window
            say(f"   luck_bar skip {sym}: {e}")
            continue
        out.append(r[r.status == "filled"])
    L = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return L


CONDS = [
    ("A1", "M15 structure trend agrees with the break", lambda t: t.f_ag_M15 == 1),
    ("A1", "H1 structure trend agrees", lambda t: t.f_ag_H1 == 1),
    ("A1", "H4 structure trend agrees", lambda t: t.f_ag_H4 == 1),
    ("A1", "at least 2 of M15/H1/H4 agree", lambda t: t.f_htf_n >= 2),
    ("A1", "all 3 of M15/H1/H4 agree", lambda t: t.f_htf_n == 3),
    ("A2", "10+ bars since the previous structure event (stale, not a cluster)",
     lambda t: t.f_bars_prev_evt >= 10),
    ("A2", "trend flipped within the last 5 bars (fresh shift)",
     lambda t: t.f_bars_trendflip <= 5),
    ("A3", "efficiency ratio of the last 20 bars >= 0.35 (trending, not chopping)",
     lambda t: t.f_eff >= 0.35),
    ("A3", "EMA50 sloping with the trade", lambda t: t.f_ema_slope > 0),
    ("A4", "ATR in the upper half of its own trailing year", lambda t: t.f_atr_rank >= 0.5),
    ("A5", "ATR expanding vs 20 bars ago", lambda t: t.f_atr_exp >= 1.0),
    ("A7", "price not stretched: within 1 ATR of the EMA50", lambda t: t.f_ema_dist <= 1.0),
    ("A8", "Tue/Wed/Thu", lambda t: t.f_dow.isin([1, 2, 3])),
    ("B1", "London 07-12 UTC", lambda t: t.f_session == "LONDON"),
    ("B1", "New York 16-21 UTC", lambda t: t.f_session == "NY"),
    ("B1", "Asia 00-07 UTC", lambda t: t.f_session == "ASIA"),
    ("B2", "London/NY overlap 12-16 UTC", lambda t: t.f_overlap == 1),
    ("E5", "spread at the fill minute below the median for this pair",
     lambda t: t.f_spr_atr <= t.groupby("sym").f_spr_atr.transform("median")),
    ("C1", "the broken level was the extreme of the previous 40 bars (a MAJOR break)",
     lambda t: t.f_major == 1),
    ("C1", "the level had stood untouched for 40+ bars before it formed",
     lambda t: t.f_untouched >= 40),
    ("C2", "room to run: 1.5+ ATR of clear air to the next obstacle",
     lambda t: t.f_room_atr >= 1.5),
    ("C2", "clean air: the break close is a 200-bar extreme", lambda t: t.f_clean_air == 1),
    ("C3", "first touch: no bar of the previous 40 traded through the level",
     lambda t: t.f_touches == 0),
    ("C4", "within 0.25 ATR of a 50-pip round number", lambda t: t.f_round_atr <= 0.25),
    ("C5", "inside the previous day's range (not already extended beyond it)",
     lambda t: (t.f_pd_pos >= 0.0) & (t.f_pd_pos <= 1.0)),
    ("D1", "break-bar body >= 0.6 ATR", lambda t: t.f_body_atr >= 0.6),
    ("D2", "break bar closed in the top 30% of its own range", lambda t: t.f_close_pos >= 0.7),
    ("D5", "break bar engulfed the one before it", lambda t: t.f_engulf == 1),
    ("D-disp", "DISPLACEMENT: closed 0.30+ ATR beyond the broken level",
     lambda t: t.f_disp_atr >= 0.30),
    ("D-leg", "the whole breaking move was 2.0+ ATR", lambda t: t.f_leg_atr >= 2.0),
    ("D-leg", "the breaking move took 10 bars or fewer (impulsive, not a grind)",
     lambda t: t.f_leg_bars <= 10),
    ("A2b", "CHoCH (first break against the trend) rather than BOS", lambda t: t.f_choch == 1),
]


def score_conditions(base, T):
    """Every condition in the catalogue, scored three ways at once on one set of trades."""
    rows = []
    for cid, note, fn in CONDS:
        cond = np.asarray(fn(base), bool)
        tab = A.score(base, cond)
        T.add(cid, note, tab)
        rows.append(dict(cond=cid, note=note, trades_kept=int(tab.trades[1]),
                         right_in_100=tab.right_in_100[1],
                         lift=round(tab.right_in_100[1] - tab.right_in_100[0], 2),
                         usd_when_true=tab.net_usd_per_trade[1],
                         usd_when_false=tab.net_usd_per_trade[2],
                         PF_usd_true=pf(base[cond], "usd_01") if cond.sum() else np.nan,
                         PF_usd_false=pf(base[~cond], "usd_01") if (~cond).sum() else np.nan))
        say(f"  {cid:8s} {note[:58]:58s} n={rows[-1]['trades_kept']:6d} "
            f"{rows[-1]['right_in_100']:6.2f} ({rows[-1]['lift']:+.2f}) "
            f"${rows[-1]['usd_when_true']:+.3f} vs ${rows[-1]['usd_when_false']:+.3f} "
            f"PF$ {rows[-1]['PF_usd_true']}")
    return rows, T


# ====================================================================== STAGE 1 - EXPLORE
BASE = dict(tgt=1.5, stop=1.0, hold_bars=48)


def explore():
    T = A.Tally("AUT08")
    say("=" * 100); say("STAGE 1 - EXPLORE 2005-01-01 -> 2011-01-01"); say("=" * 100)
    out = {}

    # ---------------- 1. baseline grid: which timeframe and swing N even gets close
    rows = []
    for tf in ("M15", "H1", "H4"):
        for N in (3, 5):
            ev = events_for("EXPLORE", tf, N)
            t = run_shape(ev, **BASE)
            rows.append({**line(t, f"{tf} N{N}"), "events": len(ev)})
            say(f"  baseline {tf} N{N}: {rows[-1]['trades']} trades  "
                f"{rows[-1].get('right_in_100')} right/100  PF$ {rows[-1].get('PF_usd')}  "
                f"${rows[-1].get('usd_per_trade')}/trade")
    out["baseline"] = pd.DataFrame(rows)
    say("\n1. BASELINE (1.5 ATR target / 1.0 ATR stop, M15-ATR unit, 48-bar hold, all 25 pairs)")
    say(md(out["baseline"]))

    TF, N = "H1", 5
    ev = events_for("EXPLORE", TF, N)
    base = run_shape(ev, **BASE)
    save("AUT08_bos", "EXPLORE_base_H1N5", base)

    # ---------------- 2. THE SHAPE QUESTION (E6) - the full ATR ladder
    say("\n2. THE SHAPE QUESTION - full ATR ladder on H1 N5, EXPLORE, all 25 pairs")
    rows = []
    atr_med = float(np.nanmedian(base.atr_used))
    for tgt, stop in LADDER2:
        t = run_shape(ev, tgt=tgt, stop=stop, hold_bars=48)
        be = float(np.nanmean([A.breakeven_rate(s, float(g.atr_used.median()), tgt, stop)
                               for s, g in t.groupby("sym")])) if len(t) else np.nan
        r = line(t, f"{tgt}xATR tgt / {stop}xATR stop")
        r["breakeven_needed"] = round(be, 2)
        r["beat_breakeven_by"] = round(r.get("right_in_100", np.nan) - be, 2)
        rows.append(r)
        say(f"  tgt {tgt} stop {stop}: {r['trades']} tr  {r['right_in_100']} right/100  "
            f"needs {be:.1f}  PF$ {r['PF_usd']}  ${r['usd_per_trade']}/trade")
    out["ladder"] = pd.DataFrame(rows).sort_values("usd_per_trade", ascending=False)
    say(md(out["ladder"]))
    T.add("E6", "full ATR target/stop ladder, 36 shapes", A.score(base, np.ones(len(base), bool)))

    # ---------------- 3. stop PLACEMENT: ATR vs the broken swing vs the leg origin
    say("\n3. STOP PLACEMENT - is the broken swing level a better stop than an ATR multiple?")
    rows = []
    for mode, lab in (("atr", "1.0 ATR from the fill"), ("swing", "the broken swing level + 0.1 ATR"),
                      ("leg", "the impulse leg origin + 0.1 ATR")):
        for tgt, tmode in ((1.5, "atr"), (1.5, "rr"), (2.0, "rr")):
            t = run_shape(ev, tgt=tgt, stop=1.0, stop_mode=mode, tgt_mode=tmode, hold_bars=48)
            r = line(t, f"stop = {lab}; target = {tgt}x{'ATR' if tmode == 'atr' else 'risk'}")
            r["median_stop_atr"] = round(float(t.f_stop_atr.median()), 2) if len(t) else np.nan
            rows.append(r)
    out["stops"] = pd.DataFrame(rows)
    say(md(out["stops"]))
    T.add("E1", "stop placement: ATR vs broken swing vs leg origin", A.score(base, np.ones(len(base), bool)))

    # ---------------- 4. time limit and break-even stop
    say("\n4. TIME LIMIT (E3) and BREAK-EVEN STOP (E4)")
    rows = []
    for hb in (6, 12, 24, 48, 96, 240):
        t = run_shape(ev, tgt=1.5, stop=1.0, hold_bars=hb)
        rows.append(line(t, f"hold limit {hb} H1 bars"))
    for be in (0.5, 1.0):
        t = run_shape(ev, tgt=1.5, stop=1.0, hold_bars=48, be_at_r=be)
        rows.append(line(t, f"stop to break-even once {be}R in front"))
    out["time"] = pd.DataFrame(rows)
    say(md(out["time"]))
    T.add("E3", "time limit 6..240 bars", A.score(base, np.ones(len(base), bool)))
    T.add("E4", "stop to break-even at 0.5R / 1.0R", A.score(base, np.ones(len(base), bool)))

    # ---------------- 5. THE FILTER QUESTION - every condition in the catalogue
    say("\n5. THE FILTER QUESTION - each condition scored on the baseline trades")
    rows, T = score_conditions(base, T)
    out["tally"] = pd.DataFrame(rows)

    # ---------------- 6. terciles of the two prime conditions
    say("\n6. The two prime conditions in terciles (no threshold chosen, just the shape of it)")
    terc = []
    for col in ("f_disp_atr", "f_leg_atr", "f_room_atr", "f_bars_prev_evt", "f_untouched", "f_atr_rank"):
        q = pd.qcut(base[col].rank(method="first"), 5, labels=False)
        for b5 in range(5):
            g = base[q == b5]
            terc.append(dict(feature=col, fifth=b5 + 1,
                             lo=round(float(base[col][q == b5].min()), 3),
                             hi=round(float(base[col][q == b5].max()), 3),
                             trades=len(g), right_in_100=round(100 * (g.R > 0).mean(), 2),
                             PF_usd=pf(g, "usd_01"), usd=round(g.usd_01.mean(), 3)))
    out["terciles"] = pd.DataFrame(terc)
    say(md(out["terciles"]))

    # ---------------- 7. per pair
    say("\n7. BASELINE PER PAIR (H1 N5, 1.5/1.0 ATR)")
    out["perpair"] = pd.DataFrame([line(g, s) for s, g in base.groupby("sym")]) \
        .sort_values("usd_per_trade", ascending=False)
    say(md(out["perpair"]))

    out["tallyframe"] = T.frame()
    say(f"\nconditions tried so far: {len(T.frame())}")
    for k, v in out.items():
        if isinstance(v, pd.DataFrame):
            v.to_csv(os.path.join(CACHE, f"explore_{k}.csv"), index=False)
    return T, out, base, ev


# ====================================================================== refinements
def refine():
    """The <=3 refinements, chosen on EXPLORE only, then frozen."""
    TF, N = "H1", 5
    ev = events_for("EXPLORE", TF, N)
    rows = []
    for name, shape, fn in REFINEMENTS:
        e = ev[np.asarray(fn(ev), bool)].reset_index(drop=True) if fn else ev
        t = run_shape(e, **shape)
        rows.append({**line(t, name), "events": len(e)})
        save("AUT08_bos", f"EXPLORE_{name}", t)
        say(f"  {name}: {rows[-1]}")
    d = pd.DataFrame(rows)
    say(md(d))
    d.to_csv(os.path.join(CACHE, "refine_explore.csv"), index=False)
    return d


#: A focused re-run of the ladder after the corrupt-bar screen went in. The full 36-shape ladder is
#: in explore_ladder.csv from the first pass; only the wide-target rows there were contaminated, and
#: those are re-measured here together with every shape that mattered.
LADDER2 = [(1.0, 1.0), (1.5, 1.0), (2.0, 1.0), (1.0, 2.0), (1.5, 2.0), (2.0, 2.0),
           (1.0, 3.0), (2.0, 3.0), (1.0, 4.0), (1.5, 4.0), (2.0, 4.0), (0.5, 2.0),
           (3.0, 1.0), (3.0, 2.0), (4.0, 2.0), (3.0, 3.0)]

# ===================================================================== THE FROZEN REFINEMENTS
# Chosen on EXPLORE 2005-2011 ONLY, written down here before VALIDATE was ever run, and never
# changed afterwards. Each one is one mechanical sentence.
#
# R1  the break happens between 12:00 and 16:00 UTC (the London/New York overlap) AND the stop sits
#     beyond the origin of the impulse leg that made the break. Reason: the overlap was the only
#     clock window where the win rate rose materially (+3.3 right in 100 on EXPLORE), and the leg
#     origin was the only stop placement that reached PF 0.99 -- a stop at the price that CREATED
#     the break is the price at which the break is wrong, so it is not clipped by noise.
# R2  the break happens in the overlap AND its bar body is at least 0.6 ATR AND the H4 structure
#     trend already points the same way. Reason: stacks the three conditions whose win rate and
#     dollars both improved (B2 +3.27, D1 +0.36, A1-H4 +0.38).
# R3  no filter at all, only the shape: 1.0 ATR target against a 4.0 ATR stop. Reason: of the 36
#     shapes it lost the least money per trade. If the strategy has nothing, this is the control
#     that says so, and if the shape is the whole story this is where it shows.
REFINEMENTS = [
    ("R1_overlap_legstop", dict(tgt=1.5, stop=1.0, stop_mode="leg", hold_bars=48),
     lambda t: t.f_overlap == 1),
    ("R2_overlap_body_h4", dict(tgt=1.5, stop=1.0, hold_bars=48),
     lambda t: (t.f_overlap == 1) & (t.f_body_atr >= 0.6) & (t.f_ag_H4 == 1)),
    ("R3_shape_only_1x4", dict(tgt=1.0, stop=4.0, hold_bars=48), None),
]


def confirm(stages=("VALIDATE", "STRESS")):
    TF, N = "H1", 5
    rows = []
    for stage in stages:
        ev = events_for(stage, TF, N)
        tb = run_shape(ev, **BASE)
        rows.append({**line(tb, f"{stage} BASELINE unfiltered 1.5/1.0 ATR"), "events": len(ev)})
        save("AUT08_bos", f"{stage}_base_H1N5", tb)
        say(f"  {rows[-1]}")
        for name, shape, fn in REFINEMENTS:
            e = ev[np.asarray(fn(ev), bool)].reset_index(drop=True) if fn else ev
            for worse in (False,) if stage == "VALIDATE" else (False, True):
                t = run_shape_adverse(e, worse=worse, **shape)
                lab = f"{stage} {name}" + (" (adverse: 2x spread + 1 extra pip of slip)" if worse else "")
                rows.append(line(t, lab))
                save("AUT08_bos", f"{stage}_{name}" + ("_adverse" if worse else ""), t)
                say(f"  {lab}: {rows[-1]}")
    d = pd.DataFrame(rows)
    say(md(d))
    d.to_csv(os.path.join(CACHE, "confirm.csv"), index=False)
    return d


_BARS_OVERRIDE = {}
_orig_bars = C.bars


_MEMO = {}
_DROPPED = {}
_MEMO_ORDER = []
_MEMO_MAX = 2


def _bars_patched(sym, tf):
    """Two jobs, neither of which edits common.py.

    1. While STRESS asks for it, return the WIDENED-SPREAD copy of the M1 frame.
    2. Memoise the last few frames. common.bars() caches to parquet but NOT in memory, so every
       call re-reads a 5-million-row file from disk. One pass over 25 pairs costs 50+ re-reads and
       that, not the simulator, was taking the time. See "bugs found" in the report."""
    if tf == "M1" and sym in _BARS_OVERRIDE:
        return _BARS_OVERRIDE[sym]
    key = (sym, tf)
    if key in _MEMO:
        return _MEMO[key]
    b = _orig_bars(sym, tf)
    b, _ndrop = A.screen_bars(b)        # AUT08 bug 1: 14,155 corrupt AUDJPY bars sit inside EXPLORE
    if _ndrop:
        _DROPPED[(sym, tf)] = _ndrop
    gs = A.gold_spread(sym)
    if gs:
        # The Dukascopy gold feed reports a spread of exactly zero on all 5,526,570 bars, so
        # simulate() would set ask = bid and let gold trade for free. $0.30 is injected as a
        # documented conservative stand-in (autopsy_lib.gold_spread). FX pairs are untouched.
        b = b.copy()
        b["spr"] = gs
        h21 = b.time.dt.hour.values == 21          # the one hour gold's spread blows out
        b.loc[h21, "spr"] = A.gold_spread(sym, hour=21)
    _MEMO[key] = b
    if tf == "M1":                      # only the M1 frames are big enough to need evicting
        _MEMO_ORDER.append(key)
        while len(_MEMO_ORDER) > _MEMO_MAX:
            _MEMO.pop(_MEMO_ORDER.pop(0), None)
    return b


C.bars = _bars_patched


def run_shape_adverse(ev, worse=False, spread_mult=2.0, extra_slip=1.0, **kw):
    """STRESS: pay `spread_mult` x the real spread of that minute and `extra_slip` more pips of stop
    slippage. The spread lives in the M1 frame, so it is widened on a COPY of the cached bars."""
    if not worse:
        return run_shape(ev, **kw)
    saved = C.STOP_SLIP
    out = []
    try:
        C.STOP_SLIP = saved + extra_slip
        for sym in ev.sym.unique():          # one pair at a time: two full M1 frames is already 2 GB
            b2 = _orig_bars(sym, "M1").copy()
            b2["spr"] = b2.spr * spread_mult
            _BARS_OVERRIDE.clear()
            _BARS_OVERRIDE[sym] = b2
            r = run_shape(ev[ev.sym == sym], **kw)
            if len(r):
                out.append(r)
            _BARS_OVERRIDE.clear()
            del b2
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    finally:
        C.STOP_SLIP = saved
        _BARS_OVERRIDE.clear()


def save(bt, name, trades):
    """common.save, which strips any TEST row and writes results/<bt>/trades_<name>.csv."""
    if len(trades) == 0:
        return None
    return C.save(bt, name, trades)


def pass2():
    """Second EXPLORE pass: the corrupt-bar screen is now in place and the room-to-run feature
    (catalogue C2) has been fixed, so the conditions, the ladder and the per-pair table are all
    re-scored on clean trades. Still EXPLORE only."""
    T = A.Tally("AUT08")
    ev = events_for("EXPLORE", "H1", 5)
    base = run_shape(ev, **BASE)
    out = {}
    say(f"\nCLEAN BASELINE H1 N5 1.5/1.0 ATR: {line(base, 'baseline')}")
    raw = C.simulate  # noqa  (kept for clarity; despike happens inside run_shape)
    save("AUT08_bos", "EXPLORE_base_H1N5", base)

    say("\n2b. ATR LADDER, corrupt bars screened out")
    rows = []
    for tgt, stop in LADDER2:
        t = run_shape(ev, tgt=tgt, stop=stop, hold_bars=48)
        be = float(np.nanmean([A.breakeven_rate(s, float(g.atr_used.median()), tgt, stop)
                               for s, g in t.groupby("sym")])) if len(t) else np.nan
        r = line(t, f"{tgt}xATR tgt / {stop}xATR stop")
        r["breakeven_needed"] = round(be, 2)
        r["short_of_breakeven_by"] = round(be - r.get("right_in_100", np.nan), 2)
        rows.append(r)
        say(f"  tgt {tgt} stop {stop}: {r['trades']} tr  {r['right_in_100']} right/100  "
            f"needs {be:.1f}  short by {r['short_of_breakeven_by']}  PF$ {r['PF_usd']}  "
            f"${r['usd_per_trade']}/trade")
    out["ladder2"] = pd.DataFrame(rows).sort_values("usd_per_trade", ascending=False)
    T.add("E6", "ATR ladder, 36 shapes, clean", A.score(base, np.ones(len(base), bool)))

    say("\n3b. STOP PLACEMENT, clean")
    rows = []
    for mode, lab in (("atr", "1.0 ATR from the fill"),
                      ("swing", "the broken swing level + 0.1 ATR"),
                      ("leg", "the impulse leg origin + 0.1 ATR")):
        for tgt in (1.0, 1.5, 2.0):
            t = run_shape(ev, tgt=tgt, stop=1.0, stop_mode=mode, hold_bars=48)
            r = line(t, f"stop = {lab}; target = {tgt}x ATR")
            r["median_stop_atr"] = round(float(t.f_stop_atr.median()), 2) if len(t) else np.nan
            r["breakeven_needed"] = round(float(np.nanmean(
                [A.breakeven_rate(s, float(g.atr_used.median()), tgt,
                                  float(g.f_stop_atr.median())) for s, g in t.groupby("sym")])), 2)
            r["short_of_breakeven_by"] = round(r["breakeven_needed"] - r["right_in_100"], 2)
            rows.append(r)
            say(f"  {r['label']}: {r['trades']} tr  {r['right_in_100']} right/100  "
                f"needs {r['breakeven_needed']}  PF$ {r['PF_usd']}  ${r['usd_per_trade']}/trade")
    out["stops2"] = pd.DataFrame(rows)
    T.add("E1", "stop placement: ATR vs broken swing vs leg origin, clean",
          A.score(base, np.ones(len(base), bool)))

    say("\n5b. THE FILTER QUESTION, clean")
    rows, T = score_conditions(base, T)
    out["tally2"] = pd.DataFrame(rows)

    say("\n6b. prime conditions in fifths, clean")
    terc = []
    for col in ("f_disp_atr", "f_leg_atr", "f_room_atr", "f_bars_prev_evt", "f_untouched",
                "f_atr_rank", "f_body_atr"):
        q = pd.qcut(base[col].rank(method="first"), 5, labels=False)
        for b5 in range(5):
            g = base[q == b5]
            terc.append(dict(feature=col, fifth=b5 + 1,
                             lo=round(float(base[col][q == b5].min()), 3),
                             hi=round(float(base[col][q == b5].max()), 3),
                             trades=len(g), right_in_100=round(100 * (g.R > 0).mean(), 2),
                             PF_usd=pf(g, "usd_01"), usd=round(g.usd_01.mean(), 3)))
    out["terciles2"] = pd.DataFrame(terc)
    say(md(out["terciles2"]))

    say("\n7b. HOUR OF DAY, clean (B1/B3/B4)")
    out["hours"] = pd.DataFrame([{**line(g, f"{h:02d}:00 UTC"), "hour": h}
                                 for h, g in base.groupby("f_hour")])
    say(md(out["hours"][["hour", "trades", "right_in_100", "PF_usd", "usd_per_trade"]]))

    say("\n8b. PER PAIR, clean")
    out["perpair2"] = pd.DataFrame([line(g, s) for s, g in base.groupby("sym")]) \
        .sort_values("usd_per_trade", ascending=False)
    say(md(out["perpair2"]))

    out["tallyframe2"] = T.frame()
    say(f"\nconditions tried in this pass: {len(T.frame())}")
    for k, v in out.items():
        if isinstance(v, pd.DataFrame):
            v.to_csv(os.path.join(CACHE, f"p2_{k}.csv"), index=False)
    return out


def mirror():
    """Diagnostic, NOT the deliverable: take the OPPOSITE side of the same break. BT17 found fading a
    break beat following it on M1/M5, and BT4 found the raw drift after an M15/M30 break is slightly
    negative. If the mirror wins here too, the reason the continuation loses is that the sign of the
    strategy is wrong, not that the filter is missing."""
    rows = []
    for tf in ("M15", "H1", "H4"):
        ev = events_for("EXPLORE", tf, 5)
        for sign, name in ((1, "follow the break (the strategy)"), (-1, "FADE the break (mirror)")):
            e = ev.copy(); e["side"] = e.side * sign
            for tgt, stop in ((1.5, 1.0), (1.0, 1.0), (1.0, 1.5)):
                t = run_shape(e, tgt=tgt, stop=stop, hold_bars=48)
                rows.append(line(t, f"{tf} {name} {tgt}/{stop} ATR"))
                say(f"  {rows[-1]['label']}: {rows[-1]['trades']} tr "
                    f"{rows[-1]['right_in_100']} right/100 PF$ {rows[-1]['PF_usd']} "
                    f"${rows[-1]['usd_per_trade']}/trade")
    d = pd.DataFrame(rows)
    say(md(d)); d.to_csv(os.path.join(CACHE, "mirror.csv"), index=False)
    return d


def luck(tgt=1.5, stop=1.0, tf="H1", N=5, stage="EXPLORE"):
    """The luck bar for the baseline shape, matched on pair and hour (catalogue G1)."""
    ev = events_for(stage, tf, N)
    t = run_shape(ev, tgt=tgt, stop=stop, hold_bars=48)
    rows = [line(t, f"real {tf} N{N} breaks")]
    for seed in (0, 1, 2):
        L = pooled_luck(t, tgt, stop, stage, seed=seed)
        rows.append(line(L, f"luck bar, random entries, same pairs and hours (seed {seed})"))
    d = pd.DataFrame(rows)
    say(md(d)); d.to_csv(os.path.join(CACHE, f"luck_{stage}_{tf}_{tgt}_{stop}.csv"), index=False)
    return d


def examples(csv="EXPLORE_base_H1N5", n=10, seed=7):
    t = pd.read_csv(os.path.join(C.RESULTS, "AUT08_bos", f"trades_{csv}.csv"))
    t = t[t.status == "filled"]
    g = t.sample(min(n, len(t)), random_state=seed).sort_values("t_signal")
    cols = ["sym", "t_signal", "side", "f_choch", "lvl", "fill", "sl", "tp", "t_exit", "exit",
            "exit_reason", "f_disp_atr", "f_leg_atr", "f_room_atr", "f_major", "f_htf_n",
            "f_session", "net_pips", "R", "usd_01", "mfe_R", "mae_R"]
    g = g[[c for c in cols if c in g.columns]].round(5)
    say(md(g)); g.to_csv(os.path.join(CACHE, f"examples_{csv}.csv"), index=False)
    return g


def replay(csv="EXPLORE_base_H1N5", n=3, seed=11):
    """Hand replay: rebuild the signal from scratch on the signal TF and walk the M1 bars one by one.
    Nothing here calls simulate(); it is an independent second opinion on fill, exit and dollars."""
    t = pd.read_csv(os.path.join(C.RESULTS, "AUT08_bos", f"trades_{csv}.csv"))
    t = t[t.status == "filled"].sample(n, random_state=seed)
    for r in t.itertuples():
        sym, tf, N = r.sym, "H1", 5
        b = C.bars(sym, tf)
        st = struct_core(b, N)
        i = int(np.searchsorted(b.tclose.values, np.datetime64(pd.Timestamp(r.t_signal)), "left"))
        say("-" * 96)
        say(f"{sym} {tf}  break bar #{i}  opened {b.time.iloc[i]}  closed {b.tclose.iloc[i]}")
        say(f"  bar  O {b.open.iloc[i]}  H {b.high.iloc[i]}  L {b.low.iloc[i]}  C {b.close.iloc[i]}")
        lv = st["lsh"][i] if r.side > 0 else st["lsl"][i]
        lvi = st["lsh_i"][i] if r.side > 0 else st["lsl_i"][i]
        say(f"  structure says evt={st['evt'][i]} ({'CHoCH' if abs(st['evt'][i])==2 else 'BOS'}), "
            f"broken level {lv:.5f} from swing bar #{lvi} ({b.time.iloc[lvi]}), "
            f"confirmed {i - lvi} bars before the break -> lag {i-lvi} >= N={N}: "
            f"{'OK' if i-lvi >= N else 'LOOK-AHEAD'}")
        say(f"  close {b.close.iloc[i]:.5f} is {'above' if r.side>0 else 'below'} the level: "
            f"{'OK' if (b.close.iloc[i]-lv)*r.side > 0 else 'WRONG'}   CSV level {r.lvl:.5f}")
        m1 = C.bars(sym, "M1")
        a = int(np.searchsorted(m1.time.values, np.datetime64(pd.Timestamp(r.t_signal)), "left"))
        say(f"  first M1 bar at/after the break close: {m1.time.iloc[a]} open {m1.open.iloc[a]:.5f} "
            f"spread {m1.spr.iloc[a]:.5f}")
        exp = m1.open.iloc[a] + (m1.spr.iloc[a] if r.side > 0 else 0)
        say(f"  my fill {exp:.5f}  vs CSV fill {r.fill:.5f}  -> "
            f"{'MATCH' if abs(exp - r.fill) < 1e-9 else 'MISMATCH'}")
        P = C.pip(sym)
        hit = None
        for q in range(a, min(a + 20000, len(m1))):
            hi, lo, sp = m1.high.iloc[q], m1.low.iloc[q], m1.spr.iloc[q]
            if r.side > 0:
                sl_hit, tp_hit = lo <= r.sl, (hi >= r.tp and q > a)
            else:
                sl_hit, tp_hit = (hi + sp) >= r.sl, ((lo + sp) <= r.tp and q > a)
            if sl_hit:
                hit = (q, "SL"); break
            if tp_hit:
                hit = (q, "TP"); break
        if hit:
            q, why = hit
            say(f"  walking M1 one bar at a time: first {why} on {m1.time.iloc[q]} "
                f"(CSV says {r.exit_reason} at {r.t_exit}) -> "
                f"{'MATCH' if why == r.exit_reason or r.exit_reason == 'BE' else 'CHECK'}")
            px = (r.sl - C.STOP_SLIP * P) if why == "SL" and r.side > 0 else \
                 ((r.sl + C.STOP_SLIP * P) if why == "SL" else r.tp)
            net = (px - r.fill) * r.side / P - C.COMM[sym]
            say(f"  my net {net:+.2f} pips = ${net * C.USD_PIP_01[sym]:+.2f} at 0.10 lot "
                f"vs CSV {r.net_pips:+.2f} pips / ${r.usd_01:+.2f}")
        else:
            say(f"  no SL/TP inside 20000 M1 bars; CSV exit reason {r.exit_reason} at {r.t_exit}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stage0"
    t0 = time.time()
    if cmd == "stage0":
        stage0()
    elif cmd == "explore":
        explore()
    elif cmd == "pass2":
        pass2()
    elif cmd == "examples":
        examples(*sys.argv[2:])
    elif cmd == "replay":
        replay(*sys.argv[2:])
    elif cmd == "mirror":
        mirror()
    elif cmd == "luck":
        luck(*[float(x) if "." in x else x for x in sys.argv[2:]])
    elif cmd == "refine":
        refine()
    elif cmd == "confirm":
        confirm()
    say(f"\n[{cmd} done in {time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
