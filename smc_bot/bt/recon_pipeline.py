"""RECON: reconciliation of BT6a vs BT6b REF (same spec, 790 vs 150 DEV trades).

One flag-driven engine that can reproduce either implementation's REF setup detection, so each rule-interpretation
difference can be flipped one at a time. Reuses (imports, never edits) BT6b's detection helpers and cached per-pair
context, smc_core for BT6a-style objects, and common.simulate for every fill/exit (both BT6 scripts do the same).

Run from TTXT root:
    .venv/Scripts/python.exe smc_bot/bt/recon_pipeline.py dev        # all DEV configs
    .venv/Scripts/python.exe smc_bot/bt/recon_pipeline.py val CANON  # final canonical config only (VAL confirm)
TEST (2025-07+) is never read: detection bars come from BT6b.prepare (cut at the period end) and trades outside the
period are dropped.
"""
import os, sys, json, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                                   # noqa: E402
from common import PERIODS, pip, COMM, STOP_SLIP, simulate, stats, RESULTS   # noqa: E402
import bt6b_pipeline as B                                       # noqa: E402  (import only; functions reused as-is)
import smc_core                                                 # noqa: E402

OUT = os.path.join(RESULTS, "RECON"); os.makedirs(OUT, exist_ok=True)
PAIRS9 = B.TRADE_PAIRS

# memoize M1 bars so simulate() does not re-read parquet on every call (no change to common.py on disk)
_bars_orig = common.bars
_memo = {}


def _bars_memo(sym, tf):
    if (sym, tf) not in _memo:
        _memo[(sym, tf)] = _bars_orig(sym, tf)
    return _memo[(sym, tf)]


common.bars = _bars_memo

# ------------------------------------------------------------------------------------------------ configs
# B-style defaults (BT6b REF); A-style values in comments
BCFG = dict(
    bias_filter=True,        # A: False  (build_ref_orders never applies z.dir == bias)
    bias_checks="all3",      # A: "creation"  (B re-checks at touch and at CHoCH)
    ema_warm=True,           # A: False  (B requires >= 200 closed H1 bars for EMA200)
    start="2021-02-01",      # A: "2021-01-01"
    zone_N=3,                # A: 4  (SWING_N["M30"] = 4 in bt6a)
    ob_def="B",              # A: "core" (OB = candle high-low) ; B: [leg-low, OB-candle high]
    ob_premit=True,          # A: False  (B drops OBs already mitigated before the break closed)
    fvg_def="B",             # A: "A" (>=0.15 x HL-ATR of the gap bar) ; B: >=0.1 x TR-ATR of bar k-3
    expiry="bars",           # A: "clock" (96 x 30 min clock time)
    touch="onesided",        # A: "overlap"
    drange="swing",          # A: "roll30" (rolling 30 H4 bars, excl. last closed)
    choch_win="incl",        # A: "after" (bars touch+1..touch+36) ; B: touch..touch+36
    ez="B",                  # A: "A" (latest FVG in [leg origin, CHoCH], smc_core fvgs, no mitigation/candle-1 check)
    pullback=True,           # A: False (B drops a limit that is not below the CHoCH close for buys)
    atr_stop="tr",           # A: "hl"
    target="nearest",        # A: "last" (smc_core lsh/lsl of H1 = last confirmed swing, broken or not)
    dedupe=True,             # A: False (A relies on simulate one_at_a_time)
    pd_point="mid",          # both: zone midpoint
    kz="local",              # both: London/NY 08-11 local time (DST-aware)
)
ACFG = dict(bias_filter=False, bias_checks="creation", ema_warm=False, start="2021-01-01", zone_N=4, ob_def="core",
            ob_premit=False, fvg_def="A", expiry="clock", touch="overlap", drange="roll30", choch_win="after", ez="A",
            pullback=False, atr_stop="hl", target="last", dedupe=False, pd_point="mid", kz="local")


def cfg(base, **kw):
    c = dict(base); c.update(kw); return c


# ------------------------------------------------------------------------------------------------ per-pair data
_PD = {}


