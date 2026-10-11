"""AUT02 - liquidity sweep reversal, autopsied.

Owner's spec: price wicks past a recent N-bar high or low, then closes back inside the range;
enter on that close, stop beyond the wick.

Read bt/AUTOPSY_BRIEF.md first. Stages: EXPLORE 2005-2011 (tune), VALIDATE 2011-2017 (confirm
only), STRESS 2017-2021 (adverse), EXAM 2021+ SEALED.

Run from the TTXT root:
    set SMC_DATASET=duka & .venv\\Scripts\\python.exe smc_bot\\bt\\aut02_sweep.py <cmd>

Commands: stage0 | base | autopsy | shape | refine | fx | examples

Costs. The Dukascopy gold M1 file carries spread = 0 on every one of its 5,526,570 bars (see the
report's bug list). `common.simulate` would therefore run gold at zero spread. This script injects
a spread itself by wrapping `common.bars` (it does NOT edit common.py): SPREAD_PRICE price units on
every bar, on top of the $1.10 round-trip commission already in common.COMM. Baseline 0.05 price
units (= $1.60 round trip at 0.10 lots, the same figure BT21E used). STRESS triples it to 0.15.
"""
import os
import sys
import json

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                                                     # noqa: E402
import autopsy_lib as A                                                # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT02_sweep")
os.makedirs(OUT, exist_ok=True)
SYM = "XAUUSD"
TF_MIN = dict(M1=1, M5=5, M15=15, M30=30, H1=60, H4=240)

SPREAD_PRICE = 0.05          # price units added to every bar's ask; see the docstring
_WINDOW = None               # (lo, hi) clip applied to every bars() call, for speed and memory
_BCACHE = {}
_ORIG_BARS = C.bars


def bars(sym, tf):
    """common.bars + a real spread + a time clip (keeps the 5.5M-bar M1 frame manageable)."""
    key = (sym, tf, SPREAD_PRICE, _WINDOW)
    if key in _BCACHE:
        return _BCACHE[key]
    b = _ORIG_BARS(sym, tf)
    if _WINDOW is not None:
        lo, hi = _WINDOW
        b = b[(b.time >= lo) & (b.time < hi)]
    b = b.reset_index(drop=True).copy()
    b["spr"] = float(SPREAD_PRICE)
    _BCACHE[key] = b
    return b


C.bars = bars
A.C.bars = bars


def set_window(stage, back_days=420, fwd_days=20):
    """Clip loaded bars to the stage plus warm-up / resolution margins. Signals are still filtered
    to the stage itself; the margin only feeds ATR/EMA warm-up and lets open trades resolve."""
    global _WINDOW
    a, z = A.STAGES[A.gate(stage)]
    _WINDOW = (str(pd.Timestamp(a) - pd.Timedelta(days=back_days)),
               str(pd.Timestamp(z) + pd.Timedelta(days=fwd_days)))
    _BCACHE.clear()


