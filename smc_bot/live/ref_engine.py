"""Canonical REF (frozen, results/RECON/RECON_report.md section 4) evaluated on a rolling window of CLOSED bars.

Canonical REF = BT6b REF + literal OB zone = recon_analysis.CANON run through recon_pipeline.build_orders.

How the live state is computed (correctness over speed):
  * The per-timeframe arrays are built with the SAME functions bt6b_pipeline.prepare uses (swings_own,
    structure_internal, atr_tr, ema, dealing_range), on the last W bars of each timeframe (closed bars only).
  * SIGNAL AUTHORITY: recon_pipeline.build_orders(CANON) itself is run on that window (its per-pair context is
    injected in memory under a private period key; no shared file is edited). The orders whose t_signal equals
    the close of the last closed M5 bar are the new stage-4 signals.
  * DIAGNOSTICS: trace() walks the same steps zone by zone (POI -> touch -> CHoCH -> filters) to report stages 0-3
    and the reason a setup was rejected. Its signals are cross-checked against build_orders on every evaluation.

One window artefact of the batch code is neutralised: build_orders caps the POI expiry at the last M30 bar of its
data (texp = tc[min(k+96, nZ-1)]), which at the right edge of a live window would hide a touch that happens inside
the still-forming M30 bar. The M30 close-time array handed to build_orders is therefore extended with future
timestamps (> T). Nothing else reads those entries (zones() uses OHLC only), so detection is unchanged.
"""
import os, sys
from collections import Counter
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SMC = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SMC, "bt"))
_cwd = os.getcwd()
import common                                  # noqa: E402  (chdir's to the TTXT root; restored below)
import recon_pipeline as R                     # noqa: E402
import bt6b_pipeline as B                      # noqa: E402
from recon_analysis import CANON               # noqa: E402  the frozen canonical config
os.chdir(_cwd)

COMM, pip = common.COMM, common.pip
PAIRS9 = list(R.PAIRS9)                        # 10 pairs minus EURGBP (BT7)
TFS = ("M5", "M30", "H1", "H4")
TF_MIN = dict(M5=5, M30=30, H1=60, H4=240)
SWING_N = dict(M5=3, M30=3, H1=5, H4=3)        # as bt6b_pipeline.prepare
# rolling windows (closed bars). Convergence: EMA200 init error decays 0.99^n (2500 H1 bars -> 1e-11);
# structure states resynchronise after the first confirmed swing high + low + break inside the window.
WINDOW = dict(M5=3000, M30=2000, H1=2500, H4=1000)
MIN_BARS = dict(M5=1200, M30=600, H1=1200, H4=300)
ZONE_KEY = (CANON["zone_N"], CANON["ob_def"], CANON["ob_premit"], CANON["fvg_def"])
LOOKBACK_SIG = np.timedelta64(3, "D")          # signals recomputed for the last 3 days (zones from 7 days: s0 - 4 days)
ZONE_LIFE = 96                                 # M30 bars (CANON expiry="bars")
CHOCH_WAIT = 36                                # M5 bars after the touch (CANON choch_win="incl": touch..touch+36)
LIMIT_MIN = 60                                 # build_orders: expire = t_signal + 60 min
MIN_RR, MIN_STOP_COMM = 2.0, 10.0
EXTEND_M30_TC = True                           # False only for the parity negative control (makes the artefact visible)
STAGE_NAMES = {0: "no bias", 1: "bias ok, no POI", 2: "POI waiting for touch", 3: "POI touched, waiting 5M CHoCH",
               4: "PENDING LIMIT signal"}

assert CANON == dict(bias_filter=True, bias_checks="all3", ema_warm=True, start="2021-02-01", zone_N=3, ob_def="core",
                     ob_premit=True, fvg_def="B", expiry="bars", touch="onesided", drange="swing", choch_win="incl",
                     ez="B", pullback=True, atr_stop="tr", target="nearest", dedupe=True, pd_point="mid",
                     kz="local"), "canonical REF config changed - the live scanner mirrors the frozen one"


