"""AUT11 stage drivers: explore | validate | stress.   Run from TTXT root:
    set SMC_DATASET=duka
    .venv\\Scripts\\python.exe smc_bot/bt/aut11_stages.py explore
Everything here uses aut11_trend (engine, controls) and writes only into results/AUT11_trend/.
"""
import os
import sys
import json
import time
import warnings

os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut11_trend as T                                                   # noqa: E402

warnings.simplefilter("ignore")
OUT = T.OUT
FX, GOLD = T.FX, ["XAUUSD"]
OPP_RULES = list(T.RULES)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def fx(t):
    return t[t.sym != "XAUUSD"]


def gd(t):
    return t[t.sym == "XAUUSD"]


def save_trades(t, name):
    t.to_csv(os.path.join(OUT, name + ".csv"), index=False)


# ------------------------------------------------------------------ EXPLORE
def explore():
    stage = "EXPLORE"
    t0 = time.time()
    base_rows, ctrl, pair_rows, year_rows, trades = [], {}, [], [], {}
    for rule in T.RULES:
        for mode in T.EXITS:
            key = "%s_%s" % (rule, mode)
            t = T.run_rule(rule, mode, stage)
            trades[key] = t
            save_trades(t, "trades_EXPLORE_" + key)
            for inst, tt in (("FX24", fx(t)), ("GOLD", gd(t))):
                if len(tt) == 0:
                    continue
                b = T.book(tt)
                base_rows.append(dict(variant=key, rule=rule, exit=mode, instrument=inst, **b))
            pr = T.by(fx(t), "sym")
            pr.insert(0, "variant", key)
            pair_rows.append(pr)
            yr = fx(t).assign(year=fx(t).t_signal.dt.year)
            yy = T.by(yr, "year")
            yy.insert(0, "variant", key)
            year_rows.append(yy)
            log("baseline", key, T.book(fx(t))["trades"], T.book(fx(t))["usd_per_trade"])
    bl = pd.DataFrame(base_rows)
    bl.to_csv(os.path.join(OUT, "explore_baselines.csv"), index=False)
    pd.concat(pair_rows).to_csv(os.path.join(OUT, "explore_by_pair.csv"), index=False)
    pd.concat(year_rows).to_csv(os.path.join(OUT, "explore_by_year.csv"), index=False)
    # ---- controls, per variant (FX24 and gold separately)
    for key, t in trades.items():
        rule, mode = key.rsplit("_", 1)
        d = {}
        for inst, tt in (("FX24", fx(t)), ("GOLD", gd(t))):
            if len(tt) < 20:
                d[inst] = dict(note="too few trades for controls", trades=len(tt))
                continue
            tt = tt.reset_index(drop=True)
            d[inst] = dict(book=T.book(tt),
                           directions=T.controls(tt, rule, mode, stage),
                           luck=T.luck(tt, rule, mode, stage, n=20000))
            if inst == "FX24":
                d[inst]["lookahead"] = T.lookahead(rule, mode, stage, syms=FX)
            else:
                d[inst]["lookahead"] = T.lookahead(rule, mode, stage, syms=GOLD)
        ctrl[key] = d
        log("controls", key, d["FX24"]["directions"]["rule_usd"], d["FX24"]["luck"]["usd_per_trade"],
            d["FX24"]["lookahead"].get("lookahead_usd"))
        T.jdump("explore_controls.json", ctrl)
    # ---- condition scan on every OPP skeleton
    sc = []
    for rule in T.RULES:
        key = rule + "_OPP"
        s = T.scan(fx(trades[key]).reset_index(drop=True), key)
        sc.append(s)
    sc = pd.concat(sc, ignore_index=True)
    sc.to_csv(os.path.join(OUT, "explore_conditions.csv"), index=False)
    log("explore done in %.0fs" % (time.time() - t0))


