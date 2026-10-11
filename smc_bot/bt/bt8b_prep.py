"""BT8b prep: (1) bigger-picture causal features, (2) exact re-simulation of exit policies, (3) spike measure from M1.

    .venv/Scripts/python.exe smc_bot/bt/bt8b_prep.py feat            # -> results/BT8b/feat_bp.pkl
    .venv/Scripts/python.exe smc_bot/bt/bt8b_prep.py resim [SYM ...]  # -> results/BT8b/parts/{sym}_pol.pkl
    .venv/Scripts/python.exe smc_bot/bt/bt8b_prep.py merge           # -> results/BT8b/policy_outcomes.pkl

Bigger-picture features (pre-registered before any result was seen; all from CLOSED bars at or before t_signal):
  D1 = H1 bars grouped into days ending 22:00 UTC (NY close; Sunday evening joins Monday); W1 = Mon..Fri of those days.
  A D1/W1 bar is used only once its nominal close (22:00 UTC of the day / Friday) is <= t_signal.
  trend      bp_d1_ema_rel/dist (EMA50 D1), bp_w1_ema_rel/dist (EMA20 W1), bp_d1_struct / bp_w1_struct (smc_core.structure
             trend, N=3 / N=2) -- all multiplied by the trade side (+ = with the trade)
  levels     room to / distance behind the previous-day, previous-week and 20-day extremes, in D1 ATR (trade-directional);
             bp_pos20 = entry position inside the 20-day range (0 = far end behind the trade, 1 = at the target-side extreme);
             bp_tp_vs_pdx = TP beyond the previous-day extreme (D1 ATR)
  day        bp_today_move (last H1 close vs previous D1 close, D1 ATR, trade-directional), bp_today_rng (today's range so far / D1 ATR)
  regime     efficiency ratio 20 on H4 and D1 (raw and signed with the trade), ADX14 D1, D1 ATR percentile over 252 days,
             log(ATR14/ATR100) D1, days since the last D1 range > 2x ATR14
  strength   per currency (USD EUR GBP JPY AUD NZD CAD CHF XAU) the mean vol-normalised return of every instrument that
             contains it (sign flipped for the quote side) over ~1 and ~5 days (anchor = D1 close 1 / 5 days back, price =
             last closed H1); trade alignment = side x (base - quote), and the rank difference (9 currencies)
  risk       JPY-cross basket (USDJPY EURJPY GBPJPY AUDJPY NZDJPY CADJPY) 1d/5d, gold 5d, and bp_risk_align5 = side x
             sign(pair risk score) x basket 5d (risk scores AUD,NZD +1; CAD,GBP +.5; EUR 0; USD -.5; CHF,JPY,XAU -1)
  calendar   day of month, calendar days to month end, week of month, UTC hour of the signal

Exit policies (re-simulated with common.simulate on every originally FILLED candidate; exact under the simulator):
  P0 = original (TP = nearest unbroken H1 liquidity), from BT13.
  P1 = TP at 2R (planned R = |entry - sl|); default cancel-if-TP-first (a nearer TP can cancel the limit: exact).
  P2 = 50% at 1R + 50% runner to the original TP with stop to break-even after +1R.
       Half A = TP 1R with cancel_limit_if_tp_first=False (fill decided by the ORIGINAL order, identical fill asserted);
       half B = P3. R_P2 = 0.5 R_A + 0.5 R_B (commission per half is half the round trip: exact).
  P3 = original TP with break-even at +1R (be_at_r = 1).
Spike: stop-out minute's 5-minute window (M1 bars k-4..k) range in units of ATR14(M5) at the signal (= planned risk /
f_stop_atr) and whether that window moved against the trade.
"""
import os, sys, time, gc
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bt8b_lib import *                                           # noqa: F401,F403
import bt8b_lib as L                                             # noqa: E402
import common                                                    # noqa: E402
from smc_core import structure                                  # noqa: E402

PARTS = os.path.join(L.OUT, "parts"); os.makedirs(PARTS, exist_ok=True)
CCY = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "XAU"]
RISK = dict(AUD=1, NZD=1, CAD=.5, GBP=.5, EUR=0, USD=-.5, CHF=-1, JPY=-1, XAU=-1)
JPYX = ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CADJPY"]


