"""AUT12 stages: autopsy on EXPLORE, then the frozen refinements through VALIDATE and STRESS.
Part 2 of bt/aut12_clock.py (imports it). Writes only results/AUT12_clock/.

    .venv\\Scripts\\python.exe smc_bot/bt/aut12_stages.py autopsy     # EXPLORE only: picks R1, R2, R3 by the pre-registered rule
    .venv\\Scripts\\python.exe smc_bot/bt/aut12_stages.py ladder      # EXPLORE shape question (diagnostic, not carried)
    .venv\\Scripts\\python.exe smc_bot/bt/aut12_stages.py final       # frozen R1, R2, R3 through VALIDATE and STRESS
"""
import os
import sys
import json
import time

os.environ.setdefault("SMC_DATASET", "duka")
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aut12_clock as K                                                   # noqa: E402
from aut12_clock import C, A, OUT, SYMS, BASE_SHAPE, EVENTS, edge_row, usd_gross, pick, sim_events, events_all  # noqa: E402


# ---------------------------------------------------------------- features attached to the picked trades
def decorate(tr, ev, L):
    f = ev.set_index("eid")
    add = [c for c in f.columns if c not in tr.columns]
    t = tr.merge(f[add], left_on="eid", right_index=True, how="left")
    sd = t.side.values.astype(float)
    mv = t["move%d" % L].values
    t["f_move_atr"] = np.abs(mv) / t.atr15.values
    t["f_eff"] = t["eff%d" % L].values
    t["f_h1_agree"] = (t.f_h1_rel.values == sd).astype(float)
    t["f_h4_agree"] = (t.f_h4_rel.values == sd).astype(float)
    t["f_h1_stretch"] = t.f_h1_dist.values * sd
    t["f_last5_agree"] = (np.sign(t.move5.values) == sd).astype(float)
    t["f_prevday_agree"] = (np.sign(t.prev_day_ret.values) == sd).astype(float)
    t["f_move_vs_med"] = t["f_move_vs_med20_%d" % L].values
    t["f_us_summer"] = t.month.isin([4, 5, 6, 7, 8, 9, 10]).astype(float)
    return t


# (catalogue group, text, column, kind)   gt = above the frozen EXPLORE median, bool = flag, one = ratio above 1, gt20 = above 0.20
COND_LIST = [
    ("D1", "pre-move is BIG in ATR (above the frozen EXPLORE median)", "f_move_atr", "gt"),
    ("A3", "pre-move is SMOOTH, efficiency above the frozen median", "f_eff", "gt"),
    ("A1", "H1 50-EMA is on the trade side", "f_h1_agree", "bool"),
    ("A1", "H4 50-EMA is on the trade side", "f_h4_agree", "bool"),
    ("A7", "trade side already stretched from the H1 mean (above frozen median)", "f_h1_stretch", "gt"),
    ("A4", "ATR high against its own 60 days (above frozen median)", "f_atr_vs_med", "gt"),
    ("A5", "ATR expanding (ratio above 1)", "f_atr_dir", "one"),
    ("A6", "a lot of the day's usual range already spent (above frozen median)", "f_range_spent", "gt"),
    ("A8", "event falls on a Friday", "f_friday", "bool"),
    ("B6", "month-end window (day 27 to day 2)", "f_monthend", "bool"),
    ("D", "the last 5 minutes agree with the trade side", "f_last5_agree", "bool"),
    ("B4", "round trip over 20 percent of one ATR (the AUT09 skip line)", "f_cost_share", "gt20"),
    ("A8", "the previous day's move was in the trade direction", "f_prevday_agree", "bool"),
    ("D1", "pre-move big against its own last 20 (ratio above 1)", "f_move_vs_med", "one"),
    ("B", "US summer time (April to October)", "f_us_summer", "bool"),
]
KIND = {c: k for (_, _, c, k) in COND_LIST}


def cond_mask(t, col, thr=None):
    v = t[col].values
    k = KIND[col]
    with np.errstate(invalid="ignore"):
        if k == "gt":
            m = v > thr[col]
        elif k == "bool":
            m = v > 0.5
        elif k == "one":
            m = v > 1.0
        else:
            m = v > 0.20
    return np.where(np.isfinite(v), m, False)


def freeze_thresholds(t):
    return {col: float(np.nanmedian(t[col].values)) for (_, _, col, k) in COND_LIST if k == "gt"}