# ----------------------------------------------------------------------------- the signal
def sweep_signals(sym, tf, N, stage, buf=0.05, stop_atr_buf=0.10, tgt_R=2.0, max_hold_h=12.0,
                  cooldown=0):
    """Every sweep-reversal signal inside `stage`, with the whole feature block.

    Sweep, decided on the CLOSED bar i of timeframe `tf`:
        SELL  high[i] > max(high[i-N..i-1]) + buf  and  close[i] < that max
        BUY   low[i]  < min(low[i-N..i-1])  - buf  and  close[i] > that min
    A bar that sweeps both sides is dropped. The N prior bars must be N contiguous real bars.
    Entry: market at the first M1 open after bar i closes.
    Stop: bar i's own wick extreme, pushed stop_atr_buf * ATR(tf,14) further out.
    Target: tgt_R times that risk.
    Every f_* value uses bars up to and including i only; bar i is closed at t_signal.
    """
    tfm = TF_MIN[tf]
    b = bars(sym, tf)
    hi, lo, cl, op = b.high.values, b.low.values, b.close.values, b.open.values
    tv = b.time.values
    n = len(b)
    at = A.atr(b, 14).values
    P = C.pip(sym)

    H = pd.Series(hi).rolling(N).max().shift(1).values          # prior-N extreme, excludes bar i
    L = pd.Series(lo).rolling(N).min().shift(1).values
    H60 = pd.Series(hi).rolling(60).max().shift(1).values
    L60 = pd.Series(lo).rolling(60).min().shift(1).values
    H240 = pd.Series(hi).rolling(240).max().shift(1).values
    L240 = pd.Series(lo).rolling(240).min().shift(1).values
    contig = np.zeros(n, bool)
    contig[N:] = (tv[N:] - tv[:-N]) == np.timedelta64(N * tfm, "m")

    sell = (hi > H + buf) & (cl < H)
    buy = (lo < L - buf) & (cl > L)
    a0, z0 = A.STAGES[stage]
    inwin = (tv >= np.datetime64(a0)) & (tv < np.datetime64(z0))
    ok = contig & inwin & ~(sell & buy) & (sell | buy) & np.isfinite(at) & (at > 0)
    idx = np.flatnonzero(ok)
    if cooldown:
        keep, last = [], {1: -10 ** 9, -1: -10 ** 9}
        for i in idx:
            dd = 1 if buy[i] else -1
            if i - last[dd] >= cooldown:
                keep.append(i)
                last[dd] = i
        idx = np.array(keep, dtype=int)
    if len(idx) == 0:
        return pd.DataFrame()

    d = np.where(buy[idx], 1, -1)                                # fade: swept low -> BUY
    lvl = np.where(d == 1, L[idx], H[idx])
    ext = np.where(d == 1, lo[idx], hi[idx])                     # the wick the stop sits beyond
    atr_i = at[idx]
    rng = (hi - lo)[idx]
    body = np.abs(cl - op)[idx]
    c, oo = cl[idx], op[idx]

    sl = ext - d * stop_atr_buf * atr_i
    risk = (c - sl) * d                                          # risk measured from the close
    tp = c + d * tgt_R * risk

    f = {}
    # ---- D: the trigger candle -------------------------------------------------------------
    f["f_depth_atr"] = (ext - lvl) * (-d) / atr_i                            # D3 sweep depth
    f["f_depth_rng"] = f["f_depth_atr"] * atr_i / np.maximum(rng, 1e-9)
    f["f_rej"] = np.where(d == 1, c - lo[idx], hi[idx] - c) / np.maximum(rng, 1e-9)   # D2 close pos
    wick = np.where(d == 1, np.minimum(oo, c) - lo[idx], hi[idx] - np.maximum(oo, c))
    f["f_wick_body"] = wick / np.maximum(body, 0.01)                         # D2 wick:body
    f["f_body_atr"] = body / atr_i                                           # D1
    f["f_rng_atr"] = rng / atr_i
    f["f_with_body"] = ((c - oo) * d > 0).astype(float)          # body already pointing to the fade
    pr = np.maximum(idx - 1, 0)
    f["f_engulf"] = ((hi[idx] >= hi[pr]) & (lo[idx] <= lo[pr])).astype(float)          # D5
    # ---- C: level quality (loop; N-wide slices per signal) ---------------------------------
    age = np.zeros(len(idx))
    touch = np.zeros(len(idx))
    for k, i in enumerate(idx):
        w = slice(i - N, i)
        if d[k] == 1:
            j = int(np.argmin(lo[w]))
            touch[k] = (lo[w] <= lvl[k] + 0.10 * atr_i[k]).sum()
        else:
            j = int(np.argmax(hi[w]))
            touch[k] = (hi[w] >= lvl[k] - 0.10 * atr_i[k]).sum()
        age[k] = i - (i - N + j)
    f["f_lvl_age"] = age                                         # D4 bars since the level was set
    f["f_touches"] = touch                                       # C3 1 = fresh, 3+ = a worn level
    f["f_also_60"] = np.where(d == 1, ext < L60[idx], ext > H60[idx]).astype(float)    # C1
    f["f_also_240"] = np.where(d == 1, ext < L240[idx], ext > H240[idx]).astype(float)
    opp = np.where(d == 1, H[idx], L[idx])
    f["f_room_atr"] = (opp - c) * d / atr_i                      # C1 room to the opposing side
    f["f_room_vs_tgt"] = f["f_room_atr"] / np.maximum((tp - c) * d / atr_i, 1e-9)      # C2
    f["f_round_1"] = (np.abs(lvl - np.round(lvl)) <= 0.15).astype(float)               # C4
    f["f_round_half"] = (np.abs(lvl * 2 - np.round(lvl * 2)) / 2 <= 0.15).astype(float)
    # sweeps already made recently: has this side's liquidity already been taken? (C1 / C3)
    same_recent = np.zeros(len(idx))
    any_recent = np.zeros(len(idx))
    pos = {int(i): k for k, i in enumerate(idx)}
    for k, i in enumerate(idx):
        lo_i = i - 20
        prev = idx[(idx >= lo_i) & (idx < i)]
        any_recent[k] = len(prev)
        same_recent[k] = int((d[[pos[int(p)] for p in prev]] == d[k]).sum()) if len(prev) else 0
    f["f_prev_sweep_same"] = same_recent
    f["f_prev_sweep_any"] = any_recent
    # ---- A: regime, HTF values read through asof_index (F4) --------------------------------
    tsig = b.tclose.values[idx]
    for htf in ("H1", "H4"):
        hb = bars(sym, htf)
        he = C.ema(hb.close.values, 50)
        ha = A.atr(hb, 14).values
        j = C.asof_index(hb, tsig)
        okj = j >= 0
        jj = np.clip(j, 0, len(hb) - 1)
        rel = np.where(okj, np.sign(hb.close.values[jj] - he[jj]), 0.0)
        f["f_%s_rel" % htf.lower()] = rel * d                    # +1 = the fade agrees with HTF
        dist = np.where(okj, (hb.close.values[jj] - he[jj]) / np.maximum(ha[jj], 1e-9), np.nan)
        f["f_%s_ext" % htf.lower()] = -dist * d                  # + = sweeping into an extension
    dcl = np.abs(np.diff(cl, prepend=cl[0]))
    net20 = np.abs(cl - np.concatenate([np.full(20, cl[0]), cl[:-20]]))
    tot20 = pd.Series(dcl).rolling(20).sum().values
    f["f_eff"] = (net20 / np.maximum(tot20, 1e-9))[idx]          # A3 efficiency: trend vs chop
    yr = max(200, int(252 * 23 * 60 / tfm))
    atmed = pd.Series(at).rolling(yr, min_periods=200).median().shift(1).values
    f["f_atr_vs_year"] = (at / np.maximum(atmed, 1e-9))[idx]     # A4 vol level vs its own year
    f["f_atr_chg"] = (at / at[np.maximum(np.arange(n) - 20, 0)])[idx]                  # A5
    # A6 / C5: prior day's range, and how much of today's range is already spent
    day = b.time.dt.floor("D")
    g = b.groupby(day.values)
    hi_pd = g.high.max()
    lo_pd = g.low.min()
    phi = hi_pd.shift(1).reindex(day.values).values
    plo = lo_pd.shift(1).reindex(day.values).values
    f["f_dpos"] = ((c - plo[idx]) / np.maximum(phi[idx] - plo[idx], 1e-9))             # C5
    runhi = g.high.cummax().values
    runlo = g.low.cummin().values
    prange = (hi_pd - lo_pd).rolling(20).median().shift(1).reindex(day.values).values
    f["f_day_spent"] = ((runhi - runlo) / np.maximum(prange, 1e-9))[idx]               # A6
    # ---- B: clock --------------------------------------------------------------------------
    tc = pd.to_datetime(tsig)
    hour = tc.hour.values
    f["f_hour"] = hour.astype(float)
    f["f_dow"] = tc.dayofweek.values.astype(float)                                     # A8
    f["f_ldn"] = ((hour >= 7) & (hour < 12)).astype(float)                             # B1
    f["f_ny"] = ((hour >= 12) & (hour < 17)).astype(float)
    f["f_asia"] = (hour < 7).astype(float)
    f["f_overlap"] = ((hour >= 12) & (hour < 16)).astype(float)                        # B2
    f["f_thin"] = ((hour >= 21) | (hour < 1)).astype(float)                            # B4
    # B5 news proxy: this bar's range against an EXPANDING median for its own hour-of-day
    hr_med = pd.Series(hi - lo).groupby(b.time.dt.hour.values).transform(
        lambda v: v.expanding(min_periods=20).median().shift(1)).values
    f["f_newsproxy"] = (rng / np.maximum(hr_med[idx], 1e-9))
    f["f_monthend"] = (tc.day.values >= 26).astype(float)                              # B6
    # ---- E: shape --------------------------------------------------------------------------
    f["f_stop_atr"] = risk / atr_i                                                     # E1
    f["f_stop_comm"] = (C.COMM[sym] + 2 * SPREAD_PRICE / P) / np.maximum(risk / P, 1e-9)
    f["f_risk_usd"] = risk / P * C.USD_PIP_01[sym]

    out = pd.DataFrame({
        "t_signal": b.tclose.values[idx], "side": d, "entry_type": "market",
        "entry": np.nan, "expire": pd.NaT, "sl": sl, "tp": tp,
        "be_at_r": np.nan, "max_hold_min": max_hold_h * 60.0,
        "tf": tf, "N": N, "lvl": lvl, "wick": ext, "sig_bar": b.time.values[idx],
        "sig_open": oo, "sig_high": hi[idx], "sig_low": lo[idx], "sig_close": c, "atr": atr_i,
    })
    for k, v in f.items():
        out[k] = v
    out = out.dropna(subset=["sl", "tp"]).reset_index(drop=True)
    # F1/F3: the fill must be the very next minute. 0.17% of signals sit on the 21:00 UTC daily
    # break or a Friday close and would otherwise fill hours later at a gapped price. Dropped.
    mt = bars(sym, "M1").time.values
    k = np.clip(np.searchsorted(mt, out.t_signal.values, "left"), 0, len(mt) - 1)
    lag = (mt[k] - out.t_signal.values) / np.timedelta64(1, "m")
    return out[lag <= 5].reset_index(drop=True)


