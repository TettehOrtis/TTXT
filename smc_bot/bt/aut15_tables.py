"""AUT15: build the Part A master table (one row per variant x view) and the counts.  Writes results/AUT15_costcut/partA_*.csv"""
import os
import sys
import json

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import common as C                                                        # noqa: E402
import aut15_select as S                                                  # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT15_costcut")
STG = ["EXPLORE", "VALIDATE", "STRESS"]

#: which saved/rebuilt order file stands for which variant id in a study's autopsy.json (for the reader)
AUTOPSY_ID = {
    "AUT01__R0_baseline": "E_m15_ema21_LIVE_FORM (gold) / R0 baseline", "AUT01__R1_shape": "R1_shape", "AUT01__R2_shape_filter": "R2_shape_filter",
    "AUT01__R3_balanced": "R3_balanced", "AUT01__LIVE_AS_SHIPPED": "LIVE_AS_SHIPPED (STRESS rebuilt by AUT15)",
    "AUT02__R0_raw_M15N20_wick2R": "BASE_M15_N20", "AUT02__R0_raw__wick_2R_M30N20": "BASE_M30_N20", "AUT02__R0_raw__atr_2x2_M30N20": "SHAPE_atr2x2_M30",
    "AUT02__R1_stretched__wick_2R_M30N20": "R1_stretched_M30", "AUT02__R2_stretched_NYoverlap__wick_2R_M30N20": "R2_stretched_NYoverlap_M30",
    "AUT02__R3_stretched_overlap_240__wick_2R_M30N20": "R3_stretched_overlap_240_M30",
    "AUT03__BASE_immediate": "BASE_immediate", "AUT03__BASE_retest": "BASE_retest", "AUT03__BASE_retest_failed_fade": "BASE_retest_failed_fade",
    "AUT03__V1_retest_room": "V1_retest_room", "AUT03__V2_immediate_room": "V2_immediate_room", "AUT03__V3_fade_failed_quiet": "V3_fade_failed_quiet",
    "AUT04__R0_all__autopsy_1p5x1p0": "R0_all_autopsy_shape (stages rebuilt by AUT15)",
    "AUT05__R1_shape": "R1 shape (same signals as P_day)",
    "AUT06__BASELINE_edge_2xATR": "BASELINE_bt2_rule (stages rebuilt by AUT15)", "AUT06__R0_best_0p5x2p0": "R0_raw_retest_best_shape (stages rebuilt by AUT15)",
    "AUT07__FVG_FILL_base": "FVG_FILL (V and S rebuilt by AUT15)",
    "AUT08__BASELINE": "BASELINE", "AUT08__M15_N3_E": "M15_N3 (EXPLORE only)", "AUT08__M15_N5_E": "M15_N5 (EXPLORE only)", "AUT08__H1_N3_E": "H1_N3 (EXPLORE only)",
    "AUT08__H4_N3_E": "H4_N3 (EXPLORE only)", "AUT08__H4_N5_E": "H4_N5 (EXPLORE only)",
    "AUT09__MAP_allhours": "BASE_allhours (random entries, EXPLORE only)", "AUT09__COST_FILTER_promising": "R1_cheap_hours (closest saved file; EXPLORE only)",
    "AUT10__base": "BASELINE", "AUT13__XAUUSD_BASE": "XAUUSD_BASE", "AUT13__EURUSD_BASE": "EURUSD_BASE",
    "AUT14__C1_B": "C1_B", "AUT14__C2_B": "C2_B", "AUT14__C3_B": "C3_B",
}

#: variants listed in an autopsy.json for which no orders were saved and no unedited builder could be run
NOT_BUILT = [
    ("AUT01", "E_m15_ema9", "gold", 3865, -3.547), ("AUT01", "E_m5_ema21", "gold", 3641, -2.932), ("AUT01", "E_m5_ema9", "gold", 4829, -3.662),
    ("AUT01", "DESCRIBED_BODY_HOLD", "gold", 13, -12.754),
    ("AUT05", "P_day", "gold", 10363, -3.796), ("AUT05", "P_ldn", "gold", 9960, -3.879), ("AUT05", "P_ny", "gold", 10283, -4.776),
    ("AUT05", "B_1sig", "gold", 15572, -4.104), ("AUT05", "B_2sig", "gold", 9821, -4.236),
    ("AUT09", "BASE_allhours_3.0T1.0S", "all", 1207057, -3.066), ("AUT09", "AVOID_dearest_quarter", "all", 256295, -3.195),
    ("AUT09", "R3_best6_cells", "all", 66520, -1.813),
]


