"""BT14 runner: the REAL REF pipeline over synthetic zero-edge data, N times.

What it does per realization ("noise world"):
  1. bt14_gbm.install(seed) builds 9 synthetic instruments (zero-drift GBM, real calendar, real spread column)
     and installs them as the `gbm` dataset. The WHOLE series is generated before anything is run over it,
     so no decision can see the future (BT14_BRIEF.md item 1).
  2. recon_pipeline.run(CANON, "DEV") -- the frozen canonical REF, unchanged, via common.simulate
     -> measurement (a), the single-config false-positive distribution.
  3. the same breadth of variant search we really ran (RECON's 48-cell major grid, see SEARCH below)
     -> measurement (b); the best config in each world is what we would have reported from that world.
  4. the world's results are written to results/BT14/parts/w<seed>.json and the world is thrown away
     (nothing is kept in RAM between worlds).

Usage (from the TTXT root):
    .venv/Scripts/python.exe smc_bot/bt/bt14_run.py worker 0 200 1          # seeds 0,1,..199 (skips finished ones)
    .venv/Scripts/python.exe smc_bot/bt/bt14_run.py worker 0 200 3          # stride 3 -> one of 3 parallel workers
    .venv/Scripts/python.exe smc_bot/bt/bt14_run.py breadth 0 8             # full 448-config search, few worlds
    .venv/Scripts/python.exe smc_bot/bt/bt14_run.py collect                 # parts/*.json -> the two CSVs
"""
import os, sys, json, time, shutil, itertools
import numpy as np
import pandas as pd

os.environ["SMC_DATASET"] = "gbm"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCRATCH = os.environ.get("BT14_SCRATCH", os.path.join(os.environ.get("TEMP", "/tmp"), "bt14_prep"))

import bt14_gbm as G                                              # noqa: E402
import common                                                     # noqa: E402
import recon_pipeline as R                                        # noqa: E402
import bt6b_pipeline as B                                         # noqa: E402

assert common.DATASET == "gbm", "BT14 must run with SMC_DATASET=gbm"
OUT = G.OUT
PARTS = G.PARTS
os.makedirs(PARTS, exist_ok=True)

CANON = R.cfg(R.BCFG, ob_def="core")        # the frozen canonical REF (RECON report section 4). Never re-tuned.

# ---------------------------------------------------------------------------- the variant search we really ran
# RECON (results/RECON/report.md section 3) enumerated 448 faithful rule readings: a full 48-cell grid over the four
# axes that actually move the result (dealing range, killzone, target, when bias is checked) plus 400 uniform-random
# configs over all 11 ambiguous axes. RECON measured that each of the 7 MINOR axes shifts the median net R by <= 0.05R,
# so the 48-cell major grid is the part of the search that generates the spread we exploited. SEARCH below is that
# grid plus the canonical config = 49 readings per world, run in EVERY world. `breadth` re-runs the full 448 on a few
# worlds to measure how much the remaining 399 readings add to the best-of (reported in the report).
MAJOR = R.MAJOR                                                   # drange, kz, target, bias_checks
SEARCH = [("CANON", CANON)] + [
    ("grid_" + "_".join(f"{k}={v}" for k, v in zip(MAJOR, combo)), R.cfg(R.BCFG, **dict(zip(MAJOR, combo))))
    for combo in itertools.product(*[R.SPACE[k] for k in MAJOR])]


def search_full(n_random=400, seed=0):
    """RECON's exact 448: the 48-cell major grid + 400 uniform-random configs over all 11 axes (same rng seed)."""
    rng = np.random.default_rng(seed)
    confs = list(SEARCH)
    for i in range(n_random):
        fl = {k: v[rng.integers(len(v))] for k, v in R.SPACE.items()}
        confs.append((f"rand{i}", R.cfg(R.BCFG, **fl)))
    return confs


