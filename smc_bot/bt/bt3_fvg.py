"""BT3 - FAIR VALUE GAPS.  Does price react from FVGs in a tradable way after real costs?

Run from the TTXT root:
    .venv/Scripts/python.exe smc_bot/bt/bt3_fvg.py dev A            # phase A: timeframe sweep (DEV only)
    .venv/Scripts/python.exe smc_bot/bt/bt3_fvg.py dev B M15        # phase B: one-factor changes around reference TF
    .venv/Scripts/python.exe smc_bot/bt/bt3_fvg.py val V02 V10 V14  # confirm <=3 variants on DEV+VAL (uses variants.json)
    .venv/Scripts/python.exe smc_bot/bt/bt3_fvg.py tables V02 ...   # markdown tables (DEV/VAL, pooled + per pair)

FVG definition (smc-strategy.md): bullish = low[k] > high[k-2], zone = (high[k-2], low[k]); bearish mirror.
The FVG is known at the CLOSE of candle 3 (bar k).  Everything below is decided on closed bars only.

Order model (all fills/exits by common.simulate on M1, real spread + commission + 0.2 pip stop slip):
  * the limit order is (conceptually) resting from the close of candle 3 for `valid` bars of the FVG timeframe.
  * FIRST TOUCH ONLY: the first M1 bar (after candle 3 closed) whose ask/bid trades into the gap is the touch.  A resting
    limit at the near edge fills exactly there.  A limit at 50% (consequent encroachment) is only alive on that touch bar
    (if the first touch does not reach 50% the zone is mitigated and the order is dead).
  * t_signal of the emitted order = open time of the touch M1 bar (= close of the previous M1 bar); it lets the target
    (leg extreme / liquidity) be computed with data known at that moment, and keeps one-position-per-pair ordering causal.
Inversion (IFVG): if a bar of the FVG timeframe CLOSES fully through the gap (bull FVG: close < low edge), the gap flips and
  is traded as a zone of the opposite direction on its first retest.
"""
import sys, os, json, pickle
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *   # noqa: F401,F403
from common import bars, asof_index, simulate, stats, save, PAIRS, PERIODS, RESULTS, atr, structure, swings, pip, period_of

CUT = pd.Timestamp("2025-07-01")             # TEST starts here: nothing at/after this time is ever loaded into signals
DEV_END = pd.Timestamp("2024-01-01")
TFMIN = {"M5": 5, "M15": 15, "M30": 30, "H1": 60}
OUT = os.path.join(RESULTS, "BT3")
VALID_BARS = 48                              # zone validity window (strategy 6.2: POI valid ~48 candles)
HOLD_BARS = 60                               # time stop, in bars of the FVG timeframe

BASE = dict(tf="M15", gap=0.5, disp=True, trend="h1", entry="edge", sl="c1", tp="2R", inv=False, valid=VALID_BARS,
            hold=HOLD_BARS, minrr=None)


def build_variants(ref):
    B = dict(BASE, tf=ref)
    v = {}
    for i, tf in enumerate(["M5", "M15", "M30", "H1"], 1):
        v[f"V0{i}_{tf}"] = dict(BASE, tf=tf)
    v["V05_gap01"] = dict(B, gap=0.1)
    v["V06_gap10"] = dict(B, gap=1.0)
    v["V07_nodisp"] = dict(B, disp=False)
    v["V08_notrend"] = dict(B, trend="none")
    v["V09_h1h4"] = dict(B, trend="h1h4")
    v["V10_ce50"] = dict(B, entry="ce")
    v["V11_slgap"] = dict(B, sl="gap")
    v["V12_tpleg"] = dict(B, tp="leg", minrr=1.0)
    v["V13_tpliq"] = dict(B, tp="liq", minrr=2.0)
    v["V14_inv"] = dict(B, inv=True, disp=False, sl="gap")
    # supplementary (phase C): the H1 trend filter is vacuous on H1 (a displacement that breaks structure already makes the
    # H1 trend equal to the gap direction), so the trend question and the 50% entry are re-asked on M15.
    v["V15_M15_notrend"] = dict(BASE, tf="M15", trend="none")
    v["V16_M15_ce50"] = dict(BASE, tf="M15", entry="ce")
    return v


# ------------------------------------------------------------------ data helpers (nothing at/after CUT)
_C = {}


