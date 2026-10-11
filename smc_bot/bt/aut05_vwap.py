"""AUT05 — VWAP bounce / rejection and the +-sigma band reversion, autopsied.

Governing documents: bt/AUTOPSY_BRIEF.md (method, catalogue, locked split, caps) and bt/AGENT_BRIEF.md.
Shared layer: bt/autopsy_lib.py (stages, ATR, ATR_LADDER, breakeven_rate, luck_bar, score, Tally) and
bt/common.py (bars, simulate, asof_index, stats, save). The six pre-registered VWAP rules themselves are
imported from bt/bt21v_lib.py (another agent's file -- imported, never edited) so that the signal sets are
bit-identical to BT21-V's and this is an autopsy of THOSE trades, not of a new rule.

Two things this file is built around, both of which are findings before any strategy number is produced:

1. VOLUME. Dukascopy's `tick_volume` is the constant integer 1 on all 5,526,570 gold M1 bars. EXPLORE /
   VALIDATE / STRESS all sit inside that feed, and the only feed with real volume (Exness `data_zero`) is
   2021+, i.e. the SEALED EXAM. So a volume-weighted VWAP cannot be computed on any tunable data in this
   project. Everything below is an UNWEIGHTED anchored VWAP -- a running mean of the typical price since
   the anchor. `stage0()` proves it rather than asserting it.

2. SPREAD. The Dukascopy `spread` column is 0 on 100% of gold minutes (ERRORS #34), so `simulate` would
   run this at zero spread. Baseline cost here is therefore COMM 1.10 + 0.50 = **1.60 pips = $1.60 per
   0.10 lot round trip** (the 0.05 price units of real raw gold spread, charged as commission because
   `common.py` must not be edited). STRESS doubles it again.

Run (from the TTXT root, Dukascopy feed):
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut05_vwap.py stage0
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut05_vwap.py explore
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut05_vwap.py validate
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut05_vwap.py stress
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut05_vwap.py examples
"""
import os
import sys
import time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # smc_bot  -> `from bt import ...`
sys.path.insert(0, HERE)                           # smc_bot/bt -> autopsy_lib does `import common`
import bt.common as C                                                   # noqa: E402
from bt import autopsy_lib as A                                         # noqa: E402
from bt import bt21v_lib as V                                           # noqa: E402

GOLD = "XAUUSD"
OUT = os.path.join(C.RESULTS, "AUT05_vwap")
PARTS = os.path.join(OUT, "parts")
os.makedirs(PARTS, exist_ok=True)

# ---- costs -----------------------------------------------------------------------------------------
# The Dukascopy gold feed reports NO spread (`spr` is 0.0 on all 5,526,570 bars), so `common.simulate`
# would quote ask = bid and trade gold for free. `autopsy_lib.gold_spread()` is the project's documented
# stand-in: 0.30 PRICE units, i.e. 3.0 pips, i.e. $3.00 a round trip at 0.10 lots. It is injected into
# the bars themselves (a wrapper round `common.bars`, never an edit to common.py) so that `simulate`
# prices it the way a real spread works -- buys fill at the ask, sell-side exits trigger on bid+spread --
# rather than as a flat fee.
COMM_BASE = 1.10          # pips of 0.1 => $1.10 per 0.10 lot round trip, the measured Zero commission
COMM_HARSH = 2.20         # STRESS adverse run: double it
SLIP_BASE = 0.2           # pips, common.py default
SLIP_HARSH = 1.0          # STRESS adverse run: 5x worse stop fills
SPREAD_HARSH = 0.60       # STRESS adverse run: double the injected spread

#: Set by set_costs(); read by the bars wrapper below.
SPREAD_USD = 0.0
_BARCACHE = {}
_ORIG_BARS = C.bars


def _bars_with_spread(sym, tf):
    """`common.bars` with the missing gold spread injected -- and CACHED.

    common.bars re-reads the parquet on every call, and `simulate` calls it once per run, so a 36-rung
    ladder re-read 5.5 million rows 72 times. Caching here is worth more than any other speed-up in this
    file; the spread injection is the reason the wrapper exists at all.
    """
    key = (sym, tf)
    hit = _BARCACHE.get(key)
    if hit is not None and hit[0] == (SPREAD_USD if (sym == GOLD and tf == "M1") else None):
        return hit[1]
    b = _ORIG_BARS(sym, tf)
    if sym == GOLD and tf == "M1" and SPREAD_USD:
        b = b.copy()
        b["spr"] = float(SPREAD_USD)
    _BARCACHE[key] = ((SPREAD_USD if (sym == GOLD and tf == "M1") else None), b)
    return b


C.bars = _bars_with_spread


# ---- the rules (BT21-V's six, renamed in plain words) ----------------------------------------------
RULES = {
    "P_day": ("V1", "day-anchored VWAP (00:00 UTC): pullback touches VWAP, closes back on the trend side -> FOLLOW"),
    "P_ldn": ("V3", "London-anchored VWAP (08:00 Europe/London, DST-aware): same pullback rule -> FOLLOW"),
    "P_ny":  ("V4", "New-York-anchored VWAP (09:30 America/New_York, DST-aware): same pullback rule -> FOLLOW"),
    "B_1sig": ("V5", "day-anchored VWAP +-1 sigma: close comes back INSIDE the band -> revert toward VWAP"),
    "B_2sig": ("V6", "day-anchored VWAP +-2 sigma: close comes back INSIDE the band -> revert toward VWAP"),
}
REF_SHAPE = (1.5, 1.0)    # the brief's reference: 1.5xATR target / 1.0xATR stop, break-even 42.9 in 100
HOLD = 1440               # minutes. common.simulate charges NO swap (ERRORS #39) so holds are capped at 1 day


# ==================================================================================== stage 0
def volume_and_spread_verdict():
    """Catalogue D6 + the spread half of E5/F-group: can a VWAP even be built on tunable data?"""
    b = C.bars(GOLD, "M1")
    v = V.raw_volume(b)
    q = V.volume_quality(b, v)
    rows = []
    for stage in ("EXPLORE", "VALIDATE", "STRESS"):
        a, z = A.STAGES[stage]
        m = (b.time >= a) & (b.time < z)
        vv = v[m.values]
        sp = b.spr.values[m.values]
        rows.append(dict(stage=stage, dates=f"{a} -> {z}", bars=int(m.sum()),
                         vol_distinct=int(len(np.unique(vv))), vol_min=float(vv.min()), vol_max=float(vv.max()),
                         pct_vol_eq_1=round(100 * float((vv == 1).mean()), 2),
                         spr_distinct=int(len(np.unique(sp))), spr_max=float(sp.max()),
                         volume_weighted_vwap_possible=bool(len(np.unique(vv)) > 1)))
    return q, pd.DataFrame(rows)


