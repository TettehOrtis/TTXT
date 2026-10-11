"""DEMO forward-test executor for the frozen canonical SMC REF strategy (RECON canonical = BT6b REF + literal OB).

  .venv/Scripts/python.exe smc_bot/live/ref_trader.py --once              # one dry-run pass: state + would-send requests
  .venv/Scripts/python.exe smc_bot/live/ref_trader.py --once --example    # + the exact request for the latest REF order
  .venv/Scripts/python.exe smc_bot/live/ref_trader.py                     # DRY-RUN loop (default: never calls order_send)
  .venv/Scripts/python.exe smc_bot/live/ref_trader.py --live              # trade the Zero DEMO (magic 770077)

Signals: live/ref_engine.py through ref_scanner.scan_pair (the exact path with backtest parity). On every new CLOSED M5
bar per pair (9 pairs, EURGBP skipped) the canonical build_orders runs on the rolling window; an order with
t_signal == T is a new REF signal -> pending BUY/SELL LIMIT at entry with SL and TP attached, broker-side expiry
(ORDER_TIME_SPECIFIED) at t_signal + 60 min, plus our own cancel as a backup.

Backtest semantics mirrored (bt/common.simulate): see README.md "Deviations" for everything that cannot be identical.
  * BACKTEST TWIN: every REF signal (taken, skipped, missed) is replayed through common.simulate itself on the live MT5
    M1 bars (common.bars is swapped in memory for the call, nothing on disk is edited). The twin decides the backtest's
    one-position-per-pair rule (incl. the busy time of an unfilled limit) and logs what the backtest would have done
    with each signal (twin.csv) - the yardstick for the live fills.
  * limit active from t_signal to expire; marketable at send time -> market order (simulate fills at the better price);
    TP traded while the limit is still pending -> cancel (simulate: not_filled); price already beyond SL -> skip.
  * SL/TP are absolute levels (never re-anchored to the fill); flat at Fri 20:45 UTC and before known holiday closures;
    max hold 10 days (never binding: weekend flat).

Safety: demo-only (account.trade_mode must be DEMO and login == Z_LOGIN), attaches to the running terminal (never logs
in again unless nothing is running), only magic 770077 + comment "REF-..." orders/positions are ever touched,
state is written BEFORE every order_send, unknown outcomes are reconciled (comment / field match against orders,
positions and history) and resent at most once after a grace period with confirmed absence.
"""
import os, sys, re, json, time, math, argparse, logging, datetime as dt
from contextlib import contextmanager
from collections import Counter, defaultdict
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ref_engine as E                                         # noqa: E402  canonical pipeline (import only)
import ref_scanner as S                                        # noqa: E402  read-only data plumbing (import only)
from ref_engine import R, B, common                            # noqa: E402

_cwd = os.getcwd()
import bt8_ml as ML                                            # noqa: E402  BT8 candidate + feature code (import only)
os.chdir(_cwd)
ROOT = os.path.dirname(E.SMC)
sys.path.insert(0, ROOT)
import MetaTrader5 as mt5                                      # noqa: E402

log = logging.getLogger("ref_trader")
UTC = dt.timezone.utc
PAIRS = list(E.PAIRS9)
OUT = os.path.join(E.SMC, "results", "FORWARD")
MAGIC = 770077
COMMENT_PREFIX = "REF-"
COMMENT_RE = re.compile(r"^REF-([A-Z]{6})-(\d{10})([BS])")

# statuses of a signal record
NEW, SKIPPED, MISSED, DRY = "NEW", "SKIPPED", "MISSED", "DRY"
SENDING, UNKNOWN, PENDING, FILLED = "SENDING", "UNKNOWN", "PENDING", "FILLED"
CLOSED, CANCELLED, EXPIRED, REJECTED, ABANDONED = "CLOSED", "CANCELLED", "EXPIRED", "REJECTED", "ABANDONED"
LIVE_ACTIVE = (SENDING, UNKNOWN, PENDING, FILLED)

RC_OK = {mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL}
RC_UNKNOWN = {mt5.TRADE_RETCODE_TIMEOUT, mt5.TRADE_RETCODE_CONNECTION}
RC_PRICE = {mt5.TRADE_RETCODE_INVALID_PRICE, mt5.TRADE_RETCODE_PRICE_OFF, mt5.TRADE_RETCODE_PRICE_CHANGED,
            mt5.TRADE_RETCODE_REQUOTE}
EXP_SPECIFIED = 4                                              # SYMBOL_EXPIRATION_SPECIFIED flag
DEAL_REASON = {mt5.DEAL_REASON_SL: "SL", mt5.DEAL_REASON_TP: "TP", mt5.DEAL_REASON_CLIENT: "MANUAL",
               mt5.DEAL_REASON_EXPERT: "BOT", mt5.DEAL_REASON_SO: "STOPOUT"}
DEAL_OUTS = (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY, getattr(mt5, "DEAL_ENTRY_INOUT", 2))

# Known non-weekend market closures (UTC). From the Zero M1 history 2021-2026 (gaps > 6 h, which common.simulate
# flattens before): FX closes 21:58 on 24/31 Dec when they are Mon-Thu (19:58 when Friday); gold also closes early on
# Thanksgiving Friday (18:29), 24 Dec (18:29), Thursday before Good Friday (20:57) and some US holidays (16:44-16:59).
# VERIFY against Exness holiday notices each season; extend with --closures FILE (JSON list of the same 4 fields).
FX8 = tuple(p for p in PAIRS if p != "XAUUSD")
CLOSURES = [
    ("2026-11-27 18:29", "2026-11-29 23:00", ("XAUUSD",), "Thanksgiving Friday, gold early close"),
    ("2026-12-24 18:29", "2026-12-25 23:00", ("XAUUSD",), "Christmas Eve, gold early close"),
    ("2026-12-24 21:58", "2026-12-25 22:05", FX8, "Christmas, FX closed"),
    ("2026-12-31 21:57", "2027-01-01 22:05", tuple(PAIRS), "New Year"),
    ("2027-03-25 20:57", "2027-03-28 22:00", ("XAUUSD",), "Good Friday, gold closed"),
    ("2027-06-18 16:59", "2027-06-20 22:00", ("XAUUSD",), "Juneteenth (observed Fri), gold early close - verify"),
    ("2027-11-26 18:29", "2027-11-28 23:00", ("XAUUSD",), "Thanksgiving Friday, gold early close"),
    ("2027-12-24 18:29", "2027-12-26 23:00", ("XAUUSD",), "Christmas Eve (Fri), gold early close"),
    ("2027-12-24 19:58", "2027-12-26 22:05", FX8, "Christmas Eve (Fri), FX early close"),
    ("2027-12-31 19:58", "2028-01-02 22:05", tuple(PAIRS), "New Year's Eve (Fri), early close"),
]


class Config:
    def __init__(self, **kw):
        self.live = False                    # False = dry-run: everything except order_send
        self.risk_usd = 5.0                  # fixed $ risk per trade (entry -> SL), ~1% of ~$450
        self.min_lot_risk_mult = 5.0         # accept the min lot only if its risk <= 5x risk_usd ($25):
        #   XAUUSD 0.01 lot (1 oz) risks $1 per $1 of stop, so 100-240 pip gold stops force $10-24 of risk.
        #   At 2.0 every gold signal was refused on sizing (2026-10-01, 2026-10-02).
        self.daily_loss_usd = 20.0           # no NEW orders for the rest of the UTC day once realized+floating <= -this
        self.max_open = 4                    # max REF positions + pending orders at once
        self.magic = MAGIC
        self.suffix = "z"
        self.poll_s = 2.0
        self.late_grace_s = 90.0             # a signal first seen later than this after t_signal is "late"
        self.unknown_grace_s = 30.0          # unknown order outcome: wait this long (and 3 clean searches) before a resend
        self.max_resends = 1
        self.market_deviation_pts = 10       # marketable-limit -> market order deviation (points)
        self.close_deviation_pts = 30
        self.holiday_flat_before_min = 10    # flatten this many minutes before a known holiday close
        self.max_hold_days = 10.0            # simulate(max_days=10); never binding because of the weekend flat
        self.cancel_grace_s = 3.0            # own cancel this long after expire if the broker has not expired it
        self.twin_days = 8.0                 # backtest twin replays the last 8 days of signals (always spans a weekend)
        self.status_every_s = 300.0
        self.closures = list(CLOSURES)
        self.out_dir = None
        for k, v in kw.items():
            if not hasattr(self, k):
                raise TypeError(f"unknown config key {k}")
            setattr(self, k, v)

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if k != "closures"}


# ============================================================================================== small helpers
def ep(x):
    """ISO string / Timestamp / datetime64 (naive = UTC) -> epoch seconds (float)."""
    if x is None or (isinstance(x, float) and x != x):
        return None
    return pd.Timestamp(x).value / 1e9 if pd.Timestamp(x).tzinfo is None else pd.Timestamp(x).timestamp()


def iso(t):
    return None if t is None else dt.datetime.fromtimestamp(float(t), UTC).strftime("%Y-%m-%d %H:%M:%S")


def pip(sym):
    return common.pip(sym)


def sid_of(pair, t_signal, side):
    return f"{pair}-{pd.Timestamp(t_signal):%y%m%d%H%M}{'B' if int(side) == 1 else 'S'}"


def comment_of(sid):
    return COMMENT_PREFIX + sid                                  # e.g. REF-EURUSD-2609291035B (22 chars < 31)


