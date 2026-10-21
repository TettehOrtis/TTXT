"""AUT13 - gold (and FX) lagging the US dollar. Lead-lag against a dollar index built from the USD pairs.

Governing documents: bt/AUT11_14_BRIEF.md, bt/AUTOPSY_BRIEF.md. Writes ONLY results/AUT13_leadlag/.
EXAM is never opened (AUTOPSY_EXAM_UNLOCK is never set), data_zero is never read.

Design (all fixed before any result was looked at):
  dollar index  = equal-weight mean of 1-minute log returns of the USD pairs, sign-flipped so that UP = dollar stronger
                  (EURUSD GBPUSD AUDUSD NZDUSD inverted; USDJPY USDCAD USDCHF as they are).
                  For a target that is itself one of the pairs, that pair is left out of its own index.
  beta          = least squares slope (no intercept) of the target's 1-minute return on the dollar's 1-minute return,
                  over the 6,900 target bars (about 5 trading days) that END k bars BEFORE the decision bar.
                  Never touches the window being measured, never touches the future.
  gap_k         = target log return over the last k minutes - beta x dollar log return over the same k minutes
  signal        = |gap_k| >= 2.0 x its own trailing RMS (same 6,900-bar window, ends one bar before the decision)
  rule          = FADE the gap: gap > 0 (target ran ahead of the dollar) -> sell, gap < 0 -> buy.
  decision      = close of bar i. Order is active from t_signal = close time of bar i, fills at the open of bar i+1.
  barriers      = 1.0 x M15 ATR stop, 1.0 x M15 ATR target, both hung off the DECISION BAR'S CLOSE (signal price, not the
                  fill), so an adverse-fill stress really moves the entry and nothing else. Time stop = hold.
  cooldown      = a new signal is ignored until the previous trade's hold has passed (no overlapping duplicates).
  costs         = deducted ONCE, at close, inside common.simulate. Gold spread 0.05 price units + $1.10 commission
                  = $1.60 round trip at 0.10 lot. FX: repaired median spread + project commission (EURUSD 0.5 pip).
"""
import os
import sys
import time
import json

os.environ.setdefault("SMC_DATASET", "duka")
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                                                        # noqa: E402
import autopsy_lib as A                                                   # noqa: E402

assert "AUTOPSY_EXAM_UNLOCK" not in os.environ, "EXAM must stay sealed"
OUT = os.path.join(C.RESULTS, "AUT13_leadlag")
os.makedirs(OUT, exist_ok=True)

USD_PAIRS = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1, "USDJPY": +1, "USDCAD": +1, "USDCHF": +1}
W = 6900                    # beta / sigma window in target bars (about 5 trading days of M1)
ZTRIG = 2.0
KS = (1, 3, 5, 15)
HOLDS = (5, 15, 30, 60)
STALE_TOL = 2               # minutes a basket pair's last close may lag the target bar and still be used
MIN_PAIRS = 4
SHAPE = (1.0, 1.0)          # target, stop in M15 ATR
BASE_K, BASE_H = 5, 15      # the pre-declared central cell used for the baseline variant

# ---------------------------------------------------------------- bars (screened, spread repaired, memoised, optional stress)
_ORIG_BARS = C.bars
_MEMO = {}
EXTRA_ADD = {}              # sym -> extra price units of spread added on M1 (adverse-cost stress)


def bars(sym, tf):
    key = (sym, tf, EXTRA_ADD.get(sym, 0.0) if tf == "M1" else 0.0)
    if key in _MEMO:
        return _MEMO[key]
    b = _ORIG_BARS(sym, tf)
    b, _ = A.screen_bars(b)
    b = b.copy()
    if tf == "M1":
        b, _ = A._repair_spread(b, sym)
        if EXTRA_ADD.get(sym):
            b["spr"] = b.spr.values + EXTRA_ADD[sym]
    for c in ("time", "tclose"):
        b[c] = b[c].astype("datetime64[ns]")
    _MEMO[key] = b
    return b


C.bars = bars


def set_stress(sym, add):
    EXTRA_ADD.clear()
    if add:
        EXTRA_ADD[sym] = add


