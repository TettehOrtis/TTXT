"""Shared backtest harness for every SMC backtest (BT1..BTn). Import:  from bt.common import *
(run scripts from the TTXT root:  .venv/Scripts/python.exe smc_bot/bt/bt1_sweep.py)

Rules that every backtest inherits:
- Data: Exness ZERO account M1 bid bars (../data_zero), 2021+. Spread from data (bid+spr = ask). Real commission.
- Date split (PLAN.md): DEV 2021-2023, VAL 2024-01..2025-06, TEST 2025-07.. (LOCKED: reports hide it unless include_test=True).
- Signals must be decided on CLOSED bars; an order becomes active at `t_signal` = close time of the decision bar.
- Simulation on M1: SL checked before TP inside a bar (pessimistic); TP never counts on the fill bar; stops slip 0.2 pip;
  flat before the weekend (Fri 20:45 UTC); one position per pair at a time (optional).
"""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SMC = os.path.dirname(HERE)
ROOT = os.path.dirname(SMC)
sys.path.insert(0, SMC)
sys.path.insert(0, os.path.join(ROOT, "research"))
# Dataset switch (default unchanged): SMC_DATASET=duka -> Dukascopy M1 2005+ in ../data_duka, own bar cache, adds period HIST.
# Duka costs = the real Dukascopy spread of each minute + Zero commission (conservative: Zero spread is ~0).
# BT14: SMC_DATASET=gbm -> synthetic zero-edge bars served from GBM_BARS (filled by bt/bt14_gbm.py); real data is
# never touched in that mode (bars() raises instead of falling back to disk).
DATASET = os.environ.get("SMC_DATASET", "zero")
if DATASET == "duka":
    os.environ["DATA_DIR"] = "data_duka"
else:
    os.environ.setdefault("DATA_DIR", "data_zero")
GBM_BARS = {}                                              # (sym, tf) -> DataFrame, only used when DATASET == "gbm"
os.chdir(ROOT)                                             # data paths (data/, data_zero/) are relative to TTXT
from smc_core import resample, ema, atr, swings, structure, fvgs, build_zones, first_touch, TF_MIN  # noqa: E402,F401

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURJPY", "XAUUSD"]
PERIODS = {"DEV": ("2021-01-01", "2024-01-01"), "VAL": ("2024-01-01", "2025-07-01"), "TEST": ("2025-07-01", "2030-01-01")}
COMM = dict(EURUSD=0.50, GBPUSD=0.63, USDJPY=0.79, AUDUSD=0.60, USDCAD=0.99, NZDUSD=0.90, EURGBP=0.83, EURJPY=1.10,
            USDCHF=0.87, XAUUSD=1.10)                      # round-trip pips (gold: $0.10 units), measured 2026-09-27
USD_PIP_01 = dict(EURUSD=1.0, GBPUSD=1.0, AUDUSD=1.0, NZDUSD=1.0, USDJPY=0.64, EURJPY=0.64, USDCAD=0.71, USDCHF=1.21,
                  EURGBP=1.32, XAUUSD=1.0)                 # $ per pip at 0.10 lot (approx, 2026 prices)
STOP_SLIP = 0.2                                            # pips
CACHE = os.path.join(SMC, "cache")
LOAD_START = "2021-01-01"
if DATASET == "duka":
    EXTRA = ["GBPJPY", "AUDJPY", "EURAUD", "EURCHF", "GBPCHF", "CADJPY", "NZDJPY", "AUDCAD", "AUDNZD", "EURCAD", "GBPAUD",
             "CHFJPY", "GBPCAD", "AUDCHF", "NZDCAD"]
    PAIRS = PAIRS + EXTRA
    PERIODS = {"HIST": ("2005-01-01", "2021-01-01"), **PERIODS}
    # crosses: Zero commission not measured -> assume $4/lot/side ($8 round trip) / pip value per lot (2026 prices)
    _pv = dict(JPY=6.4, CHF=12.1, AUD=6.5, CAD=7.1, NZD=5.9, GBP=13.2)
    for _s in EXTRA:
        COMM[_s] = round(8.0 / _pv[_s[3:]], 2)
        USD_PIP_01[_s] = _pv[_s[3:]] / 10
    CACHE = os.path.join(SMC, "cache_duka")
    LOAD_START = "2005-01-01"
