"""AUT10 - momentum continuation / trend following, autopsied.

Governing document: bt/AUTOPSY_BRIEF.md. Split: EXPLORE 2005-2011, VALIDATE 2011-2017,
STRESS 2017-2021, EXAM 2021+ SEALED (never read, never unlocked here).

Strategy family: price that has moved keeps moving. Enter WITH the move.
Base skeleton: a close beyond the prior-L extreme of the decision timeframe (Donchian
breakout) or a move of at least k x ATR over the last L bars (time-series momentum).
Decision on the CLOSED bar; fill at the open of the next M1 bar (simulate()'s own rule).
Every target and stop is a multiple of the M15 ATR of the last CLOSED M15 bar - never dollars.

Usage (from the TTXT root):
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut10_momentum.py stage0
    ... explore | ladder | validate | stress | examples
"""
import os
import sys
import time

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np                                                          # noqa: E402
np.seterr(divide="ignore", invalid="ignore")
import pandas as pd                                                         # noqa: E402

import common as C                                                          # noqa: E402
import autopsy_lib as A                                                     # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT10_momentum")
os.makedirs(OUT, exist_ok=True)
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 80)

BASE_TGT, BASE_STOP, BASE_HOLD = 1.5, 1.0, 1440          # the brief's reference ATR shape, 24h time limit
TGTS = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
STOPS = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
HOLDS = [60, 240, 480, 1440, 2880]                        # minutes, for the time-limit sweep (E3)
LADDER_WINDOW = 2880                                      # the path is scanned once over 48h, then cut per hold

RULES = {
    # name            tf     kind     L   k    htf
    "DONCH20":   dict(tf="M15", kind="donch", L=20),
    "DONCH55":   dict(tf="M15", kind="donch", L=55),
    "TSMOM12":   dict(tf="M15", kind="tsmom", L=12, k=1.0),
    "DONCH20H1": dict(tf="M15", kind="donch", L=20, htf="H1"),
    "M30DONCH20": dict(tf="M30", kind="donch", L=20),
}


# ===================================================================== features
def features(sym, rule):
    """One row per decision bar that fires a signal. Every column uses CLOSED bars only.

    Returned columns: t_signal (= close time of the decision bar), side, atr15 (price units,
    last closed M15 bar) and the f_* conditions from the catalogue.
    """
    r = RULES[rule]
    b = C.bars(sym, r["tf"]).reset_index(drop=True)
    P = C.pip(sym)
    hi, lo, cl, op = b.high.values, b.low.values, b.close.values, b.open.values
    n = len(b)
    atf = A.atr(b, 14).values                                        # ATR of the decision timeframe

    # ---------------- the trigger
    L = r["L"]
    dh = b.high.shift(1).rolling(L).max().values                     # prior L bars, bar i excluded
    dl = b.low.shift(1).rolling(L).min().values
    if r["kind"] == "donch":
        up, dn = cl > dh, cl < dl
        over = np.where(up, (cl - dh) / atf, np.where(dn, (dl - cl) / atf, np.nan))
    else:
        ret = cl - b.close.shift(L).values
        up, dn = ret > r["k"] * atf, ret < -r["k"] * atf
        over = np.abs(ret) / atf
    up, dn = np.nan_to_num(up, nan=False).astype(bool), np.nan_to_num(dn, nan=False).astype(bool)
    fresh_up = up & ~np.r_[False, up[:-1]]                           # the START of a run, not every bar of it
    fresh_dn = dn & ~np.r_[False, dn[:-1]]
    side = np.where(fresh_up, 1, np.where(fresh_dn, -1, 0))

    # ---------------- A1/A2/A7  higher timeframe
    def htf_rel(tf, span=50):
        hb = C.bars(sym, tf)
        e = C.ema(hb.close, span)
        rel = np.sign(hb.close.values - e)
        i = C.asof_index(hb, b.tclose.values)                        # last CLOSED htf bar (F4)
        ok = i >= 0
        j = np.clip(i, 0, len(hb) - 1)
        return np.where(ok, rel[j], np.nan), np.where(ok, e[j], np.nan)

    h1_rel, h1_ema = htf_rel("H1")
    h4_rel, _ = htf_rel("H4")
    flip = pd.Series(h1_rel).ne(pd.Series(h1_rel).shift(1)).cumsum()
    h1_age = pd.Series(np.arange(n)).groupby(flip.values).cumcount().values   # decision bars since the H1 flip

    # ---------------- A3 trend strength vs chop
    d = np.abs(np.r_[np.nan, np.diff(cl)])
    travel = pd.Series(d).rolling(20).sum().values
    net = np.abs(cl - b.close.shift(20).values)
    eff = np.where(travel > 0, net / travel, np.nan)                 # efficiency ratio 0..1
    e9, e21 = C.ema(b.close, 9), C.ema(b.close, 21)
    ema_sep = (e9 - e21) / atf

    # ---------------- A4/A5 volatility level and direction
    atr_rank = pd.Series(atf).rolling(96 * 250, min_periods=96 * 20).rank(pct=True).values
    atr_exp = atf / np.r_[[np.nan] * 20, atf[:-20]]

    # ---------------- A6/A8/C5 day context, FX day 22:00 -> 22:00
    dayid = (b.time + pd.Timedelta(hours=2)).dt.floor("1D")
    g = b.groupby(dayid.values)
    dhi = g.high.cummax().values                                      # day high SO FAR (includes bar i)
    dlo = g.low.cummin().values
    dr = (g.high.max() - g.low.min())
    adr = dr.rolling(20).mean().shift(1)                              # completed days only
    pdh, pdl = g.high.max().shift(1), g.low.min().shift(1)
    pcl = g.close.last().shift(1)
    k = dayid.values
    adr_v, pdh_v, pdl_v = adr.reindex(k).values, pdh.reindex(k).values, pdl.reindex(k).values
    pcl_v, ppcl_v = pcl.reindex(k).values, pcl.shift(1).reindex(k).values
    spent = (dhi - dlo) / adr_v
    prev_big = np.abs(pcl_v - ppcl_v) / adr_v
    dpos = np.where(pdh_v > pdl_v, (cl - pdl_v) / (pdh_v - pdl_v), np.nan)

    # ---------------- C1/C2 room to run: nearest prior extreme ahead, in ATR
    hi2, lo2 = b.high.shift(1).rolling(192).max().values, b.low.shift(1).rolling(192).min().values
    hi5, lo5 = b.high.shift(1).rolling(480).max().values, b.low.shift(1).rolling(480).min().values
    BIG = 99.0

    def ahead(levels, sgn):
        """Distance in ATR to the NEAREST of `levels` lying ahead of the close; BIG if none."""
        out = np.full(n, BIG)
        for lv in levels:
            dist = (lv - cl) * sgn / atf
            out = np.where((dist > 0) & (dist < out), dist, out)
        return out

    room_up = ahead([pdh_v, hi2, hi5], 1.0)
    room_dn = ahead([pdl_v, lo2, lo5], -1.0)

    # ---------------- D1/D2/D4/D5 the trigger candle
    rng = hi - lo
    body = np.abs(cl - op) / atf
    closepos = np.where(rng > 0, (cl - lo) / rng, np.nan)
    engulf = (hi > np.r_[np.nan, hi[:-1]]) & (lo < np.r_[np.nan, lo[:-1]])
    sgn_bar = np.sign(cl - op)
    run_grp = pd.Series(sgn_bar).ne(pd.Series(sgn_bar).shift(1)).cumsum()
    runlen = pd.Series(np.arange(n)).groupby(run_grp.values).cumcount().values + 1

    # ---------------- B time, E5 cost
    hr = b.time.dt.hour.values
    spr_bar = b.spr.values / P                                        # mean spread of the decision bar, in pips

    m = side != 0
    if "htf" in r:
        m &= (np.where(side > 0, h1_rel, -h1_rel) > 0)
    m &= ~np.isnan(atf) & ~np.isnan(adr_v) & ~np.isnan(eff) & (np.arange(n) > max(L, 480))

    s = side[m]
    f = pd.DataFrame(dict(
        sym=sym, t_signal=b.tclose.values[m], side=s,
        f_atrtf=atf[m],
        f_h1_agree=(np.where(s > 0, h1_rel[m], -h1_rel[m]) > 0).astype(float),
        f_h4_agree=(np.where(s > 0, h4_rel[m], -h4_rel[m]) > 0).astype(float),
        f_h1_age=h1_age[m],
        f_ext_h1=np.abs(cl[m] - h1_ema[m]) / atf[m],
        f_eff=eff[m],
        f_ema_sep=np.where(s > 0, ema_sep[m], -ema_sep[m]),
        f_atr_rank=atr_rank[m],
        f_atr_exp=atr_exp[m],
        f_day_spent=spent[m],
        f_prev_day_big=prev_big[m],
        f_dow=b.time.dt.dayofweek.values[m],
        f_hour=hr[m],
        f_room=np.where(s > 0, room_up[m], room_dn[m]),
        f_dpos=np.where(s > 0, dpos[m], 1.0 - dpos[m]),
        f_outside_pdr=np.where(s > 0, cl[m] > pdh_v[m], cl[m] < pdl_v[m]).astype(float),
        f_body=body[m],
        f_closepos=np.where(s > 0, closepos[m], 1.0 - closepos[m]),
        f_over=over[m],
        f_runlen=runlen[m],
        f_engulf=np.nan_to_num(engulf[m], nan=False).astype(float),
        f_spr_bar=spr_bar[m],
        f_year=b.time.dt.year.values[m],
    ))
    f["atr15"] = A.atr_at(sym, "M15", f.t_signal)                     # the shape unit, always M15 ATR
    f["f_comm_atr"] = C.COMM[sym] / (f.atr15 / P)                      # cost as a share of 1 ATR
    f = f[f.atr15 > 0]
    return f.dropna(subset=["atr15"]).reset_index(drop=True)


