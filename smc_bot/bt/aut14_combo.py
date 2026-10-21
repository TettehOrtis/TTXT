"""AUT14 - combine the small real edges (S1 AUT02 R1 sweep fade M30, S2 AUT03 failed-retest fade,
S3 AUT04 London opening-range breakout). Governing: bt/AUT11_14_BRIEF.md.

Step 0 (this file, `gen` and `step0`): regenerate every signal's time and direction per stage by
importing the three source modules READ-ONLY (each in its own process, because each module patches
common.bars on import), then count how often two or three agree within 60 minutes.

    SMC_DATASET=duka python aut14_combo.py gen S1|S2|S3
    SMC_DATASET=duka python aut14_combo.py step0
EXAM is never read: every frame is clipped with autopsy_lib.clip to EXPLORE/VALIDATE/STRESS.
"""
import os, sys, json
os.environ.setdefault("SMC_DATASET", "duka")
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                                  # noqa: E402
import autopsy_lib as A                             # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT14_combo")
os.makedirs(OUT, exist_ok=True)
STAGES3 = ("EXPLORE", "VALIDATE", "STRESS")


def _keep(df, extra=()):
    df = df.copy()
    df["t_signal"] = pd.to_datetime(df.t_signal)
    out = []
    for s in STAGES3:
        c = A.clip(df, s); c["stage"] = s; out.append(c)
    o = pd.concat(out, ignore_index=True)
    return o[["t_signal", "side", "stage", *extra]].sort_values("t_signal").reset_index(drop=True)


def gen(which):
    if which == "S1":
        import aut02_sweep as M
        rows = []
        for st in STAGES3:
            M.set_window(st)
            o = M.sweep_signals(M.SYM, "M30", 20, st)
            m = M.REFINE["R1_stretched"](o)
            rows.append(o[m][["t_signal", "side"]].assign(stage=st))
        d = pd.concat(rows, ignore_index=True)
        d = _keep(d)
    elif which == "S2":
        import aut03_breakretest as M
        M.patch_costs(M.SPREAD_BASE, M.SLIP_BASE)
        L = M.build_levels()
        d = M.feature_table(L, "retest_failed_fade")
        d = _keep(d)
    elif which == "S3":
        import aut04_orb as M
        ev = M.load_events("close")
        ev = ev[(ev.side != 0) & (ev.sess == "LON")]
        d = _keep(ev)
    d.to_parquet(os.path.join(OUT, "signals_%s.parquet" % which), index=False)
    print(which, d.groupby("stage").size().to_dict(), d.groupby(["stage", "side"]).size().unstack().to_dict())



# ----------------------------------------------------------------------------- step 0
WIN = pd.Timedelta(minutes=60)


def load_sigs():
    return {k: pd.read_parquet(os.path.join(OUT, "signals_%s.parquet" % k)) for k in ("S1", "S2", "S3")}


def partner_before(x, y, win=WIN, shift=None):
    """For every row of x: True if y has a SAME-side signal in (t-win, t]. Causal: y is known at t.
    `shift` moves y's clock by a timedelta (placebo)."""
    ok = np.zeros(len(x), bool)
    for sd in (1, -1):
        xi = np.flatnonzero(x.side.values == sd)
        yt = np.sort(y.t_signal.values[y.side.values == sd])
        if shift is not None:
            yt = np.sort(yt + np.timedelta64(shift))
        xt = x.t_signal.values[xi]
        hi = np.searchsorted(yt, xt, "right")
        lo = np.searchsorted(yt, xt - np.timedelta64(win), "right")
        ok[xi] = hi > lo
    return ok


def combo_pair(x, y, shift=None):
    """Combo events of the pair: the LATER signal of each agreeing couple, deduplicated by (time, side)."""
    a = x[partner_before(x, y, shift=shift)][["t_signal", "side", "stage"]]
    b = y[partner_before(y, x)][["t_signal", "side", "stage"]] if shift is None else \
        y.assign(t_signal=y.t_signal + shift)[partner_before(y.assign(t_signal=y.t_signal + shift), x)][["t_signal", "side", "stage"]]
    return pd.concat([a, b]).drop_duplicates(["t_signal", "side"])


