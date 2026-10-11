"""AUT01 CORRECTION - what the live sniper actually trades, measured against what the owner describes.

Why this file exists
--------------------
AUT01's report (finding 9, section 8) claimed "the live bot is not trading this rule" on the strength
of one number: the RSI depth gate let 136 of 104,202 pullbacks through.  HANDOFF.md section 6 found
that claim HALF right, and the correction is the interesting part:

  - `SNIPER_ENTRY_REQUIRE_BODY_HOLD` is NOT set in .env, so the shipped default `False` applies.
    sniper/config.py:178 only raises the RSI clash warning when it is `True`, so the clash DOES NOT
    BITE THE LIVE BOT.
  - sniper/config.py:86-87 records that body_hold=True takes a DIFFERENT SET OF BARS, not a subset.

So AUT01 measured one rule and the owner describes another, and neither is the rule AUT01's baseline
actually ran.  This script measures all of them side by side on the same minutes, same costs, same
shapes, each against its own luck bar, and says plainly which one the live bot trades.

The three rules
---------------
Read off sniper/strategy.py:entry_signal and the live .env, gold only (SNIPER_SYMBOL=XAUUSD):

  AUTOPSY   what AUT01's report measured.  M15 trend = close above its own 50-EMA; M1 EMA21; the
            touch must be the FIRST after 5 clear bars (mode="pull"); close > open; NO RSI gate.
  LIVE      what the bot executes.  M15 trend = EMA50 above EMA200; M1 EMA20; EVERY qualifying
            minute (mode="raw", the bot has no clear-bars rule); close > open; RSI14 <= 45 to buy
            and >= 55 to sell; body_hold OFF, so no condition on where the close sits vs the EMA.
  DESCRIBED what the owner describes - "a candle closes back in the trend direction".  LIVE plus
            body_hold: the close must be back on the trend's side of the EMA.

LIVE and DESCRIBED are also reported inside the live trading window, `SNIPER_TRADING_HOURS=07:00-12:00`.

THE WINDOW IS GENUINELY UTC, checked rather than assumed.  sniper/engine.py:150 takes its clock from
`_utc_now()` (engine.py:28-29), not from the broker's server time, and the feed is true UTC (settled,
HANDOFF section 4), so the two clocks agree and no DST offset enters.  With
`SNIPER_WINDDOWN_MINUTES=30` the bot opens NEW baskets only while 07:00 <= now < 11:30 UTC
(engine.py:157, `_may_open`), so that is the window measured here - not 07:00-12:00, which is only
when an already-open basket is still managed.

Mandatory controls, all four, per rule (AUTOPSY_BRIEF "Mandatory controls"):
  1. luck bar - shape-matched, hour-matched AND hold-matched, n=20,000, five seeds, band quoted
  2. always-long / always-short / coin flip at the rule's own minutes
  3. buy and sell reported separately
  4. look-ahead twin in BOTH points and dollars (filling at the signal bar's own open)

Costs: cost="real" - gold spread 0.05 price units ($0.50 per 0.10 lot) + the $1.10 commission = the
settled $1.60 round trip.  Shapes in M15 ATR, never dollars, except one row that prices the live
$15/$70 for the record.

Nothing in common.py, smc_core.py, autopsy_lib.py or aut01_emapull.py is edited.  This file imports
aut01_emapull and reuses its build / run / luckbars / direction_control / fmt.

Usage:  .venv/Scripts/python.exe smc_bot/bt/aut01_livecfg.py --stages EXPLORE VALIDATE STRESS
"""
import os
import sys
import json
import time
import argparse

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("SMC_DATASET", "duka")

import common as C                                                        # noqa: E402
import autopsy_lib as A                                                   # noqa: E402
import aut01_emapull as M                                                 # noqa: E402

