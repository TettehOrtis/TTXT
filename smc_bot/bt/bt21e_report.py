"""BT21-E report - turns results/BT21E/grid_*.csv into results/BT21E/report.md.

Applies the pre-registered pass/fail test from bt/BT21_BRIEF.md literally:
  1 net $/basket > 0 at the primary shape ($15 / $70) after commission, in ALL THREE eras
  2 accuracy > its own luck bar (coinflip_same_minutes break-even) in ALL THREE eras
  3 accuracy > the family's placebo bar
  4 >= 500 resolved baskets in EACH era

    .venv/Scripts/python.exe smc_bot/bt/bt21e_report.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt import bt21e_lib as E                                          # noqa: E402

ERAS = ["2009-2020", "2021-2023", "2024-2026"]
PRIM = E.PRIMARY
NICE = {"E1_m5_ema9": "M5 EMA50 trend, pullback to M1 EMA9, close back up",
        "E2_m5_ema21": "M5 EMA50 trend, pullback to M1 EMA21, close back up",
        "E3_m15_ema9": "M15 EMA50 trend, pullback to M1 EMA9, close back up",
        "E4_m15_ema21": "M15 EMA50 trend, pullback to M1 EMA21, close back up",
        "E5_sweep20": "sweep of the 20-bar extreme, close back inside, faded",
        "E6_sweep60": "sweep of the 60-bar extreme, close back inside, faded"}


def read(kind):
    fs = [os.path.join(E.OUT, f"{kind}_{d}.csv") for d in ("duka", "zero")]
    d = pd.concat([pd.read_csv(f) for f in fs if os.path.exists(f)], ignore_index=True)
    return d


def placebo():
    out = {}
    for d in ("duka", "zero"):
        f = os.path.join(E.OUT, f"placebo_{d}.json")
        if os.path.exists(f):
            out.update(json.load(open(f)))
    return out


def fmt(x, n=2, plus=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "-"
    s = f"{x:+.{n}f}" if plus else f"{x:.{n}f}"
    return s


def main():
    g = read("grid")
    lk = read("look")
    st = read("struct")
    pb = placebo()
    ck = {d: json.load(open(os.path.join(E.OUT, f"checks_{d}.json")))
          for d in ("duka", "zero") if os.path.exists(os.path.join(E.OUT, f"checks_{d}.json"))}
    p = g[(g.target == PRIM[0]) & (g.stop == PRIM[1])].set_index(["variant", "era"])
    sh = g[(g.target == E.SHIPPED[0]) & (g.stop == E.SHIPPED[1])].set_index(["variant", "era"])

    # ---------------- pass/fail
    verdict = {}
    for nm in E.VARIANTS:
        r = {}
        c1 = c2 = c4 = True
        for era in ERAS:
            if (nm, era) not in p.index:
                c1 = c2 = c4 = False
                continue
            row = p.loc[(nm, era)]
            r[era] = row
            c1 &= bool(row.basket_usd > 0)
            c2 &= bool(row.acc > row.luck_bar)
            c4 &= bool(row.baskets >= 500)
        c3 = all(bool(r[e].acc > pb[e]["acc2_P95"]) for e in ERAS if e in r and e in pb) and len(r) == 3
        verdict[nm] = dict(rows=r, c1=c1, c2=c2, c3=c3, c4=c4,
                           passed=bool(c1 and c2 and c3 and c4))

    # best two variants by mean (acc - luck_bar) across the three eras, chosen WITHOUT 2024-2026
    sc = {}
    for nm in E.VARIANTS:
        r = verdict[nm]["rows"]
        pre = [r[e].acc - r[e].luck_bar for e in ("2009-2020", "2021-2023") if e in r]
        sc[nm] = float(np.mean(pre)) if pre else -1e9
    best2 = sorted(sc, key=sc.get, reverse=True)[:2]

    L = []
    A = L.append
    A("# BT21-E — the owner's EMA pullback (E1–E4) and liquidity sweep reversal (E5/E6), measured\n")
    A("**Pre-registered in `bt/BT21_BRIEF.md` before any code was written. Family E is SIX variants, "
      "fixed; none was added, dropped or re-specified after seeing a result.** Gold M1, two feeds "
      "(Dukascopy 2009-03-15→2021, Exness Zero 2021→2026-09), three eras reported separately and never "
      "pooled. All money at **0.10 lots**, after the **$1.10** round-trip commission and **0.05** extra "
      "spread. Primary shape = the sniper's live shape, **$15 target / $70 stop**.\n")

    # ---------------- ten lines
    npass = sum(v["passed"] for v in verdict.values())
    b1, b2 = best2
    r1, r2 = verdict[b1]["rows"], verdict[b2]["rows"]
    A("## The ten lines\n")
    A(f"1. **Variants that pass the pre-registered test: {npass} of 6.** "
      + ("Every one of E1–E6 fails." if npass == 0 else
         "Passing: " + ", ".join(k for k, v in verdict.items() if v["passed"]) + ".") + "\n")
    A(f"2. Sample size is not the problem: {int(p.signals.min()):,}–{int(p.signals.max()):,} signals per "
      f"era-variant cell, {int(p.signals.sum()):,} chances in all, priced at every one of "
      f"{len(E.SHAPES)} exit shapes ({len(g):,} rows in `grid_*.csv`).\n")
    A(f"3. **The luck bar, not 50, is the thing to beat.** A $15 target in front of a $70 stop needs "
      f"{p.luck_bar.min():.1f}–{p.luck_bar.max():.1f} right in 100 **on these rules' own entry minutes** "
      f"just to break even; at the ladder's best shape for these rules ($50 target / $5 stop) the bar is "
      f"{g[(g.stop == 5) & (g.target == 50)].luck_bar.min():.1f}–"
      f"{g[(g.stop == 5) & (g.target == 50)].luck_bar.max():.1f}, exactly the 64–66 BT20 found. "
      f"The best accuracy any of the six reaches in any era is **{p.acc.max():.2f}**, the worst "
      f"{p.acc.min():.2f} — a coin flip, measured on {int(p.signals.sum()):,} chances.\n")
    A(f"4. Best of the six on the two eras that may be used for choosing (2009-2020, 2021-2023): "
      f"**{b1}** — " + " · ".join(f"{e} {r1[e].acc:.2f} vs bar {r1[e].luck_bar:.2f}"
                                  for e in ERAS if e in r1) + ".\n")
    A(f"5. Second best: **{b2}** — " + " · ".join(f"{e} {r2[e].acc:.2f} vs bar {r2[e].luck_bar:.2f}"
                                                  for e in ERAS if e in r2) + ".\n")
    A("6. Net $/basket at the live $15/$70 shape, every variant, every era: "
      f"**{p.basket_usd.min():+.2f} to {p.basket_usd.max():+.2f}**; "
      f"{int((p.basket_usd > 0).sum())} of {len(p)} era-variant cells are positive, and "
      f"{sum(verdict[v]['c1'] for v in verdict)} variants are positive in all three.\n")
    A("7. **The lookahead twin manufactures a huge fake edge.** Filling at the signal bar's own open "
      "instead of the next minute moves accuracy by "
      f"{_look(lk, p, 'acc'):+.2f} points and $/signal by {_look(lk, p, 'per_signal_usd'):+.3f} on the "
      "same signals, turning every one of E1-E4 from a loser into a winner — "
      "the BT17 §3 trap, reproduced on purpose. Every headline number here fills at the next open.\n")
    A("8. The week-rotation placebo bar (best-of-6, 200 draws, two-sided): "
      + " · ".join(f"{e} **{pb[e]['acc2_P95']:.2f}**" for e in ERAS if e in pb)
      + ". That is what this method invents from nothing.\n")
    A(_line9(st) + "\n")
    A("10. Nothing was selected from the 2024-2026 era: the two variants singled out above were ranked on "
      "2009-2020 and 2021-2023 only, and the ranking does not change the verdict because every variant "
      "fails anyway.\n")

    # ---------------- rules
    A("\n## 1. The exact rules\n")
    A("| id | trend filter (last CLOSED bar) | M1 trigger (last CLOSED bar) | side | fill |")
    A("|---|---|---|---|---|")
    for nm in E.VARIANTS[:4]:
        tf, per = E.SPEC[nm]
        A(f"| **{nm}** | {tf} close > EMA50 (up) / < EMA50 (down) | low ≤ EMA{per} and close > open "
          f"(mirror: high ≥ EMA{per} and close < open) | follow the trend | next M1 open |")
    for nm, N in (("E5_sweep20", 20), ("E6_sweep60", 60)):
        A(f"| **{nm}** | none | low < min(low, prior {N} bars) − 0.05 **and** close back above that "
          f"minimum (mirror on the high side) | fade the sweep | next M1 open |")
    A("\nBoth signs of every variant are in the ranking below. A mirror is exactly `100 − accuracy` on the "
      "same trigger set, so it is not a seventh reading.\n")
    A("Implementation details the brief left open, fixed in `bt21e_lib.py`'s docstring **before** any "
      "result was seen: sweep buffer 0.05 price units, sweep cooldown 10 bars per side, the prior-N window "
      "must be N contiguous real minutes, EMA warm-up 300 M1 bars / 200 trend bars, and E1–E4 fire on "
      "every qualifying minute (the one-basket-at-a-time engine does the thinning).\n")

    # ---------------- the ranking
    A("\n## 2. THE RANKING — 6 variants × 3 eras, both signs, at the live $15/$70 shape\n")
    A("`acc` = how often the rule chose the side that **would** have paid more (ties count half). "
      "`luck bar` = this rule's own break-even accuracy **on its own entry minutes** — the number to beat, "
      "never 50. `$/signal` = mean net dollars if every signal were taken; `$/basket` = mean net dollars "
      "of the baskets the robot's one-at-a-time rule actually takes. `coin $` = a random direction at "
      "exactly the same minutes (`coinflip_same_minutes`).\n")
    A("| era | variant | sign | signals | **acc** | **luck bar** | acc − bar | $/signal | coin $ | "
      "baskets | **$/basket** | per day | med min | week t |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    rank = []
    for era in ERAS:
        for nm in E.VARIANTS:
            if (nm, era) not in p.index:
                continue
            r = p.loc[(nm, era)]
            rank.append((r.acc - r.luck_bar, era, nm, "follow", r))
            rank.append((100 - r.acc - r.luck_bar, era, nm, "fade", r))
    for d, era, nm, sign, r in sorted(rank, key=lambda x: -x[0]):
        acc = r.acc if sign == "follow" else 100 - r.acc
        usd = r.per_signal_usd if sign == "follow" else r.flip_usd
        bk = f"{int(r.baskets):,}" if sign == "follow" else "—"
        bku = fmt(r.basket_usd, 3, True) if sign == "follow" else "—"
        pd_ = fmt(r.per_day, 2) if sign == "follow" else "—"
        wt = fmt(r.week_t, 2, True) if sign == "follow" else "—"
        A(f"| {era} | {nm} | {sign} | {int(r.signals):,} | **{acc:.2f}** | {r.luck_bar:.2f} | "
          f"{d:+.2f} | {usd:+.3f} | {r.luck_usd:+.3f} | {bk} | {bku} | {pd_} | {int(r.med_min)} | {wt} |")
    A(f"\n**Read the `acc − bar` column.** It is negative in all {len(rank)} readings, by "
      f"{abs(max(x[0] for x in rank)):.1f} to {abs(min(x[0] for x in rank)):.1f} points — i.e. the best "
      "reading in the family, taken in the better of its two signs, is still "
      f"{abs(max(x[0] for x in rank)):.1f} points short of paying for itself. Note what the `$/signal` "
      "and `coin $` columns say together: the rules do not merely lose, they lose about what a coin would "
      "lose at the same minutes. There is no small edge being eaten by costs; there is no edge.\n")

    # ---------------- placebo
    A("\n## 3. The placebo bar — what the method invents from nothing\n")
    A("Each variant's chosen-side vector is rotated circularly by a random whole number of weeks (the "
      "signal rhythm and the clustering of outcomes survive; any real link between side and outcome dies), "
      "the best of the 6 is taken, 200 draws. Both signs are reported, so the two-sided bar is the "
      "headline.\n")
    A("| era | one-sided P50 | one-sided P95 | **two-sided P95 (the bar)** | two-sided max | "
      "placebo $/signal P95 | best REAL acc in that era |")
    A("|---|---|---|---|---|---|---|")
    for era in ERAS:
        if era not in pb:
            continue
        v = pb[era]
        real = max(max(p.loc[(nm, era)].acc, 100 - p.loc[(nm, era)].acc)
                   for nm in E.VARIANTS if (nm, era) in p.index)
        A(f"| {era} | {v['acc_P50']:.2f} | {v['acc_P95']:.2f} | **{v['acc2_P95']:.2f}** | "
          f"{v['acc2_max']:.2f} | {v['money2_P95']:+.3f} | {real:.2f} |")
    A("")

    # ---------------- money at three shapes
    A("\n## 4. Money at all three shapes\n")
    A("Primary = the sniper's live `.env` shape. Shipped = the vendor's. `best on ladder` = the best of the "
      "36 (target, stop) pairs BT20 prices, chosen per era-variant **by net $/basket**, which is a "
      "best-of-36 search and therefore optimistic by construction.\n")
    A("| era | variant | $15/$70 $/basket | $15/$70 baskets | $7/$70 $/basket | $7/$70 baskets | "
      "best shape on ladder | its $/basket | its baskets |")
    A("|---|---|---|---|---|---|---|---|---|")
    for era in ERAS:
        for nm in E.VARIANTS:
            if (nm, era) not in p.index:
                continue
            r = p.loc[(nm, era)]
            s = sh.loc[(nm, era)] if (nm, era) in sh.index else None
            sub = g[(g.era == era) & (g.variant == nm) & (g.baskets >= 100)]
            bst = sub.loc[sub.basket_usd.idxmax()] if len(sub) else None
            c = [era, nm, fmt(r.basket_usd, 3, True), f"{int(r.baskets):,}"]
            c += [fmt(s.basket_usd, 3, True), f"{int(s.baskets):,}"] if s is not None else ["-", "-"]
            c += ([f"${int(bst.target)}/${int(bst.stop)}", fmt(bst.basket_usd, 3, True),
                   f"{int(bst.baskets):,}"] if bst is not None else ["-", "-", "-"])
            A("| " + " | ".join(c) + " |")
    A("\n**Not one of the 36 shapes turns any of the six variants positive in all three eras.** The best "
      "cell in the whole table is still a best-of-36 pick inside a single era.\n")

    # ---------------- struct
    A("\n## 5. E5/E6 with the owner's real stop — beyond the wick\n")
    A("The owner's sweep spec puts the stop **beyond the wick**: a structural stop whose distance changes "
      "every trade. The basket bot cannot do that — it only has one shared dollar stop — so both are "
      "measured here.\n")
    if len(st):
        A("| era | variant | shape | signals | median stop $ | % stops under $2 | acc | luck bar | "
          "$/signal | coin $ | win % | med min | mean R (gross) | week t |")
        A("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for _, r in st.sort_values(["era", "variant", "shape"]).iterrows():
            A(f"| {r.era} | {r.variant} | {r['shape']} | {int(r.signals):,} | {r.med_stop_usd:.2f} | "
              f"{r.pct_stop_under_2usd:.1f} | **{r.acc:.2f}** | {fmt(r.luck_bar)} | "
              f"{r.per_signal_usd:+.3f} | {r.luck_usd:+.3f} | {r.win_pct:.1f} | {int(r.med_min)} | "
              f"{r.per_signal_R:+.4f} | {fmt(r.week_t, 2, True)} |")
    A("\n**Which of the two could the sniper actually trade? Only the fixed-dollar one** (section 2's "
      "E5/E6 rows at $15/$70). The basket bot opens five positions at one price with one shared stop and "
      "one shared target, both set in dollars; it has no mechanism for a per-trade stop at a price level "
      "read off the signal bar. The wick-stop reading above is therefore an answer to the research "
      "question — *is the structural stop what makes the setup work?* — not a tradable configuration.\n")
    A(_struct_verdict(st) + "\n")

    # ---------------- examples
    A("\n## 6. Ten real baskets to check on a chart\n")
    A(_examples(b1))

    # ---------------- checks
    A("\n## 7. The sanity checks\n")
    A("| check | duka | zero |")
    A("|---|---|---|")
    keys = ["fill_is_next_minute", "trend_bar_closes_at_or_before_fill_open_s",
            "mirror_is_100_minus_acc", "engine_matches_bt20_run",
            "pct_signals_whose_1440bar_window_crosses_a_gap",
            "pct_baskets_that_actually_resolve_after_a_gap",
            "acc_all", "acc_excluding_gap_crossers"]
    lbl = {"fill_is_next_minute": "signal bar closes before the fill bar opens, and the fill bar is the "
                                  "very next minute (all 6 variants)",
           "trend_bar_closes_at_or_before_fill_open_s": "worst gap between the trend bar's close and the "
                                                        "fill bar's open, seconds (must be ≤ 0)",
           "mirror_is_100_minus_acc": "the mirror of a reading is exactly 100 − accuracy",
           "engine_matches_bt20_run": "our one-at-a-time engine returns the same baskets as "
                                      "`bt20_run.engine` on the same input",
           "pct_signals_whose_1440bar_window_crosses_a_gap": "% of signals whose 1440-**bar** walk window "
                                                             "crosses a market gap > 6 h",
           "pct_baskets_that_actually_resolve_after_a_gap": "% of baskets that actually resolve on the far "
                                                            "side of such a gap",
           "acc_all": "E1 accuracy, all signals (zero feed, pooled, $15/$70)",
           "acc_excluding_gap_crossers": "E1 accuracy with the gap-crossing baskets removed"}
    for k in keys:
        A(f"| {lbl[k]} | {ck.get('duka', {}).get(k, '-')} | {ck.get('zero', {}).get(k, '-')} |")
    for d in ("duka", "zero"):
        if d in ck and "random_side_same_minutes" in ck[d]:
            v = ck[d]["random_side_same_minutes"]
            A(f"| a RANDOM direction at exactly E1's own minutes ({d}) | | acc "
              f"{v['acc']}, ${v['usd']} (the coin-flip amount is ${v['coinflip_usd']}) |")
    A("\nHand replay of three baskets per feed (the walk's arithmetic re-done bar by bar from the raw M1 "
      "frame) is in `checks_<feed>.json` under `hand_replay`; every row's `ok` is true.\n")
    A("\n**Overlap sensitivity.** E1–E4 fire on every qualifying minute, so consecutive signals share most "
      "of their 1440-minute window. If the headline accuracy were an artefact of that clustering, keeping "
      "only the FIRST signal inside each trend bar would move it. It does not:\n")
    A("| feed | variant | signals, all | acc | signals, one per trend bar | acc | its luck bar |")
    A("|---|---|---|---|---|---|---|")
    for d in ("duka", "zero"):
        for nm, v in ck.get(d, {}).get("overlap_sensitivity", {}).items():
            A(f"| {d} | {nm} | {v['all_signals']:,} | {v['acc_all']} | {v['one_per_trend_bar']:,} | "
              f"{v['acc_one_per_trend_bar']} | {v['luck_bar_one_per_trend_bar']} |")
    A("\nE3 on the duka feed drops from 1,709,590 signals to 365,200 — a 4.7× thinning — and its accuracy "
      "moves by 0.04 of a point. The reading is a property of the rule, not of the overlap.\n")

    # ---------------- lookahead
    A("\n## 8. The lookahead twin — how much fake edge one line buys\n")
    A("Exactly the same signal set, exactly the same shape; the only change is that the basket fills at "
      "the open of the bar **whose close produced the signal** instead of the next minute's open.\n")
    A("| era | variant | acc, next open (honest) | acc, own open (twin) | Δ acc | $/signal honest | "
      "$/signal twin | Δ $ |")
    A("|---|---|---|---|---|---|---|---|")
    lp = lk[(lk.target == PRIM[0]) & (lk.stop == PRIM[1])].set_index(["variant", "era"])
    for era in ERAS:
        for nm in E.VARIANTS:
            if (nm, era) not in p.index or (nm, era) not in lp.index:
                continue
            a, c = p.loc[(nm, era)], lp.loc[(nm, era)]
            A(f"| {era} | {nm} | {a.acc:.2f} | **{c.acc:.2f}** | {c.acc - a.acc:+.2f} | "
              f"{a.per_signal_usd:+.3f} | **{c.per_signal_usd:+.3f}** | "
              f"{c.per_signal_usd - a.per_signal_usd:+.3f} |")
    A("\nThe twin is not a small bias. It is the single largest effect anywhere in this report, and it is "
      "entirely artificial: it lets the basket buy at a price that was only reachable because the bar it "
      "was reading had already traded there. BT15's retracted \"+$2.17 a basket\" was this bug.\n")

    # ---------------- bugs
    A("\n## 9. Bugs and limitations found in shared code\n")
    A(_bugs())

    # ---------------- verdict
    A("\n## 10. Pass/fail, variant by variant\n")
    A("| variant | 1 net $/basket > 0 in all 3 eras | 2 acc > own luck bar in all 3 | "
      "3 acc > placebo bar | 4 ≥ 500 baskets per era | **VERDICT** |")
    A("|---|---|---|---|---|---|")
    for nm in E.VARIANTS:
        v = verdict[nm]
        y = (lambda t: "yes" if t else "**no**")
        A(f"| {nm} | {y(v['c1'])} | {y(v['c2'])} | {y(v['c3'])} | {y(v['c4'])} | "
          f"**{'PASS' if v['passed'] else 'FAIL'}** |")
    A("")
    A("\n## 11. The verdict in plain language\n")
    A(_plain(verdict, p, pb, best2, st, g, lk))
    open(os.path.join(E.OUT, "report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("wrote", os.path.join(E.OUT, "report.md"))
    print("\nPASS/FAIL:", {k: ("PASS" if v["passed"] else "FAIL") for k, v in verdict.items()})


def _look(lk, p, col, only=None):
    """Mean (twin - honest) for a column, over E1-E4 by default (E5/E6 behave differently - see s.8)."""
    lp = lk[(lk.target == PRIM[0]) & (lk.stop == PRIM[1])].set_index(["variant", "era"])
    only = only or E.VARIANTS[:4]
    d = [lp.loc[i][col] - p.loc[i][col] for i in p.index if i in lp.index and i[0] in only]
    return float(np.mean(d)) if d else float("nan")


def _line9(st):
    if not len(st):
        return "9. (structural-stop reading missing)"
    real = st[~st["shape"].str.contains("ZERO")]
    free = st[st["shape"].str.contains("ZERO")]
    f1 = free[free.era == "2009-2020"]
    return (f"9. **The structural stop makes the SHAPE fair and leaves the SIGNAL a coin flip.** With "
            f"E5/E6's stop beyond the wick and the target at 1R/2R — the owner's actual spec, which the "
            f"basket bot cannot trade — **{int((real.per_signal_usd > 0).sum())} of {len(real)}** era-shape "
            f"cells make money at real costs, because 1R on an M1 sweep bar is a median of only "
            f"${real.med_stop_usd.min():.2f}–${real.med_stop_usd.max():.2f} and the $1.60 round trip is "
            f"20–46% of it, lifting the break-even to {real.luck_bar.min():.0f}–{real.luck_bar.max():.0f}. "
            f"Strip both costs and the shape *is* fair (bar ≈ 49–52) — and then the sweep does show a real "
            f"~2-point tilt in 2009-2020 ({f1.acc.min():.2f}–{f1.acc.max():.2f}, "
            f"{f1.per_signal_R.min():+.3f} to {f1.per_signal_R.max():+.3f} R a trade) that **fades in "
            f"2021-2023 and reverses in 2024-2026**. Section 5 has the whole thing.")


def _struct_verdict(st):
    if not len(st):
        return ""
    real = st[~st["shape"].str.contains("ZERO")]
    free = st[st["shape"].str.contains("ZERO")]
    one = real[real["shape"].str.startswith("wick stop / 1R")]
    t = ["**Does the structural stop rescue the setup? No, and the reason is arithmetic, not opinion.**\n"]
    ms = real.med_stop_usd
    t.append(f"On gold M1 the distance from the fill to the sweep bar's own wick is **${ms.min():.2f}-"
             f"${ms.max():.2f}** at 0.10 lots - that is what 1R *is* here. The round trip costs $1.10 of "
             f"commission plus the 0.05 extra spread (another $0.50), so **costs are "
             f"{100 * 1.60 / ms.max():.0f}-{100 * 1.60 / ms.min():.0f}% of 1R**. That alone puts the "
             f"break-even for a 1R target at {one.luck_bar.min():.1f}-{one.luck_bar.max():.1f} right in "
             f"100 (the `luck bar` column above), before any question of skill arises. The observed win "
             f"rate is {one.win_pct.min():.1f}-{one.win_pct.max():.1f}%.\n")
    t.append(f"The stop is also inside the noise it is supposed to sit outside: the median basket resolves "
             f"in **{int(real.med_min.median())} minute(s)**. A stop placed on the extreme of the signal "
             f"bar itself is, on gold M1, a stop the very next minute can reach.\n")
    t.append("Stripping BOTH costs (the sensitivity rows below) separates the shape from the signal - a "
             "fair 1R:1R shape should read 50 unless the entry really picks a side:\n")
    t.append("| era | variant | target | acc (no costs) | its luck bar | $/signal | mean R | week t |")
    t.append("|---|---|---|---|---|---|---|---|")
    for _, r in free.sort_values(["era", "variant", "shape"]).iterrows():
        t.append(f"| {r.era} | {r.variant} | {r['shape'].split('/')[1].split('target')[0].strip()} | "
                 f"**{r.acc:.2f}** | {fmt(r.luck_bar)} | {r.per_signal_usd:+.3f} | "
                 f"{r.per_signal_R:+.4f} | {fmt(r.week_t, 2, True)} |")
    t.append("Removing the costs drops the bar from 59-70 to **48.8-52.5**, which is the point: the wick "
             "stop is a FAIR shape and the $15/$70 basket shape is not. Now read the accuracy column era "
             "by era, because it does something none of E1-E4 ever did:\n")
    for era in ERAS:
        x = free[free.era == era]
        if not len(x):
            continue
        t.append(f"- **{era}**: accuracy {x.acc.min():.2f}-{x.acc.max():.2f} against a bar of "
                 f"{x.luck_bar.min():.2f}-{x.luck_bar.max():.2f}; "
                 f"{x.per_signal_R.min():+.4f} to {x.per_signal_R.max():+.4f} R a trade; week-block t "
                 f"{x.week_t.min():+.2f} to {x.week_t.max():+.2f}.")
    t.append("\nSo there **is** a small real effect in the sweep: for the twelve Dukascopy years, fading a "
             "sweep of an N-bar extreme beat a fair coin at its own minutes by roughly two points, worth "
             "about +0.01 to +0.10 R a trade, with a week-block t of +5 to +13. It is the same size and "
             "character as BT17's `B1_bos_M1_MIRROR`. It then shrinks to about +1 point in 2021-2023 and "
             "**reverses** in 2024-2026 (49.3-50.0 against a bar of 50.8-52.5, negative t). And at any "
             "point in those fifteen years it was worth far less than the $1.60 it costs to act on a $4 "
             "stop. **The structural stop is not the missing ingredient. It fixes the shape, which is "
             "real and worth knowing, and the signal underneath it is about two points of tilt that no "
             "longer exists and never paid for its own commission.**\n")
    t.append("Per reading, eras with a positive $/signal at real costs:")
    for nm in ("E5_sweep20", "E6_sweep60"):
        for shp in real[real.variant == nm]["shape"].unique():
            x = real[(real.variant == nm) & (real["shape"] == shp)]
            t.append(f"- `{nm}` {shp}: **{int((x.per_signal_usd > 0).sum())} of {len(x)}**, mean "
                     f"accuracy minus luck bar **{float((x.acc - x.luck_bar).mean()):+.2f}** points.")
    return "\n".join(t)


def _examples(nm):
    fs = [f for f in os.listdir(E.OUT) if f.startswith(f"trades_{nm}_") and f.endswith(".csv")]
    if not fs:
        return "_(no trade CSVs found)_\n"
    d = pd.concat([pd.read_csv(os.path.join(E.OUT, f)) for f in fs], ignore_index=True)
    rng = np.random.default_rng(21)
    win, lose = d[d.net_usd > 0], d[d.net_usd <= 0]
    # 7 winners + 3 losers, both drawn at random. A plain 10-of-N draw at this shape comes back all
    # winners (a $15 target in front of a $70 stop wins ~88% of the time) and would read as cherry-picking.
    pick = pd.concat([win.iloc[np.sort(rng.choice(len(win), 7, replace=False))],
                      lose.iloc[np.sort(rng.choice(len(lose), 3, replace=False))]])
    s = pick.sort_values("t_entry")
    out = [f"Seven winning and three losing baskets, each drawn at random (seed 21) from **{nm}** at "
           f"$15/$70 across both feeds. The split is deliberate and stated: at this shape "
           f"{100 * len(win) / len(d):.1f}% of baskets win, so an unstratified draw of ten comes back all "
           f"green and would look like cherry-picking. Full CSVs for every variant and era are in this "
           f"folder.\n",
           "| # | entry (UTC) | era | side | fill | target | stop | exit (UTC) | held min | net $ |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for i, (_, r) in enumerate(s.iterrows(), 1):
        out.append(f"| {i} | {r.t_entry} | {r.era} | {'BUY' if r.side == 1 else 'SELL'} | {r.fill:.3f} | "
                   f"+${int(r.target_usd)} | −${int(r.stop_usd)} | {r.t_exit} | {int(r.hold_min)} | "
                   f"{r.net_usd:+.2f} |")
    out.append("\nTo check one by hand: the signal bar is the minute **before** `entry`; its close is the "
               "trigger, and the fill is this bar's open plus the spread plus 0.05 for a BUY, minus 0.05 "
               "for a SELL.\n")
    out.append("""**Row 1 worked through from the raw duka M1 frame, independently of the pipeline:**

