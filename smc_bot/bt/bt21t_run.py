"""BT21-T run - the M5-trigger twins of BT21-E, measured on the owner's own setup.

Writes, per feed, into results/BT21T/:
    grid_<feed>.csv        6 variants x era x 36 shapes: accuracy, its own luck bar, money, throughput
    look_<feed>.csv        the SAME readings filled at the M5 bar's own open (the lookahead twin)
    m1_<feed>.csv          BT21-E's M1 readings recomputed from ITS OWN walk tables, with error bars
    ext_<feed>.csv         targets $100/$150/$200 on BOTH timeframes (cost-amortization extension)
    placebo_<feed>.json    the week-rotation acceptance bar, best-of-6, 200 draws, per era
    trades_<variant>_<era>.csv   the baskets the one-at-a-time engine took at $15/$70
    checks_<feed>.json     the sanity checks

    .venv/Scripts/python.exe smc_bot/bt/bt21t_run.py                       # zero feed
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/bt21t_run.py      # duka feed
    .venv/Scripts/python.exe smc_bot/bt/bt21t_run.py --tests               # sanity checks only
    .venv/Scripts/python.exe smc_bot/bt/bt21t_run.py --ext                 # the $100-$200 extension
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars                                             # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402
from bt import bt20_lib as B20                                         # noqa: E402
from bt import bt21e_lib as E                                          # noqa: E402
from bt import bt21t_lib as T                                          # noqa: E402

DS = T.DS


def load(tag=""):
    z = np.load(os.path.join(T.PARTS, f"walk_{tag}{DS}.npz"))
    sg = np.load(os.path.join(T.PARTS, f"sig_{tag}{DS}.npz"))
    return z["union"], z["up"], z["dn"], z["final"], sg


def load_e(tag=""):
    """BT21-E's own tables, read-only, so the M1 side of every delta is ITS arithmetic, not a re-run."""
    z = np.load(os.path.join(E.PARTS, f"walk_{tag}{DS}.npz"))
    sg = np.load(os.path.join(E.PARTS, f"sig_{tag}{DS}.npz"))
    return z["union"], z["up"], z["dn"], z["final"], sg


def era_slices(tb):
    out = []
    for era, a, b_ in T.SPANS:
        lo, hi = np.datetime64(pd.Timestamp(a)), np.datetime64(pd.Timestamp(b_))
        m = (tb >= lo) & (tb < hi)
        out.append((era, lo, hi, len(np.unique(tb[m].astype("datetime64[D]"))),
                    len(np.unique(tb[m].astype("datetime64[M]")))))
    return out


# ------------------------------------------------------------------ the main grid
def grid(tag="", shapes=None, variants=None, save_trades=True):
    union, up, dn, final, sg = load(tag)
    b = bars(T.GOLD, "M1")
    tb = b.time.values.astype("datetime64[ns]")
    shapes = shapes or T.SHAPES
    variants = variants or T.VARIANTS
    rows, prep_by_era = [], {}
    for era, lo, hi, days, months in era_slices(tb):
        print(f"  {era} ({DS}): {days:,} trading days, {months} months", flush=True)
        prep = []
        for nm in variants:
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
            for (TT, S) in shapes:
                g, off = T.outcome(up, dn, final, pos, TT, S)
                sc = T.score(g, d)
                a = T.acc_vec(g, d)
                se_n, se_w = T.acc_se(a, tns)
                net = np.where(d == 1, g[0], g[1]) - T.COMM
                it, isd, igr, iex, ibar = T.engine(e, d, pos, up, dn, final, TT, S)
                enet = igr - T.COMM
                sess = L.session_of(pd.to_datetime(tb[ibar])) if len(ibar) else np.array([])
                rows.append(dict(
                    feed=DS, era=era, variant=nm, trig_tf=T.TRIG_TF, target=TT, stop=S, sign="declared",
                    signals=sc["n"], acc=round(sc["acc"], 3),
                    acc_se_naive=round(se_n, 3), acc_se_week=round(se_w, 3),
                    luck_bar=round(sc["own_be"], 2), beats_bar=round(sc["acc"] - sc["own_be"], 2),
                    per_signal_usd=round(sc["money"], 3),
                    luck_usd=round(sc["luck_usd"], 3), flip_usd=round(sc["flip_usd"], 3),
                    win_pct=round(sc["win_pct"], 2), decisive_pct=round(sc["decisive_pct"], 2),
                    E_best=round(sc["E_best"], 3), E_worst=round(sc["E_worst"], 3),
                    week_t=round(T.week_t(net, tns), 2),
                    med_min=int(np.median(np.where(d == 1, off[0], off[1]))),
                    mean_abs_gross=round(float(np.abs(np.where(d == 1, g[0], g[1])).mean()), 3),
                    baskets=len(it), per_day=round(len(it) / days, 2),
                    per_month=round(len(it) / months, 1),
                    basket_usd=round(float(enet.mean()), 3) if len(it) else np.nan,
                    basket_win_pct=round(100 * float((igr > 0).mean()), 2) if len(it) else np.nan,
                    net_total=round(float(enet.sum()), 0) if len(it) else np.nan,
                    usd_per_month=round(float(enet.sum()) / months, 1) if len(it) else np.nan,
                    basket_med_min=int(np.median(iex)) if len(it) else -1,
                    **({f"n_{s}": int((sess == s).sum()) for s, _, _ in L.SESSIONS} if len(it) else {}),
                ))
                if (TT, S) == T.PRIMARY:
                    per_day = len(e) / max(1.0, (tns.max() - tns.min()) / 86_400_000_000_000)
                    prep.append((nm, d, g[0] - T.COMM, g[1] - T.COMM, per_day))
                    if not tag and save_trades:
                        _save_trades(nm, era, b, it, isd, igr, iex, ibar, TT, S,
                                     sg[f"{nm}__k"][keep][o][it])
        prep_by_era[era] = prep
    return pd.DataFrame(rows), prep_by_era