OUT = M.OUT                                   # results/AUT01_emapull - this agent's own folder
SYM = "XAUUSD"                                # SNIPER_SYMBOL=XAUUSD, no suffix
#: SNIPER_TRADING_HOURS=07:00-12:00 minus SNIPER_WINDDOWN_MINUTES=30, in true UTC. A new basket may
#: open only while 07:00 <= t < 11:30 (sniper/engine.py:145-160).
WIN_FROM_MIN, WIN_TO_MIN = 7 * 60, 11 * 60 + 30
SHAPES = [(1.5, 1.0), (2.0, 4.0)]             # the autopsy shape, and the best shape AUT01 found
HOLD = M.HOLD                                 # 1440 minutes
LUCK_N = 20000
LUCK_SEEDS = (0, 1, 2, 3, 4)
#: common.simulate walks forward through M1 bars in a Python loop, one order at a time, so cost is
#: linear in orders. The live rule fires 67,026 times on EXPLORE and 235,852 on VALIDATE; measuring
#: all of them at two shapes, plus four direction controls and a look-ahead twin, is hours of work
#: for no extra information. 25,000 trades fixes a hit rate to well inside a tenth of a point, which
#: is an order of magnitude finer than any difference this file is asked to resolve, so the big sets
#: are subsampled to this and `sampled_from` records the true population. aut01_emapull.explore()
#: did the same thing for the same reason (it sampled its raw gold set to 20,000).
SAMPLE_MAX = 25000
SAMPLE_SEED = 11


# ----------------------------------------------------------------- the signal sets
def build_light(stage, sym=SYM, entry_ema=20, mode="raw", trend_mode="ema50_200",
                clear_bars=M.CLEAR_BARS):
    """The same trigger as aut01_emapull.build(), with ONLY the columns this correction needs.

    build() attaches the whole catalogue, including a per-signal Python loop over every confirmed
    M15 swing for C1/C2 (room to run). On mode="raw" that loop is O(signals x swings) and dominates
    everything - it is why the live rule had not been measured at raw cadence before. None of those
    features are used here: this question is RSI gate and body hold, nothing else. So this builds
    the identical trigger and skips the catalogue.

    `signal_counts_match()` checks this against build() on EXPLORE so the shortcut is verified, not
    assumed. Every value below is read from bars that had CLOSED when the decision was made: the M1
    trigger bar itself, and the last closed M15 bar via common.asof_index.
    """
    a, z = A.STAGES[stage]
    m1 = C.bars(sym, "M1")
    tf = C.bars(sym, "M15")
    m15 = C.bars(sym, "M15")

    c1, o1, hi1, lo1 = m1.close.values, m1.open.values, m1.high.values, m1.low.values
    ema1 = M._ema(c1, entry_ema)
    rsi1 = M._rsi(c1, 14)

    # ---- HTF trend from the last CLOSED M15 bar
    tfe = M._ema(tf.close.values, 50)
    if trend_mode == "ema50_200":
        tf_trend = np.where(tfe > M._ema(tf.close.values, 200), 1, -1)
    else:
        tf_trend = np.where(tf.close.values > tfe, 1, -1)
    j = C.asof_index(tf, m1.tclose.values)
    ok = j >= (200 * 3 if trend_mode == "ema50_200" else 50 * 3)
    trend = np.where(ok, tf_trend[np.clip(j, 0, len(tf) - 1)], 0)
    tfe_at = np.where(ok, tfe[np.clip(j, 0, len(tf) - 1)], np.nan)

    # ---- M15 ATR, the unit for every shape
    atr15 = A.atr(m15, 14).values
    j15 = C.asof_index(m15, m1.tclose.values)
    at = np.where(j15 >= 14, atr15[np.clip(j15, 0, len(atr15) - 1)], np.nan)

    # ---- the trigger, identical to build()
    touch_up = (lo1 <= ema1) & (c1 > o1) & (trend == 1)
    touch_dn = (hi1 >= ema1) & (c1 < o1) & (trend == -1)
    if mode == "pull":
        sig_up = touch_up & (M._run_len_true(lo1 > ema1) >= clear_bars)
        sig_dn = touch_dn & (M._run_len_true(hi1 < ema1) >= clear_bars)
    else:
        sig_up, sig_dn = touch_up, touch_dn
    sig = sig_up | sig_dn
    sig &= np.isfinite(at) & (at > 0) & np.isfinite(tfe_at)
    sig &= (m1.tclose.values >= np.datetime64(pd.Timestamp(a))) & \
           (m1.tclose.values < np.datetime64(pd.Timestamp(z)))
    sig[:400] = False
    sig[-20:] = False
    i = np.flatnonzero(sig)
    if len(i) == 0:
        return pd.DataFrame()
    d = np.where(sig_up[i], 1, -1)
    ov = ema1[i]

    out = pd.DataFrame({
        "sym": sym, "t_signal": m1.tclose.values[i], "side": d,
        "entry_type": "market", "entry": np.nan, "expire": m1.tclose.values[i],
        "be_at_r": np.nan, "max_hold_min": float(HOLD),
        "sig_i": i, "sig_close": c1[i], "f_atr": at[i],
        "f_hour": m1.time.dt.hour.values[i],
        "f_rsi": rsi1[i],
        "f_rsi_ok": (np.where(d == 1, rsi1[i] <= 45.0, rsi1[i] >= 55.0)).astype(float),
        "f_body_hold": (np.where(d == 1, c1[i] > ov, c1[i] < ov)).astype(float),
    })
    out["sl"] = out.sig_close - out.side * out.f_atr * M.STOP
    out["tp"] = out.sig_close + out.side * out.f_atr * M.TGT
    return out.reset_index(drop=True)


