"""Re-run the SHIPPED $7/$70 basket with the costs the seller's own account actually shows.

His account summary reads Commission 0.00 and Swap 0.00, and his positions open at -$0.36 on 0.05 lots
(= 0.072 price units of spread). Our runs charged COMM['XAUUSD'] = $1.10 per basket on top of the spread,
which his broker may simply not charge. If so, every win was understated: $5.90 instead of $7.00.

This re-prices the same baskets under three cost settings so the difference is visible.
Run from smc_bot:  ../.venv/Scripts/python.exe -m bt.bt15_nocomm
"""
import numpy as np
import pandas as pd
from bt import bt15_lib as L
from bt import common

T, S = 0.7, 7.0                      # the shipped target/stop, in gold price units
CASES = [("ours: $1.10 commission + 0.05 spread", 1.10, 0.05),
         ("his: NO commission + 0.05 spread", 0.00, 0.05),
         ("his: NO commission + his measured 0.072 spread", 0.00, 0.072),
         ("perfectly free: no commission, no spread", 0.00, 0.00)]


def main():
    b = L.bars(L.GOLD, "M1")
    idx = L.opportunity_idx(b, pd.Timestamp("2021-01-01"), pd.Timestamp(L.LAST_OPP))
    t_opp = b.time.values[idx].astype("datetime64[ns]").astype(np.int64)
    print(f"{len(idx):,} entry chances on XAUUSD, shipped target ${T*10:.0f} / stop ${S*10:.0f} at 0.10 lots\n")
    print(f"{'cost setting':<48}{'avg win':>9}{'avg loss':>10}{'win rate':>10}{'per basket':>12}{'NEEDS':>8}")

    orig = common.COMM[L.GOLD]
    try:
        for name, comm, delta in CASES:
            common.COMM[L.GOLD] = comm
            pnl, texit = L.outcomes(b, idx, T, S, delta=delta)
            tp, sl = L.fillbar_masks(b, idx, T, S, delta=delta)
            pnl = L.fix_fillbar(pnl, tp, sl, T)

            ok = ~np.isnan(pnl).any(axis=0)
            p = pnl[:, ok]
            best, worst = p.max(axis=0), p.min(axis=0)
            xstar = -worst.mean() / (best.mean() - worst.mean())

            flat = p.ravel()
            w, l = flat[flat > 0], flat[flat <= 0]
            coin = L.money_at(pnl, 0.50, seed=7)
            print(f"{name:<48}{w.mean():>+9.2f}{l.mean():>+10.2f}"
                  f"{100*len(w)/len(flat):>9.1f}%{coin.mean():>+12.2f}{100*xstar:>7.1f}%")
    finally:
        common.COMM[L.GOLD] = orig

    print("\nThe spread is already inside these numbers: a basket opens negative by the spread and the")
    print("target is only reached once that is covered, exactly as he described. The commission is the")
    print("part that was charged on top, and his account statement says his broker does not charge it.")


if __name__ == "__main__":
    main()
