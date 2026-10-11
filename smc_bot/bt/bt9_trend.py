"""BT9 -- MULTI-DAY TREND FOLLOWING AS THE POSITION (time-series momentum, Moskowitz-Ooi-Pedersen style).

Run from the TTXT root:
    .venv/Scripts/python.exe smc_bot/bt/bt9_trend.py dev                 # all variants on DEV (2021-2023)
    .venv/Scripts/python.exe smc_bot/bt/bt9_trend.py val V03 V05 V09     # <=3 chosen variants on VAL (2024-01..2025-06)
TEST (2025-07+) is never generated: M1 arrays are truncated at the period end before any signal/fill is computed.

FROZEN parameters (literature, no grid):
  daily bar      = 22:00 UTC -> 22:00 UTC bucket (Mon..Fri; Sunday-evening bars belong to Monday)
  decision       = on the CLOSED daily bar (22:00 UTC). Execution = first M1 bar >= 23:00 UTC, i.e. 1h after the
                   decision, chosen from the measured rollover spread spike 21:00-23:00 UTC (not from P&L). After a
                   weekend/holiday gap, execution = 60 min after the market re-opens.
  A  TSMOM       sign(63-day return), sign(126-day return), or both agreeing (flat when they disagree); hold until the
                 signal flips; re-evaluated every daily close. Initial stop 3 x ATR20(daily, true range). After a stop
                 the position is re-opened at the next daily decision if the signal still holds (MOP is always-on).
  B  BT5 bias    sign(H1 close - EMA200(H1)) [or H4 structure trend N=5] at the last CLOSED H1/H4 bar at the daily
                 decision; hold 3 trading days. 'block' = every 3-day hold is a new trade (costs each time);
                 'roll' = at each 3-day check, keep holding if the sign is unchanged. Stop 3 x ATR20 daily.
  C  Turtle      Donchian: close > highest high of the prior 20 (55) days -> long; exit on close < lowest low of the
                 prior 10 (20) days; stop 2 x ATR20 (the 2N stop). Mirror for shorts. Re-entry only on a new breakout.
                 (Daily-close breakouts, not intraday stop orders; no System-1 'skip after winner' filter.)
Execution modes:
  flat  = harness mode: flat before every weekend / >6h market closure (simulate() rule), re-opened with the SAME
          initial stop 60 min after the re-open (unless the gap already crossed the stop -> trade ends). Each weekly
          leg pays spread + commission. All reported 'flat' numbers come from common.simulate() (one call per pair).
  hold  = literature mode: hold through weekends (cannot be done by simulate(), which forces weekend-flat). Own M1
          engine using exactly simulate()'s fill/stop/cost formulas (verified identical to simulate() in flat mode).
R = net pips of the whole logical trade / initial risk pips (fill to initial stop). Swap is NOT in net (no history);
an estimate from the live MT5 swap_long/swap_short snapshot (mt5_swap_snapshot.json) is reported separately.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *          # noqa: F401,F403
import common as CM
import numpy as np, pandas as pd

OUT = os.path.join(RESULTS, "BT9"); os.makedirs(OUT, exist_ok=True)
SWAP = json.load(open(os.path.join(OUT, "mt5_swap_snapshot.json")))
MIN = np.timedelta64(1, "m"); H1 = np.timedelta64(60, "m"); GAP = np.timedelta64(6, "h")
SLIP = CM.STOP_SLIP

VARIANTS = {
    "V01_tsmom63":        dict(fam="A", sig="r63", k=3.0),
    "V02_tsmom126":       dict(fam="A", sig="r126", k=3.0),
    "V03_tsmom_agree":    dict(fam="A", sig="agree", k=3.0),
    "V04_tsmom_agree_nostop": dict(fam="A", sig="agree", k=3.0, nostop=True),
    "V05_ema200_3d_block": dict(fam="B", sig="ema200", k=3.0, roll=False),
    "V06_ema200_3d_roll":  dict(fam="B", sig="ema200", k=3.0, roll=True),
    "V07_h4struct_3d_block": dict(fam="B", sig="h4struct", k=3.0, roll=False),
    "V08_donchian20_10":  dict(fam="C", n_in=20, n_out=10, k=2.0),
    "V09_donchian55_20":  dict(fam="C", n_in=55, n_out=20, k=2.0),
}


# ============================================================ data
class Pair:
    def __init__(self, sym, end):
        self.sym = sym; self.P = pip(sym); self.comm = COMM[sym]
        m = bars(sym, "M1"); m = m[m.time < pd.Timestamp(end)].reset_index(drop=True)
        self.t = m.time.values.astype("datetime64[ns]")
        self.o, self.h, self.l, self.c, self.s = (m[k].values for k in ("open", "high", "low", "close", "spr"))
        t = self.t
        dow = m.time.dt.dayofweek.values; hm = (m.time.dt.hour * 60 + m.time.dt.minute).values
        gap_before = np.zeros(len(t), bool); gap_before[:-1] = np.diff(t) > GAP
        self.wk_idx = np.where(((dow == 4) & (hm >= 20 * 60 + 45)) | gap_before)[0]      # simulate() rule
        self.gap_end = np.where(np.r_[False, np.diff(t) > GAP])[0]                      # first bar after a closure
        # ---- daily bars, 22:00 UTC boundary
        key = (m.time + pd.Timedelta(hours=2)).dt.normalize()
        wd = key.dt.dayofweek
        key = key + pd.to_timedelta(np.where(wd == 5, 2, np.where(wd == 6, 1, 0)), unit="D")
        cnt = key.value_counts()
        small = cnt[cnt < 120].index                          # holiday stubs -> merge into the next trading day
        ukeys = np.sort(cnt[cnt >= 120].index.values)
        kv = key.values
        if len(small):
            ms = np.isin(kv, small.values)
            kv = kv.copy(); j = np.searchsorted(ukeys, kv[ms], "left"); j = np.minimum(j, len(ukeys) - 1)
            kv[ms] = ukeys[j]
        g = pd.DataFrame({"k": kv, "o": self.o, "h": self.h, "l": self.l, "c": self.c}).groupby("k")
        D = pd.DataFrame({"open": g.o.first(), "high": g.h.max(), "low": g.l.min(), "close": g.c.last()})
        D["td"] = D.index + pd.Timedelta(hours=22)
        D = D.reset_index(drop=True)
        pc = D.close.shift(1)
        tr = np.maximum(D.high - D.low, np.maximum((D.high - pc).abs(), (D.low - pc).abs()))
        D["atr20"] = tr.rolling(20).mean()
        self.D = D
        self.td = D.td.values.astype("datetime64[ns]")

    def exec_idx(self, x):
        """First M1 bar >= x, but never within 60 min after a market re-open."""
        a = np.searchsorted(self.t, x, "left")
        if a >= len(self.t):
            return None
        j = np.searchsorted(self.gap_end, a, "right") - 1
        if j >= 0 and self.t[a] - self.t[self.gap_end[j]] < H1:
            a = np.searchsorted(self.t, self.t[self.gap_end[j]] + H1, "left")
        return a if a < len(self.t) else None


def signals(p, v):
    D = p.D; C = D.close.values; n = len(D)
    if v["fam"] == "A":
        r63 = C / np.r_[np.full(63, np.nan), C[:-63]] - 1
        r126 = C / np.r_[np.full(126, np.nan), C[:-126]] - 1
        s63, s126 = np.sign(np.nan_to_num(r63)), np.sign(np.nan_to_num(r126))
        sig = {"r63": s63, "r126": s126, "agree": np.where(s63 == s126, s63, 0)}[v["sig"]]
        valid = ~np.isnan(r126) if v["sig"] != "r63" else ~np.isnan(r63)
        return np.where(valid, sig, 0).astype(int)
    if v["fam"] == "B":
        if v["sig"] == "ema200":
            b = bars(p.sym, "H1"); x = np.sign(b.close.values - ema(b.close.values, 200)); x[:400] = 0
        else:
            b = bars(p.sym, "H4"); x = structure(b, 5)["trend"].astype(float); x[:30] = 0
        i = asof_index(b, p.td)
        return np.where(i >= 0, x[np.maximum(i, 0)], 0).astype(int)
    return None


# ============================================================ engine (same formulas as common.simulate)
def run_leg(p, f, d, sl, e, weekend):
    """Position from fill bar f, exit at close of bar e (planned). Returns dict incl. stop / weekend cut."""
    o, h, l, c, s, P = p.o, p.h, p.l, p.c, p.s, p.P
    hard = e + 1; why = "SIGNAL"
    if weekend:
        wi = p.wk_idx[np.searchsorted(p.wk_idx, f):]
        if len(wi) and wi[0] + 1 < hard:
            hard = wi[0] + 1; why = "WEEKEND"
    fill = o[f] + s[f] if d == 1 else o[f]
    hit = (l[f:hard] <= sl) if d == 1 else (h[f:hard] + s[f:hard] >= sl)
    if hit.any():
        k = f + int(np.argmax(hit))
        if d == 1:
            px = (min(sl, o[k]) - SLIP * P) if k > f else sl - SLIP * P
        else:
            px = (max(sl, o[k] + s[k]) + SLIP * P) if k > f else sl + SLIP * P
        why = "SL"; slip = SLIP
    else:
        k = hard - 1
        px = c[k] if d == 1 else c[k] + s[k]
        slip = 0.0
    spr_in = s[f] / P if d == 1 else 0.0
    spr_out = s[k] / P if d == -1 else 0.0
    net = (px - fill) * d / P - p.comm
    return dict(f=f, k=k, fill=fill, exit=px, why=why, net=net, cost=p.comm + spr_in + spr_out + slip)


def run_trade(p, f, d, sl, e, weekend, sl_check):
    """Logical trade (possibly several weekly legs in flat mode). sl_check=False -> no stop (sl only defines R)."""
    legs = []; cur = f
    sl_eff = sl if sl_check else (-1e9 if d == 1 else 1e9)
    while True:
        lg = run_leg(p, cur, d, sl_eff, e, weekend)
        legs.append(lg)
        if lg["why"] != "WEEKEND":
            return legs
        g = p.gap_end[np.searchsorted(p.gap_end, lg["k"], "right"):]
        if not len(g):
            lg["why"] = "END"; return legs
        a = np.searchsorted(p.t, p.t[g[0]] + H1, "left")
        if a >= e or a >= len(p.t):
            return legs                                         # planned exit falls in the closure: done
        fill = p.o[a] + p.s[a] if d == 1 else p.o[a]
        if sl_check and (fill - sl) * d <= 0:
            lg["why"] = "GAP_PAST_SL"; return legs              # weekend gap crossed the stop while flat
        cur = a


def backtest(p, vname, v, start, end, weekend):
    D = p.D; C = D.close.values; A = D.atr20.values; td = p.td; nD = len(D)
    sig = signals(p, v)
    fam = v["fam"]
    if fam == "C":
        HHi = D.high.shift(1).rolling(v["n_in"]).max().values; LLi = D.low.shift(1).rolling(v["n_in"]).min().values
        HHo = D.high.shift(1).rolling(v["n_out"]).max().values; LLo = D.low.shift(1).rolling(v["n_out"]).min().values
    t_start, t_end = np.datetime64(pd.Timestamp(start), "ns"), np.datetime64(pd.Timestamp(end), "ns")
    last_bar = len(p.t) - 1                                   # arrays already truncated at `end`

    def entry_dir(i):
        if np.isnan(A[i]):
            return 0
        if fam == "C":
            if np.isnan(HHi[i]):
                return 0
            return 1 if C[i] > HHi[i] else (-1 if C[i] < LLi[i] else 0)
        return int(sig[i])

    def exit_day(i, d):
        if fam == "A":
            j = np.where(sig[i + 1:] != d)[0]
            return i + 1 + j[0] if len(j) else None
        if fam == "B":
            j = i + 3
            while j < nD and v["roll"] and sig[j] == d:
                j += 3
            return j if j < nD else None
        cond = (C[i + 1:] < LLo[i + 1:]) if d == 1 else (C[i + 1:] > HHo[i + 1:])
        j = np.where(cond)[0]
        return i + 1 + j[0] if len(j) else None

    rows = []; legs_out = []
    i = int(np.searchsorted(td, t_start, "left"))
    while i < nD and td[i] < t_end:
        d = entry_dir(i)
        if d == 0:
            i += 1; continue
        f = p.exec_idx(td[i] + H1)
        if f is None or f >= last_bar:
            break
        sl = C[i] - d * v["k"] * A[i]
        j = exit_day(i, d)
        e = p.exec_idx(td[j] + H1) if j is not None else None
        why_end = None
        if e is None or e > last_bar:
            e = last_bar; why_end = "END"
        if e <= f:
            i += 1; continue
        legs = run_trade(p, f, d, sl, e, weekend, not v.get("nostop"))
        fill0 = legs[0]["fill"]; risk = (fill0 - sl) * d / p.P
        k_last = legs[-1]["k"]; why = legs[-1]["why"]
        if why == "SIGNAL" and why_end:
            why = "END"
        net = sum(lg["net"] for lg in legs); cost = sum(lg["cost"] for lg in legs)
        tf, tx = pd.Timestamp(p.t[f]), pd.Timestamp(p.t[k_last])
        # swap estimate: rollovers at each daily 22:00 UTC boundary held (Mon..Fri), triple on the MT5 3-day weekday
        sw = SWAP[p.sym]; pts = sw["swap_long"] if d == 1 else sw["swap_short"]; tri = sw["swap_rollover3days"] - 1
        nights = 0
        for lg in legs:
            a_, b_ = np.searchsorted(td, p.t[lg["f"]], "right"), np.searchsorted(td, p.t[lg["k"]], "right")
            wdays = pd.DatetimeIndex(td[a_:b_]).dayofweek
            nights += int(np.sum(np.where(wdays == tri, 3, 1)))
        swap_pips = nights * pts * sw["point"] / p.P
        rows.append(dict(variant=vname, mode="flat" if weekend else "hold", side=d, t_decision=pd.Timestamp(td[i]),
                         t_signal=tf, entry_type="market", entry=np.nan, sl=sl, tp=np.nan, t_fill=tf, fill=fill0,
                         t_exit=tx, exit=legs[-1]["exit"], exit_reason=why, n_legs=len(legs), risk_pips=risk,
                         gross_pips=net + cost, cost_pips=cost, net_pips=net, R_gross=(net + cost) / risk, R=net / risk,
                         usd_01=net * USD_PIP_01[p.sym], swap_pips_est=swap_pips, R_after_swap=(net + swap_pips) / risk,
                         usd_01_after_swap=(net + swap_pips) * USD_PIP_01[p.sym], hold_h=(tx - tf) / pd.Timedelta(hours=1),
                         status="filled", sig_close=C[i], atr20=A[i]))
        for lg in legs:
            legs_out.append(dict(trade_no=len(rows) - 1, f=lg["f"], k=lg["k"], d=d, sl=sl if not v.get("nostop") else
                                 (-1e9 if d == 1 else 1e9), why=lg["why"], net=lg["net"], cost=lg["cost"], risk=risk))
        # next decision
        if why in ("SIGNAL",) and j is not None:
            i = j
        else:
            i = int(np.searchsorted(td, p.t[k_last], "right"))
            if why in ("WEEKEND", "GAP_PAST_SL") and j is not None:
                i = max(i, j) if why == "WEEKEND" else i
    T = pd.DataFrame(rows)
    if len(T):
        T.insert(0, "sym", p.sym)
        T["period"] = period_of(T.t_signal)
    return T, pd.DataFrame(legs_out)


# ============================================================ harness replay of every flat-mode leg through simulate()
def via_simulate(p, T, L):
    """Feed every leg as a market order to common.simulate(); rebuild each logical trade from simulate()'s fills/exits.
    Returns T with net/R from simulate and a mismatch count vs the engine."""
    if not len(L):
        return T, 0
    orders = pd.DataFrame(dict(t_signal=pd.to_datetime(p.t[L.f.values]), side=L.d.values, entry_type="market",
                               entry=np.nan, expire=pd.NaT, sl=L.sl.values, tp=np.nan, be_at_r=np.nan,
                               max_hold_min=np.where(L.why.values == "SIGNAL", (p.t[L.k.values] - p.t[L.f.values]) / MIN,
                                                     np.where(L.why.values == "END",
                                                              (p.t[L.k.values] - p.t[L.f.values]) / MIN, 9 * 1440)),
                               leg=np.arange(len(L)), trade_no=L.trade_no.values))
    S = simulate(p.sym, orders, one_at_a_time=True)
    S = S.sort_values("leg").reset_index(drop=True)
    tk = np.searchsorted(p.t, S.t_exit.values.astype("datetime64[ns]"))
    mism = int(((S.status != "filled") | (tk != L.k.values) | (np.abs(S.net_pips.values - L.net.values) > 1e-6)).sum())
    g = S.assign(net=S.net_pips).groupby("trade_no").net.sum()
    T = T.copy()
    T["net_pips"] = g.reindex(range(len(T))).values
    T["R"] = T.net_pips / T.risk_pips
    T["usd_01"] = T.net_pips * USD_PIP_01[p.sym]
    T["R_after_swap"] = (T.net_pips + T.swap_pips_est) / T.risk_pips
    T["usd_01_after_swap"] = (T.net_pips + T.swap_pips_est) * USD_PIP_01[p.sym]
    return T, mism


# ============================================================ daily mark-to-market (for Sharpe / block bootstrap)
def daily_mtm(p, T, L):
    """Daily R increments (at 22:00 UTC closes, bid marks) for the pair. Sum over days == sum of net R."""
    out = np.zeros(len(p.td))
    db = np.searchsorted(p.t, p.td, "left") - 1               # last bar before each daily boundary
    for r in L.itertuples(index=False):
        f, k, d = r.f, r.k, r.d
        fill = p.o[f] + p.s[f] if d == 1 else p.o[f]
        days = np.where((db >= f) & (db < k))[0]
        marks = (p.c[db[days]] - fill) * d / p.P
        dex = min(int(np.searchsorted(p.td, p.t[k], "right")), len(p.td) - 1)
        vals = np.r_[marks, r.net]; idx = np.r_[days, dex]
        inc = np.diff(np.r_[0.0, vals]) / r.risk
        np.add.at(out, idx, inc)
    return pd.Series(out, index=pd.DatetimeIndex(p.td).normalize())


def boot_t(x, block=20, nb=2000, seed=9):
    """Stationary block bootstrap of the mean of a daily series: returns (t = mean / boot se, p(mean<=0))."""
    x = np.asarray(x, float); n = len(x)
    if n < 40 or x.std() == 0:
        return np.nan, np.nan
    rng = np.random.default_rng(seed); means = np.empty(nb)
    for b in range(nb):
        idx = np.empty(n, int); i = 0
        while i < n:
            st = rng.integers(n); L = min(rng.geometric(1 / block), n - i)
            idx[i:i + L] = (st + np.arange(L)) % n; i += L
        means[b] = x[idx].mean()
    se = means.std()
    return round(x.mean() / se, 2), round(float((means <= 0).mean()), 3)


def summarize(T, daily, label):
    s = stats(T)
    fx = T[T.sym != "XAUUSD"]; au = T[T.sym == "XAUUSD"]
    pp = T.groupby("sym").R.sum()
    tt = T.R.mean() / (T.R.std() / np.sqrt(len(T))) if len(T) > 2 else np.nan
    mon = T.groupby(T.t_exit.dt.to_period("M")).R.sum()
    tm = mon.mean() / (mon.std() / np.sqrt(len(mon))) if len(mon) > 2 else np.nan
    bt, bp = boot_t(daily.values)
    sh = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else np.nan
    return {"variant": label, **s, "avg_hold_d": round(T.hold_h.mean() / 24, 1), "avg_legs": round(T.n_legs.mean(), 1),
            "avg_R_gross": round(T.R_gross.mean(), 3), "total_R_gross": round(T.R_gross.sum(), 1),
            "avg_gross_pips": round(T.gross_pips.mean(), 1), "avg_cost_pips": round(T.cost_pips.mean(), 2),
            "avg_risk_pips": round(T.risk_pips.mean(), 1),
            "avg_R_after_swap": round(T.R_after_swap.mean(), 3), "usd_after_swap": round(T.usd_01_after_swap.sum(), 0),
            "pairs_pos": int((pp > 0).sum()),
            "long_n": int((T.side == 1).sum()), "long_avgR": round(T[T.side == 1].R.mean(), 3),
            "short_n": int((T.side == -1).sum()), "short_avgR": round(T[T.side == -1].R.mean(), 3),
            "FX9_avgR": round(fx.R.mean(), 3), "FX9_totalR": round(fx.R.sum(), 1),
            "XAU_avgR": round(au.R.mean(), 3), "XAU_totalR": round(au.R.sum(), 1),
            "t_trade": round(tt, 2), "t_monthly": round(tm, 2), "t_boot_daily": bt, "p_boot": bp,
            "sharpe_daily_ann": round(sh, 2)}


def run(period, names):
    start, end = PERIODS[period]
    summ, perpair = [], []
    cache = {}
    allT = {(n, m): [] for n in names for m in ("flat", "hold")}
    allD = {(n, m): [] for n in names for m in ("flat", "hold")}
    mism_total = 0
    for sym in PAIRS:
        p = Pair(sym, end); cache[sym] = p
        for n in names:
            for mode in ("flat", "hold"):
                T, L = backtest(p, n, VARIANTS[n], start, end, weekend=(mode == "flat"))
                if not len(T):
                    continue
                if mode == "flat":
                    T, mm = via_simulate(p, T, L); mism_total += mm
                    if mm:
                        print(f"  !! {sym} {n}: {mm} legs differ engine vs simulate()")
                allT[(n, mode)].append(T); allD[(n, mode)].append(daily_mtm(p, T, L).rename(sym))
        print(sym, "done", flush=True)
    for (n, mode), lst in allT.items():
        if not lst:
            continue
        T = pd.concat(lst, ignore_index=True)
        T = T[T.period == period]
        daily = pd.concat(allD[(n, mode)], axis=1).fillna(0)
        daily = daily[(daily.index >= start) & (daily.index < end)]
        daily = daily.loc[daily.index >= T.t_fill.min().normalize()].sum(axis=1)
        summ.append({"mode": mode, "period": period, **summarize(T, daily, n)})
        for sym, g in T.groupby("sym"):
            perpair.append({"variant": n, "mode": mode, "period": period, "sym": sym, **stats(g),
                            "avg_hold_d": round(g.hold_h.mean() / 24, 1), "avg_R_gross": round(g.R_gross.mean(), 3),
                            "long_avgR": round(g[g.side == 1].R.mean(), 3), "short_avgR": round(g[g.side == -1].R.mean(), 3),
                            "avg_R_after_swap": round(g.R_after_swap.mean(), 3)})
        save("BT9", f"{n}_{mode}_{period}", T)
        # per-year + long/short x gold/FX breakdown
        yr = T.assign(year=T.t_signal.dt.year, grp=np.where(T.sym == "XAUUSD", "XAU", "FX9"),
                      dir=np.where(T.side == 1, "long", "short"))
        br = []
        for keys, g in yr.groupby(["grp", "dir"]):
            br.append({"cut": "grp_dir", "key": "/".join(map(str, keys)), **stats(g), "avg_R_gross": round(g.R_gross.mean(), 3)})
        for keys, g in yr.groupby("year"):
            br.append({"cut": "year", "key": str(keys), **stats(g), "avg_R_gross": round(g.R_gross.mean(), 3)})
        pd.DataFrame(br).to_csv(os.path.join(OUT, f"breakdown_{n}_{mode}_{period}.csv"), index=False)
    S = pd.DataFrame(summ); PP = pd.DataFrame(perpair)
    S.to_csv(os.path.join(OUT, f"variants_{period}.csv"), index=False)
    PP.to_csv(os.path.join(OUT, f"perpair_{period}.csv"), index=False)
    print(f"engine vs simulate() leg mismatches: {mism_total}")
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 50)
    print(S[["variant", "mode", "trades", "per_week", "win_pct", "avg_R_gross", "avg_R", "total_R", "PF", "maxDD_R",
             "usd_per_01lot", "avg_hold_d", "avg_legs", "pairs_pos", "long_avgR", "short_avgR", "FX9_avgR", "XAU_avgR",
             "avg_R_after_swap", "t_trade", "t_monthly", "t_boot_daily", "sharpe_daily_ann"]].to_string(index=False))


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if what == "dev":
        run("DEV", list(VARIANTS))
    elif what == "val":
        names = sys.argv[2:]
        assert 1 <= len(names) <= 3, "at most 3 variants go to VAL"
        run("VAL", names)