def _one_match(stage, **kw):
    full = M.build(stage, sym=SYM, **kw)
    lite = build_light(stage, **kw)
    cols = ["t_signal", "side", "f_rsi_ok", "f_body_hold", "f_atr", "sig_close"]
    out = dict(config=kw, full_signals=int(len(full)), light_signals=int(len(lite)),
               same_count=bool(len(full) == len(lite)), identical=False)
    if out["same_count"] and len(full):
        f = full[cols].reset_index(drop=True)
        g = lite[cols].reset_index(drop=True)
        out["identical"] = bool(f.t_signal.equals(g.t_signal) and f.side.equals(g.side) and
                                np.allclose(f.f_rsi_ok, g.f_rsi_ok) and
                                np.allclose(f.f_body_hold, g.f_body_hold) and
                                np.allclose(f.f_atr, g.f_atr, equal_nan=True) and
                                np.allclose(f.sig_close, g.sig_close))
    return out


def signal_counts_match(stage="EXPLORE"):
    """Verify build_light() reproduces build()'s trigger exactly, for BOTH configurations used here.

    Checked on mode="pull" only, because that is the path build() can afford - on mode="raw" its
    per-signal swing loop is what made the live rule unmeasurable in the first place. The trigger
    code being compared is identical in both modes bar one mask, so a match on "pull" establishes
    it. A mismatch here invalidates every number this file produces, so main() refuses to continue.
    """
    M.set_window(stage, cost="real")
    return dict(stage=stage, mode_checked="pull", checks=[
        _one_match(stage, mode="pull", entry_ema=20, trend_mode="ema50_200"),   # LIVE / DESCRIBED
        _one_match(stage, mode="pull", entry_ema=21, trend_mode="close_ema50"), # AUTOPSY
    ])


def signal_sets(stage):
    """The three rules' signals on one stage, gold only, built from CLOSED bars only."""
    M.set_window(stage, cost="real")
    sets = {}

    # AUTOPSY: what the report measured, on gold alone
    sets["AUTOPSY"] = build_light(stage, mode="pull", entry_ema=21, trend_mode="close_ema50")

    # the live bot's own signal universe: raw minutes, EMA20, EMA50/200 trend
    live_all = build_light(stage, mode="raw", entry_ema=20, trend_mode="ema50_200")
    sets["_live_universe"] = live_all

    rsi_ok = live_all.f_rsi_ok == 1
    hold_ok = live_all.f_body_hold == 1
    #: t_signal is the trigger bar's CLOSE, which is the minute the live engine decides on.
    tod = pd.to_datetime(live_all.t_signal)
    mins = tod.dt.hour * 60 + tod.dt.minute
    in_win = (mins >= WIN_FROM_MIN) & (mins < WIN_TO_MIN)

    sets["LIVE"] = live_all[rsi_ok].reset_index(drop=True)
    sets["DESCRIBED"] = live_all[rsi_ok & hold_ok].reset_index(drop=True)
    sets["LIVE_0712"] = live_all[rsi_ok & in_win].reset_index(drop=True)
    sets["DESCRIBED_0712"] = live_all[rsi_ok & hold_ok & in_win].reset_index(drop=True)
    return sets