def sid_from_comment(c):
    m = COMMENT_RE.match(c or "")
    return f"{m.group(1)}-{m.group(2)}{m.group(3)}" if m else None


def side_of_type(t):
    return 1 if t in (mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_BUY_LIMIT) else -1


def _jd(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(o))
    return str(o)


def _replace_retry(tmp, path):
    for i in range(5):                   # Windows: antivirus/indexer can briefly lock the target (as scalper/risk.py)
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2 * (i + 1))
    os.replace(tmp, path)


def atomic_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, default=_jd)
    _replace_retry(tmp, path)


def atomic_csv(path, df):
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8")
    _replace_retry(tmp, path)


def append_csv(path, rows):
    for i in range(5):
        try:
            return S.append_csv(path, rows)
        except PermissionError:
            time.sleep(0.2 * (i + 1))
    S.append_csv(path, rows)


def size_lot(risk_usd, per_lot, vmin, vstep, vmax, mult):
    """lot = floor(risk / loss_per_lot) to the volume step; min lot only if it risks <= mult x risk.
    Returns (lot, note, risk_usd_of_lot)."""
    if not per_lot or per_lot <= 0 or not np.isfinite(per_lot):
        return 0.0, "bad tick value / stop distance", 0.0
    lot = math.floor(risk_usd / per_lot / vstep + 1e-9) * vstep
    lot = round(min(lot, vmax), 8)
    if lot < vmin - 1e-12:
        if vmin * per_lot <= mult * risk_usd + 1e-9:
            return round(vmin, 8), f"min lot {vmin} (risk ${vmin * per_lot:.2f} <= {mult:g}x ${risk_usd:.2f})", vmin * per_lot
        return 0.0, f"min lot {vmin} would risk ${vmin * per_lot:.2f} > {mult:g}x ${risk_usd:.2f}", vmin * per_lot
    return lot, "", lot * per_lot


@contextmanager
def live_bars(pair, m1):
    """Run common.simulate on live M1 bars: common.bars is swapped IN MEMORY for the duration of the call."""
    orig = common.bars
    common.bars = lambda sym, tf: m1 if (sym == pair and tf == "M1") else orig(sym, tf)
    try:
        yield
    finally:
        common.bars = orig


def resolve_rows(out, t_last, chain=True):
    """simulate() output on data up to t_last (last closed M1 bar open time) -> list of dicts with 'resolved'.
    chain=True (one_at_a_time): every non-skipped row after an unresolved one is unresolved too."""
    res, tail = [], False
    for r in out.to_dict("records"):
        st = r["status"]
        if st == "skipped_busy":
            ok = True                                     # busy_until only grows with more data -> final
        elif tail:
            ok = False
        elif st == "not_filled":
            ok = pd.Timestamp(t_last) >= pd.Timestamp(r["expire"])
        elif st == "filled":
            ok = r.get("exit_reason") != "END"
        elif st == "invalid":
            ok = True
        else:                                             # no_data: order too recent
            ok = False
        if chain and not ok and st != "skipped_busy":
            tail = True
        r["resolved"] = bool(ok)
        res.append(r)
    return res


# ============================================================================================== MT5 gateway
class MT5Gateway:
    """Every terminal call the trader makes. The mock in test_ref_trader.py implements the same methods."""

    def __init__(self, broker, live, magic=MAGIC, offset=0.0):
        self.b, self.live, self.magic, self.offset = broker, live, magic, offset

    def now(self):
        return time.time() + self.offset

    def ensure(self):
        return S.ensure()

    def account(self):
        return mt5.account_info()

    def spec(self, s):
        return self.b.spec(s)

    def tick(self, s):
        return mt5.symbol_info_tick(s)

    def loss_per_lot(self, s, dist):
        return self.b.loss_per_lot(s, dist)

    @staticmethod
    def _ok_empty(r):
        """MT5 returns None both on failure and (for history) on 'nothing found'; last_error 1 = success."""
        if r is not None:
            return list(r)
        return [] if mt5.last_error()[0] == 1 else None

    def positions(self):
        r = mt5.positions_get()
        return None if r is None else [x for x in r if x.magic == self.magic]

    def orders(self):
        r = mt5.orders_get()
        return None if r is None else [x for x in r if x.magic == self.magic]

    def history_orders(self, t0, t1):
        r = self._ok_empty(mt5.history_orders_get(dt.datetime.fromtimestamp(int(t0), UTC),
                                                  dt.datetime.fromtimestamp(int(t1), UTC)))
        return None if r is None else [x for x in r if x.magic == self.magic]

    def history_order(self, ticket):
        r = mt5.history_orders_get(ticket=int(ticket))
        return r[0] if r else None

    def deals_for_position(self, pid):
        return self._ok_empty(mt5.history_deals_get(position=int(pid)))

    def deals_between(self, t0, t1):
        r = self._ok_empty(mt5.history_deals_get(dt.datetime.fromtimestamp(int(t0), UTC),
                                                 dt.datetime.fromtimestamp(int(t1), UTC)))
        return None if r is None else [x for x in r if x.magic == self.magic]

    def deal_position(self, deal):
        d = mt5.history_deals_get(ticket=int(deal)) if deal else None
        return d[0].position_id if d else None

    def m1(self, s, t_from, closed_only=True):
        """M1 bars (time, open, high, low, close, spr in price units) with open time >= t_from. None on failure."""
        n = int(min(40000, max(10, (self.now() - t_from) / 60 + 10)))
        r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M1, 0, n)
        if r is None:
            return None
        t = np.asarray(r["time"], dtype=np.int64)
        keep = t >= t_from - 1e-9
        if closed_only:
            keep &= t + 60 <= self.now()
        r = r[keep]
        pt = self.spec(s).point
        return pd.DataFrame(dict(time=pd.to_datetime(np.asarray(r["time"], np.int64), unit="s"),
                                 open=r["open"].astype(float), high=r["high"].astype(float),
                                 low=r["low"].astype(float), close=r["close"].astype(float),
                                 spr=r["spread"].astype(float) * pt))

    def send(self, req):
        if not self.live:
            raise RuntimeError("dry-run: order_send is disabled")
        return mt5.order_send(req)

    def last_error(self):
        return mt5.last_error()


# ============================================================================================== signal source
def _h4_extra(H4):
    """Extra H4 arrays BT8's features read (as bt6b_pipeline.prepare adds them), on the engine's H4 window."""
    tre, eve, bre, prot = B.structure_external(H4["h"], H4["l"], H4["c"], H4["N"], H4["sh"], H4["sl"])
    out = dict(H4, tr_e=tre, ev_e=eve, br_e=bre, prot=prot)
    out["last_evt"] = B.last_change(H4["ev_i"] != 0)
    out["last_chg"] = B.last_change(np.r_[True, H4["tr_i"][1:] != H4["tr_i"][:-1]])
    out["last_chg_e"] = B.last_change(np.r_[True, tre[1:] != tre[:-1]])
    return out


_H4X = {}


def shadow_candidates(eng, T):
    """BT8 broad candidates (bias + discount/premium recorded as features, not applied) whose t_signal is within the
    engine's 3-day signal lookback, computed by bt8_ml.build_candidates itself on the engine's window at T."""
    sym = eng.sym
    T = np.datetime64(pd.Timestamp(T), "ns")
    ctx = {tf: eng._tf[tf][1] for tf in E.TFS}
    k4 = eng._tf["H4"][0]
    if _H4X.get(sym, (None,))[0] != k4:
        _H4X[sym] = (k4, _h4_extra(ctx["H4"]))
    ctx["H4"] = _H4X[sym][1]
    M30 = dict(ctx["M30"])
    M30["tc"] = np.r_[M30["tc"], T + np.timedelta64(30, "m") * np.arange(1, E.ZONE_LIFE + 105)]   # as ref_engine
    ctx["M30"] = M30
    ctx.update(sym=sym, period="LIVE")
    X = dict(ctx=ctx, zones={E.ZONE_KEY: eng._zones[1]})
    per = "_SHADOW_" + sym
    R.PERIODS[per] = (pd.Timestamp(T - E.LOOKBACK_SIG), pd.Timestamp(T + np.timedelta64(1, "m")))
    R._PD[(sym, per)] = X
    try:
        c = ML.build_candidates(sym, per)
    finally:
        R._PD.pop((sym, per), None); R.PERIODS.pop(per, None)
    return c


class LiveSource:
    """Signals from the scanner's own path (ref_scanner.scan_pair -> RefEngine.evaluate), read-only MT5 calls."""

    def last_T(self, pair):
        T = S.last_closed_m5(pair)
        return None if T is None else pd.Timestamp(T)

    def evaluate(self, pair):
        return S.scan_pair(pair)

    def shadow(self, pair, T):
        eng = S.ENGINES[pair]
        if eng._zones[1] is None or not eng._tf:
            return None
        return shadow_candidates(eng, T)


# ============================================================================================== trader
ORDER_COLS = ["sid", "pair", "side_txt", "status", "status_reason", "order_kind", "t_zone", "t_touch", "t_signal",
              "valid_until", "seen_utc", "late_s", "entry", "sl", "sl_why", "tp", "tp_why", "rr", "stop_pips",
              "entry_why", "poi_kind", "poi_bottom", "poi_top", "killzone", "lot", "risk_usd", "lot_note",
              "expiry_mode", "t_sent", "t_placed", "t_cancelled", "cancel_reason", "t_expired", "t_filled", "fill",
              "t_closed", "close_reason", "ticket", "position_id", "send_attempts", "retcodes", "request", "adopted",
              "twin_status", "twin_fill", "twin_exit_reason", "twin_R"]