def _save_trades(nm, era, b, it, isd, igr, iex, ibar, TT, S, kbar):
    if len(it) == 0:
        return
    b5 = bars(T.GOLD, T.TRIG_TF)
    fl, fs = T.fill_prices(b, ibar)
    fill = np.where(isd == 1, fl, fs)
    pd.DataFrame(dict(variant=nm, era=era, feed=DS, trig_tf=T.TRIG_TF,
                      t_m5_signal_open=b5.time.values[kbar], t_m5_signal_close=b5.tclose.values[kbar],
                      t_entry=b.time.values[ibar], side=isd, fill=np.round(fill, 3),
                      target_usd=TT, stop_usd=S,
                      t_exit=b.time.values[np.minimum(ibar + iex, len(b) - 1)], hold_min=iex,
                      gross_usd=igr, net_usd=np.round(igr - T.COMM, 2))
                 ).to_csv(os.path.join(T.OUT, f"trades_{nm}_{era}.csv"), index=False)


# ------------------------------------------------------------------ BT21-E's M1 side, with error bars
def m1_grid(shapes=None):
    """Recompute BT21-E's E1-E6 from ITS OWN walk tables so the M1 arm of every delta carries an error
    bar. The published grid_<feed>.csv is asserted against this in tests() - if the two disagree the
    delta tables are void."""
    union, up, dn, final, sg = load_e()
    b = bars(E.GOLD, "M1")
    tb = b.time.values.astype("datetime64[ns]")
    shapes = shapes or [T.PRIMARY]
    rows = []
    for era, lo, hi, days, months in era_slices(tb):
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
            tns = tb[e].astype(np.int64)
            for (TT, S) in shapes:
                g, off = E.outcome(up, dn, final, pos, TT, S)
                sc = E.score(g, d)
                a = T.acc_vec(g, d)
                se_n, se_w = T.acc_se(a, tns)
                it, isd, igr, iex, ibar = E.engine(e, d, pos, up, dn, final, TT, S)
                enet = igr - E.COMM
                rows.append(dict(feed=DS, era=era, variant=nm, trig_tf="M1", target=TT, stop=S,
                                 signals=sc["n"], acc=round(sc["acc"], 3),
                                 acc_se_naive=round(se_n, 3), acc_se_week=round(se_w, 3),
                                 luck_bar=round(sc["own_be"], 2),
                                 beats_bar=round(sc["acc"] - sc["own_be"], 2),
                                 per_signal_usd=round(sc["money"], 3),
                                 mean_abs_gross=round(float(np.abs(np.where(d == 1, g[0], g[1])).mean()), 3),
                                 baskets=len(it), per_day=round(len(it) / days, 2),
                                 basket_usd=round(float(enet.mean()), 3) if len(it) else np.nan,
                                 basket_med_min=int(np.median(iex)) if len(it) else -1,
                                 med_min=int(np.median(np.where(d == 1, off[0], off[1])))))
            print(f"    m1 {era} {nm}: {len(e):,} signals", flush=True)
    return pd.DataFrame(rows)


