"""The basket engine: open a group, manage it on combined dollars, obey the daily rules.

The robot reduces to one trade of the combined size, closed at +target or -stop of COMBINED floating
money. Everything here is written so that the broker, not this process, holds the truth:

  * open positions are re-read from MT5 every loop - a restart finds its own basket and keeps managing it;
  * the day's realised money is re-read from MT5's deal history - a restart cannot forget the daily limit;
  * the state file only carries what MT5 cannot answer (the losing streak per direction, the cooldown),
    and if it is missing those reset to safe values rather than guessing.

A disaster stop-loss is attached to every position, placed far wider than the basket's own dollar stop so
it never fires in normal running. It exists for one case only: this process dies and never comes back,
and nothing else would ever close the trade.
"""
import os
import time
import json
import datetime as dt

import MetaTrader5 as mt5

from .broker import BrokerDown, BUY, SELL
from . import strategy as ST

IDLE, IN_BASKET, HALTED = "IDLE", "IN_BASKET", "HALTED"


def _utc_now():
    return dt.datetime.now(dt.timezone.utc)


def _day_start(t=None):
    t = t or _utc_now()
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


class Engine:
    def __init__(self, cfg, broker, log, dry=True, notify=None, bus=None):
        self.cfg = cfg
        self.b = broker
        self.log = log
        self.dry = dry
        self._notify_raw = notify or (lambda _t: None)
        self.bus = bus
        self.halt_reason = None
        self._history_ok = False
        self._history_since = None
        self.state_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       cfg.state_file)
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        self.st = self._load()
        self._last_bar = None
        self._warned_halt = None
        self._last_phase = None          # for the window open/close alerts
        self._warned_closing = None      # the "10 minutes left" nudge, once per day

    def notify(self, text):
        """A broken or slow notifier must never reach the trading logic. Belt and braces: run.py also
        guards this, but the engine does not trust its callback either."""
        try:
            self._notify_raw(text)
        except Exception as e:
            self.log.debug("notify failed (ignored): %s", e)

    # ------------------------------------------------------------------ state
    def _blank(self):
        return dict(day=_day_start().strftime("%Y-%m-%d"), streak={"1": 0, "-1": 0},
                    cooldown_until=0.0, baskets=[], opened_at=None, open_side=None)

    def _load(self):
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    s = json.load(f)
                if s.get("day") == _day_start().strftime("%Y-%m-%d"):
                    return s
                self.log.info("state is from %s, starting a fresh day", s.get("day"))
            except Exception as e:
                self.log.warning("state unreadable (%s) - starting clean", e)
        return self._blank()

    def save(self):
        """Write the state file atomically, and never let a file problem reach the trading loop.

        os.replace can fail on Windows with "Access is denied" when an editor, a backup tool or an
        antivirus scanner has the file open for a moment. ref_trader.py hit the same thing. A short
        retry clears it; if it still will not write, the bot carries on regardless - the broker, not
        this file, is the source of truth for anything that matters.
        """
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.st, f, indent=2, default=str)
            for attempt in range(5):
                try:
                    os.replace(tmp, self.state_path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.1 * (attempt + 1))
        except Exception as e:
            self.log.warning("could not write %s (%s) - carrying on", self.state_path, e)

    def _roll_day(self):
        today = _day_start().strftime("%Y-%m-%d")
        if self.st.get("day") != today:
            self.log.info("new UTC day %s - daily limits and direction blocks reset", today)
            keep = dict(self._blank(), day=today)
            keep["cooldown_until"] = self.st.get("cooldown_until", 0.0)
            self.st = keep
            self.save()

    # ------------------------------------------------------------------ helpers
    def _usd_to_price(self, usd, lots):
        """Price distance that is worth `usd` on `lots` of this symbol."""
        s = self.b.spec
        per_price = (s.trade_tick_value / s.trade_tick_size) * lots
        return usd / per_price if per_price > 0 else 0.0

    def day_money(self, positions):
        realized, n = self.b.realized_since(_day_start())
        floating = self.b.basket_money(positions)[0] if positions else 0.0
        return realized, floating, n

    def _weekend_flat_due(self):
        if not self.cfg.flat_before_weekend:
            return False
        try:
            day, hhmm = self.cfg.weekend_flat_utc.split()
            hh, mm = (int(x) for x in hhmm.split(":"))
        except Exception:
            return False
        names = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        t = _utc_now()
        if t.weekday() > names.index(day.upper()):
            return True
        return t.weekday() == names.index(day.upper()) and (t.hour, t.minute) >= (hh, mm)

    # ------------------------------------------------------------- the trading window
    # Three questions, deliberately kept apart, because the answers differ for half an hour each day:
    #   _window_phase()      where are we now
    #   _may_open()          may a NEW basket start
    #   _window_flat_due()   must an OPEN basket be closed right now
    def _window_phase(self, t=None):
        """'ALL_DAY', 'BEFORE', 'OPEN', 'WINDDOWN' or 'AFTER'."""
        w = getattr(self.cfg, "window", None)
        if not w:
            return "ALL_DAY"
        t = t or _utc_now()
        now = t.hour * 60 + t.minute
        start, end = w
        if now < start:
            return "BEFORE"
        if now >= end:
            return "AFTER"
        return "WINDDOWN" if now >= end - self.cfg.winddown_minutes else "OPEN"

    def _may_open(self, t=None):
        return self._window_phase(t) in ("ALL_DAY", "OPEN")

    def _window_flat_due(self, t=None):
        """True once the window has ended, so anything still open must go.

        Note this stays True for the rest of the day, not just at the closing minute. A basket adopted
        after a restart at 14:00 must still be closed, and a close that fails is retried next loop.
        """
        if not getattr(self.cfg, "window", None) or not self.cfg.flat_at_window_end:
            return False
        return self._window_phase(t) == "AFTER"

    def _minutes_left(self, t=None):
        """Minutes until the window shuts, or None when there is no window."""
        w = getattr(self.cfg, "window", None)
        if not w:
            return None
        t = t or _utc_now()
        return w[1] - (t.hour * 60 + t.minute)

    def _await_history(self, timeout=20.0):
        """Do not trade until MT5 has handed us today's deal history.

        Straight after a fresh login, history_deals_get returns nothing for a few seconds. The bot would
        read today's money as $0 - which silently resets the daily loss limit on every restart, so a bad
        day could be restarted away. Our own state file knows how many legs we closed today, so wait
        until the broker's history agrees with it.
        """
        if self._history_ok:
            return
        want = sum(int(b.get("n", 0)) for b in self.st.get("baskets", []))
        if want == 0:
            self._history_ok = True              # nothing of ours to find today; nothing to wait for
            return
        if self._history_since is None:
            self._history_since = _utc_now().timestamp()
        _money, got = self.b.realized_since(_day_start())
        if got >= want:
            self.log.info("deal history in sync (%d legs today)", got)
            self._history_ok = True
            return
        if _utc_now().timestamp() - self._history_since > timeout:
            self.log.warning("deal history still shows %d of %d legs after %.0fs - carrying on, but "
                             "today's total may read low until MT5 catches up", got, want, timeout)
            self._history_ok = True
            return
        raise BrokerDown(f"waiting for MT5 to hand over today's history ({got}/{want} legs)")

    # ------------------------------------------------------------------ the loop body
    def step(self):
        self.b.ensure()
        self._await_history()
        self._roll_day()
        pos = self.b.positions()
        self._serve_requests(pos)
        self._window_alerts()
        if pos:
            self._manage(pos)
        else:
            self._maybe_open()
        self._publish(pos)

    def _window_alerts(self):
        """Tell the chat when the window opens, when it is nearly over, and when it shuts.

        The 'nearly over' one is the point of the exercise: it is the moment to decide whether to stay
        at the laptop or let the force-flat handle it.
        """
        phase = self._window_phase()
        if phase == "ALL_DAY":
            return
        first = self._last_phase is None                 # a restart mid-window is not a transition
        prev, self._last_phase = self._last_phase, phase
        hhmm = self.cfg.trading_hours
        if not first:
            if prev == "BEFORE" and phase in ("OPEN", "WINDDOWN"):
                self.notify(f"🟢 <b>WINDOW OPEN</b> — {hhmm} UTC\n\nTrading <code>{self.b.sym}</code>.")
            elif prev in ("OPEN", "WINDDOWN") and phase == "AFTER":
                self.notify(
                    f"🔴 <b>WINDOW CLOSED</b> — {hhmm} UTC\n\nNo more trades today."
                    + ("\nEverything open has been closed. Safe to shut the laptop."
                       if self.cfg.flat_at_window_end else
                       "\n⚠️ Force-flat is OFF, so any open basket is still live."))
            elif prev == "OPEN" and phase == "WINDDOWN":
                self.notify(f"🟠 <b>WINDING DOWN</b> — no new baskets.\n\nOpen ones are still managed "
                            f"until {hhmm.split('-')[1]} UTC.")
        left = self._minutes_left()
        today = _day_start().strftime("%Y-%m-%d")
        if left is not None and 0 < left <= 10 and self._warned_closing != today:
            self._warned_closing = today
            self.notify(f"⏳ <b>{left} MIN LEFT</b>\n\nThe window shuts at {hhmm.split('-')[1]} UTC and "
                        f"anything still open is closed at market.")

    def _serve_requests(self, pos):
        """Act on anything Telegram asked for. Runs on the TRADING thread, never the Telegram one."""
        if self.bus is None:
            return
        for r in self.bus.drain():
            w = r.get("what")
            if w == "pause":
                self.bus.paused = True
                self.log.info("paused by telegram")
            elif w == "resume":
                self.bus.paused = False
                self._warned_halt = None
                self.log.info("resumed by telegram")
            elif w == "close" and pos:
                money = self.b.basket_money(pos)[0]
                side = BUY if pos[0].type == mt5.POSITION_TYPE_BUY else SELL
                self.log.info("closing by telegram request")
                self._close_all(pos, money, "BY HAND", side)
            elif w == "flat":
                # Pause FIRST. If the close then fails and is retried next loop, the bot must not spend
                # that loop opening a fresh basket.
                self.bus.paused = True
                if pos:
                    money = self.b.basket_money(pos)[0]
                    side = BUY if pos[0].type == mt5.POSITION_TYPE_BUY else SELL
                    self.log.info("going flat by telegram request")
                    self._close_all(pos, money, "FLAT", side)
                else:
                    self.log.info("flat requested - nothing open, standing down")
            elif w == "stop":
                raise KeyboardInterrupt("stop requested from telegram")

    def _publish(self, pos):
        if self.bus is None:
            return
        try:
            acc = self.b.account
            realized, _ = self.b.realized_since(_day_start())
            floating = self.b.basket_money(pos)[0] if pos else 0.0
            done = self.st.get("baskets", [])
            self.bus.publish(dict(
                symbol=self.b.sym, live=not self.dry, mode=self.cfg.run_mode,
                balance=getattr(acc, "balance", 0.0), equity=getattr(acc, "equity", 0.0),
                positions=len(pos), floating=floating, realized_today=realized,
                baskets_today=len(done), wins_today=sum(1 for x in done if x.get("money", 0) > 0),
                streak_buy=self.st["streak"].get("1", 0), streak_sell=self.st["streak"].get("-1", 0),
                cooldown_s=max(0, round(self.st.get("cooldown_until", 0) - _utc_now().timestamp())),
                halt_reason=self.halt_reason,
                window=self.cfg.trading_hours or "all day",
                window_phase=self._window_phase(), window_minutes_left=self._minutes_left(),
                legs=[dict(ticket=p.ticket, side="BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                           volume=p.volume, open=p.price_open, profit=p.profit + p.swap) for p in pos]))
        except Exception as e:
            self.log.debug("publish failed: %s", e)

    # -------------------------------------------------- an open basket
    def _manage(self, pos):
        money, comm = self.b.basket_money(pos)
        side = BUY if pos[0].type == mt5.POSITION_TYPE_BUY else SELL
        if self.st.get("open_side") is None:
            self.log.info("adopted an existing basket: %d positions, side %s, floating %.2f",
                          len(pos), "BUY" if side == BUY else "SELL", money)
            self.st["open_side"] = side
            self.st["opened_at"] = self.st.get("opened_at") or _utc_now().isoformat()
            self.save()

        hit_tp = money >= self.cfg.basket_profit_target_usd
        hit_sl = money <= -self.cfg.stop_loss_usd
        weekend = self._weekend_flat_due()
        shut = self._window_flat_due()
        if not (hit_tp or hit_sl or weekend or shut):
            return
        why = ("TARGET" if hit_tp else "STOP" if hit_sl else "WEEKEND" if weekend else "WINDOW END")
        self._close_all(pos, money, why, side)

    def _close_all(self, pos, money, why, side):
        if self.dry:
            self.log.info("[DRY] would close %d positions (%s) at %.2f", len(pos), why, money)
            return
        self.log.info("closing %d positions (%s) at %.2f", len(pos), why, money)
        left = list(pos)
        for p in pos:
            try:
                self.b.close_position(p)
            except BrokerDown as e:
                self.log.error("close failed for %s: %s - will retry next loop", p.ticket, e)
        left = self.b.positions()
        if left:
            self.log.warning("%d positions still open after close attempt - retrying next loop", len(left))
            return
        won = money > 0
        k = str(side)
        self.st["streak"][k] = 0 if won else self.st["streak"].get(k, 0) + 1
        self.st["cooldown_until"] = _utc_now().timestamp() + self.cfg.cooldown_seconds
        self.st["baskets"].append(dict(closed=_utc_now().isoformat(), side=side, money=round(money, 2),
                                       why=why, n=len(pos)))
        self.st["open_side"] = None
        self.st["opened_at"] = None
        self.save()
        self.log.info("basket closed %s %+.2f  streak[%s]=%d", why, money, k, self.st["streak"][k])
        done = self.st["baskets"]
        realized = 0.0
        try:
            realized = self.b.realized_since(_day_start())[0]
        except Exception:
            pass
        icon = "✅" if won else "❌"
        self.notify(
            f"{icon} <b>BASKET CLOSED</b> — {why}\n\n"
            f"├ {'BUY' if side == BUY else 'SELL'} {len(pos)} x {self.cfg.lot_size} "
            f"<code>{self.b.sym}</code>\n"
            f"├ Result: <b>{'+' if money >= 0 else '-'}${abs(money):,.2f}</b>\n"
            f"├ Today: <b>{'+' if realized >= 0 else '-'}${abs(realized):,.2f}</b> "
            f"over {len(done)} basket(s)\n"
            f"└ Losing streak this way: <code>{self.st['streak'][k]}</code>")

    # -------------------------------------------------- no basket: should we open one?
    def _maybe_open(self):
        if self.st.get("open_side") is not None:        # we thought we had one; MT5 says no
            self.log.info("basket is gone at the broker (closed by SL/TP or by hand) - recording it")
            realized, _ = self.b.realized_since(_day_start())
            self.st["open_side"] = None
            self.st["opened_at"] = None
            self.st["cooldown_until"] = _utc_now().timestamp() + self.cfg.cooldown_seconds
            self.save()
            return

        if self.bus is not None and self.bus.paused:
            self._halt_once("paused from telegram")
            return

        if self._weekend_flat_due():
            self._halt_once("weekend - flat until Monday")
            return

        if not self._may_open():
            phase = self._window_phase()
            hhmm = self.cfg.trading_hours
            if phase == "BEFORE":
                self._halt_once(f"outside the trading window - waiting for {hhmm.split('-')[0]} UTC")
            elif phase == "WINDDOWN":
                self._halt_once(f"winding down - no new baskets in the last "
                                f"{self.cfg.winddown_minutes} min of {hhmm}")
            else:
                self._halt_once(f"trading window {hhmm} UTC has closed for today")
            return

        realized, floating, _ = self.day_money([])
        if self.cfg.daily_profit_target_usd > 0 and realized >= self.cfg.daily_profit_target_usd:
            self._halt_once(f"daily profit target reached ({realized:+.2f})",
                            f"🎯 <b>DAILY TARGET HIT</b>\n\nToday: <b>+${realized:,.2f}</b>\n"
                            f"No more trades until tomorrow (UTC).")
            return
        if self.cfg.daily_loss_limit_usd > 0 and realized <= -self.cfg.daily_loss_limit_usd:
            self._halt_once(f"daily loss limit reached ({realized:+.2f})",
                            f"🛑 <b>DAILY LOSS LIMIT HIT</b>\n\nToday: <b>-${abs(realized):,.2f}</b>\n"
                            f"No more trades until tomorrow (UTC).")
            return

        now = _utc_now().timestamp()
        if now < self.st.get("cooldown_until", 0.0):
            return

        if self.cfg.max_spread_points > 0:
            sp = self.b.spread_points()
            if sp > self.cfg.max_spread_points:
                self.log.debug("spread %.1f pts above limit %.1f - skipping", sp, self.cfg.max_spread_points)
                return

        n_trend, n_entry = ST.warmup_bars(self.cfg)
        tb = self.b.bars(self.cfg.trend_timeframe, n_trend)
        eb = self.b.bars(self.cfg.entry_timeframe, n_entry)
        bar_t = int(eb["time"][-1])
        if bar_t == self._last_bar:
            return                                       # one decision per closed entry bar
        self._last_bar = bar_t

        side, why = ST.entry_signal(self.cfg, tb, eb)
        if side == ST.NONE:
            return
        if self.st["streak"].get(str(side), 0) >= self.cfg.block_direction_after_sl:
            self.log.info("signal %s suppressed: %d losing baskets in a row that way today",
                          "BUY" if side == BUY else "SELL", self.st["streak"][str(side)])
            return
        self._open_basket(side, why)

    def _open_basket(self, side, why):
        n = int(self.cfg.positions_per_entry)
        lot = float(self.cfg.lot_size)
        total = n * lot
        # disaster stop: far wider than the basket's own dollar stop, so it only ever fires if this
        # process dies and never returns.
        sl_price = 0.0
        if self.cfg.hard_sl_points > 0:
            dist = self.cfg.hard_sl_points * self.b.spec.point
        else:
            dist = self._usd_to_price(self.cfg.stop_loss_usd * 1.5, total)
        tick = self.b.tick()
        ref = tick.ask if side == BUY else tick.bid
        if dist > 0:
            sl_price = ref - dist if side == BUY else ref + dist
        tp_price = 0.0
        if self.cfg.reward_multiplier > 0 and dist > 0:
            tp_price = (ref + dist * self.cfg.reward_multiplier if side == BUY
                        else ref - dist * self.cfg.reward_multiplier)

        label = "BUY" if side == BUY else "SELL"
        if self.dry:
            self.log.info("[DRY] would open %d x %.2f %s %s @ ~%.*f  sl=%.*f tp=%.*f  (%s)",
                          n, lot, label, self.b.sym, self.b.spec.digits, ref,
                          self.b.spec.digits, sl_price, self.b.spec.digits, tp_price, why)
            return

        self.st["open_side"] = side
        self.st["opened_at"] = _utc_now().isoformat()
        self.save()                                      # state BEFORE the first send, always

        # Every leg opens at market with the SAME stop and target. That is what the vendor actually does
        # (confirmed with the seller) and it is why the basket reduces to one trade of the combined size.
        opened = 0
        for i in range(n):
            try:
                self.b.open_position(side, lot, sl_price, tp_price, comment=f"SNIPER-{label}-{i+1}")
                opened += 1
            except BrokerDown as e:
                self.log.error("leg %d/%d failed: %s", i + 1, n, e)
                break
        if opened == 0:
            self.st["open_side"] = None
            self.st["opened_at"] = None
            self.save()
            self.log.error("no legs opened - basket abandoned")
            return
        if opened < n:
            self.log.warning("opened %d of %d legs; managing what we have", opened, n)
        self.log.info("basket OPEN %s %d x %.2f %s (%s)", label, opened, lot, self.b.sym, why)
        self.notify(
            f"{'🟢' if side == BUY else '🔴'} <b>BASKET OPEN</b> — {label}\n\n"
            f"├ <code>{self.b.sym}</code> {opened} x {lot} lots @ <code>{ref:.3f}</code>\n"
            f"├ Target: <b>+${self.cfg.basket_profit_target_usd:,.2f}</b>\n"
            f"├ Stop: <b>-${self.cfg.stop_loss_usd:,.2f}</b>\n"
            f"└ <i>{why}</i>"
            + ("" if opened == n else f"\n\n⚠️ only {opened} of {n} legs filled"))

    def _halt_once(self, reason, alert=None):
        self.halt_reason = reason
        if self._warned_halt != reason:
            self.log.info("standing down: %s", reason)
            self._warned_halt = reason
            if alert:
                self.notify(alert)

    # ------------------------------------------------------------------ status for the console
    def status(self):
        pos = self.b.positions()
        realized, floating, nd = self.day_money(pos)
        return dict(symbol=self.b.sym, positions=len(pos),
                    floating=round(floating, 2), realized_today=round(realized, 2),
                    deals_today=nd, streak=self.st.get("streak"),
                    cooldown_s=max(0, round(self.st.get("cooldown_until", 0) - _utc_now().timestamp())),
                    window=self.cfg.trading_hours or "all day",
                    window_phase=self._window_phase(), window_minutes_left=self._minutes_left(),
                    state=IN_BASKET if pos else IDLE)
