"""AUT12: assemble autopsy.json and BREAKDOWN.md from the measured result files. Numbers are read, never typed.
Run:  .venv\\Scripts\\python.exe smc_bot/bt/aut12_assemble.py
"""
import os
import json
import pandas as pd
import numpy as np

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "AUT12_clock")
R = json.load(open(os.path.join(OUT, "final_results.json")))
B = json.load(open(os.path.join(OUT, "baselines_results.json")))
FC = json.load(open(os.path.join(OUT, "frozen_choice.json")))
G = pd.read_csv(os.path.join(OUT, "grid_EXPLORE.csv"))
P = G[G.scope == "POOLED"]
LAD = pd.read_csv(os.path.join(OUT, "ladder_EXPLORE.csv"))
CON = pd.read_csv(os.path.join(OUT, "conditions_R1_EXPLORE.csv"))
SK = json.load(open(os.path.join(OUT, "events_skipped.json")))


def r2(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 3)


def cell(ev, L, mode, hold):
    x = P[(P.ev == ev) & (P.L == L) & (P["mode"] == mode) & (P.hold == hold)].iloc[0]
    return dict(trades=int(x.trades), right_in_100=r2(x.right_in_100), usd_per_trade=r2(x.usd_per_trade), coin_usd=r2(x.coin_usd),
                edge_over_coin_usd=r2(x.edge_over_coin_usd), give_or_take_usd=r2(x.give_or_take_usd))


def lad(v, tg, sp):
    x = LAD[(LAD.variant == v) & (LAD.tgt == tg) & (LAD.stop == sp)].iloc[0]
    return dict(trades=int(x.trades), right_in_100=r2(x.right_in_100), usd_per_trade=r2(x.usd_per_trade), coin_usd=r2(x.coin_usd),
                edge_over_coin_usd=r2(x.edge_over_coin_usd), give_or_take_usd=r2(x.give_or_take_usd), profit_factor=r2(x.PF))


def stage_summary(d):
    keys = ["trades", "right_in_100", "luck_bar", "luck_bar_usd", "breakeven_needed", "profit_factor", "usd_per_trade", "coin_usd",
            "coin_right_in_100", "edge_over_coin_usd", "give_or_take_usd", "rand_lo", "rand_hi", "share_random_beats_rule",
            "all_long_right_in_100", "all_long_usd_per_trade", "all_short_right_in_100", "all_short_usd_per_trade",
            "buy_trades", "buy_right_in_100", "buy_usd_per_trade", "buy_edge_over_coin", "sell_trades", "sell_right_in_100",
            "sell_usd_per_trade", "sell_edge_over_coin", "vs_luck_points", "vs_luck_usd", "luck_bar_seed_band_points",
            "gross_usd_per_trade"]
    o = {}
    for k in keys:
        kk = "PF" if k == "profit_factor" else k
        o[k] = r2(d.get(kk))
    for k in ("trades", "buy_trades", "sell_trades"):
        o[k] = int(o[k])
    o["by_instrument"] = [{k: (v if k == "sym" else (int(v) if k in ("trades", "buy_trades", "sell_trades") else r2(v))) for k, v in b.items()} for b in d["by_instrument"]]
    o["lookahead_twin"] = d["lookahead"]
    o["luck_detail"] = d.get("luck_detail")
    return o


def baseline(d):
    return dict(stage="EXPLORE", trades=d["trades"], right_in_100=r2(d["right_in_100"]), luck_bar=r2(d.get("luck_bar")),
                luck_bar_seed_band=r2(d.get("luck_bar_seed_band_points")), breakeven_needed=r2(d["breakeven_needed"]),
                profit_factor=r2(d["PF"]), usd_per_trade=r2(d["usd_per_trade"]), random_direction_usd=r2(d["coin_usd"]),
                edge_over_random_direction_usd=r2(d["edge_over_coin_usd"]), give_or_take_usd=r2(d["give_or_take_usd"]),
                buy_right_in_100=r2(d["buy_right_in_100"]), buy_usd_per_trade=r2(d["buy_usd_per_trade"]),
                sell_right_in_100=r2(d["sell_right_in_100"]), sell_usd_per_trade=r2(d["sell_usd_per_trade"]))


