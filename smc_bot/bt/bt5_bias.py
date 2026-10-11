"""BT5 -- HIGHER-TIMEFRAME BIAS AND CONTEXT (owner: Fable).

Question (PLAN.md / smc-strategy.md section 5.2, 5.7):
  Does the HTF bias the strategy relies on (1H structure + price vs 1H EMA200, 30M agreeing, EMA50) actually predict
  direction over the next 4h/12h/24h/3d, in ATR and in pips, before and after costs, better than a random baseline?
  Which discretionary CONTEXT (trending vs ranging, breakout vs retest, premium/discount of the dealing range,
  distance from EMA) should BLOCK trading? Finally: turn the best bias definition into a simple pullback trade
  (M15/M30 entry, stop beyond the pullback swing, target the next HTF liquidity) and see whether bias alone pays
  after costs.

Run from TTXT root:  .venv\\Scripts\\python.exe smc_bot\\bt\\bt5_bias.py
Two phases, both in this file:
  PART A (lines ~60-330): descriptive study of bias definitions and context, DEV only, no trading, no simulate().
  PART B (lines ~330-...): turns the best bias def into a real limit-order trade tested through simulate();
                            <=20 variants on DEV, <=3 confirmed on VAL, TEST never touched.
"""
import os, sys, time
import numpy as np
import pandas as pd

import common as _common
from common import bars, simulate, pip, COMM, PAIRS, PERIODS, period_of, asof_index, stats, report_table, save
from common import structure, ema, atr

# ------------------------------------------------------------------------------------------------------------------
# BUG WORKAROUND (documented in report.md; common.py NOT edited, per the brief).
# common.py's simulate() (bt/common.py:95/98/106) builds `tsig = np.datetime64(pd.Timestamp(r.t_signal))`, which is
# datetime64[ns], and compares it with `np.searchsorted(t1, tsig, ...)` where t1 = bars(sym,"M1").time.values is
# datetime64[ms] (the cached parquet's native unit). Mixed-unit datetime64 searchsorted silently falls back to an
# O(n) comparison per call instead of O(log n) -- confirmed by direct timing: 500 calls take 0.0s when both sides
# are [ms], ~30s when one side is [ns] on a 2.1M-row M1 array. That turns every simulate() call into O(orders*bars)
# instead of O(orders*log(bars)): one 1772-order run took 99.7s; patched, 0.44s (>200x). This silently taxes EVERY
# backtest in this program that calls simulate() on more than a few hundred orders. Worked around here (without
# touching common.py or the shared parquet cache) by monkey-patching the `bars` symbol inside the `common` module's
# own namespace so that simulate()'s internal `bars(sym, "M1")` call returns an M1 frame whose time/tclose columns
# are already datetime64[ns]. This only changes an in-memory copy for this process; the cached .parquet on disk and
# every other agent's process are untouched.
# ------------------------------------------------------------------------------------------------------------------
_orig_bars = _common.bars


def _ns_patched_bars(sym, tf):
    df = _orig_bars(sym, tf)
    if tf == "M1" and df["time"].values.dtype != np.dtype("datetime64[ns]"):
        df = df.copy()
        df["time"] = df["time"].values.astype("datetime64[ns]")
        df["tclose"] = df["tclose"].values.astype("datetime64[ns]")
    return df


_common.bars = _ns_patched_bars

RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "BT5")
os.makedirs(RESULTS, exist_ok=True)
DEV_END = pd.Timestamp(PERIODS["DEV"][1])
VAL_END = pd.Timestamp(PERIODS["VAL"][1])

HORIZONS_H = [4, 12, 24, 72]     # hours: 4h, 12h, 24h, 3 days
HZ_LABEL = {4: "4h", 12: "12h", 24: "24h", 72: "3d"}


