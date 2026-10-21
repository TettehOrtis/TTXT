"""AUT13 group-F checks: fill bar, stale basket bars, corrupt bars dropped, beta window, per-pair basket freshness."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import aut13_leadlag as L
C, A = L.C, L.A
out = {}
# corrupt bars dropped
dropped = {}
for s in list(L.USD_PAIRS) + ["XAUUSD"]:
    b0 = L._ORIG_BARS(s, "M1")
    _, n = A.screen_bars(b0)
    dropped[s] = int(n)
out["corrupt_bars_dropped"] = dropped
# F1: fill bar is the decision bar's next minute
chk = {}
for tg in ("XAUUSD", "EURUSD"):
    t = pd.read_csv(os.path.join(L.OUT, "trades_%s_BASE_EXPLORE.csv" % tg), parse_dates=["t_signal", "t_fill"])
    d = (t.t_fill - t.t_signal).dt.total_seconds() / 60
    chk[tg] = dict(fill_minus_signal_min_max=float(d.max()), fill_minus_signal_min_min=float(d.min()), trades=len(t))
out["fill_bar_check"] = chk
# basket freshness at EXPLORE events
fr = {}
for tg in ("XAUUSD", "EURUSD"):
    arr = L.build_arrays(tg)
    ev = L.signals(tg)
    ii = ev[(ev.k == 5)].i.values
    fr[tg] = dict(names=arr["names"], fresh_share_at_signals={n: round(float(arr["fresh"][j][ii].mean()), 3) for j, n in enumerate(arr["names"])},
                  mean_pairs_used=round(float(ev[ev.k == 5].n_pairs.mean()), 2))
out["basket_freshness"] = fr
# beta never uses the window it is applied to: by construction beta[i] sums 1-min returns at positions (i-k-W, i-k]; spot-check
arr = L.build_arrays("XAUUSD")
k = 5
beta, corr, sdD, sdT = L.beta_series(arr, k)
j = 2_000_000
both = arr["both"]
x = np.where(both, arr["D1"], 0.0); y = np.where(both, arr["T1"], 0.0)
lo, hi = j - k - L.W + 1, j - k + 1
b_manual = (x[lo:hi] * y[lo:hi]).sum() / (x[lo:hi] ** 2).sum()
out["beta_spot_check"] = dict(bar=int(j), window=[int(lo), int(hi - 1)], decision_bar=int(j), beta_series=float(beta[j]), beta_manual=float(b_manual),
                              window_ends_k_bars_before_decision=int(j - (hi - 1)))
json.dump(out, open(os.path.join(L.OUT, "checks_F.json"), "w"), indent=1, default=float)
print(json.dumps(out, indent=1, default=float))