RESULTS = os.path.join(SMC, "results")


def pip(sym):
    return 0.1 if sym == "XAUUSD" else (0.01 if "JPY" in sym else 0.0001)


def period_of(ts):
    ts = pd.to_datetime(ts)
    out = pd.Series("TEST", index=range(len(ts))) if hasattr(ts, "__len__") else "TEST"
    for name, (a, b) in PERIODS.items():
        m = (ts >= a) & (ts < b)
        if hasattr(ts, "__len__"):
            out[np.asarray(m)] = name
        elif m:
            return name
    return out.values if hasattr(ts, "__len__") else out


# ------------------------------------------------------------------ bars (cached)
def bars(sym, tf):
    """tf in M1, M5, M15, M30, H1, H4. Columns: time (open), open, high, low, close, spr (price units), tclose."""
    if DATASET == "gbm":                                   # BT14 placebo data: in-memory only, never read real prices
        if (sym, tf) in GBM_BARS:
            return GBM_BARS[(sym, tf)]
        raise FileNotFoundError(f"SMC_DATASET=gbm: synthetic bars for {sym} {tf} were not generated "
                                f"(real data is deliberately unreachable in this mode)")
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, f"{sym}_{tf}.parquet")
    if os.path.exists(f):
        b = pd.read_parquet(f)
        # cached parquet stores ms/us resolution; mixing it with ns timestamps makes every searchsorted O(n) (BT5 finding)
        for col in ("time", "tclose"):
            b[col] = b[col].astype("datetime64[ns]")
        return b
    from lib import load
    m1 = load(sym, LOAD_START)[["time", "open", "high", "low", "close", "spr"]].copy()
    m1["spr"] = m1.spr.clip(lower=0) * pip(sym)
    if tf == "M1":
        m1["tclose"] = m1.time + pd.Timedelta(minutes=1)
        b = m1
    else:
        b = resample(m1, tf)
    b.to_parquet(f, index=False)
    return b


def asof_index(tf_bars, t):
    """Index of the last tf bar that has CLOSED at or before time(s) t (-1 if none)."""
    return np.searchsorted(tf_bars.tclose.values, np.asarray(t, dtype="datetime64[ns]"), "right") - 1


