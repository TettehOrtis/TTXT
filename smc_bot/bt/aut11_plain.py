"""AUT11: build all-stage trade files and run tools/plain_report.py on every trades CSV (full JSON/MD/READABLE)."""
import os, sys, glob, subprocess, json
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import common as C
OUT = os.path.join(C.RESULTS, "AUT11_trend")
ROOT = os.path.dirname(C.SMC)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PR = os.path.join(C.SMC, "tools", "plain_report.py")
files = [f for f in glob.glob(os.path.join(OUT, "trades_*.csv")) if "READABLE" not in f and "ALLSTAGES" not in f]
keys = {}
for f in files:
    b = os.path.basename(f)[len("trades_"):-4]
    st, key = b.split("_", 1)
    keys.setdefault(key, {})[st] = f
best = json.load(open(os.path.join(OUT, "refinements_best.json")))
made = []
for key, d in sorted(keys.items()):
    parts = []
    for st in ("EXPLORE", "VALIDATE", "STRESS"):
        if st in d:
            t = pd.read_csv(d[st])
            t["period"] = st
            parts.append(t)
    al = pd.concat(parts, ignore_index=True)
    out = os.path.join(OUT, "trades_%s_ALLSTAGES.csv" % key)
    al.to_csv(out, index=False)
    made.append(out)
todo = made + [f for d in keys.values() for f in d.values()]
for f in todo:
    nm = os.path.basename(f)[len("trades_"):-4]
    r = subprocess.run([PY, PR, f, "--name", "AUT11 " + nm, "--risk", "0", "--lot", "0.10", "--balance", "10000"],
                       capture_output=True, text=True, cwd=ROOT)
    print(nm, "ok" if r.returncode == 0 else "FAIL " + r.stderr[-200:], flush=True)