def group_f_checks(b, sig):
    """Catalogue group F, run FIRST. Returns a list of (check, result) lines."""
    out = []
    n = len(b)
    tv = b.time.values.astype("datetime64[ns]").astype(np.int64)

    # F3 -- gaps around the trade. Every rule already demands bar k-1 -> k -> k+1 be consecutive minutes.
    bad = 0
    for nm, (k, d) in sig.items():
        bad += int(((tv[k] - tv[k - 1]) != 60_000_000_000).sum() + ((tv[k + 1] - tv[k]) != 60_000_000_000).sum())
    out.append(("F3 no signal straddles a missing minute (k-1,k,k+1 consecutive)", f"{bad} violations over all 5 rules"))
    gaps = np.diff(tv) > 6 * 3600 * 1_000_000_000
    out.append(("F3 gaps > 6h in the gold M1 feed", f"{int(gaps.sum()):,} (weekends + holidays); simulate flattens before each"))

    # F4 -- stale higher-timeframe values. autopsy_lib.atr_at uses asof_index on tclose.
    m15 = C.bars(GOLD, "M15")
    tc = b.tclose.values[:200000]
    i = C.asof_index(m15, tc)
    ok = (m15.tclose.values[i[i >= 0]] <= tc[i >= 0]).all()
    out.append(("F4 M15 ATR read with asof_index uses only CLOSED M15 bars",
                f"{bool(ok)} ({int((i >= 0).sum()):,} checked, {int((i < 0).sum())} bars precede the first M15 close "
                f"and get ATR = NaN, which drops the order)"))

    # F1 -- which bar the fill happens on: re-derived below in lookahead_twin()
    # F5 -- same-bar target-and-stop: common.simulate books the STOP when both are touched in one minute
    out.append(("F5 same-minute target+stop", "common.simulate books the SL first (pessimistic, common.py:first_exit)"))
    out.append(("F2 feed disagreement (Dukascopy vs Exness)", "IMPOSSIBLE on tunable data: data_zero starts 2021-01-01 = the sealed EXAM"))
    # F6 -- era drift: every target and stop here is a multiple of M15 ATR, never a dollar amount
    at = A.atr_at(GOLD, "M15", b.tclose.values[::5000])
    yr = pd.DatetimeIndex(b.time.values[::5000]).year
    drift = pd.DataFrame(dict(year=yr, atr=at)).groupby("year").atr.median().round(3)
    out.append(("F6 median M15 ATR by year (why shapes are in ATR, not dollars)", drift.to_dict()))
    return out


# ==================================================================================== signals
def signal_sets():
    """{rule: (signal bar index k, declared side)} for the WHOLE feed, volume forced to 1.

    weighted=False is not a choice here, it is the only option: see volume_and_spread_verdict().
    """
    b = C.bars(GOLD, "M1")
    one = np.ones(len(b))
    out = {}
    for nm, (vid, _) in RULES.items():
        e, d, k = V.signals(vid, b, one, weighted=False)
        out[nm] = (k.astype(np.int64), d.astype(np.int8))
    return b, out


# ==================================================================================== features
def _seg_id(grp, flip):
    """Segment id that restarts at every anchored-window change AND at every flip."""
    chg = np.empty(len(grp), bool)
    chg[0] = True
    chg[1:] = (grp[1:] != grp[:-1]) | flip[1:]
    return np.cumsum(chg) - 1


def _seg_cummax(x, seg):
    return pd.Series(x).groupby(seg).cummax().values


def _seg_cumsum(x, seg):
    return pd.Series(x).groupby(seg).cumsum().values


def _seg_count(seg):
    return pd.Series(1, index=np.arange(len(seg))).groupby(seg).cumsum().values


_ACACHE = {}


def anchor_arrays(b, anchor):
    """The anchored VWAP, its sigma and the within-window geometry. Cached: three anchors, five rules."""
    if anchor in _ACACHE:
        return _ACACHE[anchor]
    n = len(b)
    c, hi, lo = b.close.values, b.high.values, b.low.values
    vw, sg, cnt, valid = V.vwap_sigma(b, np.ones(n), anchor)
    grp, _, _ = V.anchor_groups(b, anchor)
    above = c > vw
    flip = np.zeros(n, bool)
    flip[1:] = above[1:] != above[:-1]          # a close-side change = a VWAP cross ("touch")
    seg = _seg_id(grp, flip)
    exc = _seg_cummax(np.abs(c - vw), seg)      # how far this excursion got from VWAP (price units)
    wseg = _seg_id(grp, np.zeros(n, bool))      # one segment per anchored window
    touch_n = _seg_cumsum(flip.astype(np.int64), wseg)
    wchg = np.empty(n, bool)
    wchg[0] = True
    wchg[1:] = grp[1:] != grp[:-1]
    dc = np.r_[0.0, np.abs(np.diff(c))]
    dc[wchg] = 0.0
    travel = _seg_cumsum(dc, wseg)
    first_c = c[np.flatnonzero(wchg)][wseg]
    with np.errstate(invalid="ignore", divide="ignore"):
        eff = np.where(travel > 0, np.abs(c - first_c) / travel, np.nan)
    rmax = _seg_cummax(hi, wseg)
    rmin = -_seg_cummax(-lo, wseg)
    _ACACHE[anchor] = dict(vw=vw, sg=sg, cnt=cnt, exc=exc, touch_n=touch_n, eff=eff,
                           wrange=rmax - rmin, wcnt=_seg_count(wseg))
    return _ACACHE[anchor]


_MCACHE = {}


def m15_arrays():
    """M15 / H1 / prior-day context. Cached across rules."""
    if "m15" in _MCACHE:
        return _MCACHE
    m15 = C.bars(GOLD, "M15")
    a15 = A.atr(m15, 14).values
    _MCACHE.update(m15=m15, a15=a15, e50=C.ema(m15.close.values, 50), e200=C.ema(m15.close.values, 200),
                   st=_structure_cached("M15", 5),
                   norm=pd.Series(a15).rolling(96 * 252, min_periods=96 * 20).mean().values,
                   h1=C.bars(GOLD, "H1"))
    _MCACHE["e50h"] = C.ema(_MCACHE["h1"].close.values, 50)
    return _MCACHE


