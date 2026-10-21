"""PREREG addendum 19: indicator ideas from mql5.com codebase page 3 as entry filters. Usage: python aut35_indicators.py STAGE VARIANT_INDEX"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut30_cooldown_vol as K
import aut32_filters as A
import common as C

SEEDS = 30


def wilder(x, n):
    return pd.Series(x).ewm(alpha=1.0 / n, adjust=False).mean().values


def m15_features(df):
    h, l, c = df.high.values, df.low.values, df.close.values
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr14 = wilder(tr, 14)
    up, dn = h - np.r_[h[0], h[:-1]], np.r_[l[0], l[:-1]] - l
    pdm, mdm = np.where((up > dn) & (up > 0), up, 0.0), np.where((dn > up) & (dn > 0), dn, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi, mdi = 100 * wilder(pdm, 14) / atr14, 100 * wilder(mdm, 14) / atr14
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    adx = wilder(np.nan_to_num(dx), 14)
    # Hurst by rescaled range over 100 log returns
    lr = np.diff(np.log(c), prepend=np.log(c[0]))
    n = 100
    hurst = np.full(len(c), np.nan)
    w = sliding_window_view(lr, n)
    z = w - w.mean(axis=1, keepdims=True)
    cs = np.cumsum(z, axis=1)
    rr = cs.max(axis=1) - cs.min(axis=1)
    ss = w.std(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        hurst[n - 1:] = np.log(rr / ss) / np.log(n)
    # SuperTrend (ATR 10, x3)
    atr10 = wilder(tr, 10)
    hl2 = (h + l) / 2
    ub, lb = hl2 + 3 * atr10, hl2 - 3 * atr10
    fu, fl = ub.copy(), lb.copy()
    st = np.ones(len(c))
    for k in range(1, len(c)):
        fu[k] = ub[k] if (ub[k] < fu[k - 1] or c[k - 1] > fu[k - 1]) else fu[k - 1]
        fl[k] = lb[k] if (lb[k] > fl[k - 1] or c[k - 1] < fl[k - 1]) else fl[k - 1]
        if st[k - 1] == 1:
            st[k] = -1 if c[k] < fl[k] else 1
        else:
            st[k] = 1 if c[k] > fu[k] else -1
    med = pd.Series(atr14).rolling(500, min_periods=100).median().values
    return dict(ADX=adx, DI=np.where(pdi > mdi, 1, -1), HURST=hurst, ST=st, ATRREL=atr14 / med)


if __name__ == "__main__":
    stage, vi = sys.argv[1], int(sys.argv[2])
    name = N.NAMES[vi]
    sp = N.spec_for(name)
    F = R.add_trends(X.build_stage(stage))
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
    k = ok[i0] & (i0 > 3)
    i, side = i0[k], s0[k]
    df15 = C.bars(X.SYM, "M15")
    mf = m15_features(df15)
    j = np.clip(C.asof_index(df15, F["tc"]), 0, len(df15) - 1)
    f = pd.DataFrame({"t_signal": F["tc"][i]})
    for key in ("ADX", "HURST", "ATRREL"):
        f[key] = mf[key][j][i]
    f["DI"] = mf["DI"][j][i] == side
    f["ST"] = mf["ST"][j][i] == side
    h_, l_ = F["h"], F["l"]
    f["FVG"] = np.where(side == 1, l_[i] > h_[i - 2], h_[i] < l_[i - 2])
    r = C.simulate(X.SYM, R.orders(F, i, side, sp["exit"]), one_at_a_time=False)
    r = r[r.status == "filled"].sort_values("t_signal", kind="stable").reset_index(drop=True)
    r = r.merge(f, on="t_signal", how="left")
    ts = pd.DatetimeIndex(r.t_signal).values.astype("datetime64[ns]").astype(np.int64)
    te = pd.DatetimeIndex(r.t_exit).values.astype("datetime64[ns]").astype(np.int64)
    cutf = os.path.join(X.OUTD, f"ind_cuts_{vi}.json")
    if stage == "EXPLORE":
        cuts = {m: {q: float(np.nanquantile(r[m].values, p)) for q, p in (("q10", .1), ("q20", .2), ("q90", .9))} for m in ("ADX", "HURST", "ATRREL")}
        json.dump(cuts, open(cutf, "w"))
    cuts = json.load(open(cutf))
    nz = lambda s: s.fillna(np.inf)  # noqa: E731  (missing feature -> keep the signal)
    masks = {"BASE": np.ones(len(r), bool),
             "ADX10": (nz(r.ADX) >= cuts["ADX"]["q10"]).values, "ADX20": (nz(r.ADX) >= cuts["ADX"]["q20"]).values,
             "DI15": r.DI.fillna(True).values.astype(bool),
             "HURST10": (nz(r.HURST) >= cuts["HURST"]["q10"]).values, "HURST20": (nz(r.HURST) >= cuts["HURST"]["q20"]).values,
             "SUPERTREND": r.ST.fillna(True).values.astype(bool), "FVG": r.FVG.values.astype(bool),
             "ATRLOW10": (nz(r.ATRREL) >= cuts["ATRREL"]["q10"]).values, "ATRHIGH10": (r.ATRREL.fillna(0) <= cuts["ATRREL"]["q90"]).values}
    rows, rng, base_n = [], np.random.default_rng(7), None
    for fname, m in masks.items():
        sel = A.apply(r, ts, te, m)
        pf, wr = A.pf_wr(sel)
        if fname == "BASE":
            base_n = len(sel)
        row = dict(variant=name, stage=stage, filt=fname, signals_kept_pct=round(100 * m.mean(), 1), trades=len(sel),
                   kept_pct=round(100 * len(sel) / base_n, 1), per_day=round(len(sel) / F["days"], 2), wr=round(wr, 2), pf=round(pf, 3),
                   usd_trade=round(sel.usd_01.mean(), 3), net=round(sel.usd_01.sum()))
        if fname != "BASE":
            p = m.mean()
            pfs, wrs = [], []
            for _ in range(SEEDS):
                a, b = A.pf_wr(A.apply(r, ts, te, rng.random(len(r)) < p))
                pfs.append(a); wrs.append(b)
            row.update(rand_pf_p90=round(np.nanquantile(pfs, .9), 3), rand_wr_p90=round(np.nanquantile(wrs, .9), 2), rand_pf_mean=round(np.nanmean(pfs), 3))
        rows.append(row)
        print(name, stage, fname, row["kept_pct"], row["wr"], row["pf"], flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"ind_{stage}_{vi}.csv"), index=False)
    print("done", flush=True)
