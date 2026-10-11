"""BT16 shared pieces — the entry test. Pre-registered in bt/BT16_BRIEF.md.

The money shape is the user's chosen one and his broker's real costs:
    profit cap 1.5 price units = $15 at 0.10 lots
    loss cap   2.0 price units = $20 at 0.10 lots
    commission 0.00 (his statement says he pays none)  ->  common.COMM['XAUUSD'] = 0.0
    spread     0.072 price units (his own measured -$0.36 on 0.05 lots)

Everything that touches prices, fills, exits or costs goes through bt.common.simulate via bt15_lib.
Nothing here re-implements a simulator. BT16 only builds DIRECTION signals and scores them against the
both-sides outcome table.

CAUSALITY (ERRORS #36): a basket is filled at the OPEN of the entry bar. Every signal input is read from
the last bar of its timeframe that CLOSED at or before that open (common.asof_index). bt16_tests.py asserts it.
"""
import os
import numpy as np
import pandas as pd

from bt import common
from bt.common import bars, asof_index, ema, structure, RESULTS   # noqa: F401
from bt import bt15_lib as L

GOLD = "XAUUSD"
# ------------------------------------------------------------------ the fixed money shape
T_PRICE = 1.5            # profit cap, gold price units  = $15 at 0.10 lots
S_PRICE = 2.0            # loss cap,   gold price units  = $20 at 0.10 lots
HIS_SPREAD = 0.072       # his measured spread, price units
USD_PER_PRICE = 10.0     # $ per $1 of gold move at 0.10 lots
LOTS = 0.10

STRIDE_MIN = 30          # the BT15 opportunity grid
LAST_OPP = L.LAST_OPP    # "2025-06-27 00:00" - TEST (2025-07-01+) is LOCKED

USD_PAIRS = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1,
             "USDJPY": +1, "USDCAD": +1, "USDCHF": +1}
LOOKBACKS = [("D1_5min", 5), ("D2_15min", 15), ("D3_1h", 60), ("D4_4h", 240), ("D5_1day", 1440)]
RULES = ["R1_emacross", "R2_breakout", "R3_rangepos", "R4_rsi", "R5_macd", "R6_bband", "R7_mom10"]
TFS = ["M1", "M5", "M15"]
EVENTS = ["NFP", "CPI", "FOMC"]
WINDOWS = [("W1_30min_before", -30), ("W2_1min_after", 1), ("W3_60min_after", 60)]
NEWS_RULES = ["M_momentum30", "L_always_long"]

SESSIONS = L.SESSIONS
OUT = os.path.join(RESULTS, "BT16")
PARTS = os.path.join(OUT, "parts")


def setup():
    """His broker charges no commission. Must be called before any simulate()."""
    common.COMM[GOLD] = 0.0
    os.makedirs(PARTS, exist_ok=True)


def feed():
    return os.environ.get("SMC_DATASET", "zero")


def span():
    """HIST comes from the duka feed, DEV+VAL from the zero feed. No overlap."""
    return ("2009-03-15", "2021-01-01") if feed() == "duka" else ("2021-01-01", LAST_OPP)


def period_of(ts):
    """HIST 2009-2020 | DEV 2021-2023 | VAL 2024-mid2025. TEST is never reached (opportunities stop at LAST_OPP)."""
    t = pd.DatetimeIndex(ts)
    out = np.full(len(t), "HIST", dtype=object)
    out[t >= pd.Timestamp("2021-01-01")] = "DEV"
    out[t >= pd.Timestamp("2024-01-01")] = "VAL"
    out[t >= pd.Timestamp("2025-07-01")] = "TEST"
    return out


# ------------------------------------------------------------------ the outcome table
def outcome_table(b, idx, tag):
    """Both sides of the $15/$20 basket at every entry chance, via bt15_lib -> common.simulate.
    Checkpointed: the laptop sleeps, so a half-finished phase resumes."""
    f = os.path.join(PARTS, f"out_{tag}.npz")
    if os.path.exists(f):
        z = np.load(f)
        return z["pnl"], z["texit"], z["idx"]
    pnl, texit = L.outcomes(b, idx, T_PRICE, S_PRICE, delta=HIS_SPREAD)
    tp, sl = L.fillbar_masks(b, idx, T_PRICE, S_PRICE, delta=HIS_SPREAD)
    pnl = L.fix_fillbar(pnl, tp, sl, T_PRICE)
    np.savez_compressed(f, pnl=pnl, texit=texit, idx=idx)
    return pnl, texit, idx


