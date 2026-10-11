"""BT16 report builder. Merges the two feeds (duka = HIST 2009-2020, zero = DEV 2021-23 + VAL 2024-mid25),
ranks every pre-registered reading by directional accuracy and writes results/BT16/report.md plus the CSVs.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt16_report
"""
import os
import numpy as np
import pandas as pd

from bt import bt16_lib as B

PER = ["HIST", "DEV", "VAL"]
SRC = {"HIST": "duka", "DEV": "zero", "VAL": "zero"}
P = B.PARTS
OUT = B.OUT


def part(n):
    return pd.read_csv(os.path.join(P, n))


def pool(rows):
    """Pool accuracy by decisive-chance count and money by chance count. Nothing else is legitimate."""
    rows = [r for r in rows if r is not None and r["n"] > 0]
    if not rows:
        return dict(n=0, n_dec=0, acc=np.nan, money=np.nan)
    nd = sum(r["n_dec"] for r in rows)
    n = sum(r["n"] for r in rows)
    acc = sum(r["acc"] * r["n_dec"] for r in rows if r["n_dec"] > 0) / nd if nd else np.nan
    money = sum(r["money"] * r["n"] for r in rows) / n
    return dict(n=n, n_dec=nd, acc=acc, money=money)


def merged(kind):
    """kind: 'read' (the 26 grid readings) or 'news' (the 18 news readings + pooled aggregates)."""
    d = {f: part(f"{kind}_{f}.csv") for f in ("duka", "zero")}
    out = []
    names = list(dict.fromkeys(list(d["duka"].reading) + list(d["zero"].reading)))
    for nm in names:
        rec = dict(reading=nm)
        fam = d["duka"][d["duka"].reading == nm]
        rec["family"] = (fam.family.iloc[0] if len(fam) else
                         d["zero"][d["zero"].reading == nm].family.iloc[0])
        per_rows = {}
        for p in PER:
            g = d[SRC[p]]
            r = g[(g.reading == nm) & (g.scope == p)]
            per_rows[p] = None if len(r) == 0 or r.n.iloc[0] == 0 else r.iloc[0].to_dict()
            rec[f"acc_{p}"] = None if per_rows[p] is None else round(per_rows[p]["acc"], 2)
            rec[f"money_{p}"] = None if per_rows[p] is None else round(per_rows[p]["money"], 3)
            rec[f"n_{p}"] = 0 if per_rows[p] is None else int(per_rows[p]["n"])
        pl = pool(list(per_rows.values()))
        rec.update(n=pl["n"], n_dec=pl["n_dec"], acc=round(pl["acc"], 2), money=round(pl["money"], 3))
        a = [rec[f"acc_{p}"] for p in PER if rec[f"acc_{p}"] is not None]
        rec["acc_min_period"] = round(min(a), 2) if a else None
        rec["acc_spread_pts"] = round(max(a) - min(a), 2) if a else None
        rec["all_three_above_50"] = bool(a and min(a) > 50.0)
        rec["all_three_above_529"] = bool(a and min(a) > 52.9)
        out.append(rec)
    return pd.DataFrame(out).sort_values("acc", ascending=False).reset_index(drop=True)


def hours():
    h = pd.concat([part("hours_duka.csv"), part("hours_zero.csv")], ignore_index=True)
    h = h[h.n_dec > 0]
    g = h.groupby(["reading", "hour"]).apply(
        lambda x: pd.Series(dict(n=x.n.sum(), n_dec=x.n_dec.sum(),
                                 acc=(x.acc * x.n_dec).sum() / x.n_dec.sum(),
                                 money=(x.money * x.n).sum() / x.n.sum(),
                                 worst_period_acc=x.acc.min(), best_period_acc=x.acc.max())),
        include_groups=False).reset_index()
    return h, g


def money_ladder(ebest, eworst, days):
    rows = []
    for X in [50, 51, 52, 52.9, 53, 54, 55, 56, 58, 60]:
        per_basket = (X / 100) * ebest + (1 - X / 100) * eworst
        rows.append(dict(accuracy_out_of_100=X, per_basket_usd_010=round(per_basket, 2),
                         per_day_usd_010=round(per_basket * days, 2),
                         per_day_usd_001=round(per_basket * days / 10, 2),
                         per_month_usd_001=round(per_basket * days / 10 * 21, 2)))
    return pd.DataFrame(rows)


def md_table(df, cols=None, names=None):
    d = df[cols] if cols else df
    head = names if names else list(d.columns)
    s = "| " + " | ".join(str(c) for c in head) + " |\n|" + "---|" * len(head) + "\n"
    for _, r in d.iterrows():
        s += "| " + " | ".join("" if pd.isna(v) else str(v) for v in r.values) + " |\n"
    return s


