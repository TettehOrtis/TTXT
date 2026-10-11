"""Shared layer for the AUTOPSY stages. Read bt/AUTOPSY_BRIEF.md first.

Owner's split (2026-10-07) REPLACES the old DEV/VAL/TEST split in common.py:

    EXPLORE   2005-01-01 -> 2011-01-01   baseline + full trade autopsy, tune here
    VALIDATE  2011-01-01 -> 2017-01-01   reasons must survive here or they die
    STRESS    2017-01-01 -> 2021-01-01   + adverse costs, worse fills, feed swap
    EXAM      2021-01-01 ->              looked at ONCE, at the very end

Note the old split called 2021-2023 "DEV". Under this split those years are the EXAM. Any
`period` column inside an existing results/*/trades_*.csv uses the OLD labels -- ignore it and
use stage_of() here instead.

This module does not edit common.py. It imports from it.
"""
import os
import numpy as np
import pandas as pd

import common as C

STAGES = {
    "EXPLORE":  ("2005-01-01", "2011-01-01"),
    "VALIDATE": ("2011-01-01", "2017-01-01"),
    "STRESS":   ("2017-01-01", "2021-01-01"),
    "EXAM":     ("2021-01-01", "2030-01-01"),
}
TUNABLE = ("EXPLORE",)                      # the only stage you may look at while choosing rules
EXAM_UNLOCK = "FINAL-EXAM-APPROVED-BY-OWNER"


def stage_of(ts):
    """Stage label for a timestamp or a series of timestamps."""
    ts = pd.to_datetime(ts)
    if not hasattr(ts, "__len__"):
        for name, (a, b) in STAGES.items():
            if pd.Timestamp(a) <= ts < pd.Timestamp(b):
                return name
        return "OUT"
    out = pd.Series("OUT", index=range(len(ts)))
    for name, (a, b) in STAGES.items():
        out[np.asarray((ts >= a) & (ts < b))] = name
    return out.values


def gate(stage):
    """Raise unless this stage may be read. The EXAM needs an explicit unlock in the environment."""
    stage = stage.upper()
    if stage not in STAGES:
        raise ValueError("unknown stage %r; pick one of %s" % (stage, list(STAGES)))
    if stage == "EXAM" and os.environ.get("AUTOPSY_EXAM_UNLOCK") != EXAM_UNLOCK:
        raise PermissionError(
            "EXAM (2021-01-01 onward) is sealed. It is read ONCE, after a variant has survived "
            "EXPLORE, VALIDATE and STRESS, and only with the owner's approval. If you think you "
            "need it now, you do not -- you are about to overfit."
        )
    return stage


def clip(df, stage, tcol="t_signal"):
    """Keep only the rows of df inside `stage`. Always use this before looking at anything."""
    stage = gate(stage)
    t = pd.to_datetime(df[tcol])
    a, b = STAGES[stage]
    return df[(t >= a) & (t < b)].copy()


# ------------------------------------------------------------------ the 277 existing trade files
def trade_files(bt=None):
    import glob
    pat = os.path.join(C.RESULTS, bt if bt else "*", "trades_*.csv")
    return [f for f in sorted(glob.glob(pat)) if "READABLE" not in f]


#: 74 of the saved files are single-symbol gold backtests (BT15, BT21*, ...) with no `sym` column.
#: They hold 113,562 of the ~118,600 EXPLORE-window trades, so they must not be skipped. Their
#: symbol is inferred from the filename, defaulting to gold, and flagged in `sym_inferred`.
DEFAULT_SYM = "XAUUSD"


def _sym_from_name(path):
    stem = os.path.basename(path).upper()
    for s in sorted(C.PAIRS, key=len, reverse=True):
        if s in stem or s in os.path.dirname(path).upper():
            return s, False
    return DEFAULT_SYM, True