# ------------------------------------------------------------------ Test 1: the dollar index
def usd_index_at(t_ns, cache={}):
    """Equal-weighted USD strength from the 7 USD pairs, strictly causal.

        usd(t) = (1/7) sum_p  s_p * log(close of p at the last M1 bar CLOSED at or before t)

    s_p = +1 when USD is the base (USDJPY/CAD/CHF), -1 when USD is the quote (EUR/GBP/AUD/NZDUSD).
    So usd() RISES when the dollar strengthens. Returns NaN where a pair has no bar yet.
    """
    t_ns = np.asarray(t_ns, dtype=np.int64)
    acc = np.zeros(len(t_ns))
    for sym, sgn in USD_PAIRS.items():
        if sym not in cache:
            p = bars(sym, "M1")
            cache[sym] = (p.tclose.values.astype("datetime64[ns]").astype(np.int64), np.log(p.close.values))
        tc, lc = cache[sym]
        k = np.searchsorted(tc, t_ns, "right") - 1
        v = np.where(k >= 0, lc[np.clip(k, 0, len(lc) - 1)], np.nan)
        acc += sgn * v
    return acc / len(USD_PAIRS)


def dollar_signals(t_open_ns):
    """Test 1: gold direction = OPPOSITE of the dollar's move over lookback L. Strictly causal."""
    now = usd_index_at(t_open_ns)
    out = {}
    for name, mins in LOOKBACKS:
        then = usd_index_at(t_open_ns - int(mins) * 60_000_000_000)
        d = now - then
        s = -np.sign(d)                      # dollar up -> gold down
        s[~np.isfinite(d)] = 0
        out[name] = s.astype(np.int8)
    return out


# ------------------------------------------------------------------ Test 3: classic rules on M1 / M5 / M15
def _rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().values
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().values
    rs = up / np.where(dn == 0, np.nan, dn)
    return 100 - 100 / (1 + rs)


def rule_signals(tf):
    """The 7 classic rules on one timeframe, as +1/-1/0 arrays aligned to that timeframe's bars.
    Every value at index k uses only data up to and including the CLOSE of bar k."""
    b = bars(GOLD, tf)
    c = b.close.values
    h, l = b.high.values, b.low.values
    cs = pd.Series(c)
    out = {}

    # R1 EMA cross (follow)
    out["R1_emacross"] = np.sign(ema(c, 9) - ema(c, 21))

    # R2 breakout of the previous 20 bars (follow)
    hh = cs.rolling(20).max().shift(1).values
    ll = cs.rolling(20).min().shift(1).values
    hh20 = pd.Series(h).rolling(20).max().shift(1).values
    ll20 = pd.Series(l).rolling(20).min().shift(1).values
    s = np.zeros(len(c))
    s[c > hh20] = 1
    s[c < ll20] = -1
    out["R2_breakout"] = s

    # R3 position in the last 20-bar range (FADE: low in the range -> long)
    rng = hh20 - ll20
    pos = np.where(rng > 0, (c - ll20) / np.where(rng > 0, rng, np.nan), np.nan)
    s = np.zeros(len(c))
    s[pos <= 0.38] = 1
    s[pos >= 0.62] = -1
    out["R3_rangepos"] = s

    # R4 RSI(14) extremes (FADE)
    r = _rsi(c)
    s = np.zeros(len(c))
    s[r <= 30] = 1
    s[r >= 70] = -1
    out["R4_rsi"] = s

    # R5 MACD(12,26) vs signal(9) (follow)
    macd = ema(c, 12) - ema(c, 26)
    out["R5_macd"] = np.sign(macd - ema(macd, 9))

    # R6 Bollinger(20,2) break (follow)
    m = cs.rolling(20).mean().values
    sd = cs.rolling(20).std(ddof=0).values
    s = np.zeros(len(c))
    s[c > m + 2 * sd] = 1
    s[c < m - 2 * sd] = -1
    out["R6_bband"] = s

    # R7 momentum over 10 bars (follow)
    out["R7_mom10"] = np.sign(c - cs.shift(10).values)

    for k in out:
        v = np.nan_to_num(out[k], nan=0.0)
        out[k] = v.astype(np.int8)
    # the first 30 bars of any rolling window are not defined -> no signal
    for k in out:
        out[k][:30] = 0
    _ = (hh, ll)
    return b, out


