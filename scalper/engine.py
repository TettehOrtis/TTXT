"""Main trading loop. Strategy-agnostic: a Strategy turns closed M1 bars into an optional Signal.

Safety properties
- every order carries a server-side SL (and TP); after the fill both are RE-ANCHORED to the actual fill price,
  and if a stop cannot be attached the position is closed at once
- an order whose outcome is unknown (timeout / None) is reconciled against open positions - never blindly retried
- positions found without bookkeeping (restart, crash) are ADOPTED (or closed if they carry no stop)
- signals are only evaluated within seconds after an M1 bar closes (matches the backtest's "enter at next bar open")
- flat before the 17:00 New York rollover / weekend; daily lock (target / loss / giveback) closes everything
- every closed trade is logged with intended vs filled price, spread, slippage, latency, reason and NET P/L
"""
import csv, os, time, logging, json, datetime as dt
from dataclasses import dataclass
import pandas as pd
import MetaTrader5 as mt5
from .risk import RiskManager, RiskConfig, atomic_json, NY

log = logging.getLogger("engine")
TRADES_CSV = "logs/trades.csv"
POS_STATE = "logs/positions.json"
FIELDS = ["t_open", "t_close", "sym", "side", "lots", "tag", "intended", "filled", "entry_slip", "spread_entry",
          "sl0", "tp0", "exit_price", "exit_reason", "gross", "commission", "swap", "net", "latency_ms", "hold_s"]
REASON = {mt5.DEAL_REASON_SL: "SL", mt5.DEAL_REASON_TP: "TP", mt5.DEAL_REASON_CLIENT: "manual",
          mt5.DEAL_REASON_EXPERT: "bot", mt5.DEAL_REASON_SO: "STOPOUT"}
BAR_WINDOW_S = 15            # a bar is tradable only this many seconds after it closed


@dataclass
class Signal:
    side: int                      # +1 / -1
    sl_dist: float                 # price units from entry to stop
    tp_dist: float = 0.0           # 0 = no fixed target (trail / time-stop only)
    max_hold_s: int = 1800
    be_at_r: float = 1.0           # move stop to break-even(+cost) after this many R in profit (0=off)
    trail_start_r: float = 1.0     # start trailing after this many R (0=off)
    trail_dist_r: float = 0.8      # trail this many R behind best price
    tag: str = ""


class Strategy:
    symbols: list = []             # base names, e.g. ["XAUUSD"]
    max_spread: dict = {}          # base -> max spread (price units) allowed at entry
    max_entry_slip_r: float = 0.4  # close immediately if entry slippage exceeds this many R

    def session_ok(self, now_utc: dt.datetime, base: str) -> bool:
        return True

    def on_bar(self, base: str, rates, tick, spec):
        """rates: DataFrame of CLOSED M1 bars (last row = the bar that just closed). Return Signal or None."""
        return None


def in_rollover_window(now_utc):
    """16:50-17:10 New York: flatten and stay out (spread blow-out, day boundary, weekend close on Fridays)."""
    ny = now_utc.astimezone(NY)
    m = ny.hour * 60 + ny.minute
    return 16 * 60 + 50 <= m < 17 * 60 + 10