def pdata(sym, period):
    key = (sym, period)
    if key in _PD:
        return _PD[key]
    ctx = B.prepare(sym, period)
    M30, M5, H1, H4 = ctx["M30"], ctx["M5"], ctx["H1"], ctx["H4"]
    X = dict(ctx=ctx)
    # smc_core H1 structure N=5 (A's target: last confirmed swing level)
    df1 = pd.DataFrame(dict(open=H1["o"], high=H1["h"], low=H1["l"], close=H1["c"]))
    st1 = smc_core.structure(df1, 5)
    X["h1_lsh"], X["h1_lsl"] = st1["lsh"], st1["lsl"]
    # A's M5 FVG flags + HL ATR
    df5 = pd.DataFrame(dict(high=M5["h"], low=M5["l"]))
    X["fvg5A_bull"], X["fvg5A_bear"] = smc_core.fvgs(df5, 0.1)
    X["atr5_hl"] = smc_core.atr(df5, 14)
    # A's H4 rolling range (30 bars, shift 1)
    X["h4_rhi"] = pd.Series(H4["h"]).rolling(30).max().shift(1).values
    X["h4_rlo"] = pd.Series(H4["l"]).rolling(30).min().shift(1).values
    X["zones"] = {}
    _PD[key] = X
    return X


def zones(X, N, ob_def, premit, fvg_def):
    key = (N, ob_def, premit, fvg_def)
    if key in X["zones"]:
        return X["zones"][key]
    A = X["ctx"]["M30"]
    o, h, l, c = A["o"], A["h"], A["l"], A["c"]
    if N == A["N"]:
        ev, br = A["ev_i"], A["br_i"]
    else:
        sh, sl = B.swings_own(h, l, N)
        _, ev, br = B.structure_internal(h, l, c, N, sh, sl)
    rows = []
    for k in np.where(ev != 0)[0]:
        d = 1 if ev[k] > 0 else -1
        a = br[k] + 1
        if a > k:
            continue
        if d == 1:
            j0 = a + int(np.argmin(l[a:k + 1]))
            j = next((jj for jj in range(j0, max(j0 - 6, a - 1), -1) if c[jj] < o[jj]), j0)
            lo, hi = (l[j0], h[j]) if ob_def == "B" else (l[j], h[j])
            if premit:
                after = np.arange(j0 + 1, k + 1); left = after[l[after] > hi]
                if len(left) and (l[left[0] + 1:k + 1] <= hi).any():
                    continue
        else:
            j0 = a + int(np.argmax(h[a:k + 1]))
            j = next((jj for jj in range(j0, max(j0 - 6, a - 1), -1) if c[jj] > o[jj]), j0)
            lo, hi = (l[j], h[j0]) if ob_def == "B" else (l[j], h[j])
            if premit:
                after = np.arange(j0 + 1, k + 1); left = after[h[after] < lo]
                if len(left) and (h[left[0] + 1:k + 1] >= lo).any():
                    continue
        rows.append((d, lo, hi, "OB", k))
    if fvg_def == "B":
        rows += B.fvg_zones(h, l, A["atr"])
    else:
        df = pd.DataFrame(dict(high=h, low=l))
        bull, bear = smc_core.fvgs(df, 0.15)
        rows += [(1, h[k - 2], l[k], "FVG", k) for k in np.where(bull)[0]]
        rows += [(-1, h[k], l[k - 2], "FVG", k) for k in np.where(bear)[0]]
    Z = pd.DataFrame(rows, columns=["d", "lo", "hi", "kind", "k"]).sort_values(["k"], kind="stable").reset_index(drop=True)
    X["zones"][key] = Z
    return Z


def drange_swing(X, dr):
    """swing (B: H4 N=3 last confirmed swing hi/lo extended by price since), swing5 (same, N=5),
    swingnoext (N=3 levels only, no extension)."""
    if dr in X["zones"]:
        return X["zones"][dr]
    H4 = X["ctx"]["H4"]
    if dr == "swing":
        out = (H4["rlo"], H4["rhi"])
    elif dr == "swing5":
        sh, sl = B.swings_own(H4["h"], H4["l"], 5)
        out = B.dealing_range(H4["h"], H4["l"], sh, sl, 5)
    elif dr == "swingnoext":
        N = 3; n = len(H4["h"]); hi = np.full(n, np.nan); lo = np.full(n, np.nan); Hh = Ll = np.nan
        for k in range(n):
            s_ = k - N
            if s_ >= 0 and H4["sh"][s_]:
                Hh = H4["h"][s_]
            if s_ >= 0 and H4["sl"][s_]:
                Ll = H4["l"][s_]
            hi[k], lo[k] = Hh, Ll
        out = (lo, hi)
    X["zones"][dr] = out
    return out


