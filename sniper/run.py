"""Sniper Spark clone - the runner.

    .venv/Scripts/python.exe -m sniper.run --status        # read-only: account, symbol, open basket, day's money
    .venv/Scripts/python.exe -m sniper.run --once          # one dry-run pass, prints what it WOULD do
    .venv/Scripts/python.exe -m sniper.run                 # DRY-RUN loop - never sends an order
    .venv/Scripts/python.exe -m sniper.run --live          # actually trade (demo unless SNIPER_RUN_MODE=MODE_LIVE)
    .venv/Scripts/python.exe -m sniper.run --print-env     # the full .env block with current values

Dry run is the default on purpose: nothing is ever sent unless --live is passed.

The loop is built never to die. Anything the broker can do to us - dropped wifi, terminal restart,
rejected order, a three-hour outage - is caught, logged, and retried on the next pass with a backoff.
Only a configuration or safety error (bad .env, a real account while in MODE_DEMO) stops the process,
because those should not be retried silently.
"""
import os
import sys
import time
import json
import signal
import logging
import argparse
import datetime as dt
from logging.handlers import RotatingFileHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sniper.config import Config                                       # noqa: E402
from sniper.broker import Broker, BrokerDown                           # noqa: E402
from sniper.engine import Engine                                       # noqa: E402
from sniper.telegram import Telegram, Bus, register_commands           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_stop = {"now": False}


