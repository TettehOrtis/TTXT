"""READ-ONLY live status scanner for the frozen canonical SMC REF strategy (RECON canonical = BT6b REF + literal OB).

  .venv/Scripts/python.exe smc_bot/live/ref_scanner.py --once     # one pass over the 9 pairs, print the table
  .venv/Scripts/python.exe smc_bot/live/ref_scanner.py --loop     # poll every 20 s, evaluate each new CLOSED M5 bar

Per pair (EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD EURJPY XAUUSD, suffix z; EURGBP skipped per BT7) it pulls the
last closed bars (M5 3000, M30 2000, H1 2500, H4 1000) from the Zero demo and evaluates the canonical pipeline with
live/ref_engine.py (which runs recon_pipeline.build_orders itself on that window). Stages:
  0 no bias (H4 structure and EMA200 side disagree)   1 bias ok, no POI   2 unmitigated POIs waiting for a touch
  3 POI touched, waiting for the 5M CHoCH (bars left)   4 CHoCH confirmed -> PENDING LIMIT signal (valid 60 min)
Outputs: smc_bot/live/status.json (atomic), results/SCANNER/status_log.csv (stage changes), results/SCANNER/signals.csv
(every stage-4 signal), results/SCANNER/rejections.csv (setups that reached a CHoCH/touch and were rejected, and why).

SAFETY: only mt5.initialize / account_info / terminal_info / symbol_select / symbol_info_tick / copy_rates_from_pos are
called. No order_send, order_check, position or order functions. It attaches to the running terminal (shared with the
USDJPY bot, same Zero account) without logging in again; it logs in via scalper.broker.Broker only if the terminal is not
running. mt5.shutdown() only closes THIS process's IPC link and is called once, at exit. Credentials are never printed.
"""
import os, sys, time, json, argparse, logging, datetime as dt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ref_engine as E                                         # noqa: E402  (imports the canonical pipeline)

ROOT = os.path.dirname(E.SMC)
sys.path.insert(0, ROOT)
import MetaTrader5 as mt5                                      # noqa: E402
from scalper.broker import Broker                              # noqa: E402

OUT_DIR = os.path.join(E.SMC, "results", "SCANNER")
STATUS = os.path.join(HERE, "status.json")
LOG_CSV = os.path.join(OUT_DIR, "status_log.csv")
SIG_CSV = os.path.join(OUT_DIR, "signals.csv")
REJ_CSV = os.path.join(OUT_DIR, "rejections.csv")
SUFFIX = "z"
PAIRS = E.PAIRS9
MT5_TF = dict(M5=mt5.TIMEFRAME_M5, M30=mt5.TIMEFRAME_M30, H1=mt5.TIMEFRAME_H1, H4=mt5.TIMEFRAME_H4)
GRACE_S = 3                    # a bar counts as closed GRACE_S seconds after its close time
POLL_S = 20
log = logging.getLogger("ref_scanner")
_OFFSET = 0.0                  # server clock - UTC epoch, seconds (Exness: 0)
_BROKER = None


# ============================================================================================== broker (read-only)
def connect():
    """Attach to the running terminal (no re-login). Fall back to Broker.connect() only if nothing is running."""
    global _BROKER, _OFFSET
    _BROKER = Broker(suffix=SUFFIX, magic=0, env="Z")                 # loads .env; never trades here
    if not mt5.initialize(path=os.getenv("MT5_PATH")):
        log.warning("attach failed (%s) -> Broker.connect()", mt5.last_error())
        _BROKER.connect()
    ai = mt5.account_info()
    if ai is None:
        raise ConnectionError(f"account_info unavailable: {mt5.last_error()}")
    want = os.getenv("Z_LOGIN")
    if want and str(ai.login) != want.strip():
        mt5.shutdown()
        raise SystemExit("terminal is logged into a different account than Z_LOGIN; not switching (would disturb "
                         "the other bot). Log the terminal into the Zero demo first.")
    if ai.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        log.warning("account is not DEMO (scanner is read-only anyway)")
    _OFFSET = server_offset()
    warm_up()
    return _BROKER


def server_offset():
    """Server clock minus UTC epoch (whole hours), from the freshest tick. 0 on Exness."""
    best = None
    for p in PAIRS:
        mt5.symbol_select(p + SUFFIX, True)
        t = mt5.symbol_info_tick(p + SUFFIX)
        if t is not None and t.time:
            best = t.time if best is None else max(best, t.time)
    if best is None:
        return 0.0
    d = best - time.time()
    h = round(d / 3600)
    if h != 0 and abs(d - h * 3600) < 900:
        log.warning("server clock is UTC%+d h (bars are compared in server time)", h)
        return h * 3600.0
    return 0.0