def tfb(sym, tf):
    k = ("b", sym, tf)
    if k not in _C:
        b = bars(sym, tf)
        _C[k] = b[b.tclose <= CUT].reset_index(drop=True)
    return _C[k]


def ctx(sym):
    k = ("ctx", sym)
    if k not in _C:
        h1, h4 = tfb(sym, "H1"), tfb(sym, "H4")
        st1, st4 = structure(h1, 5), structure(h4, 3)
        is_sh, is_sl = swings(h1.high.values, h1.low.values, 5)
        _C[k] = dict(h1=h1, h4=h4, tr1=st1["trend"], tr4=st4["trend"],
                     sh=np.where(is_sh)[0], sl=np.where(is_sl)[0])
    return _C[k]


def trend_at(sym, which, t):
    c = ctx(sym)
    b, tr = (c["h1"], c["tr1"]) if which == 1 else (c["h4"], c["tr4"])
    i = asof_index(b, t)
    return np.where(i >= 0, tr[np.maximum(i, 0)], 0)


# ------------------------------------------------------------------ zone table (regular FVGs + inversions), with first touch
def zone_table(sym, tf, until, valid=VALID_BARS):
    key = ("z", sym, tf, until, valid)
    if key in _C:
        return _C[key]
    b = tfb(sym, tf); n = len(b)
    o, h, l, c = (b[x].values for x in ("open", "high", "low", "close"))
    tc = b.tclose.values; tt = b.time.values
    N = 5 if tf == "H1" else 3
    st = structure(b, N); evt = st["evt"]
    a = atr(b)
    k = np.arange(3, n)
    ap = a[k - 3]                                       # ATR known BEFORE the 3 gap candles (not inflated by the impulse)
    bull = l[k] > h[k - 2]; bear = h[k] < l[k - 2]
    gap = np.where(bull, l[k] - h[k - 2], np.where(bear, l[k - 2] - h[k], 0.0))
    ok = (bull | bear) & np.isfinite(ap) & (gap >= 0.1 * ap) & (tc[k] < np.datetime64(until))
    k, bull, ap, gap = k[ok], bull[ok], ap[ok], gap[ok]
    d = np.where(bull, 1, -1)
    near = np.where(bull, l[k], h[k]); far = np.where(bull, h[k - 2], l[k - 2])
    origin = np.where(bull, np.minimum(l[k - 2], l[k - 1]), np.maximum(h[k - 2], h[k - 1]))
    disp = np.where(bull, (evt[k] > 0) | (evt[k - 1] > 0) | (evt[k - 2] > 0), (evt[k] < 0) | (evt[k - 1] < 0) | (evt[k - 2] < 0))
    t0 = tc[k]
    reg = pd.DataFrame(dict(kind="reg", d=d, near=near, far=far, origin=origin, atr=ap, gap_atr=gap / ap, disp=disp,
                            t_start=t0, t_fvg=t0, leg_t=tt[k - 2]))
    # inversion: first bar within `valid` bars whose CLOSE is fully through the gap (bull: close < lo, bear: close > hi)
    rows = []
    inv_t = np.full(len(reg), np.datetime64("NaT"), dtype="datetime64[ns]")
    for i in range(len(reg)):
        kk = k[i]; e = min(kk + 1 + valid, n)
        if e <= kk + 1:
            continue
        if d[i] == 1:
            x = c[kk + 1:e] < far[i]
        else:
            x = c[kk + 1:e] > far[i]
        if x.any():
            j = kk + 1 + int(np.argmax(x))
            rows.append((i, j))
            inv_t[i] = tc[j]
    reg["inv_t"] = inv_t
    if rows:
        ii = np.array([r[0] for r in rows]); jj = np.array([r[1] for r in rows])
        ok2 = tc[jj] < np.datetime64(until)
        ii, jj = ii[ok2], jj[ok2]
        inv = reg.iloc[ii].copy().reset_index(drop=True)
        inv["kind"] = "inv"; inv["d"] = -inv.d.values
        inv["near"], inv["far"] = reg.far.values[ii], reg.near.values[ii]      # flipped: the old far edge is now the near edge
        inv["origin"] = np.nan; inv["t_start"] = tc[jj]; inv["disp"] = True; inv["inv_t"] = np.datetime64("NaT")
        z = pd.concat([reg, inv], ignore_index=True)
    else:
        z = reg
    z["tr1"] = trend_at(sym, 1, z.t_start.values); z["tr4"] = trend_at(sym, 4, z.t_start.values)
    # first touch on M1 after t_start, within `valid` bars of the FVG timeframe
    m1 = bars(sym, "M1")
    t1 = m1.time.values; h1_, l1_, s1_ = m1.high.values, m1.low.values, m1.spr.values
    ia = np.searchsorted(t1, z.t_start.values, "left")
    ie = np.minimum(np.searchsorted(t1, z.t_start.values + np.timedelta64(valid * TFMIN[tf], "m"), "left"),
                    np.searchsorted(t1, np.datetime64(CUT), "left"))
    zd, zn = z.d.values, z.near.values
    touch = np.full(len(z), -1, np.int64)
    for i in range(len(z)):
        a_, e_ = ia[i], ie[i]
        if e_ <= a_:
            continue
        hit = ((l1_[a_:e_] + s1_[a_:e_]) <= zn[i]) if zd[i] == 1 else (h1_[a_:e_] >= zn[i])
        if hit.any():
            touch[i] = a_ + int(np.argmax(hit))
    z["touch"] = touch
    # consequent encroachment (50%): the resting order stays alive until the first FVG-timeframe CLOSE fully through the
    # gap (zone invalid) or the validity window ends; touch_ce = first M1 bar reaching the 50% level in that window.
    inv_ns = z.inv_t.values.astype("datetime64[ns]")
    ie_ce = np.where(np.isnat(inv_ns), ie, np.minimum(ie, np.searchsorted(t1, inv_ns, "left")))
    mid = 0.5 * (z.near.values + z.far.values)
    tce = np.full(len(z), -1, np.int64)
    for i in range(len(z)):
        a_, e_ = ia[i], ie_ce[i]
        if e_ <= a_ or z.kind.values[i] != "reg":
            continue
        hit = ((l1_[a_:e_] + s1_[a_:e_]) <= mid[i]) if zd[i] == 1 else (h1_[a_:e_] >= mid[i])
        if hit.any():
            tce[i] = a_ + int(np.argmax(hit))
    z["touch_ce"] = tce
    z = z[(z.touch >= 0) | (z.touch_ce >= 0)].reset_index(drop=True)
    z["leg_i"] = np.searchsorted(t1, z.leg_t.values, "left")
    z.attrs["t1"] = t1
    _C[key] = z
    return z


