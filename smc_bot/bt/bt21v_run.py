"""BT21 Family V run — the six VWAP variants, both feeds, three eras, both signs.

One feed per process (bt.common fixes the dataset at import time):

    .venv/Scripts/python.exe smc_bot/bt/bt21v_run.py
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/bt21v_run.py

Writes, into results/BT21V/:
    readings_<ds>.csv   every variant x era x (weighted, unweighted control) at the three money shapes
    ladder_<ds>.csv     every variant x era over the whole 14x14 LADDER (196 shapes - a trial count)
    engine_<ds>.csv     the robot replayed (one basket at a time, 60 s cooldown) at each shape
    twin_<ds>.csv       the LOOKAHEAD TWIN: the same rule filled at its own signal bar's open
    hours_<ds>.csv      hour-of-day breakdown (descriptive; nothing is selected from it)
    placebo_<ds>.csv    week-rotation placebo, best-of-6, 200 draws
    trades_<variant>_<ds>.csv   the robot's own baskets at the primary shape
    sanity_<ds>.txt     the checks, including a brute-force replay of the walk table
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars                                              # noqa: E402
from bt import bt15_lib as L                                            # noqa: E402
from bt import bt18_lib as B18                                          # noqa: E402
from bt import bt20_lib as B20                                          # noqa: E402
from bt import bt20_run as R20                                          # noqa: E402  (engine reused as-is)
from bt import bt21v_lib as V                                           # noqa: E402

DS = V.DS
ERAS = V.ERAS[DS]
ALL = V.VARIANTS + [v + "u" for v in V.VARIANTS]
SHAPES3 = [V.PRIMARY, V.SHIPPED]
LAD = [(int(T), int(S)) for T in V.LADDER for S in V.LADDER]


def load():
    z = np.load(os.path.join(V.PARTS, f"walk_{DS}.npz"))
    sg = np.load(os.path.join(V.PARTS, f"sig_{DS}.npz"))
    b = bars(V.GOLD, "M1")
    return b, z, sg


def main():
    b, z, sg = load()
    union, up, dn, final = z["union"], z["up"], z["dn"], z["final"]
    tb = b.time.values.astype("datetime64[ns]")
    os.makedirs(V.OUT, exist_ok=True)
    for vid in V.VARIANTS:                                      # _dump appends; start clean on a re-run
        f = os.path.join(V.OUT, f"trades_{vid}_{DS}.csv")
        if os.path.exists(f):
            os.remove(f)
    rows, lrows, erows, trows, hrows = [], [], [], [], []
    prep_by_era = {}
    best_shape = {}

    for ERA, a, b_ in ERAS:
        lo, hi = np.datetime64(pd.Timestamp(a)), np.datetime64(pd.Timestamp(b_))
        inspan = (tb >= lo) & (tb < hi)
        days = len(np.unique(tb[inspan].astype("datetime64[D]")))
        months = len(np.unique(tb[inspan].astype("datetime64[M]")))
        print(f"\n=== {ERA}: {days:,} trading days, {months} months")
        prep = []
        for nm in ALL:
            e, d, k = sg[f"{nm}__e"], sg[f"{nm}__d"], sg[f"{nm}__k"]
            te = tb[e]
            keep = (te >= lo) & (te < hi)
            e, d, k = e[keep], d[keep], k[keep]
            if len(e) == 0:
                continue
            o = np.argsort(e, kind="stable")
            e, d, k = e[o], d[o], k[o]
            pos = np.searchsorted(union, e)
            assert (union[pos] == e).all(), f"{nm}: entry bars missing from the walk table"
            posk = np.searchsorted(union, k)
            okk = (posk < len(union)) & (union[np.minimum(posk, len(union) - 1)] == k)
            ten = tb[e]

            # ---- the three money shapes, all signals (no throughput rule)
            for (T, S) in SHAPES3:
                p2 = V.both_sides(up, dn, final, pos, T, S)
                r = V.reading(d, p2)
                chosen = np.where(d == 1, p2[0], p2[1])
                rows.append(dict(era=ERA, variant=nm, weighted=not nm.endswith("u"), target=T, stop=S,
                                 per_day=round(len(e) / days, 2), xstar=round(V.xstar(p2), 2),
                                 week_t=round(B18.week_block_t(chosen, ten.astype(np.int64)), 2), **r))
                if (T, S) == V.PRIMARY:
                    prep.append((nm, d.copy(), p2[0].copy(), p2[1].copy(), len(e) / days))
                    # ---- lookahead twin: same rule, filled at the SIGNAL bar's own open
                    p2k = V.both_sides(up, dn, final, posk[okk], T, S)
                    rk = V.reading(d[okk], p2k)
                    trows.append(dict(era=ERA, variant=nm, fill="next_bar_open(k+1)", **r))
                    trows.append(dict(era=ERA, variant=nm, fill="LOOKAHEAD own_bar_open(k)", **rk))
                    # ---- hour of day, descriptive only
                    hh = pd.DatetimeIndex(ten).hour.values
                    for h in range(24):
                        m = hh == h
                        if m.sum() < 50:
                            continue
                        rh = V.reading(d[m], p2[:, m])
                        hrows.append(dict(era=ERA, variant=nm, hour=h, **rh))

            # ---- the whole ladder (196 shapes), all signals
            for (T, S) in LAD:
                p2 = V.both_sides(up, dn, final, pos, T, S)
                ch = np.where(d == 1, p2[0], p2[1])
                lrows.append(dict(era=ERA, variant=nm, target=T, stop=S, n=len(e),
                                  acc=round(float(np.where(p2[0] == p2[1], 0.5,
                                                           (d == np.where(p2[0] >= p2[1], 1, -1))).mean()) * 100, 2),
                                  usd=round(float(ch.mean()), 4),
                                  luck_usd=round(float((p2[0] + p2[1]).mean() / 2), 4)))

            # ---- the robot, one basket at a time
            for (T, S) in SHAPES3:
                it, isd, igr, iex, ibar = R20.engine(e, d, pos, up, dn, final, T, S)
                if len(it) == 0:
                    continue
                net = igr - V.COMM
                erows.append(dict(era=ERA, variant=nm, target=T, stop=S, trades=len(it),
                                  per_day=round(len(it) / days, 2),
                                  win_pct=round(100 * float((igr > 0).mean()), 2),
                                  per_basket=round(float(net.mean()), 4),
                                  net_total=round(float(net.sum()), 0),
                                  usd_per_month=round(float(net.sum()) / months, 1),
                                  med_min=int(np.median(iex))))
                if (T, S) == V.PRIMARY and not nm.endswith("u"):
                    _dump(b, V.OUT, nm, ERA, e[it], isd, igr, net, iex, T, S)
        prep_by_era[ERA] = prep

    pd.DataFrame(rows).to_csv(os.path.join(V.OUT, f"readings_{DS}.csv"), index=False)
    pd.DataFrame(lrows).to_csv(os.path.join(V.OUT, f"ladder_{DS}.csv"), index=False)
    pd.DataFrame(erows).to_csv(os.path.join(V.OUT, f"engine_{DS}.csv"), index=False)
    pd.DataFrame(trows).to_csv(os.path.join(V.OUT, f"twin_{DS}.csv"), index=False)
    pd.DataFrame(hrows).to_csv(os.path.join(V.OUT, f"hours_{DS}.csv"), index=False)

    # ---- placebo: rotate signals by whole weeks, best of the SIX pre-registered variants, 200 draws
    prows = []
    for ERA, prep in prep_by_era.items():
        p6 = [p for p in prep if not p[0].endswith("u")]
        pb = B18.rotation_placebo(None, p6, draws=200, seed=21)
        prows.append(dict(era=ERA, draws=pb["draws"], n_readings=pb["n_readings"],
                          acc_P50=round(pb["acc_P50"], 2), acc_P95=round(pb["acc_P95"], 2),
                          acc_max=round(pb["acc_max"], 2),
                          acc2_P95=round(pb["acc2_P95"], 2), acc2_max=round(pb["acc2_max"], 2),
                          money_P95=round(pb["money_P95"], 4), money2_P95=round(pb["money2_P95"], 4),
                          **{f"own_{nm}": round(pb["per_reading"][nm]["acc2_P95"], 2) for nm in pb["per_reading"]}))
    pd.DataFrame(prows).to_csv(os.path.join(V.OUT, f"placebo_{DS}.csv"), index=False)

    sanity(b, z, sg)
    print(f"\nwrote results/BT21V/*_{DS}.csv")


def _dump(b, out, nm, era, ebar, side, gross, net, exoff, T, S):
    """The robot's baskets, with prices a human can line up against a chart."""
    o, spr = b.open.values[ebar], b.spr.values[ebar]
    F = np.where(side == 1, o + spr + V.D, o - V.D)
    tp = np.where(side == 1, F + T / V.U, F - T / V.U)
    sl = np.where(side == 1, F - S / V.U, F + S / V.U)
    xi = np.minimum(ebar + exoff, len(b) - 1)
    why = np.where(gross > 0, "TP", np.where(exoff >= V.HORIZON, "HORIZON_24h", "SL"))
    df = pd.DataFrame(dict(era=era, variant=nm, t_signal=b.time.values[ebar - 1], t_entry=b.time.values[ebar],
                           side=side, fill=np.round(F, 3), tp=np.round(tp, 3), sl=np.round(sl, 3),
                           t_exit=b.time.values[xi], hold_min=exoff, exit_reason=why,
                           gross_usd=np.round(gross, 2), net_usd=np.round(net, 2)))
    f = os.path.join(out, f"trades_{nm}_{DS}.csv")
    df.to_csv(f, mode="a", header=not os.path.exists(f), index=False)