def step0():
    S = load_sigs()
    rows = []
    rng = np.random.default_rng(14)
    pairs = [("S1", "S3"), ("S1", "S2"), ("S2", "S3")]
    for st in STAGES3:
        s = {k: v[v.stage == st].reset_index(drop=True) for k, v in S.items()}
        for a, b in pairs:
            ev = combo_pair(s[a], s[b])
            # placebo: slide the second signal by whole days (time of day kept) 20 times
            pl = []
            for _ in range(20):
                k = int(rng.integers(7, 60)) * (1 if rng.random() < .5 else -1)
                sh = pd.Timedelta(days=k)
                bb = s[b].assign(t_signal=s[b].t_signal + sh)
                aa_ok = partner_before(s[a], bb)
                bb_ok = partner_before(bb, s[a])
                pl.append(int(aa_ok.sum() + bb_ok.sum()))
            rows.append(dict(stage=st, pair=a + "+" + b, n_a=len(s[a]), n_b=len(s[b]),
                             agree_same_dir_60m=len(ev), placebo_mean=round(float(np.mean(pl)), 1),
                             placebo_sd=round(float(np.std(pl)), 1),
                             excess=round(len(ev) - float(np.mean(pl)), 1)))
        # triple: S1+S3 combo event that also has a same-side S2 within 60 min before the later one
        e13 = combo_pair(s["S1"], s["S3"])
        t3 = partner_before(e13.assign(side=e13.side), s["S2"]) if len(e13) else np.array([], bool)
        rows.append(dict(stage=st, pair="S1+S2+S3 (S1+S3 couple and S2 same side)", n_a=len(e13), n_b=len(s["S2"]),
                         agree_same_dir_60m=int(t3.sum()), placebo_mean=None, placebo_sd=None, excess=None))
        # opposite-direction S1 vs S3 (S1 fades, S3 follows) for context
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(OUT, "step0_overlap.csv"), index=False)
    print(d.to_string())
    return d




# ----------------------------------------------------------------------------- feed + costs
SYM = "XAUUSD"
EXTRA_SPR = 0.05                 # price units injected into the all-zero duka gold spread (= $0.50 at 0.10 lot)
HOLD_MIN = 480
SHAPES = {"A": (1.5, 1.0), "B": (2.0, 2.0)}
_ORIG_BARS = C.bars
_MEMO = {}


def bars(sym, tf):
    key = (EXTRA_SPR, sym, tf)
    if key in _MEMO:
        return _MEMO[key]
    b = _ORIG_BARS(sym, tf)
    b, _ = A.screen_bars(b)
    b = b.copy()
    if sym == SYM and EXTRA_SPR:
        b["spr"] = b.spr.values + EXTRA_SPR
    for c in ("time", "tclose"):
        b[c] = b[c].astype("datetime64[ns]")
    _MEMO[key] = b
    return b


def use_costs(extra=None):
    """Install the spread-injecting bars wrapper (common.py untouched)."""
    global EXTRA_SPR
    if extra is not None:
        EXTRA_SPR = extra
    C.bars = bars


# ----------------------------------------------------------------------------- combination rules
def pair_events(x, y):
    """Later-signal rows of the agreeing couples (same side, <= 60 min apart)."""
    a = x[partner_before(x, y)]
    b = y[partner_before(y, x)]
    return pd.concat([a, b])