def feature_table(b, k, side, anchor):
    """Every condition in the catalogue that this strategy can be asked, computed from bars <= k ONLY.

    k is the SIGNAL bar (closed). The order fills at the open of k+1. Nothing here reads bar k+1 or later.
    """
    n = len(b)
    c, hi, lo, op = b.close.values, b.high.values, b.low.values, b.open.values
    AA = anchor_arrays(b, anchor)
    vw, sg, cnt = AA["vw"], AA["sg"], AA["cnt"]
    exc_atr_raw, touch_n, eff, wrange = AA["exc"], AA["touch_n"], AA["eff"], AA["wrange"]

    # --- M15 context (cached across rules) ------------------------------------------------------
    M = m15_arrays()
    m15, a15, e50, e200, st = M["m15"], M["a15"], M["e50"], M["e200"], M["st"]
    i15 = np.clip(C.asof_index(m15, b.tclose.values[k]), 0, len(m15) - 1)
    at = a15[i15]
    sep = (e50[i15] - e200[i15]) / at
    dist_e50 = (m15.close.values[i15] - e50[i15]) / at
    lsh, lsl = st["lsh"][i15], st["lsl"][i15]
    trend15 = st["trend"][i15]
    # A4 volatility LEVEL: today's M15 ATR against its own trailing-year mean (a ratio, not a rolling
    # rank -- a true rolling rank over a 24,192-bar window is O(n*w) and never finishes).
    rank = (a15 / M["norm"])[i15]
    prev = a15[np.clip(i15 - 48, 0, None)]
    exp = a15[i15] / np.where(np.isnan(prev), np.nan, prev)

    # --- H1 context -----------------------------------------------------------------------------
    h1, e50h = M["h1"], M["e50h"]
    ih = np.clip(C.asof_index(h1, b.tclose.values[k]), 0, len(h1) - 1)
    h1_up = np.sign(h1.close.values[ih] - e50h[ih]).astype(np.int8)

    # --- prior day (C5) ------------------------------------------------------------------------
    dkey = b.time.values.astype("datetime64[D]")
    dfr = pd.DataFrame(dict(d=dkey, h=hi, l=lo))
    g = dfr.groupby("d").agg(h=("h", "max"), l=("l", "min"))
    g["ph"], g["pl"] = g.h.shift(1), g.l.shift(1)
    g["rng20"] = (g.h - g.l).shift(1).rolling(20).mean()
    pm = g.reindex(dkey[k])
    with np.errstate(invalid="ignore", divide="ignore"):
        dpos = (c[k] - pm.pl.values) / (pm.ph.values - pm.pl.values)
        day_spent = wrange[k] / pm.rng20.values

    # --- the trigger candle (D) ----------------------------------------------------------------
    body = np.abs(c[k] - op[k]) / at
    rng = np.maximum(hi[k] - lo[k], 1e-9)
    closepos = np.where(side == 1, (c[k] - lo[k]) / rng, (hi[k] - c[k]) / rng)
    engulf = ((side == 1) & (c[k] > hi[k - 1]) & (op[k] <= lo[k - 1] + rng)) | \
             ((side == -1) & (c[k] < lo[k - 1]) & (op[k] >= hi[k - 1] - rng))
    overshoot = np.where(side == 1, (vw[k] - lo[k]), (hi[k] - vw[k])) / at     # D3 depth past the line
    # how many bars the pullback took: bars since the window's running extreme in the trade direction (D4)
    wcnt = AA["wcnt"]                                        # computed ONCE per anchor, not once per signal
    pb_bars = np.zeros(len(k), np.int32)
    for j in range(len(k)):
        kk, s = int(k[j]), int(side[j])
        w0 = kk - min(240, int(wcnt[kk]) - 1)
        seg_h = hi[w0:kk + 1] if s == 1 else lo[w0:kk + 1]
        pb_bars[j] = len(seg_h) - 1 - (int(np.argmax(seg_h)) if s == 1 else int(np.argmin(seg_h)))

    # --- structure / room (C1, C2) -------------------------------------------------------------
    nxt = np.where(side == 1, lsh, lsl)                       # the level the trade is heading into
    opp = np.where(side == 1, lsl, lsh)                       # the level behind it
    room = (nxt - c[k]) * side / at                           # ATR of clear air before the next swing level
    behind = (c[k] - opp) * side / at
    near_level = np.minimum(np.abs(vw[k] - lsh), np.abs(vw[k] - lsl)) / at    # VWAP sitting ON a structure level

    # --- round numbers (C4) ---------------------------------------------------------------------
    r5 = np.abs(c[k] - np.round(c[k] / 5.0) * 5.0) / at

    tt = pd.DatetimeIndex(b.time.values[k])
    f = pd.DataFrame(dict(
        sidx=np.arange(len(k)), k=k, side=side.astype(int), t_sig=b.tclose.values[k], atr=at,
        f_cnt=cnt[k].astype(np.int64),                                   # B3 bars since the anchor
        f_sig_atr=sg[k] / at,                                            # band width in ATR
        f_exc_sig=np.where(sg[k] > 0, exc_atr_raw[k] / sg[k], np.nan),   # A7 travel from VWAP, in sigma
        f_exc_atr=exc_atr_raw[k] / at,                                   # A7 travel from VWAP, in ATR
        f_touch_n=touch_n[k].astype(np.int64),                           # C3 which touch of the day this is
        f_eff=eff[k],                                                    # A3 efficiency ratio of the window
        f_ema_sep=sep,                                                   # A3 M15 EMA50-EMA200 separation
        f_m15_agree=(np.sign(dist_e50) == side).astype(np.int8),         # A1
        f_h1_agree=(h1_up == side).astype(np.int8),                      # A1
        f_trend15_agree=(trend15 == side).astype(np.int8),               # A1/A2 structural trend
        f_dist_e50=np.abs(dist_e50),                                     # A7 extension from the HTF mean
        f_atr_rank=rank,                                                 # A4
        f_atr_exp=exp,                                                   # A5
        f_day_spent=day_spent,                                           # A6
        f_dpos=dpos,                                                     # C5
        f_room=room, f_behind=behind, f_near_level=near_level,           # C1, C2, structural coincidence
        f_round5=r5,                                                     # C4
        f_body=body, f_closepos=closepos, f_engulf=engulf.astype(np.int8),  # D1, D2, D5
        f_overshoot=overshoot, f_pb_bars=pb_bars,                        # D3, D4
        f_hour=tt.hour.values, f_dow=tt.dayofweek.values,                # B1, A8
        f_ldn=((tt.hour.values >= 7) & (tt.hour.values < 16)).astype(np.int8),
        f_ny=((tt.hour.values >= 13) & (tt.hour.values < 21)).astype(np.int8),
        f_overlap=((tt.hour.values >= 13) & (tt.hour.values < 16)).astype(np.int8),   # B2
        f_asia=(tt.hour.values < 7).astype(np.int8),
        f_monthend=(tt.day.values >= 26).astype(np.int8),                # B6
        f_stop_pips=np.nan,
    ))
    return f


