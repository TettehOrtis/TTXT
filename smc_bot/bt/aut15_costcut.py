"""AUT15 - how cheap would trading have to be?  Engine + helpers.  Writes ONLY results/AUT15_costcut/.

The saved orders of each earlier study (sym, t_signal, side, sl, tp, entry, expire, be_at_r, max_hold_min) are replayed
through common.simulate with the commission and the spread multiplied by a factor f (f=1 today, f=0 no cost).
Stop and target PRICES come from the saved orders, so they are anchored to the signal and do not move with the cost.
Cost is paid once, inside the simulator. EXAM (2021+) is never read; data_zero is never read.
"""
import os
import sys
import warnings

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import common as C                                                        # noqa: E402
import autopsy_lib as A                                                   # noqa: E402

warnings.simplefilter("ignore")
OUT = os.path.join(C.RESULTS, "AUT15_costcut")
WORK = os.path.join(OUT, "_work")
os.makedirs(WORK, exist_ok=True)

MENU = [("today", 1.0), ("0.75", 0.75), ("0.50", 0.5), ("0.3125", 0.3125), ("0.15625", 0.15625), ("zero", 0.0)]
GOLD_MENU_USD = [1.60, 1.20, 0.80, 0.50, 0.25, 0.0]
FACTORS = [1.0, 0.75, 0.5, 0.3125, 0.15625, 0.0]
STAGE_NAMES = ("EXPLORE", "VALIDATE", "STRESS")

_TRUE_BARS = C.bars
_BASE = {}
_SCALED = {}
_COMM0 = dict(C.COMM)
_state = {"f": 1.0}


def _base(sym, tf):
    k = (sym, tf)
    if k not in _BASE:
        if len(_BASE) >= 4:                       # keep memory flat: at most two symbols in memory
            _BASE.clear()
            _SCALED.clear()
        C.bars = _TRUE_BARS
        try:
            _BASE[k] = A.safe_bars(sym, tf)           # corrupt bars out, missing spread repaired, gold = 0.05
        finally:
            C.bars = _scaled_bars
    return _BASE[k]


def _scaled_bars(sym, tf):
    f = _state["f"]
    k = (sym, tf, f)
    if k not in _SCALED:
        b = _base(sym, tf)
        if f == 1.0:
            _SCALED[k] = b
        else:
            b2 = b.copy(deep=False)
            b2["spr"] = b.spr.values * f
            _SCALED[k] = b2
        for kk in [x for x in _SCALED if x[0] == sym and x[1] == tf and x[2] != f]:
            del _SCALED[kk]                            # keep one scaled copy per (sym, tf)
    return _SCALED[k]


C.bars = _scaled_bars
A.C.bars = _scaled_bars


def set_cost(f):
    """Multiply commission and spread by f (inside this process only)."""
    _state["f"] = float(f)
    for s in C.COMM:
        C.COMM[s] = _COMM0[s] * f


def stage_col(t):
    return A.stage_of(pd.to_datetime(t))


def prep_orders(df, hold_default=None):
    """Make a simulate()-ready order frame from a saved trades frame."""
    o = pd.DataFrame({
        "t_signal": pd.to_datetime(df["t_signal"]).values,
        "side": df["side"].astype(int).values,
        "entry_type": (df["entry_type"].values if "entry_type" in df else np.full(len(df), "market", dtype=object)),
        "entry": (df["entry"].astype(float).values if "entry" in df else np.full(len(df), np.nan)),
        "sl": df["sl"].astype(float).values,
        "tp": df["tp"].astype(float).values,
        "be_at_r": (df["be_at_r"].astype(float).values if "be_at_r" in df else np.full(len(df), np.nan)),
    })
    if "max_hold_min" in df and df["max_hold_min"].notna().any():
        o["max_hold_min"] = df["max_hold_min"].astype(float).values
    else:
        o["max_hold_min"] = float(hold_default) if hold_default is not None else np.nan
    if "expire" in df and df["expire"].notna().any():
        o["expire"] = pd.to_datetime(df["expire"]).values
    else:
        o["expire"] = o["t_signal"]
    o["oid"] = np.arange(len(o))
    return o


def infer_hold(df):
    """max_hold_min from the TIME exits of a saved file when the file does not carry it."""
    if "max_hold_min" in df and df["max_hold_min"].notna().any():
        return None
    t = df[df.exit_reason == "TIME"]
    if len(t) == 0:
        return None
    return float(np.round(t.hold_h.max() * 60.0))


def resim(df, f, hold_default=None):
    """Replay the saved orders in `df` at cost factor f. Returns df['oid','usd','net_pips','gross_pips','status'] aligned to df."""
    set_cost(f)
    o = prep_orders(df, hold_default)
    o["sym"] = df["sym"].values
    outs = []
    for sym, g in o.groupby("sym", sort=False):
        g = g.drop(columns="sym")
        r = C.simulate(sym, g, one_at_a_time=False)
        outs.append(r[["oid", "status"] + [c for c in ("usd_01", "net_pips", "gross_pips", "exit_reason", "risk_pips") if c in r.columns]])
    set_cost(1.0)
    r = pd.concat(outs, ignore_index=True).set_index("oid").reindex(np.arange(len(o)))
    r.index = df.index
    return r


def resim_full(df, f, hold_default=None):
    """Like resim() but returns the whole simulate() frame (order columns + fills + exits), one row per order,
    with the original index of `df` in column `src_index`."""
    set_cost(f)
    o = prep_orders(df, hold_default)
    o["sym"] = df["sym"].values
    o["src_index"] = df.index.values
    outs = []
    for sym, g in o.groupby("sym", sort=False):
        g = g.drop(columns="sym")
        r = C.simulate(sym, g, one_at_a_time=False)
        outs.append(r)
    set_cost(1.0)
    return pd.concat(outs, ignore_index=True)