def ensure():
    """True if our IPC link and the terminal's server connection are up. Re-attaches our link only (never re-logs in)."""
    ti = mt5.terminal_info()
    if ti is None:
        log.warning("IPC link lost -> re-attach")
        mt5.shutdown()                                               # this process only
        ok = mt5.initialize(path=os.getenv("MT5_PATH"))
        ti = mt5.terminal_info() if ok else None
        if ti is None:
            return False
    return bool(ti.connected)


def now_server():
    return time.time() + _OFFSET


def fetch(sym, tf, n, now=None, closed_only=True):
    """Last n CLOSED bars of sym+suffix as raw arrays (times = bar open / close, datetime64[ns]). None on failure."""
    s = sym + SUFFIX
    mt5.symbol_select(s, True)
    r = mt5.copy_rates_from_pos(s, MT5_TF[tf], 0, n + 1)
    if r is None or len(r) == 0:
        return None
    t = np.asarray(r["time"], dtype=np.int64)
    if closed_only:
        now = now_server() if now is None else now
        keep = t + 60 * E.TF_MIN[tf] + GRACE_S <= now
        r, t = r[keep], t[keep]
    r, t = r[-n:], t[-n:]
    tt = t.astype("datetime64[s]").astype("datetime64[ns]")
    return dict(t=tt, tc=tt + np.timedelta64(E.TF_MIN[tf], "m"), o=r["open"].astype(float), h=r["high"].astype(float),
                l=r["low"].astype(float), c=r["close"].astype(float))


def last_closed_m5(sym):
    b = fetch(sym, "M5", 3)
    return None if b is None or not len(b["t"]) else b["tc"][-1]


def tick_age_s(sym):
    t = mt5.symbol_info_tick(sym + SUFFIX)
    return None if t is None or not t.time else now_server() - t.time


# ============================================================================================== scanning
ENGINES = {p: E.RefEngine(p) for p in PAIRS}


def _epoch(t64):
    return int(np.datetime64(t64, "s").astype(np.int64))


def get_raw(sym):
    """(T, raw, None) with every timeframe cut at the same last closed M5 bar T, or (None, None, why).
    The terminal loads history lazily: right after symbol_select the bars can lag the ticks by hours, so the bars are
    checked against the latest tick and re-requested (up to ~30 s) instead of evaluating stale data."""
    why = "no data"
    for attempt in range(15):
        if attempt:
            time.sleep(2)
        T = last_closed_m5(sym)
        if T is None:
            why = f"copy_rates failed: {mt5.last_error()}"; continue
        tk = mt5.symbol_info_tick(sym + SUFFIX)
        if tk is not None and tk.time and tk.time - _epoch(T) > 15 * 60:
            why = f"M5 history not synced (last bar close {str(T)[:16]}, last tick {pd.Timestamp(tk.time, unit='s')})"
            continue
        raw, bad = {}, None
        for tf in E.TFS:
            b = fetch(sym, tf, E.WINDOW[tf] + 5)
            if b is None or not len(b["t"]):
                bad = f"{tf} copy_rates failed: {mt5.last_error()}"; break
            k = b["tc"] <= T                               # all timeframes consistent with the same M5 close
            raw[tf] = {x: v[k][-E.WINDOW[tf]:] for x, v in b.items()}
            lag_min = (_epoch(T) - _epoch(raw[tf]["tc"][-1])) / 60 if len(raw[tf]["t"]) else 1e9
            if lag_min > E.TF_MIN[tf] + 180:
                bad = f"{tf} history not synced (last close {str(raw[tf]['tc'][-1])[:16]})"; break
        if bad:
            why = bad; continue
        return T, raw, None
    return None, None, why


def warm_up():
    """Ask the terminal for every symbol/timeframe once so the history download starts (read-only)."""
    for p in PAIRS:
        mt5.symbol_select(p + SUFFIX, True)
        for tf in E.TFS:
            mt5.copy_rates_from_pos(p + SUFFIX, MT5_TF[tf], 0, E.WINDOW[tf] + 5)
    time.sleep(2)


def scan_pair(sym):
    T, raw, why = get_raw(sym)
    if T is None:
        return dict(pair=sym, stage=-1, stage_name="no data", detail=why)
    t0 = time.time()
    st = ENGINES[sym].evaluate(raw, T)
    st["eval_ms"] = round(1000 * (time.time() - t0))
    age = tick_age_s(sym)
    st["last_tick_age_s"] = None if age is None else round(age)
    st["market_open"] = age is not None and age < 600
    st["bars"] = {tf: int(len(raw[tf]["t"])) for tf in E.TFS}
    return st


