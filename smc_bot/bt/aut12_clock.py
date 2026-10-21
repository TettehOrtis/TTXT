"""AUT12 - price around fixed clock events (London fixes, COMEX open, US data time, jobs report).

Governing documents: bt/AUT11_14_BRIEF.md, bt/AUTOPSY_BRIEF.md. Writes ONLY results/AUT12_clock/.
Pre-registration (written before any simulation): results/AUT12_clock/preregistration.txt

Run from the TTXT root:
    set SMC_DATASET=duka
    .venv\\Scripts\\python.exe smc_bot/bt/aut12_clock.py events
    .venv\\Scripts\\python.exe smc_bot/bt/aut12_clock.py explore
    ...

Design notes
  * Bars: A.safe_bars (corrupt-bar screen, missing-spread repair; gold gets 0.05 = $0.50), then CUT at
    2021-01-01 so the sealed EXAM can never be touched. Cost is paid once, inside common.simulate.
  * Every event-instance is simulated TWICE (forced long, forced short) per (hold, shape). Any rule
    (follow/fade, lookback 30/60) just picks the long or short row, so the rule and its random-direction
    null are measured on EXACTLY the same fills. coin = mean(long, short) is the exact expectation of a
    random direction at the same minutes.
  * Barriers hang off the SIGNAL price (close of the last closed M1 bar), never the fill.
"""
import os
import sys
import json
import time

os.environ.setdefault("SMC_DATASET", "duka")
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                                                        # noqa: E402
import autopsy_lib as A                                                   # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT12_clock")
os.makedirs(OUT, exist_ok=True)
CUT = pd.Timestamp("2021-01-01")            # bars are cut here: the sealed EXAM is never loaded into a frame
SYMS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
FX = ["EURUSD", "GBPUSD", "USDJPY"]
EVENTS = {                                  # name: (zone, hour, minute)
    "LFIX_AM": ("Europe/London", 10, 30),
    "LFIX_PM": ("Europe/London", 15, 0),
    "COMEX": ("America/New_York", 8, 20),
    "DATA": ("America/New_York", 8, 30),
    "NFP": ("America/New_York", 8, 30),
}
LOOKBACKS = (30, 60)
HOLDS = (15, 30, 60, 120)
BASE_SHAPE = (1.5, 1.0)
SPR_MULT = 1.0
STAGE3 = ("EXPLORE", "VALIDATE", "STRESS")

_ORIG_BARS = C.bars
_MEMO = {}


def bars(sym, tf):
    """safe_bars + cut at 2021 + optional spread multiplier (stress). Memoised."""
    key = (sym, tf, SPR_MULT)
    if key in _MEMO:
        return _MEMO[key]
    C.bars = _ORIG_BARS
    try:
        b = A.safe_bars(sym, tf)
    finally:
        C.bars = bars
    b = b[b.time < CUT].reset_index(drop=True)
    if SPR_MULT != 1.0:
        b = b.copy()
        b["spr"] = b.spr.values * SPR_MULT
    for c in ("time", "tclose"):
        b[c] = b[c].astype("datetime64[ns]")
    _MEMO[key] = b
    return b


C.bars = bars


def set_mult(m):
    global SPR_MULT
    SPR_MULT = float(m)


# ---------------------------------------------------------------- event clock (DST aware)
def event_times(name, d0, d1):
    """True-UTC timestamps of the event on every weekday d0..d1 (NFP: first Friday only)."""
    tzname, hh, mm = EVENTS[name]
    tz = ZoneInfo(tzname)
    days = pd.date_range(d0.normalize(), d1.normalize(), freq="D")
    days = days[days.dayofweek < 5]
    if name == "NFP":
        days = days[(days.dayofweek == 4) & (days.day <= 7)]
    loc = [pd.Timestamp(year=x.year, month=x.month, day=x.day, hour=hh, minute=mm, tz=tz) for x in days]
    utc = pd.DatetimeIndex([x.tz_convert("UTC").tz_localize(None) for x in loc])
    return days, utc