# ------------------------------------------------------------------ simulator
def simulate(sym, orders, one_at_a_time=True, cancel_limit_if_tp_first=True, max_days=10):
    """orders: DataFrame with columns
        t_signal (Timestamp, order active from here), side (+1/-1), entry_type ('market'|'limit'),
        entry (limit price; ignored for market), expire (Timestamp; limit orders only), sl, tp (NaN = none),
        be_at_r (NaN = none; move SL to entry+commission once price has gone this many R in favour),
        max_hold_min (NaN = none), plus ANY extra columns (sl_reason, tp_reason, context...) which are kept.
    Returns one row per order with status (filled / not_filled / invalid / skipped_busy) and results."""
    m1 = bars(sym, "M1")
    t1 = m1.time.values; o = m1.open.values; h = m1.high.values; l = m1.low.values; c = m1.close.values; s = m1.spr.values
    dow = m1.time.dt.dayofweek.values; hm = (m1.time.dt.hour * 60 + m1.time.dt.minute).values
    weekend = (dow == 4) & (hm >= 20 * 60 + 45)
    # also flat before ANY market closure (holiday early closes, e.g. 24/31 Dec): last bar before a gap > 6h (BT2 finding)
    gap_before = np.zeros(len(t1), bool)
    gap_before[:-1] = np.diff(t1) > np.timedelta64(6, "h")
    wk_idx = np.where(weekend | gap_before)[0]
    P = pip(sym); comm = COMM[sym]; slip = STOP_SLIP * P
    orders = orders.sort_values("t_signal", kind="stable").reset_index(drop=True)   # stable: equal t_signal keeps input order (BT3 finding)
    res = []
    busy_until = np.datetime64("1970-01-01")
    for r in orders.itertuples(index=False):
        row = r._asdict()
        d = int(r.side)
        tsig = np.datetime64(pd.Timestamp(r.t_signal))
        if one_at_a_time and tsig < busy_until:
            res.append({**row, "status": "skipped_busy"}); continue
        a = np.searchsorted(t1, tsig, "left")
        if a >= len(t1) - 2:
            res.append({**row, "status": "no_data"}); continue
        # ---------------- entry
        if r.entry_type == "market":
            f = a
            fill = o[f] + s[f] if d == 1 else o[f]
        else:
            e = min(np.searchsorted(t1, np.datetime64(pd.Timestamp(r.expire)), "left"), len(t1))
            if e <= a:
                res.append({**row, "status": "not_filled"}); continue
            if d == 1:
                hit = (l[a:e] + s[a:e]) <= r.entry
                ran = h[a:e] >= r.tp if cancel_limit_if_tp_first and not np.isnan(r.tp) else np.zeros(e - a, bool)
            else:
                hit = h[a:e] >= r.entry
                ran = (l[a:e] + s[a:e]) <= r.tp if cancel_limit_if_tp_first and not np.isnan(r.tp) else np.zeros(e - a, bool)
            fi = np.argmax(hit) if hit.any() else -1
            ri = np.argmax(ran) if ran.any() else -1
            if fi < 0 or (ri >= 0 and ri < fi):
                res.append({**row, "status": "not_filled"})
                if one_at_a_time:
                    busy_until = max(busy_until, t1[min(e, len(t1) - 1)])
                continue
            f = a + fi
            fill = min(r.entry, o[f] + s[f]) if d == 1 else max(r.entry, o[f])
        sl = r.sl
        risk = (fill - sl) * d
        if not risk > 0 and r.entry_type == "limit" and (r.entry - sl) * d > 0:
            # limit filled on a gap that is already beyond the stop: stopped out at once (BT1 finding B4; was silently dropped)
            px = fill - d * slip
            planned = (r.entry - sl) * d
            net = (px - fill) * d / P - comm
            res.append({**row, "status": "filled", "t_fill": pd.Timestamp(t1[f]), "fill": fill, "t_exit": pd.Timestamp(t1[f]),
                        "exit": px, "exit_reason": "GAP_SL", "hold_h": 0.0, "risk_pips": planned / P,
                        "gross_pips": (px - fill) * d / P, "net_pips": net, "R": net / (planned / P),
                        "usd_01": net * USD_PIP_01[sym], "mfe_R": 0.0, "mae_R": 0.0})
            if one_at_a_time:
                busy_until = t1[f]
            continue
        if not risk > 0 or (not np.isnan(r.tp) and (r.tp - fill) * d <= 0):
            res.append({**row, "status": "invalid", "fill": fill}); continue
        # ---------------- exit
        end = min(f + max_days * 1440, len(t1))
        if not np.isnan(r.max_hold_min):                       # clock minutes, not bar count (BT1 finding B3)
            end = min(end, np.searchsorted(t1, t1[f] + np.timedelta64(int(r.max_hold_min), "m"), "right"))
        wi = wk_idx[np.searchsorted(wk_idx, f):]
        hard = min(end, wi[0] + 1) if len(wi) else end
        tp = r.tp
        be = r.be_at_r if not np.isnan(r.be_at_r) else np.nan

        def first_exit(start, stop_price):
            """First exit in [start, hard) for a given stop. Returns (idx, price, reason) or None."""
            if start >= hard:
                return None
            sl_hit = (l[start:hard] <= stop_price) if d == 1 else (h[start:hard] + s[start:hard] >= stop_price)
            if np.isnan(tp):
                tp_hit = np.zeros(hard - start, bool)
            else:
                tp_hit = (h[start:hard] >= tp) if d == 1 else (l[start:hard] + s[start:hard] <= tp)
            if start == f:
                tp_hit[0] = False                                  # never TP on the fill bar
            si = np.argmax(sl_hit) if sl_hit.any() else hard
            ti = np.argmax(tp_hit) if tp_hit.any() else hard
            if si == hard and ti == hard:
                return None
            if si <= ti:                                           # SL first (also when same bar)
                k = start + si
                px = (min(stop_price, o[k]) - slip) if (d == 1 and k > f) else ((stop_price - slip) if d == 1 else
                      (max(stop_price, o[k] + s[k]) + slip if k > f else stop_price + slip))
                return k, px, "SL"
            return start + ti, tp, "TP"

        ex = first_exit(f, sl)
        if not np.isnan(be):
            trig = (h[f:hard] >= fill + be * risk) if d == 1 else (l[f:hard] + s[f:hard] <= fill - be * risk)
            bi = f + int(np.argmax(trig)) if trig.any() else hard
            if bi < hard and (ex is None or ex[0] > bi):                 # BE reached before any exit: move stop
                ex = first_exit(bi + 1, fill + d * comm * P)              # (active from the next minute)
                if ex is not None and ex[2] == "SL":
                    ex = (ex[0], ex[1], "BE")
        if ex is None:
            k = hard - 1
            px = c[k] if d == 1 else c[k] + s[k]
            why = "WEEKEND" if len(wi) and hard == wi[0] + 1 else ("TIME" if hard == end else "END")
            ex = (k, px, why)
        k, px, why = ex
        seg_h, seg_l = h[f:k + 1], l[f:k + 1]
        mfe = ((seg_h.max() - fill) if d == 1 else (fill - (seg_l + s[f:k + 1]).min())) / risk
        mae = ((fill - seg_l.min()) if d == 1 else ((seg_h + s[f:k + 1]).max() - fill)) / risk
        gross = (px - fill) * d / P
        net = gross - comm
        res.append({**row, "status": "filled", "t_fill": pd.Timestamp(t1[f]), "fill": fill, "t_exit": pd.Timestamp(t1[k]),
                    "exit": px, "exit_reason": why, "hold_h": (t1[k] - t1[f]) / np.timedelta64(1, "h"),
                    "risk_pips": risk / P, "gross_pips": gross, "net_pips": net, "R": net / (risk / P),
                    "usd_01": net * USD_PIP_01[sym], "mfe_R": mfe, "mae_R": mae})
        if one_at_a_time:
            busy_until = t1[k]
    out = pd.DataFrame(res)
    out.insert(0, "sym", sym)
    out["period"] = period_of(out.t_signal) if len(out) else []
    return out


