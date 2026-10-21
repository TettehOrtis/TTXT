"""AUT16 addendum 12: why gold works and forex does not. Open windows only (2005-2020). Usage: python aut29_diagnose.py"""
import os, sys
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import common as C
import autopsy_lib as A

PAIRS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURJPY"]
WIN = {"W1": ("2005-01-01", "2011-01-01"), "W2": ("2011-01-01", "2017-01-01"), "W3": ("2017-01-01", "2021-01-01")}
HS = (5, 15, 30, 60)
COSTUSD = {"XAUUSD": 1.60, "EURUSD": 1.00, "GBPUSD": 1.13, "USDJPY": (0.79 + 0.5) * 0.64, "AUDUSD": 1.10, "USDCAD": (0.99 + 0.5) * 0.71,
           "USDCHF": (0.87 + 0.5) * 1.21, "NZDUSD": 1.40, "EURGBP": (0.83 + 0.5) * 1.32, "EURJPY": (1.10 + 0.5) * 0.64}
PIPUSD = {"XAUUSD": 1.0, "EURUSD": 1.0, "GBPUSD": 1.0, "AUDUSD": 1.0, "NZDUSD": 1.0, "USDJPY": 0.64, "EURJPY": 0.64, "USDCAD": 0.71, "USDCHF": 1.21, "EURGBP": 1.32}


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def vr(c, q):
    r1 = np.diff(np.log(c))
    s = np.cumsum(np.concatenate([[0.0], r1]))
    rq = s[q:] - s[:-q]
    return float(np.var(rq) / (q * np.var(r1)))


rows = []
for sym in PAIRS:
    m1 = C.bars(sym, "M1")
    m5 = C.bars(sym, "M5")
    m15 = C.bars(sym, "M15")
    o, h, l, c = m1.open.values, m1.high.values, m1.low.values, m1.close.values
    tc = m1.tclose.values
    e200 = ema(m15.close.values, 200)
    tr15 = np.where(m15.close.values > e200, 1, -1)
    atr15 = A.atr(m15, 14).values
    j = C.asof_index(m15, tc)
    ok = j >= 260
    jj = np.clip(j, 0, len(m15) - 1)
    trend = np.where(ok, tr15[jj], 0)
    atr = np.where(ok, atr15[jj], np.nan)
    n = len(m1)
    for w, (a, z) in WIN.items():
        msk = (tc >= np.datetime64(a)) & (tc < np.datetime64(z))
        if msk.sum() < 100000:
            continue
        idx = np.flatnonzero(msk)
        idx = idx[(idx > 5) & (idx < n - 70)]
        days = pd.Series(m1.time.values[idx]).dt.date.nunique()
        pipv = PIPUSD[sym]
        # size and cost
        med_atr_price = float(np.nanmedian(atr[idx]))
        pipsz = C.pip(sym)
        atr_usd = med_atr_price / pipsz * pipv      # M15 range, dollars at 0.10 lot
        cost = COSTUSD[sym]
        # variance ratios on this window's closes
        cw = c[idx[0]:idx[-1] + 1]
        v5, v15, v60 = vr(cw, 5), vr(cw, 15), vr(cw, 60)
        c5 = m5.close.values[(m5.tclose.values >= np.datetime64(a)) & (m5.tclose.values < np.datetime64(z))]
        r5 = np.diff(np.log(c5))
        ac1 = float(np.corrcoef(r5[:-1], r5[1:])[0, 1])
        tt = trend[idx]
        with_candle = ((tt == 1) & (c[idx] > o[idx])) | ((tt == -1) & (c[idx] < o[idx]))
        against = ((tt == 1) & (c[idx] < o[idx])) | ((tt == -1) & (c[idx] > o[idx]))
        for hh in HS:
            fwd = (c[idx + hh] - o[idx + 1]) * tt / atr[idx]          # move in the trend direction, in M15 ranges
            okk = np.isfinite(fwd) & (tt != 0)
            def ms(mask):
                x = fwd[mask & okk]
                return float(x.mean()), float(1.96 * x.std(ddof=1) / np.sqrt(len(x))), len(x)
            sig = ms(with_candle)
            base = ms(np.ones(len(idx), bool))
            ag = ms(against)
            rows.append(dict(sym=sym, win=w, h=hh, days=days, atr_usd=round(atr_usd, 2), cost_usd=round(cost, 2), cost_share_pct=round(100 * cost / atr_usd, 1),
                             vr5=round(v5, 3), vr15=round(v15, 3), vr60=round(v60, 3), ac5=round(ac1, 4),
                             sig_move=sig[0], sig_pm=sig[1], sig_n=sig[2], trend_only=base[0], against=ag[0], against_pm=ag[1],
                             sig_per_day=round(sig[2] / days, 1)))
        print(sym, w, "done", flush=True)
d = pd.DataFrame(rows)
d.to_csv(os.path.join(C.RESULTS, "AUT16_layers", "DIAGNOSE_gold_vs_fx.csv"), index=False)
print("saved", len(d))
