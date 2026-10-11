"""BT4 - MARKET STRUCTURE (BOS / CHoCH).  Run from the TTXT root:
    .venv\\Scripts\\python.exe smc_bot\\bt\\bt4_structure.py dev                 # all rule sets x 8 (TF,N) cells on DEV
    .venv\\Scripts\\python.exe smc_bot\\bt\\bt4_structure.py pred                # raw predictive power (DEV)
    .venv\\Scripts\\python.exe smc_bot\\bt\\bt4_structure.py val RULE:TF:N ...   # confirm <=3 chosen variants on DEV+VAL (never TEST)

Question: (a) after a BOS does the pullback continuation pay?  (b) after a CHoCH does the new direction pay, or is it noise?
Everything is causal (structure() confirms swings N bars late, breaks need a CLOSE beyond the swing, orders start at the
close of the break bar).  All fills/exits through common.simulate() (M1, real spread, commission, slippage).
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import *                                        # noqa: E402,F401
from multiprocessing import Pool                                # noqa: E402

OUT = os.path.join(RESULTS, "BT4")
DEV_END = pd.Timestamp("2024-01-01"); VAL_END = pd.Timestamp("2025-07-01")   # never look past VAL_END (TEST locked)
TFS = ["M15", "M30", "H1", "H4"]; NS = [3, 5]
CODE_VERSION = "v2-rowfix"
WAIT_BARS = 20                       # a limit order at the retest lives 20 bars of the signal timeframe
BUF_ATR = 0.1                        # stop buffer beyond the swing (0.1 ATR) - spread/slippage is added by simulate()
RISK_MIN, RISK_MAX = 0.5, 5.0        # skip setups whose stop distance is <0.5 ATR (noise) or >5 ATR (not a stop, a hope)
MIN_RR_SWING = 1.5                   # CHoCH 'next swing' target must give at least 1.5R, else no trade
MAJOR_WIN = 40                       # 'major' BOS: the broken level is also the highest high of the previous 40 bars

# rule = (event kind, entry, exit, major-only)
RULES = {
    "BOS_retest_2R":      ("BOS",   "level",  "2R",     False),
    "BOS_retest_struct":  ("BOS",   "level",  "struct", False),
    "BOS_chase_2R":       ("BOS",   "market", "2R",     False),
    "BOS_ob_2R":          ("BOS",   "ob",     "2R",     False),
    "BOSmaj_retest_2R":   ("BOS",   "level",  "2R",     True),
    "BOSmaj_retest_struct": ("BOS", "level",  "struct", True),
    "CHOCH_retest_2R":    ("CHOCH", "level",  "2R",     False),
    "CHOCH_retest_swing": ("CHOCH", "level",  "swing",  False),
    "CHOCH_retest_struct": ("CHOCH", "level", "struct", False),
    "CHOCH_chase_2R":     ("CHOCH", "market", "2R",     False),
    "CHOCH_ob_2R":        ("CHOCH", "ob",     "2R",     False),
    "CHOCH_ob_swing":     ("CHOCH", "ob",     "swing",  False),
}


def tbars(sym, tf, end):
    b = bars(sym, tf)
    return b[b.tclose <= end].reset_index(drop=True)


def weekend_cut(ts):
    """Friday 20:45 of the week of ts (orders must not be active past it); NaT-safe."""
    d = (4 - ts.dayofweek) % 7
    cut = (ts.normalize() + pd.Timedelta(days=d, hours=20, minutes=45))
    return cut


# ------------------------------------------------------------------ order construction
def make_orders(sym, tf, N, rule, b, st, a, t0, t1):
    kind, entry_kind, exit_kind, major = RULES[rule]
    want = 1 if kind == "BOS" else 2
    o, h, l, c = b.open.values, b.high.values, b.low.values, b.close.values
    tcl = b.tclose.values
    evt = st["evt"]
    mins = TF_MIN[tf]
    ev_idx = np.where(np.abs(evt) == want)[0]
    # events used as invalidation: any break against the trade
    opp_pos = {1: tcl[np.where(evt < 0)[0]], -1: tcl[np.where(evt > 0)[0]]}
    sh_idx = np.where(st["is_sh"])[0]; sl_idx = np.where(st["is_sl"])[0]
    sh_conf = sh_idx + N; sl_conf = sl_idx + N
    rows = []
    for k in ev_idx:
        ts = pd.Timestamp(tcl[k])
        if not (t0 <= ts < t1) or not a[k] > 0 or k < 60:
            continue
        d = int(np.sign(evt[k]))
        L = st["lsh"][k] if d == 1 else st["lsl"][k]
        j0 = st["leg_i"][k]
        if j0 < 0 or np.isnan(L):
            continue
        extreme = l[j0] if d == 1 else h[j0]
        if major:
            if d == 1 and h[max(0, k - MAJOR_WIN):k].max() > L: continue
            if d == -1 and l[max(0, k - MAJOR_WIN):k].min() < L: continue
        if ts.dayofweek == 4 and ts.hour * 60 + ts.minute >= 20 * 60 + 45: continue
        sl = extreme - d * BUF_ATR * a[k]
        if entry_kind == "level":
            ent = L; sl_reason = "beyond leg-origin swing (protects the break)"; etype = "limit"
        elif entry_kind == "ob":
            ent = st["ob_hi"][k] if d == 1 else st["ob_lo"][k]; sl_reason = "beyond leg-origin swing/OB"; etype = "limit"
        else:
            ent = c[k]; sl_reason = "beyond leg-origin swing (market ref=close)"; etype = "market"
        risk = (ent - sl) * d
        if not (RISK_MIN * a[k] <= risk <= RISK_MAX * a[k]): continue
        if etype == "limit" and (ent - c[k]) * d >= 0: continue          # limit must be a pullback (on the far side of price)
        if exit_kind == "2R":
            tp = ent + d * 2 * risk; tp_reason = "2R"
        elif exit_kind == "swing":
            if d == 1:
                cand = sh_idx[:np.searchsorted(sh_conf, k, "right")][-30:]; lv = h[cand]
                lv = lv[lv >= ent + MIN_RR_SWING * risk]
                tp = lv.min() if len(lv) else np.nan
            else:
                cand = sl_idx[:np.searchsorted(sl_conf, k, "right")][-30:]; lv = l[cand]
                lv = lv[lv <= ent - MIN_RR_SWING * risk]
                tp = lv.max() if len(lv) else np.nan
            if np.isnan(tp): continue
            tp_reason = "next opposite swing (liquidity) >=1.5R"
        else:
            tp = np.nan; tp_reason = "exit when structure breaks against the trade"
        exp = ts + pd.Timedelta(minutes=mins * WAIT_BARS)
        arr = opp_pos[d]; ip = np.searchsorted(arr, np.datetime64(ts), "right")
        if ip < len(arr): exp = min(exp, pd.Timestamp(arr[ip]))          # structure broke against the idea before fill: cancel
        exp = min(exp, weekend_cut(ts))
        rows.append(dict(t_signal=ts, side=d, entry_type=etype, entry=ent, expire=exp, sl=sl, tp=tp, be_at_r=np.nan,
                         max_hold_min=np.nan, sl_reason=sl_reason, tp_reason=tp_reason, ev="BOS" if want == 1 else "CHoCH",
                         tf=tf, N=N, rule=rule, level=L, atr=a[k], risk_atr=risk / a[k]))
    return pd.DataFrame(rows), opp_pos


def run_rule(sym, tf, N, rule, b, st, a, t0, t1):
    od, opp_pos = make_orders(sym, tf, N, rule, b, st, a, t0, t1)
    if len(od) == 0:
        return od
    if RULES[rule][2] == "struct":
        p1 = od.copy(); p1["max_hold_min"] = 0.0
        r1 = simulate(sym, p1, one_at_a_time=False)
        mh = np.full(len(od), np.nan)
        m1t = bars(sym, "M1").time.values
        if "t_fill" in r1:                                  # pass 1 (max_hold=0) only finds the fill time of every order
            fill_map = {(t, s_): f for t, s_, f in zip(r1.t_signal.values, r1.side.values, r1.t_fill.values)}
            for i, (ts_, d_) in enumerate(zip(od.t_signal.values, od.side.values)):
                f = fill_map.get((ts_, d_))
                if f is None or pd.isna(f): continue
                arr = opp_pos[int(d_)]; ip = np.searchsorted(arr, f, "right")
                if ip < len(arr):
                    # simulate() counts max_hold_min in M1 ROWS, not minutes, and the M1 history has missing minutes
                    # (~4% of rows), so convert: rows between the fill row and the last M1 row of the signal bar.
                    fi = np.searchsorted(m1t, f, "left"); xi = np.searchsorted(m1t, arr[ip] - np.timedelta64(1, "m"), "right") - 1
                    mh[i] = max(0.0, float(xi - fi))
        od = od.copy(); od["max_hold_min"] = mh
        return simulate(sym, od, one_at_a_time=True, max_days=40)
    return simulate(sym, od, one_at_a_time=True)


# ------------------------------------------------------------------ raw predictive power
HS = [1, 4, 12, 24]


def predictive(sym, tf, N, b, st, a, t0, t1):
    c = b.close.values; o = b.open.values; n = len(b)
    tcl = b.tclose.values; spr = b.spr.values
    P = pip(sym); cost = (spr + COMM[sym] * P)                         # round-trip cost in price units (bar spread + commission)
    evt = st["evt"]; trend = st["trend"]
    inp = (tcl >= np.datetime64(t0)) & (tcl < np.datetime64(t1)) & (a > 0)
    idx = np.arange(n)
    rng = np.random.default_rng(12345)
    rsign = rng.choice([-1, 1], n)
    ev_rows = []; base = {}
    for hh in HS:
        ok = inp & (idx + hh < n)
        fwd = np.full(n, np.nan); fwd[:n - hh] = (c[hh:] - c[:n - hh]) / np.where(a[:n - hh] > 0, a[:n - hh], np.nan)
        for name, sg in (("random", rsign), ("trend", trend), ("bar", np.sign(c - o))):
            m = ok & (sg != 0) & ~np.isnan(fwd)
            v = fwd[m] * sg[m]
            base[(name, hh)] = (v.sum(), (v ** 2).sum(), len(v))
        for e in (1, 2):
            for d in (1, -1):
                m = ok & (evt == e * d) & ~np.isnan(fwd)
                for k in np.where(m)[0]:
                    ev_rows.append((hh, e, d, fwd[k] * d, cost[k] / a[k]))
    ev = pd.DataFrame(ev_rows, columns=["h", "e", "d", "fwd", "cost_atr"])
    ev.insert(0, "sym", sym); ev.insert(1, "tf", tf); ev.insert(2, "N", N)
    return ev, base


def task_pred(args):
    sym, tf, N = args
    b = tbars(sym, tf, DEV_END); st = structure(b, N); a = atr(b)
    return predictive(sym, tf, N, b, st, a, pd.Timestamp("2021-01-01"), DEV_END) + (sym, tf, N)


def task_dev(args):
    """One (pair, TF, N) cell, all requested rules.  Result cached on disk (resume after a crash)."""
    import pickle, hashlib
    sym, tf, N, rules, t0, t1, end = args
    key = hashlib.md5(repr((CODE_VERSION, sym, tf, N, rules, str(t0), str(t1), str(end))).encode()).hexdigest()[:12]
    cdir = os.path.join(CACHE, "bt4_cells"); os.makedirs(cdir, exist_ok=True)
    cf = os.path.join(cdir, f"{sym}_{tf}_{N}_{key}.pkl")
    if os.path.exists(cf):
        with open(cf, "rb") as fh: return pickle.load(fh)
    b = tbars(sym, tf, end); st = structure(b, N); a = atr(b)
    out = {}
    for r in rules:
        out[r] = run_rule(sym, tf, N, r, b, st, a, t0, t1)
    res = (sym, tf, N, out)
    with open(cf + ".tmp", "wb") as fh: pickle.dump(res, fh)
    os.replace(cf + ".tmp", cf)
    print("cell done", sym, tf, N, time.strftime("%H:%M:%S"), flush=True)
    return res


def collect(res):
    d = {}
    for sym, tf, N, out in res:
        for r, t in out.items():
            if len(t): d.setdefault(r, []).append(t)
    return {r: pd.concat(v, ignore_index=True) for r, v in d.items()}


def pooled_stats(t):
    f = t[t.status == "filled"]
    return stats(f) if len(f) else dict(trades=0)


def per_pair_table(t):
    f = t[t.status == "filled"]
    rows = []
    for s in PAIRS:
        g = f[f.sym == s]
        if len(g): rows.append(dict(sym=s, **stats(g)))
    rows.append(dict(sym="POOLED", **stats(f)))
    return pd.DataFrame(rows)


def main_dev():
    os.makedirs(OUT, exist_ok=True)
    rules = list(RULES)
    tasks = [(s, tf, N, rules, pd.Timestamp("2021-01-01"), DEV_END, DEV_END) for s in PAIRS for tf in TFS for N in NS]
    t_start = time.time()
    with Pool(6) as p:
        res = list(p.imap_unordered(task_dev, tasks, chunksize=1))
    print("dev done", round(time.time() - t_start), "s")
    allt = collect(res)
    rows = []
    for r, t in allt.items():
        save("BT4", f"dev_{r}", t)
        for (tf, N), g in t.groupby(["tf", "N"]):
            f = g[g.status == "filled"]
            s = stats(f) if len(f) else dict(trades=0)
            pos_pairs = int((f.groupby("sym").R.sum() > 0).sum()) if len(f) else 0
            yrs = f.groupby(f.t_fill.dt.year).R.sum().round(1).to_dict() if len(f) else {}
            rows.append(dict(rule=r, tf=tf, N=N, orders=len(g), fill_pct=round(100 * len(f) / max(1, (g.status.isin(["filled", "not_filled", "invalid"])).sum()), 1),
                             pairs_pos=pos_pairs, by_year=str(yrs), **s))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "dev_summary.csv"), index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500); pd.set_option("display.max_columns", 40)
    print(df.drop(columns=["by_year"]).to_string())


def main_pred():
    os.makedirs(OUT, exist_ok=True)
    tasks = [(s, tf, N) for s in PAIRS for tf in TFS for N in NS]
    with Pool(7) as p:
        res = p.map(task_pred, tasks, chunksize=1)
    ev = pd.concat([r[0] for r in res], ignore_index=True)
    ev.to_csv(os.path.join(OUT, "pred_events_dev.csv.gz"), index=False)
    base = {}
    for r in res:
        for k, (s, s2, n_) in r[1].items():
            key = (r[3], r[4], k[0], k[1])                                  # tf, N, baseline name, h
            x = base.get(key, (0.0, 0.0, 0)); base[key] = (x[0] + s, x[1] + s2, x[2] + n_)
    rows = []
    for (tf, N, e, hh), g in ev.groupby(["tf", "N", "e", "h"]):
        m = g.fwd.mean(); se = g.fwd.std() / np.sqrt(len(g))
        # per-pair positive count
        pp = int((g.groupby("sym").fwd.mean() > 0).sum())
        row = dict(tf=tf, N=N, ev="BOS" if e == 1 else "CHoCH", h=hh, n=len(g), mean_atr=round(m, 4), t=round(m / se, 2), pct_pos=round((g.fwd > 0).mean() * 100, 1),
                   pairs_pos=pp, cost_atr=round(g.cost_atr.mean(), 3))
        for nm in ("random", "trend", "bar"):
            s, s2, n_ = base[(tf, N, nm, hh)]
            row[f"base_{nm}"] = round(s / n_, 4)
        rows.append(row)
    df = pd.DataFrame(rows); df.to_csv(os.path.join(OUT, "pred_dev.csv"), index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
    print(df.to_string())


def md_table(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def main_val(specs):
    """specs: RULE:TF:N (at most 3).  Re-runs each on DEV and on VAL separately (fresh state) and writes per-pair tables."""
    assert len(specs) <= 3, "brief: at most 3 variants go to VAL"
    os.makedirs(OUT, exist_ok=True)
    md = []
    for sp in specs:
        rule, tf, N = sp.split(":"); N = int(N)
        outs = {}
        for per, (t0, t1) in (("DEV", (pd.Timestamp("2021-01-01"), DEV_END)), ("VAL", (DEV_END, VAL_END))):
            tasks = [(s, tf, N, [rule], t0, t1, VAL_END) for s in PAIRS]
            with Pool(7) as p:
                res = p.map(task_dev, tasks, chunksize=1)
            outs[per] = collect(res).get(rule, pd.DataFrame())
        full = pd.concat(outs.values(), ignore_index=True)
        name = f"final_{rule}_{tf}_N{N}"
        save("BT4", name, full)
        for per in ("DEV", "VAL"):
            t = outs[per]
            md.append(f"\n#### {name} - {per}\n")
            pt = per_pair_table(t)
            md.append(md_table(pt))
            f = t[t.status == "filled"]
            if per == "VAL" or True:
                yr = f.groupby([f.t_fill.dt.to_period("Q")]).R.sum().round(1)
                md.append("\nR by quarter: " + ", ".join(f"{k}:{v}" for k, v in yr.items()))
        print(name, "done")
    with open(os.path.join(OUT, "val_tables.md"), "w") as fh:
        fh.write("\n".join(md))
    print("\n".join(md))


def replay(sym, r):
    """Independent minute-by-minute re-implementation of one filled trade (plain python loop, no numpy tricks) using the
    documented conventions of simulate().  Returns dict with what a human replaying the chart would find."""
    m1 = bars(sym, "M1"); P = pip(sym); d = int(r.side)
    m1 = m1[(m1.time >= pd.Timestamp(r.t_signal)) & (m1.time <= pd.Timestamp(r.t_exit) + pd.Timedelta(minutes=5))].reset_index(drop=True)
    fill = None; log = []
    for i, x in m1.iterrows():
        if fill is None:
            if r.entry_type == "market":
                fill = x.open + x.spr if d == 1 else x.open; f_i = i; log.append(f"{x.time} MARKET fill {fill:.5f}")
            else:
                touched = (x.low + x.spr <= r.entry) if d == 1 else (x.high >= r.entry)
                if touched:
                    fill = min(r.entry, x.open + x.spr) if d == 1 else max(r.entry, x.open); f_i = i
                    log.append(f"{x.time} LIMIT touched (low+spr={x.low + x.spr:.5f}) fill {fill:.5f}" if d == 1 else f"{x.time} LIMIT touched (high={x.high:.5f}) fill {fill:.5f}")
            if fill is None: continue
        # exit checks (SL before TP; TP never on fill bar)
        sl_hit = (x.low <= r.sl) if d == 1 else (x.high + x.spr >= r.sl)
        tp_hit = (not np.isnan(r.tp)) and i > f_i and ((x.high >= r.tp) if d == 1 else (x.low + x.spr <= r.tp))
        if sl_hit or tp_hit or x.time >= pd.Timestamp(r.t_exit):
            if sl_hit:
                why = "SL"; px = (min(r.sl, x.open) - STOP_SLIP * P) if (d == 1 and i > f_i) else ((r.sl - STOP_SLIP * P) if d == 1 else (max(r.sl, x.open + x.spr) + STOP_SLIP * P if i > f_i else r.sl + STOP_SLIP * P))
            elif tp_hit:
                why = "TP"; px = r.tp
            else:
                why = r.exit_reason; px = x.close if d == 1 else x.close + x.spr
            log.append(f"{x.time} EXIT {why} at {px:.5f}")
            risk = (fill - r.sl) * d; net = (px - fill) * d / P - COMM[sym]
            return dict(fill=fill, exit=px, why=why, R=net / (risk / P), log=log, t_exit=x.time)
    return dict(fill=fill, log=log, R=np.nan)


def main_examples(name):
    f = os.path.join(OUT, f"trades_{name}.csv")
    t = pd.read_csv(f, parse_dates=["t_signal", "t_fill", "t_exit", "expire"])
    t = t[(t.status == "filled") & (t.period == "DEV")].sort_values("t_fill")
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    ex = t.iloc[np.linspace(0, len(t) - 1, 10).astype(int)]
    cols = ["sym", "t_signal", "side", "t_fill", "fill", "sl", "tp", "t_exit", "exit", "exit_reason", "R", "risk_pips", "net_pips"]
    print(ex[cols].round(5).to_string(index=False))
    print("\nREPLAY")
    for _, r in ex.iloc[[1, 5, 8]].iterrows():
        rr = replay(r.sym, r)
        print(r.sym, r.t_fill, "csv R=%.3f replay R=%.3f  csv fill=%.5f replay fill=%.5f csv exit=%.5f replay exit=%.5f" % (
            r.R, rr["R"], r.fill, rr["fill"], r.exit, rr.get("exit", np.nan)))
        for s in rr["log"]: print("   ", s)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if mode == "dev": main_dev()
    elif mode == "pred": main_pred()
    elif mode == "val": main_val(sys.argv[2:])
    elif mode == "examples": main_examples(sys.argv[2])
