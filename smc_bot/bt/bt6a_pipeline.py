"""BT6a -- FULL SMC TOP-DOWN PIPELINE (owner: Fable, independent cross-check vs BT6b).

Implements smc-strategy.md section 5.7 / BT6_BRIEF.md exactly, corrected by what BT1-BT5,BT7 proved:
  1. BIAS (HTF):   H4 structure trend (N=5) and/or price vs EMA200(H1) agree. Blocked if an H4 break happened
                   in the last `fresh_k` H4 bars (BT5: fresh breakouts are the worst context).
  2. POI:          an unmitigated OB or FVG (30M or H1) in the bias direction, in discount (buys) / premium
                   (sells) of the H4 dealing range (BT5: premium/discount alignment helps); valid 48-96 bars.
  3. WAIT:         first M5 touch of the POI only (mitigation = one touch). Record whether price swept a
                   recent M15 swing level into the zone first (flag + depth), tested with/without as a filter.
  4. LTF CONFIRM:  a CHoCH (smc_core.structure evt == +-2) in the bias direction on M5 or M15, within
                   `wait_choch` bars of the touch. "Major" variant requires the broken swing to be the
                   extreme of the previous 40 bars (BT4: structure() calls minor pullback breaks BOS/CHoCH too).
  5. ENTRY:        limit retest of the CHoCH leg's order block (proximal edge -- BT2: proximal beats 50%/distal),
                   valid `entry_wait_bars`; market-at-close is a comparison variant.
  6. STOP:         beyond the CHoCH leg's origin extreme (smc_core leg_i) + ATR buffer, i.e. where the setup is
                   proven wrong (BT1/BT4 lesson: a structural stop, not the raw wick). Skipped if stop < 10x
                   round-trip commission (BT6_BRIEF stop rule).
  7. TARGET:       next HTF (H1 or H4) unbroken swing (liquidity) with RR >= min_rr; compared against fixed
                   2R/3R, breakeven-at-1R + liquidity, a partial-at-1R + runner-to-liquidity split, and a time exit.

Never skips ahead: each step only fires once the previous step's condition is true, and every decision uses only
CLOSED bars (asof_index for HTF values). All fills/exits go through common.simulate(). Judge on GROSS R first.

Run from TTXT root:  .venv\\Scripts\\python.exe smc_bot\\bt\\bt6a_pipeline.py [smoke]
  "smoke" arg -> runs 2 pairs only, for a fast correctness check before the full 10-pair DEV run.
"""
import os
import sys
import time
import numpy as np
import pandas as pd

from common import bars, simulate, pip, COMM, PAIRS, PERIODS, period_of, asof_index, stats, report_table, save
from common import structure, ema, atr, fvgs, build_zones, first_touch

RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "BT6a")
os.makedirs(RESULTS, exist_ok=True)

TF_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
TF_TD = {k: pd.Timedelta(minutes=v) for k, v in TF_MINUTES.items()}
SWING_N = {"M5": 3, "M15": 3, "M30": 4, "H1": 5, "H4": 5}


# ======================================================================================================
# own helper code (does not touch common.py / smc_core.py)
# ======================================================================================================
def in_killzone(ts):
    """DST-aware London + NY killzones (3h from each session open), Tue-Fri only (BT7 recommendation)."""
    idx = pd.DatetimeIndex(pd.to_datetime(ts))
    utc = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    ldn_h = utc.tz_convert("Europe/London").hour
    ny_h = utc.tz_convert("America/New_York").hour
    dow = utc.dayofweek
    kz = ((ldn_h >= 8) & (ldn_h < 11)) | ((ny_h >= 8) & (ny_h < 11))
    out = kz & (dow >= 1) & (dow <= 4)
    return np.asarray(out)


def h4_range_hi_lo(h4, win=30):
    """Rolling prior H4 dealing range (causal: .shift(1) excludes the current bar)."""
    hi = pd.Series(h4.high.values).rolling(win).max().shift(1).values
    lo = pd.Series(h4.low.values).rolling(win).min().shift(1).values
    return hi, lo


def major_flags(st, win=40, highs=None, lows=None):
    """True at a break bar if the broken swing (lsh for a bull break, lsl for a bear) is also the extreme of the
    previous `win` bars -- an 'external'/major structure break, vs. structure()'s minor internal ones (BT4 finding)."""
    roll_hi = pd.Series(highs).rolling(win).max().shift(1).values
    roll_lo = pd.Series(lows).rolling(win).min().shift(1).values
    evt, lsh, lsl = st["evt"], st["lsh"], st["lsl"]
    major = np.zeros(len(evt), bool)
    bull = evt > 0
    bear = evt < 0
    major[bull] = lsh[bull] >= (roll_hi[bull] - 1e-12)
    major[bear] = lsl[bear] <= (roll_lo[bear] + 1e-12)
    return major