def combos(S, stage, rule):
    s = {k: v[v.stage == stage].reset_index(drop=True).assign(comp=k) for k, v in S.items()}
    parts = []
    if rule in ("C1", "C3"):
        parts.append(pair_events(s["S1"], s["S2"]).assign(pair="S1+S2"))
    if rule in ("C2", "C3"):
        parts.append(pair_events(s["S2"], s["S3"]).assign(pair="S2+S3"))
    if rule == "C3":
        parts.append(pair_events(s["S1"], s["S3"]).assign(pair="S1+S3"))
    d = pd.concat(parts, ignore_index=True)
    d = d.sort_values(["t_signal", "side"], kind="stable")
    d = d.drop_duplicates(["t_signal", "side"]).reset_index(drop=True)
    keep, last = [], {1: pd.Timestamp("1900-01-01"), -1: pd.Timestamp("1900-01-01")}
    for t, sd in zip(d.t_signal, d.side):
        if t - last[sd] >= WIN:
            keep.append(True)
            last[sd] = t
        else:
            keep.append(False)
    d = d[keep].reset_index(drop=True)
    return d[["t_signal", "side", "stage", "comp", "pair"]]


# ----------------------------------------------------------------------------- orders, simulate, controls
def orders(sig, shape, hold=HOLD_MIN, twin=False):
    """Market order at the first M1 open at/after t_signal. Barriers hang off the SIGNAL price
    (last closed M1 close before the signal), so a worse fill or spread cannot move them."""
    tg, st = SHAPES[shape] if isinstance(shape, str) else shape
    b = bars(SYM, "M1")
    t1 = b.time.values
    ts = pd.to_datetime(sig.t_signal).values
    a = np.searchsorted(t1, ts, "left")
    ok = (a >= 1) & (a < len(t1) - 2)
    sig = sig[ok].reset_index(drop=True)
    a = a[ok]
    ref = b.close.values[a - 1]
    at = A.atr_at(SYM, "M15", sig.t_signal.values)
    sd = sig.side.values.astype(int)
    shift = np.timedelta64(1, "m") if twin else np.timedelta64(0, "m")
    o = pd.DataFrame({
        "t_signal": pd.to_datetime(sig.t_signal).values - shift,
        "side": sd, "entry_type": "market", "entry": np.nan,
        "expire": pd.to_datetime(sig.t_signal).values + np.timedelta64(30, "m"),
        "sl": ref - sd * st * at, "tp": ref + sd * tg * at,
        "be_at_r": np.nan, "max_hold_min": float(hold), "atr15": at, "ref": ref,
    })
    for c in ("comp", "pair"):
        if c in sig.columns:
            o[c] = sig[c].values
    o["sig_time"] = pd.to_datetime(sig.t_signal).values
    return o[np.isfinite(o.atr15)].reset_index(drop=True)


def sim(sig, shape, **kw):
    o = orders(sig, shape, **kw)
    if len(o) == 0:
        return pd.DataFrame()
    t = C.simulate(SYM, o, one_at_a_time=False)
    return t[t.status == "filled"].copy()


def book(t):
    if len(t) == 0:
        return dict(trades=0)
    gp = t.net_pips[t.net_pips > 0].sum()
    gl = -t.net_pips[t.net_pips < 0].sum()
    d = dict(trades=int(len(t)), right_in_100=round(100 * float((t.net_pips > 0).mean()), 2),
             profit_factor=round(float(gp / gl), 3) if gl > 0 else None,
             usd_per_trade=round(float(t.usd_01.mean()), 3))
    for nm, sd in (("buy", 1), ("sell", -1)):
        g = t[t.side == sd]
        d[nm + "_trades"] = int(len(g))
        d[nm + "_right_in_100"] = round(100 * float((g.net_pips > 0).mean()), 2) if len(g) else None
        d[nm + "_usd_per_trade"] = round(float(g.usd_01.mean()), 3) if len(g) else None
    d["give_or_take_usd"] = round(float(2 * t.usd_01.std(ddof=1) / np.sqrt(len(t))), 2) if len(t) > 2 else None
    return d


def breakeven(t, shape):
    tg, st = SHAPES[shape]
    return round(float(np.mean([A.breakeven_rate(SYM, a, tg, st, spread=EXTRA_SPR) for a in t.atr15.values])), 2)