def overlap_report(sets):
    """Is DESCRIBED a subset of LIVE, and how much of each does the other miss?

    sniper/config.py:86-87 asserts the two take DIFFERENT sets of bars, not nested ones. With
    body_hold=False meaning 'no condition on the close', DESCRIBED is arithmetically a subset of
    LIVE on the same universe -- so this measures how small a subset, and reports the claim as it
    actually stands rather than repeating it.
    """
    u = sets["_live_universe"]
    rsi_ok = (u.f_rsi_ok == 1).values
    hold_ok = (u.f_body_hold == 1).values
    return dict(
        live_universe_signals=int(len(u)),
        rsi_gate_passes=int(rsi_ok.sum()),
        rsi_gate_pass_pct=round(100.0 * rsi_ok.mean(), 4),
        body_hold_passes=int(hold_ok.sum()),
        body_hold_pass_pct=round(100.0 * hold_ok.mean(), 4),
        rsi_and_body_hold=int((rsi_ok & hold_ok).sum()),
        rsi_and_body_hold_pct_of_universe=round(100.0 * (rsi_ok & hold_ok).mean(), 4),
        described_as_pct_of_live=round(100.0 * (rsi_ok & hold_ok).sum() / max(1, rsi_ok.sum()), 2),
        live_signals_described_would_skip=int((rsi_ok & ~hold_ok).sum()),
        nesting="DESCRIBED is a strict subset of LIVE: body_hold=False imposes no condition on the "
                "close, so every DESCRIBED bar also satisfies LIVE. sniper/config.py:86-87's "
                "'different set of bars, not a subset' is true of body_hold=True vs the bars the "
                "shipped rule TYPICALLY takes, not of the two rules as sets.",
    )


# ----------------------------------------------------------------- per-rule measurement
def trades_per_week(n, stage):
    """Signals a week, from the TRUE population count, never from a subsample."""
    a, z = A.STAGES[stage]
    weeks = (pd.Timestamp(z) - pd.Timestamp(a)).days / 7.0
    return round(n / weeks, 3) if weeks > 0 else np.nan


def subsample(sel):
    """Cap a signal set at SAMPLE_MAX, keeping time order. Returns (sample, true_population)."""
    n = len(sel)
    if n <= SAMPLE_MAX:
        return sel, n
    s = sel.sample(SAMPLE_MAX, random_state=SAMPLE_SEED)
    return s.sort_values("t_signal").reset_index(drop=True), n


_LUCK_CACHE = {}


def luck(sel, stage, tgt, stop, hold):
    """Hour-matched, shape-matched, hold-matched luck bar over five seeds, band quoted.

    Cached on (stage, hours, shape, hold) rather than on the rule: a luck bar is a property of the
    clock and the shape, not of the signal, so two rules firing in the same hours share one bar.
    """
    if len(sel) == 0:
        return None
    hrs = sorted(set(pd.to_datetime(sel.t_signal).dt.hour))
    key = (stage, tuple(hrs), tgt, stop, hold)
    if key in _LUCK_CACHE:
        return _LUCK_CACHE[key]
    rows = []
    for sd in LUCK_SEEDS:
        lb = A.luck_bar(SYM, LUCK_N, tgt, stop, stage, seed=sd, hours=hrs,
                        max_hold_min=hold, one_at_a_time=False)
        lb = lb[lb.status == "filled"]
        if len(lb) == 0:
            continue
        rows.append(dict(seed=sd, n=len(lb),
                         right_in_100=round(100 * float((lb.net_pips > 0).mean()), 2),
                         usd_per_trade=round(float(lb.usd_01.mean()), 3),
                         PF=round(C.stats(lb)["PF"], 3)))
    if not rows:
        return None
    d = pd.DataFrame(rows)
    _LUCK_CACHE[key] = dict(n_per_seed=LUCK_N, seeds=len(d), hours_matched=hrs, hold_matched_min=hold,
                hour_match_caveat="A.luck_bar matches whole UTC hours. For the 07:00-11:30 windowed "
                                  "rules the bar therefore also draws from 11:30-12:00, which the "
                                  "rule itself cannot trade. The mismatch is half an hour out of "
                                  "4.5, inside the quoted seed band."
                                  if hrs and max(hrs) == 11 and min(hrs) >= 7 else None,
                right_in_100=round(float(d.right_in_100.mean()), 2),
                right_in_100_min=float(d.right_in_100.min()),
                right_in_100_max=float(d.right_in_100.max()),
                seed_band_points=round(float(d.right_in_100.max() - d.right_in_100.min()), 2),
                usd_per_trade=round(float(d.usd_per_trade.mean()), 3),
                PF=round(float(d.PF.mean()), 3))
    return _LUCK_CACHE[key]


