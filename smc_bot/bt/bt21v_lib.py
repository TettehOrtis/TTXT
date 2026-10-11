"""BT21 Family V — VWAP. Pre-registered in bt/BT21_BRIEF.md (6 variants, V1..V6, FIXED).

Why this family exists
----------------------
VWAP is the ONE setup in this project that has never been tested at all. `research_notes/
M1_entry_candidates.md` ranked it 4th of 5 and said "test last", naming two concerns we must settle
before any V-number means anything:
  (a) gold has no real volume, so a tick-volume VWAP is a proxy;
  (b) a 24-hour market has no natural day start, so the anchor is a free parameter.
(a) is answered by `volume_quality()` and by running the whole family a SECOND time with the volume
forced to 1 (the unweighted control). (b) is answered by running three anchors (day / London / New York)
as pre-registered variants.

Machinery
---------
Nothing here re-implements a simulator. `bt20_lib.walk()` gives the first-passage table (every rung of
the LADDER, both sides) for a set of M1 bar indices; from it the outcome AND the exit minute of any
(target, stop) shape follow exactly. `bt20_run.engine()` replays the robot (one basket at a time, 60 s
cooldown). Costs are bt20's: $1.10 round-trip commission, 0.05 extra spread, 0.10 lots.

Causality
---------
Every feature of bar k uses bars <= k only (VWAP is cumulative from the anchor INCLUDING bar k, which is
closed when we decide). The basket opens at the OPEN of bar k+1. The lookahead twin (fill at bar k's own
open) is built from the same walk table and reported on purpose — BT17 §3 showed it manufactures a large
fake edge.

    .venv/Scripts/python.exe smc_bot/bt/bt21v_lib.py                      # build walk table, zero feed
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/bt21v_lib.py     # build walk table, duka feed
"""
import os
import sys
import time
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, RESULTS, DATASET                            # noqa: E402
from bt import bt15_lib as L                                            # noqa: E402
from bt import bt18_lib as B18                                          # noqa: E402
from bt import bt20_lib as B20                                          # noqa: E402

GOLD = B20.GOLD
U = B20.U                     # 10.0  $ per $1 of gold at 0.10 lots
D = B20.D                     # 0.05  extra spread, price units
COMM = B20.COMM               # 1.10  our measured Exness Zero round trip, $ per 0.10 lot
HORIZON = B20.HORIZON         # 1440 minutes walked forward
LADDER = B20.LADDER
NEVER = B20.NEVER

DS = os.environ.get("SMC_DATASET", "zero")
OUT = os.path.join(RESULTS, "BT21V")
PARTS = os.path.join(OUT, "parts")

# ---- pre-registered constants (fixed before any output was looked at) -------------------------------
MIN_BARS = 15                 # bars that must have elapsed since the anchor before a VWAP is trusted
SIGMA_K = {"V5": 1.0, "V6": 2.0}
PRIMARY = (15, 70)            # the sniper's live shape: $15 target / $70 stop at 0.10 lots
SHIPPED = (7, 70)             # the shipped shape
SESSION_TZ = {"london": ("Europe/London", 8, 0),     # as research/orb.py::SESSIONS, DST-aware
              "ny": ("America/New_York", 9, 30)}
ANCHOR_OF = {"V1": "day", "V2": "day", "V3": "london", "V4": "ny", "V5": "day", "V6": "day"}
KIND_OF = {"V1": "follow", "V2": "fade", "V3": "follow", "V4": "follow", "V5": "band", "V6": "band"}
VARIANTS = ["V1", "V2", "V3", "V4", "V5", "V6"]
DESC = {
    "V1": "day-anchored VWAP (00:00 UTC) · pullback touches VWAP · close back on the trend side · FOLLOW",
    "V2": "V1 FADED (exact mirror of V1 on the same trigger set)",
    "V3": "London-anchored VWAP (08:00 Europe/London, DST-aware) · rule as V1 · FOLLOW",
    "V4": "New-York-anchored VWAP (09:30 America/New_York, DST-aware) · rule as V1 · FOLLOW",
    "V5": "day-anchored VWAP ±1s band · close back INSIDE the band · revert TOWARD VWAP",
    "V6": "day-anchored VWAP ±2s band · close back INSIDE the band · revert TOWARD VWAP",
}
ERAS = {"duka": [("duka 2009-2020", "2009-03-15", "2021-01-01")],
        "zero": [("zero 2021-2023", "2021-01-01", "2024-01-01"),
                 ("zero 2024-2026", "2024-01-01", "2030-01-01")]}


