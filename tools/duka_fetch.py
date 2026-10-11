"""Dukascopy M1 candle downloader + consolidator (resumable, polite).

Usage (from C:\\Users\\Tetteh\\Desktop\\TTXT):
  .venv\\Scripts\\python.exe tools\\duka_fetch.py fetch [--workers 8] [--phase 1,2,3]
  .venv\\Scripts\\python.exe tools\\duka_fetch.py build [--syms EURUSD,XAUUSD]   # raw cache -> data_duka/{SYM}_M1.parquet
  .venv\\Scripts\\python.exe tools\\duka_fetch.py status

Raw cache: data_duka/raw/{SYM}/{YYYY}/{MM}/{DD}_{BID|ASK}.bi5  (0-byte file = server says no data, 404/empty).
Restart = just run `fetch` again; anything already in the raw cache is skipped.
HARD LIMIT: never requests any date >= 2025-07-01 (locked test period).
"""
import argparse, datetime as dt, json, lzma, os, random, struct, sys, threading, time, queue
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _lock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data_duka", "raw")
OUT = os.path.join(ROOT, "data_duka")
LOGDIR = os.path.join(ROOT, "smc_bot", "results", "DATA2")
LOCK_DATE = dt.date(2025, 7, 1)          # first forbidden day
START = dt.date(2005, 1, 1)
END = dt.date(2025, 6, 30)

SYMS = ("EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD EURGBP EURJPY XAUUSD "
        "GBPJPY AUDJPY EURAUD EURCHF GBPCHF CADJPY NZDJPY AUDCAD AUDNZD EURCAD GBPAUD "
        "CHFJPY GBPCAD AUDCHF NZDCAD").split()
BASE = "https://datafeed.dukascopy.com/datafeed"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def scale(sym):          # price integer -> float
    return 1e3 if ("JPY" in sym or sym == "XAUUSD") else 1e5


def point(sym):          # MT5 point used by data_zero / research.lib.point_size
    return 0.001 if ("JPY" in sym or sym == "XAUUSD") else 0.00001


def raw_path(sym, d, side):
    return os.path.join(RAW, sym, f"{d.year:04d}", f"{d.month - 1:02d}", f"{d.day:02d}_{side}.bi5")


def url(sym, d, side):
    return f"{BASE}/{sym}/{d.year:04d}/{d.month - 1:02d}/{d.day:02d}/{side}_candles_min_1.bi5"


ORIG = "EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD EURGBP EURJPY XAUUSD".split()
CROSSES = [x for x in SYMS if x not in ORIG]


def stages():
    """stage -> list of (syms, years). Priority order set by coordinator:
    1 = 10 originals 2021-2022 ; 2 = 10 originals 2005-2020 (newest first) ;
    3 = 15 crosses, all years (newest first) ; 4 = 10 originals 2023-2025H1."""
    return {
        1: [(ORIG, [2021, 2022])],
        2: [(ORIG, list(range(2020, 2004, -1)))],
        3: [(CROSSES, list(range(2025, 2004, -1)))],
        4: [(ORIG, [2023, 2024, 2025])],
    }


def daterange(y):
    d = max(dt.date(y, 1, 1), START)
    e = min(dt.date(y, 12, 31), END)
    while d <= e:
        yield d
        d += dt.timedelta(days=1)


def gen_tasks(stage_list):
    st = stages()
    for p in stage_list:
        for syms, years in st[p]:
            for y in years:
                for sy in syms:
                    for d in daterange(y):
                        if d.weekday() == 5:           # Saturday: market closed, no files
                            continue
                        assert d < LOCK_DATE
                        yield (sy, d)


# ---------------------------------------------------------------- logging
_log_lock = threading.Lock()


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with _log_lock:
        print(line, flush=True)
        try:
            with open(os.path.join(LOGDIR, "fetch.log"), "a") as f:
                f.write(line + "\n")
        except OSError:
            pass