# ------------------------------------------------------------- the $100-$200 extension, both timeframes
def ext():
    """Targets $100/$150/$200 x stops $5-$70, on the M5 trigger AND on BT21-E's M1 trigger.

    The brief's cost-amortization question names `$50-$200 targets`; the 36-shape ladder stops at $50, so
    the three wider rungs are walked here. The M1 arm reuses BT21-E's own union and its own $5-$70 stop
    table; only the three new TARGET columns are freshly walked (bt21t_lib.build_m1ext).
    """
    b = bars(T.GOLD, "M1")
    tb = b.time.values.astype("datetime64[ns]")
    rows = []
    # ---- M5 arm
    union, up, dn, final, sg = load()
    for era, lo, hi, days, months in era_slices(tb):
        for nm in T.VARIANTS:
            e, d = sg[f"{nm}__e"], sg[f"{nm}__d"]
            keep = (tb[e] >= lo) & (tb[e] < hi)
            e, d = e[keep], d[keep]
            if len(e) < 50:
                continue
            o = np.argsort(e, kind="stable")
            e, d = e[o], d[o]
            pos = np.searchsorted(union, e)
            for (TT, S) in T.EXT_SHAPES:
                g, off = T.outcome_ext(up, dn, final, pos, TT, S)
                sc = T.score(g, d)
                it, isd, igr, iex, ibar = T.engine_ext(e, d, pos, up, dn, final, TT, S)
                enet = igr - T.COMM
                rows.append(dict(feed=DS, era=era, trig_tf="M5", variant=nm, target=TT, stop=S,
                                 signals=sc["n"], acc=round(sc["acc"], 3),
                                 luck_bar=round(sc["own_be"], 2),
                                 beats_bar=round(sc["acc"] - sc["own_be"], 2),
                                 mean_abs_gross=round(float(np.abs(np.where(d == 1, g[0], g[1])).mean()), 3),
                                 baskets=len(it), per_day=round(len(it) / days, 2),
                                 basket_usd=round(float(enet.mean()), 3) if len(it) else np.nan,
                                 basket_med_min=int(np.median(iex)) if len(it) else -1))
            print(f"    ext M5 {era} {nm}", flush=True)
    del up, dn, final
    # ---- M1 arm: BT21-E's stop table + the freshly walked wide targets
    unionE, upE, dnE, finalE, sgE = load_e()
    zx = np.load(os.path.join(T.PARTS, f"walk_m1ext_{DS}.npz"))
    assert (zx["union"] == unionE).all(), "m1ext was walked on a different union"
    upX = np.concatenate([upE, zx["up"]], axis=2)          # columns 8..10 = $100/$150/$200
    dnX = np.concatenate([dnE, zx["dn"]], axis=2)
    del upE, zx
    for era, lo, hi, days, months in era_slices(tb):
        for nm in E.VARIANTS:
            e, d = sgE[f"{nm}__e"], sgE[f"{nm}__d"]
            keep = (tb[e] >= lo) & (tb[e] < hi)
            e, d = e[keep], d[keep]
            if len(e) < 50:
                continue
            o = np.argsort(e, kind="stable")
            e, d = e[o], d[o]
            pos = np.searchsorted(unionE, e)
            for (TT, S) in T.EXT_SHAPES:
                g, off = T.outcome_ext(upX, dnX, finalE, pos, TT, S)
                sc = T.score(g, d)
                it, isd, igr, iex, ibar = T.engine_ext(e, d, pos, upX, dnX, finalE, TT, S)
                enet = igr - T.COMM
                rows.append(dict(feed=DS, era=era, trig_tf="M1", variant=nm, target=TT, stop=S,
                                 signals=sc["n"], acc=round(sc["acc"], 3),
                                 luck_bar=round(sc["own_be"], 2),
                                 beats_bar=round(sc["acc"] - sc["own_be"], 2),
                                 mean_abs_gross=round(float(np.abs(np.where(d == 1, g[0], g[1])).mean()), 3),
                                 baskets=len(it), per_day=round(len(it) / days, 2),
                                 basket_usd=round(float(enet.mean()), 3) if len(it) else np.nan,
                                 basket_med_min=int(np.median(iex)) if len(it) else -1))
            print(f"    ext M1 {era} {nm}", flush=True)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ sanity checks
