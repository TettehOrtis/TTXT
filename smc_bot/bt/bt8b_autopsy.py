"""BT8b part 1: loss autopsy -- WHY do the broad REF candidates lose, and which PRE-TRADE conditions concentrate each mode.

    .venv/Scripts/python.exe smc_bot/bt/bt8b_autopsy.py

Universe: every FILLED broad candidate (independent simulation, 25 instruments, HIST 2005-20 + DEV 2021-23 + VAL 2024;
coverage-thin instrument-years removed). Also reported for the REF portfolio trades.

Failure modes (fixed before looking; path data from BT13 + bt8b_prep):
  CHOP        loser that left by the weekend / time exit (not stopped)                       -> "chop, time/weekend exit"
  SPIKE       stopped, and the 5-minute window ending at the stop-out minute spanned >= 3 x ATR14(M5) at the signal
              and moved against the trade (or a GAP_SL fill)                                    -> "news / volatility spike"
  HTF_FLIP    loser; the H4 internal trend was NOT against the trade at the signal but turned against it at an H4 close
              during the trade (h4_against_during)                                              -> "HTF bias flipped mid-trade"
  STOP_TIGHT  stopped, then the ORIGINAL TP was hit within 24 h of the stop                    -> "stop too tight"
  TOO_FAR     stopped after reaching >= +1R (mfe_R >= 1)                                        -> "right idea, target too far"
  PARTIAL     stopped after reaching +0.5R .. +1R
  NO_FT       stopped without ever reaching +0.5R                                               -> "no follow-through"
Exclusive primary label = first match in the order above. Flags (non-exclusive) are used for the condition scan.

Condition scan: for every feature (BT8's 32 + bigger-picture) the bottom and top quintile (pooled cut points) or each level
of a discrete feature. For each mode flag: rate among ALL filled trades in the condition vs expected rate, where the
expected rate conditions on RR quintile x era (RR mechanically drives most modes). Kept only if |lift| >= 15%, >= 500
trades in the condition, and the lift has the same direction in >= 4 of the 5 eras (2005-08, 2009-14, 2015-20, DEV, VAL).
Also: mean net R inside vs outside the condition, per era.
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt8b_lib as L                                              # noqa: E402

MODES = ["CHOP", "SPIKE", "HTF_FLIP", "STOP_TIGHT", "TOO_FAR", "PARTIAL", "NO_FT"]
ERAS = [e[0] for e in L.ERAS]
DESC = {  # plain-language names for the scan
    "f_n_bias_al": "bias aligned count (0-3)", "f_h4_rel": "H4 internal trend vs trade", "f_h4e_rel": "H4 external trend vs trade",
    "f_ema_rel": "H1 EMA200 side vs trade", "f_ema_dist": "distance from H1 EMA200 (H1 ATR, + = trade side)",
    "f_h4_age": "H4 trend age", "f_h4_evt_age": "bars since last H4 break", "f_dpos": "zone depth in H4 range (0 = deep discount/premium)",
    "f_epos": "entry depth in H4 range", "f_dr_width": "H4 dealing-range width", "f_zone_ob": "zone is an OB (vs FVG)",
    "f_zone_size": "zone size (M30 ATR)", "f_zone_age": "zone age at touch", "f_sweep5": "M5 sweep before CHoCH",
    "f_sweep5_depth": "M5 sweep depth", "f_sweep30": "M30 sweep", "f_bars_tc": "M5 bars touch->CHoCH",
    "f_choch_disp": "CHoCH displacement", "f_leg": "CHoCH leg size (M5 ATR)", "f_entry_fvg": "entry on an M5 FVG",
    "f_pullback": "pullback depth to the limit (M5 ATR)", "f_stop_atr": "stop size (M5 ATR)", "f_stop_comm": "stop size vs commission",
    "f_rr": "planned RR (log)", "f_tp_atr1": "target distance (H1 ATR)", "f_ldn": "London killzone", "f_dow_tue": "Tuesday",
    "f_dow_wed": "Wednesday", "f_dow_thu": "Thursday", "f_vol5": "M5 volatility vs 10-day norm", "f_vol1": "H1 volatility vs norm",
    "f_atr4_1": "H4/H1 ATR ratio",
    "bp_d1_ema_rel": "D1 EMA50 side vs trade", "bp_d1_ema_dist": "distance from D1 EMA50 (D1 ATR, + = trade side)",
    "bp_w1_ema_rel": "W1 EMA20 side vs trade", "bp_w1_ema_dist": "distance from W1 EMA20 (W1 ATR, + = trade side)",
    "bp_d1_struct": "D1 structure trend vs trade", "bp_w1_struct": "W1 structure trend vs trade",
    "bp_room_pdx": "room to previous-day extreme in trade direction (D1 ATR)", "bp_back_pdx": "distance to previous-day extreme behind",
    "bp_room_pwx": "room to previous-week extreme (D1 ATR)", "bp_back_pwx": "distance to previous-week extreme behind",
    "bp_room_20d": "room to 20-day extreme (D1 ATR)", "bp_pos20": "entry position in 20-day range (1 = at target-side extreme)",
    "bp_tp_vs_pdx": "TP beyond previous-day extreme (D1 ATR)", "bp_today_move": "today's move so far with the trade (D1 ATR)",
    "bp_today_rng": "today's range so far (D1 ATR)", "bp_er_h4": "H4 efficiency ratio (trendiness)",
    "bp_er_h4_dir": "H4 efficiency ratio signed with trade", "bp_er_d1": "D1 efficiency ratio", "bp_er_d1_dir": "D1 efficiency ratio signed with trade",
    "bp_adx_d1": "D1 ADX", "bp_volpct_d1": "D1 ATR percentile (1y)", "bp_atr_d1_rel": "D1 ATR14 vs ATR100",
    "bp_since_bigd1": "days since last big D1 range", "bp_cs1": "1d currency-strength alignment", "bp_cs5": "5d currency-strength alignment",
    "bp_cs5_rank": "5d strength rank alignment", "bp_risk1": "risk-on basket 1d", "bp_risk5": "risk-on basket 5d", "bp_gold5": "gold 5d",
    "bp_risk_align5": "trade aligned with 5d risk-on/off", "bp_dom": "day of month", "bp_to_me": "days to month end",
    "bp_wom": "week of month", "bp_hour": "UTC hour of signal",
    "side": "direction (1 long / -1 short)", "cross": "instrument is a cross (not the original 9)",
}


def load():
    c = L.load_cands()
    F = pd.read_pickle(os.path.join(L.OUT, "feat_bp.pkl"))
    P = pd.read_pickle(os.path.join(L.OUT, "policy_outcomes.pkl"))
    c = c.merge(F, on="cid", how="left").merge(P[["cid", "spike5_atr", "spike5_against", "exitbar_atr"]], on="cid", how="left")
    assert (c.t_feat_bp <= c.t_signal).all()
    return c


def label(f):
    sl = f.exit_reason.isin(["SL", "GAP_SL"])
    los = f.R < 0
    fl = pd.DataFrame(index=f.index)
    fl["CHOP"] = los & ~sl
    fl["SPIKE"] = sl & (((f.spike5_atr >= 3) & (f.spike5_against == 1)) | (f.exit_reason == "GAP_SL"))
    fl["HTF_FLIP"] = los & (f.f_h4_rel != -1) & (f.h4_against_during == 1)
    fl["STOP_TIGHT"] = sl & (f.sl_then_tp_24h == 1)
    fl["TOO_FAR"] = sl & (f.mfe_R >= 1)
    fl["PARTIAL"] = sl & (f.mfe_R >= 0.5) & (f.mfe_R < 1)
    fl["NO_FT"] = sl & (f.mfe_R < 0.5)
    prim = pd.Series("WIN_OR_BE", index=f.index)
    for m in MODES[::-1]:
        prim[fl[m] & los] = m
    prim[(~los)] = "WINNER"
    # a loser with none of the flags (should not happen: every SL loser has an MFE class)
    return fl.astype(bool), prim


def breakdown(f, prim, fl, tag):
    rows = []
    los = f.R < 0
    for era in ERAS + ["ALL"]:
        m = (f.era == era) if era != "ALL" else pd.Series(True, index=f.index)
        n_l = int((los & m).sum())
        r = dict(set=tag, era=era, filled=int(m.sum()), losers=n_l, loss_R=round(f.R[los & m].sum(), 1))
        for md in MODES:
            k = (prim == md) & m
            r[md] = int(k.sum()); r[md + "_pct"] = round(100 * k.sum() / max(n_l, 1), 1)
            r[md + "_R"] = round(f.R[k].sum(), 1)
        for md in ("SPIKE", "HTF_FLIP", "STOP_TIGHT", "TOO_FAR"):
            r["flag_" + md + "_pct"] = round(100 * (fl[md] & m).sum() / max(n_l, 1), 1)
        rows.append(r)
    return pd.DataFrame(rows)


def conditions(f):
    """Boolean masks for bottom/top quintile of continuous features, each level of discrete features."""
    feats = [x for x in f.columns if x.startswith("f_") or x.startswith("bp_")] + ["side", "cross"]
    out = {}
    for x in feats:
        v = f[x]
        u = v.dropna().unique()
        if len(u) <= 5:
            for lv in sorted(u):
                out[(x, f"= {lv:g}")] = (v == lv).values
        else:
            q1, q4 = v.quantile([0.2, 0.8])
            out[(x, f"<= {q1:.3g} (Q1)")] = (v <= q1).values
            out[(x, f">= {q4:.3g} (Q5)")] = (v >= q4).values
    return out


def scan(f, fl):
    f = f.copy()
    f["rrq"] = pd.qcut(f.rr, 5, labels=False, duplicates="drop")
    cond = conditions(f)
    era = f.era.values
    rows = []
    for md in MODES + ["WIN"]:
        y = (f.R > 0).values if md == "WIN" else fl[md].values
        # expected rate given RR quintile x era
        e = pd.Series(y).groupby([f.rrq.values, era]).transform("mean").values
        base = y.mean()
        for (x, lv), m in cond.items():
            n = int(m.sum())
            if n < 500:
                continue
            O, E = y[m].sum(), e[m].sum()
            lift = O / E if E > 0 else np.nan
            raw = y[m].mean() / base
            el = {}
            for er in ERAS:
                mm = m & (era == er)
                if mm.sum() < 30 or e[mm].sum() <= 0:
                    el[er] = np.nan
                else:
                    el[er] = y[mm].sum() / e[mm].sum()
            same = sum(1 for v in el.values() if v == v and (v > 1) == (lift > 1))
            Rin = f.R.values[m].mean(); Rout = f.R.values[~m].mean()
            rin_e = {er: f.R.values[m & (era == er)].mean() - f.R.values[~m & (era == er)].mean() for er in ERAS}
            sameR = sum(1 for v in rin_e.values() if v == v and np.sign(v) == np.sign(Rin - Rout))
            rows.append(dict(mode=md, feature=x, desc=DESC.get(x, x), cond=lv, n=n, events=int(O), rate=round(y[m].mean(), 3),
                             base=round(base, 3), lift_raw=round(raw, 2), lift_adj=round(lift, 2),
                             eras_same=same, **{f"lift_{er}": round(v, 2) if v == v else None for er, v in el.items()},
                             netR_in=round(Rin, 3), netR_out=round(Rout, 3), R_eras_same=sameR,
                             **{f"dR_{er}": round(v, 3) if v == v else None for er, v in rin_e.items()}))
    S = pd.DataFrame(rows)
    S["keep"] = (S.lift_adj.sub(1).abs() >= 0.15) & (S.eras_same >= 4)
    return S


def main():
    c = load()
    c["cross"] = (~c.sym.isin(L.ORIG9)).astype(int)
    f = c[(c.status == "filled") & ~c.excluded_cov].copy()
    fl, prim = label(f)
    f["mode"] = prim
    for m in MODES:
        f["flag_" + m] = fl[m]
    B = breakdown(f, prim, fl, "broad_all25")
    # REF portfolio trades (original 9), for comparison
    p = L.portfolio(c[~c.excluded_cov], c.ref_gate[~c.excluded_cov])
    pf = p[(p.status == "filled") & p.sym.isin(L.ORIG9)]
    Bref = breakdown(f.loc[pf.cid.map(dict(zip(f.cid, f.index))).dropna().astype(int)],
                     prim.loc[pf.cid.map(dict(zip(f.cid, f.index))).dropna().astype(int)],
                     fl.loc[pf.cid.map(dict(zip(f.cid, f.index))).dropna().astype(int)], "REF_orig9")
    BD = pd.concat([B, Bref]); BD.to_csv(os.path.join(L.OUT, "autopsy_breakdown.csv"), index=False)
    print(BD[["set", "era", "filled", "losers", "loss_R"] + [m + "_pct" for m in MODES]].to_string(index=False))
    print(BD[["set", "era"] + [m + "_R" for m in MODES] + [c_ for c_ in BD.columns if c_.startswith("flag_")]].to_string(index=False))
    # path profile per mode
    prof = f.groupby("mode").agg(n=("R", "size"), R=("R", "mean"), rr=("rr", "median"), mfe=("mfe_R", "median"),
                                 t_mfe_min=("t_mfe_min", "median"), t_mae_min=("t_mae_min", "median"),
                                 hold_h=("hold_h", "median"), stop_atr5=("f_stop_atr", "median")).round(2)
    prof.to_csv(os.path.join(L.OUT, "autopsy_mode_profile.csv")); print(prof.to_string())
    S = scan(f, fl)
    S.to_csv(os.path.join(L.OUT, "autopsy_condition_scan.csv"), index=False)
    K = S[S.keep].copy()
    K["abslog"] = np.abs(np.log(K.lift_adj))
    K = K.sort_values(["mode", "abslog"], ascending=[True, False])
    K.to_csv(os.path.join(L.OUT, "autopsy_findings_consistent.csv"), index=False)
    pd.set_option("display.width", 250)
    for md in MODES + ["WIN"]:
        k = K[K["mode"] == md].head(12)
        print(f"\n=== {md}: {len(K[K['mode'] == md])} consistent conditions (of {len(S[S['mode'] == md])})")
        print(k[["desc", "cond", "n", "events", "rate", "base", "lift_raw", "lift_adj", "eras_same"] +
                [f"lift_{e}" for e in ERAS] + ["netR_in", "netR_out", "R_eras_same"]].to_string(index=False))
    f[["cid", "sym", "t_signal", "side", "era", "R", "mode"] + ["flag_" + m for m in MODES]].to_pickle(os.path.join(L.OUT, "autopsy_labels.pkl"))


if __name__ == "__main__":
    main()
