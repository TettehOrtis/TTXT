"""BT17 phase 2 — score the 16 pre-registered SMC variants on one feed.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt17_run                        # zero (2021..2025-06)
    cd smc_bot && SMC_DATASET=duka ../.venv/Scripts/python.exe -m bt.bt17_run       # duka (2009..2020)

Writes, per feed, into results/BT17/parts/:
    var_<feed>.csv      one row per variant per date period (pooled + the three periods)
    year_<feed>.csv     one row per variant per calendar year
    hour_<feed>.csv     one row per variant per hour of day (0-23), per date period
    sess_<feed>.csv     one row per variant per session, per date period
    placebo_<feed>.json the block-rotation acceptance bar (best-of-16, 200 draws)
Each is written as soon as it is finished, so a sleep loses nothing.
"""
import json
import os
import time
import numpy as np
import pandas as pd
from bt import bt17_lib as B


def hour_of(t_ns):
    return ((t_ns // 3_600_000_000_000) % 24).astype(np.int64)


def sess_of(h):
    out = np.full(len(h), "Late", dtype=object)
    for name, a, b in B.SESSIONS:
        out[(h >= a) & (h < b)] = name
    return out


def rows_for(pnl, t_ns, entry, side, name, fam, tf, dirn):
    """Pooled + per-period + per-year + per-hour + per-session rows for one variant."""
    var, yr, hr, se = [], [], [], []
    t = t_ns[entry]

    def one(e, s, label):
        sc = B.score(pnl, e, s)
        if sc.get("n", 0) == 0:
            return None
        ee = e[sc["mask"]]
        chosen = np.where(s[sc["mask"]] == 1, pnl[0, ee], pnl[1, ee]).astype(np.float64)
        days = max(1.0, (t_ns[ee].max() - t_ns[ee].min()) / 86_400_000_000_000)
        return dict(variant=name, family=fam, tf=tf, direction=dirn, period=label, trades=sc["n"],
                    acc=round(sc["acc"], 2), money=round(sc["money"], 3), own_be=round(sc["own_be"], 2),
                    edge_vs_be=round(sc["acc"] - sc["own_be"], 2), win_pct=round(sc["win_pct"], 1),
                    decisive_pct=round(sc["decisive_pct"], 1), per_day=round(sc["n"] / days, 2),
                    money_per_day=round(sc["money"] * sc["n"] / days, 2),
                    t_week=round(B.week_block_t(chosen, t_ns[ee]), 2))

    r = one(entry, side, "POOLED")
    if r:
        var.append(r)
    for label, a, bnd in B.PERIODS3:
        m = B.span_mask(t.astype("datetime64[ns]"), a, bnd)
        if m.sum() < 30:
            continue
        r = one(entry[m], side[m], label)
        if r:
            var.append(r)
        e2, s2 = entry[m], side[m]
        h = hour_of(t_ns[e2])
        for hh in range(24):
            mh = h == hh
            if mh.sum() < 30:
                continue
            r = one(e2[mh], s2[mh], label)
            if r:
                hr.append({**r, "hour": hh})
        ss = sess_of(h)
        for sname, _, _ in B.SESSIONS:
            ms = ss == sname
            if ms.sum() < 30:
                continue
            r = one(e2[ms], s2[ms], label)
            if r:
                se.append({**r, "session": sname})
    years = pd.DatetimeIndex(t.astype("datetime64[ns]")).year.values
    for y in np.unique(years):
        m = years == y
        if m.sum() < 30:
            continue
        r = one(entry[m], side[m], str(y))
        if r:
            yr.append(r)
    return var, yr, hr, se


def main():
    t0 = time.time()
    print(f"BT17 variants, feed={B.DS}, span {B.SPAN[0]}..{B.SPAN[1]}", flush=True)
    pnl, _tex = B.outcome_table()
    m1 = B.bars(B.GOLD, "M1")
    t_ns = m1.time.values.astype("datetime64[ns]").astype(np.int64)
    nb = len(m1)

    sigs, V, Y, H, S = {}, [], [], [], []
    for name, fam, tf, dirn in B.VARIANTS:
        e, s, _k, _tf = B.signals(name)
        m = (e < nb - 3) & B.span_mask(t_ns[np.minimum(e, nb - 1)].astype("datetime64[ns]"), *B.SPAN)
        e, s = e[m], s[m].astype(np.int8)
        sigs[name] = (e, s, _k[m], tf)
        # both signs, always. On an identical trigger set the mirror is exactly 100 - accuracy, so it is
        # reported, not counted as a 17th reading (BT17_BRIEF). Its MONEY is measured, not derived.
        mirror = "fade" if dirn == "follow" else "follow"
        for nm, sd, dn in ((name, s, dirn), (name + "_MIRROR", (-s).astype(np.int8), mirror)):
            v, y, h, ss = rows_for(pnl, t_ns, e, sd, nm, fam, tf, dn)
            V += v
            Y += y
            H += h
            S += ss
            p = [r for r in v if r["period"] == "POOLED"]
            if p:
                print(f"  {nm:<30} {p[0]['trades']:>8,} chances  {p[0]['acc']:>6.2f}%  "
                      f"${p[0]['money']:>+7.3f}/basket  (break-even {p[0]['own_be']:.2f}%)", flush=True)
            else:
                print(f"  {nm:<30} no chances", flush=True)
        pd.DataFrame(V).to_csv(os.path.join(B.PARTS, f"var_{B.DS}.csv"), index=False)
        pd.DataFrame(Y).to_csv(os.path.join(B.PARTS, f"year_{B.DS}.csv"), index=False)
        pd.DataFrame(H).to_csv(os.path.join(B.PARTS, f"hour_{B.DS}.csv"), index=False)
        pd.DataFrame(S).to_csv(os.path.join(B.PARTS, f"sess_{B.DS}.csv"), index=False)

    # ---- the money ladder for this feed, and the all-minutes baseline
    base = np.where(np.isfinite(pnl).all(axis=0))[0]
    lad = {k: round(v, 3) for k, v in B.money_ladder(pnl, base).items()}

    # ---- the acceptance bar
    print("\n  block-rotation placebo, best-of-16, 200 draws ...", flush=True)
    pb = B.rotation_placebo(pnl, sigs, lambda e: t_ns[e], draws=200)
    for name in B.VAR_NAMES:
        e, s, _, _ = sigs[name]
        if len(e) > 200:
            pb.setdefault("ladders", {})[name] = {k: round(v, 3) for k, v in B.money_ladder(pnl, e).items()}
    pb["baseline_all_minutes"] = lad
    pb["n_minutes_usable"] = int(len(base))
    json.dump(pb, open(os.path.join(B.PARTS, f"placebo_{B.DS}.json"), "w"), indent=1)
    print(f"  bar (two-sided, both signs reported): accuracy P95 of best-of-16 = {pb['acc2_P95']:.2f}%  "
          f"money P95 = ${pb['money2_P95']:+.3f}/basket")
    print(f"\n  {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