# ============================================================================================== outputs
def _jd(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.datetime64, pd.Timestamp)):
        return str(pd.Timestamp(o))
    return str(o)


def atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, default=_jd, ensure_ascii=False)
    os.replace(tmp, path)


def append_csv(path, rows):
    if not rows:
        return
    df = pd.DataFrame(rows)
    new = not os.path.exists(path)
    if not new:
        cols = pd.read_csv(path, nrows=0).columns.tolist()
        df = df.reindex(columns=cols + [c for c in df.columns if c not in cols])
        if len(df.columns) > len(cols):                      # new columns: rewrite with the union header
            old = pd.read_csv(path)
            pd.concat([old, df], ignore_index=True).to_csv(path, index=False, encoding="utf-8")
            return
    df.to_csv(path, mode="a", header=new, index=False, encoding="utf-8")


def _keys(path, cols):
    if not os.path.exists(path):
        return set()
    try:
        d = pd.read_csv(path, usecols=cols, dtype=str)
        return set(map(tuple, d[cols].fillna("").values))
    except Exception:
        return set()


def focus(st):
    if st.get("stage") == 4:
        return "sig " + ",".join(s["t_signal"] for s in st.get("signals_active", []))
    if st.get("stage") == 3:
        return "wait " + ",".join(f"{w['kind']}@{w['created']}" for w in st.get("waiting", []))
    return ""


