"""BT17 shared pieces — SMC signals generated on M1 and M5.

Pre-registered in bt/BT17_BRIEF.md (16 variants, fixed before any run).

Everything that touches prices, fills, exits or costs goes through `bt.common.simulate` via
`bt15_lib.basket_orders` / `bt15_lib.fix_fillbar`. Nothing here re-implements a simulator.

The money shape is the user's own: $15 profit cap / $20 loss cap at 0.10 lots = 1.5 / 2.0 gold price units,
NO commission (his broker charges none) and his measured 0.072 price spread.

The outcome table: `common.simulate` is run for BOTH sides at EVERY M1 minute of a feed, once, and cached.
Every variant is then only an index selection into that one validated table, so no variant can get a
different simulator, cost model or fill rule from any other.

Fill rule: a signal is decided on the CLOSE of signal bar k of its own timeframe; `t_signal` is that close
time, so simulate fills at the open of the next M1 bar. No feature may come from a bar closing after that
open. bt17_tests.py asserts it.

    set SMC_DATASET=duka  -> 2009-03-15 .. 2020-12-31
    (default, zero)       -> 2021-01-01 .. 2025-06-27   (TEST 2025-07+ is LOCKED and never read)
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, simulate, COMM, RESULTS, asof_index       # noqa: E402
from smc_core import structure, fvgs, swings                          # noqa: E402
from bt import bt15_lib as L                                          # noqa: E402

GOLD = "XAUUSD"
USD_PER_PRICE = 10.0        # $ per $1 of gold move at 0.10 lots (10 oz)
T_CAP = 1.5                 # profit cap, price units  = $15 at 0.10 lots
S_CAP = 2.0                 # loss cap,   price units  = $20 at 0.10 lots
HIS_SPREAD = 0.072          # his measured price spread
BREAKEVEN = 52.9            # BT15 his_costs_grid.csv, row target 15 / stop 20
LAST_OPP = L.LAST_OPP       # 2025-06-27 00:00 -- TEST is locked

DS = os.environ.get("SMC_DATASET", "zero")
SPAN = ("2009-03-15", "2021-01-01") if DS == "duka" else ("2021-01-01", LAST_OPP)
OUT = os.path.join(RESULTS, "BT17")
PARTS = os.path.join(OUT, "parts")
os.makedirs(PARTS, exist_ok=True)

# the three date periods the brief fixes (not overlapping, not selected after the fact)
PERIODS3 = [("2009-2020", "2009-03-15", "2021-01-01"),
            ("2021-2023", "2021-01-01", "2024-01-01"),
            ("2024-mid2025", "2024-01-01", LAST_OPP)]
SESSIONS = L.SESSIONS       # Asian 0-7, London 7-12, Overlap 12-15, NewYork 15-20, Late 20-24

STRUCT_N = 5                # smc_core structure/swing confirmation, project default
MAX_AGE = 50                # bars of the signal's own timeframe a zone may wait for its first touch
SWEEP_COOLDOWN = 10         # bars; one level may not fire every bar


# ---------------------------------------------------------------- the outcome table
def outcome_table(force=False, chunk=150_000, verbose=True):
    """pnl[2, N] in dollars at 0.10 lots and texit[2, N] (int64 ns) for a basket opened at the OPEN of
    every M1 bar of this feed. Row 0 = long, row 1 = short. Checkpointed per chunk so the laptop can sleep.
    """
    COMM[GOLD] = 0.0                                   # his broker charges none (brief)
    b = bars(GOLD, "M1")
    n = len(b)
    tag = f"{DS}_T{T_CAP}_S{S_CAP}"
    # HARD LOCK: never simulate (and therefore never be able to report) an entry outside this feed's span.
    # TEST = 2025-07-01 onward. The zero feed on disk runs to 2026-09, so the cutoff is explicit, not implied.
    t_all = b.time.values.astype("datetime64[ns]")
    lo = int(np.searchsorted(t_all, np.datetime64(pd.Timestamp(SPAN[0])), "left"))
    hi = int(np.searchsorted(t_all, np.datetime64(pd.Timestamp(SPAN[1])), "left"))
    starts = list(range(0, n, chunk))
    for a in starts:                                   # one small checkpoint per chunk: the laptop sleeps
        cf = os.path.join(PARTS, f"outc_{tag}_{a:09d}.npz")
        if os.path.exists(cf) and not force:
            continue
        sub = np.arange(a, min(a + chunk, n), dtype=np.int64)
        sub = sub[(sub < n - 3) & (sub >= lo) & (sub < hi)]      # never simulate outside the span
        m = len(sub)
        if m == 0:
            np.savez(cf, pnl=np.zeros((2, 0), np.float32), texit=np.zeros((2, 0), np.int64), a0=a, m=0)
            continue
        a0 = int(sub[0])
        p = np.full((2, m), np.nan, np.float32)
        x = np.zeros((2, m), np.int64)
        for si, side in enumerate((1, -1)):
            o = L.basket_orders(b, sub, side, T_CAP, S_CAP, delta=HIS_SPREAD)
            r = simulate(GOLD, o, one_at_a_time=False)
            fl = r[r.status == "filled"]
            k = fl.opp.values.astype(np.int64)
            p[si, k] = (fl.usd_01.values - HIS_SPREAD * USD_PER_PRICE).astype(np.float32)
            x[si, k] = fl.t_exit.values.astype("datetime64[ns]").astype(np.int64)
        tp, sl = L.fillbar_masks(b, sub, T_CAP, S_CAP, delta=HIS_SPREAD)
        pay = T_CAP * USD_PER_PRICE - COMM[GOLD]       # simulate never books TP on the fill bar; it does book SL
        p = np.where(tp & ~sl & ~np.isnan(p), pay, p).astype(np.float32)
        np.savez(cf, pnl=p, texit=x, a0=a0, m=m)
        if verbose:
            print(f"  outcomes {min(a + chunk, n):,}/{n:,}", flush=True)
    pnl = np.full((2, n), np.nan, dtype=np.float32)
    texit = np.zeros((2, n), dtype=np.int64)
    for a in starts:
        z = np.load(os.path.join(PARTS, f"outc_{tag}_{a:09d}.npz"))
        m = int(z["m"])
        if m:
            a0 = int(z["a0"])
            pnl[:, a0:a0 + m] = z["pnl"]
            texit[:, a0:a0 + m] = z["texit"]
    pnl[:, ~span_mask(t_all, *SPAN)] = np.nan          # hard lock, enforced on the assembled table too
    return pnl, texit


# ---------------------------------------------------------------- the detectors (all from smc_core)
_ST = {}


def st_of(b, N):
    """smc_core.structure, memoised: it is a per-bar Python loop and several detectors want the same one."""
    key = (len(b), N, int(b.time.values[0].astype("datetime64[ns]").astype(np.int64)))
    if key not in _ST:
        _ST[key] = structure(b, N)
    return _ST[key]


def _entry_idx(m1_t, tclose):
    """Index of the first M1 bar opening at or after a signal bar's close = the k+1 fill bar."""
    return np.searchsorted(m1_t, np.asarray(tclose, dtype="datetime64[ns]"), "left")


