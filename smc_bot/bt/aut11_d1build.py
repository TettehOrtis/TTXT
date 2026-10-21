"""AUT11 step 0: build D1 bars (22:00 UTC rollover) for 24 FX pairs + gold from M1.
Writes results/AUT11_trend/_d1/*.parquet
Day boundary: 22:00 UTC. A bar labelled `day` covers [day-1 22:00, day 22:00) UTC, i.e. the label is the date of the
rollover that CLOSES the bar. Why 22:00: that is the New York 17:00 close in northern summer (and 21:00 in winter, an
hour off; the feed is true UTC with no DST, so a fixed 22:00 is used and the one-hour winter slip is accepted). It
splits the Friday close / Sunday open weekend gap cleanly, so Monday's bar is not cut in half, and no bar starts in the
middle of the London or New York session."""
import os, sys, warnings
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import common as C
import autopsy_lib as A
warnings.simplefilter("ignore")
OUT = os.path.join(C.RESULTS, "AUT11_trend", "_d1")
os.makedirs(OUT, exist_ok=True)
FX = [s for s in C.PAIRS if s != "XAUUSD"]
SYMS = FX + ["XAUUSD"]
log = []
for s in SYMS:
    b = A.safe_bars(s, "M1")
    x = b[["time", "open", "high", "low", "close", "spr"]].copy()
    x["day"] = (x.time + pd.Timedelta(hours=2)).dt.normalize()
    g = x.groupby("day", sort=True)
    d = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(),
                      "spr": g.spr.median(), "n": g.open.count(), "t_first": g.time.first(), "t_last": g.time.last()})
    d = d.reset_index()
    nraw = len(d)
    d = d[d.n >= 300].reset_index(drop=True)
    d.to_parquet(os.path.join(OUT, s + ".parquet"), index=False)
    log.append((s, len(b), nraw, len(d), str(d.day.iloc[0]), str(d.day.iloc[-1]), float(d.spr.median())))
    print(log[-1], flush=True)
pd.DataFrame(log, columns=["sym", "m1_bars", "d1_raw", "d1_kept", "first", "last", "median_spr"]).to_csv(os.path.join(OUT, "_build_log.csv"), index=False)
