"""BT16 follow-up. **POST-HOC: written AFTER seeing the results**, and labelled as such everywhere.

One cell of the 44 pre-registered readings cleared 52.9 in all three date periods:
    CPI_W2_1min_after_M_momentum30  -  57.5 pooled (55.9 / 63.3 / 58.8), 195 chances.
Before that is called anything, two things have to be ruled out:

 1. **It may not be news at all.** Our CPI dates showed NO volatility bump (0.9x-1.0x), i.e. they are mostly
    ordinary mid-month days. So test the SAME rule - continue gold's move over the last 30 one-minute bars,
    enter one minute later - at the SAME clock minute on NON-event days. Thousands of chances instead of 195.
 2. **It may be the rule, not the date.** So also test the same rule on the ordinary 30-minute entry grid.

If the rule works on ordinary days too, it is not a CPI effect. If it works nowhere else, 195 chances picked
out of 44 searched readings is exactly what the placebo bar (62-68 right in 100) says luck produces.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt16_followup
    cd smc_bot && SMC_DATASET=duka ../.venv/Scripts/python.exe -m bt.bt16_followup
"""
import os
import numpy as np
import pandas as pd

from bt import bt16_lib as B
from bt import bt15_lib as L


def main():
    B.setup()
    fd = B.feed()
    a, z = B.span()
    b = B.bars(B.GOLD, "M1")
    t1 = b.time.values.astype("datetime64[ns]")
    ev = B.news_times()
    rows = []

    for e in B.EVENTS:
        tt = ev.t[ev.event == e]
        tt = tt[(tt >= a) & (tt < z)]
        minute = int(pd.DatetimeIndex(tt).hour[0] * 60 + pd.DatetimeIndex(tt).minute[0])  # winter minute
        wanted = set(int(x) for x in np.unique(pd.DatetimeIndex(tt).hour * 60 + pd.DatetimeIndex(tt).minute))
        evdays = set(int(x) for x in pd.DatetimeIndex(tt).normalize().values.astype("int64"))
        mins = pd.DatetimeIndex(t1).hour.values * 60 + pd.DatetimeIndex(t1).minute.values
        # the entry minute of W2 is release + 1
        cand = np.where(np.isin(mins, [m + 1 for m in wanted]))[0]
        cand = cand[(t1[cand] >= np.datetime64(pd.Timestamp(a))) & (t1[cand] < np.datetime64(pd.Timestamp(z)))]
        day = pd.DatetimeIndex(t1[cand]).normalize().values.astype("int64")
        ctl = cand[~np.isin(day, list(evdays))]
        ctl = ctl[ctl > 40]
        for label, idx in (("CONTROL_non_event_days", ctl),):
            pnl, texit = L.outcomes(b, idx, B.T_PRICE, B.S_PRICE, delta=B.HIS_SPREAD)
            tp, sl = L.fillbar_masks(b, idx, B.T_PRICE, B.S_PRICE, delta=B.HIS_SPREAD)
            pnl = L.fix_fillbar(pnl, tp, sl, B.T_PRICE)
            to = b.time.values[idx].astype("datetime64[ns]")
            for rn, s in (("M_momentum30", B.momentum30(b, idx)),
                          ("L_always_long", np.ones(len(idx), np.int8))):
                sc = B.score(s, pnl, to)
                rows.append(dict(feed=fd, test=f"{e}_{label}_{rn}", clock_minute=minute, **sc))
                print(f"  {e} {label} {rn}: {sc['n']:,} chances, {sc['acc']} right in 100, "
                      f"${sc['money']:.3f}/basket", flush=True)

    # the same rule on the ordinary 30-minute grid (post-hoc reference)
    idx = L.opportunity_idx(b, pd.Timestamp(a), pd.Timestamp(z), B.STRIDE_MIN)
    pnl, texit, idx = B.outcome_table(b, idx, f"grid_{fd}")
    to = b.time.values[idx].astype("datetime64[ns]")
    s = B.momentum30(b, idx)
    sc = B.score(s, pnl, to)
    rows.append(dict(feed=fd, test="ANY_MINUTE_grid_M_momentum30", clock_minute=-1, **sc))
    print(f"  ordinary 30-minute grid: {sc['n']:,} chances, {sc['acc']} right in 100, "
          f"${sc['money']:.3f}/basket", flush=True)
    per = B.period_of(to)
    for p in ("HIST", "DEV", "VAL"):
        if (per == p).any():
            sc = B.score(s, pnl, to, per == p)
            rows.append(dict(feed=fd, test=f"ANY_MINUTE_grid_M_momentum30_{p}", clock_minute=-1, **sc))
            print(f"    {p}: {sc['n']:,} chances, {sc['acc']} right in 100", flush=True)

    f = os.path.join(B.PARTS, f"followup_{fd}.csv")
    pd.DataFrame(rows).to_csv(f, index=False)
    print("wrote", f)
    hour20(b, fd)