def _cooldown(k, side, gap):
    """Keep only signals at least `gap` bars after the previous signal of the SAME side."""
    keep = np.ones(len(k), bool)
    last = {1: -10 ** 9, -1: -10 ** 9}
    for i in range(len(k)):
        d = int(side[i])
        if k[i] - last[d] < gap:
            keep[i] = False
        else:
            last[d] = k[i]
    return keep


def sweeps(b, N=STRUCT_N, cooldown=SWEEP_COOLDOWN):
    """Liquidity sweep of the last CONFIRMED swing: wick through, close back inside.
    Returns (signal bar index, FADE side). Causal: `lsh`/`lsl` at bar k are swings confirmed at or before k.
    """
    st = st_of(b, N)
    h, l, c = b.high.values, b.low.values, b.close.values
    hi = (h > st["lsh"]) & (c <= st["lsh"]) & np.isfinite(st["lsh"])
    lo = (l < st["lsl"]) & (c >= st["lsl"]) & np.isfinite(st["lsl"])
    both = hi & lo
    hi &= ~both
    lo &= ~both                                        # ambiguous bar: skipped (brief)
    k = np.where(hi | lo)[0]
    side = np.where(hi[k], -1, 1).astype(np.int8)      # declared direction = fade the sweep
    m = _cooldown(k, side, cooldown)
    return k[m], side[m]


def _first_touch_bars(lo, hi, k0, h, l, max_age):
    """First bar j in (k0, k0+max_age] whose range trades into [lo, hi]. -1 if none. Pure bookkeeping."""
    n = len(h)
    out = np.full(len(k0), -1, np.int64)
    for i in range(len(k0)):
        a, e = k0[i] + 1, min(k0[i] + 1 + max_age, n)
        if e <= a or not (np.isfinite(lo[i]) and np.isfinite(hi[i])):
            continue
        hit = np.where((l[a:e] <= hi[i]) & (h[a:e] >= lo[i]))[0]
        if len(hit):
            out[i] = a + hit[0]
    return out


