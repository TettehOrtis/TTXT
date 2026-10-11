"""BT1 - LIQUIDITY SWEEP.  Does trading the reversal after a liquidity sweep make money after real costs?

Sweep (smc-strategy.md 2.4 / 5.1): a candle wicks BEYOND a previous (confirmed, still untaken) liquidity level and CLOSES back
inside.  High swept -> sell.  Low swept -> buy.  Comparison: the candle CLOSES beyond the level (acceptance = breakout) or a sweep
fails later (price closes beyond the sweep wick) -> trade the continuation.

Liquidity levels (all causal):
  H1  : H1 fractal swing high/low, N=5, known at the close of the 5th bar after it; alive 5 days or until first traded through
  H4  : H4 fractal swing, N=3; alive 10 days
  PD  : previous trading day's high/low (day boundary 22:00 UTC = NY close); alive during the current trading day
  AS  : Asian range (00:00-07:00 UTC) high/low; alive 07:00-16:00 UTC the same day
  EQ  : equal highs/lows: two H1 swings (N=3) within 0.1*ATR(H1) of each other, not broken between them; alive 5 days
A level is used only ONCE: the first sweep-TF bar that trades beyond it either sweeps it (close back inside) or breaks it.

Usage (from TTXT root):
  .venv/Scripts/python.exe smc_bot/bt/bt1_sweep.py dev            # all variants on DEV
  .venv/Scripts/python.exe smc_bot/bt/bt1_sweep.py val V07 V11    # chosen variants (max 3) on VAL
  .venv/Scripts/python.exe smc_bot/bt/bt1_sweep.py replay V07     # hand replay of 3 trades on raw M1
TEST (2025-07+) is never generated.
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (bars, structure, swings, ema, atr, asof_index, simulate, stats, report_table, save, PERIODS, PAIRS,  # noqa
                    pip, COMM, RESULTS)

OUT = os.path.join(RESULTS, "BT1")
os.makedirs(OUT, exist_ok=True)
TFM = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
_BC = {}


def B(sym, tf):
    if (sym, tf) not in _BC:
        _BC[(sym, tf)] = bars(sym, tf)
    return _BC[(sym, tf)]


# ------------------------------------------------------------------ liquidity levels
def _swing_levels(sym, tf, N, age_days, tag):
    b = B(sym, tf)
    h, l, tc = b.high.values, b.low.values, b.tclose.values
    is_sh, is_sl = swings(h, l, N)
    rows = []
    for s in np.where(is_sh)[0]:
        if s + N < len(b):
            rows.append((1, h[s], tc[s + N], b.time.values[s]))
    for s in np.where(is_sl)[0]:
        if s + N < len(b):
            rows.append((-1, l[s], tc[s + N], b.time.values[s]))
    z = pd.DataFrame(rows, columns=["lside", "L", "t_start", "t_origin"])
    z["t_end"] = z.t_start + pd.Timedelta(days=age_days)
    z["src"] = tag
    return z


def _trading_day(t):
    td = (t + pd.Timedelta(hours=2)).dt.floor("D")
    return td.where(td.dt.dayofweek != 6, td + pd.Timedelta(days=1))  # Sunday evening belongs to Monday


def _pd_levels(sym):
    b = B(sym, "H1").copy()
    b["td"] = _trading_day(b.time)
    g = b.groupby("td").agg(hi=("high", "max"), lo=("low", "min"), t0=("time", "min")).reset_index()
    rows = []
    for k in range(1, len(g)):
        ts, te = g.t0.iloc[k], (g.t0.iloc[k + 1] if k + 1 < len(g) else g.t0.iloc[k] + pd.Timedelta(days=1))
        rows.append((1, g.hi.iloc[k - 1], ts, te, g.td.iloc[k - 1]))
        rows.append((-1, g.lo.iloc[k - 1], ts, te, g.td.iloc[k - 1]))
    z = pd.DataFrame(rows, columns=["lside", "L", "t_start", "t_end", "t_origin"])
    z["src"] = "PD"
    return z


def _asian_levels(sym):
    b = B(sym, "M15")
    a = b[b.time.dt.hour < 7].copy()
    a = a[a.time.dt.dayofweek < 5]
    a["d"] = a.time.dt.floor("D")
    g = a.groupby("d").agg(hi=("high", "max"), lo=("low", "min"), n=("high", "size")).reset_index()
    g = g[g.n >= 20]                                                   # a real Asian session (>= 5h of data)
    rows = []
    for r in g.itertuples():
        ts, te = r.d + pd.Timedelta(hours=7), r.d + pd.Timedelta(hours=16)
        rows.append((1, r.hi, ts, te, r.d)); rows.append((-1, r.lo, ts, te, r.d))
    z = pd.DataFrame(rows, columns=["lside", "L", "t_start", "t_end", "t_origin"])
    z["src"] = "AS"
    return z


def _eq_levels(sym, N=3, tol_atr=0.1, look=120, age_days=5):
    b = B(sym, "H1")
    h, l, tc, t = b.high.values, b.low.values, b.tclose.values, b.time.values
    a = atr(b)
    is_sh, is_sl = swings(h, l, N)
    rows = []
    for side, is_s, x in ((1, is_sh, h), (-1, is_sl, l)):
        idx = np.where(is_s)[0]
        for j, s2 in enumerate(idx):
            if s2 + N >= len(b) or np.isnan(a[s2]):
                continue
            for s1 in idx[:j][::-1]:
                if s2 - s1 > look:
                    break
                if s2 - s1 < N or abs(x[s1] - x[s2]) > tol_atr * a[s2]:
                    continue
                lvl = max(x[s1], x[s2]) if side == 1 else min(x[s1], x[s2])
                mid = x[s1 + 1:s2]
                if (side == 1 and mid.max() <= lvl) or (side == -1 and mid.min() >= lvl):
                    rows.append((side, lvl, tc[s2 + N], t[s1]))
                    break
    z = pd.DataFrame(rows, columns=["lside", "L", "t_start", "t_origin"])
    z["t_end"] = z.t_start + pd.Timedelta(days=age_days)
    z["src"] = "EQ"
    return z


from functools import lru_cache


@lru_cache(maxsize=None)
def levels(sym, src):
    return _levels(sym, src).copy()


def _levels(sym, src):
    if src == "H1":
        return _swing_levels(sym, "H1", 5, 5, "H1")
    if src == "H4":
        return _swing_levels(sym, "H4", 3, 10, "H4")
    if src == "PD":
        return _pd_levels(sym)
    if src == "AS":
        return _asian_levels(sym)
    if src == "EQ":
        return _eq_levels(sym)
    raise ValueError(src)


# ------------------------------------------------------------------ sweep / break events on the sweep timeframe
@lru_cache(maxsize=None)
def events(sym, srcs, tf):
    """One row per sweep-TF bar that first trades beyond >=1 alive level. kind: 'sweep' (close back inside) or 'break'."""
    b = B(sym, tf)
    t, h, l, c = b.time.values, b.high.values, b.low.values, b.close.values
    z = pd.concat([levels(sym, s) for s in srcs], ignore_index=True)
    i0 = np.searchsorted(t, z.t_start.values.astype("datetime64[ns]"), "left")
    i1 = np.searchsorted(t, z.t_end.values.astype("datetime64[ns]"), "left")
    rec = []
    for k in range(len(z)):
        a, e, L, sd = i0[k], i1[k], z.L.values[k], z.lside.values[k]
        if e <= a:
            continue
        hit = (h[a:e] > L) if sd == 1 else (l[a:e] < L)
        if not hit.any():
            continue
        i = a + int(np.argmax(hit))
        sweep = (c[i] < L) if sd == 1 else (c[i] > L)
        rec.append((i, sd, L, "sweep" if sweep else "break", z.src.values[k], z.t_origin.values[k]))
    ev = pd.DataFrame(rec, columns=["i", "lside", "L", "kind", "src", "t_origin"])
    if ev.empty:
        return ev
    both = ev.groupby("i").lside.nunique()
    ev = ev[~ev.i.isin(both[both > 1].index)]                      # outside bar sweeping both sides: ambiguous, dropped
    out = []
    for (i, sd), g in ev.groupby(["i", "lside"]):
        sw = g[g.kind == "sweep"]
        if len(sw):                                                  # reversal: most extreme level that was swept
            r = sw.loc[sw.L.idxmax() if sd == 1 else sw.L.idxmin()]
            out.append((i, sd, r.L, "sweep", "+".join(sorted(set(sw.src))), r.t_origin))
        else:
            r = g.loc[g.L.idxmax() if sd == 1 else g.L.idxmin()]   # breakout: the furthest level closed beyond
            out.append((i, sd, r.L, "break", "+".join(sorted(set(g.src))), r.t_origin))
    ev = pd.DataFrame(out, columns=["i", "lside", "L", "kind", "src", "t_origin"])
    ev["t_bar"] = t[ev.i.values]
    ev["t_close"] = b.tclose.values[ev.i.values]
    ev["o"], ev["h"], ev["l"], ev["c"] = b.open.values[ev.i], h[ev.i], l[ev.i], c[ev.i]
    ev["atr"] = atr(b)[ev.i.values]
    return ev.sort_values("i").reset_index(drop=True)


# ------------------------------------------------------------------ opposite liquidity pool (for TP='liq')
@lru_cache(maxsize=None)
def liq_pool(sym):
    """H1 swing levels + PDH/PDL with the time each is consumed (first M15 bar trading through it)."""
    z = pd.concat([levels(sym, "H1"), levels(sym, "PD")], ignore_index=True)
    b = B(sym, "M15")
    t, h, l, tc = b.time.values, b.high.values, b.low.values, b.tclose.values
    i0 = np.searchsorted(t, z.t_start.values.astype("datetime64[ns]"), "left")
    i1 = np.searchsorted(t, z.t_end.values.astype("datetime64[ns]"), "left")
    dead = np.empty(len(z), "datetime64[ns]")
    for k in range(len(z)):
        a, e = i0[k], i1[k]
        hit = (h[a:e] > z.L.values[k]) if z.lside.values[k] == 1 else (l[a:e] < z.L.values[k])
        dead[k] = tc[a + int(np.argmax(hit))] if (e > a and hit.any()) else z.t_end.values[k]
    z["t_dead"] = dead
    return z


# ------------------------------------------------------------------ variants
BASE = dict(src=("H1",), tf="M15", mode="rev", entry="market", tp="R", rr=2.0, conf=None, htf=None, sess=(7, 17),
            hold_h=12, buf=0.1, min_risk=0.3, retrace_bars=4, conf_wait=24)
VARIANTS = {
    # --- which liquidity (M15 sweep candle, market entry at its close, SL beyond wick, TP 2R, London+NY)
    "V01_H1swing_M15": dict(),
    "V02_H4swing_M15": dict(src=("H4",)),
    "V03_PDHL_M15": dict(src=("PD",)),
    "V04_Asian_M15": dict(src=("AS",)),
    "V05_EQHL_M15": dict(src=("EQ",)),
    "V06_AllLiq_M15": dict(src=("H1", "H4", "PD", "AS", "EQ")),
    # --- which sweep timeframe
    "V07_AllLiq_M30": dict(src=("H1", "H4", "PD", "AS", "EQ"), tf="M30", hold_h=24),
    "V08_HTFliq_H1": dict(src=("H4", "PD"), tf="H1", hold_h=48),
    # --- entry / confirmation
    "V09_retrace": dict(src=("H1", "H4", "PD", "AS", "EQ"), entry="retrace"),
    "V10_M5choch": dict(src=("H1", "H4", "PD", "AS", "EQ"), conf="M5"),
    # --- targets
    "V11_TP1R": dict(src=("H1", "H4", "PD", "AS", "EQ"), rr=1.0),
    "V12_TP3R": dict(src=("H1", "H4", "PD", "AS", "EQ"), rr=3.0, hold_h=24),
    "V13_TPliq": dict(src=("H1", "H4", "PD", "AS", "EQ"), tp="liq", hold_h=48),
    # --- context
    "V14_HTFwith": dict(src=("H1", "H4", "PD", "AS", "EQ"), htf="with"),
    "V15_HTFagainst": dict(src=("H1", "H4", "PD", "AS", "EQ"), htf="against"),
    "V16_allhours": dict(src=("H1", "H4", "PD", "AS", "EQ"), sess=(0, 24)),
    # --- continuation comparison
    "C1_breakout": dict(src=("H1", "H4", "PD", "AS", "EQ"), mode="break"),
    "C2_failedsweep": dict(src=("H1", "H4", "PD", "AS", "EQ"), mode="fail"),
}


def cfg_of(name):
    c = dict(BASE); c.update(VARIANTS[name]); return c


@lru_cache(maxsize=None)
def _h1ctx(sym):
    h1 = B(sym, "H1")
    return structure(h1, 5), ema(h1.close.values, 200)


@lru_cache(maxsize=None)
def _m5st(sym):
    return structure(B(sym, "M5"), 3)


def htf_ctx(sym, t):
    """H1 context as of t: +1 if H1 structure trend up AND close > EMA200, -1 if both down, 0 mixed."""
    h1 = B(sym, "H1")
    st, e = _h1ctx(sym)
    k = asof_index(h1, t)
    tr = st["trend"][k]
    ab = np.where(h1.close.values[k] > e[k], 1, -1)
    return np.where((tr == 1) & (ab == 1), 1, np.where((tr == -1) & (ab == -1), -1, 0))


def build_orders(sym, name, period):
    c = cfg_of(name)
    a, z = PERIODS[period]
    ev = events(sym, tuple(c["src"]), c["tf"])
    ev = ev[(ev.t_close >= a) & (ev.t_close < z)]
    hr = pd.to_datetime(ev.t_bar).dt.hour
    ev = ev[(hr >= c["sess"][0]) & (hr < c["sess"][1])].reset_index(drop=True)
    tfb = B(sym, c["tf"])
    dt = pd.Timedelta(minutes=TFM[c["tf"]])
    P = pip(sym)
    rows = []
    if c["mode"] == "rev":
        ev = ev[ev.kind == "sweep"]
    elif c["mode"] == "break":
        ev = ev[ev.kind == "break"]
    else:
        ev = ev[ev.kind == "sweep"]
    if c["conf"]:
        m5 = B(sym, "M5"); st5 = _m5st(sym)
    if c["tp"] == "liq":
        pool = liq_pool(sym)
    for r in ev.itertuples(index=False):
        buf = c["buf"] * r.atr
        ext = r.h if r.lside == 1 else r.l                          # sweep wick extreme
        if c["mode"] == "rev":
            d = -r.lside
            t_sig, ref, etype, entry, expire = r.t_close, r.c, "market", np.nan, pd.NaT
            sl = ext + buf if d == -1 else ext - buf
            why_sl = f"beyond sweep wick {ext:.5f} + 0.1*ATR({c['tf']})"
            if c["conf"]:                                            # wait for an M5 close beyond the last M5 swing (CHoCH/BOS)
                s0 = np.searchsorted(m5.tclose.values, np.datetime64(r.t_close), "right")
                seg = slice(s0, min(s0 + c["conf_wait"], len(m5)))
                ok = st5["evt"][seg] * d > 0
                viol = (m5.high.values[seg] > ext) if d == -1 else (m5.low.values[seg] < ext)
                k_ok = int(np.argmax(ok)) if ok.any() else 10 ** 9
                k_v = int(np.argmax(viol)) if viol.any() else 10 ** 9
                if k_ok == 10 ** 9 or k_v <= k_ok:
                    continue
                k = s0 + k_ok
                t_sig, ref = m5.tclose.values[k], m5.close.values[k]
            elif c["entry"] == "retrace":                           # limit back at the swept level
                etype, entry, ref = "limit", r.L, r.L
                expire = pd.Timestamp(r.t_close) + c["retrace_bars"] * dt
            risk = (sl - ref) * -d if d == -1 else (ref - sl)
            if risk < c["min_risk"] * r.atr:
                continue
        elif c["mode"] == "break":                                   # acceptance beyond the level -> continuation
            d = r.lside
            t_sig, ref, etype, entry, expire = r.t_close, r.c, "market", np.nan, pd.NaT
            sl = r.L - buf if d == 1 else r.L + buf
            why_sl = f"back inside the broken level {r.L:.5f} - 0.1*ATR"
            risk = (ref - sl) * d
            if risk < c["min_risk"] * r.atr:
                continue
        else:                                                        # failed sweep: later close beyond the sweep wick
            d = r.lside
            i = r.i
            seg = slice(i + 1, min(i + 1 + 8, len(tfb)))
            cl = tfb.close.values[seg]
            go = (cl > ext) if d == 1 else (cl < ext)
            if not go.any():
                continue
            k = i + 1 + int(np.argmax(go))
            t_sig, ref, etype, entry, expire = tfb.tclose.values[k], tfb.close.values[k], "market", np.nan, pd.NaT
            sl = r.L - buf if d == 1 else r.L + buf
            why_sl = f"back inside the swept level {r.L:.5f} - 0.1*ATR"
            risk = (ref - sl) * d
            if risk < c["min_risk"] * r.atr:
                continue
        risk = abs(ref - sl)
        if c["tp"] == "R":
            tp = ref + d * c["rr"] * risk
            why_tp = f"fixed {c['rr']:g}R from reference price"
        else:
            tq = np.datetime64(t_sig)
            al = pool[(pool.t_start.values <= tq) & (pool.t_dead.values > tq) & (pool.lside.values == d)]
            cand = al.L.values[(al.L.values - ref) * d > 0]
            if not len(cand):
                continue
            tp = cand.min() if d == 1 else cand.max()                # nearest opposite liquidity
            if abs(tp - ref) < 2.0 * risk:                           # spec 5.5: skip if RR < 2
                continue
            why_tp = "nearest opposite liquidity (H1 swing / PDH-PDL)"
        rows.append(dict(t_signal=pd.Timestamp(t_sig), side=int(d), entry_type=etype, entry=entry, expire=expire, sl=sl, tp=tp,
                         be_at_r=np.nan, max_hold_min=c["hold_h"] * 60.0, sweep_bar=pd.Timestamp(r.t_bar), tf=c["tf"],
                         level=r.L, level_src=r.src, level_origin=pd.Timestamp(r.t_origin), wick_ext=ext,
                         bar_o=r.o, bar_h=r.h, bar_l=r.l, bar_c=r.c, atr_tf=r.atr,
                         depth_atr=abs(ext - r.L) / r.atr, ref=ref, risk_atr=risk / r.atr,
                         sl_reason=why_sl, tp_reason=why_tp))
    o = pd.DataFrame(rows)
    if o.empty:
        return o
    o["htf"] = htf_ctx(sym, o.t_signal.values) * o.side
    if c["htf"] == "with":
        o = o[o.htf == 1]
    elif c["htf"] == "against":
        o = o[o.htf == -1]
    return o.reset_index(drop=True)


def run(name, period):
    out = []
    for sym in PAIRS:
        o = build_orders(sym, name, period)
        if o.empty:
            continue
        out.append(simulate(sym, o))
    return pd.concat(out, ignore_index=True)


def md(df):
    """DataFrame -> markdown table (no tabulate dependency)."""
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def session_of(t):
    h = pd.to_datetime(t).dt.hour
    return np.select([h < 7, h < 12, h < 17], ["Asia", "London", "NY"], "Late")


def tables(tr):
    f = tr[tr.status == "filled"].copy()
    f["t_exit"] = pd.to_datetime(f.t_exit); f["t_fill"] = pd.to_datetime(f.t_fill)
    f = f.sort_values("t_exit")                                      # common.stats needs time order for a correct pooled maxDD
    f["session"] = session_of(f.sweep_bar)
    pooled = pd.DataFrame([{"sym": "POOLED", **stats(f)}])
    per = pd.DataFrame([{"sym": s, **stats(g)} for s, g in f.groupby("sym")])
    return pd.concat([pooled, per], ignore_index=True), f


# ------------------------------------------------------------------ independent hand replay on raw M1
def replay(trade):
    """Minute-by-minute re-simulation written independently of common.simulate (market entries only)."""
    m1 = bars(trade.sym, "M1")
    P = pip(trade.sym)
    d = int(trade.side)
    seg = m1[m1.time >= pd.Timestamp(trade.t_signal)].head(int(trade.max_hold_min) + 5).reset_index(drop=True)
    r0 = seg.iloc[0]
    fill = r0.open + r0.spr if d == 1 else r0.open
    log = [f"  fill minute {r0.time}  bid open {r0.open}  spr {r0.spr:.5f} -> fill {fill:.5f}"]
    for j in range(len(seg)):
        x = seg.iloc[j]
        bid_lo, bid_hi, ask_lo, ask_hi = x.low, x.high, x.low + x.spr, x.high + x.spr
        sl_hit = bid_lo <= trade.sl if d == 1 else ask_hi >= trade.sl
        tp_hit = (bid_hi >= trade.tp if d == 1 else ask_lo <= trade.tp) and j > 0
        if sl_hit:
            if d == 1:
                px = (min(trade.sl, x.open) if j > 0 else trade.sl) - 0.2 * P
            else:
                px = (max(trade.sl, x.open + x.spr) if j > 0 else trade.sl) + 0.2 * P
            log.append(f"  SL hit minute {x.time}: bid H/L {x.high}/{x.low} spr {x.spr:.5f} -> exit {px:.5f}"); break
        if tp_hit:
            px = trade.tp
            log.append(f"  TP hit minute {x.time}: bid H/L {x.high}/{x.low} -> exit {px:.5f}"); break
        if x.time.dayofweek == 4 and x.time.hour * 60 + x.time.minute >= 20 * 60 + 45:
            px = x.close if d == 1 else x.close + x.spr
            log.append(f"  WEEKEND exit {x.time} @ {px:.5f}"); break
        if j == int(trade.max_hold_min):
            px = x.close if d == 1 else x.close + x.spr
            log.append(f"  TIME exit {x.time} (minute #{j} after fill) @ {px:.5f}"); break
    net = (px - fill) * d / P - COMM[trade.sym]
    R = net / (abs(fill - trade.sl) / P)
    log.append(f"  replay: net {net:.2f} pips, R {R:.3f}   | harness: exit {trade.exit_reason} {trade.t_exit} @ {trade.exit:.5f}, "
               f"net {trade.net_pips:.2f}, R {trade.R:.3f}  -> {'MATCH' if abs(R - trade.R) < 1e-6 else 'DIFF'}")
    return "\n".join(log)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if cmd == "dev":
        names = sys.argv[2:] or list(VARIANTS)
        summ = []
        for n in names:
            tr = run(n, "DEV")
            save("BT1", f"{n}_DEV", tr)
            tb, f = tables(tr)
            tb.to_csv(os.path.join(OUT, f"table_{n}_DEV.csv"), index=False)
            p = tb.iloc[0].to_dict(); p["variant"] = n
            p["pairs_pos"] = int((tb.iloc[1:].avg_R > 0).sum())
            summ.append(p)
            print(n, {k: p[k] for k in ("trades", "per_week", "win_pct", "avg_R", "total_R", "PF", "maxDD_R", "pairs_pos")}, flush=True)
        s = pd.DataFrame(summ)
        f = os.path.join(OUT, "variants_DEV.csv")
        if os.path.exists(f) and sys.argv[2:]:
            old = pd.read_csv(f); s = pd.concat([old[~old.variant.isin(s.variant)], s], ignore_index=True)
        s.to_csv(f, index=False)
    elif cmd == "val":
        names = sys.argv[2:]
        assert 1 <= len(names) <= 3, "at most 3 variants go to VAL"
        for n in names:
            tr = run(n, "VAL")
            save("BT1", f"{n}_VAL", tr)
            tb, f = tables(tr)
            tb.to_csv(os.path.join(OUT, f"table_{n}_VAL.csv"), index=False)
            print(n, "VAL\n", tb.to_string(index=False), flush=True)
    elif cmd == "tables":                                            # rebuild all tables from the saved trade CSVs
        rows = []
        for n in VARIANTS:
            for per in ("DEV", "VAL"):
                fn = os.path.join(OUT, f"trades_{n}_{per}.csv")
                if not os.path.exists(fn):
                    continue
                tb, f = tables(pd.read_csv(fn))
                tb.to_csv(os.path.join(OUT, f"table_{n}_{per}.csv"), index=False)
                p = tb.iloc[0].to_dict()
                rows.append(dict(variant=n, period=per, **{k: p[k] for k in ("trades", "per_week", "win_pct", "avg_R", "total_R", "PF",
                                 "maxDD_R", "avg_net_pips", "usd_per_01lot", "avg_hold_h")},
                                 grossR=round((f.gross_pips / f.risk_pips).mean(), 3),
                                 costR=round(((f.gross_pips - f.net_pips) / f.risk_pips).mean(), 3),
                                 med_risk_pips=round(f.risk_pips.median(), 1), pairs_pos=int((tb.iloc[1:].avg_R > 0).sum())))
        s = pd.DataFrame(rows)
        s[s.period == "DEV"].drop(columns="period").to_csv(os.path.join(OUT, "variants_DEV.csv"), index=False)
        s[s.period == "VAL"].drop(columns="period").to_csv(os.path.join(OUT, "variants_VAL.csv"), index=False)
        print(md(s))
        for n in s[s.period == "VAL"].variant:
            for per in ("DEV", "VAL"):
                print(f"\n#### {n} {per}\n" + md(pd.read_csv(os.path.join(OUT, f"table_{n}_{per}.csv"))))
    elif cmd == "diag":                                              # post-hoc DEV breakdowns of one variant (no new trades)
        n, per = sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "DEV")
        t = pd.read_csv(os.path.join(OUT, f"trades_{n}_{per}.csv"), parse_dates=["t_signal", "t_exit", "t_fill", "sweep_bar"])
        f = t[t.status == "filled"].copy()
        f["session"] = session_of(f.sweep_bar)
        f["hour"] = f.sweep_bar.dt.hour
        f["depth"] = pd.cut(f.depth_atr, [0, 0.1, 0.25, 0.5, 1, 99])
        f["riskATR"] = pd.cut(f.risk_atr, [0, 0.5, 0.75, 1, 1.5, 99])
        rng = (f.bar_h - f.bar_l).replace(0, np.nan)
        f["reject"] = pd.cut(np.where(f.side == -1, (f.bar_h - f.bar_c) / rng, (f.bar_c - f.bar_l) / rng), [-.01, .5, .67, .8, 1.01])
        f["cost_R"] = (f.gross_pips - f.net_pips) / f.risk_pips
        f["grossR"] = f.gross_pips / f.risk_pips
        f["year"] = f.t_signal.dt.year
        lines = []
        for col in ["session", "htf", "level_src", "depth", "riskATR", "reject", "year", "sym"]:
            g = f.groupby(col, observed=True).agg(n=("R", "size"), win=("R", lambda x: round((x > 0).mean() * 100, 1)),
                                                   grossR=("grossR", "mean"), costR=("cost_R", "mean"), avgR=("R", "mean"),
                                                   totR=("R", "sum")).round(3)
            lines.append(f"### by {col}\n" + md(g.reset_index()))
        txt = "\n\n".join(lines)
        open(os.path.join(OUT, f"diag_{n}_{per}.md"), "w").write(txt)
        print(txt)
    elif cmd == "check":                                             # independent re-check of the sweep definition / causality
        n = sys.argv[2]
        t = pd.read_csv(os.path.join(OUT, f"trades_{n}_DEV.csv"), parse_dates=["t_signal", "sweep_bar", "level_origin"])
        t = t[t.status == "filled"]
        bad = 0
        for r in t.itertuples():
            b = B(r.sym, r.tf)
            srcs = r.level_src.split("+")
            ok_start = []
            for s in srcs:                                           # the level must have been KNOWN before the sweep bar opened
                z = levels(r.sym, s)
                m = z[(abs(z.L - r.level) < 1e-9) & (z.lside == -r.side)]
                ok_start.append(bool(len(m)) and (m.t_start <= r.sweep_bar).any())
            i = int(np.searchsorted(b.time.values, np.datetime64(r.sweep_bar)))
            x = b.iloc[i]
            swept = (x.high > r.level and x.close < r.level) if r.side == -1 else (x.low < r.level and x.close > r.level)
            m = levels(r.sym, srcs[0]); m = m[(abs(m.L - r.level) < 1e-9) & (m.lside == -r.side) & (m.t_start <= r.sweep_bar)]
            ts = m.t_start.max()
            prior = b[(b.time >= ts) & (b.time < r.sweep_bar)]
            untouched = (prior.high.max() <= r.level) if r.side == -1 else (prior.low.min() >= r.level)
            if not (all(ok_start) or any(ok_start)) or not swept or not (untouched or len(prior) == 0):
                bad += 1
                if bad <= 5:
                    print("FAIL", r.sym, r.sweep_bar, r.level, r.level_src, ok_start, swept, untouched)
            if r.t_signal < x.tclose:
                bad += 1; print("LOOKAHEAD", r.sym, r.t_signal, x.tclose)
        print(f"{n}: checked {len(t)} filled trades, {bad} failures")
    elif cmd == "replay":
        n = sys.argv[2]
        tr = pd.read_csv(os.path.join(OUT, f"trades_{n}_DEV.csv"), parse_dates=["t_signal", "t_exit", "t_fill"])
        f = tr[(tr.status == "filled") & (tr.entry_type == "market")]
        picks = [f[f.exit_reason == "TP"].iloc[0], f[f.exit_reason == "SL"].iloc[5], f[f.exit_reason == "TIME"].iloc[0]
                 if (f.exit_reason == "TIME").any() else f.iloc[7]]
        for t in picks:
            print(f"{t.sym} side {t.side} t_signal {t.t_signal} sl {t.sl:.5f} tp {t.tp:.5f}")
            print(replay(t))
