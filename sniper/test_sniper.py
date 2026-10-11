"""Tests for the Sniper Spark clone. No MT5, no network - a fake broker stands in.

    .venv/Scripts/python.exe sniper/test_sniper.py

Covers the things that lose money when they are wrong: the basket closing at the right dollar amount,
the daily limits, the cooldown, the direction block, crash recovery onto a live basket, and the rule
that a dry run never sends anything.
"""
import os
import sys
import json
import types
import logging
import tempfile
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                                     # noqa: E402
from sniper.config import Config                                       # noqa: E402
from sniper.broker import BrokerDown, BUY, SELL                        # noqa: E402
from sniper import engine as EN                                        # noqa: E402
from sniper import strategy as ST                                      # noqa: E402

LOG = logging.getLogger("test")
LOG.addHandler(logging.NullHandler())
POSITION_TYPE_BUY = 0
PASS = FAIL = 0
_real_now = EN._utc_now          # window tests freeze the clock; this puts it back afterwards


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


class FakePos:
    def __init__(self, ticket, typ, vol, profit=0.0, swap=0.0):
        self.ticket, self.type, self.volume = ticket, typ, vol
        self.profit, self.swap, self.magic = profit, swap, 990077


class FakeSpec:
    point = 0.001
    digits = 3
    volume_min, volume_step, volume_max = 0.01, 0.01, 200.0
    trade_tick_value, trade_tick_size = 0.1, 0.001
    filling_mode = 3


class FakeBroker:
    """Same surface as sniper.broker.Broker, with switches the tests can flip."""

    def __init__(self, cfg):
        self.cfg, self.sym, self.spec = cfg, "XAUUSDz", FakeSpec()
        self._pos, self.opened, self.closed = [], [], []
        self.floating, self.commission, self.realized = 0.0, 0.0, 0.0
        self.down = False
        self.bars_m1, self.bars_tf = None, None

    def _maybe_down(self):
        if self.down:
            raise BrokerDown("fake outage")

    def ensure(self):
        self._maybe_down()

    def positions(self):
        self._maybe_down()
        return list(self._pos)

    def basket_money(self, pos):
        return (self.floating, self.commission) if pos else (0.0, 0.0)

    def realized_since(self, t):
        self._maybe_down()
        return self.realized, len(self.closed)

    def tick(self):
        return types.SimpleNamespace(bid=4000.0, ask=4000.1)

    def spread_points(self):
        return 100.0

    def bars(self, tf, n):
        b = self.bars_tf if tf == self.cfg.trend_timeframe else self.bars_m1
        if b is None or len(b) < n:
            raise BrokerDown("no bars")
        return b[-n:]

    def open_position(self, side, lot, sl=0.0, tp=0.0, comment=""):
        self._maybe_down()
        self.opened.append(dict(side=side, lot=lot, sl=sl, tp=tp, comment=comment))
        self._pos.append(FakePos(100 + len(self._pos), POSITION_TYPE_BUY if side == BUY else 1, lot))
        return types.SimpleNamespace(retcode=10009)

    def close_position(self, p):
        self._maybe_down()
        self.closed.append(p.ticket)
        self._pos = [x for x in self._pos if x.ticket != p.ticket]
        return types.SimpleNamespace(retcode=10009)


def mkbars(n, base=4000.0, up=True):
    t = np.arange(n, dtype=np.int64) * 60 + 1_700_000_000
    drift = np.linspace(0, 20 if up else -20, n)
    c = base + drift + np.sin(np.arange(n) / 7.0) * 1.5
    o = c - 0.3
    return np.rec.fromarrays([t, o, c + 0.6, c - 0.6, c], names="time,open,high,low,close")


_N = [0]


def engine_with(tmp, **over):
    """A fresh engine with its OWN state file - state must never leak between cases."""
    _N[0] += 1
    cfg = Config(state_file=os.path.join(tmp, f"state_{_N[0]}.json"), log_dir=tmp, **over)
    b = FakeBroker(cfg)
    return cfg, b, EN.Engine(cfg, b, LOG, dry=False)


