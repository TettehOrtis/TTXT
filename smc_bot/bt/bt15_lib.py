"""BT15 shared pieces: the basket model, the outcome table and the day-by-day engine.

Everything that touches prices, fills, exits or costs goes through `bt.common.simulate`. Nothing here
re-implements a simulator: this module only (1) builds order tables for `simulate`, (2) stores the result
as a lookup table of "what a basket opened at minute i on side d would have paid", and (3) replays the
robot's own mechanics (60 s cooldown, pause-a-direction-after-2-losses, daily target / daily loss) over
that table.

The basket, restated (see research_notes/SNIPERSPARK_strategy.md):
every position opens at the same price with the same shared stop and target, so the basket is
arithmetically ONE trade of the combined size. We model one trade of 0.10 lots (= 10 oz, the shipped
0.02 x 5), target T dollars of gold price away, stop S away. At 0.10 lots $1 of gold = $10, so the
shipped 0.7 / 7.0 pair is exactly the shipped $7 / $70.

Costs: whatever `common.simulate` applies (per-minute spread from the data + the measured Exness Zero
commission COMM['XAUUSD'] = 1.10 gold-pips round trip = $1.10 per 0.10 lot, + 0.2 pip stop slippage),
PLUS an explicit extra spread `delta` in price units, because the gold `spread` column in both datasets
is 0 on ~97% of minutes (ERRORS #14 / #34) and would otherwise mean "assume zero spread".
delta is applied by shifting both barriers by delta and subtracting delta from the payout, which is
arithmetically identical to filling delta worse and is therefore faithful about WHICH barrier is hit.
"""
import os
import numpy as np
import pandas as pd
from bt.common import (bars, simulate, PERIODS, COMM, pip, RESULTS)  # noqa: F401

GOLD = "XAUUSD"
USD_PER_PRICE = 10.0           # $ per $1 of gold move at 0.10 lots (10 oz)
EXTRA_SPREAD = 0.05            # price units; measured Exness Zero XAUUSD spread. 0.0 = "data as-is" sensitivity
STRIDE_MIN = 30                # pre-registered opportunity grid: one entry chance every 30 minutes
LAST_OPP = "2025-06-27 00:00"  # TEST (2025-07-01+) is locked. Fri 2025-06-27 20:45 flattens every basket opened before it.

SESSIONS = [("Asian", 0, 7), ("London", 7, 12), ("Overlap", 12, 15), ("NewYork", 15, 20), ("Late", 20, 24)]
SPANS = {                      # the date splits the brief fixes (gold duka history starts 2009-03-15)
    "HIST": ("2009-03-15", "2021-01-01"),
    "DEV": ("2021-01-01", "2024-01-01"),
    "VAL": ("2024-01-01", LAST_OPP),
}
ERAS = {"2009-14": ("2009-03-15", "2015-01-01"), "2015-20": ("2015-01-01", "2021-01-01")}
PARTS = os.path.join(RESULTS, "BT15", "parts")


def session_of(ts):
    h = pd.DatetimeIndex(ts).hour.values
    out = np.full(len(h), "Late", dtype=object)
    for name, a, b in SESSIONS:
        out[(h >= a) & (h < b)] = name
    return out


# ---------------------------------------------------------------- opportunities
def opportunity_idx(b, start, end, stride_min=STRIDE_MIN):
    """Bar indices of the pre-registered entry chances: every `stride_min`-th minute of the clock."""
    t = b.time
    m = (t >= start) & (t < end)
    mins = (t.dt.hour * 60 + t.dt.minute).values
    return np.where(m.values & ((mins % stride_min) == 0))[0]


# ---------------------------------------------------------------- the basket as orders
def basket_orders(b, idx, side, T, S, delta=EXTRA_SPREAD):
    """One market basket per bar index in `idx`. Barriers are the robot's dollar barriers:
    the basket closes when its floating money reaches +T (price units) or -S."""
    o = b.open.values[idx]
    spr = b.spr.values[idx]
    if side == 1:                       # long: fills at ask = open + spr (+delta), floats against the bid
        F = o + spr
        sl, tp = F + delta - S, F + delta + T
    else:                               # short: fills at the bid, floats against the ask
        F = o
        sl, tp = F + S - delta, F - T - delta
    return pd.DataFrame(dict(t_signal=b.time.values[idx], side=side, entry_type="market", entry=np.nan,
                             expire=pd.NaT, sl=sl, tp=tp, be_at_r=np.nan, max_hold_min=np.nan,
                             opp=np.arange(len(idx), dtype=np.int64)))


