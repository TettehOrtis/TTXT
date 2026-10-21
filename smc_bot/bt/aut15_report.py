"""AUT15: assemble BREAKDOWN.md, partB_summary.csv and autopsy.json from the Part A / Part B result files.
Writes only results/AUT15_costcut/."""
import os
import sys
import json

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import common as C                                                        # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT15_costcut")
STG = ["EXPLORE", "VALIDATE", "STRESS"]
FAC = [1.0, 0.75, 0.5, 0.3125, 0.15625, 0.0]
GOLD_USD = {1.0: 1.60, 0.75: 1.20, 0.5: 0.80, 0.3125: 0.50, 0.15625: 0.25, 0.0: 0.0}

#: the six picks, in the order the rule produced them (see _work/partB_picks.json and preregistration.txt)
PICKS = [
    dict(name="AUT13_EURUSD_R1_T4_ALL", key="AUT13__EURUSD_R1_T4", view="ALL", study="AUT13", gold=False,
         title="AUT13 EURUSD, 15-minute gap fade, trade only the biggest 4th-quartile gaps (R1_T4)",
         rule=("Every minute, compare EURUSD's move over the last 15 minutes with what the other six US-dollar pairs imply. "
               "When EURUSD has lagged the dollar by a large gap (top quarter), trade to close the gap; hold 5 to 60 minutes with an ATR stop and target.")),
    dict(name="AUT13_XAUUSD_R3_L8_ALL", key="AUT13__XAUUSD_R3_L8", view="ALL", study="AUT13", gold=True,
         title="AUT13 gold, 15-minute gap fade, rule R3_L8",
         rule=("Every minute, compare gold's move over the last 15 minutes with what the US dollar index implies (beta from the past 5 days). "
               "When gold has lagged, trade to close the gap, with the AUT13 R3 filter; ATR stop and target, short hold.")),
    dict(name="AUT07_R3_ny_session_ALL", key="AUT07__R3_ny_session", view="ALL", study="AUT07", gold=False,
         title="AUT07 fair value gap fill, New York hours, 25 pairs pooled (R3)",
         rule=("A three-candle gap on M15 is filled; enter with the gap once price has crossed all of it; target 1.0 x ATR, stop 4.0 x ATR; "
               "only gaps born 12:00 to 16:00 UTC. 25 pairs pooled (gold is one of them).")),
    dict(name="AUT04_R0_all_best_4x4_GOLD", key="AUT04__R0_all__best_4x4", view="GOLD", study="AUT04", gold=True,
         title="AUT04 gold opening range breakout, every break, 4 x ATR target and stop (R0)",
         rule=("Take the first 15 minutes after the London or New York open as the range. When a minute closes beyond the range by a small buffer, "
               "trade that direction at the next open with a 4 x ATR target and a 4 x ATR stop, flat after 8 hours.")),
    dict(name="AUT01_LIVE_AS_SHIPPED_full_GOLD", key="AUT01__LIVE_AS_SHIPPED", view="GOLD", study="AUT01", gold=True,
         title="AUT01 the live sniper rule as shipped, gold, $15 target / $70 stop",
         rule=("In an M15 uptrend (50 EMA above 200 EMA) buy when an M1 bar touches the 20 EMA, closes up and RSI14 is 45 or lower "
               "(mirror for sells); target about $15, stop about $70 net, flat after one day. 25,000 of the signals per stage, a fixed random sample.")),
    dict(name="AUT07_R2_displacement_nohole_ALL", key="AUT07__R2_displacement_nohole", view="ALL", study="AUT07", gold=False,
         title="AUT07 fair value gap fill, real displacement candle only, 25 pairs pooled (R2)",
         rule=("As AUT07 R3 but instead of the clock filter, the third candle must have a real body (0.75 x ATR or more) and the gap must not sit on a hole in the data.")),
]


INST = {"AUT13_EURUSD_R1_T4_ALL": "EURUSD", "AUT13_XAUUSD_R3_L8_ALL": "gold", "AUT07_R3_ny_session_ALL": "25 pairs pooled (gold is one of them)",
        "AUT04_R0_all_best_4x4_GOLD": "gold", "AUT01_LIVE_AS_SHIPPED_full_GOLD": "gold", "AUT07_R2_displacement_nohole_ALL": "25 pairs pooled (gold is one of them)"}