def liq_target(sym, d, entry, t, ai_cache=None):
    """Nearest UNBROKEN confirmed H1 swing high above entry (d=+1) / swing low below entry (d=-1), known at time t."""
    c = ctx(sym); h1 = c["h1"]; N = 5
    idx = c["sh"] if d == 1 else c["sl"]
    conf_t = h1.tclose.values[np.minimum(idx + N, len(h1) - 1)]
    p = np.searchsorted(conf_t, np.datetime64(t), "right")
    ai = int(asof_index(h1, t))
    best = np.nan
    lv = h1.high.values if d == 1 else h1.low.values
    for s in idx[max(0, p - 120):p][::-1]:
        L = lv[s]
        if (d == 1 and L <= entry) or (d == -1 and L >= entry):
            continue
        after = lv[s + 1:ai + 1]
        if len(after) and ((d == 1 and after.max() >= L) or (d == -1 and after.min() <= L)):
            continue                                     # already swept
        if np.isnan(best) or (d == 1 and L < best) or (d == -1 and L > best):
            best = L
    return best


def make_orders(sym, v, until):
    z = zone_table(sym, v["tf"], until, v["valid"])
    t1 = bars(sym, "M1").time.values
    z = z.assign(touch_used=z.touch_ce.values if v["entry"] == "ce" else z.touch.values)
    z["touch"] = z.touch_used
    m = (z.kind.values == ("inv" if v["inv"] else "reg")) & (z.touch.values >= 0)
    z["t_touch"] = t1[np.maximum(z.touch.values, 0)]
    m &= z.gap_atr.values >= v["gap"]
    if v["disp"] and not v["inv"]:
        m &= z.disp.values
    if v["trend"] in ("h1", "h1h4"):
        m &= z.tr1.values == z.d.values
    if v["trend"] == "h1h4":
        m &= z.tr4.values == z.d.values
    z = z[m].reset_index(drop=True)
    if len(z) == 0:
        return pd.DataFrame()
    d = z.d.values.astype(float)
    buf = 0.1 * z.atr.values
    entry = z.near.values if v["entry"] == "edge" else 0.5 * (z.near.values + z.far.values)
    if v["sl"] == "c1" and not v["inv"]:
        sl = z.origin.values - d * buf
        sl_reason = "beyond impulse origin (lowest low/highest high of candles 1-2) + 0.1 ATR"
    else:
        sl = z.far.values - d * buf
        sl_reason = "beyond far edge of gap (gap closed = zone invalid) + 0.1 ATR"
    risk = (entry - sl) * d
    if v["tp"] == "2R":
        tp = entry + d * 2.0 * risk; tp_reason = "fixed 2R"
    elif v["tp"] == "leg":
        m1 = bars(sym, "M1"); hh, ll = m1.high.values, m1.low.values
        tp = np.array([(hh[a:b].max() if dd == 1 else ll[a:b].min()) if b > a else np.nan
                       for a, b, dd in zip(z.leg_i.values, z.touch.values, d)])
        tp_reason = "extreme of the displacement leg (highest high/lowest low since candle 1, before the touch)"
    else:
        tp = np.array([liq_target(sym, int(dd), e, t) for dd, e, t in zip(d, entry, z.t_touch.values)])
        tp_reason = "nearest unbroken H1 swing high/low (liquidity)"
    if v["minrr"] is not None:
        rr = (tp - entry) * d / risk
        keep = np.isfinite(rr) & (rr >= v["minrr"])
    else:
        keep = np.ones(len(z), bool)
    keep &= risk > 0
    o = pd.DataFrame(dict(t_signal=z.t_touch, side=d.astype(int), entry_type="limit", entry=entry,
                          expire=z.t_touch + pd.Timedelta(minutes=1), sl=sl, tp=tp, be_at_r=np.nan,
                          max_hold_min=float(v["hold"] * TFMIN[v["tf"]]), sl_reason=sl_reason, tp_reason=tp_reason,
                          tf=v["tf"], kind=z.kind, t_fvg=z.t_fvg, t_created=z.t_start, gap_lo=np.minimum(z.near, z.far),
                          gap_hi=np.maximum(z.near, z.far), gap_atr=z.gap_atr.round(2), disp=z.disp, h1_trend=z.tr1,
                          h4_trend=z.tr4))
    o = o[keep]
    # several zones can be touched by the same M1 bar; simulate() sorts by t_signal with an unstable sort, so ties would make
    # results non-reproducible.  Keep one order per bar: the one with the larger gap (in ATR).  (bug found in the first run)
    o = o.sort_values(["t_signal", "gap_atr"], ascending=[True, False], kind="stable").drop_duplicates("t_signal")
    return o.reset_index(drop=True)


