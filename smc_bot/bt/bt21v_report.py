"""BT21 Family V report — builds results/BT21V/report.md from the CSVs bt21v_run.py wrote.

    .venv/Scripts/python.exe smc_bot/bt/bt21v_report.py

Reads only CSVs, so it needs no dataset and touches no prices.
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "results", "BT21V")
ERAS = ["duka 2009-2020", "zero 2021-2023", "zero 2024-2026"]
VIDS = ["V1", "V2", "V3", "V4", "V5", "V6"]
DESC = {
    "V1": "day-anchored VWAP (00:00 UTC) · pullback touches VWAP · close back on the trend side → FOLLOW",
    "V2": "V1 FADED (exact mirror of V1 on the same triggers)",
    "V3": "London-anchored VWAP (08:00 Europe/London, DST-aware) · rule as V1 → FOLLOW",
    "V4": "NY-anchored VWAP (09:30 America/New_York, DST-aware) · rule as V1 → FOLLOW",
    "V5": "day-anchored VWAP ±1σ · close back INSIDE the band → revert toward VWAP",
    "V6": "day-anchored VWAP ±2σ · close back INSIDE the band → revert toward VWAP",
}


def rd(stem):
    return pd.concat([pd.read_csv(os.path.join(OUT, f"{stem}_{d}.csv")) for d in ("duka", "zero")],
                     ignore_index=True)


def _fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        f = float(v)
        if f == int(f) and abs(f) >= 1000:
            return f"{int(f):,}"
        return f"{f:.4g}"
    return str(v)


def tbl(df):
    """Markdown table without a third-party dependency (this venv has no `tabulate`)."""
    d = df.reset_index() if isinstance(df.index, pd.MultiIndex) or df.index.name else df
    cols = [str(c) for c in d.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in d.iterrows():
        out.append("| " + " | ".join(_fmt(v) for v in row.tolist()) + " |")
    return "\n".join(out)


def main():
    vq = rd("volquality")
    r = rd("readings")
    lad = rd("ladder")
    eng = rd("engine")
    twin = rd("twin")
    pl = rd("placebo")
    hrs = rd("hours")
    P = r[(r.target == 15) & (r.stop == 70)]
    S = r[(r.target == 7) & (r.stop == 70)]
    W = P[~P.variant.str.endswith("u")].set_index(["era", "variant"])
    own = {e: {v: float(pl[pl.era == e][f"own_{v}"].iloc[0]) for v in VIDS} for e in ERAS}
    bar = {e: float(pl[pl.era == e].acc2_P95.iloc[0]) for e in ERAS}
    NSIG = int(P[~P.variant.str.endswith("u")].n.sum())
    NDIST = NSIG - int(P[(P.variant == "V2")].n.sum())   # V2 reuses V1's triggers
    best2 = P[~P.variant.str.endswith("u")].sort_values("acc", ascending=False)
    top = best2.iloc[0]

    # ---------------- pass / fail, applied literally
    verdict = {}
    for v in VIDS:
        why = []
        for e in ERAS:
            x = W.loc[(e, v)]
            if x.usd <= 0:
                why.append(f"{e}: net ${x.usd:.2f}/basket ≤ 0")
            if x.acc <= 50:
                why.append(f"{e}: accuracy {x.acc:.2f} ≤ 50")
            if x.win_pct <= x.luck_win_pct:
                why.append(f"{e}: win% {x.win_pct:.2f} ≤ same-minutes coinflip {x.luck_win_pct:.2f}")
            if x.usd <= x.luck_usd:
                why.append(f"{e}: $/basket below the same-minutes coinflip ({x.luck_usd:.2f})")
            if x.acc <= bar[e]:
                why.append(f"{e}: accuracy {x.acc:.2f} ≤ placebo bar {bar[e]:.2f}")
            if x.n < 500:
                why.append(f"{e}: only {int(x.n)} baskets")
        verdict[v] = ("PASS", []) if not why else ("FAIL", why)

    L = []
    A = L.append
    A("# BT21 — Family V: VWAP on gold M1\n")
    A("**Pre-registered in `bt/BT21_BRIEF.md` before any code was written. 6 variants (V1–V6), FIXED; "
      "nothing was added, nothing post-hoc.** Both signs of every variant are reported (a mirror is exactly "
      "`100 − accuracy` on the same triggers, so it is not a 7th reading). Money is **0.10 lots** at our "
      "costs: **$1.10 round-trip commission + 0.05 extra spread**. Primary shape **$15 target / $70 stop**; "
      "$7/$70 and the best rung of the 14×14 LADDER also reported. Three eras, never pooled.\n")

    # ---------------- ten lines
    A("## The ten lines\n")
    A(f"1. **VWAP had never been tested in this project. It is now — {NSIG:,} measured chances "
      f"({NDIST:,} distinct signal minutes) across 18 calendar years, 2009–2026 — and it is a clean "
      f"negative. All six variants FAIL the pre-registered test.**")
    A("2. **The question the owner told me to settle first killed half of it before the strategy did: "
      "Dukascopy's `tick_volume` is the integer 1 on every one of its 5,526,570 gold M1 bars (2009–2024).** "
      "It is not 'suspiciously flat', it is a constant. For the whole 2009–2020 era a volume-weighted VWAP "
      "**is** the unweighted mean, by arithmetic — and the weighted and unweighted signal sets came out "
      "bit-identical (asserted, `sanity_duka.txt`). Only the Exness Zero feed has a real volume column.")
    A("3. Where real tick volume exists, **it adds nothing.** It shifts the VWAP line by a median of **$0.39** "
      "(≈ two thirds of one M1 bar's range), reshuffles *which* minutes fire (only ~27% of triggers shared "
      "with the unweighted control) and leaves the answer unchanged: mean |accuracy difference| **0.25 "
      "points**, worst **0.62** — inside one standard error (≈0.35).")
    A(f"4. Best of all 36 readings: **{top.acc:.2f} right in 100** ({top.variant}, {top.era}, "
      f"{int(top.n):,} signals). Break-even at the primary shape is **{top.xstar:.1f}**. The gap is about "
      f"**7 points**, not a rounding error.")
    A("5. **No variant is above 50 in all three eras except V2 (+0.04 / +0.07 / +0.34) — which is literally "
      "V1 with the side flipped**, and every one of those margins is below that variant's own week-rotation "
      "placebo bar (50.29 / 50.53 / 50.52). Noise with a consistent sign is not an edge.")
    A("6. **Money: every variant loses in every era at every headline shape.** At $15/$70, **−$0.80 to "
      "−$3.09 a basket** net; the robot replayed one-basket-at-a-time loses **$135 to $1,316 a month**. The "
      "shipped $7/$70 shape is worse for every variant, because its break-even is 59.6–65.0 right in 100.")
    A("7. **Judged against its own luck bar, not against 50.** A random direction at exactly these minutes "
      "wins ~80% of baskets at $15/$70 and loses ~$2.4 a basket; the rules win ~80% and lose ~$2.5. "
      "**Half the readings sit *below* their own coin flip.**")
    A("8. **The lookahead twin is the loudest result in the file.** Filling at the signal bar's own open "
      "leaves V1–V4 unchanged (−0.2 to −1.3 points: no fake edge) but hands V5/V6 **+4.15 to +11.96 "
      "accuracy points and up to +$9.69 a basket** — in zero 2024-2026 V5 reads **61.84** instead of 49.88. "
      "BT17 §3 reproduced exactly, on the band rules specifically.")
    A("9. **Nothing hides in the rollover hours.** 20:00–24:00 UTC is negative for every variant in every "
      "era; the broker's daily break hour is excluded from every signal and a check asserts it. The 23 "
      "hour-cells above 50 in all three eras are what 144 cells give by luck (expectation ≈ 18), and none "
      "makes money.")
    A("10. **Verdict: VWAP on gold M1 is a non-finding, and `research_notes/M1_entry_candidates.md` was "
      "right for exactly the reasons it gave** — no real volume, no natural day start. The first turned out "
      "to be absolute on 12 of the 18 years, so the second never even got a chance to matter.\n")

    # ---------------- pass/fail
    A("## 1. The pre-registered pass/fail, applied literally\n")
    A("A variant passes only if ALL of: (1) net $/basket > 0 at $15/$70 in **all three eras**; (2) accuracy > "
      "its own `coinflip_same_minutes` bar in **all three eras**; (3) accuracy > the family's placebo bar; "
      "(4) ≥ 500 resolved baskets in **each** era.\n")
    A("**How criterion 2 was read.** `coinflip_same_minutes` is *a random direction at exactly this rule's "
      "entry minutes*. In the **accuracy** currency that bar is **50.00 by construction** — ties count a "
      "half, so a coin scores 50 exactly (a seeded random-sign draw confirms it). The bar that is *not* 50 is "
      "the same coin flip measured in **win %** and in **$/basket**: with a $15 target and a $70 stop about "
      "**80% of baskets close green whichever side you pick**, so a rule reading 80.4% wins can still be "
      "below luck — this is what killed BT16's RSI fade and BT17's BOS fade. This report requires the "
      "variant to beat the coin flip in **all three currencies** (the strictest honest reading) and prints "
      "all three beside every number.\n")
    A(tbl(pd.DataFrame([dict(variant=v, rule=DESC[v], result=f"**{verdict[v][0]}**",
                             conditions_broken=len(verdict[v][1]),
                             first_reason_it_fails=(verdict[v][1][0] if verdict[v][1] else "—"))
                        for v in VIDS])))
    A("\n**6 of 6 FAIL.** Not one variant clears even the first condition in a single era, let alone all "
      "three. Per the brief, a fail is a fine result and is reported as a fail.\n")

    # ---------------- volume quality
    A("## 2. Is a tick-volume VWAP a usable proxy at all? — settle this before trusting any V-number\n")
    A("Per calendar year of each feed's own `tick_volume`, read straight from the M1 parquet "
      "(`bt.common.bars()` drops the column, so the alignment to the cached bars is **asserted** on "
      "timestamps and opens, not assumed).\n")
    A(tbl(vq.assign(year=vq.year.astype(str))))
    A("\n### Which eras have usable volume — stated plainly\n")
    A("| era | feed | volume verdict |")
    A("|---|---|---|")
    A("| **2009–2020** | duka | **UNUSABLE — the column is the constant 1.** 5,526,570 bars, **1 distinct "
      "value**, cv = 0, min = max = 1. A volume-weighted VWAP here is *identically* the unweighted running "
      "mean. Every V-number for this era is an **unweighted** result whatever it is labelled. |")
    A("| **2021–2023** | zero | **usable.** 781–966 distinct values a year, mean 69–78 ticks/min, cv ≈ 0.8. |")
    A("| **2024–2026** | zero | **usable, and not stationary.** mean 113 → 216 → 278 ticks/min, max 8,275. "
      "The *level* quadruples across the era, so the column is not comparable between eras either — only "
      "within-window shape is meaningful, which is all an anchored VWAP uses. |")
    A("\nThis is a data finding, not a strategy finding, and it is the more durable of the two: **no "
      "volume-weighted anything can be tested on the Dukascopy gold feed in this project.** Anything in this "
      "repo that weights by `tick_volume` while reading `SMC_DATASET=duka` is silently computing the "
      "unweighted version and will agree with it to the last bit.\n")

    # ---------------- ranked table
    A("## 3. THE RANKING — 6 variants × 3 eras × both signs, luck bar beside every reading\n")
    A("Primary shape **$15/$70**, all signals (no throughput rule). `acc` = share of chances where the "
      "declared side was the better-paying side (ties = ½, so a coin = 50.00). `luck_win_pct` / `luck_usd` = "
      "the same-minutes coin flip. `own_BE` = the accuracy this shape needs at these minutes to break even. "
      "`placebo` = best-of-6 week-rotation bar for the era; `own_placebo` = that variant's own bar. "
      "`week_t` = t-stat of $/basket bootstrapped over calendar weeks.\n")
    rk = []
    for e in ERAS:
        for v in VIDS:
            x = W.loc[(e, v)]
            rk.append(dict(era=e, reading=v, sign="declared", n=int(x.n), per_day=x.per_day, acc=x.acc,
                           luck_acc=50.0, win_pct=x.win_pct, luck_win_pct=x.luck_win_pct, usd=x.usd,
                           luck_usd=x.luck_usd, own_BE=x.xstar, placebo=bar[e], own_placebo=own[e][v],
                           week_t=x.week_t))
            rk.append(dict(era=e, reading=v + "_MIRROR", sign="flipped", n=int(x.n), per_day=x.per_day,
                           acc=round(100 - x.acc, 2), luck_acc=50.0, win_pct=np.nan,
                           luck_win_pct=x.luck_win_pct, usd=x.flipped_usd, luck_usd=x.luck_usd,
                           own_BE=x.xstar, placebo=bar[e], own_placebo=own[e][v], week_t=np.nan))
    rkd = pd.DataFrame(rk)
    A(tbl(rkd.sort_values(["era", "acc"], ascending=[True, False])))
    A("\n(V2 is V1's mirror by construction, so `V1_MIRROR` and `V2` are the same reading. The brief fixed V2 "
      "as a variant so it is reported as one, but it carries no independent information.)\n")
    A("### Three-era consistency — the test this project keeps getting burned by\n")
    cons = []
    for v in VIDS:
        a = [float(W.loc[(e, v)].acc) for e in ERAS]
        u = [float(W.loc[(e, v)].usd) for e in ERAS]
        cons.append(dict(variant=v, **{f"acc {e}": round(a[i], 2) for i, e in enumerate(ERAS)},
                         above_50_all_3="YES" if min(a) > 50 else "no",
                         beats_own_placebo_all_3="YES" if all(a[i] > own[ERAS[i]][v] for i in range(3)) else "no",
                         money_positive_all_3="YES" if min(u) > 0 else "no",
                         worst_era_usd=round(min(u), 3)))
    A(tbl(pd.DataFrame(cons)))
    v2u = [float(W.loc[(e, "V2")].usd) for e in ERAS]
    v2a = [float(W.loc[(e, "V2")].acc) - 50 for e in ERAS]
    A(f"\nOne variant (V2) is above 50 in all three eras. It is V1 faded, its three margins are "
      f"{v2a[0]:+.2f}, {v2a[1]:+.2f} and {v2a[2]:+.2f} points — each below that variant's own rotation bar "
      f"({own[ERAS[0]]['V2']:.2f} / {own[ERAS[1]]['V2']:.2f} / {own[ERAS[2]]['V2']:.2f}) — and it loses "
      f"${-max(v2u):.2f}–${-min(v2u):.2f} a basket in every one of them. **That is a non-finding, and it is "
      f"the exact shape of BT18's 52.55 and BT20's COMEX momentum.**\n")

    # ---------------- weighted vs unweighted
    A("## 4. Volume-weighted VWAP vs the unweighted control\n")
    A("The control is the identical rule with every bar's volume set to 1 — a plain running mean of the "
      "typical price since the anchor. Primary shape, all signals.\n")
    q = P.copy()
    q["base"] = q.variant.str.rstrip("u")
    q["w"] = np.where(q.variant.str.endswith("u"), "unweighted", "weighted")
    t = q.pivot_table(index=["era", "base"], columns="w", values=["n", "acc", "usd"])
    t.columns = [f"{a}_{b}" for a, b in t.columns]
    t = t.reset_index()
    t["acc_diff"] = (t.acc_weighted - t.acc_unweighted).round(2)
    t["usd_diff"] = (t.usd_weighted - t.usd_unweighted).round(3)
    A(tbl(t))
    z = t[t.era != "duka 2009-2020"]
    A(f"\n- **duka 2009–2020: every difference is exactly zero.** The volume column is constant, so the two "
      f"are the same computation — the cleanest possible confirmation that the control is wired up right.")
    A(f"- **zero 2021–2026:** mean |accuracy difference| **{z.acc_diff.abs().mean():.3f}** points, worst "
      f"**{z.acc_diff.abs().max():.2f}**; mean |$ difference| **{z.usd_diff.abs().mean():.3f}**. The standard "
      f"error of accuracy on ~20k signals is ≈0.35 points, so **none of these is distinguishable from "
      f"noise**, and the sign of the difference flips from variant to variant.")
    A("- **How different are the two lines?** On the zero feed they correlate at **0.999995**, differ by a "
      "median of **$0.39** (p95 $4.45, ≈0.67 M1 bar ranges) and put price on **opposite sides of the line on "
      "only 2.52% of minutes**. The *triggers* differ a lot (27% shared) because a wick touch is a "
      "knife-edge condition; the *information* does not differ at all.")
    A("\n**Conclusion: the tick volume adds nothing. On 12 of the 18 years it cannot add anything, because it "
      "is a constant. The volume is not why VWAP failed — but it could never have been why it worked.**\n")

    # ---------------- lookahead twin
    A("## 5. The lookahead twin — the check that had to be run\n")
    A("Identical rule, identical walk table, one difference: the basket opens at the **signal bar's own "
      "open** instead of the next bar's open. Primary shape.\n")
    tw = twin[~twin.variant.str.endswith("u")]
    w2 = tw.pivot_table(index=["era", "variant"], columns="fill", values=["acc", "usd"])
    w2.columns = [f"{a} [{str(b).split('(')[0].strip()}]" for a, b in w2.columns]
    w2 = w2.reset_index()
    ln, nx = "acc [LOOKAHEAD own_bar_open]", "acc [next_bar_open]"
    lu, nu = "usd [LOOKAHEAD own_bar_open]", "usd [next_bar_open]"
    w2["FAKE_acc_gain"] = (w2[ln] - w2[nx]).round(2)
    w2["FAKE_usd_gain"] = (w2[lu] - w2[nu]).round(3)
    A(tbl(w2[["era", "variant", nx, ln, "FAKE_acc_gain", nu, lu, "FAKE_usd_gain"]]))
    A("\n**Read this table before anything else in the file.** The pullback rules (V1–V4) gain *nothing* "
      "from the cheat — the signature of a trigger that does not encode its own fill. The **band rules "
      "(V5, V6) gain +4.15 to +11.96 accuracy points and up to +$9.69 a basket**, because 'the close came "
      "back *inside* the band' **is** a statement that price moved toward the VWAP during that bar, so "
      "filling at that bar's open buys the condition itself. Every number in this report fills at the next "
      "bar, and a check asserts the entry bar is the very next real minute (0 violations, both feeds).\n")

    # ---------------- placebo
    A("## 6. The placebo bar — what the method invents from nothing\n")
    A("Each variant's chosen-side vector is rotated circularly by a random whole number of weeks (signal "
      "rhythm and outcome clustering survive; any real link dies), the best of the six is taken, 200 draws. "
      "`acc2` is the two-sided bar, which is the right one here because both signs are reported.\n")
    A(tbl(pl[["era", "draws", "n_readings", "acc_P50", "acc_P95", "acc_max", "acc2_P95", "acc2_max",
              "money_P95", "money2_P95"]]))
    A("\nEach variant's **own** two-sided rotation bar (judged on its own sample size rather than "
      "best-of-six):\n")
    A(tbl(pl[["era"] + [f"own_{v}" for v in VIDS]]))
    A(f"\nThe best real reading in the family is **{top.acc:.2f}**; the best-of-six placebo bar on that era "
      f"is **{bar[top.era]:.2f}**. **The family does not reach its own noise floor.** The placebo's money "
      f"bar is negative in all three eras as well, so there is no shape at which luck alone pays — that is "
      f"just the spread and the commission showing up.\n")

    # ---------------- money
    A("## 7. Money at all three shapes\n")
    A("### 7a. All signals, no throughput rule — $15/$70 (primary, the sniper's live shape) and $7/$70 "
      "(shipped)\n")
    mm = []
    for e in ERAS:
        for v in VIDS:
            a = W.loc[(e, v)]
            b_ = S[(S.era == e) & (S.variant == v)].iloc[0]
            mm.append(dict(era=e, variant=v, n=int(a.n), usd_15_70=a.usd, BE_15_70=a.xstar,
                           luck_15_70=a.luck_usd, usd_7_70=b_.usd, BE_7_70=b_.xstar, luck_7_70=b_.luck_usd))
    A(tbl(pd.DataFrame(mm)))
    A("\n**Every cell is negative, in every era, at both shapes.** Gross (the price move alone, before the "
      "$1.10 commission) is simply each figure + $1.10 and is still negative everywhere. The shipped $7/$70 "
      "shape is worse than the live $15/$70 one for every variant: break-even 59.6–65.0 right in 100 against "
      "53.1–58.1.\n")
    A("### 7b. The best rung of the LADDER — 14 targets × 14 stops = **196 shapes scanned, i.e. 196 trials**\n")
    lw = lad[~lad.variant.str.endswith("u")].copy()
    lw["edge"] = (lw.usd - lw.luck_usd).round(4)
    bb = lw.sort_values("usd", ascending=False).groupby(["era", "variant"], as_index=False).first()
    A(tbl(bb.sort_values(["era", "usd"], ascending=[True, False])))
    A(f"\n{int((lw.usd > 0).sum())} of {len(lw):,} (era × variant × shape) cells are positive and **every "
      "single one is at a $150–$200 target** — the far end of the ladder, where a 24-hour horizon on a "
      "trending metal pays. The giveaway is the column beside it: at those cells the **same-minutes coin "
      "flip is also positive** ($2.99 for V1/V2 in 2024-2026, against the rule's $2.89–$3.10). That is "
      "gold's 2024–26 uptrend being collected by any basket held long enough — not a VWAP edge. **No cell is "
      "positive in all three eras**, and the single best 'edge over luck' cells are different shapes in "
      "every era, which is what a 196-trial scan does to noise.\n")
    A("### 7c. The robot replayed — one basket at a time, 60-second cooldown\n")
    A(tbl(eng[~eng.variant.str.endswith("u")]))
    A("\nThe throughput rule rescues nothing; it scales the loss. Worth noticing for its own sake: the median "
      "hold at the **same** $15/$70 shape collapses from **48 minutes** (duka 2009-2020) to **4 minutes** "
      "(zero 2024-2026). Gold's minute ranges have grown so much that a $1.50 target now sits inside one "
      "bar. That is also why 2024-2026 loses the most per basket — both barriers arrive sooner and the "
      "commission is paid either way.\n")

    # ---------------- hours
    A("## 8. Hour of day — descriptive only, nothing was selected from it\n")
    hw = hrs[~hrs.variant.str.endswith("u")].copy()
    g = hw.pivot_table(index=["variant", "hour"], columns="era", values="acc")
    g["min_acc"] = g.min(axis=1)
    k = g[g.min_acc > 50].sort_values("min_acc", ascending=False).reset_index()
    A(f"Cells above 50 in **all three eras**: **{len(k)}** of 144 (6 variants × 24 hours). A coin flip on "
      f"three independent eras gives 144 × 0.5³ ≈ **18**. {len(k)} is what noise looks like. Top 12:\n")
    A(tbl(k.head(12)))
    A("\nThe **20:00–24:00 UTC** block specifically — the hours where BT16's false finding hid around the "
      "rollover:\n")
    lt = hw[hw.hour >= 20].groupby(["era", "variant"]).apply(
        lambda d: pd.Series({"n": int(d.n.sum()),
                             "acc": round(float((d.acc * d.n).sum() / d.n.sum()), 2),
                             "usd": round(float((d.usd * d.n).sum() / d.n.sum()), 3),
                             "luck_usd": round(float((d.luck_usd * d.n).sum() / d.n.sum()), 3)}),
        include_groups=False).reset_index()
    A(tbl(lt))
    A("\n**Every cell loses money.** The broker's daily break hour (21:00 UTC summer / 22:00 winter, median "
      "spread 0.304 against 0.040 elsewhere) is excluded from every signal in this report via "
      "`bt18_lib.break_mask`, and a check asserts no fill lands inside it.\n")

    # ---------------- examples
    A("## 9. Ten example baskets a human can check on a chart\n")
    A("`V3` — London-anchored VWAP pullback, zero feed, $15/$70, 0.10 lots. `t_signal` is the bar whose "
      "**close** decided it; `t_entry` is the next minute, where the basket opened. `fill` is the modelled "
      "fill (bid open + spread + 0.05 for a buy; bid open − 0.05 for a sell). Full set in "
      "`trades_V3_zero.csv`; all six variants and both feeds are in the folder.\n")
    ex = pd.read_csv(os.path.join(OUT, "trades_V3_zero.csv")).sample(10, random_state=21).sort_values("t_entry")
    A(tbl(ex))
    A("\nFour of these were re-derived by hand from the raw M1 bars, outside the pipeline — §10.\n")

    # ---------------- sanity
    A("## 10. The sanity checks that had to pass first\n")
    for d in ("duka", "zero"):
        A(f"**{d} feed** — `sanity_{d}.txt`:\n")
        A("```")
        A(open(os.path.join(OUT, f"sanity_{d}.txt")).read().strip())
        A("```\n")
    A("Checks run outside the script:\n")
    A("| check | result |")
    A("|---|---|")
    A("| Four example baskets re-derived by hand from the raw M1 bars — previous close vs VWAP, the wick "
      "touch, the close back on the trend side, the next bar's open + spread + 0.05 | all four reproduce the "
      "CSV exactly (e.g. 2021-08-27 07:16 → prev close 1803.694 above VWAP 1803.612; bar low 1803.583 ≤ VWAP "
      "1803.619 ≤ close 1803.738; fill 1803.692 + 0.020 + 0.05 = **1803.762**) |")
    A("| VWAP and σ recomputed by brute force from `bars ≤ k only` at 300 random bars | max VWAP error "
      "**5.7e-11**, max σ error **2.4e-8** |")
    A("| `MIN_BARS` since the anchor swept 15 → 30 → 60 → 120 (pre-registered value: 15) | V1/V3/V5 accuracy "
      "moves by ≤ **0.31** points and the money stays negative in every cell — the result is not an artefact "
      "of the warm-up length |")
    A("| Anchor DST handling | the DST-aware London anchor sits at 07:00 UTC on **59.9%** of days and 08:00 "
      "on the rest; NY at 13:30 on **66.6%** and 14:30 on the rest. The brief's fixed `07:00 / 13:30` is the "
      "**summer value only**, so following `research/orb.py::session_starts` moved 40% / 33% of the anchors. |")
    A("| Anchored-window coverage | day **100%** of minutes, London **92.2%**, NY **86.5%**. The uncovered "
      "minutes are Sunday-evening-open → Monday session open, which have no anchor of that session yet; they "
      "generate **no signals** rather than being silently attached to Friday's VWAP. |")
    A("| Float σ accumulation | first implementation accumulated `Σ v·tp²` globally, reaching ~1e15 where the "
      "float64 spacing (~0.1) swamped the variance of a 20-bar-old window; σ was wrong in the 4th decimal. "
      "Fixed by centring each window on its own first typical price (`bt21v_lib.py` `vwap_sigma`). Caught by "
      "the brute-force check, not by inspection. |")
    A("")

    # ---------------- shared code
    A("## 11. Shared code: one thing worth writing down (not a bug, but it must be known)\n")
    A("`smc_bot/bt/bt20_lib.py:_fp()` — lines **103–111** — prices every rung with a single `searchsorted` "
      "by shifting row *r* of the chunk by `r * OFF`:\n")
    A("```python\n# bt20_lib.py:106-109\nOFF = 1e7\nbase = (np.arange(C, dtype=np.float64) * OFF)[:, None]\n"
      "pos = np.searchsorted((run + base).ravel(), (levels[None, :] + base).ravel(), \"left\").reshape(C, -1)\n"
      "```")
    A("With `CHUNK = 1200` (`bt20_lib.py:41`) the shift reaches **1.2e10**, where the float64 spacing is "
      "**≈1.9e-6**. The table therefore counts a barrier as touched when the running P/L is within about "
      "**$2×10⁻⁶** of the rung.\n")
    A("**Proof and impact — measured on 6,000 random walked bars × both sides of the duka feed at $15/$70 "
      "(12,000 side-cases), replaying first passage from the raw bars:**\n")
    A("| comparison used in the from-scratch replay | first-passage minutes that differ | basket outcomes "
      "that differ | mean abs $ difference per basket |")
    A("|---|---|---|---|")
    A("| strict float `>=` | 27 / 12,000 (**0.23%**) | 1 / 12,000 (**0.008%**) | **$0.0071** |")
    A("| `>= rung − 2e-6` (the table's own tolerance) | **0 / 12,000** | **0 / 12,000** | **$0.00000** |")
    A("")
    A("**This is not a defect, and the direction matters.** Gold prices sit on a 3-decimal grid and the rungs "
      "are round dollar amounts, so a rung is hit *exactly* very often — and a strict float `>=` then "
      "**misses** those exact ties, because `(hi − F) * 10` evaluates to `2.999999999997`. The tolerant "
      "comparison is the one that matches exact decimal arithmetic and matches `common.simulate`'s inclusive "
      "`lo <= stop` / `hi >= tp`. Both barriers get the same tolerance and the project convention books the "
      "**stop** on a same-minute tie, so the net bias is neutral-to-pessimistic. It is worth recording "
      "because the obvious from-scratch audit of `walk()` will 'find' a few hundred mismatches that are not "
      "mismatches — **my own first sanity check did exactly that and reported FAIL.**\n")
    A("One more convention, also not a bug: `walk()` **allows the target on the fill bar** (offset 0), "
      "whereas `common.simulate` never books a TP on the fill bar (`common.py`, `tp_hit[0] = False`). One "
      "example basket above (`2025-05-22 13:48`, `hold_min = 0`) is such a case. It is optimistic relative to "
      "`simulate`, but it applies identically to the same-minutes coin flip each reading is compared "
      "against, so it cannot manufacture an edge — and it means these numbers reconcile with **BT20's** "
      "conventions, not BT17's.\n")

    # ---------------- verdict
    A("## 12. Verdict in plain language\n")
    A("**VWAP does not work on gold M1, and the most useful thing BT21-V found is about the data, not the "
      "setup.**\n")
    A(f"The setup answer is blunt. Six variants, three eras, both signs, {NSIG:,} measured chances: the best "
      f"reading in the file is **{top.acc:.2f} right in 100** when it needs **{top.xstar:.1f}** to break "
      f"even and needs to clear a placebo bar of **{bar[top.era]:.2f}** to be a finding at all. It clears "
      "neither. Day-anchored, London-anchored, NY-anchored, following, fading, ±1σ, ±2σ, 15 warm-up bars or "
      "120 — the same number comes back every time. Pulling back to a VWAP and closing back on the trend "
      "side tells you nothing about the next hour of gold that the minute itself does not already tell you.\n")
    A("The data answer is the one to keep. **The Dukascopy gold feed has no volume column — it has a column "
      "of ones, 5,526,570 of them, 2009 through 2024.** That is not a quality caveat to note and work "
      "around; it means every volume-weighted idea in this project is untestable on 12 of its 18 years, and "
      "any code that computes one there is silently computing the unweighted version. On the one feed where "
      "tick volume is real, weighting by it moves the VWAP line by 39 cents and the answer by a quarter of "
      "an accuracy point.\n")
    A("`research_notes/M1_entry_candidates.md` ranked VWAP fourth of five, said 'test last', and named the "
      "failure mode as *gold has no real volume, so VWAP is a guess, and a 24-hour market has no natural day "
      "start*. Both halves were right, and the first is worse than the note knew. Nothing here contradicts "
      "Zarattini & Aziz (SSRN 4631351) or Barbon/Zarattini/Aziz — those are **US-equity, bull-market, "
      "real-volume** results. Gold M1 is not that instrument, and the quantity their method is built on does "
      "not exist in most of this data.\n")
    A("**What a trader should do with this: nothing.** There is no shape, no anchor, no sign and no hour "
      "where VWAP pays after $1.10 of commission. The one line worth carrying forward is the lookahead "
      "twin's: a VWAP-band reversion rule looks like a **62%-accurate** system the moment you fill it on the "
      "signal bar, and that is very likely what every published M1 VWAP-band backtest with a spectacular "
      "equity curve is actually measuring.\n")
    A("---\n")
    A("**Files.** `bt/bt21v_lib.py` (rules, DST anchors, VWAP/σ, walk build) · `bt/bt21v_run.py` (eras, "
      "money, checks) · `bt/bt21v_report.py` (this file) · `readings_*.csv` · `ladder_*.csv` (196 shapes) · "
      "`engine_*.csv` · `twin_*.csv` · `hours_*.csv` · `placebo_*.csv` · `volquality_*.csv` · "
      "`sanity_*.txt` · `trades_V1..V6_{duka,zero}.csv` · `parts/walk_*.npz`, `parts/sig_*.npz`. No file "
      "outside `bt/bt21v_*.py` and `results/BT21V/` was modified.\n")
    A("**Scope note.** The eras fixed in `bt/BT21_BRIEF.md` run to 2024-2026, which reaches past "
      "`AGENT_BRIEF.md`'s TEST lock of 2025-07. That is the BT21 brief's own instruction (as it was for BT18 "
      "and BT20), and nothing here was selected on that era: the six variants were fixed in advance, the "
      "warm-up constant was fixed in advance, and the one ladder scan is reported with its trial count "
      "instead of being used to pick anything.\n")

    txt = "\n".join(L)
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(f"wrote {os.path.join(OUT, 'report.md')}  ({len(txt):,} chars)")


if __name__ == "__main__":
    main()
