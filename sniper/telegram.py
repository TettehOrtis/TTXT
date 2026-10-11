"""Telegram link for the Sniper bot: live alerts and remote commands.

Two hard rules shape this file.

1. **Telegram must never be able to hurt trading.** Every network call lives on its own thread, outgoing
   messages go through a queue so sending can never block the trading loop, and every failure is caught
   and logged. If Telegram is down, unreachable, or misconfigured, the bot trades on exactly as before.

2. **The MT5 library is not thread-safe.** So command handlers NEVER touch MT5. The trading loop
   publishes a plain-dict snapshot of its state once per pass; the Telegram thread only reads that.
   Commands that need to DO something (pause, close, stop) drop a request on a queue which the trading
   loop picks up on its next pass and executes on its own thread.

Only the configured chat id is obeyed. Anything from anyone else is logged and ignored.
"""
import html
import json
import queue
import threading
import time
import datetime as dt

import requests

API = "https://api.telegram.org/bot{token}/{method}"


class Bus:
    """The only thing shared between the trading loop and the Telegram threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snap = {}
        self.requests = queue.Queue()
        self.paused = False

    def publish(self, snap):
        with self._lock:
            self._snap = dict(snap)
            self._snap["updated"] = time.time()

    def snapshot(self):
        with self._lock:
            return dict(self._snap)

    def ask(self, what, **kw):
        self.requests.put(dict(what=what, **kw))

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.requests.get_nowait())
            except queue.Empty:
                return out


def _money(v):
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def _esc(s):
    return html.escape(str(s))


class Telegram:
    def __init__(self, token, chat_id, log, bus, enabled=True):
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.log = log
        self.bus = bus
        self.enabled = bool(enabled and self.token and self.chat_id)
        self.cmds = {}
        self.desc = {}
        self._out = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._threads = []
        self._offset = None
        self._confirm = {}
        self._fails = 0

    # ------------------------------------------------------------------ plumbing
    def _api(self, method, **payload):
        r = requests.post(API.format(token=self.token, method=method), json=payload, timeout=35)
        r.raise_for_status()
        d = r.json()
        if not d.get("ok"):
            raise RuntimeError(d.get("description", "telegram said not ok"))
        return d.get("result")

    def send(self, text):
        """Queue a message. Never blocks, never raises, never reaches the network on this thread."""
        if not self.enabled:
            return
        try:
            self._out.put_nowait(text)
        except queue.Full:
            self.log.warning("telegram outbox full - dropping a message")

    def _sender(self):
        while not self._stop.is_set():
            try:
                text = self._out.get(timeout=1.0)
            except queue.Empty:
                continue
            for attempt in range(3):
                try:
                    self._api("sendMessage", chat_id=self.chat_id, text=text[:4000],
                              parse_mode="HTML", disable_web_page_preview=True)
                    self._fails = 0
                    break
                except Exception as e:
                    if attempt == 2:
                        self._fails += 1
                        if self._fails in (1, 10, 100):
                            self.log.warning("telegram send failed (%d in a row): %s", self._fails, e)
                    else:
                        time.sleep(1.5 * (attempt + 1))

    def _poller(self):
        while not self._stop.is_set():
            try:
                ups = self._api("getUpdates", offset=self._offset, timeout=25,
                                allowed_updates=["message"]) or []
                for u in ups:
                    self._offset = u["update_id"] + 1
                    self._handle(u.get("message") or {})
            except requests.exceptions.ReadTimeout:
                pass
            except Exception as e:
                msg = str(e)
                if "Conflict" in msg:
                    self.log.error("telegram: another program is using this bot token. "
                                   "Make a NEW bot with @BotFather for the sniper. Commands disabled.")
                    return
                self.log.debug("telegram poll: %s", e)
                self._stop.wait(5.0)

    def _handle(self, m):
        chat = str((m.get("chat") or {}).get("id", ""))
        text = (m.get("text") or "").strip()
        if not text.startswith("/"):
            return
        if chat != self.chat_id:
            self.log.warning("telegram: ignoring command from chat %s", chat)
            return
        name = text[1:].split()[0].split("@")[0].lower()
        fn = self.cmds.get(name)
        if fn is None:
            self.send(f"Unknown command <code>/{_esc(name)}</code>. Try /help")
            return
        try:
            self.send(fn())
        except Exception as e:
            self.log.exception("telegram command /%s failed", name)
            self.send(f"<b>/{_esc(name)} failed:</b> <code>{_esc(e)}</code>")

    def register(self, name, fn, desc="", *aliases):
        self.cmds[name] = fn
        self.desc[name] = desc
        for a in aliases:
            self.cmds[a] = fn

    def start(self):
        if not self.enabled:
            self.log.info("telegram: not configured - running without it")
            return self
        self._threads = [threading.Thread(target=self._sender, name="tg-send", daemon=True),
                         threading.Thread(target=self._poller, name="tg-poll", daemon=True)]
        for t in self._threads:
            t.start()
        self.log.info("telegram: connected, commands live")
        return self

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)

    # ------------------------------------------------------------------ confirmations
    def needs_confirm(self, key, prompt, seconds=60):
        """Two-step confirmation. First call arms it and returns the prompt; second within the window
        returns None, meaning 'go ahead'."""
        now = time.time()
        until = self._confirm.get(key, 0)
        if now >= until:
            self._confirm[key] = now + seconds
            return prompt
        self._confirm.pop(key, None)
        return None


# ---------------------------------------------------------------------- the commands
def register_commands(tg, bus, cfg):
    """All handlers read the snapshot the trading loop publishes. None of them touch MT5."""

    def stamp():
        return dt.datetime.now(dt.timezone.utc).strftime("%H:%M")

    def stale(s):
        age = time.time() - s.get("updated", 0)
        return f"\n\n⚠️ <i>data is {age:.0f}s old - the bot may be stopped</i>" if age > 60 else ""

    def _window_line(s):
        """A one-block header for the trading window. Empty when the bot runs all day, so the all-day
        /status looks exactly as it did before."""
        phase = s.get("window_phase")
        if not phase or phase == "ALL_DAY":
            return ""
        left = s.get("window_minutes_left")
        hhmm = _esc(s.get("window", ""))
        if phase == "BEFORE":
            body = f"waiting for {_esc(hhmm.split('-')[0])} UTC"
        elif phase == "OPEN":
            body = f"<b>open</b> — {left} min left"
        elif phase == "WINDDOWN":
            body = f"<b>winding down</b> — {left} min left, no new baskets"
        else:
            body = "<b>closed</b> for today"
        icon = {"BEFORE": "🕑", "OPEN": "🟢", "WINDDOWN": "🟠", "AFTER": "🔴"}.get(phase, "🕑")
        return f"{icon} <b>WINDOW</b> <code>{hhmm}</code> UTC — {body}\n\n"

    def cmd_status():
        s = bus.snapshot()
        if not s:
            return "🤖 <b>STATUS</b>\n\nNo data yet - the bot has not finished its first pass."
        state = "IN A BASKET" if s.get("positions") else ("PAUSED" if bus.paused else "waiting")
        return (
            _window_line(s) +
            f"🤖 <b>STATUS</b>  <code>{_esc(s.get('symbol'))}</code>\n\n"
            f"<b>Account</b>\n"
            f"├ Balance: <code>${s.get('balance', 0):,.2f}</code>\n"
            f"├ Equity: <code>${s.get('equity', 0):,.2f}</code>\n"
            f"└ Mode: <code>{_esc(s.get('mode'))}</code> / <code>{'LIVE' if s.get('live') else 'DRY RUN'}</code>\n\n"
            f"<b>Right now</b>\n"
            f"├ State: <b>{state}</b>\n"
            f"├ Open legs: <code>{s.get('positions', 0)}</code>\n"
            f"├ Floating: <b>{_money(s.get('floating', 0))}</b>\n"
            f"└ Cooldown: <code>{s.get('cooldown_s', 0)}s</code>\n\n"
            f"<b>Today</b>\n"
            f"├ Realised: <b>{_money(s.get('realized_today', 0))}</b>\n"
            f"├ Baskets: <code>{s.get('baskets_today', 0)}</code> "
            f"(<code>{s.get('wins_today', 0)}</code> won)\n"
            f"└ Losing streak: buy <code>{s.get('streak_buy', 0)}</code> / "
            f"sell <code>{s.get('streak_sell', 0)}</code>\n\n"
            f"⏰ {stamp()} UTC{stale(s)}"
        )

    def cmd_positions():
        s = bus.snapshot()
        legs = s.get("legs") or []
        if not legs:
            return f"📭 <b>POSITIONS</b>\n\nNothing open.\n\n⏰ {stamp()} UTC{stale(s)}"
        rows = [f"#{l['ticket']} {l['side']} <code>{l['volume']}</code> @ <code>{l['open']:.3f}</code> "
                f"→ <b>{_money(l['profit'])}</b>" for l in legs]
        return (f"📈 <b>POSITIONS</b> ({len(legs)})\n\n" + "\n".join(rows) +
                f"\n\n<b>Combined: {_money(s.get('floating', 0))}</b>\n"
                f"Target <code>+${cfg.basket_profit_target_usd:.2f}</code> / "
                f"stop <code>-${cfg.stop_loss_usd:.2f}</code>\n\n⏰ {stamp()} UTC{stale(s)}")

    def cmd_daily():
        s = bus.snapshot()
        n, w = s.get("baskets_today", 0), s.get("wins_today", 0)
        wr = (100.0 * w / n) if n else 0.0
        return (
            f"📋 <b>TODAY</b> {dt.datetime.now(dt.timezone.utc):%Y-%m-%d} UTC\n\n"
            f"├ Realised: <b>{_money(s.get('realized_today', 0))}</b>\n"
            f"├ Baskets: <code>{n}</code> | won <code>{w}</code> | win rate <code>{wr:.0f}%</code>\n"
            f"├ Daily target: <code>${cfg.daily_profit_target_usd:.2f}</code>\n"
            f"└ Daily loss limit: <code>${cfg.daily_loss_limit_usd:.2f}</code>\n\n"
            f"{_esc(s.get('halt_reason') or 'Trading normally.')}\n\n⏰ {stamp()} UTC{stale(s)}"
        )

    def cmd_settings():
        return (
            f"⚙️ <b>SETTINGS</b>\n\n"
            f"├ Symbol: <code>{_esc(cfg.symbol)}</code> on <code>{_esc(cfg.account)}</code>\n"
            f"├ Basket: <code>{cfg.positions_per_entry} x {cfg.lot_size}</code> lots\n"
            f"├ Target: <code>+${cfg.basket_profit_target_usd:.2f}</code>\n"
            f"├ Stop: <code>-${cfg.stop_loss_usd:.2f}</code>\n"
            f"├ Cooldown: <code>{cfg.cooldown_seconds}s</code>\n"
            f"├ Charts: <code>{_esc(cfg.trend_timeframe)}</code> trend / "
            f"<code>{_esc(cfg.entry_timeframe)}</code> entry\n"
            f"├ Daily target: <code>${cfg.daily_profit_target_usd:.2f}</code>\n"
            f"├ Daily loss limit: <code>${cfg.daily_loss_limit_usd:.2f}</code>\n"
            f"├ Block direction after: <code>{cfg.block_direction_after_sl}</code> losses\n"
            f"├ Trading window: <code>{_esc(cfg.trading_hours or 'all day')}</code> UTC\n"
            f"├ Wind-down: <code>{cfg.winddown_minutes} min</code> before the end\n"
            f"└ Close everything at the end: "
            f"<code>{'yes' if cfg.flat_at_window_end else 'NO'}</code>\n\n"
            f"<i>Change these in .env, then restart the bot.</i>"
        )

    def cmd_pause():
        bus.ask("pause")
        return ("⏸ <b>PAUSED</b>\n\nNo new baskets will be opened.\n"
                "An open basket is still managed to its target or stop.\n\nResume with /resume")

    def cmd_resume():
        bus.ask("resume")
        return "▶️ <b>RESUMED</b>\n\nLooking for entries again."

    def cmd_close():
        s = bus.snapshot()
        if not s.get("positions"):
            return "Nothing to close."
        p = tg.needs_confirm(
            "close",
            f"⚠️ <b>CLOSE THE BASKET NOW?</b>\n\n"
            f"{s.get('positions')} legs, currently <b>{_money(s.get('floating', 0))}</b>.\n"
            f"Send /close again within 60 seconds to confirm.")
        if p:
            return p
        bus.ask("close")
        return "🛑 <b>CLOSING</b> - the bot will close every leg on its next pass."

    def cmd_flat():
        """Close whatever is open AND stop opening more - the 'I need to shut the laptop now' button.

        Deliberately one command rather than /close then /pause: in a hurry, the gap between the two is
        exactly long enough for the bot to open a fresh basket.
        """
        s = bus.snapshot()
        n = s.get("positions", 0)
        p = tg.needs_confirm(
            "flat",
            f"⚠️ <b>GO FLAT AND STAND DOWN?</b>\n\n"
            + (f"{n} legs, currently <b>{_money(s.get('floating', 0))}</b>, will be closed at market.\n"
               if n else "Nothing is open.\n")
            + f"The bot will then open nothing more until you send /resume.\n"
              f"Send /flat again within 60 seconds to confirm.")
        if p:
            return p
        bus.ask("flat")
        return ("🏁 <b>GOING FLAT</b>\n\n"
                + (f"Closing {n} leg(s) on the next pass.\n" if n else "Nothing was open.\n")
                + "No new baskets until /resume. Safe to shut the laptop once /status shows nothing open.")

    def cmd_stop():
        p = tg.needs_confirm(
            "stop",
            "⚠️ <b>STOP THE BOT?</b>\n\nOpen trades are LEFT OPEN at the broker with their stops.\n"
            "Send /stop again within 60 seconds to confirm.")
        if p:
            return p
        bus.ask("stop")
        return "🔌 <b>STOPPING</b> - shutting down cleanly. Open trades are left alone."

    def cmd_help():
        return (
            "🤖 <b>SNIPER BOT</b>\n\n"
            "<b>Look</b>\n"
            "├ /status — everything at a glance\n"
            "├ /positions — open legs (/pos)\n"
            "├ /daily — today's money\n"
            "└ /settings — current configuration\n\n"
            "<b>Control</b>\n"
            "├ /pause — stop opening new baskets\n"
            "├ /resume — start again\n"
            "├ /close — close the open basket now (confirms)\n"
            "└ /stop — shut the bot down (confirms)\n\n"
            "<i>Settings live in .env and need a restart.</i>"
        )

    tg.register("status", cmd_status, "everything at a glance", "s")
    tg.register("positions", cmd_positions, "open legs", "pos", "p")
    tg.register("daily", cmd_daily, "today's money", "d")
    tg.register("settings", cmd_settings, "current configuration", "config")
    tg.register("pause", cmd_pause, "stop opening new baskets")
    tg.register("resume", cmd_resume, "start again")
    tg.register("close", cmd_close, "close the open basket now")
    tg.register("flat", cmd_flat, "close everything and stand down")
    tg.register("stop", cmd_stop, "shut the bot down")
    tg.register("help", cmd_help, "this list", "start", "h")
    return tg
