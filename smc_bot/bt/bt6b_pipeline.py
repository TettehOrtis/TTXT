"""BT6b: independent implementation of the full SMC top-down pipeline (cross-check of BT6a).

Run from the TTXT root:
    .venv/Scripts/python.exe smc_bot/bt/bt6b_pipeline.py dev              # all DEV variants (incl. REF)
    .venv/Scripts/python.exe smc_bot/bt/bt6b_pipeline.py dev REF V03       # selected DEV variants
    .venv/Scripts/python.exe smc_bot/bt/bt6b_pipeline.py val V03 V12       # <=3 chosen variants on VAL
    .venv/Scripts/python.exe smc_bot/bt/bt6b_pipeline.py check             # detection cross-checks vs smc_core + M1 replays

Pipeline (smc-strategy.md 5.7, BT6_BRIEF.md), every step must be true, nothing skips ahead:
  1 bias   H4 structure trend (N=3) == sign(close_H1 - EMA200_H1), both from CLOSED HTF bars (asof_index)
  2 POI    unmitigated 30M OB or FVG in the bias direction whose middle is in the discount (buy) / premium (sell) half of
           the H4 dealing range (last confirmed H4 swing high/low, N=3, extended by price since); valid 96 30M bars
  3 touch  first M5 bar that trades into the POI (first touch only; the POI is dead afterwards)
  4 CHoCH  M5 CHoCH (N=3) in the bias direction within 36 M5 bars of the touch (bias re-checked at touch and at CHoCH)
  5 entry  limit at the proximal edge of the latest unmitigated M5 FVG of the CHoCH leg (else the leg's M5 OB), 12 bars
  6 stop   beyond the lowest low / highest high since the touch + 0.1 ATR(M5, true range); skip if stop < 10x commission
  7 target next unbroken H1 swing (N=5, 150-bar lookback) beyond entry; skip if RR < 2
  sessions: CHoCH close inside the London (08-11 London time) or NY (08-11 New York time) killzone, Tue-Fri; skip EURGBP;
  one position per pair (simulate one_at_a_time). Fills/exits/costs: common.simulate only.

Own detection code (not smc_core): swings, internal AND external (major) structure, OBs (with pre-confirmation mitigation
check and leg-low-inclusive zone), FVGs (size vs ATR known before the gap), sweeps, dealing range, liquidity targets.
Detection bars are cut at the end of the evaluated period, so no later bar (and never TEST) is ever read.
"""
import os, sys, time, pickle, tempfile, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import bars, asof_index, simulate, stats, save, PERIODS, PAIRS, pip, COMM, RESULTS, STOP_SLIP, DATASET  # noqa: E402

OUT = os.path.join(RESULTS, "BT6b")
os.makedirs(OUT, exist_ok=True)
CACHE = os.environ.get("BT6B_CACHE", os.path.join(tempfile.gettempdir(), "bt6b_cache"))
os.makedirs(CACHE, exist_ok=True)
TRADE_PAIRS = [p for p in PAIRS if p != "EURGBP"]
SIG_START = {"DEV": "2021-02-01", "VAL": "2024-01-01"}      # DEV: 1 month warm-up for EMA200(H1) / H4 structure
TFM = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
from collections import Counter
FUNNEL = Counter()          # stage counts accumulated over build_orders calls (diagnostics)


# =============================================================================================== own detection
def atr_tr(h, l, c, n=14):
    """ATR as a rolling mean of TRUE range (smc_core.atr uses high-low only)."""
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n).mean().values


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def swings_own(h, l, N):
    """Fractal swing: high[i] > max(high[i-N..i-1]) and >= max(high[i+1..i+N]). Known only at the close of bar i+N."""
    from numpy.lib.stride_tricks import sliding_window_view as sw
    n = len(h)
    sh = np.zeros(n, bool); sl = np.zeros(n, bool)
    if n < 2 * N + 1:
        return sh, sl
    wh, wl = sw(h, 2 * N + 1), sw(l, 2 * N + 1)
    ch, cl = wh[:, N], wl[:, N]
    sh[N:n - N] = (ch > wh[:, :N].max(1)) & (ch >= wh[:, N + 1:].max(1))
    sl[N:n - N] = (cl < wl[:, :N].min(1)) & (cl <= wl[:, N + 1:].min(1))
    return sh, sl


def structure_internal(h, l, c, N, sh, sl):
    """Internal structure: a CLOSE beyond the last confirmed swing high/low. evt +-1 BOS, +-2 CHoCH (break against the
    prior trend). brk[k] = bar index of the broken swing. Same definition as smc_core.structure (verified in `check`)."""
    n = len(c)
    trend = np.zeros(n, np.int8); evt = np.zeros(n, np.int8); brk = np.full(n, -1, np.int64)
    tr = 0; lsh = lsl = np.nan; lsh_i = lsl_i = -1; shl = sll = False
    for k in range(n):
        s = k - N
        if s >= 0:
            if sh[s]:
                lsh, lsh_i, shl = h[s], s, True
            if sl[s]:
                lsl, lsl_i, sll = l[s], s, True
        if shl and c[k] > lsh:
            evt[k] = 2 if tr == -1 else 1; brk[k] = lsh_i; tr = 1; shl = False
        elif sll and c[k] < lsl:
            evt[k] = -2 if tr == 1 else -1; brk[k] = lsl_i; tr = -1; sll = False
        trend[k] = tr
    return trend, evt, brk


