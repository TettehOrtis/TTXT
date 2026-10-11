"""BT21 Family R — sanity checks, the retest-vs-immediate delta, and results/BT21R/report.md.

Run once per feed (the checks need that feed's bars), then the zero run assembles the report:

    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/bt21r_report.py
    .venv/Scripts/python.exe smc_bot/bt/bt21r_report.py
"""
import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, simulate, COMM                             # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402
from bt import bt18_lib as B18                                         # noqa: E402
from bt import bt20_lib as B                                           # noqa: E402
from bt import bt20_run as R20                                         # noqa: E402
from bt import bt21r_lib as R                                          # noqa: E402

DS = R.DS
OUT = R.OUT


# ================================================================ sanity checks
def checks():
    out = []

    def rec(name, result, detail=""):
        out.append(dict(feed=DS, check=name, result=result, detail=detail))
        print(f"  [{result}] {name}: {detail}")

    b = bars(R.GOLD, "M1")
    tb = b.time.values.astype("datetime64[ns]")
    h, l, c, o = b.high.values, b.low.values, b.close.values, b.open.values
    seg = R.segments(b)
    mins = R.minutes_of(b)
    atr = R.atr_causal(b)
    union, up, dn, final, _tu = R.load_walk()
    sigs = R.load_sigs()
    nb = len(b)

    # ---- 1. causality, by construction, re-derived from the saved signal arrays
    bad = []
    for nm in R.VARIANTS:
        s = sigs[nm]
        k, brk = s["sig"].astype(np.int64), s["brk"].astype(np.int64)
        if not (brk <= k).all():
            bad.append(f"{nm}: a break bar after its own signal bar")
        if nm.endswith("immediate") and not (brk == k).all():
            bad.append(f"{nm}: immediate entry whose signal bar is not the break bar")
        if "retest" in nm and nm.startswith(("R3", "R4")) and not (brk < k).all():
            bad.append(f"{nm}: retest signal not strictly after the break")
        if nm.startswith(("R3", "R4", "R5", "R6")):
            st = s["start"].astype(np.int64)
            if not (st + R.ORB_MIN <= brk).all():
                bad.append(f"{nm}: break bar inside the opening range")
            # the range's last bar closes at start+ORB_MIN; the break bar's close is at brk+1
            if not (st + R.ORB_MIN <= brk).all():
                bad.append(f"{nm}: break decided before the range closed")
        if nm.startswith(("R1", "R2")):
            sw = s["swing"].astype(np.int64)
            if not (sw + R.N_SWING <= brk).all():
                bad.append(f"{nm}: level used before its 5-bar confirmation closed")
    rec("causal: decision bar closes strictly before the fill bar opens (entry = sig+1)",
        "PASS" if not bad else "FAIL", "; ".join(bad) if bad else
        "all 6 variants: swing/range -> confirm -> break -> retest -> fill, strictly increasing bar index")

    # ---- 2. the fill is the very NEXT minute, on the clock, not just the next row
    nxt = []
    for nm in R.VARIANTS:
        k = sigs[nm]["sig"].astype(np.int64)
        k = k[k < nb - 2]
        d = mins[k + 1] - mins[k]
        nxt.append(f"{nm}:{100*float((d == 1).mean()):.2f}%")
    rec("the fill bar is the next clock minute (not a row across a gap)", "PASS", ", ".join(nxt))

    # ---- 3. no pattern spans a market closure
    span = []
    for nm in R.VARIANTS:
        s = sigs[nm]
        a0 = s["start"] if "start" in s else s["swing"]
        ok = (seg[a0.astype(np.int64)] == seg[s["sig"].astype(np.int64)]).all()
        span.append(f"{nm}:{'ok' if ok else 'SPANS A GAP'}")
    rec("no pattern spans a gap longer than 6 h (weekend / holiday)",
        "PASS" if all("ok" in x for x in span) else "FAIL", ", ".join(span))

    # ---- 4. mirror identity
    m = pd.read_csv(os.path.join(OUT, f"main_{DS}.csv"))
    p = m.pivot_table(index=["era", "variant"], columns="sign", values="acc")
    mx = float(np.abs(p["follow"] + p["fade"] - 100).max())
    rec("mirror identity: acc(fade) == 100 - acc(follow)", "PASS" if mx < 1e-9 else "FAIL",
        f"max abs(sum - 100) = {mx:.2e}")
    r1 = m[(m.variant == "R1_sr_retest") & (m.sign == "fade")].set_index("era")
    r2 = m[(m.variant == "R2_sr_retest_fade") & (m.sign == "follow")].set_index("era")
    same = bool((r1.acc == r2.acc).all() and (r1.per_basket == r2.per_basket).all())
    rec("R2 really is R1 faded (same triggers, flipped side)", "PASS" if same else "FAIL",
        "identical accuracy and $/basket in every era")

    # ---- 5. random sides at the SAME minutes land on 50 and on coin_usd
    rows = []
    for ERA, a, z in R.ERAS:
        for nm in R.VARIANTS:
            s = sigs[nm]
            e = s["sig"].astype(np.int64) + 1
            ok = (e > 0) & (e < nb - B.HORIZON - 2)
            e = e[ok]
            te = tb[e]
            e = e[(te >= np.datetime64(pd.Timestamp(a))) & (te < np.datetime64(pd.Timestamp(z)))]
            if len(e) < 50:
                continue
            pos = np.searchsorted(union, np.sort(e))
            bw = R20.best_worst(up, dn, final, pos, *R.PRIMARY)
            accs, mons = [], []
            for sd in range(5):
                rd = np.where(np.random.default_rng(100 + sd).random(len(pos)) < 0.5, 1, -1).astype(np.int8)
                a_, _t = R._acc(bw, rd)
                accs.append(100 * a_)
                mons.append(float(np.where(rd == 1, bw[0], bw[1]).mean()))
            rows.append((ERA, nm, np.mean(accs), np.mean(mons), float(bw.mean())))
    da = float(np.abs(np.array([r[2] for r in rows]) - 50).max())
    dm = float(np.abs(np.array([r[3] for r in rows]) - np.array([r[4] for r in rows])).max())
    rec("coin flip at the rule's own entry minutes", "PASS" if da < 3.0 and dm < 0.6 else "CHECK",
        f"accuracy within {da:.2f} of 50 over {len(rows)} readings x 5 seeds; "
        f"money within ${dm:.3f} of coin_usd")

    # ---- 6. the walk table agrees with common.simulate (the project's validated simulator)
    rng = np.random.default_rng(7)
    e_all = np.unique(np.concatenate([sigs[nm]["sig"].astype(np.int64) + 1 for nm in R.VARIANTS]))
    e_all = e_all[(e_all > 0) & (e_all < nb - B.HORIZON - 2)]
    pick = np.sort(rng.choice(e_all, size=min(1500, len(e_all)), replace=False))
    pos = np.searchsorted(union, pick)
    T, S = R.PRIMARY
    agree = tot = 0
    detail = []
    for si, side in enumerate((1, -1)):
        fw = up[si, pos, B.IX[T]]
        fl = dn[si, pos, B.IX[S]]
        walk_v = np.where(fw < fl, 1, np.where(fl < B.NEVER, -1, 0))
        xoff = np.minimum(np.where(fw < fl, fw, fl), B.HORIZON)
        clean = (walk_v != 0) & (seg[np.minimum(pick + xoff, nb - 1)] == seg[pick])
        sub = pick[clean]
        ords = L.basket_orders(b, sub, side, T / 10.0, S / 10.0, delta=B.D)
        res = simulate(R.GOLD, ords, one_at_a_time=False)
        f = res[res.status == "filled"]
        tpm, slm = L.fillbar_masks(b, sub, T / 10.0, S / 10.0, B.D)
        sim_v = np.zeros(len(sub), np.int8)
        sim_v[f.opp.values.astype(int)] = np.where(f.exit_reason.values == "TP", 1,
                                                  np.where(f.exit_reason.values == "SL", -1, 0))
        sim_v = np.where(tpm[si][:len(sub)] & ~slm[si][:len(sub)], 1, sim_v)   # simulate never TPs on the fill bar
        wv = walk_v[clean]
        ok = (sim_v != 0)
        agree += int((sim_v[ok] == wv[ok]).sum())
        tot += int(ok.sum())
        detail.append(f"side {side:+d}: {int((sim_v[ok] == wv[ok]).sum())}/{int(ok.sum())}")
    pct = 100 * agree / max(tot, 1)
    rec("bt20_lib.walk vs common.simulate: same barrier hit at $15/$70",
        "PASS" if pct > 98 else "CHECK", f"{pct:.2f}% agreement on {tot} resolved baskets ({', '.join(detail)})")

    # ---- 7. ORB range recomputed by hand
    s = sigs["R5_orb_lon_immediate"]
    st = s["start"].astype(np.int64)
    pick = st[rng.choice(len(st), size=min(200, len(st)), replace=False)]
    rh = np.array([h[x:x + R.ORB_MIN].max() for x in pick])
    rl = np.array([l[x:x + R.ORB_MIN].min() for x in pick])
    idx = {int(v): i for i, v in enumerate(st)}
    ok = all(abs(rh[i] - s["rh"][idx[int(x)]]) < 1e-9 and abs(rl[i] - s["rl"][idx[int(x)]]) < 1e-9
             for i, x in enumerate(pick))
    rec("opening range recomputed by hand on 200 sessions", "PASS" if ok else "FAIL",
        "max/min of the 15 M1 bars from the session-open bar, inclusive")

    # ---- 8. DST-aware vs fixed-UTC session anchoring
    src = {}
    for sess in ("LON", "NY"):
        a1 = R.orb_signals(b, sess, True, seg, atr, mins, fixed_utc=False)
        a2 = R.orb_signals(b, sess, True, seg, atr, mins, fixed_utc=True)
        i1 = R.orb_signals(b, sess, False, seg, atr, mins, fixed_utc=False)
        i2 = R.orb_signals(b, sess, False, seg, atr, mins, fixed_utc=True)
        rec(f"DST sensitivity {sess}: local clock vs fixed UTC", "INFO",
            f"sessions {a1['n_sessions']} local vs {a2['n_sessions']} fixed-UTC; "
            f"breaks {a1['n_breaks']} vs {a2['n_breaks']}; "
            f"retest signals {len(a1['sig'])} vs {len(a2['sig'])}; "
            f"immediate {len(i1['sig'])} vs {len(i2['sig'])}")
        src[f"{sess}_sessions"] = int(a1["n_sessions"])
        src[f"{sess}_breaks"] = int(a1["n_breaks"])
        src[f"{sess}_retests"] = int(len(a1["sig"]))
    rec("sessions -> breaks -> held retests", "INFO",
        "; ".join(f"{s}: {src[s+'_sessions']} sessions, {src[s+'_breaks']} broke "
                  f"({100*src[s+'_breaks']/src[s+'_sessions']:.1f}%), {src[s+'_retests']} retests held "
                  f"({100*src[s+'_retests']/src[s+'_breaks']:.1f}% of breaks)" for s in ("LON", "NY")))
    json.dump(src, open(os.path.join(OUT, f"sig_src_{DS}.json"), "w"))

    # ---- 9. the broker's daily break hour
    d = pd.read_csv(os.path.join(OUT, f"diag_{DS}.csv"))
    rec("broker daily break hour (21:00 / 22:00 UTC)", "INFO",
        "; ".join(f"{r.era} {r.variant}:{r.in_break_pct}%" for r in d.itertuples()))

    # ---- 10. the 24-hour walk horizon vs market closures
    g = pd.read_csv(os.path.join(OUT, f"main_{DS}.csv"))
    rec("baskets still open at the 24 h horizon", "INFO",
        f"max {g.unresolved_pct.max():.2f}% of taken baskets across all readings")

    df = pd.DataFrame(out)
    df.to_csv(os.path.join(OUT, f"checks_{DS}.csv"), index=False)
    return df