def run(sym, tf, N, stage, one=False, **kw):
    o = sweep_signals(sym, tf, N, stage, **kw)
    if len(o) == 0:
        return pd.DataFrame()
    t = C.simulate(sym, o, one_at_a_time=one)
    t = t[t.status == "filled"].copy()
    t["gross_R"] = t.gross_pips / t.risk_pips
    t["stage"] = stage
    return t


# ----------------------------------------------------------------------------- reporting helpers
def breakeven_of(t):
    """How many right in 100 THIS set of trades needs, from each trade's own risk and target."""
    P = C.pip(SYM)
    win = (t.tp - t.fill) * t.side / P
    loss = (t.fill - t.sl) * t.side / P + C.STOP_SLIP
    return float(100.0 * (loss + C.COMM[SYM]).sum() / (win + loss).sum())


def pf_usd(t):
    """Profit factor the way the owner defines it: gross dollars won / gross dollars lost."""
    w = t.usd_01[t.usd_01 > 0].sum()
    l = -t.usd_01[t.usd_01 < 0].sum()
    return round(w / l, 3) if l > 0 else np.inf


def give_or_take(t, draws=400, seed=2):
    """Plain-language error bar on dollars per trade: resample whole WEEKS, not single trades, so
    that trades which overlap in time cannot pretend to be independent."""
    if len(t) == 0:
        return np.nan
    wk = pd.to_datetime(t.t_fill).dt.to_period("W").astype(str).values
    groups = pd.Series(t.usd_01.values).groupby(wk)
    keys = list(groups.groups)
    vals = [groups.get_group(k).values for k in keys]
    rng = np.random.default_rng(seed)
    m = [np.concatenate([vals[j] for j in rng.integers(0, len(vals), len(vals))]).mean()
         for _ in range(draws)]
    return round(float(np.std(m)), 3)


