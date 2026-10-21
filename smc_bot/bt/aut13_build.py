"""AUT13: assemble autopsy.json and BREAKDOWN.md from the saved result files. Reads results only; runs no backtest."""
import os, sys, json, glob
import numpy as np, pandas as pd
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "AUT13_leadlag")
J = lambda f: json.load(open(os.path.join(OUT, f)))
STAGES = ("EXPLORE", "VALIDATE", "STRESS")
TG = ("XAUUSD", "EURUSD")
sel = J("selection_EXPLORE.json")
cells = J("refine_cells.json")
full = {t: J("full_%s.json" % t) for t in TG}
grid = pd.read_csv(os.path.join(OUT, "grid_all_stages.csv"))
why = J("why_base.json")
checks = J("checks_F.json")
rep = {}
for f in glob.glob(os.path.join(OUT, "replicate_*.json")):
    rep.update(json.load(open(f)))

NAMES = {"T4": "late New York 16-22 UTC", "E2": "M15 ATR above its own 60-day median", "L8": "dollar move small (|z| < 1)"}
CARR = sel["carried"]
VAR = [("BASE", None)] + [("R%d_%s" % (i + 1, r), r) for i, r in enumerate(CARR)]
RULE_TXT = ("Every minute, gold (or EURUSD) return over the last %d minutes minus beta times the dollar-index return over the same "
            "minutes. When that gap is at least 2 sigma of its own last-5-day spread, fade it (gap up -> sell, gap down -> buy) at the "
            "open of the next M1 bar; stop and target 1.0 x M15 ATR off the signal price; time stop %d minutes.")


def tr(t, v, s):
    return pd.read_csv(os.path.join(OUT, "trades_%s_%s_%s.csv" % (t, v, s)))


def be(t):
    w = t[t.net_pips > 0].usd_01.mean()
    l = -t[t.net_pips <= 0].usd_01.mean()
    return round(100 * l / (w + l), 2)


def gridv(t, k, h, st, col="usd"):
    r = grid[(grid.tg == t) & (grid.k == k) & (grid.h == h) & (grid.stage == st)]
    return float(r[col].iloc[0]), int(r["n"].iloc[0])


def stage_row(t, v, st):
    c = full[t]["%s|%s" % (v, st)]
    r = c["rule"]
    d = dict(trades=r["trades"], right_in_100=r["right_in_100"], luck_bar=c["luck_bar"]["right_in_100"],
             luck_bar_seed_band_points=c["luck_bar"]["seed_band_points"], luck_bar_usd=c["luck_bar"]["usd_per_trade"],
             breakeven_needed=be(tr(t, v, st)), profit_factor=r["profit_factor"], usd_per_trade=r["usd_per_trade"],
             gross_usd_per_trade_before_cost=round(r["usd_per_trade"] + c["round_trip_usd"], 3), round_trip_usd=c["round_trip_usd"],
             buy_right_in_100=r["buy_right_in_100"], buy_usd_per_trade=r["buy_usd_per_trade"], buy_trades=r["buy_trades"],
             sell_right_in_100=r["sell_right_in_100"], sell_usd_per_trade=r["sell_usd_per_trade"], sell_trades=r["sell_trades"])
    return d


def controls(t, v, st):
    c = full[t]["%s|%s" % (v, st)]
    return dict(
        always_long_usd=c["always_long"]["usd_per_trade"], always_long_right=c["always_long"]["right_in_100"],
        always_short_usd=c["always_short"]["usd_per_trade"], always_short_right=c["always_short"]["right_in_100"],
        coin_flip_usd=c["coin_flip"]["usd_per_trade"], coin_flip_right=c["coin_flip"]["right_in_100"],
        gap_rule_reversed_usd=c["reversed_rule"]["usd_per_trade"], gap_rule_reversed_right=c["reversed_rule"]["right_in_100"],
        rule_minus_coin_usd=c["rule_minus_coin_usd"], rule_vs_luck_points=c["vs_luck_points"], rule_vs_luck_usd=c["vs_luck_usd"],
        look_ahead_twin=dict(points=c["lookahead_twin"]["points"], usd=c["lookahead_twin"]["usd"],
                             note="fill at the decision bar's own open, one minute early; a fade enters BEFORE the move it fades, so the twin is far worse, not better: no leak of the harmful kind. Weak test, see report."),
        entered_1_min_late_usd=c["delay_1min"]["usd_per_trade"], entered_2_min_late_usd=c["delay_2min"]["usd_per_trade"])


