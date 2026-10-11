"""BT17 — 10 example trades a human can check on a chart, plus a hand replay of three of them.

The reading shown is BT17's top-ranked one: FADE a one-minute break of structure (`B1_bos_M1_MIRROR`).

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt17_examples

Writes results/BT17/examples_<feed>.csv and prints the hand replay.
"""
import os
import numpy as np
import pandas as pd
from bt import bt17_lib as B
from bt import bt15_lib as L
from bt.common import simulate, COMM

VAR = "B1_bos_M1"


def main():
    COMM[B.GOLD] = 0.0
    b = B.bars(B.GOLD, "M1")
    t1 = b.time.values.astype("datetime64[ns]")
    nb = len(b)
    st = B.st_of(b, 5)
    e, s, k, _tf = B.signals(VAR)
    s = (-s).astype(np.int8)                           # the MIRROR: fade the break
    m = (e < nb - 3) & B.span_mask(t1[np.minimum(e, nb - 1)], *B.SPAN)
    e, s, k = e[m], s[m], k[m]

    rng = np.random.default_rng(17)
    pick = np.sort(rng.choice(len(e), size=10, replace=False))
    o = L.basket_orders(b, e[pick], 1, B.T_CAP, B.S_CAP, delta=B.HIS_SPREAD)
    o_s = L.basket_orders(b, e[pick], -1, B.T_CAP, B.S_CAP, delta=B.HIS_SPREAD)
    rl = simulate(B.GOLD, o, one_at_a_time=False).set_index("opp")
    rs = simulate(B.GOLD, o_s, one_at_a_time=False).set_index("opp")

    rows = []
    for i, p in enumerate(pick):
        kk, ee, dd = int(k[p]), int(e[p]), int(s[p])
        r = (rl if dd == 1 else rs).loc[i]
        rows.append(dict(
            signal_bar_close=str(pd.Timestamp(b.tclose.values[kk])),
            break_was="bull BOS (close above the last swing high)" if st["evt"][kk] > 0 else
                      "bear BOS (close below the last swing low)",
            swing_level=round(float(st["lsh"][kk] if st["evt"][kk] > 0 else st["lsl"][kk]), 2),
            signal_bar_close_price=round(float(b.close.values[kk]), 2),
            we_trade="SELL (fade the up-break)" if dd == -1 else "BUY (fade the down-break)",
            fill_time=str(pd.Timestamp(t1[ee])), fill_price=round(float(r.fill), 3),
            profit_cap_at=round(float(r.tp), 3), loss_cap_at=round(float(r.sl), 3),
            exit_time=str(pd.Timestamp(r.t_exit)), exit_price=round(float(r["exit"]), 3),
            closed_by=r.exit_reason, minutes_held=int(round(r.hold_h * 60)),
            dollars_at_010_lots=round(float(r.usd_01 - B.HIS_SPREAD * B.USD_PER_PRICE), 2)))
    d = pd.DataFrame(rows)
    f = os.path.join(B.OUT, f"examples_{B.DS}.csv")
    d.to_csv(f, index=False)
    print(f"10 example trades, feed={B.DS}, FADE a one-minute break, 0.10 lots -> {f}\n")
    print(d[["signal_bar_close", "break_was", "swing_level", "we_trade", "fill_price", "exit_price",
             "closed_by", "minutes_held", "dollars_at_010_lots"]].to_string(index=False))

    # ---- hand replay: re-derive the first three outcomes from raw bars, no simulator
    print("\nhand replay of the first three, straight off the M1 bars (no simulator):")
    ok = True
    for i in range(3):
        r = d.iloc[i]
        ee = int(np.searchsorted(t1, np.datetime64(r.fill_time), "left"))
        dd = -1 if r.we_trade.startswith("SELL") else 1
        tp, sl = float(r.profit_cap_at), float(r.loss_cap_at)
        hit = None
        for j in range(ee, min(ee + 5000, nb)):
            lo, hi, sp = b.low.values[j], b.high.values[j], b.spr.values[j]
            if dd == 1:
                if lo <= sl:
                    hit = (j, "SL"); break
                if j > ee and hi >= tp:
                    hit = (j, "TP"); break
            else:
                if hi + sp >= sl:
                    hit = (j, "SL"); break
                if j > ee and lo + sp <= tp:
                    hit = (j, "TP"); break
        got = hit[1] if hit else "none"
        same = got == r.closed_by
        ok &= same
        print(f"  {r.signal_bar_close}  simulator said {r.closed_by} after {r.minutes_held} min; "
              f"by hand {got} after {(hit[0]-ee) if hit else -1} min  -> {'MATCH' if same else 'MISMATCH'}")
    print("\nhand replay:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