def line(t, label=""):
    if len(t) == 0:
        return dict(what=label, trades=0)
    s = C.stats(t)
    s["PF_usd"] = pf_usd(t)
    s["usd_per_trade"] = round(t.usd_01.mean(), 3)
    s["gross_R"] = round(t.gross_R.mean(), 4)
    s["BE_need"] = round(breakeven_of(t), 2)
    return dict(what=label, **s)


def tab(rows):
    d = pd.DataFrame(rows)
    cols = [c for c in ["what", "trades", "per_week", "win_pct", "PF", "avg_R", "gross_R",
                        "BE_need", "avg_net_pips", "usd_per_trade", "usd_per_01lot", "maxDD_R",
                        "avg_hold_h", "total_R"] if c in d.columns]
    return d[cols + [c for c in d.columns if c not in cols]]


def md(df):
    """Markdown table without the optional `tabulate` dependency (it is not installed here)."""
    df = df.copy()
    for c in df.columns:
        df[c] = df[c].map(lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v))
                          else (("%g" % v) if isinstance(v, (int, float, np.floating, np.integer))
                                else str(v)))
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    rule = "|" + "|".join(["---"] * len(df.columns)) + "|"
    body = ["| " + " | ".join(r) + " |" for r in df.astype(str).values]
    return "\n".join([head, rule] + body)


# ----------------------------------------------------------------------------- STAGE 0 (group F)
def stage0():
    set_window("EXPLORE")
    out = {}
    tf, N = "M15", 20
    o = sweep_signals(SYM, tf, N, "EXPLORE")
    m1 = bars(SYM, "M1")
    t1 = m1.time.values

    i = np.searchsorted(t1, o.t_signal.values, "left")
    ii = np.clip(i, 0, len(t1) - 1)
    out["F1a_fill_bar"] = dict(
        signals=len(o),
        max_seconds_fill_open_minus_signal_close=float(
            ((t1[ii] - o.t_signal.values) / np.timedelta64(1, "s")).max()),
        min_seconds=float(((t1[ii] - o.t_signal.values) / np.timedelta64(1, "s")).min()),
        signal_bar_opens_strictly_before_fill_bar=bool((o.sig_bar.values < t1[ii]).all()))

    t_hon = run(SYM, tf, N, "EXPLORE")
    o2 = o.copy()
    o2["t_signal"] = o2.sig_bar                      # the twin: fill at the signal bar's own open
    tw = C.simulate(SYM, o2)
    tw = tw[tw.status == "filled"]
    out["F1b_lookahead_twin"] = dict(
        honest_right_in_100=round(100 * (t_hon.net_pips > 0).mean(), 2),
        twin_right_in_100=round(100 * (tw.net_pips > 0).mean(), 2),
        d_points=round(100 * ((tw.net_pips > 0).mean() - (t_hon.net_pips > 0).mean()), 2),
        honest_usd=round(t_hon.usd_01.mean(), 3), twin_usd=round(tw.usd_01.mean(), 3),
        d_usd=round(tw.usd_01.mean() - t_hon.usd_01.mean(), 3))
    j = o.set_index(["t_signal", "side"])
    k = t_hon.set_index(["t_signal", "side"])
    own_open = j.sig_open.reindex(k.index).values
    edge = (k.fill.values - own_open) * k.index.get_level_values(1).values / C.pip(SYM)
    out["F1c_why_the_twin_is_negative"] = dict(
        pct_signal_bars_whose_body_already_fades=round(100 * float(o.f_with_body.mean()), 2),
        median_open_position_in_signal_bar=round(float(
            ((o.sig_open - o.sig_low) / (o.sig_high - o.sig_low)).median()), 3),
        median_close_position_in_signal_bar=round(float(
            ((o.sig_close - o.sig_low) / (o.sig_high - o.sig_low)).median()), 3),
        mean_usd_advantage_of_the_honest_fill_at_010=round(
            float(np.nanmean(edge) * C.USD_PIP_01[SYM]), 3))

    gi = np.flatnonzero(np.diff(t1) > np.timedelta64(6, "h"))
    fa = np.searchsorted(t1, t_hon.t_fill.values, "left")
    fz = np.searchsorted(t1, (t_hon.t_fill + pd.Timedelta(hours=12)).values, "left")
    cross = np.array([bool(((gi >= a) & (gi < z)).any()) for a, z in zip(fa, fz)])
    w = (t_hon.net_pips > 0).values
    out["F3_gaps"] = dict(pct_windows_crossing_a_hole_over_6h=round(100 * cross.mean(), 2),
                          right_in_100_no_hole=round(100 * w[~cross].mean(), 2),
                          right_in_100_with_hole=round(100 * w[cross].mean(), 2),
                          usd_no_hole=round(t_hon.usd_01.values[~cross].mean(), 3),
                          usd_with_hole=round(t_hon.usd_01.values[cross].mean(), 3))

    hb = bars(SYM, "H1")
    jj = C.asof_index(hb, o.t_signal.values)
    out["F4_asof_index"] = dict(
        max_seconds_h1_bar_close_minus_signal=float(
            ((hb.tclose.values[np.clip(jj, 0, len(hb) - 1)] - o.t_signal.values)
             / np.timedelta64(1, "s")).max()),
        any_unresolved=bool((jj < 0).any()),
        note="<=0 means every H1 value came from a bar that had already closed")

    h, l, s_ = m1.high.values, m1.low.values, m1.spr.values
    amb = 0
    for r in t_hon.itertuples():
        k2 = int(np.searchsorted(t1, np.datetime64(r.t_exit), "left"))
        if r.side == 1:
            amb += int(l[k2] <= r.sl and h[k2] >= r.tp)
        else:
            amb += int(h[k2] + s_[k2] >= r.sl and l[k2] + s_[k2] <= r.tp)
    out["F5_same_m1_bar_both_barriers"] = dict(
        trades=len(t_hon), ambiguous=int(amb), pct=round(100 * amb / max(len(t_hon), 1), 3),
        resolution="common.simulate books the stop (pessimistic); barriers are read off M1 bars")

    raw = _ORIG_BARS(SYM, "M1")
    out["F0_the_duka_gold_spread_column"] = dict(
        bars=len(raw), nonzero_spread_fraction=float((raw.spr > 0).mean()),
        injected_spread_price_units=SPREAD_PRICE,
        round_trip_cost_usd_at_010=round(
            (C.COMM[SYM] + 2 * SPREAD_PRICE / C.pip(SYM)) * C.USD_PIP_01[SYM] / 1.0, 2))
    out["F2_feed_swap"] = dict(
        duka_gold_first_bar=str(raw.time.min()), zero_feed_starts="2021-01-01",
        verdict="the two feeds only overlap inside the SEALED EXAM, so a feed swap is impossible "
                "in EXPLORE/VALIDATE/STRESS. Reported, not done.")
    out["F6_era_drift_note"] = dict(
        explore_gold_price_range=[round(float(bars(SYM, "M15").close.min()), 1),
                                  round(float(bars(SYM, "M15").close.max()), 1)],
        note="every target and stop here is an ATR or R multiple, never dollars (BT21 finding 2)")
    json.dump(out, open(os.path.join(OUT, "stage0_checks.json"), "w"), indent=1, default=str)
    print(json.dumps(out, indent=1, default=str))
    return out


