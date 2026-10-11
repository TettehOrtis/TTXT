"""Families of rule-based strategies on higher-timeframe signals, executed on the M1 path.
Every grid cell = 1 trial. Select on 2021-2024 by avg R (and t on DAILY-aggregated R); holdout 2025-26 reported.
Long and short reported separately; drift-neutral = (long R - random-long R) etc using same exits at random times.
Usage: python b3_htf.py SYM FAMILY [sprm] [slipfrac]
FAMILY: donch | tsmom | shock | xusd
"""
import sys, itertools, time
import numpy as np, pandas as pd
sys.path.insert(0, "research"); sys.path.insert(0, "research/opus")
from lib import load, pip_size
from sim import run, nonoverlap

sym = sys.argv[1]; fam = sys.argv[2]
SPRM = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
SLF = float(sys.argv[4]) if len(sys.argv) > 4 else 0.25
d = load(sym)
p = pip_size(sym)
N = len(d)
t = d.time
hr = t.dt.hour.values; mi = t.dt.minute.values; dow = t.dt.dayofweek.values; yr = t.dt.year.values
slip = SLF * float(np.median(d.spr.values))
# bars until 20:45 UTC same day (force flat before rollover)
mins = hr * 60 + mi
to_cut = (20 * 60 + 45) - mins
entry_ok = (hr >= 1) & (to_cut > 15) & ~((dow == 4) & (hr >= 19))
atr1 = ((d.high - d.low) / p).rolling(60).mean().values


def htf(tf):
    g = t.dt.floor(tf)
    grp = pd.Series(np.arange(N)).groupby(g.values)
    last = grp.max().values
    firsti = grp.min().values
    H = pd.DataFrame({"i": last, "o": d.open.values[firsti], "h": d.high.groupby(g.values).max().values,
                      "l": d.low.groupby(g.values).min().values, "c": d.close.values[last]})
    H["atr"] = ((H.h - H.l) / p).rolling(14).mean()
    H["ret"] = (H.c - H.c.shift(1)) / p
    H["sig"] = H.ret.rolling(50).std()
    return H


def evaluate(idx, side, sl_u, trail, maxhold_bars, tp_u=None):
    idx = np.asarray(idx, np.int64)
    m = entry_ok[idx] & ~np.isnan(sl_u) & (sl_u > 0)
    idx, sl_u = idx[m], sl_u[m]
    if len(idx) < 30:
        return None
    mh = np.minimum(maxhold_bars, np.maximum(to_cut[idx], 1)).astype(np.int64)
    tp = np.zeros(len(idx)) if tp_u is None else tp_u[m]
    ts, td = trail
    pn, hd, wy = run(d, sym, idx, side, tp, sl_u, mh, slip=slip, tp_through=0.0, amb=2, sprm=SPRM,
                     trail_start=ts, trail_dist=td)
    keep = nonoverlap(idx, hd) & ~np.isnan(pn)
    return idx[keep], pn[keep], pn[keep] / sl_u[keep], hd[keep]


def stats(idx, pn, R):
    y = yr[idx]
    out = {}
    for name, mk in (("tr", y <= 2024), ("te", y >= 2025)):
        if mk.sum() < 10:
            out.update({f"{name}_n": int(mk.sum())}); continue
        day = pd.Series(R[mk]).groupby(t.values[idx[mk]].astype("datetime64[D]")).sum()
        out.update({f"{name}_n": int(mk.sum()), f"{name}_R": R[mk].mean(), f"{name}_usd": pn[mk].mean() * p,
                    f"{name}_tday": day.mean() / (day.std(ddof=1) / np.sqrt(len(day))), f"{name}_win": (pn[mk] > 0).mean()})
    return out