def main():
    os.makedirs(OUT, exist_ok=True)
    bl = pd.concat([part("baseline_duka.csv"), part("baseline_zero.csv")], ignore_index=True)
    eb = float((bl.E_best * bl.n).sum() / bl.n.sum())
    ew = float((bl.E_worst * bl.n).sum() / bl.n.sum())
    ec = float((bl.E_coin * bl.n).sum() / bl.n.sum())
    xs = 100 * (-ew) / (eb - ew)

    grid = merged("read")
    news = merged("news")
    news_v = news[news.family == "news"].copy()
    news_p = news[news.family == "news_pooled"].copy()
    ref = grid[grid.family == "ref"].copy()
    grid_v = grid[grid.family != "ref"].copy()

    allv = pd.concat([grid_v, news_v], ignore_index=True).sort_values("acc", ascending=False)
    allv.to_csv(os.path.join(OUT, "ranked_all_readings.csv"), index=False)
    grid_v.to_csv(os.path.join(OUT, "ranked_grid_readings.csv"), index=False)
    news.to_csv(os.path.join(OUT, "ranked_news_readings.csv"), index=False)

    corr = pd.concat([part("corr_duka.csv"), part("corr_zero.csv")], ignore_index=True)
    corr.to_csv(os.path.join(OUT, "gold_vs_dollar_correlation.csv"), index=False)
    nv = pd.concat([part("newsvol_duka.csv"), part("newsvol_zero.csv")], ignore_index=True)
    nv.to_csv(os.path.join(OUT, "news_volatility.csv"), index=False)
    pb = {f: part(f"placebo_{f}.csv") for f in ("duka", "zero")}
    hraw, hpool = hours()
    hpool.round(3).to_csv(os.path.join(OUT, "by_hour_of_day.csv"), index=False)
    hraw.to_csv(os.path.join(OUT, "by_hour_of_day_x_period.csv"), index=False)
    days = pd.concat([part("days_duka.csv"), part("days_zero.csv")], ignore_index=True)
    days.to_csv(os.path.join(OUT, "day_by_day.csv"), index=False)

    # per year
    yr = []
    for f in ("duka", "zero"):
        d = part(f"read_{f}.csv")
        d = d[d.scope.str.startswith("Y")]
        yr.append(d)
    yr = pd.concat(yr, ignore_index=True)
    yr["year"] = yr.scope.str[1:].astype(int)
    yr.to_csv(os.path.join(OUT, "by_year.csv"), index=False)

    bpd = float(days[days.reading == "COINFLIP_50"].baskets_per_day.mean())
    lad = money_ladder(eb, ew, bpd)
    lad.to_csv(os.path.join(OUT, "money_ladder.csv"), index=False)

    # sessions
    ses = []
    for f in ("duka", "zero"):
        d = part(f"read_{f}.csv")
        ses.append(d[d.scope.isin([s for s, _, _ in B.SESSIONS])])
    ses = pd.concat(ses, ignore_index=True)
    ses.to_csv(os.path.join(OUT, "by_session.csv"), index=False)

    # ---------------------------------------------------------------- the report
    L = []
    w = L.append
    g5 = grid_v.head(8)
    nv5 = news_v.head(5)
    best = allv.iloc[0]
    cons = allv[allv.all_three_above_529 & (allv.n > 2000)]
    pb_grid = {f: np.percentile(pb[f].best_acc_grid, 95) for f in pb}
    pb_all = {f: np.percentile(pb[f].best_acc_all, 95) for f in pb}
    pbm_grid = {f: np.percentile(pb[f].best_money_grid, 95) for f in pb}

    w("# BT16 — the entry. Three ideas, measured. **Every reading ranked.**\n")
    w("Pre-registered in `bt/BT16_BRIEF.md` **before any code was written**: 44 readings, counts fixed.\n")
    w("**Every money figure in this report is at 0.10 lots** (0.02 x 5 positions). "
      "At the 0.01 lots a $350 account should use, divide every dollar by 10. "
      "The settings are the user's: **profit cap $15, loss cap $20**, his broker's real costs "
      "(**no commission**, his measured **0.072** spread). Earlier BT15 tables were mostly at $7/$70 or "
      "$200/$150 — do not compare a number here with one from there without checking the settings line.\n")
    w("TEST (2025-07-01 onward) was never read. The latest minute any BT16 simulation touched is "
      "**2025-06-26 23:30** (the last entry chance) — stated because the brief required it.\n")

    w("\n## Summary in ten lines\n")
    w(f"1. The shape is confirmed: at $15/$20 with his costs the system breaks even at "
      f"**{xs:.1f} right calls in 100**. A coin flip loses **${abs(ec):.2f} a basket**.\n")
    w(f"2. **Nothing found.** The best of the 44 readings, over all 16 years pooled, picks the right way "
      f"**{best.acc:.1f} times in 100** ({best.reading}) — and it does not hold up period to period.\n")
    n529 = int(allv.all_three_above_529.sum())
    w(f"3. Readings above 52.9 in all three date periods separately: **{n529}** out of 44 — and all {n529} are "
      f"news cells with about 60 chances per period, where an accuracy wanders 6-7 points on its own. "
      "A post-hoc control (section 4b) shows those same rules are a **49** on 190,000 chances, so they are "
      f"non-findings. Readings above 52.9 in all three periods with a usable sample: **{len(cons)}**.\n")
    w(f"4. The best large-sample reading is **{g5.iloc[0].reading} at {g5.iloc[0].acc:.2f}** out of 100 "
      f"({int(g5.iloc[0].n):,} baskets) — just *below* the {xs:.2f} it needs, and it makes "
      f"**${g5.iloc[0].money:+.2f} a basket**, i.e. nothing. And it is not stable: "
      f"{g5.iloc[0].acc_HIST} in 2009-2020, then {g5.iloc[0].acc_DEV} and {g5.iloc[0].acc_VAL}.\n")
    w(f"5. The placebo bar says that is nothing: shuffling the same 26 grid readings so they cannot possibly "
      f"know anything still produces a best-of **{pb_grid['zero']:.1f}** (2021-mid2025) and "
      f"**{pb_grid['duka']:.1f}** (2009-2020) 19 times out of 20.\n")
    w("6. **The dollar check passes and the dollar still does not predict.** Gold and our dollar index move "
      f"opposite each other hard, **{corr[(corr.window=='1h')&(corr.scope=='ALL')].corr_contemporaneous.mean():.2f}** "
      "at the 1-hour horizon — exactly the mechanism we expected. But a *past* dollar move tells you nothing "
      "about gold's *next* move: that correlation is **0.00**.\n")
    w("7. News: the approximate NFP and FOMC dates are real (gold moves 1.6x and 2.6-3.9x as much in the hour "
      "after them), the CPI dates are mostly wrong (no volatility bump at all). Even so, no event window has a "
      "direction: every cell that looks good in 2009-2020 looks bad in 2021-2025, and vice versa.\n")
    w("8. **M1 is not special.** The first time this project has read a signal off the 1-minute chart, and it "
      "lands where every other timeframe lands. The one honest pattern is that **fading** beats **following** at "
      "these horizons (range-position and RSI above breakout and Bollinger breaks) — consistent with BT4 — "
      "but over the whole clock the best fade reading tops out at **52.8 right in 100**, just short of the "
      "52.9 it needs, and pays two cents a basket. Fading is the right direction and not enough of it.\n")
    w("9. Hours: almost every good-looking hour is a different hour in a different period — noise, as BT7 and "
      "BT14 predicted. **Two exceptions, both found AFTER the fact and therefore NOT BT16 results.** "
      "(a) Being long in the last hour before gold's daily close (section 6b). "
      "(b) The FADE rules in the 20:00-21:00 UTC hour score 67.6 / 68.2 / 53.7 right in 100 on two independent "
      "feeds (section 6c). **Section 6d then tested that one to destruction.** The rollover data problem is "
      "NOT the explanation — the baskets that straddle the break are the *weak* ones on both feeds — but the "
      "live spread is **7.6x normal at 21:00 UTC** and one of his gold symbols does not quote that hour at "
      "all; and in **hour 20, the only tradeable hour, the newest period is 52.5 and 50.4 right in 100, "
      "paying -$0.07 and -$0.47.** Not tradeable.\n")
    w("10. Verdict: **none of the three pre-registered ideas produces the entry.** The gap from 50 to 52.9 is "
      "not closed by dollar strength, by news dates, or by classic rules on M1/M5/M15 read over the whole "
      "clock. The one lead that fell out of the hour table by accident survived the data-artifact test "
      "(section 6d) and then died on the live spread: the part that pays sits in an hour whose spread is 7.6x "
      "normal, and the hour he can trade is break-even in the newest period. **Nothing in BT16 is "
      "tradeable.**\n")

    w("\n## 1. The yardstick, re-measured on exactly these settings\n")
    w("Money per basket at 0.10 lots. 'Oracle' = an entry that always picks the better side (impossible, "
      "it is the ceiling). 'Coin flip' = random direction at the same minutes.\n")
    w(md_table(pd.DataFrame([
        dict(what="Oracle (always right)", per_basket=f"${eb:+.2f}"),
        dict(what="Breaks even", per_basket=f"$0.00"),
        dict(what="Coin flip (50 in 100)", per_basket=f"${ec:+.2f}"),
        dict(what="Always wrong", per_basket=f"${ew:+.2f}")]),
        names=["", "money per basket, 0.10 lots"]))
    w(f"\n**Break-even accuracy = {xs:.2f} right calls in 100** "
      f"({xs:.2f} on 2009-2020 and 52.87 on 2021-mid2025 — the same number on both feeds). "
      "That confirms the 52.9 the brief was built on.\n")
    w(f"\nEntry chances scored: **{int(bl.n.sum()):,}** (both sides of every one simulated minute by minute), "
      "every 30th minute of the clock from 2009-03-15 to 2025-06-26.\n")

    w("\n## 2. THE RANKED LIST — all 44 pre-registered readings, best first\n")
    w("`right in 100` = of the chances it took where the two directions gave different outcomes, how often it "
      "chose the one that won. Money per basket at **0.10 lots**. The three period columns are the ones that "
      "matter: **a reading that is high in one period and low in another has found nothing.**\n")
    cols = ["reading", "n", "acc", "acc_HIST", "acc_DEV", "acc_VAL", "acc_spread_pts", "money", "all_three_above_50"]
    nmz = ["reading", "baskets", "right in 100 (pooled)", "2009-2020", "2021-2023", "2024-mid2025",
           "swing between periods", "$ per basket", "above 50 in all three?"]
    w(md_table(allv, cols, nmz))
    w("\nReference rows (not variants — what the market itself did at the same chances):\n")
    w(md_table(ref, cols, nmz))
    w("\n**Read the 'swing between periods' column before the headline column.** Every reading above 51 pooled "
      "swings by more than it is above 50. That is the signature of nothing being there.\n")

    w("\n## 3. Test 1 — dollar strength. The sanity check passes; the idea still fails.\n")
    w("The index: equal-weighted from the 7 USD pairs in both feeds, +1 weight where the dollar is the base "
      "(USDJPY, USDCAD, USDCHF) and -1 where it is the quote (EURUSD, GBPUSD, AUDUSD, NZDUSD), read only from "
      "bars already closed. It rises when the dollar strengthens.\n")
    w("\n### The blocking sanity check: gold against the dollar, at the same time\n")
    cc = corr[~corr.window.str.contains("leadlag")]
    w(md_table(cc.pivot_table(index=["feed", "window"], columns="scope",
                              values="corr_contemporaneous").reset_index().round(3)))
    w("\n**This passes, strongly and everywhere.** Minus 0.4 to minus 0.56 on every window, every feed, every "
      "period. In plain words: when the dollar goes up, gold goes down, about as reliably as anything in this "
      "project has ever measured. The construction is right, so the result below is a real answer and not a "
      "broken measurement.\n")
    w("\n### And now the number that kills it: does a PAST dollar move predict gold's NEXT move?\n")
    w(md_table(corr[corr.window.str.contains("leadlag")][["feed", "window", "n", "corr_contemporaneous"]],
               names=["feed", "past dollar move over this window vs gold's next day", "windows", "correlation"]))
    w("\n**0.00 on every single one.** The dollar and gold move together *at the same instant*, and that is all. "
      "By the time you can see the dollar move on a closed bar, gold has already made its matching move. "
      "There is nothing left to trade. This is the cleanest result in BT16 and it is worth understanding, "
      "because it explains why the mechanism is real and the trade is still not there.\n")
    w("\n### The 5 dollar readings\n")
    w(md_table(grid_v[grid_v.family == "dollar"], cols, nmz))
    w("\nBest of them picks the right way about 50.4 times in 100 and loses money in every period. "
      "The mirror (gold *follows* the dollar) is just 100 minus these, so it is equally useless.\n")

    w("\n## 4. Test 2 — news events. Approximate dates. Say it out loud.\n")
    w("**We have no economic calendar.** The dates were worked out from the calendar: NFP = first Friday "
      "13:30 UTC (12:30 in US summer time), CPI = the 13th moved to the nearest weekday, FOMC = the Wednesday "
      "nearest 8 nominal dates a year. **The CPI day is a guess and the FOMC day is good to about a week.** "
      "Any finding in this section is weakened by that, and there is no finding to weaken.\n")
    w("\n### How good are our guessed dates? The volatility test tells us.\n")
    w("Average size of gold's move in the 60 minutes after the release, against the same clock minute on "
      "ordinary days. In gold price units (multiply by 10 for dollars at 0.10 lots).\n")
    w(md_table(nv, ["feed", "event", "n_events", "avg_move_60min_after", "avg_move_60min_control", "ratio"],
               ["feed", "event", "releases", "move after release", "move on an ordinary day", "times bigger"]))
    w("\n- **NFP: 1.6x.** Our first-Friday dates are landing on the real thing.\n"
      "- **FOMC: 2.6x to 3.9x.** The biggest volatility event in the data. Our Wednesday dates are landing too.\n"
      "- **CPI: 0.9x to 1.0x — no bump at all.** Either our 13th-of-the-month guess is mostly wrong, or CPI does "
      "not move gold in the hour after. Given NFP and FOMC both show up clearly, the honest reading is that "
      "**our CPI dates are mostly ordinary days** and the CPI rows below should be ignored.\n")
    w("\n**And a volatility bump is not an edge.** A bigger move makes it easier to reach the $15 profit cap "
      "AND easier to reach the $20 loss cap. It changes how fast a basket resolves, not which way.\n")
    w("\n### The 18 news readings\n")
    w("'always long' is the direct test of whether gold has a *bias* in that window. 'momentum' continues "
      "gold's own move over the 30 one-minute bars ending at the entry.\n")
    w(md_table(news_v, cols, nmz))
    w("\nPooled across the three events (an aggregate of the cells above, not an extra variant):\n")
    w(md_table(news_p, cols, nmz))
    w("\n**Look at the swing column.** FOMC one minute after, continue the move: **60 right in 100 in "
      "2009-2020 and 42 in 100 in 2021-2025.** FOMC an hour after, same rule: **48 then 69.** These are 30 to 95 "
      "baskets per cell. At that size a cell wanders about 7 points up or down by itself, and the placebo "
      "confirms it: shuffling these same cells so they cannot know anything still throws up a best-of around "
      f"**{pb_all['zero']:.0f} in 100** nineteen times out of twenty. Every one of these numbers is inside that.\n")
    w("\n**So: no directional bias found before, at, or an hour after NFP, CPI or FOMC** — on approximate "
      "dates, with samples too small to see anything under about 57 anyway.\n")

    # ---------------------------------------------------------------- the one cell that passed, and why it is not real
    fu = pd.concat([part("followup_duka.csv"), part("followup_zero.csv")], ignore_index=True)
    fu.to_csv(os.path.join(OUT, "followup_posthoc_control.csv"), index=False)
    cand = allv[allv.all_three_above_529]
    w(f"\n### 4b. The {len(cand)} readings out of 44 that cleared 52.9 in ALL THREE periods — and why they are "
      "still nothing\n")
    w("Honesty demands these get their own section rather than being buried in the table.\n")
    w(md_table(cand, cols + ["n_HIST", "n_DEV", "n_VAL"], nmz + ["chances 2009-20", "chances 2021-23",
                                                                 "chances 2024-25"]))
    w("\nAll three are news cells. Two are 'continue gold's move over the last 30 one-minute bars' on our "
      "guessed CPI minute; one is 'always long, 60 minutes after NFP'. Here is why none of them is a finding, "
      "in order of how damning it is.\n")
    mn = int(cand[[f"n_{p}" for p in PER]].min().min())
    w(f"\n**(a) Look at the last three columns: as few as {mn} chances in a period.** On 18 to 60 chances an "
      "accuracy wanders **7 to 12 points up or down on its own**. 'Consistent across all three periods' is a "
      "powerful test when each period holds thousands of trades — it is close to meaningless when the newest "
      f"period holds {mn}. These cells pass the consistency test the way three coins can all come up heads.\n")
    w("\n**(b) POST-HOC CONTROL, added after seeing this result and labelled as such.** The same two rules, at "
      "the same clock minutes, on ordinary days with no release — thousands of chances instead of 190:\n")
    w(md_table(fu[fu.test.str.contains("CONTROL") | (fu.test == "ANY_MINUTE_grid_M_momentum30")].round(3),
               ["feed", "test", "n", "acc", "money"],
               ["feed", "post-hoc control", "chances", "right in 100", "$ per basket, 0.10 lots"]))
    w("\n**Both rules are a 49.** 'Continue the 30-bar move' scores **49.0 right in 100 in 2009-2020 and 49.7 in "
      "2021-mid2025 over 190,000 chances** on the ordinary grid, and 48.4 to 50.5 on ordinary days at the very "
      "same clock minutes. 'Always long' scores 47.8 to 51.3 on the same controls (and 50.0 / 50.3 on the full "
      "grid). A rule that is a 49 everywhere it has enough data, and a 57 on 195 hand-picked chances, has not "
      "found a news effect. It has found 195 chances.\n")
    w("\n**(c) Our CPI dates are mostly not CPI.** The volatility table above shows **no bump at all** on these "
      "dates (0.9x to 1.0x) while NFP shows 1.6x and FOMC 2.6-3.9x. So whatever the two CPI cells are "
      "measuring, it is mostly ordinary mid-month days — which the control in (b) says are a 49.\n")
    w("\n**(d) Their own siblings disagree.** Same CPI date, same window, 'always long' instead of momentum: "
      "48.8 then 40.4. Same date, momentum, 30 minutes earlier instead of one minute later: 46.9 then 55.1. "
      "FOMC one minute after with momentum: 60.4 in the old years, 38.1 in the middle years. If a real effect "
      "existed, the neighbouring cells would not point in random directions.\n")
    w(f"\n**(e) They are below the placebo bar.** Searching 44 readings, luck alone produces a best-of "
      f"**{pb_all['duka']:.0f} to {pb_all['zero']:.0f} right in 100** nineteen times out of twenty, precisely "
      "because the news cells hold only 30 to 195 chances each. 57.5 is well inside that.\n")
    w("\n**Verdict: non-findings.** They are reported because they were pre-registered and they did pass the "
      "consistency test as written; they are rejected because the same rules measured on a thousand times more "
      "data are coin flips that lose money. This is exactly the shape the user has been burned by before — "
      "caught this time by a control that the earlier tests did not have.\n")

    w("\n## 5. Test 3 — direction from M1 and M5 themselves (never done in this project before)\n")
    w("M1 had only ever been the trade simulator here; the lowest signal timeframe ever tested was M5. "
      "All 21 readings use the same 30-minute entry grid, so only the *direction* differs between them.\n")
    w(md_table(grid_v[grid_v.family == "m1m5"], cols, nmz))
    w("\n### The one honest pattern: fading slightly beats following\n")
    fam = grid_v[grid_v.family == "m1m5"].copy()
    fam["spirit"] = np.where(fam.reading.str.startswith(("R3", "R4")), "fade", "follow")
    sp = fam.groupby("spirit").apply(lambda x: pd.Series(dict(
        readings=int(len(x)), avg_right_in_100=round((x.acc * x.n_dec).sum() / x.n_dec.sum(), 2),
        avg_money_per_basket=round((x.money * x.n).sum() / x.n.sum(), 3))),
        include_groups=False).reset_index()
    w(md_table(sp, names=["spirit of the rule", "readings", "average right in 100", "$ per basket, 0.10 lots"]))
    w("\nThe two fade rules (buy when price sits low in its recent range; buy when RSI is low) average above 50; "
      "the five follow rules (EMA cross, breakout, MACD, Bollinger break, 10-bar momentum) average below it. "
      "**That is the right sign** — BT4 found structure drifts toward mean-reverting as the timeframe drops, and "
      "this confirms it one and two timeframes lower than BT4 ever looked. It is a real property of the data.\n")
    w("\n**It is also not enough to trade.** The best fade reading reaches about 51 right in 100, needs 52.9, and "
      "the one that looks best of all — RSI on M1 — scores **53.4 in 2009-2020 and then 50.9 and 50.8 in the two "
      "newer periods.** That is the exact pattern the user has been burned by before, and it is a non-finding.\n")

    w("\n## 5b. Year by year — the test the brief insisted on\n")
    w("A rule that works in one period only is noise. The same is true one level down: a rule that works in some "
      "years and not others has found nothing. Here are the three best large-sample readings and the reference, "
      "**every year separately**. Money per basket at **0.10 lots**.\n")
    yt = yr[yr.reading.isin(list(g5.reading.head(3)) + ["REF_always_long"])].copy()
    piv = yt.pivot_table(index="year", columns="reading", values="acc").round(2).reset_index()
    piv["year"] = piv.year.astype(int).astype(str)
    w(md_table(piv))
    w("\nAnd the money, same readings, same years:\n")
    pm = yt.pivot_table(index="year", columns="reading", values="money").round(2).reset_index()
    pm["year"] = pm.year.astype(int).astype(str)
    w(md_table(pm))
    bestyr = yt[yt.reading == g5.iloc[0].reading]
    w(f"\n**{g5.iloc[0].reading}** — the best large-sample reading in the whole test — ranges from "
      f"**{bestyr.acc.min():.1f} to {bestyr.acc.max():.1f} right in 100 across the 17 individual years**, and is "
      f"below 50 in {int((bestyr.acc < 50).sum())} of them. Its best years are 2009-2011 and its worst are "
      "2018, 2019 and 2023. There is no year-on-year stability here at all: that is a reading with no edge "
      "whose yearly numbers are just the sampling noise of about 900 baskets a year (which wanders roughly "
      "1.7 points either way on its own, so a 9-point spread is more than noise alone — it is noise plus "
      "whatever the market happened to be doing, and neither is tradeable).\n")
    w("\nThe full year-by-year table for all 44 readings is in `by_year.csv`.\n")

    w("\n## 6. Hour of day — reported, never selected on\n")
    w("**The guard, stated before the run and held:** BT7 found no session edge and BT14 showed "
      "'London beats New York' turns up in half of all charts made from pure random numbers. So nothing in BT16 "
      "was chosen, tuned or recommended on an hour. Any single good-looking hour below is inside what noise "
      "produces unless it is large AND repeats in all three date periods. **None does.**\n")
    w("\n### What the market itself did, by hour (always-long at every chance, 0.10 lots)\n")
    hl = hpool[hpool.reading == "REF_always_long"].copy()
    hl["hour"] = hl.hour.astype(int)
    hl["n"] = hl.n.astype(int)
    hl["right_in_100"] = hl.acc.round(2)
    hl["usd_per_basket"] = hl.money.round(2)
    hl["worst_period"] = hl.worst_period_acc.round(1)
    hl["best_period"] = hl.best_period_acc.round(1)
    w(md_table(hl, ["hour", "n", "right_in_100", "usd_per_basket", "worst_period", "best_period"],
               ["hour UTC", "baskets", "right in 100", "$ per basket", "worst period", "best period"]))
    w("\n### The best reading overall, by hour\n")
    bn = g5.iloc[0].reading
    hb = hpool[hpool.reading == bn].copy()
    hb["hour"] = hb.hour.astype(int)
    hb["n"] = hb.n.astype(int)
    w(f"**{bn}**, pooled over all three periods, and the worst/best single period for the same hour:\n")
    w(md_table(hb.round(2), ["hour", "n", "acc", "money", "worst_period_acc", "best_period_acc"],
               ["hour UTC", "baskets", "right in 100", "$ per basket", "worst period", "best period"]))
    w("\nRead the last two columns. Every hour that looks good pooled has a period where it is below 50. "
      "The hour-of-day table is a way to generate 24 chances to fool yourself, which is why it is reported "
      "and not used.\n")
    # ---------------------------------------------------------------- 6b the pre-close long bias
    h20 = pd.concat([part("hour20_duka.csv"), part("hour20_zero.csv")], ignore_index=True)
    h20.to_csv(os.path.join(OUT, "hour_hold_times.csv"), index=False)
    pc = pd.concat([part("preclose_duka.csv"), part("preclose_zero.csv"),
                    part("preclose_duka_2021_2024.csv")], ignore_index=True)
    pc.to_csv(os.path.join(OUT, "preclose_posthoc.csv"), index=False)
    ro = pd.concat([part("rollover_duka.csv"), part("rollover_zero.csv")], ignore_index=True)
    ro.to_csv(os.path.join(OUT, "rollover_gap_posthoc.csv"), index=False)

    w("\n### 6b. The one thing in the hour table that is NOT noise — and it is still not a finding\n")
    w("**POST-HOC. This was not pre-registered.** It was spotted in the hour-of-day table that the brief "
      "declared descriptive-only, and the brief forbids choosing anything on an hour. It is reported as a "
      "**lead for a future pre-registered test**, not as a BT16 result.\n")
    w("\nLook at hours 20 and 21 UTC in the always-long table above. They are the only hours above 52.9 in "
      "every period. But hour 21 only exists on the ~43% of days when gold is still open then (gold's daily "
      "close moves with US summer time), so 'hour 21' is a slice of the year rather than an hour. The honest "
      "version of the same question is **how long before gold's daily close the basket opens**:\n")
    w("\n**Always long, by distance from gold's daily close. Money per basket at 0.10 lots.**\n")
    w(md_table(pc, ["feed", "bucket", "n_HIST", "acc_HIST", "money_HIST", "n_DEV", "acc_DEV", "money_DEV",
                    "n_VAL", "acc_VAL", "money_VAL"],
               ["feed", "window", "chances 09-20", "right/100 09-20", "$ 09-20",
                "chances 21-23", "right/100 21-23", "$ 21-23", "chances 24-25", "right/100 24-25", "$ 24-25"]))
    w("\n`duka` = Dukascopy 2009-2020, `zero` = Exness Zero 2021-mid2025, `duka_2021_2024` = the **independent "
      "cross-check**: the same window measured on the *other* feed over the same recent years.\n")
    w("\n**What is real about it.** Being long gold in the last hour before its daily close picks the right way "
      "more often than 50 in every period and on both feeds independently — 52.4 and 52.0 in 2009-2020, "
      "52.6 and 55.3 in 2021-2023, 59.5 and 60.6 in 2024-25. Two separate data providers agreeing on the sign "
      "across 16 years is not what noise looks like.\n")
    w("\n**Why it is still not a finding, and must not be traded on this evidence:**\n")
    w("1. **It barely pays, and only in the newest year.** On the conservative feed the money per basket in "
      "that window is **-$0.19 in 2009-2020, -$0.69 in 2021-2023, and +$2.37 in 2024 only** (257 chances). "
      "A rule that loses money for 15 years and makes it in the last one is a bull-market artifact until "
      "proven otherwise — and gold rose about 60% in that last stretch.\n")
    w("2. **It was selected on an hour, after the fact.** 24 hours x 25 readings is 600 cells. The brief "
      "banned exactly this, and the ban is why it is labelled a lead and not a result.\n")
    w("3. **One of our two feeds has a data problem right there.** Measured, post-hoc:\n")
    w(md_table(ro, ["feed", "scope", "n", "mean_gap_price", "median_gap_price", "mean_gap_usd_010", "pct_gap_up"],
               ["feed", "period", "daily rollovers", "mean jump (gold price)", "median jump",
                "mean jump $ at 0.10 lots", "% that jump UP"]))
    w("\n   The **Exness Zero** feed jumps **+0.66 of a dollar in gold price on average across every daily "
      "rollover, upward 61% of the time** — that is **+$6.61 at 0.10 lots**, nearly half the $15 profit cap, "
      "handed to any long held across the break. The **Dukascopy** feed shows essentially nothing (+0.04 "
      "average, median exactly 0.00, up only 47% of the time) and is slightly negative in the same recent "
      "years. **The two feeds disagree, so at least one is wrong**, and the feed that shows the jump is the "
      "feed that shows the bigger pre-close bias. That accounts for roughly 2.7 of the points and about $1.40 "
      "a basket of the difference between the two cross-check rows above.\n")
    w("4. **The daily rollover is exactly when a broker charges swap.** Our simulator charges none. If part of "
      "this is a real rollover jump, the financing we are not charging is the other half of the same trade.\n")
    w("\n**What to do with it:** write it down as the pre-registered question for the next test — *is gold "
      "long-biased in the last 30-60 minutes before its daily close, after swap, on both feeds, and does it "
      "hold in 2025?* **TEST (2025-07 onward) is the clean out-of-sample for exactly this, and it was not "
      "touched here.** Do not look at it until the question is written down first.\n")
    w("\nFor completeness, how those baskets resolve (they are not overnight holds, so swap on the position "
      "itself is a small worry; the rollover jump is the big one):\n")
    w(md_table(h20[h20.hour.isin([10, 15, 19, 20, 21, 22])],
               ["feed", "hour", "n", "long_money", "median_hold_min", "pct_hit_profit_cap",
                "pct_still_open_after_8h"],
               ["feed", "hour UTC", "chances", "$ per long basket", "typical minutes held",
                "% reached the $15 cap", "% still open after 8h"]))

    # ---------------------------------------------------------------- 6c the late-session fade
    lf = pd.concat([part("latefade_duka.csv"), part("latefade_zero.csv")], ignore_index=True)
    lf.to_csv(os.path.join(OUT, "late_fade_posthoc.csv"), index=False)
    lb = pd.concat([part("latebands_duka.csv"), part("latebands_zero.csv")], ignore_index=True)
    lb.to_csv(os.path.join(OUT, "late_fade_bands_posthoc.csv"), index=False)
    ls = pd.concat([part("latespread_duka.csv"), part("latespread_zero.csv")], ignore_index=True)
    ls.to_csv(os.path.join(OUT, "late_fade_spread_posthoc.csv"), index=False)

    w("\n### 6c. The biggest thing BT16 turned up — the FADE rules in the hour before gold's daily close\n")
    w("**POST-HOC. Not pre-registered, found in the descriptive hour table, and therefore NOT a BT16 result.** "
      "It is written up in full because it is the only thing in this whole test that is both large and "
      "repeated on two independent data feeds — and because the user asked which hours produce the winners.\n")
    w("\nThe two **fade** rules (buy when price sits low in its recent 20-bar range; buy when RSI is low) do far "
      "better between **20:00 and 21:00 UTC** — the last hour or two of gold's trading day — than they do "
      "anywhere else. Money per basket at **0.10 lots**, at the user's measured 0.072 spread:\n")
    w(md_table(lb[(lb.band.str.startswith("20-21")) & (lb.spread_price == 0.072) & (lb.period != "ALL")]
               .sort_values(["reading", "period"]),
               ["reading", "feed", "period", "n", "acc", "money"],
               ["reading", "feed", "period", "chances", "right in 100", "$ per basket"]))
    w("\nFor comparison, the same readings over the whole clock are 51.2 and 52.8 right in 100 and LOSE money. "
      "And 'always long' in the same hours is only 51.5 to 54.4, so this is not just gold's uptrend.\n")
    w("\n**Why two feeds matter here.** 2009-2020 is Dukascopy and 2021-2025 is Exness Zero, so those are "
      "different data providers as well as different years. The cross-check run (`late_fade_posthoc.csv`) also "
      "measured 2021-2024 on the **Dukascopy** feed and got **62.1 and 67.0** for RSI-on-M1 against the Zero "
      "feed's 59.3 and 60.8 — the same answer from a different provider over the same years. That rules out "
      "the Exness rollover artifact from section 6b as the cause.\n")
    w("\n**Now the two things that stop it being tradeable.**\n")
    w("\n**(1) It has been fading out.** Read the period column above. RSI-on-M1: **67.6 in 2009-2020, 68.2 in "
      "2021-2023, and 53.7 in 2024-mid2025.** Range-position on M5: **57.4, 56.8, then 49.7.** The newest "
      "period is the weakest for every one of them. That is the shape of an effect being arbitraged away — or "
      "of one that was never as big as it looked. Either way, **the most recent data is the data that matters**, "
      "and in the most recent data it is close to gone.\n")
    w("\n**(2) The money depends entirely on the spread, and we do not know the real late-session spread.** "
      "Every number in BT16 was priced at the user's measured **0.072**, which he measured during normal hours. "
      "Here is the same window re-priced at wider spreads (both barriers moved, so it is faithful about which "
      "barrier gets hit):\n")
    w(md_table(ls[ls.period == "ALL"].sort_values(["reading", "feed", "spread_price"]),
               ["feed", "reading", "spread_price", "spread_usd_010", "n", "acc", "money"],
               ["feed", "reading", "spread (gold price)", "spread $ at 0.10 lots", "chances",
                "right in 100", "$ per basket"]))
    w("\n**The accuracy barely moves and the money collapses.** The range-position rule is already losing at a "
      "0.20 spread. RSI-on-M1 survives 0.20 and dies by 0.40. For scale: the data's own spread column (broken "
      "on 95% of minutes, ERRORS #14/#34, but indicative where it is filled) records a mean of about **1.0 "
      "and 1.4 gold price units at 22:00 and 23:00 UTC**, against about 0.20 at 08:00 — while **20:00-21:00 is "
      "actually below the daytime average (0.09-0.12)**. That is why the window was split at 21:00: the "
      "22:00-23:00 part sits in the rollover where the spread blows out (and its accuracy jumps around "
      "61 / 46 / 69 across the three periods, i.e. noise), while 20:00-21:00 is a normal-spread hour.\n")
    w("\n**Honest verdict on 6c:** a large, cross-feed, cross-period historical pattern — fade price extremes "
      "in the hour before gold's daily close — that **has decayed to roughly nothing in the newest period** and "
      "whose profit depends on a late-session spread nobody has measured on his account. "
      "**It is the one thing worth another test, and it is not a reason to trade anything today.**\n")
    w("\n**The three things that would settle it, in order:**\n")
    w("1. **Measure his real spread at 20:00-21:30 UTC** on his own terminal for two weeks. That single number "
      "decides whether any of this is money. If it is above about 0.30, 6c is dead.\n"
      "2. Write the question down (a pre-registered brief) and then run it on **TEST 2025-07 onward**, which "
      "was deliberately not touched here and is the only clean out-of-sample left for it.\n"
      "3. Only if both pass: demo forward test at 0.01 lots, where a $15 win is $1.50.\n")

    # ---------------------------------------------------------------- 6d the straddler test
    sd = pd.concat([part("straddle_duka.csv"), part("straddle_zero.csv")], ignore_index=True)
    sd.to_csv(os.path.join(OUT, "straddle_posthoc.csv"), index=False)
    lead = sd[sd.band == "20-21 UTC (the lead)"]
    w("\n### 6d. Is 6c just the data problem from 6b? No — tested, and it is not. But the live spread narrows it.\n")
    w("**POST-HOC, and the test that nearly killed 6c.** Section 6b found the Exness Zero feed jumps +$6.61 per "
      "0.10 lot across every daily rollover. Section 6c's window is the hour immediately before that rollover. "
      "So any basket still open across the break would collect a jump that does not exist in real trading, and "
      "a fade rule that happens to sit long in that hour would harvest it.\n")
    w("\n**The rollover minute was measured, not assumed.** Every point where the next 1-minute bar is more than "
      "45 minutes and less than 8 hours later is a daily session break. On the Zero feed the break **starts at "
      "20:58 or 21:58 UTC** and **resumes at 22:01 or 23:01** (the hour shifts with US summer time); on "
      "Dukascopy it starts 21:00/22:00 and resumes 22:00/23:00. So the break is 21:00-22:00 UTC in summer and "
      "22:00-23:00 in winter — immediately after 6c's window, exactly as feared.\n")
    w("\n**Every basket still open at the break was flagged and the window split in two:**\n")
    w(md_table(lead[lead.reading.isin(["R4_rsi_M1", "R3_rangepos_M1", "R3_rangepos_M5", "REF_always_long"])
                    & (lead.period == "ALL")].sort_values(["reading", "feed"]),
               ["feed", "reading", "group", "n", "acc", "money"],
               ["feed", "reading", "basket group", "baskets", "right in 100", "$ per basket"]))
    w("\n**The answer is the opposite of the worry.** 25-44% of the window's baskets do straddle the rollover — "
      "and **they are the WEAK group, not the strong one.** Range-position on M1: 59.6 right in 100 for the "
      "baskets that close before the break against 53.4 for the straddlers on the Zero feed, and 57.8 against "
      "50.6 on Dukascopy. **Removing every straddler makes 6c stronger on both feeds, not weaker.**\n")
    w("\n**And the split itself is not cheating.** Splitting on exit time is conditioning on something that "
      "happens after entry, so it had to be controlled. The always-long control shows **no accuracy difference "
      "between the two groups at all** (Dukascopy 53.0 vs 53.5; Zero 54.6 vs 54.6), so the split is not "
      "mechanically inflating anything. It does show a small money benefit to straddling longs — about +$0.37 "
      "on Dukascopy and +$0.58 on Zero — so the rollover mildly favours longs on **both** feeds, not just the "
      "one with the big jump. That is worth a fraction of a dollar, not the $4 the lead shows.\n")
    w("\n**6c with every straddler removed, both feeds, per period:**\n")
    w(md_table(lead[(lead.group == "closes BEFORE rollover") & (lead.period != "ALL")]
               .sort_values(["reading", "feed", "period"]),
               ["reading", "feed", "period", "n", "acc", "money"],
               ["reading", "feed", "period", "baskets", "right in 100", "$ per basket"]))
    w("\n**So the data problem does not explain the lead.** That is settled.\n")
    w("\n**But the live spread by hour does narrow it, badly.** Measured from the terminal this session, the "
      "gold spread is a median of **0.040 at every hour except 21:00 UTC, where it is a median of 0.304 and an "
      "average of 0.676** — and the plain XAUUSDz symbol does not trade 21:00-22:00 at all. So **only hour 20 "
      "is in a tradeable-spread hour.** Splitting 6c again on that line, straddlers still removed:\n")
    h20only = sd[(sd.band == "hour 20 only") & (sd.group == "closes BEFORE rollover") & (sd.period != "ALL")]
    h21only = sd[(sd.band == "hour 21 only") & (sd.group == "closes BEFORE rollover") & (sd.period != "ALL")]
    w("\n**Hour 20 UTC only (spread 0.040 — the tradeable hour):**\n")
    w(md_table(h20only.sort_values(["reading", "feed", "period"]),
               ["reading", "feed", "period", "n", "acc", "money"],
               ["reading", "feed", "period", "baskets", "right in 100", "$ per basket"]))
    w("\n**Hour 21 UTC only (spread 0.304, and one symbol does not trade at all — NOT tradeable):**\n")
    w(md_table(h21only.sort_values(["reading", "feed", "period"]),
               ["reading", "feed", "period", "n", "acc", "money"],
               ["reading", "feed", "period", "baskets", "right in 100", "$ per basket"]))
    w("\n**Read those two tables together and the lead collapses to almost nothing usable.** The big numbers "
      "(60.6 to 75.6 right in 100) are all in **hour 21**, which has 7.6x the spread and which one of his two "
      "gold symbols does not even quote. In **hour 20**, the hour he could actually trade, the newest period "
      "reads **52.5 and 50.4 right in 100 and pays -$0.07 and -$0.47 a basket** — at or below the 52.85 "
      "break-even — against 56.7 and 58.8 in 2009-2020.\n")
    w("\n**Final verdict on 6b/6c/6d, stated plainly.** The rollover artifact is **cleared** — it is not the "
      "explanation. What is left is a **real 2009-2023 effect that has decayed to break-even by 2024-mid2025 "
      "in the only hour whose spread allows trading**, and whose eye-catching 67-70 numbers live in an hour "
      "that cannot be traded. **It is not tradeable on this evidence and it is not a finding.** "
      "And 67 right in 100 being far above anything else in two weeks of this project was the correct reason "
      "to be suspicious rather than excited.\n")

    w("\n### By session (the same guard applies)\n")
    sg = ses[ses.reading.isin(list(g5.reading.head(3)) + ["REF_always_long"])].copy()
    sg["years"] = np.where(sg.feed == "duka", "2009-2020", "2021-mid2025")
    w(md_table(sg.round(2).sort_values(["reading", "years"]),
               ["reading", "years", "scope", "n", "acc", "money"],
               ["reading", "years", "session", "baskets", "right in 100", "$ per basket"]))
    w("\n(Asian 00-07, London 07-12, Overlap 12-15, New York 15-20, Late 20-24 UTC.)\n")
    w("\n**London is not the best session** — it is one of the worst, the same as BT15 found. The only session "
      "that is consistently least bad is **Late (20-24 UTC)**, and that is the same thing section 6c is about: "
      "the hour or two before gold's daily close. It is not a second finding, it is the same one, and it "
      "carries the same warnings — decaying, hour-selected, and spread-dependent.\n")

    w("\n## 7. The prize, if an entry were ever found — the money at each accuracy\n")
    w(f"Money per basket at **0.10 lots**, straight from the measured outcome table (money is exactly linear in "
      f"accuracy at a fixed shape). Per day uses the **{bpd:.1f} baskets a day** the robot's own mechanics allow "
      "(one basket at a time, 60-second cooldown, a direction pauses after 2 losses in a row).\n")
    w(md_table(lad, names=["right in 100", "$ per basket (0.10 lots)", "$ per day (0.10 lots)",
                           "$ per day (0.01 lots)", "$ per month (0.01 lots, 21 days)"]))
    w("\nSo the prize is real and worth chasing: **54 right in 100 pays about "
      f"${(0.54*eb+0.46*ew):.2f} a basket**, and **56 pays about ${(0.56*eb+0.44*ew):.2f}**. "
      "Each extra point above 52.9 is worth roughly "
      f"**${(eb-ew)/100:.2f} a basket**. That is why this question is worth the work. "
      "**Nothing we tested gets there.**\n")
    w("\n### Day by day, run through the robot's own mechanics (0.10 lots)\n")
    dd = days[days.reading.isin(["COINFLIP_50", "BREAKEVEN_52.9", "SKILL_55", "SKILL_60", "REF_always_long"])]
    w(md_table(dd.round(2), ["feed", "reading", "baskets", "days", "baskets_per_day", "avg_day", "median_day",
                             "win_days_pct", "best_day", "worst_day", "worst_losing_streak_days", "per_basket"],
               ["feed", "entry", "baskets", "days", "baskets/day", "average day", "typical day", "winning days %",
                "best day", "worst day", "worst losing run (days)", "$ per basket"]))
    w("\n`feed duka` = 2009-2020, `feed zero` = 2021 to mid-2025. The 52.9 row lands on about zero a day, which "
      "is the whole point of calling it break-even.\n")

    w("\n## 8. The placebo bar — how good a reading has to look before it means anything\n")
    w("Each reading's signal was slid forward by a random whole number of weeks, which keeps the signal's own "
      "habits and the market's own clustering but destroys any real link between them. Then we take the best of "
      "all the readings in that draw, and repeat 200 times. **Nineteen times out of twenty, pure luck produces "
      "a best-of this good:**\n")
    w(md_table(pd.DataFrame([
        dict(what="26 grid readings, 2009-2020", bar=f"{pb_grid['duka']:.2f} right in 100",
             money=f"${pbm_grid['duka']:+.3f} per basket"),
        dict(what="26 grid readings, 2021-mid2025", bar=f"{pb_grid['zero']:.2f} right in 100",
             money=f"${pbm_grid['zero']:+.3f} per basket"),
        dict(what="all 44 readings incl. the small news cells, 2009-2020",
             bar=f"{pb_all['duka']:.2f} right in 100", money="-"),
        dict(what="all 44 readings incl. the small news cells, 2021-mid2025",
             bar=f"{pb_all['zero']:.2f} right in 100", money="-")]),
        names=["placebo search", "19-out-of-20 bar (accuracy)", "19-out-of-20 bar (money)"]))
    w(f"\nOur actual best large-sample reading is **{g5.iloc[0].acc:.2f}**. The bar is "
      f"**{pb_grid['zero']:.2f}**. It does not clear it, and nothing else does either. "
      "The news bar is far higher simply because those cells have 30 to 140 baskets each — small samples "
      "wander, and that is all the 60s and 68s in section 4 are.\n")

    w("\n## 9. What was tested, in full, and the honest verdict\n")
    w(md_table(pd.DataFrame([
        dict(family="Test 1 dollar strength", readings=5,
             best=f"{grid_v[grid_v.family=='dollar'].acc.max():.2f}", verdict="nothing"),
        dict(family="Test 2 news events (approximate dates)", readings=18,
             best=f"{news_v.acc.max():.2f}", verdict="nothing - samples too small to see under ~57 anyway"),
        dict(family="Test 3 M1/M5/M15 classic rules", readings=21,
             best=f"{grid_v[grid_v.family=='m1m5'].acc.max():.2f}", verdict="nothing; fading beats following, both lose"),
    ]), names=["family", "readings (pre-registered)", "best right in 100 (pooled)", "verdict"]))
    w("\n**The pre-registered verdict rule said:** if no reading beats 50 out of sample, the entry question is "
      "closed for these three ideas. What actually happened is slightly worse than that — several readings beat "
      "50 pooled, none beats it in all three periods by more than the placebo throws up by luck, and none comes "
      "near 52.9. So:\n")
    w("\n- **Dollar strength is a real mechanism and still not a trade.** The correlation is strong and negative, "
      "exactly as predicted, but it is *simultaneous*. Gold has already moved by the time the dollar move is on "
      "a closed bar. Predictive content: zero, at every lookback from 5 minutes to 5 days.\n"
      "- **News dates are mostly a dead end without a real calendar**, and the volatility test shows our NFP and "
      "FOMC dates are right while our CPI dates are mostly wrong. Even on the right dates there is no direction.\n"
      "- **M1 and M5 add nothing.** The first look at 1-minute signals in this project puts them where every "
      "other timeframe already was. The only real pattern - fade beats follow at low timeframes - confirms BT4 "
      "and is far too small to pay the costs.\n")
    w("\n**What this does not say.** It does not say gold is unpredictable. It says these 44 readings, over "
      "16 years and 190,000 entry chances, do not call direction. The honest next places to look are the ones "
      "BT16 could not: a real economic calendar with real release times and actual surprise versus expectation; "
      "order-flow or positioning data we do not have; and the real yields / real-rate channel, which is the other "
      "arithmetic mechanism for gold and is not in this dataset at all.\n")
    w("\n**The lead to write down, from section 6c.** Fading price extremes (RSI low -> buy, RSI high -> sell; "
      "or price low in its 20-bar range -> buy) in the **20:00-21:00 UTC** hour scores **67.6 right in 100 in "
      "2009-2020, 68.2 in 2021-2023 and 53.7 in 2024-mid2025** — and the same pattern appears on the other "
      "data feed over the same years, which is rare in this project. It is also **decaying**, it was "
      "**hour-selected after the fact**, and its money disappears if the real spread in that hour is above "
      "about 0.30 of a dollar in gold price. **Measure that spread first** — two weeks of his own terminal at "
      "20:00-21:30 UTC — then write a brief, then use TEST 2025-07 onward, which was not touched here.\n")
    w("\n**A second, smaller lead, from section 6b.** Being long in the last 30-60 "
      "minutes before gold's daily close picks the right way more than half the time in all three periods and "
      "on both data feeds independently. It was found by accident in a table the brief had declared "
      "descriptive-only, it only makes money in the newest year, and the Exness Zero feed has a +$6.61 jump "
      "across every daily rollover that Dukascopy does not show. So it is a **lead, not a finding**. "
      "The next test should be pre-registered as: *is gold long-biased in the last 30-60 minutes before its "
      "daily close, on both feeds, with overnight financing charged, and does it survive 2025?* "
      "**TEST 2025-07 onward is the clean out-of-sample for it and was not touched here.**\n")
    w("\n**A data problem to log either way (section 6b).** The Exness Zero XAUUSD M1 bars jump +0.66 in gold "
      "price on average across every daily rollover, upward 61% of the time, while Dukascopy shows +0.04 and a "
      "median of exactly zero over the same years. At 0.10 lots that is $6.61 a rollover. Any backtest that "
      "holds gold across the daily break on the Zero feed is being handed money that the other feed says is not "
      "there. This affects BT9 and any future overnight work, not just BT16.\n")
    w("\n**Unfinanced, declared.** `common.simulate` charges no overnight financing. At $15/$20 most baskets "
      "close the same day, so the error is small here, but every money figure above is still slightly optimistic.\n")
    w("\n## Files\n")
    w("Pre-registered results: `ranked_all_readings.csv` (every reading, pooled and per period) · "
      "`ranked_grid_readings.csv` · `ranked_news_readings.csv` · `gold_vs_dollar_correlation.csv` · "
      "`news_volatility.csv` · `by_hour_of_day.csv` · `by_hour_of_day_x_period.csv` · `by_session.csv` · "
      "`by_year.csv` · `money_ladder.csv` · `day_by_day.csv`.\n")
    w("\nPost-hoc controls (labelled post-hoc in the text and logged at the end of the brief): "
      "`followup_posthoc_control.csv` · `preclose_posthoc.csv` · `rollover_gap_posthoc.csv` · "
      "`late_fade_posthoc.csv` · `late_fade_bands_posthoc.csv` · `late_fade_spread_posthoc.csv` · "
      "`hour_hold_times.csv`. Checkpoints in `parts/` (every phase resumes).\n")
    w("\nCode: `bt/bt16_lib.py`, `bt/bt16_run.py`, `bt/bt16_report.py`, `bt/bt16_tests.py` (the causality and "
      "scorer checks), `bt/bt16_followup.py` (the post-hoc controls). Brief: `bt/BT16_BRIEF.md`, with the "
      "post-hoc log appended at the end of it.\n")

    open(os.path.join(OUT, "report.md"), "w", encoding="utf-8").write("".join(L))
    print("wrote", os.path.join(OUT, "report.md"))
    print(allv[["reading", "n", "acc", "acc_HIST", "acc_DEV", "acc_VAL", "money"]].head(15).to_string(index=False))
    print(f"\nbreak-even {xs:.2f} | placebo grid P95 zero {pb_grid['zero']:.2f} duka {pb_grid['duka']:.2f} "
          f"| all-44 P95 zero {pb_all['zero']:.2f} duka {pb_all['duka']:.2f}")


if __name__ == "__main__":
    main()