# ----------------------------------------------------------------------------- STAGE 1 baseline
GRID = [(tf, N) for tf in ("M5", "M15", "M30", "H1") for N in (10, 20, 40)]


def base(stage="EXPLORE", spread=None):
    global SPREAD_PRICE
    if spread is not None:
        SPREAD_PRICE = spread
    set_window(stage)
    rows, keep = [], {}
    for tf, N in GRID:
        t = run(SYM, tf, N, stage)
        if len(t) == 0:
            continue
        keep[(tf, N)] = t
        r = line(t, "%s N=%d" % (tf, N))
        r["med_risk_usd"] = round(t.risk_pips.median() * C.USD_PIP_01[SYM], 2)
        r["cost_pct_1R"] = round(100 * (C.COMM[SYM] + 2 * SPREAD_PRICE / C.pip(SYM))
                                 / t.risk_pips.median(), 1)
        rows.append(r)
    d = tab(rows)
    d.to_csv(os.path.join(OUT, "base_%s.csv" % stage), index=False)
    print(md(d))
    return keep, d


# ----------------------------------------------------------------------------- the luck bar (G1)
def bars_of_luck(t, stage, seed=0):
    """Two controls for one variant.
    1. `luck_bar` from autopsy_lib: RANDOM entries, random side, the same ATR shape, the same
       stage, the same hours. This is the official bar (its hold is 5 days, mine is shorter).
    2. a coin at EXACTLY my own signal minutes with EXACTLY my own stop and target distances --
       the same control BT21E called `coin $`. Supplementary, and hold-matched.
    """
    sm = float(np.median(t.f_stop_atr))
    hours = sorted(set(pd.to_datetime(t.t_signal).dt.hour))
    lb = A.luck_bar(SYM, min(4000, 6 * len(t)), 2 * sm, sm, stage, seed=seed, hours=hours)
    lb = lb[lb.status == "filled"]
    rng = np.random.default_rng(seed + 7)
    o = t[["t_signal", "entry_type", "entry", "expire", "be_at_r", "max_hold_min"]].copy()
    o["side"] = rng.choice([1, -1], size=len(t))
    risk = (t.fill - t.sl).values * t.side.values
    o["sl"] = t.sig_close.values - o.side.values * risk
    o["tp"] = t.sig_close.values + o.side.values * (t.tp.values - t.sig_close.values) \
        * t.side.values
    cf = C.simulate(SYM, o.dropna(subset=["sl", "tp"]), one_at_a_time=False)
    cf = cf[cf.status == "filled"]
    return dict(luck_bar=round(100 * (lb.net_pips > 0).mean(), 2), luck_bar_trades=len(lb),
                luck_bar_usd=round(lb.usd_01.mean(), 3),
                coin_same_minutes=round(100 * (cf.net_pips > 0).mean(), 2),
                coin_usd=round(cf.usd_01.mean(), 3))


