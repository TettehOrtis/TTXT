"""Research library: data loading, cost-realistic bracket simulator, features.

Conventions
- Bars are MT5 BID OHLC. Ask = bid + spread. `spr` is per-bar spread in PIPS.
- Decision is made at the CLOSE of bar t; the order fills at the OPEN of bar t+1
  (one bar of latency) with `slip` pips of adverse slippage.
- Long: buy at ask, exit at bid.  Short: sell at bid, exit at ask (bid + spread).
- If TP and SL are both touched inside one bar, SL wins (pessimistic).
- Stop exits get a gap-aware fill (worse of stop price / bar open) plus slippage.
- Limit TP exits require price to trade THROUGH the TP by `tp_through` pips.
"""
import numpy as np
import pandas as pd
from numba import njit


import json, os
_META = json.load(open("data/meta.json")) if os.path.exists("data/meta.json") else {}
# "unit" = the price increment all barrier/cost numbers are expressed in (a "pip" for FX)
_UNIT = {"XAUUSD": 0.10, "US30": 1.0, "DE30": 1.0, "USTEC": 1.0, "USOIL": 0.01, "BTCUSD": 1.0}


def pip_size(sym):
    if sym in _UNIT:
        return _UNIT[sym]
    return 0.01 if "JPY" in sym else 0.0001


def point_size(sym):
    if sym in _META:
        return _META[sym]["point"]
    return 0.001 if "JPY" in sym else 0.00001


DATA_DIR = os.environ.get("DATA_DIR", "data")      # "data_zero" = Zero-account history


def _path(sym):
    p = f"{DATA_DIR}/{sym}_M1.parquet"
    return p if os.path.exists(p) else f"data/{sym}_M1.parquet"


def load(sym, start="2021-01-01"):
    d = pd.read_parquet(_path(sym))
    d = d[d.time >= start].reset_index(drop=True)
    d["spr"] = (d.spread * point_size(sym) / pip_size(sym)).astype("float64")
    return d


@njit(cache=True)
def simulate(op, hi, lo, cl, spr, idx, side, tp, sl, maxhold, slip, tp_through, pip):
    """Simulate one bracket trade per signal. Returns (net_pips, bars_held, reason).
    reason: 1=TP, -1=SL, 0=timeout, 9=invalid."""
    n = len(idx)
    nb = len(op)
    pnl = np.full(n, np.nan)
    held = np.zeros(n, np.int32)
    why = np.full(n, 9, np.int8)
    for k in range(n):
        e = idx[k] + 1
        if e >= nb - 1:
            continue
        end = min(e + maxhold, nb)
        if side[k] == 1:
            entry = op[e] + (spr[e] + slip) * pip
            tpp = entry + tp[k] * pip
            slp = entry - sl[k] * pip
            r = 0
            ex = 0.0
            j = e
            while j < end:
                if lo[j] <= slp:  # stop first (pessimistic)
                    ex = min(slp, op[j]) - slip * pip
                    r = -1
                    break
                if hi[j] >= tpp + tp_through * pip:
                    ex = tpp
                    r = 1
                    break
                j += 1
            if r == 0:
                j = end - 1
                ex = cl[j] - slip * pip
            pnl[k] = (ex - entry) / pip
        else:
            entry = op[e] - slip * pip
            tpp = entry - tp[k] * pip
            slp = entry + sl[k] * pip
            r = 0
            ex = 0.0
            j = e
            while j < end:
                if hi[j] + spr[j] * pip >= slp:
                    ex = max(slp, op[j] + spr[j] * pip) + slip * pip
                    r = -1
                    break
                if lo[j] + spr[j] * pip <= tpp - tp_through * pip:
                    ex = tpp
                    r = 1
                    break
                j += 1
            if r == 0:
                j = end - 1
                ex = cl[j] + spr[j] * pip + slip * pip
            pnl[k] = (entry - ex) / pip
        held[k] = j - e + 1
        why[k] = r
    return pnl, held, why


def run_sim(d, sym, idx, side, tp, sl, maxhold=30, slip=0.2, tp_through=0.1):
    idx = np.asarray(idx, dtype=np.int64)
    side = np.broadcast_to(np.asarray(side), idx.shape).astype(np.int8).copy()
    tp = np.broadcast_to(np.asarray(tp, dtype=np.float64), idx.shape).copy()
    sl = np.broadcast_to(np.asarray(sl, dtype=np.float64), idx.shape).copy()
    return simulate(d.open.values, d.high.values, d.low.values, d.close.values,
                    d.spr.values, idx, side, tp, sl, maxhold, slip, tp_through, pip_size(sym))


# ------------------------------------------------------------------ features
def _rsi(c, n):
    dl = c.diff()
    up = dl.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-dl.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / (dn + 1e-12))


