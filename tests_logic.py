"""Offline unit tests for risk + engine logic (no broker connection). Run: python tests_logic.py"""
import time, types, datetime as dt
import pandas as pd
import MetaTrader5 as mt5
import scalper.risk as risk
import scalper.engine as eng

risk.STATE = "logs/_test_state.json"
NOW = time.time()


class D:  # fake deal
    def __init__(self, pid, entry, t, profit=0.0, comm=0.0, swap=0.0, fee=0.0):
        self.position_id, self.entry, self.time = pid, entry, t
        self.profit, self.commission, self.swap, self.fee, self.magic = profit, comm, swap, fee, 1


class FakeBroker:
    def __init__(self):
        self.deals, self.pos, self.bal, self.eq = [], [], 500.0, 500.0

    def account(self): return types.SimpleNamespace(balance=self.bal, equity=self.eq)
    def deals_since(self, ts): return list(self.deals)
    def positions(self, s=None): return self.pos


def add_closed(b, pid, t, net):
    b.deals.append(D(pid, mt5.DEAL_ENTRY_IN, t - 60))
    b.deals.append(D(pid, mt5.DEAL_ENTRY_OUT, t, profit=net))


def fresh(cfg=None):
    b = FakeBroker()
    rm = risk.RiskManager(b, cfg or risk.RiskConfig())
    rm.day = risk.DayState()
    return b, rm


ds = risk.trading_day_start().timestamp()
t0 = max(NOW - 3000, ds + 10)

# 1) pause is armed once per losing streak and NOT re-armed by the same loss after it expires
b, rm = fresh()
for i in range(3):
    add_closed(b, i + 1, t0 + i * 100, -1.0)
rm.refresh()
assert rm.day.consec_losses == 3 and rm.day.pause_until > NOW, "pause should arm after 3 losses"
rm.day.pause_until = NOW - 1                       # pause expired
rm.refresh()
assert rm.day.pause_until <= NOW, "pause must not re-arm from the same loss"
add_closed(b, 4, t0 + 400, -1.0)                   # a NEW loss re-arms
rm.refresh()
assert rm.day.pause_until > NOW, "new loss should re-arm the pause"
print("ok 1  pause arms once per streak")

# 2) giveback uses REALIZED peak: a floating spike that retraces must not lock the day
b, rm = fresh()
b.bal = 500.0
rm.refresh()                                        # target = max(2, 2% of 500 = 10) = 10
b.pos = [types.SimpleNamespace(profit=4.0, swap=0.0)]
rm.refresh()
b.pos = [types.SimpleNamespace(profit=1.0, swap=0.0)]
rm.refresh()
assert not rm.day.locked, f"floating spike/retrace must not lock: {rm.day.locked}"
print("ok 2  no giveback lock from floating spike")

# 3) realized giveback: peak >= 50% target then realized halves -> lock
b, rm = fresh()
add_closed(b, 1, t0, +6.0)
rm.refresh()
assert not rm.day.locked and rm.day.peak_real == 6.0
add_closed(b, 2, t0 + 100, -3.5)
rm.refresh()
assert rm.day.locked.startswith("GIVEBACK"), rm.day.locked
print("ok 3  realized giveback locks")

# 4) target hit via realized+floating locks; entry commission counted in the position net
b, rm = fresh()
b.deals.append(D(1, mt5.DEAL_ENTRY_IN, t0, comm=-0.5))
b.deals.append(D(1, mt5.DEAL_ENTRY_OUT, t0 + 60, profit=11.0, comm=-0.5))
rm.refresh()
assert abs(rm.day.realized - 10.0) < 1e-9 and rm.day.n_trades == 1 and rm.day.locked.startswith("TARGET"), (rm.day.realized, rm.day.locked)
print("ok 4  target lock + entry commission counted, partial deals = 1 trade")

# 5) API failure (None) must not wipe state or trigger actions
b, rm = fresh()
add_closed(b, 1, t0, -2.0)
rm.refresh()
before = rm.day.realized
b.deals_since = lambda ts: None
rm.refresh()
assert rm.day.realized == before
print("ok 5  history failure keeps last state")

# 6) fresh-bar timing
class FB:
    pass
e = eng.Engine.__new__(eng.Engine)
e.last_bar = {}
def rates_upto(minute_epoch, n=400, skip_last=0):
    ts = [minute_epoch - 60 * (n - 1 - i) for i in range(n)]
    return pd.DataFrame({"time": pd.to_datetime(ts, unit="s")})
now_min = 1_790_000_040
tick = types.SimpleNamespace(time=now_min + 3)
r = rates_upto(now_min)                            # last row == forming bar (time == now_min)
assert e._fresh_bar("X", r, tick) is not None, "just-closed bar within window should be accepted"
assert e._fresh_bar("X", r, tick) is None, "same bar must not be evaluated twice"
r2 = rates_upto(now_min + 60)
assert e._fresh_bar("X", r2, types.SimpleNamespace(time=now_min + 60 + 40)) is None, "late (40 s) must be rejected"
r3 = rates_upto(now_min + 120)
r3 = r3.iloc[:-2]                                  # minute missing (no ticks): last closed bar is 2 min old
assert e._fresh_bar("X", r3, types.SimpleNamespace(time=now_min + 122)) is None, "stale bar must be rejected"
print("ok 6  fresh-bar timing")

# 7) rollover window (DST aware): 16:50-17:10 New York
summer = dt.datetime(2026, 7, 8, 20, 55, tzinfo=dt.timezone.utc)   # 16:55 EDT
winter = dt.datetime(2026, 1, 7, 21, 55, tzinfo=dt.timezone.utc)   # 16:55 EST
assert eng.in_rollover_window(summer) and eng.in_rollover_window(winter)
assert not eng.in_rollover_window(dt.datetime(2026, 7, 8, 19, 0, tzinfo=dt.timezone.utc))
print("ok 7  rollover window is DST aware")

import os
os.path.exists(risk.STATE) and os.remove(risk.STATE)
print("ALL LOGIC TESTS PASSED")