def structure_external(h, l, c, N, sh, sl):
    """Major / external structure (BT4 lesson: 59% of internal 'BOS' break a minor lower high).
    Bull state keeps a PROTECTED LOW P = the low that launched the last bullish break (lowest low between the broken high
    and the break bar). BOS up  = close above the ARMED high = the latest confirmed swing high that is the highest high
    since P (a real higher high). CHoCH down = close below P; the new protected high = highest high between P and the
    CHoCH bar (the swing that produced the latest extreme). Bear state is the mirror. Minor lower highs / higher lows
    inside the leg are ignored. brk[k] = bar of the broken level."""
    n = len(c)
    trend = np.zeros(n, np.int8); evt = np.zeros(n, np.int8); brk = np.full(n, -1, np.int64); prot = np.full(n, np.nan)
    tr = 0; lsh = lsl = np.nan; lsh_i = lsl_i = -1; shl = sll = False
    P = np.nan; Pi = -1; arm = np.nan; arm_i = -1
    for k in range(n):
        s = k - N
        if tr == 0:
            if s >= 0 and sh[s]:
                lsh, lsh_i, shl = h[s], s, True
            if s >= 0 and sl[s]:
                lsl, lsl_i, sll = l[s], s, True
            if shl and c[k] > lsh:
                tr = 1; evt[k] = 1; brk[k] = lsh_i
                Pi = lsh_i + int(np.argmin(l[lsh_i:k + 1])); P = l[Pi]; arm = np.nan
            elif sll and c[k] < lsl:
                tr = -1; evt[k] = -1; brk[k] = lsl_i
                Pi = lsl_i + int(np.argmax(h[lsl_i:k + 1])); P = h[Pi]; arm = np.nan
        else:
            if s > Pi:
                if tr == 1 and sh[s] and h[s] >= h[Pi:s + 1].max():
                    arm, arm_i = h[s], s
                elif tr == -1 and sl[s] and l[s] <= l[Pi:s + 1].min():
                    arm, arm_i = l[s], s
            if tr == 1:
                if c[k] < P:
                    evt[k] = -2; brk[k] = Pi
                    Pi = Pi + int(np.argmax(h[Pi:k + 1])); P = h[Pi]; tr = -1; arm = np.nan
                elif arm == arm and c[k] > arm:
                    evt[k] = 1; brk[k] = arm_i
                    Pi = arm_i + int(np.argmin(l[arm_i:k + 1])); P = l[Pi]; arm = np.nan
            else:
                if c[k] > P:
                    evt[k] = 2; brk[k] = Pi
                    Pi = Pi + int(np.argmin(l[Pi:k + 1])); P = l[Pi]; tr = 1; arm = np.nan
                elif arm == arm and c[k] < arm:
                    evt[k] = -1; brk[k] = arm_i
                    Pi = arm_i + int(np.argmax(h[arm_i:k + 1])); P = h[Pi]; arm = np.nan
        trend[k] = tr; prot[k] = P
    return trend, evt, brk, prot


def ob_zones(o, h, l, c, evt, brk):
    """Order block of every structure break. Bull: leg low j0 = lowest low between the broken swing and the break bar;
    OB candle = last bearish candle at or before j0 (max 6 bars back, not before the broken swing); zone = [low(j0),
    high(OB candle)] (includes the wick that made the leg low). No bearish candle -> the j0 candle itself (flag fb).
    Dropped if price left the zone and came back into it before the break closed (already mitigated, BT2 finding)."""
    rows = []
    for k in np.where(evt != 0)[0]:
        d = 1 if evt[k] > 0 else -1
        a = brk[k] + 1
        if a > k:
            continue
        if d == 1:
            j0 = a + int(np.argmin(l[a:k + 1]))
            j = next((jj for jj in range(j0, max(j0 - 6, a - 1), -1) if c[jj] < o[jj]), -1)
            fb = j < 0; j = j0 if fb else j
            lo, hi = l[j0], h[j]
            after = np.arange(j0 + 1, k + 1)
            left = after[l[after] > hi]
            if len(left) and (l[left[0] + 1:k + 1] <= hi).any():
                continue
        else:
            j0 = a + int(np.argmax(h[a:k + 1]))
            j = next((jj for jj in range(j0, max(j0 - 6, a - 1), -1) if c[jj] > o[jj]), -1)
            fb = j < 0; j = j0 if fb else j
            lo, hi = l[j], h[j0]
            after = np.arange(j0 + 1, k + 1)
            left = after[h[after] < lo]
            if len(left) and (h[left[0] + 1:k + 1] >= lo).any():
                continue
        rows.append((d, lo, hi, "OB_fb" if fb else "OB", k))
    return rows


def fvg_zones(h, l, at, min_atr=0.1):
    """Bull FVG at k: low[k] > high[k-2], zone (high[k-2], low[k]); size >= min_atr * ATR of bar k-3 (known before the gap)."""
    n = len(h)
    rows = []
    a3 = np.full(n, np.nan); a3[3:] = at[:-3]
    thr = min_atr * np.nan_to_num(a3, nan=np.inf)
    k = np.arange(2, n)
    bull = k[(l[2:] - h[:-2]) >= thr[2:]]
    bear = k[(l[:-2] - h[2:]) >= thr[2:]]
    rows += [(1, h[i - 2], l[i], "FVG", i) for i in bull]
    rows += [(-1, h[i], l[i - 2], "FVG", i) for i in bear]
    return rows


def tf_arrays(b, N):
    o, h, l, c = (b[x].values.astype(float) for x in ("open", "high", "low", "close"))
    sh, sl = swings_own(h, l, N)
    tri, evi, bri = structure_internal(h, l, c, N, sh, sl)
    tre, eve, bre, prot = structure_external(h, l, c, N, sh, sl)
    return dict(t=b.time.values, tc=b.tclose.values, o=o, h=h, l=l, c=c, sh=sh, sl=sl, atr=atr_tr(h, l, c),
                tr_i=tri, ev_i=evi, br_i=bri, tr_e=tre, ev_e=eve, br_e=bre, prot=prot, N=N)


