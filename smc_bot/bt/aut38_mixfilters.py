"""PREREG addendum 24: free-bot rules as extra filters on our own entry (gold). Usage: python aut38_mixfilters.py STAGE VARIANT_INDEX | judge"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut32_filters as A
import aut37_botentries as B
import aut01_emapull as M
import common as C

SEEDS = 30
FILTS = ["TRI_BOTH", "TRI_M1", "EMA_X", "MAD_TREND", "MAD_RSI", "STOCH_ROOM"]


def tf_state(F, tf, fn):
    """fn(sliced bars) -> int array (+1 / -1 / 0), mapped to each M1 bar through the last CLOSED bar of that timeframe."""
    b = C.bars(X.SYM, tf)
    s0, s1 = B.window_slice(b, F["a"], F["z"])
    v = fn(b.iloc[s0:s1].reset_index(drop=True))
    j = C.asof_index(b, F["tc"]) - s0
    ok = (j >= 60) & (j < len(v))
    return np.where(ok, v[np.clip(j, 0, len(v) - 1)], 0)


def states(F):
    n = F["n"]
    out = {}
    m1 = F["m1"]
    s0, s1 = B.window_slice(m1, F["a"], F["z"])
    d = m1.iloc[s0:s1].reset_index(drop=True)
    up1, dn1, _ = B.tri_state(d)
    st1 = np.zeros(n, int)
    st1[s0:s1] = np.where(up1, 1, np.where(dn1, -1, 0))
    st5 = tf_state(F, "M5", lambda x: np.where(B.tri_state(x)[0], 1, np.where(B.tri_state(x)[1], -1, 0)))
    out["TRI_M1"] = st1
    out["TRI_BOTH"] = np.where((st1 == st5) & (st1 != 0), st1, 0)
    out["EMA_X"] = tf_state(F, "M15", lambda x: np.where(M._ema(x.close.values, 10) > M._ema(x.close.values, 30), 1, -1))
    out["MAD_TREND"] = tf_state(F, "M5", lambda x: np.where(M._ema(x.close.values, 13) > M._ema(x.close.values, 34), 1, -1))

    def mad(x):
        c, h, l = x.close.values, x.high.values, x.low.values
        up, rsi, adx = M._ema(c, 13) > M._ema(c, 34), M._rsi(c, 14), B.adx14(h, l, c)
        rng = adx < 25
        buy = up & (rsi <= np.where(rng, 48, 30))
        sell = ~up & (rsi >= np.where(rng, 52, 70))
        return np.where(buy, 1, np.where(sell, -1, 0))
    out["MAD_RSI"] = tf_state(F, "M5", mad)
    hh, ll = d.high.rolling(14).max().values, d.low.rolling(14).min().values
    raw = 100 * (d.close.values - ll) / np.where(hh > ll, hh - ll, np.nan)
    k = pd.Series(raw).rolling(3).mean().values
    ok_b, ok_s = ~(k >= 80), ~(k <= 20)                          # nan counts as room
    room = np.zeros((2, n), bool)
    room[0, s0:s1], room[1, s0:s1] = ok_b, ok_s
    out["STOCH_ROOM"] = room
    return out


def mask_for(name, S, i, side):
    if name == "STOCH_ROOM":
        return np.where(side == 1, S[name][0][i], S[name][1][i])
    return S[name][i] == side


def judge():
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "mixfilt_*_*.csv"))])
    d.to_csv(os.path.join(X.OUTD, "MIXFILT_ALL.csv"), index=False)
    order = ["EXPLORE", "VALIDATE", "STRESS"]
    res = []
    for (v, fl), g in d[d.filt != "BASE"].groupby(["variant", "filt"]):
        ok_all, beats, bits = True, 0, {}
        for s_ in order:
            x = g[g.stage == s_].iloc[0]
            b = d[(d.variant == v) & (d.filt == "BASE") & (d.stage == s_)].iloc[0]
            ok_all &= (x.kept_pct >= 80) and (x.wr >= b.wr) and (x.pf >= b.pf)
            beats += int(x.pf > x.rand_pf_p90)
            bits[s_] = f"kept {x.kept_pct:.0f}% wr {x.wr - b.wr:+.2f} pf {x.pf - b.pf:+.3f}"
        res.append(dict(variant=v, filt=fl, helps=bool(ok_all and beats >= 2), beats_chance_windows=beats, **bits))
    r = pd.DataFrame(res)
    r.to_csv(os.path.join(X.OUTD, "MIXFILT_JUDGED.csv"), index=False)
    print("variants helped (of 5) per filter:\n", r.groupby("filt").helps.sum().to_string())
    rows = []
    for fl in FILTS:
        out = {"filt": fl}
        for s_ in order:
            dd = []
            for v in d.variant.unique():
                x = d[(d.variant == v) & (d.filt == fl) & (d.stage == s_)].iloc[0]
                b = d[(d.variant == v) & (d.filt == "BASE") & (d.stage == s_)].iloc[0]
                dd.append((x.kept_pct, x.wr - b.wr, x.pf - b.pf, x.usd_trade - b.usd_trade))
            a = pd.DataFrame(dd).mean()
            out[s_] = f"kept {a[0]:.0f}% dWR {a[1]:+.2f} dPF {a[2]:+.3f} d$/trade {a[3]:+.2f}"
        rows.append(out)
    pd.set_option("display.width", 250, "display.max_colwidth", 80)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    if sys.argv[1] == "judge":
        judge()
        sys.exit()
    stage, vi = sys.argv[1], int(sys.argv[2])
    assert stage in ("EXPLORE", "VALIDATE", "STRESS")
    name = N.NAMES[vi]
    sp = N.spec_for(name)
    F = R.add_trends(X.build_stage(stage))
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
    k = ok[i0]
    i, side = i0[k], s0[k]
    S = states(F)
    r = C.simulate(X.SYM, R.orders(F, i, side, sp["exit"]), one_at_a_time=False)
    r = r[r.status == "filled"].sort_values("t_signal", kind="stable").reset_index(drop=True)
    sig = pd.DataFrame({"t_signal": F["tc"][i], "ix": i, "sd": side}).drop_duplicates("t_signal")
    r = r.merge(sig, on="t_signal", how="left")
    ts = pd.DatetimeIndex(r.t_signal).values.astype("datetime64[ns]").astype(np.int64)
    te = pd.DatetimeIndex(r.t_exit).values.astype("datetime64[ns]").astype(np.int64)
    ii, sd = r.ix.values.astype(int), r.sd.values.astype(int)
    masks = {"BASE": np.ones(len(r), bool)}
    for f_ in FILTS:
        masks[f_] = np.asarray(mask_for(f_, S, ii, sd), bool)
    rows, rng, base_n = [], np.random.default_rng(7), None
    for fname, m in masks.items():
        sel = A.apply(r, ts, te, m)
        pf, wr = A.pf_wr(sel)
        if fname == "BASE":
            base_n = len(sel)
        row = dict(variant=name, stage=stage, filt=fname, signals_kept_pct=round(100 * m.mean(), 1), trades=len(sel),
                   kept_pct=round(100 * len(sel) / base_n, 1), per_day=round(len(sel) / F["days"], 2), wr=round(wr, 2), pf=round(pf, 3),
                   usd_trade=round(sel.usd_01.mean(), 3), net=round(sel.usd_01.sum()))
        if fname != "BASE":
            p = m.mean()
            pfs = [A.pf_wr(A.apply(r, ts, te, rng.random(len(r)) < p))[0] for _ in range(SEEDS)]
            row.update(rand_pf_p90=round(np.nanquantile(pfs, .9), 3), rand_pf_mean=round(np.nanmean(pfs), 3))
        rows.append(row)
        print(name, stage, fname, row["kept_pct"], row["wr"], row["pf"], flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"mixfilt_{stage}_{vi}.csv"), index=False)
    print("done", flush=True)