# ---------------------------------------------------------------- features (all from data CLOSED before the event minute)
def build_events(sym):
    b = bars(sym, "M1")
    t = b.time.values
    op, hi, lo, cl = b.open.values, b.high.values, b.low.values, b.close.values
    spr = b.spr.values
    rngcs = np.concatenate([[0.0], np.cumsum(hi - lo)])
    b15, bh1, bh4 = bars(sym, "M15"), bars(sym, "H1"), bars(sym, "H4")
    P = C.pip(sym)
    a15 = A.atr(b15, 14)
    av = a15.values
    med60 = pd.Series(av).rolling(96 * 60, min_periods=96 * 5).median().values
    day = b.time.dt.normalize()
    dd = pd.DataFrame({"day": day, "h": b.high, "l": b.low, "c": b.close}).groupby("day").agg(
        h=("h", "max"), l=("l", "min"), c=("c", "last"))
    dd["rng"] = dd.h - dd.l
    dd["med20"] = dd.rng.shift(1).rolling(20, min_periods=10).median()
    dd["ret_prev"] = dd.c.diff()                                   # return of day d (known only after d closes)
    d0, d1 = b.time.iloc[0], b.time.iloc[-1]
    ema_h1 = np.asarray(C.ema(bh1.close, 50))
    ema_h4 = np.asarray(C.ema(bh4.close, 50))
    rows = []
    skipped = {}
    TOL = np.timedelta64(3, "m")
    for name in EVENTS:
        days, utc = event_times(name, d0, d1)
        tv = t
        pos = np.searchsorted(tv, utc.values)
        for dday, E, p in zip(days, utc, pos):
            if p >= len(tv) - 130 or p < 200:
                continue
            Ev = np.datetime64(E)
            q = p - 1                                       # last bar that opened before the event minute
            if tv[p] - Ev > TOL or Ev - tv[q] > np.timedelta64(4, "m"):
                skipped[name] = skipped.get(name, 0) + 1    # no tick within 3 minutes of the event
                continue
            r = dict(sym=sym, ev=name, date=dday, t_event=E, iE=int(p), ref=cl[q], open_E=op[p], spr_E=spr[p],
                     fill_delay_min=float((tv[p] - Ev) / np.timedelta64(1, "m")))
            ok = True
            for L in LOOKBACKS:
                st = np.searchsorted(tv, Ev - np.timedelta64(L, "m"))
                if tv[st] - (Ev - np.timedelta64(L, "m")) > TOL or st >= q:
                    ok = False
                    break
                mv = cl[q] - op[st]
                r["move%d" % L] = mv
                trav = rngcs[q + 1] - rngcs[st]
                r["eff%d" % L] = abs(mv) / trav if trav > 0 else np.nan
            if not ok:
                skipped[name] = skipped.get(name, 0) + 1
                continue
            st5 = np.searchsorted(tv, Ev - np.timedelta64(5, "m"))
            r["move5"] = cl[q] - op[st5] if st5 < q else np.nan
            rows.append(r)
    ev = pd.DataFrame(rows)
    ev["t_signal"] = ev.t_event
    E_arr = ev.t_event.values
    # ATR of the last closed M15 bar, rank, direction
    i15 = C.asof_index(b15, E_arr)
    j15 = np.clip(i15, 0, len(av) - 1)
    ev["atr15"] = av[j15]
    ev["f_atr_vs_med"] = av[j15] / med60[j15]
    ev["f_atr_dir"] = av[j15] / av[np.clip(i15 - 60, 0, len(av) - 1)]
    # HTF side (last CLOSED bar)
    for tf, bb, e in (("h1", bh1, ema_h1), ("h4", bh4, ema_h4)):
        k = np.clip(C.asof_index(bb, E_arr), 0, len(e) - 1)
        ev["f_%s_rel" % tf] = np.sign(ev.ref.values - e[k])
        ev["f_%s_dist" % tf] = (ev.ref.values - e[k]) / ev.atr15.values
    # A6 day range spent, A8 prior day move
    dstart = np.searchsorted(t, ev.date.values.astype("datetime64[ns]"))
    spent = np.full(len(ev), np.nan)
    iE = ev.iE.values
    for k in range(len(ev)):
        s0 = dstart[k]
        if iE[k] - s0 >= 30:
            spent[k] = hi[s0:iE[k]].max() - lo[s0:iE[k]].min()
    prior_day = ev.date - pd.Timedelta(days=1)
    med20 = dd.med20.reindex(ev.date.values).values
    ev["f_range_spent"] = spent / med20
    # previous completed day's return = last close of the previous calendar day with data minus the one before
    dret = dd.ret_prev
    prev_idx = np.searchsorted(dd.index.values, ev.date.values.astype("datetime64[ns]")) - 1
    ev["prev_day_ret"] = np.where(prev_idx >= 0, dret.values[np.clip(prev_idx, 0, len(dret) - 1)], np.nan)
    # cost share of one ATR
    ev["f_cost_share"] = (C.COMM[sym] + ev.spr_E.values / P) / (ev.atr15.values / P)
    ev["hour"] = ev.t_event.dt.hour
    ev["dow"] = ev.t_event.dt.dayofweek
    ev["month"] = ev.t_event.dt.month
    ev["year"] = ev.t_event.dt.year
    ev["f_monthend"] = ((ev.t_event.dt.day >= 27) | (ev.t_event.dt.day <= 2)).astype(float)
    ev["f_friday"] = (ev.dow == 4).astype(float)
    ev["stage"] = A.stage_of(ev.t_event)
    # move relative to this event's own recent normal (previous 20 occurrences, same instrument/event)
    for L in LOOKBACKS:
        ev["f_move_vs_med20_%d" % L] = np.nan
    for name, g in ev.groupby("ev"):
        for L in LOOKBACKS:
            a = g["move%d" % L].abs()
            m20 = a.shift(1).rolling(20, min_periods=10).median()
            ev.loc[g.index, "f_move_vs_med20_%d" % L] = (a / m20).values
    ev = ev[ev.stage != "OUT"].reset_index(drop=True)
    return ev, skipped