def _setup_log(cfg, live):
    d = os.path.join(ROOT, cfg.log_dir)
    os.makedirs(d, exist_ok=True)
    log = logging.getLogger("sniper")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(os.path.join(d, "sniper.log" if live else "sniper_dry.log"),
                             maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


def _on_signal(signum, frame):
    _stop["now"] = True


def main(argv=None):
    p = argparse.ArgumentParser(description="Sniper Spark clone - basket bot for MT5")
    p.add_argument("--live", action="store_true", help="actually send orders (default: dry run)")
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    p.add_argument("--status", action="store_true", help="print status and exit (read-only)")
    p.add_argument("--print-env", action="store_true", help="print the .env block for current settings")
    p.add_argument("--symbol", help="override SNIPER_SYMBOL, e.g. BTCUSD")
    p.add_argument("--account", help="override SNIPER_ACCOUNT: MT5 | ZERO | Z")
    p.add_argument("--lot-size", type=float, help="override SNIPER_LOT_SIZE")
    p.add_argument("--positions", type=int, dest="positions_per_entry", help="positions per entry")
    p.add_argument("--target", type=float, dest="basket_profit_target_usd", help="basket profit target $")
    p.add_argument("--stop", type=float, dest="stop_loss_usd", help="basket stop loss $")
    p.add_argument("--daily-loss", type=float, dest="daily_loss_limit_usd", help="daily loss limit $")
    p.add_argument("--daily-target", type=float, dest="daily_profit_target_usd", help="daily profit target $")
    p.add_argument("--hours", dest="trading_hours",
                   help="override SNIPER_TRADING_HOURS, e.g. 07:00-12:00 (use \"\" for all day)")
    a = p.parse_args(argv)

    over = {k: v for k, v in vars(a).items()
            if k not in ("live", "once", "status", "print_env") and v is not None}
    try:
        cfg = Config.load(**over)
    except Exception as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2

    if a.print_env:
        print("# Sniper Spark settings - paste into .env and edit what you need")
        print(cfg.env_block())
        return 0

    log = _setup_log(cfg, a.live)
    log.info("=" * 78)
    for w in getattr(cfg, "warnings", []):
        log.warning("%s", w)
    log.info("sniper starting | %s | symbol=%s account=%s mode=%s",
             "LIVE" if a.live else "DRY RUN (no orders will be sent)",
             cfg.symbol, cfg.account, cfg.run_mode)
    log.info("basket: %d x %.2f lots | target +$%.2f | stop -$%.2f | cooldown %ds",
             cfg.positions_per_entry, cfg.lot_size, cfg.basket_profit_target_usd,
             cfg.stop_loss_usd, cfg.cooldown_seconds)
    log.info("daily: target $%.2f | loss limit $%.2f | block direction after %d losses",
             cfg.daily_profit_target_usd, cfg.daily_loss_limit_usd, cfg.block_direction_after_sl)
    if cfg.window:
        w_from, w_to = cfg.window                  # NOT 'a' - that is the parsed command line
        w_last = w_to - cfg.winddown_minutes
        log.info("window: %s UTC | trades %02d:%02d-%02d:%02d, manages only until %02d:%02d, "
                 "%s at %02d:%02d", cfg.trading_hours, w_from // 60, w_from % 60,
                 w_last // 60, w_last % 60, w_to // 60, w_to % 60,
                 "CLOSES EVERYTHING" if cfg.flat_at_window_end else "stops looking (nothing forced)",
                 w_to // 60, w_to % 60)
    else:
        log.info("window: all day - nothing will close a basket at a set time")

    broker = Broker(cfg, log)
    try:
        broker.connect()
    except RuntimeError as e:
        log.error("%s", e)
        return 2
    except BrokerDown as e:
        log.error("cannot reach MT5: %s", e)
        if a.status or a.once:
            return 1

    bus = Bus()
    tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id, log, bus)
    register_commands(tg, bus, cfg)
    tg.start()

    def notify(text):
        """Alerts are optional noise - never let them touch trading."""
        try:
            if cfg.telegram_alerts:
                tg.send(text)
        except Exception:
            pass

    eng = Engine(cfg, broker, log, dry=not a.live, notify=notify, bus=bus)

    if a.status:
        try:
            print(json.dumps(eng.status(), indent=2))
        except BrokerDown as e:
            print(f"broker down: {e}")
            return 1
        finally:
            broker.shutdown()
        return 0

    if a.once:
        try:
            eng.step()
            log.info("status: %s", json.dumps(eng.status()))
        except BrokerDown as e:
            log.warning("broker down: %s", e)
        finally:
            broker.shutdown()
        return 0

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    acc = broker.account
    notify(f"🚀 <b>SNIPER STARTED</b>\n\n"
           f"├ <code>{broker.sym}</code> on <code>{getattr(acc, 'login', '?')}</code> "
           f"({'DEMO' if getattr(acc, 'trade_mode', 1) == 0 else 'REAL'})\n"
           f"├ Mode: <b>{'LIVE' if a.live else 'DRY RUN — no orders'}</b>\n"
           f"├ Basket: <code>{cfg.positions_per_entry} x {cfg.lot_size}</code> lots\n"
           f"├ Target <b>+${cfg.basket_profit_target_usd:,.2f}</b> / "
           f"stop <b>-${cfg.stop_loss_usd:,.2f}</b>\n"
           f"└ Balance: <code>${getattr(acc, 'balance', 0):,.2f}</code>\n\n"
           f"Send /help for what I can do.")

    last_beat = 0.0
    down_since = None
    while not _stop["now"]:
        try:
            eng.step()
            if down_since:
                gone = time.time() - down_since
                log.info("broker is back after %.0fs", gone)
                notify(f"✅ <b>CONNECTION RESTORED</b>\n\nWas down for {gone/60:.1f} minutes. "
                       f"Carrying on where it left off.")
                down_since = None
            now = time.time()
            if now - last_beat > 300:                   # a heartbeat every 5 minutes
                log.info("alive | %s", json.dumps(eng.status()))
                last_beat = now
        except KeyboardInterrupt:
            _stop["now"] = True
            break
        except BrokerDown as e:
            if down_since is None:
                down_since = time.time()
                log.warning("broker down: %s", e)
                notify(f"⚠️ <b>CONNECTION LOST</b>\n\n<code>{e}</code>\n\n"
                       f"Retrying. Open trades keep their stop at the broker.")
        except RuntimeError as e:
            log.error("stopping: %s", e)
            notify(f"🚨 <b>STOPPED</b>\n\n<code>{e}</code>")
            break
        except Exception as e:
            log.exception("unexpected error (continuing): %s", e)
        time.sleep(max(0.2, cfg.poll_seconds))

    log.info("shutting down cleanly - open positions are left as they are at the broker")
    try:
        s = eng.status()
        notify(f"🔌 <b>SNIPER STOPPED</b>\n\n"
               f"├ Open legs left at the broker: <code>{s.get('positions', 0)}</code>\n"
               f"└ Today: <b>${s.get('realized_today', 0):+,.2f}</b>")
        time.sleep(1.5)
    except Exception:
        pass
    try:
        eng.save()
    except Exception:
        pass
    tg.stop()
    broker.shutdown()
    # 3 = "a human asked us to stop" (Ctrl+C or stop_sniper.ps1). The supervisor .bat must NOT restart
    # on this, or Ctrl+C would just bring the bot straight back.
    return 3 if _stop["now"] else 0


if __name__ == "__main__":
    sys.exit(main())
