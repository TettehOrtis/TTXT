"""BT13: frozen canonical REF on never-seen history (HIST 2005-2020, HistData/duka M1, 25 instruments).

Run from the TTXT root (SMC_DATASET is forced to duka here):
    .venv/Scripts/python.exe smc_bot/bt/bt13_run.py cov              # minutes per instrument-year
    .venv/Scripts/python.exe smc_bot/bt/bt13_run.py sym EURUSD       # one instrument: REF HIST/DEV/VAL + candidates
    .venv/Scripts/python.exe smc_bot/bt/bt13_run.py all [workers]    # every instrument, process pool (default 3)

REF = recon_analysis.CANON (recon_pipeline BCFG + ob_def="core"), unchanged. Nothing shared is edited: recon_pipeline,
bt6b_pipeline and bt8_ml are imported only. bt6b's context pickles are redirected to a duka-only cache directory
(BT6B_CACHE) because bt6b.prepare keys its cache by sym+period only and would otherwise silently reuse Zero-data contexts.
TEST is never generated: PERIODS end at 2025-07-01 and the duka data ends 2024-12-31.
"""
import os, sys, time, gc, pickle
os.environ["SMC_DATASET"] = "duka"
_SCR = os.environ.get("BT13_SCRATCH", os.path.join(os.environ.get("TEMP", "/tmp"), "bt13_scratch"))
os.environ["BT6B_CACHE"] = os.path.join(_SCR, "bt6b_cache_duka")
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                                   # noqa: E402
from common import PERIODS, pip, COMM, STOP_SLIP, simulate, RESULTS   # noqa: E402
import recon_pipeline as R                                      # noqa: E402
import bt6b_pipeline as B                                       # noqa: E402
import bt8_ml as M                                              # noqa: E402

assert common.DATASET == "duka" and "HIST" in PERIODS
assert B.CACHE.endswith("bt6b_cache_duka"), B.CACHE
OUT = os.path.join(RESULTS, "BT13"); PARTS = os.path.join(OUT, "parts")
os.makedirs(PARTS, exist_ok=True)
CANON = R.cfg(R.BCFG, ob_def="core")
ORIG9 = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "XAUUSD"]
CROSS15 = list(common.EXTRA)
ALL25 = ORIG9 + ["EURGBP"] + CROSS15
MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]


def era_extra_pips(sym, years):
    """Approximate historical extra spread (pips) on top of today's Zero profile."""
    base = np.select([years <= 2008, years <= 2014, years <= 2020], [1.0, 0.4, 0.1], 0.0)
    return base * (1.0 if sym in MAJORS else 2.0)


# ------------------------------------------------------------------------------------------------ coverage
def coverage():
    rows = []
    for sym in ALL25:
        m1 = pd.read_parquet(os.path.join(common.ROOT, "data_duka", f"{sym}_M1.parquet"), columns=["time"])
        y = pd.to_datetime(m1.time).dt.year
        for yr, n in y.value_counts().sort_index().items():
            rows.append(dict(sym=sym, year=int(yr), minutes=int(n)))
    df = pd.DataFrame(rows)
    full = df[(df.year >= 2005) & (df.year <= 2024)]
    typ = full.groupby("sym").minutes.median().rename("typical")
    df = df.merge(typ, on="sym")
    df["ratio"] = (df.minutes / df.typical).round(3)
    df["excluded"] = df.ratio < 0.60
    df.to_csv(os.path.join(OUT, "coverage_minutes.csv"), index=False)
    print(df[df.ratio < 0.9].to_string())
    return df