def run_sym(args):
    sym, variants, until = args
    out = {}
    for name, v in variants.items():
        o = make_orders(sym, v, until)
        if len(o) == 0:
            out[name] = pd.DataFrame(); continue
        t = simulate(sym, o)
        out[name] = t
    return sym, out


def run_variants(variants, until):
    from concurrent.futures import ProcessPoolExecutor
    res = {n: [] for n in variants}
    with ProcessPoolExecutor(max_workers=8) as ex:
        for sym, out in ex.map(run_sym, [(s, variants, until) for s in PAIRS]):
            for n, t in out.items():
                if len(t):
                    res[n].append(t)
    return {n: (pd.concat(x, ignore_index=True) if x else pd.DataFrame()) for n, x in res.items()}


# ------------------------------------------------------------------ reporting
def top_month_share(t):
    if len(t) == 0 or t.R.sum() <= 0:
        return np.nan
    m = t.groupby(t.t_fill.dt.to_period("M")).R.sum()
    return round(m.max() / t.R.sum(), 2)


def summ(t):
    s = stats(t)
    if len(t) == 0:
        return s
    s["gross_R"] = round((t.gross_pips / t.risk_pips).mean(), 3)
    s["risk_pips"] = round(t.risk_pips.median(), 1)
    s["cost_R"] = round(s["gross_R"] - s["avg_R"], 3)
    return s


