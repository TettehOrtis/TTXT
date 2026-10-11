"""BT2 - ORDER BLOCKS. Does the first retest of an unmitigated order block hold and pay after real costs?

Run from the TTXT root:
  .venv/Scripts/python.exe smc_bot/bt/bt2_orderblock.py dev              # all DEV variants  -> results/BT2/dev_*.csv
  .venv/Scripts/python.exe smc_bot/bt/bt2_orderblock.py val V04 V07 ...  # confirm <=3 variants on VAL
  .venv/Scripts/python.exe smc_bot/bt/bt2_orderblock.py replay V04       # replay 3 trades by hand on M1 (independent code)

Definitions (smc-strategy.md 5.1 + smc_core.structure):
  Bullish OB = last bearish candle before the impulsive up-leg that CLOSES above the last confirmed swing high (BOS if the
  trend was already up, CHoCH if it was down). Zone = that candle's low..high. Bearish OB = mirror.
  The OB is only KNOWN at the close of the break bar k (t_decide). From then on a resting limit order sits at the entry level
  (proximal edge / 50% mean threshold / distal edge) until the zone expires (`valid` zone-TF bars). ONE touch only: the first
  time price trades the entry level the order fills and the OB is dead (mitigated).
  The fill minute is found here (same rule as simulate: long fills when ask low <= entry, short when bid high >= entry) and the
  order is handed to simulate() with t_signal = that minute, so a pending order does not block the pair (one position at a time
  applies to open positions only). TP/SL only use information known before the fill minute.
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bt.common as C
from bt.common import (structure, fvgs, atr, asof_index, simulate, stats, save, pip, PAIRS, PERIODS, COMM,  # noqa
                       RESULTS, TF_MIN)

# Speed shim (results unchanged): the cached bars store `time` as datetime64[ms] while simulate() searches with
# datetime64[us] scalars, so numpy re-casts the whole 2M-row M1 array on every searchsorted (~10 ms per call, ~20 ms per
# order). Serve the same bars with [us] time columns. Only the dtype unit changes, not a single value.
_orig_bars = C.bars
_bcache = {}


def bars(sym, tf):
    key = (sym, tf)
    if key not in _bcache:
        if tf == "M1":
            for kk in [x for x in _bcache if x[1] == "M1"]:
                del _bcache[kk]                                   # keep one M1 frame in memory
        b = _orig_bars(sym, tf)
        b["time"] = b.time.astype("datetime64[us]"); b["tclose"] = b.tclose.astype("datetime64[us]")
        _bcache[key] = b
    return _bcache[key]


C.bars = bars

OUT = os.path.join(RESULTS, "BT2")
os.makedirs(OUT, exist_ok=True)
NSW = {"M15": 3, "M30": 3, "H1": 5, "H4": 3}          # swing strength per zone TF (strategy 6.1/6.4)
END_VAL = pd.Timestamp(PERIODS["VAL"][1])             # nothing at/after TEST start is ever built
BUF_ATR = 0.10                                        # SL buffer beyond the distal edge, in zone-TF ATR(14)

# ------------------------------------------------------------------ variants (all of them are reported)
# tf, entry (prox|mid|distal), tp (2R|3R|leg|brk), valid (zone-TF bars), filt, be (R or nan)
BASE = dict(tf="M30", entry="prox", tp="2R", valid=48, filt="all", be=np.nan)
VARIANTS = {
    # 1) which timeframe's OBs, entry at the proximal edge (first touch), 2R
    "V01": dict(BASE, tf="M15"),
    "V02": dict(BASE, tf="M30"),
    "V03": dict(BASE, tf="H1"),
    "V04": dict(BASE, tf="H4"),
    # 2) entry at the 50% mean threshold
    "V05": dict(BASE, tf="M15", entry="mid"),
    "V06": dict(BASE, tf="M30", entry="mid"),
    "V07": dict(BASE, tf="H1", entry="mid"),
    "V08": dict(BASE, tf="H4", entry="mid"),
}
# stage 2, decided after reading stage 1 on DEV: every OB timeframe/entry was negative after costs; higher TF and the
# proximal edge were least bad. Filters/targets are tested on H1 OBs (proximal, 2R) because H1 has enough trades AND a real
# higher timeframe (H4 structure) to test "with vs against the HTF trend"; one extra run on H4 (best TF) with the leg target.
S2_BASE = dict(BASE, tf="H1")
STAGE2 = {
    "V09": dict(entry="distal"),
    "V10": dict(tp="3R"),
    "V11": dict(tp="leg"),
    "V12": dict(filt="with_htf"),
    "V13": dict(filt="against_htf"),
    "V14": dict(filt="strong"),        # displacement >= 2 ATR AND left an FVG
    "V15": dict(filt="sweep"),         # leg origin swept the previous swing low/high
    "V16": dict(filt="bos"),
    "V17": dict(filt="choch"),
    "V18": dict(valid=12),             # fresh OBs only
    "V19": dict(be=1.0),
    "V20": dict(tf="H4", tp="leg"),
    "V21": dict(obdef="dec"),          # alternative OB candle: last opposite candle right before the breaking push
}
VARIANTS.update({k: dict(S2_BASE, **v) for k, v in STAGE2.items()})


# ------------------------------------------------------------------ OB events with features
_cache = {}


def trend_at(sym, tf, t):
    key = ("trend", sym, tf)
    if key not in _cache:
        b = bars(sym, tf)
        _cache[key] = (b, structure(b, NSW[tf])["trend"])
    b, tr = _cache[key]
    i = asof_index(b, t)
    return np.where(i >= 0, tr[np.clip(i, 0, None)], 0)


def ob_events(sym, tf):
    """One row per structure break on `tf` (known at its close) with the OB zone and trader features."""
    key = ("ev", sym, tf)
    if key in _cache:
        return _cache[key]
    b = bars(sym, tf)
    b = b[b.time < END_VAL].reset_index(drop=True)
    o, h, l, c = b.open.values, b.high.values, b.low.values, b.close.values
    st = structure(b, NSW[tf])
    a = atr(b, 14)
    fb, fs = fvgs(b, 0.1)
    rows = []
    for k in np.where(st["evt"] != 0)[0]:
        d = 1 if st["evt"][k] > 0 else -1
        if np.isnan(a[k]):
            continue
        j0 = int(st["leg_i"][k])
        brk_i = int(st["lsh_i"][k] if d == 1 else st["lsl_i"][k])
        # recover the OB candle index exactly as smc_core.structure does
        j = j0
        for jj in range(j0, max(j0 - 6, brk_i), -1):
            if (c[jj] < o[jj]) if d == 1 else (c[jj] > o[jj]):
                j = jj; break
        assert l[j] == st["ob_lo"][k] and h[j] == st["ob_hi"][k]
        opp = bool((c[j] < o[j]) if d == 1 else (c[j] > o[j]))          # really an opposite candle?
        leg_ext = l[j0] if d == 1 else h[j0]                              # leg origin (swing extreme)
        brk_lvl = st["lsh"][k] if d == 1 else st["lsl"][k]                # the swing that was broken
        imp_ext = h[j0:k + 1].max() if d == 1 else l[j0:k + 1].min()      # impulse extreme so far
        disp = ((c[k] - leg_ext) * d) / a[k]                              # size of the break leg, ATR
        fvg = bool((fb if d == 1 else fs)[max(j + 2, 2):k + 1].any())     # displacement left an FVG
        prev = st["lsl"][j0 - 1] if d == 1 else st["lsh"][j0 - 1]         # opposite swing known before the leg origin
        sweep = bool(not np.isnan(prev) and ((leg_ext < prev) if d == 1 else (leg_ext > prev)))
        # already mitigated BEFORE the break confirmed it: price left the zone, then came back into it (bug #2 in report)
        def premitigated(jx):
            seg = l[jx + 1:k + 1] if d == 1 else h[jx + 1:k + 1]
            left = np.where(seg > h[jx])[0] if d == 1 else np.where(seg < l[jx])[0]
            return bool(len(left) and ((seg[left[0]:] <= h[jx]).any() if d == 1 else (seg[left[0]:] >= l[jx]).any()))
        premit = premitigated(j)
        # alternative "decisional" OB (V21): the LAST opposite candle before the break bar inside the leg (j0..k-1),
        # i.e. the candle right before the final push, instead of the candle at the leg origin used by smc_core
        jd = j
        for jj in range(k - 1, j0 - 1, -1):
            if (c[jj] < o[jj]) if d == 1 else (c[jj] > o[jj]):
                jd = jj; break
        rows.append(dict(k=k, d=d, kind="CHoCH" if abs(st["evt"][k]) == 2 else "BOS", t_ob=b.time.values[j],
                         t_decide=b.tclose.values[k], ob_lo=l[j], ob_hi=h[j], leg_ext=leg_ext, brk_lvl=brk_lvl,
                         imp_ext=imp_ext, atr=a[k], ob_atr=(h[j] - l[j]) / a[k], disp_atr=disp, fvg=fvg, sweep=sweep,
                         opp=opp, leg_bars=k - j0, close_k=c[k], premit=premit,
                         dec_lo=l[jd], dec_hi=h[jd], dec_premit=premitigated(jd), t_dec=b.time.values[jd]))
    ev = pd.DataFrame(rows)
    ev["t_decide"] = pd.to_datetime(ev.t_decide)
    ev["t_ob"] = pd.to_datetime(ev.t_ob)
    _cache[key] = (b, ev)
    return b, ev


def build_orders(sym, v, period):
    """Limit orders (one per OB that gets its first retest) for variant v, decided inside `period`."""
    tf = v["tf"]
    b, ev = ob_events(sym, tf)
    a0, a1 = map(pd.Timestamp, PERIODS[period])
    if v.get("obdef") == "dec":                                  # decisional OB: zone and stop from that candle only
        ev = ev.assign(ob_lo=ev.dec_lo, ob_hi=ev.dec_hi, premit=ev.dec_premit, t_ob=ev.t_dec,
                       leg_ext=np.where(ev.d == 1, ev.dec_lo, ev.dec_hi))
    ev = ev[(ev.t_decide >= a0) & (ev.t_decide < a1) & ~ev.premit]     # unmitigated OBs only
    m1 = bars(sym, "M1")
    t1 = m1.time.values; h1 = m1.high.values; l1 = m1.low.values; s1 = m1.spr.values
    tb0 = np.searchsorted(t1, b.time.values, "left")         # M1 index range of each zone-TF bar
    tb1 = np.searchsorted(t1, b.tclose.values, "left")
    bh, bl, bs = b.high.values, b.low.values, b.spr.values
    P = pip(sym)
    out = []
    for r in ev.itertuples(index=False):
        d = r.d
        lo, hi = r.ob_lo, r.ob_hi
        prox, dist = (hi, lo) if d == 1 else (lo, hi)
        entry = {"prox": prox, "mid": (lo + hi) / 2, "distal": dist}[v["entry"]]
        stop_ref = min(lo, r.leg_ext) if d == 1 else max(hi, r.leg_ext)     # beyond distal edge AND leg extreme
        sl = stop_ref - d * BUF_ATR * r.atr
        risk = (entry - sl) * d
        if not risk > 0 or (r.close_k - entry) * d <= 0:
            continue
        tp_fixed = {"2R": entry + d * 2 * risk, "3R": entry + d * 3 * risk}.get(v["tp"], np.nan)
        # ---- first touch of the entry level (bid low <= entry - spread for longs == ask low <= entry)
        f = -1
        kmax = min(r.k + v["valid"], len(b) - 1)
        for kk in range(r.k + 1, kmax + 1):
            if (bl[kk] > entry) if d == 1 else (bh[kk] < entry):
                continue
            s, e = tb0[kk], tb1[kk]
            hit = (l1[s:e] + s1[s:e] <= entry) if d == 1 else (h1[s:e] >= entry)
            if hit.any():
                f = s + int(np.argmax(hit)); break
        if f < 0:
            continue
        a = tb1[r.k]                                            # first M1 bar after the decision
        seg_h, seg_l = h1[a:f], l1[a:f] + s1[a:f]
        ext = max(r.imp_ext, seg_h.max()) if d == 1 and len(seg_h) else (
              min(r.imp_ext, seg_l.min()) if len(seg_l) else r.imp_ext)
        # NOTE: no "cancel if price reached the target before the retest" rule. Price is usually ALREADY beyond a 2R target
        # when the OB is confirmed (strong displacement), and a return to an unmitigated OB after a run-away is the textbook
        # setup. A first version cancelled those orders and silently removed the strongest OBs (bug #1 in the report).
        tp = tp_fixed if not np.isnan(tp_fixed) else (ext if v["tp"] == "leg" else r.brk_lvl)
        tf_ = pd.Timestamp(t1[f])
        if tf_.dayofweek == 4 and tf_.hour >= 20:
            continue                                            # no new fills into the weekend close
        out.append(dict(t_signal=tf_.as_unit("us"), side=d, entry_type="limit", entry=entry, expire=tf_ + pd.Timedelta(minutes=1),
                        sl=sl, tp=tp, be_at_r=v["be"], max_hold_min=np.nan, t_decide=r.t_decide, t_ob=r.t_ob, tf=tf,
                        kind=r.kind, ob_lo=lo, ob_hi=hi, ob_atr=round(r.ob_atr, 2), disp_atr=round(r.disp_atr, 2),
                        fvg=r.fvg, sweep=r.sweep, opp=r.opp, age_bars=int((tf_ - r.t_decide) / pd.Timedelta(minutes=TF_MIN[tf])),
                        risk_atr=round(risk / r.atr, 2), rr_plan=round((tp - entry) * d / risk, 2),
                        sl_reason=f"{'below' if d == 1 else 'above'} OB distal edge / leg extreme {stop_ref:.5f} - {BUF_ATR} ATR",
                        tp_reason={"2R": "fixed 2R", "3R": "fixed 3R", "leg": "impulse extreme (liquidity)",
                                   "brk": "broken swing level"}[v["tp"]]))
    o = pd.DataFrame(out)
    if len(o):
        o["htf_H1"] = trend_at(sym, "H1", o.t_signal.values) * o.side
        o["htf_H4"] = trend_at(sym, "H4", o.t_signal.values) * o.side
        o["own_tf"] = trend_at(sym, tf, o.t_signal.values) * o.side
    return o


HTF_OF = {"M15": "htf_H1", "M30": "htf_H1", "H1": "htf_H4", "H4": "htf_H4"}


def apply_filter(o, v):
    if not len(o):
        return o
    f = v["filt"]
    htf = o[HTF_OF[v["tf"]]]
    strong = (o.disp_atr >= 2.0) & o.fvg
    m = {"all": np.ones(len(o), bool), "with_htf": htf == 1, "against_htf": htf == -1, "strong": strong,
         "sweep": o.sweep, "bos": o.kind == "BOS", "choch": o.kind == "CHoCH",
         "with_htf_strong": (htf == 1) & strong}[f]
    return o[np.asarray(m)]


def run_variant(name, v, period, pairs=PAIRS, write=True):
    allt = []
    for sym in pairs:
        o = apply_filter(build_orders(sym, v, period), v)
        if not len(o):
            continue
        t = simulate(sym, o, one_at_a_time=True)
        t = t[t.period == period] if len(t) else t
        allt.append(t)
    T = pd.concat(allt, ignore_index=True)
    T["variant"] = name
    T["hour"] = pd.to_datetime(T.t_signal).dt.hour
    if write:
        save("BT2", f"{name}_{period}", T)
    return T


def summarize(T, by=None):
    f = T[T.status == "filled"]
    if by is None:
        return pd.Series(stats(f))
    return pd.DataFrame({k: stats(g) for k, g in f.groupby(by)}).T


def session(hr):
    return np.select([hr < 7, hr < 12, hr < 17], ["Asia 00-07", "London 07-12", "NY 12-17"], "Late 17-24")


# ------------------------------------------------------------------ independent hand replay (no simulate())
def replay(row):
    """Walk M1 bars minute by minute from the fill: long exits at bid, short at ask; SL checked first; TP not on fill bar."""
    sym = row.sym; m1 = bars(sym, "M1"); P = pip(sym)
    i = int(np.searchsorted(m1.time.values, np.datetime64(pd.Timestamp(row.t_fill)), "left"))
    d = int(row.side); sl, tp = row.sl, row.tp
    x = m1.iloc[i]
    ask_lo = x.low + x.spr
    fill = min(row.entry, x.open + x.spr) if d == 1 else max(row.entry, x.open)
    lines = [f"fill bar {x.time}  O{x.open} H{x.high} L{x.low} C{x.close} spr{x.spr:.5f}  "
             f"{'ask low' if d == 1 else 'bid high'}={ask_lo if d == 1 else x.high:.5f} vs entry {row.entry:.5f} -> fill {fill:.5f}"]
    j = i
    while True:
        y = m1.iloc[j]
        hi_ask = y.high + y.spr; lo_ask = y.low + y.spr
        slhit = (y.low <= sl) if d == 1 else (hi_ask >= sl)
        tphit = j > i and ((y.high >= tp) if d == 1 else (lo_ask <= tp))
        fri = y.time.dayofweek == 4 and y.time.hour * 60 + y.time.minute >= 20 * 60 + 45
        if slhit:
            px = (min(sl, y.open) if j > i else sl) - 0.2 * P if d == 1 else (max(sl, y.open + y.spr) if j > i else sl) + 0.2 * P
            why = "SL"; break
        if tphit:
            px, why = tp, "TP"; break
        if fri:
            px, why = (y.close if d == 1 else y.close + y.spr), "WEEKEND"; break
        j += 1
    lines.append(f"exit bar {y.time}  O{y.open} H{y.high} L{y.low} C{y.close} -> {why} at {px:.5f}")
    net = (px - fill) * d / P - COMM[sym]
    R = net / ((fill - sl) * d / P)
    return lines, why, px, R


# ------------------------------------------------------------------ main
CHOSEN = ["V04", "V18", "V20"]          # the 3 variants sent to VAL (least negative on DEV, each a distinct trader idea)


def load_trades(n, period):
    T = pd.read_csv(os.path.join(OUT, f"trades_{n}_{period}.csv"), parse_dates=["t_fill", "t_exit", "t_signal", "t_decide"])
    F = T[T.status == "filled"].copy()
    F["gR"] = F.gross_pips / F.risk_pips
    return F


def md(df, idx=True):
    """Minimal markdown table (no tabulate dependency)."""
    d = df.reset_index() if idx else df
    def f(x):
        if isinstance(x, (float, np.floating)):
            return "" if np.isnan(x) else f"{x:.6g}"
        return str(x)
    head = "| " + " | ".join(map(str, d.columns)) + " |\n|" + "---|" * len(d.columns)
    return head + "\n" + "\n".join("| " + " | ".join(f(x) for x in row) + " |" for row in d.itertuples(index=False))


def tables():
    """Markdown tables used in report.md (reproducible)."""
    out = []
    D = pd.read_csv(os.path.join(OUT, "dev_variants.csv"))
    for kk in BASE:
        D[kk] = [VARIANTS[n][kk] for n in D.variant]
    D["ob"] = [VARIANTS[n].get("obdef", "origin") for n in D.variant]
    cols = ["variant", "tf", "ob", "entry", "tp", "valid", "filt", "be", "trades", "per_week", "win_pct", "avg_R", "gross_avg_R",
            "total_R", "PF", "maxDD_R", "avg_net_pips", "avg_risk_pips", "usd_per_01lot", "avg_hold_h", "pos_pairs"]
    out.append("### All variants, DEV\n" + md(D[cols], False))
    keep = ["trades", "per_week", "win_pct", "avg_R", "total_R", "PF", "maxDD_R", "avg_net_pips", "usd_per_01lot", "avg_hold_h"]
    for n in CHOSEN:
        for p in ("DEV", "VAL"):
            F = load_trades(n, p)
            t = pd.DataFrame({s: stats(g) for s, g in F.groupby("sym")}).T[keep]
            t.loc["POOLED"] = pd.Series(stats(F))[keep]
            t["gross_avg_R"] = list(F.groupby("sym").gR.mean().round(3)) + [round(F.gR.mean(), 3)]
            out.append(f"### {n} {p}  ({VARIANTS[n]})\n" + md(t))
    for n in CHOSEN:
        rows = []
        for p in ("DEV", "VAL"):
            F = load_trades(n, p)
            F["session"] = session(F.t_fill.dt.hour)
            F["side_"] = np.where(F.side == 1, "long", "short")
            for c in ("session", "side_"):
                for k, g in F.groupby(c):
                    s = stats(g); rows.append(dict(period=p, slice=f"{c}={k}", trades=s["trades"], win_pct=s["win_pct"],
                                                   avg_R=s["avg_R"], total_R=s["total_R"], PF=s["PF"]))
        out.append(f"### {n} by session (UTC) and side\n" + md(pd.DataFrame(rows), False))
    # trader features, measured on every trade of the H1 and H4 base runs (DEV)
    for n in ("V03", "V04"):
        F = load_trades(n, "DEV")
        F["disp_q"] = pd.qcut(F.disp_atr, 4).astype(str)
        F["age_q"] = pd.cut(F.age_bars, [-1, 1, 5, 20, 1000]).astype(str)
        F["htf"] = F[HTF_OF[VARIANTS[n]["tf"]]].map({1: "with", -1: "against", 0: "none"})
        rows = []
        for c in ("kind", "fvg", "sweep", "opp", "htf", "disp_q", "age_q"):
            for k, g in F.groupby(c):
                rows.append(dict(feature=c, value=k, trades=len(g), win_pct=round((g.R > 0).mean() * 100, 1),
                                 gross_R=round(g.gR.mean(), 3), net_R=round(g.R.mean(), 3),
                                 reach_1R=round((g.mfe_R >= 1).mean() * 100, 1)))
        out.append(f"### Trader features, {n} DEV (all trades of that run)\n" + md(pd.DataFrame(rows), False))
    # weekend carry (harness issue)
    rows = []
    for n in CHOSEN:
        for p in ("DEV", "VAL"):
            F = load_trades(n, p)
            sat = [(pd.date_range(a.normalize(), b.normalize()).dayofweek == 5).any() for a, b in zip(F.t_fill, F.t_exit)]
            rows.append(dict(variant=n, period=p, trades=len(F), held_over_weekend=int(np.sum(sat)),
                             TIME_exits=int((F.exit_reason == "TIME").sum())))
    out.append("### Trades carried over a weekend (should be 0)\n" + md(pd.DataFrame(rows), False))
    # 10 example trades
    F = load_trades("V04", "DEV")
    why = ["TP"] * 4 + ["SL"] * 4 + ["WEEKEND"] * 2                   # one trade per pair, first of its kind in 2022
    F22 = F[F.t_fill >= "2022-01-01"]
    ex = pd.concat([F22[(F22.sym == s) & (F22.exit_reason == w)].iloc[:1] for s, w in zip(PAIRS, why)])
    ex = ex[["sym", "kind", "side", "ob_lo", "ob_hi", "t_decide", "t_fill", "fill", "sl", "tp", "t_exit", "exit", "exit_reason",
             "risk_pips", "net_pips", "R"]].round(5)
    out.append("### 10 example trades (V04, DEV)\n" + md(ex, False))
    txt = "\n\n".join(out)
    with open(os.path.join(OUT, "tables.md"), "w") as f:
        f.write(txt)
    print(txt)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    if cmd in ("dev", "dev2"):
        names = sys.argv[2:] or list(VARIANTS)
        rows = []
        for n in names:
            v = VARIANTS[n]
            T = run_variant(n, v, "DEV")
            s = summarize(T); s["variant"] = n
            for kk in v:
                s[kk] = v[kk]
            s["pos_pairs"] = int((summarize(T, "sym").total_R > 0).sum())
            F = T[T.status == "filled"]
            s["gross_avg_R"] = round((F.gross_pips / F.risk_pips).mean(), 3)       # before commission/slippage-free view
            s["avg_risk_pips"] = round(F.risk_pips.mean(), 1)
            rows.append(s)
            print(n, v, dict(s[["trades", "win_pct", "avg_R", "total_R", "PF", "maxDD_R", "pos_pairs"]]), flush=True)
        R = pd.DataFrame(rows)
        f = os.path.join(OUT, "dev_variants.csv")
        if os.path.exists(f):
            old = pd.read_csv(f); R = pd.concat([old[~old.variant.isin(R.variant)], R]).sort_values("variant")
        R.to_csv(f, index=False)
        print(R.to_string())
    elif cmd == "val":
        for n in sys.argv[2:]:
            T = run_variant(n, VARIANTS[n], "VAL")
            print(n, dict(summarize(T)))
    elif cmd == "replay":
        n = sys.argv[2]
        T = pd.read_csv(os.path.join(OUT, f"trades_{n}_DEV.csv"), parse_dates=["t_fill", "t_exit", "t_signal"])
        T = T[T.status == "filled"]
        pick = pd.concat([T[T.exit_reason == "TP"].iloc[[3]], T[T.exit_reason == "SL"].iloc[[7]], T[T.side == -1].iloc[[11]]])
        for r in pick.itertuples(index=False):
            lines, why, px, R = replay(r)
            print(f"\n{r.sym} {r.tf} {'LONG' if r.side == 1 else 'SHORT'} OB {r.ob_lo}-{r.ob_hi} decided {r.t_decide} "
                  f"entry {r.entry:.5f} SL {r.sl:.5f} TP {r.tp:.5f}")
            # the OB on its own timeframe: OB candle .. break bar
            b, ev = ob_events(r.sym, r.tf)
            e = ev[(ev.t_ob == pd.Timestamp(r.t_ob)) & (ev.d == r.side)].iloc[0]
            j = int(np.searchsorted(b.time.values, np.datetime64(pd.Timestamp(r.t_ob)), "left"))
            print(f"    {r.kind}: broken swing {e.brk_lvl:.5f}, leg origin {e.leg_ext:.5f}, disp {e.disp_atr:.2f} ATR, FVG {e.fvg}")
            for q in range(j - 1, int(e.k) + 1):
                x = b.iloc[q]
                tag = "OB " if q == j else ("BRK" if q == e.k else "   ")
                print(f"    {tag} {x.time} O{x.open:.5f} H{x.high:.5f} L{x.low:.5f} C{x.close:.5f} "
                      f"{'bear' if x.close < x.open else 'bull'}")
            # independent first-touch check: first M1 after the decision where the limit is reachable
            m1 = bars(r.sym, "M1"); m = m1[m1.time >= pd.Timestamp(r.t_decide)]
            reach = (m.low + m.spr <= r.entry) if r.side == 1 else (m.high >= r.entry)
            print(f"    first M1 reaching entry after decision: {m.time[reach].iloc[0]}  (trade t_fill {r.t_fill})")
            for ln in lines:
                print("   ", ln)
            print(f"    simulate: {r.exit_reason} {r.t_exit} {r.exit:.5f} R={r.R:.3f} | hand: {why} {px:.5f} R={R:.3f}")
        # bulk: independent replay of 300 random trades vs simulate()
        smp = T.sample(min(300, len(T)), random_state=1)
        diffs = []
        for r in smp.sort_values("sym").itertuples(index=False):
            _, why, px, R = replay(r)
            diffs.append((why == r.exit_reason, abs(R - r.R)))
        d = np.array(diffs, dtype=float)
        print(f"\nbulk replay of {len(d)} trades: same exit reason {d[:, 0].mean():.1%}, max |R diff| {d[:, 1].max():.4f}")
    elif cmd == "tables":
        tables()
