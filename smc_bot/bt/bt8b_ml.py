"""BT8b: ML on 2005-2024 broad REF candidates -- take/skip, target choice, break-even, size tiers.

    .venv/Scripts/python.exe smc_bot/bt/bt8b_ml.py hist            # yearly walk-forward 2006..2020, all variants, baselines
    .venv/Scripts/python.exe smc_bot/bt/bt8b_ml.py shuffle [n]     # shuffle-label test (HIST-OOS)
    .venv/Scripts/python.exe smc_bot/bt/bt8b_ml.py final           # frozen choice -> DEV (2021-23) and VAL (2024), ONCE
    .venv/Scripts/python.exe smc_bot/bt/bt8b_ml.py shap            # SHAP explanations (HIST-OOS, V02 fold models)

PRE-REGISTERED (written before any model result was seen)
Data: BT13 broad candidates (REF with bias and discount/premium as features), 25 instruments, coverage-thin instrument-years
dropped. Features: BT8's 32 `f_*` + 34 bigger-picture `bp_*` (bt8b_prep; causal, t_feat_bp <= t_signal asserted).
Pair id is NOT a feature. Training uses all 25 instruments; evaluation universe = original 9 (primary, REF baseline HIST net
+0.036) and the 15 crosses (secondary).
Walk-forward: for each year Y (HIST-OOS: 2006..2020) train on every candidate with t_signal < Y-01-01 minus 7 days AND whose
label is known before Y (filled -> t_exit < Y-01-01); predict Y. DEV (2021..2023) and VAL (2024) continue the same yearly
walk-forward with FROZEN model families, hyper-parameters, thresholds and the frozen variant choice.
Models (fixed): GBC = LightGBM classifier p(win) (300 trees, lr .03, 8 leaves, depth 3, min_child 200, subsample .8,
colsample .8, lambda 10, monotone: bias-aligned +, dpos -, RR -), score = EV = p*RR - (1-p) - cost/risk.
GBR = LightGBM regressor on net R winsorised at 1/99% (same tree settings, no monotone). LR = L2 logistic C=0.05 on
standardised median-imputed features, EV score. Row weight 1/n per (sym, t_signal) group (BT8 w_grp).
Exit policies (bt8b_prep, exact re-simulation with common.simulate): P0 original TP (nearest H1 liquidity), P1 TP 2R,
P2 50% at 1R + runner to P0's TP with break-even after 1R, P3 P0 with break-even at 1R.
Variants (14; all reported):
  V01 GBC_32_ev0      broad, BT8's 32 features only, take EV > 0            (does 13x more data alone help?)
  V02 GBC_BP_ev0      broad, 32 + bigger-picture, take EV > 0
  V03 GBC_BP_top10    broad, 32 + BP, take the top 10% (quantile of the fold's TRAINING scores)
  V04 GBR_BP_pos      broad, regressor on R, take prediction > 0
  V05 LR_BP_ev0       broad, logistic, take EV > 0
  V06 REF+GBC_veto    REF setups, skip those with V02-EV <= 0
  V07 REF_P1_2R       REF setups, static TP = 2R
  V08 REF_P2_partial  REF setups, static 50% at 1R + runner (BE after 1R)
  V09 REF_P3_BE       REF setups, static break-even at 1R
  V10 REF+ML_target   REF setups, per trade the policy in {P0,P1,P2} with the highest predicted R (3 GBR, 32+BP)
  V11 REF+ML_BE       REF setups, P3 if predicted R_P3 - R_P0 > 0 (GBR on the difference) else P0
  V12 V02+ML_target   V02's take list + V10's per-trade target choice
  V13 REF_size_tiers  REF setups, risk 0.5x / 1x / 1.5x by V02-EV tercile (tercile cut points from the fold's training REF rows)
  V14 guard           monthly retrain + BT8 guard -- only for a variant that passes HIST (else not run, not counted)
Baselines (not variants): REF, unfiltered, random filter at the same take count per year in the same universe (300 draws),
random per-trade policy choice with the same per-year mix (V10/V11), shuffle-label test.
Adoption bar (original 9): on HIST-OOS 2006-2020 net > REF and > random median; beats REF and random in > half of the 15
years and in >= 2 of 3 eras, BOTH gross and net; weekly block-bootstrap t(net) >= 2.5. Then DEV and VAL each net > 0 and >= REF.
Frozen selection for DEV/VAL (max 3): best HIST-OOS t_boot among filters {V01-V06, V13}; best among exit variants
{V07-V12}; V02 (the main bigger-picture model) if not already chosen, else the next best overall.
"""
import os, sys, json, time, warnings
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt8b_lib as L                                              # noqa: E402
from bt8b_lib import COMM, STOP_SLIP, pip                         # noqa: E402

