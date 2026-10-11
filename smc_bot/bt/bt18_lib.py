"""BT18 shared pieces — the three literature entry ideas. Pre-registered in bt/BT18_BRIEF.md (25 variants).

Everything that touches prices, fills, exits or costs goes through `bt.common.simulate`. BT18 re-implements
NOTHING: it loads the both-sides outcome table BT17 already built at exactly this money shape
($15 profit cap / $20 loss cap at 0.10 lots, no commission, 0.072 spread) read-only from
`results/BT17/parts/`, and every variant is only an index selection into that one validated table.

Fill rule: a signal is decided on the CLOSE of bar k; the basket opens at the OPEN of bar k+1, i.e. the
outcome table is read at column k+1. Rolling thresholds use windows ending at bar k-1. bt18_tests.py asserts it.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt18_run                       # zero (2021..2025-06)
    cd smc_bot && SMC_DATASET=duka ../.venv/Scripts/python.exe -m bt.bt18_run      # duka (2009..2020)
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, RESULTS                                   # noqa: E402
from bt import bt15_lib as L                                          # noqa: E402
from bt import bt17_lib as B17                                        # noqa: E402

GOLD = B17.GOLD
T_CAP, S_CAP, HIS_SPREAD = B17.T_CAP, B17.S_CAP, B17.HIS_SPREAD       # 1.5 / 2.0 / 0.072, from BT17
USD_PER_PRICE = 10.0
BREAKEVEN = 52.85                 # BT15 his_costs_grid.csv, row target 15 / stop 20
DS = B17.DS
SPAN = B17.SPAN
PERIODS3 = B17.PERIODS3
SESSIONS = L.SESSIONS
OUT = os.path.join(RESULTS, "BT18")
PARTS = os.path.join(OUT, "parts")
os.makedirs(PARTS, exist_ok=True)

MIN_SAMPLE = 1000                 # brief: a candidate needs a usable sample (BT16 died on 195 chances)

# reused, unchanged
span_mask = B17.span_mask
score = B17.score
money_ladder = B17.money_ladder
week_block_t = B17.week_block_t


def outcome_table():
    """The BT17 table, read-only. pnl[2, N] dollars at 0.10 lots, row 0 = long, row 1 = short."""
    return B17.outcome_table(verbose=False)


# ---------------------------------------------------------------- the broker's daily break
def _last_sunday(y, m):
    d = pd.date_range(f"{y}-{m:02d}-01", periods=pd.Period(f"{y}-{m:02d}").days_in_month, freq="D")
    return d[d.dayofweek == 6][-1]


def eu_dst(t):
    """True where European summer time is in force (last Sunday of March -> last Sunday of October)."""
    t = pd.DatetimeIndex(t)
    out = np.zeros(len(t), bool)
    for y in np.unique(t.year):
        a = _last_sunday(y, 3) + pd.Timedelta(hours=1)
        z = _last_sunday(y, 10) + pd.Timedelta(hours=1)
        out |= np.asarray((t >= a) & (t < z))
    return out


def us_dst(t):
    """True where US summer time is in force (2nd Sunday of March -> 1st Sunday of November)."""
    t = pd.DatetimeIndex(t)
    out = np.zeros(len(t), bool)
    for y in np.unique(t.year):
        mar = pd.date_range(f"{y}-03-01", f"{y}-03-31", freq="D")
        nov = pd.date_range(f"{y}-11-01", f"{y}-11-30", freq="D")
        a = mar[mar.dayofweek == 6][1] + pd.Timedelta(hours=2)
        z = nov[nov.dayofweek == 6][0] + pd.Timedelta(hours=2)
        out |= np.asarray((t >= a) & (t < z))
    return out


def break_mask(t):
    """The broker's daily break: 21:00-22:00 UTC in summer, 22:00-23:00 UTC in winter (EET/EEST server time).
    Median spread there is 0.304 against 0.040 the rest of the day, and the Zero account does not quote it."""
    t = pd.DatetimeIndex(t)
    h = t.hour.values
    s = eu_dst(t)
    return np.where(s, h == 21, h == 22)


def wide_break_mask(t):
    """The conservative version: drop hours 21 AND 22 UTC whatever the season."""
    h = pd.DatetimeIndex(t).hour.values
    return (h == 21) | (h == 22)


# ---------------------------------------------------------------- Idea 1: fade the extreme one-minute move
A_PCTS = [90.0, 95.0, 98.0, 99.0, 99.5, 99.9, 99.95]
A_WINDOWS = [1440, 7200]


def _thresh_cache(b, W):
    """Rolling percentiles of |one-minute move| over the W bars ENDING AT BAR k-1 (strictly causal).

    One cached file per (feed, W): the rolling quantile is the only slow part of BT18.
    """
    f = os.path.join(PARTS, f"thr_{DS}_W{W}.npz")
    if os.path.exists(f):
        z = np.load(f)
        return z["thr"], z["m"]
    m = (b.close.values - b.open.values).astype(np.float64)
    a = pd.Series(np.abs(m))
    thr = np.full((len(A_PCTS), len(m)), np.nan, np.float32)
    for i, q in enumerate(A_PCTS):
        thr[i] = a.rolling(W, min_periods=W).quantile(q / 100.0).shift(1).values.astype(np.float32)
        print(f"    rolling {q} pct over {W} bars done", flush=True)
    np.savez_compressed(f, thr=thr, m=m.astype(np.float32))
    return thr, m.astype(np.float32)


def spike_signals(b, pct, W):
    """(entry bar index, FADE side, signal bar index). |move of bar k| above the trailing pct -> take the
    opposite side at the open of bar k+1."""
    thr, m = _thresh_cache(b, W)
    i = A_PCTS.index(pct)
    t = thr[i].astype(np.float64)
    mm = m.astype(np.float64)
    k = np.where(np.isfinite(t) & (np.abs(mm) > t) & (mm != 0))[0]
    side = (-np.sign(mm[k])).astype(np.int8)        # fade: up-spike -> sell, down-drop -> buy
    return k + 1, side, k


# ---------------------------------------------------------------- Idea 2: intraday momentum (FOLLOW)
B_BLOCKS = [5, 15, 30, 60]


def block_momentum(b, Lmin):
    """Non-overlapping Lmin-minute blocks anchored to the clock. At an entry bar i whose open sits on the grid,
    the signal is sign(close[i-1] - close[i-1-Lmin]); close[i-1] is the price at the entry bar's own open."""
    t = b.time
    mins = (t.dt.hour * 60 + t.dt.minute).values
    c = b.close.values
    i = np.where(mins % Lmin == 0)[0]
    i = i[i > Lmin]
    # require the block to be real minutes, not a gap across a weekend: bar i-1-L must be exactly L minutes
    # before bar i-1 on the clock
    tv = t.values.astype("datetime64[ns]").astype(np.int64)
    good = (tv[i - 1] - tv[i - 1 - Lmin]) == Lmin * 60_000_000_000
    i = i[good]
    d = np.sign(c[i - 1] - c[i - 1 - Lmin]).astype(np.int8)
    ok = d != 0
    return i[ok], d[ok], (i - 1)[ok]