# ------------------------------------------------------------------ refinement selection (rule fixed in advance)
def pick_refinements():
    """Stated before the scan was read:
       a condition qualifies if, over the 6 OPP skeletons (FX24, EXPLORE): it keeps >= 40% of trades on each, improves
       dollars per trade on >= 4 of 6, and its mean dollar lift is positive in BOTH halves of EXPLORE (before / after 2008-01-01).
       Rank qualifiers by the mean of (profit factor on kept trades - profit factor on all trades) across the 6 skeletons.
       Take the top 3."""
    sc = pd.read_csv(os.path.join(OUT, "explore_conditions.csv"))
    sc = sc[sc.note.isna()] if "note" in sc else sc
    rows = []
    for (cid, cond), g in sc.groupby(["cat", "cond"], sort=False):
        g = g.copy()
        g["dpf"] = g.pf_on - g.pf_all
        row = dict(cat=cid, cond=cond, skeletons=len(g), min_kept_pct=g.kept_pct.min(), n_improved=int((g.d_usd > 0).sum()),
                   mean_dpf=round(float(g.dpf.mean()), 3), mean_d_usd=round(float(g.d_usd.mean()), 3),
                   mean_d_usd_h1=round(float(g.d_usd_h1.mean()), 3), mean_d_usd_h2=round(float(g.d_usd_h2.mean()), 3))
        row["qualifies"] = bool(row["min_kept_pct"] >= 40 and row["n_improved"] >= 4 and row["mean_d_usd_h1"] > 0 and row["mean_d_usd_h2"] > 0)
        rows.append(row)
    r = pd.DataFrame(rows).sort_values("mean_dpf", ascending=False)
    r.to_csv(os.path.join(OUT, "explore_refinement_ranking.csv"), index=False)
    return r


# ------------------------------------------------------------------ refined variants (EXPLORE)
def refinements():
    """The qualifiers from pick_refinements(), at most 3, each as an entry gate with thresholds frozen from EXPLORE."""
    r = pick_refinements()
    q = r[r.qualifies].head(3)
    sc = pd.read_csv(os.path.join(OUT, "explore_conditions.csv"))
    out = []
    for n, row in enumerate(q.itertuples(index=False), start=1):
        g = sc[(sc.cat == row.cat) & (sc.cond == row.cond) & sc.feature.notna()]
        spec = dict(id="R%d" % n, cat=row.cat, cond=row.cond, feature=g.feature.iloc[0], op=g.op.iloc[0],
                    thr_by_skeleton={r_.skeleton.replace("_OPP", ""): float(r_.thr) for r_ in g.itertuples()})
        out.append(spec)
    return out


def refined_run(spec, rule, stage, mode="OPP", syms=None, smult=1.0, slipx=0.0, twin=False):
    thr = spec["thr_by_skeleton"].get(rule, 0.5)       # agree60 is always true on TS60: gate is a no-op there
    return T.run_rule(rule, mode, stage, syms=syms, entry_ok=T.entry_ok_from(spec["feature"], spec["op"], thr),
                      smult=smult, slipx=slipx, twin=twin)


def refine_explore():
    stage = "EXPLORE"
    specs = refinements()
    T.jdump("refinements_chosen.json", specs)
    log("refinements", [(s["id"], s["cond"]) for s in specs])
    rows, ctrl = [], {}
    for spec in specs:
        for rule in T.RULES:
            key = "%s_%s_OPP" % (spec["id"], rule)
            t = refined_run(spec, rule, stage)
            save_trades(t, "trades_EXPLORE_" + key)
            f_ = fx(t).reset_index(drop=True)
            g_ = gd(t)
            for inst, tt in (("FX24", f_), ("GOLD", g_)):
                if len(tt):
                    rows.append(dict(variant=key, refinement=spec["id"], rule=rule, instrument=inst, **T.book(tt)))
            d = {}
            if len(f_) >= 20:
                d = dict(book=T.book(f_), directions=T.controls(f_, rule, "OPP", stage), luck=T.luck(f_, rule, "OPP", stage, n=20000),
                         lookahead=T.lookahead(rule, "OPP", stage, entry_ok=T.entry_ok_from(spec["feature"], spec["op"], spec["thr_by_skeleton"].get(rule, 0.5)), syms=FX))
            ctrl[key] = d
            log("refined", key, len(f_), T.book(f_).get("usd_per_trade"), T.book(f_).get("profit_factor"))
    rf = pd.DataFrame(rows)
    rf.to_csv(os.path.join(OUT, "explore_refined.csv"), index=False)
    T.jdump("explore_refined_controls.json", ctrl)
    # best variant per refinement = highest EXPLORE profit factor with >= 150 FX trades (fixed rule)
    best = {}
    for spec in specs:
        g = rf[(rf.refinement == spec["id"]) & (rf.instrument == "FX24") & (rf.trades >= 150)]
        if len(g):
            best[spec["id"]] = g.sort_values("profit_factor", ascending=False).iloc[0].variant
    T.jdump("refinements_best.json", best)
    log("best", best)


