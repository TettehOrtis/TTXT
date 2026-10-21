"""Judge addendum 15 stage 1 with the pre-registered rule."""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import aut16_layers as X

d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "filters_*_*.csv"))])
d.to_csv(os.path.join(X.OUTD, "FILTERS_ALL.csv"), index=False)
order = ["EXPLORE", "VALIDATE", "STRESS"]
res = []
for (v, fl), g in d[d.filt != "BASE"].groupby(["variant", "filt"]):
    ok_all, beats, rows = True, 0, {}
    for s in order:
        x = g[g.stage == s].iloc[0]
        b = d[(d.variant == v) & (d.filt == "BASE") & (d.stage == s)].iloc[0]
        ok_all &= (x.kept_pct >= 80) and (x.wr >= b.wr) and (x.pf >= b.pf)
        beats += int(x.pf > x.rand_pf_p90)
        rows[s] = (x.kept_pct, round(x.wr - b.wr, 2), round(x.pf - b.pf, 3))
    res.append(dict(variant=v, filt=fl, helps=bool(ok_all and beats >= 2), beats_chance_windows=beats,
                    **{f"{s}_kept/dWR/dPF": rows[s] for s in order}))
r = pd.DataFrame(res)
r.to_csv(os.path.join(X.OUTD, "FILTERS_JUDGED.csv"), index=False)
pas = r.groupby("filt").helps.sum().sort_values(ascending=False)
print("variants helped (of 5) per filter:\n", pas.to_string())
# average effect across variants, per window
rows = []
for fl in sorted(d.filt.unique()):
    if fl == "BASE":
        continue
    out = {"filt": fl}
    for s in order:
        dd = []
        for v in d.variant.unique():
            x = d[(d.variant == v) & (d.filt == fl) & (d.stage == s)].iloc[0]
            b = d[(d.variant == v) & (d.filt == "BASE") & (d.stage == s)].iloc[0]
            dd.append((x.kept_pct, x.wr - b.wr, x.pf - b.pf))
        a = pd.DataFrame(dd).mean()
        out[s] = f"kept {a[0]:.0f}% dWR {a[1]:+.2f} dPF {a[2]:+.3f}"
    rows.append(out)
print(pd.DataFrame(rows).to_string(index=False))