def outcomes(b, idx, T, S, delta=EXTRA_SPREAD, chunk=120_000):
    """Run both sides of a basket at every opportunity through common.simulate.

    Returns (pnl, texit) with shape (2, len(idx)): row 0 = long, row 1 = short.
    pnl is dollars at 0.10 lots after every cost; texit is the exit minute as int64 ns.
    """
    n = len(idx)
    pnl = np.full((2, n), np.nan)
    texit = np.zeros((2, n), dtype=np.int64)
    for si, side in enumerate((1, -1)):
        for a in range(0, n, chunk):
            sub = idx[a:a + chunk]
            o = basket_orders(b, sub, side, T, S, delta)
            r = simulate(GOLD, o, one_at_a_time=False)
            f = r[r.status == "filled"]
            k = f.opp.values.astype(np.int64) + a
            pnl[si, k] = f.usd_01.values - delta * USD_PER_PRICE
            texit[si, k] = f.t_exit.values.astype("datetime64[ns]").astype(np.int64)
    return pnl, texit


def bar_index(b, t_ns):
    return np.searchsorted(b.time.values.astype(np.int64), np.asarray(t_ns, dtype=np.int64), "left")


def fillbar_masks(b, idx, T, S, delta=EXTRA_SPREAD):
    """Did the basket's own first minute already touch the target (and/or the stop)?
    `common.simulate` deliberately never books a target on the fill bar but does book a stop there.
    With a target 10x closer than the stop that pessimism is one-sided and large: a $7 target is
    SMALLER than a typical gold minute's range. Pure bookkeeping on the fill bar, no simulation."""
    o, h, l, s = b.open.values[idx], b.high.values[idx], b.low.values[idx], b.spr.values[idx]
    Fl, Fs = o + s, o
    tp = np.vstack([h >= Fl + delta + T, (l + s) <= Fs - T - delta])
    sl = np.vstack([l <= Fl + delta - S, (h + s) >= Fs + S - delta])
    return tp, sl


def fix_fillbar(pnl, tp_mask, sl_mask, T):
    """Book the target when the fill minute reached it and the stop was not also in that minute's range."""
    pay = T * USD_PER_PRICE - COMM[GOLD]
    return np.where(tp_mask & ~sl_mask & ~np.isnan(pnl), pay, pnl)


# ---------------------------------------------------------------- X*, the required accuracy
def xstar(pnl):
    """The decisive measurement. An entry with skill X picks, at each chance, the side that WOULD have
    worked with probability X (X = 0.5 is a coin flip, X = 1 is the oracle). Money is then linear in X:
        money(X) = X*E[best] + (1-X)*E[worst]
    so the break-even accuracy is X* = -E[worst] / (E[best] - E[worst]).
    Returns a dict with X* and the pieces a reader needs to check it.
    """
    ok = ~np.isnan(pnl).any(axis=0)
    p = pnl[:, ok]
    if p.shape[1] == 0:
        return dict(n=0)
    best, worst = p.max(axis=0), p.min(axis=0)
    eb, ew = best.mean(), worst.mean()
    both_win = float((p > 0).all(axis=0).mean())
    out = dict(n=int(p.shape[1]), E_best=float(eb), E_worst=float(ew), E_random=float(p.mean()),
               both_win_pct=100 * both_win, decisive_pct=100 * (1 - both_win),
               coin_win_pct=100 * float((p > 0).mean()))
    out["xstar"] = float((-ew) / (eb - ew)) * 100 if eb > ew else np.nan
    # the same requirement in the robot's own language: what share of baskets must close green
    x = out["xstar"] / 100
    out["req_win_pct"] = 100 * (both_win + (1 - both_win) * x) if np.isfinite(x) else np.nan
    return out


def money_at(pnl, X, seed=0):
    """Per-basket money for an entry of skill X, no mechanics. Used to confirm xstar() by simulation."""
    ok = ~np.isnan(pnl).any(axis=0)
    p = pnl[:, ok]
    best, worst = p.max(axis=0), p.min(axis=0)
    rng = np.random.default_rng(seed)
    take_best = rng.random(p.shape[1]) < X
    return np.where(take_best, best, worst)


def skill_sides(pnl, X, seed=0):
    """Side chosen (+1/-1) at each opportunity by an entry of skill X. NaN-safe, causal in timing:
    the minute is fixed by the opportunity grid, only the DIRECTION carries the skill."""
    rng = np.random.default_rng(seed)
    better = np.where(np.nan_to_num(pnl[0], nan=-1e9) >= np.nan_to_num(pnl[1], nan=-1e9), 1, -1)
    flip = rng.random(pnl.shape[1]) >= X
    return np.where(flip, -better, better)