def stage_table():
    t = pd.read_csv(os.path.join(OUT, "partA_stage_table_all.csv"))
    a11 = S.a11_table()
    return pd.concat([t, a11], ignore_index=True)


def main():
    tab = stage_table()
    tab["key"] = tab["key"].replace({"AUT07__FVG_FILL_base_VS": "AUT07__FVG_FILL_base", "AUT01__LIVE_AS_SHIPPED_STRESS": "AUT01__LIVE_AS_SHIPPED"})
    tab["n"] = tab["n"].fillna(0)
    tab = tab.sort_values("n", ascending=False).drop_duplicates(["key", "view", "stage"])
    marks = pd.read_csv(os.path.join(OUT, "partA_marks.csv"))
    rows = []
    for (key, view), g in tab.groupby(["key", "view"]):
        g = g.set_index("stage").reindex(STG)
        m = marks[(marks.key == key) & (marks.view == view)].iloc[0]
        study = key.split("__")[0]
        lab = key.split("__", 1)[1]
        r = dict(study=study, variant=lab, autopsy_id=AUTOPSY_ID.get(key, ""), instruments=view, headline_view=bool(m.headline),
                 duplicate_of_other_row=bool(m.duplicate), stages_measured=int(m.stages_measured))
        for st, ab in zip(STG, ("E", "V", "S")):
            r["trades_" + ab] = int(g.n[st]) if pd.notna(g.n[st]) else 0
            r["edge_before_cost_usd_" + ab] = g.edge0[st]
            r["cost_paid_by_study_usd_" + ab] = g.paid_by_study[st]
            r["cost_in_aut15_world_usd_" + ab] = g.paid_my_env[st]
            r["net_in_aut15_world_usd_" + ab] = g.net1[st]
            r["net_as_study_reported_usd_" + ab] = g.saved_net[st]
            r["buy_edge_before_cost_usd_" + ab] = g.buy0[st]
            r["sell_edge_before_cost_usd_" + ab] = g.sell0[st]
        r["worst_stage_breakeven_cost_usd"] = m.worst_edge0
        r["positive_before_cost_in_all_three"] = bool(m.pos_all3)
        r["at_least_500_trades_each_stage"] = bool(m.n500_all3)
        r["buys_and_sells_both_nonneg"] = bool(m.buy_nonneg_all and m.sell_nonneg_all)
        r["worst_stage_edge_over_cost"] = m.worst_ratio
        rows.append(r)
    d = pd.DataFrame(rows).sort_values(["study", "variant", "instruments"]).reset_index(drop=True)
    d.to_csv(os.path.join(OUT, "partA_all_variants.csv"), index=False)
    nb = pd.DataFrame(NOT_BUILT, columns=["study", "variant", "instruments", "explore_trades", "explore_net_usd_as_reported"])
    nb.to_csv(os.path.join(OUT, "partA_not_built.csv"), index=False)
    h = d[d.headline_view & ~d.duplicate_of_other_row]
    cnt = dict(headline_variants_with_orders=int(len(h)), per_study=h.groupby("study").size().to_dict(),
               measured_in_all_three_stages=int((h.stages_measured == 3).sum()),
               edge_before_cost_positive_in_all_three=int(h.positive_before_cost_in_all_three.sum()),
               plus_at_least_500_trades_each_stage=int((h.positive_before_cost_in_all_three & h.at_least_500_trades_each_stage).sum()),
               clean_both_sides=int((h.positive_before_cost_in_all_three & h.at_least_500_trades_each_stage & h.buys_and_sells_both_nonneg).sum()),
               positive_all_three_but_under_500_trades=int((h.positive_before_cost_in_all_three & ~h.at_least_500_trades_each_stage).sum()),
               not_built_variants=int(len(nb)),
               net_positive_at_todays_cost_in_all_three=int(((h.net_in_aut15_world_usd_E > 0) & (h.net_in_aut15_world_usd_V > 0) & (h.net_in_aut15_world_usd_S > 0)).sum()))
    json.dump(cnt, open(os.path.join(OUT, "partA_counts.json"), "w"), indent=1)
    print(json.dumps(cnt, indent=1))
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(h[(h.net_in_aut15_world_usd_E > 0) & (h.net_in_aut15_world_usd_V > 0) & (h.net_in_aut15_world_usd_S > 0)][["study", "variant", "instruments", "net_in_aut15_world_usd_E", "net_in_aut15_world_usd_V", "net_in_aut15_world_usd_S"]])


if __name__ == "__main__":
    main()
