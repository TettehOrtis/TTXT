"""BT21 Family R — break-and-retest, and opening-range breakout with a retest.

Pre-registered in `bt/BT21_BRIEF.md`. **Six variants, fixed before any code ran:**

    R1  M1 horizontal micro S/R: swing level confirmed 5 bars later, broken by >= buffer,
        price returns inside 30 bars, the retest HOLDS -> enter on the rejection candle's close. FOLLOW.
    R2  the same trigger set, FADED.
    R3  ORB London  (first 15 minutes of the London open) -> break -> retest of the broken edge
        that holds -> enter on the rejection close.
    R4  ORB New York (first 15 minutes of the NY open), otherwise as R3.
    R5  ORB London,  IMMEDIATE break entry (control).
    R6  ORB New York, IMMEDIATE break entry (control).

R5/R6 exist only to isolate what the retest requirement adds or costs; `research/orb.py` already
showed the immediate break has no edge on gold, and reproducing that is the point.

Nothing here re-implements a simulator. The first-passage machinery, the LADDER, the costs and the
one-basket-at-a-time engine are `bt/bt20_lib.py` and `bt/bt20_run.py`, imported read-only:
    B.walk()          first-passage tables for every (target, stop) on the LADDER, both sides
    R20.engine()      one basket at a time, 60 s cooldown
    R20.best_worst()  net dollars for BOTH sides at each signal (gives the luck bar)
Costs are BT20's: $1.10 round-trip commission, 0.05 extra spread, 0.10 lots, $10 per $1 of gold.

Causality. Every rule is decided on the CLOSE of a bar k and filled at the OPEN of bar k+1.
The "lookahead twin" (fill at bar k's own open) is built from the same signals and reported beside
the real reading, because BT17 section 3 showed that single change manufactures a large fake edge.

Session anchoring is DST-aware and copied from `research/orb.py::session_starts` (London 08:00
Europe/London, New York 09:30 America/New_York), which lands on 07:00 / 13:30 UTC in summer and
08:00 / 14:30 UTC in winter. The brief's "07:00-07:15 / 13:30-13:45 UTC" are the summer values of
exactly those local opens; the method section of the brief makes DST-awareness non-negotiable, so
local time wins and the fixed-UTC version is reported as a sensitivity, not as a 7th variant.

    .venv/Scripts/python.exe smc_bot/bt/bt21r_run.py                       # zero  2021-2026
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/bt21r_run.py      # duka  2009-2020
"""
import os
import sys
import numpy as np
import pandas as pd
from numba import njit
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, RESULTS                                    # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402
from bt import bt18_lib as B18                                         # noqa: E402
from bt import bt20_lib as B                                           # noqa: E402
from bt import bt20_run as R20                                         # noqa: E402

GOLD = "XAUUSD"
DS = os.environ.get("SMC_DATASET", "zero")
OUT = os.path.join(RESULTS, "BT21R")
PARTS = os.path.join(OUT, "parts")
os.makedirs(PARTS, exist_ok=True)

# ---------------------------------------------------------------- pre-registered constants
N_SWING = 5          # brief: "a level from an M1 swing confirmed 5 bars later"
BREAK_MAX = 120      # bars a level may wait for its break before it is forgotten (2 hours)
RETEST_MAX = 30      # brief: "price returns within 30 bars"
BUFK = 0.25          # break buffer = BUFK * ATR60, causal. A fixed dollar buffer would mean something
ATR_W = 60           # different at $1,700 gold and at $4,000 gold, and the zero feed's median spread
                     # is 0.0, so research/orb.py's spread-scaled buffer collapses to zero there.
ORB_MIN = 15         # brief: the first 15 minutes of the open
ORB_WIN = 120        # bars the session has to break its range (2 hours; research/orb.py's own min window)
GAP_H = 6            # a jump of more than this many hours is a market closure: no pattern may span one

SESS = {"LON": ("Europe/London", 8, 0), "NY": ("America/New_York", 9, 30)}
SESS_UTC_SUMMER = {"LON": "07:00", "NY": "13:30"}

VARIANTS = ["R1_sr_retest", "R2_sr_retest_fade", "R3_orb_lon_retest", "R4_orb_ny_retest",
            "R5_orb_lon_immediate", "R6_orb_ny_immediate"]
