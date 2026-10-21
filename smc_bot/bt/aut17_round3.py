"""AUT16 rounds 3 and 4 (and the sealed-period runner). Rules are in results/AUT16_layers/PREREG.md, written before running.

Usage:
  python aut17_round3.py round3 STAGE        python aut17_round3.py round4 STAGE
  python aut17_round3.py controls STAGE      (shortlist, read from shortlist.json)
  AUTOPSY_EXAM_UNLOCK=... SMC_DATASET=duka|zero python aut17_round3.py exam EXAM
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import aut16_layers as X                                                  # noqa: E402
import common as C                                                        # noqa: E402
import aut01_emapull as M                                                 # noqa: E402

OUTD = X.OUTD
COMM = X.COMM
# exit spec: (target_$, stop_$, hold_minutes)
EXITS = {"E15_70": (15, 70, 1440), "E15_50": (15, 50, 1440), "E15_30": (15, 30, 1440), "E10_30": (10, 30, 1440),
         "E20_50": (20, 50, 1440), "E30_70": (30, 70, 1440), "E30_30": (30, 30, 1440), "E50_50": (50, 50, 1440),
         "E50_70": (50, 70, 1440), "E15_70_t60": (15, 70, 60), "E15_70_t240": (15, 70, 240)}
HOURS = {"ALL": None, "LON07_12": (7, 12), "OVL12_16": (12, 16), "NY12_21": (12, 21), "NOASIA07_21": (7, 21)}


# ------------------------------------------------------------------ extra trend arrays
def add_trends(F):
    tc = F["tc"]
    ex = {}
    for tf in ("M15", "M30"):
        df = C.bars(X.SYM, tf)
        c = df.close.values
        j = np.clip(C.asof_index(df, tc), 0, len(df) - 1)
        ok = C.asof_index(df, tc) >= 260
        e = {n: M._ema(c, n) for n in (50, 100, 200)}
        px = {n: np.where(c > e[n], 1, -1) for n in (50, 100, 200)}
        slope200 = np.where(e[200] > pd.Series(e[200]).shift(20).values, 1, -1)
        e50_200 = np.where(e[50] > e[200], 1, -1)
        map_ = lambda a: np.where(ok, a[j], 0)                                      # noqa: E731
        ex[(tf, "PX50")], ex[(tf, "PX100")] = map_(px[50]), map_(px[100])
        px200 = map_(px[200])
        rsi = F["tr"][(tf, "RSI")]
        ex[(tf, "PX200+RSI")] = X.combine([px200, rsi])
        ex[(tf, "PX200+EMA")] = X.combine([px200, map_(e50_200)])
        ex[(tf, "PX200+SLOPE")] = X.combine([px200, map_(slope200)])
        ex[(tf, "PX200")] = px200
    ex[("BOTH", "PX200")] = X.combine([ex[("M15", "PX200")], ex[("M30", "PX200")]])
    F["tr"].update(ex)
    # extra trigger inputs
    h, l, c, o = F["h"], F["l"], F["c"], F["o"]
    F["atr1"] = X.A.atr(F["m1"], 14).values
    rng = np.maximum(h - l, 1e-9)
    F["_pos"] = (c - l) / rng
    return F


def trig2(F, name):
    o, h, l, c = F["o"], F["h"], F["l"], F["c"]
    g, r = c > o, c < o
    if name == "G0_BODY":
        return g, r
    if name == "T_TWO":
        return g & pd.Series(g).shift(1, fill_value=False).values, r & pd.Series(r).shift(1, fill_value=False).values
    if name == "T_BIG":
        big = np.abs(c - o) >= 0.5 * F["atr1"]
        return g & big, r & big
    if name == "T_STRONGCLOSE":
        return g & (F["_pos"] >= 0.7), r & (F["_pos"] <= 0.3)
    if name == "T_E20SIDE":
        return g & (c > F["e20"]), r & (c < F["e20"])
    if name == "T_M5E20SIDE":
        with np.errstate(invalid="ignore"):
            return g & (c > F["m5_e20"]), r & (c < F["m5_e20"])
    if name == "T_M5CANDLE":
        o5 = pd.Series(o).shift(4).values
        m5g = pd.Series(c).values > o5
        with np.errstate(invalid="ignore"):
            return g & (c > o5), r & (c < o5)
    raise KeyError(name)


def signals(F, trend, trig="G0_BODY", hours="ALL", side_only=0):
    d = F["tr"][trend]
    gb, gs = trig2(F, trig)
    ok = F["in"].copy()
    if HOURS[hours] is not None:
        hr = pd.to_datetime(F["tc"]).hour.values
        a, b = HOURS[hours]
        ok &= (hr >= a) & (hr < b)
    sb = (d == 1) & gb & ok
    ss = (d == -1) & gs & ok
    if side_only == 1:
        ss[:] = False
    if side_only == -1:
        sb[:] = False
    sb[:X.WARM] = False
    ss[:X.WARM] = False
    sb[-20:] = False
    ss[-20:] = False
    i = np.flatnonzero(sb | ss)
    return i, np.where(sb[i], 1, -1)


def orders(F, i, side, ex):
    tpd, sld, hold = EXITS[ex]
    m1 = F["m1"]
    nxt = np.clip(i + 1, 0, F["n"] - 1)
    px = F["o"][nxt] + np.where(side == 1, m1.spr.values[nxt], 0.0)
    sl, tp = px - side * (sld - COMM) / 10.0, px + side * (tpd + COMM) / 10.0
    return pd.DataFrame({"t_signal": F["tc"][i], "side": side, "entry_type": "market", "entry": np.nan,
                         "expire": F["tc"][i], "sl": sl, "tp": tp, "be_at_r": np.nan, "max_hold_min": float(hold)})


def run_one(F, spec, keep_trades=False):
    """spec = dict(name, trend=(tf,name), trig, hours, side, exit)"""
    i, side = signals(F, tuple(spec["trend"]), spec.get("trig", "G0_BODY"), spec.get("hours", "ALL"), spec.get("side", 0))
    if len(i) == 0:
        return None, None
    r = C.simulate(X.SYM, orders(F, i, side, spec["exit"]), one_at_a_time=True)
    st = X.stats(r, F["days"])
    st.update(variant=spec["name"], trend=f"{spec['trend'][0]} {spec['trend'][1]}", trig=spec.get("trig", "G0_BODY"),
              hours=spec.get("hours", "ALL"), side_only=spec.get("side", 0), exit=spec["exit"], signals=len(i))
    return st, (r[r.status == "filled"] if keep_trades else None)


# ------------------------------------------------------------------ the variant lists (fixed in PREREG.md)
BASE = dict(trend=("M15", "PX200"), trig="G0_BODY", hours="ALL", side=0, exit="E15_70")


def round3_list():
    v = [dict(BASE, name="R3_BASE_M15PX200")]
    for tf in ("M15", "M30"):
        for t in ("PX50", "PX100", "PX200+RSI", "PX200+EMA", "PX200+SLOPE"):
            v.append(dict(BASE, name=f"R3_T_{tf}_{t}", trend=(tf, t)))
    v.append(dict(BASE, name="R3_T_M30_PX200", trend=("M30", "PX200")))
    v.append(dict(BASE, name="R3_T_BOTH_PX200", trend=("BOTH", "PX200")))
    for g in ("T_TWO", "T_BIG", "T_STRONGCLOSE", "T_E20SIDE", "T_M5E20SIDE", "T_M5CANDLE"):
        v.append(dict(BASE, name=f"R3_G_{g}", trig=g))
    for e in EXITS:
        if e != "E15_70":
            v.append(dict(BASE, name=f"R3_E_{e}", exit=e))
    for h in HOURS:
        if h != "ALL":
            v.append(dict(BASE, name=f"R3_H_{h}", hours=h))
    v.append(dict(BASE, name="R3_S_BUYONLY", side=1))
    v.append(dict(BASE, name="R3_S_SELLONLY", side=-1))
    return v


def load_round(stage_files):
    return pd.concat([pd.read_csv(f) for f in stage_files], ignore_index=True)


def round4_list():
    d = pd.concat([pd.read_csv(os.path.join(OUTD, f"round3_{s}.csv")) for s in ("EXPLORE", "VALIDATE", "STRESS")])
    p = d.pivot_table(index="variant", columns="stage", values=["usd_trade", "fills"], aggfunc="first")
    mean = p["usd_trade"].mean(axis=1)
    ok = p["fills"]["EXPLORE"] >= 800
    r3 = {v["name"]: v for v in round3_list()}
    rank = mean[ok].sort_values(ascending=False)

    def top(prefix, k):
        return [n for n in rank.index if n.startswith(prefix)][:k]
    trends = top("R3_T_", 2) + ["R3_BASE_M15PX200"]
    trigs = [n for n in top("R3_G_", 2)]
    exits = top("R3_E_", 3)
    out, seen = [], set()
    for t in trends:
        for g in trigs + ["R3_BASE_M15PX200"]:
            for e in exits + ["R3_BASE_M15PX200"]:
                s = dict(BASE)
                s["trend"] = r3[t]["trend"]
                s["trig"] = r3[g]["trig"]
                s["exit"] = r3[e]["exit"]
                key = (tuple(s["trend"]), s["trig"], s["exit"])
                if key in seen:
                    continue
                seen.add(key)
                s["name"] = f"R4_{s['trend'][0]}{s['trend'][1]}_{s['trig']}_{s['exit']}"
                out.append(s)
    return out


def run_list(tag, stage, specs):
    t0 = time.time()
    F = add_trends(X.build_stage(stage))
    rows = []
    for sp in specs:
        t1 = time.time()
        st, _ = run_one(F, sp)
        if st:
            st["stage"] = stage
            rows.append(st)
            print(sp["name"], st.get("fills"), st.get("usd_trade"), round(time.time() - t1), "s", flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(OUTD, f"{tag}_{stage}.csv"), index=False)
    print("done", round(time.time() - t0), "s", flush=True)


# ------------------------------------------------------------------ controls for the shortlist
def run_controls(stage, specs, tag="controls2"):
    F = add_trends(X.build_stage(stage))
    pool_all = np.flatnonzero(F["in"])
    pool_all = pool_all[(pool_all > X.WARM) & (pool_all < F["n"] - 20)]
    rows = []
    for sp in specs:
        i, side = signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
        real, _ = run_one(F, sp)
        d = F["tr"][tuple(sp["trend"])]
        pool = pool_all[d[pool_all] != 0]
        if HOURS[sp.get("hours", "ALL")] is not None:
            hr = pd.to_datetime(F["tc"]).hour.values
            a, b = HOURS[sp["hours"]]
            pool = pool[(hr[pool] >= a) & (hr[pool] < b)]
        if sp.get("side", 0):
            pool = pool[d[pool] == sp["side"]]
        rows.append(dict(variant=sp["name"], control="REAL", stage=stage, seed=-1, usd_trade=real.get("usd_trade"), pf=real.get("pf"), fills=real.get("fills")))
        for sd in range(5):
            rng = np.random.default_rng(sd)
            pick = np.sort(rng.choice(pool, size=min(len(i), len(pool)), replace=False))
            r = C.simulate(X.SYM, orders(F, pick, d[pick], sp["exit"]), one_at_a_time=True)
            s2 = X.stats(r, F["days"])
            rows.append(dict(variant=sp["name"], control="TRENDRAND", stage=stage, seed=sd, usd_trade=s2.get("usd_trade"), pf=s2.get("pf"), fills=s2.get("fills")))
        print(sp["name"], stage, "real", real.get("usd_trade"), flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(OUTD, f"{tag}_{stage}.csv"), index=False)
    print("done", flush=True)


def exam():
    """Sealed period, run ONCE per feed (SMC_DATASET picks duka or zero). Needs AUTOPSY_EXAM_UNLOCK from the owner's approval."""
    feed = os.environ.get("SMC_DATASET", "zero")
    specs = json.load(open(os.path.join(OUTD, "shortlist.json")))
    F = add_trends(X.build_stage("EXAM"))
    ed = os.path.join(OUTD, "exam")
    os.makedirs(ed, exist_ok=True)
    rows = []
    for sp in specs:
        st, tr = run_one(F, sp, keep_trades=True)
        if st is None:
            continue
        st["feed"] = feed
        rows.append(st)
        tr.to_csv(os.path.join(ed, f"{sp['name']}__{feed}.csv"), index=False)
        print(sp["name"], feed, st["fills"], st["usd_trade"], flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(OUTD, f"exam_{feed}.csv"), index=False)
    run_controls("EXAM", specs, tag=f"exam_controls_{feed}")


if __name__ == "__main__":
    mode, stage = sys.argv[1], sys.argv[2]
    if mode == "exam":
        exam()
        sys.exit(0)
    if mode == "round3":
        run_list("round3", stage, round3_list())
    elif mode == "round4":
        run_list("round4", stage, round4_list())
    elif mode == "controls":
        run_controls(stage, json.load(open(os.path.join(OUTD, "shortlist.json"))))
