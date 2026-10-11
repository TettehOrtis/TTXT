"""Parity test: replay cached history bar by bar through the LIVE engine and compare its stage-4 signals with the
canonical REF orders of recon_pipeline.build_orders(CANON) on the full-history batch context.

  .venv/Scripts/python.exe smc_bot/live/parity_test.py replay            # DEV 2023 + VAL 2024-H1, 9 pairs (parallel)
  .venv/Scripts/python.exe smc_bot/live/parity_test.py replay EURUSD DEV_2023H1
  .venv/Scripts/python.exe smc_bot/live/parity_test.py window            # same replay with SHORT windows (sensitivity)
  .venv/Scripts/python.exe smc_bot/live/parity_test.py noext             # negative control: expiry-cap artefact left in
  .venv/Scripts/python.exe smc_bot/live/parity_test.py feed              # MT5 native bars vs cached M1-resampled bars

At every closed M5 bar T in the span, the engine only sees the last W bars of each timeframe with close <= T (exactly
what the live scanner gets from MT5). A live signal = an order with t_signal == T. Match = same pair, t_signal, side and
entry/SL/TP within 1 point. Only orders are compared (the scanner does not know whether a previous trade is still
open; simulate's one-at-a-time rule is applied downstream). TEST (2025-07+) is never replayed.
"""
import os, sys, time, json
from collections import Counter
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ref_engine as E                                      # noqa: E402
from ref_engine import R, common, CANON, TFS, WINDOW, PAIRS9, point   # noqa: E402

OUT = os.path.join(E.SMC, "results", "SCANNER")
os.makedirs(OUT, exist_ok=True)
SPANS = {"DEV_2023": ("DEV", "2023-01-01", "2024-01-01"), "VAL_2024H1": ("VAL", "2024-01-01", "2024-07-01"),
         "DEV_2023H1": ("DEV", "2023-01-01", "2023-07-01")}


def full_raw(sym):
    return {tf: E.raw_from_df(common.bars(sym, tf)) for tf in TFS}


def replay(sym, span, window=None, noext=False):
    period, a, b = SPANS[span]
    assert period != "TEST" and pd.Timestamp(b) <= pd.Timestamp("2025-07-01")
    W = dict(WINDOW if window is None else window)
    F = full_raw(sym)
    eng = E.RefEngine(sym)
    E.EXTEND_M30_TC = not noext                  # negative control only
    if window is not None:
        E.MIN_BARS.update({k: min(E.MIN_BARS[k], v) for k, v in W.items()})
    tc5 = F["M5"]["tc"]
    idx = np.where((tc5 >= np.datetime64(a)) & (tc5 < np.datetime64(b)))[0]
    live, stages, mism, errs = [], Counter(), 0, []
    t0 = time.time()
    for i in idx:
        T = tc5[i]
        raw = {}
        for tf in TFS:
            A = F[tf]
            i1 = int(np.searchsorted(A["tc"], T, "right")); i0 = max(0, i1 - W[tf])
            raw[tf] = {k: v[i0:i1] for k, v in A.items()}
        try:
            st = eng.evaluate(raw, T)
        except Exception as ex:                                # keep going, report
            errs.append(f"{T}: {ex!r}"); continue
        stages[st["stage"]] += 1
        if not st.get("trace_matches_canonical", True):
            mism += 1
            if mism <= 5:
                errs.append(f"{T}: " + "; ".join(st["warnings"]))
        for s in st.get("signals_new", []):
            live.append(dict(sym=sym, t_signal=pd.Timestamp(s["t_signal"]), side=s["side"], entry=s["entry"], sl=s["sl"],
                             tp=s["tp"], rr=s["rr"], poi_kind=s.get("poi_kind"), t_touch=s.get("t_touch")))
    dt_ = time.time() - t0
    canon = R.build_orders(sym, period, CANON, Counter())
    canon = canon[(canon.t_signal >= a) & (canon.t_signal < b)]
    canon = canon[["t_signal", "side", "entry", "sl", "tp", "rr", "zone_kind", "t_touch"]].assign(sym=sym)
    return dict(sym=sym, span=span, live=pd.DataFrame(live), canon=canon.reset_index(drop=True), steps=len(idx),
                sec=round(dt_, 1), ms_per_step=round(1000 * dt_ / max(1, len(idx)), 2), stages=dict(stages),
                trace_mismatch_steps=mism, errors=errs)


def compare(res):
    rows = []
    for r in res:
        sym = r["sym"]; pt = point(sym)
        L = r["live"]; C = r["canon"]
        Lk = {(x.t_signal, int(x.side)): x for x in L.itertuples()} if len(L) else {}
        Ck = {(pd.Timestamp(x.t_signal), int(x.side)): x for x in C.itertuples()}
        for k in sorted(set(Lk) | set(Ck)):
            l, c = Lk.get(k), Ck.get(k)
            row = dict(span=r["span"], sym=sym, t_signal=k[0], side=k[1])
            if l is not None and c is not None:
                de, ds, dtp = abs(l.entry - c.entry) / pt, abs(l.sl - c.sl) / pt, abs(l.tp - c.tp) / pt
                row.update(status="match" if max(de, ds, dtp) <= 1 else "price_diff", d_entry_pts=round(de, 3),
                           d_sl_pts=round(ds, 3), d_tp_pts=round(dtp, 3))
            else:
                row.update(status="live_only" if c is None else "canon_only")
            src = c if c is not None else l
            row.update(entry=src.entry, sl=src.sl, tp=src.tp, rr=src.rr)
            rows.append(row)
    return pd.DataFrame(rows)