variants = []
for t in TG:
    for v, rid in VAR:
        k, h = (5, 15) if v == "BASE" else (cells[rid]["k"], cells[rid]["hold"])
        st_rows = {s: stage_row(t, v, s) for s in STAGES}
        ctl = {s: controls(t, v, s) for s in STAGES}
        e, va, sr = st_rows["EXPLORE"], st_rows["VALIDATE"], st_rows["STRESS"]
        adv = full[t]["%s|STRESS" % v].get("adverse", {})
        if t == "XAUUSD":
            cat = "cost" if v == "BASE" else "regime_artifact"
        else:
            cat = "cost"
        if v == "BASE":
            one = ("The dollar-gap fade is a better direction call than a coin (beats it by about $%.2f a trade) but earns only about $%.2f a trade before cost "
                   "against a $%.2f round trip, so it loses in every stage." % (ctl["VALIDATE"]["rule_minus_coin_usd"], va["gross_usd_per_trade_before_cost"], va["round_trip_usd"]))
        elif t == "XAUUSD":
            one = ("The EXPLORE gain is a buy-side bull-market reading (buys $%.2f, sells $%.2f a trade) and it reverses in VALIDATE ($%.2f), "
                   "where gold fell." % (e["buy_usd_per_trade"], e["sell_usd_per_trade"], va["usd_per_trade"]))
        else:
            one = ("Gross edge of about $%.2f a trade before cost is smaller than the $%.2f round trip in all three stages." % (va["gross_usd_per_trade_before_cost"], va["round_trip_usd"]))
        rule_txt = RULE_TXT % (k, h) + ("" if rid is None else " Only when: %s." % NAMES[rid])
        chain = []
        n = 0
        if v == "BASE":
            # attempt: best cell of the grid, then drop the dollar, then each refinement
            bestc = grid[(grid.tg == t) & (grid.stage == "EXPLORE")].sort_values("usd", ascending=False).iloc[0]
            bk, bh = int(bestc.k), int(bestc.h)
            ge, gn = gridv(t, bk, bh, "EXPLORE")
            gv, gvn = gridv(t, bk, bh, "VALIDATE")
            gs, gsn = gridv(t, bk, bh, "STRESS")
            n += 1
            chain.append(dict(n=n, targets_which_why="cost",
                              what_i_changed="Moved from the pre-declared cell (k=5, hold 15) to the best of the 16 (k,hold) cells in EXPLORE: k=%d, hold %d." % (bk, bh),
                              why_i_thought_it_would_help="A longer look-back and a longer hold let the move be bigger than the fixed round-trip cost.",
                              explore=dict(trades=gn, usd_per_trade=ge), validate=dict(trades=gvn, usd_per_trade=gv), stress=dict(trades=gsn, usd_per_trade=gs),
                              points_gained=None, usd_gained=round(ge - e["usd_per_trade"], 3),
                              outcome="made_money_on_explore_died_at_validate" if ge > 0 and gv <= 0 else ("improved_but_still_negative" if ge > e["usd_per_trade"] else "no_change"),
                              note="Picked on EXPLORE only; the same cell read on the other stages afterwards. Count of cells above zero: see grid_all_stages.csv."))
            own = {s: gridv(t, 5, 15, s, "own")[0] for s in STAGES}
            n += 1
            chain.append(dict(n=n, targets_which_why="direction_call_is_plain_reversal",
                              what_i_changed="Removed the dollar: fade the target's own k-minute move with no dollar term (control).",
                              why_i_thought_it_would_help="Tells us whether the dollar adds anything beyond ordinary short-term reversal.",
                              explore=dict(usd_per_trade=own["EXPLORE"]), validate=dict(usd_per_trade=own["VALIDATE"]), stress=dict(usd_per_trade=own["STRESS"]),
                              points_gained=None, usd_gained=round(own["EXPLORE"] - e["usd_per_trade"], 3),
                              outcome="no_change",
                              note="Own-move fade is within about $0.1-0.2 of the dollar-gap rule in every stage: most of the small gross edge is reversal, not a dollar lag."))
            for j, (v2, rid2) in enumerate(VAR[1:], 1):
                a, b, c_ = (stage_row(t, v2, s) for s in STAGES)
                n += 1
                chain.append(dict(n=n, targets_which_why="cost",
                                  what_i_changed="Refinement %s: trade only when %s (cell k=%d hold %d)." % (v2, NAMES[rid2], cells[rid2]["k"], cells[rid2]["hold"]),
                                  why_i_thought_it_would_help="EXPLORE autopsy: this condition lifted dollars on both gold and EURUSD." if True else "",
                                  explore=dict(trades=a["trades"], right_in_100=a["right_in_100"], usd_per_trade=a["usd_per_trade"]),
                                  validate=dict(trades=b["trades"], right_in_100=b["right_in_100"], usd_per_trade=b["usd_per_trade"]),
                                  stress=dict(trades=c_["trades"], usd_per_trade=c_["usd_per_trade"]),
                                  points_gained=round(a["right_in_100"] - e["right_in_100"], 2), usd_gained=round(a["usd_per_trade"] - e["usd_per_trade"], 3),
                                  outcome="made_money_on_explore_died_at_validate" if a["usd_per_trade"] > 0 else "improved_but_still_negative"))
            why_ends = "conditions_cap_reached"
            detail = ("39 of 40 conditions used (16 cells + 1 trigger level + 22 catalogue conditions). No condition passed the strict carry rule; the three best by a "
                      "relaxed rule (set before VALIDATE was read) all failed VALIDATE. A further attempt would mean selecting on VALIDATE or STRESS, which the brief forbids.")
        else:
            b0 = st_rows
            base_e = stage_row(t, "BASE", "EXPLORE")
            n += 1
            chain.append(dict(n=n, targets_which_why="cost",
                              what_i_changed="Added the filter '%s' to the base rule." % NAMES[rid],
                              why_i_thought_it_would_help="It lifted net dollars per trade on both gold and EURUSD in the EXPLORE condition scan at the base cell.",
                              explore=dict(trades=e["trades"], right_in_100=e["right_in_100"], usd_per_trade=e["usd_per_trade"]),
                              validate=dict(trades=va["trades"], right_in_100=va["right_in_100"], usd_per_trade=va["usd_per_trade"]),
                              stress=dict(trades=sr["trades"], right_in_100=sr["right_in_100"], usd_per_trade=sr["usd_per_trade"]),
                              stress_adverse={k_: dict(usd_per_trade=x["usd_per_trade"]) for k_, x in adv.items()},
                              points_gained=round(e["right_in_100"] - base_e["right_in_100"], 2), usd_gained=round(e["usd_per_trade"] - base_e["usd_per_trade"], 3),
                              outcome="made_money_on_explore_died_at_validate" if e["usd_per_trade"] > 0 else "improved_but_still_negative"))
            n += 1
            chain.append(dict(n=n, targets_which_why="shape",
                              what_i_changed="Moved the refinement to its best EXPLORE cell (k=%d, hold %d), chosen by the mean of gold and EURUSD EXPLORE dollars." % (k, h),
                              why_i_thought_it_would_help="A longer look-back and hold gave the reversal more room against the cost.",
                              explore=dict(trades=e["trades"], usd_per_trade=e["usd_per_trade"]), validate=dict(trades=va["trades"], usd_per_trade=va["usd_per_trade"]),
                              stress=dict(trades=sr["trades"], usd_per_trade=sr["usd_per_trade"]),
                              points_gained=None, usd_gained=None, outcome="made_money_on_explore_died_at_validate" if e["usd_per_trade"] > 0 else "improved_but_still_negative"))
            why_ends = "would_require_tuning_on_validate"
            detail = "Refinement cap of 3 reached; any further condition or cell would be chosen after seeing VALIDATE, which is forbidden."
        variants.append(dict(
            id="%s_%s" % (t, v), instrument=t, refinement=v,
            rule_sentence=rule_txt,
            timeframes=dict(signal="M1 gap over k=%d minutes" % k, entry="M1 (next bar open)", stop_target_unit="M15 ATR", hold_min=h),
            baseline=dict(stage="EXPLORE", **{x: e[x] for x in ("trades", "right_in_100", "luck_bar", "breakeven_needed", "profit_factor", "usd_per_trade",
                                                                   "buy_right_in_100", "buy_usd_per_trade", "sell_right_in_100", "sell_usd_per_trade")}),
            by_stage=st_rows, controls=ctl, stress_adverse_cost=adv,
            verdict="no_edge",
            why_no_edge=dict(category=cat, one_sentence=one,
                             evidence=dict(rule_vs_luck_bar_points={s: ctl[s]["rule_vs_luck_points"] for s in STAGES},
                                           holds_in_validate=bool(va["usd_per_trade"] > 0), holds_in_stress=bool(sr["usd_per_trade"] > 0),
                                           gross_before_cost_usd={s: st_rows[s]["gross_usd_per_trade_before_cost"] for s in STAGES},
                                           round_trip_usd=e["round_trip_usd"])),
            fix_attempts=chain, fix_exhausted_because=why_ends, fix_exhausted_detail=detail,
            best_surviving_version=None))