warnings.filterwarnings("ignore")
OUT = L.OUT
SEED = 88
HIST_YEARS = list(range(2006, 2021))
FINAL_YEARS = [2021, 2022, 2023, 2024]
ERA3 = {"2005-08": range(2005, 2009), "2009-14": range(2009, 2015), "2015-20": range(2015, 2021)}
MONO = {"f_n_bias_al": 1, "f_dpos": -1, "f_rr": -1}
POL = ["P0", "P1", "P2", "P3"]
NJ = 4


# ================================================================================================ data
def build():
    c = L.load_cands()
    c = c[~c.excluded_cov].reset_index(drop=True)
    F = pd.read_pickle(os.path.join(OUT, "feat_bp.pkl"))
    P = pd.read_pickle(os.path.join(OUT, "policy_outcomes.pkl"))
    c = c.merge(F, on="cid", how="left")
    assert (c.t_feat_bp <= c.t_signal).all()
    c["status_P0"] = c.status; c["t_exit_P0"] = c.t_exit
    c["R_P0"] = c.R; c["gross_R_P0"] = c.gross_R; c["usd_01_P0"] = c.usd_01
    pc = ["cid"]
    for k in ("P1", "P2", "P3"):
        cols = [f"R_{k}", f"gross_R_{k}", f"usd_01_{k}", f"t_exit_{k}"] + ([f"status_{k}"] if k != "P2" else [])
        pc += cols
    c = c.merge(P[pc], on="cid", how="left")
    f = c.status == "filled"
    for k in ("P2", "P3"):
        c[f"status_{k}"] = c.status
    c.loc[~f, "status_P1"] = c.loc[~f, "status"]
    # P1 cancelled (TP-first at 2R): no trade -> R 0 for the decision label
    for k in POL:
        nf = c[f"status_{k}"] != "filled"
        c.loc[nf, [f"R_{k}", f"gross_R_{k}", f"usd_01_{k}"]] = np.nan
    c["cost_R"] = (c.sym.map(COMM) + STOP_SLIP) / (((c.entry - c.sl) * c.side) / c.sym.map(pip))
    return c


def feats(c, which):
    f32 = [x for x in c.columns if x.startswith("f_")]
    assert len(f32) == 32, len(f32)
    bp = [x for x in c.columns if x.startswith("bp_")]
    return f32 if which == "32" else f32 + bp


# ================================================================================================ models
def _lgb(kind, F):
    import lightgbm as lgb
    kw = dict(n_estimators=300, learning_rate=0.03, num_leaves=8, max_depth=3, min_child_samples=200, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.8, reg_lambda=10.0, random_state=SEED, verbose=-1, n_jobs=NJ)
    if kind == "GBC":
        return lgb.LGBMClassifier(monotone_constraints=[MONO.get(f, 0) for f in F], **kw)
    return lgb.LGBMRegressor(**kw)


def ev(df, p):
    return p * df.rr.values - (1 - p) - df.cost_R.values