def dealing_range(h, l, sh, sl, N):
    """Per HTF bar k: last confirmed swing high/low (N) extended by any price beyond them since -> [lo, hi]."""
    n = len(h)
    hi = np.full(n, np.nan); lo = np.full(n, np.nan)
    H = L = np.nan
    for k in range(n):
        s = k - N
        if s >= 0 and sh[s]:
            H = h[s:k + 1].max()
        if s >= 0 and sl[s]:
            L = l[s:k + 1].min()
        if H == H:
            H = max(H, h[k])
        if L == L:
            L = min(L, l[k])
        hi[k], lo[k] = H, L
    return lo, hi


def last_change(x):
    """index of the last bar at or before k where x changed (or had a nonzero event)."""
    idx = np.where(x)[0]
    out = np.full(len(x), -1, np.int64)
    if len(idx):
        pos = np.searchsorted(idx, np.arange(len(x)), "right") - 1
        ok = pos >= 0
        out[ok] = idx[pos[ok]]
    return out


# =============================================================================================== context per pair/period
def prepare(sym, period):
    tag = "" if DATASET == "zero" else f"_{DATASET}"         # BT13 finding: key the cache by dataset (ERRORS #29)
    f = os.path.join(CACHE, f"{sym}_{period}{tag}_v3.pkl")
    if os.path.exists(f):
        with open(f, "rb") as fh:
            return pickle.load(fh)
    end = np.datetime64(pd.Timestamp(PERIODS[period][1]))
    B = {}
    for tf in ("M5", "M15", "M30", "H1", "H4"):
        b = bars(sym, tf)
        B[tf] = b[b.tclose.values <= end].reset_index(drop=True)          # detection never reads past the period end
    ctx = dict(sym=sym, period=period)
    ctx["M5"] = tf_arrays(B["M5"], 3)
    ctx["M15"] = tf_arrays(B["M15"], 3)
    ctx["M30"] = tf_arrays(B["M30"], 3)
    ctx["H1"] = tf_arrays(B["H1"], 5)
    ctx["H4"] = tf_arrays(B["H4"], 3)
    H1, H4 = ctx["H1"], ctx["H4"]
    H1["ema200"] = ema(H1["c"], 200)
    H4["rlo"], H4["rhi"] = dealing_range(H4["h"], H4["l"], H4["sh"], H4["sl"], 3)
    H4["last_evt"] = last_change(H4["ev_i"] != 0)
    chg = np.r_[True, H4["tr_i"][1:] != H4["tr_i"][:-1]]
    H4["last_chg"] = last_change(chg)
    chg = np.r_[True, H4["tr_e"][1:] != H4["tr_e"][:-1]]
    H4["last_chg_e"] = last_change(chg)
    for tf in ("M30", "H1"):
        A = ctx[tf]
        A["zones"] = ob_zones(A["o"], A["h"], A["l"], A["c"], A["ev_i"], A["br_i"]) + fvg_zones(A["h"], A["l"], A["atr"])
    with open(f, "wb") as fh:
        pickle.dump(ctx, fh)
    return ctx


def bias_at(ctx, t, mode):
    """Bias from CLOSED HTF bars before time(s) t. mode: both (H4 internal & EMA agree), h4, ema, h4ext (H4 external & EMA)."""
    H1, H4 = ctx["H1"], ctx["H4"]
    t = np.atleast_1d(np.asarray(t, dtype="datetime64[ns]"))
    i4 = np.searchsorted(H4["tc"], t, "right") - 1
    i1 = np.searchsorted(H1["tc"], t, "right") - 1
    ok = (i4 >= 0) & (i1 >= 200)
    i4c, i1c = np.maximum(i4, 0), np.maximum(i1, 0)
    e = np.sign(H1["c"][i1c] - H1["ema200"][i1c]).astype(int)
    s4 = H4["tr_i"][i4c].astype(int)
    s4e = H4["tr_e"][i4c].astype(int)
    if mode == "both":
        b = np.where(s4 == e, s4, 0)
    elif mode == "h4":
        b = s4
    elif mode == "ema":
        b = e
    elif mode == "h4ext":
        b = np.where(s4e == e, s4e, 0)
    else:
        raise ValueError(mode)
    return np.where(ok, b, 0)


def killzone(ts):
    """'LDN' if London local time 08:00-10:59, 'NY' if New York local 08:00-10:59 (DST-aware), else ''."""
    tu = pd.DatetimeIndex(ts).tz_localize("UTC")
    lh = tu.tz_convert("Europe/London").hour
    nh = tu.tz_convert("America/New_York").hour
    out = np.where((lh >= 8) & (lh < 11), "LDN", np.where((nh >= 8) & (nh < 11), "NY", ""))
    return out


def ltf_entry_zone(L, j, d, brk_i):
    """Entry zone of the CHoCH leg on the LTF. Leg = from the broken swing to the CHoCH bar j; origin j0 = its lowest low
    (bull). Prefer the latest FVG inside the leg (candle 1 at/after j0) that is still untouched at j's close; proximal
    edge = its top (bull). Else the leg OB (last bearish candle at/before j0, max 6 back); proximal = its high."""
    h, l, o, c, at = L["h"], L["l"], L["o"], L["c"], L["atr"]
    a = brk_i + 1
    if a > j:
        return None
    if d == 1:
        j0 = a + int(np.argmin(l[a:j + 1]))
        for m in range(j, j0 + 1, -1):
            if m - 2 < j0 or m < 3:
                break
            top, bot = l[m], h[m - 2]
            if top - bot >= 0.1 * np.nan_to_num(at[m - 3], nan=np.inf) and (m == j or l[m + 1:j + 1].min() > top):
                return dict(entry=top, ez_lo=bot, ez_hi=top, ez_kind="FVG", ez_i=m, leg_i=j0)
        ob = next((jj for jj in range(j0, max(j0 - 6, a - 1), -1) if c[jj] < o[jj]), j0)
        return dict(entry=h[ob], ez_lo=l[j0], ez_hi=h[ob], ez_kind="OB" if c[ob] < o[ob] else "OB_fb", ez_i=ob, leg_i=j0)
    j0 = a + int(np.argmax(h[a:j + 1]))
    for m in range(j, j0 + 1, -1):
        if m - 2 < j0 or m < 3:
            break
        bot, top = h[m], l[m - 2]
        if top - bot >= 0.1 * np.nan_to_num(at[m - 3], nan=np.inf) and (m == j or h[m + 1:j + 1].max() < bot):
            return dict(entry=bot, ez_lo=bot, ez_hi=top, ez_kind="FVG", ez_i=m, leg_i=j0)
    ob = next((jj for jj in range(j0, max(j0 - 6, a - 1), -1) if c[jj] > o[jj]), j0)
    return dict(entry=l[ob], ez_lo=l[ob], ez_hi=h[j0], ez_kind="OB" if c[ob] > o[ob] else "OB_fb", ez_i=ob, leg_i=j0)


