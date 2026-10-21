"""AUT13: why did the base trades win or lose? Reads the saved BASE trade files (EXPLORE / VALIDATE / STRESS)."""
import os, sys, json
import numpy as np, pandas as pd
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "AUT13_leadlag")
pd.set_option("display.width", 250)
USDPIP = {"XAUUSD": 1.0, "EURUSD": 1.0}
res = {}
for tg in ("XAUUSD", "EURUSD"):
    res[tg] = {}
    for st in ("EXPLORE", "VALIDATE", "STRESS"):
        t = pd.read_csv(os.path.join(OUT, "trades_%s_BASE_%s.csv" % (tg, st)))
        t["win"] = t.net_pips > 0
        d = {}
        d["trades"] = len(t)
        d["exit_mix_pct"] = (100 * t.exit_reason.value_counts(normalize=True)).round(1).to_dict()
        d["usd_by_exit"] = t.groupby("exit_reason").usd_01.mean().round(3).to_dict()
        d["right_by_exit"] = (100 * t.groupby("exit_reason").win.mean()).round(1).to_dict()
        d["avg_win_usd"] = round(t[t.win].usd_01.mean(), 3)
        d["avg_loss_usd"] = round(t[~t.win].usd_01.mean(), 3)
        d["breakeven_right_in_100"] = round(100 * -d["avg_loss_usd"] / (d["avg_win_usd"] - d["avg_loss_usd"]), 2)
        d["mfe_R_win"] = round(t[t.win].mfe_R.median(), 3)
        d["mae_R_loss"] = round(t[~t.win].mae_R.median(), 3)
        d["mfe_R_loss_median"] = round(t[~t.win].mfe_R.median(), 3)     # how far losers went the right way first
        d["pct_losers_that_reached_half_target"] = round(100 * (t[~t.win].mfe_R >= 0.5).mean(), 1)
        d["hold_min_median"] = round(t.hold_h.median() * 60, 1)
        if st == "EXPLORE":
            feats = ["z", "z_D", "z_T", "dollar_share", "breadth", "corr", "hour", "spr_fill", "T_k", "D_k"]
            d["winner_vs_loser_median"] = {f: [round(float(t[t.win][f].median()), 5), round(float(t[~t.win][f].median()), 5)]
                                           for f in feats if f in t}
            q = pd.qcut(t.z.abs(), 4, duplicates="drop")
            d["by_gap_size_quartile_usd"] = t.groupby(q, observed=True).usd_01.mean().round(3).rename(lambda x: str(x)).to_dict()
            q2 = pd.cut(t.dollar_share, [0, 0.25, 0.5, 0.75, 1.0], include_lowest=True)
            d["by_dollar_share_usd"] = t.groupby(q2, observed=True).usd_01.agg(["mean", "size"]).round(3).rename(index=lambda x: str(x)).to_dict("index")
            d["by_hour_usd"] = t.groupby("hour").usd_01.mean().round(2).to_dict()
        res[tg][st] = d
        print(tg, st, json.dumps(d, default=float)[:1500])
json.dump(res, open(os.path.join(OUT, "why_base.json"), "w"), indent=1, default=float)
# lead-lag timing evidence
for tg in ("XAUUSD", "EURUSD"):
    c = pd.read_csv(os.path.join(OUT, "diag_ccf_%s.csv" % tg))
    print(tg, c.pivot_table(index="lag_min", columns="stage", values="corr").loc[[-2, -1, 0, 1, 2]].round(4).to_string())
