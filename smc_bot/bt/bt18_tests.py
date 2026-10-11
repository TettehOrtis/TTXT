"""BT18 blocking checks. Run before trusting any BT18 number.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt18_tests
    cd smc_bot && SMC_DATASET=duka ../.venv/Scripts/python.exe -m bt.bt18_tests

1  every reading's last input bar closes before the entry bar opens, and the entry bar is the very next minute
2  the rolling spike threshold is built from a window ENDING AT BAR k-1 (never from bar k itself)
3  the deliberate LOOKAHEAD TWIN (fill at the open of the signal's own bar) shows a large fake edge while the
   k+1 version does not -- this is the BT15 bug that manufactured "+$2.17 a basket, t +19.5"
4  mirrors are exact complements: accuracy(mirror) == 100 - accuracy(real)
5  random sides at the same minutes measure about 50 right in 100 and lose the coin-flip amount
6  TEST (2025-07-01 onward) is NaN in the outcome table -- never simulated, never readable
7  the first-5-minute-candle entry bar really is five minutes after the candle's own open
"""
import numpy as np
import pandas as pd
from bt import bt18_lib as B

FAIL = []


def check(ok, msg):
    print(("  PASS  " if ok else "  FAIL  ") + msg, flush=True)
    if not ok:
        FAIL.append(msg)


def main():
    print(f"BT18 tests, feed={B.DS}", flush=True)
    pnl, _ = B.outcome_table()
    m1 = B.bars(B.GOLD, "M1")
    t = m1.time.values.astype("datetime64[ns]")
    t_ns = t.astype(np.int64)
    nb = len(m1)

    # ---- 1 causality: entry bar is exactly the bar after the last input bar
    bad = []
    for name in B.VAR_NAMES:
        e, s, k = B.signals(name, m1)
        if len(e) == 0:
            continue
        if not (e == k + 1).all():
            bad.append(name)
        if (s == 0).any():
            bad.append(name + " (zero side)")
    check(not bad, f"all 25 readings fill at bar k+1, last input bar is k  ({bad})")

    # ---- 2 the spike threshold never sees its own bar
    thr, mv = B._thresh_cache(m1, 1440)
    i = B.A_PCTS.index(99.9)
    a = np.abs(mv.astype(np.float64))
    rng = np.random.default_rng(0)
    pick = rng.choice(np.where(np.isfinite(thr[i]))[0], 25, replace=False)
    err = 0.0
    for k in pick:
        want = np.quantile(a[k - 1440:k], 0.999)        # the 1440 bars ENDING at k-1
        err = max(err, abs(want - thr[i][k]))
    check(err < 1e-5, f"rolling threshold = quantile of the 1440 bars ending at k-1 (max error {err:.2e})")
    check(bool(np.isnan(thr[i][:1440]).all()), "no threshold before the window is full")

    # ---- 3 the lookahead twin
    e, s, k = B.signals("A_spikefade_p99.9_w1440", m1)
    m = (e < nb - 3) & B.span_mask(t[np.minimum(e, nb - 1)], *B.SPAN)
    e, s, k = e[m], s[m], k[m]
    honest = B.score(pnl, e, s)
    cheat = B.score(pnl, k, s)                          # fill at the OPEN of the signal's own bar
    print(f"        honest (k+1): {honest['acc']:.2f} right in 100, ${honest['money']:+.3f}/basket")
    print(f"        LOOKAHEAD (k): {cheat['acc']:.2f} right in 100, ${cheat['money']:+.3f}/basket")
    # the fake edge here is NEGATIVE by construction: filling at the open of the spike bar itself puts the
    # faded side in front of the spike, so the cheat is wrong almost every time. Either sign is a huge fake
    # edge -- a rule that picks the right way 1.65 times in 100 is just its mirror at 98.35.
    check(abs(cheat["acc"] - 50) > 3.0, "the lookahead twin manufactures a large fake edge (either sign)")
    check(abs(cheat["acc"] - 50) > 5 * abs(honest["acc"] - 50), "the honest version shows nothing like it")

    # ---- 4 mirrors are exact complements
    worst = 0.0
    for name in ("A_spikefade_p99.0_w1440", "B_mom30min", "C_first5m_bothopens"):
        e, s, k = B.signals(name, m1)
        m = (e < nb - 3) & B.span_mask(t[np.minimum(e, nb - 1)], *B.SPAN)
        e, s = e[m], s[m]
        if len(e) < 100:
            continue
        r = B.score(pnl, e, s)
        q = B.score(pnl, e, (-s).astype(np.int8))
        worst = max(worst, abs(r["acc"] + q["acc"] - 100.0))
    check(worst < 1e-9, f"mirror accuracy == 100 - real accuracy (worst gap {worst:.2e})")

    # ---- 5 random sides
    e, s, k = B.signals("A_spikefade_p99.0_w1440", m1)
    m = (e < nb - 3) & B.span_mask(t[np.minimum(e, nb - 1)], *B.SPAN)
    e = e[m]
    rs = np.where(np.random.default_rng(7).random(len(e)) < 0.5, 1, -1).astype(np.int8)
    r = B.score(pnl, e, rs)
    print(f"        random sides: {r['acc']:.2f} right in 100, ${r['money']:+.3f}/basket on {r['n']:,} chances")
    check(abs(r["acc"] - 50) < 1.0, "random sides land within one point of 50")
    check(r["money"] < 0, "random sides lose money at this shape")

    # ---- 6 TEST locked
    tm = t >= np.datetime64(pd.Timestamp("2025-07-01"))
    check(int(np.isfinite(pnl[:, tm]).sum()) == 0, "TEST period is NaN in the outcome table")

    # ---- 7 first-candle geometry
    e, s, k = B.signals("C_first5m_london0700", m1)
    if len(e):
        gap = (t_ns[e] - t_ns[k - 4]) == 5 * 60_000_000_000
        hh = pd.DatetimeIndex(t[k - 4]).hour.values
        mm = pd.DatetimeIndex(t[k - 4]).minute.values
        check(bool(gap.all()), "first-candle entry bar is exactly 5 minutes after the candle's open")
        check(bool(((hh == 7) & (mm == 0)).all()), "the London candle really starts at 07:00 UTC")

    # ---- 8 the broker break really is missing from the feed
    brk = B.break_mask(m1.time)
    h = m1.time.dt.hour.values
    summer = B.eu_dst(m1.time)
    cnt = lambda msk: int(msk.sum())                                   # noqa: E731
    ref_s, ref_w = cnt(summer & (h == 20)), cnt((~summer) & (h == 20))
    brk_s, brk_w = cnt(summer & (h == 21)), cnt((~summer) & (h == 22))
    print(f"        minutes present, summer: hour 20 = {ref_s:,}, break hour 21 = {brk_s:,} "
          f"({100*brk_s/max(1, ref_s):.0f}% of a normal hour)")
    print(f"        minutes present, winter: hour 20 = {ref_w:,}, break hour 22 = {brk_w:,} "
          f"({100*brk_w/max(1, ref_w):.0f}% of a normal hour)")
    check(brk_s < 0.5 * max(1, ref_s) and brk_w < 0.5 * max(1, ref_w),
          "the feed itself is nearly empty inside the broker break, which is what the break mask says")
    print(f"        break mask covers {100*brk.mean():.2f}% of the minutes that do exist")

    print(("\nALL CHECKS PASSED" if not FAIL else f"\n{len(FAIL)} CHECK(S) FAILED: {FAIL}"), flush=True)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