# ---------------------------------------------------------------- fetching
class Throttle:
    """Shared cool-down: any 429/503/timeouts pushes everyone back; decays on success."""
    def __init__(self):
        self.lock = threading.Lock()
        self.pause_until = 0.0
        self.level = 0

    def wait(self):
        while True:
            t = self.pause_until - time.time()
            if t <= 0:
                return
            time.sleep(min(t, 5))

    def penalize(self, why):
        with self.lock:
            now = time.time()
            if now < self.pause_until:            # already cooling down: don't compound
                return
            self.level = min(self.level + 1, 5)
            delay = min(120, 5 * 2 ** self.level) * random.uniform(0.8, 1.2)
            self.pause_until = now + delay
        log(f"THROTTLE level={self.level} pause={delay:.0f}s ({why})")

    def ok(self):
        if self.level and random.random() < 0.02:
            with self.lock:
                self.level = max(0, self.level - 1)


TH = Throttle()
_tl = threading.local()
stats = {"ok": 0, "empty": 0, "fail": 0, "bytes": 0}
stats_lock = threading.Lock()


def session():
    import requests
    s = getattr(_tl, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "*/*"})
        _tl.s = s
    return s


def write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".tmp{threading.get_ident()}"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def fetch_one(sym, d, side, max_http_attempts=8):
    """Returns 'ok'|'empty'|'fail'. Writes raw cache on ok/empty. Retries forever on connection errors."""
    import requests
    assert d < LOCK_DATE
    p = raw_path(sym, d, side)
    if os.path.exists(p):
        return "cached"
    u = url(sym, d, side)
    attempts = 0
    conn_fail = 0
    while True:
        TH.wait()
        try:
            r = session().get(u, timeout=(30, 120))
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            conn_fail += 1
            _tl.s = None
            wait = min(90, 3 * 2 ** min(conn_fail, 5)) * random.uniform(0.8, 1.2)
            if conn_fail in (1, 3, 6) or conn_fail % 10 == 0:
                log(f"conn error #{conn_fail} {sym} {d} {side}: {type(e).__name__}; sleep {wait:.0f}s")
            time.sleep(wait)
            continue
        code = r.status_code
        if code == 200:
            TH.ok()
            data = r.content
            if len(data) == 0:
                write_empty(p)
                return "empty"
            try:                                     # validate before caching
                b = lzma.decompress(data)
                assert len(b) % 24 == 0
            except Exception as e:
                attempts += 1
                log(f"bad payload {sym} {d} {side} ({len(data)}B): {e!r}")
                if attempts >= max_http_attempts:
                    return "fail"
                time.sleep(5 * attempts)
                continue
            write_atomic(p, data)
            with stats_lock:
                stats["bytes"] += len(data)
            return "ok"
        if code == 404:
            TH.ok()
            write_empty(p)
            return "empty"
        # 429 / 503 / 5xx / other
        attempts += 1
        if code in (429, 503, 502, 504, 500):
            TH.penalize(f"HTTP {code} {sym} {d} {side}")
        else:
            log(f"HTTP {code} {sym} {d} {side}")
            time.sleep(5 * attempts)
        if attempts >= max_http_attempts:
            return "fail"


def write_empty(p):
    write_atomic(p, b"")


def do_day(sym, d):
    """BID first; if empty skip ASK (write empty marker)."""
    rb = fetch_one(sym, d, "BID")
    if rb == "fail":
        return "fail"
    if rb == "empty" or (rb == "cached" and os.path.getsize(raw_path(sym, d, "BID")) == 0):
        pa = raw_path(sym, d, "ASK")
        if not os.path.exists(pa):
            write_empty(pa)
        return "empty"
    ra = fetch_one(sym, d, "ASK")
    return "fail" if ra == "fail" else "ok"


def worker(q):
    while True:
        try:
            sym, d = q.get_nowait()
        except queue.Empty:
            return
        try:
            res = do_day(sym, d)
        except Exception as e:
            log(f"worker exception {sym} {d}: {e!r}")
            res = "fail"
        with stats_lock:
            stats["ok" if res == "ok" else "empty" if res == "empty" else "fail"] += 1
        if res == "fail":
            log(f"FAILED {sym} {d} (will retry next pass)")


def pending_tasks(phase_list):
    out = []
    for s, d in gen_tasks(phase_list):
        pb, pa = raw_path(s, d, "BID"), raw_path(s, d, "ASK")
        if os.path.exists(pb) and os.path.exists(pa):
            continue
        out.append((s, d))
    return out