# ================================================================== volume
def raw_volume(b=None):
    """The `tick_volume` column of the feed's own M1 parquet, aligned 1:1 to `bars(GOLD,'M1')`.

    `bt.common.bars()` drops tick_volume, so it is read straight from disk and the alignment is ASSERTED
    on the timestamps and the opens (not assumed).
    """
    if b is None:
        b = bars(GOLD, "M1")
    d = "data_duka" if DATASET == "duka" else "data_zero"
    r = pd.read_parquet(os.path.join(d, f"{GOLD}_M1.parquet"), columns=["time", "open", "tick_volume"])
    tb = b.time.values.astype("datetime64[ns]")
    tr = r.time.values.astype("datetime64[ns]")
    assert len(tr) >= len(tb), (len(tr), len(tb))
    assert (tr[:len(tb)] == tb).all(), "raw parquet rows do not line up with the cached M1 bars"
    assert np.allclose(r.open.values[:len(tb)], b.open.values), "open prices do not line up"
    return r.tick_volume.values[:len(tb)].astype(np.float64)


def volume_quality(b=None, v=None):
    """Per-year quality of the tick-volume column. The question the owner asked first: is this a usable
    proxy at all? A column that is constant carries NO information and makes the volume weighting a
    no-op by arithmetic, not by luck."""
    if b is None:
        b = bars(GOLD, "M1")
    if v is None:
        v = raw_volume(b)
    y = b.time.dt.year.values
    rows = []
    for yy in np.unique(y):
        m = y == yy
        vv = v[m]
        rows.append(dict(feed=DATASET, year=int(yy), bars=int(m.sum()), n_distinct=int(len(np.unique(vv))),
                         mean=round(float(vv.mean()), 1), median=float(np.median(vv)),
                         vmin=float(vv.min()), vmax=float(vv.max()),
                         pct_equal_1=round(100 * float((vv == 1).mean()), 2),
                         pct_zero=round(100 * float((vv == 0).mean()), 2),
                         cv=round(float(vv.std() / max(vv.mean(), 1e-9)), 3),
                         usable=bool(len(np.unique(vv)) > 10)))
    return pd.DataFrame(rows)


# ================================================================== anchors
def _weekday_local_anchors(b, tzname, hh, mm):
    """DST-aware session opens in naive UTC, exactly research/orb.py::session_starts' construction."""
    t0, t1 = b.time.iloc[0].normalize(), b.time.iloc[-1].normalize() + pd.Timedelta(days=1)
    days = pd.date_range(t0, t1, freq="D")
    days = days[days.dayofweek < 5]
    tz = ZoneInfo(tzname)
    loc = [pd.Timestamp(year=x.year, month=x.month, day=x.day, hour=hh, minute=mm, tz=tz) for x in days]
    return pd.DatetimeIndex([t.tz_convert("UTC").tz_localize(None) for t in loc]).values.astype("datetime64[ns]")


def anchors_utc(b, anchor):
    if anchor == "day":
        t0, t1 = b.time.iloc[0].normalize(), b.time.iloc[-1].normalize() + pd.Timedelta(days=1)
        return pd.date_range(t0, t1, freq="D").values.astype("datetime64[ns]")
    tzname, hh, mm = SESSION_TZ[anchor]
    return _weekday_local_anchors(b, tzname, hh, mm)


def anchor_groups(b, anchor, max_hours=24):
    """(grp, cnt, valid) per M1 bar.

    grp  — a contiguous run id; all bars of one anchored window share one id.
    cnt  — how many bars of THIS window have appeared up to and including this bar (1-based).
    valid— the bar sits inside [anchor, anchor + max_hours).

    A window runs from its anchor to the next anchor, capped at `max_hours`. For the day anchor that is
    exactly the UTC calendar day. For London/NY it covers the whole trading day INCLUDING the small hours
    of the next date; the only bars left without an anchor are the Sunday-evening .. Monday-open ones,
    which are reported in the sanity checks rather than hidden.
    """
    a = anchors_utc(b, anchor)
    tb = b.time.values.astype("datetime64[ns]")
    aid = np.searchsorted(a, tb, "right") - 1
    valid = aid >= 0
    dt = np.zeros(len(tb), np.int64)
    dt[valid] = (tb[valid] - a[aid[valid]]).astype("timedelta64[m]").astype(np.int64)
    valid &= dt < max_hours * 60
    key = np.where(valid, aid, -1)
    chg = np.empty(len(key), bool)
    chg[0] = True
    chg[1:] = key[1:] != key[:-1]
    grp = np.cumsum(chg) - 1
    start = np.flatnonzero(chg)[grp]
    cnt = np.arange(len(key), dtype=np.int64) - start + 1
    return grp, cnt, valid


def _group_cumsum(x, grp):
    """Running sum of x within each contiguous group (inclusive of the current element)."""
    cs = np.cumsum(x)
    chg = np.empty(len(grp), bool)
    chg[0] = True
    chg[1:] = grp[1:] != grp[:-1]
    starts = np.flatnonzero(chg)
    base = np.where(starts > 0, cs[starts - 1], 0.0)
    return cs - base[grp]