# ======================================================================================================
# per-pair cache: everything that does NOT depend on the variant's parameters, computed once
# ======================================================================================================
def prepare_pair(sym):
    t0 = time.time()
    h4 = bars(sym, "H4"); h1 = bars(sym, "H1"); m30 = bars(sym, "M30"); m15 = bars(sym, "M15"); m5 = bars(sym, "M5")
    st4 = structure(h4, SWING_N["H4"]); st1 = structure(h1, SWING_N["H1"])
    st30 = structure(m30, SWING_N["M30"]); st15 = structure(m15, SWING_N["M15"]); st5 = structure(m5, SWING_N["M5"])

    # ---- STEP 1: bias, on the H1 grid ----------------------------------------------------------------
    ema200_h1 = ema(h1.close.values, 200)
    atr14_h1 = atr(h1, 14)
    idx4 = asof_index(h4, h1.tclose.values)
    ok4 = idx4 >= 0
    trend4_h1 = np.zeros(len(h1), np.int8)
    trend4_h1[ok4] = st4["trend"][idx4[ok4]]
    bias_ema = np.sign(h1.close.values - ema200_h1)
    bias_h4 = trend4_h1.astype(float)
    bias_both = np.where(bias_h4 == bias_ema, bias_h4, 0)
    dist_ema_atr = np.abs(h1.close.values - ema200_h1) / atr14_h1

    evt4_idx = np.where(st4["evt"] != 0)[0]
    bars_since_evt4 = np.full(len(h1), 999, np.int64)
    if len(evt4_idx):
        pos = np.searchsorted(evt4_idx, idx4, side="right") - 1
        has = ok4 & (pos >= 0)
        bars_since_evt4[has] = idx4[has] - evt4_idx[pos[has]]

    hi4, lo4 = h4_range_hi_lo(h4)
    hi4_h1 = np.full(len(h1), np.nan); lo4_h1 = np.full(len(h1), np.nan)
    hi4_h1[ok4] = hi4[idx4[ok4]]; lo4_h1[ok4] = lo4[idx4[ok4]]

    major15 = major_flags(st15, 40, m15.high.values, m15.low.values)
    major5 = major_flags(st5, 40, m5.high.values, m5.low.values)
    atr15 = atr(m15, 14); atr5 = atr(m5, 14)

    cache = dict(sym=sym, h4=h4, h1=h1, m30=m30, m15=m15, m5=m5, st4=st4, st1=st1, st30=st30, st15=st15, st5=st5,
                 major15=major15, major5=major5, atr15=atr15, atr5=atr5,
                 h1_bias=dict(t=h1.tclose.values, bias_h4=bias_h4, bias_ema=bias_ema, bias_both=bias_both,
                               bars_since_evt4=bars_since_evt4, dist_ema_atr=dist_ema_atr, atr14_h1=atr14_h1,
                               hi4=hi4_h1, lo4=lo4_h1))

    # ---- REF cross-check config (coordinator spec): H4 structure N=3 bias, own bias table reusing the same
    # EMA200(H1)/dist/H4-range series (those don't depend on the H4 swing strength) ------------------------
    st4_n3 = structure(h4, 3)
    trend4_n3_h1 = np.zeros(len(h1), np.int8)
    trend4_n3_h1[ok4] = st4_n3["trend"][idx4[ok4]]
    bias_h4_n3 = trend4_n3_h1.astype(float)
    bias_both_n3 = np.where(bias_h4_n3 == bias_ema, bias_h4_n3, 0)
    cache["st4_n3"] = st4_n3
    cache["h1_bias_n3"] = dict(t=h1.tclose.values, bias_h4=bias_h4_n3, bias_ema=bias_ema, bias_both=bias_both_n3,
                                dist_ema_atr=dist_ema_atr, atr14_h1=atr14_h1, hi4=hi4_h1, lo4=lo4_h1)
    fvg5_bull, fvg5_bear = fvgs(m5, min_atr=0.1)
    cache["fvg5_bull"], cache["fvg5_bear"] = fvg5_bull, fvg5_bear

    # ---- STEP 2+3: build zones (H1 and M30 POI), annotate with bias, discount/premium; first-touch on M5 -------
    cache["zones_H1"] = build_and_touch(sym, cache, "H1", max_age_bars=48)
    cache["zones_M30"] = build_and_touch(sym, cache, "M30", max_age_bars=72)
    # REF's own 30M POI: max_age 96 bars, bias = H4(N3) x EMA200(H1), no fresh-breakout block (not in the REF spec)
    cache["zones_REF"] = build_and_touch(sym, cache, "M30", max_age_bars=96, bias_key="h1_bias_n3", fresh_k=None)
    print(f"  {sym}: prepared in {time.time()-t0:.1f}s "
          f"(zones H1={len(cache['zones_H1'])} touched, M30={len(cache['zones_M30'])} touched, "
          f"REF={len(cache['zones_REF'])} touched)")
    return cache


