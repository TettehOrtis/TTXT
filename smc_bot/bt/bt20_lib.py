"""BT20 - real entries x every exit shape, with the robot's throughput rules.

The gap this fills. BT18 measured the spike-fade entry at ONE exit shape ($15 target / $20 stop) and at
the SELLER's costs (no commission). BT19 measured every exit shape but with a coin-flip entry. Nobody has
ever run a real entry at a different shape, and nobody has measured how the shape changes the NUMBER of
trades: the robot holds one basket at a time with a 60-second cooldown, so a $5 stop that resolves in two
minutes frees the robot far sooner than a $70 stop that sits open for 105 minutes. More trades can mean
more money or more loss; it has never been measured.

Method. For each entry rule we take its signal bars, walk each one forward minute by minute, and record
the first minute the floating money touched every level on a fixed ladder (same technique as BT19). From
those first-passage times the outcome AND the exit minute of ANY (target, stop) pair follow instantly, so
one pass over the prices prices every shape. Then the robot's own mechanics are replayed on top:
one basket at a time, 60-second cooldown.

Costs are OURS throughout ($1.10 round-trip commission, 0.05 extra spread) so every number here
reconciles with BT19. The seller's account has no commission; his bar is about 2 points lower.
Gross = the price move in dollars. Net = after commission.

    .venv/Scripts/python.exe smc_bot/bt/bt20_lib.py            # build the walk tables
"""
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, RESULTS                                    # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402
from bt import bt18_lib as B18                                         # noqa: E402

GOLD = "XAUUSD"
U = 10.0                                  # $ per $1 of gold at 0.10 lots
D = 0.05                                  # extra spread, price units (gold spread column is 0, ERRORS #34)
COMM = 1.10                               # our measured Exness Zero round trip, $ per 0.10 lot
HORIZON = 1440                            # minutes walked forward
NEVER = np.int32(HORIZON + 1)
LADDER = np.array([3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 70, 100, 150, 200], float)
IX = {v: i for i, v in enumerate(LADDER)}
CHUNK = 1200
OUT = os.path.join(RESULTS, "BT20")
PARTS = os.path.join(OUT, "parts")

# The entry rules worth re-testing: everything BT18 scored at or above ~51.9 out of 100.
# (p98 and below are excluded - they score 51.6 and lower and would triple the walk for nothing.)
VARIANTS = ["A_spikefade_p99.95_w7200", "A_spikefade_p99.9_w7200", "A_spikefade_p99.5_w7200",
            "A_spikefade_p99.5_w1440", "A_spikefade_p99.0_w7200", "A_spikefade_p99.0_w1440",
            "A_spikefade_p99.9_w1440", "A_spikefade_p99.95_w1440",
            "B_mom30_comex1330", "B_mom30_bothopens",
            "RSI_fade_M1",          # BT16's best reading, 52.77 - never run at any other shape
            "BOS_fade_M1"]          # BT17's M1 break of structure, FADED: above 50 in 17 of 17 years


def _extra_signals(nm, b):
    """The two rules that live outside bt18_lib. Both return (entry bar index, side)."""
    if nm == "RSI_fade_M1":
        from bt import bt16_lib as B16
        s = B16.rule_signals("M1")[1]["R4_rsi"]
        k = np.where((s != 0) & (s != np.r_[0.0, s[:-1]]))[0]       # edge-triggered, not every bar it sits extreme
        return k + 1, s[k].astype(np.int8), k
    if nm == "BOS_fade_M1":
        from bt import bt17_lib as B17
        e, d, k, _tf = B17.signals("B1_bos_M1")
        return e, (-d).astype(np.int8), k                            # declared "follow", so fading flips the side
    raise KeyError(nm)

# The exit shapes. Targets and stops in dollars at 0.10 lots.
SHAPES = [(T, S) for T in (7, 10, 15, 20, 30, 50) for S in (5, 10, 15, 20, 30, 70)]