def order_blocks(b, N=STRUCT_N, max_age=MAX_AGE):
    """First retest of the order block of each structure break. Returns (retest bar index, CONTINUATION side)."""
    st = st_of(b, N)
    h, l = b.high.values, b.low.values
    kb = np.where(st["evt"] != 0)[0]
    d = np.sign(st["evt"][kb]).astype(np.int8)
    j = _first_touch_bars(st["ob_lo"][kb], st["ob_hi"][kb], kb, h, l, max_age)
    m = j >= 0
    return j[m], d[m]


def fvg_fills(b, min_atr=0.1, max_age=MAX_AGE):
    """First fill of each fair value gap. Returns (fill bar index, CONTINUATION side = gap direction)."""
    bull, bear = fvgs(b, min_atr)
    h, l = b.high.values, b.low.values
    kb = np.where(bull)[0]
    ks = np.where(bear)[0]
    k0 = np.concatenate([kb, ks])
    lo = np.concatenate([h[kb - 2], h[ks]])
    hi = np.concatenate([l[kb], l[ks - 2]])
    d = np.concatenate([np.ones(len(kb), np.int8), -np.ones(len(ks), np.int8)])
    o = np.argsort(k0, kind="stable")
    k0, lo, hi, d = k0[o], lo[o], hi[o], d[o]
    j = _first_touch_bars(lo, hi, k0, h, l, max_age)
    m = j >= 0
    return j[m], d[m]


def breaks(b, N=STRUCT_N, kind="BOS"):
    """BOS (|evt| == 1) or CHoCH (|evt| == 2). Returns (break bar index, CONTINUATION side)."""
    st = st_of(b, N)
    want = 1 if kind == "BOS" else 2
    k = np.where(np.abs(st["evt"]) == want)[0]
    return k, np.sign(st["evt"][k]).astype(np.int8)


def sweep_then_choch(b, N=STRUCT_N, within=10, cooldown=SWEEP_COOLDOWN):
    """A sweep, then a CHoCH in the fade direction within `within` bars. Entry at the CHoCH bar's close."""
    ks, ds = sweeps(b, N, cooldown)
    kc, dc = breaks(b, N, "CHoCH")
    outk, outd = [], []
    for d in (1, -1):                                  # per direction: searchsorted, not an O(n*m) scan
        kd = kc[dc == d]
        sk = ks[ds == d]
        if len(kd) == 0 or len(sk) == 0:
            continue
        j = np.searchsorted(kd, sk, "right")           # first CHoCH strictly after each sweep
        ok = (j < len(kd)) & (kd[np.minimum(j, len(kd) - 1)] <= sk + within)
        outk.append(kd[j[ok]])
        outd.append(np.full(int(ok.sum()), d, np.int8))
    if not outk:
        return np.array([], np.int64), np.array([], np.int8)
    a = np.concatenate(outk)
    dd = np.concatenate(outd)
    o = np.argsort(a, kind="stable")
    a, dd = a[o], dd[o]
    u = np.concatenate([[True], np.diff(a) > 0])       # one entry per CHoCH bar even if two sweeps point to it
    return a[u], dd[u]


# ---------------------------------------------------------------- building the 16 variants
VARIANTS = [
    ("S1_sweep_fade_M1", "S sweeps", "M1", "fade"),
    ("S2_sweep_fade_M5", "S sweeps", "M5", "fade"),
    ("S3_sweep20_fade_M1", "S sweeps", "M1", "fade"),
    ("S4_sweep20_fade_M5", "S sweeps", "M5", "fade"),
    ("O1_ob_retest_M1", "O order blocks", "M1", "follow"),
    ("O2_ob_retest_M5", "O order blocks", "M5", "follow"),
    ("F1_fvg_fill_M1", "F fair value gaps", "M1", "follow"),
    ("F2_fvg_fill_M5", "F fair value gaps", "M5", "follow"),
    ("B1_bos_M1", "B structure", "M1", "follow"),
    ("B2_choch_M1", "B structure", "M1", "follow"),
    ("B3_bos_M5", "B structure", "M5", "follow"),
    ("B4_choch_M5", "B structure", "M5", "follow"),
    ("C1_sweep_choch_M1", "C combinations", "M1", "fade"),
    ("C2_sweep_choch_M5", "C combinations", "M5", "fade"),
    ("C3_fvg_m5trend_M1", "C combinations", "M1", "follow"),
    ("C4_ob_m5trend_M1", "C combinations", "M1", "follow"),
]
VAR_NAMES = [v[0] for v in VARIANTS]