def cmd_fetch(a):
    os.makedirs(LOGDIR, exist_ok=True)
    plist = [int(x) for x in a.phase.split(",")]
    for p in plist:
        for pass_no in range(1, 6):
            tasks = pending_tasks([p])
            log(f"stage {p} pass {pass_no}: {len(tasks)} symbol-days pending")
            if not tasks:
                break
            q = queue.Queue()
            for t in tasks:
                q.put(t)
            t0 = time.time()
            ths = [threading.Thread(target=worker, args=(q,), daemon=True) for _ in range(a.workers)]
            for t in ths:
                t.start()
            total = len(tasks)
            while any(t.is_alive() for t in ths):
                time.sleep(30)
                done = total - q.qsize()
                el = time.time() - t0
                rate = done / el if el else 0
                eta = (total - done) / rate / 3600 if rate else float("inf")
                log(f"stage {p} progress: {done}/{total} taken  ok={stats['ok']} empty={stats['empty']} "
                    f"fail={stats['fail']} MB={stats['bytes']/1e6:.0f}  {rate:.2f} days/s  ETA {eta:.1f}h")
                write_status(p, total, done, eta)
            if stats["fail"] == 0:
                break
            stats["fail"] = 0
            time.sleep(120)
    log("fetch finished all requested phases")
    write_status(0, 0, 0, 0, finished=True)


def write_status(p, total, done, eta, finished=False):
    try:
        with open(os.path.join(LOGDIR, "progress.json"), "w") as f:
            json.dump({"time": str(dt.datetime.now()), "phase": p, "taken": done, "total": total,
                       "eta_h": eta, "finished": finished, **stats}, f)
    except OSError:
        pass


# ---------------------------------------------------------------- decoding / building
REC = np.dtype([("sec", ">i4"), ("o", ">i4"), ("c", ">i4"), ("l", ">i4"), ("h", ">i4"), ("v", ">f4")])


def read_raw(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    b = lzma.decompress(open(path, "rb").read())
    return np.frombuffer(b, dtype=REC)


def build_symbol(sym, verbose=True):
    sc = scale(sym)
    pt = point(sym)
    frames = []
    symdir = os.path.join(RAW, sym)
    if not os.path.isdir(symdir):
        return None
    n_bidonly = 0
    n_zero = 0
    n_days = 0
    for y in sorted(os.listdir(symdir)):
        for m in sorted(os.listdir(os.path.join(symdir, y))):
            for fn in sorted(os.listdir(os.path.join(symdir, y, m))):
                if not fn.endswith("_BID.bi5"):
                    continue
                dd = int(fn[:2])
                day = dt.datetime(int(y), int(m) + 1, dd)
                b = read_raw(os.path.join(symdir, y, m, fn))
                if b is None or len(b) == 0:
                    continue
                a = read_raw(os.path.join(symdir, y, m, fn.replace("BID", "ASK")))
                n_days += 1
                t = np.datetime64(day, "ms") + b["sec"].astype("int64") * 1000
                df = pd.DataFrame({
                    "time": t,
                    "open": b["o"] / sc, "high": b["h"] / sc, "low": b["l"] / sc, "close": b["c"] / sc,
                    "bvol": b["v"].astype("float64")})
                if a is not None and len(a):
                    ta = np.datetime64(day, "ms") + a["sec"].astype("int64") * 1000
                    adf = pd.DataFrame({"time": ta, "aclose": a["c"] / sc, "avol": a["v"].astype("float64")})
                    df = df.merge(adf, on="time", how="outer")
                else:
                    df["aclose"] = np.nan
                    df["avol"] = np.nan
                frames.append(df)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).sort_values("time")
    df = df.drop_duplicates("time", keep="last")
    # closed-market fill: volume 0 on both sides -> drop
    zero = (df.bvol.fillna(0) == 0) & (df.avol.fillna(0) == 0)
    n_zero = int(zero.sum())
    df = df[~zero]
    # rows with ask but no bid (outer join) -> no bid prices, cannot use
    nobid = df.open.isna()
    n_nobid = int(nobid.sum())
    df = df[~nobid]
    sp = ((df.aclose - df.close) / pt)
    n_noask = int(sp.isna().sum())
    sp = sp.ffill().fillna(0).round().clip(lower=0)      # missing ask -> last known spread
    out = pd.DataFrame({
        "time": df.time.astype("datetime64[ms]").values,
        "open": df.open.astype("float64").values,
        "high": df.high.astype("float64").values,
        "low": df.low.astype("float64").values,
        "close": df.close.astype("float64").values,
        # bid volume (Dukascopy float "volume"), rounded; floor 1 for kept minutes
        "tick_volume": np.maximum(np.rint(df.bvol.fillna(0).values), 1).astype("uint64"),
        "spread": sp.astype("int32").values,
    })
    out = out.reset_index(drop=True)
    p = os.path.join(OUT, f"{sym}_M1.parquet")
    out.to_parquet(p, index=False, engine="pyarrow", coerce_timestamps="ms")
    info = dict(sym=sym, rows=len(out), days=n_days, zero_vol_dropped=n_zero, ask_only_dropped=n_nobid,
                no_ask_spread_ffilled=n_noask, first=str(out.time.iloc[0]), last=str(out.time.iloc[-1]),
                mb=os.path.getsize(p) / 1e6)
    if verbose:
        log("built " + json.dumps(info))
    return info