rep_tab = {}
for key, c in rep.items():
    p, s = key.split("|")
    rep_tab.setdefault(p, {})[s] = dict(trades=c["rule"]["trades"], usd_per_trade=c["rule"]["usd_per_trade"], luck_usd=c["luck_bar"]["usd_per_trade"],
                                        reversed_usd=c["reversed_rule"]["usd_per_trade"], coin_usd=c["coin_flip"]["usd_per_trade"],
                                        round_trip_usd=c["round_trip_usd"], gross_before_cost_usd=round(c["rule"]["usd_per_trade"] + c["round_trip_usd"], 3))
for t in TG:
    rep_tab[t] = {s: dict(trades=full[t]["BASE|%s" % s]["rule"]["trades"], usd_per_trade=full[t]["BASE|%s" % s]["rule"]["usd_per_trade"],
                          luck_usd=full[t]["BASE|%s" % s]["luck_bar"]["usd_per_trade"], reversed_usd=full[t]["BASE|%s" % s]["reversed_rule"]["usd_per_trade"],
                          coin_usd=full[t]["BASE|%s" % s]["coin_flip"]["usd_per_trade"], round_trip_usd=full[t]["BASE|%s" % s]["round_trip_usd"],
                          gross_before_cost_usd=round(full[t]["BASE|%s" % s]["rule"]["usd_per_trade"] + full[t]["BASE|%s" % s]["round_trip_usd"], 3)) for s in STAGES}

