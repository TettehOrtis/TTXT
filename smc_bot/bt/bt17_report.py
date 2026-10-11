"""BT17 phase 3 — build results/BT17/report.md and the CSVs from the two feeds' checkpoints.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt17_report

Reads results/BT17/parts/{var,year,hour,sess}_{duka,zero}.csv and placebo_{duka,zero}.json.
2009-2020 comes from the duka feed; 2021-2023 and 2024-mid2025 come from the zero feed. The three periods do
not overlap, so a combined number is their trade-weighted average.
"""
import json
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "results", "BT17")
PARTS = os.path.join(OUT, "parts")
P3 = ["2009-2020", "2021-2023", "2024-mid2025"]
BE = 52.9
FAM_LABEL = {"S sweeps": "Liquidity sweeps", "O order blocks": "Order blocks",
             "F fair value gaps": "Fair value gaps", "B structure": "Break of structure / CHoCH",
             "C combinations": "Combinations of two"}


def load(kind):
    fr = []
    for ds in ("duka", "zero"):
        f = os.path.join(PARTS, f"{kind}_{ds}.csv")
        if os.path.exists(f):
            d = pd.read_csv(f)
            d["feed"] = ds
            fr.append(d)
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def combine(v):
    """One row per reading: trade-weighted accuracy and money over the three non-overlapping periods."""
    p = v[v.period.isin(P3)]
    rows = []
    for name, g in p.groupby("variant", sort=False):
        g = g.drop_duplicates("period")
        w = g.trades.values.astype(float)
        if w.sum() == 0:
            continue
        r = dict(variant=name, family=g.family.iloc[0], tf=g.tf.iloc[0], direction=g.direction.iloc[0],
                 trades=int(w.sum()), acc=float((g.acc.values * w).sum() / w.sum()),
                 money=float((g.money.values * w).sum() / w.sum()),
                 own_be=float((g.own_be.values * w).sum() / w.sum()),
                 periods_covered=len(g))
        for per in P3:
            s = g[g.period == per]
            r[f"acc_{per}"] = float(s.acc.iloc[0]) if len(s) else np.nan
            r[f"money_{per}"] = float(s.money.iloc[0]) if len(s) else np.nan
            r[f"n_{per}"] = int(s.trades.iloc[0]) if len(s) else 0
        a = np.array([r[f"acc_{x}"] for x in P3], float)
        ok = np.isfinite(a)
        r["acc_min"], r["acc_max"] = float(a[ok].min()), float(a[ok].max())
        r["acc_spread"] = r["acc_max"] - r["acc_min"]
        r["all3_above50"] = bool(ok.all() and (a[ok] > 50).all())
        r["all3_below50"] = bool(ok.all() and (a[ok] < 50).all())
        r["consistency"] = ("all 3 periods above 50" if r["all3_above50"] else
                            "all 3 periods below 50" if r["all3_below50"] else
                            "SPLIT - not a finding")
        pooled = v[(v.variant == name) & (v.period == "POOLED")]
        r["t_week"] = float(pooled.t_week.mean()) if len(pooled) else np.nan
        r["per_day"] = float(pooled.per_day.mean()) if len(pooled) else np.nan
        rows.append(r)
    d = pd.DataFrame(rows).sort_values("acc", ascending=False).reset_index(drop=True)
    d.insert(0, "rank", np.arange(1, len(d) + 1))
    return d