def run_rule(sym, stage, rule, tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD, fill_bias=False):
    """Simulate one pair. `fill_bias=True` deliberately fills at the DECISION bar's own open --
    the look-ahead that gave ORB a fake +21 points. Used only to measure the bias (F1)."""
    f = A.clip(features(sym, rule), stage)
    if len(f) == 0:
        return pd.DataFrame()
    b = C.bars(sym, r_tf(rule))
    i = C.asof_index(b, f.t_signal.values)                             # the decision bar itself
    ref = b.close.values[np.clip(i, 0, len(b) - 1)]
    o = f.drop(columns=["sym"]).copy()
    o["entry_type"] = "market"
    o["entry"] = np.nan
    o["sl"] = ref - o.side * o.atr15 * stop
    o["tp"] = ref + o.side * o.atr15 * tgt
    o["be_at_r"] = np.nan
    o["max_hold_min"] = hold
    o["expire"] = o.t_signal
    o["ref_px"] = ref
    if fill_bias:
        o["t_signal"] = b.time.values[np.clip(i, 0, len(b) - 1)]       # the banned version
    return C.simulate(sym, o)


def r_tf(rule):
    return RULES[rule]["tf"]


def pooled(trades):
    t = trades[trades.status == "filled"]
    return A.clip(t, t.stage.iloc[0]) if "stage" in t else t


def st(t):
    """stats() plus the profit factor in dollars, which is what the owner asked for."""
    t = t[t.status == "filled"] if "status" in t else t
    if len(t) == 0:
        return dict(trades=0)
    d = C.stats(t)
    u = t.usd_01.values
    gp, gl = u[u > 0].sum(), -u[u < 0].sum()
    d["PF_usd"] = round(gp / gl, 3) if gl > 0 else np.inf
    d["usd_per_trade"] = round(u.mean(), 3)
    d["right_in_100"] = round(100 * (u > 0).mean(), 2)
    return d