def pooled_stats(trades, period):
    t = trades[(trades.status == "filled") & (trades.period == period)].sort_values("t_exit")
    s = summ(t)
    allp = trades[(trades.period == period) & trades.status.isin(["filled", "not_filled"])]
    s["fill_rate"] = round((allp.status == "filled").mean(), 2) if len(allp) else np.nan
    per = t.groupby("sym").R.mean() if len(t) else pd.Series(dtype=float)
    s["pairs_pos"] = f"{int((per > 0).sum())}/{len(per)}"
    s["top_month"] = top_month_share(t)
    return s


def per_pair(trades, period):
    rows = []
    for sym in PAIRS:
        t = trades[(trades.sym == sym) & (trades.status == "filled") & (trades.period == period)].sort_values("t_exit")
        s = summ(t); s["sym"] = sym
        rows.append(s)
    return pd.DataFrame(rows).set_index("sym")


def md(df):
    df = df.reset_index()
    return "\n".join(["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)] +
                     ["| " + " | ".join("" if (isinstance(x, float) and np.isnan(x)) else str(x) for x in r) + " |"
                      for r in df.itertuples(index=False)])


COLS = ["trades", "per_week", "win_pct", "avg_R", "total_R", "PF", "maxDD_R", "avg_net_pips", "usd_per_01lot", "avg_hold_h",
        "gross_R", "cost_R", "risk_pips"]


def main():
    os.makedirs(OUT, exist_ok=True)
    cmd = sys.argv[1]
    vfile = os.path.join(OUT, "variants.json")
    reg = json.load(open(vfile)) if os.path.exists(vfile) else {}
    sfile = os.path.join(OUT, "dev_summary.csv")
    if cmd == "dev":
        phase = sys.argv[2]
        variants = build_variants(sys.argv[3] if phase == "B" else "M15")
        if phase == "A":
            variants = {n: v for n, v in variants.items() if n[:3] in ("V01", "V02", "V03", "V04")}
        elif phase == "C":
            variants = {n: v for n, v in variants.items() if n[:3] in ("V15", "V16")}
        else:
            variants = {n: v for n, v in variants.items() if n[:3] not in ("V01", "V02", "V03", "V04", "V15", "V16")}
        res = run_variants(variants, DEV_END)
        rows = []
        for n, t in res.items():
            if len(t) == 0:
                rows.append(dict(variant=n, trades=0)); continue
            save("BT3", n, t[t.period == "DEV"])           # DEV only for exploration variants
            reg[n] = variants[n]
            rows.append(dict(variant=n, tf=variants[n]["tf"], **pooled_stats(t, "DEV")))
        json.dump(reg, open(vfile, "w"), indent=1)
        df = pd.DataFrame(rows)
        old = pd.read_csv(sfile) if os.path.exists(sfile) else pd.DataFrame()
        df_all = pd.concat([old[~old.variant.isin(df.variant)] if len(old) else old, df], ignore_index=True)
        df_all.to_csv(sfile, index=False)
        print(df.to_string())
    elif cmd == "val":
        names = sys.argv[2:]
        assert len(names) <= 3, "at most 3 variants may go to VAL"
        variants = {n: reg[n] for n in names}
        res = run_variants(variants, CUT)
        rows = []
        for n, t in res.items():
            save("BT3", n, t)                                # DEV + VAL (TEST excluded: nothing >= CUT exists)
            for p in ("DEV", "VAL"):
                rows.append(dict(variant=n, period=p, **pooled_stats(t, p)))
        df = pd.DataFrame(rows)
        df.to_csv(os.path.join(OUT, "val_summary.csv"), index=False)
        print(df.to_string())
    elif cmd == "tables":
        for n in sys.argv[2:]:
            t = pd.read_csv(os.path.join(OUT, f"trades_{n}.csv"), parse_dates=["t_signal", "t_fill", "t_exit"])
            print(f"\n### {n}  params={reg.get(n)}")
            for p in ("DEV", "VAL"):
                if (t.period == p).any():
                    print(f"\n{p} pooled: {pooled_stats(t, p)}\n")
                    pp = per_pair(t, p)
                    print(md(pp[[c for c in COLS if c in pp.columns]]))


if __name__ == "__main__":
    main()