DECLARED = {"R1_sr_retest": "follow", "R2_sr_retest_fade": "fade", "R3_orb_lon_retest": "follow",
            "R4_orb_ny_retest": "follow", "R5_orb_lon_immediate": "follow",
            "R6_orb_ny_immediate": "follow"}
assert len(VARIANTS) == 6, len(VARIANTS)

PRIMARY = (15, 70)   # the sniper's live shape: $15 target / $70 stop at 0.10 lots
SHIPPED = (7, 70)    # the shipped shape
SHAPES = B.SHAPES    # 36 shapes, BT20's grid

# duka 2009-2020 | zero 2021-2023 | zero 2024-2026. Never pooled.
ERAS = ([("2009-2020", "2009-03-15", "2021-01-01")] if DS == "duka" else
        [("2021-2023", "2021-01-01", "2024-01-01"),
         ("2024-2026", "2024-01-01", "2030-01-01")])


# ================================================================ bar-level helpers
def segments(b):
    """Segment id per bar; it increments at every gap longer than GAP_H hours (weekend / holiday).
    No pattern in BT21R may span a segment boundary."""
    tv = b.time.values.astype("datetime64[ns]").astype(np.int64)
    gap = np.zeros(len(tv), bool)
    gap[1:] = np.diff(tv) > GAP_H * 3600 * 1_000_000_000
    return np.cumsum(gap).astype(np.int64)


def atr_causal(b, w=ATR_W):
    """Mean bar range over the w bars ENDING AT k-1, in price units. Value at k uses only closed bars."""
    r = pd.Series(b.high.values - b.low.values)
    return r.rolling(w, min_periods=w).mean().shift(1).values.astype(np.float64)


def minutes_of(b):
    return (b.time.values.astype("datetime64[m]").astype(np.int64))


def session_starts(b, tzname, hh, mm, maxgap=3):
    """Bar index of each session open, DST-aware. Same algorithm as research/orb.py::session_starts."""
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


def session_starts_fixed_utc(b, hhmm):
    """The brief's literal fixed-UTC anchor, for the DST sensitivity check only."""
    hh, mm = (int(x) for x in hhmm.split(":"))
    t = b.time
    return np.where((t.dt.hour.values == hh) & (t.dt.minute.values == mm))[0].astype(np.int64)


# ================================================================ R1/R2 — horizontal micro S/R
@njit(cache=True)
def _scan_sr(h, l, c, seg, sh_idx, sl_idx, atr, N, bmax, rmax, bufk):
    """For every confirmed M1 swing level: wait for a close beyond it by >= buffer, then require the
    FIRST return to the level inside `rmax` bars to close back on the break side (the rejection candle).

    Returns (signal bar, follow side, level, break bar, swing bar). One signal per level at most;
    a level whose first return closes through it is dead, not retried."""
    n = len(h)
    nsh = len(sh_idx)
    m = nsh + len(sl_idx)
    sig = np.full(m, -1, np.int64)
    side = np.zeros(m, np.int8)
    lvl = np.zeros(m, np.float64)
    brk = np.full(m, -1, np.int64)
    swg = np.full(m, -1, np.int64)
    k = 0
    for q in range(m):
        if q < nsh:
            i = sh_idx[q]
            up = True
        else:
            i = sl_idx[q - nsh]
            up = False
        lv = h[i] if up else l[i]
        j0 = i + N                      # the swing is known once bar i+N has closed
        j = j0
        bj = -1
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
            else:
                if c[j] < lv - buf:
                    bj = j
                    break
            j += 1
        if bj < 0:
            continue
        r = bj + 1
        while r < n and r <= bj + rmax:
            if seg[r] != seg[bj]:
                break
            if up:
                if l[r] <= lv:                       # came back to the broken resistance
                    if c[r] > lv:                    # and closed back above it: the retest held
                        sig[k] = r
                        side[k] = 1
                        lvl[k] = lv
                        brk[k] = bj
                        swg[k] = i
                        k += 1
                    break                            # first return decides, either way
            else:
                if h[r] >= lv:
                    if c[r] < lv:
                        sig[k] = r
                        side[k] = -1
                        lvl[k] = lv
                        brk[k] = bj
                        swg[k] = i
                        k += 1
                    break
            r += 1
    return sig[:k], side[:k], lvl[:k], brk[:k], swg[:k]