# ===================================================================== the shape ladder
def ladder_paths(sym, f, window=LADDER_WINDOW):
    """One pass over the M1 path per signal. Returns, for every ATR multiple in TGTS and STOPS,
    the minute at which that level was first touched, plus what the trade was worth on a time
    exit at each horizon in HOLDS. Mirrors simulate(): SL wins ties, TP never counts on the
    fill bar, stops slip, flat before the weekend and before any gap over 6 hours.
    """
    m1 = C.bars(sym, "M1")
    t1 = m1.time.values
    o, h, l, c, s = m1.open.values, m1.high.values, m1.low.values, m1.close.values, m1.spr.values
    dow, hm = m1.time.dt.dayofweek.values, (m1.time.dt.hour * 60 + m1.time.dt.minute).values
    weekend = (dow == 4) & (hm >= 20 * 60 + 45)
    gap = np.zeros(len(t1), bool)
    gap[:-1] = np.diff(t1) > np.timedelta64(6, "h")
    cut = np.flatnonzero(weekend | gap)
    P, comm, slip = C.pip(sym), C.COMM[sym], C.STOP_SLIP * C.pip(sym)

    ts = np.asarray(pd.to_datetime(f.t_signal).values, dtype="datetime64[ns]")
    a = np.searchsorted(t1, ts, "left")
    keep = (a < len(t1) - 2)
    rows, nt, ns = [], len(TGTS), len(STOPS)
    for q in np.flatnonzero(keep):
        fi = int(a[q])
        d = int(f.side.values[q])
        at = float(f.atr15.values[q])
        ref = float(f.ref_px.values[q])
        fill = o[fi] + s[fi] if d == 1 else o[fi]
        hard = min(fi + window, len(t1))
        ci = cut[np.searchsorted(cut, fi):]
        if len(ci):
            hard = min(hard, int(ci[0]) + 1)
        if hard - fi < 2:
            continue
        # ---- target: the fill bar never counts (simulate rule)
        if d == 1:
            tpath = np.maximum.accumulate(h[fi + 1:hard])
            spath = np.minimum.accumulate(l[fi:hard])
        else:
            tpath = np.minimum.accumulate((l + s)[fi + 1:hard])
            spath = np.maximum.accumulate((h + s)[fi:hard])
        tp_lv = ref + d * at * np.array(TGTS)
        sl_lv = ref - d * at * np.array(STOPS)
        if d == 1:
            tp_i = np.searchsorted(tpath, tp_lv, "left") + 1
            sl_i = np.searchsorted(-spath, -sl_lv, "left")
        else:
            tp_i = np.searchsorted(-tpath, -tp_lv, "left") + 1
            sl_i = np.searchsorted(spath, sl_lv, "left")
        span = hard - fi
        tp_i = np.where(tp_i >= span, 10 ** 9, tp_i)
        sl_i = np.where(sl_i >= span, 10 ** 9, sl_i)
        # ---- stop exit price, with simulate's gap-through rule
        sl_px = np.empty(ns)
        for j in range(ns):
            if sl_i[j] >= 10 ** 9:
                sl_px[j] = np.nan
                continue
            kk = fi + int(sl_i[j])
            if d == 1:
                sl_px[j] = (min(sl_lv[j], o[kk]) - slip) if kk > fi else (sl_lv[j] - slip)
            else:
                sl_px[j] = (max(sl_lv[j], o[kk] + s[kk]) + slip) if kk > fi else (sl_lv[j] + slip)
        row = dict(q=q, sym=sym, d=d, fill=fill, atr=at, span=span,
                   t_signal=f.t_signal.values[q], year=f.f_year.values[q])
        for j, tg in enumerate(TGTS):
            row["tpi%d" % j] = tp_i[j]
            row["tpx%d" % j] = ref + d * at * tg
        for j in range(ns):
            row["sli%d" % j] = sl_i[j]
            row["slx%d" % j] = sl_px[j]
        for H in HOLDS:                                                 # time-exit price per horizon
            kk = min(fi + H, hard) - 1
            row["tex%d" % H] = c[kk] if d == 1 else c[kk] + s[kk]
        rows.append(row)
    out = pd.DataFrame(rows)
    out.attrs.update(P=P, comm=comm)
    return out


def ladder_table(paths, hold=BASE_HOLD, symcol="sym", tie="SL"):
    """Profit factor, right-in-100, dollars per trade and the break-even bar for all 36 cells."""
    res = []
    P = np.array([C.pip(s) for s in paths[symcol].values])
    pv = np.array([C.USD_PIP_01[s] for s in paths[symcol].values])
    comm = np.array([C.COMM[s] for s in paths[symcol].values])
    d = paths.d.values
    fill = paths.fill.values
    span = paths.span.values
    hold_eff = np.minimum(hold, span)
    tex = paths["tex%d" % hold].values if ("tex%d" % hold) in paths else None
    for i, tg in enumerate(TGTS):
        tpi, tpx = paths["tpi%d" % i].values, paths["tpx%d" % i].values
        for j, sp in enumerate(STOPS):
            sli, slx = paths["sli%d" % j].values, paths["slx%d" % j].values
            if tie == "SL":                                 # simulate()'s own pessimism
                win = (tpi < sli) & (tpi < hold_eff)
                loss = (sli <= tpi) & (sli < hold_eff)
            else:                                           # optimistic bound, F5 only
                win = (tpi <= sli) & (tpi < hold_eff)
                loss = (sli < tpi) & (sli < hold_eff)
            tim = ~win & ~loss
            px = np.where(win, tpx, np.where(loss, slx, tex))
            net = (px - fill) * d / P - comm
            usd = net * pv
            bar = 100.0 * ((sp * paths.atr.values / P + C.STOP_SLIP) + comm) / \
                  (tg * paths.atr.values / P + sp * paths.atr.values / P + C.STOP_SLIP)
            gp, gl = usd[usd > 0].sum(), -usd[usd < 0].sum()
            res.append(dict(tgt=tg, stop=sp, trades=len(usd),
                            right_in_100=round(100 * (usd > 0).mean(), 2),
                            breakeven_bar=round(float(np.nanmean(bar)), 2),
                            PF=round(gp / gl, 3) if gl > 0 else np.inf,
                            usd_per_trade=round(float(usd.mean()), 3),
                            usd_total=round(float(usd.sum()), 0),
                            tp_share=round(100 * win.mean(), 1), sl_share=round(100 * loss.mean(), 1),
                            time_share=round(100 * tim.mean(), 1),
                            med_min_to_tp=float(np.median(tpi[win])) if win.any() else np.nan))
    return pd.DataFrame(res)


# ===================================================================== stages
def collect(stage, rule, tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD, pairs=None, **kw):
    pairs = pairs or C.PAIRS
    out = []
    for p in pairs:
        t = run_rule(p, stage, rule, tgt, stop, hold, **kw)
        if len(t):
            out.append(t)
    t = pd.concat(out, ignore_index=True, sort=False)
    t["stage"] = stage
    return t


def feats(stage, rule, pairs=None):
    pairs = pairs or C.PAIRS
    out = []
    for p in pairs:
        f = A.clip(features(p, rule), stage)
        if len(f):
            b = C.bars(p, r_tf(rule))
            i = C.asof_index(b, f.t_signal.values)
            f["ref_px"] = b.close.values[np.clip(i, 0, len(b) - 1)]
            out.append(f)
    return pd.concat(out, ignore_index=True, sort=False)


def say(*a):
    print(*a, flush=True)


def tab(df, path=None, n=200):
    if path:
        df.to_csv(os.path.join(OUT, path), index=False)
    say(df.head(n).to_string(index=False))
    return df


# ===================================================================== feature cache
FCACHE = os.environ.get("AUT10_CACHE") or os.path.join(
    os.environ.get("TEMP", "/tmp"), "aut10_fcache")