def luck(stage, hrs, shape, n=20000, seeds=(0, 1, 2, 3, 4)):
    tg, st = SHAPES[shape]
    acc, usd = [], []
    for sd in seeds:
        lb = A.luck_bar(SYM, n, tg, st, stage, seed=sd, hours=sorted(set(int(h) for h in hrs)), max_hold_min=HOLD_MIN)
        lb = lb[lb.status == "filled"]
        acc.append(100 * float((lb.net_pips > 0).mean()))
        usd.append(float(lb.usd_01.mean()))
    return dict(n=n, seeds=list(seeds), hours=sorted(set(int(h) for h in hrs)),
                right_in_100=round(float(np.mean(acc)), 2), seed_band_points=round(max(acc) - min(acc), 2),
                usd_per_trade=round(float(np.mean(usd)), 3),
                shape_matched=True, hour_matched=True, hold_matched=True)


def day_shift_null(sig, shape, draws=5, seed=3):
    """Same COUNT of random-direction entries at the same clock minutes, moved by whole days (7-60)."""
    rng = np.random.default_rng(seed)
    acc, usd = [], []
    for _ in range(draws):
        sh = rng.integers(7, 60, len(sig)) * rng.choice([-1, 1], len(sig))
        r = sig.copy()
        r["t_signal"] = pd.to_datetime(sig.t_signal).values + sh.astype("timedelta64[D]")
        r["side"] = rng.choice([1, -1], len(sig))
        t = sim(r, shape)
        acc.append(100 * float((t.net_pips > 0).mean()))
        usd.append(float(t.usd_01.mean()))
    return dict(draws=draws, right_in_100=round(float(np.mean(acc)), 2), usd_per_trade=round(float(np.mean(usd)), 3),
                band_points=round(max(acc) - min(acc), 2), band_usd=round(max(usd) - min(usd), 3))


def controls(sig, shape, stage, base, do_luck=True):
    L = sim(sig.assign(side=1), shape)
    S = sim(sig.assign(side=-1), shape)
    cu, ca = [], []
    for sd in (7, 8, 9):
        r = np.random.default_rng(sd)
        c = sim(sig.assign(side=r.choice([1, -1], len(sig))), shape)
        cu.append(float(c.usd_01.mean()))
        ca.append(100 * float((c.net_pips > 0).mean()))
    out = dict(all_long_right_in_100=round(100 * float((L.net_pips > 0).mean()), 2), all_long_usd=round(float(L.usd_01.mean()), 3),
               all_short_right_in_100=round(100 * float((S.net_pips > 0).mean()), 2), all_short_usd=round(float(S.usd_01.mean()), 3),
               coin_right_in_100=round(float(np.mean(ca)), 2), coin_usd=round(float(np.mean(cu)), 3),
               rule_minus_coin_usd=round(base["usd_per_trade"] - float(np.mean(cu)), 3),
               rule_minus_coin_points=round(base["right_in_100"] - float(np.mean(ca)), 2))
    out["day_shift_null"] = day_shift_null(sig, shape)
    if do_luck:
        hrs = pd.to_datetime(sig.t_signal).dt.hour.unique()
        lb = luck(stage, hrs, shape)
        out["luck_bar"] = lb
        out["vs_luck_points"] = round(base["right_in_100"] - lb["right_in_100"], 2)
        out["vs_luck_usd"] = round(base["usd_per_trade"] - lb["usd_per_trade"], 3)
    return out


def lookahead(sig, shape):
    cl = sim(sig, shape)
    tw = sim(sig, shape, twin=True)
    return dict(clean_right_in_100=round(100 * float((cl.net_pips > 0).mean()), 2), clean_usd=round(float(cl.usd_01.mean()), 3),
                twin_right_in_100=round(100 * float((tw.net_pips > 0).mean()), 2), twin_usd=round(float(tw.usd_01.mean()), 3),
                lookahead_points=round(100 * float((tw.net_pips > 0).mean() - (cl.net_pips > 0).mean()), 2),
                lookahead_usd=round(float(tw.usd_01.mean() - cl.usd_01.mean()), 3))


