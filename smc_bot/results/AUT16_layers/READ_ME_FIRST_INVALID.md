# READ THIS FIRST (2026-10-20): the AUT16 results in this folder are not valid

`common.simulate` (the research simulator) let the next trade enter at the open of the SAME bar in which the previous trade exited (`busy_until = t1[k]`). A chained trade therefore started before the previous exit had happened and got hindsight about that bar. The real EA cannot do that.

Found by comparing the simulator with the real EA run through the MT5 Strategy Tester, trade by trade (second trade of 2025-02-03: simulator entered 08:14, the EA 08:15).

Effect (aut42_overlap.py, PREREG addendum 27, open windows, 5 variants, plain exit):
| window | profit factor as reported | profit factor fixed | $ per trade fixed |
|---|---|---|---|
| 2009-10 | 1.11 | 0.88 | -3.16 |
| 2011-16 | 1.16 | 0.90 | -2.67 |
| 2017-20 | 1.11 | 0.90 | -2.61 |
The real EA in the tester on 2025-02 to 2026-10 gave profit factor 0.92 (Standard) and 0.96 (Zero), the same picture.

So: the AUT16 entry has no edge once the overlap is removed. The sealed-test pass, the size rule (addendum 18), the T3 split (22-23), the wait tests (24-25) and every other number in this folder that came from `common.simulate(one_at_a_time=True)` or from the one-at-a-time selection in aut30/aut32+ were produced with the leak and must not be used. The simulator is fixed (common.py, BT20); rerun anything before trusting it.
