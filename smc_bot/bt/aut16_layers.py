"""AUT16 - the live entry split into three layers (trend / pullback / trigger), one layer swapped or stacked at a time.

Owner's request (2026-10-16): keep the live sniper's idea but test other methods inside each layer, and stack two or
three checks in the trend layer, to find an entry with a real edge. Gold only (XAUUSD), duka feed, three open windows
(EXPLORE 2009-2010, VALIDATE 2011-2016, STRESS 2017-2020). EXAM stays sealed.

Layers
  TREND    last CLOSED bar of M15 or M30:   EMA50>EMA200 (live) | close vs EMA200 | RSI14 vs 50 | market structure
           (higher highs + higher lows) | trend line through the last two swing points | any stack that all agree
  PULLBACK touch of an average (M1 20/50/200, M5 20/50, M15 20) | Fibonacci 38.2-78.6 of the last M15 leg |
           RSI gate | none
  TRIGGER  M1 close in trend direction (live) | close beyond prior bar | break of prior 3 bars | M5 candle closes with
           the trend | close back through the M1 EMA20

Every decision uses bars that had CLOSED. Orders enter at the next M1 open. Cost is paid once inside common.simulate.
Two exits per variant: the live +$15/-$70 (net of commission, fill-anchored) and a symmetric 2xATR(M15)/2xATR target/stop
(which isolates the entry's direction call). A coin-flip control at the same minutes is run on the symmetric exit.
Trades are taken one at a time, like the live bot.

Usage: python aut16_layers.py phase1 STAGE   |   python aut16_layers.py phase2 STAGE
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("SMC_DATASET", "duka")

import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import common as C                                                        # noqa: E402
import autopsy_lib as A                                                   # noqa: E402
import aut01_emapull as M                                                 # noqa: E402

SYM = "XAUUSD"
OUTD = os.path.join(C.RESULTS, "AUT16_layers")
os.makedirs(OUTD, exist_ok=True)
COMM = 1.10
WARM = 700


# ------------------------------------------------------------------ causal swing machinery
def swing_series(x, n, high=True):
    """Confirmed swing points. Returns (p1, j1, p2, j2) arrays: latest and previous confirmed swing price and bar index,
    known only from bar j+n onward (so nothing leaks)."""
    s = pd.Series(x if high else -x)
    left = s.shift(1).rolling(n).max()
    right = s[::-1].shift(1).rolling(n).max()[::-1]
    is_sw = ((s > left) & (s >= right)).values
    ev_j = np.flatnonzero(is_sw)
    ev_c = ev_j + n
    keep = ev_c < len(x)
    ev_j, ev_c = ev_j[keep], ev_c[keep]
    N = len(x)
    p1, j1, p2, j2 = (np.full(N, np.nan) for _ in range(4))
    p1[ev_c] = x[ev_j]
    j1[ev_c] = ev_j
    if len(ev_j) > 1:
        p2[ev_c[1:]] = x[ev_j[:-1]]
        j2[ev_c[1:]] = ev_j[:-1]
    return tuple(pd.Series(a).ffill().values for a in (p1, j1, p2, j2))


def htf_features(df, n=3):
    c, h, l = df.close.values, df.high.values, df.low.values
    N = len(df)
    ema50, ema200 = M._ema(c, 50), M._ema(c, 200)
    rsi = M._rsi(c, 14)
    sh = swing_series(h, n, True)
    sl = swing_series(l, n, False)
    sh1p, sh1j, sh2p, sh2j = sh
    sl1p, sl1j, sl2p, sl2j = sl
    idx = np.arange(N)
    T = {}
    T["EMA"] = np.where(ema50 > ema200, 1, -1)
    T["PX200"] = np.where(c > ema200, 1, -1)
    T["RSI"] = np.where(rsi > 50, 1, np.where(rsi < 50, -1, 0))
    up_s = (sh1p > sh2p) & (sl1p > sl2p)
    dn_s = (sh1p < sh2p) & (sl1p < sl2p)
    T["STRUCT"] = np.where(up_s, 1, np.where(dn_s, -1, 0))
    with np.errstate(invalid="ignore", divide="ignore"):
        up_line = sl1p + (sl1p - sl2p) / (sl1j - sl2j) * (idx - sl1j)
        dn_line = sh1p + (sh1p - sh2p) / (sh1j - sh2j) * (idx - sh1j)
    up_t = (sl1p > sl2p) & (c > up_line)
    dn_t = (sh1p < sh2p) & (c < dn_line)
    T["TLINE"] = np.where(up_t & ~dn_t, 1, np.where(dn_t & ~up_t, -1, 0))
    for k in T:
        T[k] = np.where(idx < 260, 0, T[k])
    # Fibonacci legs (used on M15 only)
    Lw = np.where(sl1j < sh1j, sl1p, np.where(sl2j < sh1j, sl2p, np.nan))
    Hw = np.where(sh1j < sl1j, sh1p, np.where(sh2j < sl1j, sh2p, np.nan))
    return dict(T=T, H=sh1p, Lw=Lw, Lo=sl1p, Hw=Hw, ema=dict(e20=M._ema(c, 20), e50=ema50), atr=A.atr(df, 14).values)


def combine(arrs):
    a = np.vstack(arrs)
    return np.where((a == 1).all(0), 1, np.where((a == -1).all(0), -1, 0))


# ------------------------------------------------------------------ per-stage feature build (once)
def build_stage(stage):
    a, z = M.set_window(stage, cost="real")
    m1 = C.bars(SYM, "M1")
    n1 = len(m1)
    o, h, l, c = m1.open.values, m1.high.values, m1.low.values, m1.close.values
    tc = m1.tclose.values
    F = dict(a=a, z=z, m1=m1, o=o, h=h, l=l, c=c, tc=tc, n=n1)
    F["e20"], F["e50"], F["e200"] = M._ema(c, 20), M._ema(c, 50), M._ema(c, 200)
    F["rsi"] = M._rsi(c, 14)
    # higher timeframes mapped to the last closed bar at each M1 close
    tr, fib, mavg = {}, {}, {}
    for tf in ("M15", "M30"):
        df = C.bars(SYM, tf)
        f = htf_features(df)
        j = np.clip(C.asof_index(df, tc), 0, len(df) - 1)
        ok = C.asof_index(df, tc) >= 260
        T = f["T"]
        base = {k: np.where(ok, v[j], 0) for k, v in T.items()}
        # stacks
        names = {"EMA": ["EMA"], "PX200": ["PX200"], "RSI": ["RSI"], "STRUCT": ["STRUCT"], "TLINE": ["TLINE"],
                 "EMA+RSI": ["EMA", "RSI"], "EMA+TLINE": ["EMA", "TLINE"], "EMA+STRUCT": ["EMA", "STRUCT"],
                 "TLINE+RSI": ["TLINE", "RSI"], "STRUCT+RSI": ["STRUCT", "RSI"],
                 "EMA+STRUCT+RSI": ["EMA", "STRUCT", "RSI"], "EMA+TLINE+RSI": ["EMA", "TLINE", "RSI"],
                 "STRUCT+TLINE": ["STRUCT", "TLINE"]}
        for nm, parts in names.items():
            tr[(tf, nm)] = combine([base[p] for p in parts])
        if tf == "M15":
            H, Lw, Lo, Hw = (np.where(ok, f[k][j], np.nan) for k in ("H", "Lw", "Lo", "Hw"))
            at = np.where(ok, f["atr"][j], np.nan)
            mavg["M15_e20"] = np.where(ok, f["ema"]["e20"][j], np.nan)
            F["atr15"] = at
            leg_up, leg_dn = H - Lw, Hw - Lo
            with np.errstate(invalid="ignore"):
                buy_zone = (leg_up >= at) & (l <= H - 0.382 * leg_up) & (l >= H - 0.786 * leg_up)
                sell_zone = (leg_dn >= at) & (h >= Lo + 0.382 * leg_dn) & (h <= Lo + 0.786 * leg_dn)
            fib["b"], fib["s"] = np.nan_to_num(buy_zone, nan=False).astype(bool), np.nan_to_num(sell_zone, nan=False).astype(bool)
    F["tr"], F["fib"] = tr, fib
    b5 = C.bars(SYM, "M5")
    c5 = b5.close.values
    j5 = np.clip(C.asof_index(b5, tc), 0, len(b5) - 1)
    ok5 = C.asof_index(b5, tc) >= 60
    F["m5_e20"] = np.where(ok5, M._ema(c5, 20)[j5], np.nan)
    F["m5_e50"] = np.where(ok5, M._ema(c5, 50)[j5], np.nan)
    F["m15_e20"] = mavg["M15_e20"]
    minute = pd.to_datetime(tc).minute.values
    F["is5"] = (minute % 5 == 0)
    F["days"] = int(pd.Series(m1.time[(m1.time >= a) & (m1.time < z)].dt.date).nunique())
    F["in"] = (tc >= np.datetime64(a)) & (tc < np.datetime64(z))
    return F


def shift(x, k):
    out = np.empty_like(x)
    out[:k] = False if x.dtype == bool else np.nan
    out[k:] = x[:-k] if k else x
    return out


def mem(mask, k):
    if k <= 0:
        return mask
    return pd.Series(mask.astype(np.int8)).rolling(k + 1, min_periods=1).max().values > 0


# pullback specs: name -> (touch_buy, touch_sell, needs_rsi)
def pullback(F, name):
    l, h = F["l"], F["h"]
    with np.errstate(invalid="ignore"):
        if name == "P0_E20_RSI":
            return l <= F["e20"], h >= F["e20"], True
        if name == "P1_E20":
            return l <= F["e20"], h >= F["e20"], False
        if name == "P2_E50":
            return l <= F["e50"], h >= F["e50"], False
        if name == "P3_E200":
            return l <= F["e200"], h >= F["e200"], False
        if name == "P4_M5E20":
            return l <= F["m5_e20"], h >= F["m5_e20"], False
        if name == "P5_M5E50":
            return l <= F["m5_e50"], h >= F["m5_e50"], False
        if name == "P6_M15E20":
            return l <= F["m15_e20"], h >= F["m15_e20"], False
        if name == "P7_FIB":
            return F["fib"]["b"], F["fib"]["s"], False
        if name == "P8_E20_FIB":
            return (l <= F["e20"]) & F["fib"]["b"], (h >= F["e20"]) & F["fib"]["s"], False
        if name == "P9_RSI":
            return np.ones(F["n"], bool), np.ones(F["n"], bool), True
        if name == "P10_NONE":
            return np.ones(F["n"], bool), np.ones(F["n"], bool), False
    raise KeyError(name)


def trigger(F, name):
    o, h, l, c = F["o"], F["h"], F["l"], F["c"]
    ph = pd.Series(h).shift(1).values
    pl = pd.Series(l).shift(1).values
    with np.errstate(invalid="ignore"):
        if name == "G0_BODY":
            return c > o, c < o, 0
        if name == "G1_ENG":
            return (c > ph) & (c > o), (c < pl) & (c < o), 3
        if name == "G2_BRK3":
            return c > pd.Series(h).shift(1).rolling(3).max().values, c < pd.Series(l).shift(1).rolling(3).min().values, 3
        if name == "G3_M5CLOSE":
            o5 = pd.Series(o).shift(4).values
            return F["is5"] & (c > o5), F["is5"] & (c < o5), 4
        if name == "G4_BODYHOLD":
            return (c > o) & (c > F["e20"]), (c < o) & (c < F["e20"]), 0
    raise KeyError(name)


def make_signals(F, trend, pull, trig):
    tf, tn = trend
    d = F["tr"][(tf, tn)]
    tb, ts, needs_rsi = pullback(F, pull)
    gb, gs, k = trigger(F, trig)
    rb = (F["rsi"] <= 45.0) if needs_rsi else np.ones(F["n"], bool)
    rs = (F["rsi"] >= 55.0) if needs_rsi else np.ones(F["n"], bool)
    sb = (d == 1) & mem(tb, k) & gb & rb & F["in"]
    ss = (d == -1) & mem(ts, k) & gs & rs & F["in"]
    sb[:WARM] = False
    ss[:WARM] = False
    sb[-20:] = False
    ss[-20:] = False
    i = np.flatnonzero(sb | ss)
    side = np.where(sb[i], 1, -1)
    return i, side


def make_orders(F, i, side, exit_kind):
    m1 = F["m1"]
    nxt = np.clip(i + 1, 0, F["n"] - 1)
    px = F["o"][nxt] + np.where(side == 1, m1.spr.values[nxt], 0.0)
    if exit_kind == "LIVE":
        sld, tpd = (70.0 - COMM) / 10.0, (15.0 + COMM) / 10.0
        sl, tp = px - side * sld, px + side * tpd
    else:
        at = F["atr15"][i]
        sl, tp = px - side * 2.0 * at, px + side * 2.0 * at
    o = pd.DataFrame({"t_signal": F["tc"][i], "side": side, "entry_type": "market", "entry": np.nan,
                      "expire": F["tc"][i], "sl": sl, "tp": tp, "be_at_r": np.nan, "max_hold_min": 1440.0})
    return o[np.isfinite(o.sl) & np.isfinite(o.tp)].reset_index(drop=True)


def stats(r, days):
    r = r[r.status == "filled"]
    n = len(r)
    if n == 0:
        return dict(fills=0)
    u = r.usd_01.values
    w, ls = u[u > 0], u[u <= 0]
    pf = w.sum() / -ls.sum() if len(ls) and ls.sum() < 0 else np.nan
    sd = u.std(ddof=1) if n > 1 else np.nan
    return dict(fills=n, per_day=round(n / days, 2), win_pct=round(100 * (u > 0).mean(), 1), pf=round(pf, 2),
                net=round(u.sum(), 0), usd_trade=round(u.mean(), 3), give_take=round(1.96 * sd / np.sqrt(n), 2),
                avg_win=round(w.mean(), 2) if len(w) else 0.0, avg_loss=round(ls.mean(), 2) if len(ls) else 0.0,
                buy_n=int((r.side == 1).sum()), buy_usd=round(u[r.side.values == 1].mean(), 3) if (r.side == 1).any() else np.nan,
                sell_n=int((r.side == -1).sum()), sell_usd=round(u[r.side.values == -1].mean(), 3) if (r.side == -1).any() else np.nan)


def run_variant(F, name, trend, pull, trig, stage):
    i, side = make_signals(F, trend, pull, trig)
    rows = []
    if len(i) == 0:
        return rows
    for ex in ("LIVE", "ATR22"):
        o = make_orders(F, i, side, ex)
        r = C.simulate(SYM, o, one_at_a_time=True)
        st = stats(r, F["days"])
        st.update(variant=name, stage=stage, exit=ex, trend=f"{trend[0]} {trend[1]}", pull=pull, trig=trig, signals=len(i))
        if ex == "ATR22":
            rng = np.random.default_rng(7)
            cs = rng.choice([1, -1], size=len(i))
            oc = make_orders(F, i, cs, ex)
            rc = C.simulate(SYM, oc, one_at_a_time=True)
            cst = stats(rc, F["days"])
            st["coin_usd"] = cst.get("usd_trade", np.nan)
            st["coin_win"] = cst.get("win_pct", np.nan)
        rows.append(st)
    return rows


# ------------------------------------------------------------------ variant lists (fixed BEFORE running)
BASE = (("M15", "EMA"), "P0_E20_RSI", "G0_BODY")
TREND_NAMES = ["EMA", "PX200", "RSI", "STRUCT", "TLINE", "EMA+RSI", "EMA+TLINE", "EMA+STRUCT", "TLINE+RSI", "STRUCT+RSI",
               "EMA+STRUCT+RSI", "EMA+TLINE+RSI", "STRUCT+TLINE"]
PULLS = ["P1_E20", "P2_E50", "P3_E200", "P4_M5E20", "P5_M5E50", "P6_M15E20", "P7_FIB", "P8_E20_FIB", "P9_RSI", "P10_NONE"]
TRIGS = ["G1_ENG", "G2_BRK3", "G3_M5CLOSE", "G4_BODYHOLD"]


def phase1_list():
    v = [("BASE_live", *BASE)]
    for tf in ("M15", "M30"):
        for tn in TREND_NAMES:
            if (tf, tn) == ("M15", "EMA"):
                continue
            v.append((f"T_{tf}_{tn}", (tf, tn), BASE[1], BASE[2]))
    for p in PULLS:
        v.append((f"P_{p}", BASE[0], p, BASE[2]))
    for g in TRIGS:
        v.append((f"G_{g}", BASE[0], BASE[1], g))
    return v


def run(phase, stage):
    t0 = time.time()
    F = build_stage(stage)
    print(stage, "features built", round(time.time() - t0), "s; days", F["days"], flush=True)
    if phase == "phase1":
        variants = phase1_list()
    else:
        variants = [tuple(x) if not isinstance(x[1], list) else (x[0], tuple(x[1]), x[2], x[3])
                    for x in json.load(open(os.path.join(OUTD, "phase2_variants.json")))]
        variants = [(n, tuple(t), p, g) for n, t, p, g in variants]
    out = []
    for name, trend, pull, trig in variants:
        t1 = time.time()
        rows = run_variant(F, name, trend, pull, trig, stage)
        out += rows
        print(name, [(r["exit"], r.get("fills"), r.get("usd_trade")) for r in rows], round(time.time() - t1), "s", flush=True)
        pd.DataFrame(out).to_csv(os.path.join(OUTD, f"{phase}_{stage}.csv"), index=False)
    print("done", round(time.time() - t0), "s", flush=True)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
