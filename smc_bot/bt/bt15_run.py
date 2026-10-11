"""BT15 runner. Checkpointed to results/BT15/parts/ so a power loss or a usage limit only costs the
current chunk.

    set SMC_DATASET=duka & .venv/Scripts/python.exe smc_bot/bt/bt15_run.py opp      # HIST (2009-03..2020-12)
    .venv/Scripts/python.exe smc_bot/bt/bt15_run.py opp                             # DEV+VAL (Zero feed, 2021..2025-06)
    ... same for: grid, rules
Phases are independent; each skips work whose checkpoint already exists.
"""
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, ema, structure, asof_index          # noqa: E402
from bt import bt15_lib as L                                     # noqa: E402

DS = os.environ.get("SMC_DATASET", "zero")
SPAN = ("2009-03-15", "2021-01-01") if DS == "duka" else ("2021-01-01", L.LAST_OPP)
T0, S0 = 0.7, 7.0                                                # the shipped setting: $7 / $70 at 0.10 lots
TGRID = [0.3, 0.5, 0.7, 1.0, 1.5, 2.0]
SGRID = [3.0, 5.0, 7.0, 10.0, 15.0]
SUB = dict(duka=24_000, zero=18_000)[DS]                         # grid subsample size per dataset
os.makedirs(L.PARTS, exist_ok=True)


def part(name):
    return os.path.join(L.PARTS, name)


def save_npz(name, **kw):
    tmp = part(name + ".tmp.npz")
    np.savez_compressed(tmp, **kw)
    os.replace(tmp, part(name + ".npz"))
    print("  saved", name)


# ------------------------------------------------------------------ phase: opp (the headline cell)
def phase_opp():
    b = bars(L.GOLD, "M1")
    idx = L.opportunity_idx(b, *SPAN)
    print(f"{DS}: {len(b)} M1 bars {b.time.min()}..{b.time.max()}; {len(idx)} entry chances in {SPAN}")
    for delta in (L.EXTRA_SPREAD, 0.0):
        name = f"opp_{DS}_d{int(delta*100):02d}"
        if os.path.exists(part(name + ".npz")):
            print("  skip", name)
            continue
        t0 = time.time()
        pnl, tex = L.outcomes(b, idx, T0, S0, delta)
        print(f"  {name}: {2*len(idx)} baskets in {time.time()-t0:.0f}s")
        save_npz(name, idx=idx, t_opp=b.time.values[idx].astype("datetime64[ns]").astype(np.int64),
                 pnl=pnl, texit=tex)


