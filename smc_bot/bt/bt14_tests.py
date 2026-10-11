"""BT14 blocking sanity checks (BT14_BRIEF.md items 3, 4, 5) + the zero-drift assertions.

    .venv/Scripts/python.exe smc_bot/bt/bt14_tests.py           # all checks
Writes results/BT14/sanity.json. Any FAIL means every synthetic number downstream is worthless.
"""
import os, sys, json, inspect, re
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RES = {}


# ================================================================= 0. zero drift, by assertion and by measurement
def check_zero_drift():
    os.environ["SMC_DATASET"] = "gbm"
    import bt14_gbm as G
    out = dict(MU=G.MU, SUB=G.SUB)
    assert G.MU == 0.0, "generator drift must be exactly 0.0"
    src = inspect.getsource(G.gen_m1)
    # the only place a drift could enter is the increment line; assert it adds MU and nothing else
    inc = [ln for ln in src.splitlines() if "standard_normal" in ln][0]
    assert re.search(r"\*\s*step_sd\[:, None\]\s*\+\s*np\.float32\(MU\)", inc), inc
    out["increment_line"] = inc.strip()
    # measured: the realized mean log return over 5 worlds vs its own standard error
    rows = []
    for seed in range(5):
        for sym in ("EURUSD", "XAUUSD"):
            b = G.gen_m1(sym, G.hash_seed(seed, sym))
            lr = np.diff(np.log(b.close.values))
            se = lr.std() / np.sqrt(len(lr))
            rows.append(dict(seed=seed, sym=sym, mean_logret=float(lr.mean()), se=float(se),
                             z=float(lr.mean() / se), total_logret=float(np.log(b.close.values[-1] / b.close.values[0]))))
    d = pd.DataFrame(rows)
    out["max_abs_z_of_mean_logret"] = round(float(d.z.abs().max()), 2)
    out["mean_total_logret_over_worlds"] = round(float(d.total_logret.mean()), 4)
    out["pass_"] = bool(out["max_abs_z_of_mean_logret"] < 4.0)
    print("[0] zero drift:", out["increment_line"])
    print(f"    MU={G.MU} SUB={G.SUB}; |z| of realized mean log-return <= {out['max_abs_z_of_mean_logret']} "
          f"(pure sampling noise) -> {'PASS' if out['pass_'] else 'FAIL'}")
    return out


# ================================================================= 1. setup-rate calibration (brief item 3)
REAL_FUNNEL = json.load(open(os.path.join(os.path.dirname(HERE), "results", "RECON", "CANON_funnel_DEV.json")))


def check_setup_rate(seeds=(0, 1, 2)):
    import bt14_run as Rn
    import recon_pipeline as R
    import bt14_gbm as G
    import common, shutil
    stages = list(REAL_FUNNEL)
    rows = []
    for seed in seeds:
        prep = os.path.join(Rn.SCRATCH, f"cal{seed}")
        shutil.rmtree(prep, ignore_errors=True); os.makedirs(prep, exist_ok=True)
        import bt6b_pipeline as B
        B.CACHE = prep; R._PD.clear(); R._memo.clear()
        G.install(seed)
        _, fun = R.run(Rn.CANON, "DEV")
        rows.append({s: fun.get(s, 0) for s in stages})
        common.GBM_BARS.clear(); R._PD.clear(); R._memo.clear(); shutil.rmtree(prep, ignore_errors=True)
    syn = pd.DataFrame(rows).mean()
    tab = pd.DataFrame(dict(real=pd.Series(REAL_FUNNEL), synthetic=syn.round(1)))
    tab["ratio"] = (tab.synthetic / tab.real).round(2)
    bad = tab[(tab.ratio > 2.0) | (tab.ratio < 0.5)]
    out = dict(table=tab.reset_index().rename(columns={"index": "stage"}).to_dict("records"),
               worst_ratio=float(tab.ratio.max()), best_ratio=float(tab.ratio.min()),
               pass_=bool(len(bad) == 0), seeds=list(seeds))
    print("\n[1] setup-rate calibration (mean of 3 worlds vs real DEV):")
    print(tab.to_string())
    print(f"    every stage within 2x -> {'PASS' if out['pass_'] else 'FAIL (' + ','.join(bad.index) + ')'}")
    return out