# ---------------------------------------------------------------------------- statistics per world
def block_t(f, reps=2000, seed=7):
    """t-stat of mean net R from a block bootstrap over calendar weeks (BT14_BRIEF item 2: REF trades overlap
    ACROSS pairs, so the naive t over-states significance). Returns (t_naive, t_block, overlap_fraction)."""
    Rv = f.R.values
    n = len(Rv)
    if n < 3:
        return None, None, None
    t_naive = float(Rv.mean() / (Rv.std(ddof=1) / np.sqrt(n)))
    wk = f.t_fill.dt.strftime("%G-W%V").values
    groups = pd.Series(Rv).groupby(wk)
    keys = list(groups.groups)
    vals = [Rv[groups.groups[k].values] for k in keys]
    rng = np.random.default_rng(seed)
    means = np.empty(reps)
    for i in range(reps):
        pick = rng.integers(0, len(vals), len(vals))
        means[i] = np.concatenate([vals[j] for j in pick]).mean()
    sd = means.std(ddof=1)
    t_block = float(Rv.mean() / sd) if sd > 0 else None
    # overlap: share of trades whose [t_fill, t_exit] interval intersects another trade's (different pairs can overlap)
    iv = f[["t_fill", "t_exit"]].sort_values("t_fill").values
    a = iv[:, 0].astype("datetime64[ns]"); b = iv[:, 1].astype("datetime64[ns]")
    run_max = np.maximum.accumulate(np.r_[np.datetime64("1970-01-01", "ns"), b[:-1]])
    ov = float((a < run_max).mean())
    return t_naive, t_block, ov


def world_stats(t, name, full=False):
    f = t[t.status == "filled"]
    out = dict(config=name, trades=int(len(f)))
    if not len(f):
        return out
    out.update(gross_R=round(float(f.gross_R.mean()), 4), net_R=round(float(f.R.mean()), 4),
               total_net_R=round(float(f.R.sum()), 2), win_pct=round(float((f.R > 0).mean() * 100), 1))
    if full:
        tn, tb, ov = block_t(f)
        s = R.stats(f)
        per = f.groupby("sym").R.mean()
        ses = f.groupby("session").R.mean()
        out.update(t_naive=None if tn is None else round(tn, 3), t_block=None if tb is None else round(tb, 3),
                   overlap_frac=None if ov is None else round(ov, 3), PF=float(s["PF"]), maxDD_R=float(s["maxDD_R"]),
                   pairs_pos=int((per > 0).sum()), pairs_n=int(len(per)),
                   net_LDN=None if "LDN" not in ses else round(float(ses["LDN"]), 3),
                   net_NY=None if "NY" not in ses else round(float(ses["NY"]), 3),
                   usd_per_01lot=float(s["usd_per_01lot"]))
    else:
        Rv = f.R.values
        out["t_naive"] = round(float(Rv.mean() / (Rv.std(ddof=1) / np.sqrt(len(Rv)))), 3) if len(Rv) > 2 else None
    return out