# ------------------------------------------------------------------------------------------------ path info
def path_info(sym, t, ctx):
    """Per filled trade: time to MFE / MAE (minutes from fill), largest M1 range in R, SL-then-TP within 24h,
    bias at entry/exit and whether the H4 trend / combined H4+EMA bias turned against the trade during it
    (evaluated at every H4 / H1 close inside (t_fill, t_exit], closed bars only)."""
    m1 = common.bars(sym, "M1")
    t1 = m1.time.values; h = m1.high.values; l = m1.low.values; s = m1.spr.values
    H4 = ctx["H4"]; H1 = ctx["H1"]
    cols = dict(t_mfe_min=[], t_mae_min=[], max_m1_range_R=[], sl_then_tp_24h=[], bias_entry=[], bias_exit=[],
                h4_against_during=[], t_h4_against=[], bias_against_during=[], ctx_complete=[])
    for r in t.itertuples(index=False):
        if r.status != "filled":
            for k in cols:
                cols[k].append(np.nan)
            continue
        d = int(r.side)
        f = int(np.searchsorted(t1, np.datetime64(pd.Timestamp(r.t_fill)), "left"))
        k = int(np.searchsorted(t1, np.datetime64(pd.Timestamp(r.t_exit)), "left"))
        risk = r.risk_pips * pip(sym)
        hs, ls, ss = h[f:k + 1], l[f:k + 1], s[f:k + 1]
        if d == 1:
            i_mfe = int(np.argmax(hs)); i_mae = int(np.argmin(ls))
        else:
            i_mfe = int(np.argmin(ls + ss)); i_mae = int(np.argmax(hs + ss))
        cols["t_mfe_min"].append((t1[f + i_mfe] - t1[f]) / np.timedelta64(1, "m"))
        cols["t_mae_min"].append((t1[f + i_mae] - t1[f]) / np.timedelta64(1, "m"))
        cols["max_m1_range_R"].append(float((hs - ls).max() / risk) if risk > 0 else np.nan)
        stt = np.nan
        if r.exit_reason in ("SL", "GAP_SL") and np.isfinite(r.tp):
            e = int(np.searchsorted(t1, t1[k] + np.timedelta64(24, "h"), "right"))
            seg = (h[k + 1:e] >= r.tp) if d == 1 else ((l[k + 1:e] + s[k + 1:e]) <= r.tp)
            stt = float(seg.any())
        cols["sl_then_tp_24h"].append(stt)
        tf_, tx = np.datetime64(pd.Timestamp(r.t_fill)), np.datetime64(pd.Timestamp(r.t_exit))
        cols["bias_entry"].append(int(R.bias_at(ctx, np.datetime64(pd.Timestamp(r.t_signal)), True)[0]))
        cols["bias_exit"].append(int(R.bias_at(ctx, tx, True)[0]))
        a4, b4 = np.searchsorted(H4["tc"], tf_, "right"), np.searchsorted(H4["tc"], tx, "right")
        tr4 = H4["tr_i"][a4:b4].astype(int)
        ag = np.where(tr4 == -d)[0]
        cols["h4_against_during"].append(float(len(ag) > 0))
        cols["t_h4_against"].append(pd.Timestamp(H4["tc"][a4 + ag[0]]) if len(ag) else pd.NaT)
        a1, b1 = np.searchsorted(H1["tc"], tf_, "right"), np.searchsorted(H1["tc"], tx, "right")
        tt = np.unique(np.r_[H4["tc"][a4:b4], H1["tc"][a1:b1]])
        cols["bias_against_during"].append(float((R.bias_at(ctx, tt, True) == -d).any()) if len(tt) else 0.0)
        cols["ctx_complete"].append(float(tx <= H1["tc"][-1]))
    for k, v in cols.items():
        t[k] = v
    return t


def add_gross(t):
    f = t.status == "filled"
    slipped = t.exit_reason.isin(["SL", "BE", "GAP_SL"]) if "exit_reason" in t else False
    t["gross_R"] = np.where(f, (t.gross_pips + np.where(slipped, STOP_SLIP, 0.0)) / t.risk_pips, np.nan)
    return t


