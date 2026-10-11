"""BT8: ML as a FILTER / RANKER on top of SMC setups (not a signal source).

Run from the TTXT root:
    .venv/Scripts/python.exe smc_bot/bt/bt8_ml.py gen DEV        # broad candidate set + features + independent labels
    .venv/Scripts/python.exe smc_bot/bt/bt8_ml.py check DEV      # REF gates on the candidates reproduce canonical REF
    .venv/Scripts/python.exe smc_bot/bt/bt8_ml.py dev            # walk-forward inside DEV, all variants, baselines
    .venv/Scripts/python.exe smc_bot/bt/bt8_ml.py gen VAL        # (only after the DEV choice is frozen)
    .venv/Scripts/python.exe smc_bot/bt/bt8_ml.py val V? V? V?   # frozen models, run ONCE on VAL

Candidate set = canonical REF engine (recon_pipeline CANON flags, i.e. BT6b REF + literal OB) with two hard gates turned
into FEATURES: (1) the H4+EMA200 bias checks (creation, touch, CHoCH) and (2) the discount/premium position in the H4
dealing range. Every other REF rule (30M POI, first touch, M5 CHoCH <= 36 bars, killzone Tue-Fri, LTF entry zone, stop
>= 10x commission, nearest unbroken H1 liquidity with RR >= 2) is unchanged. Each candidate is simulated INDEPENDENTLY
(one_at_a_time=False) for its label; a filtered portfolio then applies REF's t_signal dedupe and one position per pair
(emulating simulate(one_at_a_time=True) exactly, verified by `check`).
TEST (2025-07+) is never read: detection bars come from BT6b.prepare (cut at the period end), candidates are only made for
t_signal inside the period, and VAL's last possible trade (Fri 2025-06-27) is flat before the weekend.
"""
import os, sys, json, time, warnings
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                                   # noqa: E402
from common import PERIODS, pip, COMM, STOP_SLIP, simulate, stats, RESULTS, USD_PIP_01   # noqa: E402
import recon_pipeline as R                                      # noqa: E402  (import only; also memoises common.bars)
import bt6b_pipeline as B                                       # noqa: E402

warnings.filterwarnings("ignore")
OUT = os.path.join(RESULTS, "BT8"); os.makedirs(OUT, exist_ok=True)
PAIRS9 = B.TRADE_PAIRS
CANON = R.cfg(R.BCFG, ob_def="core")
SEED = 8


