"""Live SMC status scanner ("follow the market"): for every pair, read closed bars from the Zero account and record
where the setup pipeline stands:  bias (1H/30M/EMA) -> nearest POI -> touched? -> 5M CHoCH? -> entry/SL/TP.
Writes smc_bot/status/status.json, status.md (table) and appends every change to status/history.csv.
READ-ONLY: it never places orders.   Usage: python smc_bot/scanner.py [--loop]
"""
import os, sys, json, time, datetime as dt
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from smc_core import resample, ema, atr, structure, build_zones
from backtest import CFG

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURJPY", "XAUUSD"]
OUT = os.path.join(HERE, "status")
PIP = lambda s: 0.1 if s == "XAUUSD" else (0.01 if "JPY" in s else 0.0001)


def connect():
    load_dotenv(os.path.join(HERE, "..", ".env"))
    ok = mt5.initialize(path=os.getenv("MT5_PATH"), login=int(os.getenv("Z_LOGIN")), password=os.getenv("Z_PASSWORD"),
                        server=os.getenv("Z_SERVER"))
    if not ok:
        raise SystemExit(f"MT5 connect failed {mt5.last_error()}")


def m1_bars(sym, n=20000):
    s = sym + "z"; mt5.symbol_select(s, True)
    r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M1, 1, n)          # from 1 = skip the forming bar
    d = pd.DataFrame(r); d["time"] = pd.to_datetime(d["time"], unit="s")
    d["spr"] = d.spread * mt5.symbol_info(s).point
    return d


def scan(sym, cfg=CFG):
    m1 = m1_bars(sym)
    h1, m30, m5 = (resample(m1, tf) for tf in ("H1", "M30", "M5"))
    sh, sm, s5 = structure(h1, cfg["N_h1"]), structure(m30, cfg["N_m30"]), structure(m5, cfg["N_m5"])
    e200 = ema(h1.close.values, 200); e50 = ema(m30.close.values, 50)
    th, tm = int(sh["trend"][-1]), int(sm["trend"][-1])
    bias = th if th != 0 and th == tm else 0
    now_px = m1.close.iloc[-1]
    z = build_zones(m30, sm, cfg["zone_age_m30"])
    last_t = m5.tclose.iloc[-1]
    z = z[(z.t_expire > last_t)]
    # a zone is still live if no M5 bar after its creation has traded into it
    live = []
    for r in z.itertuples():
        after = m5[m5.time >= r.t_created]
        touched = ((after.low <= r.hi) & (after.high >= r.lo))
        first = after[touched].tclose.iloc[0] if touched.any() else None
        live.append(first)
    z = z.assign(touched_at=live)
    fresh = z[z.touched_at.isna() & (z.dir == bias)] if bias else z.iloc[0:0]
    if len(fresh):
        dist = np.where(fresh.dir == 1, now_px - fresh.hi, fresh.lo - now_px)
        fresh = fresh.assign(dist_pips=dist / PIP(sym)).sort_values("dist_pips")
    recent_touch = z[(z.dir == bias) & z.touched_at.notna()] if bias else z.iloc[0:0]
    recent_touch = recent_touch[recent_touch.touched_at >= last_t - pd.Timedelta(minutes=5 * cfg["wait_choch"])]
    stage, detail = "NO BIAS (1H and 30M disagree)", ""
    if bias:
        stage = "WAITING FOR PRICE TO REACH POI"
        if len(fresh):
            f = fresh.iloc[0]
            detail = f"nearest {f.kind} {f.lo:.5f}-{f.hi:.5f} ({f.dist_pips:.1f} pips away)"
        else:
            detail = "no fresh 30M zone in bias direction"
        if len(recent_touch):
            t = recent_touch.iloc[-1]
            since = m5[m5.tclose > t.touched_at]
            ch = since[(s5["evt"][since.index] == (2 * bias))]
            if len(ch):
                stage = "5M CHoCH CONFIRMED -> ENTRY ON RETEST"
                detail = f"POI {t.kind} touched {t.touched_at:%H:%M}, CHoCH at {ch.tclose.iloc[0]:%H:%M}"
            else:
                stage = "IN POI - WAITING FOR 5M CHoCH"
                detail = f"POI {t.kind} {t.lo:.5f}-{t.hi:.5f} touched {t.touched_at:%H:%M} UTC"
    return dict(pair=sym, time=str(last_t), price=float(now_px), h1_trend=th, m30_trend=tm,
                h1_vs_ema200="above" if h1.close.iloc[-1] > e200[-1] else "below",
                m30_vs_ema50="above" if m30.close.iloc[-1] > e50[-1] else "below",
                bias={1: "BUY", -1: "SELL", 0: "NONE"}[bias], stage=stage, detail=detail, fresh_zones=int(len(fresh)))


def write(rows):
    os.makedirs(OUT, exist_ok=True)
    json.dump(rows, open(os.path.join(OUT, "status.json"), "w"), indent=1, default=str)
    lines = [f"# SMC status  ({dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC)", "",
             "| Pair | Bias | 1H / 30M trend | 1H vs EMA200 | Stage | Detail |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['pair']} | {r['bias']} | {r['h1_trend']:+d} / {r['m30_trend']:+d} | {r['h1_vs_ema200']} | {r['stage']} | {r['detail']} |")
    open(os.path.join(OUT, "status.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    hist = os.path.join(OUT, "history.csv")
    pd.DataFrame(rows).to_csv(hist, mode="a", header=not os.path.exists(hist), index=False)


if __name__ == "__main__":
    connect()
    while True:
        rows = []
        for s in PAIRS:
            try:
                rows.append(scan(s))
            except Exception as e:
                rows.append(dict(pair=s, bias="?", stage=f"error: {e}", detail="", h1_trend=0, m30_trend=0, h1_vs_ema200="?"))
        write(rows)
        print(open(os.path.join(OUT, "status.md"), encoding="utf-8").read())
        if "--loop" not in sys.argv:
            break
        time.sleep(300 - time.time() % 300 + 5)                    # every 5 minutes, just after the M5 close
    mt5.shutdown()
