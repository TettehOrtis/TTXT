"""BT14 generator: synthetic M1 price series that contain EXACTLY ZERO edge.

Design (BT14_BRIEF.md section 1):
  * per instrument, the per-minute log-return standard deviation is measured from the REAL DEV series
    (2021-01-01 .. 2024-01-01), SEPARATELY PER HOUR-OF-DAY, so the intraday volatility shape (and therefore the
    killzone structure REF trades) is preserved;
  * prices are a ZERO-DRIFT geometric Brownian motion at that volatility: the log-price increment of every
    sub-step is drawn from Normal(mu=0.0, sigma=sigma_hour/sqrt(SUB)).  mu is the literal constant 0.0 --
    there is no drift term anywhere in this file (asserted by bt14_tests.py);
  * each M1 bar is built from SUB=20 sub-minute steps, so highs and lows are real wicks: SMC detection reads
    wicks, and close-to-close bars would never form sweeps / order blocks at a realistic rate;
  * the CALENDAR is the real one: the minute timestamps are copied from the real series, so weekend gaps,
    holiday closures and missing minutes are byte-identical and common.simulate's weekend/gap logic behaves
    exactly as it does on real data;
  * the COST MODEL is the real one: the per-minute spread column is copied from the real series (it is stored in
    price units = measured pips x pip(sym), so it stays the same number of pips whatever level the random walk
    reaches) and commission / 0.2 pip stop slippage come from common.py unchanged.

Nothing here is aware of the pipeline: the whole series is generated first, then the causal pipeline is run over
it.  There is no path-dependent selection of any kind (that is the lookahead trap of BT14_BRIEF.md item 1).

The real DEV series is read ONCE, into a small profile file (results/BT14/vol_profile.json + the calendar in
parts/calendar.npz).  TEST (2025-07+) is never touched.

Usage:
    .venv/Scripts/python.exe smc_bot/bt/bt14_gbm.py profile      # build the volatility profile + calendar (real data)
    .venv/Scripts/python.exe smc_bot/bt/bt14_gbm.py show         # print the profile
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SUB = 20                                   # sub-minute steps per M1 bar
MU = 0.0                                   # drift. Literal zero. Do not parameterise this.
PERIOD = ("2021-01-01", "2024-01-01")      # one DEV window, 3 years
SMC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(SMC, "results", "BT14")
PARTS = os.path.join(OUT, "parts")
PROF_F = os.path.join(OUT, "vol_profile.json")
CAL_F = os.path.join(PARTS, "calendar.npz")


def trade_pairs():
    """The 9 instruments REF trades (PAIRS minus EURGBP), via the real-data config."""
    import common
    return [p for p in common.PAIRS if p != "EURGBP"]


# --------------------------------------------------------------------------------- profile (reads REAL DEV once)
def build_profile():
    """Measure, per instrument: start price, per-hour-of-day sd of the per-minute log return, and the DEV calendar."""
    os.environ.pop("SMC_DATASET", None)
    import common
    syms = trade_pairs()
    prof = {}
    cal = {}
    for sym in syms:
        m1 = common.bars(sym, "M1")
        m1 = m1[(m1.time >= PERIOD[0]) & (m1.time < PERIOD[1])].reset_index(drop=True)
        t = m1.time.values
        c = m1.close.values
        lr = np.diff(np.log(c))
        # only consecutive minutes (a gap is a weekend / holiday / hole, not a one-minute return)
        ok = np.diff(t) == np.timedelta64(1, "m")
        hod = m1.time.dt.hour.values[1:]
        sd = np.zeros(24)
        for h in range(24):
            m = ok & (hod == h)
            sd[h] = float(lr[m].std()) if m.sum() > 50 else np.nan
        # hours with too little data fall back to the all-hours sd
        allsd = float(lr[ok].std())
        sd = np.where(np.isfinite(sd), sd, allsd)
        prof[sym] = dict(p0=float(c[0]), sd_hour=[float(x) for x in sd], sd_all=allsd,
                         n_min=int(len(m1)), n_cons=int(ok.sum()))
        cal[sym + "_t"] = t.astype("datetime64[m]").astype(np.int64)        # minutes since epoch
        cal[sym + "_spr"] = m1.spr.values.astype(np.float32)
        print(f"{sym:7s} n={len(m1):8d} sd_all={allsd:.6g} sd_07={sd[7]:.6g} sd_12={sd[12]:.6g} "
              f"sd_22={sd[22]:.6g} p0={c[0]}", flush=True)
    os.makedirs(PARTS, exist_ok=True)
    with open(PROF_F, "w") as fh:
        json.dump(prof, fh, indent=1)
    np.savez_compressed(CAL_F, **cal)
    print("wrote", PROF_F, CAL_F)
    return prof


_PROF = None
_CAL = None


def profile():
    global _PROF, _CAL
    if _PROF is None:
        with open(PROF_F) as fh:
            _PROF = json.load(fh)
        _CAL = np.load(CAL_F)
    return _PROF, _CAL


# --------------------------------------------------------------------------------- the generator
def gen_m1(sym, seed):
    """One synthetic M1 series for `sym`. Zero-drift GBM, per-hour sigma, OHLC from SUB sub-minute steps,
    real calendar, real spread column. Returns DataFrame(time, open, high, low, close, spr, tclose)."""
    prof, cal = profile()
    p = prof[sym]
    tmin = cal[sym + "_t"]                                   # int64 minutes since epoch
    spr = cal[sym + "_spr"]
    n = len(tmin)
    hod = ((tmin // 60) % 24).astype(np.int64)               # hour-of-day, UTC (epoch minute 0 = 00:00 UTC)
    sd = np.asarray(p["sd_hour"], dtype=np.float64)[hod]     # per-minute sigma for each bar
    step_sd = (sd / np.sqrt(SUB)).astype(np.float32)

    rng = np.random.default_rng(seed)
    # log-price increments: Normal(MU, step_sd). MU is 0.0, so this is a driftless random walk in log space.
    inc = rng.standard_normal((n, SUB), dtype=np.float32) * step_sd[:, None] + np.float32(MU)
    lp = np.cumsum(inc.reshape(-1), dtype=np.float64).reshape(n, SUB)
    lp += np.log(p["p0"])
    px = np.exp(lp)                                          # (n, SUB) prices inside each minute

    close = px[:, -1]
    openp = np.empty(n); openp[0] = p["p0"]; openp[1:] = close[:-1]
    high = np.maximum(px.max(axis=1), openp)
    low = np.minimum(px.min(axis=1), openp)
    t = tmin.astype("datetime64[m]").astype("datetime64[ns]")
    b = pd.DataFrame(dict(time=t, open=openp, high=high, low=low, close=close, spr=spr.astype(np.float64)))
    b["tclose"] = b.time + pd.Timedelta(minutes=1)
    return b


def install(seed, syms=None, verbose=False):
    """Generate a whole synthetic world and install it as the `gbm` dataset (in-memory, all timeframes).
    Must be called with SMC_DATASET=gbm already set in the environment before `common` is imported."""
    import common
    from smc_core import resample
    assert common.DATASET == "gbm", "set SMC_DATASET=gbm before importing common"
    syms = syms or trade_pairs()
    common.GBM_BARS.clear()
    for sym in syms:
        m1 = gen_m1(sym, hash_seed(seed, sym))
        common.GBM_BARS[(sym, "M1")] = m1
        for tf in ("M5", "M15", "M30", "H1", "H4"):
            common.GBM_BARS[(sym, tf)] = resample(m1[["time", "open", "high", "low", "close", "spr"]], tf)
        if verbose:
            print(f"  gen {sym} {len(m1)} M1 bars", flush=True)
    return syms


def hash_seed(seed, sym):
    """Independent stream per (realization, instrument), reproducible from the realization seed alone."""
    return int(np.random.SeedSequence([int(seed), sum(ord(ch) * (i + 1) for i, ch in enumerate(sym))])
               .generate_state(1, dtype=np.uint32)[0])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "profile"
    if cmd == "profile":
        build_profile()
    elif cmd == "show":
        prof, _ = profile()
        for s, p in prof.items():
            print(f"{s:7s} sd_all={p['sd_all']:.6g} p0={p['p0']} n={p['n_min']}  "
                  f"hourly {['%.2g' % x for x in p['sd_hour']]}")
