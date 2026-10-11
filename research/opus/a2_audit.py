"""Audit: reproduce lib.simulate, then relax each pessimistic assumption one at a time (XAUUSD)."""
import sys, time
import numpy as np, pandas as pd
sys.path.insert(0, "research"); sys.path.insert(0, "research/opus")
from lib import load, pip_size, active_mask, run_sim
from sim import run

sym = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
d = load(sym)
p = pip_size(sym)
rng = (d.high - d.low) / p
atr = rng.rolling(60).mean().values
act = active_mask(d) & ~np.isnan(atr)
ia = np.where(act)[0]
rs = np.random.default_rng(1)
sub = np.sort(rs.choice(ia, 100000, replace=False))
med = float(np.median(d.spr.values[act]))
SL = 0.25 * med
print(f"{sym} median bar(min) spread {med:.2f}u, slip={SL:.2f}u")

# 1) equivalence with lib.simulate
a1 = run_sim(d, sym, sub, 1, 2 * atr[sub], 1.5 * atr[sub], 20, SL, 0.1)[0]
a2 = run(d, sym, sub, 1, 2 * atr[sub], 1.5 * atr[sub], 20, SL, 0.1)[0]
b1 = run_sim(d, sym, sub, -1, 2 * atr[sub], 1.5 * atr[sub], 20, SL, 0.1)[0]
b2 = run(d, sym, sub, -1, 2 * atr[sub], 1.5 * atr[sub], 20, SL, 0.1)[0]
print("reproduces lib.simulate:", np.allclose(a1, a2, equal_nan=True), np.allclose(b1, b2, equal_nan=True))

rows = []
for ktp, ksl, mh in [(1.0, 1.0, 15), (2.0, 1.5, 20), (3.0, 2.0, 30), (6.0, 3.0, 120)]:
    for name, kw in [("baseline(lib)", dict(slip=SL, tp_through=0.1, amb=0)),
                     ("amb=path heuristic", dict(slip=SL, tp_through=0.1, amb=2)),
                     ("amb=TP first (upper bound)", dict(slip=SL, tp_through=0.1, amb=1)),
                     ("slip=0", dict(slip=0, tp_through=0.1, amb=0)),
                     ("tp_through=0", dict(slip=SL, tp_through=0.0, amb=0)),
                     ("all relaxed (path, slip0, thr0)", dict(slip=0, tp_through=0, amb=2)),
                     ("ZERO COST, SL-first", dict(slip=0, tp_through=0, amb=0, sprm=0)),
                     ("ZERO COST, path", dict(slip=0, tp_through=0, amb=2, sprm=0)),
                     ("ZERO COST, TP-first", dict(slip=0, tp_through=0, amb=1, sprm=0))]:
        res = []
        for s in (1, -1):
            pn, hd, wy = run(d, sym, sub, s, ktp * atr[sub], ksl * atr[sub], mh, **kw)
            res.append(np.nanmean(pn))
        rows.append(dict(bracket=f"TP{ktp}/SL{ksl}xATR H{mh}", variant=name, long=res[0], short=res[1], avg=np.mean(res)))
    # ambiguous-bar frequency
    pn, hd, wy = run(d, sym, sub, 1, ktp * atr[sub], ksl * atr[sub], mh, slip=0, tp_through=0, amb=0, sprm=0)
    pn1, _, wy1 = run(d, sym, sub, 1, ktp * atr[sub], ksl * atr[sub], mh, slip=0, tp_through=0, amb=1, sprm=0)
    print(f"TP{ktp}/SL{ksl}: fraction of trades whose outcome depends on intrabar order: {np.mean(wy != wy1)*100:.2f}%")
r = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(r.round(3).to_string(index=False))
print("mean ATR60 (units):", np.nanmean(atr[sub]).round(2))
