"""Which SMC/EMA filters help? Filters are recorded features, so every combination is evaluated without re-running.
Chosen on 2021-2023, checked on unseen 2024-2026. Pooled over all pairs."""
import glob, itertools, os
import numpy as np, pandas as pd
df = pd.concat([pd.read_parquet(f) for f in glob.glob("smc_bot/out/setups_*.parquet")], ignore_index=True)
df = df[df.status == "traded"].copy()
df["early"] = df.t_setup.dt.year <= 2023
F = {"h1_ema_ok": "1H EMA200 agrees", "m30_ema_ok": "30M EMA50 agrees", "m5_ema_ok": "5M EMA20 agrees",
     "discount_ok": "discount/premium", "swept": "liquidity sweep", "m15_agree": "15M trend agrees", "ob_zone": "POI is an OB (not FVG)"}
df["ob_zone"] = df.zone_kind == "OB"
for c in F: df[c] = df[c].astype(bool)
def stats(x):
    return pd.Series(dict(n=len(x), win=(x.r_net > 0).mean() * 100, R=x.r_net.mean(), pips=x.net_pips.mean()))
print("ALL (no filter):", {k: round(v, 3) for k, v in stats(df[df.early]).items()}, "->", {k: round(v, 3) for k, v in stats(df[~df.early]).items()})
print("\nSINGLE FILTERS (2021-23 -> 2024-26):")
for c, name in F.items():
    a, b = stats(df[df.early & df[c]]), stats(df[~df.early & df[c]])
    print(f"  {name:22} n {a.n:4.0f} R {a.R:+.3f} win {a.win:4.1f}%  ->  n {b.n:4.0f} R {b.R:+.3f} win {b.win:4.1f}%  pips {b.pips:+.2f}")
rows = []
for k in (2, 3):
    for combo in itertools.combinations(F, k):
        m = np.logical_and.reduce([df[c] for c in combo])
        a, b = stats(df[df.early & m]), stats(df[~df.early & m])
        if a.n >= 60:
            rows.append(dict(filters=" + ".join(F[c] for c in combo), n_early=a.n, R_early=a.R, n_late=b.n, R_late=b.R, win_late=b.win, pips_late=b.pips))
r = pd.DataFrame(rows).sort_values("R_early", ascending=False)
pd.set_option("display.width", 220); pd.set_option("display.max_colwidth", 80)
print(f"\nCOMBINATIONS (2-3 filters, >=60 trades in 2021-23): {len(r)} tried. Top 10 chosen on 2021-23, with unseen 2024-26:")
print(r.head(10).round(3).to_string(index=False))
print("\nRR bucket (2021-26):"); print(df.groupby(pd.cut(df.rr, [2, 3, 4, 6, 10, 100])).apply(stats, include_groups=False).round(3))
