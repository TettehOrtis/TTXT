"""AUT11 analysis: the autopsy tables built from the stage outputs. Reads only AUT11's own files + the D1 cache."""
import os
import sys
import json
import warnings

os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut11_trend as T                                                   # noqa: E402

warnings.simplefilter("ignore")
OUT = T.OUT
STAGES = ["EXPLORE", "VALIDATE", "STRESS"]


def trades_of(key, stage):
    f = os.path.join(OUT, "trades_%s_%s.csv" % (stage, key))
    if not os.path.exists(f):
        return None
    t = pd.read_csv(f, parse_dates=["t_signal", "t_fill", "t_exit"])
    return t


def fx(t):
    return t[t.sym != "XAUUSD"]


def classify(t):
    w = t.usd_01 > 0
    c = np.where(w & (t.mfe_R >= 1), "won: rode a big move (went 3+ ATR our way)",
        np.where(w, "won: small win",
        np.where(t.mfe_R >= 1, "lost: gave back a big open profit",
        np.where(t.mfe_R < 0.33, "lost: never worked (never got 1 ATR our way)", "lost: worked a bit, then reversed"))))
    return pd.Series(c, index=t.index)


def anatomy(t):
    if t is None or len(t) == 0:
        return None
    u = t.usd_01
    tot = u.sum()
    srt = u.sort_values(ascending=False)
    d = dict(trades=len(t), total_usd=round(float(tot), 0),
             top10_usd=round(float(srt.head(10).sum()), 0),
             top10_share_of_total=(round(float(srt.head(10).sum() / tot), 2) if tot > 0 else None),
             usd_per_trade_without_top10=round(float(srt.iloc[10:].mean()), 3) if len(t) > 10 else None,
             end_of_stage_trades=int((t.exit_reason == "END").sum()),
             end_of_stage_usd=round(float(t[t.exit_reason == "END"].usd_01.sum()), 0),
             pairs=int(t.sym.nunique()))
    pp = t.groupby("sym").usd_01.sum()
    d["pairs_positive"] = int((pp > 0).sum())
    d["top3_pairs_usd"] = round(float(pp.sort_values(ascending=False).head(3).sum()), 0)
    d["top3_pairs_share_of_total"] = round(float(pp.sort_values(ascending=False).head(3).sum() / tot), 2) if tot > 0 else None
    cl = classify(t)
    g = pd.DataFrame({"reason": cl, "usd": u}).groupby("reason").agg(trades=("usd", "size"), usd_total=("usd", "sum"), usd_per_trade=("usd", "mean"))
    g["share_of_trades"] = (g.trades / len(t)).round(3)
    d["reasons"] = {k: dict(trades=int(r.trades), share_of_trades=float(r.share_of_trades), usd_total=round(float(r.usd_total), 0),
                            usd_per_trade=round(float(r.usd_per_trade), 2)) for k, r in g.iterrows()}
    d["median_nights_winners"] = float(t[u > 0].nights.median()) if (u > 0).any() else None
    d["median_nights_losers"] = float(t[u <= 0].nights.median()) if (u <= 0).any() else None
    d["avg_mfe_atr3_losers"] = round(float(t[u <= 0].mfe_R.mean()), 2)
    d["avg_mae_atr3_winners"] = round(float(t[u > 0].mae_R.mean()), 2)
    d["worst_trade_usd"] = round(float(u.min()), 0)
    d["best_trade_usd"] = round(float(u.max()), 0)
    d["trades_with_data_hole"] = int((t.max_gap_days > 5).sum())
    return d