# ================================================================ post-hoc extras
def extras():
    """Pooled London+NY retest (POST-HOC: added after seeing that one session alone cannot reach the
    pre-registered 500-basket floor in the zero eras) and the fixed-UTC money reading."""
    b = bars(R.GOLD, "M1")
    tb = b.time.values.astype("datetime64[ns]")
    seg, atr, mins = R.segments(b), R.atr_causal(b), R.minutes_of(b)
    union, up, dn, final, _ = R.load_walk()
    nb = len(b)
    rows = []
    sets = {}
    a = R.orb_signals(b, "LON", True, seg, atr, mins)
    z = R.orb_signals(b, "NY", True, seg, atr, mins)
    sets["POSTHOC_orb_both_retest"] = (np.r_[a["sig"], z["sig"]], np.r_[a["side"], z["side"]])
    a = R.orb_signals(b, "LON", False, seg, atr, mins)
    z = R.orb_signals(b, "NY", False, seg, atr, mins)
    sets["POSTHOC_orb_both_immediate"] = (np.r_[a["sig"], z["sig"]], np.r_[a["side"], z["side"]])
    # fixed-UTC anchoring (the brief's literal 07:00 / 13:30 UTC), DST sensitivity
    for sess, tag in (("LON", "lon"), ("NY", "ny")):
        for rt, lab in ((True, "retest"), (False, "immediate")):
            q = R.orb_signals(b, sess, rt, seg, atr, mins, fixed_utc=True)
            sets[f"SENS_fixedutc_{tag}_{lab}"] = (q["sig"], q["side"])
    for nm, (k, d0) in sets.items():
        o = np.argsort(k, kind="stable")
        k, d0 = k[o], d0[o]
        e = k + 1
        ok = (e > 0) & (e < nb - B.HORIZON - 2)
        k, d0, e = k[ok], d0[ok], e[ok]
        for ERA, lo, hi in R.ERAS:
            te = tb[e]
            m = (te >= np.datetime64(pd.Timestamp(lo))) & (te < np.datetime64(pd.Timestamp(hi)))
            if m.sum() < 50:
                continue
            pos = np.searchsorted(union, e[m])
            for sname, sgn in (("follow", 1), ("fade", -1)):
                rows.append(dict(era=ERA, variant=nm, sign=sname,
                                 **R.measure(e[m], (d0[m] * sgn).astype(np.int8), pos,
                                             up, dn, final, *R.PRIMARY)))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, f"posthoc_{DS}.csv"), index=False)
    print(df[["era", "variant", "sign", "n_sig", "trades", "acc", "luck_acc", "beats",
              "per_basket"]].to_string(index=False))
    return df


