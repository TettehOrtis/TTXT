"""Risk manager: position sizing, daily profit-target lock, daily loss limit, cool-downs.

Trading day = 17:00 America/New_York -> 17:00 (the FX rollover). All money in account currency (USD).
Sized for small accounts ($100-$500): risk is a % of equity, but never below the broker's min lot.

Accounting is per CLOSED POSITION (all deals of a position_id summed: profit + commission + swap + fee),
so entry-side commission and partial closes are handled correctly.
"""
import json, os, time, datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from zoneinfo import ZoneInfo
import MetaTrader5 as mt5

NY, UTC = ZoneInfo("America/New_York"), dt.timezone.utc
STATE = "logs/state.json"


def trading_day_start(now=None):
    now = now or dt.datetime.now(UTC)
    ny = now.astimezone(NY)
    start = ny.replace(hour=17, minute=0, second=0, microsecond=0)
    if ny < start:
        start -= dt.timedelta(days=1)
    return start.astimezone(UTC)


def atomic_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, default=float)
    for i in range(5):                   # Windows: antivirus/indexer can briefly lock the target file
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2 * (i + 1))
    os.replace(tmp, path)


@dataclass
class RiskConfig:
    risk_pct: float = 1.0            # % of equity risked per trade (to the stop, incl. spread)
    min_risk_usd: float = 0.50
    max_risk_usd: float = 8.0
    min_lot_risk_mult: float = 2.5   # accept min-lot only if its risk <= mult * intended risk
    daily_target_pct: float = 2.0    # lock for the day once net P/L >= this % of day-start balance
    daily_target_min_usd: float = 2.0
    daily_loss_pct: float = 3.0      # lock for the day once net P/L <= -this %
    giveback_frac: float = 0.5       # once REALIZED peak >= 50% of target, lock if realized falls this fraction from the peak
    max_trades_day: int = 12
    max_consec_losses: int = 3
    cooldown_min: int = 30           # pause after max_consec_losses
    loss_cooldown_s: int = 90        # pause after ANY losing trade
    min_equity: float = 20.0
    fixed_lot: float = 0.0           # >0: always trade this lot size


@dataclass
class DayState:
    day_start_ts: float = 0.0
    start_balance: float = 0.0
    realized: float = 0.0
    floating: float = 0.0
    n_trades: int = 0
    consec_losses: int = 0
    last_loss_ts: float = 0.0
    peak_real: float = 0.0           # peak of REALIZED pnl
    locked: str = ""                 # reason if locked for the day
    pause_until: float = 0.0
    last_pause_loss_ts: float = 0.0  # loss that already triggered a pause (prevents re-arming)

    @property
    def pnl(self):
        return self.realized + self.floating