_SCACHE = {}


def _structure_cached(tf, N):
    if (tf, N) not in _SCACHE:
        f = os.path.join(PARTS, f"struct_{tf}_{N}.npz")
        if os.path.exists(f):
            z = np.load(f)
            _SCACHE[(tf, N)] = {kk: z[kk] for kk in z.files}
        else:
            st = C.structure(C.bars(GOLD, tf), N)
            keep = {kk: st[kk] for kk in ("trend", "lsh", "lsl")}
            np.savez_compressed(f, **keep)
            _SCACHE[(tf, N)] = keep
    return _SCACHE[(tf, N)]


# ==================================================================================== outcomes
def _orders(b, k, at, side, tgt, stop, hold=HOLD, lookahead=False, unit="atr", sig=None, mode="market"):
    """One order per signal. Shapes are multiples of M15 ATR (unit='atr') or of the VWAP band sigma
    (unit='sigma'). lookahead=True is the F1 twin: it fills at bar k's OWN open instead of bar k+1's."""
    u = at if unit == "atr" else sig
    tsig = b.tclose.values[k - 1] if lookahead else b.tclose.values[k]
    ref = b.open.values[k] if lookahead else b.open.values[k + 1]
    o = pd.DataFrame(dict(sidx=np.arange(len(k)), t_signal=tsig, side=side.astype(int),
                          entry_type=mode, entry=np.nan, expire=tsig, be_at_r=np.nan,
                          max_hold_min=float(hold)))
    if mode == "limit":
        o["entry"] = ref                                  # ref is passed in as the limit price by caller
        o["expire"] = o.t_signal + pd.Timedelta(minutes=30)
    o["sl"] = ref - side * u * stop
    o["tp"] = ref + side * u * tgt
    return o.dropna(subset=["sl", "tp"]).reset_index(drop=True)


KEEP = ["sidx", "side", "status", "t_signal", "t_fill", "fill", "t_exit", "exit", "exit_reason",
        "hold_h", "risk_pips", "net_pips", "R", "usd_01", "mfe_R", "mae_R", "sl", "tp"]


def both_sides(b, k, at, tgt, stop, **kw):
    """Simulate the SAME minutes twice: everything long, then everything short.

    Every order is independent (one_at_a_time=False), so the real rule's outcome at signal i is simply the
    long row when it declared +1 and the short row when it declared -1 -- and the same-minutes coin flip
    is the average of the two. One pair of runs therefore prices the rule, its mirror, its luck bar and
    every filter subset, without ever re-simulating.
    """
    res = {}
    for s in (1, -1):
        side = np.full(len(k), s, np.int8)
        o = _orders(b, k, at, side, tgt, stop, **kw)
        r = C.simulate(GOLD, o, one_at_a_time=False)
        r = r[r.status == "filled"][KEEP].set_index("sidx")
        res[s] = r
    both = res[1].index.intersection(res[-1].index)
    return res[1].loc[both], res[-1].loc[both]


def pick(L, S, side):
    """The rule's own trades, with the same-minutes coin-flip bar carried on every row."""
    s = pd.Series(np.asarray(side), index=np.arange(len(side))).reindex(L.index)
    long_sel = (s == 1).values
    out = pd.DataFrame(index=L.index)
    for col in L.columns:
        if col in ("status", "exit_reason"):
            out[col] = np.where(long_sel, L[col].values.astype(object), S[col].values.astype(object))
        elif np.issubdtype(np.asarray(L[col].values).dtype, np.datetime64):
            out[col] = pd.to_datetime(np.where(long_sel, L[col].values, S[col].values))
        else:
            out[col] = np.where(long_sel, L[col].values, S[col].values)
    out["side"] = np.where(long_sel, 1, -1)
    # the bar: a random direction at exactly these minutes
    out["luck_usd"] = (L.usd_01.values + S.usd_01.values) / 2.0
    out["luck_win"] = ((L.net_pips.values > 0).astype(float) + (S.net_pips.values > 0)) / 2.0
    out["fade_usd"] = np.where(long_sel, S.usd_01.values, L.usd_01.values)
    out["fade_win"] = np.where(long_sel, S.net_pips.values > 0, L.net_pips.values > 0)
    return out


def summarise(t, label=""):
    """Every reading carries its own same-minutes coin flip, and BUY and SELL are reported separately.

    EXPLORE is 21 months in which gold went from $866 to $1,429, so a pooled number flatters whichever
    side the trend was on. For a mean-reversion family that is not a detail, it is the whole answer.
    """
    if len(t) == 0:
        return dict(label=label, trades=0)
    s = C.stats(t)
    out = dict(label=label, trades=s["trades"], per_week=s["per_week"], right_in_100=s["win_pct"],
               luck_in_100=round(100 * t.luck_win.mean(), 2), PF=s["PF"], avg_net_pips=s["avg_net_pips"],
               usd_per_trade=round(t.usd_01.mean(), 3), luck_usd_per_trade=round(t.luck_usd.mean(), 3),
               usd_total=s["usd_per_01lot"], maxDD_R=s["maxDD_R"], avg_hold_h=s["avg_hold_h"],
               fade_in_100=round(100 * t.fade_win.mean(), 2))
    for sd, tag in ((1, "buy"), (-1, "sell")):
        g = t[t.side == sd]
        out[tag + "_n"] = len(g)
        out[tag + "_right_in_100"] = round(100 * (g.net_pips > 0).mean(), 2) if len(g) else np.nan
        out[tag + "_luck_in_100"] = round(100 * g.luck_win.mean(), 2) if len(g) else np.nan
        out[tag + "_usd"] = round(g.usd_01.mean(), 3) if len(g) else np.nan
        out[tag + "_PF"] = (round(g.R[g.R > 0].sum() / -g.R[g.R < 0].sum(), 2)
                            if len(g) and (g.R < 0).any() else np.nan)
    return out