def cell(sig, stage, shape, label, full=True):
    t = sim(sig, shape)
    b = book(t)
    b["stage"] = stage
    b["label"] = label
    b["shape"] = shape
    if len(t):
        b["breakeven_needed"] = breakeven(t, shape)
        b["avg_hold_h"] = round(float(t.hold_h.mean()), 2)
    if full and len(t):
        b["controls"] = controls(sig, shape, stage, b)
        b["lookahead"] = lookahead(sig, shape)
    return b, t


def jdump(name, obj):
    def enc(x):
        if isinstance(x, (np.floating, np.integer)):
            return float(x)
        return str(x)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, default=enc)
    print("wrote", name, flush=True)


def explore():
    use_costs()
    A.gate("EXPLORE")
    S = load_sigs()
    res = {"cells": [], "components": []}
    tr = {}
    for rule in ("C1", "C2", "C3"):
        sig = combos(S, "EXPLORE", rule)
        print(rule, len(sig), sig.pair.value_counts().to_dict(), flush=True)
        sig.to_parquet(os.path.join(OUT, "combo_signals_EXPLORE_%s.parquet" % rule), index=False)
        for sh in SHAPES:
            b, t = cell(sig, "EXPLORE", sh, rule)
            b["pairs"] = sig.pair.value_counts().to_dict()
            res["cells"].append(b)
            tr[(rule, sh)] = t
            print(rule, sh, {k: v for k, v in b.items() if k not in ("controls", "lookahead")}, flush=True)
            jdump("explore_results.json", res)
    for k in ("S1", "S2", "S3"):
        sig = S[k][S[k].stage == "EXPLORE"].assign(comp=k, pair=k)
        for sh in SHAPES:
            b, t = cell(sig, "EXPLORE", sh, "component_" + k, full=(k != "S2"))
            res["components"].append(b)
            tr[(k, sh)] = t
            print(k, sh, {kk: v for kk, v in b.items() if kk not in ("controls", "lookahead")}, flush=True)
            jdump("explore_results.json", res)
    for (r, sh), t in tr.items():
        t.to_parquet(os.path.join(OUT, "tr_EXPLORE_%s_%s.parquet" % (r, sh)), index=False)


def indep():
    """Are the three signals really different? EXPLORE only. Writes independence_EXPLORE.json"""
    use_costs(); A.gate("EXPLORE")
    S = load_sigs(); b = bars(SYM, "M1"); t1 = b.time.values; cl = b.close.values
    out = {}
    # (a) fade-the-last-move: share of signals whose side opposes the prior 30-minute move
    fade = {}
    for k, v in S.items():
        v = v[v.stage == "EXPLORE"]
        a = np.searchsorted(t1, v.t_signal.values, "left")
        ok = a > 31
        mv = cl[a[ok] - 1] - cl[a[ok] - 31]
        sd = v.side.values[ok]
        fade[k] = dict(n=int(ok.sum()), share_against_prior_30m_move=round(float((np.sign(mv) == -sd).mean()), 3),
                       share_with=round(float((np.sign(mv) == sd).mean()), 3))
    out["fade_the_last_move"] = fade
    # (b) entry coincidence and outcome correlation of agreeing couples (shape A and B outcomes)
    tr = {sh: {k: pd.read_parquet(os.path.join(OUT, "tr_EXPLORE_%s_%s.parquet" % (k, sh))) for k in ("S1", "S2", "S3")} for sh in SHAPES}
    couples = {}
    for a_, b_ in (("S1", "S2"), ("S2", "S3"), ("S1", "S3")):
        x = S[a_][S[a_].stage == "EXPLORE"]; y = S[b_][S[b_].stage == "EXPLORE"]
        rows = []
        for sd in (1, -1):
            xs = x[x.side == sd].t_signal.values; ys = np.sort(y[y.side == sd].t_signal.values)
            for t in xs:                                  # nearest same-side y within 60 min either side
                i = np.searchsorted(ys, t)
                cand = [ys[j] for j in (i - 1, i) if 0 <= j < len(ys) and abs((ys[j] - t) / np.timedelta64(1, "m")) <= 60]
                if cand:
                    u = min(cand, key=lambda z: abs(z - t))
                    rows.append((t, u, sd, abs((u - t) / np.timedelta64(1, "m"))))
        c = pd.DataFrame(rows, columns=["tx", "ty", "side", "gap"])
        res = dict(couples=len(c), median_gap_min=float(c.gap.median()) if len(c) else None,
                   same_minute_share=round(float((c.gap == 0).mean()), 3) if len(c) else None)
        for sh in SHAPES:
            tx = tr[sh][a_][["sig_time", "side", "net_pips"]].rename(columns={"sig_time": "tx", "net_pips": "px"})
            ty = tr[sh][b_][["sig_time", "side", "net_pips"]].rename(columns={"sig_time": "ty", "net_pips": "py"})
            m = c.merge(tx, on=["tx", "side"]).merge(ty, on=["ty", "side"])
            if len(m) > 20:
                wx, wy = (m.px > 0).astype(float), (m.py > 0).astype(float)
                res["win_corr_shape_" + sh] = round(float(np.corrcoef(wx, wy)[0, 1]), 3)
                res["both_win_share_shape_" + sh] = round(float(((wx == 1) & (wy == 1)).mean()), 3)
                res["win_x_shape_" + sh] = round(float(wx.mean()), 3); res["win_y_shape_" + sh] = round(float(wy.mean()), 3)
        couples[a_ + "+" + b_] = res
    out["couples"] = couples
    jdump("independence_EXPLORE.json", out)
    print(json.dumps(out, indent=1))