def load_trades(stage, bt=None, tcol="t_signal"):
    """Every saved trade from results/, clipped to `stage`. Adds `src` (which file) and `stage`.

    Columns differ between backtests, so this returns the union and leaves gaps as NaN. Join on
    (sym, t_signal, side) when adding conditions -- never trust another backtest's f_* columns.
    """
    gate(stage)
    out = []
    for f in trade_files(bt):
        try:
            d = pd.read_csv(f)
        except Exception:
            continue
        col = next((c for c in (tcol, "t_entry", "time", "entry_time") if c in d.columns), None)
        if col is None or len(d) == 0:
            continue
        add = {}
        if "sym" in d.columns:
            add["sym_inferred"] = False
        else:
            add["sym"], add["sym_inferred"] = _sym_from_name(f)
        add["src"] = os.path.relpath(f, C.RESULTS).replace("\\", "/")
        d = d.rename(columns={col: "t_signal"})
        d["t_signal"] = pd.to_datetime(d.t_signal, errors="coerce")
        d = pd.concat([d.dropna(subset=["t_signal"]).reset_index(drop=True),
                       pd.DataFrame(add, index=range(d.t_signal.notna().sum()))], axis=1)
        out.append(clip(d, stage))
    if not out:
        return pd.DataFrame()
    t = pd.concat(out, ignore_index=True, sort=False)
    t["stage"] = stage
    return t


# ------------------------------------------------------------------ ATR, the unit for every shape
#: ---------------------------------------------------------------- CORRUPT BARS (AUT08, verified)
#: The Dukascopy files contain bars with impossible prices -- a feed splice, where one instrument's
#: prices land in another's file. Verified count across data_duka/:
#:     AUDJPY  14,155 bars from 2005-05-01 21:00 (prices jump between 0.67, 2.27, 87.9, 105, 135
#:             -- AUDUSD-shaped values spliced into an AUDJPY file that trades near 85)
#:     GBPCHF       2 bars at 2007-08-06 12:05 (121.2087 in a file trading at 2.41)
#:     AUDUSD       1 bar  at 2005-11-21 20:12
#: AUT08 measured one GBPCHF bar producing a single -$1,437,434 trade -- 97% of the entire pooled
#: loss of a 35,000-trade run. The AUDJPY block is 3,500x larger and was not caught at all.
#: `simulate()` does not defend against this: common.py:172 only tests `risk > 0`, so a negative
#: stop price passes, and common.py:200 fills a stop at `min(stop_price, open) - slip`, unbounded.
#: ALWAYS screen bars before simulating, and say in your report that you did.
def screen_bars(b, lo=5.0, hi=5.0):
    """Drop bars whose high/low is impossible for the instrument. Returns (clean_bars, n_dropped).

    Compares against the file's own median close, so it needs no per-symbol table.
    """
    m = float(b.close.median())
    bad = (b.high > m * hi) | (b.low < m / lo) | (b.low <= 0) | (b.high < b.low)
    return b[~bad].reset_index(drop=True), int(bad.sum())


#: ---------------------------------------------------------------- MISSING SPREAD (AUT09, verified)
#: The zero-spread problem is NOT gold-only. Verified across data_duka/:
#:     AUDCAD 92.1% zero   AUDCHF 92.0%   AUDJPY 92.1%   AUDNZD 92.0%   AUDUSD 92.5%
#:     CADJPY 92.0%        CHFJPY 92.3%   EURAUD 92.1%   EURCAD 92.0%   EURCHF 92.7%
#:     XAUUSD 100.0%
#: A zero there is a MISSING reading, not a zero spread -- the non-zero bars of the same file carry
#: a sane figure (AUDUSD 2.02 pips, the crosses ~20). AUT09 measured EURUSD on 2009-06 at a genuine
#: 1.22 pips against a cached mean of 0.26. So every duka backtest that trusted the column is
#: optimistic by roughly a pip on FX as well as $0.50 on gold.
#: `safe_bars()` repairs it: each zero is replaced by that symbol's own median non-zero spread.
REPAIR_SPREAD = True


def _repair_spread(b, sym):
    """Replace missing (zero) spreads with this symbol's own median non-zero spread. Returns n fixed."""
    if "spr" not in b.columns:
        return b, 0
    z = b.spr <= 0
    if not z.any():
        return b, 0
    if sym == "XAUUSD":
        fill = GOLD_SPREAD                                  # 100% missing: no non-zero to learn from
    else:
        nz = b.spr[~z]
        if len(nz) == 0:
            return b, 0
        fill = float(nz.median())
    b = b.copy()
    b.loc[z, "spr"] = fill
    return b, int(z.sum())