def events_all(force=False):
    f = os.path.join(OUT, "events.parquet")
    if os.path.exists(f) and not force:
        return pd.read_parquet(f)
    parts = []
    sk = {}
    for s in SYMS:
        t0 = time.time()
        e, skipped = build_events(s)
        parts.append(e)
        sk[s] = skipped
        print(s, len(e), skipped, "%.0fs" % (time.time() - t0), flush=True)
    ev = pd.concat(parts, ignore_index=True)
    ev["eid"] = np.arange(len(ev))
    ev.to_parquet(f, index=False)
    json.dump(sk, open(os.path.join(OUT, "events_skipped.json"), "w"), indent=1)
    return ev


# ---------------------------------------------------------------- simulation: forced long and forced short
def make_orders(e, shape, hold, early=0):
    """Two orders per event (+1 and -1). Barriers on the SIGNAL price. early=1 -> look-ahead twin (fill one minute earlier)."""
    sd, ss = shape[1], shape[0]                       # stop mult, target mult
    outs = []
    for side in (1, -1):
        tdist, sdist = shape[0] * e.atr15.values, shape[1] * e.atr15.values
        o = pd.DataFrame({
            "t_signal": (e.t_event - pd.Timedelta(minutes=early)).values,
            "side": side,
            "entry_type": "market",
            "entry": np.nan,
            "expire": (e.t_event + pd.Timedelta(minutes=hold)).values,
            "sl": e.ref.values - side * sdist,
            "tp": e.ref.values + side * tdist,
            "be_at_r": np.nan,
            "max_hold_min": float(hold),
            "eid": e.eid.values,
        })
        outs.append(o)
    return pd.concat(outs, ignore_index=True)


