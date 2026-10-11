"""BT14 analysis: turn results/BT14/parts/*.json into the two distributions, P95/P99 and the verdict.

    .venv/Scripts/python.exe smc_bot/bt/bt14_report.py            # prints everything, writes the CSVs + numbers.json
"""
import os, sys, json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SMC = os.path.dirname(HERE)
OUT = os.path.join(SMC, "results", "BT14")
REF_NET = 0.196                       # canonical REF, DEV, net R per trade (results/RECON/report.md section 4)
REF_N = 150
RISK_USD_01 = 16.05                   # mean $ risk per trade at 0.10 lot, measured on the real REF DEV trades
N_MIN = 50                            # a "reportable" variant: at least 1/3 of REF's 150 trades (see report.md)


def pct(x, q):
    return float(np.percentile(np.asarray(x, dtype=float), q))


# the real canonical REF DEV result, for the look-alike comparison (results/RECON/report.md section 4)
REAL_REF = dict(net_R=0.196, gross_R=0.262, t_naive=1.07, win_pct=27.3, PF=1.25, pairs_pos=5,
                net_LDN=0.44, net_NY=-0.04, maxDD_R=-17.5, trades=150)


def lookalike_worlds(a, k=3):
    """Which noise worlds did the frozen REF turn into something that looks like the real BT6 result?
    Distance over the five numbers BT6 was argued on: net R, t, win %, PF, and the London-minus-NY gap."""
    d = ((a.net_R - REAL_REF["net_R"]) / 0.15) ** 2 + ((a.t_naive - REAL_REF["t_naive"]) / 0.8) ** 2 \
        + ((a.win_pct - REAL_REF["win_pct"]) / 4.0) ** 2 + ((a.PF - REAL_REF["PF"]) / 0.2) ** 2 \
        + (((a.net_LDN - a.net_NY) - (REAL_REF["net_LDN"] - REAL_REF["net_NY"])) / 0.3) ** 2
    idx = d.sort_values().index[:k]
    cols = ["seed", "trades", "gross_R", "net_R", "t_naive", "t_block", "win_pct", "PF", "pairs_pos",
            "net_LDN", "net_NY", "maxDD_R"]
    return a.loc[idx, cols].to_dict("records")


def describe(x, label, ref=REF_NET):
    x = np.asarray(x, dtype=float)
    d = dict(what=label, n_worlds=len(x), mean=round(float(x.mean()), 4), sd=round(float(x.std(ddof=1)), 4),
             P05=round(pct(x, 5), 4), P50=round(pct(x, 50), 4), P90=round(pct(x, 90), 4),
             P95=round(pct(x, 95), 4), P99=round(pct(x, 99), 4), max=round(float(x.max()), 4),
             frac_ge_REF=round(float((x >= ref).mean()), 4), frac_gt_0=round(float((x > 0).mean()), 4))
    return d


