"""BT21 Family R — run every variant, every era, both signs, every exit shape.

Writes into results/BT21R/:
    main_<ds>.csv     the primary-shape reading per (variant, era, sign) + the luck bar
    grid_<ds>.csv     all 36 exit shapes per (variant, era, sign)
    twin_<ds>.csv     the LOOKAHEAD TWIN of every reading (fill at the signal bar's own open)
    diag_<ds>.csv     signal diagnostics (hours, broker break hour, weekend crossing, retest yield)
    placebo_<ds>.csv  the rotation bar (whole weeks, best-of-6 and best-of-12, 200 draws)
    trades_<variant>_<era>.csv   the taken baskets at the primary shape
    examples_<ds>.csv            10 chart-checkable trades per variant

    .venv/Scripts/python.exe smc_bot/bt/bt21r_run.py
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/bt21r_run.py
"""
import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars                                             # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402
from bt import bt18_lib as B18                                         # noqa: E402
from bt import bt20_lib as B                                           # noqa: E402
from bt import bt20_run as R20                                         # noqa: E402
from bt import bt21r_lib as R                                          # noqa: E402

DS = R.DS
SIGNS = [("follow", 1), ("fade", -1)]


def era_slice(t_entry, lo, hi):
    return (t_entry >= np.datetime64(pd.Timestamp(lo))) & (t_entry < np.datetime64(pd.Timestamp(hi)))


