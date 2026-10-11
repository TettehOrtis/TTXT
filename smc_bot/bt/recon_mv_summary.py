import os, pandas as pd, numpy as np
f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "RECON", "multiverse_DEV.csv")
m = pd.read_csv(f)
def d(x):
    return pd.Series(dict(n=len(x), trades_med=int(x.trades.median()), gross_pos=f"{(x.gross_R>0).mean():.0%}",
        net_pos=f"{(x.net_R>0).mean():.0%}", net_p10=round(x.net_R.quantile(.1),3), net_med=round(x.net_R.median(),3),
        net_p90=round(x.net_R.quantile(.9),3), gross_med=round(x.gross_R.median(),3), t_ge2=f"{(x.t_net>=2).mean():.0%}"))
print("ALL by sample"); print(m.groupby("sample").apply(d).to_string())
r = m[m["sample"] == "rand"]
for col in ["drange", "kz", "target", "bias_checks", "zone_N", "ob_premit", "fvg_def", "expiry", "choch_win", "pd_point", "ob_def"]:
    print("\nrandom sample by", col); print(r.groupby(col).apply(d).to_string())
sw = r[r.drange.isin(["swing", "swing5", "swingnoext"])]
print("\nrandom, swing-based dealing range only"); print(d(sw).to_string())
print("\nrandom, swing-based + local killzone"); print(d(sw[sw.kz == "local"]).to_string())
g = m[m["sample"] == "grid"]
print("\nGRID (major axes, minor = B)"); print(g[["drange","kz","target","bias_checks","trades","gross_R","net_R","t_net"]].to_string())