def hour20(b, fd):
    """POST-HOC, added after seeing the hour-of-day table. The ONE thing in BT16 that is above 52.9 in all
    three date periods with a real sample is 'be long at 20:00-21:00 UTC'. Before anyone gets excited, find out
    HOW those baskets resolve - because if they are held overnight, our unfinanced simulator is flattering them
    (BT9: gold swap is negative in both directions, and we charge none)."""
    a, z = B.span()
    idx = L.opportunity_idx(b, pd.Timestamp(a), pd.Timestamp(z), B.STRIDE_MIN)
    pnl, texit, idx = B.outcome_table(b, idx, f"grid_{fd}")
    to = b.time.values[idx].astype("datetime64[ns]")
    h = pd.DatetimeIndex(to).hour.values
    t_ns = to.astype(np.int64)
    hold = (texit - t_ns[None, :]) / 6e10
    cap = B.T_PRICE * B.USD_PER_PRICE
    rows = []
    for hh in range(24):
        m = h == hh
        lp, hl = pnl[0, m], hold[0, m]
        rows.append(dict(feed=fd, hour=hh, n=int(m.sum()), long_money=round(float(lp.mean()), 3),
                         median_hold_min=round(float(np.median(hl)), 0),
                         pct_hit_profit_cap=round(float((np.abs(lp - cap) < 0.3).mean() * 100), 1),
                         pct_still_open_after_60min=round(float((hl > 60).mean() * 100), 1),
                         pct_still_open_after_8h=round(float((hl > 480).mean() * 100), 1)))
    df = pd.DataFrame(rows)
    f = os.path.join(B.PARTS, f"hour20_{fd}.csv")
    df.to_csv(f, index=False)
    print(df[df.hour.isin([10, 15, 19, 20, 21, 22, 23])].to_string(index=False))
    print("wrote", f)
    preclose(b, fd, idx, pnl, to)
    rollover_gap(b, fd)
    crosscheck(b, fd)
    late_fade(b, fd)
    late_spread(b, fd)
    late_bands(b, fd)


def preclose(b, fd, idx, pnl, to):
    """POST-HOC. Hour 21 UTC only exists on the ~43% of days when gold is still open then (the daily close moves
    with US summer time), so 'hour 21' is not a clean hour - it is a slice of the year. The honest version of the
    same question is: how far is the entry from gold's DAILY CLOSE? Bucket by that instead of by the clock."""
    t1 = b.time.values.astype("datetime64[ns]").astype(np.int64)
    gap = np.zeros(len(t1), bool)
    gap[:-1] = np.diff(t1) > 45 * 60_000_000_000          # the daily session break (and weekends)
    last = np.where(gap | (np.arange(len(t1)) == len(t1) - 1))[0]
    nxt = last[np.searchsorted(last, idx, "left")]
    mins_to_close = (t1[nxt] - t1[idx]) / 6e10
    per = B.period_of(to)
    buckets = [(0, 30), (30, 60), (60, 120), (120, 240), (240, 480), (480, 1e9)]
    rows = []
    for lo, hi in buckets:
        m = (mins_to_close >= lo) & (mins_to_close < hi)
        s = np.ones(len(idx), np.int8)
        r = dict(feed=fd, bucket=f"{lo}-{hi if hi < 1e9 else 'inf'} min before the daily close")
        for p in ["ALL", "HIST", "DEV", "VAL"]:
            mm = m if p == "ALL" else (m & (per == p))
            sc = B.score(s, pnl, to, mm, fast=True)
            r[f"n_{p}"] = sc["n"]
            r[f"acc_{p}"] = None if sc["n_dec"] == 0 else round(sc["acc"], 2)
            r[f"money_{p}"] = None if sc["n"] == 0 else round(sc["money"], 3)
        rows.append(r)
    df = pd.DataFrame(rows)
    f = os.path.join(B.PARTS, f"preclose_{fd}.csv")
    df.to_csv(f, index=False)
    print(df.to_string(index=False))
    print("wrote", f)