# ------------------------------------------------------------------ phase: grid (target x stop -> X*)
def phase_grid():
    b = bars(L.GOLD, "M1")
    idx = L.opportunity_idx(b, *SPAN)
    step = max(1, len(idx) // SUB)
    sub = idx[::step]
    t_sub = b.time.values[sub].astype("datetime64[ns]").astype(np.int64)
    print(f"{DS}: grid subsample {len(sub)} of {len(idx)} chances (every {step}th)")
    for T in TGRID:
        for S in SGRID:
            name = f"grid_{DS}_T{T}_S{S}"
            if os.path.exists(part(name + ".npz")):
                continue
            t0 = time.time()
            pnl, tex = L.outcomes(b, sub, T, S, L.EXTRA_SPREAD)
            print(f"  T{T} S{S}: {2*len(sub)} baskets in {time.time()-t0:.0f}s")
            save_npz(name, t_sub=t_sub, pnl=pnl)


# ------------------------------------------------------------------ the four pre-registered readings
def rsi(x, n=14):
    d = np.diff(x, prepend=x[0])
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().values
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().values
    return 100 - 100 / (1 + up / np.maximum(dn, 1e-12))


def rule_signals(b, which):
    """Causal: M15 values are read only from the last M15 bar that had CLOSED, the M1 trigger uses the
    closed M1 bar, and t_signal is that bar's close time, so the fill is the next minute's open.
    At most ONE entry per M15 bar (pre-registered thinning)."""
    m15 = bars(L.GOLD, "M15")
    j = asof_index(m15, b.tclose.values)                       # last closed M15 bar per M1 bar
    ok = j >= 200
    c = b.close.values
    lo, hi = b.low.values, b.high.values
    if which == "a":                                           # M15 EMA50 vs EMA200 -> pullback to M1 EMA20
        e50, e200 = ema(m15.close.values, 50), ema(m15.close.values, 200)
        d = np.sign(e50[np.maximum(j, 0)] - e200[np.maximum(j, 0)]).astype(int)
        e20 = ema(c, 20)
        trig = ((d > 0) & (lo <= e20) & (c > e20)) | ((d < 0) & (hi >= e20) & (c < e20))
    elif which == "b":                                         # M15 close vs EMA200 -> M1 RSI(14) back from 30/70
        e200 = ema(m15.close.values, 200)
        d = np.sign(m15.close.values[np.maximum(j, 0)] - e200[np.maximum(j, 0)]).astype(int)
        r = rsi(c)
        rp = np.roll(r, 1)
        rp[0] = r[0]
        trig = ((d > 0) & (rp < 30) & (r >= 30)) | ((d < 0) & (rp > 70) & (r <= 70))
    elif which == "c":                                         # M15 HH/HL structure -> 38-62% retrace of the leg
        st = structure(m15, 5)
        jj = np.maximum(j, 0)
        d = st["trend"][jj].astype(int)
        lsh, lsl = st["lsh"][jj], st["lsl"][jj]
        leg = lsh - lsl
        good = np.isfinite(leg) & (leg > 0)
        zhi = np.where(d > 0, lsh - 0.382 * leg, lsl + 0.618 * leg)
        zlo = np.where(d > 0, lsh - 0.618 * leg, lsl + 0.382 * leg)
        inz = good & (((d > 0) & (lo <= zhi) & (lo >= zlo)) | ((d < 0) & (hi >= zlo) & (hi <= zhi)))
        prev = np.roll(inz, 1)
        prev[0] = False
        trig = inz & ~prev
    elif which == "d":                                         # M15 MACD sign -> first M1 close back with the trend
        cl = m15.close.values
        macd = ema(cl, 12) - ema(cl, 26)
        d = np.sign(macd - ema(macd, 9))[np.maximum(j, 0)].astype(int)
        up = np.diff(c, prepend=c[0]) > 0
        dn = np.diff(c, prepend=c[0]) < 0

        def three(x):                                          # 3 consecutive counter closes, ending at k-1
            s = pd.Series(x.astype(float)).rolling(3).sum().shift(1).values
            return s == 3
        trig = ((d > 0) & three(dn) & up) | ((d < 0) & three(up) & dn)
    else:
        raise ValueError(which)
    t = b.time
    m = (t >= SPAN[0]) & (t < SPAN[1])
    # CAUSALITY: the trigger bar k is only CLOSED at tclose[k], so the basket can open no earlier than
    # bar k+1. `outcomes()` fills at the open of the index it is given, so the index returned is k+1.
    # (Entering at the open of bar k would be reading a candle from the future — the BT14 lookahead trap;
    # it manufactured +$0.5..+$3.5 per basket here before it was caught. ERRORS #36.)
    cont = np.zeros(len(t), bool)                               # bar k+1 must immediately follow bar k
    tv = t.values.astype(np.int64)
    cont[:-1] = (tv[1:] - tv[:-1]) <= 60_000_000_000
    sel = np.where(ok & trig & (d != 0) & m.values & cont)[0]
    # one entry per M15 bar: keep the first trigger in each M15 bucket
    _, first = np.unique(j[sel], return_index=True)
    sel = np.sort(sel[first])
    return sel + 1, d[sel]


def phase_rules():
    b = bars(L.GOLD, "M1")
    for which in "abcd":
        name = f"rule_{DS}_{which}"
        if os.path.exists(part(name + ".npz")):
            print("  skip", name)
            continue
        t0 = time.time()
        sel, d = rule_signals(b, which)
        print(f"  rule {which}: {len(sel)} signals ({time.time()-t0:.0f}s detect)")
        pnl, tex = L.outcomes(b, sel, T0, S0, L.EXTRA_SPREAD)
        print(f"    simulated both sides in {time.time()-t0:.0f}s")
        save_npz(name, t_sig=b.time.values[sel].astype("datetime64[ns]").astype(np.int64), sides=d,
                 pnl=pnl, texit=tex)


if __name__ == "__main__":
    ph = sys.argv[1] if len(sys.argv) > 1 else "opp"
    print(f"=== BT15 phase {ph} dataset {DS}")
    {"opp": phase_opp, "grid": phase_grid, "rules": phase_rules}[ph]()
    print("=== done", ph, DS)