# ---------------------------------------------------------------------------- one world
def run_world(seed, confs=None, tag=""):
    """Generate world `seed`, run the canonical REF and the variant search over it, write the checkpoint."""
    confs = confs or SEARCH
    f_out = os.path.join(PARTS, f"w{seed:04d}{tag}.json")
    if os.path.exists(f_out):
        return None
    t0 = time.time()
    prep = os.path.join(SCRATCH, f"w{seed}{tag}")
    shutil.rmtree(prep, ignore_errors=True); os.makedirs(prep, exist_ok=True)
    B.CACHE = prep                                            # per-world detection cache (deleted below)
    R._PD.clear(); R._memo.clear()                            # no state carried between worlds
    G.install(seed)
    rec = dict(seed=int(seed), n_instruments=len(common.GBM_BARS) // 6, configs=[])
    try:
        for i, (name, C) in enumerate(confs):
            t, _ = R.run(C, "DEV")
            rec["configs"].append(world_stats(t, name, full=(name == "CANON")))
            del t
        rec["secs"] = round(time.time() - t0, 1)
        with open(f_out + ".tmp", "w") as fh:
            json.dump(rec, fh)
        os.replace(f_out + ".tmp", f_out)                     # atomic: a half-written part can never be collected
    finally:
        common.GBM_BARS.clear(); R._PD.clear(); R._memo.clear()
        shutil.rmtree(prep, ignore_errors=True)
    c0 = rec["configs"][0]
    best = max((c for c in rec["configs"] if c["trades"] >= 1), key=lambda c: c["net_R"])
    print(f"w{seed:04d}{tag} CANON n={c0['trades']:4d} net={c0.get('net_R'):+.3f} t={c0.get('t_naive')} | "
          f"best-of-{len(confs)} net={best['net_R']:+.3f} (n={best['trades']}, {best['config'][:34]}) "
          f"[{rec['secs']}s]", flush=True)
    return rec


def worker(lo, hi, step=1, confs=None, tag=""):
    for seed in range(int(lo) + 0, int(hi), int(step)):
        try:
            run_world(seed, confs=confs, tag=tag)
        except KeyboardInterrupt:
            raise
        except Exception as e:                                 # a single bad world must not kill the run
            print(f"w{seed:04d}{tag} FAILED: {type(e).__name__}: {e}", flush=True)
            with open(os.path.join(PARTS, "failures.log"), "a") as fh:
                fh.write(f"{seed}{tag}\t{type(e).__name__}\t{e}\n")


# ---------------------------------------------------------------------------- collect
def collect(tag="", n_min=50):
    rows, best = [], []
    for fn in sorted(os.listdir(PARTS)):
        if not (fn.startswith("w") and fn.endswith(".json")):
            continue
        if tag and f"{tag}.json" not in fn:
            continue
        if not tag and not fn[1:].replace(".json", "").isdigit():
            continue
        rec = json.load(open(os.path.join(PARTS, fn)))
        cfgs = rec["configs"]
        can = next(c for c in cfgs if c["config"] == "CANON")
        rows.append(dict(seed=rec["seed"], n_configs=len(cfgs), secs=rec.get("secs"), **can))
        ok = [c for c in cfgs if c["trades"] >= n_min]
        pool = ok or cfgs
        b = max(pool, key=lambda c: c["net_R"] if c["trades"] else -9)
        b0 = max(cfgs, key=lambda c: c["net_R"] if c["trades"] else -9)
        best.append(dict(seed=rec["seed"], n_configs=len(cfgs),
                         best_net_R=b["net_R"], best_trades=b["trades"], best_config=b["config"],
                         best_t_naive=b.get("t_naive"), best_gross_R=b.get("gross_R"),
                         best_net_R_nofloor=b0["net_R"], best_trades_nofloor=b0["trades"],
                         median_net_R=float(np.median([c["net_R"] for c in cfgs if c["trades"]])),
                         n_cfg_pos=int(sum(1 for c in cfgs if c["trades"] and c["net_R"] > 0))))
    a = pd.DataFrame(rows).sort_values("seed")
    b = pd.DataFrame(best).sort_values("seed")
    sfx = tag or ""
    a.to_csv(os.path.join(OUT, f"realizations{sfx}.csv"), index=False)
    b.to_csv(os.path.join(OUT, f"best_of_search{sfx}.csv"), index=False)
    print(f"collected {len(a)} worlds -> realizations{sfx}.csv / best_of_search{sfx}.csv")
    return a, b


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "worker":
        worker(*sys.argv[2:])
    elif cmd == "world":
        run_world(int(sys.argv[2]))
    elif cmd == "breadth":                                     # full 448-reading search on a few worlds
        worker(sys.argv[2], sys.argv[3], 1, confs=search_full(), tag="b")
    elif cmd == "collect":
        collect(*sys.argv[2:])
