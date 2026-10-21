"""AUT13 diagnostics: (1) lead-lag cross-correlation by minute lag, (2) gross move after a gap signal by horizon."""
import os, sys
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import aut13_leadlag as L
C, A = L.C, L.A


def stage_mask(t, st):
    a, b = A.STAGES[A.gate(st)]
    return (t >= np.datetime64(a)) & (t < np.datetime64(b))


def ccf(target, lags=range(-5, 6)):
    arr = L.build_arrays(target)
    T1, D1, t = arr["T1"], arr["D1"], arr["t"]
    rows = []
    for st in ("EXPLORE", "VALIDATE", "STRESS"):
        m = stage_mask(t, st)
        for lag in lags:
            # corr( target return at i+lag , dollar return at i ): lag>0 => dollar leads target
            x = D1[m]; y = np.roll(T1, -lag)[m]
            ok = np.isfinite(x) & np.isfinite(y)
            rows.append(dict(target=target, stage=st, lag_min=lag, corr=np.corrcoef(x[ok], y[ok])[0, 1], n=int(ok.sum())))
    return pd.DataFrame(rows)


def forward(target, hs=(1, 2, 3, 5, 10, 15, 30, 60)):
    ev = L.signals(target)
    tb = L.bars(target, "M1")
    op, cl, tm = tb.open.values, tb.close.values, tb.time.values.astype("datetime64[m]").astype(np.int64)
    n = len(tb); P = C.pip(target)
    rows = []
    for k in L.KS:
        e = ev[ev.k == k]
        i = e.i.values
        for st in ("EXPLORE", "VALIDATE", "STRESS"):
            a, b = A.STAGES[st]
            m = (e.t_signal >= a) & (e.t_signal < b)
            ii = i[m.values]; sd = e.side_fade.values[m.values]
            for h in hs:
                j = np.minimum(ii + h, n - 1)
                ok = (tm[j] - tm[ii]) == h
                g = sd * (cl[j] - op[np.minimum(ii + 1, n - 1)]) / P
                gg = g[ok]
                rows.append(dict(target=target, k=k, stage=st, h=h, n=int(ok.sum()), gross_pips_fade=gg.mean(), se=gg.std() / np.sqrt(max(len(gg), 1))))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    for tg in sys.argv[1:]:
        c = ccf(tg)
        print(c.pivot_table(index="lag_min", columns="stage", values="corr").round(4))
        f = forward(tg)
        print(f.pivot_table(index=["k", "h"], columns="stage", values="gross_pips_fade").round(3))
        f.to_csv(os.path.join(L.OUT, "diag_forward_%s.csv" % tg), index=False)
        c.to_csv(os.path.join(L.OUT, "diag_ccf_%s.csv" % tg), index=False)
