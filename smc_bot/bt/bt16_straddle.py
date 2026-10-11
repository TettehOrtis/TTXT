"""BT16 straddler test - does ERRORS #42 (the Exness Zero daily-rollover jump) explain the 6c lead?

POST-HOC, decisive. The 6c lead is "fade price extremes at 20:00-21:00 UTC". ERRORS #42 measured that the
Exness Zero M1 gold bars jump +0.66 of a gold price unit (+$6.61 per 0.10 lot) across every daily rollover,
61% of them upward, while Dukascopy shows +0.04 with a median of exactly 0.00. The lead's window is the hour
immediately before that rollover. So any basket STILL OPEN across the break collects a jump that does not
exist in real trading, and a fade rule that happens to be long more often in that hour would harvest it.

This splits the lead into baskets that CLOSE BEFORE the daily break and baskets that STRADDLE it, re-runs the
lead with every straddler removed, and does the same on the Dukascopy feed which has no jump.

The rollover minute is not assumed - it is measured from the data: every index k where the next M1 bar is
more than 45 minutes and less than 8 hours later (a daily session break, not a weekend). The break therefore
starts at the CLOSE of bar k (= t[k] + 1 min) and ends at t[k+1].

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt16_straddle
    cd smc_bot && SMC_DATASET=duka ../.venv/Scripts/python.exe -m bt.bt16_straddle
"""
import os
import numpy as np
import pandas as pd

from bt import bt16_lib as B
from bt import bt15_lib as L

READINGS = ["R4_rsi_M1", "R4_rsi_M5", "R3_rangepos_M1", "R3_rangepos_M5", "REF_always_long"]


def breaks(b):
    """Measured daily session breaks. Returns (start_ns, end_ns) arrays: start = close of the last bar before
    the break, end = open of the first bar after it."""
    t1 = b.time.values.astype("datetime64[ns]").astype(np.int64)
    d = np.diff(t1)
    k = np.where((d > 45 * 60_000_000_000) & (d < 8 * 3600 * 1_000_000_000))[0]
    return t1[k] + 60_000_000_000, t1[k + 1]


def main():
    B.setup()
    fd = B.feed()
    a, z = B.span()
    b = B.bars(B.GOLD, "M1")
    bs, be = breaks(b)

    # which clock minute does the break start at?
    st = pd.DatetimeIndex(bs.astype("datetime64[ns]"))
    cnt = pd.Series(st.strftime("%H:%M")).value_counts().head(6)
    print(f"\n{fd}: {len(bs):,} measured daily session breaks. Most common break START minute (UTC):")
    print(cnt.to_string())
    en = pd.DatetimeIndex(be.astype("datetime64[ns]"))
    print(f"{fd}: most common RESUME minute (UTC):")
    print(pd.Series(en.strftime("%H:%M")).value_counts().head(6).to_string())

    idx = L.opportunity_idx(b, pd.Timestamp(a), pd.Timestamp(z), B.STRIDE_MIN)
    pnl, texit, idx = B.outcome_table(b, idx, f"grid_{fd}")
    to = b.time.values[idx].astype("datetime64[ns]")
    t_ns = to.astype(np.int64)
    hr = pd.DatetimeIndex(to).hour.values
    per = B.period_of(to)
    sig = B.tf_signals(t_ns)
    sig["REF_always_long"] = np.ones(len(idx), np.int8)

    # the first break that starts at or after each entry
    j = np.searchsorted(bs, t_ns, "left")
    nxt = np.where(j < len(bs), bs[np.clip(j, 0, len(bs) - 1)], np.int64(2**62))
    # a basket straddles the break if its exit minute is at or after the break start
    straddle = np.vstack([texit[0] >= nxt, texit[1] >= nxt])

    rows = []
    for band, lo, hi in (("20-21 UTC (the lead)", 20, 21), ("hour 20 only", 20, 20), ("hour 21 only", 21, 21)):
        base = (hr >= lo) & (hr <= hi)
        for nm in READINGS:
            s = sig[nm]
            si = np.where(s == 1, 0, 1)
            st_sel = straddle[si, np.arange(len(idx))]       # straddle flag for the side actually taken
            for grp, gm in (("ALL", np.ones(len(idx), bool)),
                            ("closes BEFORE rollover", ~st_sel),
                            ("STRADDLES rollover", st_sel)):
                for p in ["ALL", "HIST", "DEV", "VAL"]:
                    m = base & gm & (np.ones(len(idx), bool) if p == "ALL" else (per == p))
                    sc = B.score(s, pnl, to, m, fast=True)
                    if sc["n"] < 25:
                        continue
                    rows.append(dict(feed=fd, band=band, reading=nm, group=grp, period=p, n=sc["n"],
                                     acc=round(sc["acc"], 2), money=round(sc["money"], 3)))
    df = pd.DataFrame(rows)
    f = os.path.join(B.PARTS, f"straddle_{fd}.csv")
    df.to_csv(f, index=False)

    # the headline: straddler share and the lead with straddlers removed
    lead = df[df.band == "20-21 UTC (the lead)"]
    print(f"\n{fd}: STRADDLER SHARE in the lead window, per reading")
    for nm in READINGS:
        tot = lead[(lead.reading == nm) & (lead.group == "ALL") & (lead.period == "ALL")]
        sd = lead[(lead.reading == nm) & (lead.group == "STRADDLES rollover") & (lead.period == "ALL")]
        if len(tot) and len(sd):
            print(f"  {nm:16s} {int(sd.n.iloc[0]):>6,} of {int(tot.n.iloc[0]):>6,} baskets straddle "
                  f"({100*sd.n.iloc[0]/tot.n.iloc[0]:>5.1f}%)")
    print(f"\n{fd}: the lead split (period ALL and per period)")
    print(lead[lead.reading.isin(["R4_rsi_M1", "R3_rangepos_M1", "R3_rangepos_M5", "REF_always_long"])]
          .to_string(index=False))
    print("wrote", f)


if __name__ == "__main__":
    main()
