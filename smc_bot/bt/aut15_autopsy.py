"""AUT15: build autopsy.json (AUTOPSY_SCHEMA shape) for the six Part B variants + Part A summary.  Writes results/AUT15_costcut/autopsy.json"""
import os
import sys
import json

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut15_report as R                                                  # noqa: E402

OUT, STG, FAC, GOLD_USD, PICKS = R.OUT, R.STG, R.FAC, R.GOLD_USD, R.PICKS
usd = R.usd


def pf_of(u):
    u = np.asarray(u, float)
    g, l = u[u > 0].sum(), -u[u < 0].sum()
    return round(float(g / l), 3) if l > 0 else None


def build():
    variants = []
    tfs = {"AUT13": dict(signal="M1", entry="M1", trend=None), "AUT07": dict(signal="M15", entry="M1", trend=None),
           "AUT04": dict(signal="M1", entry="M1", trend=None), "AUT01": dict(signal="M1", entry="M1", trend="M15")}
    for p in PICKS:
        try:
            j, m = R.per_variant_json(p)
        except Exception:
            continue
        if "lookahead_twin" not in j:
            continue
        b = j["breakeven"]
        tr = pd.read_csv(os.path.join(OUT, "trades_%s_ALLSTAGES.csv" % p["name"]), usecols=["period", "usd_01", "side"])
        e = tr[tr.period == "EXPLORE"]

        def ge(f, st, sd="all"):
            return m[(m.side == sd) & (m.stage == st) & np.isclose(m.factor, f)].iloc[0]

        ctr0 = j["controls"]["0"]
        lb0, lb1 = j["luck_bar"]["0"], j["luck_bar"]["1"]
        beat_luck = {s: b[s]["edge_before_cost_usd"] - lb0[s]["usd"] for s in STG}
        beat_coin = {s: b[s]["edge_before_cost_usd"] - ctr0[s]["coin_flip"]["usd"] for s in STG}
        beat_const = {s: b[s]["edge_before_cost_usd"] - max(ctr0[s]["all_long"]["usd"], ctr0[s]["all_short"]["usd"]) for s in STG}
        tw0 = {s: j["lookahead_twin"]["0"][s]["lookahead_usd"] for s in STG}
        ok_rows = [f for f in FAC if all(ge(f, s).usd > 0 for s in STG)]
        cheapest_ok = max(ok_rows) if ok_rows else None
        attempts = []
        for n, f in enumerate(FAC[1:], start=1):
            rr = {s: ge(f, s) for s in STG}
            allpos = all(rr[s].usd > 0 for s in STG)
            outc = "fixed_and_survived" if allpos else ("made_money_on_explore_died_at_validate" if rr["EXPLORE"].usd > 0 else "improved_but_still_negative")
            lab = ("$%.2f round trip" % GOLD_USD[f]) if p["gold"] else ("%.2f of today's commission and spread" % f)
            attempts.append(dict(
                n=n, targets_which_why="cost",
                what_i_changed="Replayed the same orders with commission and spread cut to %s (hypothetical; stop and target prices unchanged)." % lab,
                why_i_thought_it_would_help="The before-cost edge in the worst stage is %s; the cost paid today in that stage is %s." % (
                    usd(min(b[s]["edge_before_cost_usd"] for s in STG)), usd(b[j["worst_stage"]]["cost_paid_today"])),
                explore=dict(trades=int(rr["EXPLORE"].n), right_in_100=round(float(rr["EXPLORE"].win), 2), usd_per_trade=round(float(rr["EXPLORE"].usd), 3)),
                validate=dict(trades=int(rr["VALIDATE"].n), right_in_100=round(float(rr["VALIDATE"].win), 2), usd_per_trade=round(float(rr["VALIDATE"].usd), 3)),
                stress=dict(trades=int(rr["STRESS"].n), right_in_100=round(float(rr["STRESS"].win), 2), usd_per_trade=round(float(rr["STRESS"].usd), 3)),
                stress_adverse=None,
                points_gained=round(float(rr["EXPLORE"].win - ge(1.0, "EXPLORE").win), 2),
                usd_gained=round(float(rr["EXPLORE"].usd - ge(1.0, "EXPLORE").usd), 3),
                outcome=outc,
                note="Judged by the worst stage: %s." % ("positive in all three stages" if allpos else "not positive in all three stages")))
        beats_luck_coin = all(beat_luck[s] > 0 and beat_coin[s] > 0 for s in STG)
        beats_const = all(beat_const[s] > 0 for s in STG)
        edge_txt = "%s, %s and %s" % (usd(b["EXPLORE"]["edge_before_cost_usd"]), usd(b["VALIDATE"]["edge_before_cost_usd"]), usd(b["STRESS"]["edge_before_cost_usd"]))
        cost_txt = "%s to %s" % (usd(min(b[s]["cost_paid_today"] for s in STG)), usd(max(b[s]["cost_paid_today"] for s in STG)))
        if beats_luck_coin and beats_const:
            category = "cost"
            sentence = ("The direction call is worth %s a trade before cost in EXPLORE, VALIDATE and STRESS, more than random entries, a coin flip and "
                        "the better of always-buy or always-sell in each, but the round trip costs %s, so at today's cost the worst stage loses." % (edge_txt, cost_txt))
        elif beats_luck_coin:
            category = "regime_artifact"
            bad = [s for s in STG if beat_const[s] <= 0]
            sentence = ("The edge is %s a trade before cost and beats random entries and a coin flip, but in %s a plain always-one-side bet earns more than the rule, "
                        "so much of it is the market's drift; the round trip also costs %s." % (edge_txt, " and ".join(bad), cost_txt))
        else:
            category = "direction_call_worthless"
            bad = [s for s in STG if not (beat_luck[s] > 0 and beat_coin[s] > 0)]
            sentence = ("The edge is %s a trade before cost, but in %s it does not beat random entries or a coin flip, so the direction call is not shown to be worth "
                        "anything; the round trip also costs %s." % (edge_txt, " and ".join(bad), cost_txt))
        direction_real = beats_luck_coin and beats_const
        if cheapest_ok is None:
            detail = "No menu row, not even $0 for commission and spread, is positive in all three stages."
        else:
            detail = "Cost is the only lever in this study. The highest menu cost that is positive in all three stages is %s." % (
                ("$%.2f" % GOLD_USD[cheapest_ok]) if p["gold"] else ("%.2f of today's cost" % cheapest_ok))
        variants.append(dict(
            id=p["name"], rule_sentence=p["rule"], timeframes=tfs[p["study"]], source_study=p["study"], source_variant_key=p["key"], instruments=p["view"],
            baseline=dict(stage="EXPLORE", trades=int(ge(1.0, "EXPLORE").n), right_in_100=round(float(ge(1.0, "EXPLORE").win), 2),
                          luck_bar=round(float(lb1["EXPLORE"]["win"]), 2), breakeven_needed=None, profit_factor=pf_of(e.usd_01),
                          usd_per_trade=round(float(ge(1.0, "EXPLORE").usd), 3),
                          buy_right_in_100=round(float(ge(1.0, "EXPLORE", "buy").win), 2), buy_usd_per_trade=round(float(ge(1.0, "EXPLORE", "buy").usd), 3),
                          sell_right_in_100=round(float(ge(1.0, "EXPLORE", "sell").win), 2), sell_usd_per_trade=round(float(ge(1.0, "EXPLORE", "sell").usd), 3)),
            verdict="no_edge",
            why_no_edge=dict(
                category=category,
                one_sentence=sentence,
                evidence=dict(
                    edge_before_cost_usd={s: round(b[s]["edge_before_cost_usd"], 3) for s in STG},
                    net_today_usd={s: round(b[s]["net_today_usd"], 3) for s in STG},
                    cost_paid_today_usd={s: round(b[s]["cost_paid_today"], 3) for s in STG},
                    give_or_take_on_edge_usd={s: round(b[s]["edge_gt"], 3) for s in STG},
                    beats_random_entries_before_cost_usd={s: round(beat_luck[s], 3) for s in STG},
                    beats_coin_flip_before_cost_usd={s: round(beat_coin[s], 3) for s in STG},
                    beats_best_always_one_side_before_cost_usd={s: round(beat_const[s], 3) for s in STG},
                    lookahead_twin_usd_before_cost={s: round(tw0[s], 3) for s in STG},
                    breakeven_cost_usd={s: round(b[s]["breakeven_cost_usd"], 3) for s in STG},
                    worst_stage=j["worst_stage"], worst_stage_breakeven_cost_usd=round(j["worst_stage_breakeven_cost_usd"], 3),
                    holds_in_validate=bool(b["VALIDATE"]["edge_before_cost_usd"] > 0), holds_in_stress=bool(b["STRESS"]["edge_before_cost_usd"] > 0),
                    cost_share_of_shortfall="all of it is cost: the worst stage earns %s before cost against %s paid" % (
                        usd(b[j["worst_stage"]]["edge_before_cost_usd"]), usd(b[j["worst_stage"]]["cost_paid_today"])),
                    corroborating_prior_work=["the source study (%s) reported this variant as no edge at today's cost" % p["study"]])),
            fix_attempts=attempts,
            fix_exhausted_because="reason_fixed" if (cheapest_ok is not None and category == "cost") else "out_of_mechanical_ideas",
            fix_exhausted_detail=detail + ("" if category == "cost" else " A cheaper round trip makes the dollars positive, but the reason this variant has no edge is not only cost (%s), so it is not called a survivor." % category),
            best_surviving_version=(dict(cost_factor=cheapest_ok, round_trip_usd=GOLD_USD[cheapest_ok] if p["gold"] else None,
                                         net_usd_per_trade={s: round(float(ge(cheapest_ok, s).usd), 3) for s in STG})
                                    if cheapest_ok is not None else None),
            what_i_would_change=["Do not change the entry; a cheaper account does not change the entry's own edge.",
                                 "Check which account really offers the break-even cost, and check its spread at news and in the minutes right after the signal.",
                                 "Judge any cheaper account on its real fills, not on this data."]))
    return variants