def feat_cached(sym, rule):
    os.makedirs(FCACHE, exist_ok=True)
    p = os.path.join(FCACHE, "%s_%s.parquet" % (sym, rule))
    if os.path.exists(p):
        return pd.read_parquet(p)
    f = features(sym, rule)
    b = C.bars(sym, r_tf(rule))
    i = C.asof_index(b, f.t_signal.values)
    f["ref_px"] = b.close.values[np.clip(i, 0, len(b) - 1)]
    f.to_parquet(p, index=False)
    return f


def sim_rule(sym, stage, rule, tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD,
             fill_bias=False, one_at_a_time=True, extra_mask=None):
    f = A.clip(feat_cached(sym, rule), stage)
    if extra_mask is not None:
        f = f[extra_mask(f)]
    if len(f) == 0:
        return pd.DataFrame()
    o = f.drop(columns=["sym"]).copy()
    o["entry_type"] = "market"
    o["entry"] = np.nan
    o["sl"] = o.ref_px - o.side * o.atr15 * stop
    o["tp"] = o.ref_px + o.side * o.atr15 * tgt
    o["be_at_r"] = np.nan
    o["max_hold_min"] = hold
    o["expire"] = o.t_signal
    if fill_bias:                                            # the BANNED fill, measured on purpose (F1)
        b = C.bars(sym, r_tf(rule))
        i = C.asof_index(b, f.t_signal.values)
        o["t_signal"] = b.time.values[np.clip(i, 0, len(b) - 1)]
    t = C.simulate(sym, o, one_at_a_time=one_at_a_time)
    t["stage"] = stage
    return t


def run_all(stage, rule, pairs=None, **kw):
    out = []
    for p in (pairs or C.PAIRS):
        t = sim_rule(p, stage, rule, **kw)
        if len(t):
            out.append(t)
    return pd.concat(out, ignore_index=True, sort=False) if out else pd.DataFrame()


# ===================================================================== STAGE 0 : catalogue group F
def stage0(pairs=("XAUUSD", "EURUSD", "GBPJPY", "USDJPY", "AUDUSD")):
    say("# STAGE 0 - catalogue group F (the boring killers), checked before anything else\n")
    lines = []

    # ---- F1 which bar the fill happened on
    say("## F1 fill bar")
    rows = []
    for p in pairs:
        t = sim_rule(p, "EXPLORE", "DONCH20")
        tb = sim_rule(p, "EXPLORE", "DONCH20", fill_bias=True)
        ft = t[t.status == "filled"]
        fb = tb[tb.status == "filled"]
        b = C.bars(p, "M15")
        # the honest fill must land on the first M1 bar at or after the decision bar's CLOSE
        same = (pd.to_datetime(ft.t_fill) == pd.to_datetime(ft.t_signal)).mean()
        rows.append(dict(sym=p, trades=len(ft), fill_at_signal_minute_pct=round(100 * same, 2),
                         honest_pips=round(ft.net_pips.mean(), 3), honest_usd=round(ft.usd_01.mean(), 3),
                         honest_right=round(100 * (ft.usd_01 > 0).mean(), 2),
                         biased_pips=round(fb.net_pips.mean(), 3), biased_usd=round(fb.usd_01.mean(), 3),
                         biased_right=round(100 * (fb.usd_01 > 0).mean(), 2),
                         bias_usd=round(fb.usd_01.mean() - ft.usd_01.mean(), 3),
                         bias_right=round(100 * ((fb.usd_01 > 0).mean() - (ft.usd_01 > 0).mean()), 2)))
        del b
    f1 = tab(pd.DataFrame(rows), "stage0_F1_fillbar.csv")
    lines.append(f1)

    # ---- F3 gaps and holes around the signal
    say("\n## F3 gaps / missing bars")
    rows = []
    for p in pairs:
        f = A.clip(feat_cached(p, "DONCH20"), "EXPLORE")
        m1 = C.bars(p, "M1")
        a = np.searchsorted(m1.time.values, np.asarray(pd.to_datetime(f.t_signal).values, "datetime64[ns]"), "left")
        a = np.clip(a, 0, len(m1) - 1)
        lagmin = (m1.time.values[a] - np.asarray(pd.to_datetime(f.t_signal).values, "datetime64[ns]")) \
            / np.timedelta64(1, "m")
        rows.append(dict(sym=p, signals=len(f), fill_bar_is_the_next_minute_pct=round(100 * (lagmin == 0).mean(), 2),
                         lag_gt_5min_pct=round(100 * (lagmin > 5).mean(), 2),
                         lag_gt_60min_pct=round(100 * (lagmin > 60).mean(), 2),
                         max_lag_min=round(float(np.nanmax(lagmin)), 1)))
    f3 = tab(pd.DataFrame(rows), "stage0_F3_gaps.csv")
    lines.append(f3)

    # ---- F4 stale higher-timeframe values
    say("\n## F4 asof_index on the H1 value (hand-recomputed on a sample)")
    rows = []
    for p in pairs[:3]:
        f = A.clip(feat_cached(p, "DONCH20"), "EXPLORE").head(400)
        h1 = C.bars(p, "H1")
        e = C.ema(h1.close, 50)
        bad = 0
        for q in range(len(f)):
            ts = pd.Timestamp(f.t_signal.iloc[q])
            j = int(np.searchsorted(h1.tclose.values, np.datetime64(ts), "right")) - 1
            if j < 0:
                continue
            if h1.tclose.iloc[j] > ts:                               # would be a peek
                bad += 1
            want = np.sign(h1.close.values[j] - e[j]) * f.side.iloc[q] > 0
            if bool(want) != bool(f.f_h1_agree.iloc[q]):
                bad += 1
        rows.append(dict(sym=p, checked=len(f), mismatches_or_peeks=bad))
    f4 = tab(pd.DataFrame(rows), "stage0_F4_asof.csv")
    lines.append(f4)

    # ---- F5 same-minute target and stop
    say("\n## F5 same-minute target AND stop (who was first)")
    rows = []
    for p in pairs:
        f = A.clip(feat_cached(p, "DONCH20"), "EXPLORE")
        pt = ladder_paths(p, f)
        i, j = TGTS.index(BASE_TGT), STOPS.index(BASE_STOP)
        tpi, sli = pt["tpi%d" % i].values, pt["sli%d" % j].values
        hold = np.minimum(BASE_HOLD, pt.span.values)
        amb = (tpi == sli) & (tpi < hold)
        res = ladder_table(pt, BASE_HOLD)
        opt = ladder_table(pt, BASE_HOLD, tie="TP")
        cell = res[(res.tgt == BASE_TGT) & (res.stop == BASE_STOP)].iloc[0]
        cello = opt[(opt.tgt == BASE_TGT) & (opt.stop == BASE_STOP)].iloc[0]
        rows.append(dict(sym=p, trades=len(pt), same_minute_pct=round(100 * amb.mean(), 2),
                         pessimistic_right=cell.right_in_100, pessimistic_usd=cell.usd_per_trade,
                         optimistic_right=cello.right_in_100, optimistic_usd=cello.usd_per_trade,
                         sl_on_fill_minute_pct=round(100 * ((sli == 0) & (sli < hold)).mean(), 2)))
    f5 = tab(pd.DataFrame(rows), "stage0_F5_sameminute.csv")
    lines.append(f5)
    return lines


