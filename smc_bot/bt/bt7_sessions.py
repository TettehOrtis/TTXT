"""BT7 - SESSIONS, TIMING AND VOLATILITY.
Run from the TTXT root:
    .venv/Scripts/python.exe smc_bot/bt/bt7_sessions.py desc            # descriptive tables (DEV only)
    .venv/Scripts/python.exe smc_bot/bt/bt7_sessions.py dev             # all trade variants on DEV
    .venv/Scripts/python.exe smc_bot/bt/bt7_sessions.py val V1 V2 V3    # <=3 chosen variants on VAL (after DEV review)
Never touches TEST (dates >= 2025-07-01 are never even generated).

Time model (all times UTC, data is true UTC: weekly open shifts 22:05/21:05 with US DST, rollover spread spike at 21/22h):
  scheme "dst": London open = 08:00 Europe/London, NY open = 08:00 America/New_York, killzones = 3h from those opens
                (= 07-10 / 12-15 UTC in summer, 08-11 / 13-16 UTC in winter)
  scheme "utc": the fixed brief killzones London 07-10 UTC, NY 12-15 UTC all year (1h earlier than 'dst' in winter)
  Asia = 00:00 UTC -> London open ; London = London open -> NY open ; Overlap = NY open -> London close (16:00 UK);
  NY = London close -> 17:00 NY ; Off = 17:00 NY -> 24:00 UTC (rollover, wide spreads)
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *          # noqa: F401,F403  (bars, simulate, stats, save, PAIRS, COMM, pip, atr, asof_index ...)
import numpy as np, pandas as pd

OUT = os.path.join(RESULTS, "BT7"); os.makedirs(OUT, exist_ok=True)
DEV_END = np.datetime64("2024-01-01"); VAL_END = np.datetime64("2025-07-01")   # TEST starts 2025-07-01: never generated
H = pd.Timedelta(hours=1)


def dt64(x):
    return np.datetime64(pd.Timestamp(x), "ns")


# ============================================================ calendar (DST-aware session boundaries per UTC date)
def make_cal(start="2020-12-28", end="2025-06-30"):
    d = pd.date_range(start, end, freq="D")
    def loc(h, tz):
        return (d + pd.Timedelta(hours=h)).tz_localize(tz).tz_convert("UTC").tz_localize(None)
    cal = pd.DataFrame({"date": d, "wd": d.dayofweek})
    cal["ldn_open"] = loc(8, "Europe/London"); cal["ldn_close"] = loc(16, "Europe/London")
    cal["ny_open"] = loc(8, "America/New_York"); cal["ny_close"] = loc(17, "America/New_York")
    cal["summer"] = cal.ldn_open.dt.hour == 7
    cal["d0"] = d; cal["d24"] = d + pd.Timedelta(days=1)
    cal["ldn_u"] = d + 7 * H; cal["ny_u"] = d + 12 * H                     # fixed-UTC scheme opens
    return cal.set_index("date", drop=False)


CAL = make_cal()


def windows(cal):
    """name -> (start Series, end Series) of UTC times, per date."""
    W = {"Asia": (cal.d0, cal.ldn_open), "London": (cal.ldn_open, cal.ny_open), "Overlap": (cal.ny_open, cal.ldn_close),
         "NY": (cal.ldn_close, cal.ny_close), "Off(rollover)": (cal.ny_close, cal.d24),
         "KZ_LDN": (cal.ldn_open, cal.ldn_open + 3 * H), "KZ_NY": (cal.ny_open, cal.ny_open + 3 * H),
         "KZ_LDN_utc0710": (cal.ldn_u, cal.ldn_u + 3 * H), "KZ_NY_utc1215": (cal.ny_u, cal.ny_u + 3 * H)}
    return W


WIN = windows(CAL)
# fast per-date lookup (pandas .loc in inner loops was ~1000x slower)
CD = {r.date: r for r in CAL.itertuples(index=False)}
WN = {k: (dict(zip(CAL.index, v[0].values)), dict(zip(CAL.index, v[1].values))) for k, v in WIN.items()}


def prev_wd(d):
    d = pd.Timestamp(d) - pd.Timedelta(days=1)
    while d.dayofweek >= 5:
        d -= pd.Timedelta(days=1)
    return d


# ============================================================ small helpers
def md(df, floatfmt=2, index=False):
    """Markdown table without tabulate."""
    df = df.reset_index() if index else df
    cols = list(df.columns)
    def f(x):
        if isinstance(x, (float, np.floating)):
            return "" if np.isnan(x) else ("inf" if np.isinf(x) else f"{x:.{floatfmt}f}")
        return str(x)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in df.itertuples(index=False):
        lines.append("| " + " | ".join(f(x) for x in r) + " |")
    return "\n".join(lines)


def dev_mask(t):        # t: datetime64 array
    return np.asarray(t, dtype="datetime64[ns]") < DEV_END


def in_win(times, name):
    """bool array: bar open time within window `name` of its own UTC date."""
    t = pd.DatetimeIndex(times)
    dts = t.normalize()
    a = WIN[name][0].reindex(dts).values; b = WIN[name][1].reindex(dts).values
    return (t.values >= a) & (t.values < b)


# ============================================================ PART 1: descriptive statistics (DEV only)
def h1_table(sym):
    b = bars(sym, "H1"); b = b[b.time < pd.Timestamp(DEV_END)].copy()
    P = pip(sym)
    b["date"] = b.time.dt.normalize(); b["hour"] = b.time.dt.hour; b["wd"] = b.time.dt.dayofweek
    b = b[b.wd < 5].copy()                                    # Sunday evening stub bars dropped
    b["rng"] = (b.high - b.low) / P; b["body"] = (b.close - b.open).abs() / P
    b["spr_p"] = b.spr / P
    # forward 4h range starting at this hour (contiguous bars only)
    hh, ll, tt = b.high.values, b.low.values, b.time.values
    r4 = np.full(len(b), np.nan)
    for i in range(len(b) - 3):
        if tt[i + 3] - tt[i] == np.timedelta64(3, "h"):
            r4[i] = (hh[i:i + 4].max() - ll[i:i + 4].min()) / P
    b["rng4"] = r4
    b["summer"] = CAL.summer.reindex(b.date).values
    for w in ["Asia", "London", "Overlap", "NY", "Off(rollover)", "KZ_LDN", "KZ_NY", "KZ_LDN_utc0710", "KZ_NY_utc1215"]:
        b["w_" + w] = in_win(b.time.values, w)
    b["sym"] = sym
    return b


def m15_atr_table(sym):
    b = bars(sym, "M15"); b = b[b.time < pd.Timestamp(DEV_END)].copy()
    b["atr_p"] = atr(b, 14) / pip(sym); b["hour"] = b.time.dt.hour
    b["summer"] = CAL.summer.reindex(b.time.dt.normalize()).values
    b["wd"] = b.time.dt.dayofweek
    return b[b.wd < 5].dropna(subset=["atr_p"])


def window_daily_ranges(sym, names):
    """per (date, window): high-low in pips from M5 bars; DEV only, Mon-Fri."""
    b = bars(sym, "M5"); b = b[b.time < pd.Timestamp(DEV_END)]
    t = b.time.values.astype("datetime64[ns]"); h = b.high.values; l = b.low.values; P = pip(sym)
    rows = []
    for d in CAL.index[(CAL.index >= "2021-01-04") & (CAL.index < pd.Timestamp(DEV_END)) & (CAL.wd < 5)]:
        for nme in names:
            a = dt64(WN[nme][0][d]); e = dt64(WN[nme][1][d])
            i0, i1 = np.searchsorted(t, a, "left"), np.searchsorted(t, e, "left")
            exp = (e - a) / np.timedelta64(5, "m")
            if i1 - i0 < 0.6 * exp:
                continue
            rows.append((d, nme, (h[i0:i1].max() - l[i0:i1].min()) / P))
    return pd.DataFrame(rows, columns=["date", "win", "rng"]).assign(sym=sym)


def descriptive():
    res = {}
    H1 = pd.concat([h1_table(s) for s in PAIRS], ignore_index=True)
    A15 = pd.concat([m15_atr_table(s).assign(sym=s) for s in PAIRS], ignore_index=True)
    comm = pd.Series(COMM)
    H1["cost"] = H1.sym.map(comm) + H1.spr_p                    # round-trip: commission + bar-mean spread (pips)

    # ---- hour of day (UTC) x pair : median 1h range, cost % of median 1h range, cost % of median 4h range
    hr = H1.groupby(["sym", "hour"]).agg(rng_mean=("rng", "mean"), rng_med=("rng", "median"), rng4_med=("rng4", "median"),
                                          spr=("spr_p", "mean"), n=("rng", "size")).reset_index()
    hr["cost"] = hr.sym.map(comm) + hr.spr
    hr["cost_pct_1h"] = hr.cost / hr.rng_med * 100; hr["cost_pct_4h"] = hr.cost / hr.rng4_med * 100
    at = A15.groupby(["sym", "hour"]).atr_p.mean().rename("atr15_pips").reset_index()
    hr = hr.merge(at, on=["sym", "hour"])
    hr.to_csv(os.path.join(OUT, "desc_hour_by_pair.csv"), index=False)
    res["hr"] = hr
    # per pair, per season
    hs = H1.groupby(["sym", "summer", "hour"]).agg(rng_med=("rng", "median"), rng_mean=("rng", "mean"), spr=("spr_p", "mean")).reset_index()
    hs["cost"] = hs.sym.map(comm) + hs.spr; hs["cost_pct_1h"] = hs.cost / hs.rng_med * 100
    # activity index (hour mean range / that pair's mean over hours 0-20) pooled over 9 FX pairs (gold shown separately)
    base = hs[hs.hour <= 20].groupby(["sym", "summer"]).rng_mean.mean().rename("base").reset_index()
    hs = hs.merge(base, on=["sym", "summer"]); hs["idx"] = hs.rng_mean / hs.base
    hs.to_csv(os.path.join(OUT, "desc_hour_by_pair_season.csv"), index=False)
    res["hs"] = hs

    # ---- sessions x pair
    names = ["Asia", "London", "Overlap", "NY", "KZ_LDN", "KZ_NY", "KZ_LDN_utc0710", "KZ_NY_utc1215"]
    WR = pd.concat([window_daily_ranges(s, names) for s in PAIRS], ignore_index=True)
    WR.to_csv(os.path.join(OUT, "desc_window_daily_ranges.csv"), index=False)
    sess = WR.groupby(["sym", "win"]).rng.agg(["mean", "median"]).reset_index()
    length = {n: float(((WIN[n][1] - WIN[n][0]).dt.total_seconds() / 3600).mean()) for n in names}
    sess["hours"] = sess.win.map(length)
    # typical 1h and 4h move inside the window and the cost
    rows = []
    for (s, w), g in [((s, w), H1[(H1.sym == s) & H1["w_" + w]]) for s in PAIRS for w in names]:
        cost = COMM[s] + g.spr_p.mean()
        rows.append((s, w, g.rng.median(), g.rng4.median(), cost, cost / g.rng.median() * 100, cost / g.rng4.median() * 100))
    sc = pd.DataFrame(rows, columns=["sym", "win", "rng1h_med", "rng4h_med", "cost_pips", "cost_pct_1h", "cost_pct_4h"])
    sess = sess.merge(sc, on=["sym", "win"]); sess["pips_per_hour"] = sess["mean"] / sess.hours
    sess.to_csv(os.path.join(OUT, "desc_session_by_pair.csv"), index=False)
    res["sess"] = sess

    # ---- weekday
    d1 = H1.groupby(["sym", "date", "wd"]).agg(hi=("high", "max"), lo=("low", "min")).reset_index()
    d1["rng"] = 0.0
    for s in PAIRS:
        m = d1.sym == s; d1.loc[m, "rng"] = (d1.loc[m, "hi"] - d1.loc[m, "lo"]) / pip(s)
    wdt = d1.groupby(["sym", "wd"]).rng.mean().unstack()
    kzw = WR[WR.win.isin(["KZ_LDN", "KZ_NY"])].assign(wd=lambda x: pd.to_datetime(x.date).dt.dayofweek)
    kzw = kzw.groupby(["sym", "win", "wd"]).rng.mean().unstack()
    wdt.to_csv(os.path.join(OUT, "desc_weekday_daily_range.csv")); kzw.to_csv(os.path.join(OUT, "desc_weekday_kz_range.csv"))
    res["wdt"] = wdt; res["kzw"] = kzw

    # ---- robustness: are the hour rankings stable across the three DEV years?
    H1["year"] = H1.time.dt.year
    yr = H1.groupby(["sym", "year", "hour"]).rng.median().reset_index()
    yr["rel"] = yr.groupby(["sym", "year"]).rng.transform(lambda x: x / x.mean())
    res["yr"] = yr
    return res


# ============================================================ PART 1b: sweep events (descriptive + trade signals)
class Pair:
    """cached arrays for one pair"""
    def __init__(self, sym):
        self.sym = sym; self.P = pip(sym)
        self.b5 = bars(sym, "M5"); self.b15 = bars(sym, "M15")
        self.t5 = self.b5.time.values.astype("datetime64[ns]"); self.h5 = self.b5.high.values; self.l5 = self.b5.low.values
        self.a15 = atr(self.b15, 14)


def ref_range(pr, ref, d, sch):
    """Reference high/low known BEFORE the killzone starts. Returns (hi, lo, ok)."""
    c = CD[d]
    if sch == "dst":
        ldn, ny = c.ldn_open, c.ny_open
    else:
        ldn, ny = c.ldn_u, c.ny_u
    if ref == "asia":
        a, e = c.d0, ldn
    elif ref == "london":
        a, e = ldn, ny
    elif ref == "overnight":
        a, e = c.d0, ny
    elif ref == "pd":                                         # previous FX day: prev-prev NY close -> prev NY close
        p = prev_wd(d); pp = prev_wd(p)
        a, e = CD[pp].ny_close, CD[p].ny_close
    a, e = dt64(a), dt64(e)
    i0, i1 = np.searchsorted(pr.t5, a, "left"), np.searchsorted(pr.t5, e, "left")
    exp = (e - a) / np.timedelta64(5, "m")
    if ref == "pd":
        exp = 0.5 * (24 * 12)                                 # weekends/holidays: just need a real day of data
    if i1 - i0 < 0.6 * exp or i1 - i0 < 6:
        return np.nan, np.nan, False
    return pr.h5[i0:i1].max(), pr.l5[i0:i1].min(), True


def sweep_events(sym, kz, ref, sch="dst", tf="M5", maxwait_min=30, buf_atr=0.25, dates=None, pr=None):
    """One row per (date, swept side) where the killzone traded beyond the reference high/low.
    Causal signal = close of the first bar that closes back inside the range (within `maxwait_min` of the first breach)."""
    pr = pr or Pair(sym); P = pr.P
    tfb = pr.b5 if tf == "M5" else pr.b15
    tt, hh, ll, cc, tc = tfb.time.values.astype("datetime64[ns]"), tfb.high.values, tfb.low.values, tfb.close.values, tfb.tclose.values.astype("datetime64[ns]")
    tfmin = 5 if tf == "M5" else 15
    maxwait = max(1, maxwait_min // tfmin)
    rows = []
    for d in dates:
        c = CD[d]
        ldn, ny = (c.ldn_open, c.ny_open) if sch == "dst" else (c.ldn_u, c.ny_u)
        ks, hold_end = (ldn, ny) if kz == "ldn" else (ny, ny + pd.Timedelta(hours=8.5))
        ke = ks + 3 * H
        ks_, ke_ = dt64(ks), dt64(ke)
        RH, RL, ok = ref_range(pr, ref, d, sch)
        if not ok or RH <= RL:
            continue
        i0, i1 = np.searchsorted(tt, ks_, "left"), np.searchsorted(tt, ke_, "left")
        if i1 - i0 < 0.6 * (3 * 60 / tfmin):
            continue
        mid = 0.5 * (RH + RL); rng_p = (RH - RL) / P
        for sw in (+1, -1):                                   # +1: high swept (bearish reversal candidate)
            br = np.where(hh[i0:i1] > RH)[0] if sw == 1 else np.where(ll[i0:i1] < RL)[0]
            if len(br) == 0:
                continue
            j = i0 + br[0]
            k = -1
            for q in range(j, min(j + maxwait, i1)):
                if (cc[q] < RH) if sw == 1 else (cc[q] > RL):
                    k = q; break
            ev = dict(sym=sym, date=d, wd=int(c.wd), kz=kz, ref=ref, sch=sch, tf=tf, sweep=sw, ref_hi=RH, ref_lo=RL,
                      ref_rng_pips=rng_p, t_breach=pd.Timestamp(tt[j]), rejected=k >= 0, summer=bool(c.summer))
            ext_all = hh[j:i1].max() if sw == 1 else ll[j:i1].min()
            ev["max_depth_pips"] = ((ext_all - RH) if sw == 1 else (RL - ext_all)) / P
            if k < 0:
                ev["outcome"] = "breakout"
                rows.append(ev); continue
            sig = pd.Timestamp(tc[k])
            E = hh[j:k + 1].max() if sw == 1 else ll[j:k + 1].min()
            ia = asof_index(pr.b15, np.array([dt64(sig)]))[0]
            buf = buf_atr * pr.a15[ia] if ia >= 14 else np.nan
            if np.isnan(buf):
                continue
            entry_c = cc[k]
            sl = E + buf if sw == 1 else E - buf
            opp = RL if sw == 1 else RH
            # outcome on M5 bars after the signal, until hold_end
            a5 = np.searchsorted(pr.t5, dt64(sig), "left"); e5 = np.searchsorted(pr.t5, dt64(hold_end), "left")
            hs, ls = pr.h5[a5:e5], pr.l5[a5:e5]
            if sw == 1:
                st = np.argmax(hs >= sl) if (hs >= sl).any() else 10**9
                mi = np.argmax(ls <= mid) if (ls <= mid).any() else 10**9
                oi = np.argmax(ls <= opp) if (ls <= opp).any() else 10**9
            else:
                st = np.argmax(ls <= sl) if (ls <= sl).any() else 10**9
                mi = np.argmax(hs >= mid) if (hs >= mid).any() else 10**9
                oi = np.argmax(hs >= opp) if (hs >= opp).any() else 10**9
            out = "stopped" if st <= mi and st < 10**9 else ("reverse" if mi < 10**9 else "neither")
            ev.update(t_signal=sig, sweep_extreme=E, buf=buf, entry_close=entry_c, sl=sl, opp=opp, mid=mid, hold_end=hold_end,
                      risk_pips=abs(sl - entry_c) / P, tp_opp_pips=abs(entry_c - opp) / P,
                      rr_opp=abs(entry_c - opp) / abs(sl - entry_c), outcome=out, reach_opp=bool(oi < st and oi < 10**9),
                      bars_to_reject=k - j + 1)
            rows.append(ev)
    return pd.DataFrame(rows)


COMBOS = [("ldn", "asia"), ("ldn", "pd"), ("ny", "london"), ("ny", "overnight"), ("ny", "pd")]


def dev_dates():
    return [d for d in CAL.index if pd.Timestamp("2021-01-04") <= d < pd.Timestamp(DEV_END) and CD[d].wd < 5]


def sweep_descriptive():
    dates = dev_dates()
    allev = []
    for s in PAIRS:
        pr = Pair(s)
        for kz, ref in COMBOS:
            allev.append(sweep_events(s, kz, ref, "dst", "M5", dates=dates, pr=pr))
        print("  sweep events", s, flush=True)
    ev = pd.concat(allev, ignore_index=True)
    ev.to_csv(os.path.join(OUT, "desc_sweep_events_dev.csv"), index=False)
    return ev


def sweep_tables(ev):
    """P(sweep), P(reject|sweep), P(reverse|reject) ... per combo (pooled FX + gold) and per pair for the main two."""
    ndays = len(dev_dates())
    def summ(g, ndays_eff):
        n = len(g); rj = g[g.rejected]
        return dict(events=n, per_day=n / ndays_eff, reject_pct=100 * g.rejected.mean() if n else np.nan,
                    reverse_pct=100 * (rj.outcome == "reverse").mean() if len(rj) else np.nan,
                    stopped_pct=100 * (rj.outcome == "stopped").mean() if len(rj) else np.nan,
                    neither_pct=100 * (rj.outcome == "neither").mean() if len(rj) else np.nan,
                    reach_opp_pct=100 * rj.reach_opp.mean() if len(rj) else np.nan,
                    breakout_pct=100 * (~g.rejected).mean() if n else np.nan,
                    med_rr_opp=rj.rr_opp.median() if len(rj) else np.nan, med_risk_pips=rj.risk_pips.median() if len(rj) else np.nan)
    rows = []
    for (kz, ref), g in ev.groupby(["kz", "ref"]):
        for label, gg in [("FX9 pooled", g[g.sym != "XAUUSD"]), ("XAUUSD", g[g.sym == "XAUUSD"])]:
            npairs = 9 if label == "FX9 pooled" else 1
            rows.append({"kz": kz, "ref": ref, "set": label, **summ(gg, ndays * npairs)})
    t_combo = pd.DataFrame(rows)
    per_pair = []
    for (kz, ref) in [("ldn", "asia"), ("ny", "london")]:
        for s in PAIRS:
            g = ev[(ev.kz == kz) & (ev.ref == ref) & (ev.sym == s)]
            per_pair.append({"kz": kz, "ref": ref, "sym": s, **summ(g, ndays)})
    t_pair = pd.DataFrame(per_pair)
    # by weekday, FX pooled, Asia->London and London->NY
    rows = []
    for (kz, ref) in [("ldn", "asia"), ("ny", "london")]:
        g = ev[(ev.kz == kz) & (ev.ref == ref) & (ev.sym != "XAUUSD")]
        for wd in range(5):
            gg = g[g.wd == wd]; nd = sum(1 for d in dev_dates() if CD[d].wd == wd) * 9
            rows.append({"kz": kz, "ref": ref, "weekday": "MonTueWedThuFri"[wd * 3:wd * 3 + 3], **summ(gg, nd)})
    t_wd = pd.DataFrame(rows)
    # by season (DST) - is the DST-aware killzone consistent?
    rows = []
    for (kz, ref) in [("ldn", "asia"), ("ny", "london")]:
        g = ev[(ev.kz == kz) & (ev.ref == ref) & (ev.sym != "XAUUSD")]
        for sm in (True, False):
            gg = g[g.summer == sm]; nd = sum(1 for d in dev_dates() if CD[d].summer == sm) * 9
            rows.append({"kz": kz, "ref": ref, "season": "summer" if sm else "winter", **summ(gg, nd)})
    t_se = pd.DataFrame(rows)
    return t_combo, t_pair, t_wd, t_se


# ============================================================ PART 2: rule-based trade test
VARIANTS = {
    # name: dict(kz, ref, sch, tf, tp, min_risk_x_comm, be, mirror)
    "L1_asia_opp":       dict(kz="ldn", ref="asia", sch="dst", tf="M5", tp="opp"),
    "L2_asia_2R":        dict(kz="ldn", ref="asia", sch="dst", tf="M5", tp="2R"),
    "L3_asia_opp_M15":   dict(kz="ldn", ref="asia", sch="dst", tf="M15", tp="opp"),
    "L4_asia_opp_costfilter": dict(kz="ldn", ref="asia", sch="dst", tf="M5", tp="opp", minrisk=10),
    "L5_asia_opp_utcKZ": dict(kz="ldn", ref="asia", sch="utc", tf="M5", tp="opp"),
    "L6_asia_opp_BE1R":  dict(kz="ldn", ref="asia", sch="dst", tf="M5", tp="opp", be=1.0),
    "L7_mirror_2R":      dict(kz="ldn", ref="asia", sch="dst", tf="M5", tp="2R", mirror=True),
    "N1_london_opp":     dict(kz="ny", ref="london", sch="dst", tf="M5", tp="opp"),
    "N2_london_2R":      dict(kz="ny", ref="london", sch="dst", tf="M5", tp="2R"),
    "N3_london_opp_M15": dict(kz="ny", ref="london", sch="dst", tf="M15", tp="opp"),
    "N4_london_opp_costfilter": dict(kz="ny", ref="london", sch="dst", tf="M5", tp="opp", minrisk=10),
    "N5_london_opp_utcKZ": dict(kz="ny", ref="london", sch="utc", tf="M5", tp="opp"),
    "N6_mirror_2R":      dict(kz="ny", ref="london", sch="dst", tf="M5", tp="2R", mirror=True),
    "N7_overnight_opp":  dict(kz="ny", ref="overnight", sch="dst", tf="M5", tp="opp"),
}


def build_orders(sym, v, dates, pr):
    ev = sweep_events(sym, v["kz"], v["ref"], v["sch"], v["tf"], dates=dates, pr=pr)
    if len(ev) == 0:
        return pd.DataFrame(), 0
    ev = ev[ev.rejected].copy()
    if len(ev) == 0:
        return pd.DataFrame(), 0
    P = pr.P
    # one trade per day: earliest signal (either side)
    # (bug fixed after first run: an outside bar can sweep BOTH sides and close back inside on the same bar -> the sort tie made the
    #  chosen side arbitrary and results non-reproducible. Rule now: same-bar double sweep = ambiguous = no trade that day.)
    first = ev.groupby("date").t_signal.transform("min")
    ev = ev[ev.t_signal == first]
    ev = ev[ev.groupby("date").date.transform("size") == 1]
    n_sig = len(ev)
    rows = []
    for e in ev.itertuples():
        d = -e.sweep                                          # reversal direction
        entry, sl = e.entry_close, e.sl
        risk = abs(entry - sl)
        if v.get("minrisk") and e.risk_pips < v["minrisk"] * COMM[sym]:
            continue
        if v["tp"] == "opp":
            if e.rr_opp < 1.0:                                # planned RR must be >= 1 (skip otherwise)
                continue
            tp, tpr = e.opp, "opposite side of reference range (liquidity on the other side)"
        else:
            tp, tpr = entry + d * 2 * risk, "2R fixed"
        if v.get("mirror"):                                   # control: same timing, trade the breakout direction
            d = e.sweep; sl = entry - d * risk; tp = entry + d * 2 * risk
            tpr = "control: continuation, 2R"
        hold = (pd.Timestamp(e.hold_end) - e.t_signal) / pd.Timedelta(minutes=1)
        rows.append(dict(t_signal=e.t_signal, side=d, entry_type="market", entry=np.nan, expire=pd.NaT, sl=sl, tp=tp,
                         be_at_r=v.get("be", np.nan), max_hold_min=hold,
                         sl_reason=f"beyond sweep extreme {e.sweep_extreme:.5f} + 0.25*ATR15 buffer" if not v.get("mirror") else "control mirror stop",
                         tp_reason=tpr, kz=e.kz, ref=e.ref, sch=e.sch, tf=e.tf, date=e.date, wd=e.wd,
                         sig_hour=e.t_signal.hour, ref_hi=e.ref_hi, ref_lo=e.ref_lo, sweep_extreme=e.sweep_extreme,
                         planned_rr=abs(tp - entry) / risk, summer=e.summer))
    return pd.DataFrame(rows), n_sig


def run_variant(name, period_end, period_start="2021-01-04"):
    v = VARIANTS[name]; out = []; nsig = {}
    dates = [d for d in CAL.index if pd.Timestamp(period_start) <= d < pd.Timestamp(period_end) and CD[d].wd < 5]
    for s in PAIRS:
        pr = Pair(s)
        o, n = build_orders(s, v, dates, pr)
        nsig[s] = n
        if len(o):
            out.append(simulate(s, o))
    if not out:
        return pd.DataFrame(), nsig
    tr = pd.concat(out, ignore_index=True)
    tr["variant"] = name
    return tr, nsig


def pair_table(tr, periods):
    t = tr[(tr.status == "filled") & (tr.period.isin(periods))]
    rows = [{"sym": "POOLED", **stats(t)}]
    for s in PAIRS:
        g = t[t.sym == s]
        rows.append({"sym": s, **stats(g)})
    return pd.DataFrame(rows)


def cut_table(tr, col, periods=("DEV",)):
    t = tr[(tr.status == "filled") & (tr.period.isin(periods))]
    rows = []
    for k, g in t.groupby(col):
        st = stats(g); rows.append({col: k, **{x: st[x] for x in ("trades", "win_pct", "avg_R", "total_R", "PF", "avg_net_pips")}})
    return pd.DataFrame(rows)


def dev_run():
    summary = []
    for name in VARIANTS:
        tr, nsig = run_variant(name, DEV_END)
        f = tr[tr.status == "filled"]
        save("BT7", name + "_DEV", tr)
        st = stats(f)
        st.update(variant=name, signals=sum(nsig.values()), filled=len(f), cost_R=round((f.sym.map(COMM) / f.risk_pips).mean(), 3) if len(f) else np.nan,
                  avg_risk_pips=round(f.risk_pips.mean(), 1) if len(f) else np.nan)
        summary.append(st)
        print(name, {k: st[k] for k in ("trades", "win_pct", "avg_R", "total_R", "PF", "maxDD_R")}, flush=True)
    sm = pd.DataFrame(summary)
    sm.to_csv(os.path.join(OUT, "dev_variant_summary.csv"), index=False)
    return sm


def val_run(names):
    assert len(names) <= 3, "brief: at most 3 variants go to VAL"
    for name in names:
        tr, nsig = run_variant(name, VAL_END, "2021-01-04")            # DEV+VAL orders; VAL rows are reported separately
        save("BT7", name, tr)                                            # full DEV+VAL trades CSV (TEST excluded, never generated)
        print(name, "VAL", stats(tr[(tr.status == "filled") & (tr.period == "VAL")]), flush=True)


NL = chr(10)

# ---------------------------------------------------------------- generic (all-hours) SMC-type entry: does London improve it?
GENERIC = {"G1_anyhour_swingsweep_2R": dict(bias=False), "G2_anyhour_swingsweep_H1bias_2R": dict(bias=True)}


def generic_orders(sym, end, bias):
    """M15 fractal swing (N=3, confirmed 3 bars later) is swept by a bar that wicks beyond it and CLOSES back inside
    (first breach of that level only, level <= 96 bars old). Enter at that close, SL = extreme + 0.25*ATR15, TP 2R, max 8h.
    bias=True: only trade with the H1 EMA200 side of price. Every hour Mon-Fri, later cut by session."""
    b = bars(sym, "M15"); b = b[b.time < pd.Timestamp(end)].reset_index(drop=True)
    P = pip(sym); h, l, c = b.high.values, b.low.values, b.close.values; tc = b.tclose.values
    a15 = atr(b, 14)
    ish, isl = swings(h, l, 3)
    h1 = bars(sym, "H1"); e200 = ema(h1.close.values, 200)
    cur_h = cur_l = None
    rows = []
    for i in range(len(b)):
        if cur_h is not None and not cur_h[2] and i - cur_h[1] <= 96 and h[i] > cur_h[0]:
            cur_h[2] = True
            if c[i] < cur_h[0]:
                rows.append((i, -1, h[i]))
        if cur_l is not None and not cur_l[2] and i - cur_l[1] <= 96 and l[i] < cur_l[0]:
            cur_l[2] = True
            if c[i] > cur_l[0]:
                rows.append((i, +1, l[i]))
        if i >= 3 and ish[i - 3]:
            cur_h = [h[i - 3], i - 3, False]
        if i >= 3 and isl[i - 3]:
            cur_l = [l[i - 3], i - 3, False]
    out = []
    for i, d, E in rows:
        if np.isnan(a15[i]) or i < 14:
            continue
        ts = pd.Timestamp(tc[i])
        if ts.dayofweek >= 5 or ts.hour >= 20 or (ts.hour == 19 and False):
            continue
        if bias:
            j = asof_index(h1, np.array([tc[i]], dtype="datetime64[ns]"))[0]
            if j < 200 or (h1.close.values[j] > e200[j]) != (d == 1):
                continue
        sl = E - 0.25 * a15[i] if d == 1 else E + 0.25 * a15[i]
        risk = abs(c[i] - sl)
        out.append(dict(t_signal=ts, side=d, entry_type="market", entry=np.nan, expire=pd.NaT, sl=sl, tp=c[i] + d * 2 * risk,
                        be_at_r=np.nan, max_hold_min=480, sl_reason="beyond M15 sweep extreme + 0.25*ATR15", tp_reason="2R",
                        sig_hour=ts.hour, wd=ts.dayofweek, sweep_extreme=E))
    return pd.DataFrame(out)


def generic_run(name, end):
    v = GENERIC[name]; res = []
    for sy in PAIRS:
        o = generic_orders(sy, end, v["bias"])
        if len(o):
            res.append(simulate(sy, o, one_at_a_time=False))
    t = pd.concat(res, ignore_index=True); t["variant"] = name
    ts = pd.DatetimeIndex(t.t_signal)
    t["sess"] = "Off"
    for w in ["Asia", "London", "Overlap", "NY", "Off(rollover)"]:
        t.loc[in_win(ts, w), "sess"] = w
    t["kz"] = np.where(in_win(ts, "KZ_LDN"), "KZ_LDN", np.where(in_win(ts, "KZ_NY"), "KZ_NY", "other"))
    t["season"] = np.where(CAL.summer.reindex(ts.normalize()).values, "summer", "winter")
    return t


def generic_cuts(t, periods=("DEV",)):
    t = t[(t.status == "filled") & t.period.isin(periods)]
    out = []
    for col in ["sess", "kz", "sig_hour"]:
        g = []
        for k, x in t.groupby(col):
            st = stats(x); g.append({col: k, **{c_: st[c_] for c_ in ("trades", "win_pct", "avg_R", "total_R", "PF", "avg_net_pips")},
                                     "gross_R": (x.gross_pips / x.risk_pips).mean(), "cost_R": (x.sym.map(COMM) / x.risk_pips).mean()})
        out.append(md(pd.DataFrame(g), 3))
    return NL.join(out)



def load_trades(name):
    t = pd.read_csv(os.path.join(OUT, f"trades_{name}.csv"), parse_dates=["t_signal", "t_fill", "t_exit", "date"])
    return t


def analyze(names, periods):
    """DEV/VAL cuts by weekday, signal hour (UTC), year, season, exit reason -> markdown."""
    out = []
    for n in names:
        fn = n + "_DEV" if os.path.exists(os.path.join(OUT, f"trades_{n}_DEV.csv")) and not os.path.exists(os.path.join(OUT, f"trades_{n}.csv")) else n
        t = load_trades(fn); t = t[(t.status == "filled") & t.period.isin(periods)].copy()
        t["year"] = t.t_signal.dt.year; t["weekday"] = t.wd.map(dict(enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"])))
        t["season"] = np.where(t.summer, "summer", "winter")
        out.append(f"### {n} ({'+'.join(periods)})" + NL)
        for col in ["weekday", "sig_hour", "year", "season", "exit_reason"]:
            g = []
            for k, x in t.groupby(col):
                st = stats(x); g.append({col: k, **{c: st[c] for c in ("trades", "win_pct", "avg_R", "total_R", "PF", "avg_net_pips")},
                                         "gross_R": (x.gross_pips / x.risk_pips).mean(), "cost_R": (x.sym.map(COMM) / x.risk_pips).mean()})
            out.append(md(pd.DataFrame(g), 3) + NL)
    return NL.join(out)


def replay(name, idxs):
    """Independent minute-by-minute re-implementation of the fill/exit rules, compared to simulate()'s output."""
    t = load_trades(name); t = t[t.status == "filled"].reset_index(drop=True)
    for i in idxs:
        r = t.iloc[i]; sym = r.sym; P = pip(sym); m1 = bars(sym, "M1")
        d = int(r.side); m = m1[(m1.time >= r.t_signal)].head(3000).reset_index(drop=True)
        f = m.iloc[0]
        fill = f.open + f.spr if d == 1 else f.open
        print(f"--- {sym} {'LONG' if d == 1 else 'SHORT'} signal {r.t_signal}  SL {r.sl:.5f} TP {r.tp:.5f}")
        print(f"first minute {f.time}: open {f.open:.5f} spr {f.spr / P:.2f}pip -> my fill {fill:.5f} | sim fill {r.fill:.5f}")
        risk = (fill - r.sl) * d
        end_time = r.t_signal + pd.Timedelta(minutes=float(r.max_hold_min))
        why, px, tx = None, None, None
        for k in range(len(m)):
            b = m.iloc[k]
            if b.time > end_time:
                why, px, tx = "TIME", m.iloc[k - 1].close if d == 1 else m.iloc[k - 1].close + m.iloc[k - 1].spr, m.iloc[k - 1].time; break
            if b.time.dayofweek == 4 and b.time.hour * 60 + b.time.minute >= 20 * 60 + 45:
                why, px, tx = "WEEKEND", (m.iloc[k - 1].close if d == 1 else m.iloc[k - 1].close + m.iloc[k - 1].spr), m.iloc[k - 1].time; break
            sl_hit = (b.low <= r.sl) if d == 1 else (b.high + b.spr >= r.sl)
            tp_hit = (b.high >= r.tp) if d == 1 else (b.low + b.spr <= r.tp)
            if k == 0:
                tp_hit = False
            if sl_hit:
                why, tx = "SL", b.time
                px = (min(r.sl, b.open) - 0.2 * P) if (d == 1 and k > 0) else ((r.sl - 0.2 * P) if d == 1 else
                      ((max(r.sl, b.open + b.spr) + 0.2 * P) if k > 0 else r.sl + 0.2 * P)); break
            if tp_hit:
                why, px, tx = "TP", b.time, None; px = r.tp; tx = b.time; break
        gross = (px - fill) * d / P; net = gross - COMM[sym]; R = net / (risk / P)
        print(f"my exit {tx} {why} @ {px:.5f}  gross {gross:.2f}p net {net:.2f}p R {R:.3f}")
        print(f"sim exit {r.t_exit} {r.exit_reason} @ {r.exit:.5f}  gross {r.gross_pips:.2f}p net {r.net_pips:.2f}p R {r.R:.3f}")
        print("MATCH" if (why == r.exit_reason and abs(R - r.R) < 1e-6 and pd.Timestamp(tx) == r.t_exit) else "MISMATCH", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "desc"
    if mode == "desc":
        r = descriptive()
        ev = sweep_descriptive()
        tabs = sweep_tables(ev)
        for nme, t in zip(["combo", "pair", "weekday", "season"], tabs):
            t.to_csv(os.path.join(OUT, f"desc_sweep_{nme}.csv"), index=False)
        print("desc done")
    elif mode == "dev":
        print(dev_run().to_string())
    elif mode == "generic":
        for nme in GENERIC:
            t = generic_run(nme, DEV_END); save("BT7", nme + "_DEV", t)
            txt = generic_cuts(t); print(nme, stats(t[(t.status == "filled") & (t.period == "DEV")])); print(txt)
            open(os.path.join(OUT, "generic_cuts_" + nme + ".md"), "w", encoding="utf8").write(txt)
    elif mode == "analyze":
        per = sys.argv[2].split("+"); txt = analyze(sys.argv[3:], per)
        open(os.path.join(OUT, "analysis_" + sys.argv[2] + ".md"), "w", encoding="utf8").write(txt); print(txt)
    elif mode == "replay":
        replay(sys.argv[2], [int(x) for x in sys.argv[3:]])
    elif mode == "val":
        val_run(sys.argv[2:])