# ------------------------------------------------------------------ VALIDATE and STRESS
def all_variants():
    """Every variant that exists, with its spec. key -> dict(rule, mode, spec)."""
    specs = {s["id"]: s for s in json.load(open(os.path.join(OUT, "refinements_chosen.json")))}
    v = {}
    for rule in T.RULES:
        for mode in T.EXITS:
            v["%s_%s" % (rule, mode)] = dict(rule=rule, mode=mode, spec=None)
    for sid, sp in specs.items():
        for rule in T.RULES:
            v["%s_%s_OPP" % (sid, rule)] = dict(rule=rule, mode="OPP", spec=sp)
    return v


def explore_gate():
    """Stated in advance: a variant advances to VALIDATE if its EXPLORE FX24 dollars per trade > 0 and profit factor > 1."""
    b = pd.read_csv(os.path.join(OUT, "explore_baselines.csv"))
    r = pd.read_csv(os.path.join(OUT, "explore_refined.csv"))
    z = pd.concat([b, r[r.columns.intersection(b.columns)]], ignore_index=True)
    z = z[z.instrument == "FX24"]
    ok = z[(z.usd_per_trade > 0) & (z.profit_factor > 1)].variant.tolist()
    # refinement R2 on TS60 is the baseline itself (gate always true): it is not a separate variant
    ok = [k for k in ok if k != "R2_TS60_OPP"]
    return ok


def run_variant(key, v, stage, **kw):
    if v["spec"] is None:
        return T.run_rule(v["rule"], v["mode"], stage, **kw)
    return refined_run(v["spec"], v["rule"], stage, mode=v["mode"], **kw)


def entry_ok_of(v):
    sp = v["spec"]
    return None if sp is None else T.entry_ok_from(sp["feature"], sp["op"], sp["thr_by_skeleton"].get(v["rule"], 0.5))


def stage_run(stage, keys):
    V = all_variants()
    rows, pair_rows, year_rows, ctrl = [], [], [], {}
    for key in keys:
        v = V[key]
        t = run_variant(key, v, stage)
        save_trades(t, "trades_%s_%s" % (stage, key))
        f_, g_ = fx(t).reset_index(drop=True), gd(t)
        for inst, tt in (("FX24", f_), ("GOLD", g_)):
            if len(tt):
                rows.append(dict(variant=key, rule=v["rule"], exit=v["mode"], refinement=(v["spec"]["id"] if v["spec"] else "BASE"),
                                 instrument=inst, **T.book(tt)))
        if len(f_):
            pr = T.by(f_, "sym")
            pr.insert(0, "variant", key)
            pair_rows.append(pr)
            yy = T.by(f_.assign(year=f_.t_signal.dt.year), "year")
            yy.insert(0, "variant", key)
            year_rows.append(yy)
        d = {}
        eo = entry_ok_of(v)
        for inst, tt, sy in (("FX24", f_, FX), ("GOLD", g_.reset_index(drop=True), GOLD)):
            if len(tt) < 20:
                d[inst] = dict(note="too few trades for controls", trades=len(tt))
                continue
            d[inst] = dict(book=T.book(tt), directions=T.controls(tt, v["rule"], v["mode"], stage),
                           luck=T.luck(tt, v["rule"], v["mode"], stage, n=20000),
                           lookahead=T.lookahead(v["rule"], v["mode"], stage, entry_ok=eo, syms=sy))
        ctrl[key] = d
        b = T.book(f_)
        log(stage, key, b.get("trades"), b.get("usd_per_trade"), b.get("profit_factor"),
            "luck", d.get("FX24", {}).get("luck", {}).get("usd_per_trade"))
        T.jdump("%s_controls.json" % stage.lower(), ctrl)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "%s_results.csv" % stage.lower()), index=False)
    pd.concat(pair_rows).to_csv(os.path.join(OUT, "%s_by_pair.csv" % stage.lower()), index=False)
    pd.concat(year_rows).to_csv(os.path.join(OUT, "%s_by_year.csv" % stage.lower()), index=False)


def validate():
    keys = explore_gate()
    log("advancing to VALIDATE:", keys)
    T.jdump("validate_advancing.json", keys)
    stage_run("VALIDATE", keys)


STRESS_LEVELS = [("normal", 1.0, 0.0), ("spread x2", 2.0, 0.0), ("spread x2 + 1 pip slip each fill", 2.0, 1.0),
                 ("spread x3 + 2 pip slip each fill", 3.0, 2.0)]


