"""Start the bot.

  .venv\\Scripts\\python.exe run_bot.py --dry-run          # signals only, no orders (recommended first)
  .venv\\Scripts\\python.exe run_bot.py                    # trade the DEMO account
  .venv\\Scripts\\python.exe run_bot.py --capital 100      # size as if the account had $100 (range 100-500)

Stop with Ctrl+C: open positions keep their server-side stop; the bot re-adopts them on restart.
"""
import argparse, logging, os, sys
from scalper.broker import Broker
from scalper.engine import Engine
from scalper.risk import RiskConfig
from scalper.gold_ml import GoldML


class CappedBroker(Broker):
    """Sizing/targets see at most `cap` dollars of equity/balance, so a $493 demo behaves like a $100-$500 account."""

    def __init__(self, cap, **kw):
        super().__init__(**kw)
        self.cap = cap

    def account(self):
        a = super().account()
        if a is None or not self.cap:
            return a
        d = a._asdict()
        d["balance"] = min(a.balance, self.cap)
        d["equity"] = min(a.equity, self.cap - (a.balance - a.equity) if a.balance > self.cap else a.equity)
        return type("Acct", (), d)()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--capital", type=float, default=500.0, help="treat the account as this size (100-500)")
    ap.add_argument("--hours", type=float, default=0, help="stop after N hours (0 = run until Ctrl+C)")
    a = ap.parse_args()
    if not 100 <= a.capital <= 500:
        sys.exit("--capital must be between 100 and 500")
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s",
                        handlers=[logging.FileHandler("logs/bot.log", encoding="utf-8"), logging.StreamHandler()])
    # Gold min lot (0.01 = 1 oz) with a 5xATR stop risks ~$8-15 per trade at 2026 volatility.
    # Small accounts therefore need a higher risk % and a larger min-lot tolerance - this is shown, not hidden.
    small = a.capital < 300
    cfg = RiskConfig(risk_pct=3.0 if small else 2.0, min_risk_usd=1.0, max_risk_usd=15.0,
                     min_lot_risk_mult=5.0 if small else 2.0,
                     daily_target_pct=3.0, daily_target_min_usd=2.0, daily_loss_pct=6.0 if small else 4.0,
                     giveback_frac=0.5, max_trades_day=4, max_consec_losses=3, cooldown_min=60,
                     loss_cooldown_s=300, min_equity=max(40.0, 0.4 * a.capital))
    b = CappedBroker(cap=a.capital)
    b.connect()
    import time
    e = Engine(b, GoldML(b), cfg, max_open=1, poll_s=0.5, dry_run=a.dry_run)
    e.run(deadline=time.time() + a.hours * 3600 if a.hours else None)
    b.shutdown()


if __name__ == "__main__":
    main()