def vwap_sigma(b, vol, anchor):
    """Anchored VWAP and the running volume-weighted std of the typical price around it.

    vwap[k]  = sum(v*tp, anchor..k) / sum(v, anchor..k)        — bar k INCLUDED, nothing after it
    sigma[k] = sqrt( sum(v*tp^2)/sum(v) - vwap[k]^2 )
    Returns (vwap, sigma, cnt, valid) with NaN where the window is not usable.
    """
    grp, cnt, valid = anchor_groups(b, anchor)
    tp = (b.high.values + b.low.values + b.close.values) / 3.0
    v = np.where(valid, np.maximum(vol, 1e-12), 0.0)
    # Centre the price on each window's own first typical price before accumulating. Without this the
    # global cumsum of v*tp^2 reaches ~1e15, whose float64 spacing (~0.1) swamps the variance of a window
    # only 20 bars old: sigma came out wrong in the 4th decimal. Centring makes the summands O(1).
    chg = np.empty(len(grp), bool)
    chg[0] = True
    chg[1:] = grp[1:] != grp[:-1]
    ref = tp[np.flatnonzero(chg)][grp]
    q = tp - ref
    sv = _group_cumsum(v, grp)
    sqv = _group_cumsum(v * q, grp)
    sq2v = _group_cumsum(v * q * q, grp)
    with np.errstate(invalid="ignore", divide="ignore"):
        mq = sqv / sv
        vw = ref + mq
        var = sq2v / sv - mq * mq
    sig = np.sqrt(np.maximum(var, 0.0))
    bad = ~valid | ~np.isfinite(vw)
    vw = np.where(bad, np.nan, vw)
    sig = np.where(bad, np.nan, sig)
    return vw, sig, cnt, valid


# ================================================================== the six variants
def _base_ok(b, grp, cnt, valid):
    """Preconditions shared by every variant, evaluated at the SIGNAL bar k (a boolean array)."""
    n = len(b)
    tv = b.time.values.astype("datetime64[ns]").astype(np.int64)
    ok = np.zeros(n, bool)
    ok[1:] = (valid[1:] & valid[:-1] & (grp[1:] == grp[:-1])            # same anchored window
              & ((tv[1:] - tv[:-1]) == 60_000_000_000))                 # bar k-1 -> k are consecutive minutes
    ok[:-1] &= (tv[1:] - tv[:-1]) == 60_000_000_000                     # bar k -> k+1 too: the fill must not gap
    ok &= cnt >= MIN_BARS                                               # VWAP has had time to mean something
    ok[0] = False
    ok[n - HORIZON - 3:] = False                                        # entry bar must have a full horizon
    return ok


def signals(vid, b=None, vol=None, weighted=True):
    """(entry bar index k+1, declared side, signal bar index k) for one variant.

    `weighted=False` is the pre-registered CONTROL: the identical rule with every bar's volume set to 1,
    i.e. a simple running mean of the typical price since the anchor. If the two give the same answer the
    tick volume adds nothing.
    """
    if b is None:
        b = bars(GOLD, "M1")
    if vol is None:
        vol = raw_volume(b) if weighted else np.ones(len(b))
    elif not weighted:
        vol = np.ones(len(b))
    anchor = ANCHOR_OF[vid]
    vw, sig, cnt, valid = vwap_sigma(b, vol, anchor)
    grp, _, _ = anchor_groups(b, anchor)
    ok = _base_ok(b, grp, cnt, valid)
    c, hi, lo = b.close.values, b.high.values, b.low.values
    pv, pc = np.r_[np.nan, vw[:-1]], np.r_[np.nan, c[:-1]]              # bar k-1
    kind = KIND_OF[vid]
    if kind in ("follow", "fade"):
        buy = ok & (pc > pv) & (lo <= vw) & (c > vw)
        sell = ok & (pc < pv) & (hi >= vw) & (c < vw)
    else:
        m = SIGMA_K[vid]
        psig = np.r_[np.nan, sig[:-1]]
        band_ok = ok & (sig > 0) & (psig > 0)
        up, dn = vw + m * sig, vw - m * sig
        pup, pdn = pv + m * psig, pv - m * psig
        sell = band_ok & (pc > pup) & (c <= up) & (c > vw)               # outside above -> back inside -> down to VWAP
        buy = band_ok & (pc < pdn) & (c >= dn) & (c < vw)                # outside below -> back inside -> up to VWAP
    both = buy & sell
    buy &= ~both
    sell &= ~both
    k = np.flatnonzero(buy | sell)
    side = np.where(buy[k], 1, -1).astype(np.int8)
    if kind == "fade":
        side = (-side).astype(np.int8)
    e = k + 1
    keep = ~B18.break_mask(b.time.values[e])                            # the broker's daily break hour
    return e[keep], side[keep], k[keep]