# ----------------------------------------------------------------------------- STAGE 1 autopsy
def conditions(t):
    """Every catalogue condition tested, as one mechanical sentence each. Thresholds are medians or
    natural cut points, fixed here once; no threshold was searched."""
    q = t
    med = lambda c: float(np.median(q[c]))                                  # noqa: E731
    return [
        # ---- D: the trigger candle
        ("D3", "sweep depth past the level is BELOW the median (a shallow poke)",
         q.f_depth_atr <= med("f_depth_atr")),
        ("D3", "sweep depth past the level is at least 0.25 ATR (a real overshoot)",
         q.f_depth_atr >= 0.25),
        ("D2", "close sits in the far 40% of the bar from the swept extreme (strong rejection)",
         q.f_rej >= 0.60),
        ("D2", "the sweep wick is at least as long as the body",
         q.f_wick_body >= 1.0),
        ("D1", "the bar's body is under 0.5 ATR (no momentum through the level)",
         q.f_body_atr <= 0.50),
        ("D1", "the bar's whole range is at least 1.5 ATR (a violent bar)",
         q.f_rng_atr >= 1.5),
        ("D2", "the bar's body already points the way we want to fade",
         q.f_with_body == 1),
        ("D5", "the sweep bar engulfs the bar before it",
         q.f_engulf == 1),
        ("D4", "the swept level was set in the OLDER half of the lookback window",
         q.f_lvl_age >= q.N / 2),
        # ---- C: level and structure
        ("C3", "only ONE of the prior N bars came near the level (a fresh, untested level)",
         q.f_touches <= 1),
        ("C3", "three or more prior bars came near the level (a worn, third-touch level)",
         q.f_touches >= 3),
        ("C1", "the wick also cleared the 60-bar extreme (genuine deeper liquidity)",
         q.f_also_60 == 1),
        ("C1", "the wick also cleared the 240-bar extreme (multi-day liquidity)",
         q.f_also_240 == 1),
        ("C1", "this side's liquidity has NOT already been swept in the last 20 bars",
         q.f_prev_sweep_same == 0),
        ("C1", "no sweep of either side in the last 20 bars (not a chop cluster)",
         q.f_prev_sweep_any == 0),
        ("C1", "room to the opposite side of the range is above the median",
         q.f_room_atr >= med("f_room_atr")),
        ("C2", "the opposing level sits BEYOND the target, so the target is reachable",
         q.f_room_vs_tgt >= 1.0),
        ("C4", "the swept level is within $0.15 of a round dollar",
         q.f_round_1 == 1),
        ("C5", "the sweep happens OUTSIDE the prior day's range",
         (q.f_dpos > 1) | (q.f_dpos < 0)),
        # ---- A: regime and context
        ("A1", "the fade agrees with the H1 50-EMA trend",
         q.f_h1_rel > 0),
        ("A1", "the fade agrees with the H4 50-EMA trend",
         q.f_h4_rel > 0),
        ("A1", "the fade agrees with BOTH the H1 and the H4 trend",
         (q.f_h1_rel > 0) & (q.f_h4_rel > 0)),
        ("A7", "price is at least 1 H1-ATR beyond the H1 50-EMA on the swept side (extended)",
         q.f_h1_ext >= 1.0),
        ("A3", "the market is choppy: the 20-bar efficiency ratio is below the median",
         q.f_eff <= med("f_eff")),
        ("A4", "ATR is below its own trailing-year median (a quiet regime)",
         q.f_atr_vs_year <= 1.0),
        ("A5", "ATR is contracting against 20 bars ago",
         q.f_atr_chg <= 1.0),
        ("A6", "less than one typical day's range has been spent so far today",
         q.f_day_spent <= 1.0),
        # ---- B: time and session
        ("B1", "London hours, 07-12 UTC", q.f_ldn == 1),
        ("B1", "New York hours, 12-17 UTC", q.f_ny == 1),
        ("B1", "Asia hours, 00-07 UTC", q.f_asia == 1),
        ("B2", "the London/New York overlap, 12-16 UTC", q.f_overlap == 1),
        ("B4", "NOT the thin rollover hours 21-01 UTC", q.f_thin == 0),
        ("B5", "news proxy: the bar's range is at least twice the normal range for that hour",
         q.f_newsproxy >= 2.0),
        ("B6", "the last days of the month, day 26 onward", q.f_monthend == 1),
        # ---- E: shape as a filter
        ("E1", "the stop is at least 0.6 ATR away, i.e. outside normal noise",
         q.f_stop_atr >= 0.60),
        ("E1", "the round trip costs 12% of 1R or less", q.f_stop_comm <= 0.12),
    ]