def main():
    res = {}
    allkeys = []
    for f in os.listdir(OUT):
        if f.startswith("trades_") and f.endswith(".csv") and "READABLE" not in f and "ALLSTAGES" not in f:
            parts = f[len("trades_"):-4].split("_", 1)
            if parts[0] in STAGES:
                allkeys.append((parts[1], parts[0]))
    # ---- anatomy per variant and stage (FX24)
    ana = {}
    for key, st in sorted(allkeys):
        t = trades_of(key, st)
        ana["%s|%s" % (key, st)] = anatomy(fx(t))
    res["anatomy_fx24"] = ana
    # ---- direction x year for each OPP baseline-stage
    rows = []
    for key, st in sorted(allkeys):
        t = fx(trades_of(key, st))
        t = t.assign(year=t.t_signal.dt.year)
        for (y, sd), g in t.groupby(["year", "side"]):
            rows.append(dict(variant=key, stage=st, year=y, side="BUY" if sd == 1 else "SELL", trades=len(g),
                             usd_per_trade=round(float(g.usd_01.mean()), 2), usd_total=round(float(g.usd_01.sum()), 0)))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "autopsy_direction_by_year.csv"), index=False)
    # ---- pair stability: do the same pairs win in successive stages?
    stab = []
    for key in sorted({k for k, s in allkeys}):
        pp = {}
        for st in STAGES:
            t = trades_of(key, st)
            if t is not None:
                pp[st] = fx(t).groupby("sym").usd_01.mean()
        if len(pp) >= 2:
            df = pd.DataFrame(pp)
            for a_, b_ in (("EXPLORE", "VALIDATE"), ("VALIDATE", "STRESS"), ("EXPLORE", "STRESS")):
                if a_ in df and b_ in df:
                    x = df[[a_, b_]].dropna()
                    if len(x) >= 8:
                        stab.append(dict(variant=key, from_stage=a_, to_stage=b_, pairs=len(x),
                                         rank_correlation=round(float(x[a_].rank().corr(x[b_].rank())), 3),
                                         share_of_pairs_positive_in_both=round(float(((x[a_] > 0) & (x[b_] > 0)).mean()), 2),
                                         share_positive_first=round(float((x[a_] > 0).mean()), 2),
                                         share_positive_second=round(float((x[b_] > 0).mean()), 2)))
    pd.DataFrame(stab).to_csv(os.path.join(OUT, "autopsy_pair_stability.csv"), index=False)
    res["pair_stability"] = stab
    # ---- TRAIL: did the stop throw away the trend?
    tr_rows = []
    for rule in T.RULES:
        tt = trades_of("%s_TRAIL" % rule, "EXPLORE")
        oo = trades_of("%s_OPP" % rule, "EXPLORE")
        if tt is None:
            continue
        tt, oo = fx(tt), fx(oo)
        sl = tt[tt.exit_reason.isin(["SL", "GAP_SL"])]
        cont = []
        for r in sl.itertuples():
            a = T.load(r.sym)
            j = int(r.i_exit)
            if j + 20 < a.n and a.seg_end[j] >= j + 20:
                cont.append((a.c[j + 20] - r.exit) * r.side / a.atr[int(r.i_sig)])
        cont = np.array(cont)
        tr_rows.append(dict(rule=rule, trail_trades=len(tt), share_ended_by_stop=round(len(sl) / len(tt), 3),
                            trail_usd_per_trade=round(float(tt.usd_01.mean()), 2), opp_usd_per_trade=round(float(oo.usd_01.mean()), 2),
                            trail_avg_nights=round(float(tt.nights.mean()), 1), opp_avg_nights=round(float(oo.nights.mean()), 1),
                            trail_avg_win=round(float(tt[tt.usd_01 > 0].usd_01.mean()), 1), opp_avg_win=round(float(oo[oo.usd_01 > 0].usd_01.mean()), 1),
                            trail_right_in_100=round(100 * float((tt.usd_01 > 0).mean()), 1), opp_right_in_100=round(100 * float((oo.usd_01 > 0).mean()), 1),
                            price_kept_going_after_stop_in_100=round(100 * float((cont > 0).mean()), 1),
                            avg_move_after_stop_in_atr=round(float(cont.mean()), 2)))
    pd.DataFrame(tr_rows).to_csv(os.path.join(OUT, "autopsy_trail_mechanism.csv"), index=False)
    res["trail_mechanism"] = tr_rows
    # ---- refinement effect by skeleton, EXPLORE and VALIDATE
    ref = []
    for st in ("EXPLORE", "VALIDATE"):
        fn = "explore_baselines.csv" if st == "EXPLORE" else "validate_results.csv"
        b = pd.read_csv(os.path.join(OUT, fn))
        b = b[b.instrument == "FX24"].set_index("variant")
        if st == "EXPLORE":
            r_ = pd.read_csv(os.path.join(OUT, "explore_refined.csv"))
            r_ = r_[r_.instrument == "FX24"].set_index("variant")
            b = pd.concat([b, r_[r_.columns.intersection(b.columns)]])
        for rid in ("R1", "R2"):
            for rule in T.RULES:
                base, rk = "%s_OPP" % rule, "%s_%s_OPP" % (rid, rule)
                if base in b.index and rk in b.index:
                    ref.append(dict(stage=st, refinement=rid, skeleton=rule, base_trades=int(b.loc[base].trades), refined_trades=int(b.loc[rk].trades),
                                    base_usd=b.loc[base].usd_per_trade, refined_usd=b.loc[rk].usd_per_trade,
                                    base_pf=b.loc[base].profit_factor, refined_pf=b.loc[rk].profit_factor,
                                    improved=bool(b.loc[rk].usd_per_trade > b.loc[base].usd_per_trade)))
    rf = pd.DataFrame(ref)
    rf.to_csv(os.path.join(OUT, "autopsy_refinement_effect.csv"), index=False)
    res["refinement_effect"] = ref
    # ---- sign of the rule vs always-short / always-long, all stages
    ctrl_rows = []
    for st in STAGES:
        fn = os.path.join(OUT, "%s_controls.json" % st.lower())
        if not os.path.exists(fn):
            continue
        c = json.load(open(fn))
        for k, v in c.items():
            d = v.get("FX24", {})
            if "directions" in d:
                x, l, lk = d["directions"], d["luck"], d["lookahead"]
                ctrl_rows.append(dict(stage=st, variant=k, trades=x["trades"], rule_right=x["rule_right_in_100"], rule_usd=x["rule_usd"],
                                      long_right=x["all_long_right_in_100"], long_usd=x["all_long_usd"],
                                      short_right=x["all_short_right_in_100"], short_usd=x["all_short_usd"],
                                      coin_right=x["coin_right_in_100"], coin_usd=x["coin_usd"],
                                      luck_right=l["right_in_100"], luck_usd=l["usd_per_trade"], luck_band_usd=l["usd_band"],
                                      luck_band_points=l["seed_band_points"], luck_n=l["n"],
                                      rule_minus_luck_usd=round(x["rule_usd"] - l["usd_per_trade"], 3),
                                      rule_minus_coin_usd=x["rule_minus_coin_usd"], picks_better_side_in_100=x["picks_better_side_in_100"],
                                      twin_points=lk.get("lookahead_points"), twin_usd=lk.get("lookahead_usd")))
    cdf = pd.DataFrame(ctrl_rows)
    cdf.to_csv(os.path.join(OUT, "autopsy_controls_all_stages.csv"), index=False)
    res["controls_rows"] = len(cdf)
    T.jdump("autopsy_analysis.json", res)
    print("analysis written")


if __name__ == "__main__":
    main()