def point(sym):
    return 0.01 if sym == "XAUUSD" else pip(sym) / 10


def fmt_px(sym, x):
    if x is None or not np.isfinite(x):
        return "-"
    return f"{x:.{2 if sym == 'XAUUSD' else (3 if 'JPY' in sym else 5)}f}"


def ts(x):
    return None if x is None else str(pd.Timestamp(x))[:19]


def raw_from_df(df):
    """DataFrame(time, open, high, low, close[, tclose]) -> raw arrays dict (times datetime64[ns])."""
    t = df["time"].values.astype("datetime64[ns]")
    tc = df["tclose"].values.astype("datetime64[ns]") if "tclose" in df else None
    return dict(t=t, tc=tc, o=df["open"].values.astype(float), h=df["high"].values.astype(float),
                l=df["low"].values.astype(float), c=df["close"].values.astype(float))


class RefEngine:
    """One per pair. evaluate(raw, T) -> status dict. raw[tf] = dict(t, tc, o, h, l, c) of CLOSED bars (tc <= T)."""

    def __init__(self, sym):
        self.sym = sym
        self._tf = {}          # tf -> (key, arrays)
        self._zones = (None, None)

    # ------------------------------------------------------------------------------------------ arrays
    def _arrays(self, tf, r):
        key = (len(r["t"]), r["t"][0], r["t"][-1])
        hit = self._tf.get(tf)
        if hit is not None and hit[0] == key:
            return hit[1]
        o, h, l, c = r["o"], r["h"], r["l"], r["c"]
        N = SWING_N[tf]
        sh, sl = B.swings_own(h, l, N)
        tri, evi, bri = B.structure_internal(h, l, c, N, sh, sl)
        A = dict(t=r["t"], tc=r["tc"], o=o, h=h, l=l, c=c, sh=sh, sl=sl, atr=B.atr_tr(h, l, c), tr_i=tri, ev_i=evi,
                 br_i=bri, N=N)
        if tf == "H1":
            A["ema200"] = B.ema(c, 200)
        if tf == "H4":
            A["rlo"], A["rhi"] = B.dealing_range(h, l, sh, sl, 3)
        self._tf[tf] = (key, A)
        return A

    # ------------------------------------------------------------------------------------------ main
    def evaluate(self, raw, T):
        sym = self.sym
        T = np.datetime64(pd.Timestamp(T), "ns")
        out = dict(pair=sym, t_bar_close=ts(T), warnings=[])
        for tf in TFS:
            if raw.get(tf) is None or len(raw[tf]["t"]) < MIN_BARS[tf]:
                out.update(stage=-1, stage_name="insufficient history",
                           detail=f"{tf}: {0 if raw.get(tf) is None else len(raw[tf]['t'])} bars < {MIN_BARS[tf]}")
                return out
            if raw[tf]["tc"] is None:
                raw[tf]["tc"] = raw[tf]["t"] + np.timedelta64(TF_MIN[tf], "m")
            if raw[tf]["tc"][-1] > T:
                raise ValueError(f"{tf} bar closing after T passed to evaluate")
        ctx = dict(sym=sym, period="LIVE")
        for tf in TFS:
            ctx[tf] = self._arrays(tf, raw[tf])
        if len(raw["M5"]["t"]) < WINDOW["M5"] or len(raw["H1"]["t"]) < WINDOW["H1"]:
            out["warnings"].append("history shorter than the design window "
                                   + ", ".join(f"{tf}={len(raw[tf]['t'])}/{WINDOW[tf]}" for tf in TFS))
        M30 = ctx["M30"]
        # M30 close times extended into the future for build_orders' expiry lookup (see module docstring)
        fut = T + np.timedelta64(30, "m") * np.arange(1, ZONE_LIFE + 105)
        M30x = dict(M30); M30x["tc"] = np.r_[M30["tc"], fut] if EXTEND_M30_TC else M30["tc"]
        ctxR = dict(ctx); ctxR["M30"] = M30x
        X = dict(ctx=ctxR, zones={})
        zk_key = (len(M30["t"]), M30["t"][0], M30["t"][-1])
        if self._zones[0] == zk_key:
            X["zones"][ZONE_KEY] = self._zones[1]

        # ---------------- signal authority: the canonical build_orders on this window
        per = "_LIVE_" + sym
        s0, s1 = T - LOOKBACK_SIG, T + np.timedelta64(1, "m")
        R.PERIODS[per] = (pd.Timestamp(s0), pd.Timestamp(s1))
        R._PD[(sym, per)] = X
        try:
            orders = R.build_orders(sym, per, CANON, Counter())
        finally:
            R._PD.pop((sym, per), None); R.PERIODS.pop(per, None)
        self._zones = (zk_key, X["zones"][ZONE_KEY])
        if len(orders):
            orders = orders.drop(columns=["zk"])

        # ---------------- diagnostics
        tr = trace(sym, X, ctx, T, s0)
        out.update(tr)
        # cross-check trace vs build_orders
        mine = {(pd.Timestamp(s["t_signal"]), s["side"], round(s["entry"], 9), round(s["sl"], 9), round(s["tp"], 9))
                for s in tr["_signals_all"]}
        canon = {(pd.Timestamp(r.t_signal), int(r.side), round(r.entry, 9), round(r.sl, 9), round(r.tp, 9))
                 for r in orders.itertuples()} if len(orders) else set()
        out["trace_matches_canonical"] = mine == canon
        if mine != canon:
            out["warnings"].append(f"trace/build_orders mismatch: trace-only {sorted(mine - canon)} "
                                   f"canon-only {sorted(canon - mine)} (build_orders is authoritative)")
        # new signals (CHoCH closed at T) and active ones (limit still valid) from the AUTHORITATIVE orders
        byk = {(pd.Timestamp(s["t_signal"]), s["side"]): s for s in tr["_signals_all"]}
        sig_new, sig_act = [], []
        for r in (orders.itertuples() if len(orders) else []):
            s = byk.get((pd.Timestamp(r.t_signal), int(r.side)))
            if s is None or (round(s["entry"], 9), round(s["sl"], 9), round(s["tp"], 9)) != \
                    (round(r.entry, 9), round(r.sl, 9), round(r.tp, 9)):
                s = dict(pair=sym, t_signal=ts(r.t_signal), side=int(r.side), side_txt="BUY" if r.side == 1 else "SELL",
                         entry=r.entry, sl=r.sl, tp=r.tp, rr=r.rr, valid_until=ts(r.expire), killzone=r.session,
                         note="fields from build_orders only (trace disagreed)")
            if np.datetime64(r.t_signal) == T:
                sig_new.append(s)
            if np.datetime64(r.t_signal) <= T < np.datetime64(r.expire):
                sig_act.append(s)
        out["signals_new"], out["signals_active"] = sig_new, sig_act
        out["orders_recent"] = [dict(t_signal=ts(r.t_signal), side=int(r.side), entry=r.entry, sl=r.sl, tp=r.tp,
                                     rr=r.rr, session=r.session) for r in orders.itertuples()] if len(orders) else []
        # ---------------- stage
        if sig_act:
            st = 4
            s = sig_act[-1]
            detail = (f"{s['side_txt']} LIMIT {fmt_px(sym, s['entry'])} SL {fmt_px(sym, s['sl'])} TP {fmt_px(sym, s['tp'])}"
                      f" RR {s['rr']:.2f} until {s['valid_until'][11:16]}")
        elif tr["waiting"]:
            st = 3
            w = min(tr["waiting"], key=lambda z: z["bars_left"])
            detail = (f"{w['kind']} {fmt_px(sym, w['bottom'])}-{fmt_px(sym, w['top'])} touched {w['t_touch'][5:16]},"
                      f" {w['bars_left']} M5 bars left for CHoCH")
            if tr["bias"]["dir"] != w["d"]:
                detail += " (bias now differs: CHoCH would be rejected)"
        elif tr["bias"]["dir"] == 0:
            st = 0
            b = tr["bias"]
            detail = f"H4 trend {b['h4_trend']:+d} vs EMA200 side {b['ema_side']:+d}" if b["ok"] else b.get("why", "")
        else:
            pois = [z for z in tr["pois"] if z["d"] == tr["bias"]["dir"]]
            if pois:
                st = 2
                z = min(pois, key=lambda z: z["dist_pips"])
                detail = (f"{len(pois)} POI; nearest {z['kind']} {fmt_px(sym, z['bottom'])}-{fmt_px(sym, z['top'])}"
                          f" {z['dist_pips']:.1f} pips away")
            else:
                st = 1
                detail = "no unmitigated M30 POI in bias direction and discount/premium"
        out["stage"], out["stage_name"], out["detail"] = st, STAGE_NAMES[st], detail
        out.pop("_signals_all", None)
        return out