def cmd_build(a):
    global OUT
    os.makedirs(LOGDIR, exist_ok=True)
    if getattr(a, "out", ""):
        OUT = os.path.abspath(a.out)
    os.makedirs(OUT, exist_ok=True)
    syms = a.syms.split(",") if a.syms else SYMS
    infos = []
    for s in syms:
        i = build_symbol(s)
        if i:
            infos.append(i)
    with open(os.path.join(LOGDIR, "build_info_%s.json" % os.path.basename(OUT)), "w") as f:
        json.dump(infos, f, indent=1)


def cmd_best(a):
    """Supervisor-friendly end-to-end: wait for sampling job, fetch all stages slowly, build data_duka_full, DONE marker."""
    global OUT
    lockdir = os.path.join(ROOT, "logs", "data2")
    _lock.acquire(os.path.join(lockdir, "best.lock"))
    OUT = os.path.join(ROOT, "data_duka_full")
    os.makedirs(OUT, exist_ok=True)
    done_marker = os.path.join(OUT, "DONE_BEST")
    if os.path.exists(done_marker):
        log("best: DONE marker exists, nothing to do")
        return
    gate = os.path.join(ROOT, "data_duka", "DONE_SAMPLING")
    while not os.path.exists(gate) and not a.nowait:
        log("best: waiting for sampling job (data_duka/DONE_SAMPLING) ...")
        time.sleep(300)
    from types import SimpleNamespace
    for st in (1, 2, 3, 4):
        while True:
            cmd_fetch(SimpleNamespace(workers=a.workers, phase=str(st)))
            if not pending_tasks([st]):
                break
            log(f"best: stage {st} still has pending days; retrying in 10 min")
            time.sleep(600)
        syms = ORIG if st in (1, 2, 4) else CROSSES
        log(f"best: stage {st} fetched; building data_duka_full for {len(syms)} symbols")
        cmd_build(SimpleNamespace(syms=",".join(syms), out=OUT))
    open(done_marker, "w").write(str(dt.datetime.now()))
    log("best: ALL DONE")


def cmd_status(a):
    tot = done = 0
    for s in SYMS:
        for d in [x for y in range(2005, 2026) for x in daterange(y) if x.weekday() != 5]:
            tot += 1
    n_files = sum(len(fs) for _, _, fs in os.walk(RAW))
    print("raw files:", n_files, " total symbol-days (ex-Sat):", tot)
    for p in (1, 2, 3, 4):
        print("stage", p, "pending symbol-days:", len(pending_tasks([p])))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--workers", type=int, default=8)
    f.add_argument("--phase", default="1,2,3,4")
    b = sub.add_parser("build")
    b.add_argument("--syms", default="")
    b.add_argument("--out", default="")
    bb = sub.add_parser("best")
    bb.add_argument("--workers", type=int, default=2)
    bb.add_argument("--nowait", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "fetch":
        a.workers = min(a.workers, 8)
        cmd_fetch(a)
    elif a.cmd == "build":
        cmd_build(a)
    elif a.cmd == "best":
        a.workers = min(a.workers, 8)
        cmd_best(a)
    else:
        cmd_status(a)