def signals(name, cache={}):
    """(entry M1 bar index, declared side, signal bar index on its own timeframe, timeframe).

    `entry` is the FILL bar: the first M1 bar opening at or after the signal bar's close (the k+1 rule).
    """
    m1 = bars(GOLD, "M1")
    t1 = m1.time.values.astype("datetime64[ns]")

    def tf_bars(tf):
        if tf not in cache:
            cache[tf] = bars(GOLD, tf)
        return cache[tf]

    def pack(b, k, d, tf):
        return _entry_idx(t1, b.tclose.values[k]), d, k, tf

    if name == "S1_sweep_fade_M1":
        b = tf_bars("M1"); k, d = sweeps(b, 5); return pack(b, k, d, "M1")
    if name == "S2_sweep_fade_M5":
        b = tf_bars("M5"); k, d = sweeps(b, 5); return pack(b, k, d, "M5")
    if name == "S3_sweep20_fade_M1":
        b = tf_bars("M1"); k, d = sweeps(b, 20); return pack(b, k, d, "M1")
    if name == "S4_sweep20_fade_M5":
        b = tf_bars("M5"); k, d = sweeps(b, 20); return pack(b, k, d, "M5")
    if name == "O1_ob_retest_M1":
        b = tf_bars("M1"); k, d = order_blocks(b); return pack(b, k, d, "M1")
    if name == "O2_ob_retest_M5":
        b = tf_bars("M5"); k, d = order_blocks(b); return pack(b, k, d, "M5")
    if name == "F1_fvg_fill_M1":
        b = tf_bars("M1"); k, d = fvg_fills(b); return pack(b, k, d, "M1")
    if name == "F2_fvg_fill_M5":
        b = tf_bars("M5"); k, d = fvg_fills(b); return pack(b, k, d, "M5")
    if name == "B1_bos_M1":
        b = tf_bars("M1"); k, d = breaks(b, 5, "BOS"); return pack(b, k, d, "M1")
    if name == "B2_choch_M1":
        b = tf_bars("M1"); k, d = breaks(b, 5, "CHoCH"); return pack(b, k, d, "M1")
    if name == "B3_bos_M5":
        b = tf_bars("M5"); k, d = breaks(b, 5, "BOS"); return pack(b, k, d, "M5")
    if name == "B4_choch_M5":
        b = tf_bars("M5"); k, d = breaks(b, 5, "CHoCH"); return pack(b, k, d, "M5")
    if name == "C1_sweep_choch_M1":
        b = tf_bars("M1"); k, d = sweep_then_choch(b, 5, 10); return pack(b, k, d, "M1")
    if name == "C2_sweep_choch_M5":
        b = tf_bars("M5"); k, d = sweep_then_choch(b, 5, 10); return pack(b, k, d, "M5")
    if name in ("C3_fvg_m5trend_M1", "C4_ob_m5trend_M1"):
        b = tf_bars("M1")
        k, d = fvg_fills(b) if name.startswith("C3") else order_blocks(b)
        e = _entry_idx(t1, b.tclose.values[k])
        b5 = tf_bars("M5")
        tr5 = st_of(b5, 5)["trend"]
        e_ok = np.minimum(e, len(m1) - 1)
        j5 = asof_index(b5, t1[e_ok])                  # last M5 bar CLOSED at or before the fill bar's open
        agree = (j5 >= 0) & (tr5[np.maximum(j5, 0)] == d)
        return e[agree], d[agree], k[agree], "M1"
    raise KeyError(name)


# ---------------------------------------------------------------- scoring
def span_mask(t, a, b):
    return (t >= np.datetime64(pd.Timestamp(a))) & (t < np.datetime64(pd.Timestamp(b)))


def score(pnl, entry, side):
    """The two numbers that matter, for one set of chances.

    accuracy = share of chances where the chosen side was the BETTER of the two (ties count half).
      This is exactly the skill variable X behind BT15's break-even, so it compares with 52.9.
    money    = plain mean of the chosen side's dollars at 0.10 lots.
    own_be   = this variant's own break-even accuracy on its own chances.
    """
    if len(entry) == 0:
        return dict(n=0)
    pl, ps = pnl[0, entry].astype(np.float64), pnl[1, entry].astype(np.float64)
    ok = np.isfinite(pl) & np.isfinite(ps)
    pl, ps, sd = pl[ok], ps[ok], side[ok]
    if len(pl) == 0:
        return dict(n=0)
    chosen = np.where(sd == 1, pl, ps)
    other = np.where(sd == 1, ps, pl)
    best, worst = np.maximum(pl, ps), np.minimum(pl, ps)
    acc = float(np.mean(np.where(chosen > other, 1.0, np.where(chosen < other, 0.0, 0.5))))
    eb, ew = best.mean(), worst.mean()
    return dict(n=int(len(pl)), acc=100 * acc, money=float(chosen.mean()),
                own_be=float(100 * (-ew) / (eb - ew)) if eb > ew else np.nan,
                E_best=float(eb), E_worst=float(ew), win_pct=100 * float((chosen > 0).mean()),
                decisive_pct=100 * float(((pl > 0) != (ps > 0)).mean()), mask=ok)