class RiskManager:
    def __init__(self, broker, cfg: RiskConfig):
        self.b, self.cfg = broker, cfg
        self.day = DayState()
        self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self):
        try:
            s = json.load(open(STATE))
            if s.get("day_start_ts") == trading_day_start().timestamp():
                self.day = DayState(**s)
        except Exception:
            pass

    def _save(self):
        atomic_json(STATE, dict(self.day.__dict__))

    # ---------------------------------------------------------------- daily accounting
    def _closed_positions(self, ds):
        """[(close_time, net)] for positions of this bot that CLOSED since day start. None if history unreadable."""
        deals = self.b.deals_since(ds - 3 * 86400)
        if deals is None:
            return None
        groups = defaultdict(list)
        for x in deals:
            groups[x.position_id].append(x)
        out = []
        for pid, g in groups.items():
            outs = [x for x in g if x.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY, mt5.DEAL_ENTRY_INOUT)]
            if not outs:
                continue
            tclose = max(x.time for x in outs)
            if tclose < ds:
                continue
            out.append((tclose, sum(x.profit + x.commission + x.swap + x.fee for x in g)))
        out.sort()
        return out

    def refresh(self):
        d = self.day
        ds = trading_day_start().timestamp()
        acct = self.b.account()
        if acct is None:
            return d
        if d.day_start_ts != ds:                       # new trading day -> reset
            self.day = d = DayState(day_start_ts=ds)
        closed = self._closed_positions(ds)
        pos = self.b.positions()
        if closed is None or pos is None:              # API hiccup: keep last known state, do not act on partial data
            return d
        d.realized = sum(n for _, n in closed)
        d.n_trades = len(closed)
        d.floating = sum(p.profit + p.swap for p in pos)
        if d.start_balance == 0:
            d.start_balance = acct.balance - d.realized
        d.consec_losses = 0
        for t, n in reversed(closed):                  # trailing streak of losing closes
            if n < 0:
                d.consec_losses += 1
                d.last_loss_ts = max(d.last_loss_ts, t)
            else:
                break
        d.peak_real = max(d.peak_real, d.realized)
        self._evaluate_locks(acct)
        self._save()
        return d

    def target_usd(self):
        return max(self.cfg.daily_target_min_usd, self.day.start_balance * self.cfg.daily_target_pct / 100)

    def loss_limit_usd(self):
        return self.day.start_balance * self.cfg.daily_loss_pct / 100

    def _evaluate_locks(self, acct):
        d, c = self.day, self.cfg
        if d.locked:
            return
        if acct.equity < c.min_equity:
            d.locked = f"equity {acct.equity:.2f} < floor {c.min_equity}"
        elif d.pnl >= self.target_usd():
            d.locked = f"TARGET HIT +{d.pnl:.2f} >= {self.target_usd():.2f}"
        elif d.pnl <= -self.loss_limit_usd():
            d.locked = f"DAILY LOSS {d.pnl:.2f} <= -{self.loss_limit_usd():.2f}"
        elif d.peak_real >= 0.5 * self.target_usd() and d.realized <= d.peak_real * (1 - c.giveback_frac):
            d.locked = f"GIVEBACK realized peak {d.peak_real:.2f} -> {d.realized:.2f}"
        elif d.n_trades >= c.max_trades_day:
            d.locked = f"max trades {d.n_trades}"
        # pause after a losing streak - armed once per streak-ending loss, never re-armed by the same loss
        if d.consec_losses >= c.max_consec_losses and d.last_loss_ts > d.last_pause_loss_ts:
            d.pause_until = time.time() + c.cooldown_min * 60
            d.last_pause_loss_ts = d.last_loss_ts

    def can_open(self):
        d, c = self.day, self.cfg
        if d.locked:
            return False, d.locked
        now = time.time()
        if now < d.pause_until:
            return False, f"paused {int(d.pause_until - now)}s (consec losses)"
        if d.last_loss_ts and now - d.last_loss_ts < c.loss_cooldown_s:
            return False, "loss cooldown"
        return True, ""

    # ---------------------------------------------------------------- sizing
    def size(self, sym, stop_dist):
        """Lots for a worst-case adverse move of `stop_dist` price units (pass sl_dist + spread), or (0, reason)."""
        c = self.cfg
        if c.fixed_lot:
            return self.b.round_lots(sym, c.fixed_lot), f"fixed {c.fixed_lot} lot, risk~${c.fixed_lot * self.b.loss_per_lot(sym, stop_dist):.2f}"
        eq = self.b.account().equity
        risk_usd = min(c.max_risk_usd, max(c.min_risk_usd, eq * c.risk_pct / 100))
        per_lot = self.b.loss_per_lot(sym, stop_dist)
        if per_lot <= 0:
            return 0.0, "bad tick value"
        lots = self.b.round_lots(sym, risk_usd / per_lot)
        vmin = self.b.spec(sym).volume_min
        if lots < vmin:
            if vmin * per_lot <= risk_usd * c.min_lot_risk_mult:
                lots = vmin
            else:
                return 0.0, f"min lot risks ${vmin * per_lot:.2f} > {c.min_lot_risk_mult}x target ${risk_usd:.2f}"
        return lots, f"risk~${lots * per_lot:.2f}"
