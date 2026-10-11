"""Hand-checkable tests of the harness simulator on real EURUSD minutes. Run: python smc_bot/bt/test_common.py"""
import numpy as np, pandas as pd
from common import bars, simulate, pip, COMM

m1 = bars("EURUSD", "M1")
P = pip("EURUSD")
t0 = pd.Timestamp("2023-03-15 10:00")
seg = m1[(m1.time >= t0) & (m1.time < t0 + pd.Timedelta(hours=6))].reset_index(drop=True)
o0 = seg.open.iloc[0]
print("EURUSD 2023-03-15 10:00 open", o0, "| next 6h high", seg.high.max(), "low", seg.low.min())

def one(**kw):
    base = dict(t_signal=t0, side=1, entry_type="market", entry=np.nan, expire=pd.NaT, sl=np.nan, tp=np.nan,
                be_at_r=np.nan, max_hold_min=np.nan)
    base.update(kw)
    return simulate("EURUSD", pd.DataFrame([base]), one_at_a_time=False).iloc[0]

# 1) market buy, 10-pip SL, 20-pip TP: check against the first minute that touches either level
r = one(side=1, sl=o0 - 10 * P, tp=o0 + 20 * P)
ask0 = o0 + seg.spr.iloc[0]
sl_i = np.argmax(seg.low.values <= ask0 - 10 * P) if (seg.low.values <= ask0 - 10 * P).any() else None
print("\n1) BUY market:", r.status, "fill", r.fill, "(expected open+spr =", ask0, ")", "| exit", r.exit_reason, r.t_exit, r.exit,
      "| R", round(r.R, 3), "net pips", round(r.net_pips, 2))
assert abs(r.fill - ask0) < 1e-9
# 2) sell mirror
r2 = one(side=-1, sl=o0 + 10 * P, tp=o0 - 20 * P)
print("2) SELL market:", r2.status, "fill", r2.fill, "| exit", r2.exit_reason, r2.t_exit, r2.exit, "| R", round(r2.R, 3))
assert abs(r2.fill - o0) < 1e-9
# 3) limit buy 5 pips below open, expiring after 2h
r3 = one(side=1, entry_type="limit", entry=o0 - 5 * P, expire=t0 + pd.Timedelta(hours=2), sl=o0 - 15 * P, tp=o0 + 15 * P)
first = seg[(seg.low + seg.spr) <= o0 - 5 * P]
print("3) BUY limit:", r3.status, r3.get("t_fill"), "expected first touch", first.time.iloc[0] if len(first) else None, "| exit", r3.get("exit_reason"))
# 4) net pips = gross - commission, R = net / risk
if r.status == "filled":
    assert abs(r.net_pips - (r.gross_pips - COMM["EURUSD"])) < 1e-9 and abs(r.R - r.net_pips / r.risk_pips) < 1e-9
# 5) max hold 30 min -> TIME exit
r5 = one(side=1, sl=o0 - 100 * P, tp=o0 + 100 * P, max_hold_min=30)
print("5) max hold 30m:", r5.exit_reason, r5.t_exit, "| hold h", round(r5.hold_h, 2))
assert r5.exit_reason == "TIME" and abs(r5.hold_h - 0.5) < 0.02
# 6) break-even at 1R with a wide TP
r6 = one(side=1, sl=o0 - 5 * P, tp=o0 + 60 * P, be_at_r=1.0)
print("6) BE@1R:", r6.exit_reason, r6.t_exit, "R", round(r6.R, 3), "mfe_R", round(r6.mfe_R, 2))
# 7) weekend exit: Friday 20:00 buy, huge stops
r7 = simulate("EURUSD", pd.DataFrame([dict(t_signal=pd.Timestamp("2023-03-17 20:00"), side=1, entry_type="market", entry=np.nan,
             expire=pd.NaT, sl=0.5, tp=2.0, be_at_r=np.nan, max_hold_min=np.nan)]), one_at_a_time=False).iloc[0]
print("7) Friday:", r7.exit_reason, r7.t_exit)
assert r7.exit_reason == "WEEKEND"
print("\nALL HARNESS CHECKS PASSED")