# ================================================================================================== D1 / W1 bars
def htf(sym):
    h1 = common.bars(sym, "H1")[["time", "open", "high", "low", "close", "tclose"]].copy()
    sh = h1.time + pd.Timedelta(hours=2)
    h1["dkey"] = sh.dt.floor("D")
    h1 = h1[h1.dkey.dt.dayofweek < 5].reset_index(drop=True)
    g = h1.groupby("dkey")
    d1 = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(),
                       "n": g.size()}).reset_index().rename(columns={"dkey": "time"})
    d1["tclose"] = d1.time + pd.Timedelta(hours=22)          # nominal close 22:00 UTC of the day
    d1["wkey"] = d1.time - pd.to_timedelta(d1.time.dt.dayofweek, unit="D")
    g = d1.groupby("wkey")
    w1 = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last()}).reset_index()
    w1 = w1.rename(columns={"wkey": "time"})
    w1["tclose"] = w1.time + pd.Timedelta(days=4, hours=22)  # Friday 22:00 UTC
    for b, ne, na in ((d1, 50, 14), (w1, 20, 14)):
        pc = b.close.shift(1)
        tr = np.maximum(b.high - b.low, np.maximum((b.high - pc).abs(), (b.low - pc).abs()))
        b["atr"] = tr.rolling(na, min_periods=5).mean()
        b["ema"] = b.close.ewm(span=ne, adjust=False).mean()
    d1["atr100"] = (d1.high - d1.low).rolling(100, min_periods=30).mean()
    d1["tr"] = np.maximum(d1.high - d1.low, np.maximum((d1.high - d1.close.shift(1)).abs(), (d1.low - d1.close.shift(1)).abs()))
    d1["volpct"] = d1.atr.rolling(252, min_periods=60).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    d1["er20"] = (d1.close - d1.close.shift(20)) / d1.close.diff().abs().rolling(20).sum()
    # ADX14 (Wilder)
    up = d1.high.diff(); dn = -d1.low.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0); ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = 1 / 14
    atrw = d1.tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pd.Series(pdm).ewm(alpha=a, adjust=False).mean() / atrw
    ndi = 100 * pd.Series(ndm).ewm(alpha=a, adjust=False).mean() / atrw
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi)
    d1["adx"] = dx.ewm(alpha=a, adjust=False).mean()
    big = (d1.high - d1.low) > 2 * d1.atr.shift(1)
    last = np.full(len(d1), -1); lb = -10 ** 6
    for i, b_ in enumerate(big.values):
        if b_:
            lb = i
        last[i] = i - lb
    d1["since_big"] = np.minimum(last, 250)
    d1["struct"] = structure(d1, 3)["trend"]
    w1["struct"] = structure(w1, 2)["trend"]
    d1["hi20"] = d1.high.rolling(20).max(); d1["lo20"] = d1.low.rolling(20).min()
    # running intraday range per H1 bar (for today's range so far)
    h1["rh"] = h1.groupby("dkey").high.cummax(); h1["rl"] = h1.groupby("dkey").low.cummin()
    h4 = common.bars(sym, "H4")[["time", "close", "tclose"]].copy()
    h4["er20"] = (h4.close - h4.close.shift(20)) / h4.close.diff().abs().rolling(20).sum()
    return h1, h4, d1, w1


def _asof(tc, t):
    return np.searchsorted(np.asarray(tc, dtype="datetime64[ns]"), np.asarray(t, dtype="datetime64[ns]"), "right") - 1