def build_and_touch(sym, cache, poi_tf, max_age_bars, fvg_min_atr=0.15, bias_key="h1_bias", fresh_k=3):
    b = cache[poi_tf.lower()] if poi_tf.lower() in cache else bars(sym, poi_tf)
    st = cache[f"st{'1' if poi_tf=='H1' else '30'}"]
    z = build_zones(b, st, max_age_bars, fvg_min_atr)
    if not len(z):
        return z.assign(**{c: [] for c in ["mid", "size_pips", "touch_i", "t_touch", "bias_h4", "bias_ema",
                                            "bias_both", "blocked_fresh", "pos_h4", "dist_ema_atr", "t_bias",
                                            "swept", "sweep_depth_pips", "sweep_extreme"]})
    z["mid"] = (z.lo + z.hi) / 2
    z["size_pips"] = (z.hi - z.lo) / pip(sym)

    # touch on M5 (mitigation = first touch only)
    m5 = cache["m5"]
    touch_i = first_touch(z, m5)
    z["touch_i"] = touch_i
    z = z[z.touch_i >= 0].reset_index(drop=True)
    if not len(z):
        return z
    z["t_touch"] = pd.to_datetime(m5.tclose.values[z.touch_i.values])

    # bias asof zone creation (STEP 1, fixed before the POI is used)
    hb = cache[bias_key]
    idxh1 = asof_index(cache["h1"], z.t_created.values)
    ok = idxh1 >= 0
    for name in ("bias_h4", "bias_ema", "bias_both"):
        col = np.zeros(len(z))
        col[ok] = hb[name][idxh1[ok]]
        z[name] = col
    blocked = np.zeros(len(z), bool)
    if fresh_k is not None:
        blocked[ok] = hb["bars_since_evt4"][idxh1[ok]] < fresh_k    # fresh_k H4 bars fresh-breakout block (BT5 lesson)
    z["blocked_fresh"] = blocked
    pos = np.full(len(z), np.nan); dea = np.full(len(z), np.nan); tb = np.full(len(z), np.datetime64("NaT", "ns"))
    hi4 = np.full(len(z), np.nan); lo4 = np.full(len(z), np.nan)
    hi4[ok] = hb["hi4"][idxh1[ok]]; lo4[ok] = hb["lo4"][idxh1[ok]]
    pos[ok] = (z.mid.values[ok] - lo4[ok]) / (hi4[ok] - lo4[ok])
    dea[ok] = hb["dist_ema_atr"][idxh1[ok]]
    tb[ok] = hb["t"][idxh1[ok]]
    z["pos_h4"] = pos
    z["dist_ema_atr"] = dea
    z["t_bias"] = tb

    # STEP 3 (sweep flag): did price wick beyond the most recent confirmed M15 swing level on the way to the zone?
    m15 = cache["m15"]; st15 = cache["st15"]
    idx15 = asof_index(m15, z.t_touch.values)
    lo5 = m5.low.values; hi5v = m5.high.values
    swept = np.zeros(len(z), bool); depth = np.zeros(len(z)); extreme = np.full(len(z), np.nan)
    lsl15, lsh15 = st15["lsl"], st15["lsh"]
    P = pip(sym)
    for i in range(len(z)):
        j = idx15[i]
        if j < 0:
            continue
        s = max(0, z.touch_i.values[i] - 20)
        e = z.touch_i.values[i] + 1
        if z.dir.values[i] == 1:
            lvl = lsl15[j]
            if lvl == lvl:
                mn = lo5[s:e].min()
                if mn < lvl:
                    swept[i] = True; depth[i] = (lvl - mn) / P; extreme[i] = mn
        else:
            lvl = lsh15[j]
            if lvl == lvl:
                mx = hi5v[s:e].max()
                if mx > lvl:
                    swept[i] = True; depth[i] = (mx - lvl) / P; extreme[i] = mx
    z["swept"] = swept
    z["sweep_depth_pips"] = depth
    z["sweep_extreme"] = extreme
    return z