# ==================================================================================== stages
def clip_stage(b, k, side, stage):
    A.gate(stage)
    a, z = A.STAGES[stage]
    t = b.time.values[k]
    m = (t >= np.datetime64(a)) & (t < np.datetime64(z))
    return k[m], side[m]


def set_costs(harsh=False):
    """Baseline = measured commission + the injected gold spread. Adverse = both doubled, 5x stop slip.

    A feed swap is impossible at STRESS -- the Dukascopy and Exness Zero gold feeds overlap only inside
    the sealed EXAM -- so the adverse-cost run IS the stress test.
    """
    global SPREAD_USD
    C.COMM[GOLD] = COMM_HARSH if harsh else COMM_BASE
    C.STOP_SLIP = SLIP_HARSH if harsh else SLIP_BASE
    SPREAD_USD = SPREAD_HARSH if harsh else A.gold_spread(GOLD)
    return SPREAD_USD


def be_rate(atr_price, tgt, stop):
    """Break-even bar at the cost setting currently in force (spread included -- autopsy_lib default)."""
    return A.breakeven_rate(GOLD, atr_price, tgt, stop, spread=SPREAD_USD)


def stage0():
    t0 = time.time()
    set_costs(False)
    b, sig = signal_sets()
    q, verdict = volume_and_spread_verdict()
    q.to_csv(os.path.join(PARTS, "volume_quality.csv"), index=False)
    verdict.to_csv(os.path.join(PARTS, "stage0_verdict.csv"), index=False)
    print(verdict.to_string(index=False))
    lines = group_f_checks(b, sig)
    with open(os.path.join(PARTS, "stage0_groupF.txt"), "w") as fh:
        for a, r in lines:
            fh.write(f"{a}: {r}\n")
            print(f"  {a}: {r}")
    # F1 -- the lookahead twin, on both rule families
    rows = []
    for nm in RULES:
        k, d = clip_stage(b, *sig[nm], "EXPLORE")
        at = A.atr_at(GOLD, "M15", b.tclose.values[k])
        for la in (False, True):
            L, S = both_sides(b, k, at, *REF_SHAPE, lookahead=la)
            t = pick(L, S, d)
            rows.append(dict(rule=nm, fill="OWN BAR (lookahead)" if la else "next bar (honest)", **summarise(t)))
            print("   ", rows[-1]["rule"], rows[-1]["fill"], rows[-1]["right_in_100"], rows[-1]["usd_per_trade"])
    la = pd.DataFrame(rows)
    la.to_csv(os.path.join(PARTS, "stage0_lookahead.csv"), index=False)
    print(f"stage0 done {time.time()-t0:.0f}s")


def ladder_one(nm):
    """The full 36-rung ATR ladder for ONE rule, saved as soon as each rung is done.

    One rule per process: the ladder is 36 shapes x 2 sides x ~10,000 orders and `common.simulate` is a
    Python loop over orders, so five rules in one process is ~75 minutes of wall clock and loses
    everything if it is interrupted.
    """
    t0 = time.time()
    set_costs(False)
    b, sig = signal_sets()
    k, d = clip_stage(b, *sig[nm], "EXPLORE")
    f = feature_table(b, k, d, V.ANCHOR_OF[RULES[nm][0]])
    f.to_parquet(os.path.join(PARTS, "feat_EXPLORE_%s.parquet" % nm))
    at = f.atr.values
    rows = []
    for tgt, stop in A.ATR_LADDER:
        L, S = both_sides(b, k, at, tgt, stop)
        t = pick(L, S, d)
        be = be_rate(float(np.nanmedian(at)), tgt, stop)
        rows.append(dict(rule=nm, tgt=tgt, stop=stop, be_in_100=round(be, 2), **summarise(t)))
        print("  ", nm, tgt, stop, rows[-1]["trades"], rows[-1]["right_in_100"], rows[-1]["luck_in_100"],
              rows[-1]["usd_per_trade"], "%.0fs" % (time.time() - t0), flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(PARTS, "ladder_%s.csv" % nm), index=False)
        if (tgt, stop) == REF_SHAPE:
            t.to_parquet(os.path.join(PARTS, "ref_EXPLORE_%s.parquet" % nm))
    print("%s ladder done %.0fs" % (nm, time.time() - t0))


def sigma_ladder_one(nm):
    """The same question with the shape denominated in the VWAP band's own sigma instead of ATR."""
    t0 = time.time()
    set_costs(False)
    b, sig = signal_sets()
    k, d = clip_stage(b, *sig[nm], "EXPLORE")
    f = feature_table(b, k, d, V.ANCHOR_OF[RULES[nm][0]])
    sgv = (f.f_sig_atr * f.atr).values
    rows = []
    for tgt, stop in A.ATR_LADDER:
        L, S = both_sides(b, k, f.atr.values, tgt, stop, unit="sigma", sig=sgv)
        t = pick(L, S, d)
        rows.append(dict(rule=nm, unit="sigma", tgt=tgt, stop=stop, **summarise(t)))
        print("  sig", nm, tgt, stop, rows[-1]["trades"], rows[-1]["right_in_100"],
              rows[-1]["usd_per_trade"], "%.0fs" % (time.time() - t0), flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(PARTS, "ladder_sigma_%s.csv" % nm), index=False)
    print("%s sigma ladder done %.0fs" % (nm, time.time() - t0))