def sr_signals(b, seg=None, atr=None):
    """R1's raw trigger set. Deduplicated on the signal bar (first in time wins)."""
    from smc_core import swings
    h, l, c = b.high.values, b.low.values, b.close.values
    if seg is None:
        seg = segments(b)
    if atr is None:
        atr = atr_causal(b)
    is_sh, is_sl = swings(h, l, N_SWING)
    sh = np.where(is_sh)[0].astype(np.int64)
    sl = np.where(is_sl)[0].astype(np.int64)
    sig, side, lvl, brk, swg = _scan_sr(h, l, c, seg, sh, sl, atr, N_SWING, BREAK_MAX, RETEST_MAX, BUFK)
    o = np.lexsort((brk, sig))
    sig, side, lvl, brk, swg = sig[o], side[o], lvl[o], brk[o], swg[o]
    keep = np.r_[True, np.diff(sig) > 0]
    raw = len(sig)
    sig, side, lvl, brk, swg = sig[keep], side[keep], lvl[keep], brk[keep], swg[keep]
    return dict(sig=sig, side=side.astype(np.int8), level=lvl, brk=brk, swing=swg,
                n_raw=raw, n_dropped_dup=raw - len(sig), n_swings=len(sh) + len(sl))


# ================================================================ R3-R6 — opening range breakout
@njit(cache=True)
def _scan_orb(h, l, c, seg, starts, mins, atr, rng_n, win, rmax, bufk, retest):
    n = len(h)
    m = len(starts)
    sig = np.full(m, -1, np.int64)
    side = np.zeros(m, np.int8)
    rhv = np.zeros(m, np.float64)
    rlv = np.zeros(m, np.float64)
    brk = np.full(m, -1, np.int64)
    st = np.full(m, -1, np.int64)
    nbrk = 0
    k = 0
    for q in range(m):
        s = starts[q]
        if s + rng_n + win + 2 >= n:
            continue
        if mins[s + rng_n - 1] - mins[s] != rng_n - 1:       # the 15 range minutes must be contiguous
            continue
        rh = h[s]
        rl = l[s]
        for j in range(s + 1, s + rng_n):
            if h[j] > rh:
                rh = h[j]
            if l[j] < rl:
                rl = l[j]
        if not (rh > rl):
            continue
        bj = -1
        up = False
        for j in range(s + rng_n, min(s + rng_n + win, n)):
            if seg[j] != seg[s]:
                break
            a = atr[j]
            if not (a > 0.0):
                continue
            buf = bufk * a
            if c[j] > rh + buf:                               # close-based: never ambiguous
                bj = j
                up = True
                break
            if c[j] < rl - buf:
                bj = j
                up = False
                break
        if bj < 0:
            continue
        nbrk += 1
        if retest == 0:
            sig[k] = bj
            side[k] = 1 if up else -1
            rhv[k] = rh
            rlv[k] = rl
            brk[k] = bj
            st[k] = s
            k += 1
            continue
        lv = rh if up else rl
        for r in range(bj + 1, min(bj + 1 + rmax, n)):
            if seg[r] != seg[bj]:
                break
            if up:
                if l[r] <= lv:
                    if c[r] > lv:
                        sig[k] = r
                        side[k] = 1
                        rhv[k] = rh
                        rlv[k] = rl
                        brk[k] = bj
                        st[k] = s
                        k += 1
                    break
            else:
                if h[r] >= lv:
                    if c[r] < lv:
                        sig[k] = r
                        side[k] = -1
                        rhv[k] = rh
                        rlv[k] = rl
                        brk[k] = bj
                        st[k] = s
                        k += 1
                    break
    return sig[:k], side[:k], rhv[:k], rlv[:k], brk[:k], st[:k], nbrk


def orb_signals(b, sess, retest, seg=None, atr=None, mins=None, fixed_utc=False):
    if seg is None:
        seg = segments(b)
    if atr is None:
        atr = atr_causal(b)
    if mins is None:
        mins = minutes_of(b)
    if fixed_utc:
        starts = session_starts_fixed_utc(b, SESS_UTC_SUMMER[sess])
        gaps = np.zeros(len(starts))
    else:
        tz, hh, mm = SESS[sess]
        starts, _utc, gaps = session_starts(b, tz, hh, mm)
    sig, side, rh, rl, brk, st, nbrk = _scan_orb(
        b.high.values, b.low.values, b.close.values, seg, starts, mins, atr,
        ORB_MIN, ORB_WIN, RETEST_MAX, BUFK, 1 if retest else 0)
    o = np.argsort(sig, kind="stable")
    return dict(sig=sig[o], side=side[o].astype(np.int8), rh=rh[o], rl=rl[o], brk=brk[o],
                start=st[o], n_sessions=len(starts), n_breaks=nbrk,
                late_starts=int((gaps > 0).sum()))