# ======================================================================================================
# per-variant signal generation (uses the pair cache; cheap filtering + a small LTF scan)
# ======================================================================================================
def find_choch(zones, ltf_bars, st_ltf, major_flag, wait_bars, major_only):
    tclose = ltf_bars.tclose.values
    start_idx = asof_index(ltf_bars, zones.t_touch.values)
    evt = st_ltf["evt"]
    n = len(evt)
    idx_choch = np.full(len(zones), -1, np.int64)
    for i, (s, d) in enumerate(zip(start_idx, zones.dir.values)):
        if s < 0:
            continue
        target = 2 if d == 1 else -2
        lo, hi = s + 1, min(n, s + 1 + wait_bars)
        if lo >= hi:
            continue
        window = evt[lo:hi]
        if major_only:
            hits = np.nonzero((window == target) & major_flag[lo:hi])[0]
        else:
            hits = np.nonzero(window == target)[0]
        if hits.size:
            idx_choch[i] = lo + hits[0]
    return idx_choch, start_idx


def candidates(sym, cache, p):
    """STEPS 1-4 + entry/stop construction. Returns a DataFrame, one row per valid signal, WITHOUT tp/be/max_hold
    (those are the STEP 7 target-management choice, applied by build_orders per target_mode)."""
    zc = cache["zones_H1"] if p["poi_tf"] == "H1" else cache["zones_M30"]
    if not len(zc):
        return pd.DataFrame()
    bias_col = {"both": "bias_both", "h4": "bias_h4", "ema": "bias_ema"}[p["bias_mode"]]
    z = zc[(zc.dir == zc[bias_col]) & (zc.dir != 0) & (~zc.blocked_fresh)].copy()
    if p["require_pd"]:
        fav = ((z.dir == 1) & (z.pos_h4 < 0.5)) | ((z.dir == -1) & (z.pos_h4 > 0.5))
        z = z[fav.fillna(False)]
    if p.get("require_sweep"):
        z = z[z.swept]
    if not len(z):
        return pd.DataFrame()

    ltf_tf = p["ltf_tf"]
    ltf_bars = cache["m5"] if ltf_tf == "M5" else cache["m15"]
    st_ltf = cache["st5"] if ltf_tf == "M5" else cache["st15"]
    major_flag = cache["major5"] if ltf_tf == "M5" else cache["major15"]
    atr_ltf = cache["atr5"] if ltf_tf == "M5" else cache["atr15"]
    idx_choch, start_idx = find_choch(z, ltf_bars, st_ltf, major_flag, p["wait_choch"], p["major_only"])
    z = z.assign(choch_i=idx_choch, ltf_start_i=start_idx)
    z = z[z.choch_i >= 0].reset_index(drop=True)
    if not len(z):
        return pd.DataFrame()

    tclose_ltf = ltf_bars.tclose.values
    evt = st_ltf["evt"]; ob_lo = st_ltf["ob_lo"]; ob_hi = st_ltf["ob_hi"]; leg_i = st_ltf["leg_i"]
    h_arr, l_arr, c_arr = ltf_bars.high.values, ltf_bars.low.values, ltf_bars.close.values
    st1, st4 = cache["st1"], cache["st4"]
    h1b, h4b = cache["h1"], cache["h4"]
    P = pip(sym); comm = COMM[sym]

    rows = []
    for row in z.itertuples():
        k = row.choch_i
        d = int(row.dir)
        lo_, hi_ = ob_lo[k], ob_hi[k]
        if not (hi_ > lo_):
            continue
        t_choch = pd.Timestamp(tclose_ltf[k])
        if p["session_filter"] and not in_killzone([t_choch])[0]:
            continue
        buf = p["sl_buf_atr"] * (atr_ltf[k] if atr_ltf[k] == atr_ltf[k] else 0.0)
        j = leg_i[k]
        if j < 0:
            continue
        leg_extreme = l_arr[j] if d == 1 else h_arr[j]
        if p["stop_mode"] == "sweep_or_leg" and row.swept and row.sweep_extreme == row.sweep_extreme:
            leg_extreme = min(leg_extreme, row.sweep_extreme) if d == 1 else max(leg_extreme, row.sweep_extreme)
        sl = leg_extreme - buf if d == 1 else leg_extreme + buf

        if p["entry_mode"] == "limit":
            entry_type = "limit"
            entry = hi_ if d == 1 else lo_
            expire = t_choch + TF_TD[ltf_tf] * p["entry_wait_bars"]
            nominal_entry = entry
        else:
            entry_type = "market"
            entry = np.nan
            expire = t_choch
            nominal_entry = c_arr[k]

        risk_pips = (nominal_entry - sl) * d / P
        if not (risk_pips > 0) or risk_pips < 10 * comm:
            continue

        ih1 = asof_index(h1b, np.array([t_choch], dtype="datetime64[ns]"))[0]
        ih4 = asof_index(h4b, np.array([t_choch], dtype="datetime64[ns]"))[0]
        tp_h1 = (st1["lsh"][ih1] if d == 1 else st1["lsl"][ih1]) if ih1 >= 0 else np.nan
        tp_h4 = (st4["lsh"][ih4] if d == 1 else st4["lsl"][ih4]) if ih4 >= 0 else np.nan
        tp_liq = tp_h4 if p["htf_target_tf"] == "H4" else tp_h1
        rr_liq = (tp_liq - nominal_entry) * d / (risk_pips * P) if np.isfinite(tp_liq) else np.nan

        rows.append(dict(
            sym=sym, t_signal=t_choch, side=d, entry_type=entry_type, entry=entry, expire=expire, sl=sl,
            nominal_entry=nominal_entry, risk_pips=round(risk_pips, 2), tp_liq=tp_liq, rr_liq=round(rr_liq, 3) if rr_liq == rr_liq else np.nan,
            sl_reason=f"beyond {'sweep' if (p['stop_mode']=='sweep_or_leg' and row.swept) else 'CHoCH leg'} extreme "
                      f"+{p['sl_buf_atr']}xATR({ltf_tf})",
            # ---- timestamps of every step, for later ML / human replay ----
            t_bias=row.t_bias, t_poi_created=row.t_created, t_poi_touch=row.t_touch, t_choch=t_choch,
            # ---- context features ----
            bias_mode=p["bias_mode"], poi_tf=p["poi_tf"], poi_kind=row.kind, poi_size_pips=round(row.size_pips, 2),
            poi_age_bars_at_touch=round((row.t_touch - row.t_created) / TF_TD[p["poi_tf"]], 1),
            discount_pct=round(row.pos_h4, 3) if row.pos_h4 == row.pos_h4 else np.nan,
            dist_ema_atr=round(row.dist_ema_atr, 3) if row.dist_ema_atr == row.dist_ema_atr else np.nan,
            swept=bool(row.swept), sweep_depth_pips=round(row.sweep_depth_pips, 2),
            bars_to_choch=int(k - row.ltf_start_i), ltf_tf=ltf_tf, event_major=bool(major_flag[k]),
            session_kz=bool(in_killzone([t_choch])[0]), atr_ltf_pips=round(atr_ltf[k] / P, 2) if atr_ltf[k] == atr_ltf[k] else np.nan,
            entry_mode=p["entry_mode"], stop_mode=p["stop_mode"],
        ))
    return pd.DataFrame(rows)