def liquidity(ctx, t_sig, d, lookback=150):
    """Unbroken confirmed H1 swings (N=5) beyond current price at t_sig: list of levels, nearest first."""
    H1, M5 = ctx["H1"], ctx["M5"]
    t_sig = np.datetime64(t_sig, "ns")
    a1 = int(np.searchsorted(H1["tc"], t_sig, "right") - 1)
    if a1 < 10:
        return []
    m5a = np.searchsorted(M5["t"], H1["tc"][a1], "left")
    m5b = np.searchsorted(M5["tc"], t_sig, "right")
    N = H1["N"]
    lo_s = max(0, a1 - lookback)
    if d == 1:
        cand = np.where(H1["sh"][lo_s:a1 - N + 1])[0] + lo_s
        post = M5["h"][m5a:m5b].max() if m5b > m5a else -np.inf
        lv = [H1["h"][s] for s in cand if max(H1["h"][s + 1:a1 + 1].max(), post) <= H1["h"][s]]
        return sorted(lv)
    cand = np.where(H1["sl"][lo_s:a1 - N + 1])[0] + lo_s
    post = M5["l"][m5a:m5b].min() if m5b > m5a else np.inf
    lv = [H1["l"][s] for s in cand if min(H1["l"][s + 1:a1 + 1].min(), post) >= H1["l"][s]]
    return sorted(lv, reverse=True)


def sweep_info(ctx, i_t, j, d, tf="M5", look=72):
    """Did price between the touch and the CHoCH take out a confirmed swing low (bull) that was intact at the touch?
    M5: swings N=3 of the last 72 M5 bars. M30: swings N=3 of the last 96 M30 bars (closed before the touch)."""
    M5 = ctx["M5"]
    ext = M5["l"][i_t:j + 1].min() if d == 1 else M5["h"][i_t:j + 1].max()
    if tf == "M5":
        A, end = M5, i_t                                      # swings confirmed by the bar before the touch
    else:
        A = ctx["M30"]; end = int(np.searchsorted(A["tc"], M5["t"][i_t], "right"))
        look = 96
    N = A["N"]
    s0 = max(0, end - look)
    arr = A["sl"] if d == 1 else A["sh"]
    cand = np.where(arr[s0:max(s0, end - N)])[0] + s0
    best = None
    for s in cand:
        if d == 1:
            lev = A["l"][s]
            if (A["l"][s + 1:end] >= lev).all() and ext < lev:
                best = lev if best is None else min(best, lev)
        else:
            lev = A["h"][s]
            if (A["h"][s + 1:end] <= lev).all() and ext > lev:
                best = lev if best is None else max(best, lev)
    if best is None:
        return False, 0.0
    return True, abs(best - ext) / M5["atr"][j]


# =============================================================================================== variants
REF = dict(bias="both", fresh_block=0, poi_tf="M30", poi_kinds=("OB", "OB_fb", "FVG"), poi_valid=96, discount=True,
           sweep_req=False, ltf="M5", ltf_struct="int", choch_wait=36, entry="limit", limit_valid=12,
           sl_mode="touch", tp_mode="liq", min_rr=2.0, be=np.nan, partial=False, sess=("LDN", "NY"), flat20=False,
           min_stop_comm=10.0)
VARIANTS = {
    "REF": {},
    "V01_bias_h4only": dict(bias="h4"),
    "V02_bias_emaonly": dict(bias="ema"),
    "V03_block_fresh_h4break": dict(fresh_block=6),
    "V04_bias_h4_major": dict(bias="h4ext"),
    "V05_poi_OB_only": dict(poi_kinds=("OB", "OB_fb")),
    "V06_poi_FVG_only": dict(poi_kinds=("FVG",)),
    "V07_poi_H1": dict(poi_tf="H1", poi_valid=48),
    "V08_sweep_required": dict(sweep_req=True),
    "V09_ltf_major_choch": dict(ltf_struct="ext"),
    "V10_ltf_M15": dict(ltf="M15", choch_wait=12, limit_valid=4),
    "V11_market_at_choch": dict(entry="market"),
    "V12_tp_2R": dict(tp_mode="2R"),
    "V13_tp_3R": dict(tp_mode="3R"),
    "V14_tp_first_liq_ge2R": dict(tp_mode="liq_first2"),
    "V15_be_1R": dict(be=1.0),
    "V16_partial1R_runner": dict(partial=True),
    "V17_flat_20utc": dict(flat20=True),
    "V18_sl_beyond_poi": dict(sl_mode="zone"),
}


def cfg_of(name):
    c = dict(REF); c.update(VARIANTS[name]); return c


