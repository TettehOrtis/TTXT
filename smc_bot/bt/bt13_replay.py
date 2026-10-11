"""BT13 hand replays: 3 HIST REF trades re-walked minute by minute on the RAW duka M1 file (not the bar cache, not
common.simulate), plus the zone touch checked on raw M1 and the CHoCH close checked on M5 built here from raw M1.

    .venv/Scripts/python.exe smc_bot/bt/bt13_replay.py
"""
import os, sys
os.environ["SMC_DATASET"] = "duka"
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "smc_bot", "results", "BT13")
COMM = None


def pipsz(s):
    return 0.1 if s == "XAUUSD" else (0.01 if "JPY" in s else 0.0001)


def raw_m1(sym):
    d = pd.read_parquet(os.path.join(ROOT, "data_duka", f"{sym}_M1.parquet"))
    pt = 0.001 if sym == "XAUUSD" else (0.001 if "JPY" in sym else 0.00001)
    d["ask_add"] = d.spread * pt                       # price units
    d["time"] = d.time.astype("datetime64[ns]")
    return d


def replay(tr, comm):
    sym, d = tr.sym, int(tr.side)
    m = raw_m1(sym)
    P = pipsz(sym)
    out = [f"### {sym} {'LONG' if d == 1 else 'SHORT'}  zone {tr.zone_kind} [{tr.zone_lo:.5f}, {tr.zone_hi:.5f}] created "
           f"{tr.t_zone}; touch {tr.t_touch}; CHoCH close / order {tr.t_signal}; entry {tr.entry:.5f} SL {tr.sl:.5f} TP {tr.tp:.5f}"]
    # 1) touch: first M1 bar from zone creation that trades to the proximal edge must lie in the M5 bar t_touch
    w = m[(m.time >= tr.t_zone) & (m.time < tr.t_zone + pd.Timedelta(days=5))]
    hit = w[(w.low <= tr.zone_hi)] if d == 1 else w[(w.high >= tr.zone_lo)]
    t_hit = hit.time.iloc[0]
    out.append(f"- touch: first raw M1 into zone at {t_hit} -> M5 bar {t_hit.floor('5min')} (engine: {tr.t_touch}) "
               f"{'OK' if t_hit.floor('5min') == tr.t_touch else 'MISMATCH'}")
    # 2) CHoCH: M5 bar closing at t_signal, built from raw M1
    a = tr.t_signal - pd.Timedelta(minutes=5)
    b5 = m[(m.time >= a) & (m.time < tr.t_signal)]
    out.append(f"- CHoCH M5 bar {a}: O {b5.open.iloc[0]:.5f} H {b5.high.max():.5f} L {b5.low.min():.5f} C {b5.close.iloc[-1]:.5f}"
               f" ({len(b5)} M1 bars)")
    # 3) fill: limit from t_signal to expire (60 min); buy fills when ask low <= entry
    w = m[(m.time >= tr.t_signal) & (m.time < tr.expire)]
    fh = w[(w.low + w.ask_add) <= tr.entry] if d == 1 else w[w.high >= tr.entry]
    tph = w[w.high >= tr.tp] if d == 1 else w[(w.low + w.ask_add) <= tr.tp]
    if not len(fh) or (len(tph) and tph.time.iloc[0] < fh.time.iloc[0]):
        out.append("- replay: NOT FILLED"); return out, None
    f = fh.iloc[0]
    fill = min(tr.entry, f.open + f.ask_add) if d == 1 else max(tr.entry, f.open)
    out.append(f"- fill: {f.time} at {fill:.5f} (engine {tr.t_fill} at {tr.fill:.5f})")
    # 4) walk forward minute by minute: SL before TP in the same bar, no TP on the fill bar, flat Fri 20:45 / before >6h gaps
    after = m[m.time >= f.time].reset_index(drop=True)
    ex = None
    for i, r in after.iterrows():
        lo_bid, hi_bid, ask_l, ask_h = r.low, r.high, r.low + r.ask_add, r.high + r.ask_add
        sl_hit = lo_bid <= tr.sl if d == 1 else ask_h >= tr.sl
        tp_hit = (hi_bid >= tr.tp if d == 1 else ask_l <= tr.tp) and i > 0
        if sl_hit:
            px = (min(tr.sl, r.open) if i > 0 else tr.sl) - 0.2 * P if d == 1 else \
                 ((max(tr.sl, r.open + r.ask_add) if i > 0 else tr.sl) + 0.2 * P)
            ex = (r.time, px, "SL"); break
        if tp_hit:
            ex = (r.time, tr.tp, "TP"); break
        nxt = after.time.iloc[i + 1] if i + 1 < len(after) else r.time
        if (r.time.dayofweek == 4 and r.time.hour * 60 + r.time.minute >= 20 * 60 + 45) or nxt - r.time > pd.Timedelta(hours=6):
            ex = (r.time, r.close if d == 1 else r.close + r.ask_add, "WEEKEND"); break
    t_ex, px, why = ex
    net = (px - fill) * d / P - comm
    Rr = net / ((fill - tr.sl) * d / P)
    out.append(f"- exit: {t_ex} at {px:.5f} {why} -> net {net:+.1f} pips, R {Rr:+.3f} (engine {tr.t_exit} {tr.exit:.5f} "
               f"{tr.exit_reason}, R {tr.R:+.3f}) {'OK' if abs(Rr - tr.R) < 1e-3 and why == tr.exit_reason else 'MISMATCH'}")
    seg = after[(after.time >= t_ex - pd.Timedelta(minutes=2)) & (after.time <= t_ex + pd.Timedelta(minutes=1))]
    out.append("  M1 around exit:\n" + seg[["time", "open", "high", "low", "close", "spread"]].to_string(index=False))
    return out, (abs(Rr - tr.R) < 1e-3 and why == tr.exit_reason)


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import common
    t = pd.read_csv(os.path.join(OUT, "trades_REF_HIST.csv"),
                    parse_dates=["t_signal", "t_zone", "t_touch", "t_fill", "t_exit", "expire"])
    t = t[(t.status == "filled") & (t.exit_reason.isin(["SL", "TP"])) & t.zone_lo.notna()]
    pick = pd.concat([t[(t.exit_reason == "TP")].sample(1, random_state=5),
                      t[(t.exit_reason == "SL")].sample(1, random_state=5),
                      (t[t.sym == "XAUUSD"] if (t.sym == "XAUUSD").any() else t[t.exit_reason == "TP"]).sample(1, random_state=7)])
    lines = []; ok = []
    for tr in pick.itertuples(index=False):
        o, good = replay(tr, common.COMM[tr.sym])
        lines += o + [""]; ok.append(good)
    txt = "\n".join(lines) + f"\nreplays matching the engine: {sum(bool(x) for x in ok)}/{len(ok)}\n"
    open(os.path.join(OUT, "hand_replays.txt"), "w").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