# ---------------------------------------------------------------- rolling sums
def _roll(x, w, shift=0):
    """Sum of x over the w positions ending at i-shift (inclusive). NaN->0 must already be done."""
    cs = np.concatenate([[0.0], np.cumsum(x)])
    n = len(x)
    i = np.arange(n)
    hi = i - shift + 1
    lo = hi - w
    out = np.full(n, np.nan)
    ok = (lo >= 0) & (hi <= n)
    out[ok] = cs[hi[ok]] - cs[lo[ok]]
    return out


# ---------------------------------------------------------------- the dollar index aligned to a target
def basket_for(target):
    return {p: s for p, s in USD_PAIRS.items() if p != target}


def aligned_logs(target):
    """Log close of each basket pair, matched to the target's own M1 timestamps by TIME.
    A basket close is used only if its bar is at most STALE_TOL minutes older than the target bar (never newer)."""
    tb = bars(target, "M1")
    tt = tb.time.values
    out = {}
    for p in basket_for(target):
        b = bars(p, "M1")
        pos = np.searchsorted(b.time.values, tt, "right") - 1
        okp = pos >= 0
        pos = np.clip(pos, 0, len(b) - 1)
        age = (tt - b.time.values[pos]) / np.timedelta64(1, "m")
        L = np.log(b.close.values[pos])
        L[~okp | (age > STALE_TOL)] = np.nan
        out[p] = (L, (age == 0) & okp)
    return out


def build_arrays(target):
    """Everything vectorised for one target: 1-minute returns, the dollar index and its k-minute versions."""
    tb = bars(target, "M1")
    t = tb.time.values
    n = len(tb)
    logT = np.log(tb.close.values)
    al = aligned_logs(target)
    names = list(al)
    sgn = np.array([USD_PAIRS[p] for p in names], float)
    LB = np.vstack([al[p][0] for p in names])                    # (m, n)
    fresh = np.vstack([al[p][1] for p in names])
    tmin = t.astype("datetime64[m]").astype(np.int64)
    arr = dict(t=t, n=n, tb=tb, logT=logT, LB=LB, sgn=sgn, names=names, fresh=fresh, tmin=tmin)

    def kret(k):
        """target and dollar log returns over k minutes ending at bar i (NaN unless bar i-k is exactly k minutes earlier)."""
        contig = np.full(n, False)
        contig[k:] = (tmin[k:] - tmin[:-k]) == k
        T = np.full(n, np.nan)
        T[k:] = logT[k:] - logT[:-k]
        d = np.full(LB.shape, np.nan)
        d[:, k:] = LB[:, k:] - LB[:, :-k]
        d = d * sgn[:, None]
        valid = np.isfinite(d)
        cnt = valid.sum(0)
        D = np.where(cnt >= MIN_PAIRS, np.nansum(d, 0) / np.maximum(cnt, 1), np.nan)
        T[~contig] = np.nan
        D[~contig] = np.nan
        with np.errstate(invalid="ignore"):
            agree = np.where(cnt > 0, (np.sign(d) == np.sign(D)[None, :]).sum(0) / np.maximum(cnt, 1), np.nan)
        return T, D, cnt, agree

    arr["kret"] = kret
    T1, D1, c1, _ = kret(1)
    arr["T1"], arr["D1"], arr["c1"] = T1, D1, c1
    both = np.isfinite(T1) & np.isfinite(D1)
    x = np.where(both, D1, 0.0)
    y = np.where(both, T1, 0.0)
    arr["both"] = both
    arr["cums"] = dict(xy=x * y, xx=x * x, yy=y * y, n=both.astype(float))
    return arr


def beta_series(arr, k):
    """beta, past-window correlation and the two 1-minute stds, all from the W bars ending at i-k."""
    s = {nm: _roll(v, W, shift=k) for nm, v in arr["cums"].items()}
    nn = s["n"]
    with np.errstate(invalid="ignore", divide="ignore"):
        beta = np.where(nn >= 1500, s["xy"] / np.where(s["xx"] > 0, s["xx"], np.nan), np.nan)
        corr = np.where(nn >= 1500, s["xy"] / np.sqrt(np.where(s["xx"] * s["yy"] > 0, s["xx"] * s["yy"], np.nan)), np.nan)
    sdD = np.sqrt(s["xx"] / np.maximum(nn, 1))
    sdT = np.sqrt(s["yy"] / np.maximum(nn, 1))
    return beta, corr, sdD, sdT


