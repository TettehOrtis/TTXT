"""Thin, defensive wrapper over the MetaTrader5 API.

- refuses to trade on a non-demo account unless ALLOW_LIVE=1
- auto-reconnect, filling-mode detection, retry only on safe retcodes
- every fill returns intended vs filled price (real slippage) and latency
- positions()/deals return None on API error so callers can tell "nothing open" from "could not read"
"""
import os, time, logging, datetime as dt
from dataclasses import dataclass
import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

log = logging.getLogger("broker")
UTC = dt.timezone.utc
RETRY_CODES = {mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_PRICE_CHANGED, mt5.TRADE_RETCODE_PRICE_OFF}
OK_CODES = {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL}
UNKNOWN = -1          # order_send returned None / timed out: the order may or may not have been executed
SPEC_TTL = 300


@dataclass
class Fill:
    ok: bool
    retcode: int
    comment: str
    ticket: int = 0          # order ticket
    deal: int = 0
    price: float = 0.0
    volume: float = 0.0
    intended: float = 0.0
    slippage: float = 0.0    # adverse price units (positive = worse than intended)
    latency_ms: float = 0.0

    @property
    def unknown(self):
        return (not self.ok) and self.retcode in (UNKNOWN, mt5.TRADE_RETCODE_TIMEOUT, mt5.TRADE_RETCODE_CONNECTION)