def build_orders(ctx, cfg, period):
    sym = ctx["sym"]; P = pip(sym)
    Z = ctx[cfg["poi_tf"]]; M5 = ctx["M5"]; L = ctx[cfg["ltf"]]; H4 = ctx["H4"]; H1 = ctx["H1"]
    s0 = np.datetime64(pd.Timestamp(SIG_START[period])); s1 = np.datetime64(pd.Timestamp(PERIODS[period][1]))
    zones = [z for z in Z["zones"] if z[3] in cfg["poi_kinds"]]
    if not zones:
        return pd.DataFrame()
    zd = np.array([z[0] for z in zones]); zlo = np.array([z[1] for z in zones]); zhi = np.array([z[2] for z in zones])
    zk = np.array([z[4] for z in zones]); zkind = np.array([z[3] for z in zones])
    tcr = Z["tc"][zk]
    keep = (tcr >= s0 - np.timedelta64(4, "D")) & (tcr < s1)
    FUNNEL["01_zones"] += int(keep.sum())
    # 1) bias at POI creation must equal the zone direction
    b0 = bias_at(ctx, tcr, cfg["bias"])
    keep &= (b0 == zd)
    FUNNEL["02_bias_at_creation"] += int(keep.sum())
    # 2) discount / premium of the H4 dealing range at creation
    i4 = np.searchsorted(H4["tc"], tcr, "right") - 1
    rlo, rhi = H4["rlo"][np.maximum(i4, 0)], H4["rhi"][np.maximum(i4, 0)]
    pos = ((zlo + zhi) / 2 - rlo) / np.where(rhi > rlo, rhi - rlo, np.nan)
    dpos = np.where(zd == 1, pos, 1 - pos)                    # 0 = deepest discount (buy) / premium (sell)
    if cfg["discount"]:
        keep &= (dpos <= 0.5)
    FUNNEL["03_discount_premium"] += int(keep.sum())
    t5, lo5, hi5 = M5["t"], M5["l"], M5["h"]
    nZ = len(Z["tc"])
    rows = []
    for zi in np.where(keep)[0]:
        d = int(zd[zi]); k = int(zk[zi])
        texp = Z["tc"][min(k + cfg["poi_valid"], nZ - 1)]
        a = np.searchsorted(t5, tcr[zi], "left"); e = np.searchsorted(t5, texp, "left")
        if e <= a:
            continue
        hit = (lo5[a:e] <= zhi[zi]) if d == 1 else (hi5[a:e] >= zlo[zi])
        if not hit.any():
            continue
        FUNNEL["04_touched"] += 1
        it5 = a + int(np.argmax(hit))                        # 3) first touch (M5 bar); the zone is dead afterwards
        t_touch = t5[it5]
        if bias_at(ctx, t_touch, cfg["bias"])[0] != d:
            continue
        FUNNEL["05_bias_at_touch"] += 1
        # 4) LTF CHoCH in the bias direction within choch_wait LTF bars of the touch
        it = it5 if cfg["ltf"] == "M5" else int(np.searchsorted(L["t"], t_touch, "right") - 1)
        ev = L["ev_i"] if cfg["ltf_struct"] == "int" else L["ev_e"]
        br = L["br_i"] if cfg["ltf_struct"] == "int" else L["br_e"]
        w = ev[it:min(it + cfg["choch_wait"] + 1, len(ev))]
        cj = np.where(w == 2 * d)[0]
        if not len(cj):
            continue
        j = it + int(cj[0])
        FUNNEL["06_choch"] += 1
        t_sig = L["tc"][j]
        if not (s0 <= t_sig < s1):
            continue
        if bias_at(ctx, t_sig, cfg["bias"])[0] != d:
            continue
        FUNNEL["07_bias_at_choch"] += 1
        i4s = int(np.searchsorted(H4["tc"], t_sig, "right") - 1)
        if cfg["fresh_block"] and i4s - H4["last_evt"][i4s] < cfg["fresh_block"]:
            continue
        # sessions
        kz = killzone([t_sig])[0]
        if kz not in cfg["sess"] or pd.Timestamp(t_sig).dayofweek not in (1, 2, 3, 4):
            continue
        FUNNEL["08_session"] += 1
        # sweep context (M5 / M30 liquidity taken between touch and CHoCH)
        j5 = j if cfg["ltf"] == "M5" else int(np.searchsorted(M5["tc"], t_sig, "right") - 1)
        sw5, sw5d = sweep_info(ctx, it5, j5, d, "M5")
        sw30, sw30d = sweep_info(ctx, it5, j5, d, "M30")
        if cfg["sweep_req"] and not (sw5 or sw30):
            continue
        # 5) entry
        ez = ltf_entry_zone(L, j, d, int(br[j]))
        if ez is None:
            continue
        ref_px = L["c"][j]
        entry = ez["entry"] if cfg["entry"] == "limit" else ref_px
        if cfg["entry"] == "limit" and (entry - ref_px) * d >= 0:
            continue                                          # limit would not be a pullback entry
        FUNNEL["09_entry_zone"] += 1
        # 6) stop beyond the extreme since the touch + 0.1 ATR(LTF)
        ext = L["l"][it:j + 1].min() if d == 1 else L["h"][it:j + 1].max()
        buf = 0.1 * L["atr"][j]
        sl_reason = "extreme since POI touch + 0.1 ATR(%s)" % cfg["ltf"]
        if cfg["sl_mode"] == "zone":
            ext = min(ext, zlo[zi]) if d == 1 else max(ext, zhi[zi])
            i30 = int(np.searchsorted(Z["tc"], t_sig, "right") - 1)
            buf = 0.1 * Z["atr"][i30]
            sl_reason = "beyond POI distal edge and touch extreme + 0.1 ATR(%s)" % cfg["poi_tf"]
        sl = ext - d * buf
        risk = (entry - sl) * d
        if not risk > 0 or risk / P < cfg["min_stop_comm"] * COMM[sym]:
            continue
        FUNNEL["10_stop_ge_10x_comm"] += 1
        # 7) target
        liq = liquidity(ctx, t_sig, d)
        liq = [x for x in liq if (x - entry) * d > 0]
        rr_liq = abs(liq[0] - entry) / risk if liq else np.nan
        if cfg["tp_mode"] == "liq":
            if not liq or rr_liq < cfg["min_rr"]:
                continue
            tp, tp_reason = liq[0], "next unbroken H1 swing (N=5)"
        elif cfg["tp_mode"] == "liq_first2":
            ok = [x for x in liq if abs(x - entry) / risk >= cfg["min_rr"]]
            if not ok:
                continue
            tp, tp_reason = ok[0], "first unbroken H1 swing with RR>=2"
        else:
            m = 2.0 if cfg["tp_mode"] == "2R" else 3.0
            tp, tp_reason = entry + d * m * risk, "fixed %gR" % m
        FUNNEL["11_target_rr_ok"] += 1
        rr = abs(tp - entry) / risk
        # context features
        i1 = int(np.searchsorted(H1["tc"], t_sig, "right") - 1)
        atr1 = H1["atr"][i1]
        i30 = int(np.searchsorted(ctx["M30"]["tc"], t_sig, "right") - 1)
        ts = pd.Timestamp(t_sig)
        max_hold = np.nan
        if cfg["flat20"]:
            max_hold = max(5.0, (ts.normalize() + pd.Timedelta(hours=20) - ts) / pd.Timedelta(minutes=1))
        lchg = H4["last_chg"][i4s] if cfg["bias"] != "h4ext" else H4["last_chg_e"][i4s]
        rows.append(dict(
            t_signal=ts, side=d, entry_type=cfg["entry"], entry=entry,
            expire=ts + pd.Timedelta(minutes=TFM[cfg["ltf"]] * cfg["limit_valid"]), sl=sl, tp=tp,
            be_at_r=cfg["be"], max_hold_min=max_hold, sl_reason=sl_reason, tp_reason=tp_reason,
            t_bias=pd.Timestamp(H4["tc"][lchg]) if lchg >= 0 else pd.NaT,
            t_zone=pd.Timestamp(tcr[zi]), t_touch=pd.Timestamp(t_touch), t_choch=ts,
            t_leg_origin=pd.Timestamp(L["t"][ez["leg_i"]]), t_entry_zone=pd.Timestamp(L["t"][ez["ez_i"]]),
            zone_kind=zkind[zi], zone_lo=zlo[zi], zone_hi=zhi[zi], zone_size_atr=(zhi[zi] - zlo[zi]) / Z["atr"][k],
            zone_age_bars=int(np.searchsorted(Z["tc"], t_touch, "right") - 1 - k), disc_pct=round(float(dpos[zi]), 3),
            entry_src=ez["ez_kind"], sweep5=sw5, sweep5_depth_atr=round(sw5d, 2), sweep30=sw30,
            sweep30_depth_atr=round(sw30d, 2), bars_to_choch=j - it, rr=round(rr, 2), rr_liq=round(rr_liq, 2),
            risk_pips_plan=risk / P, stop_x_comm=round(risk / P / COMM[sym], 1), session=kz, weekday=ts.dayofweek,
            hour=ts.hour, atr_ltf_pips=L["atr"][j] / P, atr_m30_pips=ctx["M30"]["atr"][i30] / P, atr_h1_pips=atr1 / P,
            ema_dist_atr=round(d * (H1["c"][i1] - H1["ema200"][i1]) / atr1, 2),
            h4_trend_age=int(i4s - lchg) if lchg >= 0 else -1, h4_bars_since_break=int(i4s - H4["last_evt"][i4s]),
            _zi=zi))
    o = pd.DataFrame(rows)
    if len(o):
        # several POIs can lead to the same CHoCH: one order per signal bar (earliest touch, then OB before FVG)
        o = o.sort_values(["t_signal", "t_touch", "zone_kind"], kind="stable").drop_duplicates("t_signal").reset_index(drop=True)
        o = o.drop(columns="_zi")
    return o