def sim_events(ev, stage, shape=BASE_SHAPE, hold=60, early=0, cache=True, tag="", events=None):
    """Long and short fills for every event of `stage`. Returns the trade table (filled rows only) with eid."""
    A.gate(stage)
    e = ev[ev.stage == stage]
    if events is not None:
        e = e[e.ev.isin(events)]
        tag = tag + "_" + "-".join(events)
    key = "sim_%s_%s_%g_%g_h%d_e%d_m%g%s.parquet" % (stage, "all", shape[0], shape[1], hold, early, SPR_MULT, tag)
    f = os.path.join(OUT, "cache", key)
    os.makedirs(os.path.join(OUT, "cache"), exist_ok=True)
    if cache and os.path.exists(f):
        return pd.read_parquet(f)
    parts = []
    for s in SYMS:
        g = e[e.sym == s]
        if len(g) == 0:
            continue
        o = make_orders(g, shape, hold, early)
        tr = C.simulate(s, o, one_at_a_time=False)
        parts.append(tr)
    tr = pd.concat(parts, ignore_index=True)
    tr = tr[tr.status == "filled"].copy()
    keep = ["sym", "eid", "side", "t_signal", "t_fill", "t_exit", "fill", "exit", "exit_reason", "hold_h", "risk_pips",
            "gross_pips", "net_pips", "R", "usd_01", "mfe_R", "mae_R", "sl", "tp"]
    tr = tr[keep]
    if cache:
        tr.to_parquet(f, index=False)
    return tr


def rule_side(e, L, mode):
    """+1/-1/0 : follow or fade the sign of the pre-event move."""
    s = np.sign(e["move%d" % L].values).astype(int)
    return s if mode == "follow" else -s


def pick(ev, sim, L, mode, events=None, syms=None, side=None):
    """Trades of one rule (event, L, mode) by picking the long or short row of each event. Also returns the mirror
    row (the other side) so the exact random-direction expectation can be formed."""
    e = ev
    if events is not None:
        e = e[e.ev.isin(events)]
    if syms is not None:
        e = e[e.sym.isin(syms)]
    sd = pd.Series(rule_side(e, L, mode), index=e.eid.values)
    sd = sd[sd != 0]
    s = sim[sim.eid.isin(sd.index)]
    s = s.merge(sd.rename("rside"), left_on="eid", right_index=True)
    mine = s[s.side == s.rside]
    other = s[s.side != s.rside][["eid", "usd_01", "net_pips", "gross_pips"]].rename(
        columns={"usd_01": "usd_other", "net_pips": "net_other", "gross_pips": "gross_other"})
    out = mine.merge(other, on="eid", how="inner")
    out["usd_coin"] = 0.5 * (out.usd_01 + out.usd_other)
    out["net_coin"] = 0.5 * (out.net_pips + out.net_other)
    out["win_coin"] = 0.5 * ((out.net_pips > 0).astype(float) + (out.net_other > 0).astype(float))
    return out


def edge_row(tr):
    """One line of numbers for a picked rule: trades, right in 100, $/trade, coin, edge over coin, give-or-take.
    give_or_take is a 95% half-width that clusters events by calendar day (EURUSD and GBPUSD move together)."""
    n = len(tr)
    if n == 0:
        return dict(trades=0)
    d = (tr.usd_01 - tr.usd_coin).values
    day = pd.to_datetime(tr.t_signal).dt.normalize().values
    dbar = d.mean()
    cs = pd.Series(d - dbar).groupby(day).sum().values
    se = np.sqrt((cs ** 2).sum()) / n if n > 1 else np.nan
    gp = tr.usd_01[tr.usd_01 > 0].sum()
    gl = -tr.usd_01[tr.usd_01 < 0].sum()
    return dict(trades=n,
                right_in_100=round(100 * (tr.net_pips > 0).mean(), 2),
                coin_right_in_100=round(100 * tr.win_coin.mean(), 2),
                usd_per_trade=round(tr.usd_01.mean(), 3),
                coin_usd=round(tr.usd_coin.mean(), 3),
                edge_over_coin_usd=round(dbar, 3),
                give_or_take_usd=round(1.96 * se, 3) if np.isfinite(se) else None,
                gross_usd_per_trade=round(float(usd_gross(tr).mean()), 3),
                PF=round(gp / gl, 3) if gl > 0 else None)