_d = [r[s_]["usd_per_trade"] - r[s_]["luck_usd"] for r in rep_tab.values() for s_ in STAGES]
LUCK_RANGE = "$%.2f to $%.2f" % (min(_d), max(_d))
_c = [full[t]["%s|%s" % (v, s_)]["rule_minus_coin_usd"] for t in TG for v, _ in VAR[:1] for s_ in STAGES]
COIN_RANGE = "$%.2f to $%.2f" % (min(_c), max(_c))
print("rule minus random entry across pairs/stages:", LUCK_RANGE, "; rule minus coin (base, gold+EURUSD):", COIN_RANGE)
ccf = {t: pd.read_csv(os.path.join(OUT, "diag_ccf_%s.csv" % t)).pivot_table(index="lag_min", columns="stage", values="corr").round(4).loc[[-1, 0, 1, 2]].to_dict() for t in TG}
cells_pos = grid[grid.usd > 0].groupby(["tg", "stage"]).size().to_dict()
aj = dict(
    agent="AUT13", strategy="Gold (and FX) lagging the US dollar: fade the gap between the target's move and the move the dollar implies",
    feed="dukascopy (data_duka)", symbols=["XAUUSD", "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCAD", "USDCHF"],
    gold_spread_injected=0.05, gold_all_in_round_trip_usd_at_010_lot=1.6,
    fx_cost_note="FX spreads: the duka column is 92-96% zero (missing); the project's safe_bars rule fills each missing minute with that pair's median non-zero spread. Round trip at 0.10 lot: EURUSD $1.30, AUDUSD $0.89, USDJPY $1.47, USDCAD $1.83, GBPUSD $2.82, NZDUSD $2.78, USDCHF $4.89. GBPUSD/NZDUSD/USDCHF figures look high (median of non-zero readings is probably wider than the typical minute), so their losses are probably overstated; EURUSD and AUDUSD are the fair tests.",
    bars_screened=True, corrupt_bars_dropped={k: v for k, v in checks["corrupt_bars_dropped"].items() if v}, exam_opened=False, data_zero_read=False,
    stage_note="EXPLORE 2005-2011 (gold only from 2009-03-15, 1.8 years, one-directional), VALIDATE 2011-2017, STRESS 2017-2021. EXAM sealed.",
    conditions_tried=39, conditions_cap=40,
    conditions_breakdown=dict(trigger_level=1, shape_cells_k_by_hold=16, catalogue_conditions=22),
    carried_refinements=[dict(id="R%d_%s" % (i + 1, r), condition=NAMES[r], cell=cells[r]) for i, r in enumerate(CARR)],
    carry_rule_note="Strict rule (written before the scan: lift >= $0.30 on BOTH instruments, keep >= 20%, both gold sides better) passed NOTHING. Before VALIDATE was read the rule was relaxed to: lift > 0 on both instruments, both gold sides better, keep >= 15%; take the 3 best by the worse-of-two lift. The Asia condition missed on 14.1% kept on gold.",
    variants=variants,
    lead_lag_timing=dict(
        reading="Same-minute correlation of target and dollar returns is -0.33 to -0.40 (gold) and -0.48 to -0.69 (EURUSD). One minute later it is -0.04 or less (gold EXPLORE -0.039, VALIDATE -0.015, STRESS -0.006; EURUSD about -0.01). The link works inside one minute, so on M1 bars there is almost no lag left to trade.",
        cross_correlation_by_minute_lag=ccf,
        lag_gt_0_means="dollar return at minute i vs target return at minute i+lag"),
    own_move_fade_control="The same fade with the dollar term removed. At the base cell it is within about $0.1 to $0.2 of the dollar-gap rule in every stage (gold: -0.62/-1.10/-1.05 vs -0.57/-0.99/-0.89; EURUSD: -0.79/-1.00/-1.10 vs -0.90/-1.07/-1.02). So the small gross edge is ordinary short-term reversal, with at most a tiny dollar lag on top.",
    grid_cells_above_zero_by_instrument_and_stage={"%s_%s" % k: int(v) for k, v in cells_pos.items()},
    grid_note="16 (k,hold) cells run on every stage for the UNREFINED rule as documentation only; no selection was made from VALIDATE or STRESS columns.",
    replication_base_cell_by_pair=rep_tab,
    cross_variant_findings=[
        dict(finding="The gap-fade direction call is real but small: it beats a coin flip by " + COIN_RANGE + " a trade (base cell, gold and EURUSD) and its sign-reversed twin is much worse (gold -$2.4 to -$2.8, EURUSD -$1.7 to -$1.8). Gross edge before cost is $0.6-1.0 on gold and $0.2-0.4 on EURUSD, always under the round trip.",
             replicated_on=["XAUUSD", "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCAD", "USDCHF"]),
        dict(finding="Most of that edge is plain reversal of a sharp 2-sigma minute move; removing the dollar leaves it within $0.1-0.2.", replicated_on=["XAUUSD", "EURUSD"]),
        dict(finding="Every EXPLORE gold gain (3 of 16 cells; 3 of 3 refinements) is a buy-side result in a rising market and is gone in VALIDATE.", replicated_on=["XAUUSD EXPLORE vs VALIDATE vs STRESS"]),
        dict(finding="The dollar-to-target lag is shorter than one M1 bar: same-minute correlation is 8 to 60 times the next-minute figure.", replicated_on=["XAUUSD", "EURUSD", "3 stages"]),
        dict(finding="Adverse-cost stress has power: +0.05 and +0.10 extra gold spread moved the base STRESS result from -0.89 to -1.37 and -1.85 a trade, because the barriers hang off the signal price and only the entry moves.", replicated_on=["XAUUSD", "EURUSD"])],
    group_F_checks=dict(
        F1_fill_bar="Every order fills at the open of the bar one clock minute after the decision bar's close (t_fill minus t_signal = 0 minutes on all trades). Events whose next bar is not the next clock minute are dropped. Look-ahead twin = fill at the decision bar's own open.",
        F2_feed_disagreement="Not testable: duka and Exness overlap only inside the sealed EXAM.",
        F3_holes="Returns over k minutes are used only when the bar k minutes back is exactly k minutes earlier. Basket pairs count only when their last close is at most 2 minutes old and at least 4 pairs are present. Share of fresh (same-minute) basket bars at gold signals: 96.5% to 99.6% per pair.",
        F4_closed_bars="Dollar-index bars are matched by timestamp and never newer than the decision bar. M15 ATR comes from the last CLOSED M15 bar (autopsy_lib.atr_at).",
        F5_same_bar="SL before TP inside a bar, never TP on the fill bar (common.simulate).",
        beta_check=checks["beta_spot_check"], fill_bar_check=checks["fill_bar_check"]),
    hand_replay_three_gold_trades=J("hand_check.json"),
    sanity_hand_check="three gold base trades replayed by hand from raw M1 bars match the file to the cent (see hand_replay_three_gold_trades). Beta spot check in checks_F.json: the vectorised beta at bar 2,000,000 equals a hand sum over the 6,900 bars that end 5 bars before the decision bar (-1.2117 both ways).",
    post_hoc_untested=[
        "Fade the gap only when gold's own spread-widening is low (needs a real gold spread; the duka feed has none).",
        "Sub-minute lead-lag with tick data: the question M1 cannot answer.",
        "Combine the late-New-York and bigger-gap ideas in one rule (would be chosen after seeing the numbers).",
        "A signal built on the dollar index in the EURUSD target (the dollar index leaves out EURUSD, which led the others by a minute in 2005-2010: correlation -0.059 at lag -1)."])