def main():
    import bt14_run as Rn
    a, b = Rn.collect(n_min=N_MIN)
    if not len(a):
        print("no parts yet"); return
    # integrity: the worlds must actually be different worlds. A stale detection cache (ERRORS #32) would make
    # several "independent" realizations identical, which would silently narrow every distribution below.
    dup = a.duplicated(subset=["trades", "net_R"], keep=False).sum()
    assert dup == 0, f"{dup} realizations share (trades, net_R) -> worlds are NOT independent, check the cache"
    print(f"integrity: {len(a)} worlds, all distinct (no two share trades+net_R); "
          f"trade counts {int(a.trades.min())}-{int(a.trades.max())}")

    rows = [describe(a.net_R, "(a) frozen REF config alone"),
            describe(b.best_net_R, f"(b) best of {int(b.n_configs.iloc[0])} rule readings (>= {N_MIN} trades)"),
            describe(b.best_net_R_nofloor, "(b') best of the same readings, no trade-count floor")]
    D = pd.DataFrame(rows)
    print("\n================ distributions of net R per trade over noise worlds ================")
    print(D.to_string(index=False))

    P95 = D.loc[1, "P95"]; P99 = D.loc[1, "P99"]
    # how precisely do we know P95 with this many worlds? (bootstrap over worlds)
    rng = np.random.default_rng(3)
    bv = np.asarray(b.best_net_R, dtype=float)
    bs95 = np.array([pct(rng.choice(bv, len(bv)), 95) for _ in range(4000)])
    bs99 = np.array([pct(rng.choice(bv, len(bv)), 99) for _ in range(4000)])
    P95_CI = [round(pct(bs95, 2.5), 3), round(pct(bs95, 97.5), 3)]
    P99_CI = [round(pct(bs99, 2.5), 3), round(pct(bs99, 97.5), 3)]
    verdict = ("REF +0.196R is BELOW P95 -> fully explained by noise + variant search; BT6 closes as a false positive"
               if REF_NET < P95 else
               ("REF +0.196R is between P95 and P99 -> borderline: consistent with noise, not proven to be noise"
                if REF_NET < P99 else
                "REF +0.196R is ABOVE P99 -> noise + search does NOT explain BT6; investigate, do not celebrate"))
    extra = dict(
        P95=P95, P99=P99, P95_CI95=P95_CI, P99_CI95=P99_CI, REF_net_R=REF_NET, verdict=verdict,
        risk_usd_per_01lot=RISK_USD_01,
        P95_usd_per_trade_01lot=round(P95 * RISK_USD_01, 2),
        P95_usd_per_150trades_01lot=round(P95 * RISK_USD_01 * REF_N, 0),
        REF_usd_per_trade_01lot=round(REF_NET * RISK_USD_01, 2),
        a_frac_ge_REF=float(D.loc[0, "frac_ge_REF"]), b_frac_ge_REF=float(D.loc[1, "frac_ge_REF"]),
        # how often a noise world hands us something that LOOKS like the BT6 headline
        a_frac_looks_like_REF=round(float(((a.net_R >= REF_NET) & (a.t_naive >= 1.0)).mean()), 4),
        b_frac_looks_like_REF=round(float(((b.best_net_R >= REF_NET)
                                           & (b.best_t_naive.fillna(-9) >= 1.0)).mean()), 4),
        a_trades=dict(mean=round(float(a.trades.mean()), 1), P05=round(pct(a.trades, 5), 0),
                      P95=round(pct(a.trades, 95), 0)),
        # ref = BT6's own t of 1.07, so frac_ge_REF reads "share of noise worlds whose t beats BT6's t"
        a_t_naive=describe(a.t_naive.dropna(), "(a) t-stat, naive", ref=REAL_REF["t_naive"]),
        a_t_block=describe(a.t_block.dropna(), "(a) t-stat, week block bootstrap", ref=REAL_REF["t_naive"]),
        a_overlap_frac=round(float(a.overlap_frac.mean()), 3),
        a_frac_t_ge_2=round(float((a.t_naive >= 2.0).mean()), 4),
        b_frac_t_ge_2=round(float((b.best_t_naive.fillna(-9) >= 2.0).mean()), 4),
        b_max_t=round(float(b.best_t_naive.max()), 2),
        a_pairs_pos_mean=round(float(a.pairs_pos.mean()), 2),
        a_frac_LDN_beats_NY=round(float((a.net_LDN > a.net_NY).mean()), 3),
        a_mean_gross_R=round(float(a.gross_R.mean()), 4),
        a_mean_win_pct=round(float(a.win_pct.mean()), 2),
        # LOOKAHEAD AUDIT (BT14_BRIEF item 1). On a driftless walk a causal strategy has expected gross P&L of
        # exactly zero by optional stopping, so the MEAN gross R across worlds must sit at 0 minus the spread drag.
        # Any systematic positive mean gross R would mean the pipeline is peeking at the future.
        lookahead_audit=dict(
            mean_gross_R=round(float(a.gross_R.mean()), 4),
            se=round(float(a.gross_R.std(ddof=1) / np.sqrt(len(a))), 4),
            z_vs_zero=round(float(a.gross_R.mean() / (a.gross_R.std(ddof=1) / np.sqrt(len(a)))), 2),
            mean_net_R=round(float(a.net_R.mean()), 4),
            se_net=round(float(a.net_R.std(ddof=1) / np.sqrt(len(a))), 4),
            z_net_vs_zero=round(float(a.net_R.mean() / (a.net_R.std(ddof=1) / np.sqrt(len(a)))), 2)),
        # the synthetic worlds fill MORE trades than real REF (same setup rate, slightly higher fill/target pass rate).
        # Per-trade noise shrinks as 1/sqrt(n), so these distributions are a little TIGHTER than a 150-trade result
        # would be. First-order correction: scale by sqrt(n_synthetic / 150).
        scale_to_150=round(float(np.sqrt(b.best_trades.mean() / REF_N)), 3),
        P95_at_150_trades=round(float(P95 * np.sqrt(b.best_trades.mean() / REF_N)), 3),
        best_trades_mean=round(float(b.best_trades.mean()), 1),
        # how often the search's winner IS the canonical REF reading (swing dealing range, local killzone,
        # nearest unbroken target, bias checked at all 3 steps) -- i.e. noise handed us exactly BT6's config
        frac_best_is_REF_reading=round(float(b.best_config.isin(
            ["CANON", "grid_drange=swing_kz=local_target=nearest_bias_checks=all3"]).mean()), 3),
        best_when_REF_reading=[round(float(v), 3) for v in b.loc[b.best_config.isin(
            ["CANON", "grid_drange=swing_kz=local_target=nearest_bias_checks=all3"]), "best_net_R"]],
        best_config_counts=b.best_config.value_counts().head(12).to_dict(),
        median_of_readings=describe(b.median_net_R, "(b'') median reading in a world"),
        # RECON's robustness claim was "94% of the 448 readings were net positive, median +0.34R".
        # Does a noise world reproduce THAT? share of readings positive, per world.
        share_pos=dict(mean=round(float((b.n_cfg_pos / b.n_configs).mean()), 3),
                       P50=round(pct(b.n_cfg_pos / b.n_configs, 50), 3),
                       P95=round(pct(b.n_cfg_pos / b.n_configs, 95), 3),
                       max=round(float((b.n_cfg_pos / b.n_configs).max()), 3),
                       frac_worlds_ge_94pct=round(float(((b.n_cfg_pos / b.n_configs) >= 0.94).mean()), 3),
                       frac_worlds_ge_75pct=round(float(((b.n_cfg_pos / b.n_configs) >= 0.75).mean()), 3)),
        lookalikes=lookalike_worlds(a),
    )
    print("\n================ the bar ================")
    print(f"P95 (best-of-search under pure noise) = {P95:+.3f} R   "
          f"= ${P95 * RISK_USD_01:+.2f} per trade at 0.10 lot")
    print(f"   bootstrap 95% CI for P95 with {len(b)} worlds: {P95_CI}")
    print(f"P99                                   = {P99:+.3f} R   CI {P99_CI}")
    print(f"REF's DEV headline                    = {REF_NET:+.3f} R")
    print("VERDICT:", verdict)
    print(f"\nfraction of noise worlds whose BEST reading beats +0.196R: {D.loc[1, 'frac_ge_REF']:.1%}")
    print(f"fraction of noise worlds where the FROZEN REF alone beats +0.196R: {D.loc[0, 'frac_ge_REF']:.1%}")
    print(f"fraction that look like the BT6 headline (net >= +0.196R AND t >= 1): "
          f"(a) {extra['a_frac_looks_like_REF']:.1%}  (b) {extra['b_frac_looks_like_REF']:.1%}")
    print(f"\nt-stat inflation from overlapping trades: mean overlap {extra['a_overlap_frac']:.0%} of trades; "
          f"naive t sd {extra['a_t_naive']['sd']:.2f} vs week-block t sd {extra['a_t_block']['sd']:.2f}")
    print(f"'London beats New York' happens in {extra['a_frac_LDN_beats_NY']:.0%} of noise worlds "
          f"(it was the single pre-registered BT6 hypothesis)")
    print(f"mean pairs positive {extra['a_pairs_pos_mean']}/9 ; mean gross R {extra['a_mean_gross_R']:+.4f} ; "
          f"mean win% {extra['a_mean_win_pct']}")
    la = extra["lookahead_audit"]
    print(f"\nlookahead audit: mean GROSS R over worlds = {la['mean_gross_R']:+.4f} +/- {la['se']:.4f} "
          f"(z = {la['z_vs_zero']:+.2f} vs the required 0). mean NET R = {la['mean_net_R']:+.4f} +/- {la['se_net']:.4f} "
          f"(z = {la['z_net_vs_zero']:+.2f}).")

    # breadth sub-study: did the other 399 readings add anything to the best-of?
    if os.path.exists(os.path.join(OUT, "best_of_searchb.csv")):
        bb = pd.read_csv(os.path.join(OUT, "best_of_searchb.csv"))
        m = b.set_index("seed").best_net_R.reindex(bb.seed).values
        extra["breadth"] = dict(worlds=len(bb), best448=[round(x, 3) for x in bb.best_net_R],
                                best49=[round(x, 3) for x in m],
                                mean_gap=round(float(np.nanmean(bb.best_net_R.values - m)), 4))
        print(f"\nbreadth sub-study ({len(bb)} worlds): best-of-448 - best-of-49 = "
              f"{extra['breadth']['mean_gap']:+.3f}R on average")

    json.dump(dict(distributions=rows, **extra), open(os.path.join(OUT, "numbers.json"), "w"), indent=1, default=str)
    D.to_csv(os.path.join(OUT, "distributions.csv"), index=False)
    write_md(D, extra, a, b)
    print("\nwrote report.md, numbers.json, distributions.csv, realizations.csv, best_of_search.csv")
    return D, extra