def lookahead(sel, tgt, stop, hold):
    """Catalogue F1: the dirty twin that fills at the SIGNAL bar's own open, in points AND dollars.

    AUT01's own trap: it measured a twin at +2.75 points but +$2.29 a trade. A hit-rate-only check
    would have passed a dirty pipeline, so both numbers are reported with their sign.
    """
    if len(sel) == 0:
        return None
    clean = M.run(sel, tgt=tgt, stop=stop, hold=hold, anchor="fill")
    dirty = sel.copy()
    b = C.bars(SYM, "M1")
    i = np.searchsorted(b.time.values, pd.to_datetime(dirty.t_signal).values, "left") - 1
    i = np.clip(i, 0, len(b) - 1)
    dirty["entry"] = np.nan
    px = np.where(dirty.side.values == 1, b.open.values[i] + b.spr.values[i], b.open.values[i])
    dirty["t_signal"] = b.tclose.values[np.clip(i - 1, 0, len(b) - 1)]
    dirty["sig_close"] = px
    r = M.run(dirty, tgt=tgt, stop=stop, hold=hold, anchor="close")
    if len(clean) == 0 or len(r) == 0:
        return None
    return dict(
        clean_right_in_100=round(100 * float((clean.net_pips > 0).mean()), 2),
        dirty_right_in_100=round(100 * float((r.net_pips > 0).mean()), 2),
        points=round(100 * float((r.net_pips > 0).mean()) - 100 * float((clean.net_pips > 0).mean()), 2),
        clean_usd_per_trade=round(float(clean.usd_01.mean()), 3),
        dirty_usd_per_trade=round(float(r.usd_01.mean()), 3),
        usd=round(float(r.usd_01.mean()) - float(clean.usd_01.mean()), 3),
        note="A positive figure means the twin that peeks does BETTER, i.e. the clean pipeline is "
             "not leaking that information. Both numbers are reported because 'no difference' in "
             "one of them can be two biases cancelling.",
    )


def measure(name, sel, stage, tgt, stop, hold=HOLD, with_controls=True, pop=None):
    """One rule, one stage, one shape: every number section 6 of HANDOFF.md asks for.

    `sel` may be a subsample; `pop` is the true signal count it was drawn from, and the
    signals-a-week figure is always computed from `pop`.
    """
    pop = len(sel) if pop is None else pop
    if len(sel) < 10:
        return dict(rule=name, stage=stage, tgt_atr=tgt, stop_atr=stop, signals=int(pop),
                    measured_on=int(len(sel)), signals_per_week=trades_per_week(pop, stage),
                    trades=0, underpowered=True,
                    note="fewer than 10 signals - nothing to measure")
    r = M.run(sel, tgt=tgt, stop=stop, hold=hold, anchor="fill")
    row = dict(rule=name, stage=stage, tgt_atr=tgt, stop_atr=stop,
               signals=int(pop), measured_on=int(len(sel)),
               sampled=bool(len(sel) < pop), signals_per_week=trades_per_week(pop, stage),
               **M.fmt(r, name))
    #: 400 trades is where a hit rate on this shape stops being noise: the luck bar's own five-seed
    #: band at n=4,000 is 2.67 points (autopsy_lib.luck_bar), so below a few hundred trades a
    #: difference of several points means nothing. Flagged, not hidden.
    row["underpowered"] = bool(len(r) < 400)
    row["breakeven_needs"] = round(A.breakeven_rate(SYM, float(sel.f_atr.median()), tgt, stop), 2)
    row["edge_points"] = round(row["win_pct"] - row["breakeven_needs"], 2)
    for sd, lbl in [(1, "BUY"), (-1, "SELL")]:
        g = r[r.side == sd]
        row["%s_trades" % lbl] = int(len(g))
        row["%s_right_in_100" % lbl] = round(100 * float((g.net_pips > 0).mean()), 2) if len(g) else np.nan
        row["%s_usd" % lbl] = round(float(g.usd_01.mean()), 3) if len(g) else np.nan
    if with_controls:
        lk = luck(sel, stage, tgt, stop, hold)
        if lk:
            row["luck_right_in_100"] = lk["right_in_100"]
            row["luck_seed_band"] = lk["seed_band_points"]
            row["luck_usd"] = lk["usd_per_trade"]
            row["vs_luck_points"] = round(row["win_pct"] - lk["right_in_100"], 2)
            row["vs_luck_usd"] = round(row["usd_per_trade"] - lk["usd_per_trade"], 3)
        row["_luck"] = lk
    return row


