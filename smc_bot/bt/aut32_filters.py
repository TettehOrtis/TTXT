"""AUT16 addendum 15: entry filters that try to raise the win rate. Usage: python aut32_filters.py STAGE VARIANT_INDEX (0..4)
EXPLORE fits the cut-offs (saved to filt_cuts_<vi>.json); VALIDATE / STRESS reuse them."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import aut30_cooldown_vol as K
import common as C

SEEDS = 30


def features(F, i, side):
    c, o, h, l, e20, at = F["c"], F["o"], F["h"], F["l"], F["e20"], F["atr15"]
    a = at[i]
    s = side
    f = pd.DataFrame({"t_signal": F["tc"][i]})
    f["STRETCH"] = s * (c[i] - e20[i]) / a
    lo30 = pd.Series(l).rolling(30, min_periods=1).min().values
    hi30 = pd.Series(h).rolling(30, min_periods=1).max().values
    f["RUN"] = np.where(s == 1, c[i] - lo30[i], hi30[i] - c[i]) / a
    f["BODY"] = np.abs(c[i] - o[i]) / a
    ip = np.clip(i - 1, 0, None)
    prev_dir = np.where(c[ip] > o[ip], 1, np.where(c[ip] < o[ip], -1, 0))
    f["CONF2"] = prev_dir == s
    f["BREAK"] = np.where(s == 1, c[i] > h[ip], c[i] < l[ip])
    f["MTF"] = (F["tr"][("M15", "PX200")][i] == s) & (F["tr"][("M30", "PX100")][i] == s)
    f["hour"] = pd.DatetimeIndex(F["tc"][i]).hour.values
    return f


def pf_wr(r):
    u = r.usd_01.values
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    tp, sl = (r.exit_reason == "TP").sum(), (r.exit_reason == "SL").sum()
    return (gp / gl if gl > 0 else np.nan), (100.0 * tp / (tp + sl) if tp + sl else np.nan)


def apply(r, ts, te, mask):
    """mask over r's rows (kept signals); returns the one-at-a-time selection among the kept."""
    idx = np.flatnonzero(mask)
    keep = K.select(ts[idx], te[idx], r.usd_01.values[idx], 0, 0, 0)
    return r.iloc[idx[keep]]


if __name__ == "__main__":
    stage, vi = sys.argv[1], int(sys.argv[2])
    name = N.NAMES[vi]
    sp = N.spec_for(name)
    F = R.add_trends(X.build_stage(stage))
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
    k = ok[i0]
    i, side = i0[k], s0[k]
    f = features(F, i, side)
    r = C.simulate(X.SYM, R.orders(F, i, side, sp["exit"]), one_at_a_time=False)
    r = r[r.status == "filled"].sort_values("t_signal", kind="stable").reset_index(drop=True)
    r = r.merge(f, on="t_signal", how="left")
    ts = pd.DatetimeIndex(r.t_signal).values.astype("datetime64[ns]").astype(np.int64)
    te = pd.DatetimeIndex(r.t_exit).values.astype("datetime64[ns]").astype(np.int64)
    cutf = os.path.join(X.OUTD, f"filt_cuts_{vi}.json")
    if stage == "EXPLORE":
        cuts = {}
        for m in ("STRETCH", "RUN", "BODY"):
            v = r[m].dropna().values
            cuts[m] = {q: float(np.quantile(v, p)) for q, p in (("q10", .10), ("q20", .20), ("q80", .80), ("q90", .90))}
        base = apply(r, ts, te, np.ones(len(r), bool))
        rows_h = []
        for h_, d in base.groupby("hour"):
            dec = d.exit_reason.isin(["TP", "SL"]).sum()
            rows_h.append((h_, len(d), 100.0 * (d.exit_reason == "TP").sum() / max(1, dec)))
        g = pd.DataFrame(rows_h, columns=["hour", "n", "wr"])
        g = g[g.n >= 100].sort_values("wr")
        cuts["HOUR3"] = [int(x) for x in g.hour.values[:3]]
        json.dump(cuts, open(cutf, "w"))
    cuts = json.load(open(cutf))
    masks = {"BASE": np.ones(len(r), bool),
             "STRETCH10": (r.STRETCH <= cuts["STRETCH"]["q90"]).values, "STRETCH20": (r.STRETCH <= cuts["STRETCH"]["q80"]).values,
             "RUN10": (r.RUN <= cuts["RUN"]["q90"]).values, "RUN20": (r.RUN <= cuts["RUN"]["q80"]).values,
             "SMALL10": (r.BODY >= cuts["BODY"]["q10"]).values, "SMALL20": (r.BODY >= cuts["BODY"]["q20"]).values,
             "BIG10": (r.BODY <= cuts["BODY"]["q90"]).values, "BIG20": (r.BODY <= cuts["BODY"]["q80"]).values,
             "STRETCHLOW10": (r.STRETCH >= cuts["STRETCH"]["q10"]).values, "STRETCHLOW20": (r.STRETCH >= cuts["STRETCH"]["q20"]).values,
             "RUNLOW10": (r.RUN >= cuts["RUN"]["q10"]).values, "RUNLOW20": (r.RUN >= cuts["RUN"]["q20"]).values,
             "CONF2": r.CONF2.values.astype(bool), "BREAK": r.BREAK.values.astype(bool), "MTF": r.MTF.values.astype(bool),
             "HOUR3": ~r.hour.isin(cuts["HOUR3"]).values}
    rows = []
    rng = np.random.default_rng(7)
    base_n = None
    for fname, m in masks.items():
        sel = apply(r, ts, te, m)
        pf, wr = pf_wr(sel)
        if fname == "BASE":
            base_n = len(sel)
        row = dict(variant=name, stage=stage, filt=fname, signals_kept_pct=round(100 * m.mean(), 1), trades=len(sel),
                   kept_pct=round(100 * len(sel) / base_n, 1), per_day=round(len(sel) / F["days"], 2), wr=round(wr, 2), pf=round(pf, 3),
                   usd_trade=round(sel.usd_01.mean(), 3), net=round(sel.usd_01.sum()))
        if fname != "BASE":
            p = m.mean()
            pfs, wrs = [], []
            for _ in range(SEEDS):
                a, b = pf_wr(apply(r, ts, te, rng.random(len(r)) < p))
                pfs.append(a); wrs.append(b)
            row.update(rand_pf_p90=round(np.nanquantile(pfs, .9), 3), rand_wr_p90=round(np.nanquantile(wrs, .9), 2), rand_pf_mean=round(np.nanmean(pfs), 3))
        rows.append(row)
        print(name, stage, fname, row["kept_pct"], row["wr"], row["pf"], flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"filters_{stage}_{vi}.csv"), index=False)
    print("done", flush=True)