def build_orders(sym, cache, p):
    """Applies STEP 7 (target/management) to the candidates and returns order rows ready for simulate()."""
    cand = candidates(sym, cache, p)
    if not len(cand):
        return cand
    d = cand.side.values
    mode = p["target_mode"]
    out = cand.copy()
    out["be_at_r"] = np.nan
    out["max_hold_min"] = np.nan
    out["leg"] = "single"
    out["trade_uid"] = np.arange(len(out)).astype(str) + "_" + sym

    if mode == "liquidity":
        rr = out.rr_liq.values
        keep = np.isfinite(rr) & (rr >= p["min_rr"])
        out = out[keep].copy()
        out["tp"] = out.tp_liq
        out["tp_reason"] = "next HTF liquidity (H1/H4 unbroken swing), RR>=" + str(p["min_rr"])
        if "be_at_r" in p:
            out["be_at_r"] = p["be_at_r"]
        if "max_hold_min" in p:
            out["max_hold_min"] = p["max_hold_min"]
        return out

    if mode == "fixed":
        r = p["target_r"]
        out["tp"] = out.nominal_entry + d * r * out.risk_pips.values * out.sym.map(pip).values
        out["tp_reason"] = f"fixed {r}R"
        return out

    if mode == "partial_runner":
        rr = out.rr_liq.values
        keep = np.isfinite(rr) & (rr >= p["min_rr"])
        base = out[keep].copy()
        if not len(base):
            return base
        leg1 = base.copy()
        leg1["tp"] = leg1.nominal_entry + d[keep] * 1.0 * leg1.risk_pips.values * leg1.sym.map(pip).values
        leg1["tp_reason"] = "partial: 1R (half size)"
        leg1["leg"] = "P1_partial_1R"
        leg2 = base.copy()
        leg2["tp"] = leg2.tp_liq
        leg2["tp_reason"] = "runner: next HTF liquidity (half size), SL to BE at 1R"
        leg2["leg"] = "P2_runner_liquidity"
        leg2["be_at_r"] = 1.0
        return pd.concat([leg1, leg2], ignore_index=True)

    raise ValueError(mode)


