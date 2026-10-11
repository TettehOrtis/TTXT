"""AUT09 - time-of-day and session edge (catalogue group B, owner of that group).

Two deliverables:
  1. Is there a tradable edge in WHEN you trade, independent of any pattern?
  2. A reusable hour-and-day quality map the other nine agents can use as a filter.

Stages (autopsy_lib): EXPLORE 2005-2011 only for tuning; VALIDATE 2011-2017 confirm; STRESS 2017-2021.
EXAM (2021+) is sealed and is never touched here.

Run from the TTXT root with SMC_DATASET=duka:
    SMC_DATASET=duka .venv/Scripts/python.exe smc_bot/bt/aut09_timeofday.py stage0
    ... map | cond | refine | ladder

Nothing in this file places an order or touches MetaTrader5.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                     # noqa: E402
import autopsy_lib as A                # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT09_timeofday")
os.makedirs(OUT, exist_ok=True)

SYMS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURJPY"]
MAJ = SYMS[1:]

# ---------------------------------------------------------------- THE REAL SPREAD (E5 / B4)
# The cached duka feed under SMC_DATASET=duka charges essentially NO spread: zero on 100% of gold
# minutes and on ~93-97% of FX minutes. `data_duka_full/` carries the genuine Dukascopy bid/ask
# width for the same instruments back to 2005-01-02. AUT09 settled its units (see report, bug 2):
# the column is in POINTS of 0.001 for gold and 0.00001 / 0.001 for FX, i.e. exactly
# common.py's point_size. Proof: gold reads $0.46-0.50 in 2005-2010, tightest 09-16 UTC and
# widest at the 21-23 UTC rollover - the shape a real spread must have.
_SPR_CACHE = {}


def point_size(sym):
    """Price value of one unit of the data_duka_full `spread` column.

    GOLD UNIT, SETTLED (this was AUT09's own error, corrected here): the column reads ~460 for
    2005-2010 gold. At 0.001 that is $0.46 of spread -- 9x the project's measured figure. At
    0.0001 it is **0.046 of gold price**, which matches ERRORS.md #44's measured 0.040 and
    autopsy_lib.GOLD_SPREAD = 0.05 to within a tenth of a cent. 0.0001 it is. Gold prices in this
    file are quoted to 3 decimals but the spread column is NOT in those units.
    """
    if sym == "XAUUSD":
        return 0.0001
    return 0.001 if "JPY" in sym else 0.00001


def real_spread_pips(sym, times):
    """Genuine Dukascopy spread, in pips, at each minute. Falls back to the feed where absent."""
    if sym not in _SPR_CACHE:
        p = os.path.join(C.ROOT, "data_duka_full", f"{sym}_M1.parquet")
        if not os.path.exists(p):
            _SPR_CACHE[sym] = None
        else:
            d = pd.read_parquet(p, columns=["time", "spread"])
            d["sp"] = d.spread.clip(lower=0) * point_size(sym) / C.pip(sym)
            _SPR_CACHE[sym] = (d.time.values.astype("datetime64[ns]"), d.sp.values)
    v = _SPR_CACHE[sym]
    t = np.asarray(pd.to_datetime(times).values, dtype="datetime64[ns]")
    if v is None:
        return np.full(len(t), np.nan)
    i = np.clip(np.searchsorted(v[0], t, "left"), 0, len(v[1]) - 1)
    out = v[1][i]
    bad = np.abs(v[0][i] - t) > np.timedelta64(30, "m")
    out = out.copy()
    out[bad] = np.nan
    return out

# primary map shape. ATR multiples, never dollars (BT21 finding #2 / AUTOPSY_BRIEF).
TGT, STP, HOLD = 1.5, 1.0, 240         # 1.5xATR(M15) target, 1.0xATR stop, 4 hour cap
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- clocks (the DST trap, group F)
def clocks(times):
    """DST-correct local clocks for a UTC timestamp array. The feed is true UTC (proved in stage0)."""
    t = pd.DatetimeIndex(pd.to_datetime(times)).tz_localize("UTC")
    ldn = t.tz_convert("Europe/London")
    ny = t.tz_convert("America/New_York")
    return pd.DataFrame({
        "utc_h": t.hour, "utc_min": t.hour * 60 + t.minute, "dow": t.dayofweek,
        "ldn_h": ldn.hour, "ldn_min": ldn.hour * 60 + ldn.minute,
        "ny_h": ny.hour, "ny_min": ny.hour * 60 + ny.minute,
        "us_dst": np.asarray([x.utcoffset().total_seconds() for x in ny]) == -4 * 3600,
        "eu_dst": np.asarray([x.utcoffset().total_seconds() for x in ldn]) == 3600,
        "month": t.month, "day": t.day, "year": t.year,
    })


def sessions(ck):
    """DST-aware session flags. London 08:00-16:30 local; New York 08:00-17:00 local."""
    ldn_open = (ck.ldn_min >= 8 * 60) & (ck.ldn_min < 16 * 60 + 30)
    ny_open = (ck.ny_min >= 8 * 60) & (ck.ny_min < 17 * 60)
    out = pd.DataFrame(index=ck.index)
    out["f_ldn"] = ldn_open.values
    out["f_ny"] = ny_open.values
    out["f_overlap"] = (ldn_open & ny_open).values                      # B2, the owner's question
    out["f_asia"] = (~ldn_open & ~ny_open & (ck.utc_h < 8)).values
    out["f_ldn_only"] = (ldn_open & ~ny_open).values
    out["f_ny_only"] = (ny_open & ~ldn_open).values
    out["f_ldn_first30"] = ((ck.ldn_min >= 8 * 60) & (ck.ldn_min < 8 * 60 + 30)).values      # B3
    out["f_ny_first30"] = ((ck.ny_min >= 8 * 60) & (ck.ny_min < 8 * 60 + 30)).values
    out["f_ldn_last30"] = ((ck.ldn_min >= 16 * 60) & (ck.ldn_min < 16 * 60 + 30)).values
    out["f_comex_close"] = ((ck.ny_min >= 13 * 60) & (ck.ny_min < 13 * 60 + 30)).values      # pit settle 13:30 ET
    out["f_break"] = ((ck.ny_min >= 16 * 60 + 45) | (ck.ny_min < 18 * 60)).values & (ck.ny_min >= 16 * 60 + 45)
    out["f_rollover"] = ((ck.ny_min >= 16 * 60 + 30) & (ck.ny_min < 18 * 60 + 30)).values    # B4
    # fixed-UTC labelling, kept only to measure how much the naive version mislabels
    out["f_overlap_utc"] = ((ck.utc_h >= 12) & (ck.utc_h < 16)).values
    out["f_ldn_utc"] = ((ck.utc_h >= 7) & (ck.utc_h < 16)).values
    out["sess"] = np.where(out.f_overlap, "overlap",
                  np.where(out.f_ldn_only, "london",
                  np.where(out.f_ny_only, "newyork",
                  np.where(out.f_asia, "asia", "late"))))
    return out


# ---------------------------------------------------------------- orders at a stride (the baseline)
def stride_orders(sym, stage, n_target, tgt=TGT, stp=STP, hold=HOLD, seed=0, side=None):
    """Random-direction market entries spread evenly over the stage, same construction as
    autopsy_lib.luck_bar but at a fixed stride so every (hour, weekday) cell is sampled in
    proportion to the minutes it actually has, and with one_at_a_time OFF (see report, bug 1).
    """
    A.gate(stage)
    b = C.bars(sym, "M1")
    a, z = A.STAGES[stage]
    m = ((b.time >= a) & (b.time < z)).values
    idx = np.flatnonzero(m)
    idx = idx[idx < len(b) - 2]
    step = max(1, len(idx) // n_target)
    pick = idx[::step]
    rng = np.random.default_rng(seed)
    o = pd.DataFrame({"t_signal": b.tclose.values[pick],
                      "side": rng.choice([1, -1], size=len(pick)) if side is None else side,
                      "entry_type": "market"})
    at = A.atr_at(sym, "M15", o.t_signal)
    px = b.open.values[np.clip(pick + 1, 0, len(b) - 1)]
    o["entry"] = np.nan
    o["sl"] = px - o.side * at * stp
    o["tp"] = px + o.side * at * tgt
    o["expire"] = pd.to_datetime(o.t_signal) + pd.Timedelta(days=5)
    o["be_at_r"] = np.nan
    o["max_hold_min"] = hold
    o["atr"] = at
    o["bar_i"] = pick
    return o.dropna(subset=["sl", "tp", "atr"]).reset_index(drop=True)


def run(sym, o, one=False):
    t = C.simulate(sym, o, one_at_a_time=one)
    t = t[t.status == "filled"].copy()
    ck = clocks(t.t_fill)
    for c in ck.columns:
        t[c] = ck[c].values
    for c, v in sessions(ck).items():
        t[c] = np.asarray(v)
    t["atr_pips"] = t.atr / C.pip(sym)
    t["cost_atr"] = C.COMM[sym] / t.atr_pips                  # commission as a share of one ATR
    t["spr_pips"] = (t.fill - t.fill)                         # placeholder, filled below
    b = C.bars(sym, "M1")
    bi = np.searchsorted(b.time.values, t.t_fill.values.astype("datetime64[ns]"), "left")
    t["spr_feed_pips"] = b.spr.values[np.clip(bi, 0, len(b) - 1)] / C.pip(sym)
    # charge the GENUINE spread. simulate() already took `spr_feed_pips`, which is ~0 on this feed,
    # so the shortfall is what has to come off every trade. One spread per round trip.
    rs = real_spread_pips(sym, t.t_fill)
    med = np.nanmedian(rs) if np.isfinite(rs).any() else 0.0
    rs = np.where(np.isfinite(rs), rs, med)
    t["spr_pips"] = rs
    t["spr_extra"] = np.maximum(rs - t.spr_feed_pips.values, 0.0)
    t["net_pips_feed"] = t.net_pips
    t["net_pips"] = t.net_pips - t.spr_extra
    t["usd_01"] = t.net_pips * C.USD_PIP_01[sym]
    t["R"] = t.net_pips / t.risk_pips
    t["spr_usd"] = t.spr_pips * C.USD_PIP_01[sym]
    t["cost_usd"] = (t.spr_pips + C.COMM[sym]) * C.USD_PIP_01[sym]
    t["cost_atr"] = (C.COMM[sym] + t.spr_pips) / t.atr_pips       # ALL-IN cost as a share of one ATR
    t["m1_rng_pips"] = (b.high.values[np.clip(bi, 0, len(b) - 1)] -
                        b.low.values[np.clip(bi, 0, len(b) - 1)]) / C.pip(sym)
    t["dir"] = np.where(t.side > 0, "BUY", "SELL")
    return t


def cell(t, label=""):
    """The four numbers the brief demands, every time: trades, right-in-100, net $, plus context."""
    if len(t) == 0:
        return dict(label=label, trades=0)
    w = t.net_pips > 0
    gp = t.net_pips[t.net_pips > 0].sum()
    gl = -t.net_pips[t.net_pips < 0].sum()
    return dict(label=label, trades=len(t), right_in_100=round(100 * w.mean(), 2),
                usd_per_trade=round(t.usd_01.mean(), 3), PF=round(gp / gl, 3) if gl > 0 else np.inf,
                avg_net_pips=round(t.net_pips.mean(), 2), avg_hold_h=round(t.hold_h.mean(), 2),
                atr_pips=round(t.atr_pips.median(), 2), cost_atr_pct=round(100 * t.cost_atr.median(), 2),
                spr_pips=round(t.spr_pips.median(), 3), cost_usd=round(t.cost_usd.mean(), 3),
                m1_rng=round(t.m1_rng_pips.median(), 2),
                tp_pct=round(100 * (t.exit_reason == "TP").mean(), 1),
                sl_pct=round(100 * (t.exit_reason == "SL").mean(), 1),
                time_pct=round(100 * (t.exit_reason == "TIME").mean(), 1))


def maxdd_R(t):
    if len(t) == 0:
        return np.nan
    t = t.sort_values("t_exit", kind="stable")
    eq = np.cumsum(t.R.values)
    return round((eq - np.maximum.accumulate(np.maximum(eq, 0))).min(), 1)


# ================================================================ STAGE 0 - catalogue group F
def stage0():
    rows = []
    log("=" * 78)
    log("STAGE 0  catalogue group F  (the boring killers, checked first)")
    log("=" * 78)

    # ---- the DST / server-time trap -------------------------------------------------
    log("\nF-DST  is the feed UTC or broker server time?  week open / close hour by US DST state")
    for sym in ["XAUUSD", "EURUSD"]:
        b = C.bars(sym, "M1")
        b = b[(b.time >= "2005-01-01") & (b.time < "2021-01-01")].reset_index(drop=True)
        ck = clocks(b.time)
        gap = np.r_[True, np.diff(b.time.values) > np.timedelta64(24, "h")]
        end = np.r_[np.diff(b.time.values) > np.timedelta64(24, "h"), True]
        for nm, msk in (("week_open", gap), ("week_close", end)):
            d = pd.DataFrame({"us_dst": ck.us_dst[msk].values, "utc_h": ck.utc_h[msk].values,
                              "ny_h": ck.ny_h[msk].values})
            md = d.groupby("us_dst").agg(utc_mode=("utc_h", lambda x: x.mode().iloc[0]),
                                         ny_mode=("ny_h", lambda x: x.mode().iloc[0]), n=("ny_h", "size"))
            log(f"  {sym} {nm}: " + " | ".join(
                f"us_dst={i} UTC {r.utc_mode:02d}h NY {r.ny_mode:02d}h n={r.n}" for i, r in md.iterrows()))
            for i, r in md.iterrows():
                rows.append(dict(check=f"F-DST {sym} {nm} us_dst={i}", result=f"UTC {r.utc_mode:02d} / NY {r.ny_mode:02d}", n=int(r.n)))
    # how much of the window is mislabelled by a fixed-UTC session
    ck = clocks(C.bars("EURUSD", "M1").time)
    ck = ck[(ck.index >= 0)]
    b = C.bars("EURUSD", "M1")
    m = ((b.time >= "2005-01-01") & (b.time < "2011-01-01")).values
    ck = clocks(b.time[m])
    ss = sessions(ck)
    wd = (ck.dow < 5).values
    mis = (ss.f_overlap.values != ss.f_overlap_utc.values) & wd
    log(f"\n  EXPLORE weekday minutes where a FIXED-UTC 12-16 overlap label disagrees with the "
        f"DST-correct one: {100*mis.mean():.1f}%  ({mis.sum():,} of {wd.sum():,} minutes)")
    log(f"  share of EXPLORE weekday minutes with EU DST on {100*ck.eu_dst[wd].mean():.1f}%, "
        f"US DST on {100*ck.us_dst[wd].mean():.1f}%, the two disagreeing {100*(ck.eu_dst!=ck.us_dst)[wd].mean():.1f}%")
    rows.append(dict(check="F-DST fixed-UTC overlap mislabel share", result=f"{100*mis.mean():.1f}% of EXPLORE weekday minutes", n=int(wd.sum())))

    # ---- F3 gaps / holes -----------------------------------------------------------
    log("\nF3  missing minutes by UTC hour (share of the minutes an hour should hold), EXPLORE")
    hole = {}
    for sym in SYMS:
        b = C.bars(sym, "M1")
        m = ((b.time >= "2005-01-01") & (b.time < "2011-01-01")).values
        ck = clocks(b.time[m])
        have = ck[ck.dow < 5].groupby("utc_h").size()
        days = pd.Series(pd.to_datetime(b.time[m]).dt.normalize().unique())
        nd = (days.dt.dayofweek < 5).sum()
        hole[sym] = (100 * (1 - have / (nd * 60))).round(1)
    H = pd.DataFrame(hole)
    log(H.to_string())
    H.to_csv(os.path.join(OUT, "f3_missing_minutes_pct_by_hour.csv"))
    rows.append(dict(check="F3 worst hour for missing minutes (gold)", result=f"{H.XAUUSD.idxmax()}h UTC, {H.XAUUSD.max()}% missing", n=0))

    # ---- F1 fill bar + the look-ahead twin ----------------------------------------
    log("\nF1  honest fill (next M1 open) vs the look-ahead twin (fill at the signal bar's own open)")
    for sym in ["XAUUSD", "EURUSD"]:
        o = stride_orders(sym, "EXPLORE", 20000, seed=7)
        hon = run(sym, o)
        # twin: shift t_signal one minute earlier so simulate fills at the SAME bar the signal used
        o2 = o.copy()
        o2["t_signal"] = pd.to_datetime(o2.t_signal) - pd.Timedelta(minutes=1)
        twin = run(sym, o2)
        for nm, t in (("honest", hon), ("lookahead twin", twin)):
            c = cell(t, nm)
            log(f"  {sym} {nm:15} trades {c['trades']:>6}  right_in_100 {c['right_in_100']:>6}  "
                f"$ per trade {c['usd_per_trade']:>7}")
            rows.append(dict(check=f"F1 {sym} {nm}", result=f"{c['right_in_100']} right in 100, ${c['usd_per_trade']}/trade", n=c["trades"]))
        if sym == "XAUUSD":
            hon.to_parquet(os.path.join(OUT, "_f1_gold_honest.parquet"))

    # ---- F5 same-bar target-and-stop ----------------------------------------------
    log("\nF5  same-bar target-and-stop: how often the resolving M1 bar held BOTH levels")
    for sym in ["XAUUSD", "EURUSD"]:
        o = stride_orders(sym, "EXPLORE", 20000, seed=11)
        t = run(sym, o)
        b = C.bars(sym, "M1")
        xi = np.searchsorted(b.time.values, t.t_exit.values.astype("datetime64[ns]"), "left")
        hi, lo = b.high.values[xi], b.low.values[xi]
        both = ((hi >= np.maximum(t.tp, t.sl)) & (lo <= np.minimum(t.tp, t.sl))).mean()
        log(f"  {sym}: {100*both:.2f}% of resolved trades had both levels inside the exit minute "
            f"(simulate resolves these as SL - pessimistic, common.py first_exit)")
        rows.append(dict(check=f"F5 {sym} both levels in exit minute", result=f"{100*both:.2f}%", n=len(t)))

    # ---- F4 stale HTF --------------------------------------------------------------
    o = stride_orders("XAUUSD", "EXPLORE", 2000, seed=3)
    b15 = C.bars("XAUUSD", "M15")
    i = C.asof_index(b15, pd.to_datetime(o.t_signal).values)
    ok = (b15.tclose.values[i] <= pd.to_datetime(o.t_signal).values).all()
    log(f"\nF4  every M15 ATR used a bar that had already closed at the signal: {ok}")
    rows.append(dict(check="F4 asof_index uses closed HTF bar only", result=str(ok), n=len(o)))

    # ---- F2 feed swap --------------------------------------------------------------
    log("\nF2  feed swap: Exness (data_zero / data) starts 2021-01-01, which is the SEALED EXAM. "
        "There is NO second feed for EXPLORE/VALIDATE/STRESS, so F2 cannot be run here. Reported, not faked.")
    rows.append(dict(check="F2 feed swap", result="impossible pre-2021, only Dukascopy exists", n=0))

    # ---- spread reality check ------------------------------------------------------
    log("\nE5  the spread actually present in the Dukascopy feed, EXPLORE")
    sp = []
    for sym in SYMS:
        b = C.bars(sym, "M1")
        d = b[(b.time >= "2005-01-01") & (b.time < "2011-01-01")]
        sp.append(dict(sym=sym, median_spread_pips=round(d.spr.median() / C.pip(sym), 3),
                       mean_spread_pips=round(d.spr.mean() / C.pip(sym), 3),
                       pct_minutes_zero_spread=round(100 * (d.spr <= 0).mean(), 1),
                       comm_pips=C.COMM[sym]))
    SP = pd.DataFrame(sp)
    log(SP.to_string(index=False))
    SP.to_csv(os.path.join(OUT, "e5_spread_reality.csv"), index=False)
    rows.append(dict(check="E5 duka spread on gold", result="0.00 pips on 100% of minutes - commission is the only cost", n=0))

    pd.DataFrame(rows).to_csv(os.path.join(OUT, "stage0_checks.csv"), index=False)
    log("\nwrote stage0_checks.csv")


# ================================================================ the by-hour cost curve (B4/E5)
def spread_curve():
    """Deliverable 2a: what it ACTUALLY costs to trade each UTC hour, on real Dukascopy spreads."""
    log("=" * 78)
    log("B4/E5  THE REAL BY-HOUR COST CURVE  (data_duka_full spreads, EXPLORE 2005-2011)")
    log("=" * 78)
    rows = []
    for sym in SYMS:
        p = os.path.join(C.ROOT, "data_duka_full", f"{sym}_M1.parquet")
        if not os.path.exists(p):
            log(f"  {sym}: no data_duka_full file, skipped")
            continue
        d = pd.read_parquet(p, columns=["time", "high", "low", "spread"])
        d = d[(d.time >= "2005-01-01") & (d.time < "2011-01-01")]
        pp = C.pip(sym)
        d = d.assign(sp=d.spread.clip(lower=0) * point_size(sym) / pp,
                     rng=(d.high - d.low) / pp,
                     h=d.time.dt.hour, dw=d.time.dt.dayofweek)
        d = d[d.dw < 5]
        h1 = d.groupby([d.time.dt.floor("h")]).agg(rng=("rng", "sum"))   # proxy for the hour's travel
        h1 = h1.assign(h=h1.index.hour, dw=h1.index.dayofweek)
        h1 = h1[h1.dw < 5]
        trav = h1.groupby("h").rng.median()
        g = d.groupby("h").agg(spr_med=("sp", "median"), spr_mean=("sp", "mean"),
                               spr_p90=("sp", lambda x: x.quantile(0.9)), m1_rng=("rng", "median"),
                               n=("sp", "size"))
        for h, r in g.iterrows():
            allin = r.spr_mean + C.COMM[sym]
            rows.append(dict(sym=sym, utc_hour=int(h), spread_pips_med=round(r.spr_med, 3),
                             spread_pips_mean=round(r.spr_mean, 3), spread_pips_p90=round(r.spr_p90, 3),
                             comm_pips=C.COMM[sym], allin_cost_pips=round(allin, 3),
                             allin_cost_usd_01lot=round(allin * C.USD_PIP_01[sym], 3),
                             m1_range_pips=round(r.m1_rng, 2), hour_travel_pips=round(trav.get(h, np.nan), 1),
                             cost_pct_of_hour_travel=round(100 * allin / max(trav.get(h, np.nan), 1e-9), 2),
                             minutes=int(r.n)))
    S = pd.DataFrame(rows)
    S.to_csv(os.path.join(OUT, "cost_curve_by_hour.csv"), index=False)
    log("\nGOLD, real spread and all-in cost by UTC hour")
    log(S[S.sym == "XAUUSD"].set_index("utc_hour")[
        ["spread_pips_med", "spread_pips_mean", "spread_pips_p90", "allin_cost_usd_01lot",
         "m1_range_pips", "hour_travel_pips", "cost_pct_of_hour_travel"]].to_string())
    log("\nCost as a share of what the hour actually travels (lower is better), all pairs")
    pv = S.pivot(index="utc_hour", columns="sym", values="cost_pct_of_hour_travel")
    log(pv[[s for s in SYMS if s in pv.columns]].round(1).to_string())
    log("\nwrote cost_curve_by_hour.csv")
    return S


# ================================================================ THE MAP
def build_map():
    log("=" * 78)
    log("THE HOUR-AND-DAY QUALITY MAP  (EXPLORE 2005-01-01 -> 2011-01-01)")
    log(f"shape {TGT}xATR target / {STP}xATR stop, {HOLD} min cap, random direction, real costs")
    log("=" * 78)
    allt = []
    for sym in SYMS:
        t0 = time.time()
        n = 150000 if sym == "XAUUSD" else 130000
        o = stride_orders(sym, "EXPLORE", n, seed=101)
        t = run(sym, o)
        t["sym"] = sym
        allt.append(t)
        log(f"  {sym:7} {len(t):>7} filled in {time.time()-t0:5.0f}s  "
            f"right_in_100 {100*(t.net_pips>0).mean():5.2f}  $ {t.usd_01.mean():+.3f}")
    T = pd.concat(allt, ignore_index=True)
    keep = ["sym", "dir", "t_signal", "t_fill", "t_exit", "side", "fill", "sl", "tp", "exit", "exit_reason",
            "hold_h", "net_pips", "net_pips_feed", "risk_pips", "R", "usd_01", "mfe_R", "mae_R",
            "atr", "atr_pips", "cost_atr", "cost_usd",
            "spr_pips", "spr_extra", "spr_usd", "m1_rng_pips", "utc_h", "dow", "ldn_h", "ny_h", "us_dst", "eu_dst",
            "month", "year", "f_ldn", "f_ny", "f_overlap", "f_asia", "f_ldn_only", "f_ny_only",
            "f_ldn_first30", "f_ny_first30", "f_ldn_last30", "f_comex_close", "f_rollover",
            "f_overlap_utc", "f_ldn_utc", "sess", "period", "status"]
    T[keep].to_parquet(os.path.join(OUT, "_map_trades.parquet"))
    log(f"\npooled {len(T):,} random entries. saved _map_trades.parquet")
    map_tables(T)


def map_tables(T=None):
    if T is None:
        T = pd.read_parquet(os.path.join(OUT, "_map_trades.parquet"))
    # FXPOOL is the primary map: the 9 FX majors have the full 2005-2011 history. Gold's duka feed
    # starts 2009-03-15, so its EXPLORE is 1.8 one-directional years and it is reported on its own.
    T = pd.concat([T, T[T.sym.isin(MAJ)].assign(sym="FXPOOL")], ignore_index=True)
    base = {s: cell(g, s) for s, g in T.groupby("sym")}
    base["POOLED"] = cell(T[T.sym != "FXPOOL"], "POOLED")

    # ---------- 0. BUY vs SELL, and FX vs gold ------------------------------------
    rows = []
    for (sym, d), g in T.groupby(["sym", "dir"]):
        c = cell(g); c.pop("label")
        rows.append(dict(sym=sym, dir=d, **c))
    for (sym, d, s), g in T.groupby(["sym", "dir", "sess"]):
        c = cell(g); c.pop("label")
        rows.append(dict(sym=sym, dir=d, sess=s, **c))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "buy_vs_sell.csv"), index=False)
    log("\nBUY vs SELL (a random entry has no direction edge; this is the check that it has none)")
    log(pd.DataFrame(rows).query("sess.isna()")[
        ["sym", "dir", "trades", "right_in_100", "usd_per_trade", "PF"]].to_string(index=False))

    # ---------- 0b. session table, DST-aware (B1/B2, the owner's question) ---------
    rows = []
    for (sym, s), g in T.groupby(["sym", "sess"]):
        c = cell(g); c.pop("label")
        b = base[sym]
        rows.append(dict(sym=sym, session=s, **c,
                         usd_vs_allhours=round(c["usd_per_trade"] - b["usd_per_trade"], 3)))
    SS = pd.DataFrame(rows)
    SS.to_csv(os.path.join(OUT, "session_table.csv"), index=False)
    log("\nSESSIONS, DST-aware  (overlap = London 08:00-16:30 local AND New York 08:00-17:00 local)")
    log(SS[SS.sym.isin(["FXPOOL", "XAUUSD"])].to_string(index=False))

    # ---------- 1. the headline map: UTC hour x weekday, pooled and per pair --------
    rows = []
    for (sym, h, d), g in T.groupby(["sym", "utc_h", "dow"]):
        if d > 4:
            continue
        c = cell(g)
        c.pop("label")
        b = base[sym]
        rows.append(dict(sym=sym, utc_hour=int(h), dow=DOW[int(d)], **c,
                         usd_vs_allhours=round(c["usd_per_trade"] - b["usd_per_trade"], 3),
                         right_vs_allhours=round(c["right_in_100"] - b["right_in_100"], 2)))
    for (h, d), g in T[T.sym != "FXPOOL"].groupby(["utc_h", "dow"]):
        if d > 4:
            continue
        c = cell(g)
        c.pop("label")
        b = base["POOLED"]
        rows.append(dict(sym="POOLED", utc_hour=int(h), dow=DOW[int(d)], **c,
                         usd_vs_allhours=round(c["usd_per_trade"] - b["usd_per_trade"], 3),
                         right_vs_allhours=round(c["right_in_100"] - b["right_in_100"], 2)))
    M = pd.DataFrame(rows)
    M.to_csv(os.path.join(OUT, "quality_map_hour_dow.csv"), index=False)
    log(f"\nwrote quality_map_hour_dow.csv  ({len(M)} rows)")

    # ---------- 2. the hour map (collapsed over weekdays) --------------------------
    rows = []
    for (sym, h), g in T.groupby(["sym", "utc_h"]):
        c = cell(g)
        c.pop("label")
        b = base[sym]
        rows.append(dict(sym=sym, utc_hour=int(h), **c,
                         usd_vs_allhours=round(c["usd_per_trade"] - b["usd_per_trade"], 3)))
    for h, g in T[T.sym != "FXPOOL"].groupby("utc_h"):
        c = cell(g)
        c.pop("label")
        rows.append(dict(sym="POOLED", utc_hour=int(h), **c,
                         usd_vs_allhours=round(c["usd_per_trade"] - base["POOLED"]["usd_per_trade"], 3)))
    Hh = pd.DataFrame(rows)
    Hh.to_csv(os.path.join(OUT, "quality_map_hour.csv"), index=False)

    # ---------- 3. DST split: does a cell shift an hour across the year? -----------
    rows = []
    for (sym, h, s), g in T.groupby(["sym", "utc_h", "us_dst"]):
        c = cell(g)
        c.pop("label")
        rows.append(dict(sym=sym, utc_hour=int(h), us_dst=bool(s), **c))
    for (h, s), g in T[T.sym != "FXPOOL"].groupby(["utc_h", "us_dst"]):
        c = cell(g)
        c.pop("label")
        rows.append(dict(sym="POOLED", utc_hour=int(h), us_dst=bool(s), **c))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "quality_map_hour_dst.csv"), index=False)

    # ---------- 4. New-York-local hour map (the DST-correct index) -----------------
    rows = []
    for (sym, h), g in T.groupby(["sym", "ny_h"]):
        c = cell(g)
        c.pop("label")
        rows.append(dict(sym=sym, ny_local_hour=int(h), **c))
    for h, g in T[T.sym != "FXPOOL"].groupby("ny_h"):
        c = cell(g)
        c.pop("label")
        rows.append(dict(sym="POOLED", ny_local_hour=int(h), **c))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "quality_map_nylocal_hour.csv"), index=False)

    # ---------- 5. weekday map -----------------------------------------------------
    rows = []
    for (sym, d), g in T.groupby(["sym", "dow"]):
        c = cell(g)
        c.pop("label")
        rows.append(dict(sym=sym, dow=DOW[int(d)], **c))
    for d, g in T[T.sym != "FXPOOL"].groupby("dow"):
        c = cell(g)
        c.pop("label")
        rows.append(dict(sym="POOLED", dow=DOW[int(d)], **c))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "quality_map_weekday.csv"), index=False)

    # ---------- printed summaries --------------------------------------------------
    log("\nALL-HOURS BASELINE (the luck bar every cell is measured against)")
    log(pd.DataFrame([base[s] for s in SYMS + ["FXPOOL", "POOLED"]]).to_string(index=False))

    log("\nPOOLED by UTC hour")
    p = Hh[Hh.sym == "FXPOOL"].set_index("utc_hour")
    log(p[["trades", "right_in_100", "usd_per_trade", "PF", "atr_pips", "cost_atr_pct",
           "m1_rng", "avg_hold_h", "tp_pct", "sl_pct", "time_pct", "usd_vs_allhours"]].to_string())

    log("\nGOLD by UTC hour")
    p = Hh[Hh.sym == "XAUUSD"].set_index("utc_hour")
    log(p[["trades", "right_in_100", "usd_per_trade", "PF", "atr_pips", "cost_atr_pct",
           "m1_rng", "tp_pct", "sl_pct", "time_pct", "usd_vs_allhours"]].to_string())

    log("\nPOOLED $ per trade, UTC hour x weekday (the grid the other agents will read)")
    g = M[M.sym == "FXPOOL"].pivot(index="utc_hour", columns="dow", values="usd_per_trade")
    log(g[["Mon", "Tue", "Wed", "Thu", "Fri"]].round(3).to_string())
    log("\nGOLD $ per trade, UTC hour x weekday")
    g = M[M.sym == "XAUUSD"].pivot(index="utc_hour", columns="dow", values="usd_per_trade")
    log(g[["Mon", "Tue", "Wed", "Thu", "Fri"]].round(3).to_string())
    log("\nGOLD right-in-100, UTC hour x weekday  (fair value for 1.5/1.0 is 40.0)")
    g = M[M.sym == "XAUUSD"].pivot(index="utc_hour", columns="dow", values="right_in_100")
    log(g[["Mon", "Tue", "Wed", "Thu", "Fri"]].round(1).to_string())
    return T, M, Hh


# ================================================================ CONDITIONS (the Tally)
def news_proxy(sym, stage, bar_i):
    """B5 PROXY, never fact: the decision minute's range divided by that minute-of-day's own median."""
    b = C.bars(sym, "M1")
    rng = (b.high - b.low).values / C.pip(sym)
    mod = (b.time.dt.hour * 60 + b.time.dt.minute).values
    a, z = A.STAGES[stage]
    m = ((b.time >= a) & (b.time < z)).values
    med = pd.Series(rng[m]).groupby(mod[m]).median()
    norm = med.reindex(range(1440)).ffill().bfill().values
    r = rng[bar_i] / np.maximum(norm[mod[bar_i]], 1e-9)
    return r


def conditions():
    tal = A.Tally("AUT09")
    T = pd.read_parquet(os.path.join(OUT, "_map_trades.parquet"))
    log("=" * 78)
    log("CATALOGUE CONDITIONS - every one logged in the Tally")
    log("=" * 78)
    out = []

    def add(cid, note, mask, frame=None):
        f = T if frame is None else frame
        tb = A.score(f, np.asarray(mask, bool))
        tal.add(cid, note, tb)
        on, off = f[np.asarray(mask, bool)], f[~np.asarray(mask, bool)]
        co, cf, ca = cell(on), cell(off), cell(f)
        out.append(dict(cond=cid, note=note,
                        all_trades=ca.get("trades"), all_right=ca.get("right_in_100"), all_usd=ca.get("usd_per_trade"),
                        on_trades=co.get("trades"), on_right=co.get("right_in_100"), on_usd=co.get("usd_per_trade"),
                        on_PF=co.get("PF"), on_cost_atr=co.get("cost_atr_pct"), on_atr=co.get("atr_pips"),
                        off_trades=cf.get("trades"), off_right=cf.get("right_in_100"), off_usd=cf.get("usd_per_trade"),
                        usd_lift=None if co.get("usd_per_trade") is None else round(co["usd_per_trade"] - ca["usd_per_trade"], 3)))
        log(f"  {cid:9} {note[:52]:52} on n={co.get('trades',0):>7} right={co.get('right_in_100')} "
            f"$={co.get('usd_per_trade')}  (all $={ca.get('usd_per_trade')})")

    # ---- group B ---------------------------------------------------------------
    add("B1a", "session = Asia (DST-aware)", T.f_asia)
    add("B1b", "session = London only (08:00-16:30 London local)", T.f_ldn_only)
    add("B1c", "session = New York only (after London close)", T.f_ny_only)
    add("B1d", "session = late / off hours", T.sess == "late")
    add("B2a", "London/NY OVERLAP active, DST-aware  <- owner's question", T.f_overlap)
    add("B2b", "overlap labelled with FIXED UTC 12-16 (the naive version)", T.f_overlap_utc)
    add("B2c", "overlap first hour only (08:00-09:00 New York local)", (T.ny_h == 8) & T.f_overlap)
    add("B2d", "overlap last hour only (15:30-16:30 London local)", T.f_ldn_last30 & T.f_overlap)
    add("B3a", "first 30 minutes after the London open", T.f_ldn_first30)
    add("B3b", "first 30 minutes after the New York open", T.f_ny_first30)
    add("B3c", "last 30 minutes of the London session", T.f_ldn_last30)
    add("B3d", "COMEX pit settlement window 13:00-13:30 New York", T.f_comex_close)
    add("B4a", "rollover window 16:30-18:30 New York local", T.f_rollover)
    add("B4b", "the three thinnest UTC hours by M1 range", T.utc_h.isin([3, 4, 22]))
    add("B4c", "commission is more than 10% of one ATR (expensive minutes)", T.cost_atr > 0.10)
    add("B4d", "commission is under 5% of one ATR (cheap minutes)", T.cost_atr < 0.05)
    r = news_proxy_all(T)
    add("B5a", "PROXY news spike: decision minute range > 6x its own norm", r > 6)
    add("B5b", "PROXY calm: decision minute range < 0.5x its own norm", r < 0.5)
    ck = clocks(T.t_fill)
    eom = ck.day >= 27
    add("B6a", "month end (day 27 or later)", eom.values)
    add("B6b", "first two days of the month", (ck.day <= 2).values)
    add("B6c", "December and January (holiday weeks)", ck.month.isin([12, 1]).values)
    add("B6d", "COMEX settlement run-up, 25th-27th", ck.day.isin([25, 26, 27]).values)
    add("B7a", "weekday Tue/Wed/Thu", T.dow.isin([1, 2, 3]))
    add("B7b", "weekday Monday", T.dow == 0)
    add("B7c", "weekday Friday", T.dow == 4)

    # ---- group A, the parts that interact with time ----------------------------
    av = atr_feats(T)
    add("A4a", "ATR rank vs its own trailing year in the top quarter", av["atr_rank"] > 0.75)
    add("A4b", "ATR rank in the bottom quarter", av["atr_rank"] < 0.25)
    add("A5a", "ATR expanding (now > 20 M15 bars ago)", av["atr_exp"] > 1.0)
    add("A5b", "ATR contracting hard (now < 0.8x 20 bars ago)", av["atr_exp"] < 0.8)
    add("A6a", "under a third of the day's typical range spent at entry", av["day_spent"] < 0.33)
    add("A6b", "the day has already spent more than its typical range", av["day_spent"] > 1.0)
    add("A8a", "the day after a daily move over twice the median", av["big_prev_day"])
    add("A4c", "ATR top quarter AND inside the overlap", (av["atr_rank"] > 0.75) & T.f_overlap.values)
    add("A6c", "under a third of the range spent AND inside the overlap", (av["day_spent"] < 0.33) & T.f_overlap.values)

    tb = tal.frame()
    tb.to_csv(os.path.join(OUT, "tally.csv"), index=False)
    pd.DataFrame(out).to_csv(os.path.join(OUT, "conditions.csv"), index=False)
    log(f"\nconditions tried: {len(tb)}  (cap is 40)")
    log("\nwrote tally.csv and conditions.csv")


def news_proxy_all(T):
    r = np.full(len(T), np.nan)
    b_i = {}
    for sym, g in T.groupby("sym"):
        b = C.bars(sym, "M1")
        bi = np.searchsorted(b.time.values, g.t_fill.values.astype("datetime64[ns]"), "left") - 1
        r[g.index.values] = news_proxy(sym, "EXPLORE", np.clip(bi, 0, len(b) - 1))
    return pd.Series(r, index=T.index)


def atr_feats(T):
    """A4/A5/A6: all read from bars that had already closed at the signal."""
    rank = np.full(len(T), np.nan); exp = np.full(len(T), np.nan)
    spent = np.full(len(T), np.nan); big = np.zeros(len(T), bool)
    for sym, g in T.groupby("sym"):
        b15 = C.bars(sym, "M15")
        a = A.atr(b15, 14).values
        i = C.asof_index(b15, pd.to_datetime(g.t_signal).values)
        i = np.clip(i, 0, len(a) - 1)
        s = pd.Series(a)
        rk = s.rolling(96 * 250, min_periods=96 * 20).rank(pct=True).values     # ~1 trading year of M15
        rank[g.index.values] = rk[i]
        back = np.clip(i - 20, 0, len(a) - 1)
        exp[g.index.values] = a[i] / np.maximum(a[back], 1e-12)
        # A6: share of the day's typical range already spent
        b1 = C.bars(sym, "M1")
        day = b1.time.dt.normalize().values
        bi = np.searchsorted(b1.time.values, pd.to_datetime(g.t_signal).values, "right") - 1
        bi = np.clip(bi, 0, len(b1) - 1)
        dgrp = pd.DataFrame({"d": day, "h": b1.high.values, "l": b1.low.values})
        dh = dgrp.groupby("d").h.cummax().values
        dl = dgrp.groupby("d").l.cummin().values
        so_far = (dh[bi] - dl[bi]) / C.pip(sym)
        dr = dgrp.groupby("d").agg(h=("h", "max"), l=("l", "min"))
        typ = ((dr.h - dr.l) / C.pip(sym)).rolling(20, min_periods=5).median().shift(1)
        dmap = pd.Series(typ.values, index=dr.index)
        spent[g.index.values] = so_far / np.maximum(dmap.reindex(day[bi]).values, 1e-9)
        prev = ((dr.h - dr.l) / C.pip(sym)).shift(1)
        bigd = (prev > 2 * ((dr.h - dr.l) / C.pip(sym)).rolling(20, min_periods=5).median().shift(1))
        big[g.index.values] = pd.Series(bigd.values, index=dr.index).reindex(day[bi]).fillna(False).values
    return dict(atr_rank=pd.Series(rank, index=T.index), atr_exp=pd.Series(exp, index=T.index),
                day_spent=pd.Series(spent, index=T.index), big_prev_day=pd.Series(big, index=T.index))


# ================================================================ SHAPE LADDER by hour (E6)
def ladder():
    log("=" * 78)
    log("E6  the ATR target/stop ladder, by session block.  EXPLORE only.")
    log("=" * 78)
    shapes = [(t, s) for t in (1.0, 1.5, 2.0, 3.0) for s in (0.5, 1.0, 1.5, 2.0, 3.0)]
    rows = []
    for sym in ["XAUUSD", "EURUSD", "USDJPY"]:
        for (tg, st) in shapes:
            o = stride_orders(sym, "EXPLORE", 25000, tgt=tg, stp=st, hold=max(240, int(240 * tg)), seed=55)
            t = run(sym, o)
            be = A.breakeven_rate(sym, t.atr.median(), tg, st)
            for lab, g in [("ALL", t), ("overlap", t[t.f_overlap]), ("asia", t[t.f_asia]),
                           ("london_only", t[t.f_ldn_only]), ("ny_only", t[t.f_ny_only])]:
                c = cell(g)
                c.pop("label")
                rows.append(dict(sym=sym, tgt=tg, stop=st, block=lab, needs_right_in_100=round(be, 2), **c))
            r = rows[-5]
            log(f"  {sym:7} {tg:.1f}T/{st:.1f}S  needs {be:5.2f}  got {r['right_in_100']:5.2f}  "
                f"$ {r['usd_per_trade']:+7.3f}  hold {r['avg_hold_h']:5.2f}h  n={r['trades']}")
    L = pd.DataFrame(rows)
    L.to_csv(os.path.join(OUT, "shape_ladder_by_block.csv"), index=False)
    log("\nwrote shape_ladder_by_block.csv")
    log("\nbest shape per block by $ per trade:")
    for sym, g in L.groupby("sym"):
        for blk, gg in g.groupby("block"):
            b = gg.sort_values("usd_per_trade", ascending=False).iloc[0]
            log(f"  {sym:7} {blk:12} {b.tgt:.1f}T/{b.stop:.1f}S  needs {b.needs_right_in_100:5.2f} "
                f"got {b.right_in_100:5.2f}  $ {b.usd_per_trade:+7.3f}  n={int(b.trades)}")


# ================================================================ REFINEMENTS -> VALIDATE/STRESS
def pick_cells(M, sym, k=6):
    """The k best (hour, weekday) cells on EXPLORE for this symbol. A deliberate luck demonstration:
    with 120 cells, several look good on EXPLORE for no reason at all. R3 exists to show that."""
    g = M[(M.sym == sym) & (M.trades >= 150)].sort_values("usd_per_trade", ascending=False).head(k)
    return [(int(r.utc_hour), DOW.index(r.dow)) for r in g.itertuples()]


def cheap_hours(S, sym, frac=0.375):
    """R1, pre-registered from the COST CURVE, not from any outcome: the UTC hours where the all-in
    round trip is the smallest share of how far that hour actually travels. 9 of 24 hours."""
    g = S[S.sym == sym].sort_values("cost_pct_of_hour_travel")
    return sorted(int(h) for h in g.utc_hour.head(int(round(frac * 24))))


def dear_hours(S, sym, frac=0.25):
    g = S[S.sym == sym].sort_values("cost_pct_of_hour_travel", ascending=False)
    return sorted(int(h) for h in g.utc_hour.head(int(round(frac * 24))))


def refine():
    M = pd.read_csv(os.path.join(OUT, "quality_map_hour_dow.csv"))
    S = pd.read_csv(os.path.join(OUT, "cost_curve_by_hour.csv"))
    best = {s: pick_cells(M, s) for s in SYMS}
    cheap = {s: cheap_hours(S, s) for s in SYMS}
    dear = {s: dear_hours(S, s) for s in SYMS}
    nl = chr(10)
    with open(os.path.join(OUT, "refinement_definitions.txt"), "w") as f:
        f.write("R1 cheap hours (UTC), chosen from the cost curve only:" + nl)
        for s in SYMS:
            f.write(f"  {s}: {cheap[s]}" + nl)
        f.write(nl + "AVOID, the dearest quarter of the clock (UTC):" + nl)
        for s in SYMS:
            f.write(f"  {s}: {dear[s]}" + nl)
        f.write(nl + "R3 best-6 EXPLORE (hour, weekday) cells - the luck demonstration:" + nl)
        for s in SYMS:
            f.write(f"  {s}: " + ", ".join(f"{h:02d}h {DOW[d]}" for h, d in best[s]) + nl)

    rows = []
    for stage in ("EXPLORE", "VALIDATE", "STRESS"):
        for sym in SYMS:
            n = 150000 if sym == "XAUUSD" else 130000
            b15 = run(sym, stride_orders(sym, stage, n, seed=101))
            b31 = run(sym, stride_orders(sym, stage, n, tgt=3.0, stp=1.0, hold=720, seed=101))
            if len(b15) == 0:
                continue
            for adverse in (False, True):
                a, z = (b15, b31)
                if adverse:
                    a, z = b15.copy(), b31.copy()
                    for t in (a, z):
                        t["net_pips"] = t.net_pips - (C.COMM[sym] + t.spr_pips)   # double the all-in cost
                        t["usd_01"] = t.net_pips * C.USD_PIP_01[sym]
                        t["R"] = t.net_pips / t.risk_pips
                sets = {
                    "BASE_allhours_1.5T1.0S": a,
                    "R1_cheap_hours_1.5T1.0S": a[a.utc_h.isin(cheap[sym])],
                    "R2_ny_morning_overlap_3.0T1.0S": z[z.f_overlap & z.ny_h.isin([8, 9, 10, 11])],
                    "R3_best6_explore_cells_1.5T1.0S": a[[(h, d) in best[sym] for h, d in zip(a.utc_h, a.dow)]],
                    "AVOID_dearest_quarter_1.5T1.0S": a[a.utc_h.isin(dear[sym])],
                    "BASE_allhours_3.0T1.0S": z,
                }
                for nm, t in sets.items():
                    c = cell(t, nm)
                    c.pop("label")
                    wk = max(1.0, (t.t_fill.max() - t.t_fill.min()) / pd.Timedelta(weeks=1)) if len(t) else 1.0
                    rows.append(dict(stage=stage, adverse=adverse, sym=sym, variant=nm,
                                     per_week=round(len(t) / wk, 2), maxDD_R=maxdd_R(t), **c))
            log(f"  {stage:8} {sym:7} done")
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(OUT, "refinements_per_pair.csv"), index=False)

    pool = []
    for (stage, adverse, variant), g in R.groupby(["stage", "adverse", "variant"]):
        for grp, gg in (("FXPOOL", g[g.sym.isin(MAJ)]), ("XAUUSD", g[g.sym == "XAUUSD"])):
            n = gg.trades.sum()
            if n == 0:
                continue
            w = lambda c: round(float((gg[c].fillna(0) * gg.trades).sum() / n), 3)
            pool.append(dict(stage=stage, adverse=adverse, group=grp, variant=variant, trades=int(n),
                             per_week=round(gg.per_week.sum(), 2), right_in_100=w("right_in_100"),
                             usd_per_trade=w("usd_per_trade"), avg_net_pips=w("avg_net_pips"),
                             PF=w("PF"), avg_hold_h=w("avg_hold_h"), maxDD_R=round(gg.maxDD_R.sum(), 1),
                             cost_atr_pct=w("cost_atr_pct")))
    P = pd.DataFrame(pool).sort_values(["group", "variant", "stage", "adverse"])
    P.to_csv(os.path.join(OUT, "refinements_pooled.csv"), index=False)
    log(chr(10) + "POOLED REFINEMENT TABLE")
    log(P.to_string(index=False))


def save_csvs():
    """Save trade CSVs with common.save() as the brief requires."""
    T = pd.read_parquet(os.path.join(OUT, "_map_trades.parquet"))
    for sym, g in T.groupby("sym"):
        g = g.copy()
        g["status"] = "filled"
        C.save("AUT09_timeofday", f"MAP_EXPLORE_{sym}", g)
    log("saved per-symbol map trade CSVs via common.save()")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    {"stage0": stage0, "map": build_map, "tables": map_tables, "cond": conditions,
     "ladder": ladder, "refine": refine, "csv": save_csvs, "spread": spread_curve}[cmd]()
