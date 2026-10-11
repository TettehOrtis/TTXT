"""BT18 phase 2 — score the 25 pre-registered variants on one feed.

    cd smc_bot && ../.venv/Scripts/python.exe -m bt.bt18_run                       # zero (2021..2025-06)
    cd smc_bot && SMC_DATASET=duka ../.venv/Scripts/python.exe -m bt.bt18_run      # duka (2009..2020)

Writes, per feed, into results/BT18/parts/ (each file as soon as it is finished, so a sleep loses nothing):
    var_<feed>.csv        one row per reading per scope (POOLED, the three date periods, break-excluded views)
    year_<feed>.csv       one row per reading per calendar year
    hour_<feed>.csv       one row per reading per hour of day, per date period
    sess_<feed>.csv       one row per reading per session, per date period
    spikedrop_<feed>.csv  Idea 1 only: the up-spike half and the down-drop half separately
    placebo_<feed>.json   the block-rotation acceptance bar (best-of-25, 200 draws)
Every money figure is dollars per basket at 0.10 lots.
"""
import json
import os
import time
import numpy as np
import pandas as pd
from bt import bt18_lib as B


def hour_of(t_ns):
    return ((t_ns // 3_600_000_000_000) % 24).astype(np.int64)


def sess_of(h):
    out = np.full(len(h), "Late", dtype=object)
    for name, a, b in B.SESSIONS:
        out[(h >= a) & (h < b)] = name
    return out


def one(pnl, t_ns, e, s, name, fam, dirn, label):
    sc = B.score(pnl, e, s)
    if sc.get("n", 0) == 0:
        return None
    ee = e[sc["mask"]]
    chosen = np.where(s[sc["mask"]] == 1, pnl[0, ee], pnl[1, ee]).astype(np.float64)
    days = max(1.0, (t_ns[ee].max() - t_ns[ee].min()) / 86_400_000_000_000)
    return dict(variant=name, family=fam, direction=dirn, period=label, trades=sc["n"],
                acc=round(sc["acc"], 2), money=round(sc["money"], 3), own_be=round(sc["own_be"], 2),
                edge_vs_be=round(sc["acc"] - sc["own_be"], 2), win_pct=round(sc["win_pct"], 1),
                decisive_pct=round(sc["decisive_pct"], 1), per_day=round(sc["n"] / days, 2),
                money_per_day=round(sc["money"] * sc["n"] / days, 2),
                t_week=round(B.week_block_t(chosen, t_ns[ee]), 2))


def rows_for(pnl, t_ns, entry, side, name, fam, dirn, brk, wbrk, spike_side=None):
    var, yr, hr, se, sd_rows = [], [], [], [], []
    t = t_ns[entry]
    td = t.astype("datetime64[ns]")

    def add(e, s, label, bucket):
        r = one(pnl, t_ns, e, s, name, fam, dirn, label)
        if r:
            bucket.append(r)
        return r

    add(entry, side, "POOLED", var)
    nb = ~brk[entry]
    add(entry[nb], side[nb], "POOLED_no_broker_break", var)
    nw = ~wbrk[entry]
    add(entry[nw], side[nw], "POOLED_no_hours_21_22", var)
    inb = brk[entry]
    if inb.sum() >= 30:
        add(entry[inb], side[inb], "INSIDE_broker_break_UNTRADEABLE", var)

    for label, a, z in B.PERIODS3:
        m = B.span_mask(td, a, z)
        if m.sum() < 30:
            continue
        add(entry[m], side[m], label, var)
        m2 = m & ~brk[entry]
        if m2.sum() >= 30:
            add(entry[m2], side[m2], label + "_no_broker_break", var)
        e2, s2 = entry[m], side[m]
        h = hour_of(t_ns[e2])
        for hh in range(24):
            mh = h == hh
            if mh.sum() >= 30:
                r = one(pnl, t_ns, e2[mh], s2[mh], name, fam, dirn, label)
                if r:
                    hr.append({**r, "hour": hh})
        ss = sess_of(h)
        for sname, _, _ in B.SESSIONS:
            ms = ss == sname
            if ms.sum() >= 30:
                r = one(pnl, t_ns, e2[ms], s2[ms], name, fam, dirn, label)
                if r:
                    se.append({**r, "session": sname})
        if spike_side is not None:
            for tag, want in (("up_spike", 1), ("down_drop", -1)):
                mm = m & (spike_side == want)
                if mm.sum() >= 30:
                    r = one(pnl, t_ns, entry[mm], side[mm], name, fam, dirn, label)
                    if r:
                        sd_rows.append({**r, "half": tag})
    if spike_side is not None:
        for tag, want in (("up_spike", 1), ("down_drop", -1)):
            mm = spike_side == want
            if mm.sum() >= 30:
                r = one(pnl, t_ns, entry[mm], side[mm], name, fam, dirn, "POOLED")
                if r:
                    sd_rows.append({**r, "half": tag})

    years = pd.DatetimeIndex(td).year.values
    for y in np.unique(years):
        m = years == y
        if m.sum() >= 30:
            add(entry[m], side[m], str(y), yr)
    return var, yr, hr, se, sd_rows


def main():
    t0 = time.time()
    print(f"BT18 variants, feed={B.DS}, span {B.SPAN[0]}..{B.SPAN[1]}", flush=True)
    print(f"  ${B.T_CAP*10:.0f} profit cap / ${B.S_CAP*10:.0f} loss cap at 0.10 lots, no commission, "
          f"{B.HIS_SPREAD} spread, break-even {B.BREAKEVEN} right in 100", flush=True)
    pnl, _tex = B.outcome_table()
    m1 = B.bars(B.GOLD, "M1")
    t_ns = m1.time.values.astype("datetime64[ns]").astype(np.int64)
    nb = len(m1)
    brk = B.break_mask(m1.time)
    wbrk = B.wide_break_mask(m1.time)
    print(f"  broker break covers {100*brk.mean():.1f}% of minutes", flush=True)

    V, Y, H, S, SD, prep, LAD, EX = [], [], [], [], [], [], [], []
    for name, fam, dirn in [(v[0], v[1], v[2]) for v in B.VARIANTS]:
        e, s, k = B.signals(name, m1)
        m = (e < nb - 3) & (e >= 0)
        m &= B.span_mask(t_ns[np.minimum(np.maximum(e, 0), nb - 1)].astype("datetime64[ns]"), *B.SPAN)
        e, s, k = e[m], s[m].astype(np.int8), k[m]
        if len(e) == 0:
            print(f"  {name:<28} no chances in this feed", flush=True)
            continue
        spike = (-s).astype(np.int8) if fam.startswith("1") else None   # the sign of the MOVE we faded
        mirror = "fade" if dirn == "follow" else "follow"
        for nm, sv, dn, sp in ((name, s, dirn, spike), (name + "_MIRROR", (-s).astype(np.int8), mirror, None)):
            v, y, h, ss, sdr = rows_for(pnl, t_ns, e, sv, nm, fam, dn, brk, wbrk, sp)
            V += v
            Y += y
            H += h
            S += ss
            SD += sdr
            p = [r for r in v if r["period"] == "POOLED"]
            if p:
                print(f"  {nm:<34} {p[0]['trades']:>9,} chances {p[0]['per_day']:>7.1f}/day  "
                      f"{p[0]['acc']:>6.2f} right in 100  ${p[0]['money']:>+7.3f}/basket  "
                      f"(its break-even {p[0]['own_be']:.2f})", flush=True)
        # what this reading's own chance set pays as accuracy rises -- a property of the chances, not a result
        lad = B.money_ladder(pnl, e, accs=(50.0, B.BREAKEVEN, 54.0, 55.0, 56.0, 58.0))
        LAD.append(dict(variant=name, feed=B.DS, **{f"acc_{k}": round(v, 3) for k, v in lad.items()}))
        pd.DataFrame(LAD).to_csv(os.path.join(B.PARTS, f"ladder_{B.DS}.csv"), index=False)

        # ten example chances a human can check on a chart (from the declared sign)
        rg = np.random.default_rng(18)
        sc0 = B.score(pnl, e, s)
        if sc0.get("n", 0) >= 10:
            ee, ss = e[sc0["mask"]], s[sc0["mask"]]
            kk = k[sc0["mask"]]
            pick = np.sort(rg.choice(len(ee), 10, replace=False))
            for j in pick:
                i, d = int(ee[j]), int(ss[j])
                EX.append(dict(variant=name, signal_bar_close=str(pd.Timestamp(t_ns[i])),
                               signal_bar_open=str(pd.Timestamp(t_ns[kk[j]])),
                               move_of_signal_bar=round(float(m1.close.values[kk[j]] - m1.open.values[kk[j]]), 3),
                               we_do="BUY" if d == 1 else "SELL",
                               fill_open_of_next_minute=round(float(m1.open.values[i]), 3),
                               usd_at_010_lots=round(float(pnl[0 if d == 1 else 1, i]), 2),
                               the_other_side_would_have_paid=round(float(pnl[1 if d == 1 else 0, i]), 2)))
            pd.DataFrame(EX).to_csv(os.path.join(B.PARTS, f"examples_{B.DS}.csv"), index=False)

        # placebo input: the real (declared) sign only; both signs come out of the two-sided bar
        sc = B.score(pnl, e, s)
        if sc.get("n", 0) >= 200:
            ee = e[sc["mask"]]
            days = max(1.0, (t_ns[ee].max() - t_ns[ee].min()) / 86_400_000_000_000)
            prep.append((name, s[sc["mask"]], pnl[0, ee].astype(np.float64),
                         pnl[1, ee].astype(np.float64), len(ee) / days))
        pd.DataFrame(V).to_csv(os.path.join(B.PARTS, f"var_{B.DS}.csv"), index=False)
        pd.DataFrame(Y).to_csv(os.path.join(B.PARTS, f"year_{B.DS}.csv"), index=False)
        pd.DataFrame(H).to_csv(os.path.join(B.PARTS, f"hour_{B.DS}.csv"), index=False)
        pd.DataFrame(S).to_csv(os.path.join(B.PARTS, f"sess_{B.DS}.csv"), index=False)
        pd.DataFrame(SD).to_csv(os.path.join(B.PARTS, f"spikedrop_{B.DS}.csv"), index=False)

    pf = os.path.join(B.PARTS, f"placebo_{B.DS}.json")
    if not os.path.exists(pf):
        print(f"  placebo: rotating {len(prep)} readings, 200 draws", flush=True)
        pb = B.rotation_placebo(pnl, prep, draws=200)
        json.dump(pb, open(pf, "w"), indent=1)
        print(f"  bar (two-sided, best of {pb['n_readings']}): {pb['acc2_P95']:.2f} right in 100, "
              f"${pb['money2_P95']:+.3f}/basket", flush=True)
    print(f"\n  done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
