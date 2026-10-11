"""SMC backtest: produce every rule-valid setup with its features and its REAL outcome (Zero-account costs).
Usage: python smc_bot/backtest.py EURUSD [N_ltf N_htf]      -> smc_bot/out/setups_EURUSD.parquet
"""
import os, sys, time
import numpy as np
import pandas as pd

os.environ.setdefault("DATA_DIR", "data_zero")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "research"))
from lib import load, pip_size
from smc_core import resample, ema, atr, structure, build_zones, first_touch

# real Zero commission, round trip, in pips (gold: $0.10 units) - measured 2026-09-27
COMM = dict(EURUSD=0.50, GBPUSD=0.63, USDJPY=0.79, AUDUSD=0.60, USDCAD=0.99, NZDUSD=0.90, EURGBP=0.83, EURJPY=1.10,
            USDCHF=0.87, XAUUSD=1.10)
CFG = dict(N_h1=5, N_m30=3, N_m15=3, N_m5=3, zone_age_m30=96, wait_choch=36, order_wait_m1=60, min_rr=2.0,
           sl_buf_atr=0.1, stop_slip=0.2, session=(7, 19), cancel_on_close_through=True)


def run(sym, cfg=CFG, start="2021-01-01"):
    t0 = time.time()
    p = pip_size(sym)
    m1 = load(sym, start)
    m1["spr"] = m1.spr.clip(lower=0) * p                   # price units
    bars = {tf: resample(m1, tf) for tf in ("M5", "M15", "M30", "H1")}
    h1, m30, m15, m5 = bars["H1"], bars["M30"], bars["M15"], bars["M5"]
    st = {"H1": structure(h1, cfg["N_h1"]), "M30": structure(m30, cfg["N_m30"]), "M15": structure(m15, cfg["N_m15"]),
          "M5": structure(m5, cfg["N_m5"])}
    ema200_h1, ema50_m30, ema20_m5 = ema(h1.close.values, 200), ema(m30.close.values, 50), ema(m5.close.values, 20)
    atr_h1, atr_m30, atr_m5 = atr(h1), atr(m30), atr(m5)
    zones = build_zones(m30, st["M30"], cfg["zone_age_m30"])
    touch = first_touch(zones, m5)
    zones["touch"] = touch
    zones = zones[zones.touch >= 0].sort_values("touch").reset_index(drop=True)

    tc5 = m5.tclose.values
    def asof(tf_bars, t):                                  # index of last TF bar CLOSED at or before t
        return np.searchsorted(tf_bars.tclose.values, t, "right") - 1
    ih1_all, im30_all, im15_all = asof(h1, tc5), asof(m30, tc5), asof(m15, tc5)

    t1 = m1.time.values; o1, h1a, l1, c1, s1 = (m1[x].values for x in ("open", "high", "low", "close", "spr"))
    dow1 = m1.time.dt.dayofweek.values; hm1 = (m1.time.dt.hour * 60 + m1.time.dt.minute).values
    hi5, lo5, cl5 = m5.high.values, m5.low.values, m5.close.values
    evt5, leg5, oblo5, obhi5 = st["M5"]["evt"], st["M5"]["leg_i"], st["M5"]["ob_lo"], st["M5"]["ob_hi"]
    # M5 FVGs for entry zones
    fb = np.zeros(len(m5), bool); fs = np.zeros(len(m5), bool)
    fb[2:] = lo5[2:] > hi5[:-2]; fs[2:] = hi5[2:] < lo5[:-2]
    sh_h1 = np.where(st["H1"]["is_sh"])[0]; sl_h1 = np.where(st["H1"]["is_sl"])[0]
    Nh = cfg["N_h1"]
    hh1, ll1 = h1.high.values, h1.low.values

    def h1_target(d, entry, ih):
        """Nearest unbroken, confirmed 1H swing beyond entry in trade direction (liquidity)."""
        cand = sh_h1 if d == 1 else sl_h1
        cand = cand[(cand + Nh <= ih)][-15:]                  # confirmed by the last closed H1 bar
        best = np.nan
        for s in cand[::-1]:
            lvl = hh1[s] if d == 1 else ll1[s]
            if d == 1 and lvl > entry and hh1[s + 1:ih + 1].max(initial=-np.inf) < lvl:
                best = lvl if np.isnan(best) else min(best, lvl)
            if d == -1 and lvl < entry and ll1[s + 1:ih + 1].min(initial=np.inf) > lvl:
                best = lvl if np.isnan(best) else max(best, lvl)
        return best

    def bias_at(j):
        ih, im = ih1_all[j], im30_all[j]
        if ih < 210 or im < 60:
            return 0, {}
        th, tm = st["H1"]["trend"][ih], st["M30"]["trend"][im]
        info = dict(h1_trend=th, m30_trend=tm, h1_ema_dist=(h1.close.values[ih] - ema200_h1[ih]) / atr_h1[ih],
                    m30_ema_dist=(m30.close.values[im] - ema50_m30[im]) / atr_m30[im], m15_trend=st["M15"]["trend"][im15_all[j]])
        if th != 0 and th == tm:
            return int(th), info
        return 0, info

    busy_until = -1                    # M5 index until which this pair is occupied (watch/order/trade)
    out = []
    for zi in range(len(zones)):
        z = zones.iloc[zi]
        j = int(z.touch)
        if j <= busy_until:
            continue
        d = int(z.dir)
        bias, info = bias_at(j)
        hr = pd.Timestamp(tc5[j]).hour; wd = pd.Timestamp(tc5[j]).dayofweek
        if bias != d or not (cfg["session"][0] <= hr < cfg["session"][1]) or wd >= 5 or (wd == 4 and hr >= 17):
            continue
        ih = ih1_all[j]
        rng_hi, rng_lo = st["H1"]["lsh"][ih], st["H1"]["lsl"][ih]
        zmid = (z.lo + z.hi) / 2
        pos_in_range = (zmid - rng_lo) / (rng_hi - rng_lo) if rng_hi > rng_lo else np.nan
        im = im30_all[j]
        # liquidity sweep: did the touch bar / watch window take out the last 30M swing beyond the zone?
        m30_swing = st["M30"]["lsl"][im] if d == 1 else st["M30"]["lsh"][im]
        # ---- WATCH: wait for 5M CHoCH in bias direction
        ext = lo5[j] if d == 1 else hi5[j]
        setup = None
        for k in range(j, min(j + cfg["wait_choch"] + 1, len(m5) - 1)):
            ext = min(ext, lo5[k]) if d == 1 else max(ext, hi5[k])
            if cfg["cancel_on_close_through"] and ((d == 1 and cl5[k] < z.lo) or (d == -1 and cl5[k] > z.hi)):
                break
            if bias_at(k)[0] != d:
                break
            e = evt5[k]
            if (d == 1 and e == 2) or (d == -1 and e == -2):       # CHoCH in our direction
                setup = k; break
        if setup is None:
            busy_until = max(busy_until, j)
            continue
        k = setup
        # ---- entry zone: newest 5M FVG inside the CHoCH leg, else the leg's 5M OB
        lg = leg5[k]
        fv = np.where((fb if d == 1 else fs)[lg + 2:k + 1])[0]
        if len(fv):
            q = lg + 2 + fv[-1]
            ez_lo, ez_hi, ekind = (hi5[q - 2], lo5[q], "FVG") if d == 1 else (hi5[q], lo5[q - 2], "FVG")
        else:
            ez_lo, ez_hi, ekind = oblo5[k], obhi5[k], "OB"
        buf = cfg["sl_buf_atr"] * atr_m5[k]
        if d == 1:
            entry = ez_hi; sl = min(ez_lo, ext) - buf
        else:
            entry = ez_lo; sl = max(ez_hi, ext) + buf
        ihk = ih1_all[k]
        tp = h1_target(d, entry, ihk)
        risk = abs(entry - sl)
        if np.isnan(tp) or risk <= 0 or np.isnan(atr_m5[k]):
            busy_until = max(busy_until, k); continue
        rr = abs(tp - entry) / risk
        feats = dict(sym=sym, t_setup=pd.Timestamp(tc5[k]), dir=d, zone_kind=z.kind, entry_kind=ekind, hour=hr, dow=wd,
                     zone_size_atr=(z.hi - z.lo) / atr_m30[im], zone_age_h=(tc5[j] - z.t_created) / np.timedelta64(1, "h"),
                     pos_in_h1_range=pos_in_range, discount_ok=(pos_in_range <= 0.5) if d == 1 else (pos_in_range >= 0.5),
                     swept=bool((ext < m30_swing) if d == 1 else (ext > m30_swing)) if not np.isnan(m30_swing) else False,
                     bars_to_choch=k - j, rr=rr, risk_pips=risk / p, risk_atr5=risk / atr_m5[k], tp_atr_h1=abs(tp - entry) / atr_h1[ihk],
                     m5_ema_ok=bool((cl5[k] > ema20_m5[k]) if d == 1 else (cl5[k] < ema20_m5[k])),
                     h1_ema_ok=bool(info.get("h1_ema_dist", 0) * d > 0), m30_ema_ok=bool(info.get("m30_ema_dist", 0) * d > 0),
                     h1_ema_dist=info.get("h1_ema_dist", np.nan) * d, m30_ema_dist=info.get("m30_ema_dist", np.nan) * d,
                     m15_agree=int(info.get("m15_trend", 0) == d), atr5_pips=atr_m5[k] / p, atr_h1_pips=atr_h1[ihk] / p,
                     entry=entry, sl=sl, tp=tp)
        if rr < cfg["min_rr"]:
            busy_until = max(busy_until, k)
            out.append({**feats, "status": "rr_too_low", "filled": False, "r_net": np.nan, "net_pips": np.nan})
            continue
        # ---- ORDER + TRADE on M1 (limit entry; SL-first when ambiguous; flat before weekend)
        m = np.searchsorted(t1, tc5[k], "left")
        fill_m, fill_px = -1, np.nan
        for mm in range(m, min(m + cfg["order_wait_m1"], len(t1))):
            ask_lo, bid_hi, bid_lo = l1[mm] + s1[mm], h1a[mm], l1[mm]
            if d == 1:
                if bid_hi >= tp: break                                  # ran to target without us
                if ask_lo <= entry: fill_m, fill_px = mm, min(entry, o1[mm] + s1[mm]); break
            else:
                if bid_lo + s1[mm] <= tp: break
                if bid_hi >= entry: fill_m, fill_px = mm, max(entry, o1[mm]); break
        if fill_m < 0:
            busy_until = max(busy_until, k + cfg["order_wait_m1"] // 5)
            out.append({**feats, "status": "not_filled", "filled": False, "r_net": np.nan, "net_pips": np.nan})
            continue
        slip = cfg["stop_slip"] * p
        ex_px, why, mm = np.nan, "", fill_m
        while mm < len(t1):
            if d == 1:
                if l1[mm] <= sl: ex_px, why = min(sl, o1[mm] if mm > fill_m else sl) - slip, "SL"; break
                if h1a[mm] >= tp and mm > fill_m: ex_px, why = tp, "TP"; break
            else:
                if h1a[mm] + s1[mm] >= sl: ex_px, why = max(sl, o1[mm] + s1[mm] if mm > fill_m else sl) + slip, "SL"; break
                if l1[mm] + s1[mm] <= tp and mm > fill_m: ex_px, why = tp, "TP"; break
            if dow1[mm] == 4 and hm1[mm] >= 20 * 60 + 45:
                ex_px, why = (c1[mm] if d == 1 else c1[mm] + s1[mm]), "WEEKEND"; break
            mm += 1
        if np.isnan(ex_px):
            break
        gross = (ex_px - fill_px) * d / p
        net = gross - COMM[sym]
        r_pips = abs(fill_px - sl) / p
        out.append({**feats, "status": "traded", "filled": True, "fill": fill_px, "exit": ex_px, "exit_why": why,
                    "t_exit": pd.Timestamp(t1[mm]), "hold_h": (t1[mm] - t1[fill_m]) / np.timedelta64(1, "h"),
                    "net_pips": net, "r_net": net / r_pips if r_pips > 0 else np.nan})
        busy_until = np.searchsorted(tc5, t1[mm], "left")
    df = pd.DataFrame(out)
    print(f"{sym}: zones {len(zones)} touched | setups {len(df)} | traded {int((df.status == 'traded').sum()) if len(df) else 0} "
          f"| {time.time() - t0:.0f}s", flush=True)
    return df


if __name__ == "__main__":
    sym = sys.argv[1]
    df = run(sym)
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    df.to_parquet(os.path.join(HERE, "out", f"setups_{sym}.parquet"), index=False)
    t = df[df.status == "traded"]
    if len(t):
        yr = t.groupby(t.t_setup.dt.year).agg(n=("r_net", "size"), win=("r_net", lambda x: (x > 0).mean() * 100),
                                              avgR=("r_net", "mean"), pips=("net_pips", "mean"))
        print(yr.round(2).to_string())
        print(f"ALL: n={len(t)} win%={(t.r_net > 0).mean() * 100:.1f} avgR={t.r_net.mean():+.3f} net pips/trade={t.net_pips.mean():+.2f} "
              f"| exits: {t.exit_why.value_counts().to_dict()}")