def autopsy():
    """The catalogue, asked once, at the reference shape, on the primary rule -- plus the drift control,
    the luck bars and the entry-mechanics test. Reuses the ladder stage's cached parquet files."""
    t0 = time.time()
    set_costs(False)
    b, sig = signal_sets()
    tally = A.Tally("AUT05")
    feats, picks = {}, {}
    for nm in RULES:
        fp = os.path.join(PARTS, "feat_EXPLORE_%s.parquet" % nm)
        rp = os.path.join(PARTS, "ref_EXPLORE_%s.parquet" % nm)
        if os.path.exists(fp) and os.path.exists(rp):
            feats[nm], picks[nm] = pd.read_parquet(fp), pd.read_parquet(rp)
        else:
            k, d = clip_stage(b, *sig[nm], "EXPLORE")
            feats[nm] = feature_table(b, k, d, V.ANCHOR_OF[RULES[nm][0]])
            L, S = both_sides(b, k, feats[nm].atr.values, *REF_SHAPE)
            picks[nm] = pick(L, S, d)
        print("  loaded", nm, len(picks[nm]), flush=True)

    # ---------- the catalogue, asked ONCE, on the biggest pullback rule ----------
    rows = []
    t = picks["P_day"]
    f = feats["P_day"].set_index("sidx").reindex(t.index)
    for cond_id, note, mask in conditions(f, t):
        tab = A.score(t, mask)
        tab["luck_in_100"] = [round(100 * t.luck_win.mean(), 2),
                              round(100 * t[mask].luck_win.mean(), 2) if mask.sum() else np.nan,
                              round(100 * t[~mask].luck_win.mean(), 2) if (~mask).sum() else np.nan]
        tab["edge_vs_luck"] = (tab.right_in_100 - tab.luck_in_100).round(2)
        tally.add(cond_id, note, tab)
        rows.append(dict(cond=cond_id, note=note, kept=int(tab.trades[1]),
                         right_in_100=tab.right_in_100[1], luck_in_100=tab.luck_in_100[1],
                         edge=tab.edge_vs_luck[1], usd=tab.net_usd_per_trade[1],
                         right_if_false=tab.right_in_100[2], usd_if_false=tab.net_usd_per_trade[2],
                         all_right=tab.right_in_100[0], all_usd=tab.net_usd_per_trade[0]))
    pd.DataFrame(rows).to_csv(os.path.join(PARTS, "explore_autopsy.csv"), index=False)
    tf = tally.frame()
    tf.to_csv(os.path.join(PARTS, "explore_tally.csv"), index=False)
    print("conditions tried:", len(tf))

    # ---------- the survivors, re-checked on the other four rules (confirmation, not new trials) ------
    srv = ["B3b", "STRUCT", "C1b", "B1c", "A7", "A6", "D2"]
    crows = []
    for nm in RULES:
        tt = picks[nm]
        ff = feats[nm].set_index("sidx").reindex(tt.index)
        for cond_id, note, mask in conditions(ff, tt):
            if cond_id not in srv:
                continue
            g = tt[mask]
            crows.append(dict(rule=nm, cond=cond_id, kept=int(mask.sum()),
                              right_in_100=round(100 * (g.net_pips > 0).mean(), 2) if len(g) else np.nan,
                              luck_in_100=round(100 * g.luck_win.mean(), 2) if len(g) else np.nan,
                              usd=round(g.usd_01.mean(), 3) if len(g) else np.nan, note=note))
    pd.DataFrame(crows).to_csv(os.path.join(PARTS, "explore_survivors_all_rules.csv"), index=False)

    # ---------- the drift control: always-long / always-short at exactly the same minutes ----------
    drows = []
    for nm in RULES:
        k, d = clip_stage(b, *sig[nm], "EXPLORE")
        L, S = both_sides(b, k, feats[nm].atr.values, *REF_SHAPE)
        t = pick(L, S, d)
        for lab, g, w in (("the rule's own direction call", t, t.net_pips > 0),
                          ("ALWAYS LONG, same minutes", L, L.net_pips > 0),
                          ("ALWAYS SHORT, same minutes", S, S.net_pips > 0)):
            drows.append(dict(rule=nm, reading=lab, n=len(g),
                              right_in_100=round(100 * w.mean(), 2), usd=round(g.usd_01.mean(), 3)))
        drows.append(dict(rule=nm, reading="coin flip, same minutes", n=len(t),
                          right_in_100=round(100 * t.luck_win.mean(), 2), usd=round(t.luck_usd.mean(), 3)))
        pd.DataFrame(drows).to_csv(os.path.join(PARTS, "explore_drift_control.csv"), index=False)
    print(pd.DataFrame(drows).to_string(index=False))

    # ---------- autopsy_lib's own luck bar, matched on hours ----------
    lb = []
    for nm in RULES:
        k, d = clip_stage(b, *sig[nm], "EXPLORE")
        hrs = sorted(set(pd.DatetimeIndex(b.time.values[k]).hour))
        r = A.luck_bar(GOLD, 20000, *REF_SHAPE, "EXPLORE", seed=7, hours=hrs)
        fl = r[r.status == "filled"]
        lb.append(dict(rule=nm, n_random=len(r), filled=len(fl),
                       skipped_busy=int((r.status == "skipped_busy").sum()),
                       right_in_100=round(100 * (fl.net_pips > 0).mean(), 2), usd=round(fl.usd_01.mean(), 3)))
        print("  luck_bar", lb[-1], flush=True)
        pd.DataFrame(lb).to_csv(os.path.join(PARTS, "explore_luckbar.csv"), index=False)

    # ---------- entry mechanics: market, or a limit resting back at the VWAP ----------
    erows = []
    for nm in ("P_day",):
        k, d = clip_stage(b, *sig[nm], "EXPLORE")
        f = feats[nm]
        vw = anchor_arrays(b, V.ANCHOR_OF[RULES[nm][0]])["vw"]
        for tgt, stop in ((1.5, 1.0), (3.0, 1.0)):
            for mode, lvl in (("market", None), ("limit_vwap", vw[k]),
                              ("limit_half", (vw[k] + b.close.values[k]) / 2)):
                if mode == "market":
                    L, S = both_sides(b, k, f.atr.values, tgt, stop)
                else:
                    L, S = _limit_both(b, k, f.atr.values, d, tgt, stop, lvl)
                t = pick(L, S, d)
                erows.append(dict(rule=nm, entry=mode, tgt=tgt, stop=stop, **summarise(t)))
                print("  entry", nm, mode, tgt, stop, erows[-1]["trades"],
                      erows[-1]["right_in_100"], erows[-1]["usd_per_trade"], flush=True)
                pd.DataFrame(erows).to_csv(os.path.join(PARTS, "explore_entry_modes.csv"), index=False)
    print("autopsy done %.0fs" % (time.time() - t0))


def _limit_both(b, k, at, side, tgt, stop, level):
    """A limit order resting at `level` (the VWAP, or half way back to it), placed after bar k closed."""
    res = {}
    for s in (1, -1):
        sd = np.full(len(k), s, np.int8)
        o = pd.DataFrame(dict(sidx=np.arange(len(k)), t_signal=b.tclose.values[k], side=sd.astype(int),
                              entry_type="limit", entry=level, be_at_r=np.nan, max_hold_min=float(HOLD)))
        o["expire"] = o.t_signal + pd.Timedelta(minutes=30)
        o["sl"] = level - s * at * stop
        o["tp"] = level + s * at * tgt
        o = o.dropna(subset=["sl", "tp", "entry"]).reset_index(drop=True)
        r = C.simulate(GOLD, o, one_at_a_time=False)
        res[s] = r[r.status == "filled"][KEEP].set_index("sidx")
    both = res[1].index.intersection(res[-1].index)
    return res[1].loc[both], res[-1].loc[both]