def strength_grid():
    """Hourly grid of per-instrument normalised 1d/5d returns -> currency strength, risk basket, gold."""
    grid = pd.date_range("2004-06-01", "2025-01-01", freq="h").values.astype("datetime64[ns]")
    r1 = {}; r5 = {}
    for sym in L.ALL25:
        h1, h4, d1, w1 = htf(sym)
        ih = _asof(h1.tclose.values, grid); idd = _asof(d1.tclose.values, grid)
        ok = (ih >= 0) & (idd >= 6)
        c_now = np.where(ok, h1.close.values[np.maximum(ih, 0)], np.nan)
        dc = d1.close.values; atrr = (d1.atr / d1.close).values
        a1 = np.where(ok, dc[np.maximum(idd - 1, 0)], np.nan); a5 = np.where(ok, dc[np.maximum(idd - 5, 0)], np.nan)
        ar = np.where(ok, atrr[np.maximum(idd, 0)], np.nan)
        # stale guard: last H1 close older than 3 days -> NaN
        stale = ok & ((grid - h1.tclose.values.astype("datetime64[ns]")[np.maximum(ih, 0)]) > np.timedelta64(72, "h"))
        v1 = np.log(c_now / a1) / ar; v5 = np.log(c_now / a5) / (ar * np.sqrt(5))
        v1[stale] = np.nan; v5[stale] = np.nan
        r1[sym] = v1.astype("float32"); r5[sym] = v5.astype("float32")
        print("grid", sym, flush=True)
    S1 = {}; S5 = {}
    for x in CCY:
        a1 = []; a5 = []
        for sym in L.ALL25:
            if sym[:3] == x:
                a1.append(r1[sym]); a5.append(r5[sym])
            elif sym[3:] == x:
                a1.append(-r1[sym]); a5.append(-r5[sym])
        S1[x] = np.nanmean(np.vstack(a1), 0); S5[x] = np.nanmean(np.vstack(a5), 0)
    g = pd.DataFrame({"t": grid})
    for x in CCY:
        g[f"S1_{x}"] = S1[x]; g[f"S5_{x}"] = S5[x]
    rk = pd.DataFrame({x: S5[x] for x in CCY}).rank(axis=1)
    for x in CCY:
        g[f"K5_{x}"] = rk[x].values
    g["risk1"] = np.nanmean(np.vstack([r1[s] for s in JPYX]), 0)
    g["risk5"] = np.nanmean(np.vstack([r5[s] for s in JPYX]), 0)
    g["gold5"] = r5["XAUUSD"]
    return g