# ================================================================ the six variants
def all_signals(b):
    """{variant: dict(sig, side, ...)} plus the shared diagnostics. Decision bar = `sig`; the basket
    opens at `sig + 1` (and at `sig` for the lookahead twin)."""
    seg, atr, mins = segments(b), atr_causal(b), minutes_of(b)
    sr = sr_signals(b, seg, atr)
    out = {"R1_sr_retest": dict(sr),
           "R2_sr_retest_fade": dict(sr, side=(-sr["side"]).astype(np.int8)),
           "R3_orb_lon_retest": orb_signals(b, "LON", True, seg, atr, mins),
           "R4_orb_ny_retest": orb_signals(b, "NY", True, seg, atr, mins),
           "R5_orb_lon_immediate": orb_signals(b, "LON", False, seg, atr, mins),
           "R6_orb_ny_immediate": orb_signals(b, "NY", False, seg, atr, mins)}
    return out, dict(seg=seg, atr=atr, mins=mins)


def entry_union(sigs, nbars):
    """Every bar a basket could open at: the causal fill (k+1) and the lookahead twin (k)."""
    e = np.concatenate([np.r_[s["sig"], s["sig"] + 1] for s in sigs.values()])
    e = np.unique(e)
    return e[(e > 0) & (e < nbars - B.HORIZON - 2)].astype(np.int64)


# ================================================================ walk table (BT20 machinery)
def walk_path(ds=None):
    return os.path.join(PARTS, f"walk_{ds or DS}.npz")


def build_walk(b=None, force=False):
    import time as _t
    f = walk_path()
    if os.path.exists(f) and not force:
        print("  skip", f)
        return
    if b is None:
        b = bars(GOLD, "M1")
    sigs, _aux = all_signals(b)
    union = entry_union(sigs, len(b))
    for nm in VARIANTS:
        print(f"    {nm}: {len(sigs[nm]['sig']):,} signals")
    print(f"  {DS}: {len(union):,} unique entry bars to walk")
    t0 = _t.time()
    o = B.walk(b, union)
    np.savez_compressed(f + ".tmp.npz", union=union,
                        t=b.time.values[union].astype("datetime64[ns]").astype(np.int64), **o)
    os.replace(f + ".tmp.npz", f)
    np.savez_compressed(os.path.join(PARTS, f"sig_{DS}.npz"),
                        **{f"{nm}__{k}": np.asarray(v) for nm, s in sigs.items()
                           for k, v in s.items() if isinstance(v, np.ndarray)},
                        **{f"{nm}__meta_{k}": np.int64(v) for nm, s in sigs.items()
                           for k, v in s.items() if not isinstance(v, np.ndarray)})
    print(f"  walk done in {_t.time()-t0:.0f}s")


def load_walk(ds=None):
    z = np.load(walk_path(ds))
    return z["union"], z["up"], z["dn"], z["final"], z["t"]


def load_sigs(ds=None):
    z = np.load(os.path.join(PARTS, f"sig_{ds or DS}.npz"))
    out = {}
    for nm in VARIANTS:
        d = {k.split("__", 1)[1]: z[k] for k in z.files if k.startswith(nm + "__")}
        out[nm] = d
    return out


# ================================================================ measurement
def _acc(bw, isd):
    """Tie-aware accuracy, the BT15/BT17 convention: when both sides pay the same the direction call
    is irrelevant and counts as a half. (BT20's `acc` silently gives every tie to the long.)"""
    better = np.where(bw[0] >= bw[1], 1, -1)
    tie = bw[0] == bw[1]
    return float(np.where(tie, 0.5, (isd == better)).mean()), float(tie.mean())


def _bar(bw):
    best, worst = bw.max(axis=0), bw.min(axis=0)
    eb, ew = float(best.mean()), float(worst.mean())
    xs = 100.0 * (-ew) / (eb - ew) if eb > ew else np.nan
    return xs, float(bw.mean()), eb, ew