def conditions(f, t):
    """Every catalogue condition this strategy can be asked, as one mechanical sentence each."""
    q = lambda col, p: f[col] >= f[col].quantile(p)                     # noqa: E731
    ql = lambda col, p: f[col] <= f[col].quantile(p)                    # noqa: E731
    out = [
        ("B3", "at least 120 minutes have passed since the anchor", (f.f_cnt >= 120).values),
        ("B3b", "30 to 120 minutes since the anchor", ((f.f_cnt >= 30) & (f.f_cnt < 120)).values),
        ("A7", "price had travelled at least 1.5 sigma from VWAP before coming back", (f.f_exc_sig >= 1.5).values),
        ("A7b", "price had travelled at least 1.0 ATR from VWAP before coming back", (f.f_exc_atr >= 1.0).values),
        ("C3", "this is the FIRST touch of VWAP in the window", (f.f_touch_n <= 1).values),
        ("C3b", "this is the third touch of VWAP or later", (f.f_touch_n >= 3).values),
        ("A3", "the window is trending, not chopping (efficiency ratio in the top third)", q("f_eff", 2 / 3.).values),
        ("A3b", "M15 EMA50 and EMA200 are at least 1 ATR apart", (f.f_ema_sep.abs() >= 1.0).values),
        ("A1", "the trade agrees with the M15 EMA50 side", (f.f_m15_agree == 1).values),
        ("A1b", "the trade agrees with the H1 EMA50 side", (f.f_h1_agree == 1).values),
        ("A1c", "the trade agrees with M15 market structure (BOS/CHoCH trend)", (f.f_trend15_agree == 1).values),
        ("A7c", "price is NOT stretched from the M15 EMA50 (under 1 ATR)", ql("f_dist_e50", 0.5).values),
        ("A4", "M15 ATR is in the top third of its own trailing year", q("f_atr_rank", 2 / 3.).values),
        ("A4b", "M15 ATR is in the bottom third of its own trailing year", ql("f_atr_rank", 1 / 3.).values),
        ("A5", "ATR is expanding (now above its value 12 hours ago)", (f.f_atr_exp >= 1.0).values),
        ("A6", "less than half the normal day's range has been spent", ql("f_day_spent", 0.5).values),
        ("C5", "price sits in the middle half of the prior day's range", ((f.f_dpos > 0.25) & (f.f_dpos < 0.75)).values),
        ("C1", "at least 1 ATR of clear air to the next swing level in the trade's direction", (f.f_room >= 1.0).values),
        ("C1b", "at least 2 ATR of clear air to the next swing level", (f.f_room >= 2.0).values),
        ("C2", "the opposing level is at least 1 ATR behind the entry", (f.f_behind >= 1.0).values),
        ("STRUCT", "the VWAP line sits within 0.25 ATR of a confirmed M15 swing level", (f.f_near_level <= 0.25).values),
        ("C4", "entry is not within 0.1 ATR of a $5 round number", (f.f_round5 > 0.1).values),
        ("D1", "the trigger candle's body is at least 0.3 ATR", (f.f_body >= 0.3).values),
        ("D2", "the trigger closed in the far third of its own range", (f.f_closepos >= 2 / 3.).values),
        ("D3", "the pullback overshot the VWAP by at least 0.25 ATR", (f.f_overshoot >= 0.25).values),
        ("D3b", "the pullback barely tagged the VWAP (overshoot under 0.1 ATR)", (f.f_overshoot < 0.1).values),
        ("D4", "the pullback took at least 10 bars", (f.f_pb_bars >= 10).values),
        ("D5", "the trigger engulfed the previous candle", (f.f_engulf == 1).values),
        ("B1", "London hours only (07:00-16:00 UTC)", (f.f_ldn == 1).values),
        ("B1b", "New York hours only (13:00-21:00 UTC)", (f.f_ny == 1).values),
        ("B1c", "Asia hours only (before 07:00 UTC)", (f.f_asia == 1).values),
        ("B2", "the London/New York overlap only (13:00-16:00 UTC)", (f.f_overlap == 1).values),
        ("B6", "the last few days of the month", (f.f_monthend == 1).values),
        ("A8", "Tuesday to Thursday only", f.f_dow.isin([1, 2, 3]).values),
        ("SIG", "the band is wide relative to ATR (sigma over 0.75 ATR)", (f.f_sig_atr >= 0.75).values),
        ("E2", "the trade's own stop was at least 1 ATR wide", (t.risk_pips.values * C.pip(GOLD) >= f.atr.values * 0.9)),
    ]
    return out


# ------------------------------------------------------------------ the refinements
def refinements():
    """The <=3 that go forward. Chosen on EXPLORE only; see report.md section 6."""
    return {
        "R1_shape": dict(rule="P_day", tgt=3.0, stop=1.0, mask=None,
                         note="the base rule, re-shaped to 3.0xATR target / 1.0xATR stop"),
        "R2_youngvwap": dict(rule="P_day", tgt=3.0, stop=1.0, mask="B3b",
                             note="R1 plus condition B3b: the anchor is 30 to 120 minutes old"),
        "R3_onlevel": dict(rule="P_day", tgt=3.0, stop=1.0, mask="STRUCT",
                           note="R1 plus condition STRUCT: the VWAP sits within 0.25 ATR of a confirmed "
                                "M15 swing level"),
    }


#: The two filter thresholds are FROZEN here from EXPLORE and are not re-fitted on VALIDATE or STRESS.
#: STRUCT's 0.25 is an absolute ATR distance, so it transfers unchanged. B3b's 30/120 are clock minutes.
def _mask_of(name, f):
    if name is None:
        return np.ones(len(f), bool)
    if name == "B3b":
        return ((f.f_cnt >= 30) & (f.f_cnt < 120)).values
    if name == "STRUCT":
        return (f.f_near_level <= 0.25).values
    raise KeyError(name)