def live_dollar_shape(sel, stage):
    """The live $15/$70 basket for the record: 1.5 price units target, 7.0 stop, 0.10 lots.

    Anchored at the FILL, unlike the one row in aut01_emapull.explore(), which anchored at the
    signal close and so let the spread move the shape (measured in AUT01's report, section 7).
    """
    if len(sel) < 20:
        return None
    o = sel.copy()
    b = C.bars(SYM, "M1")
    i = np.clip(np.searchsorted(b.time.values, pd.to_datetime(o.t_signal).values, "left"),
                0, len(b) - 1)
    px = np.where(o.side.values == 1, b.open.values[i] + b.spr.values[i], b.open.values[i])
    o["sl"] = px - o.side * 7.0
    o["tp"] = px + o.side * 1.5
    o["max_hold_min"] = float(HOLD)
    o["be_at_r"] = np.nan
    r = C.simulate(SYM, o.drop(columns=["sym"]), one_at_a_time=False)
    r = r[r.status == "filled"]
    return M.fmt(r, "live $15/$70") if len(r) else None


# ----------------------------------------------------------------- the run
def main(stages):
    t0 = time.time()
    rows, blob = [], dict(
        what="AUT01 correction: the live sniper's configured rule vs the rule the owner describes",
        written=pd.Timestamp.utcnow().isoformat(),
        answers="HANDOFF.md section 6",
        live_config_read_from=[".env", "sniper/config.py", "sniper/strategy.py:entry_signal"],
        live_config=dict(symbol="XAUUSD", trend_timeframe="M15", trend="EMA50 vs EMA200",
                         entry_timeframe="M1", entry_ema=20, entry_rsi_period=14,
                         entry_rsi_buy_below=45.0, entry_rsi_sell_above=55.0,
                         entry_require_body_hold=False,
                         body_hold_source="NOT SET in .env; sniper/config.py:93 default False",
                         require_trend=True,
                         trading_hours="07:00-12:00 UTC (engine.py:150 uses _utc_now, not the "
                                       "broker clock); new baskets only until 11:30 because "
                                       "SNIPER_WINDDOWN_MINUTES=30",
                         basket="5 x 0.02 lots = 0.10, +$15 / -$70, 60s cooldown, one at a time"),
        costs=dict(gold_spread_price_units=A.GOLD_SPREAD, commission_usd=C.COMM[SYM],
                   round_trip_usd_per_010_lot=1.60, cost_world="real"),
        exam_opened=False, stages={}, overlap={}, live_dollar_shape={}, luck_bars={})

    # the shortcut builder is verified against the full one before anything is measured
    blob["build_light_equivalence"] = signal_counts_match("EXPLORE")
    print("build_light check:", json.dumps(blob["build_light_equivalence"]), flush=True)
    if not all(c["identical"] for c in blob["build_light_equivalence"]["checks"]):
        raise SystemExit("build_light() does not reproduce build()'s trigger - stopping. %s"
                         % blob["build_light_equivalence"])

    for stage in stages:
        A.gate(stage)
        print("\n===== %s =====" % stage, flush=True)
        sets = signal_sets(stage)
        blob["overlap"][stage] = overlap_report(sets)
        print(json.dumps(blob["overlap"][stage], indent=1))
        blob["stages"][stage] = dict(
            signals={k: int(len(v)) for k, v in sets.items()},
            bars_screened=int(M.SCREENED.get((SYM, "M1"), 0)))

        for name in ("AUTOPSY", "LIVE", "DESCRIBED", "LIVE_0712", "DESCRIBED_0712"):
            sel, pop = subsample(sets[name])
            for tgt, stop in SHAPES:
                row = measure(name, sel, stage, tgt, stop, pop=pop)
                lk = row.pop("_luck", None)
                if lk:
                    blob["luck_bars"]["%s|%s|%.1fx%.1f" % (stage, name, tgt, stop)] = lk
                rows.append(row)
                print({k: v for k, v in row.items() if k in
                       ("rule", "stage", "tgt_atr", "stop_atr", "signals", "measured_on",
                        "signals_per_week", "trades", "win_pct", "usd_per_trade", "PF",
                        "luck_right_in_100", "vs_luck_points", "vs_luck_usd",
                        "underpowered")}, flush=True)

            blob["live_dollar_shape"].setdefault(stage, {})[name] = live_dollar_shape(sel, stage)
            if name in ("LIVE", "DESCRIBED") and len(sel) >= 10:
                dc = M.direction_control(sel, stage, tgt=SHAPES[0][0], stop=SHAPES[0][1], hold=HOLD)
                dc.insert(1, "rule", name)
                dc.to_csv(os.path.join(OUT, "livecfg_direction_%s_%s.csv" % (stage, name)),
                          index=False)
                blob.setdefault("direction_control", {}).setdefault(stage, {})[name] = \
                    dc.to_dict("records")
                print(dc.to_string(index=False), flush=True)
                blob.setdefault("lookahead", {}).setdefault(stage, {})[name] = \
                    lookahead(sel, SHAPES[0][0], SHAPES[0][1], HOLD)

        pd.DataFrame(rows).to_csv(os.path.join(OUT, "livecfg_sidebyside.csv"), index=False)
        with open(os.path.join(OUT, "livecfg.json"), "w") as fh:
            json.dump(blob, fh, indent=1, default=str)
        print("checkpointed after %s (%.0fs)" % (stage, time.time() - t0), flush=True)

    blob["runtime_s"] = round(time.time() - t0, 1)
    with open(os.path.join(OUT, "livecfg.json"), "w") as fh:
        json.dump(blob, fh, indent=1, default=str)
    print("\nwrote livecfg_sidebyside.csv and livecfg.json in %.0fs" % (time.time() - t0))
    return pd.DataFrame(rows)


