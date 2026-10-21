"""AUT15 Part A runner: per symbol, replay every saved order source at cost factors 1.0 and 0.0.
Usage: python aut15_runA.py [workers]   (writes results/AUT15_costcut/_work/A/<sym>.parquet)"""
import os
import sys
import glob
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def run_sym(args):
    sym, factors, orders_dir, out_dir = args
    import aut15_costcut as K
    import pandas as pd
    import numpy as np
    t0 = time.time()
    rows = []
    for f in sorted(glob.glob(os.path.join(orders_dir, "*.parquet"))):
        key = os.path.splitext(os.path.basename(f))[0]
        d = pd.read_parquet(f)
        d = d[d.sym == sym]
        if len(d) == 0:
            continue
        res = {}
        for fac in factors:
            r = K.resim(d, fac)
            res[fac] = r
        o = pd.DataFrame({"key": key, "sym": sym, "row": d["row"].values})
        for fac in factors:
            tag = ("%g" % fac).replace(".", "p")
            o["usd_" + tag] = res[fac].usd_01.values
            o["st_" + tag] = (res[fac].status.values == "filled")
        rows.append(o)
        print("%s %s n=%d  %.0fs" % (sym, key, len(d), time.time() - t0), flush=True)
    if rows:
        pd.concat(rows, ignore_index=True).to_parquet(os.path.join(out_dir, sym + ".parquet"), index=False)
    return sym, time.time() - t0


if __name__ == "__main__":
    import pandas as pd
    import aut15_costcut as K
    nw = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    orders_dir = os.path.join(K.WORK, sys.argv[2] if len(sys.argv) > 2 else "orders")
    out_dir = os.path.join(K.WORK, sys.argv[3] if len(sys.argv) > 3 else "A")
    FACS = [float(x) for x in sys.argv[4].split(",")] if len(sys.argv) > 4 else [1.0, 0.0]
    os.makedirs(out_dir, exist_ok=True)
    cnt = {}
    for f in glob.glob(os.path.join(orders_dir, "*.parquet")):
        for s, n in pd.read_parquet(f, columns=["sym"]).sym.value_counts().items():
            cnt[s] = cnt.get(s, 0) + n
    syms = sorted(cnt, key=lambda s: -cnt[s])
    print("symbols by load:", {s: cnt[s] for s in syms}, flush=True)
    jobs = [(s, FACS, orders_dir, out_dir) for s in syms if not os.path.exists(os.path.join(out_dir, s + ".parquet"))]
    with ProcessPoolExecutor(max_workers=nw) as ex:
        for s, secs in ex.map(run_sym, jobs):
            print("DONE", s, round(secs), "s", flush=True)
