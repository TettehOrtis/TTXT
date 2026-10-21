"""PREREG addendum 23: entry rules of free bots (trillex, mad808, Stochastic Scalper, EMA 10/30 cross) tested as entries.
Usage: python aut37_botentries.py PAIR STAGE      (PAIR = XAUUSD or an FX major; open windows only)
       python aut37_botentries.py judge"""
import os, sys, glob
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut27_fx as FX
import aut01_emapull as M
import common as C

EXITS = ("E30_70", "E50_70")
REF = {"E30_70": "R3_E_E30_70", "E50_70": "R4_M30PX100_T_E20SIDE_E50_70"}
ENTRIES = ["TRI_NORMAL", "TRI_FLIP", "MAD_RSI", "STOCH", "EMA_X"]


def psar(h, l, step=0.02, mx=0.2):
    n = len(h)
    out = np.empty(n)
    up = True
    af, ep, sar = step, h[0], l[0]
    out[0] = sar
    for k in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[k - 1], l[k - 2] if k > 1 else l[k - 1])
            if l[k] < sar:
                up, sar, ep, af = False, ep, l[k], step
            elif h[k] > ep:
                ep, af = h[k], min(af + step, mx)
        else:
            sar = max(sar, h[k - 1], h[k - 2] if k > 1 else h[k - 1])
            if h[k] > sar:
                up, sar, ep, af = True, ep, h[k], step
            elif l[k] < ep:
                ep, af = l[k], min(af + step, mx)
        out[k] = sar
    return out


def wilder(x, n):
    return pd.Series(x).ewm(alpha=1.0 / n, adjust=False).mean().values


