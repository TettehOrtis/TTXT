"""BT19 - the time map. When does a basket reach each dollar level, up and down?

BT15 measured HOW FAR a basket travels (mae/mfe) and how long it was open in total. It never recorded
WHEN each dollar level was touched. Every question in this run is a first-passage-time question:

  - how long to reach +$7 / +$15 / +$30 / +$50 / +$100
  - how long to reach -$10 / -$30 / -$70
  - how high can it go in 24 h, and at what minute does the peak land
  - the kill rule: a basket is -$X at minute M. Does it still come back?
  - per session (Asian / London / Overlap / New York), how many chances and what they pay

Method: same entry chances (every 30th minute), same costs and same fill convention as bt15_lib, so the
money numbers reconcile with BT15. For each basket we walk forward up to HORIZON minutes and record the
first bar offset at which the floating money touched each level on a fixed ladder. Everything else is
derived from those first-passage times, so no question needs a second pass over the prices.

Floating money is GROSS of the $1.10 round-trip commission (it is a price excursion, not a closed trade);
a basket that closes at +$7 gross pays +$5.90 net. Levels, ladders and the horizon are fixed below before
any result is looked at.

    set SMC_DATASET=duka & .venv/Scripts/python.exe smc_bot/bt/bt19_timemap.py
    .venv/Scripts/python.exe smc_bot/bt/bt19_timemap.py
    .venv/Scripts/python.exe smc_bot/bt/bt19_timemap.py report
"""
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, COMM                                       # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402

DS = os.environ.get("SMC_DATASET", "zero")
SPAN = ("2009-03-15", "2021-01-01") if DS == "duka" else ("2021-01-01", L.LAST_OPP)
D = L.EXTRA_SPREAD                       # 0.05 price units of extra spread (gold spread column is 0, ERRORS #34)
C_USD = COMM[L.GOLD]                     # $1.10 round trip at 0.10 lots
U = L.USD_PER_PRICE                      # $10 per $1 of gold at 0.10 lots

HORIZON = 1440                           # minutes of forward walk (24 h); the user ruled out 26 h holds
UP = np.array([3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 70, 100, 150, 200], float)    # profit ladder, dollars
DN = np.array([3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 70, 100, 150, 200], float)    # loss ladder, dollars
CHUNK = 1200
PARTS = os.path.join(L.RESULTS, "BT19", "parts")
RES = os.path.join(L.RESULTS, "BT19")
NEVER = np.int32(HORIZON + 1)            # sentinel: level not touched inside the horizon


def _first_passage(run, levels):
    """First column index at which each row of `run` (monotone non-decreasing) reaches each level.

    Flattening trick: shifting row r by r*OFF makes the whole 2-D array globally non-decreasing, so one
    searchsorted answers every row and every level at once. Returns int32 (C, n_levels), NEVER if untouched.
    """
    C, H = run.shape
    OFF = 1e7
    flat = (run + (np.arange(C, dtype=np.float64) * OFF)[:, None]).ravel()
    q = levels[None, :] + (np.arange(C, dtype=np.float64) * OFF)[:, None]
    pos = np.searchsorted(flat, q.ravel(), "left").reshape(C, len(levels))
    off = (pos - (np.arange(C) * H)[:, None]).astype(np.int64)
    return np.where(off >= H, NEVER, off).astype(np.int32)


def walk(b, idx):
    """First-passage tables for both sides at every entry chance.

    Returns dict of (2, N, n_levels) int32 bar offsets for up/down, plus (2, N) peak and trough in dollars
    and the bar offset of the peak. Row 0 = long, row 1 = short.
    """
    n = len(idx)
    hi, lo, op, spr = (b.high.values.astype(np.float64), b.low.values.astype(np.float64),
                       b.open.values.astype(np.float64), b.spr.values.astype(np.float64))
    out = {"up": np.full((2, n, len(UP)), NEVER, np.int32), "dn": np.full((2, n, len(DN)), NEVER, np.int32),
           "peak": np.zeros((2, n), np.float32), "trough": np.zeros((2, n), np.float32),
           "tpeak": np.zeros((2, n), np.int32)}
    ar = np.arange(HORIZON)
    for si, side in enumerate((1, -1)):
        for a in range(0, n, CHUNK):
            ix = idx[a:a + CHUNK]
            J = ix[:, None] + ar[None, :]
            H_, L_ = hi[J], lo[J]
            if side == 1:                                  # long: fills at ask + extra spread, floats on the bid
                F = op[ix] + spr[ix] + D
                up_path, dn_path = (H_ - F[:, None]) * U, (L_ - F[:, None]) * U
            else:                                          # short: fills at bid - extra spread, floats on the ask
                F = op[ix] - D
                S_ = spr[J]
                up_path, dn_path = (F[:, None] - (L_ + S_)) * U, (F[:, None] - (H_ + S_)) * U
            runmax = np.maximum.accumulate(up_path, axis=1)
            runmin = np.minimum.accumulate(dn_path, axis=1)
            k = slice(a, a + len(ix))
            out["up"][si, k] = _first_passage(runmax, UP)
            out["dn"][si, k] = _first_passage(-runmin, DN)
            out["peak"][si, k] = runmax[:, -1]
            out["trough"][si, k] = runmin[:, -1]
            out["tpeak"][si, k] = np.argmax(up_path >= runmax[:, -1:] - 1e-9, axis=1)
    return out


def run():
    b = bars(L.GOLD, "M1")
    idx = L.opportunity_idx(b, *SPAN)
    idx = idx[idx + HORIZON < len(b) - 1]                  # need a full forward window
    t_opp = b.time.values[idx].astype("datetime64[ns]").astype(np.int64)
    os.makedirs(PARTS, exist_ok=True)
    f = os.path.join(PARTS, f"tm_{DS}.npz")
    if os.path.exists(f):
        print("skip", f)
        return
    print(f"{DS}: {len(idx)} entry chances, {2*len(idx)} baskets, horizon {HORIZON} min")
    t0 = time.time()
    o = walk(b, idx)
    # wall-clock minutes for a bar offset (weekend and daily-break gaps make bars != minutes)
    tmin = (b.time.values.astype("datetime64[m]").astype(np.int64))
    o["t_opp"] = t_opp
    o["idx"] = idx.astype(np.int64)
    o["tmin"] = tmin
    np.savez_compressed(f + ".tmp.npz", **o)
    os.replace(f + ".tmp.npz", f)
    print(f"  done in {time.time()-t0:.0f}s -> {f}")


if __name__ == "__main__":
    run()
