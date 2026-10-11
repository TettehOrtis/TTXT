"""BT8b shared helpers (own file; nothing shared is edited).

Forces SMC_DATASET=duka and a duka-only BT6B_CACHE BEFORE common is imported (ERRORS #29), although BT8b never calls the
bt6b / recon context builders: it works from the BT13 candidate pickles plus raw duka bars (cache_duka) and common.simulate.
bt8_ml is deliberately NOT imported (it imports recon_pipeline, which memoises every M1 frame -> RAM); portfolio() and the
weekly block bootstrap are copied verbatim from bt8_ml.portfolio / bt13_report.week_boot.
"""
import os, sys
os.environ["SMC_DATASET"] = "duka"
_SCR = os.environ.get("BT13_SCRATCH", os.path.join(os.environ.get("TEMP", "/tmp"), "bt13_scratch"))
os.environ["BT6B_CACHE"] = os.path.join(_SCR, "bt6b_cache_duka")
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                                   # noqa: E402
from common import RESULTS, stats, COMM, STOP_SLIP, pip, USD_PIP_01   # noqa: E402,F401

assert common.DATASET == "duka" and "HIST" in common.PERIODS
OUT = os.path.join(RESULTS, "BT8b"); os.makedirs(OUT, exist_ok=True)
BT13 = os.path.join(RESULTS, "BT13")
ORIG9 = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "XAUUSD"]
CROSS15 = list(common.EXTRA)
ALL25 = ORIG9 + ["EURGBP"] + CROSS15
TEST_START = pd.Timestamp("2025-07-01")
ERAS = [("2005-08", 2005, 2008), ("2009-14", 2009, 2014), ("2015-20", 2015, 2020), ("DEV", 2021, 2023), ("VAL", 2024, 2025)]
ORDER_COLS = ["t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min"]


def era_of_year(y):
    for n, a, b in ERAS:
        if a <= y <= b:
            return n
    return "other"


def cov_excl():
    cv = pd.read_csv(os.path.join(BT13, "coverage_minutes.csv"))
    return set(zip(cv[cv.excluded].sym, cv[cv.excluded].year))


def load_cands(periods=("HIST", "DEV", "VAL")):
    cs = []
    for p in periods:
        c = pd.read_pickle(os.path.join(BT13, f"candidates_{p}.pkl"))
        cs.append(c)
    c = pd.concat(cs, ignore_index=True)
    assert (c.t_signal < TEST_START).all(), "TEST row found"
    assert (c.t_feat_max <= c.t_signal).all()
    assert c.cid.is_unique
    c["year"] = c.t_signal.dt.year
    c["era"] = c.year.map(era_of_year)
    ex = cov_excl()
    c["excluded_cov"] = [(s, y) in ex for s, y in zip(c.sym, c.year)]
    c["grp"] = np.where(c.sym.isin(ORIG9), "ORIG9", np.where(c.sym == "EURGBP", "EURGBP", "CROSS15"))
    return c


# ------------------------------------------------------------------------------------------ portfolio (from bt8_ml)
def portfolio(c, mask, cols=None):
    """REF dedupe (per pair & t_signal keep earliest touch) + one position per pair, emulating simulate(one_at_a_time).
    `cols` optionally maps status/t_exit to policy-specific columns (dict with keys 'status', 't_exit')."""
    s = c[np.asarray(mask)]
    if not len(s):
        return s
    st_c = (cols or {}).get("status", "status"); te_c = (cols or {}).get("t_exit", "t_exit")
    s = s.sort_values(["sym", "t_signal", "t_touch", "zone_kind", "ord"], kind="stable").drop_duplicates(["sym", "t_signal"])
    keep = []
    for sym, g in s.groupby("sym", sort=False):
        busy = np.datetime64("1970-01-01")
        ts = g.t_signal.values; st = g[st_c].values; te = g[te_c].values; bn = g.busy_nf.values; idx = g.index.values
        for i in range(len(g)):
            if ts[i] < busy:
                continue
            keep.append(idx[i])
            if st[i] == "filled":
                busy = te[i]
            elif st[i] == "not_filled" and not pd.isna(bn[i]):
                busy = max(busy, bn[i])
    return s.loc[keep]


RNG = np.random.default_rng(88)


def week_boot(R, weeks, n=5000, rng=None):
    """Block bootstrap by calendar week (bt13_report.week_boot): returns (se of mean, 95% CI, P(mean<=0))."""
    rng = rng or RNG
    df = pd.DataFrame(dict(R=R, w=weeks))
    g = df.groupby("w").R.agg(["sum", "count"])
    s, c = g["sum"].values, g["count"].values
    k = len(g)
    idx = rng.integers(0, k, size=(n, k))
    means = s[idx].sum(1) / c[idx].sum(1)
    return means.std(ddof=1), np.quantile(means, [0.025, 0.975]), (means <= 0).mean()


def row(f, name, rcol="R", gcol="gross_R", ucol="usd_01", boot=True, w=None):
    """Summary of FILLED trades f (R in rcol, optional size weights w)."""
    if not len(f):
        return dict(variant=name, trades=0)
    w = np.ones(len(f)) if w is None else np.asarray(w, float)
    R = f[rcol].values * w; G = f[gcol].values * w
    ff = f.assign(R=R).sort_values("t_exit", kind="stable")
    eq = np.cumsum(ff.R.values); dd = (eq - np.maximum.accumulate(np.maximum(eq, 0))).min()
    gp, gl = R[R > 0].sum(), -R[R < 0].sum()
    d = dict(variant=name, trades=len(f), win_pct=round((f[rcol].values > 0).mean() * 100, 1), gross_R=round(G.mean(), 3),
             net_R=round(R.mean(), 3), total_net_R=round(R.sum(), 1), PF=round(gp / gl, 2) if gl > 0 else np.inf,
             maxDD_R=round(dd, 1), usd_01=round(float((f[ucol].values * w).sum()), 0))
    if boot and len(f) > 5:
        wk = f.t_signal.dt.to_period("W").astype(str).values
        se, ci, p0 = week_boot(R, wk, 3000)
        seg, _, _ = week_boot(G, wk, 3000)
        d.update(t_boot=round(R.mean() / se, 2), t_boot_gross=round(G.mean() / seg, 2), ci95=f"[{ci[0]:+.3f}, {ci[1]:+.3f}]")
    return d


def add_gross(t):
    f = t.status == "filled"
    slipped = t.exit_reason.isin(["SL", "BE", "GAP_SL"])
    t["gross_R"] = np.where(f, (t.gross_pips + np.where(slipped, STOP_SLIP, 0.0)) / t.risk_pips, np.nan)
    return t