# ==================================================================================================================
# helpers not in smc_core (own code, per the brief's "write only your own script" rule)
# ==================================================================================================================
def adx14(b, n=14):
    """Wilder-style ADX(n) on closed bars. Causal: value at bar k uses only bars <= k. First bar is NaN (needs a diff)."""
    h, l, c = b.high.values, b.low.values, b.close.values
    up = h[1:] - h[:-1]
    dn = l[:-1] - l[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    atr_s = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values
    atr_s = np.where(atr_s <= 0, np.nan, atr_s)
    pdi = 100 * pd.Series(plus_dm).ewm(alpha=1 / n, adjust=False).mean().values / atr_s
    mdi = 100 * pd.Series(minus_dm).ewm(alpha=1 / n, adjust=False).mean().values / atr_s
    denom = (pdi + mdi)
    dx = 100 * np.abs(pdi - mdi) / np.where(denom == 0, np.nan, denom)
    adx_s = pd.Series(dx).ewm(alpha=1 / n, adjust=False).mean().values
    return np.concatenate([[np.nan], adx_s])


def h4_range_context(h4, win=30, breakout_window=8, retest_atr=0.5, fail_atr=1.0):
    """Causal H4 dealing-range state machine.
    win: bars in the 'prior range' (30 H4 bars ~ 5 days). breakout_window: how many H4 bars a breakout stays 'fresh'.
    Returns: pos (0..1 position of close in the prior range = premium/discount), state per bar:
      0 none, 1 breakout up (fresh bar), 2 retest of the up breakout level, 3 failed up breakout (closed back through),
      -1/-2/-3 mirror for down breakouts.
    """
    h, l, c = h4.high.values, h4.low.values, h4.close.values
    n = len(h4)
    roll_hi = pd.Series(h).rolling(win).max().shift(1).values
    roll_lo = pd.Series(l).rolling(win).min().shift(1).values
    a = atr(h4, 14)
    pos = (c - roll_lo) / np.where((roll_hi - roll_lo) == 0, np.nan, (roll_hi - roll_lo))
    state = np.zeros(n, np.int8)
    cur_dir, cur_level, age = 0, np.nan, 999
    for k in range(n):
        if np.isnan(roll_hi[k]) or np.isnan(roll_lo[k]):
            continue
        ak = a[k] if not np.isnan(a[k]) else 1e9
        broke_up = c[k] > roll_hi[k]
        broke_dn = c[k] < roll_lo[k]
        if broke_up:
            cur_dir, cur_level, age = 1, roll_hi[k], 0
        elif broke_dn:
            cur_dir, cur_level, age = -1, roll_lo[k], 0
        elif cur_dir != 0:
            age += 1
        if cur_dir == 1:
            if age > breakout_window:
                cur_dir = 0
            elif c[k] < cur_level - fail_atr * ak:
                state[k] = 3; cur_dir = 0
            elif age == 0:
                state[k] = 1
            elif abs(c[k] - cur_level) <= retest_atr * ak:
                state[k] = 2
        elif cur_dir == -1:
            if age > breakout_window:
                cur_dir = 0
            elif c[k] > cur_level + fail_atr * ak:
                state[k] = -3; cur_dir = 0
            elif age == 0:
                state[k] = -1
            elif abs(c[k] - cur_level) <= retest_atr * ak:
                state[k] = -2
    return pos, state


STATE_NAME = {0: "NONE", 1: "BREAKOUT_UP", 2: "RETEST_UP", 3: "FAIL_UP", -1: "BREAKOUT_DN", -2: "RETEST_DN", -3: "FAIL_DN"}


# ==================================================================================================================
# PART A: build the per-pair, per-H1-bar context table (bias defs + discretionary context), DEV only
# ==================================================================================================================
def build_table(sym):
    h1 = bars(sym, "H1"); h4 = bars(sym, "H4"); m30 = bars(sym, "M30")
    st_h1 = structure(h1, 5)
    st_h4 = structure(h4, 5)
    st_m30 = structure(m30, 4)
    ema200_h1 = ema(h1.close.values, 200)
    ema50_m30 = ema(m30.close.values, 50)
    atr14_h1 = atr(h1, 14)
    atr100_h1 = atr(h1, 100)
    adx_h1 = adx14(h1)
    pos_h4, state_h4 = h4_range_context(h4)

    idx_h4 = asof_index(h4, h1.tclose.values)
    idx_m30 = asof_index(m30, h1.tclose.values)
    ok = (idx_h4 >= 0) & (idx_m30 >= 0)

    n = len(h1)
    trend_h4_asof = np.full(n, 0, np.int8)
    pos_h4_asof = np.full(n, np.nan)
    state_h4_asof = np.full(n, 0, np.int8)
    trend_h4_asof[ok] = st_h4["trend"][idx_h4[ok]]
    pos_h4_asof[ok] = pos_h4[idx_h4[ok]]
    state_h4_asof[ok] = state_h4[idx_h4[ok]]

    trend_m30_asof = np.full(n, 0, np.int8)
    close_m30_asof = np.full(n, np.nan)
    ema50_m30_asof = np.full(n, np.nan)
    trend_m30_asof[ok] = st_m30["trend"][idx_m30[ok]]
    close_m30_asof[ok] = m30.close.values[idx_m30[ok]]
    ema50_m30_asof[ok] = ema50_m30[idx_m30[ok]]

    df = pd.DataFrame({
        "sym": sym, "k": np.arange(n), "t": h1.tclose.values, "close_h1": h1.close.values,
        "atr14_h1": atr14_h1, "atr100_h1": atr100_h1, "adx14_h1": adx_h1,
        "trend_h1": st_h1["trend"], "ema200_h1": ema200_h1,
        "trend_h4": trend_h4_asof, "pos_h4": pos_h4_asof, "state_h4": state_h4_asof,
        "trend_m30": trend_m30_asof, "close_m30": close_m30_asof, "ema50_m30": ema50_m30_asof,
        "spr_h1_pips": h1.spr.values / pip(sym),
    })
    df["dist_ema_atr"] = (df.close_h1 - df.ema200_h1).abs() / df.atr14_h1
    df["range_contraction"] = df.atr14_h1 / df.atr100_h1
    df["regime"] = np.where(df.adx14_h1 >= 25, "TREND", np.where(df.adx14_h1 < 18, "RANGE", "MIXED"))
    df["extended"] = df.dist_ema_atr > 2.0
    df["state_h4_name"] = df.state_h4.map(STATE_NAME)
    return df


def bias_defs(df):
    sign_ema200 = np.sign(df.close_h1.values - df.ema200_h1.values)
    sign_ema50m30 = np.sign(df.close_m30.values - df.ema50_m30.values)
    th1 = df.trend_h1.values.astype(float)
    th4 = df.trend_h4.values.astype(float)
    tm30 = df.trend_m30.values.astype(float)

    def agree(*arrs):
        base = arrs[0]
        ok = np.ones(len(base), bool)
        for a in arrs[1:]:
            ok &= (a == base)
        out = np.where(ok, base, 0)
        return out

    d = {}
    d["h1_struct"] = th1
    d["h4_struct"] = th4
    d["ema200_h1"] = sign_ema200
    d["ema50_m30"] = sign_ema50m30
    d["m30_struct"] = tm30
    d["h1_and_ema200"] = agree(th1, sign_ema200)
    d["h4_and_h1"] = agree(th4, th1)
    d["spec_full_struct"] = agree(th1, sign_ema200, tm30)      # strategy 5.2: 1H structure + EMA200 + 30M agrees (structure)
    d["spec_full_ema50"] = agree(th1, sign_ema200, sign_ema50m30)  # ...30M agrees read as EMA50 M30 side
    return d


def forward_returns(df):
    """Add fwd_pips_<h>/fwd_atr_<h> columns: price move `h` hours after df.t (>=0 rows, no direction applied yet)."""
    tclose = df.t.values
    close = df.close_h1.values
    for h in HORIZONS_H:
        target = tclose + np.timedelta64(h, "h")
        idx = np.searchsorted(tclose, target, side="left")
        valid = idx < len(tclose)
        fwd = np.full(len(df), np.nan)
        fwd[valid] = close[idx[valid]]
        df[f"fwdpx_{h}"] = fwd
    return df


def shuffle_null(dirn, raw_pips, n_shuf=200, seed=0):
    """Null mean/std of the signed-pips mean if the nonzero signs of `dirn` were random (shuffle sign, keep |dir| mask)."""
    rng = np.random.default_rng(seed)
    mask = dirn != 0
    if mask.sum() < 30:
        return np.nan, np.nan
    signs = np.sign(dirn[mask])
    mags = raw_pips[mask] * signs   # "unsigned" move already in the actual-bias direction sign convention removed:
    # recover the underlying unsigned move: raw_pips passed in is UNSIGNED (dir not yet applied) so just multiply by random signs
    means = np.empty(n_shuf)
    for i in range(n_shuf):
        rs = rng.choice([-1.0, 1.0], size=mask.sum())
        means[i] = np.nanmean(raw_pips[mask] * rs)
    return means.mean(), means.std()


def part_a(pairs):
    print("PART A: building per-pair context tables ...")
    tabs = []
    for sym in pairs:
        t0 = time.time()
        df = build_table(sym)
        df = forward_returns(df)
        tabs.append(df)
        print(f"  {sym}: {len(df)} H1 bars in {time.time()-t0:.1f}s")
    full = pd.concat(tabs, ignore_index=True)
    full["period"] = period_of(full.t)
    dev = full[full.period == "DEV"].copy()
    dev = dev[(dev.k >= 250) & dev.trend_h4.notna() & dev.close_m30.notna()].reset_index(drop=True)
    print(f"PART A: DEV rows (all pairs, k>=250) = {len(dev)}")

    biases = bias_defs(dev)
    cost_pips = dev.spr_h1_pips.values + dev.sym.map(COMM).values

    rows = []
    null_rows = []
    for name, dirn in biases.items():
        cov = (dirn != 0).mean() * 100
        for h in HORIZONS_H:
            raw_move = (dev[f"fwdpx_{h}"].values - dev.close_h1.values)     # unsigned price move
            unsigned_pips = raw_move / dev.sym.map(pip).values
            signed_pips = unsigned_pips * dirn
            signed_atr = (raw_move * dirn) / dev.atr14_h1.values
            after_pips = signed_pips - cost_pips
            after_atr = signed_atr - (cost_pips * dev.sym.map(pip).values) / dev.atr14_h1.values
            m = (dirn != 0) & np.isfinite(signed_pips)
            n = int(m.sum())
            if n < 30:
                continue
            null_mean, null_std = shuffle_null(dirn[m], unsigned_pips[m])
            z = (np.nanmean(signed_pips[m]) - null_mean) / null_std if null_std and null_std > 0 else np.nan
            rows.append(dict(bias=name, horizon=HZ_LABEL[h], n=n, coverage_pct=round(cov, 1),
                              mean_pips_before=round(np.nanmean(signed_pips[m]), 2),
                              mean_pips_after=round(np.nanmean(after_pips[m]), 2),
                              mean_atr_before=round(np.nanmean(signed_atr[m]), 3),
                              mean_atr_after=round(np.nanmean(after_atr[m]), 3),
                              win_pct_after=round((after_pips[m] > 0).mean() * 100, 1),
                              null_mean_pips=round(null_mean, 2) if null_mean == null_mean else np.nan,
                              z_vs_random=round(z, 2) if z == z else np.nan))
    tab_a = pd.DataFrame(rows)
    tab_a.to_csv(os.path.join(RESULTS, "partA_bias_predictive.csv"), index=False)
    print("\n=== PART A: bias definitions vs random baseline (DEV, pooled 10 pairs) ===")
    print(tab_a.to_string(index=False))

    # ---- context stratification for the two most interesting bias defs, headline horizon 24h -------------------
    strat_rows = []
    for name in ["h1_struct", "h1_and_ema200"]:
        dirn = biases[name]
        h = 24
        raw_move = (dev[f"fwdpx_{h}"].values - dev.close_h1.values)
        signed_pips = (raw_move / dev.sym.map(pip).values) * dirn
        after_pips = signed_pips - cost_pips
        signed_atr = (raw_move * dirn) / dev.atr14_h1.values
        d2 = dev.copy()
        d2["dir"] = dirn
        d2["after_pips"] = after_pips
        d2["signed_atr"] = signed_atr
        d2 = d2[d2.dir != 0]
        for label, g in d2.groupby("regime"):
            strat_rows.append(dict(bias=name, cut="regime", value=label, n=len(g),
                                    mean_pips_after=round(g.after_pips.mean(), 2), mean_atr_after=round(g.signed_atr.mean(), 3),
                                    win_pct=round((g.after_pips > 0).mean() * 100, 1)))
        for label, g in d2.groupby("extended"):
            strat_rows.append(dict(bias=name, cut="extended", value=str(label), n=len(g),
                                    mean_pips_after=round(g.after_pips.mean(), 2), mean_atr_after=round(g.signed_atr.mean(), 3),
                                    win_pct=round((g.after_pips > 0).mean() * 100, 1)))
        for label, g in d2.groupby("state_h4_name"):
            strat_rows.append(dict(bias=name, cut="h4_breakout_state", value=label, n=len(g),
                                    mean_pips_after=round(g.after_pips.mean(), 2), mean_atr_after=round(g.signed_atr.mean(), 3),
                                    win_pct=round((g.after_pips > 0).mean() * 100, 1)))
        # premium/discount aligned with bias direction: favourable = buy in discount (pos<0.4) / sell in premium (pos>0.6)
        d2["favourable_pd"] = ((d2.dir > 0) & (d2.pos_h4 < 0.4)) | ((d2.dir < 0) & (d2.pos_h4 > 0.6))
        d2["unfavourable_pd"] = ((d2.dir > 0) & (d2.pos_h4 > 0.6)) | ((d2.dir < 0) & (d2.pos_h4 < 0.4))
        for label, mask in [("favourable(discount/premium)", d2.favourable_pd), ("unfavourable(premium/discount)", d2.unfavourable_pd),
                             ("equilibrium", ~d2.favourable_pd & ~d2.unfavourable_pd)]:
            g = d2[mask]
            if len(g) < 20:
                continue
            strat_rows.append(dict(bias=name, cut="premium_discount", value=label, n=len(g),
                                    mean_pips_after=round(g.after_pips.mean(), 2), mean_atr_after=round(g.signed_atr.mean(), 3),
                                    win_pct=round((g.after_pips > 0).mean() * 100, 1)))
    tab_strat = pd.DataFrame(strat_rows)
    tab_strat.to_csv(os.path.join(RESULTS, "partA_context_stratified.csv"), index=False)
    print("\n=== PART A: context stratification (24h horizon, after costs, DEV pooled) ===")
    print(tab_strat.to_string(index=False))
    return dev, biases


# ==================================================================================================================
# PART B: turn the best bias definition into a real trade -- pullback entry on M15, stop beyond the pullback swing,
#         target the next HTF liquidity (smc-strategy.md 5.4/5.5/5.7), tested through simulate() for real fills/costs.
# ==================================================================================================================
def htf_liquidity(sym):
    """H1 and H4 structure (for trend, and lsh/lsl = last confirmed swing high/low = 'next HTF liquidity')."""
    h1 = bars(sym, "H1"); h4 = bars(sym, "H4")
    st1 = structure(h1, 5); st4 = structure(h4, 5)
    return h1, st1, h4, st4


def h1_context_light(sym):
    """Re-derive the H1-cadence bias/gate context (same defs as Part A) for use inside Part B's M15 trade generator."""
    df = build_table(sym)
    biases = bias_defs(df)
    return df, biases


def gate_pass(gate, d, regime, state_h4):
    if gate is None:
        return True
    fresh_same_dir = (d == 1 and state_h4 in (1, 2)) or (d == -1 and state_h4 in (-1, -2))
    if gate == "block_fresh_breakout":
        return not fresh_same_dir
    if gate == "block_trend_regime":
        return regime != "TREND"
    if gate == "block_both":
        return (not fresh_same_dir) and (regime != "TREND")
    raise ValueError(gate)


def build_orders(sym, core_bias="h4_struct", gate=None, event_set=(1, 2, -1, -2), entry_style="edge",
                  target_tf="H1", min_rr=1.5, sl_buf_atr=0.15, expiry_bars=16):
    """One row per candidate trade (bias-aligned M15 structure break = 'pullback resolved'), ready for simulate()."""
    h1, st1, h4, st4 = htf_liquidity(sym)
    ctx, biases = h1_context_light(sym)
    dirn_h1 = biases[core_bias]                          # bias direction, H1 cadence, same order as ctx rows
    regime_h1 = ctx.regime.values
    state_h4_h1 = ctx.state_h4.values
    t_h1 = ctx.t.values                                   # H1 tclose (decision available from here)

    m15 = bars(sym, "M15")
    st15 = structure(m15, 3)
    a15 = atr(m15, 14)
    evt = st15["evt"]; ob_lo = st15["ob_lo"]; ob_hi = st15["ob_hi"]
    tclose15 = m15.tclose.values

    idx_h1_of_m15 = asof_index(pd.DataFrame({"tclose": t_h1}), tclose15)     # last H1 ctx row closed at/before M15 close
    idx_h4_of_m15 = asof_index(h4, tclose15)

    rows = []
    P = pip(sym)
    for k in np.where(evt != 0)[0]:
        if evt[k] not in event_set:
            continue
        d = 1 if evt[k] > 0 else -1
        ih1 = idx_h1_of_m15[k]
        if ih1 < 0:
            continue
        bd = dirn_h1[ih1]
        if bd != d:
            continue
        if not gate_pass(gate, d, regime_h1[ih1], state_h4_h1[ih1]):
            continue
        lo, hi = ob_lo[k], ob_hi[k]
        if not (hi > lo):
            continue
        buf = sl_buf_atr * (a15[k] if a15[k] == a15[k] else 0.0)
        if entry_style == "edge":
            entry = hi if d == 1 else lo
        else:
            entry = (lo + hi) / 2
        sl = lo - buf if d == 1 else hi + buf
        ih4 = idx_h4_of_m15[k]
        if target_tf == "H1":
            tp = st1["lsh"][ih1] if d == 1 else st1["lsl"][ih1]
        else:
            tp = st4["lsh"][ih4] if (ih4 >= 0 and d == 1) else (st4["lsl"][ih4] if ih4 >= 0 else np.nan)
        if not np.isfinite(tp) or (tp - entry) * d <= 0:
            continue
        risk = (entry - sl) * d
        if not risk > 0:
            continue
        rr = (tp - entry) * d / risk
        if rr < min_rr:
            continue
        t_signal = pd.Timestamp(tclose15[k])
        rows.append(dict(t_signal=t_signal, side=d, entry_type="limit", entry=entry,
                          expire=t_signal + pd.Timedelta(minutes=15 * expiry_bars), sl=sl, tp=tp,
                          be_at_r=np.nan, max_hold_min=np.nan,
                          sl_reason="beyond M15 pullback OB (+0.15 ATR buffer)",
                          tp_reason=f"next {target_tf} swing liquidity (lsh/lsl)",
                          event_kind="BOS" if abs(evt[k]) == 1 else "CHoCH", bias=core_bias, gate=str(gate),
                          rr_planned=round(rr, 2)))
    return pd.DataFrame(rows)


def run_variant(name, pairs, **kw):
    all_orders = []
    for sym in pairs:
        o = build_orders(sym, **kw)
        if len(o):
            o.insert(0, "sym", sym)
            all_orders.append(o)
    if not all_orders:
        return None, pd.DataFrame()
    trades_by_sym = []
    for sym, grp in pd.concat(all_orders, ignore_index=True).groupby("sym"):
        trades_by_sym.append(simulate(sym, grp.drop(columns=["sym"])))
    trades = pd.concat(trades_by_sym, ignore_index=True)
    tab = save("BT5", name, trades)
    return trades, tab


VARIANTS = [
    dict(name="v01_h4struct_nogate",      core_bias="h4_struct",  gate=None),
    dict(name="v02_h4struct_blockbreak",  core_bias="h4_struct",  gate="block_fresh_breakout"),
    dict(name="v03_h4struct_blocktrend",  core_bias="h4_struct",  gate="block_trend_regime"),
    dict(name="v04_h4struct_blockboth",   core_bias="h4_struct",  gate="block_both"),
    dict(name="v05_ema200_nogate",        core_bias="ema200_h1",  gate=None),
    dict(name="v06_ema200_blockbreak",    core_bias="ema200_h1",  gate="block_fresh_breakout"),
    dict(name="v07_ema200_blocktrend",    core_bias="ema200_h1",  gate="block_trend_regime"),
    dict(name="v08_ema200_blockboth",     core_bias="ema200_h1",  gate="block_both"),
    dict(name="v09_h1struct_blockboth",   core_bias="h1_struct",  gate="block_both"),
    dict(name="v10_h4struct_bosOnly",     core_bias="h4_struct",  gate="block_both", event_set=(1, -1)),
    dict(name="v11_h4struct_chochOnly",   core_bias="h4_struct",  gate="block_both", event_set=(2, -2)),
    dict(name="v12_h4struct_targetH4",    core_bias="h4_struct",  gate="block_both", target_tf="H4"),
    dict(name="v13_h4struct_rr1p2",       core_bias="h4_struct",  gate="block_both", min_rr=1.2),
    dict(name="v14_h4struct_rr2p0",       core_bias="h4_struct",  gate="block_both", min_rr=2.0),
    dict(name="v15_h4struct_midEntry",    core_bias="h4_struct",  gate="block_both", entry_style="mid"),
    dict(name="v16_h4struct_wide_expiry", core_bias="h4_struct",  gate="block_both", expiry_bars=32),
]


def part_b_dev(pairs):
    print("\n\nPART B: building & simulating pullback trades, DEV only, per variant ...")
    summary = []
    for v in VARIANTS:
        kw = {k: x for k, x in v.items() if k != "name"}
        trades, tab = run_variant(v["name"], pairs, **kw)
        if trades is None or not len(trades):
            summary.append(dict(variant=v["name"], **kw, trades=0)); continue
        dev_t = trades[(trades.period == "DEV") & (trades.status == "filled")]
        s = stats(dev_t) if len(dev_t) else dict(trades=0)
        summary.append(dict(variant=v["name"], core_bias=kw.get("core_bias"), gate=kw.get("gate"), **s))
        print(f"  {v['name']:28s} DEV: {s}")
    summ = pd.DataFrame(summary)
    summ.to_csv(os.path.join(RESULTS, "partB_variants_DEV.csv"), index=False)
    print("\n=== PART B: all variants, DEV, pooled 10 pairs ===")
    print(summ.to_string(index=False))
    return summ


def part_b_val(pairs, chosen):
    print("\n\nPART B: confirming chosen variants on VAL ...")
    rows = []
    for name in chosen:
        kw = {k: x for k, x in next(v for v in VARIANTS if v["name"] == name).items() if k != "name"}
        trades, tab = run_variant(name, pairs, **kw)
        val_t = trades[(trades.period == "VAL") & (trades.status == "filled")]
        s = stats(val_t) if len(val_t) else dict(trades=0)
        rows.append(dict(variant=name, **s))
        print(f"  {name}: VAL {s}")
        per_pair = report_table(trades[trades.t_signal.notna()], by=("sym", "period"))
        per_pair.to_csv(os.path.join(RESULTS, f"perpair_{name}.csv"), index=False)
    pd.DataFrame(rows).to_csv(os.path.join(RESULTS, "partB_chosen_VAL.csv"), index=False)


if __name__ == "__main__":
    dev, biases = part_a(PAIRS)
    print("\nPART A done. CSVs in", RESULTS)
    summ = part_b_dev(PAIRS)
    # chosen <=3 variants to confirm on VAL -- picked from the DEV table printed above (best avg_R / PF with real n,
    # not the single top row of a big grid). See report.md for the reasoning.
    CHOSEN = ["v04_h4struct_blockboth", "v08_ema200_blockboth", "v14_h4struct_rr2p0"]
    part_b_val(PAIRS, CHOSEN)
    print("\nBT5 done.")