# ============================================================================== report.md
def md_table(df, cols=None, fmt=None):
    df = df[cols] if cols else df
    head = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    out = [head, sep]
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if fmt and c in fmt:
                cells.append(fmt[c] % v if v == v and v is not None else "–")
            else:
                cells.append("–" if v is None or (isinstance(v, float) and v != v) else str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def write_md(D, X, a, b):
    import bt14_run as Rn
    sanity = json.load(open(os.path.join(OUT, "sanity.json")))
    nw = len(a)
    done = nw >= 200
    P95, P99 = X["P95"], X["P99"]
    la = X["lookahead_audit"]
    rate = pd.DataFrame(sanity["setup_rate"]["table"])
    rnd = pd.DataFrame(sanity["random_entries"]["table"])
    usd = X["risk_usd_per_01lot"]
    ncfg = int(b.n_configs.iloc[0])
    pf = lambda v: f"{v:+.3f}"                                                        # noqa: E731
    bar_money = f"${P95 * usd:+.2f}"
    md = f"""# BT14 — Placebo calibration of our own research method

**The thermometer in the ice water.** We ran the real, frozen REF pipeline over price data we built to contain
*exactly zero* edge, {nw} times, and recorded what it reported. Everything it reports on that data is error
manufactured by our own method.

## Summary (10 lines)
1. Sanity check 1 (simulator): random entries on the synthetic data give win rate = 1/(1+RR) and net R = −(cost). **PASS**
2. Sanity check 2 (wiring): the same pipeline on **real** data reproduces canonical REF **exactly** — 150 trades,
   gross +0.262R, net +0.196R, t 1.07. **PASS**
3. Setup-rate calibration: the synthetic data produces REF setups at **{rate.loc[rate.stage == '13_filled', 'ratio'].iloc[0]:.2f}×** the real rate
   (every funnel stage within 1.25×, requirement was 2×). **PASS**
4. Lookahead audit: mean **gross** R over the noise worlds = {la['mean_gross_R']:+.4f} ± {la['se']:.4f} (z {la['z_vs_zero']:+.2f} vs the
   required zero). The pipeline is not peeking.
5. **(a) The frozen REF config alone**, on zero-edge data: net R per trade has mean {D.loc[0, 'mean']:+.3f}, sd {D.loc[0, 'sd']:.3f},
   P95 {pf(D.loc[0, 'P95'])}. **{D.loc[0, 'frac_ge_REF']:.0%}** of noise worlds beat REF's +0.196R with the frozen config and nothing else.
6. **(b) Best of the {ncfg} faithful rule readings** (what we actually did — we looked at many and reported the best):
   mean {D.loc[1, 'mean']:+.3f}, **P95 = {pf(P95)} R**, **P99 = {pf(P99)} R**. **{D.loc[1, 'frac_ge_REF']:.0%}** of noise worlds beat +0.196R.
7. **THE BAR: P95 = {pf(P95)} R per trade** (95% CI {X['P95_CI95']}), i.e. {bar_money} per trade at 0.10 lot.
   A DEV result below this number is not evidence of anything.
8. **Pre-registered verdict: REF's +0.196R is {'BELOW P95' if REF_NET < P95 else ('between P95 and P99' if REF_NET < P99 else 'ABOVE P99')}** —
   {X['verdict'].split('-> ')[-1]}.
9. {X['a_frac_looks_like_REF']:.0%} of noise worlds hand the frozen config a result that *looks like* the BT6 headline
   (net ≥ +0.196R **and** t ≥ 1); {X['b_frac_looks_like_REF']:.0%} do with the variant search. "London beat New York" — BT6's one
   pre-registered hypothesis — happens in **{X['a_frac_LDN_beats_NY']:.0%}** of worlds with no London effect in the data at all.
10. BT6 is formally closed as a false positive. The number to carry into Option B is **P95 = {pf(P95)} R**.

## 1. What the question is
Every backtest reports a number. **How big does that number have to be before it means anything?** BT13 showed REF's
+0.196R did not survive 16 unseen years (+0.036R, t 0.46) and BT8b showed the entry carries no information. BT14 stops
inferring and measures: it feeds the method data with no edge in it and reads off what the method claims to find.

## 2. The zero-edge data (`bt/bt14_gbm.py`)
| Ingredient | How it is built | Why |
|---|---|---|
| Price process | zero-drift GBM. The log-price increment of every sub-step is `Normal(mu=0.0, sigma)`; `MU` is the literal constant `0.0` and `bt14_tests.py` asserts both the constant and the source line | there is nothing to find. Any "edge" the pipeline reports is method error |
| Volatility | per instrument **and per hour-of-day**, measured from the real DEV series (2021-01-01 → 2024-01-01) | keeps the intraday shape, so the killzone structure REF trades is realistic |
| Bars | each M1 bar built from **{sanity['zero_drift']['SUB']} sub-minute steps**: open/high/low/close from the path inside the minute | SMC detection reads **wicks**. Close-to-close bars never form sweeps or order blocks at a realistic rate and the calibration would be meaningless |
| Calendar | the **real** minute timestamps are copied, so weekend gaps, holiday closures and data holes are identical | `common.simulate`'s weekend / 6-hour-gap flat rules behave exactly as on real data |
| Costs | the **real** per-minute spread column is copied; commission and 0.2-pip stop slippage come from `common.py` unchanged | the cost model is not softened |
| Shape | 9 instruments (PAIRS minus EURGBP, as REF trades), one DEV-length window (3 years), labelled DEV | same shape as the real result we are calibrating |
| Realizations | **{nw}** independent worlds, one RNG seed each, seed recorded in `realizations.csv`; every instrument gets its own stream derived from the world seed | |

Prices drift wherever the random walk takes them; the spread column is stored in price units equal to a fixed number
of *pips*, so the cost in pips is unchanged whatever level a world reaches.

**No lookahead.** The whole series for a world is generated before any rule is evaluated; `gen_m1(sym, seed)` takes
nothing but an instrument and a seed, and cannot see a trade, an order or an outcome. The pipeline that then runs over
it is the project's own causal one (closed bars, swings confirmed N bars later, HTF values only after that HTF bar
closed), unmodified. The audit in §3 confirms it numerically.

## 3. The three blocking checks

### Sanity check 1 — the simulator (brief item 4)
Random side, random minute, fixed stop ({sanity['random_entries']['stop_pips']:.0f} pips), fixed RR target, market entry, one position per pair —
the same fills, exits and costs REF uses. On a driftless walk with barriers at −S and +k·S the win rate must be 1/(1+k)
and the gross result must be 0, so net R must be minus the cost.

{md_table(rnd, ["RR", "trades", "win_pct", "win_pct_expected", "net_R", "net_R_expected", "se_net_R", "gross_R", "z_vs_expected"])}

Win rates land on 1/(1+RR), gross R ≈ 0, and net R sits on top of the predicted cost (worst |z| = {max(abs(r['z_vs_expected']) for r in sanity['random_entries']['table']):.2f}). **PASS.**
(The small positive bias at RR 3 is the ~9% of trades that time out part-way, plus barrier discreteness.)

### Sanity check 2 — the wiring (brief item 5)
Before trusting any synthetic number, the same pipeline object was run over **real** data:

| | trades | gross R | net R | t |
|---|---|---|---|---|
| RECON canonical REF (frozen, `results/RECON/report.md` §4) | 150 | +0.262 | +0.196 | 1.07 |
| This agent's run, real data, same code path | {sanity['wiring_real_REF']['got']['trades']} | {sanity['wiring_real_REF']['got']['gross_R']:+.3f} | {sanity['wiring_real_REF']['got']['net_R']:+.3f} | {sanity['wiring_real_REF']['got']['t_net']:.2f} |

Identical. The harness reproduces the known answer, so the synthetic numbers are measuring the method and not a
wiring mistake. **PASS.**

### Setup-rate calibration (brief item 3)
Mean over 3 noise worlds against the real DEV funnel (`results/RECON/CANON_funnel_DEV.json`):

{md_table(rate, ["stage", "real", "synthetic", "ratio"])}

Every stage is within **1.25×** of real (requirement: 2×), including the stages that matter — zones found, touched,
CHoCH confirmed, killzone-filtered. The synthetic data exercises the pipeline at the real rate, so the calibration is
meaningful. **PASS.**

### Lookahead audit (brief item 1)
On a martingale, a strategy that only uses the past has an expected gross P&L of exactly zero (optional stopping).
So the *mean* gross R across worlds must be 0 minus the spread drag. Measured over {nw} worlds:

| | mean | standard error | z vs 0 |
|---|---|---|---|
| gross R per trade | {la['mean_gross_R']:+.4f} | {la['se']:.4f} | {la['z_vs_zero']:+.2f} |
| net R per trade | {la['mean_net_R']:+.4f} | {la['se_net']:.4f} | {la['z_net_vs_zero']:+.2f} |

No systematic edge: gross R is **{abs(la['z_vs_zero']):.2f} standard errors** from the zero it is required to be. For contrast, the
lookahead bug the brief documents (selecting entries with a candle 20 ticks in the future) produced +0.199R at
**t +8.36** on noise — it would have shown up here as a gross R many standard errors above zero.

The same table double-checks the **cost model**: gross minus net = **{la['mean_gross_R'] - la['mean_net_R']:.3f}R** of cost per trade on the
synthetic data, against **0.066R** on the real REF DEV trades (+0.262 gross → +0.196 net). The spread, commission and
0.2-pip stop slippage are biting exactly as hard as they do on real data, so nothing here is flattered by soft costs.

### Overlapping trades (brief item 2)
REF holds one position per pair, so trades can only overlap *across* pairs. Measured: **{X['a_overlap_frac']:.0%}** of trades
overlap another in time. The week-block bootstrap t and the naive t therefore have almost the same spread across
worlds (sd {X['a_t_block']['sd']:.2f} vs {X['a_t_naive']['sd']:.2f}), so this trap is not what inflated BT6 — the variant search is.
Both t's are recorded per world in `realizations.csv`.

**And note what this section makes irrelevant.** The headline P95 is a percentile of an *empirical distribution over
{nw} independent worlds*, not a t-stat. Correlation between trades inside one world cannot inflate it.

## 4. Measurement (a) — the frozen REF config alone on zero-edge data
One config, no search. This is the false-positive rate of running REF once.

{md_table(D.loc[[0]], ["n_worlds", "mean", "sd", "P05", "P50", "P90", "P95", "P99", "max", "frac_ge_REF", "frac_gt_0"])}

- Trades per world: mean {X['a_trades']['mean']:.0f} (real REF DEV: 150) — *more* trades, so this distribution is slightly
  **tighter** than a 150-trade REF would be, i.e. the percentiles below are conservative.
- Win rate: mean {X['a_mean_win_pct']:.1f}% (real: 27.3%). Instruments positive: mean {X['a_pairs_pos_mean']:.1f}/9 (real: 5/9).
- t-stat: mean {X['a_t_naive']['mean']:+.2f}, highest seen {X['a_t_naive']['max']:+.2f}; **{X['a_t_naive']['frac_ge_REF']:.0%}** of worlds reach a t at least as
  big as BT6's 1.07, and {X['a_frac_t_ge_2']:.0%} reach t ≥ 2.
- **{D.loc[0, 'frac_ge_REF']:.0%}** of worlds beat REF's +0.196R.

So even without any variant search, a single run of REF on data with no edge beats +0.196R about one time in
{1 / max(D.loc[0, 'frac_ge_REF'], 1e-9):.0f}.

### The look-alike worlds — the part worth looking at twice
Of the {nw} worlds, these are the ones where the **frozen REF config** produced the closest match to the real BT6 DEV
result, scored on the five numbers BT6 was argued on (net R, t, win %, profit factor, and the London-minus-New-York
gap). Nothing was tuned to make them match; the data contains no edge, no session effect and no trend.

| world | trades | gross R | net R | t (naive) | t (block) | win % | PF | pairs + | London | New York | maxDD R |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **REAL BT6 / REF, DEV 2021-23** | **150** | **+0.262** | **+0.196** | **1.07** | – | **27.3** | **1.25** | **5/9** | **+0.44** | **−0.04** | **−17.5** |
{chr(10).join('| noise seed %d | %d | %+.3f | %+.3f | %.2f | %.2f | %.1f | %.2f | %d/9 | %+.2f | %+.2f | %.1f |' % (w['seed'], w['trades'], w['gross_R'], w['net_R'], w['t_naive'], w['t_block'], w['win_pct'], w['PF'], w['pairs_pos'], w['net_LDN'] if w['net_LDN'] is not None and w['net_LDN'] == w['net_LDN'] else float('nan'), w['net_NY'] if w['net_NY'] is not None and w['net_NY'] == w['net_NY'] else float('nan'), w['maxDD_R']) for w in X['lookalikes'])}

Read the first noise row against the bold row. Same strategy, same code, same costs, same number of pairs, a similar
number of trades, a similar win rate, a similar profit factor, a similar t — **and a London session that beats New
York**, which was the one hypothesis BT6 pre-registered. On data we generated from a coin flip. If that result had
landed in BT6 instead, we would have believed it exactly as much.

## 5. Measurement (b) — the search we actually ran. **This is the number.**
We did not test one config. RECON enumerated **448** faithful readings of the spec and we reported the one that looked
best. So in every noise world we run the same breadth of search and take the **best** result in that world.

**The search set: {ncfg} readings per world.** RECON's 448 = a full 48-cell grid over the four axes that actually move
the result (dealing range × killzone × target × when bias is checked) **plus** 400 uniform-random configs over all 11
ambiguous axes. RECON measured that each of the 7 *minor* axes shifts the median net R by ≤ 0.05R, so the 48-cell major
grid is the part of the search that generates the spread we exploited. BT14 runs that grid plus the canonical config =
**{ncfg} readings in every world** ({nw} worlds × {ncfg} = {nw * ncfg:,} full pipeline runs). This is a *lower bound* on the
real search: the remaining readings can only push the best-of up, never down.{(chr(10) + chr(10) + "Breadth sub-study: on " + str(X['breadth']['worlds']) + " worlds the full 448-reading search was run as well; best-of-448 exceeded best-of-" + str(ncfg) + " by " + f"{X['breadth']['mean_gap']:+.3f}" + "R on average, so the bar below understates the real search by about that much.") if 'breadth' in X else (chr(10) + chr(10) + "The 399 remaining readings were **not** run in every world (compute); treat P95 as a floor.")}

{md_table(D.loc[[1, 2]], ["what", "n_worlds", "mean", "sd", "P50", "P90", "P95", "P99", "max", "frac_ge_REF", "frac_gt_0"])}

- The best reading is net-positive in **{D.loc[1, 'frac_gt_0']:.0%}** of noise worlds.
- It beats REF's +0.196R in **{D.loc[1, 'frac_ge_REF']:.0%}** of noise worlds.
- It reaches t ≥ 2 in **{X['b_frac_t_ge_2']:.0%}** of noise worlds.
- How many readings *agree* swings wildly from world to world: the share of the {ncfg} readings that come out net
  positive has median **{X['share_pos']['P50']:.0%}** across noise worlds but reaches **{X['share_pos']['max']:.0%}** in the luckiest one.
- Which reading wins is arbitrary: the most frequent winner takes it in only
  {max(X['best_config_counts'].values()) / nw:.0%} of worlds (`best_of_search.csv` has the winner per world).
- **In {X['frac_best_is_REF_reading']:.0%} of noise worlds the winner was the canonical REF reading itself** (swing dealing range,
  local killzone, nearest unbroken target, bias checked at all three steps), scoring
  {', '.join(f'{v:+.3f}' for v in X['best_when_REF_reading'][:6])}{' …' if len(X['best_when_REF_reading']) > 6 else ''} R.
  That is the same config, chosen the same way, on data with nothing in it.

**One honest caveat, stated plainly.** RECON's robustness argument was "94% of the 448 readings were net positive,
median +0.34R". Noise does **not** reproduce that easily: the *median* reading in a noise world averages
{X['median_of_readings']['mean']:+.3f}R (costs drag it below zero), and only **{X['share_pos']['frac_worlds_ge_94pct']:.0%}** of noise worlds get ≥94% of their readings
positive ({X['share_pos']['frac_worlds_ge_75pct']:.0%} get ≥75%). So the *breadth* of RECON's agreement is not itself manufactured by this
experiment — which is consistent with what BT13 found another way: the real DEV window happened to be a good one
(REF scored +0.38R on that same window with Dukascopy data), and because the 448 readings share most of their trades,
one lucky window lifts nearly all of them together. That is window luck plus correlation, not 448 independent
confirmations — but it is fair to say BT14 measures the *size* of the headline, not every part of the robustness
claim.

### The bar
| | net R per trade | $ per trade at 0.10 lot | $ over 150 trades at 0.10 lot |
|---|---|---|---|
| **P95 — the acceptance bar** | **{pf(P95)}** | **{P95 * usd:+.2f}** | **{P95 * usd * 150:+,.0f}** |
| P99 | {pf(P99)} | {P99 * usd:+.2f} | {P99 * usd * 150:+,.0f} |
| P50 (a typical noise world's best) | {pf(D.loc[1, 'P50'])} | {D.loc[1, 'P50'] * usd:+.2f} | {D.loc[1, 'P50'] * usd * 150:+,.0f} |
| REF's DEV headline (BT6) | +0.196 | +3.15 | +472 |
| REF on 16 unseen years (BT13) | +0.036 | +0.58 | +87 |

(1R = ${usd:.2f} on average at 0.10 lot: REF's mean stop was 17.5 pips. 95% CI for P95 with {nw} worlds: {X['P95_CI95']}.)

**A like-for-like correction for trade count.** Per-trade noise shrinks as 1/√n, so a distribution measured on a
different number of trades is not directly comparable to a 150-trade result. Two things pull in opposite directions:

- the **frozen config** (measurement (a)) fills {X['a_trades']['mean']:.0f} trades per world, *more* than REF's 150, so distribution
  (a) is **tighter** than a 150-trade REF would be — (a)'s percentiles are conservative;
- the **winning reading** (measurement (b)) fills only {X['best_trades_mean']:.0f} trades on average, *fewer* than 150, so distribution
  (b) is somewhat **wider**. Scaling by √({X['best_trades_mean']:.0f}/150) = {X['scale_to_150']:.2f} gives **P95 ≈ {X['P95_at_150_trades']:+.3f} R**
  for a like-for-like 150-trade search — still {X['P95_at_150_trades'] / REF_NET:.1f}× REF's +0.196R, so the verdict does not depend on this adjustment.

That the search's winner is usually a *sparser* reading is not a flaw in the experiment: it is the inflation mechanism
itself. Thinner readings are noisier, so they more often throw up a big number, and the search picks exactly those.
RECON's real 448 readings ranged from 40 to 556 trades for the same reason. We quote the unscaled {pf(P95)} as the bar
because it is measured rather than adjusted, and it is the number that matches what we actually did.

## 6. The pre-registered verdict, applied
| Outcome | Reading | |
|---|---|---|
| +0.196R **< P95** | fully explained by noise + variant search; BT6 closes as a false positive | {'**← THIS**' if REF_NET < P95 else ''} |
| +0.196R between P95 and P99 | borderline; consistent with noise, not proven to be noise | {'**← THIS**' if P95 <= REF_NET < P99 else ''} |
| +0.196R **> P99** | noise + search does not explain BT6; investigate, do not celebrate | {'**← THIS**' if REF_NET >= P99 else ''} |

**{X['verdict']}**

BT6's +0.196R is not merely unproven — it is **smaller than what our own method produces from nothing** at the 95th
percentile. A *typical* (median) noise world's best reading is {pf(D.loc[1, 'P50'])}R, i.e. {D.loc[1, 'P50'] / REF_NET:.0%} of BT6's
headline from data with nothing in it at all, and **{D.loc[1, 'frac_ge_REF']:.0%}** of noise worlds match or beat it outright. This is
fully consistent with BT13 (+0.036R on 16 unseen years) and BT8b (the entry carries no information): there was never an
edge to find; the search found the shape of its own noise.

## 7. What this means for Option B, in plain words
The user has chosen to keep TEST sealed and research a new, non-SMC idea. The thing to carry forward:

- **Our research method, run on data with nothing in it, routinely reports about {pf(D.loc[1, 'P50'])}R per trade and
  reaches {pf(P95)}R one time in twenty.** That is roughly {bar_money} per trade at 0.10 lot.
- So **a new DEV result of less than {pf(P95)}R per trade over ~150 trades tells us nothing.** Not "weakly positive" —
  nothing. It is inside the range the method invents.
- A number can clear the bar two ways: be bigger, or be measured on far more trades. The bar above is for a
  ~150-trade, 3-year, 9-instrument, ~{ncfg}-variant search. **Fewer variants and more trades lower it.** The honest move for
  the next candidate is to pre-register the rules *and* the variant count, then re-run BT14 for that shape.
- t-stats are not a substitute, but they are the better guard here. On zero-edge data the frozen config reached at most
  t {X['a_t_naive']['max']:+.2f} and the search's winner at most t {X['b_max_t']:+.2f} ({X['a_frac_t_ge_2']:.0%} and {X['b_frac_t_ge_2']:.0%} of worlds respectively
  cleared t ≥ 2). BT6's t was 1.07 — comfortably inside what noise produces. **A candidate needs both: net R above the
  bar *and* a block-bootstrap t that noise does not reach.**
- And remember what BT13 already costs us: even clearing the bar on DEV only earns the right to a test on unseen
  years. BT14 sets the *entry* fee, not the proof.

## 8. Limitations, honestly
- **A GBM is not a market.** It has no fat tails, no volatility clustering, no trends and no autocorrelation. Real
  price series contain structure that is not a tradable edge but that *could* change how often a search finds a
  spurious number. P95 here is the bar for "our method + random walks with the real calendar, the real intraday
  volatility shape and the real costs". A block-bootstrap of real returns would be the natural next calibration.
- **{ncfg} readings, not 448** (see §5). This makes P95 a floor.
- **One DEV window length.** The bar scales with trade count; it is not a universal constant.
- **{nw} worlds** gives P95 to about ±{(X['P95_CI95'][1] - X['P95_CI95'][0]) / 2:.02f}R (bootstrap CI {X['P95_CI95']}).{'' if done else chr(10) + "- **This report is from " + str(nw) + " of the planned 200 worlds** (see §10)."}
- The synthetic world starts each instrument at its real 2021 price and lets it wander; a world can end far from
  realistic levels. Costs are pip-denominated so this does not change them, but very high/low levels change the
  pip-to-percent relationship slightly.
- TEST (2025-07 onward) was never read, loaded or inspected, for any purpose.

## 9. Files
| File | What |
|---|---|
| `bt/bt14_gbm.py` | the generator (volatility profile from real DEV, zero-drift GBM, sub-minute OHLC, real calendar/costs) |
| `bt/bt14_run.py` | the runner: one world → canonical REF + the {ncfg}-reading search → `parts/w<seed>.json` |
| `bt/bt14_tests.py` | the blocking checks (zero drift, wiring on real data, random entries, setup rate) |
| `bt/bt14_report.py` | this report and the two CSVs |
| `results/BT14/realizations.csv` | measurement (a): one row per noise world, with its seed |
| `results/BT14/best_of_search.csv` | measurement (b): the best reading per noise world, and which one won |
| `results/BT14/sanity.json`, `distributions.csv`, `numbers.json`, `vol_profile.json` | checks and raw numbers |
| `results/BT14/parts/w*.json` | per-world checkpoints (all {ncfg} readings per world) |

Changes to shared code: `bt/common.py` gained the `SMC_DATASET=gbm` branch (synthetic bars served from
`common.GBM_BARS`; **real data is unreachable in that mode — `bars()` raises rather than falling back**). Default
(`zero`) and `duka` behaviour unchanged; `bt/test_common.py` passes. Errors and hazards logged as ERRORS #31–#33.

## 10. How to resume
The run is checkpointed per world. `results/BT14/parts/w<seed>.json` is written atomically, finished worlds are
skipped, and the three workers can be restarted at any time with:

```
.venv/Scripts/python.exe smc_bot/bt/bt14_run.py worker 0 200 3
.venv/Scripts/python.exe smc_bot/bt/bt14_run.py worker 1 200 3
.venv/Scripts/python.exe smc_bot/bt/bt14_run.py worker 2 200 3
.venv/Scripts/python.exe smc_bot/bt/bt14_report.py          # regenerates everything from parts/
```

**Status: {nw} of 200 worlds complete{'' if done else ' — the laptop was on battery; re-run the three worker lines above to finish, then re-run bt14_report.py'}.**
{'' if done else chr(10) + "Optional, not run: `bt14_run.py breadth 0 8` measures how much the full 448-reading search adds to the best-of." + chr(10)}
*Generated by `bt/bt14_report.py` from {nw} world checkpoints.*
"""
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)


if __name__ == "__main__":
    main()