def cond_scan(t, thr):
    allrow = edge_row(t)
    rows = []
    for cid, text, col, kind in COND_LIST:
        m = cond_mask(t, col, thr)
        on, off = t[m], t[~m]
        r = dict(cat=cid, cond=text, col=col, kept=int(m.sum()), kept_pct=round(100 * m.mean(), 1))
        if len(on) >= 25 and len(off) >= 25:
            a, b = edge_row(on), edge_row(off)
            r.update(acc_on=a["right_in_100"], acc_off=b["right_in_100"], coin_acc_on=a["coin_right_in_100"],
                     usd_on=a["usd_per_trade"], usd_off=b["usd_per_trade"], edge_on=a["edge_over_coin_usd"],
                     edge_off=b["edge_over_coin_usd"], give_or_take_on=a["give_or_take_usd"],
                     lift_edge=round(a["edge_over_coin_usd"] - allrow["edge_over_coin_usd"], 3))
        rows.append(r)
    return pd.DataFrame(rows)


def dst_sanity():
    """The DATA (08:30 New York) minute should be the busiest minute in summer AND winter if the clock is right."""
    b = K.bars("EURUSD", "M1")
    e = events_all()
    g = e[(e.sym == "EURUSD") & (e.ev == "DATA") & (e.stage == "EXPLORE")]
    rng = (b.high.values - b.low.values) / C.pip("EURUSD")
    out = []
    for lab, mask in (("US summer (EDT)", g.month.isin([4, 5, 6, 7, 8, 9, 10])), ("US winter (EST)", ~g.month.isin([4, 5, 6, 7, 8, 9, 10]))):
        h = g[mask]
        iE = h.iE.values
        row = {"season": lab, "events": len(h), "event_utc_hour": int(h.hour.mode().iloc[0]), "event_utc_minute": 30}
        for k in (-2, -1, 0, 1, 2):
            row["range_pips_E%+d" % k] = round(float(np.mean(rng[iE + k])), 2)
        row["range_pips_one_hour_earlier"] = round(float(np.mean(rng[iE - 60])), 2)
        out.append(row)
    return pd.DataFrame(out)


def breakeven_pooled(t, shape=BASE_SHAPE):
    num, den = 0.0, 0
    for s, g in t.groupby("sym"):
        be = A.breakeven_rate(s, float(g.atr15.median()), shape[0], shape[1], spread=float(g.spr_E.median()))
        num += be * len(g)
        den += len(g)
    return round(num / den, 2) if den else None