class Recorder:
    def __init__(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        self.prev = {}
        if os.path.exists(STATUS):
            try:
                old = json.load(open(STATUS, encoding="utf-8"))
                self.prev = {p: (v.get("stage"), v.get("_focus", "")) for p, v in old.get("pairs", {}).items()}
            except Exception:
                pass
        self.sig_keys = _keys(SIG_CSV, ["pair", "t_signal"])
        self.rej_keys = _keys(REJ_CSV, ["pair", "t_event", "event", "poi_kind", "created"])
        self.status = {}

    def record(self, results):
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        logs, sigs, rejs = [], [], []
        for st in results:
            p = st["pair"]
            st["_focus"] = focus(st)
            key = (st.get("stage"), st["_focus"])
            if self.prev.get(p) != key:
                logs.append(dict(scan_utc=now, t_bar_close=st.get("t_bar_close"), pair=p,
                                 prev_stage=(self.prev.get(p) or (None,))[0], stage=st.get("stage"),
                                 stage_name=st.get("stage_name"), bias=(st.get("bias") or {}).get("txt"),
                                 detail=st.get("detail")))
                self.prev[p] = key
            for s in st.get("signals_active", []):
                if (p, s["t_signal"]) not in self.sig_keys:
                    late = s["t_signal"] != st.get("t_bar_close")
                    sigs.append({"logged_utc": now, "seen_late": late, **s})
                    self.sig_keys.add((p, s["t_signal"]))
            for e in st.get("events_recent", []):
                if e["event"] == "signal":
                    continue
                k = (p, e["t_event"], e["event"], e.get("kind", e.get("poi_kind", "")) or "", e.get("created", "") or "")
                if k not in self.rej_keys:
                    rejs.append(dict(logged_utc=now, pair=p, t_event=e["t_event"], event=e["event"], reason=e["reason"],
                                     poi_kind=k[3], created=k[4], side=e.get("side"), bottom=e.get("bottom"),
                                     top=e.get("top"), t_touch=e.get("t_touch"), t_choch=e.get("t_choch")))
                    self.rej_keys.add(k)
            self.status[p] = st
        append_csv(LOG_CSV, logs)
        append_csv(SIG_CSV, sigs)
        append_csv(REJ_CSV, rejs)
        atomic_json(STATUS, dict(generated_utc=now, strategy="canonical REF (RECON: BT6b REF + literal OB zone)",
                                 read_only=True, server_offset_h=_OFFSET / 3600,
                                 stages={str(k): v for k, v in E.STAGE_NAMES.items()},
                                 pairs={p: self.status[p] for p in PAIRS if p in self.status}))
        return logs, sigs, rejs


def table(results):
    lines = [f"SMC REF scanner  {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%S} UTC  (read-only)", "",
             f"{'pair':7} {'M5 close':16} {'price':>10} {'bias':5} {'H4':>3} {'EMA':>4} {'st':>2}  detail",
             "-" * 118]
    for st in results:
        b = st.get("bias") or {}
        mk = "" if st.get("market_open", True) else "  [market closed]"
        lines.append(f"{st['pair']:7} {str(st.get('t_bar_close', ''))[:16]:16} "
                     f"{E.fmt_px(st['pair'], st.get('price', np.nan)):>10} {b.get('txt', '?'):5} "
                     f"{b.get('h4_trend', 0):+3d} {b.get('ema_side', 0):+4d} {st.get('stage', -1):>2}  "
                     f"{st.get('detail', '')}{mk}")
    for st in results:
        for s in st.get("signals_active", []):
            p = st["pair"]
            lines += ["", f"*** {p} {s['side_txt']} LIMIT  signal {s['t_signal']} UTC, valid until {s['valid_until']} "
                          f"({s.get('killzone')}, {s.get('weekday')})",
                      f"    entry {E.fmt_px(p, s['entry'])}  <- {s.get('entry_why', '')}",
                      f"    SL    {E.fmt_px(p, s['sl'])}  <- {s.get('sl_why', '')}",
                      f"    TP    {E.fmt_px(p, s['tp'])}  <- {s.get('tp_why', '')}",
                      f"    RR {s['rr']}  stop {s.get('stop_pips')} pips = {s.get('stop_x_comm')} x commission; "
                      f"POI {s.get('poi_kind')} {E.fmt_px(p, s.get('poi_bottom', np.nan))}-"
                      f"{E.fmt_px(p, s.get('poi_top', np.nan))} touched {s.get('t_touch')}",
                      f"    cancel if {s.get('cancel_if')}; {s.get('backtest_note')}"]
    rec = []
    for st in results:
        T = pd.Timestamp(st["t_bar_close"]) if st.get("t_bar_close") else None
        for e in st.get("events_recent", []):
            if e["event"] == "rejected" and T is not None and pd.Timestamp(e["t_event"]) > T - pd.Timedelta(hours=6):
                rec.append(f"  {st['pair']:7} {e['t_event'][5:16]} {e.get('side', ''):4} {e.get('kind', '')}: {e['reason']}")
    if rec:
        lines += ["", "rejected setups (last 6 h):"] + rec
    warn = [f"  {st['pair']}: {w}" for st in results for w in st.get("warnings", [])]
    if warn:
        lines += ["", "warnings:"] + warn
    return "\n".join(lines)


def one_pass(rec):
    results = []
    for p in PAIRS:
        try:
            results.append(scan_pair(p))
        except Exception as ex:                                   # never crash the loop on one pair
            log.exception("scan %s", p)
            results.append(dict(pair=p, stage=-1, stage_name="error", detail=repr(ex)[:200]))
    rec.record(results)
    return results


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true", help="single pass (default)")
    g.add_argument("--loop", action="store_true", help=f"poll every {POLL_S}s, act on each new closed M5 bar")
    ap.add_argument("--poll", type=float, default=POLL_S)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for i in range(10):
        try:
            connect(); break
        except ConnectionError as ex:
            log.warning("connect failed (%s), retry in %ds", ex, 10 * (i + 1)); time.sleep(10 * (i + 1))
    else:
        raise SystemExit("cannot connect to MT5")
    rec = Recorder()
    try:
        results = one_pass(rec)
        print(table(results), flush=True)
        if not a.loop:
            return
        last = {r["pair"]: r.get("t_bar_close") for r in results}
        down_since = None
        while True:
            time.sleep(max(1.0, a.poll))
            try:
                if not ensure():
                    down_since = down_since or time.time()
                    log.warning("broker/terminal disconnected for %.0fs; waiting", time.time() - down_since)
                    continue
                down_since = None
                new = [p for p in PAIRS if (lambda T: T is not None and str(pd.Timestamp(T)) != last.get(p))
                       (last_closed_m5(p))]
                if not new:
                    continue                                       # weekend / no new bar yet
                res = []
                for p in PAIRS:
                    if p in new:
                        try:
                            r = scan_pair(p)
                        except Exception as ex:
                            log.exception("scan %s", p)
                            r = dict(pair=p, stage=-1, stage_name="error", detail=repr(ex)[:200])
                        if r.get("t_bar_close"):
                            last[p] = r["t_bar_close"]
                        res.append(r)
                    else:
                        res.append(rec.status.get(p, dict(pair=p, stage=-1, detail="pending")))
                rec.record([r for r in res if r["pair"] in new])
                print("\n" + table(res), flush=True)
            except Exception:
                log.exception("loop error; retrying")
                time.sleep(5)
    finally:
        mt5.shutdown()                                             # closes this process's IPC link only


if __name__ == "__main__":
    main()
