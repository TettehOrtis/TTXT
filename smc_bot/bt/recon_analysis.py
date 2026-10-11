"""RECON analysis helpers: cumulative A->B path, trade overlap, concentration/bootstrap, canonical DEV (+VAL)."""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recon_pipeline as R  # noqa: E402

OUT = R.OUT


def key(t):
    f = t[t.status == "filled"]
    return set(zip(f.sym, f.t_signal.astype(str), f.side))


def path():
    """Start from A_REF and switch A's choices to B's one at a time, cumulatively, in order of impact."""
    order = ["bias_filter", "drange", "target", "bias_checks", "ema_warm", "start", "zone_N", "ob_def", "ob_premit",
             "fvg_def", "expiry", "touch", "choch_win", "ez", "pullback", "atr_stop", "dedupe"]
    C = dict(R.ACFG); rows = []
    t, fun = R.run(C); s = R.summ(t, "A_REF"); s["orders"] = fun["12_orders"]; rows.append(s)
    for k in order:
        C[k] = R.BCFG[k]
        t, fun = R.run(C)
        s = R.summ(t, "+B " + k); s["orders"] = fun.get("12_orders", 0); rows.append(s)
        print(s["config"], s["trades"], s["gross_R"], s["net_R"], flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "path_A_to_B_DEV.csv"), index=False)
    return df


def overlap_and_concentration():
    tb, _ = R.run(R.BCFG)
    kb = key(tb)
    res = []
    for name, C in [("D05_ob_candle_range", R.cfg(R.BCFG, ob_def="core")),
                    ("D10_drange_roll30", R.cfg(R.BCFG, drange="roll30")),
                    ("R04_drange_swing5", R.cfg(R.BCFG, drange="swing5")),
                    ("R06_drange_roll20", R.cfg(R.BCFG, drange="roll20")),
                    ("R07_drange_roll50", R.cfg(R.BCFG, drange="roll50")),
                    ("R09_killzone_fixed_utc", R.cfg(R.BCFG, kz="utc")),
                    ("R10_target_first_ge2R", R.cfg(R.BCFG, target="first2r")),
                    ("D02_bias_creation_only", R.cfg(R.BCFG, bias_checks="creation")),
                    ("A_REF", R.ACFG), ("R02_A_with_bias", R.cfg(R.ACFG, bias_filter=True))]:
        t, _ = R.run(C); k = key(t)
        f = t[t.status == "filled"]
        shared = f[[x in kb for x in zip(f.sym, f.t_signal.astype(str), f.side)]]
        only = f[[x not in kb for x in zip(f.sym, f.t_signal.astype(str), f.side)]]
        res.append(dict(config=name, trades=len(f), shared_with_B=len(k & kb), B_only=len(kb - k),
                        new_only=len(k - kb), net_R_shared=round(shared.R.mean(), 3) if len(shared) else None,
                        net_R_new=round(only.R.mean(), 3) if len(only) else None))
    ov = pd.DataFrame(res); ov.to_csv(os.path.join(OUT, "overlap_vs_B_DEV.csv"), index=False)
    print(ov.to_string())
    # concentration / bootstrap on B_REF DEV
    f = tb[tb.status == "filled"].copy()
    R_ = f.R.values; G = f.gross_R.values
    rng = np.random.default_rng(1)
    bs = np.array([rng.choice(R_, len(R_)).mean() for _ in range(10000)])
    bsg = np.array([rng.choice(G, len(G)).mean() for _ in range(10000)])
    srt = np.sort(R_)[::-1]
    conc = dict(n=len(R_), net_mean=round(R_.mean(), 3), gross_mean=round(G.mean(), 3),
                net_CI95=[round(np.percentile(bs, 2.5), 3), round(np.percentile(bs, 97.5), 3)],
                gross_CI95=[round(np.percentile(bsg, 2.5), 3), round(np.percentile(bsg, 97.5), 3)],
                P_net_le_0=round((bs <= 0).mean(), 3), P_gross_le_0=round((bsg <= 0).mean(), 3),
                net_without_top3=round(srt[3:].mean(), 3), net_without_top5=round(srt[5:].mean(), 3),
                net_without_top10=round(srt[10:].mean(), 3), top5_R=[round(x, 2) for x in srt[:5]],
                net_ex_USDCHF_USDJPY=round(f[~f.sym.isin(["USDCHF", "USDJPY"])].R.mean(), 3),
                n_ex_USDCHF_USDJPY=int((~f.sym.isin(["USDCHF", "USDJPY"])).sum()))
    print(conc)
    json.dump(conc, open(os.path.join(OUT, "B_REF_concentration_DEV.json"), "w"), indent=1)


CANON = R.cfg(R.BCFG, ob_def="core")   # B REF + literal spec OB (candle high-low, smc-strategy 5.1)


def canonical(period):
    t, fun = R.run(CANON, period, tag="CANON")
    s = R.summ(t, "CANON"); s["period"] = period
    f = t[t.status == "filled"]
    pp = f.groupby("sym").agg(trades=("R", "size"), net_R=("R", "mean"), gross_R=("gross_R", "mean")).round(3)
    pp.to_csv(os.path.join(OUT, f"CANON_perpair_{period}.csv"))
    json.dump(fun, open(os.path.join(OUT, f"CANON_funnel_{period}.json"), "w"), indent=1)
    print(s); print(fun); print(pp.to_string())
    return s


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "path":
        print(path().to_string())
    elif cmd == "overlap":
        overlap_and_concentration()
    elif cmd == "canon":
        canonical(sys.argv[2])