def adx14(h, l, c):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    up, dn = h - np.r_[h[0], h[:-1]], np.r_[l[0], l[:-1]] - l
    pdm, mdm = np.where((up > dn) & (up > 0), up, 0.0), np.where((dn > up) & (dn > 0), dn, 0.0)
    at = wilder(tr, 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi, mdi = 100 * wilder(pdm, 14) / at, 100 * wilder(mdm, 14) / at
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    return wilder(np.nan_to_num(dx), 14)


def window_slice(df, a, z, warm=90, tail=25):
    s0 = int(np.searchsorted(df.time.values, np.datetime64(a - pd.Timedelta(days=warm))))
    s1 = int(np.searchsorted(df.time.values, np.datetime64(z + pd.Timedelta(days=tail))))
    return s0, min(s1, len(df))


def tf_events(F, tf, fn):
    """fn(sliced bars) -> (buy_bool, sell_bool) on that slice; returns M1 decision indices and sides (M1 bar closing with the TF bar)."""
    b = C.bars(X.SYM, tf)
    s0, s1 = window_slice(b, F["a"], F["z"])
    d = b.iloc[s0:s1].reset_index(drop=True)
    buy, sell = fn(d)
    tcl = d.tclose.values
    idx_b = np.searchsorted(F["tc"], tcl[buy])
    idx_s = np.searchsorted(F["tc"], tcl[sell])
    keep_b = (idx_b < F["n"]) & (F["tc"][np.minimum(idx_b, F["n"] - 1)] == tcl[buy])
    keep_s = (idx_s < F["n"]) & (F["tc"][np.minimum(idx_s, F["n"] - 1)] == tcl[sell])
    i = np.r_[idx_b[keep_b], idx_s[keep_s]]
    side = np.r_[np.ones(keep_b.sum(), int), -np.ones(keep_s.sum(), int)]
    o = np.argsort(i, kind="stable")
    return i[o], side[o]


def tri_state(d):
    c, h, l = d.close.values, d.high.values, d.low.values
    e5, e20, e40 = M._ema(c, 5), M._ema(c, 20), M._ema(c, 40)
    s = psar(h, l)
    up = (e5 > e20) & (e20 > e40) & (s < c)
    dn = (e5 < e20) & (e20 < e40) & (s > c)
    return up, dn, s < c


def entry_signals(F, name):
    n = F["n"]
    if name in ("TRI_NORMAL", "TRI_FLIP"):
        m1 = F["m1"]
        s0, s1 = window_slice(m1, F["a"], F["z"])
        up1, dn1, below = tri_state(m1.iloc[s0:s1].reset_index(drop=True))
        b5 = C.bars(X.SYM, "M5")
        t0, t1_ = window_slice(b5, F["a"], F["z"])
        up5, dn5, _ = tri_state(b5.iloc[t0:t1_].reset_index(drop=True))
        j = C.asof_index(b5, F["tc"][s0:s1]) - t0                # last closed M5 bar of each M1 bar, inside the slice
        okj = (j >= 60) & (j < len(up5))
        jj = np.clip(j, 0, len(up5) - 1)
        a_up, a_dn = up1 & up5[jj] & okj, dn1 & dn5[jj] & okj
        if name == "TRI_NORMAL":
            ev_b = a_up & ~np.r_[False, a_up[:-1]]
            ev_s = a_dn & ~np.r_[False, a_dn[:-1]]
        else:
            b1, b2 = np.r_[False, below[:-1]], np.r_[False, False, below[:-2]]
            fresh_b = ~b2 & b1 & below
            fresh_s = b2 & ~b1 & ~below
            ev_b, ev_s = fresh_b & a_up, fresh_s & a_dn
        ib, is_ = s0 + np.flatnonzero(ev_b), s0 + np.flatnonzero(ev_s)
        i = np.r_[ib, is_]
        side = np.r_[np.ones(len(ib), int), -np.ones(len(is_), int)]
        o = np.argsort(i, kind="stable")
        return i[o], side[o]
    if name == "MAD_RSI":
        def fn(d):
            c, h, l = d.close.values, d.high.values, d.low.values
            ef, es, rsi, adx = M._ema(c, 13), M._ema(c, 34), M._rsi(c, 14), adx14(h, l, c)
            rng = adx < 25
            ok = np.arange(len(c)) > 60
            return (ef > es) & (rsi <= np.where(rng, 48, 30)) & ok, (ef < es) & (rsi >= np.where(rng, 52, 70)) & ok
        return tf_events(F, "M5", fn)
    if name == "EMA_X":
        def fn(d):
            c = d.close.values
            ef, es = M._ema(c, 10), M._ema(c, 30)
            up, dn = ef > es, ef < es
            ok = np.arange(len(c)) > 60
            return up & ~np.r_[False, up[:-1]] & ok, dn & ~np.r_[False, dn[:-1]] & ok
        return tf_events(F, "M15", fn)
    if name == "STOCH":
        m1 = F["m1"]
        s0, s1 = window_slice(m1, F["a"], F["z"])
        d = m1.iloc[s0:s1].reset_index(drop=True)
        hh, ll = d.high.rolling(14).max().values, d.low.rolling(14).min().values
        raw = 100 * (d.close.values - ll) / np.where(hh > ll, hh - ll, np.nan)
        k = pd.Series(raw).rolling(3).mean().values
        dd = pd.Series(k).rolling(3).mean().values
        kp = np.r_[np.nan, k[:-1]]
        with np.errstate(invalid="ignore"):
            ev_b = (kp < 20) & (k > 20) & (k > dd)
            ev_s = (kp > 80) & (k < 80) & (k < dd)
        ib, is_ = s0 + np.flatnonzero(ev_b), s0 + np.flatnonzero(ev_s)
        i = np.r_[ib, is_]
        side = np.r_[np.ones(len(ib), int), -np.ones(len(is_), int)]
        o = np.argsort(i, kind="stable")
        return i[o], side[o]
    raise KeyError(name)


def make_orders(F, i, side, ex):
    return R.orders(F, i, side, ex) if X.SYM == "XAUUSD" else FX.fx_orders(F, i, side, ex)


def run_one(F, i, side, ex, ctl=True):
    r = C.simulate(X.SYM, make_orders(F, i, side, ex), one_at_a_time=True)
    st = N.stats2(r, F["days"])
    if ctl:
        cs = []
        for sd in range(3):
            rng = np.random.default_rng(sd)
            rs = rng.choice([-1, 1], size=len(i))
            rc = C.simulate(X.SYM, make_orders(F, i, rs, ex), one_at_a_time=True)
            cs.append(X.stats(rc, F["days"]).get("usd_trade", np.nan))
        st["ctl_max"] = round(float(np.nanmax(cs)), 3)
    return st


def judge():
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "botentries_*_*.csv"))])
    d.to_csv(os.path.join(X.OUTD, "BOTENTRIES_ALL.csv"), index=False)
    order = ["EXPLORE", "VALIDATE", "STRESS"]
    res = []
    for (pair, ent, ex), g in d[~d.entry.str.startswith("OURS")].groupby(["pair", "entry", "exit"]):
        if set(g.stage) != set(order):
            continue
        passes, improves, bits = True, True, []
        for s_ in order:
            x = g[g.stage == s_].iloc[0]
            o = d[(d.pair == pair) & (d.entry.str.startswith("OURS")) & (d.exit == ex) & (d.stage == s_)].iloc[0]
            passes &= (x.usd_trade > 0) and (x.usd_trade > x.ctl_max)
            improves &= x.usd_trade >= o.usd_trade
            bits.append(f"{x.usd_trade:+.2f}(win {x.win_pct:.0f}%, ours {o.usd_trade:+.2f}, coin {x.ctl_max:+.2f})")
        res.append(dict(pair=pair, entry=ent, exit=ex, passes=bool(passes), improves_ours=bool(passes and improves), W1=bits[0], W2=bits[1], W3=bits[2]))
    r = pd.DataFrame(res)
    r.to_csv(os.path.join(X.OUTD, "BOTENTRIES_JUDGED.csv"), index=False)
    pd.set_option("display.width", 300, "display.max_colwidth", 80)
    print(r.groupby(["pair", "entry"])[["passes", "improves_ours"]].sum().to_string())
    print(r[["pair", "entry", "exit", "W1", "W2", "W3"]].to_string(index=False))


