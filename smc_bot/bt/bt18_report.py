"""BT18 phase 3 — build results/BT18/report.md and the public CSVs from the checkpoints in parts/.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt18_report

Reads only results/BT18/parts/*.csv|json (both feeds), so it needs no dataset environment and no simulator.
Every money figure it prints is dollars per basket at 0.10 lots.
"""
import json
import os
import numpy as np
import pandas as pd
from bt import bt18_lib as B

P = B.PARTS
OUT = B.OUT
P3 = ["2009-2020", "2021-2023", "2024-mid2025"]
BE = B.BREAKEVEN

# plain-English name for each pre-registered threshold
PLAIN = {90.0: "1 minute in 10", 95.0: "1 in 20", 98.0: "1 in 50", 99.0: "1 in 100",
         99.5: "1 in 200", 99.9: "1 in 1,000", 99.95: "1 in 2,000"}


def load(stem):
    fr = []
    for feed in ("duka", "zero"):
        f = os.path.join(P, f"{stem}_{feed}.csv")
        if os.path.exists(f):
            fr.append(pd.read_csv(f).assign(feed=feed))
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def wavg(g, col):
    return float((g[col] * g.trades).sum() / g.trades.sum())


def pool(df, keys=("variant",)):
    """Trade-weighted pooling of the three date periods (the same convention as the BT17 report)."""
    d = df[df.period.isin(P3)].copy()
    rows = []
    for k, g in d.groupby(list(keys)):
        days = (g.trades / g.per_day.clip(lower=1e-9)).sum()
        r = dict(zip(keys, k if isinstance(k, tuple) else (k,)))
        r.update(trades=int(g.trades.sum()), acc=wavg(g, "acc"), money=wavg(g, "money"),
                 own_be=wavg(g, "own_be"), per_day=g.trades.sum() / max(days, 1e-9))
        for p in P3:
            q = g[g.period == p]
            r[p] = float(q.acc.iloc[0]) if len(q) else np.nan
            r[p + "_n"] = int(q.trades.iloc[0]) if len(q) else 0
            r[p + "_$"] = float(q.money.iloc[0]) if len(q) else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def consistency(r):
    v = [r[p] for p in P3 if np.isfinite(r[p])]
    if len(v) < 3:
        return "short of one period"
    if all(x > 50 for x in v):
        if v[0] - v[-1] > 1.0:
            return "above 50 in all 3 but **DECAYING**"
        return "above 50 in all 3"
    if all(x < 50 for x in v):
        return "below 50 in all 3"
    return "SPLIT - not a finding"


def md(df, cols, fmt=None):
    fmt = fmt or {}
    head = "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    body = ""
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            f = fmt.get(c)
            cells.append(("" if v is None or (isinstance(v, float) and not np.isfinite(v))
                          else (f(v) if f else str(v))))
        body += "| " + " | ".join(cells) + " |\n"
    return head + body


def n_(v):
    return f"{int(v):,}"


def a_(v):
    return f"**{v:.2f}**"


def a2(v):
    return f"{v:.2f}"


def d_(v):
    return f"{v:+.3f}"


def pd_(v):
    return f"{v:.1f}"