class Trader:
    def __init__(self, gw, cfg, source, out_dir=None):
        self.gw, self.cfg, self.src = gw, cfg, source
        self.dir = out_dir or cfg.out_dir or (OUT if cfg.live else os.path.join(OUT, "dry_run"))
        os.makedirs(self.dir, exist_ok=True)
        p = lambda f: os.path.join(self.dir, f)                  # noqa: E731
        self.f_state, self.f_orders, self.f_events = p("state.json"), p("orders.csv"), p("order_events.csv")
        self.f_trades, self.f_skips, self.f_daily = p("trades.csv"), p("skips.csv"), p("daily.csv")
        self.f_twin, self.f_shadow, self.f_shlab = p("twin.csv"), p("shadow_candidates.csv"), p("shadow_labels.csv")
        self.st = self._load()
        self._dirty_orders = True
        self._last_twin = 0.0
        self._last_status = 0.0
        self._pnl_cache = (0.0, (0.0, 0.0))
        self._backoff = {}
        self._warned = set()
        self._eval_backoff = {}

    # ------------------------------------------------------------------------------------ persistence
    def _load(self):
        st = dict(version=1, mode="live" if self.cfg.live else "dry", started=iso(self.gw.now()), signals={},
                  last_T={}, shadow_open={}, shadow_seen={}, day=None, day_block=None)
        if os.path.exists(self.f_state):
            try:
                with open(self.f_state, encoding="utf-8") as fh:
                    st.update(json.load(fh))
                log.info("state loaded: %d signal records", len(st["signals"]))
            except Exception:
                log.exception("state unreadable -> starting empty (live orders are ADOPTED from the terminal)")
        return st

    def save(self):
        now = self.gw.now()
        keep = now - 21 * 86400                                  # prune final records older than 3 weeks
        for sid in [s for s, r in self.st["signals"].items()
                    if r["status"] not in LIVE_ACTIVE and ep(r["t_signal"]) < keep and r.get("twin_final", True)]:
            self.st["signals"].pop(sid)
        self.st["shadow_seen"] = {k: v for k, v in self.st["shadow_seen"].items() if v > now - 5 * 86400}
        self.st["saved_utc"] = iso(now)
        atomic_json(self.f_state, self.st)
        if self._dirty_orders:
            self._write_orders()

    def _write_orders(self):
        old = pd.read_csv(self.f_orders, dtype=str) if os.path.exists(self.f_orders) else pd.DataFrame(columns=["sid"])
        rows = []
        for r in sorted(self.st["signals"].values(), key=lambda r: r["t_signal"]):
            x = {c: r.get(c) for c in ORDER_COLS}
            tw = r.get("twin") or {}
            x.update(twin_status=tw.get("status"), twin_fill=tw.get("fill"), twin_exit_reason=tw.get("exit_reason"),
                     twin_R=tw.get("R"))
            x["retcodes"] = " | ".join(r.get("retcodes", []))
            x["request"] = json.dumps(r.get("request"), default=_jd) if r.get("request") else ""
            rows.append(x)
        new = pd.DataFrame(rows, columns=ORDER_COLS)
        old = old[~old.sid.isin(new.sid)] if len(old) else old     # keep pruned history rows
        atomic_csv(self.f_orders, pd.concat([old.reindex(columns=ORDER_COLS), new.astype(object)], ignore_index=True)
                   if len(old) else new)
        self._dirty_orders = False

    def event(self, rec, ev, detail="", retcode=None):
        self._dirty_orders = True
        append_csv(self.f_events, [dict(utc=iso(self.gw.now()), sid=rec["sid"], pair=rec["pair"], event=ev,
                                        status=rec["status"], retcode=retcode, detail=detail)])
        log.info("%s %s %s %s", rec["sid"], ev, detail, "" if retcode is None else f"rc={retcode}")

    def skip(self, rec, category, reason, status=SKIPPED):
        rec["status"], rec["status_reason"] = status, f"{category}: {reason}"
        append_csv(self.f_skips, [dict(utc=iso(self.gw.now()), sid=rec["sid"], pair=rec["pair"], side=rec["side_txt"],
                                       t_signal=rec["t_signal"], valid_until=rec["valid_until"], entry=rec["entry"],
                                       sl=rec["sl"], tp=rec["tp"], rr=rec.get("rr"), category=category, reason=reason,
                                       mode=self.st["mode"])])
        self.event(rec, "skip", f"{category}: {reason}")

    # ------------------------------------------------------------------------------------ helpers
    def sym(self, pair):
        return pair + self.cfg.suffix

    def recs(self, *status, pair=None):
        return [r for r in self.st["signals"].values() if (not status or r["status"] in status)
                and (pair is None or r["pair"] == pair)]

    def flat_reason(self, t, pair):
        u = dt.datetime.fromtimestamp(t, UTC)
        m = u.hour * 60 + u.minute
        if (u.weekday() == 4 and m >= 20 * 60 + 45) or u.weekday() == 5 or (u.weekday() == 6 and m < 21 * 60):
            return "WEEKEND"
        for c0, c1, syms, note in self.cfg.closures:
            if pair in syms and ep(c0) - 60 * self.cfg.holiday_flat_before_min <= t < ep(c1):
                return f"HOLIDAY ({note}, close {c0} UTC)"
        return None

    def _is_ref(self, x):
        tracked = {r.get("ticket") for r in self.recs()} | {r.get("position_id") for r in self.recs()}
        return x.magic == self.cfg.magic and ((x.comment or "").startswith(COMMENT_PREFIX) or x.ticket in tracked)

    def day_pnl(self, P):
        """(realized net today UTC, floating incl. entry commission) of REF positions, from broker deal history."""
        now = self.gw.now()
        if now - self._pnl_cache[0] < 20:
            return self._pnl_cache[1]
        day0 = math.floor(now / 86400) * 86400
        deals = self.gw.deals_between(day0 - 6 * 86400, now + 86400)
        if deals is None:
            return self._pnl_cache[1]
        tracked = {r.get("position_id") for r in self.recs()} | {r.get("ticket") for r in self.recs()}
        g = defaultdict(list)
        for d in deals:
            g[d.position_id].append(d)
        live = {p.ticket: p for p in P if self._is_ref(p)}
        real = flo = 0.0
        for pid, ds in g.items():
            ins = [d for d in ds if d.entry == mt5.DEAL_ENTRY_IN]
            if not (pid in tracked or any((d.comment or "").startswith(COMMENT_PREFIX) for d in ins)):
                continue
            outs = [d for d in ds if d.entry in DEAL_OUTS]
            if pid in live:
                flo += sum(d.commission + d.fee for d in ds)
            elif outs and max(d.time for d in outs) >= day0:
                real += sum(d.profit + d.commission + d.swap + d.fee for d in ds)
        flo += sum(p.profit + p.swap for p in live.values())
        self._pnl_cache = (now, (real, flo))
        return real, flo

    # ------------------------------------------------------------------------------------ backtest twin
    def twin_run(self, pair, recs):
        """common.simulate(one_at_a_time=True) on live M1 bars for these REF orders -> {sid: row} or None."""
        if not recs:
            return {}
        recs = sorted(recs, key=lambda r: (r["t_signal"], r["sid"]))
        m1 = self.gw.m1(self.sym(pair), ep(recs[0]["t_signal"]) - 60, closed_only=True)
        if m1 is None or len(m1) < 3:
            return None
        o = pd.DataFrame([dict(t_signal=pd.Timestamp(r["t_signal"]), side=int(r["side"]), entry_type="limit",
                               entry=float(r["entry"]), expire=pd.Timestamp(r["valid_until"]), sl=float(r["sl"]),
                               tp=float(r["tp"]), be_at_r=np.nan, max_hold_min=np.nan, sid=r["sid"]) for r in recs])
        with live_bars(pair, m1):
            out = common.simulate(pair, o, one_at_a_time=True, max_days=int(self.cfg.max_hold_days))
        return {r["sid"]: r for r in resolve_rows(out, m1.time.iloc[-1], chain=True)}

    def twin_busy(self, pair, rec):
        """(busy?, detail) per the backtest's one-position-per-pair rule, incl. the busy time of an unfilled limit."""
        t0 = ep(rec["t_signal"]) - self.cfg.twin_days * 86400
        prior = [r for r in self.recs(pair=pair) if ep(r["t_signal"]) >= t0 and r["t_signal"] < rec["t_signal"]]
        if not prior:
            return False, ""
        tw = self.twin_run(pair, prior + [rec])
        if tw is None:
            return None, "backtest twin unavailable (no M1 bars)"
        for r in prior:
            x = tw.get(r["sid"])
            if x and x["status"] != "skipped_busy" and not x["resolved"]:
                what = "open" if x["status"] == "filled" else "pending limit"
                return True, f"backtest: {r['sid']} still {what}"
        me = tw.get(rec["sid"])
        if me and me["status"] == "skipped_busy":
            last = [(r["sid"], tw[r["sid"]]) for r in prior if tw.get(r["sid"], {}).get("status") in ("filled", "not_filled")]
            src = last[-1] if last else ("?", {})
            until = src[1].get("t_exit") if src[1].get("status") == "filled" else src[1].get("expire")
            return True, f"backtest: pair busy until {str(until)[:16]} ({src[0]} {src[1].get('status')})"
        return False, ""

    def update_twins(self, force=False):
        now = self.gw.now()
        if not force and now - self._last_twin < 60:
            return
        self._last_twin = now
        for pair in PAIRS:
            open_ = [r for r in self.recs(pair=pair) if not r.get("twin_final")]
            if not open_:
                continue
            t0 = min(ep(r["t_signal"]) for r in open_) - self.cfg.twin_days * 86400
            recs = [r for r in self.recs(pair=pair) if ep(r["t_signal"]) >= t0]
            try:
                tw = self.twin_run(pair, recs)
            except Exception:
                log.exception("twin %s", pair)
                continue
            if tw is None:
                continue
            for r in recs:
                x = tw.get(r["sid"])
                if x is None or r.get("twin_final"):
                    continue
                keep = {k: x.get(k) for k in ("status", "t_fill", "fill", "t_exit", "exit", "exit_reason", "risk_pips",
                                              "gross_pips", "net_pips", "R", "mfe_R", "mae_R", "hold_h")}
                r["twin"] = {k: (None if v is None or v is pd.NaT or (isinstance(v, float) and v != v) else
                                 (str(v) if isinstance(v, pd.Timestamp) else v)) for k, v in keep.items()}
                r["twin_final"] = bool(x["resolved"])
                if x["resolved"]:
                    self._dirty_orders = True
                    append_csv(self.f_twin, [dict(sid=r["sid"], pair=pair, side=r["side_txt"], t_signal=r["t_signal"],
                                                  live_status=r["status"], entry=r["entry"], sl=r["sl"], tp=r["tp"],
                                                  **{f"bt_{k}": v for k, v in r["twin"].items()})])

    # ------------------------------------------------------------------------------------ main cycle
    def cycle(self):
        now = self.gw.now()
        if not self.gw.ensure():
            log.warning("terminal disconnected; waiting (orders keep their broker-side SL/TP/expiry)")
            return
        P, O = self.gw.positions(), self.gw.orders()
        if P is None or O is None:
            log.warning("positions/orders unreadable this cycle")
            return
        self.reconcile(P, O)
        P, O = self.gw.positions(), self.gw.orders()
        if P is None or O is None:
            return
        self.time_rules(P, O)
        for pair in PAIRS:
            try:
                T = self.src.last_T(pair)
                if T is not None and str(T) != self.st["last_T"].get(pair) and now >= self._eval_backoff.get(pair, 0):
                    self.on_bar(pair)
            except Exception:
                log.exception("bar %s", pair)
        self.update_twins()
        self.daily(P)
        if now - self._last_status > self.cfg.status_every_s:
            self._last_status = now
            log.info(self.status_line(P, O))
        self.save()

    # ------------------------------------------------------------------------------------ signals
    def on_bar(self, pair):
        st = self.src.evaluate(pair)
        T = st.get("t_bar_close")
        if st.get("stage", -1) < 0 or not T:
            log.warning("%s: no evaluation (%s) - retry in 60 s", pair, st.get("detail"))
            self._eval_backoff[pair] = self.gw.now() + 60
            return
        self.st["last_T"][pair] = str(pd.Timestamp(T))
        for w in st.get("warnings", []):
            if "mismatch" in w:
                log.warning("%s: %s", pair, w)
        full = {(s["t_signal"], int(s["side"])): s for s in st.get("signals_active", []) + st.get("signals_new", [])}
        for o in st.get("orders_recent", []):
            s = full.get((str(pd.Timestamp(o["t_signal"]))[:19], int(o["side"])), {})
            rec = self._new_rec(pair, {**o, **s}, st)
            if rec["sid"] in self.st["signals"]:
                continue
            self.st["signals"][rec["sid"]] = rec
            self.handle_signal(rec)
        try:
            self.shadow(pair, T, st)
        except Exception:
            log.exception("shadow %s", pair)

    def _new_rec(self, pair, s, st):
        now = self.gw.now()
        side = int(s["side"])
        t_sig = str(pd.Timestamp(s["t_signal"]))[:19]
        vu = s.get("valid_until") or str(pd.Timestamp(t_sig) + pd.Timedelta(minutes=E.LIMIT_MIN))[:19]
        risk = (s["entry"] - s["sl"]) * side
        sid = sid_of(pair, t_sig, side)
        return dict(sid=sid, comment=comment_of(sid), pair=pair, side=side, side_txt="BUY" if side == 1 else "SELL",
                    t_zone=s.get("poi_created"), t_touch=s.get("t_touch"), t_signal=t_sig, valid_until=vu,
                    seen_utc=iso(now), late_s=round(now - ep(t_sig)), t_bar_close=st.get("t_bar_close"),
                    entry=float(s["entry"]), sl=float(s["sl"]), tp=float(s["tp"]), rr=float(s["rr"]),
                    stop_pips=round(risk / pip(pair), 2), entry_why=s.get("entry_why"), sl_why=s.get("sl_why"),
                    tp_why=s.get("tp_why"), poi_kind=s.get("poi_kind"), poi_bottom=s.get("poi_bottom"),
                    poi_top=s.get("poi_top"), entry_src=s.get("entry_src"), killzone=s.get("killzone", s.get("session")),
                    bias=(st.get("bias") or {}).get("txt"), status=NEW, status_reason="", retcodes=[],
                    send_attempts=0, twin_final=False)

    def handle_signal(self, rec):
        now = self.gw.now()
        pair = rec["pair"]
        texp = ep(rec["valid_until"])
        if now >= texp:
            return self.skip(rec, "missed", "limit already expired when first seen (trader not running at t_signal)",
                             MISSED)
        why = self.flat_reason(now, pair)
        if why:
            return self.skip(rec, "market_closing", why)
        if rec["late_s"] > self.cfg.late_grace_s:
            done = self._late_outcome(rec)
            if done:
                return self.skip(rec, "late", done, MISSED)
        busy, detail = self.twin_busy(pair, rec)
        if busy:
            return self.skip(rec, "backtest_busy", detail)
        if busy is None:
            log.warning("%s: %s -> live busy check only", rec["sid"], detail)
        P, O = self.gw.positions(), self.gw.orders()
        if P is None or O is None:
            return self.skip(rec, "api", "positions/orders unreadable at signal time")
        s = self.sym(pair)
        mine = [x for x in list(P) + list(O) if x.symbol == s and self._is_ref(x)]
        act = [r for r in self.recs(SENDING, UNKNOWN, pair=pair) if r["sid"] != rec["sid"]]
        if mine or act:
            what = ", ".join([f"{'position' if hasattr(x, 'price_current') and not hasattr(x, 'time_setup') else 'order'}"
                              f" {x.ticket}" for x in mine] + [f"{r['sid']} {r['status']}" for r in act])
            return self.skip(rec, "live_busy", f"our {what} on {pair} (live one-position-per-pair)")
        if self.cfg.live:
            real, flo = self.day_pnl(P)
            if self.st.get("day_block") == iso(math.floor(now / 86400) * 86400)[:10] or real + flo <= -self.cfg.daily_loss_usd:
                self.st["day_block"] = iso(math.floor(now / 86400) * 86400)[:10]
                return self.skip(rec, "daily_loss", f"day P/L {real + flo:+.2f} (realized {real:+.2f}, floating "
                                                    f"{flo:+.2f}) <= -{self.cfg.daily_loss_usd:.2f}")
            n_open = len([x for x in list(P) + list(O) if self._is_ref(x)]) + len(self.recs(SENDING, UNKNOWN))
        else:
            n_open = len([r for r in self.recs(DRY) if r["sid"] != rec["sid"] and not r.get("twin_final")
                          and (r.get("twin") or {}).get("status") != "skipped_busy"])
        if n_open >= self.cfg.max_open:
            return self.skip(rec, "max_open", f"{n_open} REF positions/orders open >= max {self.cfg.max_open}")
        self.place(rec)

    def _late_outcome(self, rec):
        """A late signal is placed only if the backtest's limit would still be pending (no fill, TP not traded)."""
        s, d = self.sym(rec["pair"]), rec["side"]
        m = self.gw.m1(s, ep(rec["t_signal"]), closed_only=False)
        if m is None:
            return "late and M1 bars unavailable"
        if not len(m):
            return None
        hit = (m.low + m.spr <= rec["entry"]) if d == 1 else (m.high >= rec["entry"])
        ran = (m.high >= rec["tp"]) if d == 1 else (m.low + m.spr <= rec["tp"])
        fi = int(np.argmax(hit.values)) if hit.any() else -1
        ri = int(np.argmax(ran.values)) if ran.any() else -1
        if fi >= 0 and not (0 <= ri < fi):
            return f"late {rec['late_s']}s: backtest limit already filled at {m.time.iloc[fi]}"
        if ri >= 0:
            return f"late {rec['late_s']}s: TP traded at {m.time.iloc[ri]} before a fill (backtest not_filled)"
        return None

    # ------------------------------------------------------------------------------------ order placement
    def build_request(self, rec, tick, spec, lot, market):
        d, s = rec["side"], self.sym(rec["pair"])
        rd = lambda x: round(float(x), spec.digits)               # noqa: E731
        if market:
            return dict(action=mt5.TRADE_ACTION_DEAL, symbol=s, volume=lot,
                        type=mt5.ORDER_TYPE_BUY if d == 1 else mt5.ORDER_TYPE_SELL,
                        price=rd(tick.ask if d == 1 else tick.bid), sl=rd(rec["sl"]), tp=rd(rec["tp"]),
                        deviation=self.cfg.market_deviation_pts, magic=self.cfg.magic, comment=rec["comment"],
                        type_time=mt5.ORDER_TIME_GTC, type_filling=self.fillings(spec, market=True)[0])
        req = dict(action=mt5.TRADE_ACTION_PENDING, symbol=s, volume=lot,
                   type=mt5.ORDER_TYPE_BUY_LIMIT if d == 1 else mt5.ORDER_TYPE_SELL_LIMIT,
                   price=rd(rec["entry"]), sl=rd(rec["sl"]), tp=rd(rec["tp"]), deviation=0, magic=self.cfg.magic,
                   comment=rec["comment"], type_filling=self.fillings(spec, market=False)[0])
        if spec.expiration_mode & EXP_SPECIFIED:
            req.update(type_time=mt5.ORDER_TIME_SPECIFIED, expiration=int(round(ep(rec["valid_until"]) + self.gw.offset)))
        else:
            req.update(type_time=mt5.ORDER_TIME_GTC)
        return req

    @staticmethod
    def fillings(spec, market):
        fm = spec.filling_mode
        ioc_fok = ([mt5.ORDER_FILLING_IOC] if fm & 2 else []) + ([mt5.ORDER_FILLING_FOK] if fm & 1 else [])
        if market:
            return ioc_fok or [mt5.ORDER_FILLING_RETURN]
        return [mt5.ORDER_FILLING_RETURN] + ioc_fok             # pending: RETURN first, others on 10030 (rejected)

    def place(self, rec):
        now = self.gw.now()
        pair, d = rec["pair"], rec["side"]
        s = self.sym(pair)
        tick, spec = self.gw.tick(s), self.gw.spec(s)
        if tick is None or not tick.bid or not tick.ask:
            return self.skip(rec, "api", "no tick")
        px = tick.ask if d == 1 else tick.bid                    # where a buy/sell executes now
        if (px - rec["sl"]) * d <= 0:
            return self.skip(rec, "gap", f"price {px} already beyond SL {rec['sl']} (backtest: GAP_SL, stopped at once "
                                         f"for -(slip+commission))")
        if ((tick.bid if d == 1 else tick.ask) - rec["tp"]) * d >= 0:
            return self.skip(rec, "tp_reached", f"TP {rec['tp']} already traded before a fill (backtest: not_filled)")
        market = (px - rec["entry"]) * d <= 0                    # limit would be marketable -> simulate fills at open
        ref_px = px if market else rec["entry"]
        per_lot = self.gw.loss_per_lot(s, abs(ref_px - rec["sl"]))
        lot, note, risk = size_lot(self.cfg.risk_usd, per_lot, spec.volume_min, spec.volume_step, spec.volume_max,
                                   self.cfg.min_lot_risk_mult)
        if lot <= 0:
            return self.skip(rec, "sizing", note)
        if not market and ep(rec["valid_until"]) - now < 10:
            return self.skip(rec, "missed", "less than 10 s of validity left")
        req = self.build_request(rec, tick, spec, lot, market)
        rec.update(lot=lot, risk_usd=round(risk, 2), lot_note=note, order_kind="market" if market else "limit",
                   request=req, expiry_mode="broker" if req.get("type_time") == mt5.ORDER_TIME_SPECIFIED else "self",
                   px_at_send=px)
        if not self.cfg.live:
            rec["status"], rec["status_reason"] = DRY, "dry-run: order_send not called"
            self.event(rec, "dry_run", "would send " + json.dumps(req, default=_jd))
            print(f"DRY-RUN {rec['sid']} would send: {json.dumps(req, default=_jd)}", flush=True)
            return
        self._send(rec, req, market)

    def _send(self, rec, req, market):
        fills = self.fillings(self.gw.spec(req["symbol"]), market)
        i = fills.index(req["type_filling"]) if req["type_filling"] in fills else 0
        for attempt in range(6):
            rec["status"] = SENDING
            rec["t_sent"] = iso(self.gw.now())
            rec["send_attempts"] = rec.get("send_attempts", 0) + 1
            rec["request"] = req
            self.save()                                          # persisted BEFORE order_send (crash -> reconcile)
            try:
                r = self.gw.send(dict(req))
            except Exception as ex:
                r = None
                log.exception("order_send raised: %r", ex)
            if r is None:
                rec["status"], rec["unknown_since"], rec["unknown_checks"] = UNKNOWN, self.gw.now(), 0
                rec["unknown_sends"] = rec.get("unknown_sends", 0) + 1
                rec["retcodes"].append(f"{rec['t_sent']} None {self.gw.last_error()}")
                return self.event(rec, "send_unknown", f"order_send returned None {self.gw.last_error()}")
            rc = r.retcode
            rec["retcodes"].append(f"{rec['t_sent']} {rc} {getattr(r, 'comment', '')}")
            if rc in RC_OK:
                rec["ticket"] = int(r.order) if r.order else None
                if market:
                    rec["status"], rec["t_filled"], rec["fill"] = FILLED, iso(self.gw.now()), float(r.price)
                    rec["position_id"] = self.gw.deal_position(r.deal) or rec["ticket"]
                    return self.event(rec, "filled_market", f"ticket {rec['ticket']} @ {r.price} vol {r.volume}", rc)
                rec["status"], rec["t_placed"] = PENDING, iso(self.gw.now())
                return self.event(rec, "placed", f"ticket {rec['ticket']}", rc)
            if rc in RC_UNKNOWN:
                rec["status"], rec["unknown_since"], rec["unknown_checks"] = UNKNOWN, self.gw.now(), 0
                rec["unknown_sends"] = rec.get("unknown_sends", 0) + 1
                return self.event(rec, "send_unknown", getattr(r, "comment", ""), rc)
            if rc == mt5.TRADE_RETCODE_INVALID_FILL and i + 1 < len(fills):
                i += 1
                req = dict(req, type_filling=fills[i])
                self.event(rec, "retry_filling", f"filling {fills[i - 1]} rejected -> {fills[i]}", rc)
                continue
            if rc == mt5.TRADE_RETCODE_INVALID_EXPIRATION and req.get("type_time") == mt5.ORDER_TIME_SPECIFIED:
                req = {k: v for k, v in req.items() if k != "expiration"}
                req["type_time"] = mt5.ORDER_TIME_GTC
                rec["expiry_mode"] = "self"
                self.event(rec, "retry_gtc", "broker refused ORDER_TIME_SPECIFIED -> GTC + own cancel", rc)
                continue
            if rc in RC_PRICE and not rec.get("price_retried"):
                rec["status"], rec["price_retried"] = NEW, True
                self.event(rec, "retry_price", "price moved; re-deciding once", rc)
                return self.place(rec)
            rec["status"], rec["status_reason"] = REJECTED, f"retcode {rc} {getattr(r, 'comment', '')}"
            return self.event(rec, "rejected", getattr(r, "comment", ""), rc)
        rec["status"], rec["status_reason"] = REJECTED, "send attempts exhausted"
        self.event(rec, "rejected", "send attempts exhausted")

    # ------------------------------------------------------------------------------------ reconciliation
    def _match(self, rec, x):
        c = getattr(x, "comment", "") or ""
        if c and (c == rec["comment"] or (len(c) >= 12 and rec["comment"].startswith(c))):
            return True
        req = rec.get("request") or {}
        if x.symbol != self.sym(rec["pair"]) or side_of_type(x.type if hasattr(x, "time_setup") else
                                                             (mt5.ORDER_TYPE_BUY if x.type == 0 else mt5.ORDER_TYPE_SELL)) != rec["side"]:
            return False
        vol = getattr(x, "volume_initial", None) or getattr(x, "volume", None)
        t_set = getattr(x, "time_setup", None) or getattr(x, "time", 0)
        pt = self.gw.spec(x.symbol).point
        px_ok = (rec.get("order_kind") == "market" or not hasattr(x, "time_setup")
                 or abs(getattr(x, "price_open", 0) - req.get("price", -1)) <= pt / 2)
        return (vol is not None and abs(vol - rec.get("lot", -1)) < 1e-9 and px_ok
                and t_set >= ep(rec.get("t_sent") or rec["t_signal"]) - 10)

    def _find(self, rec, P, O):
        """('order'|'position'|'history', obj) if the order behind rec exists anywhere; (None, None) if confirmed absent;
        ('error', None) if an API call failed (absence NOT confirmed)."""
        for o in O:
            if self._match(rec, o):
                return "order", o
        for p in P:
            if self._match(rec, p):
                return "position", p
        t0 = ep(rec.get("t_sent") or rec["t_signal"]) - 120
        h = self.gw.history_orders(t0, self.gw.now() + 3600)
        if h is None:
            return "error", None
        for o in h:
            if self._match(rec, o):
                return "history", o
        return None, None

    def reconcile(self, P, O):
        now = self.gw.now()
        byo = {o.ticket: o for o in O}
        byp = {p.ticket: p for p in P}
        # 1) orders whose send outcome is unknown (timeout / None / crash between save and response)
        for rec in self.recs(SENDING, UNKNOWN):
            kind, x = self._find(rec, P, O)
            if kind == "error":
                continue
            if kind is not None:
                self._adopt_found(rec, kind, x)
                continue
            if rec["status"] == SENDING:
                rec["status"], rec["unknown_since"], rec["unknown_checks"] = UNKNOWN, now, 0
            rec["unknown_checks"] = rec.get("unknown_checks", 0) + 1
            if now - rec.get("unknown_since", now) >= self.cfg.unknown_grace_s and rec["unknown_checks"] >= 3:
                if rec.get("unknown_sends", 1) <= self.cfg.max_resends and now < ep(rec["valid_until"]) - 10 \
                        and not self.flat_reason(now, rec["pair"]):
                    rec["status"] = NEW
                    self.event(rec, "resend", f"not found after {now - rec['unknown_since']:.0f}s and "
                                              f"{rec['unknown_checks']} clean searches -> resending once")
                    self.place(rec)
                else:
                    self.skip(rec, "abandoned", "order outcome unknown and never found (not resent)", ABANDONED)
        # 2) pending orders
        for rec in self.recs(PENDING):
            if rec.get("ticket") in byo:
                continue
            h = self.gw.history_order(rec["ticket"]) if rec.get("ticket") else None
            if h is None:
                pos = byp.get(rec.get("ticket"))
                if pos is not None:
                    self._to_filled(rec, pos)
                    continue
                rec.setdefault("gone_since", now)
                if now - rec["gone_since"] > 300:
                    self.skip(rec, "abandoned", "pending order vanished without history", ABANDONED)
                continue
            self._from_history(rec, h, byp)
        # 3) open positions
        for rec in self.recs(FILLED):
            pid = rec.get("position_id") or rec.get("ticket")
            if pid in byp:
                self._protect(rec, byp[pid])
                continue
            self._log_trade(rec)
        # 4) orphans: our magic + REF comment, not tracked (restart without state, or a lost record)
        tracked = {r.get("ticket") for r in self.recs(*LIVE_ACTIVE)} | {r.get("position_id") for r in self.recs(*LIVE_ACTIVE)}
        for x, kind in [(o, "order") for o in O] + [(p, "position") for p in P]:
            if x.ticket in tracked:
                continue
            sid = sid_from_comment(x.comment)
            if sid is not None and not self.cfg.live:
                if (kind, x.ticket) not in self._warned:
                    self._warned.add((kind, x.ticket))
                    log.warning("live REF %s %s (%s) present - dry-run does not adopt it (counts as live busy)",
                                kind, x.ticket, x.comment)
                continue
            if sid is None:
                if (kind, x.ticket) not in self._warned:
                    self._warned.add((kind, x.ticket))
                    log.warning("%s %s has magic %s but no REF comment (%r) -> not touched", kind, x.ticket,
                                self.cfg.magic, x.comment)
                continue
            self._adopt_orphan(sid, kind, x)

    def _adopt_found(self, rec, kind, x):
        if kind == "order":
            rec.update(status=PENDING, ticket=int(x.ticket), t_placed=iso(x.time_setup))
            self.event(rec, "reconciled", f"unknown send -> pending order {x.ticket} found")
        elif kind == "position":
            rec.update(ticket=int(x.ticket), position_id=int(x.ticket))
            self._to_filled(rec, x)
            self.event(rec, "reconciled", f"unknown send -> position {x.ticket} found")
        else:
            rec.update(ticket=int(x.ticket))
            self.event(rec, "reconciled", f"unknown send -> history order {x.ticket} state {x.state}")
            self._from_history(rec, x, {})

    def _from_history(self, rec, h, byp):
        st = h.state
        t_done = iso(getattr(h, "time_done", 0) or self.gw.now())
        if st in (mt5.ORDER_STATE_FILLED, getattr(mt5, "ORDER_STATE_PARTIAL", 3)):
            pid = getattr(h, "position_id", 0) or rec.get("ticket")
            pos = byp.get(pid)
            rec["position_id"] = pid
            if pos is not None:
                self._to_filled(rec, pos)
            else:                                            # filled and already closed (e.g. while we were down)
                rec["status"], rec["t_filled"] = FILLED, rec.get("t_filled") or t_done
                self.event(rec, "filled", f"order {rec['ticket']} filled (position already closed)")
                self._log_trade(rec)
        elif st == mt5.ORDER_STATE_CANCELED:
            rec.update(status=CANCELLED, t_cancelled=t_done, cancel_reason=rec.get("cancel_reason") or "EXTERNAL")
            rec["status_reason"] = rec["cancel_reason"]
            self.event(rec, "cancelled", rec["cancel_reason"])
        elif st == mt5.ORDER_STATE_EXPIRED:
            rec.update(status=EXPIRED, t_expired=t_done, status_reason="broker expiry (unfilled)")
            self.event(rec, "expired", "broker-side expiry")
        elif st == mt5.ORDER_STATE_REJECTED:
            rec.update(status=REJECTED, status_reason="rejected by server")
            self.event(rec, "rejected", "history state REJECTED")
        # other states (placed / request_*) -> still in flight, next cycle

    def _to_filled(self, rec, pos):
        rec["position_id"] = int(pos.ticket)
        if rec["status"] != FILLED:
            rec.update(status=FILLED, t_filled=iso(pos.time), fill=float(pos.price_open))
            self.event(rec, "filled", f"position {pos.ticket} @ {pos.price_open} vol {pos.volume}")
        self._protect(rec, pos)

    def _protect(self, rec, pos):
        """REF SL/TP are absolute levels: make sure the position carries exactly them (re-attach if missing/changed)."""
        spec = self.gw.spec(pos.symbol)
        want_sl, want_tp = round(rec["sl"], spec.digits), round(rec["tp"], spec.digits)
        if abs(pos.sl - want_sl) <= spec.point / 2 and abs(pos.tp - want_tp) <= spec.point / 2:
            return
        if self.gw.now() < self._backoff.get(pos.ticket, 0):
            return
        req = dict(action=mt5.TRADE_ACTION_SLTP, symbol=pos.symbol, position=pos.ticket, sl=want_sl, tp=want_tp,
                   magic=self.cfg.magic)
        r = self.gw.send(req) if self.cfg.live else None
        ok = r is not None and r.retcode in RC_OK
        self.event(rec, "reattach_sltp", f"pos {pos.ticket} had sl={pos.sl} tp={pos.tp} -> {want_sl}/{want_tp} "
                                         f"{'ok' if ok else 'FAILED'}", getattr(r, "retcode", None))
        if not ok:
            self._backoff[pos.ticket] = self.gw.now() + 10
            if not pos.sl:
                log.error("position %s has NO stop and SL could not be attached -> closing", pos.ticket)
                self.close(rec, pos, "NO_STOP")

    def _adopt_orphan(self, sid, kind, x):
        pair = sid[:6]
        rec = self.st["signals"].get(sid)
        if rec is None:
            t_sig = pd.Timestamp(dt.datetime.strptime(sid[7:17], "%y%m%d%H%M"))
            side = side_of_type(x.type) if kind == "order" else (1 if x.type == mt5.POSITION_TYPE_BUY else -1)
            vu = iso(x.time_expiration) if kind == "order" and getattr(x, "time_expiration", 0) else \
                str(t_sig + pd.Timedelta(minutes=E.LIMIT_MIN))[:19]
            rec = self._new_rec(pair, dict(t_signal=t_sig, side=side, entry=x.price_open, sl=x.sl, tp=x.tp,
                                           rr=abs(x.tp - x.price_open) / max(abs(x.price_open - x.sl), 1e-12),
                                           valid_until=vu), {})
            rec["lot"] = getattr(x, "volume_initial", None) or x.volume
            self.st["signals"][sid] = rec
        rec["adopted"] = True
        rec["ticket"] = int(x.ticket)
        if kind == "order":
            rec.update(status=PENDING, t_placed=iso(x.time_setup))
            self.event(rec, "adopted", f"pending order {x.ticket} (magic+comment) re-attached to state")
        else:
            rec["position_id"] = int(x.ticket)
            if rec["status"] != FILLED:
                rec.update(status=FILLED, t_filled=iso(x.time), fill=float(x.price_open))
            self.event(rec, "adopted", f"position {x.ticket} (magic+comment) re-attached to state")
            if not x.sl and not rec.get("sl"):
                self.close(rec, x, "NO_STOP")

    # ------------------------------------------------------------------------------------ time rules
    def time_rules(self, P, O):
        now = self.gw.now()
        byo = {o.ticket: o for o in O}
        byp = {p.ticket: p for p in P}
        for rec in self.recs(PENDING):
            o = byo.get(rec.get("ticket"))
            if o is None:
                continue
            why = self.flat_reason(now, rec["pair"])
            if why:
                self.cancel(rec, o, why.split(" ")[0])
            elif now >= ep(rec["valid_until"]) + (self.cfg.cancel_grace_s if rec.get("expiry_mode") == "broker" else 0):
                self.cancel(rec, o, "EXPIRY_SELF")
            else:
                ran = self._tp_traded(rec)
                if ran:
                    self.cancel(rec, o, "TP_FIRST", ran)
        for rec in self.recs(FILLED):
            p = byp.get(rec.get("position_id") or rec.get("ticket"))
            if p is None:
                continue
            why = self.flat_reason(now, rec["pair"])
            if why:
                self.close(rec, p, why.split(" ")[0])
            elif now - (ep(rec.get("t_filled")) or now) >= self.cfg.max_hold_days * 86400:
                self.close(rec, p, "TIME")

    def _tp_traded(self, rec):
        """simulate(cancel_limit_if_tp_first): TP traded (bid high >= TP buy / ask low <= TP sell) while still pending."""
        s, d = self.sym(rec["pair"]), rec["side"]
        t = self.gw.tick(s)
        if t is not None and ((t.bid >= rec["tp"]) if d == 1 else (t.ask <= rec["tp"])):
            return f"tick {t.bid if d == 1 else t.ask}"
        m = self.gw.m1(s, ep(rec["t_signal"]), closed_only=False)
        if m is not None and len(m):
            ran = (m.high >= rec["tp"]) if d == 1 else (m.low + m.spr <= rec["tp"])
            if ran.any():
                return f"M1 bar {m.time[ran.values].iloc[0]}"
        return None

    def cancel(self, rec, o, why, detail=""):
        if self.gw.now() < self._backoff.get(o.ticket, 0):
            return
        rec["cancel_reason"] = why
        if not self.cfg.live:
            return
        self.save()
        req = dict(action=mt5.TRADE_ACTION_REMOVE, order=int(o.ticket), magic=self.cfg.magic, comment=rec["comment"])
        r = self.gw.send(req)
        rc = getattr(r, "retcode", None)
        if r is not None and rc in RC_OK:
            rec.update(status=CANCELLED, t_cancelled=iso(self.gw.now()), status_reason=why)
            self.event(rec, "cancelled", f"{why} {detail}".strip(), rc)
        else:                                                    # filled meanwhile / unknown -> reconcile next cycle
            self._backoff[o.ticket] = self.gw.now() + 5
            self.event(rec, "cancel_failed", f"{why}: {getattr(r, 'comment', self.gw.last_error())}", rc)

    def close(self, rec, p, why):
        if self.gw.now() < self._backoff.get(p.ticket, 0):
            return
        rec["close_reason"] = why
        if not self.cfg.live:
            return
        self.save()
        t = self.gw.tick(p.symbol)
        buy = p.type == mt5.POSITION_TYPE_BUY
        spec = self.gw.spec(p.symbol)
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=p.symbol, volume=p.volume,
                   type=mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY, position=int(p.ticket),
                   price=(t.bid if buy else t.ask) if t else 0.0, deviation=self.cfg.close_deviation_pts,
                   magic=self.cfg.magic, comment=f"REF-close {why}"[:31], type_time=mt5.ORDER_TIME_GTC,
                   type_filling=self.fillings(spec, market=True)[0])
        r = self.gw.send(req)
        rc = getattr(r, "retcode", None)
        ok = r is not None and rc in RC_OK
        if not ok:
            self._backoff[p.ticket] = self.gw.now() + 5
        self.event(rec, "close_sent" if ok else "close_failed", f"{why} pos {p.ticket}", rc)

    # ------------------------------------------------------------------------------------ closed-trade logging
    def _log_trade(self, rec):
        pid = rec.get("position_id") or rec.get("ticket")
        deals = self.gw.deals_for_position(pid) if pid else None
        now = self.gw.now()
        outs = [x for x in (deals or []) if x.entry in DEAL_OUTS]
        if not outs:
            rec.setdefault("gone_since", now)
            if now - rec["gone_since"] > 180:
                rec.update(status=CLOSED, t_closed=iso(now), status_reason="closed; no closing deal found in history")
                self.event(rec, "closed_unlogged", "position gone, no closing deal after 180 s")
            return
        ins = [x for x in deals if x.entry == mt5.DEAL_ENTRY_IN]
        d, pair = rec["side"], rec["pair"]
        P_ = pip(pair)
        vin = sum(x.volume for x in ins) or 1.0
        fill = sum(x.price * x.volume for x in ins) / vin if ins else rec.get("fill")
        vout = sum(x.volume for x in outs) or 1.0
        px = sum(x.price * x.volume for x in outs) / vout
        last = max(outs, key=lambda x: x.time_msc if hasattr(x, "time_msc") else x.time)
        why = DEAL_REASON.get(last.reason, str(last.reason))
        if why == "BOT":
            why = rec.get("close_reason") or "BOT"
        gross = sum(x.profit for x in deals)
        comm = sum(x.commission + x.fee for x in deals)
        swap = sum(x.swap for x in deals)
        net = gross + comm + swap
        lot = vin if ins else rec.get("lot")
        risk_fill = self.gw.loss_per_lot(self.sym(pair), abs(fill - rec["sl"])) * lot if fill else None
        t_fill = min(x.time for x in ins) if ins else ep(rec.get("t_filled"))
        tw = rec.get("twin") or {}
        row = dict(sid=rec["sid"], pair=pair, side=rec["side_txt"], order_kind=rec.get("order_kind"),
                   t_signal=rec["t_signal"], lot=lot, risk_usd_planned=rec.get("risk_usd"),
                   entry=rec["entry"], sl=rec["sl"], tp=rec["tp"], rr=rec.get("rr"),
                   t_fill=iso(t_fill), fill=fill, slip_pips=round((fill - rec["entry"]) * d / P_, 2) if fill else None,
                   t_exit=iso(last.time), exit=px, exit_reason=why,
                   hold_h=round((last.time - t_fill) / 3600, 2) if t_fill else None,
                   pips=round((px - fill) * d / P_, 2) if fill else None,
                   risk_pips_fill=round((fill - rec["sl"]) * d / P_, 2) if fill else None,
                   R_pips=round((px - fill) / (fill - rec["sl"]), 3) if fill and fill != rec["sl"] else None,
                   usd_gross=round(gross, 2), commission=round(comm, 2), swap=round(swap, 2), usd_net=round(net, 2),
                   risk_usd_fill=round(risk_fill, 2) if risk_fill else None,
                   R_net=round(net / risk_fill, 3) if risk_fill else None,
                   R_net_planned=round(net / rec["risk_usd"], 3) if rec.get("risk_usd") else None,
                   position_id=pid, adopted=rec.get("adopted", False),
                   bt_status=tw.get("status"), bt_fill=tw.get("fill"), bt_exit_reason=tw.get("exit_reason"),
                   bt_R=tw.get("R"))
        append_csv(self.f_trades, [row])
        rec.update(status=CLOSED, t_closed=iso(last.time), close_reason=why, status_reason=f"closed {why}",
                   usd_net=round(net, 2), fill=fill)
        self._pnl_cache = (0.0, self._pnl_cache[1])
        self.event(rec, "closed", f"{why} exit {px} net ${net:+.2f} ({row['R_net']} R)")

    # ------------------------------------------------------------------------------------ shadow (ML data)
    def shadow(self, pair, T, st):
        c = self.src.shadow(pair, T)
        if c is None or not len(c):
            return
        c = c.copy()
        c["cid"] = [f"{pair}-{pd.Timestamp(t):%y%m%d%H%M}{'B' if d == 1 else 'S'}-{pd.Timestamp(z):%y%m%d%H%M}{k[0]}"
                    for t, d, z, k in zip(c.t_signal, c.side, c.t_zone, c.zone_kind)]
        c = c.drop_duplicates("cid")
        seen = self.st["shadow_seen"]
        new = c[~c.cid.isin(list(seen))]
        if not len(new):
            return
        now = self.gw.now()
        ref = {(r["t_signal"], r["side"]) for r in self.recs(pair=pair)}
        rows = []
        for r in new.to_dict("records"):
            key = (str(pd.Timestamp(r["t_signal"]))[:19], int(r["side"]))
            rows.append({"logged_utc": iso(now), "pair": pair, "cid": r["cid"],
                         "seen_late": str(pd.Timestamp(r["t_signal"])) != str(pd.Timestamp(T)),
                         "ref_signal": key in ref, **{k: v for k, v in r.items() if k not in ("cid", "sym")}})
            seen[r["cid"]] = now
            self.st["shadow_open"][r["cid"]] = dict(pair=pair, t_signal=str(pd.Timestamp(r["t_signal"])), side=int(r["side"]),
                                                    entry=float(r["entry"]), sl=float(r["sl"]), tp=float(r["tp"]),
                                                    expire=str(pd.Timestamp(r["expire"])))
            if r["ref_gate"] and key not in ref and str(pd.Timestamp(r["t_signal"])) == str(pd.Timestamp(T)):
                log.warning("shadow %s passes the REF gates but is not a REF order at %s (dedupe or mismatch)",
                            r["cid"], T)
        append_csv(self.f_shadow, rows)
        self.update_shadow_labels(pair)

    def update_shadow_labels(self, pair=None):
        """Independent outcome of every shadow candidate (simulate one_at_a_time=False, as BT8 labels)."""
        by = defaultdict(list)
        for cid, x in self.st["shadow_open"].items():
            if pair is None or x["pair"] == pair:
                by[x["pair"]].append((cid, x))
        for p, lst in by.items():
            t0 = min(ep(x["t_signal"]) for _, x in lst) - 60
            m1 = self.gw.m1(self.sym(p), t0, closed_only=True)
            if m1 is None or len(m1) < 3:
                continue
            o = pd.DataFrame([dict(t_signal=pd.Timestamp(x["t_signal"]), side=x["side"], entry_type="limit",
                                   entry=x["entry"], expire=pd.Timestamp(x["expire"]), sl=x["sl"], tp=x["tp"],
                                   be_at_r=np.nan, max_hold_min=np.nan, cid=cid) for cid, x in lst])
            with live_bars(p, m1):
                out = common.simulate(p, o, one_at_a_time=False, max_days=int(self.cfg.max_hold_days))
            rows = []
            for r in resolve_rows(out, m1.time.iloc[-1], chain=False):
                if not r["resolved"]:
                    continue
                self.st["shadow_open"].pop(r["cid"], None)
                f = r["status"] == "filled"
                slipped = r.get("exit_reason") in ("SL", "BE", "GAP_SL")
                rows.append(dict(cid=r["cid"], pair=p, t_signal=r["t_signal"], status=r["status"],
                                 t_fill=r.get("t_fill"), fill=r.get("fill"), t_exit=r.get("t_exit"),
                                 exit=r.get("exit"), exit_reason=r.get("exit_reason"), R=r.get("R"),
                                 gross_R=((r["gross_pips"] + (common.STOP_SLIP if slipped else 0.0)) / r["risk_pips"])
                                 if f else None, win=(float(r["R"] > 0) if f else None),
                                 mfe_R=r.get("mfe_R"), mae_R=r.get("mae_R"), hold_h=r.get("hold_h")))
            append_csv(self.f_shlab, rows)

    # ------------------------------------------------------------------------------------ reporting
    def daily(self, P):
        now = self.gw.now()
        today = iso(now)[:10]
        prev = self.st.get("day")
        if prev is None:
            self.st["day"] = today
            return
        if prev == today:
            return
        self.st["day"] = today
        recs = [r for r in self.recs() if (r["t_signal"] or "")[:10] == prev]
        cats = Counter((r.get("status_reason") or "").split(":")[0] for r in recs if r["status"] in (SKIPPED, MISSED))
        tr = pd.read_csv(self.f_trades) if os.path.exists(self.f_trades) else pd.DataFrame()
        tr = tr[tr.t_exit.astype(str).str[:10] == prev] if len(tr) else tr
        acct = self.gw.account()
        row = dict(date=prev, mode=self.st["mode"], signals=len(recs),
                   placed=sum(r["status"] not in (SKIPPED, MISSED, NEW) for r in recs),
                   skipped=sum(r["status"] in (SKIPPED, MISSED) for r in recs),
                   skip_reasons=";".join(f"{k}={v}" for k, v in cats.items()),
                   closed_trades=len(tr), wins=int((tr.usd_net > 0).sum()) if len(tr) else 0,
                   usd_net=round(tr.usd_net.sum(), 2) if len(tr) else 0.0,
                   R_net=round(tr.R_net.sum(), 3) if len(tr) else 0.0,
                   balance=getattr(acct, "balance", None), equity=getattr(acct, "equity", None),
                   open_positions=len(self.recs(FILLED)), pending=len(self.recs(PENDING)),
                   shadow_candidates=sum(1 for v in self.st["shadow_seen"].values() if iso(v)[:10] == prev))
        append_csv(self.f_daily, [row])
        log.info("DAILY %s", " ".join(f"{k}={v}" for k, v in row.items()))

    def status_line(self, P, O):
        real, flo = self.day_pnl(P) if self.cfg.live else (0.0, 0.0)
        act = [f"{r['sid']}:{r['status']}" for r in self.recs(*LIVE_ACTIVE)]
        return (f"[{self.st['mode']}] day P/L {real + flo:+.2f} (real {real:+.2f} float {flo:+.2f}) | active {act or '-'}"
                f" | our positions {len([p for p in P if self._is_ref(p)])} orders {len([o for o in O if self._is_ref(o)])}"
                f" | shadow open {len(self.st['shadow_open'])} | last bars "
                + " ".join(f"{p}:{str(self.st['last_T'].get(p, '-'))[11:16]}" for p in PAIRS))

    def example_request(self, pair, o):
        """The exact request an order like `o` (an engine order dict) would produce NOW (never sent)."""
        rec = self._new_rec(pair, o, {})
        s = self.sym(pair)
        tick, spec = self.gw.tick(s), self.gw.spec(s)
        d = rec["side"]
        px = tick.ask if d == 1 else tick.bid
        market = (px - rec["entry"]) * d <= 0
        ref_px = px if market else rec["entry"]
        lot, note, risk = size_lot(self.cfg.risk_usd, self.gw.loss_per_lot(s, abs(ref_px - rec["sl"])),
                                   spec.volume_min, spec.volume_step, spec.volume_max, self.cfg.min_lot_risk_mult)
        return rec, lot, note, risk, (self.build_request(rec, tick, spec, lot, market) if lot > 0 else None), market


