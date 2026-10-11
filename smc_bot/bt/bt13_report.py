"""BT13 aggregation: tables, pre-registered verdict, duka-vs-zero overlap, CSVs, examples.

    .venv/Scripts/python.exe smc_bot/bt/bt13_report.py        (after bt13_run.py all)
Reads results/BT13/parts/*.pkl written by bt13_run.py; writes results/BT13/*.csv|txt|json.
"""
import os, sys, json, glob
os.environ["SMC_DATASET"] = "duka"
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                                   # noqa: E402
from common import RESULTS, stats                               # noqa: E402
import bt13_run as T                                            # noqa: E402  (sets the duka bt6b cache first)

OUT = os.path.join(RESULTS, "BT13"); PARTS = os.path.join(OUT, "parts")
ORIG9 = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "XAUUSD"]
CROSS15 = list(common.EXTRA)
ALL25 = ORIG9 + ["EURGBP"] + CROSS15
ERAS = [("2005-08", 2005, 2008), ("2009-14", 2009, 2014), ("2015-20", 2015, 2020)]
RNG = np.random.default_rng(13)


def load_parts(kind, period):
    fs = [os.path.join(PARTS, f"{s}_{period}_{kind}.pkl") for s in ALL25]
    dfs = [pd.read_pickle(f) for f in fs if os.path.exists(f)]
    dfs = [d for d in dfs if len(d)]
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def cov_excl():
    cv = pd.read_csv(os.path.join(OUT, "coverage_minutes.csv"))
    return set(zip(cv[cv.excluded].sym, cv[cv.excluded].year))


def filled(t):
    return t[t.status == "filled"].copy()


def era_of(y):
    for n, a, b in ERAS:
        if a <= y <= b:
            return n
    return "other"


def week_boot(R, weeks, n=5000):
    """Block bootstrap by calendar week: resample weeks with replacement; returns (se of mean, 95% CI, P(mean<=0))."""
    df = pd.DataFrame(dict(R=R, w=weeks))
    g = df.groupby("w").R.agg(["sum", "count"])
    s, c = g["sum"].values, g["count"].values
    k = len(g)
    idx = RNG.integers(0, k, size=(n, k))
    means = s[idx].sum(1) / c[idx].sum(1)
    return means.std(ddof=1), np.quantile(means, [0.025, 0.975]), (means <= 0).mean()


def row(g, name=None, boot=False):
    if not len(g):
        return dict(group=name, trades=0)
    s = stats(g)
    R = g.R.values
    d = dict(group=name, trades=len(g), win_pct=s["win_pct"], gross_R=round(g.gross_R.mean(), 3), net_R=round(R.mean(), 3),
             total_net_R=round(R.sum(), 1), PF=s["PF"], t=round(R.mean() / (R.std(ddof=1) / np.sqrt(len(R))), 2) if len(R) > 1 else np.nan,
             maxDD_R=s["maxDD_R"], usd_01=s["usd_per_01lot"], per_week=s["per_week"], avg_hold_h=s["avg_hold_h"])
    if boot and len(g) > 5:
        wk = g.t_signal.dt.to_period("W").astype(str).values
        se, ci, p0 = week_boot(R, wk)
        seg, cig, _ = week_boot(g.gross_R.values, wk)
        d.update(t_boot_week=round(R.mean() / se, 2), ci95_net=f"[{ci[0]:+.3f}, {ci[1]:+.3f}]", p_net_le0=round(p0, 4),
                 t_boot_gross=round(g.gross_R.mean() / seg, 2))
    return d


def table(t, by, boot=False):
    rows = []
    for k, g in t.groupby(by, sort=True):
        rows.append(row(g, k if not isinstance(k, tuple) else "|".join(map(str, k)), boot))
    return pd.DataFrame(rows)


def verdict(t, syms, need_inst, label):
    f = t[t.sym.isin(syms)]
    if len(f) < 6:
        return dict(label=label, trades=len(f))
    p = row(f, label, boot=True)
    eras = {n: f[f.era == n].R.mean() for n, _, _ in ERAS}
    eras_g = {n: f[f.era == n].gross_R.mean() for n, _, _ in ERAS}
    inst = f.groupby("sym").gross_R.mean()
    inst_n = f.groupby("sym").R.mean()
    c1 = p["net_R"] > 0 and p["t_boot_week"] >= 2.5
    c2 = sum(v > 0 for v in eras.values()) >= 2
    c3 = int((inst > 0).sum()) >= need_inst
    return dict(label=label, **p, era_net={k: round(v, 3) for k, v in eras.items()},
                era_gross={k: round(v, 3) for k, v in eras_g.items()},
                inst_gross_pos=f"{int((inst > 0).sum())}/{len(inst)}", inst_net_pos=f"{int((inst_n > 0).sum())}/{len(inst_n)}",
                C1_net_pos_t25=bool(c1), C2_eras=bool(c2), C3_instruments=bool(c3), CONFIRMED=bool(c1 and c2 and c3))


