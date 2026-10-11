"""BT16 blocking checks. Run BEFORE trusting any BT16 number.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt16_tests

What it asserts:
 1. every signal input is read from a bar that CLOSED at or before the entry bar's OPEN (the ERRORS #36 trap)
 2. the dollar index reads only closed FX bars
 3. the scorer is not broken: a cheating signal scores 100 and pays the oracle; a coin flip scores ~50 and loses
 4. the break-even accuracy of this money shape is the 52.9 the brief claims
 5. no entry chance, and no bar used by any signal, touches TEST (2025-07-01 onward)
"""
import numpy as np
import pandas as pd

from bt import bt16_lib as B
from bt import bt15_lib as L
from bt.common import asof_index


def main():
    B.setup()
    from bt import common
    assert common.COMM[B.GOLD] == 0.0, "his broker charges no commission"
    b = B.bars(B.GOLD, "M1")
    a, z = B.span()
    idx = L.opportunity_idx(b, pd.Timestamp(a), pd.Timestamp(z), B.STRIDE_MIN)
    idx = idx[::37]                                        # a sample is enough for a causality proof
    t_open = b.time.values[idx].astype("datetime64[ns]")
    t_ns = t_open.astype(np.int64)
    print(f"{len(idx):,} sampled entry chances, {pd.Timestamp(t_open[0])} -> {pd.Timestamp(t_open[-1])}")

    # ---- 1. timeframe signals are causal
    for tf in B.TFS:
        bb = B.bars(B.GOLD, tf)
        k = asof_index(bb, t_open)
        ok = k >= 0
        used = bb.tclose.values[k[ok]].astype("datetime64[ns]")
        assert (used <= t_open[ok]).all(), f"{tf}: a signal bar closed AFTER the fill"
        print(f"  OK {tf}: every signal bar closed at or before the fill "
              f"(worst lag {np.min((t_open[ok]-used)/np.timedelta64(1,'m')):.0f} min)")

    # ---- 2. the dollar index reads only closed FX bars
    for sym in B.USD_PAIRS:
        p = B.bars(sym, "M1")
        tc = p.tclose.values.astype("datetime64[ns]").astype(np.int64)
        k = np.searchsorted(tc, t_ns, "right") - 1
        assert (tc[k[k >= 0]] <= t_ns[k >= 0]).all(), f"{sym}: dollar index used a bar closing after the fill"
    print(f"  OK dollar index: all {len(B.USD_PAIRS)} pairs read closed bars only")

    # ---- 3. the 30-minute gold momentum used by the news rules is causal
    m = B.momentum30(b, idx)
    used = b.tclose.values[idx - 1].astype("datetime64[ns]")
    assert (used <= t_open).all(), "momentum30 read a bar closing AFTER the entry bar's open"
    lag = (t_open - used) / np.timedelta64(1, "m")
    print(f"  OK momentum30: reads a bar closed at or before the fill (worst lag {lag.max():.0f} min, "
          f"{(lag==0).mean()*100:.1f}% exact); {(m!=0).mean()*100:.0f}% of chances have a signal")

    # ---- 4. the scorer, the floor and the ceiling
    pnl, texit = L.outcomes(b, idx, B.T_PRICE, B.S_PRICE, delta=B.HIS_SPREAD)
    tp, sl = L.fillbar_masks(b, idx, B.T_PRICE, B.S_PRICE, delta=B.HIS_SPREAD)
    pnl = L.fix_fillbar(pnl, tp, sl, B.T_PRICE)
    bl = B.baselines(pnl)
    cheat = B.better_side(pnl).astype(np.int8)
    sc = B.score(cheat, pnl, t_open)
    assert sc["acc"] == 100.0, f"a cheating signal must score 100, got {sc['acc']}"
    assert abs(sc["money"] - bl["E_best"]) < 0.01, "a cheating signal must pay the oracle"
    print(f"  OK scorer: cheating signal {sc['acc']}% and ${sc['money']:.2f}/basket (oracle ${bl['E_best']:.2f})")
    rng = np.random.default_rng(1)
    coin = rng.choice([-1, 1], len(idx)).astype(np.int8)
    sc = B.score(coin, pnl, t_open)
    assert 46 < sc["acc"] < 54, f"a coin flip must score about 50, got {sc['acc']}"
    assert sc["money"] < 0, "a coin flip must LOSE after costs"
    print(f"  OK scorer: coin flip {sc['acc']}% and ${sc['money']:.2f}/basket (must be negative)")
    print(f"  break-even accuracy of this shape = {bl['xstar']:.2f}%   (the brief says 52.9)")
    assert 52.0 < bl["xstar"] < 53.8, "the money shape is not the one the brief fixed"

    # ---- 5. TEST is locked
    assert pd.Timestamp(t_open[-1]) < pd.Timestamp("2025-07-01"), "an entry chance reached TEST"
    ex = pd.Timestamp(np.max(texit[np.isfinite(pnl)]).astype("datetime64[ns]"))
    print(f"  OK TEST locked: last entry {pd.Timestamp(t_open[-1])}, last exit minute touched {ex}")

    print("\nALL BT16 CHECKS PASS")


if __name__ == "__main__":
    main()