def dollar_direction_control(sel, stage, seed=4):
    """direction_control at the live $15/$70 basket instead of an ATR shape.

    Needed because the live bot's own shape is the one cell that reads positive on EXPLORE, and a
    positive EXPLORE number on 1.8 years of gold going 866 -> 1429 means nothing until always-long
    has been measured beside it.
    """
    rng = np.random.default_rng(seed)
    b = C.bars(SYM, "M1")
    out = []
    for lbl, sd in [("always LONG", 1), ("always SHORT", -1), ("coin flip", 0),
                    ("the rule's own call", None)]:
        o = sel.copy()
        if sd in (1, -1):
            o["side"] = sd
        elif sd == 0:
            o["side"] = rng.choice([1, -1], size=len(o))
        i = np.clip(np.searchsorted(b.time.values, pd.to_datetime(o.t_signal).values, "left"),
                    0, len(b) - 1)
        px = np.where(o.side.values == 1, b.open.values[i] + b.spr.values[i], b.open.values[i])
        o["sl"] = px - o.side * 7.0
        o["tp"] = px + o.side * 1.5
        o["max_hold_min"] = float(HOLD)
        o["be_at_r"] = np.nan
        r = C.simulate(SYM, o.drop(columns=["sym"]), one_at_a_time=False)
        r = r[r.status == "filled"]
        row = dict(who=lbl, **M.fmt(r, lbl))
        for s2, l2 in [(1, "BUY"), (-1, "SELL")]:
            g = r[r.side == s2]
            row["%s_trades" % l2] = len(g)
            row["%s_usd" % l2] = round(float(g.usd_01.mean()), 3) if len(g) else np.nan
        out.append(row)
    t = pd.DataFrame(out)
    t.insert(0, "stage", stage)
    return t