def add_gross(t):
    t = t.copy()
    slipped = t.exit_reason.isin(["SL", "BE", "GAP_SL"])
    t["gross_R"] = (t.gross_pips + np.where(slipped, STOP_SLIP, 0.0)) / t.risk_pips   # before commission and stop slippage
    return t


def run_variant(name, period, pairs=TRADE_PAIRS, verbose=True):
    cfg = cfg_of(name)
    allt = []
    for sym in pairs:
        ctx = prepare(sym, period)
        o = build_orders(ctx, cfg, period)
        if not len(o):
            continue
        if cfg["partial"]:
            runner = o.copy(); runner["be_at_r"] = 1.0
            r2 = simulate(sym, runner)
            f = r2[r2.status == "filled"].copy()
            half = f[o.columns].copy()
            risk = (f.fill - f.sl) * f.side
            half["tp"] = f.fill + f.side * risk                # 1R from the actual fill
            half["be_at_r"] = np.nan
            r1 = simulate(sym, half, one_at_a_time=False, cancel_limit_if_tp_first=False)
            r1 = r1.set_index(f.index)
            r2 = r2.copy()
            for col in ("gross_pips", "net_pips", "R", "usd_01"):
                r2.loc[f.index, col] = 0.5 * r1[col].values + 0.5 * f[col].values
            r2.loc[f.index, "exit_reason"] = r1.exit_reason.values + "+" + f.exit_reason.values
            r2.loc[f.index, "t_exit"] = np.maximum(r1.t_exit.values, f.t_exit.values)
            res = r2
        else:
            res = simulate(sym, o)
        allt.append(res)
    t = pd.concat(allt, ignore_index=True) if allt else pd.DataFrame()
    if len(t):
        t["variant"] = name
        t = t[t.period == period].reset_index(drop=True)
        f = t.status == "filled"
        t.loc[f, "gross_R"] = add_gross(t[f]).gross_R
    return t