def run_refinements(stage, harsh=False, tag=None):
    t0 = time.time()
    set_costs(harsh)
    b, sig = signal_sets()
    rows, keep = [], {}
    for name, spec in refinements().items():
        nm = spec["rule"]
        k, d = clip_stage(b, *sig[nm], stage)
        f = feature_table(b, k, d, V.ANCHOR_OF[RULES[nm][0]])
        m = _mask_of(spec.get("mask"), f)
        k2, d2, f2 = k[m], d[m], f[m].reset_index(drop=True)
        f2["sidx"] = np.arange(len(f2))
        if spec.get("entry") == "limit_vwap":
            vw, _, _, _ = V.vwap_sigma(b, np.ones(len(b)), V.ANCHOR_OF[RULES[nm][0]])
            L, S = _limit_both(b, k2, f2.atr.values, d2, spec["tgt"], spec["stop"], vw[k2])
        else:
            L, S = both_sides(b, k2, f2.atr.values, spec["tgt"], spec["stop"])
        t = pick(L, S, d2)
        t = t.join(f2.set_index("sidx").drop(columns=["side", "atr"]), how="left")
        be = be_rate(float(np.nanmedian(f2.atr)), spec["tgt"], spec["stop"])
        rows.append(dict(refinement=name, stage=stage + ("_HARSH" if harsh else ""), be_in_100=round(be, 2),
                         **summarise(t), note=spec["note"]))
        keep[name] = t
        print(" ", rows[-1], flush=True)
    out = pd.DataFrame(rows)
    sfx = (tag or stage) + ("_HARSH" if harsh else "")
    out.to_csv(os.path.join(PARTS, f"refinements_{sfx}.csv"), index=False)
    for name, t in keep.items():
        tt = t.reset_index().rename(columns={"index": "sidx"})
        tt["sym"] = GOLD
        tt["status"] = "filled"
        tt["gross_pips"] = tt.net_pips + C.COMM[GOLD]
        tt["period"] = C.period_of(tt.t_signal)
        C.save("AUT05_vwap", f"{name}_{sfx}", tt)
    print(f"{stage} refinements done {time.time()-t0:.0f}s")
    return out


def examples():
    """10 trades a human can check on a chart, re-derived from the raw bars."""
    set_costs(False)
    b, sig = signal_sets()
    vw, sg, cnt, _ = V.vwap_sigma(b, np.ones(len(b)), "day")
    k, d = clip_stage(b, *sig["P_day"], "EXPLORE")
    f = feature_table(b, k, d, "day")
    m = _mask_of("B3b", f)
    k2, d2, f2 = k[m], d[m], f[m].reset_index(drop=True)
    f2["sidx"] = np.arange(len(f2))
    L, S = both_sides(b, k2, f2.atr.values, 3.0, 1.0)
    t = pick(L, S, d2).join(f2.set_index("sidx"), how="left", rsuffix="_f")
    rng = np.random.default_rng(3)
    pickr = t.iloc[np.sort(rng.choice(len(t), 10, replace=False))]
    cols = ["t_signal", "t_fill", "side", "fill", "sl", "tp", "t_exit", "exit", "exit_reason",
            "hold_h", "R", "usd_01", "f_cnt", "f_eff", "f_exc_sig", "f_touch_n"]
    ex = pickr[cols].copy()
    ex["vwap_at_signal"] = vw[pickr.k.values].round(3)
    ex["sigma_at_signal"] = sg[pickr.k.values].round(3)
    ex["prev_close"] = b.close.values[pickr.k.values - 1].round(3)
    ex["sig_low"] = b.low.values[pickr.k.values].round(3)
    ex["sig_high"] = b.high.values[pickr.k.values].round(3)
    ex["sig_close"] = b.close.values[pickr.k.values].round(3)
    ex["next_open"] = b.open.values[pickr.k.values + 1].round(3)
    # hand re-derivation of the rule, outside the signal code
    ok = []
    for r in ex.itertuples():
        if r.side == 1:
            ok.append(bool(r.prev_close > r.vwap_at_signal and r.sig_low <= r.vwap_at_signal < r.sig_close))
        else:
            ok.append(bool(r.prev_close < r.vwap_at_signal and r.sig_high >= r.vwap_at_signal > r.sig_close))
    ex["rule_reproduced_by_hand"] = ok
    ex["fill_is_next_minute_open"] = np.isclose(ex.fill.values, ex.next_open.values)
    ex.to_csv(os.path.join(PARTS, "examples.csv"), index=False)
    print(ex.to_string())
    print("rule reproduced by hand on all 10:", all(ok))
    # brute-force VWAP re-computation at those bars, using bars <= k only
    err = []
    for kk in pickr.k.values:
        d0 = b.time.values[kk].astype("datetime64[D]")
        j0 = int(np.searchsorted(b.time.values.astype("datetime64[D]"), d0, "left"))
        tp = (b.high.values[j0:kk + 1] + b.low.values[j0:kk + 1] + b.close.values[j0:kk + 1]) / 3.0
        err.append(abs(tp.mean() - vw[kk]))
    print("max VWAP error, recomputed from scratch with bars <= k only:", max(err))


def break_even_bars():
    """The shape question, stated as the brief states it: how many right in 100 does each shape need?"""
    b = C.bars(GOLD, "M1")
    a, z = A.STAGES["EXPLORE"]
    m = (b.time >= a) & (b.time < z)
    at = float(np.nanmedian(A.atr_at(GOLD, "M15", b.tclose.values[m.values][::97])))
    rows = []
    for comm, lab in ((COMM_BASE, "baseline $1.60"), (COMM_HARSH, "stress $3.20")):
        C.COMM[GOLD] = comm
        for tgt, stop in A.ATR_LADDER:
            rows.append(dict(cost=lab, tgt=tgt, stop=stop, median_atr=round(at, 3),
                             needs_right_in_100=round(A.breakeven_rate(GOLD, at, tgt, stop), 2),
                             win_dollars=round(A.shape_dollars(GOLD, at, tgt), 2)))
    set_costs(False)
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(PARTS, "breakeven_bars.csv"), index=False)
    print(d[d.cost == "baseline $1.60"].to_string(index=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stage0"
    if cmd == "stage0":
        stage0()
    elif cmd == "be":
        break_even_bars()
    elif cmd == "ladder":
        ladder_one(sys.argv[2])
    elif cmd == "sigma":
        sigma_ladder_one(sys.argv[2])
    elif cmd == "autopsy":
        autopsy()
    elif cmd == "validate":
        run_refinements("VALIDATE")
    elif cmd == "stress":
        run_refinements("STRESS")
        run_refinements("STRESS", harsh=True)
    elif cmd == "explore_ref":
        run_refinements("EXPLORE")
    elif cmd == "examples":
        examples()
    else:
        raise SystemExit(f"unknown command {cmd!r}")