def signals(target, arr=None, ks=KS, mode="gap"):
    """One row per (bar, k) where |gap| >= ZTRIG sigma. Cached to parquet.
    mode="own" is a CONTROL: no dollar at all, the gap is the target's own k-minute return (plain short-term reversal)."""
    f = os.path.join(OUT, "signals_%s%s.parquet" % (target, "" if mode == "gap" else "_" + mode))
    if os.path.exists(f):
        return pd.read_parquet(f)
    arr = arr or build_arrays(target)
    tb = arr["tb"]
    n = arr["n"]
    frames = []
    nxt = np.zeros(n, bool)
    nxt[:-1] = (arr["tmin"][1:] - arr["tmin"][:-1]) == 1
    for k in ks:
        T, D, cnt, agree = arr["kret"](k)
        beta, corr, sdD, sdT = beta_series(arr, k)
        gap = (T - beta * D) if mode == "gap" else T.copy()
        gv = np.isfinite(gap)
        g0 = np.where(gv, gap, 0.0)
        # trailing RMS of the gap over the W bars ending at i-1 (every gap there only uses data up to i-1)
        s2 = _roll(g0 * g0, W, shift=1)
        nv = _roll(gv.astype(float), W, shift=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            sig = np.sqrt(s2 / np.maximum(nv, 1))
            sig[nv < 1500] = np.nan
            z = gap / sig
        m = np.isfinite(z) & (np.abs(z) >= ZTRIG) & nxt        # next bar is the next clock minute -> fill is 1 minute later
        i = np.flatnonzero(m)
        j = np.minimum(i + 1, n - 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            fr = pd.DataFrame(dict(
                k=k, i=i, t_signal=pd.to_datetime(tb.tclose.values[i]),
                ref=tb.close.values[i], nxt_open=tb.open.values[j], spr_fill=tb.spr.values[j],
                gap=gap[i], z=z[i], sig=sig[i], T_k=T[i], D_k=D[i], beta=beta[i], corr=corr[i],
                z_D=D[i] / (np.sqrt(k) * sdD[i]), z_T=T[i] / (np.sqrt(k) * sdT[i]),
                dollar_share=np.abs(beta[i] * D[i]) / (np.abs(beta[i] * D[i]) + np.abs(T[i])),
                n_pairs=cnt[i], breadth=agree[i]))
        frames.append(fr)
    ev = pd.concat(frames, ignore_index=True)
    ev["side_fade"] = -np.sign(ev.gap).astype(int)
    ev["hour"] = ev.t_signal.dt.hour
    ev["dow"] = ev.t_signal.dt.dayofweek
    ev.to_parquet(f, index=False)
    return ev


# ================================================================ events -> orders -> simulate
STAGE_LIST = ("EXPLORE", "VALIDATE", "STRESS")
_CACHE = {}


def events(target, mode="gap"):
    """Signal table with the M15 ATR of the last closed bar attached (no peeking)."""
    key = (target, mode)
    if key not in _CACHE:
        ev = signals(target, mode=mode)
        ev = ev.copy()
        ev["atr15"] = A.atr_at(target, "M15", ev.t_signal.values)
        ev = ev[np.isfinite(ev.atr15) & (ev.atr15 > 0)].sort_values(["k", "i"]).reset_index(drop=True)
        ev["tmin"] = ev.t_signal.values.astype("datetime64[m]").astype(np.int64)
        _CACHE[key] = ev
    return _CACHE[key]


def stage_events(ev, stage):
    a, b = A.STAGES[A.gate(stage)]
    return ev[(ev.t_signal >= a) & (ev.t_signal < b)]


def cooldown(e, hold):
    """Greedy: keep a signal only if at least `hold` minutes have passed since the last KEPT one. e sorted by time."""
    tm = e.tmin.values
    keep = np.zeros(len(e), bool)
    last = -10 ** 12
    for j in range(len(tm)):
        if tm[j] - last >= hold:
            keep[j] = True
            last = tm[j]
    return e[keep]


def cell(ev, stage, k, hold):
    """The rule's own events for one (k, hold) cell in one stage, after the cooldown."""
    e = stage_events(ev[ev.k == k], stage).sort_values("tmin")
    return cooldown(e, hold)


def make_orders(t_sig, ref, atr, side, hold, shape=SHAPE, delay=0, extra=None):
    t_sig = pd.to_datetime(t_sig) + pd.Timedelta(minutes=delay)
    side = np.asarray(side).astype(int)
    o = pd.DataFrame({
        "t_signal": t_sig, "side": side, "entry_type": "market", "entry": np.nan,
        "expire": t_sig + pd.Timedelta(minutes=hold),
        "sl": np.asarray(ref) - side * np.asarray(atr) * shape[1],
        "tp": np.asarray(ref) + side * np.asarray(atr) * shape[0],
        "be_at_r": np.nan, "max_hold_min": float(hold), "anchor_px": np.asarray(ref)})
    if extra is not None:
        for c in extra.columns:
            if c not in o.columns:
                o[c] = extra[c].values
    return o


def run(target, e, hold, side=None, delay=0, shape=SHAPE):
    sd = e.side_fade.values if side is None else (np.full(len(e), side) if np.isscalar(side) else side)
    o = make_orders(e.t_signal.values, e.ref.values, e.atr15.values, sd, hold, shape, delay, extra=e)
    return C.simulate(target, o, one_at_a_time=False)


def filled(tr):
    return tr[tr.status == "filled"].copy() if len(tr) else tr


def book(t, target):
    t = filled(t) if "status" in t else t
    if len(t) == 0:
        return dict(trades=0)
    gp = t.net_pips[t.net_pips > 0].sum()
    gl = -t.net_pips[t.net_pips < 0].sum()
    d = dict(trades=int(len(t)), right_in_100=round(100 * float((t.net_pips > 0).mean()), 2),
             profit_factor=round(float(gp / gl), 3) if gl > 0 else None,
             usd_per_trade=round(float(t.usd_01.mean()), 3),
             avg_hold_min=round(float(t.hold_h.mean() * 60), 1))
    for nm, sd in (("buy", 1), ("sell", -1)):
        g = t[t.side == sd]
        d[nm + "_trades"] = int(len(g))
        d[nm + "_right_in_100"] = round(100 * float((g.net_pips > 0).mean()), 2) if len(g) else None
        d[nm + "_usd_per_trade"] = round(float(g.usd_01.mean()), 3) if len(g) else None
    return d


def round_trip_usd(target, spr_fill_mean):
    return (C.COMM[target] + spr_fill_mean / C.pip(target)) * C.USD_PIP_01[target]


# ---------------------------------------------------------------- random-entry luck bar (anchored the same way as the rule)
_RB = {}


def _rand_pool(target):
    if target not in _RB:
        tb = bars(target, "M1")
        tm = tb.time.values.astype("datetime64[m]").astype(np.int64)
        nxt = np.zeros(len(tb), bool)
        nxt[:-1] = (tm[1:] - tm[:-1]) == 1
        _RB[target] = dict(tb=tb, nxt=nxt, hr=tb.tclose.dt.hour.values, tclose=tb.tclose.values)
    return _RB[target]


def luck(target, stage, hold, hours, n=20000, seeds=(0, 1, 2, 3, 4), shape=SHAPE):
    """Random entries at the SAME hours (same hour mix as the rule), same shape, same hold, same anchoring and costs."""
    A.gate(stage)
    p = _rand_pool(target)
    a, b = A.STAGES[stage]
    inwin = (p["tclose"] >= np.datetime64(a)) & (p["tclose"] < np.datetime64(b)) & p["nxt"]
    uh, cnt = np.unique(np.asarray(hours), return_counts=True)
    share = cnt / cnt.sum()
    pools = {h: np.flatnonzero(inwin & (p["hr"] == h)) for h in uh}
    res = []
    for sd_ in seeds:
        rng = np.random.default_rng(1000 + sd_)
        pick = []
        for h, sh in zip(uh, share):
            m = int(round(n * sh))
            if len(pools[h]) and m:
                pick.append(rng.choice(pools[h], size=min(m, len(pools[h])), replace=False))
        pick = np.sort(np.concatenate(pick))
        tb = p["tb"]
        tcl = tb.tclose.values[pick]
        ref = tb.close.values[pick]
        atr = A.atr_at(target, "M15", tcl)
        okm = np.isfinite(atr) & (atr > 0)
        side = rng.choice([1, -1], size=len(pick))
        o = make_orders(tcl[okm], ref[okm], atr[okm], side[okm], hold, shape)
        t = filled(C.simulate(target, o, one_at_a_time=False))
        res.append(dict(right=100 * float((t.net_pips > 0).mean()), usd=float(t.usd_01.mean()), n=len(t)))
    r = pd.DataFrame(res)
    return dict(n=int(r.n.mean()), seeds=list(seeds), right_in_100=round(float(r.right.mean()), 2),
                seed_band_points=round(float(r.right.max() - r.right.min()), 2),
                seed_values=[round(x, 2) for x in r.right],
                usd_per_trade=round(float(r.usd.mean()), 3),
                usd_seed_band=round(float(r.usd.max() - r.usd.min()), 3),
                shape_matched=True, hour_matched=True, hold_matched=True, anchored_to="signal price (decision bar close)")


def controls(target, e, hold, stage, with_luck=True, n_luck=20000, seeds=(0, 1, 2, 3, 4)):
    """The four mandatory controls for one cell/stage plus the reversed rule, delay test and look-ahead twin."""
    rule = filled(run(target, e, hold))
    out = dict(rule=book(rule, target))
    out["reversed_rule"] = book(run(target, e, hold, side=-e.side_fade.values), target)
    out["always_long"] = book(run(target, e, hold, side=1), target)
    out["always_short"] = book(run(target, e, hold, side=-1), target)
    cu, ca = [], []
    for s in (7, 8, 9):
        r = np.random.default_rng(s)
        c = filled(run(target, e, hold, side=r.choice([1, -1], size=len(e))))
        cu.append(c.usd_01.mean())
        ca.append(100 * (c.net_pips > 0).mean())
    out["coin_flip"] = dict(right_in_100=round(float(np.mean(ca)), 2), usd_per_trade=round(float(np.mean(cu)), 3),
                            seeds=[7, 8, 9], usd_seed_band=round(float(max(cu) - min(cu)), 3))
    # look-ahead twin: the SAME orders but filled at the decision bar's OWN open (one minute early)
    tw = filled(run(target, e, hold, delay=-1))
    out["lookahead_twin"] = dict(book(tw, target),
                                 points=round(100 * float((tw.net_pips > 0).mean()) - out["rule"]["right_in_100"], 2),
                                 usd=round(float(tw.usd_01.mean()) - out["rule"]["usd_per_trade"], 3))
    for dly in (1, 2):                      # how fast does the gap close? the same rule entered 1 and 2 minutes later
        out["delay_%dmin" % dly] = book(run(target, e, hold, delay=dly), target)
    if with_luck and len(rule):
        out["luck_bar"] = luck(target, stage, hold, e.hour.values, n_luck, seeds)
        out["vs_luck_points"] = round(out["rule"]["right_in_100"] - out["luck_bar"]["right_in_100"], 2)
        out["vs_luck_usd"] = round(out["rule"]["usd_per_trade"] - out["luck_bar"]["usd_per_trade"], 3)
    out["rule_minus_coin_usd"] = round(out["rule"]["usd_per_trade"] - out["coin_flip"]["usd_per_trade"], 3)
    out["round_trip_usd"] = round(round_trip_usd(target, float(e.spr_fill.mean())), 3)
    return out, rule


if __name__ == "__main__":
    for tg in sys.argv[1:]:
        t0 = time.time()
        ev = signals(tg)
        print(tg, len(ev), ev.groupby("k").size().to_dict(), round(time.time() - t0, 1), "s")