def autopsy():
    set_window("EXPLORE")
    prim = ("M15", 20)
    alt = [("M5", 20), ("M30", 20)]
    T = {}
    for tf, N in [prim] + alt:
        T[(tf, N)] = run(SYM, tf, N, "EXPLORE")
    t = T[prim]
    tal = A.Tally("AUT02_sweep")
    rows = []
    for (tf, N), tt in T.items():
        rows.append(line(tt, "%s N=%d, all signals" % (tf, N)))
        rows[-1].update(bars_of_luck(tt, "EXPLORE"))
        rows[-1]["give_or_take_usd"] = give_or_take(tt)
    for (tf, N), tt in T.items():                     # side split: is this just gold's uptrend?
        for s, nm in ((1, "BUY (swept low)"), (-1, "SELL (swept high)")):
            rows.append(line(tt[tt.side == s], "%s N=%d, %s" % (tf, N, nm)))
            rows[-1]["give_or_take_usd"] = give_or_take(tt[tt.side == s])
    head = tab(rows)
    head.to_csv(os.path.join(OUT, "explore_head.csv"), index=False)
    print("\n### EXPLORE baseline, luck bars and side split\n")
    print(md(head))

    conds = conditions(t)
    alt_c = {k: dict((i, c) for i, (_, _, c) in enumerate(conditions(v))) for k, v in T.items()}
    out = []
    for i, (cid, note, c) in enumerate(conds):
        c = np.asarray(c, bool)
        s = A.score(t, c)
        tal.add(cid, note, s)
        r = dict(cond=cid, condition=note, kept=int(s.trades[1]),
                 kept_pct=round(100 * s.trades[1] / s.trades[0], 1),
                 right_in_100=s.right_in_100[1], lift=round(s.right_in_100[1] - s.right_in_100[0], 2),
                 usd=s.net_usd_per_trade[1],
                 d_usd=round(s.net_usd_per_trade[1] - s.net_usd_per_trade[0], 3),
                 usd_if_false=s.net_usd_per_trade[2])
        for (tf, N), tt in T.items():
            if (tf, N) == prim:
                continue
            cc = np.asarray(alt_c[(tf, N)][i], bool)
            s2 = A.score(tt, cc)
            r["d_usd_%s" % tf] = round(s2.net_usd_per_trade[1] - s2.net_usd_per_trade[0], 3)
        r["give_or_take"] = give_or_take(t[c])
        out.append(r)
    d = pd.DataFrame(out).sort_values("d_usd", ascending=False)
    d.to_csv(os.path.join(OUT, "tally_explore.csv"), index=False)
    print("\n### The tally: %d conditions tried on M15 N=20 (EXPLORE)\n" % len(d))
    print(md(d))
    tal.frame().to_csv(os.path.join(OUT, "tally_raw.csv"), index=False)
    for (tf, N), tt in T.items():
        C.save("AUT02_sweep", "EXPLORE_%s%d" % (tf, N), tt)
    return T, d


# ----------------------------------------------------------------------------- STAGE 1 shape
def reshape(o, tgt_R=None, tgt_atr=None, stop_atr=None, max_hold_h=None, be_at_r=None):
    """Re-price the SAME signals with a different target/stop/time. Nothing about the entry moves."""
    o = o.copy()
    d, c = o.side.values, o.sig_close.values
    if stop_atr is not None:
        o["sl"] = c - d * stop_atr * o.atr.values
    risk = (c - o.sl.values) * d
    if tgt_atr is not None:
        o["tp"] = c + d * tgt_atr * o.atr.values
    elif tgt_R is not None:
        o["tp"] = c + d * tgt_R * risk
    if max_hold_h is not None:
        o["max_hold_min"] = max_hold_h * 60.0
    o["be_at_r"] = np.nan if be_at_r is None else be_at_r
    return o


def shape():
    set_window("EXPLORE")
    rows = []
    for tf, N in (("M15", 20), ("M30", 20)):
        o = sweep_signals(SYM, tf, N, "EXPLORE")
        atr_med = float(np.median(o.atr))
        # E6a: the wick stop, target walked out in R
        for g in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
            t = C.simulate(SYM, reshape(o, tgt_R=g))
            t = t[t.status == "filled"]
            t["gross_R"] = t.gross_pips / t.risk_pips
            rows.append(line(t, "%s N=%d wick stop, target %.1fR" % (tf, N, g)))
            rows[-1]["give_or_take_usd"] = give_or_take(t)
        # E6b: the ATR ladder - both barriers in ATR, the brief's own ladder
        for tg, st in A.ATR_LADDER:
            t = C.simulate(SYM, reshape(o, tgt_atr=tg, stop_atr=st))
            t = t[t.status == "filled"]
            t["gross_R"] = t.gross_pips / t.risk_pips
            rows.append(line(t, "%s N=%d ATR ladder t=%.1f s=%.1f" % (tf, N, tg, st)))
            rows[-1]["BE_ladder"] = round(A.breakeven_rate(SYM, atr_med, tg, st), 2)
        # E3: a time stop, wick stop + 2R
        for h in (0.5, 1, 2, 4, 8, 12, 24, 48):
            t = C.simulate(SYM, reshape(o, tgt_R=2.0, max_hold_h=h))
            t = t[t.status == "filled"]
            t["gross_R"] = t.gross_pips / t.risk_pips
            rows.append(line(t, "%s N=%d wick stop 2R, time stop %sh" % (tf, N, h)))
        # E4: move the stop to break even
        for be in (0.5, 1.0, 1.5):
            t = C.simulate(SYM, reshape(o, tgt_R=2.0, be_at_r=be))
            t = t[t.status == "filled"]
            t["gross_R"] = t.gross_pips / t.risk_pips
            rows.append(line(t, "%s N=%d wick stop 2R, stop to break even at %.1fR" % (tf, N, be)))
    d = tab(rows)
    d.to_csv(os.path.join(OUT, "shape_explore.csv"), index=False)
    print(md(d))
    return d


