"""MT5 gateway for the Sniper Spark clone.

Everything that can be broken by a dropped wifi connection lives behind this class. The rules:

  * no MT5 call is made outside `_call`, which converts any failure - exception, None, falsy return,
    terminal not connected - into `BrokerDown`;
  * `BrokerDown` is never fatal. `ensure()` reconnects with an exponential backoff, and the main loop
    simply tries again on the next pass;
  * the BROKER is the source of truth. Open positions are re-read from MT5 every loop and the day's
    realised money is re-read from MT5's own deal history, so a crash, a restart or a 3-hour outage
    cannot make the bot forget a basket it already has open;
  * symbols are resolved per account, so one .env runs on Zero (XAUUSDz) or Standard (XAUUSDm).
"""
import os
import time
import datetime as dt

import MetaTrader5 as mt5
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUY, SELL = 1, -1


class BrokerDown(Exception):
    """MT5 is unreachable, the terminal is disconnected, or a call failed. Always recoverable."""


class Broker:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.sym = None                 # the resolved, account-specific symbol name
        self.spec = None
        self.account = None
        self._ok = False
        self._next_try = 0.0
        self._backoff = cfg.reconnect_backoff_s
        self._comm = {}                 # ticket -> commission already charged; fixed once a position is open

    # ------------------------------------------------------------------ connection
    def _creds(self):
        load_dotenv(os.path.join(ROOT, ".env"), override=False)
        p = {"MT5": "MT5", "ZERO": "ZERO", "Z": "Z"}[self.cfg.account]
        login = os.environ.get(f"{p}_LOGIN")
        pwd = os.environ.get(f"{p}_PASSWORD")
        srv = os.environ.get(f"{p}_SERVER")
        path = os.environ.get("MT5_PATH")
        if not (login and pwd and srv):
            raise RuntimeError(f".env is missing {p}_LOGIN / {p}_PASSWORD / {p}_SERVER")
        return int(login), pwd, srv, path

    def connect(self):
        """Attach to the running terminal if it is already on the right account, else log in."""
        login, pwd, srv, path = self._creds()
        if not mt5.initialize(**({"path": path} if path and os.path.exists(path) else {})):
            if not mt5.initialize(login=login, password=pwd, server=srv,
                                  **({"path": path} if path and os.path.exists(path) else {})):
                raise BrokerDown(f"initialize failed: {mt5.last_error()}")
        acc = mt5.account_info()
        if acc is None or int(acc.login) != login:
            if not mt5.initialize(login=login, password=pwd, server=srv,
                                  **({"path": path} if path and os.path.exists(path) else {})):
                raise BrokerDown(f"login to {login} failed: {mt5.last_error()}")
            acc = mt5.account_info()
        if acc is None:
            raise BrokerDown("no account_info after connect")

        # DEMO guard. MODE_DEMO must never touch a real-money account.
        is_demo = int(acc.trade_mode) == mt5.ACCOUNT_TRADE_MODE_DEMO
        if self.cfg.run_mode == "MODE_DEMO" and not is_demo:
            mt5.shutdown()
            raise RuntimeError(f"REFUSING: account {acc.login} is not a demo and run_mode is MODE_DEMO. "
                               f"Set SNIPER_RUN_MODE=MODE_LIVE in .env only if you really mean it.")
        # The account we ENDED UP on, not the one we asked for. mt5.initialize(login=...) can return
        # True while leaving the terminal on a different account, and trading the wrong account is the
        # worst failure this bot could have.
        if int(acc.login) != login:
            mt5.shutdown()
            raise RuntimeError(
                f"REFUSING: asked for account {login} ({self.cfg.account}) but the terminal is on "
                f"{acc.login}. Close MT5, log into {login}, and start again - or set SNIPER_ACCOUNT "
                f"to the block in .env whose login is {acc.login}.")

        self.account = acc
        t = mt5.terminal_info()
        if t is not None and not t.trade_allowed:
            self.log.warning("AUTOTRADING IS OFF in MetaTrader 5 - every order will be rejected with "
                             "'AutoTrading disabled by client'. Click the 'Algo Trading' button in the "
                             "MT5 toolbar so it turns green.")
        self.sym = self._resolve_symbol(self.cfg.symbol)
        # Select FIRST, then read. A symbol that is not in Market Watch can return None from
        # symbol_info, and asking before selecting made a reconnect fail with "no symbol_info".
        mt5.symbol_select(self.sym, True)
        self.spec = mt5.symbol_info(self.sym)
        if self.spec is None:                       # terminal can be briefly busy; one retry
            time.sleep(0.5)
            mt5.symbol_select(self.sym, True)
            self.spec = mt5.symbol_info(self.sym)
        if self.spec is None:
            raise BrokerDown(f"no symbol_info for {self.sym} (is it tradable on this account?)")
        self._ok = True
        self._backoff = self.cfg.reconnect_backoff_s
        self.log.info("connected login=%s server=%s mode=%s balance=%.2f %s symbol=%s",
                      acc.login, acc.server, "DEMO" if is_demo else "REAL", acc.balance, acc.currency, self.sym)
        return self

    def _resolve_symbol(self, base):
        """'XAUUSD' -> whatever this account actually calls it (XAUUSDz, XAUUSDm, XAUUSD...)."""
        if mt5.symbol_info(base) is not None:
            return base
        all_syms = mt5.symbols_get()
        if not all_syms:
            raise BrokerDown("symbols_get returned nothing")
        names = [s.name for s in all_syms]
        exact = [n for n in names if n.upper() == base.upper()]
        if exact:
            return exact[0]
        cands = [n for n in names if n.upper().startswith(base.upper())]
        if not cands:
            raise RuntimeError(f"no symbol on this account starts with {base!r}. "
                               f"Check SNIPER_SYMBOL. Examples here: {sorted(names)[:8]}")
        cands.sort(key=len)                     # XAUUSDz before XAUUSD247z
        self.log.info("symbol %s resolved to %s (options: %s)", base, cands[0], cands[:4])
        return cands[0]

    def ensure(self):
        """Call once per loop. Raises BrokerDown while the connection is still down."""
        if self._ok:
            t = mt5.terminal_info()
            if t is not None and getattr(t, "connected", False):
                return
            self.log.warning("terminal reports disconnected - will reconnect")
            self._ok = False
            self._next_try = 0.0
        now = time.time()
        if now < self._next_try:
            raise BrokerDown("waiting for reconnect backoff")
        try:
            try:
                mt5.shutdown()
            except Exception:
                pass
            self.connect()
        except RuntimeError:
            raise                                   # config / safety errors must not be retried silently
        except Exception as e:
            self._next_try = now + self._backoff
            self._backoff = min(self._backoff * 2, self.cfg.reconnect_max_s)
            raise BrokerDown(f"reconnect failed ({e}); next try in {self._backoff:.0f}s") from None

    def _call(self, fn, *a, **kw):
        try:
            # MUST NOT pass an empty **kw: CPython then hands the C extension an empty kwargs dict, and
            # MetaTrader5's trade functions reject that with "-2 Unnamed arguments not allowed".
            # order_send(req) works; order_send(req, **{}) does not. Market-data calls tolerate it,
            # which is why only order_send ever failed.
            r = fn(*a, **kw) if kw else fn(*a)
        except Exception as e:
            self._ok = False
            raise BrokerDown(f"{getattr(fn, '__name__', fn)} raised {e}") from None
        if r is None:
            self._ok = False
            raise BrokerDown(f"{getattr(fn, '__name__', fn)} returned None: {mt5.last_error()}")
        return r

    # ------------------------------------------------------------------ market data
    def tick(self):
        t = self._call(mt5.symbol_info_tick, self.sym)
        if not t.bid or not t.ask:
            raise BrokerDown("tick has no bid/ask")
        return t

    def spread_points(self):
        t = self.tick()
        return (t.ask - t.bid) / self.spec.point

    TF = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
          "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}

    def bars(self, tf, n):
        """The last n CLOSED bars, oldest first. Index -1 is the most recent closed bar."""
        r = self._call(mt5.copy_rates_from_pos, self.sym, self.TF[tf], 1, int(n))
        if len(r) < n:
            raise BrokerDown(f"only {len(r)}/{n} {tf} bars available")
        return r

    # ------------------------------------------------------------------ our positions
    def positions(self):
        """Only the positions this bot owns: our magic, our symbol."""
        r = mt5.positions_get(symbol=self.sym)
        if r is None:
            if mt5.last_error()[0] == 1:            # RES_S_OK with an empty set
                return []
            self._ok = False
            raise BrokerDown(f"positions_get failed: {mt5.last_error()}")
        return [p for p in r if p.magic == self.cfg.magic]

    def basket_money(self, positions):
        """Combined floating money of the basket, in account currency.

        profit + swap is what MT5 shows. Commission is charged on the DEALS, not the position, so on a
        raw-spread account it has to be fetched and counted or the basket closes before it is really
        at the target.

        Measured on Exness Zero gold: the WHOLE round-trip commission is taken on the entry deal
        (-0.22 per 0.02 lot in, 0.00 out), so the entry deals are already the full cost. An earlier
        version doubled it and quietly moved a $7 target to $9.20. Commission is cached per ticket -
        it cannot change once the position is open.
        """
        gross = sum(p.profit + p.swap for p in positions)
        if not self.cfg.include_commission or not positions:
            return gross, 0.0
        comm = 0.0
        for p in positions:
            c = self._comm.get(p.ticket)
            if c is None:
                d = mt5.history_deals_get(position=p.ticket)
                c = sum(x.commission for x in d) if d else 0.0
                if d:
                    self._comm[p.ticket] = c
            comm += c
        if len(self._comm) > 500:
            self._comm.clear()
        return gross + comm, comm

    # ------------------------------------------------------------------ orders
    def _filling(self):
        m = int(self.spec.filling_mode)
        if m & 2:
            return mt5.ORDER_FILLING_IOC
        if m & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _round_lot(self, lot):
        step = self.spec.volume_step or 0.01
        lot = max(self.spec.volume_min, min(self.spec.volume_max, lot))
        return round(round(lot / step) * step, 8)

    def open_position(self, side, lot, sl=0.0, tp=0.0, comment="SNIPER"):
        t = self.tick()
        price = t.ask if side == BUY else t.bid
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=self.sym, volume=self._round_lot(lot),
                   type=mt5.ORDER_TYPE_BUY if side == BUY else mt5.ORDER_TYPE_SELL,
                   price=price, deviation=int(self.cfg.max_slippage_points),
                   magic=int(self.cfg.magic), comment=comment[:31],
                   type_time=mt5.ORDER_TIME_GTC, type_filling=self._filling())
        if sl:
            req["sl"] = round(sl, self.spec.digits)
        if tp:
            req["tp"] = round(tp, self.spec.digits)
        r = self._call(mt5.order_send, req)
        if r.retcode != mt5.TRADE_RETCODE_DONE:
            # a rejected filling mode is worth one retry with the other one
            if r.retcode in (mt5.TRADE_RETCODE_INVALID_FILL,):
                req["type_filling"] = (mt5.ORDER_FILLING_FOK if req["type_filling"] == mt5.ORDER_FILLING_IOC
                                       else mt5.ORDER_FILLING_IOC)
                r = self._call(mt5.order_send, req)
            if r.retcode != mt5.TRADE_RETCODE_DONE:
                raise BrokerDown(f"open rejected retcode={r.retcode} {getattr(r, 'comment', '')}")
        return r

    def close_position(self, p):
        t = self.tick()
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=self.sym, volume=p.volume,
                   type=mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                   position=p.ticket, price=t.bid if p.type == mt5.POSITION_TYPE_BUY else t.ask,
                   deviation=int(self.cfg.max_slippage_points), magic=int(self.cfg.magic),
                   comment="SNIPER-close", type_time=mt5.ORDER_TIME_GTC, type_filling=self._filling())
        r = self._call(mt5.order_send, req)
        if r.retcode != mt5.TRADE_RETCODE_DONE:
            raise BrokerDown(f"close rejected retcode={r.retcode} {getattr(r, 'comment', '')}")
        return r

    # ------------------------------------------------------------------ history (authoritative)
    def realized_since(self, t_from):
        """Our realised money since t_from, straight from MT5's deal history.

        This is how the daily profit/loss limits survive a restart: nothing is trusted from our own
        state file that MT5 can be asked about directly.
        """
        d = mt5.history_deals_get(t_from, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1))
        if d is None:
            if mt5.last_error()[0] == 1:
                return 0.0, 0
            self._ok = False
            raise BrokerDown(f"history_deals_get failed: {mt5.last_error()}")
        # Count EVERY deal, entries included. Exness charges the whole commission on the ENTRY deal, so
        # filtering to exit deals only (an earlier version did) reported the day as $1.10 per basket
        # better than it really was - and the daily loss limit is built on this number.
        ours = [x for x in d if x.magic == self.cfg.magic and x.symbol == self.sym]
        closed = sum(1 for x in ours
                     if x.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY, mt5.DEAL_ENTRY_INOUT))
        return sum(x.profit + x.commission + x.swap for x in ours), closed

    def shutdown(self):
        try:
            mt5.shutdown()
        except Exception:
            pass
        self._ok = False