def build_ref_orders(sym, cache):
    """REF cross-check configuration (coordinator spec, exact match to BT6b's implementation, for a like-for-like
    comparison -- kept separate from the v01-v19 exploratory grid so neither pipeline's own code has to bend):
      bias = H4 structure N=3 AND EMA200(H1) agree (no fresh-breakout block -- not part of this spec);
      POI  = unmitigated 30M OB/FVG in bias direction, discount/premium of the H4 range, valid 96 bars;
      wait = first M5 touch;
      LTF  = 5M CHoCH (N=3) in bias direction within 36 bars of the touch;
      entry= limit at the proximal edge of the 5M FVG (else OB) of the CHoCH leg, valid 12 bars;
      stop = beyond the extreme reached since the touch (inclusive of any sweep), + 0.1xATR(5M);
      target = next unbroken H1 swing (N=5), RR>=2;
      session = London+NY killzones, Tue-Fri; skip if stop < 10x commission; one position per pair.
    """
    z = cache["zones_REF"]
    if not len(z):
        return pd.DataFrame()
    fav = ((z.dir == 1) & (z.pos_h4 < 0.5)) | ((z.dir == -1) & (z.pos_h4 > 0.5))
    z = z[fav.fillna(False)].copy()
    if not len(z):
        return pd.DataFrame()

    m5, st5, atr5 = cache["m5"], cache["st5"], cache["atr5"]
    bull5, bear5 = cache["fvg5_bull"], cache["fvg5_bear"]
    idx_choch, start_idx = find_choch(z, m5, st5, cache["major5"], wait_bars=36, major_only=False)
    z = z.assign(choch_i=idx_choch, ltf_start_i=start_idx)
    z = z[z.choch_i >= 0].reset_index(drop=True)
    if not len(z):
        return pd.DataFrame()

    tclose5 = m5.tclose.values
    evt, ob_lo, ob_hi, leg_i = st5["evt"], st5["ob_lo"], st5["ob_hi"], st5["leg_i"]
    h_arr, l_arr = m5.high.values, m5.low.values
    st1, h1b = cache["st1"], cache["h1"]
    P = pip(sym); comm = COMM[sym]

    rows = []
    for row in z.itertuples():
        k = row.choch_i
        d = int(row.dir)
        j = leg_i[k]
        if j < 0:
            continue
        t_choch = pd.Timestamp(tclose5[k])
        if not in_killzone([t_choch])[0]:
            continue
        # entry zone: the 5M FVG inside the CHoCH leg (j..k) if any, else the CHoCH leg's OB
        fvg_arr = bull5 if d == 1 else bear5
        seg = fvg_arr[max(j, 0):k + 1]
        idxs = np.nonzero(seg)[0]
        if idxs.size:
            fk = max(j, 0) + idxs[-1]
            lo_, hi_ = (h_arr[fk - 2], l_arr[fk]) if d == 1 else (h_arr[fk], l_arr[fk - 2])
            poi_kind = "FVG"
        else:
            lo_, hi_ = ob_lo[k], ob_hi[k]
            poi_kind = "OB"
        if not (hi_ > lo_):
            continue
        entry = hi_ if d == 1 else lo_
        expire = t_choch + TF_TD["M5"] * 12
        buf = 0.1 * (atr5[k] if atr5[k] == atr5[k] else 0.0)
        ti = int(row.touch_i)
        if d == 1:
            sl = l_arr[ti:k + 1].min() - buf
        else:
            sl = h_arr[ti:k + 1].max() + buf
        risk_pips = (entry - sl) * d / P
        if not (risk_pips > 0) or risk_pips < 10 * comm:
            continue
        ih1 = asof_index(h1b, np.array([t_choch], dtype="datetime64[ns]"))[0]
        tp = (st1["lsh"][ih1] if d == 1 else st1["lsl"][ih1]) if ih1 >= 0 else np.nan
        if not np.isfinite(tp):
            continue
        rr = (tp - entry) * d / (risk_pips * P)
        if rr < 2.0:
            continue
        rows.append(dict(
            sym=sym, t_signal=t_choch, side=d, entry_type="limit", entry=entry, expire=expire, sl=sl, tp=tp,
            be_at_r=np.nan, max_hold_min=np.nan,
            sl_reason="beyond the extreme since the POI touch +0.1xATR(M5)",
            tp_reason="next H1 (N=5) unbroken swing liquidity, RR>=2",
            t_bias=row.t_bias, t_poi_created=row.t_created, t_poi_touch=row.t_touch, t_choch=t_choch,
            poi_kind=poi_kind, discount_pct=round(row.pos_h4, 3) if row.pos_h4 == row.pos_h4 else np.nan,
            swept=bool(row.swept), sweep_depth_pips=round(row.sweep_depth_pips, 2),
            bars_to_choch=int(k - row.ltf_start_i), rr_planned=round(rr, 3),
        ))
    return pd.DataFrame(rows)