# ================================================================================================ candidates
def _roll_mean(x, n):
    return pd.Series(x).rolling(n, min_periods=n // 2).mean().values


def build_candidates(sym, period):
    """REF pipeline with the bias and discount gates recorded instead of applied. One row per (zone, CHoCH) setup."""
    C = CANON
    X = R.pdata(sym, period); ctx = X["ctx"]
    M30, M5, H1, H4 = ctx["M30"], ctx["M5"], ctx["H1"], ctx["H4"]
    s0 = np.datetime64(pd.Timestamp(C["start"] if period == "DEV" else PERIODS[period][0]))
    s1 = np.datetime64(pd.Timestamp(PERIODS[period][1]))
    Z = R.zones(X, C["zone_N"], C["ob_def"], C["ob_premit"], C["fvg_def"])
    zd, zlo, zhi, zk, zkind = Z.d.values, Z.lo.values, Z.hi.values, Z.k.values, Z.kind.values
    tcr = M30["tc"][zk]
    keep = (tcr >= s0 - np.timedelta64(4, "D")) & (tcr < s1)
    bias_c = R.bias_at(ctx, tcr, True)
    mid = (zlo + zhi) / 2
    rlo, rhi = R.drange_swing(X, "swing")
    i4z = np.maximum(np.searchsorted(H4["tc"], tcr, "right") - 1, 0)
    zrlo, zrhi = rlo[i4z], rhi[i4z]
    pos = (mid - zrlo) / np.where(zrhi > zrlo, zrhi - zrlo, np.nan)
    dpos = np.where(zd == 1, pos, 1 - pos)
    # causal helper series
    atr5 = M5["atr"]; atr5_m = _roll_mean(atr5, 2880)
    atr1 = H1["atr"]; atr1_m = _roll_mean(atr1, 500)
    t5, lo5, hi5 = M5["t"], M5["l"], M5["h"]
    nZ = len(M30["tc"])
    ev, br = M5["ev_i"], M5["br_i"]
    rows = []
    for zi in np.where(keep)[0]:
        d = int(zd[zi]); k = int(zk[zi])
        texp = M30["tc"][min(k + 96, nZ - 1)]
        a = np.searchsorted(t5, tcr[zi], "left"); e = np.searchsorted(t5, texp, "left")
        if e <= a:
            continue
        hit = (lo5[a:e] <= zhi[zi]) if d == 1 else (hi5[a:e] >= zlo[zi])
        if not hit.any():
            continue
        it = a + int(np.argmax(hit))
        bias_t = int(R.bias_at(ctx, t5[it], True)[0])
        w = ev[it:min(it + 37, len(ev))]
        cj = np.where(w == 2 * d)[0]
        if not len(cj):
            continue
        j = it + int(cj[0])
        t_sig = M5["tc"][j]
        if not (s0 <= t_sig < s1):
            continue
        bias_x = int(R.bias_at(ctx, t_sig, True)[0])
        kz = R.killzone(t_sig, "local")
        if kz not in ("LDN", "NY") or pd.Timestamp(t_sig).dayofweek not in (1, 2, 3, 4):
            continue
        ez = B.ltf_entry_zone(M5, j, d, int(br[j]))
        if ez is None:
            continue
        entry = ez["entry"]
        if (entry - M5["c"][j]) * d >= 0:
            continue
        ext = M5["l"][it:j + 1].min() if d == 1 else M5["h"][it:j + 1].max()
        buf = 0.1 * (atr5[j] if atr5[j] == atr5[j] else 0.0)
        sl = ext - d * buf
        risk = (entry - sl) * d
        if not risk > 0 or risk / pip(sym) < 10 * COMM[sym]:
            continue
        liq = [x for x in B.liquidity(ctx, t_sig, d) if (x - entry) * d > 0]
        tp = liq[0] if liq else np.nan
        if not np.isfinite(tp):
            continue
        rr = (tp - entry) * d / risk
        if rr < 2.0:
            continue
        # ---------------- features (all from bars closed at or before t_sig)
        i1 = int(np.searchsorted(H1["tc"], t_sig, "right") - 1)
        i4 = int(np.searchsorted(H4["tc"], t_sig, "right") - 1)
        a1 = atr1[i1]; a4 = H4["atr"][i4]
        ema_d = (H1["c"][i1] - H1["ema200"][i1]) / a1 * d
        cr_lo, cr_hi = rlo[i4], rhi[i4]
        epos = (entry - cr_lo) / (cr_hi - cr_lo) if cr_hi > cr_lo else np.nan
        epos = epos if d == 1 else (1 - epos if epos == epos else np.nan)
        swp5, swp5d = B.sweep_info(ctx, it, j, d, "M5")
        swp30, _ = B.sweep_info(ctx, it, j, d, "M30")
        bl = hi5[br[j]] if d == 1 else lo5[br[j]]
        tfeat = max(pd.Timestamp(H1["tc"][i1]), pd.Timestamp(H4["tc"][i4]), pd.Timestamp(M5["tc"][j]),
                    pd.Timestamp(tcr[zi]))
        ts = pd.Timestamp(t_sig)
        rows.append(dict(
            t_signal=ts, side=d, entry_type="limit", entry=entry, expire=ts + pd.Timedelta(minutes=60), sl=sl, tp=tp,
            be_at_r=np.nan, max_hold_min=np.nan, t_zone=pd.Timestamp(tcr[zi]), t_touch=pd.Timestamp(t5[it]),
            zone_kind=zkind[zi], zone_lo=zlo[zi], zone_hi=zhi[zi], entry_src=ez["ez_kind"], session=kz, zk=k,
            ord=len(rows), t_feat_max=tfeat,
            # gates as raw info
            bias_c=int(bias_c[zi]), bias_t=bias_t, bias_x=bias_x, dpos_raw=dpos[zi],
            ref_gate=bool(bias_c[zi] == d and bias_t == d and bias_x == d and dpos[zi] <= 0.5),
            # model features
            f_n_bias_al=int(bias_c[zi] == d) + int(bias_t == d) + int(bias_x == d),
            f_h4_rel=int(H4["tr_i"][i4]) * d, f_h4e_rel=int(H4["tr_e"][i4]) * d,
            f_ema_rel=int(np.sign(H1["c"][i1] - H1["ema200"][i1])) * d, f_ema_dist=float(np.clip(ema_d, -15, 15)),
            f_h4_age=np.log1p(i4 - H4["last_chg"][i4]), f_h4_evt_age=np.log1p(i4 - H4["last_evt"][i4]),
            f_dpos=dpos[zi], f_epos=epos, f_dr_width=np.log((cr_hi - cr_lo) / a4) if cr_hi > cr_lo else np.nan,
            f_zone_ob=int(zkind[zi] == "OB"), f_zone_size=(zhi[zi] - zlo[zi]) / M30["atr"][k],
            f_zone_age=np.log1p((t5[it] - tcr[zi]) / np.timedelta64(1, "h")),
            f_sweep5=int(swp5), f_sweep5_depth=swp5d, f_sweep30=int(swp30), f_bars_tc=j - it,
            f_choch_disp=(M5["c"][j] - bl) * d / atr5[j], f_leg=(M5["c"][j] - ext) * d / atr5[j],
            f_entry_fvg=int(ez["ez_kind"] == "FVG"), f_pullback=(M5["c"][j] - entry) * d / atr5[j],
            f_stop_atr=risk / atr5[j], f_stop_comm=np.log(risk / pip(sym) / COMM[sym]), f_rr=np.log(rr),
            f_tp_atr1=np.log((tp - entry) * d / a1), f_ldn=int(kz == "LDN"),
            f_dow_tue=int(ts.dayofweek == 1), f_dow_wed=int(ts.dayofweek == 2), f_dow_thu=int(ts.dayofweek == 3),
            f_vol5=np.log(atr5[j] / atr5_m[j]), f_vol1=np.log(a1 / atr1_m[i1]), f_atr4_1=np.log(a4 / a1),
            rr=round(rr, 3)))
    o = pd.DataFrame(rows)
    if len(o):
        o.insert(0, "sym", sym)
    return o


def _busy_nf(sym, t):
    """busy_until that simulate(one_at_a_time=True) sets after an UNFILLED limit: first M1 bar at/after expire."""
    m1 = common.bars(sym, "M1"); t1 = m1.time.values
    e = np.minimum(np.searchsorted(t1, t.expire.values.astype("datetime64[ns]"), "left"), len(t1) - 1)
    a = np.searchsorted(t1, t.t_signal.values.astype("datetime64[ns]"), "left")
    out = pd.Series(pd.to_datetime(t1[e]), index=t.index)
    out[e <= a] = pd.NaT
    return out


def gen(period):
    allc = []
    for sym in PAIRS9:
        t0 = time.time()
        o = build_candidates(sym, period)
        if not len(o):
            continue
        s = simulate(sym, o.drop(columns=["sym"]), one_at_a_time=False)
        s = s.sort_values(["t_signal", "ord"], kind="stable").reset_index(drop=True)
        s["busy_nf"] = _busy_nf(sym, s)
        allc.append(s)
        print(f"{period} {sym}: {len(o)} candidates, {int((s.status == 'filled').sum())} filled ({time.time() - t0:.0f}s)",
              flush=True)
    c = pd.concat(allc, ignore_index=True)
    c = c[c.period == period].reset_index(drop=True)
    f = c.status == "filled"
    slipped = c.exit_reason.isin(["SL", "BE", "GAP_SL"])
    c["gross_R"] = np.where(f, (c.gross_pips + np.where(slipped, STOP_SLIP, 0.0)) / c.risk_pips, np.nan)
    c["win"] = np.where(f, (c.R > 0).astype(float), np.nan)
    c["cid"] = [f"{period[0]}{i:05d}" for i in range(len(c))]
    grp = c.groupby(["sym", "t_signal"]).cid.transform("count")
    c["w_grp"] = 1.0 / grp
    assert (c.t_feat_max <= c.t_signal).all(), "feature timestamp after order time"
    assert (c.t_signal < pd.Timestamp(PERIODS[period][1])).all()
    c.to_pickle(os.path.join(OUT, f"candidates_{period}.pkl"))
    c.to_csv(os.path.join(OUT, f"candidates_{period}.csv"), index=False)
    return c


def load(period):
    return pd.read_pickle(os.path.join(OUT, f"candidates_{period}.pkl"))


FEATS = None


def feats(c):
    return [x for x in c.columns if x.startswith("f_")]


# ================================================================================================ portfolio emulation
def portfolio(c, mask):
    """REF dedupe (per pair & t_signal keep earliest touch) + one position per pair, emulating simulate(one_at_a_time)."""
    s = c[np.asarray(mask)]
    if not len(s):
        return s
    s = s.sort_values(["sym", "t_signal", "t_touch", "zone_kind", "ord"], kind="stable").drop_duplicates(["sym", "t_signal"])
    keep = []
    for sym, g in s.groupby("sym", sort=False):
        busy = np.datetime64("1970-01-01")
        ts = g.t_signal.values; st = g.status.values; te = g.t_exit.values; bn = g.busy_nf.values; idx = g.index.values
        for i in range(len(g)):
            if ts[i] < busy:
                continue
            keep.append(idx[i])
            if st[i] == "filled":
                busy = te[i]
            elif st[i] == "not_filled" and not pd.isna(bn[i]):
                busy = max(busy, bn[i])
    return s.loc[keep]


def summ(p, name="", n_cand=None, n_take=None):
    f = p[p.status == "filled"]
    if not len(f):
        return dict(variant=name, trades=0)
    s = stats(f)
    se = f.R.std() / np.sqrt(len(f))
    per = f.groupby("sym").R.mean()
    yr = f.groupby(f.t_signal.dt.year).R.mean().round(3).to_dict()
    d = dict(variant=name, trades=len(f), take_rate=round(n_take / n_cand, 3) if n_cand else None,
             win_pct=s["win_pct"], gross_R=round(f.gross_R.mean(), 3), net_R=round(f.R.mean(), 3),
             total_net_R=round(f.R.sum(), 1), PF=s["PF"], t_net=round(f.R.mean() / se, 2),
             pairs_pos=f"{(per > 0).sum()}/{len(per)}", maxDD_R=s["maxDD_R"], usd_01=s["usd_per_01lot"],
             per_week=round(len(f) / max(1, (f.t_fill.max() - f.t_fill.min()).days / 7), 2),
             LDN=round(f[f.session == "LDN"].R.mean(), 3), NY=round(f[f.session == "NY"].R.mean(), 3))
    d.update({f"net_{k}": v for k, v in yr.items()})
    return d


def per_pair(p):
    f = p[p.status == "filled"]
    rows = []
    for sym, g in list(f.groupby("sym")) + [("POOLED", f)]:
        s = stats(g)
        rows.append(dict(pair=sym, trades=len(g), win_pct=s["win_pct"], gross_R=round(g.gross_R.mean(), 3),
                         net_R=round(g.R.mean(), 3), total_net_R=round(g.R.sum(), 1), PF=s["PF"], maxDD_R=s["maxDD_R"],
                         avg_net_pips=s["avg_net_pips"], usd_01=s["usd_per_01lot"], avg_hold_h=s["avg_hold_h"]))
    return pd.DataFrame(rows)


# ================================================================================================ models
def _prep(tr, te, F):
    mu = tr[F].median()
    Xtr = tr[F].fillna(mu).values.astype(float); Xte = te[F].fillna(mu).values.astype(float)
    return Xtr, Xte


MONO = {"f_n_bias_al": 1, "f_dpos": -1, "f_rr": -1}


def fit_predict(kind, tr, te, F, shuffle_rng=None):
    """Train on FILLED training rows; return scores for all rows of `te` (and of `tr` for the threshold)."""
    trf = tr[tr.status == "filled"]
    y_win = trf.win.values.copy(); y_R = trf.R.values.copy(); w = trf.w_grp.values
    if shuffle_rng is not None:
        perm = shuffle_rng.permutation(len(trf)); y_win = y_win[perm]; y_R = y_R[perm]
    Xf, Xall = _prep(trf, pd.concat([tr, te]), F)
    Xtr_all, Xte = Xall[:len(tr)], Xall[len(tr):]
    if kind == "LR":
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        sc = StandardScaler().fit(Xf)
        m = LogisticRegression(C=0.05, max_iter=2000).fit(sc.transform(Xf), y_win, sample_weight=w)
        pr = lambda X: m.predict_proba(sc.transform(X))[:, 1]                       # noqa: E731
        model = (m, sc)
    elif kind == "GBC":
        import lightgbm as lgb
        mono = [MONO.get(f, 0) for f in F]
        m = lgb.LGBMClassifier(n_estimators=150, learning_rate=0.03, num_leaves=4, max_depth=2, min_child_samples=120,
                               subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=10.0,
                               monotone_constraints=mono, random_state=SEED, verbose=-1)
        m.fit(Xf, y_win, sample_weight=w)
        pr = lambda X: m.predict_proba(X)[:, 1]                                      # noqa: E731
        model = m
    elif kind == "GBR":
        import lightgbm as lgb
        cap = np.quantile(y_R, 0.99)
        m = lgb.LGBMRegressor(n_estimators=150, learning_rate=0.03, num_leaves=4, max_depth=2, min_child_samples=150,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=10.0,
                              random_state=SEED, verbose=-1)
        m.fit(Xf, np.clip(y_R, None, cap), sample_weight=w)
        return m.predict(Xtr_all), m.predict(Xte), m
    else:
        raise ValueError(kind)

    def ev(df, p):  # expected net R per trade: p * RR - (1 - p) - costs
        cost = (df.sym.map(COMM).values + STOP_SLIP) / (((df.entry - df.sl) * df.side).values / df.sym.map(pip).values)
        return p * df.rr.values - (1 - p) - cost
    return ev(tr, pr(Xtr_all)), ev(te, pr(Xte)), model


VARIANTS = {   # name: (model, threshold rule, universe)
    "V01_LR_top30": ("LR", ("top", 0.30), "all"),
    "V02_LR_top15": ("LR", ("top", 0.15), "all"),
    "V03_LR_ev0": ("LR", ("abs", 0.0), "all"),
    "V04_GBC_top30": ("GBC", ("top", 0.30), "all"),
    "V05_GBC_top15": ("GBC", ("top", 0.15), "all"),
    "V06_GBC_ev0": ("GBC", ("abs", 0.0), "all"),
    "V07_GBR_top30": ("GBR", ("top", 0.30), "all"),
    "V08_GBR_top15": ("GBR", ("top", 0.15), "all"),
    "V09_GBR_pos": ("GBR", ("abs", 0.0), "all"),
    "V10_REF+LR_ev0": ("LR", ("abs", 0.0), "ref"),      # ML on top of the hand rules: skip REF setups with EV <= 0
}


def thresh(rule, s_tr):
    return np.quantile(s_tr, 1 - rule[1]) if rule[0] == "top" else rule[1]


def quarter_folds(c):
    q = pd.date_range("2022-01-01", "2024-01-01", freq="QS")
    return [(q[i], q[i + 1]) for i in range(len(q) - 1)]


def year_folds(c):
    return [(pd.Timestamp("2022-01-01"), pd.Timestamp("2023-01-01")), (pd.Timestamp("2023-01-01"), pd.Timestamp("2024-01-01"))]


def walk_forward(c, kind, folds, shuffle_rng=None):
    F = feats(c)
    sc = pd.Series(np.nan, index=c.index); thr_tr = {}
    for a, b in folds:
        tr = c[(c.t_signal < a) & ((c.status != "filled") | (c.t_exit < a))]        # embargo: labels known before a
        te = c[(c.t_signal >= a) & (c.t_signal < b)]
        s_tr, s_te, _ = fit_predict(kind, tr, te, F, shuffle_rng)
        sc.loc[te.index] = s_te
        thr_tr[a] = s_tr
    return sc, thr_tr


def select(c, sc, thr_tr, rule, universe, folds):
    m = pd.Series(False, index=c.index)
    for a, b in folds:
        te = (c.t_signal >= a) & (c.t_signal < b)
        m |= te & (sc >= thresh(rule, thr_tr[a]))
    if universe == "ref":
        m &= c.ref_gate
    return m


# ================================================================================================ DEV
def main_dev():
    c = load("DEV")
    F = feats(c)
    oos = (c.t_signal >= "2022-01-01")
    co = c[oos]
    folds = quarter_folds(c)
    rows = []
    base_all = portfolio(c, oos); base_ref = portfolio(c, oos & c.ref_gate)
    rows.append(summ(base_all, "BASE_unfiltered", oos.sum(), oos.sum()))
    rows.append(summ(base_ref, "BASE_REF", oos.sum(), (oos & c.ref_gate).sum()))
    scores = {}; masks = {}
    for kind in ("LR", "GBC", "GBR"):
        t0 = time.time()
        scores[kind] = walk_forward(c, kind, folds)
        print(kind, f"{time.time() - t0:.0f}s", flush=True)
    rng = np.random.default_rng(SEED)
    rand_rows = []
    for name, (kind, rule, uni) in VARIANTS.items():
        sc, thr_tr = scores[kind]
        m = select(c, sc, thr_tr, rule, uni, folds)
        masks[name] = m
        p = portfolio(c, m)
        d = summ(p, name, oos.sum(), m.sum())
        # random filter with the same take-rate within the same universe (bootstrap distribution)
        uni_idx = np.where((oos & (c.ref_gate if uni == "ref" else True)).values)[0]
        k = int(m.sum()); sims = []
        for _ in range(500):
            rm = np.zeros(len(c), bool); rm[rng.choice(uni_idx, k, replace=False)] = True
            f = portfolio(c, rm); f = f[f.status == "filled"]
            sims.append(f.R.mean())
        sims = np.array(sims)
        d["rand_net_med"] = round(np.median(sims), 3); d["rand_net_p95"] = round(np.quantile(sims, 0.95), 3)
        d["p_vs_random"] = round((sims >= d["net_R"]).mean(), 3)
        rows.append(d)
        print(d, flush=True)
    # annual folds for the LR (robustness of the fold scheme)
    sc_y, thr_y = walk_forward(c, "LR", year_folds(c))
    for name in ("V03_LR_ev0",):
        kind, rule, uni = VARIANTS[name]
        m = select(c, sc_y, thr_y, rule, uni, year_folds(c))
        rows.append(summ(portfolio(c, m), name + "_annualfolds", oos.sum(), m.sum()))
    V = pd.DataFrame(rows)
    V.to_csv(os.path.join(OUT, "variants_DEV_OOS.csv"), index=False)
    # OOF predictions
    oof = c.loc[oos, ["cid", "sym", "t_signal", "side", "status", "R", "gross_R", "ref_gate"]].copy()
    for kind in scores:
        oof[f"score_{kind}"] = scores[kind][0][oos]
    for name, m in masks.items():
        oof[f"take_{name}"] = m[oos]
    oof.to_csv(os.path.join(OUT, "oof_predictions_DEV.csv"), index=False)
    # by-quarter lift table
    q = []
    for a, b in folds:
        w = (c.t_signal >= a) & (c.t_signal < b)
        r = dict(quarter=str(a.date()))
        for nm, mk in [("unfilt", w), ("REF", w & c.ref_gate)] + [(n, w & masks[n]) for n in ("V01_LR_top30", "V03_LR_ev0", "V04_GBC_top30", "V09_GBR_pos")]:
            f = portfolio(c, mk); f = f[f.status == "filled"]
            r[f"{nm}_n"] = len(f); r[f"{nm}_net"] = round(f.R.mean(), 3) if len(f) else np.nan
        q.append(r)
    pd.DataFrame(q).to_csv(os.path.join(OUT, "by_quarter_DEV_OOS.csv"), index=False)
    print(V.to_string())
    return V


def shuffle_test(n=20):
    """Labels permuted inside every training fold: the OOS lift over 'unfiltered' must be ~0."""
    c = load("DEV"); oos = c.t_signal >= "2022-01-01"; folds = quarter_folds(c)
    base = portfolio(c, oos); base = base[base.status == "filled"].R.mean()
    rows = []
    for kind, rule in (("LR", ("abs", 0.0)), ("GBC", ("top", 0.30)), ("GBR", ("abs", 0.0))):
        for i in range(n):
            rng = np.random.default_rng(100 + i)
            sc, thr = walk_forward(c, kind, folds, shuffle_rng=rng)
            m = select(c, sc, thr, rule, "all", folds)
            f = portfolio(c, m); f = f[f.status == "filled"]
            rows.append(dict(model=kind, rule=str(rule), rep=i, trades=len(f), net_R=f.R.mean(), lift=f.R.mean() - base))
        print(kind, pd.DataFrame(rows).query("model == @kind").lift.describe().round(3).to_dict(), flush=True)
    df = pd.DataFrame(rows); df.to_csv(os.path.join(OUT, "shuffle_label_test.csv"), index=False)
    return df


# ================================================================================================ final model + VAL
def fit_full(kind, c):
    F = feats(c)
    s_tr, _, model = fit_predict(kind, c, c.iloc[:1], F)
    return model, s_tr, F


def importance(kind="LR"):
    c = load("DEV"); model, s_tr, F = fit_full(kind, c)
    if kind == "LR":
        m, sc = model
        imp = pd.DataFrame(dict(feature=F, coef_std=m.coef_[0])).sort_values("coef_std", key=abs, ascending=False)
    else:
        imp = pd.DataFrame(dict(feature=F, gain=model.booster_.feature_importance("gain"))).sort_values("gain", ascending=False)
    imp.to_csv(os.path.join(OUT, f"importance_{kind}.csv"), index=False)
    return imp


def score_period(kind, rule, c_train, c_eval):
    F = feats(c_train)
    s_tr, s_ev, model = fit_predict(kind, c_train, c_eval, F)
    thr = thresh(rule, s_tr)
    return s_ev, thr, model


def main_val(names):
    dev = load("DEV"); val = load("VAL")
    assert (val.t_signal < pd.Timestamp("2025-07-01")).all()
    rows = [summ(portfolio(val, np.ones(len(val), bool)), "BASE_unfiltered", len(val), len(val)),
            summ(portfolio(val, val.ref_gate), "BASE_REF", len(val), val.ref_gate.sum())]
    pp = {"BASE_REF": per_pair(portfolio(val, val.ref_gate)), "BASE_unfiltered": per_pair(portfolio(val, np.ones(len(val), bool)))}
    out = val[["cid", "sym", "t_signal", "ref_gate"]].copy()
    for name in names:
        kind, rule, uni = VARIANTS[name]
        s, thr, _ = score_period(kind, rule, dev, val)
        m = pd.Series(s >= thr, index=val.index)
        if uni == "ref":
            m &= val.ref_gate
        p = portfolio(val, m)
        rows.append(summ(p, name, len(val), m.sum()))
        pp[name] = per_pair(p)
        out[f"score_{name}"] = s; out[f"take_{name}"] = m.values; out[f"thr_{name}"] = thr
        p.to_csv(os.path.join(OUT, f"trades_{name}_VAL.csv"), index=False)
    V = pd.DataFrame(rows); V.to_csv(os.path.join(OUT, "variants_VAL.csv"), index=False)
    out.to_csv(os.path.join(OUT, "val_scores.csv"), index=False)
    for n, df in pp.items():
        df.to_csv(os.path.join(OUT, f"perpair_{n}_VAL.csv"), index=False)
    print(V.to_string())
    for n, df in pp.items():
        print(n); print(df.to_string(index=False))
    return V


# ================================================================================================ live retrain schedule
def retrain_schedule(c, start, end, kind="LR", rule=("abs", 0.0), min_labels=400, guard_months=12, min_oos_trades=40):
    """Proposed live procedure ('learns from new trades'), replayed on historical candidates.
    On the 1st of every month: retrain on every candidate whose outcome is known (t_exit before the month start;
    expanding window). The model is always scored in SHADOW mode. It is allowed to filter live orders in a month only if
    (a) it was trained on >= min_labels filled setups, (b) its shadow selections over the trailing `guard_months`
    months (each scored by the model that existed at the time, i.e. true OOS) contain >= min_oos_trades trades, and
    (c) their net avg R is > 0 AND >= the REF hand rules' net avg R over the same months. Otherwise REF rules trade.
    Returns (monthly decision table, boolean take-mask of the traded portfolio, shadow ML mask)."""
    F = feats(c)
    months = pd.date_range(start, end, freq="MS")
    shadow = pd.Series(False, index=c.index); live = pd.Series(False, index=c.index); log = []
    for a in months:
        b = a + pd.offsets.MonthBegin(1)
        tr = c[(c.t_signal < a) & ((c.status != "filled") | (c.t_exit < a))]
        te_m = (c.t_signal >= a) & (c.t_signal < b)
        n_lab = int((tr.status == "filled").sum())
        use_ml = False; g_ml = g_ref = np.nan; g_n = 0
        if n_lab >= min_labels and te_m.any():
            s_tr, s_te, _ = fit_predict(kind, tr, c[te_m], F)
            shadow.loc[te_m[te_m].index] = s_te >= thresh(rule, s_tr)
            g0 = a - pd.DateOffset(months=guard_months)
            win = (c.t_signal >= g0) & (c.t_signal < a) & (c.t_exit < a).fillna(True)
            fm = portfolio(c, win & shadow); fm = fm[fm.status == "filled"]
            fr = portfolio(c, win & c.ref_gate); fr = fr[fr.status == "filled"]
            g_n = len(fm); g_ml = fm.R.mean() if g_n else np.nan; g_ref = fr.R.mean() if len(fr) else 0.0
            use_ml = g_n >= min_oos_trades and g_ml > 0 and g_ml >= g_ref
        live |= te_m & (shadow if use_ml else c.ref_gate)
        log.append(dict(month=str(a.date()), labels=n_lab, guard_ml_trades=g_n, guard_ml_net=round(g_ml, 3) if g_ml == g_ml else None,
                        guard_ref_net=round(g_ref, 3) if g_ref == g_ref else None, mode="ML" if use_ml else "REF"))
    return pd.DataFrame(log), live, shadow


def main_retrain_dev():
    c = load("DEV")
    log, live, shadow = retrain_schedule(c, "2022-01-01", "2023-12-01")
    log.to_csv(os.path.join(OUT, "retrain_schedule_DEV.csv"), index=False)
    oos = c.t_signal >= "2022-01-01"
    r = [summ(portfolio(c, live), "V11_monthly_retrain_guarded", oos.sum(), live.sum()),
         summ(portfolio(c, shadow & oos), "V11s_monthly_retrain_shadowML", oos.sum(), (shadow & oos).sum())]
    print(log.to_string()); print(pd.DataFrame(r).to_string())
    pd.DataFrame(r).to_csv(os.path.join(OUT, "variants_DEV_OOS_retrain.csv"), index=False)


# ================================================================================================ examples
EXCOLS = ["cid", "sym", "side", "zone_kind", "t_zone", "zone_lo", "zone_hi", "t_touch", "t_signal", "session", "entry",
          "sl", "tp", "rr", "status", "t_fill", "fill", "t_exit", "exit", "exit_reason", "gross_R", "R", "usd_01",
          "ref_gate", "f_n_bias_al", "f_dpos", "f_stop_atr", "f_sweep5"]


M_UNI = {k: v[2] for k, v in VARIANTS.items()}


def examples(name, n_take=10, n_skip=5, seed=3):
    val = load("VAL"); sc = pd.read_csv(os.path.join(OUT, "val_scores.csv"))
    val = val.merge(sc[["cid", f"score_{name}", f"take_{name}", f"thr_{name}"]], on="cid")
    tk = pd.read_csv(os.path.join(OUT, f"trades_{name}_VAL.csv"))
    tk = val[val.cid.isin(tk[tk.status == "filled"].cid)]
    uni = val.ref_gate if M_UNI.get(name) == "ref" else True
    sk = val[(~val[f"take_{name}"]) & (val.status == "filled") & uni]
    # stratified for chart checking: half winners, half losers (a plain random draw of 10 was all losers)
    ex_t = pd.concat([tk[tk.R > 0].sample(min(n_take // 2, int((tk.R > 0).sum())), random_state=seed),
                      tk[tk.R <= 0].sample(min(n_take // 2, int((tk.R <= 0).sum())), random_state=seed)]).sort_values("t_signal")
    ex_s = sk.sample(min(n_skip, len(sk)), random_state=seed).sort_values("t_signal")
    cols = EXCOLS + [f"score_{name}", f"thr_{name}"]
    ex = pd.concat([ex_t.assign(model="TOOK")[["model"] + cols], ex_s.assign(model="SKIPPED")[["model"] + cols]])
    ex.to_csv(os.path.join(OUT, f"examples_{name}_VAL.csv"), index=False)
    print(ex.to_string())


# ================================================================================================ check
def check(period):
    """REF gates applied to the candidate set + portfolio emulation must reproduce canonical REF exactly."""
    c = load(period)
    p = portfolio(c, c.ref_gate)
    f = p[p.status == "filled"]
    can = pd.read_csv(os.path.join(RESULTS, "RECON", f"trades_CANON_{period}.csv"), parse_dates=["t_signal"])
    cf = can[can.status == "filled"]
    k1 = set(zip(f.sym, f.t_signal, f.R.round(6))); k2 = set(zip(cf.sym, cf.t_signal, cf.R.round(6)))
    print(f"{period}: emulated REF {len(f)} trades gross {f.gross_R.mean():.3f} net {f.R.mean():.3f} | canonical "
          f"{len(cf)} gross {cf.gross_R.mean():.3f} net {cf.R.mean():.3f} | identical trades: {len(k1 & k2)}; "
          f"only-emul {len(k1 - k2)}; only-canon {len(k2 - k1)}")
    return len(k1 ^ k2) == 0


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        assert sys.argv[2] in ("DEV", "VAL")
        gen(sys.argv[2])
    elif cmd == "check":
        check(sys.argv[2])
    elif cmd == "dev":
        main_dev()
    elif cmd == "shuffle":
        shuffle_test(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    elif cmd == "importance":
        print(importance(sys.argv[2] if len(sys.argv) > 2 else "LR").to_string())
    elif cmd == "retrain":
        main_retrain_dev()
    elif cmd == "val":
        main_val(sys.argv[2:])
    elif cmd == "examples":
        examples(sys.argv[2])
