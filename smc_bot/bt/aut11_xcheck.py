"""AUT11 cross-check: the daily engine's costs against common.simulate, on trades that never touch a weekend."""
import os, sys, warnings
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import common as C
import autopsy_lib as A
warnings.simplefilter("ignore")
import aut11_trend as T

_ORIG = C.bars
_M = {}
def bars(sym, tf):
    k = (sym, tf)
    if k not in _M:
        b, _ = A.screen_bars(_ORIG(sym, tf))
        b, _ = A._repair_spread(b, sym)
        for c in ("time", "tclose"):
            b[c] = b[c].astype("datetime64[ns]")
        _M[k] = b
    return _M[k]
C.bars = bars

rows = []
rng = np.random.default_rng(1)
for sym in ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "EURCHF", "GBPJPY", "XAUUSD"]:
    t = T.run_rule("TS20", "OPP", "VALIDATE" if sym == "XAUUSD" else "EXPLORE", syms=[sym])
    t = t[(t.exit_reason == "OPP") & (t.nights <= 3)]
    # no Friday 20:45 flatten inside the trade: require entry and exit within Mon-Thu or exit <= Fri 20:44
    def clean(r):
        rng_ = pd.date_range(r.t_fill.normalize(), r.t_exit.normalize(), freq="D")
        for d in rng_:
            if d.dayofweek == 4:
                f0 = d + pd.Timedelta(hours=20, minutes=45)
                if r.t_exit >= f0 and r.t_fill < d + pd.Timedelta(days=1):
                    return False
        return (r.t_exit - r.t_fill) < pd.Timedelta(hours=72) and (r.t_exit.dayofweek not in (5, 6))
    t = t[[clean(r) for r in t.itertuples()]]
    t = t.iloc[:60] if len(t) > 60 else t
    if len(t) == 0:
        continue
    d = t.side.values
    fill = t.fill.values
    o = pd.DataFrame({"t_signal": t.t_fill.values, "side": d, "entry_type": "market", "entry": np.nan,
                      "expire": pd.NaT, "sl": t.fill.values - d * 1000 * C.pip(sym), "tp": np.nan, "be_at_r": np.nan,
                      "max_hold_min": ((t.t_exit - t.t_fill).dt.total_seconds() / 60).values})
    sm = C.simulate(sym, o, one_at_a_time=False)
    sm = sm[sm.status == "filled"].reset_index(drop=True)
    m = t.reset_index(drop=True).iloc[:len(sm)]
    for k in range(min(len(m), len(sm))):
        rows.append(dict(sym=sym, mine_net_pips=m.net_pips[k], sim_net_pips=sm.net_pips[k],
                         diff_pips=m.net_pips[k] - sm.net_pips[k], mine_fill=m.fill[k], sim_fill=sm.fill[k],
                         fill_diff=m.fill[k] - sm.fill[k]))
r = pd.DataFrame(rows)
r["abs_diff"] = r.diff_pips.abs()
print(r.groupby("sym").agg(n=("abs_diff", "size"), mean_abs_diff_pips=("abs_diff", "mean"), median_abs=("abs_diff", "median"),
                           max_abs=("abs_diff", "max"), mean_fill_diff=("fill_diff", "mean")).round(4))
print("overall", len(r), "mean abs diff pips", round(r.abs_diff.mean(), 3), "median", round(r.abs_diff.median(), 3),
      "mean signed", round(r.diff_pips.mean(), 3))
# pure-cost identity: gross edge zero => both should lose the same round trip. Compare fills (should match exactly)
print("share of identical fills:", round(float((r.fill_diff.abs() < 1e-9).mean()), 3))
r.to_csv(os.path.join(T.OUT, "xcheck_vs_simulate.csv"), index=False)