def main():
    tmp = tempfile.mkdtemp(prefix="sniper_t")
    print("config")
    c = Config()
    check("defaults match the vendor's $300 sheet",
          (c.lot_size, c.positions_per_entry, c.basket_profit_target_usd, c.stop_loss_usd,
           c.cooldown_seconds, c.block_direction_after_sl) == (0.02, 5, 7.0, 70.0, 60, 2))
    for bad in (dict(lot_size=0), dict(positions_per_entry=0), dict(stop_loss_usd=-1),
                dict(run_mode="MODE_WHATEVER"), dict(trend_timeframe="M7"), dict(account="NOPE")):
        try:
            Config(**bad)
            check(f"rejects {bad}", False)
        except ValueError:
            check(f"rejects {bad}", True)
    check("override beats .env", Config.load(symbol="BTCUSD").symbol == "BTCUSD")
    # These check WHICH warning fired, not how many. Counting every warning made the whole block fail
    # the moment an unrelated one was added.
    def warned(cfg, needle):
        return any(needle in w for w in cfg.warnings)

    half = Config(telegram_token="AAGNXkuJ6z0GOuA9uhr30iQu0r1shBsEBgE")
    check("half-pasted telegram token disables telegram, does NOT stop the bot",
          half.telegram_token == "" and warned(half, "TELEGRAM_TOKEN"))
    check("accepts a full telegram token",
          Config(telegram_token="8796893471:AAHdTixYgGgSNCv2hOOsL_FmU37SD2cViOM").telegram_token.startswith("879"))
    check("blank telegram token is fine and warns about nothing",
          Config(telegram_token="").telegram_token == ""
          and not warned(Config(telegram_token=""), "TELEGRAM"))

    print("strategy")
    cfg = Config()
    up, dn = mkbars(800, up=True), mkbars(800, up=False)
    check("uptrend detected", ST.trend_of(cfg, up) == BUY)
    check("downtrend detected", ST.trend_of(cfg, dn) == SELL)
    r = ST.rsi(up["close"], 14)
    check("rsi in range", float(np.nanmin(r[20:])) >= 0 and float(np.nanmax(r[20:])) <= 100)
    check("ema tracks price", abs(ST.ema(up["close"], 20)[-1] - up["close"][-1]) < 5)

    # ---- the body-hold switch, and the RSI clash it collides with -----------------------------
    def one_bar(bars, close, low, high=None, open_=None):
        """Copy bars with the LAST bar forced to a given shape."""
        b2 = bars.copy()
        b2["close"][-1] = close
        b2["low"][-1] = low
        b2["high"][-1] = high if high is not None else max(close, bars["high"][-1])
        b2["open"][-1] = open_ if open_ is not None else close - 0.3
        return b2

    base = Config(entry_rsi_buy_below=100.0, entry_rsi_sell_above=0.0)   # RSI gate off, isolate the body
    hold = Config(entry_rsi_buy_below=100.0, entry_rsi_sell_above=0.0, entry_require_body_hold=True)
    e20 = ST.ema(up["close"], base.entry_ema)[-1]
    wick_held = one_bar(up, close=e20 + 1.0, low=e20 - 1.0)        # wick pierces, body closes above
    body_broke = one_bar(up, close=e20 - 0.5, low=e20 - 1.0, open_=e20 - 0.8)   # body closes through
    check("body-hold OFF takes a bar whose body closed THROUGH the EMA",
          ST.entry_signal(base, up, body_broke)[0] == BUY)
    check("body-hold ON rejects that same bar",
          ST.entry_signal(hold, up, body_broke)[0] == ST.NONE)
    check("body-hold ON takes the wick-pierced, body-held bar",
          ST.entry_signal(hold, up, wick_held)[0] == BUY)
    check("body-hold ON still needs the wick to reach the EMA",
          ST.entry_signal(hold, up, one_bar(up, close=e20 + 2.0, low=e20 + 1.0))[0] == ST.NONE)
    check("the two settings select DIFFERENT bars, not a subset",
          ST.entry_signal(base, up, body_broke)[0] == BUY
          and ST.entry_signal(hold, up, body_broke)[0] == ST.NONE
          and ST.entry_signal(hold, up, wick_held)[0] == BUY)
    check("default is unchanged - body-hold is OFF as shipped",
          Config().entry_require_body_hold is False
          and ST.entry_signal(Config(entry_rsi_buy_below=100.0), up, body_broke)[0] == BUY)
    clash = Config(entry_require_body_hold=True)                   # default RSI 45/55
    check("config WARNS about the body-hold / RSI clash instead of silently never trading",
          warned(clash, "almost no BUY") and warned(clash, "almost no SELL"))
    check("no clash warning once the RSI gate is opened",
          not warned(Config(entry_require_body_hold=True, entry_rsi_buy_below=100.0,
                            entry_rsi_sell_above=0.0), "almost no"))

    print("basket: target and stop")
    cfg, b, e = engine_with(tmp)
    b.bars_m1, b.bars_tf = mkbars(600), mkbars(900)
    b._pos = [FakePos(1, POSITION_TYPE_BUY, 0.02) for _ in range(5)]
    b.floating = 7.5
    e.st["open_side"] = BUY
    e.step()
    check("closes at +$7 target", len(b._pos) == 0 and len(b.closed) == 5)

    cfg, b, e = engine_with(tmp)
    b._pos = [FakePos(i, POSITION_TYPE_BUY, 0.02) for i in range(5)]
    b.floating = -70.5
    e.st["open_side"] = BUY
    e.step()
    check("closes at -$70 stop", len(b._pos) == 0)

    cfg, b, e = engine_with(tmp)
    b._pos = [FakePos(i, POSITION_TYPE_BUY, 0.02) for i in range(5)]
    b.floating = 3.0
    e.st["open_side"] = BUY
    e.step()
    check("holds between target and stop", len(b._pos) == 5)

    print("streak, cooldown, direction block")
    cfg, b, e = engine_with(tmp)
    b._pos = [FakePos(i, POSITION_TYPE_BUY, 0.02) for i in range(5)]
    b.floating = -70.5
    e.st["open_side"] = BUY
    e.step()
    check("loss increments the streak", e.st["streak"]["1"] == 1)
    check("cooldown armed", e.st["cooldown_until"] > dt.datetime.now(dt.timezone.utc).timestamp())
    b._pos = [FakePos(i, POSITION_TYPE_BUY, 0.02) for i in range(5)]
    b.floating = 8.0
    e.st["open_side"] = BUY
    e.step()
    check("win resets the streak", e.st["streak"]["1"] == 0)

    cfg, b, e = engine_with(tmp)
    b.bars_m1, b.bars_tf = mkbars(600), mkbars(900)
    e.st["streak"]["1"] = 2
    e.st["cooldown_until"] = 0
    e._last_bar = None
    import sniper.engine as _en
    orig = ST.entry_signal
    _en.ST.entry_signal = lambda *a: (BUY, "forced")
    try:
        e.step()
        check("direction blocked after 2 losses", len(b.opened) == 0)
        e.st["streak"]["1"] = 0
        e._last_bar = None
        e.step()
        check("unblocked direction opens a basket", len(b.opened) == 5)
        check("all legs same side and lot",
              all(o["side"] == BUY and o["lot"] == 0.02 for o in b.opened))
        check("disaster stop attached below entry", all(0 < o["sl"] < 4000.0 for o in b.opened))

        print("cooldown gate")
        cfg, b, e = engine_with(tmp)
        b.bars_m1, b.bars_tf = mkbars(600), mkbars(900)
        e.st["cooldown_until"] = dt.datetime.now(dt.timezone.utc).timestamp() + 999
        e._last_bar = None
        e.step()
        check("no entry during cooldown", len(b.opened) == 0)

        print("daily limits")
        cfg, b, e = engine_with(tmp, daily_loss_limit_usd=50.0)
        b.bars_m1, b.bars_tf = mkbars(600), mkbars(900)
        b.realized = -51.0
        e._last_bar = None
        e.step()
        check("daily loss limit stops new baskets", len(b.opened) == 0)

        cfg, b, e = engine_with(tmp, daily_profit_target_usd=200.0)
        b.bars_m1, b.bars_tf = mkbars(600), mkbars(900)
        b.realized = 201.0
        e._last_bar = None
        e.step()
        check("daily profit target stops new baskets", len(b.opened) == 0)

        print("one decision per closed bar")
        cfg, b, e = engine_with(tmp)
        b.bars_m1, b.bars_tf = mkbars(600), mkbars(900)
        e._last_bar = None
        e.step()
        n1 = len(b.opened)
        b._pos = []
        e.st["open_side"] = None
        e.st["cooldown_until"] = 0
        e.step()
        check("same bar does not fire twice", len(b.opened) == n1)

        print("dry run sends nothing")
        cfg2 = Config(state_file=os.path.join(tmp, "s2.json"), log_dir=tmp)
        b2 = FakeBroker(cfg2)
        b2.bars_m1, b2.bars_tf = mkbars(600), mkbars(900)
        e2 = EN.Engine(cfg2, b2, LOG, dry=True)
        e2._last_bar = None
        e2.step()
        check("dry run opened nothing", len(b2.opened) == 0)
        b2._pos = [FakePos(i, POSITION_TYPE_BUY, 0.02) for i in range(5)]
        b2.floating = 9.0
        e2.st["open_side"] = BUY
        e2.step()
        check("dry run closed nothing", len(b2._pos) == 5)
    finally:
        _en.ST.entry_signal = orig

    print("crash recovery")
    cfg, b, e = engine_with(tmp)
    b._pos = [FakePos(i, POSITION_TYPE_BUY, 0.02) for i in range(5)]
    b.floating = 2.0
    e.st["open_side"] = None                       # state lost; broker still holds the basket
    e.step()
    check("adopts an orphan basket", e.st["open_side"] == BUY and len(b._pos) == 5)
    b.floating = 7.2
    e.step()
    check("manages the adopted basket to target", len(b._pos) == 0)

    sp = e.state_path
    check("state file written", os.path.exists(sp))
    with open(sp) as f:
        check("state is valid json", isinstance(json.load(f), dict))

    print("broker outage")
    cfg, b, e = engine_with(tmp)
    b.down = True
    try:
        e.step()
        check("outage raises BrokerDown", False)
    except BrokerDown:
        check("outage raises BrokerDown", True)
    b.down = False
    b.bars_m1, b.bars_tf = mkbars(600), mkbars(900)
    e._last_bar = None
    e.step()
    check("recovers after the outage", True)

    cfg, b, e = engine_with(tmp)
    b._pos = [FakePos(i, POSITION_TYPE_BUY, 0.02) for i in range(5)]
    b.floating = -71.0
    e.st["open_side"] = BUY
    b.down = True
    try:
        e.step()
    except BrokerDown:
        pass
    check("positions survive an outage mid-close", len(b._pos) == 5)
    b.down = False
    e.step()
    check("closes once the broker is back", len(b._pos) == 0)

    print("history sync on restart")
    cfgH, bH, eH = engine_with(tmp)
    bH.bars_m1, bH.bars_tf = mkbars(600), mkbars(900)
    # the bot restarts knowing it closed a 5-leg basket today, but MT5 has not handed history over yet
    eH.st["baskets"] = [dict(closed="x", side=1, money=-71.1, why="STOP", n=5)]
    bH.realized = 0.0
    bH.closed = []
    eH._history_ok, eH._history_since = False, None
    try:
        eH.step()
        check("refuses to trade before history arrives", False, "it stepped anyway")
    except BrokerDown:
        check("refuses to trade before history arrives", True)
    check("opened nothing while waiting", len(bH.opened) == 0)

    bH.closed = [1, 2, 3, 4, 5]                      # history catches up
    bH.realized = -71.1
    eH.step()
    check("trades once history agrees", eH._history_ok is True)

    cfgH2, bH2, eH2 = engine_with(tmp)
    bH2.bars_m1, bH2.bars_tf = mkbars(600), mkbars(900)
    eH2.st["baskets"] = []                          # genuinely a fresh day
    eH2._history_ok, eH2._history_since = False, None
    eH2.step()
    check("a real fresh day does not wait", eH2._history_ok is True)

    print("commission maths")
    from sniper.broker import Broker as RB2
    import sniper.broker as BR
    rb2 = RB2.__new__(RB2)
    rb2.cfg = Config(include_commission=True)
    rb2._comm = {}
    fake_deals = {1: [types.SimpleNamespace(commission=-0.22)],
                  2: [types.SimpleNamespace(commission=-0.22)]}
    real_get = BR.mt5.history_deals_get
    BR.mt5.history_deals_get = lambda position=None: fake_deals.get(position)
    try:
        ps = [FakePos(1, POSITION_TYPE_BUY, 0.02, profit=4.0),
              FakePos(2, POSITION_TYPE_BUY, 0.02, profit=4.0)]
        money, comm = RB2.basket_money(rb2, ps)
        # Exness charges the WHOLE round trip on entry, so -0.44 is the full cost, not half of it.
        # Doubling it (the old bug) silently turned a $7 target into $7.88 here, $9.20 on a 5-leg basket.
        check("commission counted once, not doubled", abs(comm - (-0.44)) < 1e-9, f"got {comm}")
        check("basket money = profit minus commission", abs(money - (8.0 - 0.44)) < 1e-9, f"got {money}")
        check("commission is cached per ticket", rb2._comm == {1: -0.22, 2: -0.22})
        rb2.cfg = Config(include_commission=False)
        m2, c2 = RB2.basket_money(rb2, ps)
        check("commission can be switched off", abs(m2 - 8.0) < 1e-9 and c2 == 0.0)
    finally:
        BR.mt5.history_deals_get = real_get

    print("mt5 call wrapper")
    from sniper.broker import Broker as RealBroker
    seen = {}

    def spy(*a, **kw):
        seen["args"], seen["kwargs"], seen["kw_was_passed"] = a, kw, "kwargs" in seen
        return "ok"

    rb = RealBroker.__new__(RealBroker)
    rb._ok = True
    probe = {"action": 1}
    # The bug this guards: fn(*a, **kw) with an EMPTY kw makes CPython hand the MetaTrader5 C extension
    # an empty kwargs dict, and its trade functions reject that with "-2 Unnamed arguments not allowed".
    # order_send(req) works, order_send(req, **{}) returns None. Only trade calls are affected, so the
    # bot fetched prices happily and silently failed to place every single order.
    calls = []

    def recorder(*a, **kw):
        calls.append((a, dict(kw)))
        return "ok"

    RealBroker._call(rb, recorder, probe)
    check("no-kwargs call passes ONLY positional args", calls[-1] == ((probe,), {}))
    RealBroker._call(rb, recorder, probe, extra=1)
    check("real kwargs are still forwarded", calls[-1] == ((probe,), {"extra": 1}))

    import inspect
    src = inspect.getsource(RealBroker._call)
    check("_call guards against empty kwargs", "if kw else fn(*a)" in src)

    print("telegram")
    from sniper.telegram import Telegram, Bus, register_commands
    bus = Bus()
    cfgT = Config(telegram_token="T", telegram_chat_id="111")
    tg = Telegram(cfgT.telegram_token, cfgT.telegram_chat_id, LOG, bus)
    register_commands(tg, bus, cfgT)
    sent = []
    tg.send = sent.append                               # capture instead of hitting the network

    tg._handle({"chat": {"id": "999"}, "text": "/status"})
    check("ignores commands from another chat", len(sent) == 0)
    tg._handle({"chat": {"id": "111"}, "text": "/status"})
    check("answers the configured chat", len(sent) == 1 and "STATUS" in sent[0])
    tg._handle({"chat": {"id": "111"}, "text": "hello there"})
    check("ignores plain chat messages", len(sent) == 1)
    tg._handle({"chat": {"id": "111"}, "text": "/nonsense"})
    check("unknown command answers politely", "Unknown command" in sent[-1])

    bus.publish(dict(symbol="XAUUSDz", balance=444.4, equity=450.0, positions=5, floating=3.2,
                     realized_today=-12.0, baskets_today=3, wins_today=2, streak_buy=1, streak_sell=0,
                     cooldown_s=0, live=True, mode="MODE_DEMO",
                     legs=[dict(ticket=1, side="BUY", volume=0.02, open=4000.0, profit=0.64)]))
    sent.clear()
    tg._handle({"chat": {"id": "111"}, "text": "/status"})
    check("status shows real numbers", "444.40" in sent[-1] and "XAUUSDz" in sent[-1])
    tg._handle({"chat": {"id": "111"}, "text": "/positions"})
    check("positions lists the legs", "#1" in sent[-1] and "BUY" in sent[-1])
    tg._handle({"chat": {"id": "111"}, "text": "/daily"})
    check("daily reports the loss", "12.00" in sent[-1])
    tg._handle({"chat": {"id": "111"}, "text": "/help"})
    check("help lists commands", "/pause" in sent[-1] and "/close" in sent[-1])

    tg._handle({"chat": {"id": "111"}, "text": "/pause"})
    check("pause is queued", any(r["what"] == "pause" for r in bus.drain()))
    sent.clear()
    tg._handle({"chat": {"id": "111"}, "text": "/close"})
    check("close asks for confirmation first", "again within 60" in sent[-1])
    check("close did NOT act on the first call", bus.requests.empty())
    tg._handle({"chat": {"id": "111"}, "text": "/close"})
    check("close acts on the second call", any(r["what"] == "close" for r in bus.drain()))
    sent.clear()
    tg._handle({"chat": {"id": "111"}, "text": "/stop"})
    check("stop asks for confirmation first", "again within 60" in sent[-1])
    check("stop did NOT act on the first call", bus.requests.empty())

    tgOff = Telegram("", "", LOG, Bus())
    check("no token = telegram disabled", tgOff.enabled is False)
    tgOff.send("anything")
    check("disabled send is a harmless no-op", True)

    cfgP, bP, eP = engine_with(tmp)
    busP = Bus()
    eP.bus = busP
    bP.bars_m1, bP.bars_tf = mkbars(600), mkbars(900)
    busP.paused = True
    eP._last_bar = None
    eP.step()
    check("paused engine opens nothing", len(bP.opened) == 0)

    cfgN, bN, eN = engine_with(tmp)
    boom = []
    eN._notify_raw = lambda t: (_ for _ in ()).throw(RuntimeError("telegram exploded"))
    bN._pos = [FakePos(i, POSITION_TYPE_BUY, 0.02) for i in range(5)]
    bN.floating = 8.0
    eN.st["open_side"] = BUY
    try:
        eN.step()
        check("a broken notifier still closes the basket", len(bN._pos) == 0)
    except RuntimeError:
        check("a broken notifier still closes the basket", False, "notify raised into the engine")

    # ---------------------------------------------------------------- the trading window (V2)
    print("trading window - parsing")
    check("blank means all day", Config(trading_hours="").window is None)
    check("07:00-12:00 parses to minutes", Config(trading_hours="07:00-12:00").window == (420, 720))
    check("7:00-12:00 (one digit hour) parses", Config(trading_hours="7:00-12:00").window == (420, 720))
    for bad in ("07:00", "7-12", "07:00~12:00", "25:00-26:00", "07:00-07:60", "garbage"):
        try:
            Config(trading_hours=bad)
            check(f"rejects {bad!r}", False)
        except ValueError:
            check(f"rejects {bad!r}", True)
    for bad in ("12:00-07:00", "09:00-09:00"):
        try:
            Config(trading_hours=bad)
            check(f"rejects backwards/zero window {bad!r}", False)
        except ValueError:
            check(f"rejects backwards/zero window {bad!r}", True)
    try:
        Config(trading_hours="07:00-08:00", winddown_minutes=60)
        check("rejects a wind-down as long as the window", False)
    except ValueError:
        check("rejects a wind-down as long as the window", True)
    check("warns when the window has no force-flat",
          any("FLAT_AT_WINDOW_END" in w for w in
              Config(trading_hours="07:00-12:00", flat_at_window_end=False).warnings))
    check("warns when the daily loss limit is below one basket's stop",
          any("SMALLER" in w for w in Config(daily_loss_limit_usd=50.0, stop_loss_usd=70.0).warnings))
    check("no such warning when the limit is sensible",
          not any("SMALLER" in w for w in Config(daily_loss_limit_usd=150.0, stop_loss_usd=70.0).warnings))

    print("trading window - phases")
    cfgW, bW, eW = engine_with(tmp, trading_hours="07:00-12:00", winddown_minutes=30)

    def at(h, m=0):
        return dt.datetime(2026, 10, 7, h, m, tzinfo=dt.timezone.utc)

    for h, m, want in [(6, 59, "BEFORE"), (0, 0, "BEFORE"), (7, 0, "OPEN"), (10, 0, "OPEN"),
                       (11, 29, "OPEN"), (11, 30, "WINDDOWN"), (11, 59, "WINDDOWN"),
                       (12, 0, "AFTER"), (23, 59, "AFTER")]:
        check(f"{h:02d}:{m:02d} is {want}", eW._window_phase(at(h, m)) == want)
    check("07:00 exactly may open", eW._may_open(at(7, 0)))
    check("11:29 may open", eW._may_open(at(11, 29)))
    check("11:30 may NOT open (wind-down)", not eW._may_open(at(11, 30)))
    check("12:00 may NOT open", not eW._may_open(at(12, 0)))
    check("06:59 may NOT open", not eW._may_open(at(6, 59)))
    check("minutes left at 11:00 is 60", eW._minutes_left(at(11, 0)) == 60)

    cfgA, bA, eA = engine_with(tmp)                       # no window at all
    check("no window means ALL_DAY", eA._window_phase(at(3, 0)) == "ALL_DAY")
    check("no window may always open", eA._may_open(at(3, 0)) and eA._may_open(at(23, 0)))
    check("no window never forces flat", not eA._window_flat_due(at(23, 0)))

    print("trading window - force-flat")
    check("flat is due after the window", eW._window_flat_due(at(12, 0)))
    check("flat stays due later in the day", eW._window_flat_due(at(18, 0)))
    check("flat is NOT due during wind-down", not eW._window_flat_due(at(11, 45)))
    check("flat is NOT due before the window", not eW._window_flat_due(at(5, 0)))
    cfgF, bF, eF = engine_with(tmp, trading_hours="07:00-12:00", flat_at_window_end=False)
    check("force-flat off means no forced close", not eF._window_flat_due(at(18, 0)))

    print("trading window - the engine acts on it")
    # A losing basket, sat open after the window shut, must be closed anyway.
    cfg1, b1, e1 = engine_with(tmp, trading_hours="07:00-12:00")
    b1._pos = [FakePos(1, POSITION_TYPE_BUY, 0.02) for _ in range(5)]
    b1.floating = -22.0                                   # neither target nor stop: only the window closes it
    e1.st["open_side"] = BUY
    EN._utc_now = lambda: at(12, 1)
    try:
        e1.step()
        check("a basket open past the window end is closed", len(b1._pos) == 0)
        check("and it is recorded as WINDOW END",
              e1.st["baskets"] and e1.st["baskets"][-1]["why"] == "WINDOW END")
    finally:
        EN._utc_now = _real_now

    # Inside the window, that same basket is left alone.
    cfg2, b2, e2 = engine_with(tmp, trading_hours="07:00-12:00")
    b2._pos = [FakePos(1, POSITION_TYPE_BUY, 0.02) for _ in range(5)]
    b2.floating = -22.0
    e2.st["open_side"] = BUY
    EN._utc_now = lambda: at(10, 0)
    try:
        e2.step()
        check("a basket inside the window is left alone", len(b2._pos) == 5)
    finally:
        EN._utc_now = _real_now

    # Target still beats the window: a winner closed at 12:01 is reported as TARGET, not WINDOW END.
    cfg3, b3, e3 = engine_with(tmp, trading_hours="07:00-12:00")
    b3._pos = [FakePos(1, POSITION_TYPE_BUY, 0.02) for _ in range(5)]
    b3.floating = 9.0
    e3.st["open_side"] = BUY
    EN._utc_now = lambda: at(12, 1)
    try:
        e3.step()
        check("a winner past the window end still reads TARGET",
              e3.st["baskets"] and e3.st["baskets"][-1]["why"] == "TARGET")
    finally:
        EN._utc_now = _real_now

    # Starting the bot outside the window must sit idle, not trade.
    for hh, label in [(5, "before"), (13, "after"), (11, "during wind-down")]:
        cfgX, bX, eX = engine_with(tmp, trading_hours="07:00-12:00")
        bX._pos = []
        EN._utc_now = lambda hh=hh: at(hh, 40 if hh == 11 else 0)
        try:
            eX.step()
            check(f"started {label} the window: opens nothing", len(bX._pos) == 0)
            check(f"started {label} the window: says why", bool(eX.halt_reason))
        finally:
            EN._utc_now = _real_now

    print("trading window - alerts and /flat")
    sent = []
    cfgN, bN2, eN2 = engine_with(tmp, trading_hours="07:00-12:00")
    eN2._notify_raw = sent.append
    eN2._last_phase = "BEFORE"
    EN._utc_now = lambda: at(7, 0)
    try:
        eN2._window_alerts()
        check("alerts when the window opens", any("WINDOW OPEN" in x for x in sent))
        sent.clear()
        eN2._last_phase = "OPEN"
        EN._utc_now = lambda: at(11, 55)
        eN2._window_alerts()
        check("alerts when winding down", any("WINDING DOWN" in x for x in sent))
        check("and nudges with the minutes left", any("MIN LEFT" in x for x in sent))
        sent.clear()
        eN2._window_alerts()
        check("the minutes-left nudge is sent once a day, not every second",
              not any("MIN LEFT" in x for x in sent))
        sent.clear()
        eN2._last_phase = "WINDDOWN"
        EN._utc_now = lambda: at(12, 0)
        eN2._window_alerts()
        check("alerts when the window closes", any("WINDOW CLOSED" in x for x in sent))
        sent.clear()
        eN2._last_phase = None                            # a restart mid-window is not a transition
        EN._utc_now = lambda: at(9, 0)
        eN2._window_alerts()
        check("a restart mid-window does not fire a false 'window open'", not sent)
    finally:
        EN._utc_now = _real_now

    class Bus:
        def __init__(self, reqs):
            self.reqs, self.paused = reqs, False

        def drain(self):
            r, self.reqs = self.reqs, []
            return r

        def publish(self, snap):
            pass

    cfgL, bL, eL = engine_with(tmp)
    eL.bus = Bus([{"what": "flat"}])
    bL._pos = [FakePos(1, POSITION_TYPE_BUY, 0.02) for _ in range(5)]
    bL.floating = -3.0
    eL.st["open_side"] = BUY
    eL._serve_requests(bL.positions())
    check("/flat closes everything", len(bL._pos) == 0)
    check("/flat also pauses, so nothing reopens", eL.bus.paused is True)
    check("/flat is recorded as FLAT", eL.st["baskets"] and eL.st["baskets"][-1]["why"] == "FLAT")

    cfgL2, bL2, eL2 = engine_with(tmp)
    eL2.bus = Bus([{"what": "flat"}])
    bL2._pos = []
    eL2._serve_requests([])
    check("/flat with nothing open still pauses", eL2.bus.paused is True)

    print("expect.py knows more than one symbol")
    from sniper import expect as EX
    check("gold is 100 oz a lot", EX.CONTRACTS["XAUUSD"]["units"] == 100.0)
    check("FX is 100k units a lot", EX.CONTRACTS["EURUSD"]["units"] == 100_000.0)
    check("bitcoin is 1 coin a lot", EX.CONTRACTS["BTCUSD"]["units"] == 1.0)
    check("no JPY or cross pair is listed (they need a currency conversion)",
          not any("JPY" in s for s in EX.CONTRACTS))
    try:
        EX.run(Config(), symbol="USDJPY")
        check("an unknown symbol is refused, not guessed", False)
    except SystemExit:
        check("an unknown symbol is refused, not guessed", True)
    # The bug this guards against: --window used to filter only the ENTRY hour and then hold the trade
    # for 24 hours, so a market needing 5 hours to resolve was scored on exits the bot never reaches.
    # EURUSD read +$4.85 a basket that way and -$1.78 once the 12:00 close was counted. If the force-flat
    # modelling ever gets dropped, 'forced' goes to zero and this fails.
    src = open(EX.__file__, encoding="utf-8").read()
    check("expect models the window's force-flat, not just the entry hour",
          "deadline_ix" in src and "forced_n" in src)
    check("the force-flat is only applied when the config asks for it",
          "if window and flat_at_end:" in src)

    print("stopping cleanly (Ctrl+C path)")
    import sniper.run as RUN
    RUN._stop["now"] = False
    RUN._on_signal(2, None)
    check("Ctrl+C sets the stop flag", RUN._stop["now"] is True)
    RUN._stop["now"] = False
    src = open(RUN.__file__, encoding="utf-8").read()
    check("a human stop returns exit code 3", "return 3 if _stop[\"now\"] else 0" in src)
    bat = os.path.join(os.path.dirname(os.path.dirname(RUN.__file__)), "start_sniper.bat")
    b = open(bat, encoding="utf-8").read()
    check("supervisor does NOT restart on code 3",
          'if "%RC%"=="3"' in b and b.index('if "%RC%"=="3"') < b.index("goto loop"))
    check("supervisor does NOT restart on code 2 (bad config)", 'if "%RC%"=="2"' in b)
    check("supervisor uses the venv python directly (no activation needed)",
          ".venv\\Scripts\\python.exe" in b)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
