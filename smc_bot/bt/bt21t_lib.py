"""BT21-T - does raising the signal timeframe from M1 to M5 give more edge, and more trades?

Pre-registered in bt/BT21T_BRIEF.md. SIX variants, FIXED before any code was written. Every one is the
M5 twin of a BT21-E reading, so the difference is attributable to the trigger timeframe and nothing else.

    T1  M15 close > EMA50 (trend up) . M5 low <= EMA9   . M5 close > open  -> BUY  (mirror -> SELL)
    T2  M15 close > EMA50            . M5 low <= EMA21  . M5 close > open  -> BUY
    T3  H1  close > EMA50            . M5 low <= EMA9   . M5 close > open  -> BUY
    T4  H1  close > EMA50            . M5 low <= EMA21  . M5 close > open  -> BUY
    T5  M5 low < min(low, prior 20 M5 bars) - buffer, close back INSIDE the range -> BUY (mirror -> SELL)
    T6  same, 60 M5-bar lookback

The twin map (BT21T_BRIEF 'the 6 variants'):
    brief twin (one rung up on BOTH filter and trigger): T1<->E1, T2<->E2, T5<->E5, T6<->E6.
    trigger-only twin (filter held FIXED, only the trigger moves M1->M5): T1<->E3, T2<->E4, T5<->E5,
        T6<->E6.  T3/T4 are the new H1 rung and have no M1 twin.
Both are reported. The trigger-only pair is the cleaner attribution of the timeframe; the brief's pair is
reported because the brief names it.

EVERYTHING ELSE IS BT21-E's, UNCHANGED, AND IMPORTED RATHER THAN RE-IMPLEMENTED:
  outcome / score / engine / rotation_placebo / week_t / fill_prices come straight out of bt21e_lib, so
  the tie convention (a tie in accuracy counts a HALF - BT21_SUMMARY open issue 4 - and a same-minute
  touch of both barriers books the STOP in money), the luck bar, the 1440-minute horizon, the costs and
  the one-basket-at-a-time replay are byte-identical to the M1 run being differenced against.
  The first-passage kernel is bt20_lib._fp. No new simulator was written.

Implementation choices the brief leaves open, FIXED HERE before any result was seen (each one is the M5
image of the choice bt21e_lib made on M1, and no alternative was run):
  * sweep buffer = 0.05 price units, as on M1.
  * sweep cooldown = 10 M5 bars per side (bt21e used 10 M1 bars; the unit follows the trigger bar).
  * the prior-N window must be N contiguous real M5 bars, and bar i must follow bar i-1 by 5 minutes.
  * warm-up: 300 closed M5 bars for the trigger EMA, 200 closed bars of the trend timeframe.
  * T1-T4 fire on EVERY qualifying M5 bar; the one-basket-at-a-time engine is what thins them.
  * LEVELS carries the 8 dollar rungs BT21-E priced (so bt21e_lib.outcome/engine index it unchanged) plus
    100/150/200, which exist ONLY for the brief's cost-amortization extension and are labelled as such.

CAUSALITY - the bot acts on MINUTES, not on M5 bars:
  * the trend comes from the last M15/H1 bar that has CLOSED at or before the fill bar's open
    (common.asof_index);
  * the trigger comes from the last CLOSED M5 bar k;
  * the fill is the open of the FIRST M1 BAR AFTER THAT M5 BAR CLOSES, i.e. the M1 bar whose open time
    equals the M5 bar's close time. A trigger with no such M1 bar (the M5 bar before a weekend or
    holiday gap) is dropped, exactly as bt21e_lib drops the equivalent M1 case.
  * `--look` refills everything at the open of the M5 bar's OWN FIRST MINUTE - the lookahead twin.
    BT21R found the twin grows with how violent the signal bar is; an M5 bar is more violent than an M1
    bar, so the twin is predicted to be LARGER here than BT21-E's +7.5 points. Measured, not assumed.

Money: 0.10 lots, $1.10 round-trip commission, 0.05 extra spread. Gross = the price move in dollars.

    .venv/Scripts/python.exe smc_bot/bt/bt21t_run.py                          # zero feed (default)
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/bt21t_run.py         # duka feed
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, asof_index, RESULTS                        # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402
from bt import bt20_lib as B20                                         # noqa: E402
from bt import bt21e_lib as E                                          # noqa: E402  (read-only reuse)
from smc_core import ema                                               # noqa: E402

GOLD = "XAUUSD"
TRIG_TF = "M5"                 # the whole point of this run
U = E.U                        # 10.0  $ per $1 of gold at 0.10 lots
D = E.D                        # 0.05  extra spread, price units
COMM = E.COMM                  # 1.10  round trip, $ per 0.10 lot
HORIZON = E.HORIZON            # 1440 minutes walked forward
NEVER = E.NEVER                # int16 1441

# The first 8 entries ARE bt21e_lib.LEVELS in the same order, so bt21e_lib.IX indexes this array
# unchanged and bt21e_lib.outcome / engine can be called on our tables with no edits at all.
BASE_LEVELS = list(E.LEVELS)                                   # [5, 7, 10, 15, 20, 30, 50, 70]
EXT_LEVELS = [100.0, 150.0, 200.0]                             # cost-amortization extension only
LEVELS = np.array(BASE_LEVELS + EXT_LEVELS, float)
IX = {v: i for i, v in enumerate(LEVELS)}
assert all(IX[v] == E.IX[v] for v in E.LEVELS), "level order must match bt21e_lib.LEVELS"

SHAPES = list(E.SHAPES)                                        # the 36-shape ladder, unchanged
EXT_SHAPES = [(T, S) for T in (100, 150, 200) for S in (5, 10, 15, 20, 30, 70)]
PRIMARY = E.PRIMARY                                            # (15, 70) the sniper's live shape
SHIPPED = E.SHIPPED                                            # (7, 70)
CHUNK = 1200

VARIANTS = ["T1_m15_ema9", "T2_m15_ema21", "T3_h1_ema9", "T4_h1_ema21", "T5_sweep20", "T6_sweep60"]
SPEC = {"T1_m15_ema9": ("M15", 9), "T2_m15_ema21": ("M15", 21),
        "T3_h1_ema9": ("H1", 9), "T4_h1_ema21": ("H1", 21),
        "T5_sweep20": (None, 20), "T6_sweep60": (None, 60)}
# the brief's twin, and the twin that holds the trend filter fixed so ONLY the trigger moves
TWIN_BRIEF = {"T1_m15_ema9": "E1_m5_ema9", "T2_m15_ema21": "E2_m5_ema21",
              "T5_sweep20": "E5_sweep20", "T6_sweep60": "E6_sweep60"}
TWIN_TRIGGER = {"T1_m15_ema9": "E3_m15_ema9", "T2_m15_ema21": "E4_m15_ema21",
                "T5_sweep20": "E5_sweep20", "T6_sweep60": "E6_sweep60"}

SWEEP_BUF = E.SWEEP_BUF        # 0.05
SWEEP_COOLDOWN = E.SWEEP_COOLDOWN   # 10 trigger bars per side
WARM_TRIG = 300                # closed M5 bars before the trigger EMA may be used
WARM_TF = E.WARM_TF            # 200 closed bars of the trend timeframe

DS = os.environ.get("SMC_DATASET", "zero")
SPANS = E.SPANS                # the same three eras, never pooled
OUT = os.path.join(RESULTS, "BT21T")
PARTS = os.path.join(OUT, "parts")

TF_STEP = {"M5": np.timedelta64(5, "m")}[TRIG_TF]


# ------------------------------------------------------------------ signals
def signals(nm, b1=None, b5=None, lookahead=False):
    """(fill M1 bar index, side, trigger M5 bar index, wick extreme price) for one variant.

    The honest fill is the M1 bar whose OPEN TIME EQUALS the trigger M5 bar's CLOSE TIME - the first
    minute the live bot could have acted. With lookahead=True the fill moves to the M1 bar that opens at
    the M5 bar's own OPEN time, i.e. the broken twin that buys at a price the signal bar itself produced.
    The signal SET is identical in both cases; only the fill bar moves.
    """
    b1 = bars(GOLD, "M1") if b1 is None else b1
    b5 = bars(GOLD, TRIG_TF) if b5 is None else b5
    tf_trend, p = SPEC[nm]
    n5 = len(b5)
    t5 = b5.time.values.astype("datetime64[ns]")
    tc5 = b5.tclose.values.astype("datetime64[ns]")
    t1 = b1.time.values.astype("datetime64[ns]")
    op, hi, lo, cl = b5.open.values, b5.high.values, b5.low.values, b5.close.values

    # --- where the honest fill lands, for every M5 bar (needed before the trend is read)
    fpos = np.searchsorted(t1, tc5, "left")
    fok = fpos < len(t1)
    fpos_c = np.minimum(fpos, len(t1) - 1)
    fok &= (t1[fpos_c] == tc5)                       # the very next minute must exist in the feed
    # the lookahead twin's fill: the M1 bar that opens when the M5 bar opens
    lpos = np.searchsorted(t1, t5, "left")
    lok = (lpos < len(t1))
    lok &= (t1[np.minimum(lpos, len(t1) - 1)] == t5)
    # BOTH fills must exist for a bar to be a signal at all, so the honest run and the lookahead twin
    # share one signal set and the twin isolates exactly one line (bt21e_run's twin assertion). The
    # bars this drops are the handful whose M5 label starts inside a data gap: see checks_*.json.
    # The horizon/bounds guard is applied to BOTH fills for the same reason: the twin's fill is five
    # minutes earlier, so a trigger near the end of the feed would otherwise survive in the twin and not
    # in the honest run, and the two sets would no longer be the same set.
    nb = len(t1)
    fok = (fok & lok
           & (fpos > 0) & (fpos < nb - HORIZON - 2)
           & (lpos > 0) & (lpos < nb - HORIZON - 2))

    k = np.arange(n5)
    if tf_trend is not None:
        bt = bars(GOLD, tf_trend)
        e50 = ema(bt.close.values, 50)
        tr = np.sign(bt.close.values - e50).astype(np.int8)
        j = asof_index(bt, t1[fpos_c])               # last trend bar CLOSED at or before the fill open
        okj = fok & (j >= WARM_TF)
        trd = np.where(okj, tr[np.maximum(j, 0)], 0)
        em = ema(cl, p)
        buy = okj & (trd == 1) & (lo <= em) & (cl > op) & (k >= WARM_TRIG)
        sell = okj & (trd == -1) & (hi >= em) & (cl < op) & (k >= WARM_TRIG)
        both = buy & sell
        assert not both.any(), f"{nm}: {both.sum()} bars qualified as BOTH buy and sell"
        trig = k[buy | sell]
        side = np.where(buy[buy | sell], 1, -1).astype(np.int8)
        wick = np.full(len(trig), np.nan)
    else:
        N = p
        s_lo = pd.Series(lo).rolling(N).min().shift(1).values     # prior N M5 bars, excluding bar i
        s_hi = pd.Series(hi).rolling(N).max().shift(1).values
        gap = np.zeros(n5, bool)
        gap[N:] = (t5[N:] - t5[:-N]) == N * TF_STEP               # N contiguous real M5 bars
        contig = gap & np.r_[False, (np.diff(t5) == TF_STEP)]
        buy = contig & (lo < s_lo - SWEEP_BUF) & (cl > s_lo)
        sell = contig & (hi > s_hi + SWEEP_BUF) & (cl < s_hi)
        ambig = buy & sell
        buy, sell = buy & ~ambig, sell & ~ambig                   # ambiguous bar skipped (BT17 rule)
        fire = (buy | sell) & fok & (k >= max(WARM_TRIG, N + 2))
        trig = np.where(fire)[0]
        side = np.where(buy[trig], 1, -1).astype(np.int8)
        m = E._cooldown(trig, side, SWEEP_COOLDOWN)               # bt21e's helper, unchanged
        trig, side = trig[m], side[m]
        wick = np.where(side == 1, lo[trig], hi[trig])

    fill = (lpos if lookahead else fpos)[trig]
    ok = fok[trig]
    return (fill[ok].astype(np.int64), side[ok], trig[ok].astype(np.int64), wick[ok])


def all_signals(b1=None, b5=None, lookahead=False):
    b1 = bars(GOLD, "M1") if b1 is None else b1
    b5 = bars(GOLD, TRIG_TF) if b5 is None else b5
    sig = {nm: signals(nm, b1, b5, lookahead) for nm in VARIANTS}
    union = np.unique(np.concatenate([v[0] for v in sig.values()]))
    return sig, union


# ------------------------------------------------------------------ the walk (bt20's kernel)
def walk(b, idx, levels=LEVELS, horizon=HORIZON, verbose=False):
    """First-passage tables at `idx`, both sides, int16 offsets. Same arithmetic as bt20_lib.walk and
    bt21e_lib.walk; only the ladder is longer (the three extension rungs). Kernel = bt20_lib._fp."""
    n = len(idx)
    hi, lo, op, spr = (b.high.values.astype(np.float64), b.low.values.astype(np.float64),
                       b.open.values.astype(np.float64), b.spr.values.astype(np.float64))
    cl = b.close.values.astype(np.float64)
    out = {"up": np.full((2, n, len(levels)), NEVER, np.int16),
           "dn": np.full((2, n, len(levels)), NEVER, np.int16),
           "final": np.zeros((2, n), np.float32)}
    ar = np.arange(horizon)
    for si, side in enumerate((1, -1)):
        for a in range(0, n, CHUNK):
            ix = idx[a:a + CHUNK]
            J = ix[:, None] + ar[None, :]
            if side == 1:                              # long: fills at ask + extra spread, floats on bid
                F = op[ix] + spr[ix] + D
                up_path, dn_path = (hi[J] - F[:, None]) * U, (lo[J] - F[:, None]) * U
                fin = (cl[ix + horizon - 1] - F) * U
            else:                                      # short: fills at bid - extra spread, floats on ask
                F = op[ix] - D
                S_ = spr[J]
                up_path, dn_path = (F[:, None] - (lo[J] + S_)) * U, (F[:, None] - (hi[J] + S_)) * U
                fin = (F - (cl[ix + horizon - 1] + spr[ix + horizon - 1])) * U
            kk = slice(a, a + len(ix))
            out["up"][si, kk] = B20._fp(np.maximum.accumulate(up_path, axis=1), levels)
            out["dn"][si, kk] = B20._fp(-np.minimum.accumulate(dn_path, axis=1), levels)
            out["final"][si, kk] = fin
            if verbose and (a // CHUNK) % 100 == 0:
                print(f"      side {side} {a:,}/{n:,}", flush=True)
    return out


# ------------------------------------------------------------------ BT21-E's arithmetic, imported
outcome = E.outcome                   # gross $ and exit offset for both sides at a shape (8 base rungs)
score = E.score                       # acc (ties = half), own luck bar, coinflip_same_minutes, money
engine = E.engine                     # one basket at a time, 60 s cooldown (= bt20_run.engine)
rotation_placebo = E.rotation_placebo  # week rotation, best-of-6, 200 draws
week_t = E.week_t
fill_prices = E.fill_prices


def outcome_ext(up, dn, final, pos, T, S):
    """outcome() for a shape whose TARGET is one of the extension rungs (100/150/200).

    Identical logic to bt21e_lib.outcome; it exists only because that function's IX dict stops at 70.
    """
    iT, iS = IX[float(T)], IX[float(S)]
    n = len(pos)
    g = np.zeros((2, n))
    off = np.zeros((2, n), np.int64)
    for si in range(2):
        fw = up[si, pos, iT].astype(np.int32)
        fl = dn[si, pos, iS].astype(np.int32)
        win = fw < fl
        res = fl < NEVER
        g[si] = np.where(win, float(T), np.where(res, -float(S), final[si, pos]))
        off[si] = np.where(win, fw, np.where(res, fl, HORIZON))
    return g, off


def engine_ext(ebar, side, pos, up, dn, final, T, S):
    """engine() for an extension-target shape. Same loop as bt21e_lib.engine, different index dict."""
    it, isd, igr, iex, ibar = [], [], [], [], []
    free = np.int64(-1)
    iT, iS = IX[float(T)], IX[float(S)]
    for i in range(len(ebar)):
        e = ebar[i]
        if e < free:
            continue
        j = pos[i]
        si = 0 if side[i] == 1 else 1
        fw, fl = int(up[si, j, iT]), int(dn[si, j, iS])
        if fw < fl:
            off, gross = fw, float(T)
        elif fl < fw:
            off, gross = fl, float(-S)
        elif fl < NEVER:
            off, gross = fl, float(-S)
        else:
            off, gross = HORIZON, float(final[si, j])
        it.append(i)
        isd.append(side[i])
        igr.append(gross)
        iex.append(off)
        ibar.append(e)
        free = e + off + 1
    return (np.array(it, np.int64), np.array(isd, np.int8), np.array(igr, float),
            np.array(iex, np.int64), np.array(ibar, np.int64))


# ------------------------------------------------------------------ the error bar on an accuracy
def acc_vec(g, side):
    """Per-signal accuracy indicator: 1 right, 0 wrong, 0.5 a tie (BT15/BT17/BT21 convention)."""
    net = g - COMM
    chosen = np.where(side == 1, net[0], net[1])
    other = np.where(side == 1, net[1], net[0])
    return np.where(chosen > other, 1.0, np.where(chosen < other, 0.0, 0.5))


def acc_se(a, t_ns):
    """(naive SE, week-clustered SE) of mean(a) in ACCURACY POINTS.

    M1 and M5 signals overlap heavily (consecutive signals share most of a 1440-minute window), so the
    binomial SE is a floor, not the answer. The clustered SE treats a calendar week as one observation -
    the same unit bt21e_lib.week_t uses - and is the number any M1 -> M5 delta must be judged against.
    """
    a = np.asarray(a, float)
    n = len(a)
    if n < 3:
        return (np.nan, np.nan)
    naive = 100.0 * float(a.std(ddof=1)) / np.sqrt(n)
    wk = (np.asarray(t_ns, np.int64) // (7 * 86_400_000_000_000))
    u, inv = np.unique(wk, return_inverse=True)
    K = len(u)
    if K < 3:
        return (naive, np.nan)
    cnt = np.bincount(inv).astype(float)
    sums = np.bincount(inv, weights=a)
    m = a.mean()
    se = np.sqrt(((sums - cnt * m) ** 2).sum() / (K * (K - 1))) / max(1e-12, cnt.mean())
    return (naive, 100.0 * float(se))


# ------------------------------------------------------------------ build
def build(ds=DS, lookahead=False, verbose=True):
    """Write parts/walk[_look]_<ds>.npz and sig[_look]_<ds>.npz. One process per feed."""
    import time
    os.makedirs(PARTS, exist_ok=True)
    tag = "look_" if lookahead else ""
    f = os.path.join(PARTS, f"walk_{tag}{ds}.npz")
    if os.path.exists(f):
        print("  skip", f)
        return
    b1 = bars(GOLD, "M1")
    b5 = bars(GOLD, TRIG_TF)
    sig, union = all_signals(b1, b5, lookahead)
    for nm, (e, d, _k, _w) in sig.items():
        print(f"    {nm}: {len(e):,} signals  ({(d == 1).sum():,} buy / {(d == -1).sum():,} sell)")
    print(f"  {ds}{' LOOKAHEAD' if lookahead else ''}: {len(union):,} unique entry bars to walk",
          flush=True)
    t0 = time.time()
    o = walk(b1, union, verbose=verbose)
    np.savez_compressed(f + ".tmp.npz", union=union,
                        t=b1.time.values[union].astype("datetime64[ns]").astype(np.int64), **o)
    os.replace(f + ".tmp.npz", f)
    np.savez_compressed(os.path.join(PARTS, f"sig_{tag}{ds}.npz"),
                        **{f"{nm}__e": v[0] for nm, v in sig.items()},
                        **{f"{nm}__d": v[1] for nm, v in sig.items()},
                        **{f"{nm}__k": v[2] for nm, v in sig.items()},
                        **{f"{nm}__w": v[3] for nm, v in sig.items()})
    print(f"  done in {time.time() - t0:.0f}s", flush=True)


def build_m1_ext(ds=DS, verbose=True):
    """The EXTENSION ONLY: re-walk BT21-E's own M1 entry bars to get first passage at $100/$150/$200.

    BT21-E's published tables stop at $70, so the brief's `$50-$200 targets` question cannot be answered
    on the M1 side without this. Nothing else about BT21-E is recomputed: the `union` array is loaded
    from its parts file and walked unchanged, so position indices line up exactly and the $5-$70 rungs
    are read from BT21-E's own table.
    """
    import time
    os.makedirs(PARTS, exist_ok=True)
    f = os.path.join(PARTS, f"walk_m1ext_{ds}.npz")
    if os.path.exists(f):
        print("  skip", f)
        return
    z = np.load(os.path.join(E.PARTS, f"walk_{ds}.npz"))
    union = z["union"]
    b1 = bars(GOLD, "M1")
    print(f"  m1ext {ds}: re-walking BT21-E's {len(union):,} M1 entry bars at $100/$150/$200", flush=True)
    t0 = time.time()
    lv = np.array(EXT_LEVELS, float)
    o = walk(b1, union, levels=lv, verbose=verbose)
    np.savez_compressed(f + ".tmp.npz", union=union, **o)
    os.replace(f + ".tmp.npz", f)
    print(f"  done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    if "--m1ext" in sys.argv:
        build_m1_ext(DS)
    else:
        build(DS, lookahead="--look" in sys.argv)