# ---------------------------------------------------------------- the robot's mechanics, day by day
def engine(t_opp, pnl, texit, sides, cooldown_s=60, block_after=2, daily_target=0.0, daily_loss=0.0):
    """Replay the robot. One basket at a time; 60 s cooldown after a close; a direction pauses after
    `block_after` consecutive losing baskets and un-pauses at the start of the next trading day; the
    trading day ends on reaching the daily profit target or the daily loss limit (0 = off).

    t_opp, texit: int64 ns. sides: +1/-1 per opportunity, 0 = no signal at that chance.
    Returns a DataFrame of baskets.
    """
    n = len(t_opp)
    cd = int(cooldown_s) * 1_000_000_000
    day = t_opp // 86_400_000_000_000
    free_at = np.int64(0)
    cur_day = np.int64(-1)
    day_pnl = 0.0
    stopped = False
    streak = {1: 0, -1: 0}
    rows = []
    for i in range(n):
        d = int(sides[i])
        if d == 0:
            continue
        if day[i] != cur_day:
            cur_day, day_pnl, stopped = day[i], 0.0, False
            streak[1] = streak[-1] = 0
        if stopped or t_opp[i] < free_at:
            continue
        if streak[d] >= block_after:
            continue
        si = 0 if d == 1 else 1
        p = pnl[si, i]
        if not np.isfinite(p):
            continue
        day_pnl += p
        rows.append((t_opp[i], d, p, texit[si, i]))
        free_at = texit[si, i] + cd
        streak[d] = streak[d] + 1 if p < 0 else 0
        if daily_target > 0 and day_pnl >= daily_target:
            stopped = True
        if daily_loss > 0 and day_pnl <= -daily_loss:
            stopped = True
    if not rows:
        return pd.DataFrame(columns=["t_entry", "side", "pnl", "t_exit"])
    a = np.array(rows, dtype=object)
    return pd.DataFrame(dict(t_entry=pd.to_datetime(a[:, 0].astype(np.int64)), side=a[:, 1].astype(int),
                             pnl=a[:, 2].astype(float), t_exit=pd.to_datetime(a[:, 3].astype(np.int64))))


# ---------------------------------------------------------------- plain-English day statistics
def day_stats(bk, balance=350.0):
    """Everything the user asked to see first: days, in dollars."""
    if len(bk) == 0:
        return dict(baskets=0, days=0)
    d = bk.assign(day=bk.t_entry.dt.floor("D")).groupby("day").pnl.agg(["sum", "count"])
    v = d["sum"].values
    n = len(v)
    # worst run of losing days in a row
    run = best_run = 0
    for x in v:
        run = run + 1 if x < 0 else 0
        best_run = max(best_run, run)
    months = bk.assign(m=bk.t_entry.dt.to_period("M")).groupby("m").pnl.sum()
    eq = balance + np.cumsum(v)
    ruin = int(np.argmax(eq <= 0)) + 1 if (eq <= 0).any() else 0
    years = max(1e-9, (bk.t_entry.max() - bk.t_entry.min()).days / 365.25)
    return dict(baskets=int(len(bk)), days=n, baskets_per_day=round(len(bk) / n, 1),
                avg_day=round(float(v.mean()), 2), median_day=round(float(np.median(v)), 2),
                win_days_pct=round(100 * float((v > 0).mean()), 1),
                best_day=round(float(v.max()), 2), worst_day=round(float(v.min()), 2),
                worst_losing_streak_days=int(best_run),
                avg_month=round(float(months.mean()), 2), worst_month=round(float(months.min()), 2),
                best_month=round(float(months.max()), 2), months_down_pct=round(100 * float((months < 0).mean()), 1),
                per_basket=round(float(bk.pnl.mean()), 3), basket_win_pct=round(100 * float((bk.pnl > 0).mean()), 2),
                total=round(float(bk.pnl.sum()), 0), per_year=round(float(bk.pnl.sum() / years), 0),
                days_to_zero_from=round(balance, 0),
                days_to_zero=(ruin if ruin else (int(balance / -v.mean()) if v.mean() < 0 else 0)),
                max_dd=round(float((np.cumsum(v) - np.maximum.accumulate(np.maximum(np.cumsum(v), 0))).min()), 0))


def block_t(x, blocks):
    """t-stat of mean(x) with a block bootstrap over calendar days (overlapping baskets are correlated)."""
    x = np.asarray(x, float)
    u, inv = np.unique(blocks, return_inverse=True)
    sums = np.bincount(inv, weights=x)
    cnt = np.bincount(inv)
    m = x.mean()
    k = len(u)
    se = np.sqrt(((sums - cnt * m) ** 2).sum() / (k * (k - 1))) / max(1e-12, cnt.mean())
    return float(m / se) if se > 0 else np.nan