def summarize(t, name, period):
    f = t[t.status == "filled"] if len(t) else t
    if not len(f):
        return dict(variant=name, period=period, trades=0)
    s = stats(f)
    g = f.gross_R.values
    gp, gl = g[g > 0].sum(), -g[g < 0].sum()
    per = f.groupby("sym").R.mean()
    perg = f.groupby("sym").gross_R.mean()
    return dict(variant=name, period=period, orders=len(t), fill_pct=round(100 * len(f) / max(1, (t.status != "skipped_busy").sum()), 1),
                **s, gross_avg_R=round(g.mean(), 3), gross_total_R=round(g.sum(), 1), cost_R=round(g.mean() - f.R.mean(), 3),
                PF_gross=round(gp / gl, 2) if gl > 0 else np.inf, se_R=round(f.R.std() / np.sqrt(len(f)), 3),
                pairs_pos=f"{(per > 0).sum()}/{len(per)}", pairs_pos_gross=f"{(perg > 0).sum()}/{len(perg)}",
                med_risk_pips=round(f.risk_pips.median(), 1))


def per_pair(t):
    f = t[t.status == "filled"]
    rows = []
    for sym, g in f.groupby("sym"):
        s = stats(g)
        rows.append(dict(sym=sym, **s, gross_avg_R=round(g.gross_R.mean(), 3), gross_total_R=round(g.gross_R.sum(), 1)))
    s = stats(f)
    rows.append(dict(sym="POOLED", **s, gross_avg_R=round(f.gross_R.mean(), 3), gross_total_R=round(f.gross_R.sum(), 1)))
    return pd.DataFrame(rows)


def md(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def run_period(period, names):
    assert period in ("DEV", "VAL")
    summ = []
    for name in names:
        t0 = time.time()
        FUNNEL.clear()
        t = run_variant(name, period)
        fun = dict(sorted(FUNNEL.items()))
        if len(t):
            fun["12_orders_after_dedupe"] = int(len(t))
            fun["13_filled"] = int((t.status == "filled").sum())
            fun["13b_skipped_busy"] = int((t.status == "skipped_busy").sum())
        print("   funnel:", fun, flush=True)
        with open(os.path.join(OUT, f"funnel_{name}_{period}.json"), "w") as fh:
            json.dump(fun, fh, indent=1)
        save("BT6b", f"{name}_{period}", t)
        s = summarize(t, name, period)
        summ.append(s)
        print(f"{name:28s} {period} trades={s.get('trades')} grossR={s.get('gross_avg_R')} netR={s.get('avg_R')} "
              f"PF={s.get('PF')} pairs+={s.get('pairs_pos')} ({time.time() - t0:.0f}s)", flush=True)
        if len(t):
            pp = per_pair(t)
            pp.to_csv(os.path.join(OUT, f"perpair_{name}_{period}.csv"), index=False)
    S = pd.DataFrame(summ)
    f = os.path.join(OUT, f"variants_{period}.csv")
    if os.path.exists(f):
        old = pd.read_csv(f)
        old = old[~old.variant.isin(S.variant)]
        S = pd.concat([old, S], ignore_index=True)
    S.to_csv(f, index=False)
    return S


def breakdown(period):
    """Robustness view of every saved variant: t-stat, per year, per side, per session."""
    import glob
    rows = []
    for f in sorted(glob.glob(os.path.join(OUT, f"trades_*_{period}.csv"))):
        n = os.path.basename(f)[7:-(len(period) + 5)]
        t = pd.read_csv(f)
        t = t[t.status == "filled"]
        if not len(t):
            continue
        y = t.groupby(t.t_signal.str[:4]).R.mean().round(2).to_dict()
        yg = t.groupby(t.t_signal.str[:4]).gross_R.mean().round(2).to_dict()
        ss = t.groupby("side").R.mean().round(2).to_dict()
        se = t.R.std() / np.sqrt(len(t))
        ses = t.groupby("session").R.mean().round(2).to_dict()
        rows.append(dict(variant=n, trades=len(t), gross_R=round(t.gross_R.mean(), 3), net_R=round(t.R.mean(), 3),
                         t_net=round(t.R.mean() / se, 2), **{f"net_{k}": v for k, v in y.items()},
                         **{f"gross_{k}": v for k, v in yg.items()}, long=ss.get(1), short=ss.get(-1),
                         LDN=ses.get("LDN"), NY=ses.get("NY")))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, f"breakdown_{period}.csv"), index=False)
    print(df.to_string())


def examples(name, period, n=10, seed=7):
    """n example trades with every timestamp (for a human to follow on a chart)."""
    t = pd.read_csv(os.path.join(OUT, f"trades_{name}_{period}.csv"))
    t = t[t.status == "filled"]
    t = pd.concat([t[t.exit_reason == "TP"].sample(min(4, (t.exit_reason == "TP").sum()), random_state=seed),
                   t[t.exit_reason != "TP"].sample(n - min(4, (t.exit_reason == "TP").sum()), random_state=seed)])
    t = t.sort_values("t_signal")
    cols = ["sym", "side", "t_bias", "t_zone", "zone_kind", "zone_lo", "zone_hi", "t_touch", "sweep5", "sweep30", "t_choch",
            "entry_src", "entry", "sl", "tp", "rr", "t_fill", "fill", "t_exit", "exit", "exit_reason", "R", "gross_R"]
    df = t[cols].copy()
    for c in ("R", "gross_R"):
        df[c] = df[c].round(2)
    dig = lambda s, x: round(x, 3 if s in ("XAUUSD",) or "JPY" in s else 5)
    for c in ("zone_lo", "zone_hi", "entry", "sl", "tp", "fill", "exit"):
        df[c] = [dig(s, x) for s, x in zip(df.sym, df[c])]
    df.to_csv(os.path.join(OUT, f"examples_{name}_{period}.csv"), index=False)
    print(md(df))