def killzone(t_sig, mode):
    if mode == "local":
        return B.killzone([t_sig])[0]
    h = pd.Timestamp(t_sig).hour                       # fixed UTC reading of the brief: 07-11 and 12-16 UTC
    return "LDN" if 7 <= h < 11 else ("NY" if 12 <= h < 16 else "")


def bias_at(ctx, t, warm):
    H1, H4 = ctx["H1"], ctx["H4"]
    t = np.atleast_1d(np.asarray(t, dtype="datetime64[ns]"))
    i4 = np.searchsorted(H4["tc"], t, "right") - 1
    i1 = np.searchsorted(H1["tc"], t, "right") - 1
    ok = (i4 >= 0) & (i1 >= (200 if warm else 0))
    i4c, i1c = np.maximum(i4, 0), np.maximum(i1, 0)
    e = np.sign(H1["c"][i1c] - H1["ema200"][i1c]).astype(int)
    s4 = H4["tr_i"][i4c].astype(int)
    return np.where(ok, np.where(s4 == e, s4, 0), 0)


def ez_A(X, j, d, brk):
    """BT6a entry zone: latest smc_core FVG (flag at bar m in [leg origin, CHoCH]) else the CHoCH leg OB (proximal)."""
    M5 = X["ctx"]["M5"]; h, l, o, c = M5["h"], M5["l"], M5["o"], M5["c"]
    a = brk + 1
    if a > j:
        return None
    if d == 1:
        j0 = a + int(np.argmin(l[a:j + 1]))
        idx = np.nonzero(X["fvg5A_bull"][j0:j + 1])[0]
        if idx.size:
            m = j0 + idx[-1]
            return dict(entry=l[m], ez_kind="FVG")
        ob = next((jj for jj in range(j0, max(j0 - 6, a - 1), -1) if c[jj] < o[jj]), j0)
        return dict(entry=h[ob], ez_kind="OB")
    j0 = a + int(np.argmax(h[a:j + 1]))
    idx = np.nonzero(X["fvg5A_bear"][j0:j + 1])[0]
    if idx.size:
        m = j0 + idx[-1]
        return dict(entry=h[m], ez_kind="FVG")
    ob = next((jj for jj in range(j0, max(j0 - 6, a - 1), -1) if c[jj] > o[jj]), j0)
    return dict(entry=l[ob], ez_kind="OB")