# ------------------------------------------------------------------------------------------------ per instrument
def do_sym(sym, periods=("HIST", "DEV", "VAL"), cands=True):
    t00 = time.time()
    for tf in ("M5", "M15", "M30", "H1", "H4"):
        common.bars(sym, tf)                                    # builds cache_duka/{sym}_{tf}.parquet on first call
    print(f"{sym}: bars ready ({time.time() - t00:.0f}s)", flush=True)
    for period in periods:
        t0 = time.time()
        from collections import Counter
        fun = Counter()
        X = R.pdata(sym, period); ctx = X["ctx"]
        o = R.build_orders(sym, period, CANON, fun)
        fun["12_orders"] += len(o)
        if len(o):
            t = simulate(sym, o)
            t = t[t.period == period].reset_index(drop=True)
            t = add_gross(t)
            t = path_info(sym, t, ctx)
            if period == "HIST":                                 # era-cost sensitivity: re-simulate with wider spread
                m1 = common.bars(sym, "M1"); orig = m1.spr.values.copy()
                yrs = m1.time.dt.year.values
                m1["spr"] = orig + era_extra_pips(sym, yrs) * pip(sym)
                te = simulate(sym, o)
                m1["spr"] = orig
                te = add_gross(te[te.period == period].reset_index(drop=True))
                te.to_pickle(os.path.join(PARTS, f"{sym}_{period}_ref_era.pkl"))
        else:
            t = pd.DataFrame()
        t.to_pickle(os.path.join(PARTS, f"{sym}_{period}_ref.pkl"))
        with open(os.path.join(PARTS, f"{sym}_{period}_funnel.pkl"), "wb") as fh:
            pickle.dump(dict(fun), fh)
        nf = int((t.status == "filled").sum()) if len(t) else 0
        print(f"{sym} {period}: REF orders {len(o)} filled {nf} ({time.time() - t0:.0f}s)", flush=True)
        if cands:
            t0 = time.time()
            c = M.build_candidates(sym, period)
            if len(c):
                s = simulate(sym, c.drop(columns=["sym"]), one_at_a_time=False)
                s = s[s.period == period]
                s = s.sort_values(["t_signal", "ord"], kind="stable").reset_index(drop=True)
                s["busy_nf"] = M._busy_nf(sym, s)
                s = add_gross(s)
                s["win"] = np.where(s.status == "filled", (s.R > 0).astype(float), np.nan)
                s = path_info(sym, s, ctx)
                assert (s.t_feat_max <= s.t_signal).all()
                s.to_pickle(os.path.join(PARTS, f"{sym}_{period}_cand.pkl"))
                print(f"{sym} {period}: candidates {len(s)} filled {int((s.status == 'filled').sum())} "
                      f"({time.time() - t0:.0f}s)", flush=True)
        R._PD.clear(); gc.collect()
        f = os.path.join(B.CACHE, f"{sym}_{period}_v3.pkl")
        if os.path.exists(f) and os.environ.get("BT13_KEEP_CTX") != "1":
            os.remove(f)
    R._memo.clear(); gc.collect()
    print(f"{sym}: done ({time.time() - t00:.0f}s)", flush=True)


def _worker(sym):
    try:
        do_sym(sym)
        return sym, "ok"
    except Exception as e:                                       # report, keep the pool going
        import traceback
        traceback.print_exc()
        return sym, f"ERR {e!r}"


def run_all(workers=3, syms=None):
    import multiprocessing as mp
    syms = syms or ALL25
    todo = [s for s in syms if not os.path.exists(os.path.join(PARTS, f"{s}_VAL_cand.pkl"))]
    print("todo", todo, flush=True)
    with mp.get_context("spawn").Pool(workers, maxtasksperchild=1) as pool:
        for sym, st in pool.imap_unordered(_worker, todo):
            print("FINISHED", sym, st, flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "cov":
        coverage()
    elif cmd == "sym":
        do_sym(sys.argv[2])
    elif cmd == "all":
        run_all(int(sys.argv[2]) if len(sys.argv) > 2 else 3, sys.argv[3:] or None)