def rollover_gap(b, fd):
    """POST-HOC. The pre-close long bias could simply be the gap across gold's DAILY ROLLOVER, which is
    exactly the moment a broker charges swap. Our simulator charges none (BT9: gold swap is negative both
    ways), so a systematic positive rollover gap would be money we are NOT allowed to count.
    Measures the gap: open of the first bar after the break minus close of the last bar before it."""
    t1 = b.time.values.astype("datetime64[ns]").astype(np.int64)
    d = np.diff(t1)
    k = np.where((d > 45 * 60_000_000_000) & (d < 8 * 3600 * 1_000_000_000))[0]   # daily break, not weekend
    g = b.open.values[k + 1] - b.close.values[k]
    yr = pd.DatetimeIndex(b.time.values[k]).year
    rows = [dict(feed=fd, scope="ALL", n=len(g), mean_gap_price=round(float(g.mean()), 4),
                 median_gap_price=round(float(np.median(g)), 4),
                 mean_gap_usd_010=round(float(g.mean()) * B.USD_PER_PRICE, 3),
                 pct_gap_up=round(float((g > 0).mean() * 100), 1))]
    for p, lo, hi in (("HIST", 2009, 2021), ("DEV", 2021, 2024), ("VAL", 2024, 2026)):
        m = (yr >= lo) & (yr < hi)
        if m.sum() > 20:
            rows.append(dict(feed=fd, scope=p, n=int(m.sum()), mean_gap_price=round(float(g[m].mean()), 4),
                             median_gap_price=round(float(np.median(g[m])), 4),
                             mean_gap_usd_010=round(float(g[m].mean()) * B.USD_PER_PRICE, 3),
                             pct_gap_up=round(float((g[m] > 0).mean() * 100), 1)))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(B.PARTS, f"rollover_{fd}.csv"), index=False)
    print(df.to_string(index=False))

def crosscheck(b, fd):
    """POST-HOC, and the decisive one. The pre-close long bias is much bigger on the Exness Zero feed than on
    the Dukascopy feed, and the Zero feed also shows a +0.66 price-unit jump across every daily rollover that
    Dukascopy does not show at all. So: measure the SAME pre-close window on the Dukascopy feed over the SAME
    recent years (2021-2024, where both feeds exist). If the bias is a market fact both feeds should see it.
    Dukascopy only - the zero feed has already been measured over these years."""
    if fd != "duka":
        return
    a, z = "2021-01-01", "2024-12-25"
    idx = L.opportunity_idx(b, pd.Timestamp(a), pd.Timestamp(z), B.STRIDE_MIN)
    pnl, texit, idx = B.outcome_table(b, idx, "grid_duka_recent")
    to = b.time.values[idx].astype("datetime64[ns]")
    print(f"\nCROSS-CHECK: duka feed, {a} -> {z}, {len(idx):,} chances")
    preclose(b, "duka_2021_2024", idx, pnl, to)


def late_fade(b, fd):
    """POST-HOC, the biggest thing BT16 turned up by accident. In the LAST HOURS of the gold day (20-23 UTC)
    the two FADE readings do far better than anywhere else: RSI-on-M1 scores 64.6 / 59.3 / 60.8 across the
    three periods and the range-position rule 54.3 / 56.2 / 54.6 on 18,000 chances. Both are above the 52.9
    break-even in all three periods, which nothing else in BT16 manages with a real sample.

    This runs the cross-feed check that decides whether it is real: the SAME readings, the SAME hours, on the
    OTHER feed over the SAME recent years (Dukascopy 2021-2024, where both feeds exist). Hour-selected and
    post-hoc either way, so it can only ever be a lead for a pre-registered test - but a lead that two
    independent feeds and three independent periods agree on is worth writing down properly."""
    spans = [("2009-01-01", "2021-01-01", "HIST"), ("2021-01-01", "2024-12-25", "2021-2024")]
    rows = []
    for a, z, lab in spans:
        if fd == "zero" and lab == "HIST":
            continue
        tag = f"grid_{fd}" if (fd == "zero" or lab == "HIST") else "grid_duka_recent"
        idx = L.opportunity_idx(b, pd.Timestamp(max(a, "2009-03-15")), pd.Timestamp(z), B.STRIDE_MIN)
        pnl, texit, idx = B.outcome_table(b, idx, tag)
        to = b.time.values[idx].astype("datetime64[ns]")
        sig = B.tf_signals(to.astype(np.int64))
        sig["REF_always_long"] = np.ones(len(idx), np.int8)
        hr = pd.DatetimeIndex(to).hour.values
        per = B.period_of(to)
        for nm in ("R4_rsi_M1", "R4_rsi_M5", "R3_rangepos_M1", "R3_rangepos_M5", "REF_always_long"):
            for p in ["ALL", "HIST", "DEV", "VAL"]:
                m = (hr >= 20) & (hr <= 23)
                if p != "ALL":
                    m = m & (per == p)
                sc = B.score(sig[nm], pnl, to, m, fast=True)
                if sc["n"] < 50:
                    continue
                rows.append(dict(feed=fd, window=lab, reading=nm, period=p, n=sc["n"],
                                 acc=round(sc["acc"], 2), money=round(sc["money"], 3)))
    df = pd.DataFrame(rows)
    f = os.path.join(B.PARTS, f"latefade_{fd}.csv")
    df.to_csv(f, index=False)
    print("\nLATE-SESSION FADE (hours 20-23 UTC), post-hoc cross-feed check")
    print(df.to_string(index=False))
    print("wrote", f)