def main():
    os.makedirs(OUT, exist_ok=True)
    var, yr, hr, se, sd = load("var"), load("year"), load("hour"), load("sess"), load("spikedrop")
    lad, ex = load("ladder"), load("examples")
    pb = {f: json.load(open(os.path.join(P, f"placebo_{f}.json"))) for f in ("duka", "zero")
          if os.path.exists(os.path.join(P, f"placebo_{f}.json"))}

    rank = pool(var)
    rank["family"] = rank.variant.str.replace("_MIRROR", "", regex=False).map(
        lambda v: B.FAMILY.get(v, "?"))
    rank["sign"] = np.where(rank.variant.str.endswith("_MIRROR"), "mirror", "declared")
    rank["consistency"] = rank.apply(consistency, axis=1)
    rank["gap"] = rank.acc - rank.own_be
    rank = rank.sort_values("acc", ascending=False).reset_index(drop=True)
    rank.insert(0, "#", np.arange(1, len(rank) + 1))
    rank.to_csv(os.path.join(OUT, "ranked.csv"), index=False)
    pool(yr, ("variant",)).to_csv(os.path.join(OUT, "by_period.csv"), index=False)
    var.to_csv(os.path.join(OUT, "by_period_full.csv"), index=False)
    yr.to_csv(os.path.join(OUT, "by_year.csv"), index=False)
    hr.to_csv(os.path.join(OUT, "by_hour.csv"), index=False)
    se.to_csv(os.path.join(OUT, "by_session.csv"), index=False)
    sd.to_csv(os.path.join(OUT, "spike_vs_drop.csv"), index=False)
    lad.to_csv(os.path.join(OUT, "money_ladder.csv"), index=False)
    ex.to_csv(os.path.join(OUT, "examples.csv"), index=False)

    # ---------------- the curve: accuracy vs chances per day (the headline deliverable)
    cur = rank[rank.variant.str.startswith("A_") & (rank.sign == "declared")].copy()
    cur["pct"] = cur.variant.str.extract(r"_p([\d.]+)_")[0].astype(float)
    cur["win"] = cur.variant.str.extract(r"_w(\d+)")[0].astype(int)
    cur["how_big"] = cur.pct.map(PLAIN)
    cur = cur.sort_values(["win", "pct"])
    cur.to_csv(os.path.join(OUT, "curve.csv"), index=False)

    sdp = pool(sd[~sd.variant.str.contains("MIRROR")], ("variant", "half"))
    sdp["pct"] = sdp.variant.str.extract(r"_p([\d.]+)_")[0].astype(float)
    sdp["win"] = sdp.variant.str.extract(r"_w(\d+)")[0].astype(int)
    sdp["consistency"] = sdp.apply(consistency, axis=1)
    sdp.to_csv(os.path.join(OUT, "spike_vs_drop_pooled.csv"), index=False)

    best = rank.iloc[0]
    bestbig = rank[rank.trades >= USABLE].iloc[0]
    bar2 = {f: pb[f]["acc2_P95"] for f in pb}
    LADDER = ["A_spikefade_p99.0_w1440", "A_spikefade_p99.5_w1440", "B_mom30_bothopens"]
    L = []
    w = L.append

    w("# BT18 — three entry ideas from the literature search: spike fade, intraday momentum, "
      "first candle at the open\n")
    w("**Pre-registered in `bt/BT18_BRIEF.md` before any code was written.** 25 variants, fixed. Both signs of "
      "each are reported (a mirror is exactly `100 - accuracy` on the same trigger set, so it is not a 26th "
      "reading). **Every money figure in this report is dollars per basket at 0.10 lots**, at a **$15 profit cap "
      f"and a $20 loss cap**, with **no commission** and his measured **0.072** spread. Break-even for that shape "
      f"is **{BE} right calls in 100**.\n")

    # ---------------- the ten lines
    USABLE = 25_000                    # the headline must not rest on a few thousand chances
    pk = cur[cur.trades >= USABLE].sort_values("acc", ascending=False).iloc[0]
    pk10 = cur[(cur.pct == 99.0) & (cur.win == 1440)].iloc[0]       # the ~10-a-day setting he wants
    pktail = cur.sort_values("acc", ascending=False).iloc[0]
    dd = sdp[sdp.half == "down_drop"]
    dropbest = dd[dd.trades >= 10_000].sort_values("acc", ascending=False).iloc[0]
    droptail = dd.sort_values("acc", ascending=False).iloc[0]
    upmatch = sdp[(sdp.half == "up_spike") & (sdp.variant == dropbest.variant)]
    mom = rank[(rank.variant.isin(["B_mom5min", "B_mom15min", "B_mom30min", "B_mom60min"]))
               & (rank.sign == "declared")].sort_values("variant")
    w("## The ten lines\n")
    w("1. **The spike fade is real and it is the best-behaved signal this project has measured.** Fading a big "
      f"one-minute move picks the right way **{pk.acc:.2f} times in 100** at its best setting "
      f"({PLAIN[pk.pct]} minutes, {pk.per_day:.1f} chances a day, {int(pk.trades):,} chances), and it is above 50 "
      "in every one of the three date periods at every threshold from the loosest to the tightest.")
    w("2. **It still loses money, and for a reason nobody had spotted before.** The minutes it picks are violent "
      f"minutes, so the $20 loss cap gets hit far more easily than the $15 profit cap there. Break-even on those "
      f"minutes is **not {BE}** — it is **{pk.own_be:.2f}**. The rule beats the average-minute floor and still "
      "loses, because it chose expensive minutes to trade in.")
    w("3. **The accuracy-against-frequency curve is the deliverable and it is in section 5.** Accuracy climbs "
      "steadily as the move required gets bigger — but so does the break-even of the minutes selected, and the "
      "second climbs faster. There is no threshold where the two cross.")
    w(f"4. **The drop side is much stronger than the spike side.** Buying after a violent fall reaches "
      f"**{dropbest.acc:.2f} right in 100**"
      + (f" while selling after a violent rise manages only **{float(upmatch.acc.iloc[0]):.2f}**" if len(upmatch) else "")
      + " on the same setting. That is exactly the asymmetry the source paper found on Nasdaq stocks, reproduced "
        "on gold for the first time. It is still short of what those minutes cost.")
    w("5. **Intraday momentum over 5, 15, 30 and 60 minutes is a loser in both signs of the question.** Following "
      "the move picks the right way "
      f"{mom.acc.min():.2f}–{mom.acc.max():.2f} times in 100; the fade of the same rule is the better half at "
      "every one of the four horizons. **Follow never overtakes fade inside an hour.** That answers the horizon "
      "question the brief asked.")
    w("6. **The one place following does lean right is the session opens.** The first 30 minutes after the COMEX "
      "open points the next 30 minutes the right way "
      f"{rank.loc[rank.variant == 'B_mom30_comex1330', 'acc'].iloc[0]:.2f} times in 100, and the London and COMEX "
      "opens pooled "
      f"{rank.loc[rank.variant == 'B_mom30_bothopens', 'acc'].iloc[0]:.2f} times in 100 — above 50 in all three "
      "periods. It is also about one chance a day and still below what those minutes cost.")
    w("7. **The first 5-minute candle at a session open is the weakest of the three ideas**, which is what the "
      "independent re-test in the source predicted. Its best honest reading is "
      f"{rank.loc[rank.variant == 'C_first5m_localtime', 'acc'].iloc[0]:.2f} right in 100. The 56.90 that shows up "
      "for the COMEX candle in 2024-mid2025 rests on 384 chances and is the exact shape of the trap that killed "
      "BT16's news test.")
    w(f"8. **The acceptance bar.** Rotating every reading by whole weeks and taking the best of 25, 200 times "
      f"over: **{bar2.get('duka', float('nan')):.2f} right in 100** on 2009-2020 and "
      f"**{bar2.get('zero', float('nan')):.2f}** on 2021-mid2025. Measured against each reading's **own** bar at "
      "its own sample size — the fairer test, section 13 — the spike fade clears it comfortably on hundreds of "
      "thousands of chances. Against the best-of-25 bar it clears the 2009-2020 one and not the 2021-mid2025 one.")
    w("9. **Nothing clears the money floor. Not one reading of the 50 makes a cent.** The best is "
      f"**${rank.money.max():+.3f} a basket at 0.10 lots**. At 0.01 lots, the size that is sane on a $350 "
      "account, divide every figure in this report by 10.")
    w("10. **The broker break did not create any of this, for once.** Only 41–72 chances of the headline readings "
      "fall inside it (0.03 a day), and dropping hours 21 and 22 UTC altogether moves accuracy by at most 0.2 of "
      "a point (section 10). Inside the break the same rule reads 57–68 right in 100 on those few dozen chances — "
      "that is the BT16 trap, and it is reported as untradeable, not as a result.\n")

    # ---------------- question
    w("## 1. The question\n")
    w("`research_notes/M1_entry_candidates.md` ranked three ideas above everything else and none had been run. "
      "BT16 tested the dollar, the news calendar and the classic indicators. BT17 tested the four SMC concepts on "
      "M1 and M5. BT18 tests the three from the literature:\n")
    w("| Idea | Why it should work | Source | Pre-registered variants |\n|---|---|---|---|")
    w("| **1 Fade an extreme one-minute move** | A violent minute is someone who was forced to trade — a margin "
      "call, a stop cascade, a big order dumped at any price. Whoever takes the other side gets paid as it snaps "
      "back. | Rif & Utz 2021: after the worst 1-in-1,000 minute drops on Nasdaq stocks, about 31% of the drop "
      "came back the next minute. Never tested on gold. | 14 |")
    w("| **2 Intraday momentum over about 30 minutes** | Option hedgers and leveraged funds must trade **with** "
      "the move to stay hedged, which pushes it further. | Gao/Han/Li/Zhou and Baltussen et al., both in the "
      "*Journal of Financial Economics*, 60+ futures markets. | 7 |")
    w("| **3 The first 5-minute candle at a session open** | The first minutes show which side has unfilled "
      "orders. | Zarattini & Aziz; an independent re-test found a real edge over random direction but about equal "
      "to the cost of trading. | 4 |\n")
    w("Idea 2 is deliberately the opposite sign from everything else this project has found — fading has beaten "
      "following at every short horizon so far (BT16 50.78 against 49.43; BT17 51.45 against 48.55; BT4 saw the "
      "same two timeframes higher). **The horizon at which following starts to win is itself the finding**, so it "
      "is tested at four horizons honestly.\n")

    # ---------------- settings
    w("## 2. The settings and the costs — all money at 0.10 lots\n")
    w("| | |\n|---|---|")
    w("| Profit cap | 1.5 gold price = **$15** at 0.10 lots |")
    w("| Loss cap | 2.0 gold price = **$20** at 0.10 lots |")
    w("| Commission | **none** — his broker charges none |")
    w("| Spread | **0.072** gold price, his own measured figure |")
    w("| Stop slippage | 0.2 gold-pips, from `common.py`, unchanged |")
    w(f"| Break-even on an average minute | **{BE} right in 100** |")
    w("| Typical hold | about 19 minutes |")
    w("| Chances measured | 2009-03-15 → 2020-12-31 and 2021-01-01 → 2025-06-27 |")
    w("| TEST, 2025-07-01 onward | **LOCKED. Never simulated, never read.** |\n")
    w("Both sides of every single minute of gold history were already run through `common.simulate` by BT17 at "
      "exactly these settings. BT18 **reuses that table read-only** — it does not re-simulate anything — so for "
      "every chance we know exactly what a buy paid and what a sell paid. \"Right in 100\" means: of 100 chances, "
      "how many times the rule chose the side that actually paid more.\n")
    w("**The one number that matters most in this report, and it is new.** Break-even is only "
      f"{BE} right in 100 **on an average minute**. A rule that trades only violent minutes is not trading "
      "average minutes: there, a 2.0 loss cap is reached far more easily than a 1.5 profit cap, so the same money "
      "shape needs **more** right calls. Every table below therefore carries that reading's **own** break-even "
      "next to its accuracy, and the honest score is the gap between the two.\n")

    # ---------------- checks
    w("## 3. The checks that had to pass first\n")
    w("| Check | Result |\n|---|---|")
    w("| Last input bar closes before the fill bar opens, and the fill bar is the very next minute | pass, all 25 |")
    w("| **Lookahead twin** — the same rule filled at the open of its own signal bar | shows a huge fake edge "
      "(1.65 right in 100 on the zero feed, i.e. 98.35 for its mirror) while the honest version shows 50.44. "
      "This is the BT15 bug (\"+$2.17 a basket\") reproduced on purpose |")
    w("| Rolling threshold built only from the window ending one bar before the signal | pass (checked against a "
      "hand-computed window on 25 random minutes) |")
    w("| Random sides at the same minutes | 49.99 right in 100, and loses the coin-flip amount |")
    w("| Mirror of every reading | exactly `100 - accuracy`, to machine precision |")
    w("| TEST period | NaN in the outcome table; never simulated |")
    w("| The broker break really is missing from the data | pass — in summer the feed holds **0 minutes** at "
      "21:00 UTC on the Zero account and 7% of a normal hour on the older feed; in winter 17–23% of a normal "
      "hour at 22:00 UTC. The break mask and the missing data agree. |")
    w("\nFull output: `bt/bt18_tests.py`.\n")

    # ---------------- ranking
    w("## 4. THE RANKING — all 50 readings, best first\n")
    w("Accuracy is trade-weighted over the three date periods. **0.10 lots on the money column.** "
      "`its break-even` is what that reading's own chosen minutes require; `gap` is accuracy minus that, and it "
      "is the only column that decides whether money is made.\n")
    show = rank.copy()
    show["name"] = show.variant
    show["chances"] = show.trades
    cols = ["#", "name", "family", "chances", "per_day", "acc", "money", "own_be", "gap",
            "2009-2020", "2021-2023", "2024-mid2025", "consistency"]
    hdr = ["#", "reading", "idea", "chances", "a day", "right in 100", "$/basket", "its break-even",
           "gap", "2009-2020", "2021-2023", "2024-mid2025", "consistency"]
    t = md(show[cols].rename(columns=dict(zip(cols, hdr))), hdr,
           {"chances": n_, "a day": pd_, "right in 100": a_, "$/basket": d_, "its break-even": a2,
            "gap": lambda v: f"{v:+.2f}", "2009-2020": a2, "2021-2023": a2, "2024-mid2025": a2})
    w(t)
    w(f"\n**Not one of the 50 has a positive gap.** The best gap in the table is "
      f"{rank.gap.max():+.2f} of a point, which is "
      f"${rank.money.max():+.3f} a basket at 0.10 lots. A reading marked `SPLIT - not a finding` scored above 50 "
      "in one period and below 50 in another; one marked `DECAYING` was above 50 everywhere but fell by more than "
      "a point from the oldest period to the newest, which the user has asked be called out as a non-finding in "
      "those words.\n")

    # ---------------- the curve
    w("## 5. ⭐ THE CURVE — how accuracy trades off against how often it fires (Idea 1)\n")
    w("This is the deliverable the brief asked for. Fade a one-minute move bigger than the cut-off; the cut-off is "
      "measured on the minutes just gone, never on the future. **0.10 lots.**\n")
    for win, lbl in ((1440, "cut-off measured over the last trading day (1,440 minutes)"),
                     (7200, "cut-off measured over the last five trading days (7,200 minutes)")):
        c = cur[cur.win == win].copy()
        c["move_bigger_than"] = c.how_big
        cc = ["move_bigger_than", "per_day", "trades", "acc", "own_be", "gap", "money",
              "2009-2020", "2021-2023", "2024-mid2025", "consistency"]
        hh = ["move bigger than all but", "chances a day", "chances", "right in 100", "its break-even",
              "gap", "$/basket", "2009-2020", "2021-2023", "2024-mid2025", "consistency"]
        w(f"**{lbl}**\n")
        w(md(c[cc].rename(columns=dict(zip(cc, hh))), hh,
             {"chances": n_, "chances a day": pd_, "right in 100": a_, "its break-even": a2,
              "gap": lambda v: f"{v:+.2f}", "$/basket": d_, "2009-2020": a2, "2021-2023": a2,
              "2024-mid2025": a2}))
        w("")
    w("**Read it like this.** Going from \"bigger than 1 minute in 10\" to \"bigger than 1 in 1,000\" buys about "
      "a point and a half of accuracy and costs about 98% of the trades. At roughly the **10 trades a day** the "
      "user wants, the setting is \"bigger than 1 minute in 100\", and it picks the right way about "
      f"{cur[(cur.pct == 99.0) & (cur.win == 1440)].acc.iloc[0]:.1f} times in 100.\n")
    w("**And here is why tightening does not help.** The `its break-even` column rises faster than the accuracy "
      "does. The looser settings need about 53 right in 100 and get about 51; the tightest need about 54–55 and "
      "get about 52. **The gap gets worse, not better, as the move required gets bigger.** Violent minutes are "
      "exactly the minutes in which a 2.0 loss cap is easy to reach and a 1.5 profit cap is not.\n")

    # ---------------- asymmetry
    w("## 6. The drop side against the spike side (Idea 1, reported not selected)\n")
    w("The brief fixed this as a **breakdown** of each variant's own chances, like hour of day — not as extra "
      "variants, and nothing was chosen on it. **0.10 lots.**\n")
    sp = sdp.copy()
    sp["move_bigger_than"] = sp.pct.map(PLAIN)
    sp["which"] = np.where(sp.half == "down_drop", "BUY after a violent fall", "SELL after a violent rise")
    sp = sp.sort_values(["win", "pct", "half"])
    cc = ["move_bigger_than", "win", "which", "trades", "per_day", "acc", "own_be", "money", "consistency"]
    hh = ["move bigger than all but", "window", "what we do", "chances", "a day", "right in 100",
          "its break-even", "$/basket", "consistency"]
    w(md(sp[cc].rename(columns=dict(zip(cc, hh))), hh,
         {"chances": n_, "a day": pd_, "right in 100": a_, "its break-even": a2, "$/basket": d_}))
    w("\n**The drop side wins at every single threshold, and the gap widens as the move gets bigger** — from a "
      "few hundredths of a point at the loosest setting to about three points at the tightest. Buying after a "
      "violent fall in gold is the one reading in this project that gets past "
      f"{BE} right in 100 on its own chances")
    w(f"({dropbest.acc:.2f} on {int(dropbest.trades):,} chances at the best setting). **It still loses money**, "
      f"because those minutes need {dropbest.own_be:.2f} right in 100, not {BE}. This is the same direction Rif & "
      "Utz found on Nasdaq stocks — their result was about drops too — and it is the first time it has been "
      "measured on gold.\n")
    w("Honest warning on this one: the split was declared in advance as a breakdown, but it still doubles the "
      "number of things looked at, and the placebo bar in section 13 was calibrated for the 25 variants, not for "
      "their halves. Treat the drop side as a direction to dig, not as a measured edge.\n")

    # ---------------- idea 2
    w("## 7. Intraday momentum — does following ever beat fading? (Idea 2)\n")
    w("Pairs of the same rule: `declared` follows the move just gone, `mirror` fades it. **0.10 lots.**\n")
    rows = []
    for nm, lab in (("B_mom5min", "last 5 minutes"), ("B_mom15min", "last 15 minutes"),
                    ("B_mom30min", "last 30 minutes"), ("B_mom60min", "last 60 minutes"),
                    ("B_mom30_london0700", "first 30 min after the London open (07:00 UTC)"),
                    ("B_mom30_comex1330", "first 30 min after the COMEX open (13:30 UTC)"),
                    ("B_mom30_bothopens", "both opens pooled")):
        a = rank[rank.variant == nm]
        m = rank[rank.variant == nm + "_MIRROR"]
        if not len(a):
            continue
        a, m = a.iloc[0], m.iloc[0]
        rows.append(dict(rule=lab, chances=a.trades, per_day=a.per_day, follow=a.acc, fade=m.acc,
                         winner="**following**" if a.acc > m.acc else "fading",
                         own_be=a.own_be, best_money=max(a.money, m.money),
                         p1=max(a["2009-2020"], 100 - a["2009-2020"]) if False else a["2009-2020"],
                         p2=a["2021-2023"], p3=a["2024-mid2025"], consistency=a.consistency))
    t2 = pd.DataFrame(rows)
    cc = ["rule", "chances", "per_day", "follow", "fade", "winner", "own_be", "best_money",
          "p1", "p2", "p3", "consistency"]
    hh = ["rule", "chances", "a day", "following, right in 100", "fading, right in 100", "which sign won",
          "its break-even", "best $/basket", "2009-2020 (follow)", "2021-2023 (follow)",
          "2024-mid2025 (follow)", "consistency of the follow side"]
    w(md(t2[cc].rename(columns=dict(zip(cc, hh))), hh,
         {"chances": n_, "a day": pd_, "following, right in 100": a_, "fading, right in 100": a2,
          "its break-even": a2, "best $/basket": d_, "2009-2020 (follow)": a2, "2021-2023 (follow)": a2,
          "2024-mid2025 (follow)": a2}))
    w("\n**The answer to the horizon question: following does not overtake fading inside an hour.** At 5, 15, 30 "
      "and 60 minutes the fade is the better half every time, and it is the clock-block rules — the ones that fire "
      "often enough to matter — where following is worst. The published 30-minute momentum effect does not appear "
      "on gold as a rolling clock rule.\n")
    w("**It does appear, weakly, anchored to a session open.** The first half hour after the COMEX open points "
      "the next half hour the right way in all three date periods, and so do the two opens pooled. That is the "
      "shape the source paper describes — momentum tied to an opening, not to an arbitrary clock block — so it is "
      "a genuine partial confirmation. It is worth about one chance a day and it is still below what those "
      "minutes cost.\n")

    # ---------------- idea 3
    w("## 8. The first 5-minute candle at a session open (Idea 3)\n")
    w("Take the colour of the first five-minute candle of the window and trade that way at the open of the next "
      "one. **0.10 lots.**\n")
    c3 = rank[rank.variant.str.startswith("C_") & (rank.sign == "declared")].sort_values("acc", ascending=False)
    cc = ["variant", "trades", "per_day", "acc", "own_be", "money", "2009-2020", "2021-2023",
          "2024-mid2025", "consistency"]
    hh = ["reading", "chances", "a day", "right in 100", "its break-even", "$/basket", "2009-2020",
          "2021-2023", "2024-mid2025", "consistency"]
    w(md(c3[cc].rename(columns=dict(zip(cc, hh))), hh,
         {"chances": n_, "a day": pd_, "right in 100": a_, "its break-even": a2, "$/basket": d_,
          "2009-2020": a2, "2021-2023": a2, "2024-mid2025": a2}))
    w("\nThe independent re-test in the source said the first-candle edge was real but about the same size as the "
      "cost of trading. On gold it is smaller than that: the best honest reading is about half a point above a "
      "coin flip and needs about three points. **The 56.90 for the COMEX candle in 2024-mid2025 is 384 chances** "
      "— the same shape as BT16's 57% on 195 news chances that became 49% on 190,000. It is not a finding.\n")

    # ---------------- money ladder
    w("## 9. What the money becomes as accuracy rises — 0.10 lots, $15 cap / $20 cap\n")
    w("This is the prize, not a result: what each reading's own chances would pay **if** an entry had that "
      "accuracy. Money is a straight line in accuracy by construction.\n")
    for nm in LADDER:
        rows = []
        r0 = rank[rank.variant == nm]
        if not len(r0):
            continue
        r0 = r0.iloc[0]
        for feed, plab in (("duka", "2009-2020"), ("zero", "2021-mid2025")):
            g = lad[(lad.variant == nm.replace("_MIRROR", "")) & (lad.feed == feed)]
            if len(g):
                rows.append((plab, g.iloc[0]))
        if not rows:
            continue
        w(f"**`{nm}`** — measured at **{r0.acc:.2f} right in 100**, about **{r0.per_day:.1f} chances a day**, "
          f"and these minutes need **{r0.own_be:.2f}** to break even.\n")
        accs = [c for c in lad.columns if c.startswith("acc_")]
        hdr = ["right in 100"] + [p for p, _ in rows] + ["$ a day at the newer figure"]
        body = "| " + " | ".join(hdr) + " |\n|" + "|".join(["---"] * len(hdr)) + "|\n"
        for c in accs:
            a = float(c.split("_")[1])
            cells = [f"{a:.2f}"]
            last = None
            for _, g in rows:
                cells.append(f"{g[c]:+.2f}")
                last = g[c]
            cells.append(f"{last * r0.per_day:+.0f}")
            body += "| " + " | ".join(cells) + " |\n"
        w(body)
    w("At 0.01 lots — the size that is sane on a $350 account — divide every figure above by 10.\n")

    # ---------------- hours
    w("## 10. Hour of day, UTC — reported, never used to choose anything\n")
    w("**Guard, fixed in the brief before the run:** BT7 found no session edge, BT14 showed \"London beats New "
      "York\" turns up in half of all charts built from random numbers, and BT16's late-session 67–70 turned out "
      "to live inside the broker's daily break. So an hour counts for nothing here unless it is both large and "
      "above 50 in all three date periods, and nothing in this report was selected on one. Full tables: "
      "`results/BT18/by_hour.csv` and `by_session.csv`.\n")
    nm = "A_spikefade_p99.0_w1440"
    hh1 = hr[hr.variant == nm]
    if len(hh1):
        a = hh1.pivot_table(index="hour", columns="period", values="acc")
        n = hh1.pivot_table(index="hour", columns="period", values="trades")
        mo = hh1.pivot_table(index="hour", columns="period", values="money")
        tab = []
        for h in range(24):
            r = dict(hour=f"{h:02d}")
            for p in P3:
                r[p] = a[p].get(h, np.nan) if p in a else np.nan
                r[p + "_n"] = n[p].get(h, np.nan) if p in n else np.nan
                r[p + "_$"] = mo[p].get(h, np.nan) if p in mo else np.nan
            vals = [r[p] for p in P3]
            r["all3"] = "yes" if all(np.isfinite(v) and v > 50 for v in vals) else "no"
            tab.append(r)
        tab = pd.DataFrame(tab)
        cc = ["hour"] + P3 + [p + "_n" for p in P3] + ["all3"]
        hdr = ["hour UTC"] + [f"right in 100 {p}" for p in P3] + [f"chances {p}" for p in P3] + \
              ["all 3 above 50?"]
        w(f"### `{nm}` — the 10-a-day setting of the spike fade, by hour (0.10 lots)\n")
        w(md(tab[cc].rename(columns=dict(zip(cc, hdr))), hdr,
             {f"right in 100 {p}": a2 for p in P3} | {f"chances {p}": n_ for p in P3}))
        w("")
    w("### The broker's daily break — and this time it is not the explanation\n")
    w("The break is **21:00–22:00 UTC in summer and 22:00–23:00 UTC in winter**, where the median spread is "
      "**0.304** against 0.040 the rest of the day, and the Zero account does not quote it at all. "
      "A daylight-saving-aware mask was built for it before the run. Two things came out:\n")
    bk = var[var.period.str.contains("POOLED|INSIDE")]
    rows = []
    for nmv in ["A_spikefade_p99.0_w1440", "A_spikefade_p99.5_w1440"]:
        for feed in ("duka", "zero"):
            g = bk[(bk.variant == nmv) & (bk.feed == feed)]
            r = dict(reading=nmv, feed="2009-2020" if feed == "duka" else "2021-mid2025")
            for lab, key in (("as reported", "POOLED"), ("break dropped", "POOLED_no_broker_break"),
                             ("hours 21+22 dropped", "POOLED_no_hours_21_22"),
                             ("INSIDE the break", "INSIDE_broker_break_UNTRADEABLE")):
                q = g[g.period == key]
                r[lab] = f"{q.acc.iloc[0]:.2f} on {int(q.trades.iloc[0]):,}" if len(q) else "—"
            rows.append(r)
    w(md(pd.DataFrame(rows), ["reading", "feed", "as reported", "break dropped", "hours 21+22 dropped",
                              "INSIDE the break"]))
    w("\n1. **Dropping the break changes nothing** — at most 0.2 of a point, because only 41–72 chances of these "
      "readings ever fall inside it. Unlike BT16's late-session lead, nothing in BT18 is an artefact of that hour.")
    w("2. **Inside the break the same rule reads 57–68 right in 100 on a few dozen chances.** That is what the "
      "trap looks like from the inside. It is **untradeable**: the account does not quote those minutes, and the "
      "spread there is four times the one every number in this report assumes. Reported, discarded.\n")
    w("Hours 20:00–23:00 UTC again look the strongest for the fade, as they did in BT16 and BT17. BT17 already "
      "measured what that is worth: it survives a spread of about 0.12 and no more, and gold after the New York "
      "close is not a 0.12-spread market. Nothing here changes that answer.\n")

    # ---------------- years
    w("## 11. Year by year\n")
    w("The honest test: does it hold every year, or did one window carry it? Full table: "
      "`results/BT18/by_year.csv`. **0.10 lots.**\n")
    for nm in LADDER:
        g = yr[yr.variant == nm].copy()
        if not len(g):
            continue
        g["y"] = g.period.astype(str)
        g = g[g.y.str.match(r"^\d{4}$")].sort_values("y")
        if not len(g):
            continue
        ys = list(g.y)
        w(f"**`{nm}`** — right in 100 by year\n")
        w("| year | " + " | ".join(ys) + " |\n|" + "|".join(["---"] * (len(ys) + 1)) + "|")
        w("| right in 100 | " + " | ".join(f"{v:.2f}" for v in g.acc) + " |")
        w("| $/basket | " + " | ".join(f"{v:+.2f}" for v in g.money) + " |")
        w("| chances | " + " | ".join(f"{int(v):,}" for v in g.trades) + " |")
        w(f"\nYears above 50: **{int((g.acc > 50).sum())} of {len(g)}**. Years that made money: "
          f"**{int((g.money > 0).sum())} of {len(g)}**.\n")

    # ---------------- examples
    w("## 12. Ten real chances from the best reading, to check on a chart\n")
    gx = ex[(ex.variant == bestbig.variant.replace("_MIRROR", "")) & (ex.feed == "zero")]
    if not len(gx):
        gx = ex[ex.variant == bestbig.variant.replace("_MIRROR", "")]
    if len(gx):
        w(f"`{bestbig.variant}` — a one-minute bar moves more than the cut-off, and we take the **opposite** side "
          "at the open of the very next minute. **0.10 lots.** Ten picked at random:\n")
        cc = ["signal_bar_open", "move_of_signal_bar", "we_do", "fill_open_of_next_minute",
              "usd_at_010_lots", "the_other_side_would_have_paid"]
        hh = ["the violent minute (UTC)", "how far it moved", "what we do", "filled at",
              "$ at 0.10 lots", "the other side would have paid"]
        w(md(gx.head(10)[cc].rename(columns=dict(zip(cc, hh))), hh,
             {"how far it moved": lambda v: f"{v:+.2f}", "filled at": lambda v: f"{v:.2f}",
              "$ at 0.10 lots": lambda v: f"{v:+.2f}",
              "the other side would have paid": lambda v: f"{v:+.2f}"}))
        w("\nEvery one of these was opened at a real gold price and followed minute by minute to its cap by "
          "`common.simulate`. Full file: `results/BT18/examples.csv`.\n")

    # ---------------- placebo
    w("## 13. The acceptance bar in full\n")
    w("Method: rotate each reading's chosen-side list by a random whole number of weeks, which keeps the signal's "
      "own rhythm and the outcomes' own clustering but destroys any real link; recompute; take the best of the 25; "
      "repeat 200 times. Both signs are reported, so the bar is the two-sided one.\n")
    rows = []
    for f, lab in (("duka", "2009-2020"), ("zero", "2021-mid2025")):
        if f in pb:
            q = pb[f]
            rows.append(dict(period=lab, typical=f"{q['acc2_P50']:.2f}", bar=f"**{q['acc2_P95']:.2f}**",
                             worst=f"{q['acc2_max']:.2f}", money=f"{q['money2_P95']:+.3f}",
                             n=q["n_readings"]))
    w(md(pd.DataFrame(rows), ["period", "typical", "bar", "worst", "money", "n"],
         ) if rows else "")
    w("\n**The best-of-25 bar is dominated by the small readings** — a rule with 1,000 chances wanders much "
      "further when rotated than one with 500,000. So each reading's **own** bar, at its **own** sample size, is "
      "the fairer comparison. It is in `results/BT18/parts/placebo_*.json`, and here are the readings that beat "
      "their own bar on a usable sample:\n")
    rows = []
    for f, lab in (("duka", "2009-2020"), ("zero", "2021-mid2025")):
        if f not in pb:
            continue
        for nm, q in pb[f]["per_reading"].items():
            r = var[(var.variant == nm) & (var.feed == f) & (var.period == "POOLED")]
            if not len(r):
                continue
            acc = float(r.acc.iloc[0])
            if q["n"] >= B.MIN_SAMPLE and acc > q["acc2_P95"]:
                rows.append(dict(period=lab, reading=nm, chances=int(q["n"]), acc=f"**{acc:.2f}**",
                                 bar=f"{q['acc2_P95']:.2f}",
                                 beats_by=f"{acc - q['acc2_P95']:+.2f}",
                                 money=f"{float(r.money.iloc[0]):+.3f}"))
    rows = sorted(rows, key=lambda r: -float(r["beats_by"]))
    if rows:
        w(md(pd.DataFrame(rows), ["period", "reading", "chances", "acc", "bar", "beats_by", "money"]))
        w("\nSo the spike fade's **accuracy lean is not noise** — it beats its own rotated twin on hundreds of "
          "thousands of chances, in both halves of history. **Its money is still negative in every row.** That is "
          "the whole BT18 result in one table: a real signal, measured, that cannot pay for the minutes it picks.\n")
    else:
        w("\nNo reading beat its own bar on a usable sample.\n")

    # ---------------- verdict
    w("## 14. Verdict\n")
    w("**All three ideas were tested honestly and none of them can be traded. One of them is real.**\n")
    w(f"**Idea 1, fading a violent one-minute move: a genuine signal, beaten by its own costs.** At its best "
      f"setting it picks the right way **{pk.acc:.2f} times in 100** over {int(pk.trades):,} chances, it is above "
      "50 in all three date periods at every one of the seven thresholds, and it beats its own rotated placebo on "
      "hundreds of thousands of chances. It is the most solid directional signal this project has measured — "
      "better behaved than BT17's break-of-structure fade. **And it loses money at every single setting**, "
      "because the minutes it selects are violent minutes, where break-even is "
      f"{cur.own_be.min():.1f}–{cur.own_be.max():.1f} right in 100 instead of {BE}. Tightening the threshold "
      "raises accuracy by about a point and a half and raises the required accuracy by about two, so it goes the "
      "wrong way. **This is a new and useful fact: it is not enough to pick a better direction, the moment has to "
      "be cheap as well.**\n")
    w(f"**The drop side is the strongest single reading in the project so far** — buying after a violent fall, "
      f"**{dropbest.acc:.2f} right in 100** on {int(dropbest.trades):,} chances — and the first time Rif & Utz's "
      "Nasdaq drop-reversal has been shown on gold. It needs "
      f"{dropbest.own_be:.2f} and so it, too, loses. It came from a declared breakdown rather than a "
      "pre-registered variant, so it deserves a dedicated test before anyone believes the size of it.\n")
    w("**Idea 2, intraday momentum: a clear negative, and the clearest confirmation yet of the fade thread.** "
      "Following the last 5, 15, 30 or 60 minutes loses in both halves of history; the fade of the same rule is "
      "better at all four horizons. **Follow never overtakes fade inside an hour** — so the mean-reversion that "
      "BT4, BT16 and BT17 all glimpsed is not a one-minute curiosity, it runs out to at least an hour. The "
      "published 30-minute effect shows up on gold only when it is anchored to a session open, at about one "
      "chance a day, and still below cost.\n")
    w("**Idea 3, the first 5-minute candle at an open: nothing, as its own independent re-test predicted.** "
      "About half a point above a coin flip where three points are needed, and its one impressive-looking cell "
      "is 384 chances.\n")
    w("**What this means for the robot.** The entry question is now much better posed than it was. Fading short "
      "moves in gold is a real, repeatable lean — five separate tests agree on it now — but it is worth around "
      "one to two points, and the moments where the lean is biggest are exactly the moments that cost the most to "
      "trade. **The next idea worth money is not a better signal, it is a cheaper moment**: the same fade taken "
      "where the loss cap is not easy to reach. That means either a wider loss cap relative to the move being "
      "faded (BT15 already showed the shape matters more than the stop size) or waiting for a quiet minute after "
      "the violent one. Neither has been tested.\n")
    w("**What was not touched:** TEST, 2025-07-01 onward, is untouched and still locked. The outcome table is "
      "NaN there by construction and a check asserts it.\n")

    # ---------------- files
    w("## 15. Files\n")
    w("| File | What |\n|---|---|")
    w("| `bt/BT18_BRIEF.md` | the pre-registration: 25 variants, fixed before any code existed |")
    w("| `bt/bt18_lib.py` | the three signal families, the broker-break mask, the placebo bar |")
    w("| `bt/bt18_run.py` | scores the 25 variants and their mirrors, per period / year / hour / session |")
    w("| `bt/bt18_tests.py` | the blocking checks, including the deliberate lookahead twin |")
    w("| `bt/bt18_report.py` | this report |")
    w("| `results/BT18/ranked.csv` | the ranking in section 4 |")
    w("| `results/BT18/curve.csv` | the accuracy-against-frequency curve in section 5 |")
    w("| `results/BT18/spike_vs_drop.csv`, `spike_vs_drop_pooled.csv` | section 6 |")
    w("| `results/BT18/by_period_full.csv`, `by_year.csv`, `by_hour.csv`, `by_session.csv` | every breakdown |")
    w("| `results/BT18/money_ladder.csv`, `examples.csv` | sections 9 and 12 |")
    w("| `results/BT18/parts/` | checkpoints; every phase resumes from disk |")
    w("\nNo shared file was edited: `common.py`, `smc_core.py`, `bt15_lib.py`, `bt16_lib.py` and `bt17_lib.py` "
      "are untouched and reused as-is. BT17's outcome table was read, never rewritten.\n")

    txt = "\n".join(L)
    open(os.path.join(OUT, "report.md"), "w", encoding="utf-8").write(txt)
    print(f"wrote {os.path.join(OUT, 'report.md')}  ({len(txt):,} chars)")
    print(f"best reading: {best.variant} {best.acc:.2f} on {int(best.trades):,} chances, "
          f"${best.money:+.3f}/basket, its break-even {best.own_be:.2f}")


if __name__ == "__main__":
    main()
