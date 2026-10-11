"""BT17 addendum — how much spread erases the late-session result?

Not a new variant: a sensitivity measurement on chances already selected by the pre-registered ranking.

Why it is needed. The only cell in BT17 that is both large and above 50 in all three date periods is the
LATE session (20:00-24:00 UTC) of the fade-an-M1-break reading. That window is the thinnest-liquidity hour
of the gold day (after the New York close, across the daily rollover), where the real dealing spread is far
wider than during London. BT17 charged a FLAT 0.072 everywhere, because the `spread` column of both feeds is
0 on essentially every gold minute (project ERRORS #14 / #34) and cannot tell us the hourly shape. So the
late-session number is measured under a cost assumption that is known to be wrong in exactly that window.

This script re-runs those same chances at wider spreads and reports where the money reaches zero.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt17_spread
    cd smc_bot && SMC_DATASET=duka ../.venv/Scripts/python.exe -m bt.bt17_spread
"""
import os
import numpy as np
import pandas as pd
from bt import bt17_lib as B
from bt import bt15_lib as L
from bt.common import simulate, COMM

DELTAS = [0.072, 0.10, 0.15, 0.20, 0.30, 0.40]
VAR = "B1_bos_M1"          # its MIRROR (fade) is BT17's top-ranked reading
SESS_LATE = (20, 24)


def outcomes_at(b, idx, delta):
    n = len(idx)
    pnl = np.full((2, n), np.nan)
    for si, side in enumerate((1, -1)):
        o = L.basket_orders(b, idx, side, B.T_CAP, B.S_CAP, delta=delta)
        r = simulate(B.GOLD, o, one_at_a_time=False)
        f = r[r.status == "filled"]
        pnl[si, f.opp.values.astype(np.int64)] = f.usd_01.values - delta * B.USD_PER_PRICE
    tp, sl = L.fillbar_masks(b, idx, B.T_CAP, B.S_CAP, delta=delta)
    pay = B.T_CAP * B.USD_PER_PRICE - COMM[B.GOLD]
    return np.where(tp & ~sl & ~np.isnan(pnl), pay, pnl)


def main():
    COMM[B.GOLD] = 0.0
    b = B.bars(B.GOLD, "M1")
    t1 = b.time.values.astype("datetime64[ns]")
    nb = len(b)
    e, s, _k, _tf = B.signals(VAR)
    s = (-s).astype(np.int8)                           # the MIRROR: fade the break
    m = (e < nb - 3) & B.span_mask(t1[np.minimum(e, nb - 1)], *B.SPAN)
    e, s = e[m], s[m]
    h = pd.DatetimeIndex(t1[e]).hour.values
    late = (h >= SESS_LATE[0]) & (h < SESS_LATE[1])

    rows = []
    for label, sel in (("LATE 20-24 UTC", late), ("the other 20 hours", ~late), ("all hours", np.ones(len(e), bool))):
        ee, ss = e[sel], s[sel]
        for dl in DELTAS:
            p = outcomes_at(b, ee, dl)
            ok = np.isfinite(p).all(axis=0)
            pp, sd = p[:, ok], ss[ok]
            chosen = np.where(sd == 1, pp[0], pp[1])
            other = np.where(sd == 1, pp[1], pp[0])
            best, worst = pp.max(axis=0), pp.min(axis=0)
            acc = 100 * np.mean(np.where(chosen > other, 1.0, np.where(chosen < other, 0.0, 0.5)))
            be = 100 * (-worst.mean()) / (best.mean() - worst.mean())
            rows.append(dict(window=label, spread=dl, chances=int(ok.sum()), acc=round(acc, 2),
                             break_even=round(be, 2), money=round(float(chosen.mean()), 3)))
            print(f"  {label:<20} spread {dl:.3f}  {int(ok.sum()):>7,} chances  "
                  f"{acc:>6.2f} right in 100  break-even {be:>5.2f}  ${chosen.mean():+.3f}/basket", flush=True)
    d = pd.DataFrame(rows)
    f = os.path.join(B.OUT, f"spread_sensitivity_{B.DS}.csv")
    d.to_csv(f, index=False)
    print(f"\nwrote {f}")


if __name__ == "__main__":
    main()