def autopsy_explore():
    ev = events_all()
    gx = pd.read_csv(os.path.join(OUT, "grid_EXPLORE.csv"))
    d = dst_sanity()
    d.to_csv(os.path.join(OUT, "dst_sanity.csv"), index=False)
    print(d.to_string())
    p = gx[(gx.scope == "POOLED") & (gx.trades >= 800)].sort_values("edge_over_coin_usd", ascending=False)
    r1 = p.iloc[0]
    r2 = p[(p.ev != r1.ev) | (p["mode"] != r1["mode"])].iloc[0]
    choice = {"R1": dict(ev=r1.ev, L=int(r1.L), mode=r1["mode"], hold=int(r1.hold)),
              "R2": dict(ev=r2.ev, L=int(r2.L), mode=r2["mode"], hold=int(r2.hold))}
    print(choice)
    ex = ev[ev.stage == "EXPLORE"]
    res = {}
    for nm, c in choice.items():
        sim = sim_events(ev, "EXPLORE", BASE_SHAPE, c["hold"])
        tr = pick(ex, sim, c["L"], c["mode"], events=[c["ev"]])
        t = decorate(tr, ev, c["L"])
        res[nm] = (t, freeze_thresholds(t))
    t1, thr1 = res["R1"]
    scan = cond_scan(t1, thr1)
    scan.to_csv(os.path.join(OUT, "conditions_R1_EXPLORE.csv"), index=False)
    print(scan[["cat", "cond", "kept", "acc_on", "acc_off", "usd_on", "usd_off", "edge_on", "edge_off", "lift_edge", "give_or_take_on"]].to_string())
    ok = scan[scan.kept >= 300].sort_values("edge_on", ascending=False)
    best = ok.iloc[0]
    choice["R3"] = dict(ev=choice["R1"]["ev"], L=choice["R1"]["L"], mode=choice["R1"]["mode"], hold=choice["R1"]["hold"],
                        cond_col=best.col, cond_text=best.cond, cond_thr=thr1.get(best.col))
    json.dump(dict(choice=choice, thresholds_R1=thr1), open(os.path.join(OUT, "frozen_choice.json"), "w"), indent=1)
    print("R3 =", choice["R3"])
    for nm in ("R1", "R2"):
        t, thr = res[nm]
        rows = []
        for xr, g in t.groupby("exit_reason"):
            rows.append(dict(exit_reason=xr, trades=len(g), pct=round(100 * len(g) / len(t), 1), usd_per_trade=round(g.usd_01.mean(), 3),
                             usd_coin=round(g.usd_coin.mean(), 3), mfe_R=round(g.mfe_R.mean(), 2), mae_R=round(g.mae_R.mean(), 2)))
        pd.DataFrame(rows).to_csv(os.path.join(OUT, "why_exit_%s_EXPLORE.csv" % nm), index=False)
        print(nm, "exit reasons\n", pd.DataFrame(rows).to_string())
        t["win"] = (t.net_pips > 0).astype(int)
        w = t.groupby("win")[["mfe_R", "mae_R", "f_move_atr", "f_eff", "hold_h", "usd_01", "usd_coin"]].mean().round(3)
        w.to_csv(os.path.join(OUT, "why_winners_losers_%s_EXPLORE.csv" % nm))
        print(w.to_string())
        for key in ("sym", "hour", "year", "dow"):
            g = pd.DataFrame([{key: k, **edge_row(x)} for k, x in t.groupby(key)])
            g.to_csv(os.path.join(OUT, "why_by_%s_%s_EXPLORE.csv" % (key, nm)), index=False)
            print(nm, key, "\n", g[[key, "trades", "right_in_100", "usd_per_trade", "coin_usd", "edge_over_coin_usd", "give_or_take_usd"]].to_string())
        gg = pd.DataFrame([dict(sym=s, trades=len(x), gross_after_spread_usd=float(usd_gross(x).mean()),
                                commission_usd=float((x.usd_01 - usd_gross(x)).mean()), net_usd=x.usd_01.mean(),
                                coin_net_usd=x.usd_coin.mean()) for s, x in t.groupby("sym")]).round(3)
        gg.to_csv(os.path.join(OUT, "why_cost_%s_EXPLORE.csv" % nm), index=False)
        print(gg.to_string())
    return choice, res


# ---------------------------------------------------------------- EXPLORE shape question (diagnostic, not carried)
def ladder():
    ev = events_all()
    ch = json.load(open(os.path.join(OUT, "frozen_choice.json")))["choice"]
    rows = []
    ex = ev[ev.stage == "EXPLORE"]
    for nm in ("R1", "R2"):
        c = ch[nm]
        for (tg, sp) in A.ATR_LADDER:
            sim = sim_events(ev, "EXPLORE", (tg, sp), c["hold"], events=[c["ev"]])
            tr = pick(ex, sim, c["L"], c["mode"], events=[c["ev"]])
            r = edge_row(tr)
            rows.append(dict(variant=nm, ev=c["ev"], L=c["L"], mode=c["mode"], hold=c["hold"], tgt=tg, stop=sp,
                             **{k: r[k] for k in ("trades", "right_in_100", "coin_right_in_100", "usd_per_trade", "coin_usd",
                                                  "edge_over_coin_usd", "give_or_take_usd", "PF")}))
        print(nm, "done", flush=True)
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(OUT, "ladder_EXPLORE.csv"), index=False)
    for nm in ("R1", "R2"):
        x = d[d.variant == nm].sort_values("usd_per_trade", ascending=False)
        print(nm, "best by dollars\n", x.head(6).to_string())
        x = d[d.variant == nm].sort_values("edge_over_coin_usd", ascending=False)
        print(nm, "best by edge over coin\n", x.head(6).to_string())
    return d


# ---------------------------------------------------------------- the frozen refinements through the stages
def luck_cached(sym, stage, hours, hold, shape, seeds=(0, 1, 2, 3, 4), n=20000):
    os.makedirs(os.path.join(OUT, "cache"), exist_ok=True)
    f = os.path.join(OUT, "cache", "luck_%s_%s_%s_h%d_%g_%g.json" % (sym, stage, "-".join(str(h) for h in hours), hold, shape[0], shape[1]))
    if os.path.exists(f):
        return json.load(open(f))
    acc, usd, cnt = [], [], []
    for sd in seeds:
        lb = A.luck_bar(sym, n, shape[0], shape[1], stage, seed=sd, hours=list(hours), max_hold_min=hold)
        lb = lb[lb.status == "filled"]
        acc.append(100 * (lb.net_pips > 0).mean())
        usd.append(lb.usd_01.mean())
        cnt.append(len(lb))
    out = dict(right_in_100=float(np.mean(acc)), usd=float(np.mean(usd)), band=float(max(acc) - min(acc)), n=int(np.mean(cnt)),
               seed_values=[round(a, 2) for a in acc], seeds=list(seeds))
    json.dump(out, open(f, "w"))
    return out