def walk(b, idx):
    """First-passage tables at the given bar indices, both sides.

    Returns up/dn as (2, n, levels) int32 bar offsets (NEVER = not touched inside the horizon) and
    `final` as (2, n) float32, the floating dollars at the end of the horizon - needed so a basket that
    resolves neither barrier still books a number instead of silently vanishing.
    """
    n = len(idx)
    hi, lo, op, spr = (b.high.values.astype(np.float64), b.low.values.astype(np.float64),
                       b.open.values.astype(np.float64), b.spr.values.astype(np.float64))
    cl = b.close.values.astype(np.float64)
    out = {"up": np.full((2, n, len(LADDER)), NEVER, np.int32),
           "dn": np.full((2, n, len(LADDER)), NEVER, np.int32),
           "final": np.zeros((2, n), np.float32)}
    ar = np.arange(HORIZON)
    for si, side in enumerate((1, -1)):
        for a in range(0, n, CHUNK):
            ix = idx[a:a + CHUNK]
            J = ix[:, None] + ar[None, :]
            if side == 1:                              # long: fills at ask + extra spread, floats on the bid
                F = op[ix] + spr[ix] + D
                up_path, dn_path = (hi[J] - F[:, None]) * U, (lo[J] - F[:, None]) * U
                fin = (cl[ix + HORIZON - 1] - F) * U
            else:                                      # short: fills at bid - extra spread, floats on the ask
                F = op[ix] - D
                S_ = spr[J]
                up_path, dn_path = (F[:, None] - (lo[J] + S_)) * U, (F[:, None] - (hi[J] + S_)) * U
                fin = (F - (cl[ix + HORIZON - 1] + spr[ix + HORIZON - 1])) * U
            k = slice(a, a + len(ix))
            out["up"][si, k] = _fp(np.maximum.accumulate(up_path, axis=1), LADDER)
            out["dn"][si, k] = _fp(-np.minimum.accumulate(dn_path, axis=1), LADDER)
            out["final"][si, k] = fin
    return out


def _fp(run, levels):
    """First column at which each row of `run` (monotone non-decreasing) reaches each level.
    Shifting row r by r*OFF makes the array globally sorted, so one searchsorted answers every row."""
    C, H = run.shape
    OFF = 1e7
    base = (np.arange(C, dtype=np.float64) * OFF)[:, None]
    pos = np.searchsorted((run + base).ravel(), (levels[None, :] + base).ravel(), "left").reshape(C, -1)
    off = (pos - (np.arange(C) * H)[:, None]).astype(np.int64)
    return np.where(off >= H, NEVER, off).astype(np.int32)


def signal_bars(ds):
    """Every variant's (entry bar, declared side), plus the sorted union of entry bars for one walk."""
    b = bars(GOLD, "M1")
    sig = {}
    for nm in VARIANTS:
        e, d, _k = _extra_signals(nm, b) if nm in ("RSI_fade_M1", "BOS_fade_M1") else B18.signals(nm, b)
        ok = (e > 0) & (e < len(b) - HORIZON - 2)
        sig[nm] = (e[ok].astype(np.int64), d[ok].astype(np.int8))
    union = np.unique(np.concatenate([v[0] for v in sig.values()]))
    return b, sig, union


def build(ds):
    os.makedirs(PARTS, exist_ok=True)
    f = os.path.join(PARTS, f"walk_{ds}.npz")
    if os.path.exists(f):
        print("  skip", f)
        return
    b, sig, union = signal_bars(ds)
    for nm, (e, d) in sig.items():
        print(f"    {nm}: {len(e):,} signals")
    print(f"  {ds}: {len(union):,} unique entry bars to walk")
    t0 = time.time()
    o = walk(b, union)
    np.savez_compressed(f + ".tmp.npz", union=union,
                        t=b.time.values[union].astype("datetime64[ns]").astype(np.int64), **o)
    os.replace(f + ".tmp.npz", f)
    # the per-variant signal lists, so the report does not have to regenerate them
    np.savez_compressed(os.path.join(PARTS, f"sig_{ds}.npz"),
                        **{f"{nm}__e": e for nm, (e, d) in sig.items()},
                        **{f"{nm}__d": d for nm, (e, d) in sig.items()})
    print(f"  done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    build(os.environ.get("SMC_DATASET", "zero"))