# ------------------------------------------------------------------------------------------------ pipeline
def build_orders(sym, period, C, fun):
    X = pdata(sym, period); ctx = X["ctx"]
    M30, M5, H1, H4 = ctx["M30"], ctx["M5"], ctx["H1"], ctx["H4"]
    s0 = np.datetime64(pd.Timestamp(C["start"] if period == "DEV" else PERIODS[period][0]))
    s1 = np.datetime64(pd.Timestamp(PERIODS[period][1]))
    Z = zones(X, C["zone_N"], C["ob_def"], C["ob_premit"], C["fvg_def"])
    zd, zlo, zhi, zk, zkind = Z.d.values, Z.lo.values, Z.hi.values, Z.k.values, Z.kind.values
    tcr = M30["tc"][zk]
    keep = (tcr >= s0 - np.timedelta64(4, "D")) & (tcr < s1)
    fun["01_zones"] += int(keep.sum())
    if C["bias_filter"]:
        keep &= bias_at(ctx, tcr, C["ema_warm"]) == zd
    fun["02_bias_creation"] += int(keep.sum())
    mid = (zlo + zhi) / 2 if C.get("pd_point", "mid") == "mid" else np.where(zd == 1, zhi, zlo)
    dr = C["drange"]
    if dr.startswith("swing"):
        rlo, rhi = drange_swing(X, dr)
        i4 = np.maximum(np.searchsorted(H4["tc"], tcr, "right") - 1, 0)
        rlo, rhi = rlo[i4], rhi[i4]
        pos = (mid - rlo) / np.where(rhi > rlo, rhi - rlo, np.nan)
        dpos = np.where(zd == 1, pos, 1 - pos)
        keep &= dpos <= 0.5
    elif dr.startswith("roll"):
        n = int(dr[4:])
        i1 = np.searchsorted(H1["tc"], tcr, "right") - 1
        i4 = np.searchsorted(H4["tc"], H1["tc"][np.maximum(i1, 0)], "right") - 1
        ok = (i1 >= 0) & (i4 >= 0)
        rhi = pd.Series(H4["h"]).rolling(n).max().shift(1).values[np.maximum(i4, 0)]
        rlo = pd.Series(H4["l"]).rolling(n).min().shift(1).values[np.maximum(i4, 0)]
        pos = np.where(ok, (mid - rlo) / (rhi - rlo), np.nan)
        keep &= ((zd == 1) & (pos < 0.5)) | ((zd == -1) & (pos > 0.5))
    fun["03_pd"] += int(keep.sum())
    t5, lo5, hi5 = M5["t"], M5["l"], M5["h"]
    nZ = len(M30["tc"])
    atr_stop = M5["atr"] if C["atr_stop"] == "tr" else X["atr5_hl"]
    ev, br = M5["ev_i"], M5["br_i"]
    rows = []
    for zi in np.where(keep)[0]:
        d = int(zd[zi]); k = int(zk[zi])
        texp = M30["tc"][min(k + 96, nZ - 1)] if C["expiry"] == "bars" else tcr[zi] + np.timedelta64(96 * 30, "m")
        a = np.searchsorted(t5, tcr[zi], "left"); e = np.searchsorted(t5, texp, "left")
        if e <= a:
            continue
        if C["touch"] == "onesided":
            hit = (lo5[a:e] <= zhi[zi]) if d == 1 else (hi5[a:e] >= zlo[zi])
        else:
            hit = (lo5[a:e] <= zhi[zi]) & (hi5[a:e] >= zlo[zi])
        if not hit.any():
            continue
        fun["04_touched"] += 1
        it = a + int(np.argmax(hit))
        if C["bias_filter"] and C["bias_checks"] == "all3" and bias_at(ctx, t5[it], C["ema_warm"])[0] != d:
            continue
        fun["05_bias_touch"] += 1
        lo_w = it if C["choch_win"] == "incl" else it + 1
        w = ev[lo_w:min(it + 37, len(ev))]
        cj = np.where(w == 2 * d)[0]
        if not len(cj):
            continue
        j = lo_w + int(cj[0])
        fun["06_choch"] += 1
        t_sig = M5["tc"][j]
        if not (s0 <= t_sig < s1):
            continue
        fun["06b_in_period"] += 1
        if C["bias_filter"] and C["bias_checks"] == "all3" and bias_at(ctx, t_sig, C["ema_warm"])[0] != d:
            continue
        fun["07_bias_choch"] += 1
        kz = killzone(t_sig, C.get("kz", "local"))
        if kz not in ("LDN", "NY") or pd.Timestamp(t_sig).dayofweek not in (1, 2, 3, 4):
            continue
        fun["08_session"] += 1
        ez = B.ltf_entry_zone(M5, j, d, int(br[j])) if C["ez"] == "B" else ez_A(X, j, d, int(br[j]))
        if ez is None:
            continue
        entry = ez["entry"]
        if C["pullback"] and (entry - M5["c"][j]) * d >= 0:
            continue
        fun["09_entry_zone"] += 1
        ext = M5["l"][it:j + 1].min() if d == 1 else M5["h"][it:j + 1].max()
        buf = 0.1 * (atr_stop[j] if atr_stop[j] == atr_stop[j] else 0.0)
        sl = ext - d * buf
        risk = (entry - sl) * d
        if not risk > 0 or risk / pip(sym) < 10 * COMM[sym]:
            continue
        fun["10_stop_ok"] += 1
        if C["target"] in ("nearest", "first2r"):
            liq = [x for x in B.liquidity(ctx, t_sig, d) if (x - entry) * d > 0]
            if C["target"] == "first2r":
                liq = [x for x in liq if (x - entry) * d / risk >= 2.0]
            tp = liq[0] if liq else np.nan
        else:
            i1 = int(np.searchsorted(H1["tc"], t_sig, "right") - 1)
            tp = (X["h1_lsh"][i1] if d == 1 else X["h1_lsl"][i1]) if i1 >= 0 else np.nan
        if not np.isfinite(tp):
            continue
        rr = (tp - entry) * d / risk
        if rr < 2.0:
            continue
        fun["11_target_rr"] += 1
        ts = pd.Timestamp(t_sig)
        rows.append(dict(t_signal=ts, side=d, entry_type="limit", entry=entry, expire=ts + pd.Timedelta(minutes=60),
                         sl=sl, tp=tp, be_at_r=np.nan, max_hold_min=np.nan, t_zone=pd.Timestamp(tcr[zi]),
                         t_touch=pd.Timestamp(t5[it]), zone_kind=zkind[zi], entry_src=ez["ez_kind"], rr=round(rr, 2),
                         session=kz, zk=k))
    o = pd.DataFrame(rows)
    if len(o):
        if C["dedupe"]:
            o = o.sort_values(["t_signal", "t_touch", "zone_kind"], kind="stable").drop_duplicates("t_signal")
        else:
            o = o.sort_values(["t_signal", "t_zone"], kind="stable")      # A: zones in creation order
        o = o.reset_index(drop=True)
    return o