# ============================================================================================== entry point
def connect(cfg):
    """Attach to the running terminal (no re-login), verify the Zero demo account. Returns an MT5Gateway."""
    from scalper.broker import Broker
    b = Broker(suffix=cfg.suffix, magic=cfg.magic, env="Z")     # loads .env (never printed)
    if not mt5.initialize(path=os.getenv("MT5_PATH")):
        log.warning("attach failed (%s) -> Broker.connect()", mt5.last_error())
        b.connect()                                              # refuses non-demo unless ALLOW_LIVE=1
    ai = mt5.account_info()
    if ai is None:
        raise ConnectionError(f"account_info unavailable: {mt5.last_error()}")
    want = (os.getenv("Z_LOGIN") or "").strip()
    if not want or str(ai.login) != want:
        mt5.shutdown()
        raise SystemExit("REFUSING: terminal account is not Z_LOGIN (not switching: would disturb the other bots)")
    if ai.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        mt5.shutdown()
        raise SystemExit("REFUSING: account is not a DEMO account (this forward test is demo-only)")
    ti = mt5.terminal_info()
    if cfg.live and ti is not None and not ti.trade_allowed:
        log.error("terminal AutoTrading is OFF: every order would be rejected (retcode 10027)")
    S._OFFSET = S.server_offset()
    S.warm_up()
    log.info("attached: demo account OK, balance %.2f %s, server offset %+.0f s, mode %s", ai.balance, ai.currency,
             S._OFFSET, "LIVE (demo orders)" if cfg.live else "DRY-RUN")
    return MT5Gateway(b, live=cfg.live, magic=cfg.magic, offset=S._OFFSET)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="send orders to the Zero DEMO (default: dry-run)")
    ap.add_argument("--once", action="store_true", help="one cycle, print state, exit")
    ap.add_argument("--example", action="store_true", help="with --once: print the request for the latest REF order")
    ap.add_argument("--risk", type=float, default=5.0, help="$ risk per trade (default 5.00)")
    ap.add_argument("--daily-loss", type=float, default=20.0)
    ap.add_argument("--max-open", type=int, default=4)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--closures", help="JSON file: extra [[close_utc, reopen_utc, [pairs], note], ...]")
    a = ap.parse_args()
    cfg = Config(live=a.live, risk_usd=a.risk, daily_loss_usd=a.daily_loss, max_open=a.max_open, poll_s=a.poll)
    if a.closures:
        with open(a.closures, encoding="utf-8") as fh:
            cfg.closures += [tuple(x[:2]) + (tuple(x[2]), x[3]) for x in json.load(fh)]
    logdir = os.path.join(ROOT, "logs", "ref_trader")
    os.makedirs(logdir, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s",
                        handlers=[logging.FileHandler(os.path.join(logdir, "trader.log" if a.live else "trader_dry.log"),
                                                      encoding="utf-8"), logging.StreamHandler()])
    log.info("config %s", cfg.as_dict())
    for i in range(10):
        try:
            gw = connect(cfg)
            break
        except ConnectionError as ex:
            log.warning("connect failed (%s), retry in %ds", ex, 10 * (i + 1)); time.sleep(10 * (i + 1))
    else:
        raise SystemExit("cannot connect to MT5")
    tr = Trader(gw, cfg, LiveSource())
    try:
        if a.once:
            tr.cycle()
            tr.update_twins(force=True)
            tr.save()
            P, O = gw.positions() or [], gw.orders() or []
            print(tr.status_line(P, O))
            print("state:", {k: len(v) if isinstance(v, dict) else v for k, v in tr.st.items()})
            for r in sorted(tr.recs(), key=lambda r: r["t_signal"])[-12:]:
                print(f"  {r['sid']:22} {r['status']:9} {r.get('status_reason', '')[:90]}  bt={(r.get('twin') or {}).get('status')}"
                      f"/{(r.get('twin') or {}).get('exit_reason')}")
            if a.example:
                for pair in PAIRS:
                    st = tr.src.evaluate(pair)
                    if st.get("orders_recent"):
                        o = st["orders_recent"][-1]
                        rec, lot, note, risk, req, mk = tr.example_request(pair, o)
                        print(f"\nEXAMPLE (never sent) {rec['sid']} {'MARKET (limit marketable now)' if mk else 'LIMIT'} "
                              f"lot {lot} risk ${risk:.2f} {note}\n  {json.dumps(req, default=_jd)}")
                        break
                else:
                    print("no REF order in the last 3 days on any pair -> no example request")
            return
        log.info("loop start (poll %.1fs)", cfg.poll_s)
        while True:
            try:
                tr.cycle()
            except Exception:
                log.exception("cycle error; retrying")
                time.sleep(5)
            time.sleep(cfg.poll_s)
    except KeyboardInterrupt:
        log.info("stopped by user (pending orders keep their broker expiry, positions their SL/TP)")
    finally:
        try:
            tr.save()
        finally:
            mt5.shutdown()                                       # this process's IPC link only


if __name__ == "__main__":
    main()