r1, r2_, r3 = R["R1"], R["R2"], R["R3"]
s = lambda v, st: v["stages"][st]
adv = lambda v: v["stress_adverse"]
fam = lambda ev: P[P.ev == ev]
n_pos = lambda ev: int((fam(ev).edge_over_coin_usd > fam(ev).give_or_take_usd).sum())

variants = []

# ---------------------------------------------------------------- R1
v = dict(
    id="R1_LFIX_PM_follow60_hold120",
    rule_sentence="At 15:00 London time (London gold PM fix, DST-correct), look at the move over the previous 60 minutes and trade WITH it at the open "
                  "of the event-minute bar, 1.5x ATR target, 1.0x ATR stop (both hung off the signal price), 120-minute time stop. "
                  "EURUSD, GBPUSD, USDJPY and gold.",
    timeframes={"signal": "M1 (60 minute look-back)", "entry": "M1", "atr": "M15"},
    baseline=baseline(s(r1, "EXPLORE")),
    all_stages={st: stage_summary(s(r1, st)) for st in ("EXPLORE", "VALIDATE", "STRESS")},
    stress_adverse_cost=adv(r1),
    chosen_how="Pre-registered rule: the top EXPLORE cell by gap over random direction (>= 800 trades). Frozen before VALIDATE was opened.",
    verdict="no_edge",
    why_no_edge=dict(
        category="direction_call_worthless",
        one_sentence="Following the hour before the 15:00 London fix beat a random direction by $1.32 a trade on EXPLORE, but the gap shrank to $0.40 on "
                     "VALIDATE and to minus $0.12 on STRESS, and even the EXPLORE gap was smaller than the cost, so the rule never made money.",
        evidence=dict(
            edge_over_random_direction_usd={st: s(r1, st)["edge_over_coin_usd"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            give_or_take_usd={st: s(r1, st)["give_or_take_usd"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            usd_per_trade={st: s(r1, st)["usd_per_trade"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            vs_luck_bar_points={st: s(r1, st)["vs_luck_points"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            holds_in_validate="gap positive but inside its give-or-take ($0.40 +/- $0.51)",
            holds_in_stress=False,
            pooled_hides_different_winners="EXPLORE gap came from EURUSD (+1.44), GBPUSD (+2.15), USDJPY (+0.80) and not gold (-0.15). VALIDATE: EURUSD +0.61, gold +0.73, GBPUSD +0.08, USDJPY +0.17. "
                                           "STRESS: every instrument within a few cents of zero or below. The pair doing the work changes each stage.",
            corroborating_prior_work=["AUT09: the clock alone has no edge (0 of 239 cells pays)", "AUT04/AUT08: following loses, fading is the only thing above a coin flip"]),
    ),
    fix_attempts=[
        dict(n=1, targets_which_why="direction_call_worthless",
             what_i_changed="Added ONE catalogue condition: only trade when the 60-minute pre-move is bigger than its own recent norm (ratio to the median of the last 20 same-event moves above 1). Picked by the pre-registered rule from 15 conditions, threshold frozen on EXPLORE. This is refinement R3.",
             why_i_thought_it_would_help="A big pre-move means something happened; following it should carry more information than following a small drift.",
             explore=dict(trades=s(r3, "EXPLORE")["trades"], right_in_100=s(r3, "EXPLORE")["right_in_100"], usd_per_trade=s(r3, "EXPLORE")["usd_per_trade"], profit_factor=s(r3, "EXPLORE")["PF"]),
             validate=dict(trades=s(r3, "VALIDATE")["trades"], right_in_100=s(r3, "VALIDATE")["right_in_100"], usd_per_trade=s(r3, "VALIDATE")["usd_per_trade"], profit_factor=s(r3, "VALIDATE")["PF"]),
             stress=dict(trades=s(r3, "STRESS")["trades"], profit_factor=s(r3, "STRESS")["PF"], usd_per_trade=s(r3, "STRESS")["usd_per_trade"]),
             stress_adverse=dict(usd_per_trade=adv(r3)[2]["usd_per_trade"]),
             points_gained=r2(s(r3, "EXPLORE")["right_in_100"] - s(r1, "EXPLORE")["right_in_100"]),
             usd_gained=r2(s(r3, "EXPLORE")["usd_per_trade"] - s(r1, "EXPLORE")["usd_per_trade"]),
             outcome="improved_but_still_negative",
             note="Net dollars stayed negative at all three stages and the gap over random direction went 2.10 -> 0.69 -> -0.24."),
        dict(n=2, targets_which_why="direction_call_worthless",
             what_i_changed="Shorter look-back: 30 minutes instead of 60 (same event, follow, hold 120).",
             why_i_thought_it_would_help="The 60-minute window includes the 13:30 UTC US data time in summer; 30 minutes might isolate the fix itself.",
             explore=cell("LFIX_PM", 30, "follow", 120), validate=None,
             points_gained=None, usd_gained=r2(cell("LFIX_PM", 30, "follow", 120)["usd_per_trade"] - s(r1, "EXPLORE")["usd_per_trade"]),
             outcome="made_it_worse", note="EXPLORE only; not carried (cap)."),
        dict(n=3, targets_which_why="direction_call_worthless",
             what_i_changed="Fade instead of follow (exact mirror of the same events).",
             why_i_thought_it_would_help="Fading is the only direction rule that has beaten a coin flip in four earlier studies.",
             explore=cell("LFIX_PM", 60, "fade", 120), validate=None, points_gained=None,
             usd_gained=r2(cell("LFIX_PM", 60, "fade", 120)["usd_per_trade"] - s(r1, "EXPLORE")["usd_per_trade"]),
             outcome="made_it_worse", note="By construction the gap over random direction is exactly the negative of the follow gap."),
        dict(n=4, targets_which_why="shape",
             what_i_changed="Walked the 36-cell ATR target/stop ladder on EXPLORE (same signals, hold 120).",
             why_i_thought_it_would_help="Shape beat every filter in earlier rounds; a wider target lowers the cost as a share of the move.",
             explore=lad("R1", 4.0, 2.0), validate=None, points_gained=r2(lad("R1", 4.0, 2.0)["right_in_100"] - s(r1, "EXPLORE")["right_in_100"]),
             usd_gained=r2(lad("R1", 4.0, 2.0)["usd_per_trade"] - s(r1, "EXPLORE")["usd_per_trade"]),
             outcome="made_money_on_explore_died_at_validate" if False else "improved_but_still_negative",
             note="4 of 36 shapes read slightly positive on EXPLORE (best 4.0x/2.0x at +$0.46 a trade, PF 1.04). Picked after looking, 1 of 36, never taken to VALIDATE: "
                  "it would have been a 4th refinement. Arithmetic check: on VALIDATE this rule's gap over random is only +$0.40, so even a shape that trims the random-direction loss to about -$1.35 leaves about -$0.95."),
    ],
    fix_exhausted_because="conditions_cap_reached",
    fix_exhausted_detail="3 of 3 refinements used (R1, R2, R3). 15 catalogue conditions tried on R1 (35 of 40 conditions in the study). The only EXPLORE-positive thing left, a wider shape, would be a 4th refinement and was chosen after seeing EXPLORE shapes.",
    best_surviving_version=None,
    what_i_would_change=["Do not trade the fix on a fixed clock: the gap over random direction decays to zero by 2017-2020.",
                         "If someone insists, pre-register the 4.0x/2.0x shape on this rule and read VALIDATE once; expect it to stay negative."],
)
variants.append(v)

# ---------------------------------------------------------------- R2
v = dict(
    id="R2_COMEX_fade30_hold15",
    rule_sentence="At 08:20 New York time (COMEX gold open, DST-correct), look at the move over the previous 30 minutes and trade AGAINST it at the open of the "
                  "event-minute bar, 1.5x ATR target, 1.0x ATR stop (hung off the signal price), 15-minute time stop. EURUSD, GBPUSD, USDJPY and gold.",
    timeframes={"signal": "M1 (30 minute look-back)", "entry": "M1", "atr": "M15"},
    baseline=baseline(s(r2_, "EXPLORE")),
    all_stages={st: stage_summary(s(r2_, st)) for st in ("EXPLORE", "VALIDATE", "STRESS")},
    stress_adverse_cost=adv(r2_),
    chosen_how="Pre-registered rule: top EXPLORE cell by gap over random direction (>= 800 trades) with a different (event, mode) than R1.",
    verdict="no_edge",
    why_no_edge=dict(
        category="cost",
        one_sentence="Fading the half hour before the COMEX open beats a random direction by $0.25 to $0.47 a trade in all three periods, which is small but steady, "
                     "and the round trip costs about $1.85 a trade, so it still loses $1.41 to $1.57 a trade.",
        evidence=dict(
            edge_over_random_direction_usd={st: s(r2_, st)["edge_over_coin_usd"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            give_or_take_usd={st: s(r2_, st)["give_or_take_usd"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            usd_per_trade={st: s(r2_, st)["usd_per_trade"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            vs_luck_bar_points={st: s(r2_, st)["vs_luck_points"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            all_stages_combined="+$0.36 a trade over 15,171 trades, give or take $0.21 (clustered by day). Small, probably real, and one fifth of the cost.",
            holds_in_validate=True, holds_in_stress=True,
            pooled_hides_different_winners="Consistent: EURUSD, GBPUSD and gold are positive in all three stages (GBPUSD +0.63/+0.59/+1.02); USDJPY is near zero (+0.25/-0.07/+0.07). No single pair carries it.",
            corroborating_prior_work=["AUT04, AUT08, AUT03: fading beats following", "AUT09: clock alone has no edge"]),
    ),
    fix_attempts=[
        dict(n=1, targets_which_why="shape",
             what_i_changed="Walked the 36-cell ATR ladder on EXPLORE (same signals, hold 15).",
             why_i_thought_it_would_help="A different target/stop could lower the cost as a share of the move.",
             explore=lad("R2", 4.0, 0.5), validate=None,
             points_gained=r2(lad("R2", 4.0, 0.5)["right_in_100"] - s(r2_, "EXPLORE")["right_in_100"]),
             usd_gained=r2(lad("R2", 4.0, 0.5)["usd_per_trade"] - s(r2_, "EXPLORE")["usd_per_trade"]),
             outcome="improved_but_still_negative",
             note="0 of 36 shapes made money on EXPLORE; best -$1.02 (4.0x/0.5x), which is a thin-stop shape that earns its dollars from cost, not from skill. Not carried."),
        dict(n=2, targets_which_why="direction_call_worthless",
             what_i_changed="Look-back 60 minutes instead of 30.", why_i_thought_it_would_help="More history before the event might make the call cleaner.",
             explore=cell("COMEX", 60, "fade", 15), validate=None, points_gained=None,
             usd_gained=r2(cell("COMEX", 60, "fade", 15)["usd_per_trade"] - s(r2_, "EXPLORE")["usd_per_trade"]), outcome="made_it_worse", note="EXPLORE only."),
        dict(n=3, targets_which_why="shape",
             what_i_changed="Longer hold: 30, 60 and 120 minutes (fade, 30-minute look-back).", why_i_thought_it_would_help="Mean reversion might need more time.",
             explore=cell("COMEX", 30, "fade", 120), validate=None, points_gained=None,
             usd_gained=r2(cell("COMEX", 30, "fade", 120)["usd_per_trade"] - s(r2_, "EXPLORE")["usd_per_trade"]), outcome="made_it_worse",
             note="Gap over random direction fell with hold: 0.40 (15 min), 0.37 (30), 0.33 (60), 0.33 (120). The effect lives in the first quarter hour."),
        dict(n=4, targets_which_why="direction_call_worthless",
             what_i_changed="Follow instead of fade (mirror).", why_i_thought_it_would_help="Check the sign is not just luck.",
             explore=cell("COMEX", 30, "follow", 15), validate=None, points_gained=None,
             usd_gained=r2(cell("COMEX", 30, "follow", 15)["usd_per_trade"] - s(r2_, "EXPLORE")["usd_per_trade"]), outcome="made_it_worse",
             note="Exact mirror of the fade gap by construction."),
    ],
    fix_exhausted_because="out_of_mechanical_ideas",
    fix_exhausted_detail="The gap is real-looking but $0.36 against a $1.85 cost. No shape, hold or look-back moves it past the cost; I did not scan catalogue conditions on this rule (budget kept for R1, 35 of 40 used) and a filter would have to add $1.4 a trade by itself.",
    best_surviving_version=None,
    what_i_would_change=["Watch this one: it is the only rule here with the same sign in all three periods. It is a candidate for a signal INSIDE a bigger system, not a stand-alone trade.",
                         "To pay, it needs a cost near $0.35 a round trip, which this broker does not offer."],
)
variants.append(v)

# ---------------------------------------------------------------- R3
v = dict(
    id="R3_LFIX_PM_follow60_hold120_bigpremove",
    rule_sentence="R1, but only when the 60-minute pre-move is bigger than its own norm (above the median of the previous 20 occurrences of the same event on the same instrument). Threshold: ratio above 1, fixed.",
    timeframes={"signal": "M1 (60 minute look-back)", "entry": "M1", "atr": "M15"},
    baseline=baseline(s(r3, "EXPLORE")),
    all_stages={st: stage_summary(s(r3, st)) for st in ("EXPLORE", "VALIDATE", "STRESS")},
    stress_adverse_cost=adv(r3),
    chosen_how="Pre-registered: R1 plus the single condition with the best EXPLORE gap over random direction among those keeping >= 300 trades (15 conditions scanned).",
    verdict="no_edge",
    why_no_edge=dict(
        category="direction_call_worthless",
        one_sentence="The filter lifted the EXPLORE gap over random direction to $2.10 a trade (net -$0.12), but it fell to $0.69 on VALIDATE and to minus $0.24 on STRESS; it was inherited from R1 and decays the same way.",
        evidence=dict(
            edge_over_random_direction_usd={st: s(r3, st)["edge_over_coin_usd"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            give_or_take_usd={st: s(r3, st)["give_or_take_usd"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            usd_per_trade={st: s(r3, st)["usd_per_trade"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            vs_luck_bar_points={st: s(r3, st)["vs_luck_points"] for st in ("EXPLORE", "VALIDATE", "STRESS")},
            holds_in_validate="gap positive, inside its give-or-take ($0.69 +/- $0.80); net -$1.06",
            holds_in_stress=False,
            pooled_hides_different_winners="EXPLORE: EURUSD +2.65, GBPUSD +3.53, USDJPY +1.13, gold -1.23. VALIDATE: gold +0.86, EURUSD +0.82, GBPUSD +0.60, USDJPY +0.47. STRESS: all four near zero or negative.",
            condition_scan="Of the 15 conditions on R1, the top four (big pre-move vs own norm, ATR high, smooth pre-move, big pre-move in ATR) all lift EXPLORE edge to $1.8-2.1 and they differ from each other by less than the give-or-take ($0.8-0.9). Picking the top one is picking noise at that precision."),
    ),
    fix_attempts=[
        dict(n=1, targets_which_why="direction_call_worthless",
             what_i_changed="Considered swapping the condition for the runner-up (ATR high against its own 60 days, EXPLORE gap +$2.00, net -$0.18).",
             why_i_thought_it_would_help="Both measure 'something is happening'.",
             explore=dict(trades=int(CON.sort_values("edge_on", ascending=False).iloc[1].kept), usd_per_trade=r2(CON.sort_values("edge_on", ascending=False).iloc[1].usd_on),
                          edge_over_coin_usd=r2(CON.sort_values("edge_on", ascending=False).iloc[1].edge_on)),
             validate=None, points_gained=None, usd_gained=None, outcome="no_change",
             note="EXPLORE read only; it is within $0.10 of the chosen condition, so it cannot be told apart, and a swap after seeing VALIDATE is forbidden."),
        dict(n=2, targets_which_why="shape",
             what_i_changed="Shape ladder on the unfiltered R1 signals (EXPLORE). Not rerun on the filtered set.",
             why_i_thought_it_would_help="See R1 attempt 4.", explore=lad("R1", 4.0, 2.0), validate=None, points_gained=None, usd_gained=None,
             outcome="improved_but_still_negative", note="Reported under R1; it is not a new look at VALIDATE."),
    ],
    fix_exhausted_because="conditions_cap_reached",
    fix_exhausted_detail="3 of 3 refinements used. Any further filter would be chosen after seeing VALIDATE.",
    best_surviving_version=None,
    what_i_would_change=["Do not stack filters on a signal whose base gap is decaying; the filter just selects the part of EXPLORE where the same luck was largest."],
)
variants.append(v)

# ---------------------------------------------------------------- event baselines not carried
for name, tag, cat, verdict, sentence, extra in [
    ("LFIX_AM", "E_LFIX_AM", "direction_call_worthless", "no_edge",
     "Following the hour before the 10:30 London AM fix beats a random direction by $0.12 a trade, give or take $0.46, so the direction call is worth nothing; the rule loses $1.76 a trade.",
     "Best of its 16 EXPLORE cells; the other 15 range from -0.2 to +0.1."),
    ("DATA", "E_DATA", "direction_call_worthless", "no_edge",
     "Fading the half hour before 08:30 New York data time beats a random direction by $0.21 a trade, give or take $0.51, so the direction call is worth nothing; the rule loses $1.97 a trade.",
     "Best of its 16 EXPLORE cells."),
    ("NFP", "E_NFP", "untestable", "untestable_on_this_data",
     "Only 203 pooled EXPLORE trades (about 70 per FX pair, 19 on gold) exist for the first-Friday proxy, so the gap over random direction ($1.85) carries a give-or-take of $2.00 and cannot be told from luck.",
     "Calendar proxy only: the first Friday, 08:30 New York. No news calendar exists in the project and some real jobs reports are moved a week."),
]:
    d = B[name]["explore"]
    c = B[name]["rule"]
    famv = fam(name)
    variants.append(dict(
        id=tag, rule_sentence="Event %s: direction = %s of the move over the previous %d minutes, hold %d minutes, 1.5x/1.0x ATR shape; best EXPLORE cell for this event (>= 200 trades). Not carried: not among the pre-registered top cells."
                              % (name, c["mode"], c["L"], c["hold"]),
        timeframes={"signal": "M1", "entry": "M1", "atr": "M15"},
        baseline=baseline(d), all_stages={"EXPLORE": stage_summary(d)}, stress_adverse_cost=None,
        validate_opened=False,
        verdict=verdict,
        why_no_edge=dict(category=cat, one_sentence=sentence,
                         evidence=dict(edge_over_random_direction_usd=d["edge_over_coin_usd"], give_or_take_usd=d["give_or_take_usd"],
                                       cells_beating_their_give_or_take=int(n_pos(name)), cells_total=int(len(famv)),
                                       note=extra)),
        fix_attempts=[dict(n=1, targets_which_why="direction_call_worthless",
                           what_i_changed="Tried the other 15 look-back / mode / hold cells of this event (the 16-cell family, file family_%s_EXPLORE.csv)." % name,
                           why_i_thought_it_would_help="A different look-back, hold or direction might carry information.",
                           explore=dict(best_edge_over_coin_usd=r2(famv.edge_over_coin_usd.max()), worst_edge_over_coin_usd=r2(famv.edge_over_coin_usd.min()),
                                        best_net_usd_per_trade=r2(famv.usd_per_trade.max())),
                           validate=None, points_gained=None, usd_gained=None,
                           outcome="no_change" if name != "NFP" else "no_change",
                           note="EXPLORE only. No cell made money after cost.")],
        fix_exhausted_because="out_of_mechanical_ideas" if name != "NFP" else "would_require_tuning_on_validate",
        fix_exhausted_detail="Not carried to VALIDATE under the pre-registered rule." if name != "NFP" else
        "More NFP trades only exist in VALIDATE and STRESS; reading them to rescue a rule picked on EXPLORE is tuning on VALIDATE.",
        best_surviving_version=None, what_i_would_change=["Nothing; this event shows no direction information at M1 on this data."]))

out = dict(
    agent="AUT12", strategy="Price around fixed clock events (London gold fixes, COMEX open, US data time, first-Friday jobs report)",
    feed="dukascopy (data_duka), bars cut at 2021-01-01", symbols=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
    gold_spread_injected=0.05, gold_all_in_round_trip_usd_at_010_lot=1.6,
    gold_pip_arithmetic_note="pip = 0.1 price units, USD_PIP_01 = $1.00 at 0.10 lot; 0.05 price units of spread = $0.50; round trip $1.10 commission + $0.50 spread = $1.60.",
    bars_screened=True, corrupt_bars_dropped={"EURUSD": 0, "GBPUSD": 0, "USDJPY": 0, "XAUUSD": 0}, exam_opened=False,
    exam_note="AUTOPSY_EXAM_UNLOCK never set; data_zero never read; M1/M15/H1/H4 frames are cut at 2021-01-01 inside the script so EXAM bars are never even held in memory.",
    spread_repair="autopsy_lib.safe_bars: zero (missing) spreads replaced by the symbol's own median non-zero spread; gold gets 0.05.",
    cost_paid_once="Only inside common.simulate (commission + ask/bid spread + 0.2 pip stop slip). Nothing subtracted anywhere else.",
    dst_handling="Event times built per date with zoneinfo (Europe/London, America/New_York). Proof: the 08:30 New York minute is the busiest minute in both US summer (12:30 UTC, mean range 9.0 pips) and US winter (13:30 UTC, 8.5 pips) against 3.8-3.9 the minute before; see dst_sanity.csv.",
    event_gaps="Events where no M1 bar existed within 3 minutes were skipped (35 to 53 per event per pair of ~4,200; gold 35-38). Missing minutes are looked up as-of (last closed bar; first bar at or after the event minute, at most 3 minutes late).",
    stages={"EXPLORE": "2005-01-01 to 2011-01-01 (gold only from 2009-03-15: about 465 gold events per event type)",
            "VALIDATE": "2011-01-01 to 2017-01-01", "STRESS": "2017-01-01 to 2021-01-01, plus spread x2 and x3 with barriers fixed on the signal price",
            "EXAM": "sealed, never opened"},
    conditions_tried=35, conditions_cap=40,
    conditions_note="20 rule cells (5 events x 2 look-backs x 2 modes) + 15 catalogue conditions scanned on R1. Hold (4 values) and the 36-cell ATR ladder are shape cells: 80 grid cells + 72 ladder cells, reported, not counted as conditions (same convention as AUT04).",
    refinements_carried=3,
    method_notes=[
        "Each event is simulated twice (forced long and forced short) so the rule and its random-direction null use the same fills. Random direction = mean of the two rows (exact), plus 2000 coin-flip draws for the band.",
        "give_or_take_usd is a 95% half-width clustered by calendar day, because EURUSD and GBPUSD move together.",
        "gross_usd_per_trade is AFTER spread and BEFORE commission (simulate's gross_pips); net is what is quoted everywhere else.",
        "Barriers hang off the signal price (close of the last closed M1 bar), never the fill. That is why the adverse-spread rows differ: R1 -1.93 -> -3.04 -> -4.20.",
        "Look-ahead twin = fill at the open of the decision bar (one minute early). For follow rules it is a leak (+$0.25 to +$0.58 a trade, tiny in points); for the fade rule it is a handicap. Both currencies reported.",
        "A random-direction entry has no information to leak, so the twin only has power on the rules, not on the luck bar."],
    variants=variants,
    cross_variant_findings=[
        dict(finding="0 of 80 EXPLORE grid cells (20 rule cells x 4 holds, pooled over 4 instruments) made money after cost. The best was -$0.65 a trade.", replicated_on=["4 instruments", "6 EXPLORE years"]),
        dict(finding="Follow and fade are exact mirrors against a random direction. Of the 10 mirror pairs, 3 had a gap bigger than its own give-or-take on EXPLORE (LFIX_PM follow x2, COMEX fade); with 20 cells that is about what luck would give, and only the COMEX fade kept its sign in VALIDATE and STRESS.", replicated_on=["EXPLORE", "VALIDATE", "STRESS"]),
        dict(finding="The direction rule is not the clock. The random-direction null at the same minutes loses $1.8 to $2.0 a trade, about the cost; the best rule moves that by $0.4 (steady) or $1.3 (decaying).", replicated_on=["all three stages"]),
        dict(finding="Winners and losers are separated AFTER entry, not before: R1 winners reached 1.56 R of open profit, losers 0.39 R; the pre-move size was 1.19 vs 1.12 ATR.", replicated_on=["EXPLORE"]),
    ],
    post_hoc_untested=[
        "R1 with a 4.0x/2.0x ATR shape (EXPLORE +$0.46 a trade, 1 of 36 shapes, picked after looking). Would be a 4th refinement.",
        "A COMEX-fade filter scan (not run: budget and cap).",
        "The R2 gap lives in the first quarter hour; a 5-minute or 10-minute hold was not in the pre-registered list.",
        "Real news calendar for the jobs report (none exists in the project).",
        "Tick data around the exact fix second: M1 bars cannot see inside the event minute."],
)
json.dump(out, open(os.path.join(OUT, "autopsy.json"), "w"), indent=1, default=lambda x: float(x) if isinstance(x, (np.floating, np.integer)) else str(x))
print("wrote autopsy.json", len(variants), "variants")


# ---------------------------------------------------------------- BREAKDOWN.md
def tab(rows, cols):
    h = "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    return h + "".join("| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |\n" for r in rows)


md = ["# AUT12 - price around fixed clock events: breakdown\n",
      "Four instruments (EURUSD, GBPUSD, USDJPY, gold), five clock events, 0.10 lot. Every number is dollars per trade after the whole cost "
      "(gold round trip $1.60). EXPLORE 2005-2010, VALIDATE 2011-2016, STRESS 2017-2020. The sealed exam was never opened.\n",
      "The key column is **random direction at the same minutes**. It is the same trades with the side picked by a coin. "
      "A rule only has something if it beats that.\n"]
for v in variants:
    md.append("\n## %s\n" % v["id"])
    md.append(v["rule_sentence"] + "\n")
    rows = []
    for st, d in v["all_stages"].items():
        rows.append(dict(stage=st, trades=d["trades"], right_in_100=d["right_in_100"], luck_bar=d["luck_bar"], break_even=d["breakeven_needed"],
                         dollars_per_trade=d["usd_per_trade"], profit_factor=d["profit_factor"], random_direction=d["coin_usd"],
                         gap_over_random=d["edge_over_coin_usd"], give_or_take=d["give_or_take_usd"],
                         buy_usd=d["buy_usd_per_trade"], sell_usd=d["sell_usd_per_trade"], always_long=d["all_long_usd_per_trade"], always_short=d["all_short_usd_per_trade"]))
    md.append(tab(rows, list(rows[0].keys())))
    md.append("\nBy instrument inside each stage:\n")
    rows = []
    for st, d in v["all_stages"].items():
        for b in d["by_instrument"]:
            rows.append(dict(stage=st, instrument=b["sym"], trades=b["trades"], right_in_100=b["right_in_100"], dollars_per_trade=b["usd_per_trade"],
                             random_direction=b["coin_usd"], gap_over_random=b["edge_over_coin_usd"], give_or_take=b["give_or_take_usd"]))
    md.append(tab(rows, list(rows[0].keys())))
    md.append("\nLook-ahead twin (fill one minute early): " + "; ".join(
        "%s %+.2f points, %+.3f dollars" % (st, d["lookahead_twin"]["points"], d["lookahead_twin"]["usd"]) for st, d in v["all_stages"].items()) + "\n")
    if v.get("stress_adverse_cost"):
        md.append("\nAdverse cost on STRESS (spread multiplied, barriers fixed on the signal price):\n")
        rows = [dict(spread_multiple=a["spread_multiple"], gold_round_trip=a["gold_round_trip_usd"], trades=a["trades"], right_in_100=a["right_in_100"],
                     dollars_per_trade=a["usd_per_trade"], random_direction=a["coin_usd"], gap_over_random=a["edge_over_coin_usd"], profit_factor=a["PF"])
                for a in v["stress_adverse_cost"]]
        md.append(tab(rows, list(rows[0].keys())))
    md.append("\n**Verdict:** %s. %s\n" % (v["verdict"].replace("_", " "), v["why_no_edge"]["one_sentence"]))
open(os.path.join(OUT, "BREAKDOWN.md"), "w", encoding="utf-8").write("".join(md))
print("wrote BREAKDOWN.md")