def jl(name):
    return json.load(open(os.path.join(OUT, "partB_%s.json" % name)))


def ml(name):
    return pd.read_csv(os.path.join(OUT, "partB_%s_menu.csv" % name))


def f2(x, d=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return ("%." + str(d) + "f") % x


def usd(x, d=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return ("-$%." + str(d) + "f") % abs(x) if x < 0 else ("$%." + str(d) + "f") % x


def paid_mean(j):
    return float(np.mean([j["cost_paid_today_usd"][s] for s in STG]))


def cost_label(p, j, f):
    if p["gold"]:
        return "$%.2f" % GOLD_USD[f] + (" (today)" if f == 1.0 else "")
    return "x%.2f of today (about $%.2f)" % (f, f * paid_mean(j)) + (" (today)" if f == 1.0 else "")


def breakeven_table(p, j, m):
    L = ["| Round trip cost | EXPLORE | VALIDATE | STRESS | Positive in all three? |", "|---|---|---|---|---|"]
    for f in FAC:
        g = m[(m.side == "all") & np.isclose(m.factor, f)].set_index("stage")
        vals = [g.usd[s] for s in STG]
        L.append("| %s | %s | %s | %s | %s |" % (cost_label(p, j, f), *[usd(v) for v in vals], "yes" if all(v > 0 for v in vals) else "no"))
    return "\n".join(L)


def be_rows(j):
    L = ["| | EXPLORE | VALIDATE | STRESS |", "|---|---|---|---|"]
    b = j["breakeven"]
    L.append("| Trades | " + " | ".join(str(int(b[s].get("n", 0))) if "n" in b[s] else "" for s in STG) + " |")
    return L


def controls_table(j, m):
    L = ["| Stage | Cost | Rule | Always buy | Always sell | Coin flip | Random entries (band) | Look-ahead twin |", "|---|---|---|---|---|---|---|---|"]
    for fk, nm in (("0", "none"), ("1", "today")):
        for st in STG:
            c = j["controls"][fk][st]
            lb = j["luck_bar"][fk][st]
            tw = j["lookahead_twin"][fk][st]
            L.append("| %s | %s | %s | %s | %s | %s | %s (give or take %s) | %s (%+.1f pips) |" % (
                st, nm, usd(c["rule"]["usd"]), usd(c["all_long"]["usd"]), usd(c["all_short"]["usd"]), usd(c["coin_flip"]["usd"]),
                usd(lb["usd"]), f2(lb["usd_band"]), usd(tw["lookahead_usd"]), tw["lookahead_pips"]))
    return "\n".join(L)


def side_table(j, m):
    L = ["| Stage | Side | Trades | $ per trade before cost | $ per trade at today's cost |", "|---|---|---|---|---|"]
    for st in STG:
        for sd in ("buy", "sell"):
            a = m[(m.side == sd) & (m.stage == st) & np.isclose(m.factor, 0.0)].iloc[0]
            b = m[(m.side == sd) & (m.stage == st) & np.isclose(m.factor, 1.0)].iloc[0]
            L.append("| %s | %s | %d | %s | %s |" % (st, sd, a.n, usd(a.usd), usd(b.usd)))
    return "\n".join(L)


def pair_table(m, top=None):
    s = m[m.side.str.startswith("sym:")].copy()
    if len(s) == 0:
        return None
    s["sym"] = s.side.str[4:]
    z = s[np.isclose(s.factor, 0.0)].pivot(index="sym", columns="stage", values="usd")[STG]
    t = s[np.isclose(s.factor, 1.0)].pivot(index="sym", columns="stage", values="usd")[STG]
    n = s[np.isclose(s.factor, 0.0)].pivot(index="sym", columns="stage", values="n")[STG]
    d = pd.concat([n.add_prefix("n_"), z.add_prefix("before_cost_"), t.add_prefix("today_")], axis=1)
    return d


def per_variant_json(p):
    j = jl(p["name"])
    m = ml(p["name"])
    return j, m


def main():
    rows = []
    md = []
    md.append("# AUT15 - how cheap would trading have to be? BREAKDOWN\n")
    md.append("Plain words. Dollars are at 0.10 lot, per trade. \"Before cost\" means the same trades replayed with no commission and no spread. "
              "\"Break-even cost\" is the round trip cost at which the average trade earns nothing; it equals the before-cost edge. "
              "Gold today is $1.60 a round trip ($1.10 commission + $0.50 spread). The cost menu is made up for this test; it is not a price list of any broker. "
              "Stop slippage (0.2 pip on trades that hit the stop) is not part of the menu and stays in every row, including $0.\n")
    md.append("Part A (all 14 studies, every variant, every stage) is in `partA_all_variants.csv`. Below: the counts and the variants that are positive before cost in all three stages. Then Part B: the six variants the pre-registered rule picked.\n")
    cnt = json.load(open(os.path.join(OUT, "partA_counts.json")))
    pa = pd.read_csv(os.path.join(OUT, "partA_all_variants.csv"))
    h = pa[pa.headline_view & ~pa.duplicate_of_other_row]
    md.append("## Part A - the counts\n")
    md.append("| Step | Variants left |\n|---|---|")
    md.append("| Headline variants with saved or rebuilt orders (14 studies) | %d |" % cnt["headline_variants_with_orders"])
    md.append("| Measured in all three stages (EXPLORE, VALIDATE, STRESS) | %d |" % cnt["measured_in_all_three_stages"])
    md.append("| Positive before cost in all three stages | %d |" % cnt["edge_before_cost_positive_in_all_three"])
    md.append("| ... and at least 500 trades in each stage | %d |" % cnt["plus_at_least_500_trades_each_stage"])
    md.append("| ... and buys and sells both not negative before cost | %d |" % cnt["clean_both_sides"])
    md.append("| Positive at today's cost in all three stages (any trade count) | %d |" % cnt["net_positive_at_todays_cost_in_all_three"])
    md.append("| Not built (orders never saved, no builder that runs unedited): listed in `partA_not_built.csv` | %d |\n" % cnt["not_built_variants"])
    md.append("Per study, headline variants counted: %s.\n" % ", ".join("%s %d" % (k, v) for k, v in cnt["per_study"].items()))
    md.append("### Variants positive before cost in all three stages\n")
    L = ["| Study | Variant | Trades E / V / S | Before cost $ E / V / S | Cost paid today (EXPLORE) | Net today $ E / V / S | 500+ trades? | Both sides >= 0? |", "|---|---|---|---|---|---|---|---|"]
    q = h[h.positive_before_cost_in_all_three].sort_values("worst_stage_edge_over_cost", ascending=False)
    for r in q.itertuples():
        L.append("| %s | %s (%s) | %d / %d / %d | %s / %s / %s | %s | %s / %s / %s | %s | %s |" % (
            r.study, r.variant, r.instruments, r.trades_E, r.trades_V, r.trades_S,
            usd(r.edge_before_cost_usd_E), usd(r.edge_before_cost_usd_V), usd(r.edge_before_cost_usd_S), usd(r.cost_in_aut15_world_usd_E),
            usd(r.net_in_aut15_world_usd_E), usd(r.net_in_aut15_world_usd_V), usd(r.net_in_aut15_world_usd_S),
            "yes" if r.at_least_500_trades_each_stage else "NO", "yes" if r.buys_and_sells_both_nonneg else "no"))
    md.append("\n".join(L) + "\n")
    md.append("Costs here are in the AUT15 cost world (corrupt bars out, missing FX spread filled with the pair's median real spread, gold 0.05 = $0.50 plus $1.10 commission). "
              "Studies run before the spread repair show smaller costs in their own reports; the before-cost column does not depend on this.\n")
    md.append("## Part B - the six picked variants\n")
    for p in PICKS:
        try:
            j, m = per_variant_json(p)
        except Exception as e:  # not run yet
            md.append("## %s\n\n(not finished: %s)\n" % (p["name"], e))
            continue
        if "lookahead_twin" not in j:
            md.append("## %s\n\n(not finished yet)\n" % p["name"])
            continue
        b = j["breakeven"]
        ws = j["worst_stage"]
        md.append("## %s\n" % p["title"])
        md.append("Rule: %s\n" % p["rule"])
        md.append("Orders replayed: %d (%s). Instruments: %s.\n" % (j["orders"], ", ".join("%s %d" % (s, int(m[(m.side == 'all') & (m.stage == s) & np.isclose(m.factor, 1.0)].n.iloc[0])) for s in STG), INST[p["name"]]))
        md.append("### Net dollars per trade at each cost\n")
        md.append(breakeven_table(p, j, m) + "\n")
        md.append("### Break-even cost, by stage\n")
        L = ["| | EXPLORE | VALIDATE | STRESS |", "|---|---|---|---|"]
        L.append("| Before-cost edge = break-even cost (give or take) | " + " | ".join("%s (%s)" % (usd(b[s]["edge_before_cost_usd"]), f2(b[s]["edge_gt"])) for s in STG) + " |")
        L.append("| Cost paid today | " + " | ".join(usd(b[s]["cost_paid_today"]) for s in STG) + " |")
        L.append("| Net at today's cost | " + " | ".join(usd(b[s]["net_today_usd"]) for s in STG) + " |")
        L.append("| Share of today's cost it can carry | " + " | ".join("%.0f%%" % (100 * b[s]["breakeven_factor"]) for s in STG) + " |")
        md.append("\n".join(L) + "\n")
        md.append("**Worst stage: %s. Break-even cost there: %s** (today's cost in that stage: %s).\n" % (ws, usd(j["worst_stage_breakeven_cost_usd"]), usd(b[ws]["cost_paid_today"])))
        md.append("Power check (dollars differ across all six cost rows, so the cut really changes the result): %s.\n" % ", ".join("%s %s" % (s, "yes" if j["power_check_usd_differs_across_rows"][s] else "NO") for s in STG))
        md.append("### Buys and sells\n")
        md.append(side_table(j, m) + "\n")
        md.append("### The controls (does the direction call beat luck?)\n")
        md.append(controls_table(j, m) + "\n")
        md.append("Random entries: 20,000 per seed, 5 seeds, same hours, same ATR shape, same hold. \"Band\" is the spread between seeds. "
                  "Look-ahead twin: the same trade filled one minute earlier, at the signal bar's own open; a clean pipeline should not gain from it. "
                  "A positive number would be a leak; a negative number means filling early is worse, which a gap-fade or breakout rule would show if it holds no future information.\n")
        pt = pair_table(m)
        if pt is not None and p["view"] == "ALL" and len(pt) > 3:
            pt.to_csv(os.path.join(OUT, "partB_%s_by_pair.csv" % p["name"]))
            pos = {s: int((pt["before_cost_" + s] > 0).sum()) for s in STG}
            md.append("### By pair, inside each stage\n")
            md.append("Pairs with a positive before-cost edge: %s of %d (full table: `partB_%s_by_pair.csv`).\n" % (
                ", ".join("%s %d" % (s, pos[s]) for s in STG), len(pt), p["name"]))
        rows.append(dict(name=p["name"], key=p["key"], view=p["view"], study=p["study"],
                         **{"edge_before_cost_" + s: b[s]["edge_before_cost_usd"] for s in STG},
                         **{"net_today_" + s: b[s]["net_today_usd"] for s in STG},
                         **{"cost_today_" + s: b[s]["cost_paid_today"] for s in STG},
                         **{"breakeven_cost_" + s: b[s]["breakeven_cost_usd"] for s in STG},
                         worst_stage=ws, worst_stage_breakeven_cost_usd=j["worst_stage_breakeven_cost_usd"],
                         worst_stage_cost_today=b[ws]["cost_paid_today"],
                         worst_stage_breakeven_share_of_today=b[ws]["breakeven_factor"],
                         highest_menu_row_positive_in_all_three=max([f for f in FAC if all(m[(m.side == "all") & (m.stage == s) & np.isclose(m.factor, f)].usd.iloc[0] > 0 for s in STG)] or [np.nan]),
                         net_positive_at_todays_cost_in_all_three=bool(all(b[s]["net_today_usd"] > 0 for s in STG))))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "partB_summary.csv"), index=False)
    open(os.path.join(OUT, "BREAKDOWN.md"), "w", encoding="utf-8").write("\n".join(md))
    print("BREAKDOWN written", len(rows), "variants")


if __name__ == "__main__":
    main()