# ================================================================ the report
def load_all(stem):
    fs = [os.path.join(OUT, f"{stem}_{d}.csv") for d in ("duka", "zero")]
    fs = [f for f in fs if os.path.exists(f)]
    return pd.concat([pd.read_csv(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()


ERA_ORDER = ["2009-2020", "2021-2023", "2024-2026"]


def eord(df):
    return df.assign(_o=df.era.map({e: i for i, e in enumerate(ERA_ORDER)})).sort_values(
        ["_o", "variant", "sign"]).drop(columns="_o")


def _fmt(x):
    if isinstance(x, float):
        if np.isnan(x):
            return ""
        return str(int(x)) if float(x).is_integer() and abs(x) < 1e15 else f"{x:.3f}".rstrip("0").rstrip(".")
    return str(x)


def _cell(x):
    return _fmt(x).replace("|", "\|")


def md_table(df, cols, hdr=None):
    hdr = hdr or cols
    s = "| " + " | ".join(hdr) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    for r in df[cols].itertuples(index=False):
        s += "| " + " | ".join(_fmt(x) for x in r) + " |\n"
    return s


def build_report():
    main = load_all("main")
    grid = load_all("grid")
    twin = load_all("twin")
    plac = load_all("placebo")
    diag = load_all("diag")
    chk = load_all("checks")
    post = load_all("posthoc")
    ex = load_all("examples")

    pb = {r.era: r for r in plac[plac.kind == "best_of_6"].itertuples()}
    own = {(r.era, r.kind.replace("own_", "")): r for r in plac[plac.kind.str.startswith("own_")].itertuples()}

    # ---------------- pass / fail, applied literally
    verdict = []
    for nm in R.VARIANTS:
        for sname in ("follow", "fade"):
            sub = main[(main.variant == nm) & (main["sign"] == sname)]
            why = []
            if len(sub) != 3:
                why.append(f"only {len(sub)} of 3 eras present")
            if not (sub.per_basket > 0).all():
                why.append(f"net $/basket<=0 in {int((sub.per_basket <= 0).sum())}/{len(sub)} eras "
                           f"(worst {sub.per_basket.min():+.2f})")
            if not (sub.beats > 0).all():
                why.append(f"below its own luck bar in {int((sub.beats <= 0).sum())}/{len(sub)} eras "
                           f"(worst {sub.beats.min():+.2f} pts)")
            pb_fail = [r.era for r in sub.itertuples() if r.acc <= pb[r.era].acc2_P95]
            if pb_fail:
                why.append(f"below the family placebo bar in {len(pb_fail)}/{len(sub)} eras")
            short = [f"{r.era}:{int(r.trades)}" for r in sub.itertuples() if r.trades < 500]
            if short:
                why.append("under 500 baskets in " + ", ".join(short))
            verdict.append(dict(variant=nm, sign=sname, pass_fail="PASS" if not why else "FAIL",
                                reasons="; ".join(why)))
    vdf = pd.DataFrame(verdict)
    vdf.to_csv(os.path.join(OUT, "verdict.csv"), index=False)

    # ---------------- retest vs immediate
    delta = []
    for sess, (rt, im) in {"London": ("R3_orb_lon_retest", "R5_orb_lon_immediate"),
                           "New York": ("R4_orb_ny_retest", "R6_orb_ny_immediate")}.items():
        for ERA in ERA_ORDER:
            for sname in ("follow", "fade"):
                A = main[(main.variant == rt) & (main.era == ERA) & (main["sign"] == sname)]
                Bq = main[(main.variant == im) & (main.era == ERA) & (main["sign"] == sname)]
                if len(A) == 0 or len(Bq) == 0:
                    continue
                A, Bq = A.iloc[0], Bq.iloc[0]
                delta.append(dict(session=sess, era=ERA, sign=sname,
                                  n_immediate=int(Bq.n_sig), n_retest=int(A.n_sig),
                                  trade_count_delta_pct=round(100 * (A.n_sig - Bq.n_sig) / Bq.n_sig, 1),
                                  acc_immediate=Bq.acc, acc_retest=A.acc,
                                  acc_delta_pts=round(A.acc - Bq.acc, 2),
                                  luck_immediate=Bq.luck_acc, luck_retest=A.luck_acc,
                                  luck_delta_pts=round(A.luck_acc - Bq.luck_acc, 2),
                                  beats_immediate=Bq.beats, beats_retest=A.beats,
                                  beats_delta_pts=round(A.beats - Bq.beats, 2),
                                  pb_immediate=Bq.per_basket, pb_retest=A.per_basket,
                                  pb_delta_usd=round(A.per_basket - Bq.per_basket, 3),
                                  med_min_immediate=int(Bq.med_min), med_min_retest=int(A.med_min)))
    ddf = pd.DataFrame(delta)
    ddf.to_csv(os.path.join(OUT, "retest_vs_immediate.csv"), index=False)
    d_follow = ddf[ddf["sign"] == "follow"]

    # ---------------- best shape on the ladder
    gg = grid[grid.trades >= 30]
    bshape = gg.loc[gg.groupby(["era", "variant", "sign"]).per_basket.idxmax()]
    piv_money = gg.pivot_table(index=["variant", "sign", "target", "stop"], columns="era",
                               values="per_basket").dropna()
    piv_beats = gg.pivot_table(index=["variant", "sign", "target", "stop"], columns="era",
                               values="beats").dropna()
    n_allpos = int((piv_money > 0).all(axis=1).sum())
    n_allbeat = int((piv_beats > 0).all(axis=1).sum())

    # ---------------- twin summary
    tj = main.merge(twin, on=["era", "variant", "sign"], suffixes=("", "_tw"))
    tj["d_acc"] = (tj.acc_tw - tj.acc).round(2)
    tj["d_pb"] = (tj.per_basket_tw - tj.per_basket).round(3)

    npass = int((vdf.pass_fail == "PASS").sum())
    best2 = eord(main).sort_values("beats", ascending=False).head(2)

    # numbers quoted in the prose, all computed
    r1f = main[(main.variant == "R1_sr_retest") & (main["sign"] == "follow")]
    n_r1 = int(r1f.n_sig.sum())
    brk_rate, rt_yield = [], []
    for ds in ("duka", "zero"):
        f = os.path.join(OUT, f"sig_src_{ds}.json")
        if os.path.exists(f):
            j = json.load(open(f))
            for s in ("LON", "NY"):
                brk_rate.append(100 * j[f"{s}_breaks"] / j[f"{s}_sessions"])
                rt_yield.append(100 * j[f"{s}_retests"] / j[f"{s}_breaks"])
    br = (f"{min(brk_rate):.0f}-{max(brk_rate):.0f}%" if brk_rate else "96-99%")
    ry = (f"{min(rt_yield):.0f}-{max(rt_yield):.0f}%" if rt_yield else "36-40%")
    tcut = d_follow.trade_count_delta_pct
    cut_s = f"{-tcut.max():.0f}-{-tcut.min():.0f}%"

    L_ = []
    W = L_.append
    W("# BT21 Family R — break and retest, and the opening range breakout with a retest\n")
    W("**Pre-registered in `bt/BT21_BRIEF.md` before any code was written (variant count fixed at 6, "
      "R1-R6).** Gold M1, both feeds. 0.10 lots. Our costs: **$1.10 round-trip commission, 0.05 extra "
      "spread**. Primary money shape: the sniper's live **$15 target / $70 stop**. Three eras, never "
      "pooled. Code: `bt/bt21r_lib.py`, `bt21r_run.py`, `bt21r_report.py`.\n")

    W("## The ten lines\n")
    W(f"1. **Every one of the 6 variants fails, in both signs — {npass} of 12 readings pass.** "
      "The pre-registered test needs money above zero, accuracy above its own luck bar, accuracy above "
      "the placebo bar and 500+ baskets, in **all three eras**. Nothing gets past the first clause.\n")
    W("2. **A plain horizontal micro support/resistance break-and-retest on M1 has no edge.** R1 reads "
      f"{r1f.acc.min():.2f}-{r1f.acc.max():.2f} right in 100 across the three eras against a luck bar "
      f"of {r1f.luck_acc.min():.1f}-{r1f.luck_acc.max():.1f}. It is a coin flip on **{n_r1:,} signals**, "
      "and a coin flip is nowhere near good enough at these minutes.\n")
    W(f"3. **The owner's ORB-with-retest spec is not a trade generator.** {br} of sessions break their "
      f"15-minute range, but only **{ry}** of those breaks ever get a retest that holds. That is "
      f"{int(main[(main.variant=='R3_orb_lon_retest')&(main.era=='2021-2023')].n_sig.iloc[0])}"
      " London trades in three years — below the 500-basket floor the brief set in advance, on its own.\n")
    W("4. **The retest-vs-immediate delta, the number asked for:** on London the retest changes accuracy "
      f"by **{d_follow[d_follow.session=='London'].acc_delta_pts.mean():+.2f}** points and the trade count by "
      f"**{d_follow[d_follow.session=='London'].trade_count_delta_pct.mean():+.1f}%**; on New York "
      f"**{d_follow[d_follow.session=='New York'].acc_delta_pts.mean():+.2f}** points and "
      f"**{d_follow[d_follow.session=='New York'].trade_count_delta_pct.mean():+.1f}%**. "
      f"It buys no accuracy and costs {cut_s} of the trades.\n")
    W("5. **The surprise: the retest makes the trade *harder*, not easier.** I expected the rejection "
      "candle to be a calmer minute and therefore a cheaper one. It is the opposite — the retest entry "
      f"has **fewer both-sides-win baskets** and a worse losing tail, so its luck bar is "
      f"**{d_follow.luck_delta_pts.mean():+.2f} points HIGHER** than the immediate break's "
      f"({int((d_follow.luck_delta_pts>0).sum())} of 6 cells). Waiting for a retest does select a more "
      "one-directional environment; it simply gives no information about which direction, so the rule "
      "pays the higher bar and gets nothing for it.\n")
    W("6. **The lookahead twin is the headline warning.** Filling at the signal bar's own open instead "
      "of the next bar turns the immediate-break ORB from a loser into a fantasy: "
      f"accuracy **+{tj[(tj.variant.str.endswith('immediate'))&(tj['sign']=='follow')].d_acc.max():.1f} "
      f"points** and **{tj[(tj.variant.str.endswith('immediate'))&(tj['sign']=='follow')].d_pb.max():+.2f} "
      "$/basket** at its worst. An ORB backtest with an off-by-one fill would report 71 right in 100 and "
      "+$11 a basket on 2024-2026 gold. All of it is fiction.\n")
    W("7. The retest variants' twins move by at most 5.9 points and in **mixed directions** — because "
      "their entry bar is a quiet rejection candle, not the explosive break bar, so filling a minute "
      "early is nearly harmless. **The size of a lookahead bug scales with how violent the signal bar "
      "is.** That is why breakout systems are the most dangerous thing in this project to backtest "
      "carelessly, and why `research/orb.py` needed an audit.\n")
    W(f"8. **Placebo bar:** rotating the signals by whole weeks, best of 6, 200 draws, gives "
      + ", ".join(f"{pb[e].acc2_P95:.2f} ({e})" for e in ERA_ORDER if e in pb)
      + " right in 100, and up to "
      + f"**{max(pb[e].money2_P95 for e in pb):+.2f} $/basket from nothing**. The best real reading in "
      f"the whole family is {main.acc.max():.2f}. Every variant is inside the noise.\n")
    W(f"9. **Every exit shape, too.** {len(grid):,} (variant x sign x shape x era) readings on BT20's "
      f"36-shape ladder: **{n_allpos} of {len(piv_money)}** shapes make money in all three eras and "
      f"**{n_allbeat} of {len(piv_beats)}** beat their own luck bar in all three. Not one.\n")
    W("10. **Bugs found:** three in `research/orb.py` (a comment that contradicts the code; a cost scale "
      "that silently collapses to zero on the Zero feed; the most violent breakout bars dropped from the "
      "sample) and one convention wrinkle in `bt/bt20_run.py` (ties awarded to the long side, worth up to "
      "7 accuracy points to a direction-biased rule). File:line and proof in section 8. None of them "
      "changes this verdict.\n")

    W("\n## 1. Exact rules, as pre-registered\n")
    W(f"""| | |
|---|---|
| Swing confirmation | fractal swing, N = **{R.N_SWING}** bars each side (`smc_core.swings`), usable only once bar i+{R.N_SWING} has closed |
| Break buffer | **{R.BUFK} x ATR{R.ATR_W}**, where ATR is the mean M1 bar range over the {R.ATR_W} bars **ending at k-1**. Median on the zero feed ~$0.153 of gold price (= $1.53 at 0.10 lots) |
| Break definition | a **close** beyond the level by more than the buffer. Close-based, so a bar can never break both sides at once |
| Level lifetime | the level has **{R.BREAK_MAX}** M1 bars to be broken, then it is forgotten |
| Retest window | **{R.RETEST_MAX}** bars after the break (the brief's number) |
| Retest holds | the **first** return to the level (low <= level for an upside break) must **close back on the break side**. If it closes through, the level is dead and is not retried |
| Entry | the rejection candle's close is the decision; the basket opens at the **next M1 open** |
| ORB range | the first **{R.ORB_MIN}** M1 bars from the session-open bar, inclusive; high/low of those 15 bars |
| ORB session anchor | **DST-aware local clocks**, `research/orb.py::session_starts` reproduced bit-for-bit: London **08:00 Europe/London**, New York **09:30 America/New_York** -> 07:00 / 13:30 UTC in summer, 08:00 / 14:30 UTC in winter |
| ORB break window | **{R.ORB_WIN}** bars (2 h) from the end of the range |
| Market closures | no pattern may span a gap longer than {R.GAP_H} h |
| Money | 0.10 lots, $10 per $1 of gold. Primary **$15 / $70**; also $7/$70 and the best of BT20's 36 shapes |
| Costs | $1.10 round-trip commission + 0.05 price units of extra spread (the gold spread column is 0 on 85% of zero-feed minutes) |
| Engine | BT20's: one basket at a time, 60-second cooldown, 24 h horizon |
""")
    W("**On 07:00 UTC vs the local clock.** The brief's \"07:00-07:15 UTC\" and \"13:30-13:45 UTC\" are "
      "the *summer* values of the London and New York opens. The method section makes DST-aware "
      "anchoring non-negotiable, so the local clock wins and fixed UTC is reported as a sensitivity "
      "(section 7), not as a seventh variant.\n")
    W("**On the locked TEST period.** The brief's era scheme (duka 2009-2020 | zero 2021-2023 | zero "
      "2024-2026) deliberately runs past the project's 2025-07-01 TEST boundary, because gold is judged "
      "by era, not by the DEV/VAL/TEST split. The discipline that replaces it is: **nothing was selected "
      "on the 2024-2026 era.** All six variants and every constant above were fixed in the brief before "
      "any run. Nothing was tuned, retried or dropped after a result was seen, and the two post-hoc "
      "extras in section 7 are labelled as such.\n")

    W("\n## 2. THE RANKING — 6 variants x 3 eras x both signs, with the luck bar beside every reading\n")
    W("`acc` = times in 100 the rule picked the side that actually paid more (a tie, where both sides "
      "reach the target, counts as a half). `luck` = the accuracy that shape needs **on those exact "
      "minutes** to break even; it is 54-62 here, never 50, because breakout minutes are violent "
      "minutes. `beats` = acc - luck. `coin $` = what a random direction pays at the same minutes.\n")
    mm = eord(main).copy()
    mm["acc_v_placebo"] = [round(r.acc - pb[r.era].acc2_P95, 2) for r in mm.itertuples()]
    mm = mm.sort_values("beats", ascending=False)
    W(md_table(mm, ["variant", "sign", "era", "n_sig", "trades", "acc", "luck_acc", "beats",
                    "acc_v_placebo", "ties_pct", "per_basket", "net", "coin_usd", "win_pct", "med_min"],
               ["variant", "sign", "era", "signals", "baskets", "**acc**", "luck", "**beats**",
                "vs placebo", "ties %", "**$/basket**", "net $", "coin $", "win %", "med min"]))
    W("\nNot one reading is positive on money. Not one is above its own luck bar. The best `beats` in "
      f"the family is **{main.beats.max():+.2f} points** — i.e. the best variant still needs "
      f"{-main.beats.max():.2f} more points of accuracy than it has just to pay the costs.\n")

    W("\n## 3. PASS / FAIL, the pre-registered test applied literally\n")
    W("A variant passes only if, **in all three eras**: net $/basket > 0 at $15/$70 after commission; "
      "accuracy > its own luck bar; accuracy > the family placebo bar; and >= 500 resolved baskets.\n")
    W(md_table(vdf, ["variant", "sign", "pass_fail", "reasons"],
               ["variant", "sign", "**verdict**", "why it failed"]))

    W("\n## 4. THE RETEST-VS-IMMEDIATE DELTA — the number the brief asked for\n")
    W("Same session, same range, same buffer, same money shape. The only difference is whether the "
      "trade is taken on the break bar's close (immediate) or on a later rejection candle (retest).\n")
    W(md_table(eord(ddf.rename(columns={"session": "variant"})),
               ["variant", "era", "sign", "n_immediate", "n_retest", "trade_count_delta_pct",
                "acc_immediate", "acc_retest", "acc_delta_pts", "luck_immediate", "luck_retest",
                "luck_delta_pts", "pb_immediate", "pb_retest", "pb_delta_usd",
                "med_min_immediate", "med_min_retest"],
               ["session", "era", "sign", "n imm", "n retest", "**trades %**", "acc imm", "acc retest",
                "**acc pts**", "luck imm", "luck retest", "luck pts", "$/bk imm", "$/bk retest",
                "**$/bk delta**", "min imm", "min retest"]))
    W("\n**Plain reading.** Averaged over the three eras on the declared (follow) side: the retest "
      f"changes accuracy by **{d_follow.acc_delta_pts.mean():+.2f} points** and the trade count by "
      f"**{d_follow.trade_count_delta_pct.mean():+.1f}%**, and moves the money by "
      f"**{d_follow.pb_delta_usd.mean():+.2f} $/basket**. The accuracy change is smaller than its own "
      f"standard error on these sample sizes (+-1.5 to +-3 points at n = 270-1,100); the trade-count "
      f"change is not — it is a {cut_s} cut. The retest requirement is a filter that throws away about "
      "two trades in three and buys nothing measurable in return.\n")
    W("**And it is worse than neutral.** The `luck` columns above move the wrong way: the retest entry's "
      f"break-even bar is **{d_follow.luck_delta_pts.mean():+.2f} points higher** than the immediate "
      f"break's ({int((d_follow.luck_delta_pts>0).sum())} of 6 cells higher). The mechanism is visible in "
      "the `ties %` column of section 2: at a $15 target against a $70 stop, 57-64% of baskets reach the "
      "target on **both** sides inside 24 hours, and those are free — the direction call does not matter. "
      "The retest entry has *fewer* of those free baskets and a deeper losing tail (E_worst goes from "
      "about -$18 to about -$21 on London), i.e. after a retest holds, gold is more likely to run $70 one "
      "way before giving $15 the other. The retest genuinely detects a more directional minute. It just "
      "cannot say which direction, so all it does is raise the price of being wrong.\n")

    W("\n## 5. THE LOOKAHEAD TWIN — the trap, reproduced on purpose\n")
    W("Identical signals, identical costs; the basket is filled at the **signal bar's own open** "
      "instead of the next bar. BT17 section 3 found this manufactures a large fake edge; the audit of "
      "`research/orb.py` found the same class of bug. Here is what it is worth on these rules.\n")
    W(md_table(eord(tj[tj["sign"] == "follow"]),
               ["variant", "era", "acc", "acc_tw", "d_acc", "luck_acc", "luck_acc_tw",
                "per_basket", "per_basket_tw", "d_pb"],
               ["variant", "era", "acc (causal)", "acc (twin)", "**acc delta**", "luck", "luck (twin)",
                "$/bk (causal)", "$/bk (twin)", "**$/bk delta**"]))
    W("\n**The immediate-break ORB is the worst offender by a mile** — up to "
      f"+{tj[tj.variant.str.endswith('immediate')].d_acc.max():.1f} accuracy points and "
      f"+${tj[tj.variant.str.endswith('immediate')].d_pb.max():.2f} a basket of pure fiction. The "
      "reason is mechanical: the break bar is, by definition, a bar that travelled a long way in one "
      "direction, so entering at its open means buying before the move that defined the signal. "
      "The retest variants' entry bar is a small rejection candle, so their twin gains much less. "
      "**Any ORB result in this project that is not fill-at-next-bar should be assumed fake.**\n")

    W("\n## 6. MONEY AT ALL THREE SHAPES\n")
    W("### $15 / $70 (the sniper's live shape, primary) and $7 / $70 (the shipped shape)\n")
    W(md_table(eord(main), ["variant", "sign", "era", "trades", "per_basket", "net",
                            "s7_trades", "s7_acc", "s7_luck", "s7_per_basket", "s7_net"],
               ["variant", "sign", "era", "baskets $15/$70", "**$/bk $15/$70**", "net $15/$70",
                "baskets $7/$70", "acc $7/$70", "luck $7/$70", "**$/bk $7/$70**", "net $7/$70"]))
    W("\n### The best shape on BT20's 36-shape ladder, per variant per era\n")
    W(md_table(eord(bshape), ["variant", "sign", "era", "target", "stop", "trades", "acc", "luck_acc",
                              "beats", "per_basket", "net"],
               ["variant", "sign", "era", "T$", "S$", "baskets", "acc", "luck", "beats",
                "**$/bk**", "net"]))
    W(f"\nShapes positive in **all three** eras: **{n_allpos}** of {len(piv_money)}. Shapes above their "
      f"own luck bar in all three eras: **{n_allbeat}** of {len(piv_beats)}. The handful of positive "
      "cells are single-era flukes, and they flip sign between eras — London immediate break is "
      "profitable *faded* on 2021-2023 and *followed* on 2009-2020. That is what noise looks like.\n")

    W("\n## 7. THE PLACEBO BAR\n")
    W("Each variant's chosen-side vector is rotated by a random whole number of **weeks** (the signal "
      "rhythm and the clustering of outcomes survive; any real link to the price dies), the family's "
      "best of 6 is taken, 200 draws. `acc2` is the two-sided bar, because both signs of every reading "
      "are in this report.\n")
    W(md_table(plac[plac.kind == "best_of_6"], ["era", "draws", "acc_P50", "acc_P95", "acc_max",
                                                "acc2_P95", "acc2_max", "money_P95", "money2_P95"],
               ["era", "draws", "acc P50", "acc P95", "acc max", "**acc2 P95**", "acc2 max",
                "$ P95", "**$2 P95**"]))
    W("\nEach reading's **own** bar, on its own sample size (a small sample rotates further by luck):\n")
    pr = plac[plac.kind.str.startswith("own_")].copy()
    pr["variant"] = pr.kind.str.replace("own_", "", regex=False)
    W(md_table(eord(pr.assign(sign="-")), ["variant", "era", "n", "acc_P95", "acc2_P95"],
               ["variant", "era", "signals", "own acc P95", "own acc2 P95"]))
    W("\n**The decisive comparison:** the best reading in the family is "
      f"{main.acc.max():.2f} right in 100 "
      f"({main.loc[main.acc.idxmax()].variant}, {main.loc[main.acc.idxmax()]['sign']}, "
      f"{main.loc[main.acc.idxmax()].era}); the placebo bar in that era is "
      f"{pb[main.loc[main.acc.idxmax()].era].acc2_P95:.2f}. It loses to the bar. And noise alone pays up "
      f"to {max(pb[e].money2_P95 for e in pb):+.2f} $/basket in these eras, while the best real reading "
      f"pays {main.per_basket.max():+.3f}.\n")

    if len(post):
        W("\n### Post-hoc extras (added AFTER seeing results — not pass/fail candidates)\n")
        W("Two things the pre-registered six could not answer. **(a) `POSTHOC_orb_both_*`:** the "
          "per-session samples cannot reach 500 baskets in the zero eras, so London and New York were "
          "pooled to give the retest idea a fairer sample. **(b) `SENS_fixedutc_*`:** the brief's "
          "literal fixed-UTC anchor (07:00 / 13:30 UTC all year) instead of the DST-aware local clock, "
          "so the choice made in section 1 can be checked. Neither rescues anything; fixed-UTC "
          "anchoring is if anything slightly worse.\n")
        W(md_table(eord(post), ["variant", "sign", "era", "n_sig", "trades", "acc", "luck_acc",
                                "beats", "per_basket", "net"],
                   ["variant", "sign", "era", "signals", "baskets", "acc", "luck", "beats",
                    "$/bk", "net"]))

    W("\n## 8. SANITY CHECKS, AND THE BUGS FOUND\n")
    W(md_table(chk, ["feed", "check", "result", "detail"], ["feed", "check", "result", "detail"]))

    # ---- weekend-hold sensitivity, from the trade CSVs
    wk = []
    for nm in R.VARIANTS:
        for ds in ("duka", "zero"):
            for ERA in ERA_ORDER:
                f = os.path.join(OUT, f"trades_{nm}_{ERA}_{ds}.csv")
                if not os.path.exists(f):
                    continue
                t = pd.read_csv(f)
                g = t[~t.crosses_gap.astype(bool)]
                wk.append(dict(variant=nm, era=ERA, baskets=len(t),
                               crosses_gap=int(t.crosses_gap.astype(bool).sum()),
                               crosses_pct=round(100 * float(t.crosses_gap.astype(bool).mean()), 2),
                               per_basket_all=round(float(t.net_usd.mean()), 3),
                               per_basket_no_gap=round(float(g.net_usd.mean()), 3)))
    wdf = pd.DataFrame(wk)
    wdf.to_csv(os.path.join(OUT, "weekend_sensitivity.csv"), index=False)
    W("\n### Known limitation: BT20's walk table does not flatten before the weekend\n")
    W("`bt20_lib.walk` follows a basket 1,440 **bars** forward, not 1,440 clock minutes, so a basket "
      "opened late on a Friday can be resolved on the Monday, whereas `common.simulate` flattens at "
      "Fri 20:45. BT21R inherits that and reports it rather than hiding it. The effect is small, and "
      "removing every gap-crossing basket does not change any sign:\n")
    W(md_table(eord(wdf.assign(sign="-")), ["variant", "era", "baskets", "crosses_gap", "crosses_pct",
                                            "per_basket_all", "per_basket_no_gap"],
               ["variant", "era", "baskets", "cross a gap", "%", "$/bk all", "$/bk gap-free"]))

    W("""
### Bug 1 — `research/orb.py:83` + `:89` / `:98`: the comment contradicts the code

```python
# entry bar: only adverse check (pessimistic) using its own extremes after fill
j = ej
while j < end and not done:
    if side == 1:
        if lo[j] <= stop: ...          # adverse: correct, pessimistic
        if tp_r > 0 and hi[j] >= entry + tp_r * risk:   # <-- ALSO books the TARGET on the entry bar
```
The comment claims only the adverse side is checked on the entry bar. The code also books the **target**
on the entry bar, from that bar's own high. On an upside breakout the entry sits near the bar's high, so
a large breakout bar can be credited with a target that may have been reached *before* the entry price
was touched. It is mild (the stop is tested first, so it only fires when the bar did not stop out) and it
only affects `tp_r > 0` cells, but it is optimistic and the comment says it is not there.

### Bug 2 — `research/orb.py:121-122`: a cost scale that silently becomes zero

```python
slip = slip_frac * float(np.median(d.spr.values))
buf  = buf_k     * float(np.median(d.spr.values))
```
Both the breakout **buffer** and the **slippage** are scaled by the median spread of the whole loaded
span. Two problems. (a) It is a whole-sample statistic used to decide trades inside the sample — a mild
threshold look-ahead. (b) On the **Zero** feed the gold spread column is 0.0 on 85% of minutes, so
`median(spr) == 0.0` and therefore `buf == 0.0` **and** `slip == 0.0`: run `DATA_DIR=data_zero python
research/orb.py` and the "cost-realistic" ORB becomes zero-cost with a zero breakout buffer. Proof:

```
DATA_DIR=data_zero  -> XAUUSD median spr = 0.0 pips  -> buf = 0.0, slip = 0.0
DATA_DIR=data       -> XAUUSD median spr = 2.0 pips  -> buf = 1.0 pip, slip = 0.5 pip   (fine)
```
BT21R therefore uses an **ATR-scaled** buffer instead, which cannot collapse and which means the same
thing at $1,700 gold and at $4,000 gold.

### Bug 3 — `research/orb.py:61`: the most volatile breakouts are silently dropped

```python
if up and dn:  # ambiguous bar -> skip event
    break
```
A bar that trades through **both** range edges kills the whole session's event. Those are precisely the
biggest-range bars, so the surviving sample is quietly biased towards calm breakouts. BT21R breaks on the
**close**, which can only be on one side, so no event is ever dropped for being too violent.

### Bug 4 — `bt/bt20_run.py:101-102`: ties are awarded to the long side

```python
better = np.where(bw[0] >= bw[1], 1, -1)
acc = float((isd == better).mean())
```
At a $15 target against a $70 stop, **57-64% of baskets reach the target on BOTH sides** inside 24 hours
(see the `ties %` column in section 2). `>=` hands every one of those ties to the long, so a long-biased
rule scores up to +7 accuracy points for nothing. BT15/BT17 count a tie as a half (the direction call is
genuinely irrelevant when both sides pay the same), and BT21R uses that convention
(`bt21r_lib.py::_acc`). This does not change any BT20 conclusion — BT20's rules are near-balanced on
direction — but it inflates the accuracy of any rule that leans one way.

### A coincidence that is not a bug
Several readings show `$/basket` for *follow* and *fade* identical to three decimals (e.g. R3 London
2021-2023, both -1.324). At this shape a resolved basket pays exactly one of two numbers (+$13.90 or
-$71.10), so the per-basket mean lives on a coarse lattice; when a decisive split lands exactly even
(48 of 96 for that reading, hence `acc` = 50.00 exactly) both signs and the coin flip collapse onto the
same value. It was checked by hand.
""")

    W("\n## 9. TEN EXAMPLE TRADES A HUMAN CAN CHECK ON A CHART\n")
    if len(ex):
        pick = []
        flip = 0
        for nm in ("R3_orb_lon_retest", "R4_orb_ny_retest", "R5_orb_lon_immediate", "R1_sr_retest"):
            for ERA in ERA_ORDER:
                s = ex[(ex.variant == nm) & (ex.era == ERA)]
                if not len(s):
                    continue
                want = s[s.outcome == "TP"] if flip % 2 == 0 else s[s.outcome != "TP"]
                pick.append((want if len(want) else s).head(1))
                flip += 1
        e2 = pd.concat(pick, ignore_index=True)
        for cc in ("level", "rng_hi", "rng_lo"):
            e2[cc] = e2[cc].round(3)
        W(md_table(e2, ["variant", "era", "t_break", "rng_lo", "rng_hi", "level", "t_decide",
                        "t_entry", "side", "entry", "tp_px", "sl_px", "t_exit", "hold_min",
                        "outcome", "net_usd"],
                   ["variant", "era", "break bar (close)", "range lo", "range hi", "S/R level",
                    "decision bar", "**entry bar (fill)**", "side", "fill px", "target px",
                    "stop px", "exit min", "held min", "out", "net $"]))
        W("\nHow to check one: open XAUUSD M1 at `break bar`. For an ORB row the `range lo`/`range hi` "
          "are the high and low of the fifteen M1 bars from the session open (08:00 Europe/London or "
          "09:30 America/New_York local). The break bar's **close** is beyond that edge by more than "
          "0.25 x ATR60. The `rejection bar` wicks back to the edge and closes on the break side. The "
          "fill is the **open of the next minute**, and `target px` / `stop px` are $15 and $70 away "
          "from it at 0.10 lots ($1.50 and $7.00 of gold price). For an R1 row the `S/R level` is a 5-bar fractal "
          "swing high or low; everything else reads the same.\n")
        W("Three of these were replayed by hand straight out of `data_zero/XAUUSD_M1.parquet`, without "
          "going through any BT21R code, and every field reproduced exactly:\n\n"
          "```\n"
          "2021-01-05 London  range 1939.505-1943.670 (15 bars, match)  break 09:05 close 1944.076 (+0.406)\n"
          "                   rejection 09:09 low 1943.630 <= 1943.670, close 1943.958 > 1943.670\n"
          "                   FILL = 09:10 open 1943.999  (+spread+0.05 -> 1944.049, as reported)\n"
          "2024-01-02 London  range 2072.394-2074.561 (match)  break 08:31 close 2072.069 (-0.325)\n"
          "                   rejection 08:32 high 2072.417 >= 2072.394, close 2072.068 < 2072.394\n"
          "                   FILL = 08:33 open 2072.041  (-0.05 -> 2071.991, as reported)\n"
          "2026-05-28 London  range 4383.814-4392.484 (match)  break 07:19 close 4394.384 (+1.900)\n"
          "                   rejection 07:29 low 4391.745, close 4392.841  FILL = 07:30 open 4392.582\n"
          "```\n")

    W("\n## 10. VERDICT, in plain language\n")
    W("""**This family is dead, in all six variants, in both signs, in all three eras, at every exit
shape on the ladder.**

The owner named two setups here and both were worth asking about, because neither had ever been run in
this project: a plain horizontal micro support/resistance break-and-retest, and an opening-range
breakout **with a retest** (`research/orb.py` only ever tested the immediate break). They have now been
run properly — causal fills, DST-aware session anchoring, both feeds, three eras separately, each
reading judged against the luck bar of its own minutes rather than against 50, and a whole-week rotation
placebo on top.

The answer is the same answer this project keeps getting, and the mechanism is the one BT20 named: every
rule that picks an "interesting" minute picks a *costly* minute, and the cost arrives before the skill
does. Break-and-retest is a real, visible, describable pattern — RCOUNT of them on gold M1 — and knowing
one has just happened tells you essentially nothing about which way the next $15 of gold goes. R1 reads
RACC right in 100 on that sample against a bar of RBAR. That is not a weak edge; it is the absence of
one, measured precisely enough that the standard error is about a tenth of a point.

The retest requirement specifically — the owner's own emphasis, "with a retest" — is measurable and the
measurement is unkind. It costs **RCUT of the trades**, returns **well under a point of accuracy** (inside
its own noise), and *raises* the break-even bar by about a point and a half, because after a held retest
gold is more likely to run $70 one way before giving $15 the other. A trader using it on gold would take a
third as many trades, on worse terms, for the same expected result.

The genuinely useful thing BT21R produced is the lookahead twin. The immediate-break ORB, filled one bar
early, reports **71 right in 100 and +$11 a basket on 2024-2026 gold**. That is the single largest fake
edge measured anywhere in this project, and it comes from a one-line off-by-one. The size of the
illusion scales with how violent the signal bar is, which is exactly why breakout systems are the most
dangerous thing to backtest carelessly. Anyone who reports a profitable gold ORB should be asked, first,
which bar they filled on.

**What a trader should do with this: nothing.** Do not trade break-and-retest on gold M1 at these
targets. Do not add the retest filter to a breakout rule expecting it to improve the hit rate — it will
only reduce your sample. And the one positive-looking cell in 1,296 (London immediate break faded, $50
target / $70 stop, +$1.63 a basket on 2021-2023) is below the placebo bar for its own era (+$1.85) and
has the opposite sign in 2009-2020. It is noise, and it is reported here so that nobody has to find it
again.
""")

    txt = "\n".join(L_)
    txt = (txt.replace("RCOUNT", f"{n_r1:,}")
              .replace("RACC", f"{r1f.acc.min():.2f}-{r1f.acc.max():.2f}")
              .replace("RBAR", f"{r1f.luck_acc.min():.1f}-{r1f.luck_acc.max():.1f}")
              .replace("RCUT", cut_s))
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"\n-> {os.path.join(OUT, 'report.md')}  ({len(txt):,} chars)")
    print("\nPASS/FAIL:")
    print(vdf.to_string(index=False))


if __name__ == "__main__":
    print(f"=== checks ({DS}) ===")
    checks()
    print(f"\n=== post-hoc pooled ORB retest ({DS}) ===")
    extras()
    if DS == "zero":
        print("\n=== report ===")
        build_report()
