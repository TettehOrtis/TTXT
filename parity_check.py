"""Live-vs-research parity: features/prediction computed from LIVE-fetched MT5 bars must equal the research pipeline
for the same timestamps. Read-only (no orders). Run while market is closed or open."""
import sys, logging
import numpy as np, pandas as pd
from scalper.broker import Broker
from scalper.gold_ml import GoldML
sys.path.insert(0, "research")
from lib import load, features2

logging.basicConfig(level=logging.WARNING)
b = Broker(); b.connect()
s = GoldML(b)
live = b.rates("XAUUSDm", 6000)
closed = live.iloc[:-1]
ref = load("XAUUSD", "2026-08-01")
fr = features2(ref, "XAUUSD")[s.meta["features"]]
fr.index = ref.time
diffs, preds = [], []
for k in (1, 30, 120, 400, 900):                         # several bar-ends inside the fetched window
    cb = closed.iloc[: len(closed) - k + 1] if k > 1 else closed
    t = cb.time.iloc[-1]
    if t not in fr.index:
        continue
    res = s.compute("XAUUSD", cb)
    if res is None:
        print(t, "live features NaN (e.g. other market closed)"); continue
    pred, atr_u, row = res
    rrow = fr.loc[t]
    if rrow.isna().any():
        print(t, "research features NaN"); continue
    rp = float(s.model.predict(rrow.values.astype(np.float32).reshape(1, -1))[0])
    rel = (row - rrow).abs() / (rrow.abs() + 1e-6)
    worst = rel.sort_values(ascending=False).head(3)
    diffs.append(worst.iloc[0]); preds.append(abs(pred - rp))
    print(f"{t}  live pred {pred:+.4f}  research pred {rp:+.4f}  | worst rel feature diffs: "
          + ", ".join(f"{i}={v:.2e}" for i, v in worst.items()))
b.shutdown()
print("\nmax |pred diff| =", max(preds) if preds else None, "| PARITY", "OK" if preds and max(preds) < 0.02 else "CHECK")
