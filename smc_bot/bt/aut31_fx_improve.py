"""AUT16 addendum 14: forex rescue grid. Usage: python aut31_fx_improve.py STAGE PAIR  (open windows only)"""
import os, sys, itertools
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut30_cooldown_vol as CV
import common as C

COSTUSD = {"GBPUSD": 1.13, "EURUSD": 1.00, "EURJPY": (1.10 + 0.5) * 0.64, "USDJPY": (0.79 + 0.5) * 0.64, "AUDUSD": 1.10, "USDCAD": (0.99 + 0.5) * 0.71,
           "USDCHF": (0.87 + 0.5) * 1.21, "NZDUSD": 1.40, "EURGBP": (0.83 + 0.5) * 1.32}
PIPUSD = {"GBPUSD": 1.0, "EURUSD": 1.0, "AUDUSD": 1.0, "NZDUSD": 1.0, "USDJPY": 0.64, "EURJPY": 0.64, "USDCAD": 0.71, "USDCHF": 1.21, "EURGBP": 1.32}
TRENDS = [("M15", "PX200"), ("M30", "PX100"), ("M30", "PX200"), ("H1", "PX100"), ("H1", "PX200")]
EXITS = {"X30": (2.122, 4.7), "X50": (3.486, 4.7), "X75": (5.229, 7.05), "X100": (6.972, 9.4)}
HOURS = {"ALL": (0, 24), "OVL": (12, 16)}
CSHARE = {"none": None, "le12": 0.12}


def slow_trends(F, pair):
    tc = F["tc"]
    for tf, n in (("M30", 200), ("H1", 100), ("H1", 200)):
        df = C.bars(pair, tf)
        c = df.close.values
        e = X.M._ema(c, n)
        j = np.clip(C.asof_index(df, tc), 0, len(df) - 1)
        ok = C.asof_index(df, tc) >= 260
        F["tr"][(tf, f"PX{n}")] = np.where(ok, np.where(c > e, 1, -1)[j], 0)


def orders(F, i, side, kt, ks):
    o = CV.atr_orders(F, i, side, kt, ks)
    return o


if __name__ == "__main__":
    stage, pair = sys.argv[1], sys.argv[2]
    X.SYM = pair
    F = R.add_trends(X.build_stage(stage))
    slow_trends(F, pair)
    ix = pd.DatetimeIndex(F["tc"]); hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    pip = C.pip(pair)
    rows = []
    for trend in TRENDS:
        i0, s0 = R.signals(F, trend, "T_E20SIDE", "ALL", 0)
        k = ok[i0]
        i, side = i0[k], s0[k]
        atr_usd = F["atr15"][i] / pip * PIPUSD[pair]
        cshare = COSTUSD[pair] / atr_usd
        for ename, (kt, ks) in EXITS.items():
            o = CV.atr_orders(F, i, side, kt, ks)
            # atr_orders may drop rows with non-finite levels; keep the extra columns aligned through the simulator
            o["hr"] = hr[i][: len(o)] if len(o) == len(i) else hr[i][np.isin(F["tc"][i], o.t_signal.values)]
            o["cshare"] = cshare[: len(o)] if len(o) == len(i) else cshare[np.isin(F["tc"][i], o.t_signal.values)]
            r = C.simulate(pair, o, one_at_a_time=False)
            r = r[r.status == "filled"].sort_values("t_signal", kind="stable")
            for hname, (h0, h1) in HOURS.items():
                for cname, cthr in CSHARE.items():
                    m = np.array(((r.hr >= h0) & (r.hr < h1)).values, dtype=bool)
                    if cthr is not None:
                        m = m & np.array((r.cshare <= cthr).values, dtype=bool)
                    rr = r[m]
                    if len(rr) < 50:
                        continue
                    ts = pd.DatetimeIndex(rr.t_signal).values.astype("datetime64[ns]").astype(np.int64)
                    te = pd.DatetimeIndex(rr.t_exit).values.astype("datetime64[ns]").astype(np.int64)
                    u = rr.usd_01.values
                    keep = CV.select(ts, te, u, 0, 0, 0)
                    if keep.sum() < 20:
                        continue
                    st = CV.stats(rr.t_exit.values[keep], u[keep], F["days"])
                    sd = rr.side.values[keep]
                    st.update(pair=pair, stage=stage, trend=f"{trend[0]} {trend[1]}", exit=ename, hours=hname, cshare=cname,
                              buy_usd=round(u[keep][sd == 1].mean(), 2) if (sd == 1).any() else np.nan,
                              sell_usd=round(u[keep][sd == -1].mean(), 2) if (sd == -1).any() else np.nan)
                    rows.append(st)
        print(pair, stage, trend, len(rows), flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"fximp_{stage}_{pair}.csv"), index=False)
    print("done", flush=True)
