"""Gold ML strategy (FORWARD-TEST CANDIDATE - not a proven edge).

Model: LightGBM regression of the 120-minute forward gold return (vol-normalised), 60 features (price action,
session levels, cross-asset EURUSD/USDJPY/US30/USOIL/BTCUSD). Trades only the most extreme ~1% of scores.
Exit: 120-minute time stop + server-side catastrophic stop at 5x ATR60. No TP, no trailing (the backtest showed
tighter stops destroy the small edge). Entries 07:00-18:30 UTC so the exit never crosses the rollover.
Walk-forward 2023-2026: about +$0.20..0.50 per trade per 0.01 lot, ~0.3 trades/day, t~1 -> treat as UNPROVEN.
"""
import os, sys, json, datetime as dt, logging
import numpy as np
import lightgbm as lgb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "research"))
from lib import features2, prep_live, pip_size   # identical feature code to the research pipeline
from .engine import Strategy, Signal

log = logging.getLogger("gold_ml")
OTHERS = ("EURUSD", "USDJPY", "US30", "USOIL", "BTCUSD")


class GoldML(Strategy):
    symbols = ["XAUUSD"]
    max_spread = {"XAUUSD": 0.45}          # $0.45; median ~0.16-0.26
    max_entry_slip_r = 0.15
    n_history = 6000

    def __init__(self, broker, model_path="models/gold_h120.txt", meta_path="models/gold_h120.json", n_bars=4500):
        self.b = broker
        self.model = lgb.Booster(model_file=model_path)
        self.meta = json.load(open(meta_path))
        self.n_bars = n_bars
        self.last_pred = None

    def session_ok(self, now, base):
        if now.weekday() >= 5 or (now.weekday() == 4 and now.hour >= 19):
            return False
        m = now.hour * 60 + now.minute
        return 7 * 60 <= m <= 18 * 60 + 30

    def compute(self, base, closed):
        """Feature row + prediction for the last closed bar. Returns (pred, atr60_units, row) or None."""
        frames = {}
        for o in OTHERS:
            r = self.b.rates(self.b.sym(o), self.n_bars)
            if r is not None:
                frames[o] = r
        d = prep_live(closed.tail(self.n_bars).reset_index(drop=True), base)
        f = features2(d, base, frames=frames)
        f = f[self.meta["features"]]
        row = f.iloc[-1]
        if row.isna().any():
            log.info("feature NaN (%s) - skip", ", ".join(row.index[row.isna()][:5]))
            return None
        pred = float(self.model.predict(row.values.astype(np.float32).reshape(1, -1))[0])
        return pred, float(row["atr60"]), row

    def on_bar(self, base, closed, tick, spec):
        res = self.compute(base, closed)
        if res is None:
            return None
        pred, atr_u, _ = res
        self.last_pred = pred
        hi, lo = self.meta["thr_hi"], self.meta["thr_lo"]
        log.info("bar %s pred=%+.3f (thr %+.2f/%+.2f) atr60=%.1fu", closed.time.iloc[-1], pred, lo, hi, atr_u)
        if lo < pred < hi:
            return None
        side = 1 if pred >= hi else -1
        sl_dist = self.meta["k_sl"] * atr_u * pip_size(base)
        return Signal(side=side, sl_dist=sl_dist, tp_dist=0.0, max_hold_s=self.meta["H"] * 60,
                      be_at_r=0.0, trail_start_r=0.0, tag=f"gml{pred:+.2f}")