def tf_signals(t_open_ns):
    """All 21 Test-3 readings, mapped onto the entry chances with asof_index (closed bars only)."""
    t = t_open_ns.astype("datetime64[ns]")
    res = {}
    for tf in TFS:
        b, sig = rule_signals(tf)
        k = asof_index(b, t)                       # last bar CLOSED at or before the entry bar's OPEN
        ok = k >= 0
        kk = np.clip(k, 0, len(b) - 1)
        for name, arr in sig.items():
            v = arr[kk].astype(np.int8)
            v[~ok] = 0
            res[f"{name}_{tf}"] = v
    return res


# ------------------------------------------------------------------ Test 2: approximate news dates
def _us_dst(y):
    """US summer time: 2nd Sunday of March -> 1st Sunday of November (the 2007+ regime)."""
    mar = pd.date_range(f"{y}-03-01", f"{y}-03-31", freq="D")
    nov = pd.date_range(f"{y}-11-01", f"{y}-11-30", freq="D")
    start = mar[mar.dayofweek == 6][1]
    end = nov[nov.dayofweek == 6][0]
    return start, end


def _in_dst(ts):
    a, b = _us_dst(ts.year)
    return a <= ts.normalize() < b


FOMC_NOMINAL = [(1, 28), (3, 18), (4, 29), (6, 17), (7, 29), (9, 17), (10, 28), (12, 15)]


def news_times(y0=2009, y1=2025):
    """APPROXIMATE release minutes (UTC). We have no economic calendar; see BT16_BRIEF.md."""
    rows = []
    for y in range(y0, y1 + 1):
        for m in range(1, 13):
            days = pd.date_range(f"{y}-{m:02d}-01", periods=pd.Period(f"{y}-{m:02d}").days_in_month, freq="D")
            # NFP: first Friday, 13:30 UTC winter / 12:30 UTC summer
            fri = days[days.dayofweek == 4][0]
            rows.append(("NFP", fri + pd.Timedelta(hours=12 if _in_dst(fri) else 13, minutes=30)))
            # CPI: the 13th moved to the nearest weekday (the DAY IS A GUESS)
            d = pd.Timestamp(f"{y}-{m:02d}-13")
            if d.dayofweek == 5:
                d -= pd.Timedelta(days=1)
            elif d.dayofweek == 6:
                d += pd.Timedelta(days=1)
            rows.append(("CPI", d + pd.Timedelta(hours=12 if _in_dst(d) else 13, minutes=30)))
        # FOMC: the Wednesday nearest each nominal date, statement 18:00 UTC summer / 19:00 UTC winter
        for (m, dd) in FOMC_NOMINAL:
            d = pd.Timestamp(f"{y}-{m:02d}-{dd:02d}")
            off = (2 - d.dayofweek) % 7
            w = d + pd.Timedelta(days=off if off <= 3 else off - 7)
            rows.append(("FOMC", w + pd.Timedelta(hours=18 if _in_dst(w) else 19)))
    ev = pd.DataFrame(rows, columns=["event", "t"]).sort_values("t").reset_index(drop=True)
    return ev[(ev.t >= "2009-03-15") & (ev.t < LAST_OPP)].reset_index(drop=True)


def news_chances(b, ev, a, z):
    """Bar indices for every (event, window) entry minute inside [a, z). Returns a tidy frame."""
    t1 = b.time.values.astype("datetime64[ns]")
    rows = []
    for name, off in WINDOWS:
        tt = (ev.t + pd.Timedelta(minutes=int(off))).values.astype("datetime64[ns]")
        k = np.searchsorted(t1, tt, "left")
        k = np.clip(k, 0, len(t1) - 1)
        gap = (t1[k] - tt) / np.timedelta64(1, "m")
        good = (gap >= 0) & (gap <= 2) & (tt >= np.datetime64(pd.Timestamp(a))) & (tt < np.datetime64(pd.Timestamp(z)))
        for i in np.where(good)[0]:
            rows.append((ev.event.iloc[i], name, int(k[i]), pd.Timestamp(t1[k[i]]), ev.t.iloc[i]))
    return pd.DataFrame(rows, columns=["event", "window", "bar", "t_open", "t_release"])