def run(C, period="DEV", pairs=PAIRS9, tag=None):
    from collections import Counter
    fun = Counter(); allt = []
    for sym in pairs:
        o = build_orders(sym, period, C, fun)
        if not len(o):
            continue
        fun["12_orders"] += len(o)
        allt.append(simulate(sym, o))
    t = pd.concat(allt, ignore_index=True)
    t = t[t.period == period].reset_index(drop=True)
    f = t.status == "filled"
    slipped = t.exit_reason.isin(["SL", "BE", "GAP_SL"])
    t["gross_R"] = np.where(f, (t.gross_pips + np.where(slipped, STOP_SLIP, 0.0)) / t.risk_pips, np.nan)
    fun["13_filled"] = int(f.sum()); fun["13b_busy"] = int((t.status == "skipped_busy").sum())
    if tag:
        t.to_csv(os.path.join(OUT, f"trades_{tag}_{period}.csv"), index=False)
    return t, dict(sorted(fun.items()))


def summ(t, name):
    f = t[t.status == "filled"]
    if not len(f):
        return dict(config=name, trades=0)
    s = stats(f)
    se = f.R.std() / np.sqrt(len(f))
    per = f.groupby("sym").R.mean()
    yr = f.groupby(f.t_signal.astype(str).str[:4]).R.mean().round(2).to_dict()
    ses = f.groupby("session").R.mean().round(2).to_dict()
    return dict(config=name, trades=len(f), gross_R=round(f.gross_R.mean(), 3), net_R=round(f.R.mean(), 3),
                total_net_R=round(f.R.sum(), 1), PF=s["PF"], win_pct=s["win_pct"], t_net=round(f.R.mean() / se, 2),
                pairs_pos=f"{(per > 0).sum()}/{len(per)}", maxDD_R=s["maxDD_R"],
                **{f"net_{k}": v for k, v in yr.items()}, LDN=ses.get("LDN"), NY=ses.get("NY"))


# ------------------------------------------------------------------------------------------------ experiment list
FLAG_A = {k: ACFG[k] for k in BCFG}
DIFFS = [  # (name, dict of flags that move B -> A)
    ("D01_no_bias_filter", dict(bias_filter=False)),
    ("D02_bias_creation_only", dict(bias_checks="creation")),
    ("D03_no_ema_warmup", dict(ema_warm=False, start="2021-01-01")),
    ("D04_zone_N4", dict(zone_N=4)),
    ("D05_ob_candle_range", dict(ob_def="core")),
    ("D06_keep_premitigated_OB", dict(ob_premit=False)),
    ("D07_fvg_def_A", dict(fvg_def="A")),
    ("D08_expiry_clock", dict(expiry="clock")),
    ("D09_touch_overlap", dict(touch="overlap")),
    ("D10_drange_roll30", dict(drange="roll30")),
    ("D11_choch_after_touch", dict(choch_win="after")),
    ("D12_entryzone_A", dict(ez="A")),
    ("D13_no_pullback_check", dict(pullback=False)),
    ("D14_atr_hl", dict(atr_stop="hl")),
    ("D15_target_last_swing", dict(target="last")),
    ("D16_no_dedupe", dict(dedupe=False)),
]
EXTRA = [  # other reasonable readings of the spec (not A or B), for robustness only
    ("R01_no_discount_filter", cfg(BCFG, drange="none")),
    ("R02_A_with_bias", cfg(ACFG, bias_filter=True)),
    ("R03_A_with_bias_all3", cfg(ACFG, bias_filter=True, bias_checks="all3", ema_warm=True, start="2021-02-01")),
    ("R04_drange_swing5", cfg(BCFG, drange="swing5")),
    ("R05_drange_swing_noext", cfg(BCFG, drange="swingnoext")),
    ("R06_drange_roll20", cfg(BCFG, drange="roll20")),
    ("R07_drange_roll50", cfg(BCFG, drange="roll50")),
    ("R08_pd_proximal_edge", cfg(BCFG, pd_point="prox")),
    ("R09_killzone_fixed_utc", cfg(BCFG, kz="utc")),
    ("R10_target_first_ge2R", cfg(BCFG, target="first2r")),
    ("R11_zone_N5", cfg(BCFG, zone_N=5)),
    ("R12_B_roll30+last_swing", cfg(BCFG, drange="roll30", target="last")),
]
AB = cfg(ACFG, bias_filter=True)
EXTRA += [("AB+B_" + n, cfg(AB, **{k: BCFG[k] for k in fl})) for n, fl in DIFFS if n != "D01_no_bias_filter"]


