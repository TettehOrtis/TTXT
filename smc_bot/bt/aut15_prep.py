"""AUT15 prep: registry of saved order sources -> slim parquet files in results/AUT15_costcut/_work/orders/.
Writes only to AUT15_costcut/_work. Reads other studies' result files (read-only)."""
import os
import sys
import glob
import json

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import common as C                                                        # noqa: E402
import autopsy_lib as A                                                   # noqa: E402

R = C.RESULTS
WORK = os.path.join(R, "AUT15_costcut", "_work")
ORD = os.path.join(WORK, "orders")
os.makedirs(ORD, exist_ok=True)

# (study, src_label, relpath, views) ; views: list of (view_name, sym filter or None)
G = [("GOLD", ["XAUUSD"]), ("ALL", None)]
GONLY = [("GOLD", ["XAUUSD"])]
ALLV = [("ALL", None)]
SRC = []


def add(study, label, rel, views):
    SRC.append(dict(study=study, label=label, rel=rel, views=views))


for lab in ("R0_baseline", "R1_shape", "R2_shape_filter", "R3_balanced"):
    add("AUT01", lab, "AUT01_emapull/trades_%s_ALLSTAGES.csv" % lab, G)
add("AUT01", "LIVE_AS_SHIPPED", "AUT01_emapull/trades_live_as_shipped_CORRECTED_ALLSTAGES.csv", GONLY)
for lab in ("R0_raw", "R1_stretched", "R2_stretched_NYoverlap", "R3_stretched_overlap_240"):
    add("AUT02", lab + "_M15N20_wick2R", "AUT02_sweep/trades_%s_ALLSTAGES.csv" % lab, GONLY)
for lab in ("BASE_immediate", "BASE_immediate_matched", "BASE_retest", "BASE_retest_failed_fade", "V1_retest_room",
            "V2_immediate_room", "V3_fade_failed_quiet"):
    add("AUT03", lab, "AUT03_breakretest/trades_%s_ALLSTAGES.csv" % lab, GONLY)
add("AUT04", "R0_all_follow", "AUT04_orb/tr_BASE_follow_EXPLORE.parquet", GONLY)
add("AUT04", "BASE_fade", "AUT04_orb/tr_BASE_fade_EXPLORE.parquet", GONLY)
add("AUT04", "LONDON_4x4_best", "AUT04_orb/trades_LONDON_4x4_best_EXPLORE.csv", GONLY)
for lab in ("R1_shape", "R2_youngvwap", "R3_onlevel"):
    add("AUT05", lab, "AUT05_vwap/trades_%s_ALLSTAGES.csv" % lab, GONLY)
add("AUT06", "EXPLORE_baseline", "AUT06_orderblock/trades_EXPLORE_baseline.csv", G)
for lab in ("R1_shape_fullfill", "R2_displacement_nohole", "R3_ny_session"):
    for st in ("EXPLORE", "VALIDATE", "STRESS"):
        pass
    add("AUT07", lab, ["AUT07_fvg/trades_%s_%s.csv" % (lab, st) for st in ("EXPLORE", "VALIDATE", "STRESS")], G)
for lab in ("BASELINE", "R1_overlap_legstop", "R2_overlap_body_h4", "R3_shape_only_1x4"):
    add("AUT08", lab, "AUT08_bos/trades_%s_ALLSTAGES.csv" % lab, G)
add("AUT09", "COST_FILTER_promising", "AUT09_timeofday/trades_COST_FILTER_promising.csv", G)
add("AUT09", "MAP_allhours", sorted(glob.glob(os.path.join(R, "AUT09_timeofday", "trades_MAP_EXPLORE_*.csv"))), G)
for lab in ("base", "R1_fade_bestshape", "R2_fade_overlap", "R3_follow_bestshape"):
    add("AUT10", lab, "AUT10_momentum/trades_%s_ALLSTAGES.csv" % lab, G)
for lab in ("R1", "R2", "R3"):
    add("AUT12", lab, "AUT12_clock/trades_%s_ALL.csv" % lab, [("GOLD", ["XAUUSD"]), ("ALL", None), ("EURUSD", ["EURUSD"]), ("GBPUSD", ["GBPUSD"]), ("USDJPY", ["USDJPY"])])
for lab in ("E_LFIX_AM", "E_DATA", "E_NFP"):
    add("AUT12", lab, "AUT12_clock/trades_%s_EXPLORE.csv" % lab, [("ALL", None)])
for s in ("EURUSD", "XAUUSD"):
    for lab in ("BASE", "R1_T4", "R2_E2", "R3_L8"):
        add("AUT13", "%s_%s" % (s, lab), "AUT13_leadlag/trades_%s_%s_ALLSTAGES.csv" % (s, lab), [("ALL", None)])
for lab in ("C1_B", "C2_B", "C3_B", "S1_B", "S3_B"):
    add("AUT14", lab, "AUT14_combo/trades_%s_ALLSTAGES.csv" % lab, GONLY)

KEEP = ["sym", "t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min",
        "usd_01", "exit_reason", "hold_h", "status", "t_fill", "fill"]


def load_one(path):
    if path.endswith(".parquet"):
        d = pd.read_parquet(path)
    else:
        d = pd.read_csv(path, usecols=lambda c: c in KEEP)
    return d


def build(entry):
    paths = entry["rel"] if isinstance(entry["rel"], list) else [entry["rel"]]
    frames = []
    for p in paths:
        p = p if os.path.isabs(p) else os.path.join(R, p)
        d = load_one(p)
        if "sym" not in d.columns:
            d["sym"] = "XAUUSD"
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    d["t_signal"] = pd.to_datetime(d["t_signal"])
    if "status" in d.columns:
        d = d[d.status == "filled"]
    # AUT07 gold ALLSTAGES = gold slice of the per-stage files: no duplicate issue because we read the per-stage files only
    d["stage"] = A.stage_of(d["t_signal"])
    d = d[d.stage.isin(["EXPLORE", "VALIDATE", "STRESS"])].reset_index(drop=True)
    hold = None
    if "max_hold_min" not in d.columns or d["max_hold_min"].isna().all():
        if "exit_reason" in d.columns and (d.exit_reason == "TIME").any():
            hold = float(np.round(d.loc[d.exit_reason == "TIME", "hold_h"].max() * 60.0))
        d["max_hold_min"] = hold if hold is not None else np.nan
    out = d[[c for c in ["sym", "t_signal", "side", "entry_type", "entry", "expire", "sl", "tp", "be_at_r", "max_hold_min", "usd_01", "stage"] if c in d.columns]].copy()
    if "entry_type" not in out:
        out["entry_type"] = "market"
    out["entry_type"] = out["entry_type"].fillna("market")
    for c in ("entry", "be_at_r"):
        if c not in out:
            out[c] = np.nan
    if "expire" not in out:
        out["expire"] = out["t_signal"]
    out["expire"] = pd.to_datetime(out["expire"]).fillna(out["t_signal"])
    out["row"] = np.arange(len(out))
    return out, hold


if __name__ == "__main__":
    info = []
    for e in SRC:
        key = "%s__%s" % (e["study"], e["label"])
        o, hold = build(e)
        o.to_parquet(os.path.join(ORD, key + ".parquet"), index=False)
        cnt = o.groupby("stage").size().to_dict()
        print(key, len(o), cnt, "hold_inferred", hold, flush=True)
        info.append(dict(key=key, study=e["study"], label=e["label"], n=len(o), stages=cnt, hold_inferred=hold,
                         views=[(v, s) for v, s in e["views"]]))
    json.dump(info, open(os.path.join(WORK, "registry.json"), "w"), indent=1, default=str)