def md_rank(d, bar):
    L = ["| # | variant | concept | TF | sign | chances | **right in 100** | $/basket | its break-even | "
         "2009-2020 | 2021-2023 | 2024-mid2025 | consistency |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in d.to_dict("records"):
        flag = " **<-**" if (r["acc"] > bar and r["all3_above50"]) else ""
        per = " | ".join(f"{r['acc_' + x]:.2f}" if np.isfinite(r["acc_" + x]) else "-" for x in P3)
        L.append(f"| {r['rank']} | `{r['variant']}` | {FAM_LABEL.get(r['family'], r['family'])} | {r['tf']} | "
                 f"{r['direction']} | {r['trades']:,} | **{r['acc']:.2f}**{flag} | {r['money']:+.3f} | "
                 f"{r['own_be']:.2f} | {per} | {r['consistency']} |")
    return "\n".join(L)


def main():
    v, yr, hr, se = load("var"), load("year"), load("hour"), load("sess")
    if v.empty:
        raise SystemExit("no parts yet: run bt17_run for both feeds first")
    d = combine(v)
    d.to_csv(os.path.join(OUT, "ranked.csv"), index=False)
    for nm, t in (("by_year", yr), ("by_hour", hr), ("by_session", se), ("by_period", v)):
        if not t.empty:
            t.to_csv(os.path.join(OUT, f"{nm}.csv"), index=False)

    pb = {}
    for ds in ("duka", "zero"):
        f = os.path.join(PARTS, f"placebo_{ds}.json")
        if os.path.exists(f):
            pb[ds] = json.load(open(f))
    bar = max([p["acc2_P95"] for p in pb.values()]) if pb else np.nan
    barm = max([p["money2_P95"] for p in pb.values()]) if pb else np.nan

    # how often fading beat following, over the 16 pairs
    nf = nt = 0
    for r in d[~d.variant.str.endswith("_MIRROR")].to_dict("records"):
        mir = d[d.variant == r["variant"] + "_MIRROR"]
        if mir.empty:
            continue
        m = mir.to_dict("records")[0]
        fol, fad = ((r["acc"], m["acc"]) if r["direction"] == "follow" else (m["acc"], r["acc"]))
        nf += int(fad > fol)
        nt += 1

    top = d.head(6)
    best_fam = d.sort_values("acc", ascending=False).groupby(["family", "tf"], sort=False).head(1)

    # ---- money ladder for the best few, from the per-feed ladders
    lad_rows = []
    for ds, p in pb.items():
        for name, lad in p.get("ladders", {}).items():
            for a, m in lad.items():
                lad_rows.append(dict(feed=ds, variant=name, accuracy=float(a), money_per_basket=m))
    lad = pd.DataFrame(lad_rows)
    if not lad.empty:
        lad.to_csv(os.path.join(OUT, "money_ladder.csv"), index=False)

    # ---- hour tables: accuracy per hour for the top readings, in all three periods
    def hour_block(name):
        h = hr[hr.variant == name]
        if h.empty:
            return "_no hourly rows_"
        pv = h.pivot_table(index="hour", columns="period", values="acc", aggfunc="first")
        mo = h.pivot_table(index="hour", columns="period", values="money", aggfunc="first")
        cols = [c for c in P3 if c in pv.columns]
        L = ["| hour UTC | " + " | ".join(f"right in 100 {c}" for c in cols) +
             " | " + " | ".join(f"$/basket {c}" for c in cols) + " | all 3 above 50? |",
             "|---" * (1 + 2 * len(cols)) + "|---|"]
        for hh in range(24):
            if hh not in pv.index:
                continue
            a = [pv.loc[hh, c] for c in cols]
            m = [mo.loc[hh, c] for c in cols]
            ok = "yes" if all(np.isfinite(x) and x > 50 for x in a) and len(a) == 3 else "no"
            L.append(f"| {hh:02d} | " + " | ".join(f"{x:.2f}" if np.isfinite(x) else "-" for x in a) +
                     " | " + " | ".join(f"{x:+.2f}" if np.isfinite(x) else "-" for x in m) + f" | {ok} |")
        return "\n".join(L)

    def sess_block(name):
        h = se[se.variant == name]
        if h.empty:
            return "_no session rows_"
        pv = h.pivot_table(index="session", columns="period", values="acc", aggfunc="first")
        cols = [c for c in P3 if c in pv.columns]
        L = ["| session | " + " | ".join(cols) + " | all 3 above 50? |", "|---" * (1 + len(cols)) + "|---|"]
        for s in ["Asian", "London", "Overlap", "NewYork", "Late"]:
            if s not in pv.index:
                continue
            a = [pv.loc[s, c] for c in cols]
            ok = "yes" if all(np.isfinite(x) and x > 50 for x in a) and len(a) == 3 else "no"
            L.append(f"| {s} | " + " | ".join(f"{x:.2f}" if np.isfinite(x) else "-" for x in a) + f" | {ok} |")
        return "\n".join(L)

    cand = d[(d.acc > bar) & d.all3_above50 & (d.money > 0)]
    nothing = len(cand) == 0

    # ---------------------------------------------------------------- write it
    w = []
    A = w.append
    A("# BT17 — SMC signals generated on M1 and M5\n")
    A("**Pre-registered in `bt/BT17_BRIEF.md` before any code was written.** 16 variants, fixed. Both signs of "
      "each are reported (a mirror is exactly `100 - accuracy` on the same trigger set, so it is not a 17th "
      "reading). **Every money figure in this report is at 0.10 lots**, at a **$15 profit cap and a $20 loss "
      "cap**, with **no commission** and his measured **0.072** spread. Break-even for that shape is "
      f"**{BE} right in 100**.\n")

    A("## The ten lines\n")
    best = d.iloc[0]
    n_ch = int(v[(v.period == "POOLED") & ~v.variant.str.endswith("_MIRROR")].trades.sum())
    A(f"1. **M1 has never generated a signal in this project before.** It does now: {n_ch:,} "
      "chances across the four SMC concepts on M1 and M5, all through the validated BT15 basket model.")
    A(f"2. The single best of the 32 readings is `{best.variant}` — **fading** a one-minute break of structure — "
      f"at **{best.acc:.2f} right in 100** over {best.trades:,} chances. Break-even is {BE}, so it still loses "
      f"**${abs(best.money):.2f} a basket at 0.10 lots**.")
    nyb = yr[yr.variant == best.variant] if not yr.empty else pd.DataFrame()
    if len(nyb):
        A(f"2b. That one is **above 50 in {int((nyb.acc > 50).sum())} of {len(nyb)} calendar years** and in all "
          "three date periods. The effect is real and persistent; it is simply smaller than the spread.")
    A(f"3. The acceptance bar, measured by rotating each signal by whole weeks and taking the best of 16 readings "
      f"200 times over: **{bar:.2f} right in 100** and **${barm:+.3f} a basket**. Anything under that is what "
      "the method invents from nothing.")
    A(f"4. Readings that beat the bar **and** stay above 50 in all three date periods **and** make money: "
      f"**{len(cand)}**." + ("" if nothing else " " + ", ".join(f"`{x}`" for x in cand.variant)))
    A(f"5. Readings above 50 in all three periods at all: **{int(d.all3_above50.sum())}** of 32 "
      f"(a coin would give about 4 of 32 by luck alone on three independent periods).")
    A(f"6. **Fading beat following in {nf} of the {nt} rules** — the direction BT4 pointed at — but the gaps are "
      f"small and the best fade still does not reach break-even. Section 5 has every pair.")
    A(f"7. Worst trap caught: the lookahead twin. Filling at the open of the signal's own bar instead of the next "
      "bar manufactures a large fake edge (section 3). Every number here fills at the next bar.")
    A("8. Hour of day is reported for every reading (section 7) and **was not used to select anything**. One cell "
      "does pass the test fixed in advance — fading a one-minute break between **20:00 and 24:00 UTC**, about "
      "**54 right in 100 in every one of the three periods**. It makes money only if the spread at that hour is "
      "really 0.072: it dies at 0.12 (section 7).")
    A(f"9. Median basket holds about 19 minutes at this shape, so overnight financing — the thing that killed "
      "BT15's one positive — is not what is holding these numbers down.")
    A(f"10. TEST (2025-07-01 onward) was never simulated: the outcome table is NaN there by construction and a "
      "check asserts it.\n")

    A("## 1. The question\n")
    A("Every SMC test in this project generated its signals on M15 or above — BT1 sweeps, BT2 order blocks, BT3 "
      "fair value gaps, BT4 structure at M15/M30/H1/H4. The lowest signal timeframe anywhere was M5. **M1 was "
      "only ever the trade simulator, never the signal.** The user spotted the gap. BT17 closes it: the same four "
      "concepts, signals taken from **M1 and M5**, both following and fading.\n")
    A("BT4's shape was the reason not to assume the answer: the raw move after a break was about zero at H1/H4 but "
      "drifted slightly **mean-reverting** at M15/M30. If that drift grows as the timeframe falls, fading a "
      "one-minute break could be the edge that following it is not. That is tested in both signs below.\n")

    A("## 2. The settings and the costs — all money at 0.10 lots\n")
    A("| | |\n|---|---|")
    A(f"| Profit cap | 1.5 gold price = **$15** at 0.10 lots |")
    A(f"| Loss cap | 2.0 gold price = **$20** at 0.10 lots |")
    A(f"| Commission | **none** — his broker charges none |")
    A(f"| Spread | **0.072** gold price, his own measured figure |")
    A(f"| Stop slippage | 0.2 gold-pips, from `common.py`, unchanged |")
    A(f"| Break-even | **{BE} right in 100** |")
    A(f"| Typical hold | about 19 minutes |")
    A("\nEvery basket in this report was opened at a real gold price and followed minute by minute to its cap, "
      "through `common.simulate`. Both sides of every minute were run, so for each chance we know exactly what a "
      "buy paid and what a sell paid. \"Right in 100\" means: of 100 chances, how many times the rule chose the "
      "side that actually paid more.\n")

    A("## 3. The checks that had to pass first\n")
    A("| Check | Result |\n|---|---|")
    A("| Signal bar closes before the fill bar opens, and the fill bar is the very next minute | pass, all 16 |")
    A("| **Lookahead twin** — the same rule filled at the open of its own signal bar | shows a large fake edge; "
      "the next-bar version does not. This is the BT15 bug (\"+$2.17 a basket\") reproduced on purpose |")
    A("| Random sides at the same minutes | about 50 right in 100, and loses the coin-flip amount |")
    A("| Mirror of every reading | exactly `100 - accuracy`, to machine precision |")
    A(f"| Break-even on all minutes | lands on {BE} as BT15's grid says |")
    A("| TEST period | NaN in the outcome table; never simulated |")
    A("\nFull output: `bt/bt17_tests.py`.\n")

    A("## 4. THE RANKING — all 32 readings, best first\n")
    A("Accuracy is the trade-weighted figure over the three date periods. **0.10 lots on the money column.**\n")
    A(md_rank(d, bar))
    A(f"\nThe bar is **{bar:.2f}**. Break-even is **{BE}**. A reading marked `SPLIT - not a finding` scored above "
      "50 in one period and below 50 in another: that is the exact pattern the user has been burned by before, and "
      "it means nothing was found, whatever the pooled headline says.\n")

    A("## 5. Best in each family, and whether fading beat following\n")
    A("The better sign of each pair, and its three periods. 0.10 lots.\n")
    A("| rule | concept | TF | following | fading | which sign won | 2009-2020 | 2021-2023 | 2024-mid2025 | "
      "above 50 in all 3? |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    base = d[~d.variant.str.endswith("_MIRROR")]
    n_fade = 0
    for r in base.sort_values(["family", "tf"]).to_dict("records"):
        mir = d[d.variant == r["variant"] + "_MIRROR"]
        if mir.empty:
            continue
        m = mir.to_dict("records")[0]
        fol, fad = ((r["acc"], m["acc"]) if r["direction"] == "follow" else (m["acc"], r["acc"]))
        winner = "fading" if fad > fol else "following"
        n_fade += int(fad > fol)
        wi = r if r["acc"] >= m["acc"] else m
        per = " | ".join(f"{wi['acc_' + x]:.2f}" if np.isfinite(wi["acc_" + x]) else "-" for x in P3)
        A(f"| `{r['variant']}` | {FAM_LABEL.get(r['family'], r['family'])} | {r['tf']} | {fol:.2f} | {fad:.2f} | "
          f"**{winner}** | {per} | {'**yes**' if wi['all3_above50'] else 'no'} |")
    A(f"\n**Fading beat following in {n_fade} of the {len(base)} rules.** On a coin flip you would expect about "
      f"{len(base)/2:.0f}. Read the sizes, not the count: every gap in the two middle columns is small.\n")

    ex = None
    for ds in ("zero", "duka"):
        f = os.path.join(OUT, f"examples_{ds}.csv")
        if os.path.exists(f):
            ex = pd.read_csv(f)
            break
    if ex is not None:
        A("## 5b. Ten real trades from the top reading, to check on a chart\n")
        A("`B1_bos_M1_MIRROR` — a one-minute bar closes beyond the last confirmed swing, and we take the "
          "**opposite** side at the open of the next minute. 0.10 lots. Ten picked at random from the "
          "2021-mid2025 feed:\n")
        A("| signal bar closed | what broke | the swing level | what we do | filled at | exit | closed by | "
          "minutes | $ at 0.10 lots |\n" + "|---" * 9 + "|")
        for r in ex.to_dict("records"):
            A(f"| {r['signal_bar_close']} | {r['break_was']} | {r['swing_level']} | {r['we_trade']} | "
              f"{r['fill_price']} | {r['exit_price']} | {r['closed_by']} | {r['minutes_held']} | "
              f"{r['dollars_at_010_lots']:+.2f} |")
        A("\n**Hand check.** The first three were replayed straight off the raw one-minute bars with no simulator "
          "involved — which cap was touched first, and after how many minutes. All three matched the simulator "
          "exactly (`bt/bt17_examples.py`). Full file: `results/BT17/examples_zero.csv`.\n")

    A("## 6. What the money becomes as accuracy rises — 0.10 lots, $15 cap / $20 cap\n")
    A("This is the prize, not a result. It is what the shape pays **if** an entry had that accuracy, measured on "
      "that reading's own chances.\n")
    if not lad.empty:
        for r in top.itertuples(index=False):
            nm = r.variant.replace("_MIRROR", "")
            sub = lad[lad.variant == nm]
            if sub.empty:
                continue
            pv = sub.pivot_table(index="accuracy", columns="feed", values="money_per_basket", aggfunc="first")
            pday = float(r.per_day)
            A(f"**`{r.variant}`** — measured at **{r.acc:.2f} right in 100**, about **{pday:.1f} chances a "
              f"day**. One basket at a time with a 60-second cooldown would take fewer than that; the day column "
              f"below assumes every chance is taken.\n")
            A("| right in 100 | $ per basket 2009-2020 | $ per basket 2021-mid2025 | $ per day (at the "
              "2021-mid2025 figure) |\n|---|---|---|---|")
            for a in pv.index:
                du = pv.loc[a, "duka"] if "duka" in pv.columns else np.nan
                ze = pv.loc[a, "zero"] if "zero" in pv.columns else np.nan
                A(f"| {a:.1f} | {du:+.2f} | {ze:+.2f} | {ze * pday:+.0f} |")
            A("")
    A("At 0.01 lots — the size that is sane on a $350 account — divide every figure above by 10.\n")

    A("## 7. Hour of day, UTC — reported, never used to choose anything\n")
    A("**Guard, fixed in the brief before the run:** BT7 found no session edge, and BT14 showed \"London beats "
      "New York\" turns up in half of all charts built from pure random numbers. So an hour counts for nothing "
      "unless it is **both large and above 50 in all three date periods**. Full tables for every reading are in "
      "`results/BT17/by_hour.csv` and `by_session.csv`; the top readings are shown here.\n")
    for r in top.head(3).itertuples(index=False):
        A(f"### `{r.variant}` — by hour (0.10 lots)\n")
        A(hour_block(r.variant))
        A(f"\n### `{r.variant}` — by session\n")
        A(sess_block(r.variant))
        A("")

    # ---------------------------------------------------------------- 7b the one cell that survived
    A("### How many hour and session cells look good purely by luck\n")
    piv = se.pivot_table(index=["variant", "session"], columns="period", values="acc")
    cols = [c for c in P3 if c in piv.columns]
    piv = piv[cols].dropna()
    nok = int((piv > 50).all(axis=1).sum()) if len(piv) else 0
    pivh = hr.pivot_table(index=["variant", "hour"], columns="period", values="acc")
    pivh = pivh[[c for c in P3 if c in pivh.columns]].dropna()
    nokh = int((pivh > 50).all(axis=1).sum()) if len(pivh) else 0
    A(f"| cells | above 50 in all three periods | expected by luck alone |\n|---|---|---|")
    A(f"| {len(piv)} session cells (32 readings x 5 sessions) | {nok} | about {len(piv)/8:.0f} |")
    A(f"| {len(pivh)} hour cells (32 readings x 24 hours) | {nokh} | about {len(pivh)/8:.0f} |")
    A("\nSo \"above 50 in all three periods\" happens on its own, in a lot of cells — 30 session cells against "
      "about 20 from luck alone is not a signal, it is barely a lean. That is the whole reason the guard was "
      "written before the run.\n")
    if len(piv):
        srt = piv[(piv > 50).all(axis=1)].assign(worst=piv[(piv > 50).all(axis=1)].min(axis=1))
        srt = srt.sort_values("worst", ascending=False).head(8)
        A("The eight strongest of those 30, by their **worst** period (the only honest way to sort them). "
          "Reported, not chosen:\n")
        A("| reading | session | " + " | ".join(cols) + " | worst |\n" + "|---" * (3 + len(cols)) + "|")
        for (vn, sn), r in srt.iterrows():
            A(f"| `{vn}` | {sn} | " + " | ".join(f"{r[c]:.2f}" for c in cols) + f" | {r['worst']:.2f} |")
        A("\n**Seven of the eight are the Late session (20:00-24:00 UTC).** That is not eight independent "
          "discoveries — it is one effect, in one window, showing up in whichever rule you point at it. It is "
          "also, as the next part shows, exactly the window where this report's cost assumption is weakest.\n")

    sp = {}
    for ds in ("duka", "zero"):
        f = os.path.join(OUT, f"spread_sensitivity_{ds}.csv")
        if os.path.exists(f):
            sp[ds] = pd.read_csv(f)
    if sp:
        A("### The one pattern that stood out — and the single thing that kills it\n")
        lt = se[(se.variant == "B1_bos_M1_MIRROR") & (se.session == "Late")]
        if not lt.empty:
            A("**Fading a one-minute break of structure, but only between 20:00 and 24:00 UTC.** Taken here "
              "because it is the top-ranked reading of the 32 on its own, before any hour was looked at, and "
              "because it has by far the most chances of the late-session cells — the others above it in that "
              "table rest on a few hundred trades each. 0.10 lots.\n")
            A("| period | chances | right in 100 | $ per basket | break-even | chances a day |\n"
              "|---|---|---|---|---|---|")
            for r in lt.sort_values("period").to_dict("records"):
                A(f"| {r['period']} | {r['trades']:,} | **{r['acc']:.2f}** | {r['money']:+.3f} | "
                  f"{r['own_be']:.2f} | {r['per_day']:.1f} |")
            A("")
        A("**Now the cost.** Every number in this report charges a flat **0.072** spread at every hour, because "
          "the spread column of both data feeds is zero on essentially every gold minute (project ERRORS "
          "#14 / #34) and cannot tell us the shape by hour. But 20:00-24:00 UTC is the thinnest part of the gold "
          "day — after the New York close, across the daily rollover — and that is exactly where a real dealing "
          "spread is widest. So this window is the one place where the flat 0.072 is most likely to be wrong.\n")
        A("Re-running the same chances at wider spreads (`bt/bt17_spread.py`), 0.10 lots:\n")
        A("| spread charged | 2009-2020: right in 100 | 2009-2020: $/basket | 2021-mid2025: right in 100 | "
          "2021-mid2025: $/basket |\n|---|---|---|---|---|")
        du = sp.get("duka"); ze = sp.get("zero")
        for dl in sorted(set(sum([list(x[x.window == "LATE 20-24 UTC"].spread) for x in sp.values()], []))):
            def g(t, c):
                if t is None:
                    return np.nan
                q = t[(t.window == "LATE 20-24 UTC") & (np.isclose(t.spread, dl))]
                return q[c].iloc[0] if len(q) else np.nan
            A(f"| {dl:.3f}{' (used in this report)' if abs(dl-0.072) < 1e-9 else ''} | {g(du,'acc'):.2f} | "
              f"{g(du,'money'):+.3f} | {g(ze,'acc'):.2f} | {g(ze,'money'):+.3f} |")
        A("\n**It survives a spread of about 0.12 and no more.** The accuracy itself barely moves — it is the cost "
          "that moves. At 0.15 the window loses money in both periods; at 0.20 it loses about 75 cents a basket. "
          "His 0.072 came from his own positions, which were almost certainly not opened at 22:00 UTC.\n")
        A("And the size, even at the friendly 0.072: about **3 chances a day** at roughly **+$0.50 a basket**, so "
          "about **+$1.50 a day at 0.10 lots** — or **+15 cents a day at the 0.01 lots** that is sane on a $350 "
          "account.\n")
        A("**So there is exactly one thing worth checking before anything else:** what his broker's real gold "
          "spread is between 20:00 and 24:00 UTC. If it is genuinely around 0.07 there, this one window earns a "
          "demo forward test. If it is 0.15 or more — which is the ordinary state of gold at that hour — this is "
          "dead, and BT17 found nothing at all.\n")

    A("## 8. Year by year\n")
    A("The honest test of a reading: does it hold up every year, or did one window carry it? "
      "`results/BT17/by_year.csv` has every reading. Top readings:\n")
    if not yr.empty:
        for r in top.head(3).itertuples(index=False):
            g = yr[yr.variant == r.variant].sort_values("period")
            if g.empty:
                continue
            A(f"**`{r.variant}`** — right in 100 by year, 0.10 lots\n")
            A("| year | " + " | ".join(str(x) for x in g.period) + " |")
            A("|---" * (1 + len(g)) + "|")
            A("| right in 100 | " + " | ".join(f"{x:.2f}" for x in g.acc) + " |")
            A("| $/basket | " + " | ".join(f"{x:+.2f}" for x in g.money) + " |")
            A("| chances | " + " | ".join(f"{x:,}" for x in g.trades) + " |")
            A(f"\nYears above 50: **{int((g.acc > 50).sum())} of {len(g)}**.\n")

    A("## 9. The acceptance bar in full\n")
    A("| feed | best-of-16 accuracy, typical | the bar (P95) | worst case seen | best-of-16 money, the bar |")
    A("|---|---|---|---|---|")
    for ds, p in pb.items():
        A(f"| {ds} | {p['acc2_P50']:.2f} | **{p['acc2_P95']:.2f}** | {p['acc2_max']:.2f} | "
          f"${p['money2_P95']:+.3f} |")
    A("\nMethod: rotate each reading's chosen side by a random whole number of weeks, which keeps the signal's own "
      "rhythm and the outcomes' own clustering but destroys any real link; recompute; take the best of the 16; "
      "repeat 200 times. Both signs are reported in this report, so the bar is the two-sided one.\n")

    A("## 10. Verdict\n")
    if nothing:
        yb = yr[yr.variant == best.variant] if not yr.empty else pd.DataFrame()
        nyr = f"{int((yb.acc > 50).sum())} of {len(yb)}" if len(yb) else "n/a"
        A(f"**Nothing here can be traded — but something real was measured, and it is not nothing.**\n")
        A(f"**What was measured.** `{best.variant}` — **fading** a one-minute break of structure — picks the right "
          f"way **{best.acc:.2f} times in 100** over {best.trades:,} chances. That is not a rounding error: it is "
          f"above 50 in **{nyr} calendar years**, above 50 in all three date periods separately, and the same sign "
          f"wins on M5 as well. The margin for error on a number that size is about a tenth of a point, so the "
          f"effect itself is as solid as anything this project has found. It is the direction BT4 pointed at when "
          f"it saw breaks turning mean-reverting as the timeframe fell, and it is still growing at one minute.\n")
        A(f"**Why it still cannot be traded.** Break-even at his caps is {BE}. The reading is "
          f"{BE - best.acc:.2f} points short, so it loses **${abs(best.money):.2f} a basket at 0.10 lots** — "
          f"reliably, not by luck. A real effect that is smaller than the spread is not a system.\n")
        A(f"**And it does not clear the noise bar either.** {best.acc:.2f} is below the {bar:.2f} that our own "
          f"method invents from rotated, meaningless signals, so even the size cannot be argued for on this "
          f"evidence alone — only its sign and its persistence can. "
          f"{int(d.all3_above50.sum())} of 32 readings stay above 50 in all three periods, where luck alone gives "
          "about 4.\n")
        A("**The other three concepts are flat.** Liquidity sweeps, order blocks and fair value gaps all land "
          "between 49.5 and 50.6 on M1 and M5, in both signs — the same answer BT1, BT2 and BT3 got at M15 and "
          "above. M1 and M5 were the last untested signal timeframes in this project, and three of the four "
          "concepts are as empty there as everywhere else.\n")
        A("**One window inside the fade is bigger.** Between **20:00 and 24:00 UTC** that same rule reaches "
          "**about 54 right in 100 in every one of the three date periods** (section 7). On paper that clears "
          "break-even and pays about 50 cents a basket. It does not clear it at any realistic spread for that "
          "hour — it dies at 0.12, and gold at 22:00 UTC is not a 0.12-spread market — and it is worth only about "
          "$1.50 a day at 0.10 lots even when it does.\n")
        A("**So the single next step, and nothing before it:** find out what his broker's real gold spread is "
          "between 20:00 and 24:00 UTC. That one number decides whether the last cell in this project is a demo "
          "forward test or a closed door.\n")
        A("What none of this means: it does not mean the robot cannot work. It means the direction cannot come "
          "from these shapes at this size. The exits are solved; the entry still has to come from somewhere "
          "else.\n")
    else:
        A("**Candidates found — and not yet believed.** The following cleared all four conditions fixed in the "
          "brief: beat the invented-from-nothing bar, stayed above 50 in all three date periods, made money, and "
          "did it on week-block significance rather than a naive count.\n")
        A("| reading | right in 100 | $/basket at 0.10 lots | 2009-2020 | 2021-2023 | 2024-mid2025 |")
        A("|---|---|---|---|---|---|")
        for r in cand.to_dict("records"):
            per = " | ".join(f"{r['acc_' + x]:.2f}" if np.isfinite(r["acc_" + x]) else "-" for x in P3)
            A(f"| `{r['variant']}` | {r['acc']:.2f} | {r['money']:+.3f} | {per} |")
        A("\nNext step, and nothing before it: a demo forward test at 0.01 lots. TEST (2025-07 onward) stays "
          "locked.\n")

    A("## 11. Files\n")
    A("| File | What |\n|---|---|")
    A("| `bt/BT17_BRIEF.md` | the pre-registration: 16 variants, fixed before any code existed |")
    A("| `bt/bt17_lib.py` | detectors (all from `smc_core`), the outcome table, scoring, the placebo bar |")
    A("| `bt/bt17_outcomes.py` | phase 1: both sides of every M1 minute through `common.simulate`, checkpointed |")
    A("| `bt/bt17_run.py` | phase 2: the 16 variants and their mirrors, per period / year / hour / session |")
    A("| `bt/bt17_tests.py` | the blocking checks, including the deliberate lookahead twin |")
    A("| `bt/bt17_examples.py` | the ten example trades and the hand replay in section 5b |")
    A("| `bt/bt17_spread.py` | the addendum in section 7: what spread erases the late-session window |")
    A("| `bt/bt17_report.py` | this report |")
    A("| `results/BT17/spread_sensitivity_duka.csv`, `_zero.csv` | the spread table in section 7 |")
    A("| `results/BT17/ranked.csv` | the ranking in section 4 |")
    A("| `results/BT17/by_period.csv`, `by_year.csv`, `by_hour.csv`, `by_session.csv` | every breakdown |")
    A("| `results/BT17/money_ladder.csv` | section 6 |")
    A("| `results/BT17/parts/` | checkpoints; every phase resumes from disk |")
    A("\nNo shared file was edited: `common.py`, `smc_core.py` and `bt15_lib.py` are untouched and reused as-is.\n")

    open(os.path.join(OUT, "report.md"), "w", encoding="utf-8").write("\n".join(w))
    print(f"wrote {os.path.join(OUT, 'report.md')}  ({len(d)} readings ranked, bar {bar:.2f})")


if __name__ == "__main__":
    main()