def measure(e, d, pos, up, dn, final, T, S, with_engine=True):
    """One (variant, era, shape) reading. `e` entry bars (sorted), `d` declared sides, `pos` their row
    in the walk table. Signal-level accuracy is the entry's skill; the engine gives the robot's money."""
    bw_all = R20.best_worst(up, dn, final, pos, T, S)
    acc_all, tie_all = _acc(bw_all, d)
    bar_all, coin_all, eb, ew = _bar(bw_all)
    r = dict(n_sig=len(e), acc=round(100 * acc_all, 2), luck_acc=round(bar_all, 2),
             beats=round(100 * acc_all - bar_all, 2), ties_pct=round(100 * tie_all, 2),
             coin_usd=round(coin_all, 3), E_best=round(eb, 2), E_worst=round(ew, 2))
    if not with_engine:
        return r
    it, isd, igr, iex, ibar = R20.engine(e, d, pos, up, dn, final, T, S)
    if len(it) == 0:
        return dict(r, trades=0)
    net = igr - B.COMM
    bw = R20.best_worst(up, dn, final, pos[it], T, S)
    acc_t, _tie_t = _acc(bw, isd)
    bar_t, coin_t, _a, _b = _bar(bw)
    r.update(trades=len(it), take_pct=round(100 * len(it) / len(e), 1),
             acc_taken=round(100 * acc_t, 2), luck_acc_taken=round(bar_t, 2),
             win_pct=round(100 * float((igr > 0).mean()), 2),
             gross=round(float(igr.sum()), 0), net=round(float(net.sum()), 0),
             per_basket=round(float(net.mean()), 3), coin_usd_taken=round(coin_t, 3),
             med_min=int(np.median(iex)), mean_min=round(float(iex.mean()), 1),
             unresolved_pct=round(100 * float((iex >= B.HORIZON).mean()), 2))
    return r


# ================================================================ placebo bar
def rotation_placebo(prep, draws=200, seed=21, best_of=None):
    """BT18's acceptance bar in BT21R's units: rotate each variant's chosen-side vector by a random
    whole number of WEEKS (signal rhythm and outcome clustering survive, any real link dies),
    recompute, take the best of the family in that draw, repeat `draws` times, report the P95.

    `prep` = [(name, sides, net_long, net_short, signals_per_day)]. Both signs of every reading are in
    the report, so acc2 (the two-sided bar, max(a, 100-a)) is the headline one."""
    rng = np.random.default_rng(seed)
    acc, mon, acc2, mon2 = [], [], [], []
    own = {nm: [] for nm, _, _, _, _ in prep}
    own2 = {nm: [] for nm, _, _, _, _ in prep}
    for _ in range(draws):
        aa, mm, a2, m2 = [], [], [], []
        for nm, sd, pl, ps, per_day in prep:
            w = rng.integers(1, 401)
            sh = max(1, int(max(per_day, 1e-6) * 7 * w)) % max(2, len(sd))
            r = np.roll(sd, sh)
            chosen = np.where(r == 1, pl, ps)
            other = np.where(r == 1, ps, pl)
            a = float(np.mean(np.where(chosen > other, 1.0, np.where(chosen < other, 0.0, 0.5)))) * 100
            aa.append(a)
            mm.append(float(chosen.mean()))
            a2.append(max(a, 100 - a))
            m2.append(max(float(chosen.mean()), float(other.mean())))
            own[nm].append(a)
            own2[nm].append(max(a, 100 - a))
        acc.append(max(aa))
        mon.append(max(mm))
        acc2.append(max(a2))
        mon2.append(max(m2))
    q = lambda v, p: float(np.percentile(v, p))                        # noqa: E731
    return dict(draws=draws, n_readings=len(prep), best_of=best_of or len(prep),
                acc_P50=q(acc, 50), acc_P95=q(acc, 95), acc_max=float(np.max(acc)),
                money_P50=q(mon, 50), money_P95=q(mon, 95), money_max=float(np.max(mon)),
                acc2_P50=q(acc2, 50), acc2_P95=q(acc2, 95), acc2_max=float(np.max(acc2)),
                money2_P95=q(mon2, 95),
                per_reading={nm: dict(n=int(len(prep[i][1])), acc_P95=q(own[nm], 95),
                                      acc2_P95=q(own2[nm], 95))
                             for i, (nm, _, _, _, _) in enumerate(prep)})


if __name__ == "__main__":
    build_walk()