def money_ladder(pnl, entry, accs=(50.0, 52.9, 54.0, 55.0, 56.0, 58.0)):
    """What the shape pays per basket, at 0.10 lots, as accuracy rises. Linear in accuracy by construction."""
    pl, ps = pnl[0, entry].astype(np.float64), pnl[1, entry].astype(np.float64)
    ok = np.isfinite(pl) & np.isfinite(ps)
    best, worst = np.maximum(pl[ok], ps[ok]), np.minimum(pl[ok], ps[ok])
    eb, ew = best.mean(), worst.mean()
    return {a: float((a / 100) * eb + (1 - a / 100) * ew) for a in accs}


def week_block_t(x, t_ns):
    """t of mean(x) with a bootstrap over calendar WEEKS: M1 signals overlap heavily and cluster."""
    x = np.asarray(x, float)
    wk = (t_ns // (7 * 86_400_000_000_000)).astype(np.int64)
    u, inv = np.unique(wk, return_inverse=True)
    sums = np.bincount(inv, weights=x)
    cnt = np.bincount(inv)
    m, k = x.mean(), len(u)
    if k < 3:
        return np.nan
    se = np.sqrt(((sums - cnt * m) ** 2).sum() / (k * (k - 1))) / max(1e-12, cnt.mean())
    return float(m / se) if se > 0 else np.nan


def rotation_placebo(pnl, sigs, t_ns_of, draws=200, seed=17):
    """The acceptance bar, BT14's logic in BT17's units.

    For each of the 16 readings, circularly rotate the chosen-side vector by a random whole number of weeks.
    Signal persistence and outcome clustering survive; any real link is destroyed. Take the best of the 16 in
    each draw; the P95 over `draws` draws is the bar.
    """
    rng = np.random.default_rng(seed)
    best_acc, best_money = [], []
    prep = []
    for name, (entry, side, _k, _tf) in sigs.items():
        s = score(pnl, entry, side)
        if s.get("n", 0) < 200:
            continue
        e = entry[s["mask"]]
        pl, ps = pnl[0, e].astype(np.float64), pnl[1, e].astype(np.float64)
        t = t_ns_of(e)
        days = max(1.0, (t.max() - t.min()) / 86_400_000_000_000)
        prep.append((side[s["mask"]], pl, ps, len(e) / days))
    best_acc2, best_money2 = [], []
    for _ in range(draws):
        aa, mm, aa2, mm2 = [], [], [], []
        for sd, pl, ps, per_day in prep:
            w = rng.integers(1, 401)                   # whole weeks of displacement
            sh = max(1, int(per_day * 7 * w)) % len(sd)
            r = np.roll(sd, sh)
            chosen = np.where(r == 1, pl, ps)
            other = np.where(r == 1, ps, pl)
            a = np.mean(np.where(chosen > other, 1.0, np.where(chosen < other, 0.0, 0.5))) * 100
            aa.append(a)
            mm.append(chosen.mean())
            aa2.append(max(a, 100 - a))                # BOTH signs are reported, so the search is two-sided
            mm2.append(max(chosen.mean(), other.mean()))
        best_acc.append(max(aa))
        best_money.append(max(mm))
        best_acc2.append(max(aa2))
        best_money2.append(max(mm2))
    return dict(draws=draws, n_readings=len(prep),
                acc_P50=float(np.percentile(best_acc, 50)),
                acc_P95=float(np.percentile(best_acc, 95)), acc_max=float(np.max(best_acc)),
                money_P50=float(np.percentile(best_money, 50)),
                money_P95=float(np.percentile(best_money, 95)), money_max=float(np.max(best_money)),
                acc2_P50=float(np.percentile(best_acc2, 50)),
                acc2_P95=float(np.percentile(best_acc2, 95)), acc2_max=float(np.max(best_acc2)),
                money2_P50=float(np.percentile(best_money2, 50)),
                money2_P95=float(np.percentile(best_money2, 95)), money2_max=float(np.max(best_money2)))
