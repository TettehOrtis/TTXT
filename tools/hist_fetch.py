"""HistData.com M1 (ASCII, BID) bulk downloader. One zip per pair-year. Resumable, 1 connection, polite delay.

  .venv\\Scripts\\python.exe tools\\hist_fetch.py [--stages 1,2,3,4]
Output: data_duka/histdata_raw/{SYM}_{YYYY}.zip  (zero-byte {SYM}_{YYYY}.none = not offered)
HistData timestamps are EST fixed UTC-5 (no DST). Conversion to UTC is done in tools/duka_build.py.
2025 is intentionally skipped: the year zip would contain 2025-07-01+ (locked TEST period).
"""
import argparse, datetime as dt, io, os, random, re, sys, time, zipfile
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data_duka", "histdata_raw")
LOGF = os.path.join(ROOT, "smc_bot", "results", "DATA2", "hist_fetch.log")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
ORIG = "EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD EURGBP EURJPY XAUUSD".split()
CROSSES = ("GBPJPY AUDJPY EURAUD EURCHF GBPCHF CADJPY NZDJPY AUDCAD AUDNZD EURCAD GBPAUD "
           "CHFJPY GBPCAD AUDCHF NZDCAD").split()
LAST_YEAR = 2024
DELAY = 1.5


def log(m):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOGF), exist_ok=True)
    with open(LOGF, "a") as f:
        f.write(line + "\n")


def stages():
    return {
        1: [(ORIG, [2021, 2022, 2023])],
        2: [(ORIG, list(range(2020, 2004, -1)))],
        3: [(CROSSES, list(range(2024, 2004, -1)))],
        4: [(ORIG, [2024])],
    }


S = requests.Session()
S.headers["User-Agent"] = UA
_avail = {}


def base(sym):
    return f"http://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{sym.lower()}"


def robust_get(u, **kw):
    for i in range(12):
        try:
            r = S.get(u, timeout=(30, 120), **kw)
            if r.status_code == 200:
                return r
            log(f"GET {u} -> {r.status_code}")
        except requests.RequestException as e:
            log(f"GET error {type(e).__name__}")
        time.sleep(min(120, 5 * 2 ** min(i, 5)) * random.uniform(.8, 1.2))
    raise RuntimeError("giving up " + u)


def years_available(sym):
    if sym not in _avail:
        r = robust_get(base(sym))
        _avail[sym] = {int(x) for x in re.findall(r"1-minute-bar-quotes/%s/(\d{4})(?:[\"'/])" % sym.lower(), r.text)}
        time.sleep(DELAY)
    return _avail[sym]


def fetch(sym, y):
    zp = os.path.join(RAW, f"{sym}_{y}.zip")
    np = os.path.join(RAW, f"{sym}_{y}.none")
    if os.path.exists(zp) or os.path.exists(np):
        return "cached"
    assert y <= LAST_YEAR
    if y not in years_available(sym):
        open(np, "wb").close()
        log(f"{sym} {y}: not offered")
        return "none"
    page = f"{base(sym)}/{y}"
    for attempt in range(10):
        r = robust_get(page)
        m = re.search(r'id="tk"\s+value="([0-9a-f]+)"', r.text)
        if not m:
            log(f"{sym} {y}: no tk token"); time.sleep(10); continue
        time.sleep(DELAY)
        form = dict(tk=m.group(1), date=str(y), datemonth=str(y), platform="ASCII", timeframe="M1", fxpair=sym)
        try:
            p = S.post("http://www.histdata.com/get.php", data=form, headers={"Referer": page}, timeout=(30, 300))
        except requests.RequestException as e:
            log(f"{sym} {y}: POST error {type(e).__name__}"); time.sleep(20 * (attempt + 1)); continue
        if p.status_code == 200 and p.content[:2] == b"PK":
            try:
                zipfile.ZipFile(io.BytesIO(p.content)).testzip()
            except Exception as e:
                log(f"{sym} {y}: bad zip {e!r}"); time.sleep(10); continue
            os.makedirs(RAW, exist_ok=True)
            tmp = zp + ".tmp"
            open(tmp, "wb").write(p.content)
            os.replace(tmp, zp)
            log(f"{sym} {y}: ok {len(p.content)/1e6:.2f} MB")
            return "ok"
        log(f"{sym} {y}: POST {p.status_code} {p.content[:80]!r}")
        time.sleep(20 * (attempt + 1))
    return "fail"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="1,2,3,4")
    a = ap.parse_args()
    st = stages()
    for s in [int(x) for x in a.stages.split(",")]:
        for _ in range(3):
            fails = 0
            for syms, years in st[s]:
                for y in years:
                    for sym in syms:
                        r = fetch(sym, y)
                        if r == "fail":
                            fails += 1
                        if r in ("ok", "none"):
                            time.sleep(DELAY)
            if not fails:
                break
            time.sleep(120)
        log(f"stage {s} complete")
    log("ALL DONE")