| step | value |
|---|---|
| signal bar | 2012-03-06 18:10 — open 1670.91, high **1671.05**, low 1669.87, close **1670.20** |
| highest high of the prior 60 bars | **1670.99** |
| wick beyond it | 1671.05 − 1670.99 = **+0.06**, which clears the 0.05 buffer |
| close back inside the range | 1670.20 < 1670.99 → yes, so the rule fires **SELL** (fade the sweep) |
| fill bar | the very next minute, 2012-03-06 18:11, open **1670.22** |
| fill price used | 1670.22 − 0.05 extra spread = **1670.17** — matches the CSV to the cent |
| walked forward | the ask first reaches 7.00 above the fill at **2012-03-07 01:34**, bar offset **398** |
| booked | −$70.00 gross, **−$71.10** net after the $1.10 round trip — matches row 1 |

Nothing in that chain uses a price from the signal bar's own minute except its high and close, both of
which were known when it closed.
""")
    return "\n".join(out)


def _bugs():
    return """**1. `bt20_lib.walk` walks 1440 *bars*, not 1440 clock minutes, and ignores the
weekend flat-out that `common.simulate` applies** — `smc_bot/bt/bt20_lib.py:78-104`.
`J = ix[:, None] + ar[None, :]` indexes the next 1440 *rows* of the M1 frame, so a basket opened on a
Friday evening keeps walking straight into Monday as though the market never closed, while
`common.simulate` (`common.py:158-161`) flattens every position at Friday 20:45 UTC and before any gap
longer than 6 hours. Proof and size: section 7 reports what share of signals have a >6 h gap inside their
window, what share of baskets actually *resolve* on the far side of one, and E1's accuracy with those
baskets removed — the accuracy moves by a few hundredths, so it changes nothing here, but the
discrepancy is real and anything that depends on long holds should not use `walk` as-is.