def late_spread(b, fd):
    """POST-HOC and decisive for whether the late-session fade is tradeable at all.

    Everything in BT16 was priced with the user's MEASURED spread of 0.072 price units - but he measured that
    during normal hours. In the last hour before gold's daily close the real spread widens a lot. The fade
    readings make $0.4 to $3.5 a basket in that window, and the whole spread cost at 0.072 is only $0.72, so
    the result is extremely sensitive to it. This re-prices the same chances at 0.072 / 0.20 / 0.40 / 0.60
    price units of spread and shows what survives.

    IMPORTANT: a wider spread is applied exactly as bt15_lib does it - both barriers shift and the payout drops
    by the spread - so it is faithful about WHICH barrier gets hit, not just about the money."""
    a, z = B.span()
    idx = L.opportunity_idx(b, pd.Timestamp(a), pd.Timestamp(z), B.STRIDE_MIN)
    to_all = b.time.values[idx].astype("datetime64[ns]")
    hr = pd.DatetimeIndex(to_all).hour.values
    keep = (hr >= 20) & (hr <= 23)
    sub = idx[keep]
    to = to_all[keep]
    sig = B.tf_signals(to.astype(np.int64))
    per = B.period_of(to)
    rows = []
    for d in (0.072, 0.20, 0.40, 0.60):
        pnl, texit = L.outcomes(b, sub, B.T_PRICE, B.S_PRICE, delta=d)
        tp, sl = L.fillbar_masks(b, sub, B.T_PRICE, B.S_PRICE, delta=d)
        pnl = L.fix_fillbar(pnl, tp, sl, B.T_PRICE)
        for nm in ("R4_rsi_M1", "R3_rangepos_M1", "R3_rangepos_M5"):
            for p in ["ALL", "HIST", "DEV", "VAL"]:
                m = None if p == "ALL" else (per == p)
                sc = B.score(sig[nm], pnl, to, m, fast=True)
                if sc["n"] < 50:
                    continue
                rows.append(dict(feed=fd, spread_price=d, spread_usd_010=round(d * 10, 2), reading=nm,
                                 period=p, n=sc["n"], acc=round(sc["acc"], 2), money=round(sc["money"], 3)))
        print(f"  spread {d}: done", flush=True)
    df = pd.DataFrame(rows)
    f = os.path.join(B.PARTS, f"latespread_{fd}.csv")
    df.to_csv(f, index=False)
    print(df[df.period == "ALL"].to_string(index=False))
    print("wrote", f)


def late_bands(b, fd):
    """POST-HOC. The data's own spread column (broken on 95% of minutes, ERRORS #14/#34, but indicative where
    it is populated) says gold's recorded spread at 22:00-23:00 UTC averages 4-7x what it is at 08:00, while
    20:00-21:00 is actually BELOW the daytime average. So the late-session fade has to be split: the part in
    20-21 (normal spread) and the part in 22-23 (the rollover, where spread blows out). Only the first could
    ever be tradeable."""
    a, z = B.span()
    idx = L.opportunity_idx(b, pd.Timestamp(a), pd.Timestamp(z), B.STRIDE_MIN)
    to_all = b.time.values[idx].astype("datetime64[ns]")
    hr_all = pd.DatetimeIndex(to_all).hour.values
    rows = []
    for band, lo, hi in (("20-21 UTC (normal spread)", 20, 21), ("22-23 UTC (the rollover)", 22, 23)):
        keep = (hr_all >= lo) & (hr_all <= hi)
        sub, to = idx[keep], to_all[keep]
        sig = B.tf_signals(to.astype(np.int64))
        per = B.period_of(to)
        for d in (0.072, 0.20):
            pnl, texit = L.outcomes(b, sub, B.T_PRICE, B.S_PRICE, delta=d)
            tp, sl = L.fillbar_masks(b, sub, B.T_PRICE, B.S_PRICE, delta=d)
            pnl = L.fix_fillbar(pnl, tp, sl, B.T_PRICE)
            for nm in ("R4_rsi_M1", "R3_rangepos_M1", "R3_rangepos_M5"):
                for pp in ["ALL", "HIST", "DEV", "VAL"]:
                    m = None if pp == "ALL" else (per == pp)
                    sc = B.score(sig[nm], pnl, to, m, fast=True)
                    if sc["n"] < 50:
                        continue
                    rows.append(dict(feed=fd, band=band, spread_price=d, reading=nm, period=pp,
                                     n=sc["n"], acc=round(sc["acc"], 2), money=round(sc["money"], 3)))
    df = pd.DataFrame(rows)
    f = os.path.join(B.PARTS, f"latebands_{fd}.csv")
    df.to_csv(f, index=False)
    print(df[df.period == "ALL"].to_string(index=False))
    print("wrote", f)


if __name__ == "__main__":
    main()
