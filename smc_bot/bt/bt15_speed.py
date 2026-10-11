"""BT15 addendum 3: the SPEED vs REQUIRED-ACCURACY trade-off.

The user's question, and it is the right one: the recommended 20.00/15.00 shape has the lowest accuracy
requirement but ties money up for hours. He wants fast trades. This builds the menu he chooses from -
for each target/stop shape: how long a basket actually takes, how many you get per day, what it needs
from the entry, and what it pays at 50/55/60% accuracy on a $350 account at 0.01 lots.

Run:  .venv/Scripts/python.exe -m bt.bt15_speed
"""
import os
import numpy as np
import pandas as pd
from bt import bt15_lib as L

OUT = os.path.join(L.RESULTS, "BT15")
PARTS = os.path.join(OUT, "parts")
os.makedirs(PARTS, exist_ok=True)

# target, stop in GOLD PRICE units. $ at 0.10 lots = price x 10. Spans fast -> slow.
SHAPES = [(0.3, 3.0), (0.5, 5.0), (0.7, 7.0), (1.0, 7.0), (1.5, 10.0),
          (2.0, 15.0), (3.0, 15.0), (5.0, 15.0), (10.0, 15.0), (20.0, 15.0)]
LOT_DIV = 10.0          # report at 0.01 lots (the sane size for a $350 account)


def main():
    b = L.bars(L.GOLD, "M1")
    idx = L.opportunity_idx(b, pd.Timestamp(L.SPANS["HIST"][0]), pd.Timestamp(L.LAST_OPP))
    t_opp = b.time.values[idx].astype("datetime64[ns]").astype(np.int64)
    print(f"{len(idx):,} entry chances, {b.time.iloc[0]} -> {b.time.iloc[-1]}", flush=True)

    rows = []
    for T, S in SHAPES:
        ck = os.path.join(PARTS, f"speed_{T}_{S}.csv")
        if os.path.exists(ck):
            rows.append(pd.read_csv(ck).iloc[0].to_dict()); print(f"  cached {T}/{S}", flush=True); continue

        pnl, texit = L.outcomes(b, idx, T, S)
        tp, sl = L.fillbar_masks(b, idx, T, S)
        pnl = L.fix_fillbar(pnl, tp, sl, T)

        ok = ~np.isnan(pnl).any(axis=0)
        p = pnl[:, ok]
        best, worst = p.max(axis=0), p.min(axis=0)
        xstar = -worst.mean() / (best.mean() - worst.mean())      # break-even accuracy

        # hold time of the side an average trade actually takes (both sides, finite only)
        hold = (texit - t_opp[None, :]) / 6e10                     # ns -> minutes
        hv = hold[np.isfinite(pnl) & (hold > 0)]

        r = dict(target_price=T, stop_price=S,
                 target_usd_010=T * 10, stop_usd_010=S * 10,
                 target_usd_001=T, stop_usd_001=S,              # at 0.01 lots $ == price units
                 median_hold_min=float(np.median(hv)), mean_hold_min=float(hv.mean()),
                 required_accuracy_pct=round(100 * xstar, 2))

        for X in (0.50, 0.55, 0.60):
            sides = L.skill_sides(pnl, X, seed=7)
            bk = L.engine(t_opp, pnl, texit, sides)
            d = L.day_stats(bk, balance=350.0 * LOT_DIV)         # scale balance so 0.01-lot maths is exact
            r[f"per_basket_{int(X*100)}"] = round(d["per_basket"] / LOT_DIV, 3)
            r[f"per_day_{int(X*100)}"] = round(d["avg_day"] / LOT_DIV, 2)
            r[f"baskets_per_day_{int(X*100)}"] = d["baskets_per_day"]
            if X == 0.50:
                r["basket_win_pct_50"] = d["basket_win_pct"]
        rows.append(r)
        pd.DataFrame([r]).to_csv(ck, index=False)
        print(f"  {T}/{S}: median hold {r['median_hold_min']:.0f} min, "
              f"{r['baskets_per_day_50']}/day, needs {r['required_accuracy_pct']}%", flush=True)

    df = pd.DataFrame(rows).sort_values("median_hold_min").reset_index(drop=True)
    df.to_csv(os.path.join(OUT, "speed_tradeoff.csv"), index=False)
    print("\n" + "=" * 100)
    print("THE MENU - sorted fastest first. Money is at 0.01 lots on a $350 account.")
    print("=" * 100)
    print(f"{'target':>7}{'stop':>7}{'median hold':>13}{'per day':>9}{'needs':>8}"
          f"{'$/day @50%':>12}{'$/day @55%':>12}{'$/day @60%':>12}")
    for _, r in df.iterrows():
        print(f"${r.target_usd_001:>6.1f}${r.stop_usd_001:>6.1f}{r.median_hold_min:>10.0f} min"
              f"{r.baskets_per_day_50:>9.1f}{r.required_accuracy_pct:>7.1f}%"
              f"{r.per_day_50:>+12.2f}{r.per_day_55:>+12.2f}{r.per_day_60:>+12.2f}")
    print("\nNote: long-hold rows cross overnight rollovers, which this simulator does NOT charge.")
    print("Gold costs money to hold overnight in both directions (BT9), so slow rows are optimistic.")


if __name__ == "__main__":
    main()
