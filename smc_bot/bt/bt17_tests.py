"""BT17 blocking checks. Run before any result is believed.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt17_tests

1. CAUSALITY — every variant's signal bar must close at or before its fill bar's open, and the fill bar must be
   the FIRST M1 bar at or after that close (the k+1 rule).
2. LOOKAHEAD TWIN — the same variant filled at the open of the signal's OWN bar must show a large fake edge.
   This reproduces and isolates the BT15 bug ("+$2.17 a basket, t +19.5") and proves the k+1 version is clean.
3. COIN FLIP — random sides at the SAME signal minutes must measure about 50% accuracy and the coin-flip money,
   so neither the minute selection nor the scoring code can manufacture an edge by itself.
4. MIRROR — the mirror of every variant must be exactly 100 - accuracy on the same trigger set.
5. BREAK-EVEN — the all-minutes break-even must land on BT15's 52.9 for this shape.
"""
import sys
import numpy as np
from bt import bt17_lib as B

FAIL = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)
    if not cond:
        FAIL.append(name)


def main():
    pnl, _ = B.outcome_table()
    m1 = B.bars(B.GOLD, "M1")
    t1 = m1.time.values.astype("datetime64[ns]")
    t1i = t1.astype(np.int64)
    nb = len(m1)
    print(f"\nBT17 checks, feed={B.DS}\n")

    # ---- 1 causality
    print("1. causality (signal bar closes at or before the fill bar's open, and it is the next bar)")
    for name, _fam, tf, _d in B.VARIANTS:
        e, s, k, tfn = B.signals(name)
        b = B.bars(B.GOLD, tfn)
        m = e < nb
        e, k = e[m], k[m]
        tc = b.tclose.values[k].astype("datetime64[ns]")
        ok1 = bool((tc <= t1[e]).all())                       # signal closed before the fill opened
        prev = np.maximum(e - 1, 0)
        ok2 = bool((t1[prev] < tc).all())                     # and the fill bar is the FIRST such bar
        ck(name, ok1 and ok2, f"{len(e):,} chances, worst gap {np.max((t1[e]-tc)/np.timedelta64(1,'m')):.0f} min")

    # ---- 2 lookahead twin
    print("\n2. lookahead twin: filling at the open of the signal's OWN bar must look fake-good")
    for name in ("S1_sweep_fade_M1", "B1_bos_M1"):
        e, s, k, tfn = B.signals(name)
        b = B.bars(B.GOLD, tfn)
        good = e[(e < nb - 3)]
        gs = s[(e < nb - 3)]
        bad_t = b.time.values[k[(e < nb - 3)]].astype("datetime64[ns]")   # the signal bar's OWN open
        bad = np.searchsorted(t1, bad_t, "left")
        mk = B.span_mask(t1[np.minimum(bad, nb - 1)], *B.SPAN) & (bad < nb - 3)
        sc_good = B.score(pnl, good[mk], gs[mk])
        sc_bad = B.score(pnl, bad[mk], gs[mk])
        ck(f"{name} twin", sc_bad["acc"] - sc_good["acc"] > 2.0,
           f"own-bar fill {sc_bad['acc']:.2f}% vs k+1 fill {sc_good['acc']:.2f}% "
           f"(${sc_bad['money']:+.2f} vs ${sc_good['money']:+.2f} per basket)")

    # ---- 3 coin flip at the same minutes
    print("\n3. random sides at the same minutes must be ~50% and lose the coin-flip amount")
    rng = np.random.default_rng(17)
    for name in ("S1_sweep_fade_M1", "F1_fvg_fill_M1", "B3_bos_M5"):
        e, s, _k, _tf = B.signals(name)
        m = (e < nb - 3) & B.span_mask(t1[np.minimum(e, nb - 1)], *B.SPAN)
        e = e[m]
        r = rng.choice([1, -1], size=len(e)).astype(np.int8)
        sc = B.score(pnl, e, r)
        se = 50 / np.sqrt(sc["n"])
        ck(f"{name} coinflip", abs(sc["acc"] - 50) < 4 * se,
           f"{sc['acc']:.2f}% (+/-{se:.2f}), ${sc['money']:+.3f} per basket at 0.10 lots")

    # ---- 4 mirror is exactly the complement
    print("\n4. the mirror of a variant is exactly 100 - accuracy")
    worst = 0.0
    for name, _f, _tf, _d in B.VARIANTS:
        e, s, _k, _t = B.signals(name)
        m = (e < nb - 3) & B.span_mask(t1[np.minimum(e, nb - 1)], *B.SPAN)
        e, s = e[m], s[m]
        a, bm = B.score(pnl, e, s), B.score(pnl, e, (-s).astype(np.int8))
        worst = max(worst, abs(a["acc"] + bm["acc"] - 100))
    ck("mirror complement", worst < 1e-6, f"worst deviation {worst:.2e} points")

    # ---- 5 break-even
    print("\n5. the break-even for this money shape")
    base = np.where(np.isfinite(pnl).all(axis=0))[0]
    p = pnl[:, base].astype(np.float64)
    best, w = p.max(axis=0), p.min(axis=0)
    be = 100 * (-w.mean()) / (best.mean() - w.mean())
    ck("break-even 52.9", abs(be - 52.9) < 0.5, f"measured {be:.2f}% on {len(base):,} minutes")

    # ---- 6 TEST is not in the table
    print("\n6. TEST (2025-07-01+) must be absent from the outcome table")
    late = t1 >= np.datetime64("2025-07-01")
    ck("test locked", (not late.any()) or bool(np.isnan(pnl[:, late]).all()),
       f"{int(late.sum()):,} test minutes on disk, all NaN in the table")

    print(f"\n{'ALL CHECKS PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