def validate_gate():
    """Stated in advance: advance to STRESS if VALIDATE FX24 dollars per trade > 0 and profit factor > 1."""
    r = pd.read_csv(os.path.join(OUT, "validate_results.csv"))
    r = r[r.instrument == "FX24"]
    return r[(r.usd_per_trade > 0) & (r.profit_factor > 1)].variant.tolist()


def stress():
    best = json.load(open(os.path.join(OUT, "refinements_best.json")))
    surv = validate_gate()
    show_only = [k for k in best.values() if k not in surv]
    keys = surv + show_only
    log("STRESS survivors of VALIDATE:", surv, "| best-of-refinement shown for completeness only:", show_only)
    T.jdump("stress_advancing.json", dict(survivors=surv, completeness_only=show_only))
    stage_run("STRESS", keys)
    V = all_variants()
    rows = []
    for key in keys:
        v = V[key]
        eo = entry_ok_of(v)
        norm = None
        for nm, sm, sl in STRESS_LEVELS:
            t = run_variant(key, v, "STRESS", smult=sm, slipx=sl)
            f_ = fx(t)
            if nm == "normal":
                norm = f_
            b = T.book(f_)
            # expected extra cost per trade: (smult-1) x the normal round-trip spread + 2 fills x slip, in dollars
            sp_pips = np.array([T.load(s).spr[int(i)] / T.load(s).P for s, i in zip(norm.sym, norm.i_fill)])
            usdp = np.array([T.load(s).usd for s in norm.sym])
            exp_delta = -float(((sm - 1.0) * sp_pips * usdp + 2 * sl * usdp).mean())
            rows.append(dict(variant=key, level=nm, spread_mult=sm, slip_pips_each_fill=sl, trades=b["trades"],
                             right_in_100=b["right_in_100"], profit_factor=b["profit_factor"], usd_per_trade=b["usd_per_trade"],
                             delta_vs_normal=None, expected_delta=round(exp_delta, 3)))
        base_u = [r_ for r_ in rows if r_["variant"] == key and r_["level"] == "normal"][0]["usd_per_trade"]
        for r_ in rows:
            if r_["variant"] == key:
                r_["delta_vs_normal"] = round(r_["usd_per_trade"] - base_u, 3)
        log("stress adverse", key, [(r_["level"], r_["usd_per_trade"]) for r_ in rows if r_["variant"] == key])
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "stress_adverse.csv"), index=False)


def gold_stress():
    """Gold is the secondary instrument. The FX gate (stated in advance) never lets gold reach STRESS on its own, and gold
    did well on VALIDATE, so this extra look is added AFTER seeing VALIDATE and is labelled as such. Gate: gold VALIDATE
    dollars per trade > 0 with at least 30 trades. Descriptive only; nothing is chosen from it."""
    v = pd.read_csv(os.path.join(OUT, "validate_results.csv"))
    g = v[(v.instrument == "GOLD") & (v.usd_per_trade > 0) & (v.trades >= 30)].variant.tolist()
    V = all_variants()
    rows, ctrl = [], {}
    for key in g:
        t = run_variant(key, V[key], "STRESS", syms=GOLD)
        t.to_csv(os.path.join(OUT, "goldtrades_STRESS_%s.csv" % key), index=False)
        rows.append(dict(variant=key, instrument="GOLD", **T.book(t)))
        if len(t) >= 20:
            tt = t.reset_index(drop=True)
            ctrl[key] = dict(directions=T.controls(tt, V[key]["rule"], V[key]["mode"], "STRESS"),
                             luck=T.luck(tt, V[key]["rule"], V[key]["mode"], "STRESS", n=20000),
                             lookahead=T.lookahead(V[key]["rule"], V[key]["mode"], "STRESS", entry_ok=entry_ok_of(V[key]), syms=GOLD))
        log("gold stress", key, rows[-1].get("trades"), rows[-1].get("usd_per_trade"))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "stress_gold_results.csv"), index=False)
    T.jdump("stress_gold_controls.json", ctrl)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "explore"
    if cmd == "explore":
        explore()
    elif cmd == "goldstress":
        gold_stress()
    elif cmd == "stress":
        stress()
    elif cmd == "validate":
        validate()
    elif cmd == "refine":
        refine_explore()
    elif cmd == "pick":
        print(pick_refinements().to_string())