if __name__ == "__main__":
    if sys.argv[1] == "judge":
        judge()
        sys.exit()
    pair, stage = sys.argv[1], sys.argv[2]
    assert stage in ("EXPLORE", "VALIDATE", "STRESS"), "open windows only"
    X.SYM = pair
    F = R.add_trends(X.build_stage(stage))
    ix = pd.DatetimeIndex(F["tc"])
    hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    inw = F["in"] & ok
    rows = []
    for ex in EXITS:                                                  # our own entry on the same exit
        sp = N.spec_for(REF[ex])
        i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        k = inw[i0] & (i0 < F["n"] - 20)
        st = run_one(F, i0[k], s0[k], ex, ctl=False)
        st.update(pair=pair, stage=stage, entry="OURS_" + REF[ex], exit=ex, signals=int(k.sum()))
        rows.append(st)
        print(pair, stage, st["entry"], st["fills"], st["win_pct"], st["usd_trade"], flush=True)
    for name in ENTRIES:
        i0, s0 = entry_signals(F, name)
        k = (i0 > X.WARM) & (i0 < F["n"] - 20)
        i0, s0 = i0[k], s0[k]
        k = inw[i0]
        i, side = i0[k], s0[k]
        for ex in EXITS:
            if len(i) < 50:
                continue
            st = run_one(F, i, side, ex)
            st.update(pair=pair, stage=stage, entry=name, exit=ex, signals=len(i))
            rows.append(st)
            print(pair, stage, name, ex, st["fills"], st["win_pct"], st["usd_trade"], st["ctl_max"], flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"botentries_{pair}_{stage}.csv"), index=False)
    print("done", flush=True)