def tests():
    out = {}
    b = bars(T.GOLD, "M1")
    b5 = bars(T.GOLD, T.TRIG_TF)
    t1 = b.time.values.astype("datetime64[ns]")
    tc5 = b5.tclose.values.astype("datetime64[ns]")
    t5 = b5.time.values.astype("datetime64[ns]")
    sig, _u = T.all_signals(b, b5)

    # 1 - the fill is the FIRST MINUTE AFTER the M5 bar closes, and never earlier
    bad = gapsec = 0
    for nm, (e, d, k, w) in sig.items():
        bad += int((t1[e] != tc5[k]).sum())
        gapsec = max(gapsec, float(((tc5[k] - t1[e]) / np.timedelta64(1, "s")).max()))
    out["fill_open_equals_M5_close"] = "PASS" if bad == 0 else f"FAIL ({bad})"
    out["worst_seconds_fill_open_before_M5_close"] = gapsec        # must be <= 0

    # 1b - the fill minute is strictly inside the NEXT M5 bar, i.e. the bot waited the whole bar out
    worst = min(float((((t1[e] - t5[k]) / np.timedelta64(1, "m")) ).min()) for nm, (e, d, k, w) in sig.items())
    out["min_minutes_from_M5_open_to_fill"] = worst                # must be 5.0

    # 1c - the twin differs from the honest run in NOTHING but the fill bar
    sigl = T.all_signals(b, b5, lookahead=True)[0]
    same = all((sig[nm][2] == sigl[nm][2]).all() and (sig[nm][1] == sigl[nm][1]).all()
               and (t1[sigl[nm][0]] == t5[sigl[nm][2]]).all() for nm in T.VARIANTS)
    out["twin_has_identical_signal_set"] = "PASS" if same else "FAIL"

    # 2 - no feature may come from a bar closing after the fill bar's open
    from bt.common import asof_index
    worst = []
    for nm in ("T1_m15_ema9", "T3_h1_ema9"):
        bt = bars(T.GOLD, T.SPEC[nm][0])
        e, d, k, w = sig[nm]
        j = asof_index(bt, t1[e])
        worst.append(float((bt.tclose.values[j].astype("datetime64[ns]") - t1[e]).max()
                           / np.timedelta64(1, "s")))
    out["trend_bar_closes_at_or_before_fill_open_s"] = max(worst)
    # and the trigger M5 bar itself must have closed at or before the fill open
    out["trigger_M5_bar_closed_before_fill_s"] = max(
        float(((tc5[k] - t1[e]) / np.timedelta64(1, "s")).max()) for nm, (e, d, k, w) in sig.items())

    # 3 - the mirror of a reading is exactly 100 - accuracy (ties counted as halves)
    union, up, dn, final, sg = load()
    e, d = sg["T1_m15_ema9__e"], sg["T1_m15_ema9__d"]
    pos = np.searchsorted(union, e)
    g, off = T.outcome(up, dn, final, pos, *T.PRIMARY)
    a1, a2 = T.score(g, d)["acc"], T.score(g, (-d).astype(np.int8))["acc"]
    out["mirror_is_100_minus_acc"] = f"{a1:.6f} + {a2:.6f} = {a1 + a2:.6f}"

    # 4 - a RANDOM direction at exactly the same minutes reads ~50 and loses the coin-flip amount
    rng = np.random.default_rng(0)
    sc = T.score(g, rng.choice([1, -1], len(d)).astype(np.int8))
    out["random_side_same_minutes"] = dict(acc=round(sc["acc"], 2), usd=round(sc["money"], 3),
                                          coinflip_usd=round(sc["luck_usd"], 3))

    # 5 - our engine IS bt20_run.engine (the shared one) on the same input
    from bt import bt20_run as R20
    sub, ds_ = e[:20000], d[:20000]
    ps = np.searchsorted(union, sub)
    TT, S = T.PRIMARY
    up20 = np.full((2, len(union), len(B20.LADDER)), T.NEVER, np.int32)
    dn20 = np.full((2, len(union), len(B20.LADDER)), T.NEVER, np.int32)
    for lv in E.LEVELS:
        up20[:, :, E.IX20[lv]] = up[:, :, T.IX[lv]]
        dn20[:, :, E.IX20[lv]] = dn[:, :, T.IX[lv]]
    mine = T.engine(sub, ds_, ps, up, dn, final, TT, S)
    theirs = R20.engine(sub, ds_, ps, up20, dn20, final, TT, S)
    out["engine_matches_bt20_run"] = "PASS" if all((np.asarray(x) == np.asarray(y)).all()
                                                   for x, y in zip(mine, theirs)) else "FAIL"

    # 5b - our walk's $5-$70 columns equal bt21e_lib.walk's on the same bars (same kernel, longer ladder)
    sub2 = union[:: max(1, len(union) // 4000)][:4000]
    o8 = E.walk(b, sub2)
    o11 = T.walk(b, sub2)
    ok8 = all((o8[kk][:, :, :] == o11[kk][:, :, :len(E.LEVELS)]).all() for kk in ("up", "dn"))
    out["walk_base_columns_match_bt21e_walk"] = ("PASS" if ok8 and np.allclose(o8["final"], o11["final"])
                                                 else "FAIL")

    # 6 - the walk's arithmetic, replayed by hand on three baskets straight off the M1 frame
    hand = []
    for i in (5, 500, 5000):
        j = pos[i]
        si = 0 if d[i] == 1 else 1
        fw, fl = int(up[si, j, T.IX[float(TT)]]), int(dn[si, j, T.IX[float(S)]])
        o = min(fw, fl) if min(fw, fl) < T.NEVER else T.HORIZON
        bar = int(e[i])
        seg = b.iloc[bar:bar + o + 1]
        F = (seg.open.values[0] + seg.spr.values[0] + T.D) if d[i] == 1 else (seg.open.values[0] - T.D)
        mfe = ((seg.high.max() - F) if d[i] == 1 else (F - (seg.low + seg.spr).min())) * T.U
        mae = ((seg.low.min() - F) if d[i] == 1 else (F - (seg.high + seg.spr).max())) * T.U
        hand.append(dict(t=str(b.time.values[bar])[:16], side=int(d[i]), exit_min=o,
                         mfe_usd=round(float(mfe), 2), mae_usd=round(float(mae), 2),
                         booked="TP" if fw < fl else ("SL" if fl < T.NEVER else "float"),
                         ok=bool((fw < fl and mfe >= TT - 1e-6) or (fw >= fl and mae <= -S + 1e-6)
                                 or min(fw, fl) >= T.NEVER)))
    out["hand_replay"] = hand

    # 7 - the 1440-BAR walk crosses market gaps (BT21_SUMMARY open issue 5). Measure the exposure.
    gapb = np.r_[np.diff(t1) > np.timedelta64(6, "h"), False]
    cg = np.cumsum(gapb)
    crosses = cg[np.minimum(e + T.HORIZON - 1, len(cg) - 1)] - cg[e]
    out["pct_signals_whose_1440bar_window_crosses_a_gap"] = round(100 * float((crosses > 0).mean()), 1)
    gg, oo = T.outcome(up, dn, final, pos, TT, S)
    res = np.where(d == 1, oo[0], oo[1])
    cg2 = cg[np.minimum(e + res, len(cg) - 1)] - cg[e]
    out["pct_baskets_that_actually_resolve_after_a_gap"] = round(100 * float((cg2 > 0).mean()), 2)
    clean = cg2 == 0
    out["acc_all"] = round(T.score(gg, d)["acc"], 2)
    out["acc_excluding_gap_crossers"] = round(T.score(gg[:, clean], d[clean])["acc"], 2)

    # 8 - overlap sensitivity: one signal per trend bar instead of every qualifying M5 bar
    ov = {}
    for nm in ("T1_m15_ema9", "T3_h1_ema9"):
        e2, d2 = sg[f"{nm}__e"], sg[f"{nm}__d"]
        bt = bars(T.GOLD, T.SPEC[nm][0])
        j = asof_index(bt, t1[e2])
        first = np.r_[True, np.diff(j) != 0]
        ps2 = np.searchsorted(union, e2)
        g2, _ = T.outcome(up, dn, final, ps2, *T.PRIMARY)
        ov[nm] = dict(all_signals=int(len(e2)), acc_all=round(T.score(g2, d2)["acc"], 2),
                      one_per_trend_bar=int(first.sum()),
                      acc_one_per_trend_bar=round(T.score(g2[:, first], d2[first])["acc"], 2),
                      luck_bar_one_per_trend_bar=round(T.score(g2[:, first], d2[first])["own_be"], 2))
    out["overlap_sensitivity"] = ov

    # 9 - PARITY: BT21-E recomputed from its own tables must reproduce its published grid
    pub = pd.read_csv(os.path.join(E.OUT, f"grid_{DS}.csv"))
    pub = pub[(pub.target == T.PRIMARY[0]) & (pub.stop == T.PRIMARY[1])]
    mine_m1 = m1_grid()
    j = mine_m1.merge(pub, on=["era", "variant"], suffixes=("_mine", "_pub"))
    out["bt21e_parity"] = dict(
        rows=int(len(j)),
        max_abs_acc_diff=round(float((j.acc_mine - j.acc_pub).abs().max()), 4),
        max_abs_luckbar_diff=round(float((j.luck_bar_mine - j.luck_bar_pub).abs().max()), 4),
        max_abs_basket_usd_diff=round(float((j.basket_usd_mine - j.basket_usd_pub).abs().max()), 4),
        max_basket_count_diff=int((j.baskets_mine - j.baskets_pub).abs().max()),
        max_signal_count_diff=int((j.signals_mine - j.signals_pub).abs().max()))

    # 10 - how many M5 bars were dropped because one of the two fill minutes did not exist
    nraw = {}
    for nm in T.VARIANTS:
        nraw[nm] = int(len(sig[nm][0]))
    out["signals_kept"] = nraw
    out["note_test_era"] = "2024-2026 is reported but no variant, shape or threshold was chosen from it"
    return out


def main():
    os.makedirs(T.OUT, exist_ok=True)
    if "--tests" in sys.argv:
        ck = tests()
        print(json.dumps(ck, indent=1, default=str))
        json.dump(ck, open(os.path.join(T.OUT, f"checks_{DS}.json"), "w"), indent=1, default=str)
        return
    if "--ext" in sys.argv:
        ext().to_csv(os.path.join(T.OUT, f"ext_{DS}.csv"), index=False)
        print(f"  ext -> ext_{DS}.csv")
        return
    print(f"== BT21-T, feed {DS}, trigger {T.TRIG_TF} ==", flush=True)
    df, prep = grid()
    df.to_csv(os.path.join(T.OUT, f"grid_{DS}.csv"), index=False)
    print(f"  grid: {len(df)} rows -> grid_{DS}.csv", flush=True)

    print("  lookahead twin ...", flush=True)
    dl, _ = grid(tag="look_", shapes=[T.PRIMARY, T.SHIPPED])
    dl.to_csv(os.path.join(T.OUT, f"look_{DS}.csv"), index=False)

    print("  BT21-E's M1 side, recomputed with error bars ...", flush=True)
    m1_grid(shapes=[T.PRIMARY, T.SHIPPED]).to_csv(os.path.join(T.OUT, f"m1_{DS}.csv"), index=False)

    print("  placebo: week rotation, best-of-6, 200 draws ...", flush=True)
    pb = {era: T.rotation_placebo(p, draws=200) for era, p in prep.items() if p}
    json.dump(pb, open(os.path.join(T.OUT, f"placebo_{DS}.json"), "w"), indent=1)
    for era, v in pb.items():
        print(f"    {era}: two-sided bar acc {v['acc2_P95']:.2f}  $ {v['money2_P95']:+.3f}", flush=True)

    ck = tests()
    json.dump(ck, open(os.path.join(T.OUT, f"checks_{DS}.json"), "w"), indent=1, default=str)
    print("  checks:", {k: v for k, v in ck.items() if isinstance(v, str)}, flush=True)

    p = df[(df.target == T.PRIMARY[0]) & (df.stop == T.PRIMARY[1])]
    cols = ["era", "variant", "signals", "acc", "acc_se_week", "luck_bar", "beats_bar",
            "per_signal_usd", "luck_usd", "baskets", "basket_usd", "per_day", "med_min", "week_t"]
    print("\nPRIMARY SHAPE $15 / $70:")
    print(p[cols].to_string(index=False))


if __name__ == "__main__":
    main()