CARRIED = [("C1", "B"), ("C3", "B"), ("C2", "B")]     # fixed from EXPLORE ranking (usd minus luck bar), see report


def stage_run(stage, adverse=(0.10, 0.20)):
    """Run the carried cells ONCE on a later stage, plus the components alone at the same shape."""
    use_costs(0.05)
    A.gate(stage)
    S = load_sigs()
    res = {"cells": [], "components": [], "adverse": []}
    for rule, sh in CARRIED:
        sig = combos(S, stage, rule)
        sig.to_parquet(os.path.join(OUT, "combo_signals_%s_%s.parquet" % (stage, rule)), index=False)
        b, t = cell(sig, stage, sh, rule)
        b["pairs"] = sig.pair.value_counts().to_dict()
        res["cells"].append(b)
        t.to_parquet(os.path.join(OUT, "tr_%s_%s_%s.parquet" % (stage, rule, sh)), index=False)
        print(stage, rule, sh, {k: v for k, v in b.items() if k not in ("controls", "lookahead")}, flush=True)
        jdump("%s_results.json" % stage.lower(), res)
    for k in ("S1", "S2", "S3"):
        sig = S[k][S[k].stage == stage].assign(comp=k, pair=k)
        b, t = cell(sig, stage, "B", "component_" + k, full=(k != "S2"))
        res["components"].append(b)
        t.to_parquet(os.path.join(OUT, "tr_%s_%s_B.parquet" % (stage, k)), index=False)
        print(stage, k, {kk: v for kk, v in b.items() if kk not in ("controls", "lookahead")}, flush=True)
        jdump("%s_results.json" % stage.lower(), res)
    if stage == "STRESS":
        for sp in adverse:
            use_costs(sp)
            for rule, sh in CARRIED:
                sig = combos(S, stage, rule)
                b, t = cell(sig, stage, sh, rule, full=False)
                b["injected_spread"] = sp
                res["adverse"].append(b)
                print("ADV", sp, rule, b, flush=True)
                jdump("stress_results.json", res)
        use_costs(0.05)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        gen(sys.argv[2])
    elif cmd == "step0":
        step0()
    elif cmd == "explore":
        explore()
    elif cmd == "indep":
        indep()
    elif cmd in ("validate", "stress"):
        stage_run(cmd.upper())