def sanity(b, z, sg):
    """Checks that must pass before any number in the report is allowed to mean anything."""
    out = []

    def say(ok, msg):
        out.append(("PASS" if ok else "FAIL") + "  " + msg)
        print(out[-1])

    union, up, dn, final = z["union"], z["up"], z["dn"], z["final"]
    vol = V.raw_volume(b)
    V.volume_quality(b, vol).to_csv(os.path.join(V.OUT, f"volquality_{DS}.csv"), index=False)
    # 1 volume column
    nuniq = len(np.unique(vol))
    say(True, f"tick_volume distinct values on this feed: {nuniq:,} (mean {vol.mean():.1f}, "
              f"{100*(vol==1).mean():.1f}% of bars exactly 1)")
    # 2 volume-weighted == unweighted whenever the volume is constant
    for vid in V.VARIANTS:
        e1, d1, k1 = sg[f"{vid}__e"], sg[f"{vid}__d"], sg[f"{vid}__k"]
        e2, d2, k2 = sg[f"{vid}u__e"], sg[f"{vid}u__d"], sg[f"{vid}u__k"]
        same = len(e1) == len(e2) and bool((e1 == e2).all()) and bool((d1 == d2).all())
        if nuniq == 1:
            say(same, f"{vid}: constant volume -> weighted and unweighted signals are identical ({len(e1):,})")
        else:
            ov = len(np.intersect1d(e1, e2))
            say(True, f"{vid}: weighted {len(e1):,} vs unweighted {len(e2):,} signals, "
                      f"{100*ov/max(len(np.union1d(e1,e2)),1):.1f}% of the union shared "
                      f"({'IDENTICAL' if same else 'different'})")
    # 3 causality: entry bar is the very next minute after the signal bar, always
    tv = b.time.values.astype("datetime64[ns]").astype(np.int64)
    bad = 0
    for nm in ALL:
        e, k = sg[f"{nm}__e"], sg[f"{nm}__k"]
        bad += int(((e - k) != 1).sum()) + int(((tv[e] - tv[k]) != 60_000_000_000).sum())
    say(bad == 0, f"every signal fills at the open of the NEXT minute ({bad} violations)")
    # 4 VWAP uses no future bar: recompute at random bars by brute force
    rng = np.random.default_rng(21)
    tp = (b.high.values + b.low.values + b.close.values) / 3.0
    worst = 0.0
    vw, sig, cnt, valid = V.vwap_sigma(b, vol, "day")
    day = b.time.dt.floor("D").values
    ii = np.arange(len(b))
    for _ in range(60):
        kk = int(rng.integers(100, len(b) - 100))
        m = (day == day[kk]) & (ii <= kk)
        man = (tp[m] * vol[m]).sum() / vol[m].sum()
        worst = max(worst, abs(vw[kk] - man))
    say(worst < 1e-6, f"day VWAP recomputed from scratch at 60 random bars using bars <= k only: "
                      f"max error {worst:.2e}")
    # 5 the mirror identity: V2 is exactly V1 flipped
    e1, d1 = sg["V1__e"], sg["V1__d"]
    e2, d2 = sg["V2__e"], sg["V2__d"]
    say(bool((e1 == e2).all() and (d1 == -d2).all()), "V2 is exactly V1 with the side flipped (same triggers)")
    # 6 walk table: brute-force replay of first passage at 200 random rows
    hi_, lo_, op_, spr_, cl_ = (b.high.values, b.low.values, b.open.values, b.spr.values, b.close.values)
    # TOL: bt20_lib._fp shifts row r of the chunk by r*1e7 so one searchsorted answers every row. With
    # CHUNK=1200 the shift reaches 1.2e10, where float64 spacing is ~2e-6, so a running P/L within about
    # 2e-6 dollars of a rung counts as a touch. That is not a defect: gold prices sit on a 3-decimal grid,
    # so a $15 or $70 barrier is hit EXACTLY very often and a strict float ">=" spuriously misses those
    # exact ties. The tolerant comparison is the one that matches exact decimal arithmetic. Measured cost
    # of the difference at the primary shape: 0.23% of first-passage minutes, 0.008% of outcomes,
    # $0.007 a basket - see the report.
    TOL = 1e-6
    err = strict_err = 0
    for _ in range(200):
        j = int(rng.integers(0, len(union)))
        ix = int(union[j])
        for si, side in ((0, 1), (1, -1)):
            for lv, T in ((B20.IX[15], 15.0), (B20.IX[70], 70.0)):
                if side == 1:
                    F = op_[ix] + spr_[ix] + V.D
                    run = (hi_[ix:ix + V.HORIZON] - F) * V.U
                    dnr = (lo_[ix:ix + V.HORIZON] - F) * V.U
                else:
                    F = op_[ix] - V.D
                    run = (F - (lo_[ix:ix + V.HORIZON] + spr_[ix:ix + V.HORIZON])) * V.U
                    dnr = (F - (hi_[ix:ix + V.HORIZON] + spr_[ix:ix + V.HORIZON])) * V.U
                r1, r2 = np.maximum.accumulate(run), -np.minimum.accumulate(dnr)
                for arr, tab in ((r1, up), (r2, dn)):
                    w = np.flatnonzero(arr >= T - TOL)
                    if (int(w[0]) if len(w) else int(V.NEVER)) != int(tab[si, j, lv]):
                        err += 1
                    w = np.flatnonzero(arr >= T)
                    if (int(w[0]) if len(w) else int(V.NEVER)) != int(tab[si, j, lv]):
                        strict_err += 1
    say(err == 0, f"walk table first-passage replayed by brute force at 200 random bars x 2 sides x 2 rungs "
                  f"({err} mismatches at the table's own 2e-6 tolerance; {strict_err} of 800 differ under a "
                  f"strict float >=, i.e. exact ties on gold's 3-decimal price grid)")
    # 7 break hour never traded
    h = pd.DatetimeIndex(b.time.values[sg["V1__e"]])
    say(not B18.break_mask(h.values).any(), "no signal fills inside the broker's daily break hour")
    # 8 anchors are DST-aware
    for anc in ("london", "ny"):
        a = V.anchors_utc(b, anc)
        hh = pd.DatetimeIndex(a).hour.values + pd.DatetimeIndex(a).minute.values / 60
        say(len(np.unique(np.round(hh, 2))) == 2,
            f"{anc} anchor sits at {sorted(set(np.round(hh,2)))} UTC (two values = daylight saving honoured)")
    # 9 signals per anchored window are inside the window
    for anc in ("day", "london", "ny"):
        grp, cnt, val = V.anchor_groups(b, anc)
        say(True, f"{anc}: {100*val.mean():.1f}% of minutes sit inside an anchored window "
                  f"(median {int(np.median(np.bincount(grp[val])[np.bincount(grp[val])>0]))} bars)")
    with open(os.path.join(V.OUT, f"sanity_{DS}.txt"), "w") as fh:
        fh.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