rows = []
rs = np.random.default_rng(7)
t0 = time.time()
if fam in ("donch", "tsmom"):
    for tf in ("15min", "1h"):
        H = htf(tf)
        barmin = 15 if tf == "15min" else 60
        for L in ((20, 55) if fam == "donch" else (4, 12, 24)):
            if fam == "donch":
                up = H.c > H.h.shift(1).rolling(L).max()
                dn = H.c < H.l.shift(1).rolling(L).min()
            else:
                z = (H.c - H.c.shift(L)) / p / (H.sig * np.sqrt(L))
                up, dn = z > 1.5, z < -1.5
            # only first bar of a new signal
            up = up & ~up.shift(1, fill_value=False); dn = dn & ~dn.shift(1, fill_value=False)
            for ksl, trail, hold in itertools.product((1.0, 2.0), ((1.0, 1.0), (0.0, 0.0)), (8, 24)):
                for side, sg in ((1, up), (-1, dn)):
                    sel = H[sg & H.atr.notna()]
                    r = evaluate(sel.i.values, side, ksl * sel.atr.values, trail, hold * barmin)
                    if r is None: continue
                    # random-time control with same exits and same side (drift/cost baseline)
                    ri = rs.choice(np.where(entry_ok & ~np.isnan(atr1))[0], len(sel) * 3)
                    Hmap = H.set_index("i").atr.reindex(np.arange(N), method="ffill").values
                    rc = evaluate(np.sort(ri), side, ksl * Hmap[np.sort(ri)], trail, hold * barmin)
                    st = stats(*r[:3]); sc = stats(*rc[:3])
                    rows.append(dict(fam=fam, tf=tf, L=L, ksl=ksl, trail=trail[1] > 0, hold=hold, side=side, **st,
                                     ctl_tr_R=sc.get("tr_R"), ctl_te_R=sc.get("te_R")))
elif fam in ("shock",):
    for tf, zthr, mode, (ktp, ksl), hold in itertools.product(("1min", "5min"), (4.0, 6.0), ("fade", "follow"),
                                                                ((0.5, 1.0), (1.0, 1.0), (0.0, 1.0)), (15, 60)):
        H = htf(tf)
        z = H.ret / H.sig.shift(1)
        for side in (1, -1):
            # fade: after a big DOWN bar go long (side=1); follow: after big UP go long
            if mode == "fade":
                sg = (z < -zthr) if side == 1 else (z > zthr)
            else:
                sg = (z > zthr) if side == 1 else (z < -zthr)
            sel = H[sg & H.sig.notna()]
            size = np.abs(sel.ret.values)
            r = evaluate(sel.i.values, side, ksl * size, (0.0, 0.0), hold, ktp * size if ktp > 0 else None)
            if r is None: continue
            rows.append(dict(fam=fam, tf=tf, z=zthr, mode=mode, ktp=ktp, ksl=ksl, hold=hold, side=side, **stats(*r[:3])))
elif fam == "xusd":
    # USD-proxy lead: EURUSD & USDJPY 5-min z-move; gold hasn't followed yet
    others = {}
    for o, sgn in (("EURUSD", 1), ("USDJPY", -1)):
        od = pd.read_parquet(f"data/{o}_M1.parquet", columns=["time", "close"]).drop_duplicates("time").set_index("time").close
        oc = od.reindex(t.values, method="ffill", tolerance=pd.Timedelta("2min")).values
        others[o] = (oc, sgn)
    cl = d.close.values
    for lb in (5, 15):
        usd = np.zeros(N)
        for o, (oc, sgn) in others.items():
            rr = pd.Series(np.log(oc)).diff(lb)
            usd += sgn * (rr / rr.rolling(1440).std()).values   # + means USD weaker -> gold up
        usd /= 2
        gr = pd.Series(cl).diff(lb) / p
        gz = (gr / gr.rolling(1440).std()).values
        for zt, lagmax, (ksl, ktp), hold in itertools.product((2.0, 3.0), (0.5, 99.0), ((1.0, 1.0), (1.5, 0.0)), (15, 60)):
            for side in (1, -1):
                sg = (side * usd > zt) & (side * gz < lagmax)
                sg = sg & ~np.roll(sg, 1)
                ii = np.where(sg & ~np.isnan(atr1))[0]
                sl = ksl * 3 * atr1[ii]
                r = evaluate(ii, side, sl, (0.0, 0.0), hold, ktp * 3 * atr1[ii] if ktp > 0 else None)
                if r is None: continue
                rows.append(dict(fam=fam, lb=lb, z=zt, gold_lag=lagmax, ksl=ksl, ktp=ktp, hold=hold, side=side, **stats(*r[:3])))

g = pd.DataFrame(rows)
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 40)
print(f"{sym} {fam}: {len(g)} trials, sprm={SPRM}, slip={slip:.2f}u, {time.time()-t0:.0f}s")
print(g.sort_values("tr_R", ascending=False).head(12).round(3).to_string(index=False))
print("\nmean over all cells: train R %.3f  holdout R %.3f ; corr(train R, holdout R) = %.2f" %
      (g.tr_R.mean(), g.te_R.mean(), g[["tr_R", "te_R"]].dropna().corr().iloc[0, 1]))
print("by side:", g.groupby("side")[["tr_R", "te_R"]].mean().round(3).to_dict())
g.to_csv(f"research/opus/b3_{sym}_{fam}.csv", index=False)