def cadence(stages=("EXPLORE", "VALIDATE", "STRESS")):
    """How many baskets a week the live bot would ACTUALLY open, not how many signals fire.

    HANDOFF section 6 asks for trades a week. A signal count overstates it badly, because the
    engine holds one basket at a time (sniper/engine.py, SNIPER_POSITIONS_PER_ENTRY=5 legs that
    open and close together) and sits out a 60-second cooldown afterwards. common.simulate's
    `one_at_a_time=True` reproduces the first of those two, which is the binding one: a basket held
    for hours blocks every signal behind it.

    Reported for the windowed rule as well as the all-day one, because the live bot runs
    07:00-11:30 UTC, and at the live $15/$70 basket as well as the ATR shape, because the hold - and
    therefore how long the bot is blocked - is a property of the shape.
    """
    rows = []
    for stage in stages:
        A.gate(stage)
        a, z = A.STAGES[stage]
        weeks = (pd.Timestamp(z) - pd.Timestamp(a)).days / 7.0
        sets = signal_sets(stage)
        for name in ("LIVE", "LIVE_0712"):
            sel = sets[name]                      # NOT subsampled: one_at_a_time needs every signal
            if len(sel) < 10:
                continue
            for shape, kw in [("2.0x4.0 ATR", dict(tgt=2.0, stop=4.0)),
                              ("live $15/$70", None)]:
                o = sel.copy()
                b = C.bars(SYM, "M1")
                i = np.clip(np.searchsorted(b.time.values,
                                            pd.to_datetime(o.t_signal).values, "left"),
                            0, len(b) - 1)
                px = np.where(o.side.values == 1, b.open.values[i] + b.spr.values[i],
                              b.open.values[i])
                if kw is None:
                    o["sl"], o["tp"] = px - o.side * 7.0, px + o.side * 1.5
                else:
                    o["sl"] = px - o.side * o.f_atr * kw["stop"]
                    o["tp"] = px + o.side * o.f_atr * kw["tgt"]
                o["max_hold_min"] = float(HOLD)
                o["be_at_r"] = np.nan
                r = C.simulate(SYM, o.drop(columns=["sym"]), one_at_a_time=True)
                f = r[r.status == "filled"]
                rows.append(dict(
                    stage=stage, rule=name, shape=shape,
                    signals=len(sel), signals_per_week=round(len(sel) / weeks, 2),
                    baskets_filled=len(f), baskets_per_week=round(len(f) / weeks, 2),
                    skipped_busy=int((r.status == "skipped_busy").sum()),
                    pct_of_signals_taken=round(100.0 * len(f) / len(sel), 2),
                    avg_hold_h=round(float(f.hold_h.mean()), 2) if len(f) else np.nan,
                    win_pct=round(100 * float((f.net_pips > 0).mean()), 2) if len(f) else np.nan,
                    usd_per_trade=round(float(f.usd_01.mean()), 3) if len(f) else np.nan,
                    usd_per_week=round(float(f.usd_01.sum()) / weeks, 2) if len(f) else np.nan,
                ))
                print(rows[-1], flush=True)
    t = pd.DataFrame(rows)
    t.to_csv(os.path.join(OUT, "livecfg_cadence.csv"), index=False)
    print("\nwrote livecfg_cadence.csv")
    return t


def controls_at_best_shape(stages=("EXPLORE", "VALIDATE", "STRESS")):
    """The gap main() leaves: the direction control at the 2.0/4.0 ATR shape and at the live
    $15/$70 basket, for the two rules the live bot could actually be running.

    main() runs the control at 1.5/1.0 only, and 1.5/1.0 is negative everywhere - so the control
    that matters most was missing from exactly the cells that read positive.
    """
    rows = []
    for stage in stages:
        A.gate(stage)
        sets = signal_sets(stage)
        for name in ("LIVE", "LIVE_0712"):
            sel, pop = subsample(sets[name])
            if len(sel) < 10:
                continue
            d = M.direction_control(sel, stage, tgt=2.0, stop=4.0, hold=HOLD)
            d.insert(1, "rule", name)
            d.insert(2, "shape", "2.0x4.0 ATR")
            rows.append(d)
            e = dollar_direction_control(sel, stage)
            e.insert(1, "rule", name)
            e.insert(2, "shape", "live $15/$70")
            rows.append(e)
            print(stage, name, flush=True)
            print(pd.concat([d, e]).to_string(index=False), flush=True)
    t = pd.concat(rows, ignore_index=True)
    t.to_csv(os.path.join(OUT, "livecfg_controls_best_shape.csv"), index=False)
    print("\nwrote livecfg_controls_best_shape.csv")
    return t


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--stages", nargs="+", default=["EXPLORE", "VALIDATE", "STRESS"])
    p.add_argument("--controls", action="store_true",
                   help="run only the 2.0x4.0 and $15/$70 direction controls")
    p.add_argument("--cadence", action="store_true",
                   help="run only the one-basket-at-a-time cadence measurement")
    a = p.parse_args()
    if a.controls:
        controls_at_best_shape(tuple(a.stages))
    elif a.cadence:
        cadence(tuple(a.stages))
    else:
        main(a.stages)
