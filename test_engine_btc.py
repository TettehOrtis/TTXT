"""Integration test of the full engine on BTCUSDm (24/7). DUMB alternating-signal strategy - plumbing test only."""
import time, logging, sys
from scalper.broker import Broker
from scalper.engine import Engine, Signal, Strategy
from scalper.risk import RiskConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

class Dumb(Strategy):
    symbols = ["BTCUSD"]
    max_spread = {"BTCUSD": 60.0}
    def __init__(self): self.n = 0
    def on_bar(self, base, rates, tick, spec):
        self.n += 1
        side = 1 if self.n % 2 else -1
        return Signal(side=side, sl_dist=70.0, tp_dist=90.0, max_hold_s=100, be_at_r=0.6, trail_start_r=0.8, trail_dist_r=0.5, tag="dumb")

b = Broker(); b.connect()
cfg = RiskConfig(risk_pct=1.0, min_risk_usd=0.3, max_risk_usd=1.0, daily_target_min_usd=0.15, daily_target_pct=0.0,
                 daily_loss_pct=2.0, max_trades_day=6, loss_cooldown_s=5, min_lot_risk_mult=5)
e = Engine(b, Dumb(), cfg, max_open=1, poll_s=0.5)
e.run(deadline=time.time() + float(sys.argv[1]) * 60)
print("open positions at end:", len(b.positions()))
b.shutdown()