# ----------------------------------------------------------------------------- the refinements
#: The three refinements carried out of EXPLORE. Nested, so each line can be judged separately.
#: Every threshold was fixed on EXPLORE and is NEVER re-tuned afterwards.
REFINE = {
    "R0_raw": lambda o: np.ones(len(o), bool),
    "R1_stretched": lambda o: (o.f_h1_ext >= 1.0).values,
    "R2_stretched_NYoverlap": lambda o: ((o.f_h1_ext >= 1.0) & (o.f_overlap == 1)).values,
    "R3_stretched_overlap_240": lambda o: ((o.f_h1_ext >= 1.0) & (o.f_overlap == 1)
                                           & (o.f_also_240 == 1)).values,
}
SHAPES = {"wick_2R": dict(tgt_R=2.0), "atr_2x2": dict(tgt_atr=2.0, stop_atr=2.0)}


def refine(stage="EXPLORE", spread=0.05, tag=None, grid=(("M15", 20), ("M30", 20), ("M15", 10))):
    global SPREAD_PRICE
    SPREAD_PRICE = spread
    set_window(stage)
    tag = tag or stage
    rows = []
    for tf, N in grid:
        o0 = sweep_signals(SYM, tf, N, stage)
        for rn, fn in REFINE.items():
            m = fn(o0)
            if m.sum() < 25:
                continue
            o = o0[m].reset_index(drop=True)
            for sn, kw in SHAPES.items():
                t = C.simulate(SYM, reshape(o, **kw), one_at_a_time=False)
                t = t[t.status == "filled"].copy()
                if len(t) < 20:
                    continue
                t["gross_R"] = t.gross_pips / t.risk_pips
                r = line(t, "%s N=%d | %s | %s" % (tf, N, rn, sn))
                r["give_or_take_usd"] = give_or_take(t)
                r.update(bars_of_luck(t, stage))
                r["usd_BUY"] = round(t.usd_01[t.side == 1].mean(), 3)
                r["usd_SELL"] = round(t.usd_01[t.side == -1].mean(), 3)
                r["n_BUY"] = int((t.side == 1).sum())
                r["stage"] = stage
                r["spread"] = spread
                rows.append(r)
                C.save("AUT02_sweep", "%s_%s%d_%s_%s" % (tag, tf, N, rn, sn), t)
                print(".", end="", flush=True)
    d = tab(rows)
    d.to_csv(os.path.join(OUT, "refine_%s.csv" % tag), index=False)
    print()
    print(md(d))
    return d


def oneatatime(stage="EXPLORE", spread=0.05):
    """The tradable version: the robot holds one position at a time, as common.simulate defaults."""
    global SPREAD_PRICE
    SPREAD_PRICE = spread
    set_window(stage)
    rows = []
    for tf, N in (("M15", 20), ("M30", 20)):
        o0 = sweep_signals(SYM, tf, N, stage)
        for rn in ("R0_raw", "R2_stretched_NYoverlap", "R3_stretched_overlap_240"):
            m = REFINE[rn](o0)
            if m.sum() < 25:
                continue
            t = C.simulate(SYM, reshape(o0[m].reset_index(drop=True), tgt_R=2.0),
                           one_at_a_time=True)
            t = t[t.status == "filled"].copy()
            t["gross_R"] = t.gross_pips / t.risk_pips
            rows.append(line(t, "%s N=%d | %s | wick_2R | one at a time" % (tf, N, rn)))
            rows[-1]["stage"] = stage
    d = tab(rows)
    d.to_csv(os.path.join(OUT, "oneatatime_%s.csv" % stage), index=False)
    print(md(d))
    return d


def examples(n=10, seed=2):
    """Ten trades a human can check on a chart, stratified so it is not all winners."""
    set_window("EXPLORE")
    o0 = sweep_signals(SYM, "M30", 20, "EXPLORE")
    o = o0[REFINE["R2_stretched_NYoverlap"](o0)].reset_index(drop=True)
    t = C.simulate(SYM, reshape(o, tgt_R=2.0))
    t = t[t.status == "filled"].copy()
    rng = np.random.default_rng(seed)
    w = t[t.net_pips > 0]
    l = t[t.net_pips <= 0]
    pick = pd.concat([w.iloc[rng.choice(len(w), 4, replace=False)],
                      l.iloc[rng.choice(len(l), 6, replace=False)]]).sort_values("t_signal")
    cols = ["sig_bar", "t_signal", "side", "lvl", "sig_high", "sig_low", "sig_close", "wick",
            "fill", "sl", "tp", "t_exit", "exit", "exit_reason", "hold_h", "net_pips", "usd_01",
            "f_h1_ext", "f_also_240", "f_depth_atr", "f_rej"]
    pick = pick[cols].round(3)
    pick.to_csv(os.path.join(OUT, "examples.csv"), index=False)
    print(md(pick))
    return pick


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stage0"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "stage0":
        stage0()
    elif cmd == "base":
        base(arg or "EXPLORE")
    elif cmd == "autopsy":
        autopsy()
    elif cmd == "shape":
        shape()
    elif cmd == "refine":
        refine(arg or "EXPLORE")
    elif cmd == "stress":
        refine("STRESS", spread=0.15, tag="STRESS_adverse")
    elif cmd == "one":
        oneatatime(arg or "EXPLORE")
    elif cmd == "examples":
        examples()