json.dump(aj, open(os.path.join(OUT, "autopsy.json"), "w"), indent=1, default=float)
print("autopsy.json written", len(variants), "variants")


# ---------------------------------------------------------------- BREAKDOWN.md
def money(x):
    return ("-$%.2f" % -x) if x < 0 else ("$%.2f" % x)


def vtable(v):
    L = ["| stage | trades | right in 100 | random entry (right in 100) | needs to break even | net $ a trade | before cost $ | buys $ | sells $ | profit factor |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for s in STAGES:
        r = v["by_stage"][s]
        L.append("| %s | %d | %.1f | %.1f (+/-%.1f) | %.1f | %s | %s | %s | %s | %.2f |" % (
            s, r["trades"], r["right_in_100"], r["luck_bar"], r["luck_bar_seed_band_points"], r["breakeven_needed"], money(r["usd_per_trade"]),
            money(r["gross_usd_per_trade_before_cost"]), money(r["buy_usd_per_trade"]), money(r["sell_usd_per_trade"]), r["profit_factor"]))
    L += ["", "Controls at the same minutes, net $ a trade (EXPLORE / VALIDATE / STRESS):", "",
          "| control | EXPLORE | VALIDATE | STRESS |", "|---|---|---|---|"]
    for lab, key in (("the rule", None), ("always buy", "always_long_usd"), ("always sell", "always_short_usd"), ("coin flip", "coin_flip_usd"),
                     ("gap rule with the sign reversed", "gap_rule_reversed_usd"), ("entered 1 minute late", "entered_1_min_late_usd"),
                     ("entered 2 minutes late", "entered_2_min_late_usd")):
        if key is None:
            vals = [v["by_stage"][s]["usd_per_trade"] for s in STAGES]
        else:
            vals = [v["controls"][s][key] for s in STAGES]
        L.append("| %s | %s | %s | %s |" % ((lab,) + tuple(money(x) for x in vals)))
    L.append("| look-ahead twin, change in right-in-100 / $ | %s |" % " | ".join(
        "%+.1f pts / %s" % (v["controls"][s]["look_ahead_twin"]["points"], money(v["controls"][s]["look_ahead_twin"]["usd"])) for s in STAGES))
    adv = v["stress_adverse_cost"]
    if adv:
        L += ["", "STRESS with worse costs (barrier prices fixed, only the entry and exit prices move): normal %s" % money(v["by_stage"]["STRESS"]["usd_per_trade"]) +
              "; " + "; ".join("%s -> %s" % (("extra spread %s" % (("%.1f pip" % (float(k.replace("extra_spread_", "")) / 0.0001)) if float(k.replace("extra_spread_", "")) < 0.001 else k.replace("extra_spread_", "") + " price units")), money(x["usd_per_trade"])) for k, x in adv.items())]
    return "\n".join(L)


md = ["# AUT13 BREAKDOWN: does gold (or EURUSD) lag the US dollar?", "",
      "One rule, four versions, two instruments. All money is dollars per trade at 0.10 lot, after the full round-trip cost, paid once. "
      "Gold round trip $1.60. EURUSD round trip about $1.30. EXPLORE 2005-2011 (gold only from March 2009), VALIDATE 2011-2017, STRESS 2017-2021. The final exam period was not opened.", "",
      "The rule: when the target has moved more than the dollar explains (2 sigma), trade against the gap. 'Before cost' means the net result plus the round trip.", ""]
for v in variants:
    ttl = "%s, %s" % (v["instrument"], "base rule (5-minute gap, 15-minute hold)" if v["refinement"] == "BASE" else
                        "%s: only when %s (%d-minute gap, %d-minute hold)" % (v["refinement"], NAMES[v["refinement"].split("_", 1)[1]], int(v["timeframes"]["signal"].split("=")[1].split(" ")[0]), v["timeframes"]["hold_min"]))
    md += ["## " + ttl, "", vtable(v), "", "**Verdict:** no edge. " + v["why_no_edge"]["one_sentence"], ""]
md += ["## Same base rule on the other five dollar pairs (net $ a trade, base cell)", "",
       "| pair | round trip $ | EXPLORE | VALIDATE | STRESS | random entry (VALIDATE) | before cost (VALIDATE) |", "|---|---|---|---|---|---|---|"]
for p in ("EURUSD", "AUDUSD", "USDJPY", "USDCAD", "GBPUSD", "NZDUSD", "USDCHF", "XAUUSD"):
    r = rep_tab[p]
    md.append("| %s | %.2f | %s | %s | %s | %s | %s |" % (p, r["VALIDATE"]["round_trip_usd"], money(r["EXPLORE"]["usd_per_trade"]), money(r["VALIDATE"]["usd_per_trade"]),
                                                         money(r["STRESS"]["usd_per_trade"]), money(r["VALIDATE"]["luck_usd"]), money(r["VALIDATE"]["gross_before_cost_usd"])))
md += ["", "**Verdict:** no pair pays; the rule beats a random entry on every pair and every stage (by " + LUCK_RANGE + " a trade) but never by the round trip.", "",
       "## How long is the lag?", "",
       "| minutes after the dollar move | gold EXPLORE | gold VALIDATE | gold STRESS | EURUSD EXPLORE | EURUSD VALIDATE | EURUSD STRESS |", "|---|---|---|---|---|---|---|"]
for lag in (0, 1, 2):
    md.append("| %d | %s |" % (lag, " | ".join("%.3f" % ccf[t][s][lag] for t in TG for s in STAGES)))
md += ["", "(Correlation of the target's one-minute move with the dollar index's move that many minutes earlier.)", "",
       "**Verdict:** the link is almost all inside one minute, so on one-minute bars there is almost nothing left to trade after the dollar has moved.", ""]
open(os.path.join(OUT, "BREAKDOWN.md"), "w", encoding="utf-8").write("\n".join(md))
print("BREAKDOWN.md written")