def run_ref(pairs, caches):
    """Runs the REF cross-check config on all pairs except EURGBP (coordinator instruction / BT7 lesson)."""
    ref_pairs = [p for p in pairs if p != "EURGBP"]
    all_orders = []
    for sym in ref_pairs:
        o = build_ref_orders(sym, caches[sym])
        if len(o):
            all_orders.append(o)
    if not all_orders:
        print("REF: 0 trades"); return None
    orders = pd.concat(all_orders, ignore_index=True)
    trades_by_sym = []
    for sym, grp in orders.groupby("sym"):
        trades_by_sym.append(simulate(sym, grp.drop(columns=["sym"])))
    trades = pd.concat(trades_by_sym, ignore_index=True)
    save("BT6a", "REF_crosscheck", trades)
    g_dev = gross_net_summary(trades, "DEV")
    print(f"REF_crosscheck DEV: {g_dev}")
    pp_dev = per_pair_table(trades, "DEV")
    pp_dev.to_csv(os.path.join(RESULTS, "REF_perpair_DEV.csv"), index=False)
    print(pp_dev.to_string(index=False))
    return trades


# ======================================================================================================
# variants (<=20 on DEV, chosen from trading logic per step of the pipeline -- one axis at a time from v01 base)
# ======================================================================================================
BASE = dict(bias_mode="both", poi_tf="H1", require_pd=True, require_sweep=False, ltf_tf="M5", wait_choch=24,
            major_only=False, entry_mode="limit", entry_wait_bars=8, stop_mode="leg", sl_buf_atr=0.15,
            target_mode="liquidity", min_rr=2.0, htf_target_tf="H1", session_filter=True)


def V(name, **overrides):
    p = dict(BASE); p.update(overrides); p["name"] = name
    return p


VARIANTS = [
    V("v01_base"),
    V("v02_bias_h4only", bias_mode="h4"),
    V("v03_bias_emaonly", bias_mode="ema"),
    V("v04_poi_m30", poi_tf="M30"),
    V("v05_no_pd_filter", require_pd=False),
    V("v06_require_sweep", require_sweep=True),
    V("v07_ltf_m15", ltf_tf="M15", wait_choch=8),
    V("v08_major_choch", major_only=True),
    V("v09_market_entry", entry_mode="market"),
    V("v10_stop_sweep_or_leg", stop_mode="sweep_or_leg"),
    V("v11_target_2R", target_mode="fixed", target_r=2.0),
    V("v12_target_3R", target_mode="fixed", target_r=3.0),
    V("v13_be1r_liquidity", be_at_r=1.0),
    V("v14_time_exit_24h", max_hold_min=1440.0),
    V("v15_no_session_filter", session_filter=False),
    V("v16_rr1p5", min_rr=1.5),
    V("v17_wait36", wait_choch=36),
    V("v18_target_h4", htf_target_tf="H4"),
    V("v19_partial_runner", target_mode="partial_runner"),
]


def run_variant(name, pairs, caches, **p):
    all_orders = []
    for sym in pairs:
        o = build_orders(sym, caches[sym], p)
        if len(o):
            all_orders.append(o)
    if not all_orders:
        return None, None
    orders = pd.concat(all_orders, ignore_index=True)
    keep_cols = ["t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min"]
    extra_cols = [c for c in orders.columns if c not in keep_cols + ["sym"]]
    trades_by_sym = []
    for sym, grp in orders.groupby("sym"):
        cols = keep_cols + extra_cols
        trades_by_sym.append(simulate(sym, grp[cols]))
    trades = pd.concat(trades_by_sym, ignore_index=True)
    tab = save("BT6a", name, trades)
    return trades, tab


def gross_net_summary(trades, period):
    t = trades[(trades.period == period) & (trades.status == "filled")].copy()
    if not len(t):
        return dict(trades=0, per_week=np.nan, win_pct=np.nan, gross_avg_R=np.nan, gross_total_R=np.nan,
                    gross_PF=np.nan, net_avg_R=np.nan, net_total_R=np.nan, PF=np.nan, maxDD_R=np.nan, avg_hold_h=np.nan)
    gross_R = t.gross_pips.values / t.risk_pips.values
    s = stats(t)
    gp = gross_R[gross_R > 0].sum(); gl = -gross_R[gross_R < 0].sum()
    return dict(trades=s["trades"], per_week=s["per_week"], win_pct=s["win_pct"],
                gross_avg_R=round(gross_R.mean(), 3), gross_total_R=round(gross_R.sum(), 1),
                gross_PF=round(gp / gl, 2) if gl > 0 else np.inf,
                net_avg_R=s["avg_R"], net_total_R=s["total_R"], PF=s["PF"], maxDD_R=s["maxDD_R"],
                avg_hold_h=s["avg_hold_h"])