def main():
    b = bars(R.GOLD, "M1")
    tb = b.time.values.astype("datetime64[ns]")
    seg = R.segments(b)
    union, up, dn, final, _tu = R.load_walk()
    sigs = R.load_sigs()
    nb = len(b)

    rows, grid, twin, diag, examples = [], [], [], [], []
    placebo_rows = []

    for ERA, a, z in R.ERAS:
        m_era = (tb >= np.datetime64(pd.Timestamp(a))) & (tb < np.datetime64(pd.Timestamp(z)))
        days = len(np.unique(tb[m_era].astype("datetime64[D]")))
        months = len(np.unique(tb[m_era].astype("datetime64[M]")))
        print(f"\n=== {ERA} ({DS}): {days:,} trading days, {months} months ===")
        prep = []
        for nm in R.VARIANTS:
            s = sigs[nm]
            k = s["sig"].astype(np.int64)
            d0 = s["side"].astype(np.int8)
            e = k + 1
            ok = (e > 0) & (e < nb - B.HORIZON - 2)
            k, d0, e = k[ok], d0[ok], e[ok]
            te = tb[e]
            keep = era_slice(te, a, z)
            k, d0, e, te = k[keep], d0[keep], e[keep], te[keep]
            if len(e) == 0:
                continue
            o = np.argsort(e, kind="stable")
            k, d0, e, te = k[o], d0[o], e[o], te[o]
            pos = np.searchsorted(union, e)
            assert (union[pos] == e).all(), f"{nm}: entry bar missing from the walk table"
            pos_k = np.searchsorted(union, k)                     # the lookahead twin's row
            twin_ok = (union[np.minimum(pos_k, len(union) - 1)] == k)

            # ---- diagnostics (sign-independent)
            hr = pd.DatetimeIndex(te).hour.values
            brk_h = int(B18.break_mask(pd.DatetimeIndex(te)).sum())
            diag.append(dict(era=ERA, variant=nm, n_sig=len(e), per_day=round(len(e) / days, 3),
                             in_broker_break=brk_h,
                             in_break_pct=round(100 * brk_h / len(e), 2),
                             hours_2024=int(((hr >= 20) | (hr < 1)).sum()),
                             **{f"h{h:02d}": int((hr == h).sum()) for h in range(24)}))

            for sname, sgn in SIGNS:
                d = (d0 * sgn).astype(np.int8)
                rec = R.measure(e, d, pos, up, dn, final, *R.PRIMARY)
                rec2 = R.measure(e, d, pos, up, dn, final, *R.SHIPPED)
                rows.append(dict(era=ERA, variant=nm, sign=sname, days=days, months=months,
                                 per_day=round(len(e) / days, 3), per_month=round(len(e) / months, 2),
                                 **rec,
                                 s7_trades=rec2.get("trades", 0), s7_acc=rec2.get("acc"),
                                 s7_luck=rec2.get("luck_acc"), s7_per_basket=rec2.get("per_basket"),
                                 s7_net=rec2.get("net")))
                # ---- lookahead twin at the primary shape
                if twin_ok.all():
                    tw = R.measure(e, d, pos_k, up, dn, final, *R.PRIMARY)
                    twin.append(dict(era=ERA, variant=nm, sign=sname, **tw))
                # ---- all 36 shapes
                for (T, S) in R.SHAPES:
                    g = R.measure(e, d, pos, up, dn, final, T, S)
                    grid.append(dict(era=ERA, variant=nm, sign=sname, target=T, stop=S, **g))

            # ---- the placebo's raw material: both sides' net dollars at the primary shape
            bw = R20.best_worst(up, dn, final, pos, *R.PRIMARY)
            prep.append((nm, d0.copy(), bw[0].copy(), bw[1].copy(), len(e) / days))

            # ---- taken baskets at the primary shape, declared sign: CSV + examples
            it, isd, igr, iex, ibar = R20.engine(e, d0, pos, up, dn, final, *R.PRIMARY)
            if len(it):
                fill = np.where(isd == 1, b.open.values[ibar] + b.spr.values[ibar] + B.D,
                                b.open.values[ibar] - B.D)
                xbar = np.minimum(ibar + iex, nb - 1)
                T0, S0 = R.PRIMARY
                tr = pd.DataFrame(dict(
                    variant=nm, era=ERA, t_decide=tb[k[it]], t_entry=tb[ibar], side=isd,
                    entry=np.round(fill, 3),
                    tp_px=np.round(fill + isd * T0 / 10.0, 3), sl_px=np.round(fill - isd * S0 / 10.0, 3),
                    t_exit=tb[xbar], close_at_exit=np.round(b.close.values[xbar], 3),
                    hold_min=iex, gross_usd=np.round(igr, 2), net_usd=np.round(igr - B.COMM, 2),
                    outcome=np.where(igr > 0, "TP", np.where(igr <= -S0, "SL", "TIME")),
                    level=np.round(s["level"][ok][keep][o][it], 3) if "level" in s else np.nan,
                    rng_hi=np.round(s["rh"][ok][keep][o][it], 3) if "rh" in s else np.nan,
                    rng_lo=np.round(s["rl"][ok][keep][o][it], 3) if "rl" in s else np.nan,
                    t_break=tb[s["brk"][ok][keep][o][it]],
                    crosses_gap=(seg[xbar] != seg[ibar])))
                tr.to_csv(os.path.join(R.OUT, f"trades_{nm}_{ERA}_{DS}.csv"), index=False)
                # examples: spread evenly over the era so winners AND losers show up, not the first 10
                examples.append(tr.iloc[np.linspace(0, len(tr) - 1, min(10, len(tr))).astype(int)])
                print(f"  {nm:22s} {sname:6s} sig={len(e):7,d} taken={len(it):6,d} "
                      f"net/basket={float((igr - B.COMM).mean()):+.3f} "
                      f"gapcross={100*float((seg[xbar] != seg[ibar]).mean()):.2f}%")

        if prep:
            p6 = R.rotation_placebo(prep, draws=200, seed=21)
            placebo_rows.append(dict(era=ERA, kind="best_of_6", **{k: v for k, v in p6.items()
                                                                   if k != "per_reading"}))
            for nm, v in p6["per_reading"].items():
                placebo_rows.append(dict(era=ERA, kind=f"own_{nm}", n_readings=1, **v))

    def W(df, name):
        d = pd.DataFrame(df)
        d.to_csv(os.path.join(R.OUT, f"{name}_{DS}.csv"), index=False)
        print(f"  -> {name}_{DS}.csv  ({len(d)} rows)")
        return d

    os.makedirs(R.OUT, exist_ok=True)
    print()
    main_df = W(rows, "main")
    W(grid, "grid")
    W(twin, "twin")
    W(diag, "diag")
    W(placebo_rows, "placebo")
    if examples:
        pd.concat(examples, ignore_index=True).to_csv(
            os.path.join(R.OUT, f"examples_{DS}.csv"), index=False)

    cols = ["era", "variant", "sign", "n_sig", "trades", "acc", "luck_acc", "beats", "per_basket",
            "net", "coin_usd", "med_min"]
    print("\nPRIMARY SHAPE $15/$70 — every reading:")
    print(main_df[cols].to_string(index=False))


if __name__ == "__main__":
    main()
