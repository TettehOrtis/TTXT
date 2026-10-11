"""BT20 run - every entry rule at every exit shape, with the robot's one-basket-at-a-time rule.

Replays the robot on each entry rule's own signals: one basket at a time, 60-second cooldown after a
close. The shape therefore changes the NUMBER of trades, not just the money per trade - a $5 stop frees
the robot in minutes, a $70 stop can hold it for hours. That throughput effect has never been measured.

Our costs ($1.10 round trip). Gross = price move in dollars; net = after commission.

    .venv/Scripts/python.exe smc_bot/bt/bt20_run.py
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars                                             # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402
from bt import bt20_lib as B                                           # noqa: E402

# bt.common fixes the dataset at import time, so ONE feed per process. Run both, then report.
# The recent years are split out on purpose: BT18's headline 52.55 is 73% pre-2021 and the same rule reads
# a coin flip on 2021-2023. A number pooled across all 16 years would hide exactly that.
DS = os.environ.get("SMC_DATASET", "zero")
SPANS = ([("2009-2021", "2009-03-15", "2021-01-01")] if DS == "duka" else
         [("2021-2025", "2021-01-01", L.LAST_OPP),
          ("2021-2023", "2021-01-01", "2024-01-01"),
          ("2024-2025", "2024-01-01", L.LAST_OPP)])


def engine(ebar, side, pos, up, dn, final, T, S):
    """One basket at a time, 60 s (= 1 bar) cooldown. Returns taken-trade arrays."""
    it, isd, igr, iex, ibar = [], [], [], [], []
    free = np.int64(-1)
    iT, iS = B.IX[T], B.IX[S]
    for i in range(len(ebar)):
        e = ebar[i]
        if e < free:
            continue
        j = pos[i]
        si = 0 if side[i] == 1 else 1
        fw, fl = up[si, j, iT], dn[si, j, iS]
        if fw < fl:
            off, gross = int(fw), float(T)
        elif fl < fw:
            off, gross = int(fl), float(-S)
        elif fl < B.NEVER:                             # SAME minute touched both: book the stop (project
            off, gross = int(fl), float(-S)            # convention is SL before TP inside a bar)
        else:                                          # neither barrier inside 24 h: book the floating value
            off, gross = B.HORIZON, float(final[si, j])
        it.append(i)
        isd.append(side[i])
        igr.append(gross)
        iex.append(off)
        ibar.append(e)
        free = e + off + 1
    return (np.array(it, np.int64), np.array(isd, np.int8), np.array(igr, float),
            np.array(iex, np.int64), np.array(ibar, np.int64))


def best_worst(up, dn, final, pos, T, S):
    """Net dollars for BOTH sides at each signal - needed for the entry's own break-even accuracy."""
    iT, iS = B.IX[T], B.IX[S]
    out = np.zeros((2, len(pos)))
    for si in range(2):
        fw, fl = up[si, pos, iT], dn[si, pos, iS]
        # same-minute ties go to the stop, as in engine(); only a genuine no-resolution uses the float
        g = np.where(fw < fl, T, np.where(fl < B.NEVER, -S, final[si, pos]))
        out[si] = g - B.COMM
    return out


def main():
    rows = []
    z = np.load(os.path.join(B.PARTS, f"walk_{DS}.npz"))
    union, up, dn, final = z["union"], z["up"], z["dn"], z["final"]
    sg = np.load(os.path.join(B.PARTS, f"sig_{DS}.npz"))
    tb = bars(B.GOLD, "M1").time.values.astype("datetime64[ns]")
    for ERA, a, b_ in SPANS:
        lo, hi = np.datetime64(pd.Timestamp(a)), np.datetime64(pd.Timestamp(b_))
        inspan = (tb >= lo) & (tb < hi)
        days = len(np.unique(tb[inspan].astype("datetime64[D]")))
        months = len(np.unique(tb[inspan].astype("datetime64[M]")))
        print(f"{ERA} ({DS}): {days:,} trading days, {months} months")
        for nm in B.VARIANTS:
            e, d = sg[f"{nm}__e"], sg[f"{nm}__d"]
            te = tb[np.minimum(e, len(tb) - 1)]
            keep = (e < len(tb)) & (te >= lo) & (te < hi)
            e, d = e[keep], d[keep]
            if len(e) == 0:
                continue
            o = np.argsort(e, kind="stable")
            e, d = e[o], d[o]
            pos = np.searchsorted(union, e)
            for (T, S) in B.SHAPES:
                it, isd, igr, iex, ibar = engine(e, d, pos, up, dn, final, T, S)
                if len(it) < 100:
                    continue
                net = igr - B.COMM
                bw = best_worst(up, dn, final, pos[it], T, S)
                better = np.where(bw[0] >= bw[1], 1, -1)
                acc = float((isd == better).mean())
                eb, ew = bw.max(axis=0).mean(), bw.min(axis=0).mean()
                xstar = 100 * (-ew) / (eb - ew) if eb > ew else np.nan
                sess = L.session_of(pd.to_datetime(tb[ibar]))
                rows.append(dict(era=ERA, variant=nm, target=T, stop=S, trades=len(it),
                                 per_day=round(len(it) / days, 2), per_month=round(len(it) / months, 1),
                                 win_pct=round(100 * float((igr > 0).mean()), 2),
                                 acc=round(100 * acc, 2), own_be=round(xstar, 2),
                                 beats_bar=round(100 * acc - xstar, 2),
                                 gross=round(float(igr.sum()), 0), net=round(float(net.sum()), 0),
                                 per_basket=round(float(net.mean()), 3),
                                 per_day_usd=round(float(net.sum()) / days, 2),
                                 per_month_usd=round(float(net.sum()) / months, 1),
                                 med_min=int(np.median(iex)),
                                 **{f"n_{s}": int((sess == s).sum()) for s, _, _ in L.SESSIONS},
                                 **{f"usd_{s}": round(float(net[sess == s].sum()), 0) for s, _, _ in L.SESSIONS}))
    df = pd.DataFrame(rows)
    os.makedirs(B.OUT, exist_ok=True)
    df.to_csv(os.path.join(B.OUT, f"grid_{DS}.csv"), index=False)
    print(f"\n{len(df)} rows -> grid_{DS}.csv")
    print("\nBest 20 by money per basket (both feeds):")
    cols = ["era", "variant", "target", "stop", "trades", "per_day", "acc", "own_be", "beats_bar",
            "per_basket", "per_day_usd", "med_min"]
    print(df.sort_values("per_basket", ascending=False).head(20)[cols].to_string(index=False))
    print("\nAny shape where the entry BEATS its own bar:")
    w = df[df.beats_bar > 0].sort_values("beats_bar", ascending=False)
    print(w[cols].head(25).to_string(index=False) if len(w) else "  none")


if __name__ == "__main__":
    main()