# ===================================================================== reporting helpers
def save(bt, name, trades):
    """common.save, which strips any TEST row and writes results/<bt>/trades_<name>.csv."""
    if len(trades) == 0:
        return None
    return C.save(bt, name, trades)


def maxdd_usd(t):
    if len(t) == 0:
        return 0.0
    t = t.sort_values("t_exit", kind="stable")
    eq = np.cumsum(t.usd_01.values)
    return round(float((eq - np.maximum.accumulate(np.maximum(eq, 0))).min()), 0)


def pf(t, col="usd_01"):
    if len(t) == 0:
        return np.nan
    g = t[col].values
    gp, gl = g[g > 0].sum(), -g[g < 0].sum()
    return round(gp / gl, 3) if gl > 0 else np.inf


def line(t, label=""):
    if "status" in t.columns:
        t = t[t.status == "filled"]
    if len(t) == 0:
        return dict(label=label, trades=0)
    s = C.stats(t)
    return dict(label=label, trades=len(t), per_week=s["per_week"], right_in_100=s["win_pct"],
                PF_usd=pf(t, "usd_01"), avg_net_pips=s["avg_net_pips"],
                usd_per_trade=round(float(t.usd_01.mean()), 3), usd_total=round(float(t.usd_01.sum()), 0),
                avg_hold_h=s["avg_hold_h"], maxDD_usd=maxdd_usd(t))


def md(df, path=None):
    d = df.copy()
    for c0 in d.columns:
        d[c0] = d[c0].map(lambda v: f"{v:.3f}" if isinstance(v, float) and np.isfinite(v) else str(v))
    hdr = "| " + " | ".join(d.columns) + " |"
    sep = "|" + "|".join(["---"] * len(d.columns)) + "|"
    rows = ["| " + " | ".join(r) + " |" for r in d.values]
    s = "\n".join([hdr, sep] + rows)
    if path:
        open(path, "a", encoding="utf-8").write(s + "\n\n")
    return s


# ===================================================================== catalogue conditions
CONDS = [
    ("A1", "H1 structure (EMA50 side) agrees with the trade", lambda t: t.f_h1_agree == 1),
    ("A1", "H4 structure (EMA50 side) agrees with the trade", lambda t: t.f_h4_agree == 1),
    ("A2", "fresh H1 bias: flipped within the last 5 decision bars", lambda t: t.f_h1_age <= 5),
    ("A2", "stale H1 bias: 20+ bars since it last flipped", lambda t: t.f_h1_age >= 20),
    ("A3", "efficiency ratio of the last 20 bars >= 0.35 (trending, not chopping)",
     lambda t: t.f_eff >= 0.35),
    ("A3", "9/21 EMA already separated 0.10+ ATR with the trade", lambda t: t.f_ema_sep >= 0.10),
    ("A4", "ATR in the upper half of its own trailing year", lambda t: t.f_atr_rank >= 0.5),
    ("A5", "ATR expanding vs 20 bars ago", lambda t: t.f_atr_exp >= 1.0),
    ("A6", "under 60% of the day's typical range spent at entry", lambda t: t.f_day_spent <= 0.6),
    ("A6", "80%+ of the day's typical range already spent", lambda t: t.f_day_spent >= 0.8),
    ("A7", "not stretched: within 1 ATR of the H1 EMA50", lambda t: t.f_ext_h1 <= 1.0),
    ("A7", "stretched: 2+ ATR from the H1 EMA50", lambda t: t.f_ext_h1 >= 2.0),
    ("A8", "Tue/Wed/Thu only", lambda t: t.f_dow.isin([1, 2, 3])),
    ("A8", "day after an unusually large daily move (0.8+ ADR)", lambda t: t.f_prev_day_big >= 0.8),
    ("B1", "London 07-12 UTC", lambda t: (t.f_hour >= 7) & (t.f_hour < 12)),
    ("B1", "New York 16-21 UTC", lambda t: (t.f_hour >= 16) & (t.f_hour < 21)),
    ("B1", "Asia 00-07 UTC", lambda t: t.f_hour < 7),
    ("B2", "London/NY overlap 12-16 UTC", lambda t: (t.f_hour >= 12) & (t.f_hour < 16)),
    ("B4", "AUT09's hard-blocked hours excluded (19-23, 02-04 UTC)",
     lambda t: ~t.f_hour.isin(A.BAD_HOURS_UTC)),
    ("E5", "round-trip cost <= 20% of the ATR risked (AUT09 cost share)",
     lambda t: t.f_comm_atr <= A.COST_SHARE_SKIP),
    ("C1", "room to run: 1.5+ ATR of clear air ahead before the next opposing level",
     lambda t: t.f_room >= 1.5),
    ("C1", "no room: under 0.5 ATR to the next opposing level", lambda t: t.f_room < 0.5),
    ("C5", "breakout closes outside the previous day's range (genuine extension)",
     lambda t: t.f_outside_pdr == 1),
    ("C5", "entry still inside the previous day's range", lambda t: (t.f_dpos >= 0) & (t.f_dpos <= 1)),
    ("D1", "trigger candle body >= 0.6 ATR", lambda t: t.f_body >= 0.6),
    ("D2", "trigger closed in the top 30% of its own range", lambda t: t.f_closepos >= 0.7),
    ("D4", "run of 3+ same-direction bars going into the trigger", lambda t: t.f_runlen >= 3),
    ("D5", "trigger candle engulfed the one before it", lambda t: t.f_engulf == 1),
    ("D-over", "closed 0.3+ ATR beyond the breakout level (strong break)", lambda t: t.f_over >= 0.3),
    ("D-over", "barely broke: under 0.1 ATR beyond the level", lambda t: t.f_over < 0.1),
]


