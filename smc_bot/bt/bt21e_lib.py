"""BT21 Family E - the owner's EMA-pullback (E1-E4) and liquidity-sweep-reversal (E5-E6) specs.

Pre-registered in bt/BT21_BRIEF.md. SIX variants, fixed before any code was written:

    E1  M5  close > EMA50 (trend up)  . M1 low <= EMA9   . M1 close > open   -> BUY  (mirror -> SELL)
    E2  M5  close > EMA50             . M1 low <= EMA21  . M1 close > open   -> BUY
    E3  M15 close > EMA50             . M1 low <= EMA9   . M1 close > open   -> BUY
    E4  M15 close > EMA50             . M1 low <= EMA21  . M1 close > open   -> BUY
    E5  M1 low  < min(low, prior 20 bars) - buffer, close back INSIDE the range -> BUY (mirror -> SELL)
    E6  same, 60-bar lookback

Implementation choices that the brief leaves open, fixed HERE before any result was seen (they are not
tuned and no alternative was run):
  * sweep buffer = 0.05 price units (= half a gold pip = the project's EXTRA_SPREAD), so a sweep must
    clear the prior extreme by more than one spread's worth.
  * sweep cooldown = 10 bars per side (bt17_lib.SWEEP_COOLDOWN, the project default): one level may not
    re-fire every minute.
  * the prior-N-bar window must be N contiguous real minutes (no weekend / holiday gap inside it), the
    same guard bt18_lib uses at line 143.
  * EMA warm-up: the M1 EMA needs 300 closed bars, the trend EMA50 needs 200 closed bars of its own
    timeframe, before a signal may fire.
  * E1-E4 fire on EVERY qualifying minute (no one-per-trend-bar cap). The one-basket-at-a-time engine is
    what thins them; a `one per trend bar` sensitivity is reported beside the headline.

CAUSALITY, the thing that has burned this project before (BT15's "+$2.17 a basket", BT17 section 3):
  * the trend comes from the last trend-timeframe bar that has CLOSED at or before the FILL bar's open
    (`common.asof_index`);
  * the trigger comes from the last CLOSED M1 bar k;
  * the fill is the open of M1 bar k+1.
  `bt21e_run.py --look` reruns everything filling at bar k's own open instead - the "lookahead twin" -
  so the report can print how much fake edge that one line manufactures.

MACHINERY: the first-passage kernel is `bt20_lib._fp` and the one-basket-at-a-time replay is a faithful
copy of `bt20_run.engine` (parameterised for this family's shorter ladder). `bt21e_tests` asserts both
against the originals. No new simulator was written.

Money: 0.10 lots, $1.10 round-trip commission, 0.05 extra spread (the gold spread column is 0 on ~97% of
minutes, ERRORS #14/#34). Gross = the price move in dollars, net = after commission.

    .venv/Scripts/python.exe smc_bot/bt/bt21e_run.py            # SMC_DATASET=zero (default)
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/bt21e_run.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, asof_index, RESULTS                        # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402
from bt import bt20_lib as B20                                         # noqa: E402
from smc_core import ema                                               # noqa: E402

GOLD = "XAUUSD"
U = 10.0                       # $ per $1 of gold at 0.10 lots
D = 0.05                       # extra spread, price units
COMM = 1.10                    # our measured Exness Zero round trip, $ per 0.10 lot
HORIZON = B20.HORIZON          # 1440 minutes walked forward (same as BT20)
NEVER = np.int32(HORIZON + 1)

# the shapes BT20 priced, trimmed to the 8 distinct levels they need (memory: this feed has >1M signals)
LEVELS = np.array([5, 7, 10, 15, 20, 30, 50, 70], float)
IX = {v: i for i, v in enumerate(LEVELS)}
SHAPES = [(T, S) for T in (7, 10, 15, 20, 30, 50) for S in (5, 10, 15, 20, 30, 70)]
PRIMARY = (15, 70)             # the sniper's LIVE shape (.env): $15 target / $70 stop at 0.10 lots
SHIPPED = (7, 70)             # the shape the vendor shipped
CHUNK = 1200
LADDER20, IX20 = B20.LADDER, B20.IX      # only used by the test that re-runs bt20_run.engine on our table

VARIANTS = ["E1_m5_ema9", "E2_m5_ema21", "E3_m15_ema9", "E4_m15_ema21", "E5_sweep20", "E6_sweep60"]
SPEC = {"E1_m5_ema9": ("M5", 9), "E2_m5_ema21": ("M5", 21),
        "E3_m15_ema9": ("M15", 9), "E4_m15_ema21": ("M15", 21),
        "E5_sweep20": (None, 20), "E6_sweep60": (None, 60)}
SWEEP_BUF = 0.05
SWEEP_COOLDOWN = 10
WARM_M1 = 300
WARM_TF = 200

DS = os.environ.get("SMC_DATASET", "zero")
# the three eras the brief fixes. Never pooled into one number.
SPANS = ([("2009-2020", "2009-03-15", "2021-01-01")] if DS == "duka" else
         [("2021-2023", "2021-01-01", "2024-01-01"),
          ("2024-2026", "2024-01-01", "2027-01-01")])
OUT = os.path.join(RESULTS, "BT21E")
PARTS = os.path.join(OUT, "parts")


# ------------------------------------------------------------------ signals
def _cooldown(k, side, bars_):
    """One level may not re-fire every minute: drop a signal within `bars_` of the previous one on the
    same side. Copy of bt17_lib._cooldown (that module's helper is private to it)."""
    keep = np.ones(len(k), bool)
    last = {1: -10 ** 9, -1: -10 ** 9}
    for i in range(len(k)):
        d = int(side[i])
        if k[i] - last[d] < bars_:
            keep[i] = False
        else:
            last[d] = k[i]
    return keep


def signals(nm, b, lookahead=False):
    """(entry bar index, side, trigger bar index, wick extreme price) for one variant.

    entry = trigger + 1 (fill at the NEXT M1 open).  With lookahead=True entry = trigger, which is the
    deliberately broken twin: it fills at the open of the bar whose close produced the signal.
    `wick` is the sweep bar's extreme (NaN for E1-E4), used only by the structural-stop reading.
    """
    tf, p = SPEC[nm]
    n = len(b)
    t1 = b.time.values.astype("datetime64[ns]")
    op, hi, lo, cl = (b.open.values, b.high.values, b.low.values, b.close.values)
    k = np.arange(n - 2)                                  # trigger bars; k+1 must exist
    if tf is not None:
        bt = bars(GOLD, tf)
        e50 = ema(bt.close.values, 50)
        tr = np.sign(bt.close.values - e50).astype(np.int8)
        # the lookahead twin changes ONLY the fill bar, never the signal set, so the comparison isolates
        # the one line that matters
        j = asof_index(bt, t1[k + 1])                     # last tf bar CLOSED at or before the fill open
        okj = j >= WARM_TF
        trd = np.where(okj, tr[np.maximum(j, 0)], 0)
        em = ema(cl, p)
        buy = okj & (trd == 1) & (lo[k] <= em[k]) & (cl[k] > op[k]) & (k >= WARM_M1)
        sell = okj & (trd == -1) & (hi[k] >= em[k]) & (cl[k] < op[k]) & (k >= WARM_M1)
        both = buy & sell                                 # impossible (trend is one sign) - assert it
        assert not both.any(), f"{nm}: {both.sum()} bars qualified as BOTH buy and sell"
        trig = k[buy | sell]
        side = np.where(buy[buy | sell], 1, -1).astype(np.int8)
        wick = np.full(len(trig), np.nan)
    else:
        N = p
        s_lo = pd.Series(lo).rolling(N).min().shift(1).values      # prior N bars, excluding bar i
        s_hi = pd.Series(hi).rolling(N).max().shift(1).values
        # the prior-N window must be N contiguous real minutes, and bar i must follow bar i-1
        gap = np.zeros(n, bool)
        dt = (t1[N:] - t1[:-N]) == np.timedelta64(N, "m")
        gap[N:] = dt
        contig = gap & np.r_[False, (np.diff(t1) == np.timedelta64(1, "m"))]
        buy = contig & (lo < s_lo - SWEEP_BUF) & (cl > s_lo)
        sell = contig & (hi > s_hi + SWEEP_BUF) & (cl < s_hi)
        ambig = buy & sell
        buy = buy & ~ambig
        sell = sell & ~ambig                              # ambiguous bar skipped (BT17 convention)
        fire = np.zeros(n, bool)
        fire[k] = (buy | sell)[k] & (k >= max(WARM_M1, N + 2))
        trig = np.where(fire)[0]
        side = np.where(buy[trig], 1, -1).astype(np.int8)
        m = _cooldown(trig, side, SWEEP_COOLDOWN)
        trig, side = trig[m], side[m]
        wick = np.where(side == 1, lo[trig], hi[trig])
    # The signal set is defined ONCE, by the honest rule, so the lookahead twin differs in nothing but the
    # fill bar. The fill bar must be the very NEXT MINUTE, not merely the next row: ~0.3% of triggers are
    # the last bar before a weekend or holiday gap, where the live bot could not have been filled at that
    # price minutes later and a gap fill is a jump in either direction. Those triggers are not traded.
    tc = b.tclose.values.astype("datetime64[ns]")
    ok = ((trig + 1 > 0) & (trig + 1 < n - HORIZON - 2) & (t1[trig + 1] == tc[trig]))
    trig, side, wick = trig[ok], side[ok], wick[ok]
    entry = trig if lookahead else trig + 1
    return entry.astype(np.int64), side, trig.astype(np.int64), wick


def all_signals(b, lookahead=False):
    sig = {nm: signals(nm, b, lookahead) for nm in VARIANTS}
    union = np.unique(np.concatenate([v[0] for v in sig.values()]))
    return sig, union


# ------------------------------------------------------------------ the walk (bt20's kernel, 8 levels)
def walk(b, idx, levels=LEVELS, horizon=HORIZON, verbose=False):
    """First-passage tables at `idx`, both sides, int16 offsets. Identical arithmetic to bt20_lib.walk -
    only the ladder is shorter and the dtype smaller (this family has >1M signals and 4 GB of free RAM).
    The first-passage kernel itself is bt20_lib._fp, unchanged. bt21e_tests asserts equality."""
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
            if verbose and (a // CHUNK) % 200 == 0:
                print(f"      side {side} {a:,}/{n:,}", flush=True)
    return out


def fill_prices(b, idx):
    """The two fill prices the walk assumes, for the example-trade table."""
    op, spr = b.open.values[idx], b.spr.values[idx]
    return op + spr + D, op - D                                   # long, short


# ------------------------------------------------------------------ outcomes at a shape
def outcome(up, dn, final, pos, T, S):
    """Gross dollars and exit offset for BOTH sides at every signal, at shape (T, S).

    Tie convention is BT20's: when the same minute touches both barriers, book the STOP (the project's
    pessimistic SL-before-TP rule). When neither barrier is reached inside the 1440-minute horizon the
    floating value at the end of the horizon is booked, so nothing silently vanishes.
    """
    iT, iS = IX[T], IX[S]
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


def score(g, side):
    """The decisive numbers for one set of chances, at one shape. Everything NET of commission.

    acc      share of chances where the chosen side was the better of the two (ties count half).
    own_be   this rule's OWN break-even accuracy on its OWN minutes -- the "luck bar". A rule that picks
             volatile minutes faces 64-66 here, not 50 (BT20 section 3).
    luck_usd what a RANDOM direction would have paid at exactly these minutes (coinflip_same_minutes).
    """
    net = g - COMM
    pl, ps = net[0], net[1]
    chosen = np.where(side == 1, pl, ps)
    other = np.where(side == 1, ps, pl)
    best, worst = np.maximum(pl, ps), np.minimum(pl, ps)
    eb, ew = best.mean(), worst.mean()
    acc = float(np.mean(np.where(chosen > other, 1.0, np.where(chosen < other, 0.0, 0.5))))
    return dict(n=len(side), acc=100 * acc, money=float(chosen.mean()),
                own_be=float(100 * (-ew) / (eb - ew)) if eb > ew else np.nan,
                luck_usd=float(0.5 * (eb + ew)), flip_usd=float(other.mean()),
                E_best=float(eb), E_worst=float(ew),
                win_pct=100 * float((chosen > 0).mean()),
                decisive_pct=100 * float(((pl > 0) != (ps > 0)).mean()))


# ------------------------------------------------------------------ the robot's throughput rule
def engine(ebar, side, pos, up, dn, final, T, S):
    """One basket at a time, 60 s (= 1 bar) cooldown. Faithful copy of bt20_run.engine with this
    family's ladder; bt21e_tests asserts it returns the same trades as the original on the same input."""
    it, isd, igr, iex, ibar = [], [], [], [], []
    free = np.int64(-1)
    iT, iS = IX[T], IX[S]
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


# ------------------------------------------------------------------ structural stop (E5/E6 only)
def struct_stop_usd(b, entry, side, wick):
    """The stop distance in dollars at 0.10 lots implied by "stop beyond the wick".

    Expressed in the same dollars the walk uses, so the barrier sits on the wick extreme to within the
    0.05 extra spread (for a short the trigger is the ASK reaching the wick, i.e. the bid one spread
    below it; the gold spread column is 0 on ~97% of minutes so the difference is immaterial).
    A signal whose fill is already past its own wick has stop <= 0 and cannot be traded.
    """
    op, spr = b.open.values.astype(np.float64), b.spr.values.astype(np.float64)
    F = np.where(side == 1, op[entry] + spr[entry] + D, op[entry] - D)
    return np.where(side == 1, (F - wick) * U, (wick - F) * U)


def struct_outcome(b, entry, side, sd, rmult, horizon=HORIZON, chunk=4000, dsp=D):
    """Outcome with a per-signal dollar stop `sd` and a target at `rmult` x it.

    `sd` is passed in rather than derived, so the MIRROR of a reading can be evaluated at exactly the
    same barrier distances as the declared side - otherwise the mirror would get a nonsensical stop on
    the wrong side of the wick and the accuracy comparison would be meaningless.
    Returns (gross $, exit offset).
    """
    hi, lo, op, spr, cl = (b.high.values.astype(np.float64), b.low.values.astype(np.float64),
                           b.open.values.astype(np.float64), b.spr.values.astype(np.float64),
                           b.close.values.astype(np.float64))
    F = np.where(side == 1, op[entry] + spr[entry] + dsp, op[entry] - dsp)
    Sd = np.asarray(sd, float)
    n = len(entry)
    gross = np.full(n, np.nan)
    off = np.zeros(n, np.int64)
    ar = np.arange(horizon)
    for a in range(0, n, chunk):
        s = slice(a, a + chunk)
        ix, d, f, sd = entry[s], side[s], F[s], Sd[s]
        J = ix[:, None] + ar[None, :]
        up_p = np.where(d[:, None] == 1, (hi[J] - f[:, None]), (f[:, None] - (lo[J] + spr[J]))) * U
        dn_p = np.where(d[:, None] == 1, (lo[J] - f[:, None]), (f[:, None] - (hi[J] + spr[J]))) * U
        up_r = np.maximum.accumulate(up_p, axis=1)
        dn_r = np.minimum.accumulate(dn_p, axis=1)
        tgt = rmult * sd
        hw = up_r >= tgt[:, None]
        hl = dn_r <= -sd[:, None]
        jw = np.where(hw.any(1), hw.argmax(1), horizon + 1)
        jl = np.where(hl.any(1), hl.argmax(1), horizon + 1)
        fin = np.where(d == 1, cl[ix + horizon - 1] - f, f - (cl[ix + horizon - 1] + spr[ix + horizon - 1])) * U
        g = np.where(jw < jl, tgt, np.where(jl <= horizon, -sd, fin))
        o = np.where(jw < jl, jw, np.where(jl <= horizon, jl, horizon))
        gross[s] = g
        off[s] = o
    return gross, off


# ------------------------------------------------------------------ the acceptance bar
def rotation_placebo(prep, draws=200, seed=21):
    """BT14's logic in this family's units (copy of bt18_lib.rotation_placebo, best-of-6 instead of 25).

    Circularly rotate each reading's chosen-side vector by a random whole number of weeks: the signal
    rhythm and the clustering of outcomes survive, any real link between side and outcome dies. Take the
    best of the 6 variants in each draw; the P95 over `draws` draws is the bar. Both signs of every
    variant are reported, so the two-sided bar (acc2 / money2) is the headline one.
    """
    rng = np.random.default_rng(seed)
    acc, mon, acc2, mon2 = [], [], [], []
    own = {nm: [] for nm, _, _, _, _ in prep}
    own2 = {nm: [] for nm, _, _, _, _ in prep}
    for _ in range(draws):
        aa, mm, a2, m2 = [], [], [], []
        for nm, sd, pl, ps, per_day in prep:
            w = rng.integers(1, 401)
            sh = max(1, int(per_day * 7 * w)) % max(2, len(sd))
            r = np.roll(sd, sh)
            chosen = np.where(r == 1, pl, ps)
            other = np.where(r == 1, ps, pl)
            a = float(np.mean(np.where(chosen > other, 1.0, np.where(chosen < other, 0.0, 0.5))) * 100)
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
    q = (lambda v, p: float(np.percentile(v, p)))
    return dict(draws=draws, n_readings=len(prep),
                acc_P50=q(acc, 50), acc_P95=q(acc, 95), acc_max=float(np.max(acc)),
                money_P50=q(mon, 50), money_P95=q(mon, 95),
                acc2_P50=q(acc2, 50), acc2_P95=q(acc2, 95), acc2_max=float(np.max(acc2)),
                money2_P50=q(mon2, 50), money2_P95=q(mon2, 95),
                per_reading={nm: dict(n=int(len(prep[i][1])), acc_P95=q(own[nm], 95),
                                      acc2_P95=q(own2[nm], 95))
                             for i, (nm, _, _, _, _) in enumerate(prep)})


week_block_t = L.block_t                                  # day-block t, used on basket-level money


def week_t(x, t_ns):
    """t of mean(x) bootstrapped over calendar WEEKS (M1 signals overlap and cluster heavily)."""
    x = np.asarray(x, float)
    wk = (np.asarray(t_ns, np.int64) // (7 * 86_400_000_000_000))
    u, inv = np.unique(wk, return_inverse=True)
    sums = np.bincount(inv, weights=x)
    cnt = np.bincount(inv)
    m, k = x.mean(), len(u)
    if k < 3:
        return np.nan
    se = np.sqrt(((sums - cnt * m) ** 2).sum() / (k * (k - 1))) / max(1e-12, cnt.mean())
    return float(m / se) if se > 0 else np.nan


# ------------------------------------------------------------------ build
def build(ds=DS, lookahead=False, verbose=True):
    """Write parts/walk[_look]_<ds>.npz and sig[_look]_<ds>.npz. One process per feed (bt.common fixes
    the dataset at import time)."""
    import time
    os.makedirs(PARTS, exist_ok=True)
    tag = "look_" if lookahead else ""
    f = os.path.join(PARTS, f"walk_{tag}{ds}.npz")
    if os.path.exists(f):
        print("  skip", f)
        return
    b = bars(GOLD, "M1")
    sig, union = all_signals(b, lookahead)
    for nm, (e, d, _k, _w) in sig.items():
        print(f"    {nm}: {len(e):,} signals  ({(d == 1).sum():,} buy / {(d == -1).sum():,} sell)")
    print(f"  {ds}{' LOOKAHEAD' if lookahead else ''}: {len(union):,} unique entry bars to walk", flush=True)
    t0 = time.time()
    o = walk(b, union, verbose=verbose)
    np.savez_compressed(f + ".tmp.npz", union=union,
                        t=b.time.values[union].astype("datetime64[ns]").astype(np.int64), **o)
    os.replace(f + ".tmp.npz", f)
    np.savez_compressed(os.path.join(PARTS, f"sig_{tag}{ds}.npz"),
                        **{f"{nm}__e": v[0] for nm, v in sig.items()},
                        **{f"{nm}__d": v[1] for nm, v in sig.items()},
                        **{f"{nm}__k": v[2] for nm, v in sig.items()},
                        **{f"{nm}__w": v[3] for nm, v in sig.items()})
    print(f"  done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    build(DS, lookahead="--look" in sys.argv)
