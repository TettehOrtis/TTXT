"""BT21-E run - the owner's EMA-pullback (E1-E4) and sweep-reversal (E5/E6) specs, measured.

Writes, per feed, into results/BT21E/:
    grid_<feed>.csv        every variant x era x shape: accuracy, its own luck bar, money, throughput
    look_<feed>.csv        the SAME readings filled at the signal bar's own open (the lookahead twin)
    struct_<feed>.csv      E5/E6 with the stop at the WICK and the target at 1R / 2R (the owner's real spec)
    placebo_<feed>.json    the week-rotation acceptance bar, best-of-6, 200 draws, per era
    trades_<variant>_<era>.csv   the baskets the one-at-a-time engine actually took at the primary shape
    checks_<feed>.json     the sanity checks

    .venv/Scripts/python.exe smc_bot/bt/bt21e_run.py                          # zero feed
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/bt21e_run.py         # duka feed
    .venv/Scripts/python.exe smc_bot/bt/bt21e_run.py --tests                  # sanity checks only
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars                                             # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402
from bt import bt21e_lib as E                                          # noqa: E402

DS = E.DS


def load(tag=""):
    z = np.load(os.path.join(E.PARTS, f"walk_{tag}{DS}.npz"))
    sg = np.load(os.path.join(E.PARTS, f"sig_{tag}{DS}.npz"))
    return z["union"], z["up"], z["dn"], z["final"], sg


def era_slices(tb):
    out = []
    for era, a, b_ in E.SPANS:
        lo, hi = np.datetime64(pd.Timestamp(a)), np.datetime64(pd.Timestamp(b_))
        m = (tb >= lo) & (tb < hi)
        out.append((era, lo, hi, len(np.unique(tb[m].astype("datetime64[D]"))),
                    len(np.unique(tb[m].astype("datetime64[M]")))))
    return out


# ------------------------------------------------------------------ the main grid
def grid(tag="", shapes=None):
    union, up, dn, final, sg = load(tag)
    b = bars(E.GOLD, "M1")
    tb = b.time.values.astype("datetime64[ns]")
    shapes = shapes or E.SHAPES
    rows = []
    prep_by_era = {}
    for era, lo, hi, days, months in era_slices(tb):
        print(f"  {era} ({DS}): {days:,} trading days, {months} months", flush=True)
        prep = []
        for nm in E.VARIANTS:
            e, d = sg[f"{nm}__e"], sg[f"{nm}__d"]
            te = tb[e]
            keep = (te >= lo) & (te < hi)
            e, d = e[keep], d[keep]
            if len(e) < 50:
                continue
            o = np.argsort(e, kind="stable")
            e, d = e[o], d[o]
            pos = np.searchsorted(union, e)
            assert (union[pos] == e).all(), f"{nm}: entry bars missing from the walk table"
            tns = tb[e].astype(np.int64)
            for (T, S) in shapes:
                g, off = E.outcome(up, dn, final, pos, T, S)
                sc = E.score(g, d)
                net = np.where(d == 1, g[0], g[1]) - E.COMM
                # the robot's own throughput rule on top
                it, isd, igr, iex, ibar = E.engine(e, d, pos, up, dn, final, T, S)
                enet = igr - E.COMM
                sess = L.session_of(pd.to_datetime(tb[ibar])) if len(ibar) else np.array([])
                rows.append(dict(
                    feed=DS, era=era, variant=nm, target=T, stop=S, sign="declared",
                    signals=sc["n"], acc=round(sc["acc"], 2), luck_bar=round(sc["own_be"], 2),
                    beats_bar=round(sc["acc"] - sc["own_be"], 2),
                    per_signal_usd=round(sc["money"], 3),
                    luck_usd=round(sc["luck_usd"], 3), flip_usd=round(sc["flip_usd"], 3),
                    win_pct=round(sc["win_pct"], 2), decisive_pct=round(sc["decisive_pct"], 2),
                    E_best=round(sc["E_best"], 3), E_worst=round(sc["E_worst"], 3),
                    week_t=round(E.week_t(net, tns), 2),
                    med_min=int(np.median(np.where(d == 1, off[0], off[1]))),
                    baskets=len(it), per_day=round(len(it) / days, 2),
                    per_month=round(len(it) / months, 1),
                    basket_usd=round(float(enet.mean()), 3) if len(it) else np.nan,
                    basket_win_pct=round(100 * float((igr > 0).mean()), 2) if len(it) else np.nan,
                    net_total=round(float(enet.sum()), 0) if len(it) else np.nan,
                    usd_per_month=round(float(enet.sum()) / months, 1) if len(it) else np.nan,
                    **({f"n_{s}": int((sess == s).sum()) for s, _, _ in L.SESSIONS} if len(it) else {}),
                ))
                if (T, S) == E.PRIMARY:
                    per_day = len(e) / max(1.0, (tns.max() - tns.min()) / 86_400_000_000_000)
                    prep.append((nm, d, g[0] - E.COMM, g[1] - E.COMM, per_day))
                    if not tag:
                        _save_trades(nm, era, b, e, d, it, isd, igr, iex, ibar, T, S)
        prep_by_era[era] = prep
    return pd.DataFrame(rows), prep_by_era


def _save_trades(nm, era, b, e, d, it, isd, igr, iex, ibar, T, S):
    if len(it) == 0:
        return
    fl, fs = E.fill_prices(b, ibar)
    fill = np.where(isd == 1, fl, fs)
    t_in = b.time.values[ibar]
    t_out = b.time.values[np.minimum(ibar + iex, len(b) - 1)]
    pd.DataFrame(dict(variant=nm, era=era, feed=DS, t_entry=t_in, side=isd, fill=np.round(fill, 3),
                      target_usd=T, stop_usd=S, t_exit=t_out, hold_min=iex,
                      gross_usd=igr, net_usd=np.round(igr - E.COMM, 2))
                 ).to_csv(os.path.join(E.OUT, f"trades_{nm}_{era}.csv"), index=False)


# ------------------------------------------------------------------ structural stop (E5/E6)
def struct():
    union, up, dn, final, sg = load()
    b = bars(E.GOLD, "M1")
    tb = b.time.values.astype("datetime64[ns]")
    rows = []
    for era, lo, hi, days, months in era_slices(tb):
        for nm in ("E5_sweep20", "E6_sweep60"):
            e, d, w = sg[f"{nm}__e"], sg[f"{nm}__d"], sg[f"{nm}__w"]
            te = tb[e]
            keep = (te >= lo) & (te < hi)
            e, d, w = e[keep], d[keep], w[keep]
            if len(e) < 50:
                continue
            Sd0 = E.struct_stop_usd(b, e, d, w)
            ok = Sd0 > 0                      # fill already past its own wick: untradable, dropped
            ee, dd, Sd = e[ok], d[ok], Sd0[ok]
            # (rmult, spread, commission). The third row of each pair is a COST SENSITIVITY, not a new
            # variant: it strips both costs so the shape's fairness and the entry's skill can be told
            # apart. A 1R:1R shape is fair, so a zero-cost reading of it lands on 50 unless the entry
            # really picks a side.
            for rm, dsp, cm in ((1.0, E.D, E.COMM), (2.0, E.D, E.COMM),
                                (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)):
                g, off = E.struct_outcome(b, ee, dd, Sd, rm, dsp=dsp)
                net = g - cm
                # the luck bar: the SAME barrier distances taken on the OPPOSITE side, same minutes
                g2, off2 = E.struct_outcome(b, ee, (-dd).astype(np.int8), Sd, rm, dsp=dsp)
                net2 = g2 - cm
                best, worst = np.maximum(net, net2), np.minimum(net, net2)
                acc = float(np.mean(np.where(net > net2, 1.0, np.where(net < net2, 0.0, 0.5))) * 100)
                eb, ew = best.mean(), worst.mean()
                tag = f"wick stop / {rm:.0f}R target" + ("" if cm else " — ZERO COSTS (sensitivity)")
                rows.append(dict(feed=DS, era=era, variant=nm, shape=tag,
                                 signals=len(g), dropped_no_room=int((~ok).sum()),
                                 med_stop_usd=round(float(np.median(Sd)), 2),
                                 pct_stop_under_2usd=round(100 * float((Sd < 2).mean()), 1),
                                 acc=round(acc, 2),
                                 luck_bar=round(100 * (-ew) / (eb - ew), 2) if eb > ew else np.nan,
                                 per_signal_usd=round(float(net.mean()), 3),
                                 luck_usd=round(float(0.5 * (eb + ew)), 3),
                                 win_pct=round(100 * float((net > 0).mean()), 2),
                                 med_min=int(np.median(off)),
                                 per_signal_R=round(float((g / Sd).mean()), 4),
                                 per_signal_R_net=round(float(net.mean() / np.median(Sd)), 4),
                                 week_t=round(E.week_t(net, tb[e[ok]].astype(np.int64)), 2)))
                print("   ", rows[-1]["era"], nm, rows[-1]["shape"], "acc", rows[-1]["acc"],
                      "bar", rows[-1]["luck_bar"], "$", rows[-1]["per_signal_usd"], flush=True)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ sanity checks
def tests():
    out = {}
    b = bars(E.GOLD, "M1")
    t1 = b.time.values.astype("datetime64[ns]")
    sig, _u = E.all_signals(b)

    # 1 - the fill bar is the very next minute after the signal bar closes
    bad = 0
    for nm, (e, d, k, w) in sig.items():
        bad += int((e != k + 1).sum()) + int((t1[e] != b.tclose.values[k].astype("datetime64[ns]")).sum())
    out["fill_is_next_minute"] = "PASS" if bad == 0 else f"FAIL ({bad})"

    # 1b - the lookahead twin must differ from the honest run in NOTHING but the fill bar
    sigl = E.all_signals(b, lookahead=True)[0]
    same = all((sig[nm][2] == sigl[nm][2]).all() and (sig[nm][1] == sigl[nm][1]).all()
               and (sigl[nm][0] == sigl[nm][2]).all() for nm in E.VARIANTS)
    out["twin_has_identical_signal_set"] = "PASS" if same else "FAIL"

    # 2 - no feature may come from a bar closing after the fill bar's open
    worst = []
    for nm in ("E1_m5_ema9", "E3_m15_ema9"):
        tf = E.SPEC[nm][0]
        bt = bars(E.GOLD, tf)
        e, d, k, w = sig[nm]
        from bt.common import asof_index
        j = asof_index(bt, t1[e])
        worst.append(float((bt.tclose.values[j].astype("datetime64[ns]") - t1[e]).max()
                           / np.timedelta64(1, "s")))
    out["trend_bar_closes_at_or_before_fill_open_s"] = max(worst)

    # 3 - the declared side is unique and the mirror is exactly 100 - acc
    union, up, dn, final, sg = load()
    e, d = sg["E1_m5_ema9__e"], sg["E1_m5_ema9__d"]
    pos = np.searchsorted(union, e)
    g, off = E.outcome(up, dn, final, pos, *E.PRIMARY)
    a1 = E.score(g, d)["acc"]
    a2 = E.score(g, (-d).astype(np.int8))["acc"]
    out["mirror_is_100_minus_acc"] = f"{a1:.6f} + {a2:.6f} = {a1 + a2:.6f}"

    # 4 - a RANDOM direction at exactly the same minutes reads ~50 and loses the coin-flip amount
    rng = np.random.default_rng(0)
    rd = rng.choice([1, -1], len(d)).astype(np.int8)
    sc = E.score(g, rd)
    out["random_side_same_minutes"] = dict(acc=round(sc["acc"], 2), usd=round(sc["money"], 3),
                                          coinflip_usd=round(sc["luck_usd"], 3))

    # 5 - the engine agrees with bt20_run.engine on the same input
    from bt import bt20_run as R20
    sub = e[:20000]
    ps = np.searchsorted(union, sub)
    ds_ = d[:20000]
    T, S = E.PRIMARY
    up20 = np.full((2, len(union), len(E.LADDER20)), E.NEVER, np.int32)
    dn20 = np.full((2, len(union), len(E.LADDER20)), E.NEVER, np.int32)
    for lv in E.LEVELS:
        up20[:, :, E.IX20[lv]] = up[:, :, E.IX[lv]]
        dn20[:, :, E.IX20[lv]] = dn[:, :, E.IX[lv]]
    mine = E.engine(sub, ds_, ps, up, dn, final, T, S)
    theirs = R20.engine(sub, ds_, ps, up20, dn20, final, T, S)
    out["engine_matches_bt20_run"] = "PASS" if all((np.asarray(x) == np.asarray(y)).all()
                                                   for x, y in zip(mine, theirs)) else "FAIL"

    # 6 - the walk's own arithmetic, replayed by hand on three baskets
    hand = []
    for i in (5, 500, 5000):
        j = pos[i]
        si = 0 if d[i] == 1 else 1
        fw, fl = int(up[si, j, E.IX[T]]), int(dn[si, j, E.IX[S]])
        o = min(fw, fl) if min(fw, fl) < E.NEVER else E.HORIZON
        bar = int(e[i])
        seg = b.iloc[bar:bar + o + 1]
        F = (seg.open.values[0] + seg.spr.values[0] + E.D) if d[i] == 1 else (seg.open.values[0] - E.D)
        mfe = ((seg.high.max() - F) if d[i] == 1 else (F - (seg.low + seg.spr).min())) * E.U
        mae = ((seg.low.min() - F) if d[i] == 1 else (F - (seg.high + seg.spr).max())) * E.U
        hand.append(dict(t=str(b.time.values[bar])[:16], side=int(d[i]), exit_min=o,
                         mfe_usd=round(float(mfe), 2), mae_usd=round(float(mae), 2),
                         booked="TP" if fw < fl else ("SL" if fl < E.NEVER else "float"),
                         ok=bool((fw < fl and mfe >= T - 1e-6) or (fw >= fl and mae <= -S + 1e-6)
                                 or min(fw, fl) >= E.NEVER)))
        hand[-1]["ok"] = bool(hand[-1]["ok"])
    out["hand_replay"] = hand

    # 7 - the walk crosses weekend gaps (bt20 walks 1440 BARS, not clock minutes). Measure the exposure.
    gapb = np.r_[np.diff(t1) > np.timedelta64(6, "h"), False]
    cg = np.cumsum(gapb)
    crosses = cg[np.minimum(e + E.HORIZON - 1, len(cg) - 1)] - cg[e]
    out["pct_signals_whose_1440bar_window_crosses_a_gap"] = round(100 * float((crosses > 0).mean()), 1)
    gg, oo = E.outcome(up, dn, final, pos, T, S)
    res = np.where(d == 1, oo[0], oo[1])
    cg2 = cg[np.minimum(e + res, len(cg) - 1)] - cg[e]
    out["pct_baskets_that_actually_resolve_after_a_gap"] = round(100 * float((cg2 > 0).mean()), 2)
    clean = cg2 == 0
    out["acc_excluding_gap_crossers"] = round(E.score(gg[:, clean], d[clean])["acc"], 2)
    out["acc_all"] = round(E.score(gg, d)["acc"], 2)

    # 8 - overlap sensitivity: E1-E4 fire on every qualifying minute, so consecutive signals share most
    # of their outcome. Keeping only the FIRST signal inside each trend bar removes that overlap. If the
    # headline accuracy were an artefact of clustering, this is where it would move.
    from bt.common import asof_index as _ai
    ov = {}
    for nm in ("E1_m5_ema9", "E3_m15_ema9"):
        e, d = sg[f"{nm}__e"], sg[f"{nm}__d"]
        bt = bars(E.GOLD, E.SPEC[nm][0])
        j = _ai(bt, t1[e])
        first = np.r_[True, np.diff(j) != 0]                       # first signal per trend bar
        ps = np.searchsorted(union, e)
        gg2, _ = E.outcome(up, dn, final, ps, *E.PRIMARY)
        ov[nm] = dict(all_signals=int(len(e)), acc_all=round(E.score(gg2, d)["acc"], 2),
                      one_per_trend_bar=int(first.sum()),
                      acc_one_per_trend_bar=round(E.score(gg2[:, first], d[first])["acc"], 2),
                      luck_bar_one_per_trend_bar=round(E.score(gg2[:, first], d[first])["own_be"], 2))
    out["overlap_sensitivity"] = ov

    # 9 - TEST discipline: nothing was selected from the 2024-2026 era (declared, not asserted)
    out["note_test_era"] = "2024-2026 is reported but no variant, shape or threshold was chosen from it"
    return out


def main():
    os.makedirs(E.OUT, exist_ok=True)
    if "--tests" in sys.argv:
        print(json.dumps(tests(), indent=1, default=str))
        json.dump(tests(), open(os.path.join(E.OUT, f"checks_{DS}.json"), "w"), indent=1, default=str)
        return
    print(f"== BT21-E, feed {DS} ==", flush=True)
    df, prep = grid()
    df.to_csv(os.path.join(E.OUT, f"grid_{DS}.csv"), index=False)
    print(f"  grid: {len(df)} rows -> grid_{DS}.csv", flush=True)

    print("  lookahead twin ...", flush=True)
    dl, _ = grid(tag="look_", shapes=[E.PRIMARY, E.SHIPPED])
    dl.to_csv(os.path.join(E.OUT, f"look_{DS}.csv"), index=False)

    print("  structural stop (E5/E6) ...", flush=True)
    struct().to_csv(os.path.join(E.OUT, f"struct_{DS}.csv"), index=False)

    print("  placebo: week rotation, best-of-6, 200 draws ...", flush=True)
    pb = {era: E.rotation_placebo(p, draws=200) for era, p in prep.items() if p}
    json.dump(pb, open(os.path.join(E.OUT, f"placebo_{DS}.json"), "w"), indent=1)
    for era, v in pb.items():
        print(f"    {era}: two-sided bar acc {v['acc2_P95']:.2f}  $ {v['money2_P95']:+.3f}", flush=True)

    ck = tests()
    json.dump(ck, open(os.path.join(E.OUT, f"checks_{DS}.json"), "w"), indent=1, default=str)
    print("  checks:", {k: v for k, v in ck.items() if isinstance(v, str)}, flush=True)

    p = df[(df.target == E.PRIMARY[0]) & (df.stop == E.PRIMARY[1])]
    cols = ["era", "variant", "signals", "acc", "luck_bar", "beats_bar", "per_signal_usd", "luck_usd",
            "baskets", "basket_usd", "per_day", "med_min", "week_t"]
    print("\nPRIMARY SHAPE $15 / $70:")
    print(p[cols].to_string(index=False))


if __name__ == "__main__":
    main()
