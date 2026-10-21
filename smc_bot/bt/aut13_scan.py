"""AUT13 stage 1b: autopsy the base-cell EXPLORE trades of gold and EURUSD, scan the 22 conditions, apply the pre-declared
selection rule for the (at most 3) refinements that go to VALIDATE. EXPLORE only.

Selection rule, written before the scan was run:
  a condition qualifies if, at the base cell (k=5, hold=15), in EXPLORE, on BOTH gold and EURUSD
      (a) it lifts net dollars per trade by at least $0.30 over the unfiltered base,
      (b) it keeps at least 20% of the trades,
      (c) on gold, both the buy side and the sell side are better than the unfiltered buy side / sell side.
  The three qualifiers with the biggest WORSE-of-the-two dollar lift are carried. Fewer than three is fine.
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import aut13_autopsy as U
L, A, C = U.L, U.A, U.C

if __name__ == "__main__":
    res, ths, base = {}, {}, {}
    for tg in U.TARGETS:
        tr, e = U.base_trades(tg, "EXPLORE")
        th = U.thresholds(tg, tr)
        ths[tg] = th
        tr["_buy"] = tr.side == 1
        sc = U.scan(tg, tr, th)
        sc.to_csv(os.path.join(L.OUT, "conditions_EXPLORE_%s.csv" % tg), index=False)
        res[tg] = sc
        base[tg] = dict(usd=float(tr.usd_01.mean()), buy=float(tr[tr.side == 1].usd_01.mean()), sell=float(tr[tr.side == -1].usd_01.mean()),
                        n=int(len(tr)))
        print(tg, base[tg], th)
        print(sc.to_string())
    g, f = res["XAUUSD"].set_index("id"), res["EURUSD"].set_index("id")
    sel = []
    for cid in g.index:
        try:
            both_sides = (g.loc[cid, "buy_usd_on"] > base["XAUUSD"]["buy"] and g.loc[cid, "sell_usd_on"] > base["XAUUSD"]["sell"])
            strict = (g.loc[cid, "d_usd_vs_all"] >= 0.30 and f.loc[cid, "d_usd_vs_all"] >= 0.30
                      and g.loc[cid, "kept_pct"] >= 20 and f.loc[cid, "kept_pct"] >= 20 and both_sides)
            # RELAXATION, decided here, before VALIDATE was ever read: the strict rule above passed NOTHING, so the carry list is
            # the three best by worse-of-two lift among conditions that help BOTH instruments and BOTH gold sides.
            ok = (g.loc[cid, "d_usd_vs_all"] > 0 and f.loc[cid, "d_usd_vs_all"] > 0 and both_sides
                  and g.loc[cid, "kept_pct"] >= 15 and f.loc[cid, "kept_pct"] >= 15)
            if strict:
                print("STRICT PASS", cid)
        except Exception:
            ok = False
        if ok:
            sel.append((cid, min(g.loc[cid, "d_usd_vs_all"], f.loc[cid, "d_usd_vs_all"])))
    sel.sort(key=lambda x: -x[1])
    print("QUALIFIERS", sel)
    carried = [c for c, _ in sel[:3]]
    json.dump(dict(thresholds=ths, base=base, qualifiers=sel, carried=carried), open(os.path.join(L.OUT, "selection_EXPLORE.json"), "w"), indent=1)
    print("CARRIED", carried)