# ================================================================= 2. the simulator on random entries (brief item 4)
def check_random_entries(seed=0, rr_list=(1.0, 2.0, 3.0), n_per_pair=500, stop_pips=25.0):
    """Random side, random time, fixed stop and a fixed RR target, market entry, one position per pair -- exactly the
    fills/exits/costs REF uses. On a driftless random walk with barriers at -S and +k*S the win rate must be
    1/(1+k) and the gross result 0, so net R must be minus the cost."""
    import bt14_run as Rn                                       # noqa: F401  (sets SMC_DATASET=gbm)
    import bt14_gbm as G
    import common
    from common import simulate, pip, COMM, STOP_SLIP
    G.install(seed)
    syms = G.trade_pairs()
    rows = []
    for rr in rr_list:
        allt = []
        for si, sym in enumerate(syms):
            m1 = common.GBM_BARS[(sym, "M1")]
            P = pip(sym)
            rng = np.random.default_rng(1000 + int(rr * 10) + si)
            n = len(m1)
            # entries in the active part of the day, well inside the series
            idx = np.sort(rng.choice(np.arange(200, n - 20000), n_per_pair, replace=False))
            t = m1.time.values[idx]
            side = rng.choice([-1, 1], len(idx))
            ref = m1.close.values[idx - 1]                      # last CLOSED bar before the signal: causal
            sl = ref - side * stop_pips * P
            tp = ref + side * rr * stop_pips * P
            o = pd.DataFrame(dict(t_signal=pd.to_datetime(t), side=side, entry_type="market", entry=np.nan,
                                  expire=pd.to_datetime(t), sl=sl, tp=tp, be_at_r=np.nan, max_hold_min=np.nan))
            allt.append(simulate(sym, o))
        f = pd.concat(allt, ignore_index=True)
        f = f[f.status == "filled"]
        # costs this experiment must pay, computed from the harness's own constants, per trade
        comm_R = np.array([COMM[s] for s in f.sym]) / f.risk_pips.values
        sl_like = f.exit_reason.isin(["SL", "BE", "GAP_SL"]).values
        slip_R = STOP_SLIP / f.risk_pips.values * sl_like
        # the spread is paid inside `risk_pips` already (fill = ask for a buy) and again on the exit side
        spr_R = np.array([(common.GBM_BARS[(s, "M1")].spr.mean() / pip(s)) for s in f.sym]) / f.risk_pips.values
        pred = -(comm_R + slip_R + spr_R * 0.5).mean()
        wr = float((f.R > 0).mean())
        netR = float(f.R.mean())
        gR = float((f.gross_pips.values + STOP_SLIP * sl_like) / f.risk_pips.values).__float__() if False else \
            float(((f.gross_pips.values + STOP_SLIP * sl_like) / f.risk_pips.values).mean())
        se = f.R.std(ddof=1) / np.sqrt(len(f))
        rows.append(dict(RR=rr, trades=int(len(f)), win_pct=round(wr * 100, 2),
                         win_pct_expected=round(100 / (1 + rr), 2),
                         net_R=round(netR, 4), net_R_expected=round(float(pred), 4), se_net_R=round(float(se), 4),
                         gross_R=round(gR, 4), z_vs_expected=round(float((netR - pred) / se), 2),
                         tp_pct=round(float((f.exit_reason == "TP").mean() * 100), 1),
                         timeout_pct=round(float(f.exit_reason.isin(["TIME", "WEEKEND", "END"]).mean() * 100), 1)))
    tab = pd.DataFrame(rows)
    ok = bool((tab.z_vs_expected.abs() < 3.0).all() and
              (((tab.win_pct - tab.win_pct_expected).abs()) < 3.0).all())
    out = dict(table=tab.to_dict("records"), pass_=ok, stop_pips=stop_pips, n_per_pair=n_per_pair, seed=seed)
    print("\n[2] random entries on synthetic data (sanity check 1):")
    print(tab.to_string(index=False))
    print(f"    win rate == 1/(1+RR) and net R == -(cost) -> {'PASS' if ok else 'FAIL'}")
    common.GBM_BARS.clear()
    return out


# ================================================================= 3. the wiring reproduces canonical REF (item 5)
def check_wiring():
    """Run the SAME pipeline object over REAL data in a subprocess (SMC_DATASET unset) and compare with the
    frozen canonical REF of results/RECON/report.md: DEV 150 trades, gross +0.262, net +0.196, t 1.07."""
    import subprocess, textwrap
    code = textwrap.dedent(r"""
        import os, sys, json
        os.environ.pop("SMC_DATASET", None)
        sys.path.insert(0, r"%s")
        import recon_pipeline as R
        t, fun = R.run(R.cfg(R.BCFG, ob_def="core"), "DEV")
        s = R.summ(t, "CANON")
        print("JSON" + json.dumps({k: (None if v is None else (float(v) if hasattr(v, "item") or isinstance(v, float) else v))
                                   for k, v in s.items() if k in ("trades","gross_R","net_R","t_net","win_pct","PF","maxDD_R")}))
    """) % HERE
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(HERE)))
    line = [l for l in p.stdout.splitlines() if l.startswith("JSON")]
    if not line:
        print(p.stdout[-3000:], p.stderr[-3000:])
        return dict(pass_=False, error="no output")
    got = json.loads(line[0][4:])
    exp = dict(trades=150, gross_R=0.262, net_R=0.196, t_net=1.07)
    ok = (got["trades"] == exp["trades"] and abs(got["gross_R"] - exp["gross_R"]) < 0.002
          and abs(got["net_R"] - exp["net_R"]) < 0.002 and abs(got["t_net"] - exp["t_net"]) < 0.02)
    out = dict(got=got, expected_RECON=exp, pass_=bool(ok))
    print("\n[3] wiring check on REAL data (sanity check 2):")
    print(f"    expected (RECON canonical REF): {exp}")
    print(f"    got                           : {got}")
    print(f"    -> {'PASS' if ok else 'FAIL'}")
    return out


if __name__ == "__main__":
    which = sys.argv[1:] or ["drift", "wiring", "random", "rate"]
    if "drift" in which:
        RES["zero_drift"] = check_zero_drift()
    if "wiring" in which:
        RES["wiring_real_REF"] = check_wiring()
    if "random" in which:
        RES["random_entries"] = check_random_entries()
    if "rate" in which:
        RES["setup_rate"] = check_setup_rate()
    import bt14_gbm as G
    f = os.path.join(G.OUT, "sanity.json")
    old = json.load(open(f)) if os.path.exists(f) else {}      # merge, so running one check keeps the others
    old.update(RES)
    json.dump(old, open(f, "w"), indent=1, default=str)
    bad = [k for k, v in RES.items() if not (v.get("pass") or v.get("pass_"))]
    print("\n==== BT14 sanity:", "ALL PASS" if not bad else "FAILED: " + ", ".join(bad), "====")
    sys.exit(1 if bad else 0)