def momentum30(b, bar_idx):
    """Gold's own move over the last 30 one-minute bars ending at the entry bar's OPEN. Causal: closes of bars
    that closed at or before that open (M1 bar j closes at the open of bar j+1)."""
    c = b.close.values
    i = np.asarray(bar_idx) - 1           # last M1 bar closed at the entry bar's open
    j = i - 30
    s = np.where(j >= 0, np.sign(c[np.clip(i, 0, len(c) - 1)] - c[np.clip(j, 0, len(c) - 1)]), 0)
    return s.astype(np.int8)


# ------------------------------------------------------------------ scoring
def decisive(pnl):
    """Chances where the two sides have different outcomes (so the direction choice mattered)."""
    ok = np.isfinite(pnl).all(axis=0)
    return ok & (pnl[0] != pnl[1])


def better_side(pnl):
    return np.where(pnl[0] >= pnl[1], 1, -1)


def score(sides, pnl, t_open, mask=None, fast=False):
    """The headline numbers for one reading.
    acc  = of the chances it took where the two sides differed, how often it chose the better side
    money= average dollars per basket at 0.10 lots over every chance it took
    t    = week-block t-stat of the money (overlapping baskets are correlated; naive t is never used alone)
    """
    ok = np.isfinite(pnl).all(axis=0) & (sides != 0)
    if mask is not None:
        ok = ok & mask
    if ok.sum() == 0:
        return dict(n=0, n_dec=0, acc=np.nan, money=np.nan, t=np.nan)
    si = np.where(sides[ok] == 1, 0, 1)
    p = pnl[si, np.where(ok)[0]]
    dec = ok & decisive(pnl)
    if dec.sum() > 0:
        bs = better_side(pnl)[dec]
        acc = 100.0 * float((sides[dec] == bs).mean())
    else:
        acc = np.nan
    if fast:
        return dict(n=int(ok.sum()), n_dec=int(dec.sum()), acc=acc, money=float(p.mean()), t=np.nan)
    wk = pd.DatetimeIndex(t_open[ok]).floor("7D").values.astype("int64")
    return dict(n=int(ok.sum()), n_dec=int(dec.sum()), acc=round(acc, 2),
                money=round(float(p.mean()), 4), t=round(float(L.block_t(p, wk)), 2))


def breakdown(name, family, sides, pnl, t_open):
    """One row per scope: ALL, each date period, each year, each hour of day, each session."""
    rows = []
    per = period_of(t_open)
    hrs = pd.DatetimeIndex(t_open).hour.values
    yrs = pd.DatetimeIndex(t_open).year.values
    ses = L.session_of(t_open)
    scopes = [("ALL", None)]
    scopes += [(p, per == p) for p in ("HIST", "DEV", "VAL") if (per == p).any()]
    scopes += [(f"Y{y}", yrs == y) for y in np.unique(yrs)]
    scopes += [(f"H{h:02d}", hrs == h) for h in range(24)]
    scopes += [(s, ses == s) for s, _, _ in SESSIONS]
    for sc, m in scopes:
        rows.append(dict(reading=name, family=family, scope=sc, **score(sides, pnl, t_open, m)))
    return rows


def baselines(pnl):
    """The floor and the ceiling, for the report. Money per basket at 0.10 lots."""
    ok = np.isfinite(pnl).all(axis=0)
    p = pnl[:, ok]
    best, worst = p.max(axis=0), p.min(axis=0)
    eb, ew = float(best.mean()), float(worst.mean())
    return dict(n=int(ok.sum()), E_best=eb, E_worst=ew, E_coin=float(p.mean()),
                xstar=100 * (-ew) / (eb - ew), decisive_pct=100 * float((p[0] != p[1]).mean()),
                coin_green_pct=100 * float((p > 0).mean()))


def money_at_acc(pnl, X, mask=None):
    """What an entry of accuracy X pays per basket, at this shape. Money is linear in X."""
    ok = np.isfinite(pnl).all(axis=0)
    if mask is not None:
        ok = ok & mask
    p = pnl[:, ok]
    return float(X * p.max(axis=0).mean() + (1 - X) * p.min(axis=0).mean())