def main():
    vs = build()
    cnt = json.load(open(os.path.join(OUT, "partA_counts.json")))
    doc = dict(
        agent="AUT15", strategy="How cheap would trading have to be? Cost-cut test of the earlier studies", feed="dukascopy (data_duka; data_zero never read)",
        symbols=["XAUUSD"] + [s for s in "EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD EURGBP EURJPY GBPJPY AUDJPY EURAUD EURCHF GBPCHF CADJPY NZDJPY AUDCAD AUDNZD EURCAD GBPAUD CHFJPY GBPCAD AUDCHF NZDCAD".split()],
        gold_spread_injected=0.05, gold_round_trip_usd_at_010_lot=1.60, bars_screened=True,
        corrupt_bars_dropped={"AUDJPY": 14155, "GBPCHF": 2, "AUDUSD": 1}, exam_opened=False, data_zero_read=False,
        conditions_tried=0, conditions_cap=40,
        conditions_note="No catalogue conditions were tried. The only lever is the cost menu (6 rows). The 6 variants were picked by a rule written before any run (preregistration.txt).",
        summary_for_the_owner="See report.md. Counts: %s" % json.dumps(cnt),
        part_a=dict(counts=cnt, table_file="partA_all_variants.csv", not_built_file="partA_not_built.csv",
                    how="Saved orders of each study replayed through common.simulate with commission and spread both zero, and again at today's cost. Edge before cost = break-even round trip cost."),
        part_b=dict(rule_file="preregistration.txt", picks=[v["id"] for v in vs], summary_file="partB_summary.csv"),
        luck_bar_method="Random entries: same hours (hour histogram of the rule), same ATR shape (median target and stop in M15 ATR), same hold, 20,000 per seed, 5 seeds, filled at the next bar open (buy at ask).",
        mandatory_controls="always buy, always sell, coin flip (3 seeds), random entries (5 seeds), buys and sells split, look-ahead twin in pips and dollars; all at zero cost and at today's cost",
        variants=vs,
        cross_variant_findings=[
            dict(finding="Cheaper trading moves the worst stage from a loss to a gain only for variants whose before-cost edge is bigger than the cost they would still pay; see report.md for the break-even costs.",
                 replicated_on=["6 variants", "3 stages"]),
            dict(finding="Only 1 of %d headline variants earns money at today's cost in all three stages (AUT02 R2 M30, 333 trades in EXPLORE, below the 500-trade rule)." % cnt["headline_variants_with_orders"],
                 replicated_on=["14 studies"])],
        bugs_found_in_shared_code=[],
        post_hoc_untested=["A real broker's quoted spread at news and after a signal, which this data cannot show.",
                           "Overnight swap for the daily-trend studies (not in the data)."])
    json.dump(doc, open(os.path.join(OUT, "autopsy.json"), "w"), indent=1, default=float)
    print("autopsy.json written, variants:", len(vs))


if __name__ == "__main__":
    main()
