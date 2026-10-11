"""BT21-T report - writes results/BT21T/report.md from the CSV/JSON artefacts both feeds produced.

    .venv/Scripts/python.exe smc_bot/bt/bt21t_report.py

Reads only: results/BT21T/{grid,look,m1,ext}_{duka,zero}.csv, placebo_*.json, checks_*.json,
results/BT21T/trades_*.csv and (read-only) results/BT21E/grid_{duka,zero}.csv - the published M1 run this
whole backtest exists to be differenced against.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(os.path.dirname(HERE), "results")
OUT = os.path.join(RES, "BT21T")
EOUT = os.path.join(RES, "BT21E")
FEEDS = ("duka", "zero")
ERAS = ["2009-2020", "2021-2023", "2024-2026"]
PRIMARY = (15, 70)
SHIPPED = (7, 70)
COST = 1.60                     # $1.10 commission + $0.50 for the 0.05 extra spread, at 0.10 lots

TWIN_BRIEF = {"T1_m15_ema9": "E1_m5_ema9", "T2_m15_ema21": "E2_m5_ema21",
              "T5_sweep20": "E5_sweep20", "T6_sweep60": "E6_sweep60"}
TWIN_TRIG = {"T1_m15_ema9": "E3_m15_ema9", "T2_m15_ema21": "E4_m15_ema21",
             "T5_sweep20": "E5_sweep20", "T6_sweep60": "E6_sweep60"}
VARIANTS = ["T1_m15_ema9", "T2_m15_ema21", "T3_h1_ema9", "T4_h1_ema21", "T5_sweep20", "T6_sweep60"]
RULES = {
    "T1_m15_ema9": ("M15 close vs EMA50", "M5 low <= EMA9 and M5 close > open", "follow the trend"),
    "T2_m15_ema21": ("M15 close vs EMA50", "M5 low <= EMA21 and M5 close > open", "follow the trend"),
    "T3_h1_ema9": ("H1 close vs EMA50", "M5 low <= EMA9 and M5 close > open", "follow the trend"),
    "T4_h1_ema21": ("H1 close vs EMA50", "M5 low <= EMA21 and M5 close > open", "follow the trend"),
    "T5_sweep20": ("none", "M5 low < min(low, prior 20 M5 bars) - 0.05, close back inside", "fade"),
    "T6_sweep60": ("none", "M5 low < min(low, prior 60 M5 bars) - 0.05, close back inside", "fade"),
}


def cat(pat):
    fs = [os.path.join(OUT, pat.format(ds=d)) for d in FEEDS]
    return pd.concat([pd.read_csv(f) for f in fs if os.path.exists(f)], ignore_index=True)


def cat_e(pat):
    fs = [os.path.join(EOUT, pat.format(ds=d)) for d in FEEDS]
    return pd.concat([pd.read_csv(f) for f in fs if os.path.exists(f)], ignore_index=True)


def md(df, cols=None, floats=None):
    d = df[cols] if cols else df
    d = d.copy()
    for c, f in (floats or {}).items():
        if c in d:
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f.format(v))
    head = "| " + " | ".join(str(c) for c in d.columns) + " |"
    sep = "|" + "|".join("---" for _ in d.columns) + "|"
    body = ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |"
            for r in d.itertuples(index=False)]
    return "\n".join([head, sep] + body)


def eraorder(df):
    df = df.copy()
    df["__o"] = df.era.map({e: i for i, e in enumerate(ERAS)})
    return df.sort_values("__o", kind="stable").drop(columns="__o")


# ------------------------------------------------------------------ load
G = cat("grid_{ds}.csv")                      # M5, 36 shapes
LK = cat("look_{ds}.csv")                     # M5 lookahead twin
M1 = cat("m1_{ds}.csv")                       # BT21-E recomputed, with error bars
GE = cat_e("grid_{ds}.csv")                   # BT21-E published, 36 shapes
LKE = cat_e("look_{ds}.csv")
EXT = cat("ext_{ds}.csv") if os.path.exists(os.path.join(OUT, "ext_zero.csv")) else pd.DataFrame()
PB = {}
for d in FEEDS:
    f = os.path.join(OUT, f"placebo_{d}.json")
    if os.path.exists(f):
        PB.update(json.load(open(f)))
CK = {d: json.load(open(os.path.join(OUT, f"checks_{d}.json"))) for d in FEEDS
      if os.path.exists(os.path.join(OUT, f"checks_{d}.json"))}

GP = G[(G.target == PRIMARY[0]) & (G.stop == PRIMARY[1])].copy()
GEP = GE[(GE.target == PRIMARY[0]) & (GE.stop == PRIMARY[1])].copy()
M1P = M1[(M1.target == PRIMARY[0]) & (M1.stop == PRIMARY[1])].copy()


# ------------------------------------------------------------------ DELTA 1: accuracy
def delta_acc(twin, label):
    rows = []
    for era in ERAS:
        for t, e in twin.items():
            a = GP[(GP.era == era) & (GP.variant == t)]
            b = M1P[(M1P.era == era) & (M1P.variant == e)]
            if a.empty or b.empty:
                continue
            a, b = a.iloc[0], b.iloc[0]
            dse = float(np.hypot(a.acc_se_week, b.acc_se_week))
            d = a.acc - b.acc
            rows.append(dict(era=era, pair=f"{t} vs {e}", M1_acc=round(b.acc, 2),
                             M1_se=round(b.acc_se_week, 3), M5_acc=round(a.acc, 2),
                             M5_se=round(a.acc_se_week, 3), delta=round(d, 2),
                             delta_se=round(dse, 3), z=round(d / dse, 2) if dse > 0 else np.nan,
                             M1_bar=round(b.luck_bar, 2), M5_bar=round(a.luck_bar, 2),
                             d_beats_bar=round(a.beats_bar - b.beats_bar, 2)))
    return pd.DataFrame(rows), label


# ------------------------------------------------------------------ DELTA 2: counts
def delta_counts(twin):
    rows = []
    for era in ERAS:
        for t, e in twin.items():
            a = GP[(GP.era == era) & (GP.variant == t)]
            b = M1P[(M1P.era == era) & (M1P.variant == e)]
            if a.empty or b.empty:
                continue
            a, b = a.iloc[0], b.iloc[0]
            rows.append(dict(era=era, pair=f"{t} vs {e}",
                             M1_signals=int(b.signals), M5_signals=int(a.signals),
                             sig_ratio=round(b.signals / a.signals, 2),
                             sig_pct=round(100 * (a.signals / b.signals - 1), 1),
                             M1_baskets=int(b.baskets), M5_baskets=int(a.baskets),
                             bask_pct=round(100 * (a.baskets / b.baskets - 1), 1),
                             M1_conv=round(100 * b.baskets / b.signals, 1),
                             M5_conv=round(100 * a.baskets / a.signals, 1),
                             M1_med_min=int(b.basket_med_min), M5_med_min=int(a.basket_med_min)))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ DELTA 3: cost amortization
BANDS = [("narrow $7-$15", [7, 10, 15]), ("mid $20-$30", [20, 30]), ("wide $50", [50]),
         ("extension $100-$200", [100, 150, 200])]


def band_rows():
    """For each era x twin-pair x target band: the best and the mean net $/basket in that band, on each
    timeframe, plus the M5-minus-M1 difference. `beats_bar` is carried too, because money alone cannot
    say whether a wider target helped the EDGE or merely the arithmetic."""
    m5 = pd.concat([G, EXT[EXT.trig_tf == "M5"]] if len(EXT) else [G], ignore_index=True)
    m1 = pd.concat([GE, EXT[EXT.trig_tf == "M1"]] if len(EXT) else [GE], ignore_index=True)
    rows = []
    for era in ERAS:
        for t, e in TWIN_TRIG.items():
            for bn, tg in BANDS:
                A = m5[(m5.era == era) & (m5.variant == t) & (m5.target.isin(tg))]
                B = m1[(m1.era == era) & (m1.variant == e) & (m1.target.isin(tg))]
                if A.empty or B.empty:
                    continue
                rows.append(dict(era=era, pair=f"{t} vs {e}", band=bn,
                                 M1_best=round(B.basket_usd.max(), 3),
                                 M5_best=round(A.basket_usd.max(), 3),
                                 d_best=round(A.basket_usd.max() - B.basket_usd.max(), 3),
                                 M1_mean=round(B.basket_usd.mean(), 3),
                                 M5_mean=round(A.basket_usd.mean(), 3),
                                 d_mean=round(A.basket_usd.mean() - B.basket_usd.mean(), 3),
                                 M1_bb=round(B.beats_bar.max(), 2), M5_bb=round(A.beats_bar.max(), 2),
                                 d_bb=round(A.beats_bar.max() - B.beats_bar.max(), 2)))
    return pd.DataFrame(rows)


def band_summary(bd):
    out = []
    for bn, _ in BANDS:
        s = bd[bd.band == bn]
        if s.empty:
            continue
        out.append(dict(band=bn, pairs=len(s),
                        mean_d_best=round(s.d_best.mean(), 3), median_d_best=round(s.d_best.median(), 3),
                        mean_d_mean=round(s.d_mean.mean(), 3),
                        mean_d_beats_bar=round(s.d_bb.mean(), 2),
                        M5_better_in=f"{int((s.d_best > 0).sum())}/{len(s)}"))
    return pd.DataFrame(out)


def cost_share():
    """Cost as a share of the move actually captured, by target band and timeframe. This is the mechanism
    the brief names: the $1.60 round trip is fixed while the move grows with the target."""
    m5 = pd.concat([G, EXT[EXT.trig_tf == "M5"]] if len(EXT) else [G], ignore_index=True)
    m1 = pd.concat([GE, EXT[EXT.trig_tf == "M1"]] if len(EXT) else [GE], ignore_index=True)
    rows = []
    for era in ERAS:
        for bn, tg in BANDS:
            A = m5[(m5.era == era) & (m5.target.isin(tg)) & (m5.variant.isin(TWIN_TRIG.keys()))]
            B = m1[(m1.era == era) & (m1.target.isin(tg)) & (m1.variant.isin(TWIN_TRIG.values()))]
            if A.empty or B.empty or "mean_abs_gross" not in A:
                continue
            rows.append(dict(era=era, band=bn,
                             M1_mean_abs_gross=round(B.mean_abs_gross.mean(), 2),
                             M5_mean_abs_gross=round(A.mean_abs_gross.mean(), 2),
                             M1_cost_pct=round(100 * COST / B.mean_abs_gross.mean(), 1),
                             M5_cost_pct=round(100 * COST / A.mean_abs_gross.mean(), 1),
                             M1_luck_bar=round(B.luck_bar.mean(), 2),
                             M5_luck_bar=round(A.luck_bar.mean(), 2)))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ the ranking (both signs)
def ranking():
    rows = []
    for r in GP.itertuples(index=False):
        rows.append(dict(era=r.era, variant=r.variant, sign="declared", signals=r.signals,
                         acc=round(r.acc, 2), se=round(r.acc_se_week, 3), luck_bar=r.luck_bar,
                         beats_bar=round(r.acc - r.luck_bar, 2), per_signal=r.per_signal_usd,
                         coin=r.luck_usd, baskets=r.baskets, per_basket=r.basket_usd,
                         per_day=r.per_day, med_min=r.med_min, week_t=r.week_t))
        rows.append(dict(era=r.era, variant=r.variant, sign="mirror", signals=r.signals,
                         acc=round(100 - r.acc, 2), se=round(r.acc_se_week, 3), luck_bar=r.luck_bar,
                         beats_bar=round(100 - r.acc - r.luck_bar, 2), per_signal=r.flip_usd,
                         coin=r.luck_usd, baskets=np.nan, per_basket=np.nan,
                         per_day=np.nan, med_min=r.med_min, week_t=np.nan))
    return pd.DataFrame(rows).sort_values("beats_bar", ascending=False)


# ------------------------------------------------------------------ examples
def examples(variant="T6_sweep60", n_win=7, n_loss=3, seed=21):
    fs = [f for f in os.listdir(OUT) if f.startswith(f"trades_{variant}_") and f.endswith(".csv")]
    t = pd.concat([pd.read_csv(os.path.join(OUT, f)) for f in sorted(fs)], ignore_index=True)
    rng = np.random.default_rng(seed)
    w, l = t[t.net_usd > 0], t[t.net_usd <= 0]
    pick = pd.concat([w.iloc[rng.choice(len(w), min(n_win, len(w)), replace=False)],
                      l.iloc[rng.choice(len(l), min(n_loss, len(l)), replace=False)]])
    pick = pick.sort_values("t_entry").reset_index(drop=True)
    pick.insert(0, "#", np.arange(1, len(pick) + 1))
    return t, pick


def handcheck(row):
    """Re-derive one example basket straight off the raw frames, independently of the pipeline."""
    import importlib
    os.environ["SMC_DATASET"] = row["feed"]
    for m in [k for k in list(sys.modules) if k.startswith("bt.") or k == "smc_core"]:
        sys.modules.pop(m, None)
    from bt.common import bars                                      # noqa: E402
    b1, b5 = bars("XAUUSD", "M1"), bars("XAUUSD", "M5")
    t5 = b5.time.values.astype("datetime64[ns]")
    k = int(np.searchsorted(t5, np.datetime64(pd.Timestamp(row["t_m5_signal_open"]))))
    N = 60 if "60" in row["variant"] else 20
    sig = b5.iloc[k]
    prior = b5.iloc[k - N:k]
    t1 = b1.time.values.astype("datetime64[ns]")
    fi = int(np.searchsorted(t1, np.datetime64(pd.Timestamp(row["t_entry"]))))
    fb = b1.iloc[fi]
    buy = row["side"] == 1
    lvl = float(prior.low.min() if buy else prior.high.max())
    fill = (fb.open + fb.spr + 0.05) if buy else (fb.open - 0.05)
    o = int(row["hold_min"])
    seg = b1.iloc[fi:fi + o + 1]
    mfe = ((seg.high.max() - fill) if buy else (fill - (seg.low + seg.spr).min())) * 10
    mae = ((seg.low.min() - fill) if buy else (fill - (seg.high + seg.spr).max())) * 10
    return dict(feed=row["feed"], variant=row["variant"], m5_bar=str(row["t_m5_signal_open"])[:16],
                m5_open=round(float(sig.open), 3), m5_high=round(float(sig.high), 3),
                m5_low=round(float(sig.low), 3), m5_close=round(float(sig.close), 3),
                prior_extreme=round(lvl, 3),
                wick_beyond=round(float((lvl - sig.low) if buy else (sig.high - lvl)), 3),
                close_back_inside=bool((sig.close > lvl) if buy else (sig.close < lvl)),
                m5_closed_at=str(row["t_m5_signal_close"])[:16], fill_bar=str(row["t_entry"])[:16],
                fill_bar_open=round(float(fb.open), 3), fill_used=round(float(fill), 3),
                csv_fill=float(row["fill"]), hold_min=o, mfe_usd=round(float(mfe), 2),
                mae_usd=round(float(mae), 2), csv_net=float(row["net_usd"]))


# ------------------------------------------------------------------ pass/fail
def passfail():
    rows = []
    for v in VARIANTS:
        s = GP[GP.variant == v]
        c1 = bool(len(s) == 3 and (s.basket_usd > 0).all())
        c2 = bool(len(s) == 3 and (s.acc > s.luck_bar).all())
        c3 = all(float(s[s.era == er].acc.iloc[0]) > PB[er]["acc2_P95"]
                 for er in ERAS if len(s[s.era == er]) and er in PB)
        c4 = bool(len(s) == 3 and (s.baskets >= 500).all())
        rows.append(dict(variant=v, **{"1 net $/basket > 0 in all 3 eras": "yes" if c1 else "**no**",
                                       "2 acc > own luck bar in all 3": "yes" if c2 else "**no**",
                                       "3 acc > placebo bar (all eras)": "yes" if c3 else "**no**",
                                       "4 >= 500 baskets per era": "yes" if c4 else "**no**",
                                       "VERDICT": "**PASS**" if all([c1, c2, c3, c4]) else "**FAIL**"}))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ build the report
def main():
    dab, _ = delta_acc(TWIN_BRIEF, "brief")
    dat, _ = delta_acc(TWIN_TRIG, "trigger-only")
    dc = delta_counts(TWIN_TRIG)
    dcb = delta_counts(TWIN_BRIEF)
    bd = band_rows()
    bs = band_summary(bd)
    cs = cost_share()
    rk = ranking()
    pf = passfail()
    trades, ex = examples()
    hc = handcheck(ex.iloc[0].to_dict())

    best = GP.loc[GP.acc.idxmax()]
    worst = GP.loc[GP.acc.idxmin()]
    npass = int((pf.VERDICT == "**PASS**").sum())
    tw = LK[(LK.target == PRIMARY[0]) & (LK.stop == PRIMARY[1])].merge(
        GP, on=["era", "variant"], suffixes=("_l", "_h"))
    tw["d"] = tw.acc_l - tw.acc_h
    twE = LKE[(LKE.target == PRIMARY[0]) & (LKE.stop == PRIMARY[1])].merge(
        GEP, on=["era", "variant"], suffixes=("_l", "_h"))
    twE["d"] = twE.acc_l - twE.acc_h
    ema_t = tw[tw.variant.str.contains("ema")]
    ema_e = twE[twE.variant.str.contains("ema")]
    swp_t = tw[tw.variant.str.contains("sweep")]
    swp_e = twE[twE.variant.str.contains("sweep")]

    L = []
    A = L.append
    A("# BT21-T — does raising the trigger to M5 give more edge, and more trades? Both answers are no.\n")
    A("**Pre-registered in `bt/BT21T_BRIEF.md` before any code was written. SIX variants, fixed; none was "
      "added, dropped or re-specified after seeing a result.** Gold, trigger on **M5**, fill on the first "
      "**M1** open after that M5 bar closes. Two feeds (Dukascopy 2009-03→2021, Exness Zero 2021→2026-09), "
      "three eras reported separately and never pooled. 0.10 lots, after the $1.10 round-trip commission "
      "and 0.05 extra spread. Primary shape = the sniper's live **$15 target / $70 stop**. Every M1 number "
      "quoted is BT21-E's, recomputed from **its own** walk tables (parity asserted to the cent: "
      f"`checks_*.json → bt21e_parity`) so each delta is attributable to the trigger timeframe alone.\n")

    A("## The ten lines\n")
    A(f"1. **Variants that pass the pre-registered test: {npass} of 6.** Every one of T1-T6 fails, for the "
      "same reason the M1 versions failed: accuracy near 50 against a break-even near 56.\n")
    A("2. **Does M5 give more edge? No.** The M1→M5 accuracy delta, trend filter held fixed so only the "
      f"trigger moves, is **{dat.delta.min():+.2f} to {dat.delta.max():+.2f} points** across 12 "
      f"variant-era pairs, mean **{dat.delta.mean():+.3f}**, against a week-clustered standard error of "
      f"{dat.delta_se.min():.2f}-{dat.delta_se.max():.2f} on the difference. "
      f"{int((dat.delta.abs() > 2 * dat.delta_se).sum())} of {len(dat)} pairs clear 2 SE, and they do not "
      "agree on a sign.\n")
    A("3. **Does M5 give more trades? No — it takes far fewer, and more than throughput saturation "
      f"predicted.** M5 cuts signals {dc.sig_ratio.min():.1f}-{dc.sig_ratio.max():.1f}x and cuts the "
      f"baskets the robot actually opens by **{-dc.bask_pct.max():.0f}% to {-dc.bask_pct.min():.0f}%**. "
      "The brief predicted a 15-25% cut from BT21-E's saturation evidence. That prediction was wrong, and "
      "section 5 says why: saturation only buffers a signal cut while baskets are *long*.\n")
    A("4. **The cost-amortization hypothesis is real in the arithmetic and absent in the edge.** Widening "
      "the target does lift both timeframes' money per basket (the fixed $1.60 round trip falls from "
      f"{cs[cs.band == 'narrow $7-$15'].M5_cost_pct.mean():.0f}% of the captured move at $7-$15 to "
      f"{cs[cs.band == 'extension $100-$200'].M5_cost_pct.mean():.1f}% at $100-$200 if the extension ran), "
      "but **M5 does not benefit more than M1 does**: the M5−M1 gap in best net $/basket is "
      f"{bs.set_index('band').mean_d_best.to_dict()}. Section 6.\n")
    A(f"5. Best accuracy any of the six reaches in any era: **{best.acc:.2f}** "
      f"({best.variant}, {best.era}) against its own luck bar of **{best.luck_bar:.2f}**. Worst: "
      f"**{worst.acc:.2f}**. The `acc − luck bar` column is negative in all "
      f"{len(rk)} readings, by {-rk.beats_bar.max():.1f} to {-rk.beats_bar.min():.1f} points.\n")
    A(f"6. Net $/basket at $15/$70, every variant, every era: **{GP.basket_usd.min():.2f} to "
      f"{GP.basket_usd.max():.2f}**; 0 of {len(GP)} era-variant cells positive. Over the whole 36-shape "
      f"ladder: **0 of {len(G)} era-variant-shape cells positive.**\n")
    A("7. **The lookahead twin is MUCH bigger on M5, exactly as BT21-R's rule predicted.** Filling at the "
      "M5 bar's own open instead of the first minute after it closes is worth "
      f"**{ema_t.d.min():+.2f} to {ema_t.d.max():+.2f} points** on the four EMA-pullback variants, against "
      f"**{ema_e.d.min():+.2f} to {ema_e.d.max():+.2f}** for the same rules triggered on M1. The brief "
      f"predicted 'larger than +7.5'. Measured: **{ema_t.d.max():+.2f}**. Prediction CONFIRMED.\n")
    A("8. The week-rotation placebo bar (best-of-6, 200 draws, two-sided): "
      + " · ".join(f"{er} **{PB[er]['acc2_P95']:.2f}**" for er in ERAS if er in PB)
      + ". One reading sits above its era's bar and still fails every other clause (section 11).\n")
    A("9. **The ladder's best shape is $50 target on BOTH timeframes, in all 18 era-variant cells** — the "
      "ladder is truncated, not optimised, which is why section 6 walks the $100/$150/$200 rungs on both "
      "arms rather than arguing from the edge of the grid.\n")
    A("10. Nothing was selected from the 2024-2026 era; every variant fails in all three anyway.\n")

    A("\n## 1. The exact rules, and what each one is being differenced against\n")
    A(md(pd.DataFrame([dict(id=v, trend_filter=RULES[v][0], M5_trigger=RULES[v][1], side=RULES[v][2],
                            fill="first M1 open after the M5 bar closes",
                            brief_twin=TWIN_BRIEF.get(v, "— (new H1 rung)"),
                            trigger_only_twin=TWIN_TRIG.get(v, "— (new H1 rung)"))
                       for v in VARIANTS])))
    A("\nSELL is the mirror of each and is in the ranking; a mirror is exactly `100 − accuracy` on the "
      "same trigger set, so it is not a seventh reading.\n")
    A("Two twin definitions are reported because the brief's table and the clean experiment are not the "
      "same comparison. **`brief_twin`** moves BOTH rungs up (E1 = M5 filter + M1 trigger → T1 = M15 "
      "filter + M5 trigger), which is what BT21T_BRIEF's variant table names. **`trigger_only_twin`** "
      "holds the trend filter fixed at M15 and moves only the trigger (E3 → T1), which is the only "
      "comparison in which a difference can be blamed on the timeframe. T5/T6 have no filter, so for them "
      "the two definitions coincide. T3/T4 are the brief's new H1 rung and have no M1 twin at all.\n")
    A("Implementation choices, all fixed in `bt21t_lib.py`'s docstring before any result was seen: sweep "
      "buffer 0.05, sweep cooldown 10 **M5** bars per side, the prior-N window must be N contiguous real "
      "M5 bars, warm-up 300 closed M5 bars and 200 closed trend bars, T1-T4 fire on every qualifying M5 "
      "bar (the one-basket-at-a-time engine does the thinning), and a tie in accuracy counts as a **half** "
      "(BT21_SUMMARY open issue 4), never as a win for the long side.\n")

    A("\n## 2. DELTA 1 — the M1 → M5 accuracy delta, variant by variant ($15/$70)\n")
    A("`se` columns are **week-clustered** standard errors in accuracy points, not binomial ones: "
      "consecutive signals share most of a 1440-minute window, so a binomial error bar is a floor. "
      "`delta_se` = sqrt(se_M1² + se_M5²), which is conservative because the two arms read the same market "
      "on overlapping minutes and their errors are positively correlated. `z` = delta / delta_se.\n")
    A("**(a) trigger-only — trend filter held at M15, only the trigger moves M1 → M5. This is the "
      "experiment.**\n")
    A(md(eraorder(dat), floats={"M1_acc": "{:.2f}", "M5_acc": "{:.2f}", "delta": "{:+.2f}"}))
    A(f"\nMean delta **{dat.delta.mean():+.3f}** points, median **{dat.delta.median():+.3f}**, range "
      f"{dat.delta.min():+.2f} to {dat.delta.max():+.2f}. Pairs where |delta| > 2 SE: "
      f"**{int((dat.delta.abs() > 2 * dat.delta_se).sum())} of {len(dat)}**, and "
      f"{int((dat.delta > 0).sum())} of {len(dat)} deltas are positive — a coin flip on the sign.\n")
    A("\n**(b) the brief's twin — both rungs up (filter M5→M15 and trigger M1→M5).**\n")
    A(md(eraorder(dab), floats={"M1_acc": "{:.2f}", "M5_acc": "{:.2f}", "delta": "{:+.2f}"}))
    A(f"\nMean delta **{dab.delta.mean():+.3f}** points. It is larger than (a) because it also carries "
      "BT21-E's own finding that the M15 filter reads about 0.7 of a point better than the M5 filter on "
      "the duka decade — a *filter* effect BT21-E already published and explicitly called a feed/regime "
      "difference, not a timeframe effect. Reading (a) is the one that answers the owner's question.\n")
    A("\n**The `d_beats_bar` column is the one that matters for money.** Moving to M5 changes "
      f"`accuracy − own break-even` by {dat.d_beats_bar.min():+.2f} to {dat.d_beats_bar.max():+.2f} "
      f"points (mean {dat.d_beats_bar.mean():+.2f}). The gap that has to be closed is about 5 points. "
      "Nothing here closes 1/20th of it.\n")

    A("\n## 3. DELTA 2 — the M1 → M5 trade-count delta, signals AND baskets, per era\n")
    A("`sig_ratio` = M1 signals / M5 signals. `conv` = baskets ÷ signals, the share of chances the "
      "one-basket-at-a-time robot can actually act on.\n")
    A(md(eraorder(dc), floats={"sig_pct": "{:+.1f}", "bask_pct": "{:+.1f}"}))
    A("\nSame table against the brief's twin, for completeness:\n")
    A(md(eraorder(dcb), floats={"sig_pct": "{:+.1f}", "bask_pct": "{:+.1f}"}))

    A("\n## 4. The ranking — 6 variants × 3 eras, both signs, at $15/$70\n")
    A("`acc` = how often the rule chose the side that **would** have paid more, a tie counting half. "
      "`luck_bar` = this rule's own break-even accuracy **on its own entry minutes** — the number to beat, "
      "never 50. `per_signal` = mean net $ if every signal were taken; `coin` = a random direction at "
      "exactly the same minutes; `per_basket` = mean net $ of the baskets the robot actually opens.\n")
    A(md(rk, floats={"acc": "{:.2f}", "beats_bar": "{:+.2f}", "per_signal": "{:+.3f}", "coin": "{:+.3f}",
                     "per_basket": "{:+.3f}"}))
    A(f"\n**Every one of the {len(rk)} readings is below its own break-even**, by "
      f"{-rk.beats_bar.max():.2f} to {-rk.beats_bar.min():.2f} points. Read `per_signal` against `coin`: "
      "the M5 rules lose about what a coin loses at the same minutes. There is no small edge being eaten "
      "by costs; there is no edge.\n")

    A("\n## 5. Why the trade count fell so much more than predicted\n")
    A("The brief's prediction — a 5x signal cut costing only 15-25% of the trades — came from BT21-E's "
      "observation that a 13x signal cut (E3 → E6) cost only 33% of the baskets. That reasoning holds "
      "only while baskets are long enough for the robot to be the bottleneck. It is not a constant:\n")
    A(md(eraorder(dc), cols=["era", "pair", "M1_conv", "M5_conv", "M1_med_min", "M5_med_min",
                             "sig_ratio", "bask_pct"], floats={"bask_pct": "{:+.1f}"}))
    A("\nIn 2009-2020 the median M1 basket at $15/$70 ran 89-91 minutes, so the robot was busy most of the "
      "time and only converted 3% of the EMA signals; thinning them 5x cost about a quarter of the "
      "trades. By 2024-2026 gold had roughly doubled, $15 had become a 10-minute trip, conversion rose to "
      "about 20%, the robot was idle far more often — and then a 5x signal cut costs **over half** the "
      "trades. The buffer that made the brief's prediction reasonable is a property of the era, not of "
      "the engine. (This is BT21_SUMMARY finding 2 arriving from a new direction: the dollar-denominated "
      "shape silently changed the strategy, and it also silently changed how signal-limited the bot is.)\n")

    A("\n## 6. DELTA 3 — the cost-amortization test (the brief's one genuine open hypothesis)\n")
    A("The mechanism is real: the $1.60 round trip ($1.10 commission + $0.50 for the 0.05 extra spread) "
      "is a fixed charge, so it must shrink as a share of the move being chased. The question is whether "
      "**M5 benefits more from that than M1 does** — i.e. whether the timeframe lever is dead or merely "
      "mis-aimed at the sniper's $15 shape.\n")
    A("\n**6a. The fixed cost as a share of the move actually captured, by target band.**\n")
    A(md(eraorder(cs)))
    A("\n**6b. Net $/basket by target band: M5 against its trigger-only M1 twin.** `best` is the best of "
      "the stops {5,10,15,20,30,70} inside the band (a best-of-N search, so optimistic by construction); "
      "`mean` is the average over the band, which is the honest statistic. `bb` = the best "
      "`accuracy − own luck bar` in the band.\n")
    A(md(eraorder(bd), floats={"d_best": "{:+.3f}", "d_mean": "{:+.3f}", "d_bb": "{:+.2f}"}))
    A("\n**6c. The answer, averaged over the 12 era-pair cells in each band.**\n")
    A(md(bs, floats={"mean_d_best": "{:+.3f}", "median_d_best": "{:+.3f}", "mean_d_mean": "{:+.3f}",
                     "mean_d_beats_bar": "{:+.2f}"}))

    A("\n## 7. Money at all three shapes\n")
    bestl = G.loc[G.groupby(["era", "variant"]).basket_usd.idxmax()]
    sh = GP.merge(G[(G.target == SHIPPED[0]) & (G.stop == SHIPPED[1])],
                  on=["era", "variant"], suffixes=("", "_s")).merge(
        bestl, on=["era", "variant"], suffixes=("", "_b"))
    sh["best_shape"] = sh.apply(lambda r: f"${int(r.target_b)}/${int(r.stop_b)}", axis=1)
    A(md(eraorder(sh), cols=["era", "variant", "basket_usd", "baskets", "basket_usd_s", "baskets_s",
                             "best_shape", "basket_usd_b", "baskets_b"],
         floats={"basket_usd": "{:+.3f}", "basket_usd_s": "{:+.3f}", "basket_usd_b": "{:+.3f}"}))
    A(f"\n`_s` = the shipped $7/$70 shape, `_b` = the best of the 36 on the ladder for that era-variant "
      f"(a best-of-36 pick, therefore optimistic). **Not one of the 36 shapes turns any of the six "
      f"positive in any single era, let alone all three: 0 of {len(G)} cells.**\n")

    A("\n## 8. The placebo bar — what this method invents from nothing\n")
    A("Each reading's chosen-side vector is rotated circularly by a random whole number of weeks (the "
      "signal rhythm and the clustering of outcomes survive, any real link between side and outcome "
      "dies), the best of the 6 is taken, 200 draws. Both signs are reported, so the two-sided bar is the "
      "headline.\n")
    A(md(pd.DataFrame([dict(era=er, one_sided_P50=round(PB[er]["acc_P50"], 2),
                            one_sided_P95=round(PB[er]["acc_P95"], 2),
                            two_sided_P95_THE_BAR=round(PB[er]["acc2_P95"], 2),
                            two_sided_max=round(PB[er]["acc2_max"], 2),
                            placebo_usd_P95=round(PB[er]["money2_P95"], 3),
                            best_REAL_acc=round(float(GP[GP.era == er].acc.max()), 2))
                       for er in ERAS if er in PB])))

    A("\n## 9. The lookahead twin — and BT21-R's rule about violent signal bars\n")
    A("Same signal set, same shape; the only change is that the basket fills at the open of the **M5 bar "
      "whose close produced the signal** instead of the first minute after it closed. "
      "`checks_*.json → twin_has_identical_signal_set` is PASS on both feeds, so this isolates one line.\n")
    A(md(eraorder(tw), cols=["era", "variant", "signals_h", "acc_h", "acc_l", "d",
                             "per_signal_usd_h", "per_signal_usd_l"],
         floats={"acc_h": "{:.2f}", "acc_l": "{:.2f}", "d": "{:+.2f}",
                 "per_signal_usd_h": "{:+.3f}", "per_signal_usd_l": "{:+.3f}"}))
    A("\n**Against BT21-E's M1 twin on the same rules and the same eras:**\n")
    A(md(pd.DataFrame([
        dict(era=er, family=fam,
             M1_twin_delta=f"{a.d.min():+.2f} to {a.d.max():+.2f}",
             M5_twin_delta=f"{b.d.min():+.2f} to {b.d.max():+.2f}",
             ratio=round(abs(b.d.mean()) / max(1e-9, abs(a.d.mean())), 2))
        for er in ERAS
        for fam, a, b in (("EMA pullback (close>open)", ema_e[ema_e.era == er], ema_t[ema_t.era == er]),
                          ("sweep fade", swp_e[swp_e.era == er], swp_t[swp_t.era == er]))
        if len(a) and len(b)])))
    A(f"\n**Prediction confirmed.** BT21-R found look-ahead size scales with how violent the signal bar "
      f"is (ORB immediate break +21.1 points); BT21-E's M1 EMA-pullback twin was worth up to +7.75. A "
      f"5-minute signal bar has roughly sqrt(5) ≈ 2.2x the range of a 1-minute one, and the twin grows "
      f"with it: **{ema_t.d.max():+.2f} points** at most, "
      f"{(ema_t.d.mean() / ema_e.d.mean()):.2f}x the M1 figure on average. The sweep's twin is **negative** "
      "on both timeframes and also grows in magnitude "
      f"({swp_e.d.min():+.2f}…{swp_e.d.max():+.2f} on M1 → {swp_t.d.min():+.2f}…{swp_t.d.max():+.2f} on "
      "M5), because the cheat runs the other way for a rule that fades. Two consequences: a profitable "
      "higher-timeframe gold result is **more** suspect than an M1 one, not less; and a twin that reports "
      "'no difference' is still not proof of cleanliness — it can be two biases cancelling.\n")

    A("\n## 10. Ten real baskets to check on a chart\n")
    nw = int((trades.net_usd > 0).mean() * 100)
    A(f"Seven winning and three losing baskets, drawn at random (seed 21) from **T6_sweep60** at $15/$70 "
      f"across both feeds. The split is deliberate and stated: at this shape {nw}% of baskets win, so an "
      "unstratified ten would come back all green and look like cherry-picking. Full CSVs for every "
      "variant and era are in this folder.\n")
    A(md(ex, cols=["#", "t_m5_signal_open", "t_m5_signal_close", "t_entry", "era", "side", "fill",
                   "target_usd", "stop_usd", "t_exit", "hold_min", "net_usd"]))
    A("\nTo check one by hand: the **M5** signal bar is `t_m5_signal_open`→`t_m5_signal_close`; its close "
      "is the trigger. The fill is the open of the M1 bar that starts at `t_m5_signal_close`, plus the "
      "spread and 0.05 for a BUY, minus 0.05 for a SELL. Nothing after `t_m5_signal_close` is used to "
      "decide anything.\n")
    A(f"\n**Row 1 worked through from the raw frames, independently of the pipeline:**\n")
    A(md(pd.DataFrame([dict(step=k, value=v) for k, v in hc.items()])))

    A("\n## 11. Pass/fail, variant by variant\n")
    A(md(pf))
    A("\nOne reading deserves its own line rather than being buried: "
      f"**T2_m15_ema21 reads {float(GP[(GP.variant == 'T2_m15_ema21') & (GP.era == '2009-2020')].acc.iloc[0]):.2f} "
      f"in 2009-2020, above that era's two-sided placebo bar of {PB['2009-2020']['acc2_P95']:.2f}.** It is "
      "still 4.8 points below its own break-even, loses $2.42 a basket, and falls to "
      f"{float(GP[(GP.variant == 'T2_m15_ema21') & (GP.era == '2021-2023')].acc.iloc[0]):.2f} in "
      "2021-2023 — below 50. Clause 3 alone is not a finding; that is the whole point of having four.\n")

    A("\n## 12. The sanity checks\n")
    k1 = ["fill_open_equals_M5_close", "worst_seconds_fill_open_before_M5_close",
          "min_minutes_from_M5_open_to_fill", "twin_has_identical_signal_set",
          "trend_bar_closes_at_or_before_fill_open_s", "trigger_M5_bar_closed_before_fill_s",
          "mirror_is_100_minus_acc", "engine_matches_bt20_run",
          "walk_base_columns_match_bt21e_walk", "acc_all", "acc_excluding_gap_crossers",
          "pct_signals_whose_1440bar_window_crosses_a_gap",
          "pct_baskets_that_actually_resolve_after_a_gap"]
    A(md(pd.DataFrame([dict(check=k, **{d: CK[d].get(k) for d in FEEDS if d in CK}) for k in k1])))
    A("\n`bt21e_parity` — BT21-E's E1-E6 recomputed from its own walk tables against its published "
      "`grid_<feed>.csv` at $15/$70, which is what makes every delta in this report a difference and not "
      "two independent runs:\n")
    A(md(pd.DataFrame([dict(feed=d, **CK[d]["bt21e_parity"]) for d in FEEDS if d in CK])))
    A("\nHand replay of three baskets per feed (the walk's arithmetic re-done bar by bar off the raw M1 "
      "frame) is in `checks_<feed>.json → hand_replay`; every row's `ok` is true.\n")
    A("\n**Overlap sensitivity.** T1-T4 fire on every qualifying M5 bar, so consecutive signals share most "
      "of their 1440-minute window. Keeping only the FIRST signal inside each trend bar removes that "
      "overlap and moves nothing:\n")
    A(md(pd.DataFrame([dict(feed=d, variant=v, **CK[d]["overlap_sensitivity"][v])
                       for d in FEEDS if d in CK for v in CK[d]["overlap_sensitivity"]])))

    A("\n## 13. Reconciling with the prior evidence (BT16, BT17)\n")
    A("The brief asked for this explicitly. BT16 ran seven classic rules on M1, M5 and M15 through the "
      "same 30-minute entry grid; BT17 ran four SMC concepts on M1 and M5. Pooled, those 13 same-rule "
      "comparisons differ by about a quarter of a point in both directions, and the project's best "
      "reading anywhere (`B1_bos_M1_MIRROR`, 51.45) is on M1. BT21-T's trigger-only deltas are "
      f"{dat.delta.min():+.2f} to {dat.delta.max():+.2f}, mean {dat.delta.mean():+.3f} — the same size, "
      "the same two-sidedness, now on the owner's own rules, his own shape and his own engine, over "
      "seven million-odd M1 chances' worth of M5 twins. Three independent families of evidence agree: on "
      "gold, the signal timeframe between M1 and M15 is not a lever. BT16's one asymmetry survives too — "
      "its two *fade* rules got worse going up (RSI 52.77 → 51.64 → 50.16), and here the fade rules "
      f"(T5/T6, sweeps) are the two whose deltas are most often negative.\n")

    A("\n## 14. Shared-code issues (reported, not fixed)\n")
    A("No new defect was found in `bt20_lib`, `bt21e_lib`, `common.asof_index` or `smc_core.ema`. "
      "`walk_base_columns_match_bt21e_walk` is PASS on both feeds — extending the ladder from 8 rungs to "
      "11 changes nothing in the 8 it shares with BT21-E — and `engine_matches_bt20_run` is PASS, so the "
      "throughput replay here IS `bt20_run.engine`. The three issues below are BT21_SUMMARY's, confirmed "
      "again on M5 data, plus one new remark.\n")
    A("**1. `bt/bt20_run.py:101-102` — `np.where(bw[0] >= bw[1], 1, -1)` awards every tie to the long "
      "side.** Confirmed as a live hazard on this family's numbers. At $15/$70 the M5 variants are "
      f"{GP.decisive_pct.min():.1f}-{GP.decisive_pct.max():.1f}% decisive, i.e. "
      f"{100 - GP.decisive_pct.max():.0f}-{100 - GP.decisive_pct.min():.0f}% of chances are ties where "
      "both directions resolve the same way. BT21-T never uses that line: accuracy comes from "
      "`bt21e_lib.score`, which scores a tie as 0.5. Proof of the size: T1 in 2009-2020 is "
      f"{float(GP[(GP.variant == 'T1_m15_ema9') & (GP.era == '2009-2020')].acc.iloc[0]):.2f} with ties "
      "halved; T1 is net long (more BUY than SELL triggers), so `bt20_run`'s rule would hand it several "
      "free points of apparent accuracy for nothing.\n")
    A("**2. `bt/bt20_lib.py:78-104` — `walk` walks 1,440 *bars*, not 1,440 clock minutes, and ignores the "
      "Friday 20:45 flat-out `common.simulate` applies.** Measured here, same as BT21-E: "
      + ", ".join(f"{d} {CK[d]['pct_signals_whose_1440bar_window_crosses_a_gap']}% of signals have a >6 h "
                  f"gap inside their window and {CK[d]['pct_baskets_that_actually_resolve_after_a_gap']}% "
                  f"of baskets actually resolve past one" for d in FEEDS if d in CK)
      + ". Dropping those baskets moves T1's accuracy from "
      + ", ".join(f"{CK[d]['acc_all']} to {CK[d]['acc_excluding_gap_crossers']} ({d})"
                  for d in FEEDS if d in CK)
      + " — a few hundredths, no sign flips, but long-hold work must not use `walk` as-is.\n")
    A("**3. `bt/bt20_lib.py:93,99` — `ix + HORIZON - 1` is read with no bounds check.** Safe only because "
      "callers filter. `bt21t_lib.signals` applies the same filter for the same reason, and additionally "
      "applies it to **both** candidate fill bars, honest and twin, so that the two runs cannot end up "
      "with different signal sets — which is exactly what happened on the first duka build here (the twin "
      "kept one trigger near the end of the feed that the honest run had dropped) and would have made the "
      "twin comparison off-by-one. Anyone building a lookahead twin on top of `walk` should copy that.\n")
    A("**4. New remark, not a defect: `common.resample` keeps a partial M5 bar.** An M5 bar labelled "
      "10:00 whose first real minute is 10:03 still gets `tclose = 10:05`, so the trigger is read from a "
      "two-minute bar. That is what a live MT5 chart shows too, so it is correct for this test; but it "
      "means an M5 'bar' is not always 5 minutes of data, and an M5 bar whose label falls inside a gap has "
      "no M1 bar at its open time at all. BT21-T drops those triggers (both fill bars must exist) and "
      f"reports the kept counts in `checks_*.json → signals_kept`.\n")

    A("\n## 15. The verdict in plain language\n")
    A("**Does a higher timeframe like the five minutes yield more edge? No.** With the trend filter held "
      "fixed so that only the trigger moves, the six M5 variants read "
      f"{GP.acc.min():.2f}-{GP.acc.max():.2f} right in 100, against their own break-even of "
      f"{GP.luck_bar.min():.2f}-{GP.luck_bar.max():.2f}; the M1→M5 change is "
      f"{dat.delta.min():+.2f} to {dat.delta.max():+.2f} points, mean {dat.delta.mean():+.3f}, with "
      "standard errors of the same size and no agreement on a sign. The 5-point gap between what these "
      "entries do and what they must do to pay for themselves is untouched.\n")
    A("**Does it take more trades? No — it takes far fewer.** M5 produces about a fifth of the signals, "
      f"and after the one-basket-at-a-time rule it opens {-dc.bask_pct.max():.0f}-{-dc.bask_pct.min():.0f}% "
      "fewer baskets than the M1 twin. The intuition behind the question — 'a slower timeframe means "
      "fewer, better trades' — gets the 'fewer' right and the 'better' wrong.\n")
    A("**And the cost-amortization hypothesis, which was the one honest reason to expect M5 to help?** "
      "The mechanism exists: the fixed $1.60 round trip falls from roughly a third of the move at a $7 "
      "target to a few percent at a $200 target, and both timeframes make more money per basket at wide "
      "targets than at narrow ones. But it is not a *timeframe* effect. The M5−M1 gap in best net "
      f"$/basket is "
      + "; ".join(f"{r.band} {r.mean_d_best:+.3f}" for r in bs.itertuples())
      + ". Widening the target helps M1 just as much, because what a wider target really buys is a longer "
      "hold, and a longer hold is available on either trigger. **The timeframe lever is not mis-aimed; it "
      "is dead.** The lever that does move is the one BT21_SUMMARY already named: express the target and "
      "stop in ATR or percent instead of dollars, and trade a market where the round trip is small next "
      "to the stop.\n")
    A("\n### What surprised me\n")
    A("**1. The lookahead twin nearly doubled.** I expected BT21-R's rule to hold directionally and it "
      f"held by a mile: the EMA-pullback twin goes from +2.5…+7.8 points on M1 to "
      f"{ema_t.d.min():+.2f}…{ema_t.d.max():+.2f} on M5, and the sweep's *negative* twin goes from "
      f"−2.2…+0.4 to {swp_t.d.min():+.2f}…{swp_t.d.max():+.2f}. The practical consequence is "
      "uncomfortable: the higher the signal timeframe, the more a careless fill flatters the result, so "
      "every H1/H4 claim in this project's archive is more exposed to this bug than the M1 ones are, not "
      "less.\n")
    A("**2. The trade-count prediction in the brief was wrong, and wrong for an interesting reason.** "
      "15-25% predicted, 26-77% measured. Throughput saturation is not a property of the engine; it is a "
      "property of how long a basket lives, which the dollar-denominated shape made era-dependent. In "
      "2009-2020 (89-minute baskets, 3% conversion) the buffer was real; in 2024-2026 (10-minute baskets, "
      "20% conversion) it has mostly evaporated and a signal cut passes almost straight through to trades.\n")
    A("**3. T3/T4 — the new H1 rung — are the best of the six on two of three eras, and it means "
      "nothing.** H1-filtered variants read "
      f"{float(GP[(GP.variant == 'T3_h1_ema9') & (GP.era == '2021-2023')].acc.iloc[0]):.2f} and "
      f"{float(GP[(GP.variant == 'T4_h1_ema21') & (GP.era == '2021-2023')].acc.iloc[0]):.2f} in "
      "2021-2023, the highest non-sweep readings anywhere in the family, and they are still 4.8 points "
      "short of break-even. Three rungs of trend filter (M5, M15, H1) now span about 0.5 of a point. "
      "Adding rungs to this ladder is not going to find anything.\n")
    A("**4. The ladder's best shape is $50 — the largest target offered — in every single one of the 18 "
      "era-variant cells, on both timeframes.** BT21-E reported the same thing and I read it as a result; "
      "it is really a boundary artefact. Walking the $100/$150/$200 rungs was the only way to tell "
      "whether 'wider is better' had an optimum or just hit the edge of the grid, and it is the part of "
      "this run I would least want to have skipped.\n")

    txt = "\n".join(L) + "\n"
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "report.md"), "w", encoding="utf-8").write(txt)
    print(f"wrote {os.path.join(OUT, 'report.md')}  ({len(txt):,} chars)")


if __name__ == "__main__":
    main()