def usd_gross(tr):
    """Gross dollars (before the single cost deduction) from net_pips and the per-pip value."""
    pv = tr.sym.map(C.USD_PIP_01)
    return tr.gross_pips * pv



def rand_band(tr, draws=2000, seed=0):
    """Random direction at the SAME events: pick each event's long or short row by a fair coin. Returns the mean
    dollars per trade at the 2.5 / 50 / 97.5 percent points of the draws, and the share of draws at or above the rule."""
    if len(tr) == 0:
        return dict(rand_lo=None, rand_mid=None, rand_hi=None, share_random_beats_rule=None)
    r = np.random.default_rng(seed)
    a, b = tr.usd_01.values, tr.usd_other.values
    pickmine = r.random((draws, len(a))) < 0.5
    m = np.where(pickmine, a, b).mean(axis=1)
    return dict(rand_lo=round(float(np.percentile(m, 2.5)), 3), rand_mid=round(float(np.percentile(m, 50)), 3),
                rand_hi=round(float(np.percentile(m, 97.5)), 3),
                share_random_beats_rule=round(float((m >= a.mean()).mean()), 4))


def side_split(tr):
    d = {}
    for nm, sdv in (("buy", 1), ("sell", -1)):
        g = tr[tr.side == sdv]
        d["%s_trades" % nm] = len(g)
        d["%s_right_in_100" % nm] = round(100 * (g.net_pips > 0).mean(), 2) if len(g) else None
        d["%s_usd_per_trade" % nm] = round(g.usd_01.mean(), 3) if len(g) else None
        d["%s_edge_over_coin" % nm] = round((g.usd_01 - g.usd_coin).mean(), 3) if len(g) else None
    return d


def grid(ev, stage, shape=BASE_SHAPE, holds=HOLDS, events=None):
    """Every rule cell x hold, pooled and by instrument. The one table EXPLORE chooses from."""
    rows = []
    for hold in holds:
        sim = sim_events(ev, stage, shape, hold)
        for name in (events or EVENTS):
            for L in LOOKBACKS:
                for mode in ("follow", "fade"):
                    tr = pick(ev[ev.stage == stage], sim, L, mode, events=[name])
                    base = dict(stage=stage, ev=name, L=L, mode=mode, hold=hold, shape="%g/%g" % shape, scope="POOLED")
                    rows.append({**base, **edge_row(tr), **rand_band(tr), **side_split(tr)})
                    for s in SYMS:
                        g = tr[tr.sym == s]
                        rows.append({**base, "scope": s, **edge_row(g)})
    return pd.DataFrame(rows)


def explore():
    ev = events_all()
    t0 = time.time()
    g = grid(ev, "EXPLORE")
    g.to_csv(os.path.join(OUT, "grid_EXPLORE.csv"), index=False)
    print("grid done %.0fs" % (time.time() - t0))
    p = g[g.scope == "POOLED"].sort_values("edge_over_coin_usd", ascending=False)
    print(p.head(15)[["ev", "L", "mode", "hold", "trades", "right_in_100", "coin_right_in_100", "usd_per_trade", "coin_usd",
                      "edge_over_coin_usd", "give_or_take_usd", "share_random_beats_rule"]].to_string())
    print(p.tail(5)[["ev", "L", "mode", "hold", "trades", "usd_per_trade", "coin_usd", "edge_over_coin_usd"]].to_string())
    return g


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "events"
    if cmd == "events":
        ev = events_all(force=True)
        print(ev.groupby(["sym", "ev", "stage"]).size().unstack().to_string())
    elif cmd == "explore":
        explore()