def fit_predict(kind, F, tr, te, target="win", shuffle_rng=None, contrib=False):
    """Fit on FILLED training rows (label `target`), score training rows and te rows.
    kind: GBC (EV), LR (EV), GBR (prediction of `target`, winsorised)."""
    trf = tr[tr.status == "filled"]
    y = trf[target].values.astype(float); w = trf.w_grp.values
    ok = np.isfinite(y)
    trf, y, w = trf[ok], y[ok], w[ok]
    if shuffle_rng is not None:
        y = y[shuffle_rng.permutation(len(y))]
    mu = trf[F].median().fillna(0.0)
    Xf = trf[F].fillna(mu).values if kind == "LR" else trf[F].values
    Xtr = tr[F].fillna(mu).values if kind == "LR" else tr[F].values
    Xte = te[F].fillna(mu).values if kind == "LR" else te[F].values
    extra = None
    if kind == "LR":
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        sc = StandardScaler().fit(Xf)
        m = LogisticRegression(C=0.05, max_iter=3000).fit(sc.transform(Xf), y, sample_weight=w)
        s_tr, s_te = ev(tr, m.predict_proba(sc.transform(Xtr))[:, 1]), ev(te, m.predict_proba(sc.transform(Xte))[:, 1])
        model = (m, sc, mu)
    elif kind == "GBC":
        m = _lgb("GBC", F).fit(Xf, y, sample_weight=w)
        p_te = m.predict_proba(Xte)[:, 1]
        s_tr, s_te = ev(tr, m.predict_proba(Xtr)[:, 1]), ev(te, p_te)
        model = m
        if contrib:
            extra = dict(p=p_te, contrib=m.booster_.predict(Xte, pred_contrib=True))
    else:
        lo, hi = np.quantile(y, [0.01, 0.99])
        m = _lgb("GBR", F).fit(Xf, np.clip(y, lo, hi), sample_weight=w)
        s_tr, s_te = m.predict(Xtr), m.predict(Xte)
        model = m
    return s_tr, s_te, model, extra


MODELS = {  # name: (kind, feature set, target)
    "GBC_32": ("GBC", "32", "win"), "GBC_BP": ("GBC", "BP", "win"), "GBR_BP": ("GBR", "BP", "R"), "LR_BP": ("LR", "BP", "win"),
    "GBR_P0": ("GBR", "BP", "R_P0"), "GBR_P1": ("GBR", "BP", "R_P1z"), "GBR_P2": ("GBR", "BP", "R_P2"),
    "GBR_BEd": ("GBR", "BP", "BEdiff"),
}


def add_targets(c):
    c["R_P1z"] = np.where(c.status == "filled", c.R_P1.fillna(0.0), np.nan)       # P1 cancelled -> 0 (no trade)
    c["BEdiff"] = c.R_P3 - c.R_P0
    return c


def walk(c, years, models=MODELS, shuffle_rng=None, contrib=False):
    """Yearly expanding walk-forward. Returns dict model -> Series of OOS scores, plus per-year training thresholds."""
    sc = {m: pd.Series(np.nan, index=c.index) for m in models}
    thr = {}; contribs = {}
    for Y in years:
        a = pd.Timestamp(f"{Y}-01-01")
        tr = c[(c.t_signal < a - pd.Timedelta(days=7)) & ((c.status != "filled") | (c.t_exit < a))]
        te = c[c.year == Y]
        if not len(te):
            continue
        for name, (kind, fs, tgt) in models.items():
            F = feats(c, fs)
            s_tr, s_te, model, extra = fit_predict(kind, F, tr, te, tgt, shuffle_rng, contrib and name == "GBC_BP")
            sc[name].loc[te.index] = s_te
            if name == "GBC_BP":
                thr[(Y, "top10")] = np.quantile(s_tr, 0.90)
                rg = tr.ref_gate.values
                thr[(Y, "ref_t1")], thr[(Y, "ref_t2")] = np.quantile(s_tr[rg], [1 / 3, 2 / 3])
                if extra is not None:
                    contribs[Y] = (te.index.values, extra["p"], extra["contrib"], F)
        print(f"  year {Y}: train {len(tr)} ({int((tr.status == 'filled').sum())} filled) test {len(te)}", flush=True)
    return sc, thr, contribs