def safe_bars(sym, tf, _memo={}):
    """`C.bars()` screened for corrupt bars, with missing spreads repaired, and memoised.

    Use this, not `C.bars()`. Three fixes in one:
      - drops the 14,158 corrupt Dukascopy bars (AUT08)
      - fills the 92-100% missing spread readings (AUT09)
      - memoises, because `C.bars()` re-reads a 250 MB parquet on EVERY call, which dominated run
        time -- a 36-shape ladder went from minutes to hours (AUT08 bug 3)
    """
    key = (sym, tf, C.DATASET, REPAIR_SPREAD)
    if key not in _memo:
        import warnings
        b, n = screen_bars(C.bars(sym, tf))
        if n:
            warnings.warn("screen_bars dropped %d corrupt %s %s bars" % (n, sym, tf))
        if REPAIR_SPREAD:
            b, nz = _repair_spread(b, sym)
            if nz:
                warnings.warn("repaired %d missing %s %s spreads" % (nz, sym, tf))
        _memo[key] = b
    return _memo[key]


# ------------------------------------------------------------------ the cost-share filter (AUT09)
#: AUT09's headline, on 1.36M random entries: "trade the overlap" is the WRONG instruction;
#: "trade when the round trip is a small share of the move you are playing for" is the right one,
#: and it largely subsumes the first. Cost share is 1.9x stronger than the session clock, and it
#: wins the conditioning test both ways -- holding cost share fixed, Asia and the late hours are as
#: good as the overlap; about 60% of the whole session effect IS cost share.
#: It needs no session calendar and no DST logic, and it transfers across pairs and eras.
#: CAVEAT: below ~10% of one ATR the filter is worth nothing. Gold's round trip is 6-18% of an ATR,
#: so gold's five cost groups have no ordering at all. Check your instrument against that line first.
COST_SHARE_SKIP = 0.20          # skip when the round trip exceeds this share of the ATR risked
COST_SHARE_HARD = 0.35          # never trade above this
COST_SHARE_FLOOR = 0.10         # below this the filter has no power -- do not bother applying it
BAD_HOURS_UTC = (19, 20, 21, 22, 23, 2, 3, 4)   # AUT09: hard-block. Worst are 20 and 21, not 22.


def cost_share(sym, atr_price, stop_mult=1.0, spread=None):
    """Round-trip cost as a share of the risk taken. Feed it to COST_SHARE_SKIP / _HARD."""
    p = C.pip(sym)
    spread = spread_of(sym) if spread is None else spread
    risk_pips = atr_price * stop_mult / p
    return (C.COMM[sym] + spread / p) / risk_pips if risk_pips > 0 else np.inf


def spread_of(sym, hour=None):
    """Spread in PRICE units for `sym`. Gold uses the measured figure; FX uses the repaired median."""
    if sym == "XAUUSD":
        return gold_spread(sym, hour=hour)
    b = safe_bars(sym, "M1")
    return float(b.spr.median()) if "spr" in b.columns else 0.0


