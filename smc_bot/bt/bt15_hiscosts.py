"""Smaller loss cap + bigger profit cap, priced with HIS broker's costs (no commission).

The user's question, exactly: if we cut the loss cap down and raise the profit cap, how many minutes
does a trade take, how many trades do we get in a day, and what are the chances of making money?

Costs here are his, not ours: Commission 0.00 (his account statement says so) and the 0.072 price
spread his own positions show (-$0.36 on 0.05 lots). Everything at 0.10 lots.

Run from smc_bot:  ../.venv/Scripts/python.exe -m bt.bt15_hiscosts
"""
import os
import numpy as np
import pandas as pd
from bt import bt15_lib as L
from bt import common

HIS_SPREAD = 0.072
TARGETS = [0.7, 1.0, 1.5, 2.0, 3.0]          # $7, $10, $15, $20, $30 at 0.10 lots
STOPS = [1.5, 2.0, 3.0]                      # $15, $20, $30
OUT = os.path.join(L.RESULTS, "BT15")


def main():
    common.COMM[L.GOLD] = 0.0                # his broker charges none
    b = L.bars(L.GOLD, "M1")
    idx = L.opportunity_idx(b, pd.Timestamp("2021-01-01"), pd.Timestamp(L.LAST_OPP))
    t_opp = b.time.values[idx].astype("datetime64[ns]").astype(np.int64)
    print(f"{len(idx):,} entry chances. His costs: no commission, {HIS_SPREAD} spread. 0.10 lots.\n")

    rows = []
    for T in TARGETS:
        for S in STOPS:
            pnl, texit = L.outcomes(b, idx, T, S, delta=HIS_SPREAD)
            tp, sl = L.fillbar_masks(b, idx, T, S, delta=HIS_SPREAD)
            pnl = L.fix_fillbar(pnl, tp, sl, T)

            ok = ~np.isnan(pnl).any(axis=0)
            p = pnl[:, ok]
            best, worst = p.max(axis=0), p.min(axis=0)
            needs = 100 * (-worst.mean() / (best.mean() - worst.mean()))

            hold = (texit - t_opp[None, :]) / 6e10
            hv = hold[np.isfinite(pnl) & (hold > 0)]

            bk = L.engine(t_opp, pnl, texit, L.skill_sides(pnl, 0.50, seed=7))
            d = L.day_stats(bk, balance=350.0)
            rows.append(dict(target=T * 10, stop=S * 10, median_min=float(np.median(hv)),
                             per_day=d["baskets_per_day"], win_pct=d["basket_win_pct"],
                             per_basket=d["per_basket"], day_50=d["avg_day"], needs=round(needs, 1)))
            print(f"  ${T*10:>3.0f}/${S*10:>3.0f}  {np.median(hv):>5.0f} min  {d['baskets_per_day']:>5.1f}/day  "
                  f"needs {needs:>5.1f}%", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "his_costs_grid.csv"), index=False)
    print("\n" + "=" * 92)
    print("HIS COSTS (no commission). 0.10 lots. Sorted by how long a trade takes.")
    print("=" * 92)
    print(f"{'PROFIT cap':>11}{'LOSS cap':>10}{'typical trade':>15}{'trades/day':>12}"
          f"{'win rate':>10}{'per trade':>11}{'NEEDS':>8}")
    for _, r in df.sort_values("median_min").iterrows():
        print(f"${r.target:>10.0f}${r.stop:>9.0f}{r.median_min:>12.0f} min{r.per_day:>12.1f}"
              f"{r.win_pct:>9.1f}%{r.per_basket:>+11.2f}{r.needs:>7.1f}%")


if __name__ == "__main__":
    main()