# ================================================================================================ variants
def masks_and_policy(c, sc, thr, years):
    """Return dict variant -> (take mask, policy Series (P0..P3) or None, size weights Series or None)."""
    yr = c.year
    inY = yr.isin(years)
    thr10 = yr.map({Y: thr[(Y, "top10")] for Y in years if (Y, "top10") in thr})
    t1 = yr.map({Y: thr[(Y, "ref_t1")] for Y in years if (Y, "ref_t1") in thr})
    t2 = yr.map({Y: thr[(Y, "ref_t2")] for Y in years if (Y, "ref_t2") in thr})
    ref = c.ref_gate & inY
    V = {}
    V["V01_GBC_32_ev0"] = (inY & (sc["GBC_32"] > 0), None, None)
    V["V02_GBC_BP_ev0"] = (inY & (sc["GBC_BP"] > 0), None, None)
    V["V03_GBC_BP_top10"] = (inY & (sc["GBC_BP"] >= thr10), None, None)
    V["V04_GBR_BP_pos"] = (inY & (sc["GBR_BP"] > 0), None, None)
    V["V05_LR_BP_ev0"] = (inY & (sc["LR_BP"] > 0), None, None)
    V["V06_REF+GBC_veto"] = (ref & (sc["GBC_BP"] > 0), None, None)
    V["V07_REF_P1_2R"] = (ref, pd.Series("P1", index=c.index), None)
    V["V08_REF_P2_partial"] = (ref, pd.Series("P2", index=c.index), None)
    V["V09_REF_P3_BE"] = (ref, pd.Series("P3", index=c.index), None)
    pred = pd.DataFrame({"P0": sc["GBR_P0"], "P1": sc["GBR_P1"], "P2": sc["GBR_P2"]})
    allna = pred.isna().all(axis=1)
    choice = pred.fillna(-1e9).idxmax(axis=1).where(~allna, "P0")
    V["V10_REF+ML_target"] = (ref, choice, None)
    V["V11_REF+ML_BE"] = (ref, pd.Series(np.where(sc["GBR_BEd"] > 0, "P3", "P0"), index=c.index), None)
    V["V12_V02+ML_target"] = (V["V02_GBC_BP_ev0"][0], choice, None)
    size = pd.Series(np.where(sc["GBC_BP"] < t1, 0.5, np.where(sc["GBC_BP"] < t2, 1.0, 1.5)), index=c.index)
    V["V13_REF_size_tiers"] = (ref, None, size)
    return V


def apply_policy(c, pol):
    """Per-row chosen policy columns: status_x, t_exit_x, R_x, gross_R_x, usd_01_x."""
    d = c.copy()
    pol = pd.Series("P0", index=c.index) if pol is None else pol
    for col in ("status", "t_exit", "R", "gross_R", "usd_01"):
        out = d[f"{col}_P0"].copy()
        for k in ("P1", "P2", "P3"):
            m = pol == k
            if m.any():
                out[m] = d.loc[m, f"{col}_{k}"]
        d[f"{col}_x"] = out
    d["pol"] = pol
    return d


def trades(c, mask, pol=None, size=None):
    d = apply_policy(c, pol)
    p = L.portfolio(d, mask, cols={"status": "status_x", "t_exit": "t_exit_x"})
    f = p[p.status_x == "filled"].copy()
    f["w"] = 1.0 if size is None else size.loc[f.index].values
    f["Rx"] = f.R_x * f.w; f["Gx"] = f.gross_R_x * f.w; f["Ux"] = f.usd_01_x * f.w
    return f


def summ(f, name, boot=True):
    g = f.assign(R=f.Rx, gross_R=f.Gx, usd_01=f.Ux, t_exit=f.t_exit_x)
    return L.row(g, name, boot=boot)


def by(f, key):
    return f.groupby(key).agg(n=("Rx", "size"), gross=("Gx", "mean"), net=("Rx", "mean"), usd=("Ux", "sum"))


# ================================================================================================ evaluation
def random_filter(c, mask, universe, years, n=300, rng=None, pol=None):
    """Random rows at the same per-year take count inside `universe` (evaluated on ORIG9)."""
    rng = rng or np.random.default_rng(SEED)
    d = apply_policy(c, pol)
    uni_idx = {Y: np.where((universe & (c.year == Y)).values)[0] for Y in years}
    k = {Y: int((mask & (c.year == Y)).sum()) for Y in years}
    tot, yrs, gro = [], [], []
    for _ in range(n):
        rm = np.zeros(len(c), bool)
        for Y in years:
            if k[Y] and len(uni_idx[Y]):
                rm[rng.choice(uni_idx[Y], min(k[Y], len(uni_idx[Y])), replace=False)] = True
        p = L.portfolio(d, rm, cols={"status": "status_x", "t_exit": "t_exit_x"})
        f = p[(p.status_x == "filled")]
        tot.append(f.R_x.mean()); gro.append(f.gross_R_x.mean())
        yrs.append(f.groupby("year").R_x.mean().reindex(years).values)
    return np.array(tot), np.array(gro), np.nanmedian(np.vstack(yrs), 0)