def score_conditions(base, T):
    rows = []
    for cid, note, fn in CONDS:
        cond = np.asarray(fn(base), bool)
        tab = A.score(base, cond)
        T.add(cid, note, tab)
        rows.append(dict(cond=cid, note=note, trades_kept=int(tab.trades[1]),
                         right_in_100=tab.right_in_100[1],
                         lift=round(tab.right_in_100[1] - tab.right_in_100[0], 2),
                         usd_when_true=tab.net_usd_per_trade[1], usd_when_false=tab.net_usd_per_trade[2],
                         PF_usd_true=pf(base[cond]) if cond.sum() else np.nan,
                         PF_usd_false=pf(base[~cond]) if (~cond).sum() else np.nan))
        say(f"  {cid:8s} {note[:58]:58s} n={rows[-1]['trades_kept']:6d} "
            f"{rows[-1]['right_in_100']:6.2f} ({rows[-1]['lift']:+.2f}) "
            f"${rows[-1]['usd_when_true']:+.3f} vs ${rows[-1]['usd_when_false']:+.3f} "
            f"PF$ {rows[-1]['PF_usd_true']}")
    return rows


# ===================================================================== controls (mandatory, all 4)
def sim_rule_side(sym, stage, rule, tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD, side_fn=None,
                  one_at_a_time=True):
    """Like sim_rule but lets a control override `side` -- always-long, always-short, coin-flip,
    or the FADE diagnostic (opposite of the real signal). sl/tp are rebuilt from the new side."""
    f = A.clip(feat_cached(sym, rule), stage)
    if len(f) == 0:
        return pd.DataFrame()
    f = f.copy()
    if side_fn is not None:
        f["side"] = side_fn(f)
    o = f.drop(columns=["sym"]).copy()
    o["entry_type"] = "market"
    o["entry"] = np.nan
    o["sl"] = o.ref_px - o.side * o.atr15 * stop
    o["tp"] = o.ref_px + o.side * o.atr15 * tgt
    o["be_at_r"] = np.nan
    o["max_hold_min"] = hold
    o["expire"] = o.t_signal
    t = C.simulate(sym, o, one_at_a_time=one_at_a_time)
    t["stage"] = stage
    return t


def run_all_side(stage, rule, side_fn, pairs=None, **kw):
    out = []
    for p in (pairs or C.PAIRS):
        t = sim_rule_side(p, stage, rule, side_fn=side_fn, **kw)
        if len(t):
            out.append(t)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def controls(base, stage, rule, pairs=None, seed=0):
    """The four mandatory controls (AUTOPSY_BRIEF.md): always-long / always-short / coin-flip at the
    rule's own minutes (tells skill from a one-directional sample), plus the FADE diagnostic the
    brief all but predicts (following loses, fading is the only thing that beats a coin flip)."""
    rows = [line(base, "REAL momentum signal (follow the breakout)")]
    al = run_all_side(stage, rule, lambda f: np.ones(len(f)), pairs=pairs)
    rows.append(line(al, "control: ALWAYS LONG at the same signal minutes"))
    asx = run_all_side(stage, rule, lambda f: -np.ones(len(f)), pairs=pairs)
    rows.append(line(asx, "control: ALWAYS SHORT at the same signal minutes"))
    cf = run_all_side(stage, rule, lambda f, s=seed: np.random.default_rng(s).choice([1, -1], size=len(f)),
                      pairs=pairs)
    rows.append(line(cf, "control: COIN FLIP direction at the same signal minutes"))
    fd = run_all_side(stage, rule, lambda f: -f.side.values, pairs=pairs)
    rows.append(line(fd, "diagnostic: FADE the same breakout (take the opposite side)"))
    detail = dict(always_long=line(al, "always_long"), always_short=line(asx, "always_short"),
                 coin_flip=line(cf, "coin_flip"), fade=line(fd, "fade"))
    return pd.DataFrame(rows), detail


def luck(base, stage, cap=4000, seeds=(0, 1, 2)):
    """Luck bar (G1): shape-, hour- and hold-matched random entries, same pairs, >=20,000 pooled
    across seeds. Report beside it, never against 50."""
    hrs = sorted(pd.to_datetime(base.t_signal).dt.hour.unique().tolist())
    rows = []
    for seed in seeds:
        out = []
        for sym, g in base.groupby("sym"):
            n = int(min(len(g), cap))
            if n < 20:
                continue
            try:
                r = A.luck_bar(sym, n, BASE_TGT, BASE_STOP, stage, seed=seed, hours=hrs,
                               max_hold_min=BASE_HOLD)
            except Exception as e:
                say(f"   luck_bar skip {sym}: {e}")
                continue
            out.append(r[r.status == "filled"])
        L = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
        rows.append(line(L, f"luck bar, random entries, same pairs/hours/hold (seed {seed})"))
    return pd.DataFrame(rows)


LADDER2 = [(1.0, 1.0), (1.5, 1.0), (2.0, 1.0), (1.0, 2.0), (1.5, 2.0), (2.0, 2.0),
           (1.0, 3.0), (2.0, 3.0), (1.0, 4.0), (1.5, 4.0), (2.0, 4.0), (0.5, 2.0),
           (3.0, 1.0), (3.0, 2.0), (4.0, 2.0), (3.0, 3.0)]


def shape_ladder(stage, rule, pairs=None, hold=BASE_HOLD):
    rows = []
    for tgt, stop in LADDER2:
        t = run_all(stage, rule, pairs=pairs, tgt=tgt, stop=stop, hold=hold)
        t = t[t.status == "filled"] if len(t) else t
        be = float(np.nanmean([A.breakeven_rate(s, float(g.atr15.median()), tgt, stop)
                               for s, g in t.groupby("sym")])) if len(t) else np.nan
        r = line(t, f"{tgt}xATR tgt / {stop}xATR stop")
        r["breakeven_needed"] = round(be, 2)
        r["beat_breakeven_by"] = round(r.get("right_in_100", np.nan) - be, 2) if r.get("trades", 0) else np.nan
        rows.append(r)
        say(f"  tgt {tgt} stop {stop}: {r.get('trades')} tr  {r.get('right_in_100')} right/100  "
            f"needs {be:.1f}  PF$ {r.get('PF_usd')}  ${r.get('usd_per_trade')}/trade")
    return pd.DataFrame(rows)