def rule_trades(ev, stage, c, shape=BASE_SHAPE, early=0, thr=None):
    """The frozen rule on one stage. c = dict(ev, L, mode, hold[, cond_col]). Returns decorated trades and the sim table."""
    es = ev[ev.stage == stage]
    sim = sim_events(ev, stage, shape, c["hold"], early=early)
    tr = pick(es, sim, c["L"], c["mode"], events=[c["ev"]])
    t = decorate(tr, ev, c["L"])
    if c.get("cond_col"):
        m = cond_mask(t, c["cond_col"], thr)
        t = t[m].copy()
    return t, sim


def stage_block(ev, stage, c, thr, with_luck=True, shape=BASE_SHAPE):
    t, sim = rule_trades(ev, stage, c, shape, thr=thr)
    d = dict(stage=stage, **edge_row(t), **K.rand_band(t), **K.side_split(t))
    d["breakeven_needed"] = breakeven_pooled(t, shape) if len(t) else None
    eids = set(t.eid.values)
    for nm, sdv in (("all_long", 1), ("all_short", -1)):
        g = sim[(sim.side == sdv) & sim.eid.isin(eids)]
        d[nm + "_right_in_100"] = round(100 * (g.net_pips > 0).mean(), 2)
        d[nm + "_usd_per_trade"] = round(g.usd_01.mean(), 3)
    byi = []
    for s in SYMS:
        g = t[t.sym == s]
        if len(g):
            r = edge_row(g)
            r.update(K.side_split(g))
            r["sym"] = s
            byi.append(r)
    d["by_instrument"] = byi
    if with_luck and len(t):
        num_a = num_u = 0.0
        band = 0.0
        det = {}
        for s in SYMS:
            g = t[t.sym == s]
            if len(g) == 0:
                continue
            hrs = sorted(int(h) for h in pd.to_datetime(g.t_signal).dt.hour.unique())
            lb = luck_cached(s, stage, hrs, c["hold"], shape)
            det[s] = dict(hours=hrs, right_in_100=round(lb["right_in_100"], 2), usd=round(lb["usd"], 3), seed_band=round(lb["band"], 2), n=lb["n"])
            num_a += lb["right_in_100"] * len(g)
            num_u += lb["usd"] * len(g)
            band = max(band, lb["band"])
        d["luck_bar"] = round(num_a / len(t), 2)
        d["luck_bar_usd"] = round(num_u / len(t), 3)
        d["luck_bar_seed_band_points"] = round(band, 2)
        d["vs_luck_points"] = round(d["right_in_100"] - d["luck_bar"], 2)
        d["vs_luck_usd"] = round(d["usd_per_trade"] - d["luck_bar_usd"], 3)
        d["luck_detail"] = det
    tt, _ = rule_trades(ev, stage, c, shape, early=1, thr=thr)
    j = t[["eid", "net_pips", "usd_01"]].merge(tt[["eid", "net_pips", "usd_01"]], on="eid", suffixes=("", "_tw"))
    d["lookahead"] = dict(pairs=len(j),
                          clean_right_in_100=round(100 * (j.net_pips > 0).mean(), 2),
                          twin_right_in_100=round(100 * (j.net_pips_tw > 0).mean(), 2),
                          points=round(100 * ((j.net_pips_tw > 0).mean() - (j.net_pips > 0).mean()), 2),
                          clean_usd=round(j.usd_01.mean(), 3), twin_usd=round(j.usd_01_tw.mean(), 3),
                          usd=round(j.usd_01_tw.mean() - j.usd_01.mean(), 3))
    return d, t