class Broker:
    def __init__(self, suffix="m", magic=550055, env="MT5"):
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
        self.env = env                        # .env prefix: MT5 (Standard) or Z (Zero)
        self.suffix, self.magic = suffix, magic
        self._spec = {}

    # ---------------------------------------------------------------- connection
    def connect(self, tries=5):
        for i in range(tries):
            ok = mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv(f"{self.env}_LOGIN")),
                                password=os.getenv(f"{self.env}_PASSWORD"), server=os.getenv(f"{self.env}_SERVER"))
            if ok:
                ai = mt5.account_info()
                if ai is not None and ai.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO and os.getenv("ALLOW_LIVE") != "1":
                    mt5.shutdown()
                    raise SystemExit("REFUSING: account is not DEMO (set ALLOW_LIVE=1 to override)")
                if ai is not None:
                    log.info("connected acct=%s server=%s bal=%.2f eq=%.2f lev=1:%s",
                             ai.login, ai.server, ai.balance, ai.equity, ai.leverage)
                return True
            log.warning("mt5.initialize failed (%s) try %d/%d", mt5.last_error(), i + 1, tries)
            time.sleep(2 * (i + 1))
        raise ConnectionError("cannot connect to MT5")

    def ensure(self):
        ti = mt5.terminal_info()
        if ti is None or not ti.connected:
            log.warning("terminal disconnected -> reconnect")
            mt5.shutdown()
            self.connect()

    def shutdown(self):
        mt5.shutdown()

    # ---------------------------------------------------------------- market data
    def sym(self, base):
        return base + self.suffix

    def spec(self, s):
        c = self._spec.get(s)
        if c is None or time.time() - c[0] > SPEC_TTL:
            mt5.symbol_select(s, True)
            i = mt5.symbol_info(s)
            if i is None:
                if c is None:
                    raise RuntimeError(f"symbol_info({s}) unavailable")
                return c[1]                       # keep stale spec rather than crash
            c = (time.time(), i)
            self._spec[s] = c
        return c[1]

    def tick(self, s):
        return mt5.symbol_info_tick(s)

    def rates(self, s, n=600):
        r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M1, 0, n)
        if r is None or len(r) == 0:
            return None
        d = pd.DataFrame(r)
        d["time"] = pd.to_datetime(d["time"], unit="s")
        return d

    def is_open(self, s, max_age=120):
        """Open if trading is allowed on the symbol and its last tick is fresh (broker clock == UTC epoch)."""
        i = self.spec(s)
        t = self.tick(s)
        if t is None or i.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
            return False
        return abs(time.time() - t.time) < max_age

    # ---------------------------------------------------------------- account/positions
    def account(self):
        return mt5.account_info()

    def positions(self, s=None):
        """This bot's positions (by magic). Returns None if the API call FAILED (do not treat as 'none open')."""
        p = mt5.positions_get(symbol=s) if s else mt5.positions_get()
        if p is None:
            return None
        return [x for x in p if x.magic == self.magic]

    def deals_since(self, epoch_from):
        """All deals of this bot since epoch_from (tz-aware UTC query). None on API failure."""
        d = mt5.history_deals_get(dt.datetime.fromtimestamp(int(epoch_from), UTC),
                                  dt.datetime.fromtimestamp(int(time.time()) + 86400, UTC))
        if d is None:
            return None
        return [x for x in d if x.magic == self.magic and x.time >= epoch_from]

    def deals_for_position(self, ticket):
        d = mt5.history_deals_get(position=ticket)
        return list(d) if d else []

    # ---------------------------------------------------------------- sizing helpers
    def loss_per_lot(self, s, price_dist):
        """USD loss for 1.0 lot if price moves `price_dist` against us (broker's own tick maths)."""
        i = self.spec(s)
        tv = getattr(i, "trade_tick_value_loss", 0.0) or i.trade_tick_value
        return price_dist / i.trade_tick_size * tv

    def round_lots(self, s, lots):
        i = self.spec(s)
        steps = int(lots / i.volume_step + 1e-9)
        return max(0.0, min(round(steps * i.volume_step, 8), i.volume_max))

    def _fill_mode(self, s):
        fm = self.spec(s).filling_mode
        if fm & 2:
            return mt5.ORDER_FILLING_IOC
        if fm & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    # ---------------------------------------------------------------- orders
    def _send(self, req, intended, side):
        t0 = time.perf_counter()
        for _ in range(3):
            r = mt5.order_send(req)
            if r is None:
                log.error("order_send None: %s (outcome UNKNOWN)", mt5.last_error())
                return Fill(False, UNKNOWN, str(mt5.last_error()))
            if r.retcode in OK_CODES:
                lat = (time.perf_counter() - t0) * 1000
                slip = (r.price - intended) if side == 1 else (intended - r.price)
                return Fill(True, r.retcode, r.comment, ticket=r.order, deal=r.deal, price=r.price, volume=r.volume,
                            intended=intended, slippage=slip, latency_ms=lat)
            if r.retcode in RETRY_CODES:
                t = self.tick(req["symbol"])
                req["price"] = t.ask if req["type"] == mt5.ORDER_TYPE_BUY else t.bid
                continue
            return Fill(False, r.retcode, r.comment)
        return Fill(False, -2, "retries exhausted")

    def open(self, s, side, lots, sl=None, tp=None, comment="sc", deviation=20):
        """side: +1 buy / -1 sell. sl/tp are absolute prices (server-side protective orders)."""
        i = self.spec(s)
        t = self.tick(s)
        price = t.ask if side == 1 else t.bid
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=s, volume=lots,
                   type=mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL, price=price,
                   deviation=deviation, magic=self.magic, comment=comment[:30],
                   type_time=mt5.ORDER_TIME_GTC, type_filling=self._fill_mode(s))
        if sl:
            req["sl"] = round(sl, i.digits)
        if tp:
            req["tp"] = round(tp, i.digits)
        chk = mt5.order_check(req)
        if chk is None or chk.retcode not in (0, mt5.TRADE_RETCODE_DONE):
            return Fill(False, chk.retcode if chk else -3, f"order_check: {chk.comment if chk else mt5.last_error()}")
        return self._send(req, price, side)

    def position_id_of(self, fill):
        """Resolve the position ticket for a fill (deal -> position_id); falls back to the order ticket."""
        if fill.deal:
            d = mt5.history_deals_get(ticket=fill.deal)
            if d:
                return d[0].position_id
        return fill.ticket

    def close(self, pos, deviation=30):
        t = self.tick(pos.symbol)
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        price = t.bid if is_buy else t.ask
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=pos.symbol, volume=pos.volume,
                   type=mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                   position=pos.ticket, price=price, deviation=deviation, magic=self.magic, comment="sc-close",
                   type_time=mt5.ORDER_TIME_GTC, type_filling=self._fill_mode(pos.symbol))
        return self._send(req, price, -1 if is_buy else 1)

    def modify(self, pos, sl=None, tp=None):
        """Returns (ok, retcode)."""
        i = self.spec(pos.symbol)
        req = dict(action=mt5.TRADE_ACTION_SLTP, symbol=pos.symbol, position=pos.ticket,
                   sl=round(sl if sl is not None else pos.sl, i.digits),
                   tp=round(tp if tp is not None else pos.tp, i.digits), magic=self.magic)
        r = mt5.order_send(req)
        if r is None:
            return False, UNKNOWN
        return r.retcode in OK_CODES, r.retcode