# ===================================================================== STAGE 1 - EXPLORE
def explore():
    T = A.Tally("AUT10")
    say("=" * 100); say("STAGE 1 - EXPLORE 2005-01-01 -> 2011-01-01"); say("=" * 100)
    out = {}

    # ---------------- 1. which rule skeleton even gets close
    say("\n1. BASELINE RULE GRID (1.5xATR target / 1.0xATR stop, 24h hold, all pairs)")
    rows = []
    for name in RULES:
        t = run_all("EXPLORE", name, tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD)
        rows.append(line(t, name))
        say(f"  {name}: {rows[-1]}")
    out["rule_grid"] = pd.DataFrame(rows)
    say(md(out["rule_grid"]))
    ranked = out["rule_grid"][out["rule_grid"].trades > 200].sort_values("usd_per_trade", ascending=False)
    RULE = ranked.iloc[0]["label"] if len(ranked) else "DONCH20"
    say(f"\nChosen rule skeleton: {RULE}")

    base = run_all("EXPLORE", RULE, tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD)
    base = base[base.status == "filled"].reset_index(drop=True)
    save("AUT10_momentum", f"EXPLORE_base_{RULE}", base)
    say(f"baseline: {line(base, 'baseline')}")

    # ---------------- 2. THE SHAPE QUESTION (E6)
    say("\n2. THE SHAPE QUESTION - ATR ladder, 16 shapes, EXPLORE, all pairs")
    out["ladder"] = shape_ladder("EXPLORE", RULE)
    say(md(out["ladder"]))
    T.add("E6", "ATR target/stop ladder, 16 shapes", A.score(base, np.ones(len(base), bool)))

    # ---------------- 3. TIME LIMIT (E3)
    say("\n3. TIME LIMIT sweep (E3)")
    rows = []
    for h in HOLDS:
        t = run_all("EXPLORE", RULE, tgt=BASE_TGT, stop=BASE_STOP, hold=h)
        rows.append(line(t, f"hold limit {h} min"))
        say(f"  {rows[-1]}")
    out["time"] = pd.DataFrame(rows)
    say(md(out["time"]))
    T.add("E3", "time limit sweep 60..2880 min", A.score(base, np.ones(len(base), bool)))

    # ---------------- 4. THE FILTER QUESTION - every catalogue condition
    say("\n4. THE FILTER QUESTION - each condition scored on the baseline trades")
    rows = score_conditions(base, T)
    out["tally"] = pd.DataFrame(rows)

    # ---------------- 5. mandatory controls
    say("\n5. CONTROLS - always-long / always-short / coin-flip / fade")
    ctl_df, ctl_detail = controls(base, "EXPLORE", RULE)
    out["controls"] = ctl_df
    say(md(ctl_df))

    # ---------------- 6. buy/sell split
    say("\n6. BUY / SELL split")
    out["buysell"] = pd.DataFrame([line(base[base.side > 0], "BUY"), line(base[base.side < 0], "SELL")])
    say(md(out["buysell"]))

    # ---------------- 7. luck bar
    say("\n7. LUCK BAR (G1) - matched pairs, hours, hold")
    out["luck"] = luck(base, "EXPLORE")
    say(md(out["luck"]))

    # ---------------- 8. per pair
    say("\n8. PER PAIR (baseline shape)")
    out["perpair"] = pd.DataFrame([line(g, s) for s, g in base.groupby("sym")]) \
        .sort_values("usd_per_trade", ascending=False)
    say(md(out["perpair"]))

    out["tallyframe"] = T.frame()
    say(f"\nconditions tried: {len(T.frame())}")
    for k, v in out.items():
        if isinstance(v, pd.DataFrame):
            v.to_csv(os.path.join(OUT, f"explore_{k}.csv"), index=False)
    with open(os.path.join(OUT, "chosen_rule.txt"), "w") as fh:
        fh.write(RULE)
    out["_rule"] = RULE
    out["_controls_detail"] = ctl_detail
    return out


def chosen_rule():
    """The rule skeleton explore() picked, persisted to disk so a separate `python ... validate`
    invocation (a fresh process) can find it without re-running explore()."""
    p = os.path.join(OUT, "chosen_rule.txt")
    if os.path.exists(p):
        return open(p).read().strip()
    return "DONCH20"


# ===================================================================== THE FROZEN REFINEMENTS
# Chosen on EXPLORE 2005-01-01 -> 2011-01-01 ONLY (see explore_*.csv / the EXPLORE log), and never
# changed after this point. Each is one mechanical sentence. The literal momentum-continuation
# strategy (follow the breakout) lost money on EVERY one of 25 pairs, at all 16 ATR shapes, at all
# 5 time limits, and read BELOW its own shape-and-hour-matched luck bar on all 3 seeds
# (36.2 right-in-100 / -$2.096 a trade vs a luck bar of 38.9-39.1 / -$1.14 to -$1.41). Every
# always-long / always-short / coin-flip control beat the real signal. The one thing that came
# close to breakeven was taking the OPPOSITE side of the same breakout (-$0.502 a trade, 41.9 right
# in 100, PF 0.931) -- consistent with four prior strategies in this project (BT17, BT21, AUT03,
# AUT08) finding "following loses, fading is the only thing above a coin flip". The three
# refinements below test whether that holds up, whether the best shape adds anything on top of it,
# and whether the literal strategy (still following) has anything left once given its best shape.
#
# R1  FADE the breakout (take the opposite side of the signal) and widen the shape to the best one
#     found on the ladder, 1.0xATR target / 4.0xATR stop. Reason: fading was the single largest
#     effect measured (+1.594 a trade over following) and 1.0/4.0 was the least-bad shape on the
#     follow side (-$1.035 vs baseline's -$2.096); this tests whether the two stack.
# R2  FADE the breakout, restricted to the London/New York overlap (12:00-16:00 UTC), at the base
#     1.5xATR/1.0xATR shape. Reason: B2 (the overlap) was the best clock condition found on the
#     follow side (+2.49 points) and is probably mostly AUT09's cost-share effect; this tests
#     whether it adds anything on top of fading rather than just being a different way to say "low
#     cost share."
# R3  Keep FOLLOWING the breakout exactly as specified (no fade), but use the best shape found,
#     1.0xATR target / 4.0xATR stop. Reason: this is the honest control for the literal strategy --
#     if momentum continuation has anything at all, shape is where it would show (shape beats every
#     filter 3-6x per this project's settled finding), and it must be reported even though R1
#     is expected to beat it.
FADE = lambda f: -f.side.values                                           # noqa: E731
REFINEMENTS = [
    ("R1_fade_bestshape", dict(shape=dict(tgt=1.0, stop=4.0, hold=BASE_HOLD), side_fn=FADE)),
    ("R2_fade_overlap", dict(shape=dict(tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD), side_fn=FADE,
                             mask=lambda t: (t.f_hour >= 12) & (t.f_hour < 16))),
    ("R3_follow_bestshape", dict(shape=dict(tgt=1.0, stop=4.0, hold=BASE_HOLD), side_fn=None)),
]