def prep(t, excl):
    t = filled(t)
    t["year"] = t.t_signal.dt.year
    t["era"] = t.year.map(era_of)
    t["excluded_cov"] = [(s, y) in excl for s, y in zip(t.sym, t.year)]
    t["dir"] = np.where(t.side == 1, "long", "short")
    t["grp"] = np.where(t.sym.isin(ORIG9), "ORIG9", np.where(t.sym == "EURGBP", "EURGBP", "CROSS15"))
    return t


def main():
    excl = cov_excl()
    res = {}
    H = load_parts("ref", "HIST"); HE = load_parts("ref_era", "HIST")
    D = load_parts("ref", "DEV"); V = load_parts("ref", "VAL")
    for nm, df in (("HIST", H), ("DEV", D), ("VAL", V)):
        df["t_choch_open"] = df.t_signal - pd.Timedelta(minutes=5)
    # ---------------- feature columns from the candidate set (REF gate + portfolio emulation)
    import bt8_ml as M
    feat_rows = {}
    for per in ("HIST", "DEV", "VAL"):
        c = load_parts("cand", per)
        if not len(c):
            continue
        p = M.portfolio(c, c.ref_gate)
        pf = p[p.status == "filled"]
        ref = {"HIST": H, "DEV": D, "VAL": V}[per]
        rf = ref[ref.status == "filled"]
        k1 = set(zip(pf.sym, pf.t_signal, pf.R.round(6))); k2 = set(zip(rf.sym, rf.t_signal, rf.R.round(6)))
        res[f"cand_emul_check_{per}"] = dict(emulated=len(k1), ref=len(k2), identical=len(k1 & k2), only_emul=len(k1 - k2),
                                              only_ref=len(k2 - k1))
        c = c.reset_index(drop=True)
        c["cid"] = [f"{per[0]}{i:06d}" for i in range(len(c))]                 # BT8 conventions for BT8b
        c["w_grp"] = 1.0 / c.groupby(["sym", "t_signal"]).cid.transform("count")
        fc = [x for x in c.columns if x.startswith("f_")] + ["bias_c", "bias_t", "bias_x", "dpos_raw", "zone_lo", "zone_hi"]
        feat_rows[per] = p[["sym", "t_signal"] + fc].drop_duplicates(["sym", "t_signal"])
        c.to_pickle(os.path.join(OUT, f"candidates_{per}.pkl"))
        c.to_csv(os.path.join(OUT, f"candidates_{per}.csv"), index=False)
        res[f"candidates_{per}"] = dict(rows=len(c), filled=int((c.status == "filled").sum()), ref_gate=int(c.ref_gate.sum()),
                                        instruments=int(c.sym.nunique()))
    for per, df in (("HIST", H), ("DEV", D), ("VAL", V)):
        if per in feat_rows:
            df = df.merge(feat_rows[per], on=["sym", "t_signal"], how="left")
        df["year"] = df.t_signal.dt.year
        df["excluded_cov"] = [(s, y) in excl for s, y in zip(df.sym, df.year)]
        df.to_csv(os.path.join(OUT, f"trades_REF_{per}.csv"), index=False)
        if per == "HIST":
            H = df
        elif per == "DEV":
            D = df
        else:
            V = df
    HE.to_csv(os.path.join(OUT, "trades_REF_HIST_eraspread.csv"), index=False)

    h = prep(H, excl); he = prep(HE, excl)
    hp = h[~h.excluded_cov]; hep = he[~he.excluded_cov]
    # (b) deterministic era cost: the extra spread paid in full on every trade (conservative upper bound)

    hd = hp.copy()
    xs = np.array([T.era_extra_pips(s, np.array([y]))[0] for s, y in zip(hd.sym, hd.year)])
    hd["R"] = hd.R - xs / hd.risk_pips; hd["gross_R"] = hd.gross_R - xs / hd.risk_pips
    hd["usd_01"] = hd.usd_01 - xs * hd.sym.map(common.USD_PIP_01)
    lines = []

    def out(name, df):
        df.to_csv(os.path.join(OUT, f"{name}.csv"), index=False)
        lines.append(f"\n## {name}\n" + df.to_string(index=False))

    # ---------------- verdicts
    V1 = verdict(hp, ORIG9, 6, "PRIMARY ORIG9 HIST (coverage exclusions removed)")
    V1b = verdict(h, ORIG9, 6, "ORIG9 HIST incl. thin years")
    V2 = verdict(hp, CROSS15, 9, "SECONDARY CROSS15 HIST (coverage exclusions removed)")
    V2b = verdict(h, CROSS15, 9, "CROSS15 HIST incl. thin years")
    V3 = verdict(hep, ORIG9, 6, "ORIG9 HIST, era spread")
    V4 = verdict(hep, CROSS15, 9, "CROSS15 HIST, era spread")
    V5 = verdict(hd, ORIG9, 6, "ORIG9 HIST, era spread deducted per trade")
    V6 = verdict(hd, CROSS15, 9, "CROSS15 HIST, era spread deducted per trade")
    res["verdicts"] = [V1, V1b, V2, V2b, V3, V4, V5, V6]
    res["excluded_instrument_years"] = sorted([f"{s} {y}" for s, y in excl])
    res["excluded_trades"] = int(h.excluded_cov.sum())
    # ---------------- tables
    out("HIST_by_group", table(hp, "grp", boot=True))
    out("HIST_by_era_group", table(hp, ["grp", "era"], boot=True))
    out("HIST_by_instrument", table(hp, ["grp", "sym"]))
    out("HIST_by_instrument_era", table(hp, ["sym", "era"]))
    out("HIST_by_year_ORIG9", table(hp[hp.grp == "ORIG9"], "year"))
    out("HIST_by_year_CROSS15", table(hp[hp.grp == "CROSS15"], "year"))
    out("HIST_eraspread_by_group", table(hep, ["grp", "era"], boot=True))
    out("HIST_eraspread_by_instrument", table(hep, ["grp", "sym"]))
    out("HIST_eracost_deducted_by_group", table(hd, ["grp", "era"], boot=True))
    out("HIST_session", table(hp, ["grp", "session"], boot=True))
    out("HIST_direction", table(hp, ["grp", "dir"], boot=True))
    out("HIST_by_group_incl_thin", table(h, "grp", boot=True))
    out("HIST_exit_reasons", hp.groupby(["grp", "exit_reason"]).R.agg(["count", "mean"]).round(3).reset_index())
    # top-5 removed and leave-one-out
    rows = []
    for g, syms in (("ORIG9", ORIG9), ("CROSS15", CROSS15)):
        f = hp[hp.sym.isin(syms)]
        for k in (0, 3, 5, 10, 20):
            ff = f.drop(f.R.nlargest(k).index) if k else f
            rows.append(dict(group=g, removed_top=k, trades=len(ff), gross_R=round(ff.gross_R.mean(), 3), net_R=round(ff.R.mean(), 3)))
    out("HIST_top_winners_removed", pd.DataFrame(rows))
    rows = []
    for g, syms in (("ORIG9", ORIG9), ("CROSS15", CROSS15)):
        for s in syms:
            ff = hp[hp.sym.isin(syms) & (hp.sym != s)]
            if not len(ff):
                continue
            wk = ff.t_signal.dt.to_period("W").astype(str).values
            se, _, _ = week_boot(ff.R.values, wk, 2000)
            rows.append(dict(group=g, removed=s, trades=len(ff), gross_R=round(ff.gross_R.mean(), 3), net_R=round(ff.R.mean(), 3),
                             t_boot=round(ff.R.mean() / se, 2)))
    out("HIST_leave_one_instrument_out", pd.DataFrame(rows))
    # ---------------- DEV / VAL on duka + overlap with Zero canonical
    dv = []
    for per, df in (("DEV", D), ("VAL", V)):
        f = prep(df, excl)
        for g, syms in (("ORIG9", ORIG9), ("CROSS15", CROSS15)):
            dv.append({**row(f[f.sym.isin(syms)], f"{per} {g}", boot=True)})
    out("DUKA_DEV_VAL", pd.DataFrame(dv))
    ov = []
    matched_all = []
    for per, df in (("DEV", D), ("VAL", V)):
        z = pd.read_csv(os.path.join(RESULTS, "RECON", f"trades_CANON_{per}.csv"), parse_dates=["t_signal", "t_fill", "t_exit"])
        z = z[z.t_signal < "2025-01-01"].copy(); z["t_signal"] = z.t_signal.astype("datetime64[ns]")
        df = df.copy(); df["t_signal"] = df.t_signal.astype("datetime64[ns]")
        k = df[df.sym.isin(ORIG9) & (df.t_signal < "2025-01-01")]
        for stage, zz, kk in (("orders", z, k), ("filled", z[z.status == "filled"], k[k.status == "filled"])):
            m_exact = zz.merge(kk, on=["sym", "t_signal", "side"], suffixes=("_z", "_d"))
            # tolerance match: same sym/side, |dt| <= 30 min (nearest)
            zz2 = zz.sort_values("t_signal"); kk2 = kk.sort_values("t_signal")
            m_tol = pd.merge_asof(zz2, kk2, on="t_signal", by=["sym", "side"], tolerance=pd.Timedelta("30min"),
                                  direction="nearest", suffixes=("_z", "_d"))
            n_tol = int(m_tol["entry_d"].notna().sum())
            d = dict(period=per, stage=stage, zero=len(zz), duka=len(kk), exact_match=len(m_exact), tol30_match=n_tol,
                     pct_of_zero_exact=round(100 * len(m_exact) / max(1, len(zz)), 1),
                     pct_of_duka_exact=round(100 * len(m_exact) / max(1, len(kk)), 1),
                     pct_of_zero_tol30=round(100 * n_tol / max(1, len(zz)), 1))
            if stage == "filled" and len(m_exact) > 2:
                d["R_corr_exact"] = round(np.corrcoef(m_exact.R_z, m_exact.R_d)[0, 1], 3)
                d["same_exit_reason_pct"] = round(100 * (m_exact.exit_reason_z == m_exact.exit_reason_d).mean(), 1)
                d["zero_net_matched"] = round(m_exact.R_z.mean(), 3); d["duka_net_matched"] = round(m_exact.R_d.mean(), 3)
                d["entry_diff_pips_med"] = round((m_exact.entry_z - m_exact.entry_d).abs().div(m_exact.sym.map(common.pip)).median(), 2)
                d["zero_net_all"] = round(zz.R.mean(), 3); d["duka_net_all"] = round(kk.R.mean(), 3)
                matched_all.append(m_exact.assign(period=per))
            ov.append(d)
    out("DUKA_vs_ZERO_overlap", pd.DataFrame(ov))
    if matched_all:
        pd.concat(matched_all).to_csv(os.path.join(OUT, "DUKA_vs_ZERO_matched_trades.csv"), index=False)
    # ---------------- funnels
    fun = {}
    for per in ("HIST", "DEV", "VAL"):
        tot = {}
        for s in ALL25:
            f = os.path.join(PARTS, f"{s}_{per}_funnel.pkl")
            if os.path.exists(f):
                for k, v in pd.read_pickle(f).items():
                    tot[k] = tot.get(k, 0) + v
        fun[per] = dict(sorted(tot.items()))
    res["funnels_all25"] = fun
    # ---------------- path info summary (for BT8b)
    pi = hp.groupby("grp")[["mfe_R", "mae_R", "t_mfe_min", "t_mae_min", "h4_against_during", "bias_against_during",
                            "sl_then_tp_24h"]].mean().round(3).reset_index()
    out("HIST_path_summary", pi)
    # ---------------- examples: 10 from HIST ORIG9 (5 winners, 5 losers, random)
    cols = ["sym", "side", "zone_kind", "t_zone", "zone_lo", "zone_hi", "t_touch", "t_choch_open", "t_signal", "session",
            "entry", "sl", "tp", "rr", "t_fill", "fill", "t_exit", "exit", "exit_reason", "gross_R", "R", "usd_01",
            "mfe_R", "mae_R"]
    o9 = hp[hp.grp == "ORIG9"]
    ex = pd.concat([o9[o9.R > 0].sample(5, random_state=13), o9[o9.R <= 0].sample(5, random_state=13)]).sort_values("t_signal")
    ex[cols].to_csv(os.path.join(OUT, "examples_HIST.csv"), index=False)
    lines.append("\n## examples_HIST\n" + ex[cols].to_string(index=False))
    with open(os.path.join(OUT, "bt13_summary.json"), "w") as fh:
        json.dump(res, fh, indent=1, default=str)
    with open(os.path.join(OUT, "bt13_tables.txt"), "w") as fh:
        fh.write(json.dumps(res, indent=1, default=str) + "\n" + "\n".join(lines))
    print(json.dumps(res, indent=1, default=str))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