# ============================================================================================== diagnostics
def trace(sym, X, ctx, T, s0):
    """Mirror of recon_pipeline.build_orders (CANON flags) per zone, keeping the reason at every step."""
    M30, M5, H1, H4 = ctx["M30"], ctx["M5"], ctx["H1"], ctx["H4"]
    P, cm = pip(sym), COMM[sym]
    Z = X["zones"][ZONE_KEY]
    zd, zlo, zhi, zk, zkind = Z.d.values, Z.lo.values, Z.hi.values, Z.k.values, Z.kind.values
    tcr = M30["tc"][zk]
    nreal = len(M30["tc"])
    t5, tc5, lo5, hi5, c5 = M5["t"], M5["tc"], M5["l"], M5["h"], M5["c"]
    n5 = len(t5); iT = n5 - 1
    ev, br = M5["ev_i"], M5["br_i"]
    price = float(c5[-1])
    # ---- bias now (same function as the pipeline)
    i4 = int(np.searchsorted(H4["tc"], T, "right") - 1); i1 = int(np.searchsorted(H1["tc"], T, "right") - 1)
    bias_now = int(R.bias_at(ctx, T, CANON["ema_warm"])[0])
    h4t = int(H4["tr_i"][i4]); es = int(np.sign(H1["c"][i1] - H1["ema200"][i1]))
    rlo_n, rhi_n = float(H4["rlo"][i4]), float(H4["rhi"][i4])
    ppos = (price - rlo_n) / (rhi_n - rlo_n) if rhi_n > rlo_n else np.nan
    bias = dict(dir=bias_now, txt={1: "BUY", -1: "SELL", 0: "NONE"}[bias_now], ok=bool(i1 >= 200 and i4 >= 0),
                h4_trend=h4t, h4_last_close=ts(H4["tc"][i4]), ema_side=es, h1_close=float(H1["c"][i1]),
                ema200=float(H1["ema200"][i1]), h4_range_lo=rlo_n, h4_range_hi=rhi_n,
                price_pos_in_range=None if ppos != ppos else round(float(ppos), 3))
    if not bias["ok"]:
        bias["why"] = "fewer than 200 closed H1 bars (EMA200 warm-up)"
    # ---- zones considered by build_orders at this T: tcr in [s0 - 4 days, T]
    sel = np.where((tcr >= s0 - np.timedelta64(4, "D")) & (tcr <= T))[0]
    b0 = R.bias_at(ctx, tcr[sel], CANON["ema_warm"])
    rlo, rhi = H4["rlo"], H4["rhi"]
    j4 = np.maximum(np.searchsorted(H4["tc"], tcr[sel], "right") - 1, 0)
    mid = (zlo[sel] + zhi[sel]) / 2
    pos = (mid - rlo[j4]) / np.where(rhi[j4] > rlo[j4], rhi[j4] - rlo[j4], np.nan)
    dpos = np.where(zd[sel] == 1, pos, 1 - pos)
    cur_close = (T.astype("datetime64[m]").astype(np.int64) // 30 + 1) * 30   # close of the M30 bar holding the next M5 bar
    pois, waiting, events, sigs = [], [], [], []
    for n_, zi in enumerate(sel):
        d, k = int(zd[zi]), int(zk[zi])
        if b0[n_] != d or not (dpos[n_] <= 0.5):
            continue                                              # never a REF POI (bias at creation / not discount-premium)
        base = dict(kind=zkind[zi], tf="M30", d=d, side="BUY" if d == 1 else "SELL", bottom=float(zlo[zi]),
                    top=float(zhi[zi]), created=ts(tcr[zi]), disc_prem_pos=round(float(dpos[n_]), 3))
        kexp = k + ZONE_LIFE
        if kexp < nreal:
            texp = M30["tc"][kexp]; e = int(np.searchsorted(t5, texp, "left"))
            base["expires"] = ts(texp); left = 0
        else:
            e = n5; left = kexp - nreal + 1                       # M30 bars (incl. the forming one) still valid
            base["expires"] = ts(np.datetime64(int(cur_close + 30 * (left - 1)), "m")) + " (est.)"
        base["m30_bars_left"] = left
        a = int(np.searchsorted(t5, tcr[zi], "left"))
        hit = (lo5[a:e] <= zhi[zi]) if d == 1 else (hi5[a:e] >= zlo[zi])
        if not hit.any():
            if left > 0:
                dist = (price - zhi[zi]) if d == 1 else (zlo[zi] - price)
                pois.append({**base, "dist_pips": round(float(dist / P), 1)})
            continue
        it = a + int(np.argmax(hit))
        t_touch = t5[it]
        z = {**base, "t_touch": ts(t_touch)}
        if int(R.bias_at(ctx, t_touch, CANON["ema_warm"])[0]) != d:
            events.append(dict(t_event=ts(tc5[it]), event="rejected", reason="bias changed at POI touch", **z))
            continue
        w = ev[it:min(it + CHOCH_WAIT + 1, n5)]
        cj = np.where(w == 2 * d)[0]
        if not len(cj):
            if it + CHOCH_WAIT > iT:
                ext = lo5[it:].min() if d == 1 else hi5[it:].max()
                waiting.append({**z, "bars_left": int(it + CHOCH_WAIT - iT), "extreme_since_touch": float(ext)})
            else:
                events.append(dict(t_event=ts(tc5[it + CHOCH_WAIT]), event="expired",
                                   reason="no 5M CHoCH within 36 bars of the touch", **z))
            continue
        j = it + int(cj[0])
        t_sig = tc5[j]
        z["t_choch"] = ts(t_sig); z["bars_to_choch"] = int(j - it)
        if t_sig < s0:
            continue
        rej = []
        bj = int(R.bias_at(ctx, t_sig, CANON["ema_warm"])[0])
        if bj != d:
            rej.append(f"bias changed by CHoCH close (now {bj:+d})")
        else:
            kz = R.killzone(t_sig, CANON["kz"])
            dow = pd.Timestamp(t_sig).dayofweek
            if kz not in ("LDN", "NY"):
                rej.append(f"CHoCH {str(t_sig)[11:16]} UTC outside London/NY killzone")
            if dow not in (1, 2, 3, 4):
                rej.append(f"not Tue-Fri ({pd.Timestamp(t_sig).day_name()[:3]})")
        ez = None
        if not rej:
            ez = B.ltf_entry_zone(M5, j, d, int(br[j]))
            if ez is None:
                rej.append("no LTF entry zone")
            elif CANON["pullback"] and (ez["entry"] - c5[j]) * d >= 0:
                rej.append("limit entry is not a pullback from the CHoCH close")
        if not rej:
            entry = float(ez["entry"])
            ext = lo5[it:j + 1].min() if d == 1 else hi5[it:j + 1].max()
            at = M5["atr"][j]
            buf = 0.1 * (at if at == at else 0.0)
            sl = ext - d * buf
            risk = (entry - sl) * d
            if not risk > 0:
                rej.append("stop on the wrong side of entry")
            elif risk / P < MIN_STOP_COMM * cm:
                rej.append(f"stop too small: {risk / P:.1f} pips < 10 x commission ({MIN_STOP_COMM * cm:.1f} pips)")
        if not rej:
            liq = [x for x in B.liquidity(ctx, t_sig, d) if (x - entry) * d > 0]
            if not liq:
                rej.append("no unbroken H1 swing beyond entry (no target)")
            else:
                tp = float(liq[0]); rr = (tp - entry) * d / risk
                if rr < MIN_RR:
                    rej.append(f"RR {rr:.2f} < 2 (TP {fmt_px(sym, tp)} nearest unbroken H1 swing)")
        if rej:
            events.append(dict(t_event=ts(t_sig), event="rejected", reason="; ".join(rej), **z))
            continue
        # ---- signal (fields for a human)
        a1 = int(np.searchsorted(H1["tc"], t_sig, "right") - 1)
        arr = H1["sh"] if d == 1 else H1["sl"]
        lv = H1["h"] if d == 1 else H1["l"]
        cand = np.where(arr[max(0, a1 - 150):a1 - H1["N"] + 1] & (lv[max(0, a1 - 150):a1 - H1["N"] + 1] == tp))[0]
        t_sw = H1["t"][max(0, a1 - 150) + cand[-1]] if len(cand) else None
        stop_p = risk / P
        sigs.append(dict(
            pair=sym, t_signal=ts(t_sig), side=d, side_txt="BUY" if d == 1 else "SELL", order="LIMIT", entry=entry,
            entry_why=f"proximal edge of the 5M {ez['ez_kind']} of the CHoCH leg ({fmt_px(sym, ez['ez_lo'])}-"
                      f"{fmt_px(sym, ez['ez_hi'])}, bar {ts(M5['t'][ez['ez_i']])})",
            sl=float(sl), sl_why=f"{'lowest low' if d == 1 else 'highest high'} since POI touch {fmt_px(sym, ext)} "
                                 f"{'-' if d == 1 else '+'} 0.1 x ATR14(M5,TR) {fmt_px(sym, buf)}",
            tp=tp, tp_why=f"nearest unbroken H1 swing {'high' if d == 1 else 'low'} (N=5, 150-bar lookback)"
                          + (f" formed {ts(t_sw)}" if t_sw is not None else ""),
            rr=round(float(rr), 2), stop_pips=round(float(stop_p), 1), comm_pips=cm,
            stop_x_comm=round(float(stop_p / cm), 1), valid_until=ts(t_sig + np.timedelta64(LIMIT_MIN, "m")),
            killzone=R.killzone(t_sig, CANON["kz"]), weekday=pd.Timestamp(t_sig).day_name()[:3], killzone_ok=True,
            poi_kind=z["kind"], poi_bottom=z["bottom"], poi_top=z["top"], poi_created=z["created"],
            t_touch=z["t_touch"], bars_to_choch=z["bars_to_choch"], entry_src=ez["ez_kind"],
            cancel_if="TP trades before the limit fills, or unfilled at valid_until",
            backtest_note="backtest skips it if a previous REF trade on this pair is still open"))
        events.append(dict(t_event=ts(t_sig), event="signal", reason="", **z))
    # dedupe exactly like build_orders: one order per t_signal (earliest touch, then zone_kind, then zone order)
    if sigs:
        df = pd.DataFrame(sigs)
        df["_k"] = pd.to_datetime(df.t_signal); df["_t"] = pd.to_datetime(df.t_touch)
        df = df.sort_values(["_k", "_t", "poi_kind"], kind="stable")
        keep = ~df._k.duplicated()
        for i in df.index[~keep]:
            events.append(dict(t_event=df.at[i, "t_signal"], event="deduped",
                               reason="same CHoCH already signalled via another POI", poi_kind=df.at[i, "poi_kind"],
                               created=df.at[i, "poi_created"]))
        sigs = [sigs[i] for i in df.index[keep]]
    events.sort(key=lambda e: e["t_event"])
    return dict(price=price, bias=bias, pois=sorted(pois, key=lambda z: z["dist_pips"]), waiting=waiting,
                events_recent=events, _signals_all=sigs)