def random_policy(c, mask, choice, years, n=300, rng=None):
    """Random per-trade policy with the same per-year mix as the ML choice (among the ML's own REF trades)."""
    rng = rng or np.random.default_rng(SEED + 1)
    tot, gro, yrs = [], [], []
    for _ in range(n):
        pol = choice.copy()
        for Y in years:
            m = (mask & (c.year == Y)).values
            v = pol.values[m].copy(); rng.shuffle(v); pv = pol.values.copy(); pv[m] = v; pol = pd.Series(pv, index=c.index)
        f = trades(c, mask, pol)
        tot.append(f.Rx.mean()); gro.append(f.Gx.mean()); yrs.append(f.groupby("year").Rx.mean().reindex(years).values)
    return np.array(tot), np.array(gro), np.nanmedian(np.vstack(yrs), 0)


def evaluate(c, V, years, tag, n_rand=300):
    o9 = c.sym.isin(L.ORIG9)
    inY = c.year.isin(years)
    base = {"BASE_REF": trades(c, c.ref_gate & inY & o9), "BASE_unfiltered": trades(c, inY & o9)}
    ref_y = base["BASE_REF"].groupby("year").Rx.mean().reindex(years)
    ref_gy = base["BASE_REF"].groupby("year").Gx.mean().reindex(years)
    rows, per_year, per_era, per_inst, trades_out = [], {}, [], [], {}
    for name, f in base.items():
        d = summ(f, name); d["take_rows"] = None; rows.append(d)
        per_year[name] = by(f, "year"); trades_out[name] = f
    for name, (m, pol, size) in V.items():
        m9 = m & o9 & inY                                   # restrict to this period's years (bug fix: DEV/VAL pooled)
        f = trades(c, m9, pol, size)
        trades_out[name] = f
        d = summ(f, name); d["take_rows"] = int(m9.sum())
        y = f.groupby("year").Rx.mean().reindex(years); gy = f.groupby("year").Gx.mean().reindex(years)
        if name.startswith(("V07", "V08", "V09")):
            rt = rg = ry = None
        elif name.startswith(("V10", "V11")):
            rt, rg, ry = random_policy(c, m9, pol, years, n_rand)
        elif name.startswith(("V06", "V13")):
            rt, rg, ry = random_filter(c, m9, c.ref_gate & inY & o9, years, n_rand) if not name.startswith("V13") else (None, None, None)
        else:
            rt, rg, ry = random_filter(c, m9, inY & o9, years, n_rand, pol=pol)
        d["yrs_beat_REF_net"] = int((y > ref_y).sum()); d["yrs_beat_REF_gross"] = int((gy > ref_gy).sum())
        if rt is not None:
            d["rand_med"] = round(np.nanmedian(rt), 3); d["rand_p95"] = round(np.nanquantile(rt, 0.95), 3)
            d["rand_gross_med"] = round(np.nanmedian(rg), 3)
            d["p_vs_rand"] = round(float((rt >= d["net_R"]).mean()), 3)
            d["yrs_beat_rand_net"] = int((y.values > ry).sum())
        # eras (HIST only)
        eb = []
        for en, yy in ERA3.items():
            fy = f[f.year.isin(yy)]; fr = base["BASE_REF"][base["BASE_REF"].year.isin(yy)]
            if len(fy):
                eb.append((fy.Rx.mean() > fr.Rx.mean()) and (fy.Gx.mean() > fr.Gx.mean()))
                per_era.append(dict(variant=name, era=en, n=len(fy), gross=round(fy.Gx.mean(), 3), net=round(fy.Rx.mean(), 3),
                                    ref_gross=round(fr.Gx.mean(), 3), ref_net=round(fr.Rx.mean(), 3)))
        d["eras_beat_REF"] = f"{sum(eb)}/{len(eb)}"
        rows.append(d); per_year[name] = by(f, "year")
    for name in base:
        for en, yy in ERA3.items():
            fy = trades_out[name][trades_out[name].year.isin(yy)]
            if len(fy):
                per_era.append(dict(variant=name, era=en, n=len(fy), gross=round(fy.Gx.mean(), 3), net=round(fy.Rx.mean(), 3)))
    for name, f in trades_out.items():
        for s, g in f.groupby("sym"):
            per_inst.append(dict(variant=name, sym=s, n=len(g), gross=round(g.Gx.mean(), 3), net=round(g.Rx.mean(), 3),
                                 usd=round(g.Ux.sum(), 0)))
    T = pd.DataFrame(rows)
    T.to_csv(os.path.join(OUT, f"variants_{tag}.csv"), index=False)
    PY = pd.concat({k: v for k, v in per_year.items()}, names=["variant", "year"]).round(3).reset_index()
    PY.to_csv(os.path.join(OUT, f"by_year_{tag}.csv"), index=False)
    pd.DataFrame(per_era).to_csv(os.path.join(OUT, f"by_era_{tag}.csv"), index=False)
    pd.DataFrame(per_inst).to_csv(os.path.join(OUT, f"by_instrument_{tag}.csv"), index=False)
    return T, trades_out