def stress_adverse(ev, c, thr, mults=(1.0, 2.0, 3.0)):
    """STRESS stage with the spread multiplied. Barriers hang off the SIGNAL price, so a worse fill really costs."""
    rows = []
    for m in mults:
        K.set_mult(m)
        try:
            es = ev[ev.stage == "STRESS"]
            sim = sim_events(ev, "STRESS", BASE_SHAPE, c["hold"])
            tr = pick(es, sim, c["L"], c["mode"], events=[c["ev"]])
            t = decorate(tr, ev, c["L"])
            if c.get("cond_col"):
                t = t[cond_mask(t, c["cond_col"], thr)]
            r = edge_row(t)
            rows.append(dict(spread_multiple=m, trades=r["trades"], right_in_100=r["right_in_100"], usd_per_trade=r["usd_per_trade"],
                             coin_usd=r["coin_usd"], edge_over_coin_usd=r["edge_over_coin_usd"], PF=r["PF"],
                             gold_round_trip_usd=round(1.10 + 0.5 * m, 2)))
        finally:
            K.set_mult(1.0)
    return rows


def final():
    ev = events_all()
    fc = json.load(open(os.path.join(OUT, "frozen_choice.json")))
    ch, thr = fc["choice"], fc["thresholds_R1"]
    res = {}
    for nm in ("R1", "R2", "R3"):
        c = ch[nm]
        res[nm] = dict(rule=c, stages={})
        allt = []
        for st in K.STAGE3:
            t0 = time.time()
            d, t = stage_block(ev, st, c, thr)
            res[nm]["stages"][st] = d
            t = t.copy()
            t["period"] = st
            allt.append(t)
            print(nm, st, d["trades"], d["right_in_100"], d.get("luck_bar"), d["usd_per_trade"], d["coin_usd"], d["edge_over_coin_usd"],
                  d["give_or_take_usd"], "%.0fs" % (time.time() - t0), flush=True)
        res[nm]["stress_adverse"] = stress_adverse(ev, c, thr)
        print(nm, "adverse", res[nm]["stress_adverse"], flush=True)
        a = pd.concat(allt, ignore_index=True)
        a["status"] = "filled"
        a["variant"] = nm
        a.to_csv(os.path.join(OUT, "trades_%s_ALL.csv" % nm), index=False)
        for st in K.STAGE3:
            a[a.period == st].to_csv(os.path.join(OUT, "trades_%s_%s.csv" % (nm, st)), index=False)
        json.dump(res, open(os.path.join(OUT, "final_results.json"), "w"), indent=1,
                  default=lambda x: float(x) if isinstance(x, (np.floating, np.integer)) else str(x))
    return res


def baselines():
    """EXPLORE-only baselines for the events NOT carried (best EXPLORE cell per event; VALIDATE/STRESS never touched for them)."""
    ev = events_all()
    gx = pd.read_csv(os.path.join(OUT, "grid_EXPLORE.csv"))
    out = {}
    for name in ("LFIX_AM", "DATA", "NFP"):
        p = gx[(gx.scope == "POOLED") & (gx.ev == name) & (gx.trades >= 200)].sort_values("edge_over_coin_usd", ascending=False)
        r = p.iloc[0]
        c = dict(ev=name, L=int(r.L), mode=r["mode"], hold=int(r.hold))
        d, t = stage_block(ev, "EXPLORE", c, {})
        t = t.copy()
        t["period"] = "EXPLORE"
        t["status"] = "filled"
        t.to_csv(os.path.join(OUT, "trades_E_%s_EXPLORE.csv" % name), index=False)
        # the whole 16-cell family for this event (what the fix attempts were)
        fam = gx[(gx.scope == "POOLED") & (gx.ev == name)][["L", "mode", "hold", "trades", "right_in_100", "coin_right_in_100", "usd_per_trade",
                                                           "coin_usd", "edge_over_coin_usd", "give_or_take_usd"]]
        fam.to_csv(os.path.join(OUT, "family_%s_EXPLORE.csv" % name), index=False)
        out[name] = dict(rule=c, explore=d)
        print(name, c, d["trades"], d["right_in_100"], d.get("luck_bar"), d["usd_per_trade"], d["coin_usd"], d["edge_over_coin_usd"], d["give_or_take_usd"], flush=True)
    json.dump(out, open(os.path.join(OUT, "baselines_results.json"), "w"), indent=1,
              default=lambda x: float(x) if isinstance(x, (np.floating, np.integer)) else str(x))
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "autopsy"
    if cmd == "baselines":
        baselines()
    elif cmd == "ladder":
        ladder()
    elif cmd == "final":
        final()
    elif cmd == "autopsy":
        autopsy_explore()