def run_refinement(stage, rule, spec, pairs=None):
    shape, side_fn, mask = spec["shape"], spec.get("side_fn"), spec.get("mask")
    if side_fn is not None:
        t = run_all_side(stage, rule, side_fn, pairs=pairs, **shape)
    else:
        t = run_all(stage, rule, pairs=pairs, **shape)
    t = t[t.status == "filled"] if len(t) else t
    if mask is not None and len(t):
        t = t[np.asarray(mask(t), bool)]
    return t


def refine(rule=None):
    """Re-simulate the frozen REFINEMENTS on EXPLORE only, to confirm each one still reads as it did
    when chosen (sanity check, not a re-tune)."""
    rule = rule or chosen_rule()
    rows = []
    for name, spec in REFINEMENTS:
        t = run_refinement("EXPLORE", rule, spec)
        rows.append(line(t, name))
        save("AUT10_momentum", f"EXPLORE_{name}", t)
        say(f"  {name}: {rows[-1]}")
    d = pd.DataFrame(rows)
    say(md(d))
    d.to_csv(os.path.join(OUT, "refine_explore.csv"), index=False)
    return d


def validate(rule=None):
    """STAGE 2 - VALIDATE 2011-2017, untouched during EXPLORE. A refinement that does not beat its
    own luck bar / break-even here is discarded and recorded as discarded, never re-tuned."""
    rule = rule or chosen_rule()
    say("=" * 100); say("STAGE 2 - VALIDATE 2011-01-01 -> 2017-01-01"); say("=" * 100)
    rows = []
    base = run_all("VALIDATE", rule, tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD)
    base = base[base.status == "filled"].reset_index(drop=True)
    save("AUT10_momentum", "VALIDATE_base", base)
    rows.append(line(base, "VALIDATE baseline unfiltered"))
    say(f"  {rows[-1]}")
    for name, spec in REFINEMENTS:
        t = run_refinement("VALIDATE", rule, spec)
        save("AUT10_momentum", f"VALIDATE_{name}", t)
        rows.append(line(t, f"VALIDATE {name}"))
        say(f"  {rows[-1]}")
    ctl_df, ctl_detail = controls(base, "VALIDATE", rule)
    say(md(ctl_df))
    ctl_df.to_csv(os.path.join(OUT, "validate_controls.csv"), index=False)
    d = pd.DataFrame(rows)
    say(md(d))
    d.to_csv(os.path.join(OUT, "validate.csv"), index=False)
    return d, ctl_df


def stress(rule=None):
    """STAGE 3 - STRESS 2017-2021 + adverse costs. Feed swap is impossible (AUTOPSY_BRIEF.md
    correction #4 -- duka and Exness Zero only overlap inside the sealed EXAM); an adverse-cost run
    (2x spread, +1 extra pip of stop slippage) substitutes for it."""
    rule = rule or chosen_rule()
    say("=" * 100); say("STAGE 3 - STRESS 2017-01-01 -> 2021-01-01 (+ adverse costs)"); say("=" * 100)
    rows = []
    base = run_all("STRESS", rule, tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD)
    base = base[base.status == "filled"].reset_index(drop=True)
    save("AUT10_momentum", "STRESS_base", base)
    rows.append(line(base, "STRESS baseline unfiltered"))
    say(f"  {rows[-1]}")
    for name, spec in REFINEMENTS:
        t = run_refinement("STRESS", rule, spec)
        save("AUT10_momentum", f"STRESS_{name}", t)
        rows.append(line(t, f"STRESS {name}"))
        say(f"  {rows[-1]}")
    d = pd.DataFrame(rows)
    say(md(d))
    d.to_csv(os.path.join(OUT, "stress.csv"), index=False)

    # ---- adverse costs: 2x spread, +1 pip stop slip, applied via a patched C.bars on a COPY
    say("\nADVERSE COSTS: 2x spread + 1 extra pip of stop slippage")
    rows2 = []
    saved_slip = C.STOP_SLIP
    orig_bars = C.bars
    override = {}

    def patched(sym, tf):
        if tf == "M1" and sym in override:
            return override[sym]
        return orig_bars(sym, tf)

    specs = [("BASE", dict(shape=dict(tgt=BASE_TGT, stop=BASE_STOP, hold=BASE_HOLD)))] + REFINEMENTS
    try:
        C.STOP_SLIP = saved_slip + 1.0
        C.bars = patched
        for name, spec in specs:
            shape, side_fn, mask = spec["shape"], spec.get("side_fn"), spec.get("mask")
            out = []
            for p in C.PAIRS:
                b2 = orig_bars(p, "M1").copy()
                b2["spr"] = b2.spr * 2.0
                override.clear(); override[p] = b2
                if side_fn is not None:
                    t = sim_rule_side(p, "STRESS", rule, side_fn=side_fn, **shape)
                else:
                    t = sim_rule(p, "STRESS", rule, **shape)
                if mask is not None and len(t):
                    t = t[t.status == "filled"]
                    t = t[np.asarray(mask(t), bool)]
                if len(t):
                    out.append(t)
            override.clear()
            t = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
            save("AUT10_momentum", f"STRESS_{name}_adverse", t)
            rows2.append(line(t, f"STRESS {name} (adverse costs)"))
            say(f"  {rows2[-1]}")
    finally:
        C.STOP_SLIP = saved_slip
        C.bars = orig_bars
        override.clear()
    d2 = pd.DataFrame(rows2)
    say(md(d2))
    d2.to_csv(os.path.join(OUT, "stress_adverse.csv"), index=False)
    return d, d2


def examples(csv="EXPLORE_base_DONCH20", n=12, seed=7):
    path = os.path.join(OUT, f"trades_{csv}.csv")
    t = pd.read_csv(path)
    t = t[t.status == "filled"] if "status" in t else t
    g = t.sample(min(n, len(t)), random_state=seed).sort_values("t_signal")
    cols = ["sym", "t_signal", "side", "f_over", "f_room", "f_h1_agree", "f_eff", "fill", "sl", "tp",
            "t_exit", "exit", "exit_reason", "net_pips", "R", "usd_01", "mfe_R", "mae_R"]
    g = g[[c for c in cols if c in g.columns]]
    say(md(g))
    g.to_csv(os.path.join(OUT, f"examples_{csv}.csv"), index=False)
    return g


# ===================================================================== dispatcher
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stage0"
    t0 = time.time()
    globals()[cmd](*sys.argv[2:])
    say("[%s done in %.1f min]" % (cmd, (time.time() - t0) / 60))