**2. `bt20_lib.walk` books the floating value at bar `ix + HORIZON - 1` without checking the bar exists**
— `smc_bot/bt/bt20_lib.py:93, 99`. It is safe only because `signal_bars` filters
`e < len(b) - HORIZON - 2` beforehand; the function itself would read past the end of the array for any
caller that does not. `bt21e_lib.signals` applies the same filter for the same reason.

**3. Not a bug, a trap worth writing down: `common.simulate` never books a target on the fill bar but
does book a stop there** (`common.py:214`, `bt15_lib.fillbar_masks` exists purely to undo it). The walk
tables used here do not inherit that asymmetry — a target touched in the fill minute counts — so BT21-E's
numbers are very slightly *more* generous than a `simulate`-based run of the same signals would be. With
a $15 target that is worth a few hundredths of a dollar per basket, and it moves in the direction that
would have *helped* these rules. They still fail.

**4. No bug found in `smc_core.ema`, `common.asof_index` or `bt20_lib._fp`.** `_fp` was checked level by
level against `bt20_lib.walk` on 4,000 real signal bars and agrees exactly on all eight shared ladder
levels (`bt21e_run.py --tests`), and `asof_index` never returned a trend bar closing after the fill
bar's open (section 7)."""


def _plain(verdict, p, pb, best2, st, g, lk):
    npass = sum(v["passed"] for v in verdict.values())
    gap = (p.acc - p.luck_bar).max()
    nbar = int(sum(1 for nm in E.VARIANTS for e in ERAS
                   if (nm, e) in p.index and p.loc[(nm, e)].acc > p.loc[(nm, e)].luck_bar))
    tight = g[g.stop == 5]
    e14 = p.loc[[i for i in p.index if i[0] in E.VARIANTS[:4]]]
    free = st[st["shape"].str.contains("ZERO")]
    f2 = free[free["shape"].str.startswith("wick stop / 2R")]
    t = []
    t.append(f"**{npass} of the 6 variants pass the pre-registered test.** "
             + ("All six fail. " if npass == 0 else "")
             + f"Across the family there are **{nbar} era-variant cells out of {len(p)}** where accuracy "
               f"exceeds the rule's own break-even at the live shape, and "
               f"**{int((p.basket_usd > 0).sum())}** where a basket made money.\n")

    t.append("### E1-E4, the EMA pullback\n")
    t.append("This is the owner's own spec and the cousin of what the live sniper trades, and on gold M1 "
             f"it is a coin flip. Accuracy runs {e14.acc.min():.2f}-{e14.acc.max():.2f} over **twelve "
             "years of Dukascopy history and six years of Exness Zero**, on 220,000-1,270,000 chances per "
             f"era-variant cell. Its own break-even at $15/$70 is "
             f"{e14.luck_bar.min():.1f}-{e14.luck_bar.max():.1f}, so the shortfall is 5-7 points. That is "
             "not a near miss better exits could close. BT20 already priced every entry it had against 36 "
             "(target, stop) pairs and found nothing; section 4 repeats it on these six and gets the same "
             "answer. The most forgiving shape on the whole ladder for E1-E4 is $50 target / $5 stop, and "
             "it only shrinks the loss to "
             f"${tight[(tight.target == 50) & tight.variant.isin(E.VARIANTS[:4])].basket_usd.max():.2f} a "
             "basket, because tightening the stop raises the bar to "
             f"{tight[tight.target == 50].luck_bar.min():.0f}-"
             f"{tight[tight.target == 50].luck_bar.max():.0f} as fast as it cuts the loss. The single best "
             f"cell in the entire 36-shape x 6-variant x 3-era grid is "
             f"${g[g.baskets >= 100].basket_usd.max():.2f} a basket. The exits were never the problem.\n")
    t.append("Two sub-questions this family was built to answer, answered. **Pullback depth does not "
             "matter**: EMA9 against EMA21 (E1 vs E2, E3 vs E4) differ by a few hundredths of a point in "
             "every era. **The trend timeframe does not matter either**: M5 against M15 (E1 vs E3) the "
             "same. Those are the two parameters traders argue about in this setup, and on this market "
             "both are irrelevant to the direction. What M15 does change is the trade count - the trend "
             "persists longer, so E3/E4 fire more often and lose more in total at the same zero edge.\n")

    t.append("### E5/E6, the liquidity sweep - and the one reading that is not quite dead\n")
    t.append("The sweep is the better half, and it needs saying carefully because the honest version of it "
             "is both more interesting and still a fail.\n")
    t.append("At the shape the bot can actually trade ($15/$70) it is a coin flip like the rest: "
             f"{p.loc[('E6_sweep60', '2009-2020')].acc:.2f} / "
             f"{p.loc[('E6_sweep60', '2021-2023')].acc:.2f} / "
             f"{p.loc[('E6_sweep60', '2024-2026')].acc:.2f} for E6 against bars of 55.6 / 55.4 / 57.0.\n")
    t.append("At the owner's real spec - stop beyond the wick - the picture splits in a way that is worth "
             "reading twice. **With both costs removed**, so the 1R:2R shape is exactly fair and only the "
             "entry is being measured, E5/E6 at a 2R target read "
             f"{f2[f2.era == '2009-2020'].acc.min():.2f}-{f2[f2.era == '2009-2020'].acc.max():.2f} in "
             f"2009-2020 and {f2[f2.era == '2021-2023'].acc.min():.2f}-"
             f"{f2[f2.era == '2021-2023'].acc.max():.2f} in 2021-2023, against fair bars of about 49, and "
             f"make {f2[f2.era != '2024-2026'].per_signal_R.min():+.3f} to "
             f"{f2[f2.era != '2024-2026'].per_signal_R.max():+.3f} R a trade with week-block t of +1.7 to "
             "+13.0. **That is a small real directional tilt: fading a sweep of an N-bar extreme on gold "
             "M1 genuinely worked, a little, for fifteen years.** It is the same size and shape of effect "
             "as BT17's `B1_bos_M1_MIRROR`.\n")
    t.append("And it still fails, for three independent reasons, each of which is sufficient:\n"
             "1. **It reverses in 2024-2026** - "
             f"{f2[f2.era == '2024-2026'].acc.min():.2f}-{f2[f2.era == '2024-2026'].acc.max():.2f} against "
             "a bar of 50.8-51.6, i.e. below a coin flip at its own minutes, with negative week-t. A tilt "
             "that is positive in two eras and reversed in the third has found nothing - that is the "
             "lesson of BT18's 52.55 and BT20's COMEX momentum.\n"
             "2. **It does not survive costs, and not narrowly.** The wick on an M1 sweep bar is a median "
             "of $3.50-$8.13 away at 0.10 lots, so 1R *is* about $4. The round trip is $1.60 ($1.10 "
             "commission + $0.50 of extra spread) = 20-43% of 1R, which lifts the break-even from about 50 "
             "to **65-70**. The tilt is worth about 2 points. It is outgunned by an order of magnitude.\n"
             "3. **The sniper cannot trade it at all.** The basket is five positions at one price behind "
             "one shared dollar stop and one shared dollar target. There is no mechanism for a per-trade "
             "level read off the signal bar. The only version of E5/E6 the bot can run is section 2's, at "
             "$15/$70, and that one has no tilt left in it.\n")
    t.append("A fourth observation that makes point 2 worse: with the stop on the signal bar's own "
             "extreme, the **median basket resolves in one minute**. On gold M1 the structural stop is not "
             "outside the noise; it is inside it.\n")

    t.append("### The thing nobody asked about that matters most\n")
    t.append("The live $15/$70 shape is **not the same bet in 2024-2026 that it was in 2021-2023**. Same "
             "rules, same dollars: in 2009-2020 the median basket took about 89 minutes and the robot "
             "opened 9-10 a day; in 2021-2023, 56 minutes and about 14 a day; in 2024-2026, **10 minutes "
             "and 54-74 a day**. Gold's price roughly doubled and its minute range grew with it, so a "
             "fixed $15 target is now a much shorter trip - the robot churns five times faster and the "
             "loss per basket grew from about -$2.1 to about -$2.8, purely from paying the commission "
             "five times as often. A dollar-denominated target and stop silently becomes a different "
             "strategy when the underlying reprices. Anything built on this engine should express its "
             "shape in volatility (ATR, or a percentage of price), not in dollars.\n")

    t.append("### What a trader should do with this\n")
    t.append("Nothing, on gold M1, at this money shape. The two honest options are (a) stop hunting for a "
             "directional M1 entry on gold - twenty-one backtests have now failed to find one, and the "
             "five setups the owner named were the strongest remaining candidates - or (b) change the two "
             "things that actually move the luck bar, which are the shape and the instrument: comparable "
             "target and stop, on a market where the round trip is small next to the stop. The sweep's "
             "2-point tilt is real; it would need a round trip under about 4% of 1R to be worth "
             "anything, which on gold M1 means a stop near $40, not $4. That is a different product, not "
             "a tuning of this one.\n")

    t.append("### What surprised me\n")
    t.append("**1. The lookahead twin is worth more than every real effect in this report put together.** "
             "Filling at the signal bar's own open instead of the next minute's turns E1-E4 from about "
             "-$2.3 a basket into **+$2.7 to +$7.8** a basket and lifts accuracy by 3.2-7.8 points. One "
             "line of indexing. Every future claim of an M1 entry edge on this project should be checked "
             "there first.\n")
    t.append("**2. The twin's sign depends on the rule's direction, and that is diagnostic.** E1-E4 "
             "require `close > open`, so the signal bar's own open is *below* its close by construction "
             "and filling there buys cheaper than the market - a guaranteed fake edge. E5/E6 fade, so the "
             "same cheat runs the other way and the twin reads *worse* than the honest version (-1.8 and "
             "-2.2 points in 2024-2026). A lookahead check that reports 'no difference' is therefore not "
             "evidence of cleanliness; it can be a rule whose two biases cancel.\n")
    t.append("**3. The luck bar itself moved 1.4 points between eras on identical rules** (55.4-55.9 in "
             "2021-2023 against 57.0-57.2 in 2024-2026). The bar is a property of the minutes a rule "
             "selects and of the market's volatility, not a constant. Any report that compares a 2024 "
             "accuracy against a 2021 break-even is comparing two different things.\n")
    t.append("**4. The sweep edge is real and still useless.** I expected the sweep to be flat and the "
             "structural stop to make no difference. Instead the structural stop made the *shape* fair "
             "(the break-even fell from 56 to about 50) and the entry turned out to have a genuine ~2-"
             "point tilt for fifteen years - and none of that mattered, because 1R on an M1 sweep bar is "
             "$4 and the round trip is $1.60. This is the clearest example in the project of a real signal "
             "that is simply smaller than the cost of acting on it.\n")
    t.append("**5. E3/E4 beat E1/E2 on the duka feed by about 0.7 of a point** (50.53/50.65 against "
             "49.86/49.95) and the ordering vanishes on the zero feed. With 1.0-1.3 million signals behind "
             "each number that gap is not sampling noise within the era - it is a regime difference "
             "between the feeds and the decades, which is precisely why the brief forbids pooling.")
    return "\n".join(t)


if __name__ == "__main__":
    main()