def per_pair_table(trades, period):
    t = trades[(trades.period == period) & (trades.status == "filled")]
    rows = []
    for sym, g in t.groupby("sym"):
        gr = g.gross_pips.values / g.risk_pips.values
        s = stats(g)
        gp = gr[gr > 0].sum(); gl = -gr[gr < 0].sum()
        rows.append(dict(sym=sym, **s, gross_avg_R=round(gr.mean(), 3), gross_total_R=round(gr.sum(), 1),
                          gross_PF=round(gp / gl, 2) if gl > 0 else np.inf))
    return pd.DataFrame(rows)


def confirm_on_val(chosen, pairs, caches):
    """Runs the chosen (<=3) variants and reports DEV+VAL, pooled and per-pair. Called manually after inspecting
    the DEV variant table (never choose from VAL)."""
    rows = []
    for name in chosen:
        v = next(x for x in VARIANTS if x["name"] == name)
        p = {k: x for k, x in v.items() if k != "name"}
        trades, _ = run_variant(name, pairs, caches, **p)
        if trades is None:
            rows.append(dict(variant=name, period="VAL", trades=0)); continue
        for period in ("DEV", "VAL"):
            g = gross_net_summary(trades, period)
            rows.append(dict(variant=name, period=period, **g))
            per_pair_table(trades, period).to_csv(os.path.join(RESULTS, f"perpair_{name}_{period}.csv"), index=False)
        trades.head(0)  # no-op; trades_<name>.csv already saved by run_variant()->save()
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(RESULTS, "chosen_DEV_VAL_summary.csv"), index=False)
    print("\n=== CHOSEN variants: DEV vs VAL (gross R first, then net) ===")
    print(out.to_string(index=False))
    return out


def main(pairs):
    print(f"BT6a: preparing {len(pairs)} pairs (bars, structure, zones, touches, sweeps) ...")
    caches = {}
    for sym in pairs:
        caches[sym] = prepare_pair(sym)

    print("\nBT6a: REF cross-check configuration (coordinator spec, 9 pairs ex-EURGBP) ...")
    ref_trades = run_ref(pairs, caches)

    print("\nBT6a: running", len(VARIANTS), "variants on DEV ...")
    dev_summary = []
    all_trades = {}
    for v in VARIANTS:
        p = {k: x for k, x in v.items() if k != "name"}
        t0 = time.time()
        trades, tab = run_variant(v["name"], pairs, caches, **p)
        all_trades[v["name"]] = trades
        if trades is None:
            dev_summary.append(dict(variant=v["name"], trades=0)); print(f"  {v['name']}: 0 trades"); continue
        g = gross_net_summary(trades, "DEV")
        dev_summary.append(dict(variant=v["name"], **g))
        print(f"  {v['name']:24s} DEV: trades={g.get('trades')} gross_avg_R={g.get('gross_avg_R')} "
              f"net_avg_R={g.get('net_avg_R')} PF={g.get('PF')}  ({time.time()-t0:.1f}s)")
    dev_df = pd.DataFrame(dev_summary)
    dev_df.to_csv(os.path.join(RESULTS, "DEV_variant_summary.csv"), index=False)
    print("\n=== DEV summary (gross R first, then net) ===")
    print(dev_df.to_string(index=False))
    return caches, all_trades, dev_df, ref_trades


# Chosen for VAL (picked from DEV trading logic, not from a leaderboard search -- see report.md):
#   v01_base:         the pipeline exactly as specified (bias=both, H1 POI, H1 liquidity target) -- the flagship.
#   v03_bias_emaonly:  BT5 found EMA200(H1) was one of only two bias defs with a real (if tiny) >random edge;
#                      this isolates it (drops the H4-structure AND-requirement) and DEV shows the strongest,
#                      most-traded positive cell (53 trades, gross +0.427R, net +0.360R, PF 1.47).
#   v18_target_h4:     "next HTF liquidity" explicitly allows H1 or H4 (smc-strategy 5.5/5.7); H4 targets are
#                      further away (bigger RR) and DEV shows a real gross edge (34 trades, gross +0.384R, PF 1.40).
CHOSEN = ["v01_base", "v03_bias_emaonly", "v18_target_h4"]

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        main(PAIRS[:2])
    elif len(sys.argv) > 1 and sys.argv[1] == "val":
        caches = {sym: prepare_pair(sym) for sym in PAIRS}
        confirm_on_val(CHOSEN, PAIRS, caches)
    else:
        main(PAIRS)