def feat():
    c = L.load_cands()
    g = strength_grid()
    out = []
    for sym in L.ALL25:
        cs = c[c.sym == sym]
        if not len(cs):
            continue
        h1, h4, d1, w1 = htf(sym)
        t = cs.t_signal.values.astype("datetime64[ns]"); d = cs.side.values.astype(float)
        ent = cs.entry.values; tp = cs.tp.values
        i1 = _asof(h1.tclose, t); i4 = _asof(h4.tclose, t); idd = _asof(d1.tclose, t); iw = _asof(w1.tclose, t)
        ig = _asof(g.t.values, t)
        bad = (i1 < 0) | (i4 < 0) | (idd < 0) | (iw < 0) | (ig < 0)       # first days of 2005: no closed W1/D1 yet -> NaN
        i1, i4, idd, iw, ig = [np.maximum(x, 0) for x in (i1, i4, idd, iw, ig)]
        D = d1.iloc[idd].reset_index(drop=True); W = w1.iloc[iw].reset_index(drop=True)
        H = h1.iloc[i1].reset_index(drop=True); G = g.iloc[ig].reset_index(drop=True)
        a = D.atr.values; aw = W.atr.values
        f = pd.DataFrame({"cid": cs.cid.values})
        f["bp_d1_ema_rel"] = np.sign(D.close - D.ema).values * d
        f["bp_d1_ema_dist"] = np.clip((D.close - D.ema).values / a * d, -10, 10)
        f["bp_w1_ema_rel"] = np.sign(W.close - W.ema).values * d
        f["bp_w1_ema_dist"] = np.clip((W.close - W.ema).values / aw * d, -10, 10)
        f["bp_d1_struct"] = D.struct.values * d
        f["bp_w1_struct"] = W.struct.values * d
        pdx_f = np.where(d == 1, D.high, D.low); pdx_b = np.where(d == 1, D.low, D.high)
        pwx_f = np.where(d == 1, W.high, W.low); pwx_b = np.where(d == 1, W.low, W.high)
        x20_f = np.where(d == 1, D.hi20, D.lo20); x20_b = np.where(d == 1, D.lo20, D.hi20)
        f["bp_room_pdx"] = np.clip((pdx_f - ent) * d / a, -5, 5)
        f["bp_back_pdx"] = np.clip((ent - pdx_b) * d / a, -5, 5)
        f["bp_room_pwx"] = np.clip((pwx_f - ent) * d / a, -10, 10)
        f["bp_back_pwx"] = np.clip((ent - pwx_b) * d / a, -10, 10)
        f["bp_room_20d"] = np.clip((x20_f - ent) * d / a, -10, 15)
        rng20 = (D.hi20 - D.lo20).values
        f["bp_pos20"] = np.clip(np.where(rng20 > 0, (ent - x20_b) * d / rng20, np.nan), -0.5, 1.5)
        f["bp_tp_vs_pdx"] = np.clip((tp - pdx_f) * d / a, -5, 5)
        same_day = (H.dkey.values.astype("datetime64[ns]") + np.timedelta64(22, "h")) > D.tclose.values.astype("datetime64[ns]")
        f["bp_today_move"] = np.clip(np.where(same_day, (H.close - D.close).values * d / a, 0.0), -5, 5)
        f["bp_today_rng"] = np.clip(np.where(same_day, (H.rh - H.rl).values / a, 0.0), 0, 5)
        f["bp_er_h4"] = np.abs(h4.er20.values[i4]); f["bp_er_h4_dir"] = h4.er20.values[i4] * d
        f["bp_er_d1"] = np.abs(D.er20.values); f["bp_er_d1_dir"] = D.er20.values * d
        f["bp_adx_d1"] = D.adx.values
        f["bp_volpct_d1"] = D.volpct.values
        f["bp_atr_d1_rel"] = np.log(D.atr / D.atr100).values
        f["bp_since_bigd1"] = np.log1p(D.since_big.values)
        b, q = sym[:3], sym[3:]
        f["bp_cs1"] = d * (G[f"S1_{b}"] - G[f"S1_{q}"]).values
        f["bp_cs5"] = d * (G[f"S5_{b}"] - G[f"S5_{q}"]).values
        f["bp_cs5_rank"] = d * (G[f"K5_{b}"] - G[f"K5_{q}"]).values / 8.0
        pr = np.sign(RISK[b] - RISK[q])
        f["bp_risk1"] = G.risk1.values; f["bp_risk5"] = G.risk5.values; f["bp_gold5"] = G.gold5.values
        f["bp_risk_align5"] = d * pr * G.risk5.values
        ts = pd.DatetimeIndex(cs.t_signal.values)
        f["bp_dom"] = ts.day; f["bp_to_me"] = (ts.days_in_month - ts.day).values; f["bp_wom"] = (ts.day - 1) // 7
        f["bp_hour"] = ts.hour + ts.minute / 60.0
        tf = np.max(np.vstack([h1.tclose.values.astype("datetime64[ns]")[i1], h4.tclose.values.astype("datetime64[ns]")[i4],
                               d1.tclose.values.astype("datetime64[ns]")[idd], w1.tclose.values.astype("datetime64[ns]")[iw],
                               g.t.values[ig]]), 0)
        bpc = [x for x in f.columns if x.startswith("bp_")]
        f.loc[bad, bpc] = np.nan
        tf = np.where(bad, t, tf)
        f["t_feat_bp"] = tf
        assert (tf <= t).all(), f"{sym}: bigger-picture feature after t_signal"
        out.append(f)
        print("feat", sym, len(f), flush=True)
    F = pd.concat(out, ignore_index=True)
    F.to_pickle(os.path.join(L.OUT, "feat_bp.pkl"))
    print(F.describe().T.round(3).to_string())
    return F


# ================================================================================================== policy re-simulation
_orig_bars = common.bars
_m = {}


def _bars1(sym, tf):                                           # keep ONE instrument's M1 in memory (RAM)
    k = (sym, tf)
    if k not in _m:
        _m.clear(); gc.collect()
        _m[k] = _orig_bars(sym, tf)
    return _m[k]


common.bars = _bars1                                            # module-local patch; nothing on disk changes