def _bar_at(b, hour, minute, season=None):
    """Indices of the bars whose open is exactly at hour:minute UTC. `season` optionally selects a
    per-day UTC hour that moves with daylight saving (array of hours, one per bar)."""
    t = b.time
    h, mi = t.dt.hour.values, t.dt.minute.values
    if season is None:
        return np.where((h == hour) & (mi == minute))[0]
    return np.where((h == season) & (mi == minute))[0]


def open_momentum(b, hour, minute, Lmin=30):
    """Sign of the move over the first Lmin minutes after a session open; entry at the next minute.
    Entry bar = the bar opening at hour:minute+Lmin; the move is read from closed bars only."""
    i = _bar_at(b, (hour * 60 + minute + Lmin) // 60 % 24, (minute + Lmin) % 60)
    c = b.close.values
    tv = b.time.values.astype("datetime64[ns]").astype(np.int64)
    i = i[i > Lmin + 1]
    good = (tv[i - 1] - tv[i - 1 - Lmin]) == Lmin * 60_000_000_000
    i = i[good]
    d = np.sign(c[i - 1] - c[i - 1 - Lmin]).astype(np.int8)
    ok = d != 0
    return i[ok], d[ok], (i - 1)[ok]


# ---------------------------------------------------------------- Idea 3: first 5-minute candle at an open
def first_candle(b, hour, minute, dst=None):
    """Colour of the first 5-minute candle of the window; entry at the open of the second one.

    The candle is the five M1 bars opening at hour:minute .. +4, so its direction is
    close(bar at +4) - open(bar at hour:minute). The entry bar is the one opening at +5 minutes.
    `dst`: None = fixed UTC; 'eu' / 'us' = the UTC hour moves with daylight saving (summer one hour earlier).
    """
    t = b.time
    h, mi = t.dt.hour.values, t.dt.minute.values
    tv = t.values.astype("datetime64[ns]").astype(np.int64)
    if dst is None:
        want_h = np.full(len(h), hour)
    else:
        s = eu_dst(t) if dst == "eu" else us_dst(t)
        want_h = np.where(s, hour - 1, hour)
    k0 = np.where((h == want_h) & (mi == minute))[0]                   # bar opening the candle
    k0 = k0[(k0 + 5) < len(h)]
    good = (tv[k0 + 5] - tv[k0]) == 5 * 60_000_000_000                # all five minutes present
    k0 = k0[good]
    d = np.sign(b.close.values[k0 + 4] - b.open.values[k0]).astype(np.int8)
    ok = d != 0
    return (k0 + 5)[ok], d[ok], (k0 + 4)[ok]


# ---------------------------------------------------------------- the 25 variants
VARIANTS = []
for _W in A_WINDOWS:
    for _q in A_PCTS:
        VARIANTS.append((f"A_spikefade_p{_q}_w{_W}", "1 spike fade", "fade", dict(kind="spike", pct=_q, W=_W)))
for _Lm in B_BLOCKS:
    VARIANTS.append((f"B_mom{_Lm}min", "2 intraday momentum", "follow", dict(kind="block", L=_Lm)))
VARIANTS += [
    ("B_mom30_london0700", "2 intraday momentum", "follow", dict(kind="openmom", h=7, m=0)),
    ("B_mom30_comex1330", "2 intraday momentum", "follow", dict(kind="openmom", h=13, m=30)),
    ("B_mom30_bothopens", "2 intraday momentum", "follow", dict(kind="openmom_both")),
    ("C_first5m_london0700", "3 first 5-min candle", "follow", dict(kind="first", h=7, m=0, dst=None)),
    ("C_first5m_comex1330", "3 first 5-min candle", "follow", dict(kind="first", h=13, m=30, dst=None)),
    ("C_first5m_bothopens", "3 first 5-min candle", "follow", dict(kind="first_both")),
    ("C_first5m_localtime", "3 first 5-min candle", "follow", dict(kind="first_local")),
]
VAR_NAMES = [v[0] for v in VARIANTS]
SPEC = {v[0]: v[3] for v in VARIANTS}
FAMILY = {v[0]: v[1] for v in VARIANTS}
DIRN = {v[0]: v[2] for v in VARIANTS}
assert len(VARIANTS) == 25, len(VARIANTS)


def _merge(*parts):
    e = np.concatenate([p[0] for p in parts])
    d = np.concatenate([p[1] for p in parts])
    k = np.concatenate([p[2] for p in parts])
    o = np.argsort(e, kind="stable")
    return e[o], d[o], k[o]


def signals(name, b=None):
    """(entry bar index = k+1, declared side, signal bar index k)."""
    if b is None:
        b = bars(GOLD, "M1")
    spec = SPEC[name]
    kind = spec["kind"]
    if kind == "spike":
        return spike_signals(b, spec["pct"], spec["W"])
    if kind == "block":
        return block_momentum(b, spec["L"])
    if kind == "openmom":
        return open_momentum(b, spec["h"], spec["m"])
    if kind == "openmom_both":
        return _merge(open_momentum(b, 7, 0), open_momentum(b, 13, 30))
    if kind == "first":
        return first_candle(b, spec["h"], spec["m"], spec["dst"])
    if kind == "first_both":
        return _merge(first_candle(b, 7, 0, None), first_candle(b, 13, 30, None))
    if kind == "first_local":
        return _merge(first_candle(b, 8, 0, "eu"), first_candle(b, 13, 30, "us"))
    raise KeyError(name)


# ---------------------------------------------------------------- the acceptance bar
def rotation_placebo(pnl, prep, draws=200, seed=18):
    """BT14's logic in BT18's units: circularly rotate each reading's chosen-side vector by a random whole
    number of weeks (signal rhythm and outcome clustering survive, any real link dies), recompute, take the
    best of the 25 in that draw, repeat `draws` times and report the P95 of the best-of-25.
    Both signs of every reading are in the report, so the two-sided bar (acc2) is the headline one.
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
            a = np.mean(np.where(chosen > other, 1.0, np.where(chosen < other, 0.0, 0.5))) * 100
            aa.append(a)
            mm.append(chosen.mean())
            a2.append(max(a, 100 - a))
            m2.append(max(chosen.mean(), other.mean()))
            own[nm].append(a)
            own2[nm].append(max(a, 100 - a))
        acc.append(max(aa))
        mon.append(max(mm))
        acc2.append(max(a2))
        mon2.append(max(m2))
    q = lambda v, p: float(np.percentile(v, p))                        # noqa: E731
    return dict(draws=draws, n_readings=len(prep),
                acc_P50=q(acc, 50), acc_P95=q(acc, 95), acc_max=float(np.max(acc)),
                money_P50=q(mon, 50), money_P95=q(mon, 95), money_max=float(np.max(mon)),
                acc2_P50=q(acc2, 50), acc2_P95=q(acc2, 95), acc2_max=float(np.max(acc2)),
                money2_P50=q(mon2, 50), money2_P95=q(mon2, 95), money2_max=float(np.max(mon2)),
                # each reading's OWN rotation bar, so a reading can be judged against noise on its own
                # sample size rather than against the best of 25 (a small sample rotates further by luck)
                per_reading={nm: dict(n=int(len(prep[i][2])), acc_P95=q(own[nm], 95),
                                      acc2_P95=q(own2[nm], 95))
                             for i, (nm, _, _, _, _) in enumerate(prep)})