def crosses_table(c, V, years, tag):
    cr = c.sym.isin(L.CROSS15); inY = c.year.isin(years)
    rows = [summ(trades(c, c.ref_gate & inY & cr), "BASE_REF"), summ(trades(c, inY & cr), "BASE_unfiltered")]
    for name, (m, pol, size) in V.items():
        rows.append(summ(trades(c, m & cr & inY, pol, size), name))
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, f"variants_{tag}_CROSS15.csv"), index=False)
    return T


def adopt(T):
    ref = T[T.variant == "BASE_REF"].iloc[0]
    out = []
    for _, r in T[T.variant.str.startswith("V")].iterrows():
        rnd_ok = (r.get("rand_med") is None or pd.isna(r.get("rand_med"))) or (r.net_R > r.rand_med and r.yrs_beat_rand_net > 7)
        ok = (r.net_R > ref.net_R and r.gross_R > ref.gross_R and r.yrs_beat_REF_net > 7 and r.yrs_beat_REF_gross > 7
              and int(str(r.eras_beat_REF).split("/")[0]) >= 2 and r.t_boot >= 2.5 and rnd_ok)
        out.append(dict(variant=r.variant, passes_HIST=bool(ok)))
    return pd.DataFrame(out)


# ================================================================================================ commands
def main_hist():
    c = add_targets(build())
    t0 = time.time()
    sc, thr, contribs = walk(c, HIST_YEARS, contrib=True)
    print(f"walk-forward {time.time() - t0:.0f}s", flush=True)
    S = c[["cid", "sym", "t_signal", "year", "status", "R", "ref_gate"]].copy()
    for k, v in sc.items():
        S[f"score_{k}"] = v
    S[S.year.isin(HIST_YEARS)].to_pickle(os.path.join(OUT, "oof_scores_HIST.pkl"))
    pd.to_pickle(dict(thr=thr, contribs=contribs), os.path.join(OUT, "oof_aux_HIST.pkl"))
    V = masks_and_policy(c, sc, thr, HIST_YEARS)
    T, tr = evaluate(c, V, HIST_YEARS, "HIST_OOS")
    A = adopt(T); A.to_csv(os.path.join(OUT, "adoption_HIST_OOS.csv"), index=False)
    X = crosses_table(c, V, HIST_YEARS, "HIST_OOS")
    # frozen selection
    t = T.set_index("variant")
    filt = [v for v in t.index if v.startswith(("V01", "V02", "V03", "V04", "V05", "V06", "V13"))]
    exi = [v for v in t.index if v.startswith(("V07", "V08", "V09", "V10", "V11", "V12"))]
    s1 = t.loc[filt].t_boot.idxmax(); s2 = t.loc[exi].t_boot.idxmax()
    rest = [v for v in t.index if v.startswith("V") and v not in (s1, s2)]
    s3 = "V02_GBC_BP_ev0" if "V02_GBC_BP_ev0" not in (s1, s2) else t.loc[rest].t_boot.idxmax()
    sel = dict(chosen=[s1, s2, s3], frozen_at=pd.Timestamp.now().isoformat(), passes=A.to_dict("records"))
    json.dump(sel, open(os.path.join(OUT, "frozen_selection.json"), "w"), indent=1)
    pd.set_option("display.width", 300)
    print(T.to_string(index=False)); print(A.to_string(index=False)); print(X.to_string(index=False)); print(sel)
    for name in ("BASE_REF", "V02_GBC_BP_ev0"):
        tr[name].to_csv(os.path.join(OUT, f"trades_{name}_HIST_OOS.csv"), index=False)
    # calibration of p(win) (V02 model), OOS
    calib(c, sc)
    # policy mix of the dynamic choice
    pol = V["V10_REF+ML_target"][1]
    print("V10 choice mix (REF rows):", pol[c.ref_gate & c.year.isin(HIST_YEARS)].value_counts().to_dict())