# =============================================================================================== sanity checks
def check():
    import smc_core
    ctx = prepare("EURUSD", "DEV")
    for tf in ("M5", "M30", "H4"):
        A = ctx[tf]
        b = pd.DataFrame(dict(open=A["o"], high=A["h"], low=A["l"], close=A["c"]))
        sh, sl = smc_core.swings(A["h"], A["l"], A["N"])
        st = smc_core.structure(b, A["N"])
        print(tf, "swings equal smc_core:", bool((sh == A["sh"]).all() and (sl == A["sl"]).all()),
              "| internal trend equal:", bool((st["trend"] == A["tr_i"]).all()), "evt equal:", bool((st["evt"] == A["ev_i"]).all()),
              "| internal breaks:", int((A["ev_i"] != 0).sum()), "external breaks:", int((A["ev_e"] != 0).sum()),
              "| trend agreement int/ext: %.2f" % (A["tr_i"] == A["tr_e"]).mean())
    # external structure invariants on H4: after a bull CHoCH/BOS the protected low is below the break close
    A = ctx["H4"]
    k = np.where(A["ev_e"] > 0)[0]
    print("H4 external: protected low < break close for all bull breaks:", bool((A["prot"][k] < A["c"][k]).all()))
    k = np.where(A["ev_e"] < 0)[0]
    print("H4 external: protected high > break close for all bear breaks:", bool((A["prot"][k] > A["c"][k]).all()))
    # OB count vs smc_core
    Z = ctx["M30"]["zones"]
    print("M30 zones:", pd.Series([z[3] for z in Z]).value_counts().to_dict())


def replay(csv, n=3, seed=1):
    """Independent M1 replay of a few filled trades (limit fill on ask/bid, SL first, no TP on fill bar)."""
    t = pd.read_csv(csv, parse_dates=["t_signal", "expire", "t_fill", "t_exit"])
    t = t[(t.status == "filled") & (t.exit_reason.isin(["SL", "TP"]))].sample(n, random_state=seed)
    lines = []
    for _, r in t.iterrows():
        m1 = bars(r.sym, "M1")
        P = pip(r.sym); d = int(r.side)
        seg = m1[(m1.time >= r.t_signal) & (m1.time < r.t_signal + pd.Timedelta(days=10))].reset_index(drop=True)
        fi = None
        for i, x in seg.iterrows():
            if x.time >= r.expire:
                break
            if (d == 1 and x.low + x.spr <= r.entry) or (d == -1 and x.high >= r.entry):
                fi = i; break
        fill = min(r.entry, seg.open[fi] + seg.spr[fi]) if d == 1 else max(r.entry, seg.open[fi])
        ex = None
        for i in range(fi, len(seg)):
            x = seg.iloc[i]
            slhit = x.low <= r.sl if d == 1 else x.high + x.spr >= r.sl
            tphit = (x.high >= r.tp if d == 1 else x.low + x.spr <= r.tp) and i > fi
            if slhit:
                ex = (x.time, "SL"); break
            if tphit:
                ex = (x.time, "TP"); break
        lines.append(f"{r.sym} side={d} signal={r.t_signal} entry={r.entry:.5f} sl={r.sl:.5f} tp={r.tp:.5f} | "
                     f"simulate: fill {r.t_fill} @ {r.fill:.5f}, exit {r.t_exit} {r.exit_reason} R={r.R:.3f} | "
                     f"hand: fill {seg.time[fi]} @ {fill:.5f}, exit {ex[0]} {ex[1]} -> "
                     f"{'MATCH' if (seg.time[fi] == r.t_fill and ex[0] == r.t_exit and ex[1] == r.exit_reason) else 'DIFF'}")
    return lines


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    args = sys.argv[2:]
    if cmd == "prep":
        per = args[0] if args else "DEV"
        for s in TRADE_PAIRS:
            t0 = time.time(); prepare(s, per); print(s, per, f"{time.time() - t0:.0f}s", flush=True)
    elif cmd == "dev":
        run_period("DEV", args or list(VARIANTS))
    elif cmd == "val":
        assert 1 <= len(args) <= 3, "at most 3 variants go to VAL"
        run_period("VAL", args)
    elif cmd == "check":
        check()
    elif cmd == "breakdown":
        breakdown(args[0] if args else "DEV")
    elif cmd == "show":                                       # show <variant> <period> <row>: M5 bars of one setup
        t = pd.read_csv(os.path.join(OUT, f"trades_{args[0]}_{args[1]}.csv"), parse_dates=["t_touch", "t_choch", "t_zone"])
        r = t.iloc[int(args[2])]
        ctx = prepare(r.sym, args[1]); M5 = ctx["M5"]
        a = np.searchsorted(M5["t"], np.datetime64(r.t_touch) - np.timedelta64(60, "m"))
        b = np.searchsorted(M5["tc"], np.datetime64(r.t_choch), "right")
        print(r[["sym", "side", "t_zone", "zone_kind", "zone_lo", "zone_hi", "t_touch", "t_choch", "t_leg_origin",
                 "t_entry_zone", "entry_src", "entry", "sl", "tp", "rr", "status", "exit_reason", "R"]].to_string())
        df = pd.DataFrame(dict(time=M5["t"][a:b], o=M5["o"][a:b], h=M5["h"][a:b], l=M5["l"][a:b], c=M5["c"][a:b],
                               swH=M5["sh"][a:b], swL=M5["sl"][a:b], trend=M5["tr_i"][a:b], evt=M5["ev_i"][a:b]))
        print(df.to_string())
    elif cmd == "examples":
        examples(args[0], args[1] if len(args) > 1 else "DEV")
    elif cmd == "replay":
        for x in replay(args[0], int(args[1]) if len(args) > 1 else 3):
            print(x)
