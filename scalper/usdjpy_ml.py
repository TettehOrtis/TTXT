"""USDJPY 20-minute ML scalp on the Exness ZERO account (FORWARD TEST - weak evidence, see models/usdjpy_h20.json).
Every closed M1 bar 07:00-20:00 UTC: predict the 20-minute move; trade only the most extreme 0.5% of scores.
Exit: time stop after 20 minutes; safety stop 20 pips at the broker. No take profit (the backtest used a pure time exit).
"""
import os, sys, json, logging
import numpy as np
import lightgbm as lgb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "research"))
from lib import features2, prep_live, pip_size   # identical feature code to the research pipeline
from .engine import Strategy, Signal

log = logging.getLogger("usdjpy_ml")
OTHERS = ("EURUSD", "US30", "USOIL", "BTCUSD")


class UsdJpyML(Strategy):
    symbols = ["USDJPY"]
    max_spread = {"USDJPY": 0.012}        # 1.2 pips; Zero is ~0 in these hours
    max_entry_slip_r = 0.1                # close at once if entry slips > 2 pips
    n_history = 6000                      # M1 bars the engine fetches (features need the previous full day)

    def __init__(self, broker, model_path="models/usdjpy_h20.txt", meta_path="models/usdjpy_h20.json", n_bars=4500):
        self.b = broker
        self.model = lgb.Booster(model_file=model_path)
        self.meta = json.load(open(meta_path))
        self.n_bars = n_bars

    def session_ok(self, now, base):
        if now.weekday() >= 5 or (now.weekday() == 4 and now.hour >= 19):
            return False
        return 7 <= now.hour < 20

    def compute(self, base, closed):
        frames = {}
        for o in OTHERS:
            r = self.b.rates(self.b.sym(o), self.n_bars)
            if r is not None:
                frames[o] = r
        d = prep_live(closed.tail(self.n_bars).reset_index(drop=True), base)
        f = features2(d, base, frames=frames)[self.meta["features"]]
        row = f.iloc[-1]
        if row.isna().any():
            log.info("feature NaN (%s) - skip", ", ".join(row.index[row.isna()][:5]))
            return None
        return float(self.model.predict(row.values.astype(np.float32).reshape(1, -1))[0]), row

    def on_bar(self, base, closed, tick, spec):
        res = self.compute(base, closed)
        if res is None:
            return None
        pred, _ = res
        hi, lo = self.meta["thr_hi"], self.meta["thr_lo"]
        log.info("bar %s pred=%+.3f (trade if <= %+.3f or >= %+.3f)", closed.time.iloc[-1], pred, lo, hi)
        if lo < pred < hi:
            return None
        side = 1 if pred >= hi else -1
        return Signal(side=side, sl_dist=self.meta["sl_pips"] * pip_size(base), tp_dist=0.0,
                      max_hold_s=self.meta["H"] * 60, be_at_r=0.0, trail_start_r=0.0, tag=f"uj{pred:+.2f}")
