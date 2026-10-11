import os, sys, numpy as np
os.environ["DATA_DIR"] = "data_zero"
from scalper.broker import Broker
from scalper.usdjpy_ml import UsdJpyML, OTHERS
sys.path.insert(0, "research")
from lib import load, features2
b = Broker(suffix="z", magic=660066, env="Z"); b.connect()
for o in OTHERS: print(o, "live symbol ok" if b.rates(b.sym(o), 5) is not None else "MISSING")
s = UsdJpyML(b)
closed = b.rates("USDJPYz", 6000).iloc[:-1]
ref = load("USDJPY", "2026-08-01"); fr = features2(ref, "USDJPY")[s.meta["features"]]; fr.index = ref.time
worst = 0
for k in (1, 45, 200, 700):
    cb = closed.iloc[: len(closed) - k + 1]; t = cb.time.iloc[-1]
    if t not in fr.index: print(t, "not in research data"); continue
    res = s.compute("USDJPY", cb)
    if res is None: print(t, "live NaN"); continue
    pred, row = res; rp = float(s.model.predict(fr.loc[[t]].values.astype(np.float32))[0])
    rel = ((row - fr.loc[t]).abs() / (fr.loc[t].abs() + 1e-6)).sort_values(ascending=False)
    worst = max(worst, abs(pred - rp))
    print(f"{t} live {pred:+.4f} research {rp:+.4f} | biggest feature diffs: " + ", ".join(f"{i}={v:.1e}" for i, v in rel.head(3).items()))
print("max pred diff", round(worst, 4), "| threshold gap to signal:", s.meta["thr_lo"], s.meta["thr_hi"])
b.shutdown()