def atr(b, n=14):
    """Wilder ATR on a bars frame, in price units. Causal: the value at i uses bars up to i."""
    pc = b.close.shift(1)
    tr = pd.concat([b.high - b.low, (b.high - pc).abs(), (b.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def atr_at(sym, tf, times, n=14):
    """ATR of the last CLOSED `tf` bar at each time. Never peeks at the bar in progress."""
    b = C.bars(sym, tf)
    a = atr(b, n).values
    i = C.asof_index(b, pd.to_datetime(times).values)
    return np.where(i >= 0, a[np.clip(i, 0, len(a) - 1)], np.nan)


# Every target and stop is a multiple of M15 ATR, never a dollar amount.
# BT21 finding #2: a fixed $15 target quietly became a 5x-faster strategy as gold repriced.
ATR_LADDER = [(t, s) for t in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0) for s in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)]


def shape_dollars(sym, atr_price, tgt_mult, lot=0.10):
    """Net dollars at `lot` for an ATR-sized winner, commission included."""
    pv = C.USD_PIP_01[sym] * (lot / 0.10)
    return (atr_price * tgt_mult / C.pip(sym)) * pv - C.COMM[sym] * pv


#: ---------------------------------------------------------------- SPREAD WARNING (AUT02, verified)
#: `data_duka/XAUUSD_M1.parquet` carries a spread column that is ZERO on all 5,526,570 bars
#: (nunique == 1, max == 0). Under SMC_DATASET=duka, `simulate()` therefore sets ask = bid and
#: GOLD TRADES AT NO SPREAD. Every duka gold backtest in this project that did not inject a
#: spread -- BT15, BT17, BT20, BT21 included -- is optimistic by the whole spread.
#: duka FX pairs are fine (EURUSD max 8, mean 0.31 pips).
#: `data_duka_full/` DOES carry a real gold spread and reaches back to 2005-01-02, but its units
#: are unverified (median 361 with gold near $1274 -- consistent with points of 0.001, i.e. ~$0.36,
#: NOT with common.py's pip scaling of 0.1). Do not use it until the units are settled.
#: Until then: inject a spread explicitly with `gold_spread()` and state the figure in your report.
#:
#: THE NUMBER IS SETTLED. ERRORS.md #34 records the real raw gold spread measured on the live Zero
#: terminal: **0.05 of gold price = $0.50 per 0.10 lot**. ERRORS #44 confirms it, measuring 0.040 at
#: every hour except 21:00 UTC (0.304, and plain XAUUSDz does not trade 21:00-22:00). BT15 already
#: charges exactly 0.05 on every gold basket. Use 0.05. Do not invent your own figure:
#:   - an earlier draft of this file used 0.30 -- 6x too high
#:   - AUT09's cost_curve_by_hour.csv reports ~4.6 "spread_pips" for gold, i.e. 0.46 price units,
#:     ~9x too high; its by-hour COST column is wrong, though its by-hour TRAVEL column is fine
GOLD_SPREAD = 0.05              # price units; = $0.50 per 0.10 lot. Source: ERRORS.md #34, #44
GOLD_SPREAD_21UTC = 0.304       # the one bad hour (ERRORS #44); that hour is effectively untradeable


def gold_spread(sym, stage=None, hour=None):
    """Spread in PRICE units to inject when the feed reports none. Returns 0 where the feed is real.

    Pass `hour` (UTC) to get the 21:00 widening, which is ~6x the rest of the day.
    """
    if sym != "XAUUSD" or C.DATASET != "duka":
        return 0.0
    return GOLD_SPREAD_21UTC if hour == 21 else GOLD_SPREAD


def breakeven_rate(sym, atr_price, tgt_mult, stop_mult, spread=None):
    """How many right in 100 this ATR shape needs just to break even after costs.

    `spread` is in PRICE units and defaults to `gold_spread(sym)`, because the duka gold feed
    reports none. Pass 0.0 only if you have confirmed the feed carries a real spread (AUT02 bug 2).
    """
    p = C.pip(sym)
    spread = gold_spread(sym) if spread is None else spread
    win = atr_price * tgt_mult / p - spread / p
    loss = atr_price * stop_mult / p + C.STOP_SLIP + spread / p
    return 100.0 * (loss + C.COMM[sym]) / (win + loss)


# ------------------------------------------------------------------ the luck bar (catalogue G1)
def luck_bar(sym, n, tgt_mult, stop_mult, stage, seed=0, hours=None, max_hold_min=None,
             one_at_a_time=False):
    """Hit rate of RANDOM entries with the same ATR shape, same stage, same costs.

    Beating 50 proves nothing. This is the number a real signal has to beat. Pass `hours` (UTC
    hours) when the real signal only fires at certain times -- the bar must match its clock, and
    `max_hold_min` to match its time stop.

    AUT03 bug 2, fixed: this used to call `simulate` with its `one_at_a_time=True` default, so
    asking for 4,000 random entries returned only 2,727-2,789 filled. The rest were skipped_busy,
    and the survivors were exactly those following a fast-resolving trade -- biased toward calm
    stretches. Five seeds at n=4,000 on EXPLORE gold gave 37.59 / 39.19 / 39.86 / 40.77 / 39.97:
    a 3.2-point spread on a number meant to be an acceptance threshold. The default is now False,
    so every draw is measured independently, which is what a luck bar means.

    The hold was also hard-coded to five days, so the bar resolved a higher share of its trades
    than any rule with a shorter time stop. Pass `max_hold_min` to match the rule you are judging.
    """
    gate(stage)
    b = C.bars(sym, "M1")
    a, z = STAGES[stage]
    m = (b.time >= a) & (b.time < z)
    if hours is not None:
        # AUT02 bug 3: callers derive `hours` from t_signal, which is a bar CLOSE. Matching on the
        # bar's open put the luck bar up to an hour out for any H1/H4-derived hour list.
        m &= b.tclose.dt.hour.isin(list(hours))
    idx = np.flatnonzero(m.values)
    if len(idx) == 0:
        raise ValueError("no M1 bars for %s in %s" % (sym, stage))
    rng = np.random.default_rng(seed)
    pick = np.sort(rng.choice(idx, size=min(n, len(idx)), replace=False))
    o = pd.DataFrame({
        "t_signal": b.tclose.values[pick],
        "side": rng.choice([1, -1], size=len(pick)),
        "entry_type": "market",
    })
    at = atr_at(sym, "M15", o.t_signal)
    # AUT02 bug 4 and AUT03 bug 3: the next bar's open is a BID. `simulate` fills a buy at
    # open + spread and a sell at open, so anchoring both sides at open +/- half a spread left a
    # long's stop half a spread too tight and a short's half a spread too wide. Anchor each side
    # at the price it actually fills at.
    spr = gold_spread(sym)
    px = b.open.values[np.clip(pick + 1, 0, len(b) - 1)] + np.where(o.side.values > 0, spr, 0.0)
    o["entry"] = np.nan
    o["sl"] = px - o.side * at * stop_mult
    o["tp"] = px + o.side * at * tgt_mult
    hold = 60 * 24 * 5 if max_hold_min is None else max_hold_min
    o["expire"] = o.t_signal + pd.Timedelta(minutes=hold)
    o["be_at_r"] = np.nan
    o["max_hold_min"] = hold
    return C.simulate(sym, o.dropna(subset=["sl", "tp"]).reset_index(drop=True),
                      one_at_a_time=one_at_a_time)


# ------------------------------------------------------------------ scoring a reason
def score(trades, cond):
    """Report a condition three ways at once. A condition that lifts the hit rate while throwing
    away 90% of the trades has almost certainly found nothing -- which is why trades and dollars
    are reported beside it.
    """
    def row(t, label):
        if len(t) == 0:
            return dict(group=label, trades=0, right_in_100=np.nan, net_usd_per_trade=np.nan)
        if "net_pips" in t:
            wins = t.net_pips > 0
        elif "R" in t:
            wins = t.R > 0
        else:
            raise KeyError("trades need a net_pips or R column to score wins")
        usd = t.usd_01 if "usd_01" in t else pd.Series(np.nan, index=t.index)
        return dict(group=label, trades=len(t),
                    right_in_100=round(100 * wins.mean(), 2),
                    net_usd_per_trade=round(usd.mean(), 3))
    cond = np.asarray(cond, dtype=bool)
    return pd.DataFrame([row(trades, "all"),
                         row(trades[cond], "condition true"),
                         row(trades[~cond], "condition false")])


class Tally:
    """Count every condition tried. The trial count is part of the result (catalogue G1)."""

    def __init__(self, name):
        self.name, self.rows = name, []

    def add(self, cond_id, note, table):
        self.rows.append(dict(cond=cond_id, note=note,
                              lift=round(table.right_in_100[1] - table.right_in_100[0], 2),
                              kept=int(table.trades[1]),
                              usd=table.net_usd_per_trade[1]))
        return table

    def frame(self):
        d = pd.DataFrame(self.rows)
        d.attrs["conditions_tried"] = len(d)
        return d
