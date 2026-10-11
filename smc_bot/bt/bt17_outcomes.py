"""BT17 phase 1 — build the both-sides outcome table for every M1 minute of a feed.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt17_outcomes            # zero  (2021 .. 2025-06-27)
    cd smc_bot && SMC_DATASET=duka ../.venv/Scripts/python.exe -m bt.bt17_outcomes   # duka (2009 .. 2020)

Checkpointed one small file per 150k minutes in results/BT17/parts/, so it resumes after a sleep.
"""
import time
import numpy as np
from bt import bt17_lib as B


def main():
    t0 = time.time()
    print(f"BT17 outcome table, feed={B.DS}, ${B.T_CAP*10:.0f} profit cap / ${B.S_CAP*10:.0f} loss cap "
          f"at 0.10 lots, no commission, {B.HIS_SPREAD} spread", flush=True)
    pnl, texit = B.outcome_table()
    b = B.bars(B.GOLD, "M1")
    t = b.time.values.astype("datetime64[ns]")
    m = B.span_mask(t, *B.SPAN) & np.isfinite(pnl).all(axis=0)
    p = pnl[:, m].astype(np.float64)
    best, worst = p.max(axis=0), p.min(axis=0)
    be = 100 * (-worst.mean()) / (best.mean() - worst.mean())
    hold = (texit[:, m] - t[m].astype(np.int64)[None, :]) / 6e10
    print(f"\n  {m.sum():,} usable minutes in {B.SPAN[0]}..{B.SPAN[1]}")
    print(f"  break-even accuracy on ALL minutes: {be:.2f}%   (BT15 grid says 52.9)")
    print(f"  coin flip money per basket: ${p.mean():+.3f} at 0.10 lots")
    print(f"  oracle (always right) money: ${best.mean():+.3f} | always wrong: ${worst.mean():+.3f}")
    print(f"  median hold {np.median(hold[hold > 0]):.0f} min")
    print(f"  latest minute simulated: {np.max(texit[:, m]).astype('datetime64[ns]')}")
    print(f"\n  {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