class Engine:
    def __init__(self, broker, strategy: Strategy, risk_cfg: RiskConfig, max_open=1, poll_s=0.5, dry_run=False):
        self.b, self.s, self.dry = broker, strategy, dry_run
        self.rm = RiskManager(broker, risk_cfg)
        self.max_open, self.poll = max_open, poll_s
        self.last_bar = {}
        self.mod_backoff = {}
        self.pos = self._load_pos()
        self._last_log = 0
        os.makedirs("logs", exist_ok=True)
        if not os.path.exists(TRADES_CSV):
            csv.writer(open(TRADES_CSV, "w", newline="")).writerow(FIELDS)

    # ------------------------------------------------------------- persistence of per-position context
    def _load_pos(self):
        try:
            return {int(k): v for k, v in json.load(open(POS_STATE)).items()}
        except Exception:
            return {}

    def _save_pos(self):
        atomic_json(POS_STATE, {str(k): v for k, v in self.pos.items()})

    # ------------------------------------------------------------- reconciliation
    def _adopt_orphans(self, live):
        """Any bot position without bookkeeping (restart/crash/unknown order outcome) is adopted, or closed if unprotected."""
        for p in live:
            if p.ticket in self.pos:
                continue
            if not p.sl:
                log.error("ORPHAN %s %s has NO stop -> closing", p.ticket, p.symbol)
                self.b.close(p)
                continue
            t = self.b.tick(p.symbol)
            side = 1 if p.type == mt5.POSITION_TYPE_BUY else -1
            self.pos[p.ticket] = dict(t_open=dt.datetime.fromtimestamp(p.time, dt.timezone.utc).isoformat(), ts_open=p.time,
                                      sym=p.symbol, side=side, lots=p.volume, tag="adopted", intended=p.price_open,
                                      filled=p.price_open, slip=0.0, spread=(t.ask - t.bid) if t else 0.0, sl0=p.sl, tp0=p.tp,
                                      R=abs(p.price_open - p.sl), lat=0, max_hold_s=1800, be_at_r=1.0, trail_start_r=1.0,
                                      trail_dist_r=0.8)
            log.warning("ADOPTED orphan position %s %s side=%+d lots=%s sl=%s", p.ticket, p.symbol, side, p.volume, p.sl)
            self._save_pos()

    # ------------------------------------------------------------- trade logging
    def _log_closed(self, live):
        live_t = {p.ticket for p in live}
        for t in [t for t in self.pos if t not in live_t]:
            ctx = self.pos[t]
            deals = self.b.deals_for_position(t)
            outs = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
            if not outs:                                   # history may lag the close by a moment: keep context, retry
                ctx.setdefault("gone_ts", time.time())
                if time.time() - ctx["gone_ts"] > 180:
                    log.error("position %s vanished but no closing deal found after 180 s - dropping context", t)
                    self.pos.pop(t)
                    self._save_pos()
                continue
            self.pos.pop(t)
            o = outs[-1]
            gross = sum(d.profit for d in deals)
            comm = sum(d.commission + d.fee for d in deals)
            swap = sum(d.swap for d in deals)
            net = gross + comm + swap
            row = [ctx["t_open"], dt.datetime.fromtimestamp(o.time, dt.timezone.utc).isoformat(), ctx["sym"], ctx["side"],
                   ctx["lots"], ctx["tag"], ctx["intended"], ctx["filled"], round(ctx["slip"], 5), round(ctx["spread"], 5),
                   ctx["sl0"], ctx["tp0"], o.price, ctx.get("forced") or REASON.get(o.reason, str(o.reason)), round(gross, 4),
                   round(comm, 4), round(swap, 4), round(net, 4), round(ctx["lat"]), int(o.time - ctx["ts_open"])]
            csv.writer(open(TRADES_CSV, "a", newline="")).writerow(row)
            log.info("CLOSED %s %s %+d lots=%s net=%+.2f (%s)", t, ctx["sym"], ctx["side"], ctx["lots"], net, row[13])
            self._save_pos()

    # ------------------------------------------------------------- position management
    def _force_close(self, p, why):
        ctx = self.pos.get(p.ticket)
        if ctx is not None:
            ctx["forced"] = why
            self._save_pos()
        r = self.b.close(p)
        if not r.ok:
            if ctx is not None:
                ctx.pop("forced", None)
            self.mod_backoff[p.ticket] = time.time() + 5
            log.warning("close(%s) failed: %s %s", why, r.retcode, r.comment)
        return r.ok

    def _manage(self, live):
        for p in live:
            ctx = self.pos.get(p.ticket)
            if not ctx:
                continue
            t = self.b.tick(p.symbol)
            if t is None:
                continue
            spec = self.b.spec(p.symbol)
            is_buy = p.type == mt5.POSITION_TYPE_BUY
            px = t.bid if is_buy else t.ask                     # price we would exit at
            fav = (px - p.price_open) if is_buy else (p.price_open - px)
            R = ctx["R"]
            if time.time() - ctx["ts_open"] > ctx["max_hold_s"] and time.time() >= self.mod_backoff.get(p.ticket, 0):
                self._force_close(p, "TIME")
                continue
            new_sl = None
            if ctx["be_at_r"] and fav >= ctx["be_at_r"] * R:
                lock = ctx["spread"] * 1.5                       # cover spread so BE is genuinely >= 0 net
                new_sl = p.price_open + lock if is_buy else p.price_open - lock
            if ctx["trail_start_r"] and fav >= ctx["trail_start_r"] * R:
                cand = px - ctx["trail_dist_r"] * R if is_buy else px + ctx["trail_dist_r"] * R
                if new_sl is None or (cand > new_sl if is_buy else cand < new_sl):
                    new_sl = cand
            if new_sl is None or not p.sl:
                continue
            better = new_sl > p.sl if is_buy else new_sl < p.sl
            min_dist = max(spec.trade_stops_level, spec.trade_freeze_level) * spec.point + (t.ask - t.bid)
            inside = (t.bid - new_sl >= min_dist) if is_buy else (new_sl - t.ask >= min_dist)
            if better and abs(new_sl - p.sl) >= 0.1 * R and inside and time.time() >= self.mod_backoff.get(p.ticket, 0):
                ok, rc = self.b.modify(p, sl=new_sl)
                if not ok:
                    self.mod_backoff[p.ticket] = time.time() + 5
                    log.warning("modify SL %s rejected rc=%s (backoff 5s)", p.ticket, rc)

    def _close_all(self, live, why):
        for p in live:
            self._force_close(p, why)

    # ------------------------------------------------------------- entries
    def _fresh_bar(self, sym, r, tick):
        """Return closed bars iff the last closed bar is the JUST-closed minute and we are within BAR_WINDOW_S of its close."""
        now_min = int(tick.time // 60) * 60
        closed = r[r.time < pd.Timestamp(now_min, unit="s")]
        if len(closed) < 300:
            return None
        bar_t = closed.time.iloc[-1]
        if int(bar_t.timestamp()) != now_min - 60:               # minute missing (no ticks) or stale data
            return None
        if self.last_bar.get(sym) == bar_t:
            return None
        self.last_bar[sym] = bar_t                               # each bar is evaluated at most once
        if tick.time - now_min > BAR_WINDOW_S:
            return None                                          # too late: would not match backtest entry timing
        return closed

    def _try_entries(self, now, live):
        ok, _ = self.rm.can_open()
        if not ok or in_rollover_window(now):
            return
        open_syms = {p.symbol for p in live}
        if len(open_syms) >= self.max_open:
            return
        for base in self.s.symbols:
            sym = self.b.sym(base)
            if sym in open_syms or not self.s.session_ok(now, base) or not self.b.is_open(sym):
                continue
            tick = self.b.tick(sym)
            r = self.b.rates(sym, getattr(self.s, "n_history", 700))
            if r is None or tick is None:
                continue
            closed = self._fresh_bar(sym, r, tick)
            if closed is None:
                continue
            spec = self.b.spec(sym)
            spread = tick.ask - tick.bid
            if spread > self.s.max_spread.get(base, 1e9):
                continue
            sig = self.s.on_bar(base, closed, tick, spec)
            if sig is not None:
                self._execute(sym, base, sig, spread)
                return

    def _execute(self, sym, base, sig, spread):
        lots, note = self.rm.size(sym, sig.sl_dist + spread)
        if lots <= 0:
            log.info("skip %s: %s", sym, note)
            return
        t = self.b.tick(sym)
        entry = t.ask if sig.side == 1 else t.bid
        if self.dry:
            log.info("DRY-RUN signal %s %+d %.2f lots @~%s SL dist %.5f [%s] %s", sym, sig.side, lots, entry, sig.sl_dist, sig.tag, note)
            return
        sl = entry - sig.side * sig.sl_dist
        tp = entry + sig.side * sig.tp_dist if sig.tp_dist else None
        f = self.b.open(sym, sig.side, lots, sl=sl, tp=tp, comment=sig.tag or base)
        if f.unknown:
            log.error("order outcome UNKNOWN (%s %s) -> reconciling", f.retcode, f.comment)
            time.sleep(1.5)
            live = self.b.positions(sym)
            if live:
                self._adopt_orphans(live)
            return
        if not f.ok:
            log.warning("open failed %s: %s %s", sym, f.retcode, f.comment)
            return
        pid = self.b.position_id_of(f)
        pos = next((x for x in (self.b.positions(sym) or []) if x.ticket == pid), None)
        fsl = f.price - sig.side * sig.sl_dist                    # re-anchor protective orders to the ACTUAL fill
        ftp = f.price + sig.side * sig.tp_dist if sig.tp_dist else None
        if pos is not None:
            attached = False
            for _ in range(3):
                ok, rc = self.b.modify(pos, sl=fsl, tp=ftp if ftp else pos.tp)
                if ok:
                    attached = True
                    break
                time.sleep(0.3)
            if not attached and not pos.sl:
                log.error("could not attach stop to %s -> closing immediately", pid)
                self.b.close(pos)
                return
        if f.slippage > self.s.max_entry_slip_r * sig.sl_dist and pos is not None:
            log.warning("entry slippage %.5f > %.1fR -> closing immediately", f.slippage, self.s.max_entry_slip_r)
            self.b.close(pos)
            return
        self.pos[pid] = dict(t_open=dt.datetime.now(dt.timezone.utc).isoformat(), ts_open=time.time(), sym=sym, side=sig.side,
                             lots=f.volume or lots, tag=sig.tag, intended=f.intended, filled=f.price, slip=f.slippage,
                             spread=spread, sl0=fsl, tp0=ftp or 0, R=sig.sl_dist, lat=f.latency_ms, max_hold_s=sig.max_hold_s,
                             be_at_r=sig.be_at_r, trail_start_r=sig.trail_start_r, trail_dist_r=sig.trail_dist_r)
        self._save_pos()
        log.info("OPEN %s %+d %.2f lots @%s (intended %s slip %+.3f lat %.0fms) SL=%s TP=%s [%s] %s",
                 sym, sig.side, f.volume or lots, f.price, f.intended, f.slippage, f.latency_ms, round(fsl, 5),
                 ftp and round(ftp, 5), sig.tag, note)

    # ------------------------------------------------------------- loop
    def run(self, deadline=None):
        d0 = self.rm.refresh()
        log.info("engine start | symbols=%s | day target=$%.2f loss_limit=$%.2f start_bal=$%.2f", self.s.symbols,
                 self.rm.target_usd(), self.rm.loss_limit_usd(), d0.start_balance)
        while deadline is None or time.time() < deadline:
            try:
                self.b.ensure()
                now = dt.datetime.now(dt.timezone.utc)
                live = self.b.positions()
                if live is None:                               # API hiccup: do nothing this cycle
                    time.sleep(1)
                    continue
                self._adopt_orphans(live)
                self._log_closed(live)
                d = self.rm.refresh()
                if d.locked:
                    if live:
                        log.info("LOCKED (%s) -> closing everything", d.locked)
                        self._close_all(live, "LOCK")
                    if time.time() - self._last_log > 300:
                        log.info("day locked: %s | net today %+.2f", d.locked, d.pnl)
                        self._last_log = time.time()
                    time.sleep(5)
                    continue
                if in_rollover_window(now):
                    if live:
                        log.info("rollover window -> flatten")
                        self._close_all(live, "ROLLOVER")
                    time.sleep(5)
                    continue
                self._manage(live)
                self._try_entries(now, live)
                if time.time() - self._last_log > 300:
                    log.info("day: net %+.2f (real %+.2f float %+.2f) trades=%d target=$%.2f | %s", d.pnl, d.realized,
                             d.floating, d.n_trades, self.rm.target_usd(), self.rm.can_open()[1] or "ready")
                    self._last_log = time.time()
                time.sleep(self.poll)
            except KeyboardInterrupt:
                log.info("stopped by user (open positions keep their server-side SL/TP)")
                break
            except Exception:
                log.exception("loop error")
                time.sleep(5)
