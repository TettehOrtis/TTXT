"""Replay 3 gold base trades by hand from the raw M1 bars."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import aut13_leadlag as L
C = L.C
t = pd.read_csv(os.path.join(L.OUT, "trades_XAUUSD_BASE_EXPLORE.csv"), parse_dates=["t_signal", "t_fill", "t_exit"])
b = L.bars("XAUUSD", "M1").set_index("time")
out = []
for r in t.sample(3, random_state=1).itertuples():
    f = b.loc[r.t_fill]
    exp_fill = f.open + (f.spr if r.side == 1 else 0.0)
    ex = b.loc[r.t_exit]
    seg = b.loc[r.t_fill:r.t_exit]
    out.append(dict(t_signal=str(r.t_signal), side=int(r.side), t_fill=str(r.t_fill), fill_in_file=float(r.fill), fill_by_hand=float(exp_fill),
                    sl=float(r.sl), tp=float(r.tp), exit_reason=r.exit_reason, exit_in_file=float(r.exit),
                    bar_low_min=float(seg.low.min()), bar_high_max=float((seg.high).max()), net_pips=float(r.net_pips),
                    net_by_hand=float(((r.exit - exp_fill) * r.side) / 0.1 - 1.10)))
print(json.dumps(out, indent=1))
json.dump(out, open(os.path.join(L.OUT, "hand_check.json"), "w"), indent=1)