def all_signals(b=None):
    """Every variant, volume-weighted and unweighted (control). Keys 'V1'..'V6' and 'V1u'..'V6u'."""
    if b is None:
        b = bars(GOLD, "M1")
    vol = raw_volume(b)
    one = np.ones(len(b))
    out = {}
    for vid in VARIANTS:
        out[vid] = signals(vid, b, vol, True)
        out[vid + "u"] = signals(vid, b, one, False)
    return b, out


# ================================================================== the walk table
def build():
    os.makedirs(PARTS, exist_ok=True)
    f = os.path.join(PARTS, f"walk_{DS}.npz")
    b, sig = all_signals()
    if os.path.exists(f):
        print("  skip", f)
    for nm, (e, d, k) in sig.items():
        print(f"    {nm}: {len(e):,} signals")
    np.savez_compressed(os.path.join(PARTS, f"sig_{DS}.npz"),
                        **{f"{nm}__e": v[0] for nm, v in sig.items()},
                        **{f"{nm}__d": v[1] for nm, v in sig.items()},
                        **{f"{nm}__k": v[2] for nm, v in sig.items()})
    if os.path.exists(f):
        return
    # entry bars (k+1) AND the lookahead-twin bars (k) in ONE walk, so the twin cannot differ by anything
    # except which bar it fills on.
    union = np.unique(np.concatenate([v[0] for v in sig.values()] + [v[2] for v in sig.values()]))
    union = union[(union > 0) & (union < len(b) - HORIZON - 2)]
    print(f"  {DS}: {len(union):,} unique bars to walk")
    t0 = time.time()
    o = B20.walk(b, union)
    np.savez_compressed(f + ".tmp.npz", union=union,
                        t=b.time.values[union].astype("datetime64[ns]").astype(np.int64), **o)
    os.replace(f + ".tmp.npz", f)
    print(f"  walk done in {time.time()-t0:.0f}s")


# ================================================================== money / accuracy from the walk
def both_sides(up, dn, final, pos, T, S):
    """Net $ at 0.10 lots for BOTH sides at each signal: (2, n). Same convention as bt20_run.best_worst
    (a minute that touches target and stop books the stop)."""
    iT, iS = B20.IX[T], B20.IX[S]
    out = np.zeros((2, len(pos)))
    for si in range(2):
        fw, fl = up[si, pos, iT], dn[si, pos, iS]
        out[si] = np.where(fw < fl, T, np.where(fl < NEVER, -S, final[si, pos])) - COMM
    return out


def reading(side, pnl2):
    """One reading: the rule's accuracy and money, and the luck bar measured at the SAME minutes.

    accuracy — share of chances where the declared side was the side that paid more; a tie (both sides
               pay the same, e.g. both hit the target) makes the direction call irrelevant and counts a
               half, so a random direction scores exactly 50 by construction.
    luck bar — `coinflip_same_minutes`: a random direction at exactly these minutes. Computed exactly as
               the average of the two sides, which is the expectation of the coin flip, in all three
               currencies (accuracy, win %, $/basket). The win-% bar is the one that is NOT 50: with a
               $15 target and a $70 stop most baskets close green, so a rule reading 64 % wins may still
               be below what a coin flip does at the same minutes.
    """
    pl, ps = pnl2[0], pnl2[1]
    better = np.where(pl >= ps, 1, -1)
    tie = pl == ps
    acc = float(np.where(tie, 0.5, (side == better)).mean()) * 100
    chosen = np.where(side == 1, pl, ps)
    other = np.where(side == 1, ps, pl)
    return dict(n=len(side), acc=round(acc, 2), ties_pct=round(100 * float(tie.mean()), 2),
                win_pct=round(100 * float((chosen > 0).mean()), 2),
                usd=round(float(chosen.mean()), 4),
                luck_acc=50.0,
                luck_win_pct=round(100 * float(((pl > 0).astype(float) + (ps > 0)).mean() / 2), 2),
                luck_usd=round(float((pl + ps).mean() / 2), 4),
                flipped_usd=round(float(other.mean()), 4),
                se=round(float(chosen.std(ddof=1) / np.sqrt(max(len(chosen), 2))), 4))


def xstar(pnl2):
    """Break-even accuracy for this shape at these minutes: X* = -E[worst]/(E[best]-E[worst])."""
    best, worst = pnl2.max(axis=0), pnl2.min(axis=0)
    eb, ew = best.mean(), worst.mean()
    return float(100 * (-ew) / (eb - ew)) if eb > ew else np.nan


if __name__ == "__main__":
    print(f"feed = {DS}")
    build()