def resim(sym, c):
    f = c[(c.sym == sym) & (c.status == "filled")].copy()
    if not len(f):
        return
    t0 = time.time()
    base = f[L.ORDER_COLS + ["cid"]].copy()
    rk = (base.entry - base.sl) * base.side
    out = f[["cid", "t_fill", "t_exit", "R"]].rename(columns={"t_fill": "t_fill0", "t_exit": "t_exit0", "R": "R0"})
    keep = ["cid", "status", "t_fill", "t_exit", "exit_reason", "R", "gross_R", "usd_01", "hold_h", "mfe_R"]

    o = base.copy(); o["tp"] = o.entry + o.side * 2.0 * rk
    p1 = L.add_gross(common.simulate(sym, o, one_at_a_time=False))[keep]
    o = base.copy(); o["tp"] = o.entry + o.side * 1.0 * rk
    pa = L.add_gross(common.simulate(sym, o, one_at_a_time=False, cancel_limit_if_tp_first=False))[keep]
    o = base.copy(); o["be_at_r"] = 1.0
    p3 = L.add_gross(common.simulate(sym, o, one_at_a_time=False))[keep]
    out = out.merge(p1.add_suffix("_P1").rename(columns={"cid_P1": "cid"}), on="cid") \
             .merge(pa.add_suffix("_PA").rename(columns={"cid_PA": "cid"}), on="cid") \
             .merge(p3.add_suffix("_P3").rename(columns={"cid_P3": "cid"}), on="cid")
    assert (out.status_PA == "filled").all() and (out.t_fill_PA == out.t_fill0).all(), "half-A fill differs"
    assert (out.status_P3 == "filled").all() and (out.t_fill_P3 == out.t_fill0).all(), "P3 fill differs"
    f1 = out.status_P1 == "filled"
    assert (out.t_fill_P1[f1] == out.t_fill0[f1]).all()
    out["R_P2"] = 0.5 * out.R_PA + 0.5 * out.R_P3
    out["gross_R_P2"] = 0.5 * out.gross_R_PA + 0.5 * out.gross_R_P3
    out["usd_01_P2"] = 0.5 * out.usd_01_PA + 0.5 * out.usd_01_P3
    out["t_exit_P2"] = np.maximum(out.t_exit_PA.values, out.t_exit_P3.values)
    # spike: 5-minute window ending at the stop-out minute, in ATR14(M5) at signal
    m1 = common.bars(sym, "M1"); t1 = m1.time.values; h = m1.high.values; l = m1.low.values; op = m1.open.values; cl = m1.close.values
    k = np.searchsorted(t1, f.t_exit.values.astype("datetime64[ns]"), "left")
    k0 = np.maximum(k - 4, 0)
    rng5 = np.array([h[a:b + 1].max() - l[a:b + 1].min() for a, b in zip(k0, k)])
    mv5 = (cl[k] - op[k0]) * f.side.values
    atr5 = (f.entry - f.sl).values * f.side.values / f.f_stop_atr.values
    out["spike5_atr"] = rng5 / atr5
    out["spike5_against"] = (mv5 < 0).astype(float)
    out["exitbar_atr"] = (h[k] - l[k]) / atr5
    out.to_pickle(os.path.join(PARTS, f"{sym}_pol.pkl"))
    print(f"{sym}: {len(f)} filled re-simulated, P1 filled {int(f1.sum())} ({time.time() - t0:.0f}s)", flush=True)


def merge():
    fs = [os.path.join(PARTS, f"{s}_pol.pkl") for s in L.ALL25]
    miss = [f for f in fs if not os.path.exists(f)]
    assert not miss, miss
    P = pd.concat([pd.read_pickle(f) for f in fs], ignore_index=True)
    P.to_pickle(os.path.join(L.OUT, "policy_outcomes.pkl"))
    print(len(P)); print(P.describe().T.round(3).to_string())


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "feat":
        feat()
    elif cmd == "resim":
        c = L.load_cands()
        syms = sys.argv[2:] or L.ALL25
        for s in syms:
            if os.path.exists(os.path.join(PARTS, f"{s}_pol.pkl")):
                continue
            resim(s, c)
            _m.clear(); gc.collect()
    elif cmd == "merge":
        merge()