def calib(c, sc):
    m = c.year.isin(HIST_YEARS) & (c.status == "filled") & sc["GBC_BP"].notna()
    d = c[m].copy(); d["ev"] = sc["GBC_BP"][m]
    d["p"] = (d.ev + 1 + d.cost_R) / (d.rr + 1)
    d["dec"] = pd.qcut(d.p, 10, labels=False, duplicates="drop")
    t = d.groupby("dec").agg(n=("R", "size"), p_pred=("p", "mean"), win=("win", "mean"), ev_pred=("ev", "mean"),
                             net=("R", "mean"), gross=("gross_R", "mean")).round(3)
    d["evq"] = pd.qcut(d.ev, 10, labels=False, duplicates="drop")
    t2 = d.groupby("evq").agg(n=("R", "size"), ev_pred=("ev", "mean"), net=("R", "mean"), gross=("gross_R", "mean"),
                              win=("win", "mean"), rr=("rr", "median")).round(3)
    from scipy.stats import spearmanr
    rho = spearmanr(d.ev, d.R).correlation
    t.to_csv(os.path.join(OUT, "calibration_pwin_HIST_OOS.csv")); t2.to_csv(os.path.join(OUT, "ev_deciles_HIST_OOS.csv"))
    print("calibration p(win) deciles\n", t.to_string()); print("EV deciles\n", t2.to_string()); print("spearman EV vs R", round(rho, 3))
    # EV decile net per era (does ranking hold?)
    d["era"] = d.year.map(L.era_of_year)
    print(d.groupby(["era", pd.qcut(d.ev, 5, labels=False)]).R.mean().unstack().round(3).to_string())


def main_shuffle(n=8):
    c = add_targets(build())
    o9 = c.sym.isin(L.ORIG9); inY = c.year.isin(HIST_YEARS)
    base = trades(c, inY & o9).Rx.mean(); base_ref = trades(c, c.ref_gate & inY & o9).Rx.mean()
    rows = []
    for i in range(n):
        rng = np.random.default_rng(1000 + i)
        sc, thr, _ = walk(c, HIST_YEARS, models={"GBC_BP": MODELS["GBC_BP"], "GBR_BP": MODELS["GBR_BP"]}, shuffle_rng=rng)
        for nm, m in (("V02_shuf", inY & (sc["GBC_BP"] > 0)), ("V04_shuf", inY & (sc["GBR_BP"] > 0)),
                      ("V06_shuf", c.ref_gate & inY & (sc["GBC_BP"] > 0))):
            f = trades(c, m & o9)
            rows.append(dict(rep=i, variant=nm, trades=len(f), net=f.Rx.mean(), gross=f.Gx.mean(),
                             lift_vs_unfilt=f.Rx.mean() - base, lift_vs_REF=f.Rx.mean() - base_ref))
        print(pd.DataFrame(rows).tail(3).round(3).to_string(index=False), flush=True)
    D = pd.DataFrame(rows); D.to_csv(os.path.join(OUT, "shuffle_label_test.csv"), index=False)
    print(D.groupby("variant")[["trades", "net", "gross", "lift_vs_unfilt", "lift_vs_REF"]].agg(["mean", "std"]).round(3).to_string())