def main_dev(only=None):
    rows = []; funs = {}
    todo = [("B_REF", BCFG), ("A_REF", ACFG)]
    todo += [(n, cfg(BCFG, **fl)) for n, fl in DIFFS]                      # B with ONE A-choice
    todo += [("A+B_" + n, cfg(ACFG, **{k: BCFG[k] for k in fl})) for n, fl in DIFFS]   # A with ONE B-choice
    todo += list(EXTRA)
    if only:
        todo = [x for x in todo if x[0] in only]
    for name, C in todo:
        t0 = time.time()
        t, fun = run(C, "DEV", tag=name if name in ("B_REF", "A_REF") else None)
        s = summ(t, name); s["orders"] = fun.get("12_orders", 0)
        rows.append(s); funs[name] = fun
        print(f"{name:34s} n={s['trades']:4d} gross={s.get('gross_R')} net={s.get('net_R')} t={s.get('t_net')} "
              f"({time.time() - t0:.0f}s)", flush=True)
        df = pd.DataFrame(rows)
        df.to_csv(os.path.join(OUT, "recon_DEV.csv" if not only else "recon_DEV_extra.csv"), index=False)
        with open(os.path.join(OUT, "funnels_DEV.json" if not only else "funnels_DEV_extra.json"), "w") as fh:
            json.dump(funs, fh, indent=1)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if cmd == "dev":
        main_dev(sys.argv[2:] or None)


# ------------------------------------------------------------------------------------------------ multiverse
# every combination below is a defensible reading of the REF spec (A-only bugs / non-faithful choices excluded:
# missing bias filter, last-swing-even-if-broken target, A's entry zone, overlap touch, HL ATR, no dedupe)
SPACE = dict(bias_checks=["all3", "creation"], zone_N=[3, 4], ob_def=["B", "core"], ob_premit=[True, False],
             fvg_def=["B", "A"], expiry=["bars", "clock"], choch_win=["incl", "after"], pd_point=["mid", "prox"],
             drange=["swing", "swing5", "swingnoext", "roll20", "roll30", "roll50"], kz=["local", "utc"],
             target=["nearest", "first2r"])
MAJOR = ["drange", "kz", "target", "bias_checks"]


def multiverse(n_random=400, seed=0):
    import itertools
    rng = np.random.default_rng(seed)
    confs = []
    for combo in itertools.product(*[SPACE[k] for k in MAJOR]):          # full grid on the 4 major axes, minor = B
        confs.append(("grid", dict(zip(MAJOR, combo))))
    for _ in range(n_random):                                            # uniform random over the whole space
        confs.append(("rand", {k: v[rng.integers(len(v))] for k, v in SPACE.items()}))
    rows = []
    f = os.path.join(OUT, "multiverse_DEV.csv")
    for i, (kind, fl) in enumerate(confs):
        C = cfg(BCFG, **fl)
        t, fun = run(C, "DEV")
        s = summ(t, f"{kind}{i}")
        rows.append({**{k: str(C[k]) for k in SPACE}, "sample": kind, **{k: s.get(k) for k in
                     ("trades", "gross_R", "net_R", "total_net_R", "PF", "t_net", "pairs_pos", "LDN", "NY")}})
        if i % 20 == 0:
            pd.DataFrame(rows).to_csv(f, index=False); print(i, rows[-1], flush=True)
    pd.DataFrame(rows).to_csv(f, index=False)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "multiverse":
    multiverse()
