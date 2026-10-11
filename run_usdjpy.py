"""USDJPY scalp bot on the ZERO demo account (forward test).

  .venv\\Scripts\\python.exe run_usdjpy.py --dry-run     # signals only
  .venv\\Scripts\\python.exe run_usdjpy.py               # trade the Zero demo, 0.10 lot
  .venv\\Scripts\\python.exe usdjpy_report.py            # results so far

Needs the laptop ON and online 07:00-20:00 UTC (Mon-Fri). Positions always carry a 20-pip stop at the broker.
"""
import argparse, logging, os, time
import scalper.engine as eng
import scalper.risk as risk
from scalper.broker import Broker
from scalper.usdjpy_ml import UsdJpyML

LOGDIR = "logs/usdjpy"
os.makedirs(LOGDIR, exist_ok=True)
eng.TRADES_CSV, eng.POS_STATE, risk.STATE = f"{LOGDIR}/trades.csv", f"{LOGDIR}/positions.json", f"{LOGDIR}/state.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lot", type=float, default=0.10)
    ap.add_argument("--hours", type=float, default=0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s",
                        handlers=[logging.FileHandler(f"{LOGDIR}/bot.log", encoding="utf-8"), logging.StreamHandler()])
    cfg = risk.RiskConfig(fixed_lot=a.lot, daily_target_pct=100.0, daily_target_min_usd=1e9,   # no profit lock: we want the full sample
                          daily_loss_pct=30.0 / 450 * 100, giveback_frac=1.0, max_trades_day=6, max_consec_losses=4,
                          cooldown_min=60, loss_cooldown_s=0, min_equity=250.0)
    b = Broker(suffix="z", magic=660066, env="Z")
    b.connect()
    e = eng.Engine(b, UsdJpyML(b), cfg, max_open=1, poll_s=0.5, dry_run=a.dry_run)
    e.run(deadline=time.time() + a.hours * 3600 if a.hours else None)
    b.shutdown()


if __name__ == "__main__":
    main()