def run_replays(jobs, tag, window=None, noext=False):
    from multiprocessing import Pool
    with Pool(min(8, len(jobs))) as p:
        res = p.starmap(replay, [(s, sp, window, noext) for s, sp in jobs])
    cmp_ = compare(res)
    cmp_.to_csv(os.path.join(OUT, f"parity_{tag}.csv"), index=False)
    summ = []
    for r in res:
        c = cmp_[(cmp_.sym == r["sym"]) & (cmp_.span == r["span"])] if len(cmp_) else cmp_
        vc = c.status.value_counts().to_dict() if len(c) else {}
        summ.append(dict(span=r["span"], sym=r["sym"], canon_orders=len(r["canon"]), live_signals=len(r["live"]),
                         match=vc.get("match", 0), price_diff=vc.get("price_diff", 0), canon_only=vc.get("canon_only", 0),
                         live_only=vc.get("live_only", 0), steps=r["steps"], ms_per_step=r["ms_per_step"],
                         trace_mismatch_steps=r["trace_mismatch_steps"], errors=len(r["errors"]),
                         stages=json.dumps(r["stages"], sort_keys=True)))
        for e in r["errors"][:5]:
            print("  ", r["sym"], r["span"], e)
    S = pd.DataFrame(summ)
    S.to_csv(os.path.join(OUT, f"parity_summary_{tag}.csv"), index=False)
    print(S.to_string())
    print("TOTAL", S[["canon_orders", "live_signals", "match", "price_diff", "canon_only", "live_only",
                      "trace_mismatch_steps", "errors"]].sum().to_dict())
    bad = cmp_[cmp_.status != "match"] if len(cmp_) else cmp_
    if len(bad):
        print(bad.to_string())
    return S, cmp_


# ------------------------------------------------------------------------------------------------ feed check (MT5)
def feed_check(days=10):
    """Compare MT5 native M5/M30/H1/H4 bars of the Zero demo with the cached bars (resampled from M1) where both
    exist. Read-only: copy_rates_* only."""
    import MetaTrader5 as mt5
    import ref_scanner as S
    br = S.connect()
    rows = []
    for sym in PAIRS9:
        for tf in TFS:
            nat = S.fetch(sym, tf, int(days * 1440 / E.TF_MIN[tf]) + 5, now=None, closed_only=True)
            if nat is None:
                rows.append(dict(sym=sym, tf=tf, note="no MT5 data")); continue
            cb = common.bars(sym, tf)
            n = pd.DataFrame(dict(time=nat["t"], o=nat["o"], h=nat["h"], l=nat["l"], c=nat["c"]))
            c = cb[(cb.time >= n.time.min()) & (cb.time <= n.time.max())][["time", "open", "high", "low", "close"]]
            m = n.merge(c, on="time", how="outer", indicator=True).sort_values("time").reset_index(drop=True)
            # a cached bar is PARTIAL if it is the last cached bar (cache built mid-bar) or the M1 cache has a hole right
            # after it (MT5 has the next bar, the cache does not)
            nxt_mt5_only = (m._merge.shift(-1) == "left_only") & (m.time.shift(-1) <= c.time.max())
            partial = (m.time == c.time.max()) | nxt_mt5_only
            both = m[(m._merge == "both")]
            pt = point(sym)
            diff = ((both.o - both.open).abs().gt(pt / 2) | (both.h - both.high).abs().gt(pt / 2) |
                    (both.l - both.low).abs().gt(pt / 2) | (both.c - both.close).abs().gt(pt / 2))
            rows.append(dict(sym=sym, tf=tf, overlap_from=str(n.time.min()), overlap_to=str(c.time.max()),
                             both=len(both), cache_holes=int(((m._merge == "left_only") & (m.time <= c.time.max())).sum()),
                             cache_only=int((m._merge == "right_only").sum()), ohlc_diff_all=int(diff.sum()),
                             ohlc_diff_complete_bars=int((diff & ~partial[both.index]).sum())))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "feed_check.csv"), index=False)
    print(df.to_string())
    return df


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "replay"
    if cmd == "replay":
        if len(sys.argv) > 3:
            jobs, tag = [(sys.argv[2], sys.argv[3])], f"{sys.argv[2]}_{sys.argv[3]}"
        else:
            jobs, tag = [(s, sp) for sp in ("DEV_2023", "VAL_2024H1") for s in PAIRS9], "main"
        run_replays(jobs, tag)
    elif cmd == "window":                      # sensitivity: much shorter windows -> which mismatches appear?
        short = dict(M5=1200, M30=600, H1=1200, H4=300)
        run_replays([(s, "DEV_2023H1") for s in PAIRS9], "short_window", window=short)
    elif cmd == "noext":                       # negative control: without the M30 close-time extension
        run_replays([(s, "DEV_2023") for s in PAIRS9], "no_m30_extension", noext=True)
    elif cmd == "feed":
        feed_check()
