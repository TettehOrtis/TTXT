"""AUT15 Part B: re-run one picked variant at the cost menu, with the four controls.
Usage: python aut15_partB.py <order key> <view>      e.g.  AUT04__R0_all__best_4x4 GOLD
Writes results/AUT15_costcut/partB_<name>_*.{json,csv}.  Orders are the saved orders of the earlier study (stop and
target PRICES fixed, anchored to the signal), replayed through common.simulate with commission and spread scaled."""
import os
import sys
import json
import time

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut15_costcut as K                                                 # noqa: E402
import autopsy_lib as A                                                   # noqa: E402
import common as C                                                        # noqa: E402

OUT, WORK = K.OUT, K.WORK
FACS = K.FACTORS
STG = list(K.STAGE_NAMES)
VIEWS = {"GOLD": ["XAUUSD"], "ALL": None, "EURUSD": ["EURUSD"], "GBPUSD": ["GBPUSD"], "USDJPY": ["USDJPY"]}


def find_orders(key):
    for sub in ("orders", "orders_extra", "orders_extra2", "orders_extra3", "orders_extra4"):
        f = os.path.join(WORK, sub, key + ".parquet")
        if os.path.exists(f):
            return f
    raise FileNotFoundError(key)


def gt(x):
    """give or take: rough 95% range of a mean, trades treated as independent (they are not, so the true range is wider)"""
    x = np.asarray(x, float)
    return float(1.96 * x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else np.nan


def cell(u):
    u = np.asarray(u, float)
    return dict(n=int(len(u)), usd=float(u.mean()) if len(u) else np.nan, gt=gt(u) if len(u) > 1 else np.nan,
                win=float(100 * (u > 0).mean()) if len(u) else np.nan)


def run_menu(d):
    full = {}
    for f in FACS:
        t0 = time.time()
        r = K.resim_full(d, f)
        full[f] = r
        print("  menu f=%g  %d orders  %.0fs" % (f, len(r), time.time() - t0), flush=True)
    return full


def forced_run(d, side_fn, f):
    """Same entry minutes, same shape (stop and target distances re-anchored on the bar open), direction replaced."""
    outs = []
    for sym, g in d.groupby("sym", sort=False):
        K.set_cost(f)
        b = K._scaled_bars(sym, "M1")
        idx = np.searchsorted(b.time.values, pd.to_datetime(g.t_signal).values, "left")
        idx = np.clip(idx, 0, len(b) - 1)
        p0 = b.open.values[idx]
        d0 = g.side.values.astype(int)
        sl_d = (p0 - g.sl.values) * d0
        tp_d = (g.tp.values - p0) * d0
        nd = side_fn(g)
        o = g[["t_signal", "entry_type", "entry", "expire", "be_at_r", "max_hold_min"]].copy()
        o["side"] = nd
        o["sl"] = p0 - nd * sl_d
        o["tp"] = p0 + nd * tp_d
        o["oid"] = np.arange(len(o))
        o["stage"] = g.stage.values
        o["rside"] = d0
        r = C.simulate(sym, o, one_at_a_time=False)
        outs.append(r[r.status == "filled"])
    K.set_cost(1.0)
    return pd.concat(outs, ignore_index=True)


def controls(d, fs=(0.0, 1.0)):
    res = {}
    for f in fs:
        rows = {}
        for nm, fn in (("rule", lambda g: g.side.values.astype(int)),
                       ("all_long", lambda g: np.ones(len(g), int)),
                       ("all_short", lambda g: -np.ones(len(g), int))):
            r = forced_run(d, fn, f)
            for st in STG:
                rows.setdefault(st, {})[nm] = cell(r[r.stage == st].usd_01.values)
        coin = {st: [] for st in STG}
        for sd in (7, 8, 9):
            rg = np.random.default_rng(sd)
            r = forced_run(d, lambda g: rg.choice([1, -1], size=len(g)), f)
            for st in STG:
                coin[st].append(cell(r[r.stage == st].usd_01.values))
        for st in STG:
            rows[st]["coin_flip"] = dict(n=int(np.mean([c["n"] for c in coin[st]])), usd=float(np.mean([c["usd"] for c in coin[st]])),
                                         win=float(np.mean([c["win"] for c in coin[st]])), seeds=[7, 8, 9])
        res["%g" % f] = rows
    return res


def shape_stats(d):
    """per (sym, stage): target and stop in multiples of the M15 ATR, hold, and the hour-of-day histogram of the rule's entries"""
    out = {}
    K.set_cost(1.0)
    for sym, g in d.groupby("sym", sort=False):
        b = K._scaled_bars(sym, "M1")
        idx = np.clip(np.searchsorted(b.time.values, pd.to_datetime(g.t_signal).values, "left"), 0, len(b) - 1)
        d0 = g.side.values.astype(int)
        fill = b.open.values[idx] + np.where(d0 == 1, b.spr.values[idx], 0.0)
        at = A.atr_at(sym, "M15", pd.to_datetime(g.t_signal))
        tpm = (g.tp.values - fill) * d0 / at
        slm = (fill - g.sl.values) * d0 / at
        for st in STG:
            m = (g.stage.values == st) & np.isfinite(tpm) & np.isfinite(slm)
            if m.sum() == 0:
                continue
            hrs = pd.to_datetime(g.t_signal[m]).dt.hour.values
            out[(sym, st)] = dict(n=int(m.sum()), tp_mult=float(np.median(tpm[m])), sl_mult=float(np.median(slm[m])),
                                  hold=float(np.nanmedian(g.max_hold_min.values[m])),
                                  hours=np.bincount(hrs, minlength=24).astype(float))
    return out


def luck_one(sym, n, st, shp, seed, f):
    K.set_cost(f)
    b = K._scaled_bars(sym, "M1")
    lo, hi = A.STAGES[st]
    ok = ((b.time >= lo) & (b.time < hi)).values
    th = b.tclose.dt.hour.values
    rng = np.random.default_rng(seed)
    hist = shp["hours"] / shp["hours"].sum()
    nh = rng.multinomial(n, hist)
    picks = []
    for h in np.flatnonzero(nh):
        idx = np.flatnonzero(ok & (th == h))
        idx = idx[idx < len(b) - 3]
        if len(idx) == 0:
            continue
        picks.append(rng.choice(idx, size=nh[h], replace=len(idx) < nh[h]))
    pick = np.sort(np.concatenate(picks))
    side = rng.choice([1, -1], size=len(pick))
    tsig = b.tclose.values[pick]
    at = A.atr_at(sym, "M15", tsig)
    fill = b.open.values[pick + 1] + np.where(side == 1, b.spr.values[pick + 1], 0.0)
    o = pd.DataFrame({"t_signal": tsig, "side": side, "entry_type": "market", "entry": np.nan,
                      "expire": tsig, "sl": fill - side * at * shp["sl_mult"], "tp": fill + side * at * shp["tp_mult"],
                      "be_at_r": np.nan, "max_hold_min": shp["hold"]})
    o = o[np.isfinite(o.sl) & np.isfinite(o.tp)].reset_index(drop=True)
    r = C.simulate(sym, o, one_at_a_time=False)
    r = r[r.status == "filled"]
    K.set_cost(1.0)
    return r.usd_01.values


def luckbar(d, shp, fs=(0.0, 1.0), n=20000, seeds=(0, 1, 2, 3, 4)):
    res = {}
    for f in fs:
        per = {}
        for st in STG:
            keys = [(s, st) for (s, st2) in shp if st2 == st]
            tot = sum(shp[k]["n"] for k in keys)
            if tot == 0:
                continue
            means, wins = [], []
            for sd in seeds:
                us = []
                for k in keys:
                    nk = int(round(n * shp[k]["n"] / tot))
                    if nk < 20:
                        continue
                    us.append(luck_one(k[0], nk, st, shp[k], sd * 100 + 7, f))
                u = np.concatenate(us)
                means.append(float(u.mean()))
                wins.append(float(100 * (u > 0).mean()))
            per[st] = dict(n_per_seed=n, usd=float(np.mean(means)), usd_band=float(max(means) - min(means)),
                           win=float(np.mean(wins)), win_band=float(max(wins) - min(wins)), seeds=list(seeds))
        res["%g" % f] = per
    return res


def twin(d, fs=(0.0, 1.0)):
    res = {}
    e = d.copy()
    e["t_signal"] = pd.to_datetime(e.t_signal) - pd.Timedelta(minutes=1)
    e["expire"] = pd.to_datetime(e.expire) - pd.Timedelta(minutes=1)
    for f in fs:
        a = K.resim_full(d, f)
        b = K.resim_full(e, f)
        a, b = a[a.status == "filled"], b[b.status == "filled"]
        rows = {}
        for st in STG:
            sa = a[a.src_index.isin(d.index[d.stage == st])]
            sb = b[b.src_index.isin(d.index[d.stage == st])]
            rows[st] = dict(clean_n=int(len(sa)), twin_n=int(len(sb)), clean_usd=float(sa.usd_01.mean()), twin_usd=float(sb.usd_01.mean()),
                            clean_pips=float(sa.net_pips.mean()), twin_pips=float(sb.net_pips.mean()),
                            clean_win=float(100 * (sa.usd_01 > 0).mean()), twin_win=float(100 * (sb.usd_01 > 0).mean()))
            rows[st]["lookahead_usd"] = rows[st]["twin_usd"] - rows[st]["clean_usd"]
            rows[st]["lookahead_pips"] = rows[st]["twin_pips"] - rows[st]["clean_pips"]
            rows[st]["lookahead_win_points"] = rows[st]["twin_win"] - rows[st]["clean_win"]
        res["%g" % f] = rows
    return res


def main(key, view):
    t0 = time.time()
    name = "%s_%s" % (key.replace("__", "_"), view)
    d = pd.read_parquet(find_orders(key))
    syms = VIEWS[view]
    if syms is not None:
        d = d[d.sym.isin(syms)]
    d = d[d.stage.isin(STG)].reset_index(drop=True)
    print(name, "orders", len(d), flush=True)
    out = dict(name=name, key=key, view=view, orders=len(d), cost_menu_factors=FACS)

    full = run_menu(d)
    base = full[1.0]
    base = base[base.status == "filled"].copy()
    base["period"] = d.stage.values[base.src_index.values]
    base = base.sort_values("t_signal", kind="stable").reset_index(drop=True)
    for f in FACS:
        s = full[f].set_index("src_index").usd_01
        base["usd_menu_%g" % f] = s.reindex(base.src_index.values).values
    base.to_csv(os.path.join(OUT, "trades_%s_ALLSTAGES.csv" % name), index=False)

    rows = []
    for f in FACS:
        r = full[f]
        r = r[r.status == "filled"].copy()
        r["stage"] = d.stage.values[r.src_index.values]
        for st in STG:
            g = r[r.stage == st]
            for side_nm, sd in (("all", None), ("buy", 1), ("sell", -1)):
                h = g if sd is None else g[g.side == sd]
                c = cell(h.usd_01.values)
                rows.append(dict(factor=f, stage=st, side=side_nm, **c, pips=float(h.net_pips.mean()) if len(h) else np.nan))
            if d.sym.nunique() > 1:
                for s2, h in g.groupby("sym"):
                    c = cell(h.usd_01.values)
                    rows.append(dict(factor=f, stage=st, side="sym:" + s2, **c, pips=float(h.net_pips.mean())))
    menu = pd.DataFrame(rows)
    menu.to_csv(os.path.join(OUT, "partB_%s_menu.csv" % name), index=False)

    r0, r1 = full[0.0], full[1.0]
    m = r0.set_index("src_index").usd_01.to_frame("u0").join(r1.set_index("src_index").usd_01.rename("u1"))
    m["stage"] = d.stage.values[m.index.values]
    paid = {st: float((m[m.stage == st].u0 - m[m.stage == st].u1).mean()) for st in STG}
    out["cost_paid_today_usd"] = paid
    be = {}
    for st in STG:
        g = menu[(menu.stage == st) & (menu.side == "all")].set_index("factor")
        u = g.usd.reindex(FACS).values
        x = np.array(FACS)
        slope, icpt = np.polyfit(x, u, 1)
        fstar = -icpt / slope if slope < 0 else np.nan
        be[st] = dict(edge_before_cost_usd=float(g.usd[0.0]), net_today_usd=float(g.usd[1.0]), cost_paid_today=paid[st],
                      breakeven_factor=float(fstar), breakeven_cost_usd=float(fstar * paid[st]) if np.isfinite(fstar) else np.nan,
                      edge_gt=float(g["gt"][0.0]), line_slope=float(slope), usd_by_factor={("%g" % f): float(v) for f, v in zip(FACS, u)})
        cross = np.nan
        for i in range(len(FACS) - 1):
            ua, ub = u[i], u[i + 1]
            if ua <= 0 < ub or ua < 0 <= ub:
                cross = FACS[i] + (0 - ua) * (FACS[i + 1] - FACS[i]) / (ub - ua)
                break
        be[st]["breakeven_factor_from_rows"] = float(cross) if np.isfinite(cross) else None
    out["breakeven"] = be
    out["power_check_usd_differs_across_rows"] = {st: bool(len(set(np.round(list(be[st]["usd_by_factor"].values()), 4))) == len(FACS)) for st in STG}
    out["worst_stage_breakeven_cost_usd"] = float(min(be[st]["breakeven_cost_usd"] for st in STG))
    out["worst_stage"] = min(STG, key=lambda st: be[st]["breakeven_cost_usd"])
    json.dump(out, open(os.path.join(OUT, "partB_%s.json" % name), "w"), indent=1, default=float)
    print(" menu done", round(time.time() - t0), "s", flush=True)

    out["controls"] = controls(d)
    json.dump(out, open(os.path.join(OUT, "partB_%s.json" % name), "w"), indent=1, default=float)
    print(" controls done", round(time.time() - t0), "s", flush=True)
    shp = shape_stats(d)
    out["shape_used_for_luck_bar"] = {"%s|%s" % k: {x: v[x] for x in v if x != "hours"} for k, v in shp.items()}
    out["luck_bar"] = luckbar(d, shp)
    json.dump(out, open(os.path.join(OUT, "partB_%s.json" % name), "w"), indent=1, default=float)
    print(" luck done", round(time.time() - t0), "s", flush=True)
    out["lookahead_twin"] = twin(d)
    json.dump(out, open(os.path.join(OUT, "partB_%s.json" % name), "w"), indent=1, default=float)
    print(" ALL DONE", name, round(time.time() - t0), "s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