# ------------------------------------------------------------------ reporting
def stats(t):
    if len(t) == 0:
        return dict(trades=0)
    t = t.sort_values("t_exit", kind="stable")              # drawdown must follow time, not pair order (BT1 finding B1)
    R = t.R.values
    eq = np.cumsum(R); dd = (eq - np.maximum.accumulate(np.maximum(eq, 0))).min()
    weeks = max(1.0, (t.t_fill.max() - t.t_fill.min()) / pd.Timedelta(weeks=1))
    gp, gl = R[R > 0].sum(), -R[R < 0].sum()
    return dict(trades=len(t), per_week=round(len(t) / weeks, 2), win_pct=round((R > 0).mean() * 100, 1),
                avg_R=round(R.mean(), 3), total_R=round(R.sum(), 1), PF=round(gp / gl, 2) if gl > 0 else np.inf,
                maxDD_R=round(dd, 1), avg_net_pips=round(t.net_pips.mean(), 2), usd_per_01lot=round(t.usd_01.sum(), 0),
                avg_hold_h=round(t.hold_h.mean(), 1))


def report_table(trades, by=("period",), include_test=False):
    t = trades[trades.status == "filled"]
    if not include_test:
        t = t[t.period != "TEST"]
    rows = []
    for k, g in t.groupby(list(by)):
        rows.append({**dict(zip(by, k if isinstance(k, tuple) else (k,))), **stats(g)})
    return pd.DataFrame(rows)


def save(bt, name, trades, include_test=False):
    """Write results/<bt>/trades_<name>.csv (TEST rows removed unless include_test) and return the period table."""
    d = os.path.join(RESULTS, bt); os.makedirs(d, exist_ok=True)
    t = trades if include_test else trades[trades.period != "TEST"]
    t.to_csv(os.path.join(d, f"trades_{name}.csv"), index=False)
    return report_table(trades, include_test=include_test)
