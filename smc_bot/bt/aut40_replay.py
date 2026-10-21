"""Replay of the live MomentumEA logic (variant 4) on exported broker M1 bars, bar by bar, like a live stream.
Usage: python aut40_replay.py EXPORT.csv [LIVE_TRADES.csv]
Modes: PLAIN (5 legs, one exit, every bucket full), SIZE (= v2.20: plain exit + size rule), V3 (= T3 split + stop to entry + slot + size rule).
Costs: the broker's real spread per bar; the research simulator's phantom commission is added back (Standard account has none)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut35_shaperule as A
import common as C

STOP, TARGET, OFF = 6.89, 5.11, 0.11
POINT = 0.001
CHAIN_S = 125
WEAK = 0.4
FR, WTS_FULL, WTS_WEAK = (0.5, 0.75, 1.0), (0.2, 0.3, 0.5), (0.25, 0.25, 0.5)


def load_export(path):
    m = pd.read_csv(path)
    m["time"] = pd.to_datetime(m["time"], format="%Y.%m.%d %H:%M")
    m = m.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    m["spr"] = m["spread"] * POINT
    return m


def signals(m):
    c, o = m.close.values, m.open.values
    e20 = pd.Series(c).ewm(span=20, adjust=False).mean().values
    ts = m.set_index("time")
    m30 = ts.close.resample("30min", label="left", closed="left").last().dropna()
    e100 = m30.ewm(span=100, adjust=False).mean()
    # trend at M1 bar j = last M30 bar CLOSED by the open of bar j: M30 bar opening at X is closed from X+30min
    closed_at = m30.index + pd.Timedelta(minutes=30)
    tr = pd.Series(np.where(m30.values > e100.values, 1, -1), index=closed_at)
    t1 = pd.DatetimeIndex(m.time.values)
    idx = tr.index.searchsorted(t1, side="right") - 1
    trend = np.where(idx >= 0, tr.values[np.clip(idx, 0, None)], 0)
    sig = np.zeros(len(m), int)                                  # sig[j] = decision at the open of bar j, from bar j-1
    up, dn = (c > o) & (c > e20), (c < o) & (c < e20)
    sig[1:] = np.where((trend[1:] == 1) & up[:-1], 1, np.where((trend[1:] == -1) & dn[:-1], -1, 0))
    # the first 3 days warm the averages
    sig[t1 < t1[0] + pd.Timedelta(days=3)] = 0
    return sig


def allowed(t):
    dow, hr, mi = t.dayofweek, t.hour, t.minute          # Monday = 0
    if dow == 5 or dow == 6:
        return False
    if 20 <= hr < 24:
        return False
    if dow == 0 and hr < 7:
        return False
    if dow == 4 and hr * 60 + mi >= 20 * 60 + 45:
        return False
    return True


def replay(m, mode, start):
    F = {"m1": m, "o": m.open.values, "h": m.high.values, "l": m.low.values, "c": m.close.values}
    S = A.Sim(F)
    sig = signals(m)
    t1 = S.t1
    out, release, last_win, last_rel = [], np.datetime64("1970-01-01"), False, None
    j = 1
    n = len(m)
    while j < n - 2:
        if t1[j] < np.datetime64(start) or sig[j] == 0 or t1[j] < release or not allowed(pd.Timestamp(t1[j])):
            j += 1
            continue
        d = int(sig[j])
        fill = S.o[j] + S.s[j] if d == 1 else S.o[j]
        stop = fill - d * STOP
        full = True
        if mode != "PLAIN":
            gap = (t1[j] - last_rel) / np.timedelta64(1, "s") if last_rel is not None else 1e9
            full = bool(last_win and gap <= CHAIN_S)
        size = 1.0 if full else WEAK
        if mode == "V3":
            tps = [fill + d * (f * (TARGET - OFF) + OFF) for f in FR]
            wts = WTS_FULL if full else WTS_WEAK
            r = S.trade(j, d, fill, stop, tps, wts, None, True, 1440)
            rb = S.trade(j, d, fill, stop, [fill + d * TARGET], (1.0,), None, False, 1440)
            if r is None or rb is None:
                j += 1
                continue
            k = r[1]
            kb = max(r[1], rb[1])
        else:
            tps = [fill + d * TARGET]
            r = S.trade(j, d, fill, stop, tps, (1.0,), None, False, 1440)
            if r is None:
                j += 1
                continue
            k = kb = r[1]
        usd = (r[0] + A.COMM * A.USD) * size            # add back the phantom commission (Standard account has none)
        release = t1[kb]
        last_win = usd > 0
        last_rel = t1[kb] + np.timedelta64(30, "s")
        out.append(dict(open=pd.Timestamp(t1[j]), side="BUY" if d == 1 else "SELL", size="full" if full else "weak", usd=round(usd, 2),
                        end=pd.Timestamp(t1[k]), how="/".join(r[2]), release=pd.Timestamp(release)))
        j = max(j + 1, kb)       # the next entry can only start at/after the release bar
    return pd.DataFrame(out)


def summary(t, label):
    if len(t) == 0:
        return dict(rule=label, trades=0)
    u = t.usd.values
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    cum = np.cumsum(u)
    return dict(rule=label, trades=len(t), win_pct=round(100 * (u > 0).mean(), 1), pf=round(gp / gl, 2) if gl else np.nan, net=round(u.sum(), 1),
                avg=round(u.mean(), 2), worst_dd=round((np.maximum.accumulate(cum) - cum).max(), 1))


if __name__ == "__main__":
    m = load_export(sys.argv[1])
    print("bars", len(m), m.time.min(), "->", m.time.max())
    end = m.time.max()
    rows, res = [], {}
    for days in (5, 10, 20):
        start = end - pd.Timedelta(days=days)
        for mode in ("PLAIN", "SIZE", "V3"):
            t = replay(m, mode, start)
            res[(days, mode)] = t
            s = summary(t, f"last {days}d {mode}")
            rows.append(s)
    pd.set_option("display.width", 200)
    print(pd.DataFrame(rows).to_string(index=False))
    res[(10, "V3")].to_csv(os.path.splitext(sys.argv[1])[0] + "_replay_v3_10d.csv", index=False)
    if len(sys.argv) > 2:
        live = pd.read_csv(sys.argv[2], header=0, names=list("abcdefghijklmn"), usecols=range(14), engine="python", on_bad_lines="skip")
    # since the 3.00 start (2026-10-20 08:10) side by side
    t = replay(m, "V3", pd.Timestamp("2026-10-20 08:00"))
    print(t.to_string(index=False))