def features(d, sym):
    """All features use information available at the CLOSE of the bar (no look-ahead)."""
    p = pip_size(sym)
    c = d.close
    f = pd.DataFrame(index=d.index)
    rng = (d.high - d.low) / p
    atr14 = rng.rolling(14).mean()
    atr60 = rng.rolling(60).mean()
    f["atr14"] = atr14
    f["atr60"] = atr60
    f["atr_ratio"] = atr14 / (atr60 + 1e-9)
    f["spr"] = d.spr
    f["spr_over_atr"] = d.spr / (atr14 + 1e-9)
    for n in (1, 2, 3, 5, 10, 20, 30, 60, 120, 240):
        f[f"ret{n}"] = (c - c.shift(n)) / p / (atr60 + 1e-9)          # vol-normalised
    f["rsi2"] = _rsi(c, 2)
    f["rsi14"] = _rsi(c, 14)
    for n in (20, 60):
        m = c.rolling(n).mean()
        s = c.rolling(n).std()
        f[f"bbz{n}"] = (c - m) / (s + 1e-12)
    for n in (20, 50, 200):
        f[f"emad{n}"] = (c - c.ewm(span=n, adjust=False).mean()) / p / (atr60 + 1e-9)
    hh, ll = d.high.rolling(60).max(), d.low.rolling(60).min()
    f["rngpos60"] = (c - ll) / (hh - ll + 1e-12)
    hh, ll = d.high.rolling(240).max(), d.low.rolling(240).min()
    f["rngpos240"] = (c - ll) / (hh - ll + 1e-12)
    body = (d.close - d.open) / p
    f["body"] = body / (atr14 + 1e-9)
    f["upwick"] = (d.high - np.maximum(d.open, d.close)) / p / (atr14 + 1e-9)
    f["dnwick"] = (np.minimum(d.open, d.close) - d.low) / p / (atr14 + 1e-9)
    tv = d.tick_volume.astype(float)
    f["tvol_rel"] = tv / (tv.rolling(60).mean() + 1e-9)
    f["tvol_rel5"] = tv.rolling(5).mean() / (tv.rolling(60).mean() + 1e-9)
    hr = d.time.dt.hour + d.time.dt.minute / 60
    f["hr_sin"] = np.sin(2 * np.pi * hr / 24)
    f["hr_cos"] = np.cos(2 * np.pi * hr / 24)
    f["hour"] = d.time.dt.hour
    f["dow"] = d.time.dt.dayofweek
    # consecutive same-direction bars
    dirn = np.sign(c.diff()).fillna(0)
    grp = (dirn != dirn.shift()).cumsum()
    f["streak"] = dirn * (dirn.groupby(grp).cumcount() + 1)
    return f


def active_mask(d, h0=7, h1=20):
    """Trade only in liquid hours; never near the 21:00 UTC rollover or the Friday close."""
    h = d.time.dt.hour
    m = (h >= h0) & (h < h1)
    fri_late = (d.time.dt.dayofweek == 4) & (h >= 19)
    return (m & ~fri_late).values


# ------------------------------------------------------------------ features v2 (session + cross-asset)
def features2(d, sym, others=("EURUSD", "USDJPY", "US30", "USOIL", "BTCUSD"), frames=None):
    """frames: optional {sym: DataFrame(time, high, low, close)} for cross-asset inputs (live mode); else read parquet."""
    f = features(d, sym)
    p = pip_size(sym)
    c, t = d.close, d.time
    atr60 = f["atr60"] * p
    day = t.dt.floor("D")
    g = d.groupby(day)
    day_open = g.open.transform("first")
    f["d_open"] = (c - day_open) / (atr60 + 1e-12)
    dh, dl = g.high.cummax(), g.low.cummin()
    f["d_hi"] = (dh - c) / (atr60 + 1e-12)
    f["d_lo"] = (c - dl) / (atr60 + 1e-12)
    f["d_rngpos"] = (c - dl) / (dh - dl + 1e-12)
    daily = d.groupby(day).agg(h=("high", "max"), l=("low", "min"), c=("close", "last"))
    daily["ph"], daily["pl"], daily["pc"] = daily.h.shift(1), daily.l.shift(1), daily.c.shift(1)
    pm = daily.loc[day.values, ["ph", "pl", "pc"]].reset_index(drop=True)
    pm.index = d.index
    f["pd_hi"] = (pm.ph - c) / (atr60 + 1e-12)
    f["pd_lo"] = (c - pm.pl) / (atr60 + 1e-12)
    f["pd_close"] = (c - pm.pc) / (atr60 + 1e-12)
    # Asian range (00:00-07:00 UTC) of the current day
    hr = t.dt.hour
    asian = (hr < 7)
    ah = d.high.where(asian).groupby(day).transform("max")
    al = d.low.where(asian).groupby(day).transform("min")
    f["asia_hi"] = ((ah - c) / (atr60 + 1e-12)).where(~asian, 0.0)
    f["asia_lo"] = ((c - al) / (atr60 + 1e-12)).where(~asian, 0.0)
    mins = hr * 60 + t.dt.minute
    f["m_since_lon"] = (mins - 7 * 60).clip(lower=-1)
    f["m_since_ny"] = (mins - 13 * 60 - 30).clip(lower=-1)
    # cross-asset returns (aligned on time, forward-filled a few bars)
    for o in others:
        if o == sym:
            continue
        if frames is not None:
            if o not in frames:
                continue
            od = frames[o][["time", "high", "low", "close"]].drop_duplicates("time").set_index("time")
        elif os.path.exists(_path(o)):
            od = pd.read_parquet(_path(o), columns=["time", "high", "low", "close"]).drop_duplicates("time").set_index("time")
        else:
            continue
        oc = od.close.reindex(t.values, method="ffill", tolerance=pd.Timedelta("3min")).reset_index(drop=True)
        orng = ((od.high - od.low).rolling(60).mean()).reindex(t.values, method="ffill", tolerance=pd.Timedelta("3min")).reset_index(drop=True)
        for n in (1, 5, 15):
            f[f"x_{o}_r{n}"] = (oc - oc.shift(n)) / (orng + 1e-12)
    return f


def prep_live(df, sym):
    """Give live MT5 rates the same columns load() produces."""
    df = df.copy()
    df["spr"] = (df.spread * point_size(sym) / pip_size(sym)).astype("float64")
    return df