def main_final():
    sel = json.load(open(os.path.join(OUT, "frozen_selection.json")))
    chosen = sel["chosen"]
    print("frozen choice:", chosen)
    c = add_targets(build())
    assert (c.t_signal < L.TEST_START).all()
    need = set()
    for v in chosen:
        need |= {"V01": {"GBC_32"}, "V02": {"GBC_BP"}, "V03": {"GBC_BP"}, "V04": {"GBR_BP"}, "V05": {"LR_BP"}, "V06": {"GBC_BP"},
                 "V07": set(), "V08": set(), "V09": set(), "V10": {"GBR_P0", "GBR_P1", "GBR_P2"}, "V11": {"GBR_BEd"},
                 "V12": {"GBC_BP", "GBR_P0", "GBR_P1", "GBR_P2"}, "V13": {"GBC_BP"}}[v[:3]]
    need |= {"GBC_BP"}
    models = {k: MODELS[k] for k in MODELS if k in need}
    sc, thr, _ = walk(c, FINAL_YEARS, models=models)
    for k in MODELS:
        sc.setdefault(k, pd.Series(np.nan, index=c.index))
    V = masks_and_policy(c, sc, thr, FINAL_YEARS)
    V = {k: v for k, v in V.items() if k in chosen}
    out = []
    for per, yrs in (("DEV", [2021, 2022, 2023]), ("VAL", [2024])):
        T, tr = evaluate(c, V, yrs, per, n_rand=300)
        T.insert(0, "period", per); out.append(T)
        X = crosses_table(c, V, yrs, per); X.insert(0, "period", per); out.append(X.assign(group="CROSS15"))
        for name in tr:
            tr[name].to_csv(os.path.join(OUT, f"trades_{name}_{per}.csv"), index=False)
    D = pd.concat(out); D.to_csv(os.path.join(OUT, "variants_DEV_VAL.csv"), index=False)
    pd.set_option("display.width", 300); print(D.to_string(index=False))


def main_shap():
    import bt8b_autopsy as A
    c = add_targets(build())
    aux = pd.read_pickle(os.path.join(OUT, "oof_aux_HIST.pkl"))
    V02 = pd.read_csv(os.path.join(OUT, "trades_V02_GBC_BP_ev0_HIST_OOS.csv"))
    rows = []; imp = []
    for Y, (idx, p, con, F) in aux["contribs"].items():
        cc = pd.DataFrame(con[:, :-1], columns=F, index=idx)
        imp.append(cc.abs().mean())
        take = c.loc[idx]
        sel = take[take.cid.isin(V02.cid) & take.sym.isin(L.ORIG9)]
        pos = {v: k for k, v in enumerate(idx)}
        for i in sel.index:
            rows.append((i, Y, p[pos[i]], cc.loc[i]))
    I = pd.concat(imp, axis=1).mean(1).sort_values(ascending=False)
    I.rename("mean_abs_shap_logodds").to_csv(os.path.join(OUT, "shap_importance_V02.csv"))
    print(I.head(20).round(4).to_string())
    rng = np.random.default_rng(5)
    rr = pd.DataFrame([dict(i=i, Y=Y, p=pp) for i, Y, pp, _ in rows])
    rr["R"] = c.loc[rr.i, "R"].values
    win = rr[rr.R > 0].sample(10, random_state=5); los = rr[rr.R <= 0].sample(10, random_state=5)
    lookup = {i: s for i, _, _, s in rows}
    ex = []
    for tag, d in (("WINNER", win), ("LOSER", los)):
        for _, r in d.sort_values("i").iterrows():
            s = lookup[r.i]; x = c.loc[r.i]
            top = s.sort_values(key=abs, ascending=False).head(4)
            reasons = "; ".join(f"{A.DESC.get(k, k)} = {x[k]:.2f} ({v:+.2f})" for k, v in top.items())
            ex.append(dict(kind=tag, sym=x.sym, side="long" if x.side == 1 else "short", t_signal=x.t_signal, session=x.session,
                           entry=x.entry, sl=x.sl, tp=x.tp, rr=x.rr, exit=x.exit_reason, R=round(x.R, 2), usd_01=round(x.usd_01, 0),
                           p_win=round(r.p, 3), EV=round(x.rr * r.p - (1 - r.p) - x.cost_R, 2), top_reasons=reasons))
    E = pd.DataFrame(ex); E.to_csv(os.path.join(OUT, "shap_examples_V02_HIST_OOS.csv"), index=False)
    pd.set_option("display.width", 400); pd.set_option("display.max_colwidth", 300)
    print(E.to_string(index=False))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "hist":
        main_hist()
    elif cmd == "shuffle":
        main_shuffle(int(sys.argv[2]) if len(sys.argv) > 2 else 8)
    elif cmd == "final":
        main_final()
    elif cmd == "shap":
        main_shap()
