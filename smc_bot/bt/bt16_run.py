"""BT16 runner. Pre-registered in bt/BT16_BRIEF.md. Checkpoints every phase to results/BT16/parts/.

    cd smc_bot
    ../.venv/Scripts/python.exe -m bt.bt16_run                 # zero feed  -> DEV 2021-23 + VAL 2024-mid25
    SMC_DATASET=duka ../.venv/Scripts/python.exe -m bt.bt16_run  # duka feed -> HIST 2009-2020

TEST (2025-07-01 onward) is LOCKED: entry chances stop at bt15_lib.LAST_OPP = 2025-06-27.
"""
import os
import time
import numpy as np
import pandas as pd

from bt import bt16_lib as B
from bt import bt15_lib as L


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def ck(name):
    return os.path.join(B.PARTS, name)


# ------------------------------------------------------------------ phase 1: grid outcomes
def phase_grid(b, fd, a, z):
    idx = L.opportunity_idx(b, pd.Timestamp(a), pd.Timestamp(z), B.STRIDE_MIN)
    log(f"{fd}: {len(idx):,} entry chances on the 30-minute grid, {b.time.iloc[0]} -> {pd.Timestamp(z)}")
    t0 = time.time()
    pnl, texit, idx = B.outcome_table(b, idx, f"grid_{fd}")
    log(f"{fd}: outcome table ready ({time.time()-t0:.0f}s)")
    return idx, pnl, texit


# ------------------------------------------------------------------ phase 2: the 26 grid readings
def phase_readings(b, fd, idx, pnl):
    f = ck(f"read_{fd}.csv")
    sf = ck(f"sides_{fd}.npz")
    t_open = b.time.values[idx].astype("datetime64[ns]")
    t_ns = t_open.astype(np.int64)
    if os.path.exists(f) and os.path.exists(sf):
        log(f"{fd}: readings cached")
        return pd.read_csv(f), dict(np.load(sf)), t_open
    sides = {}
    log(f"{fd}: building the dollar index from the 7 USD pairs ...")
    sides.update({k: v for k, v in B.dollar_signals(t_ns).items()})
    log(f"{fd}: building the M1/M5/M15 rule signals ...")
    sides.update(B.tf_signals(t_ns))
    # reference rows (descriptive, not variants): what the market itself did
    sides["REF_always_long"] = np.ones(len(idx), np.int8)
    rows = []
    for name, s in sides.items():
        fam = "dollar" if name.startswith("D") else ("ref" if name.startswith("REF") else "m1m5")
        rows += B.breakdown(name, fam, s, pnl, t_open)
        log(f"  scored {name}")
    df = pd.DataFrame(rows)
    df.insert(0, "feed", fd)
    df.to_csv(f, index=False)
    np.savez_compressed(sf, **sides)
    return df, sides, t_open


# ------------------------------------------------------------------ phase 3: news
def phase_news(b, fd, a, z):
    f = ck(f"news_{fd}.csv")
    cf = ck(f"newschances_{fd}.csv")
    if os.path.exists(f):
        log(f"{fd}: news cached")
        return pd.read_csv(f), pd.read_csv(cf, parse_dates=["t_open", "t_release"])
    ev = B.news_times()
    ch = B.news_chances(b, ev, a, z)
    log(f"{fd}: {len(ch):,} news entry chances ({len(ev):,} approximate releases in range)")
    bar = ch.bar.values
    uniq, inv = np.unique(bar, return_inverse=True)
    pnl, texit, _ = B.outcome_table(b, uniq, f"news_{fd}")
    pn = pnl[:, inv]                                        # per chance
    mom = B.momentum30(b, bar)
    t_open = ch.t_open.values.astype("datetime64[ns]")
    rows = []
    for e in B.EVENTS:
        for wname, _ in B.WINDOWS:
            m = (ch.event.values == e) & (ch.window.values == wname)
            for rname in B.NEWS_RULES:
                s = mom.copy() if rname.startswith("M") else np.ones(len(bar), np.int8)
                s = np.where(m, s, 0).astype(np.int8)
                nm = f"{e}_{wname}_{rname}"
                rows += B.breakdown(nm, "news", s, pn, t_open)
    # pooled across the three events (aggregate, not a new variant)
    for wname, _ in B.WINDOWS:
        for rname in B.NEWS_RULES:
            m = (ch.window.values == wname)
            s = mom.copy() if rname.startswith("M") else np.ones(len(bar), np.int8)
            s = np.where(m, s, 0).astype(np.int8)
            rows += B.breakdown(f"ALLEVENTS_{wname}_{rname}", "news_pooled", s, pn, t_open)
    df = pd.DataFrame(rows)
    df.insert(0, "feed", fd)
    df.to_csv(f, index=False)
    ch.to_csv(cf, index=False)
    np.savez_compressed(ck(f"newsarr_{fd}.npz"), pnl=pn, mom=mom, bar=bar, t_open=t_open.astype(np.int64),
                        event=ch.event.values.astype("U8"), window=ch.window.values.astype("U20"))
    return df, ch


# ------------------------------------------------------------------ phase 4: gold vs dollar correlation
def phase_corr(b, fd):
    f = ck(f"corr_{fd}.csv")
    if os.path.exists(f):
        log(f"{fd}: correlation cached")
        return pd.read_csv(f)
    g = b[b.time < pd.Timestamp(B.LAST_OPP)]               # TEST (2025-07-01+) is LOCKED, even for a correlation
    gt = g.tclose.values.astype("datetime64[ns]").astype(np.int64)
    gl = np.log(g.close.values)
    rows = []
    for wname, mins in [("15min", 15), ("1h", 60), ("4h", 240), ("1day", 1440)]:
        step = int(mins) * 60_000_000_000
        grid = np.arange(gt[0] + step, gt[-1], step)         # non-overlapping windows
        u1, u0 = B.usd_index_at(grid), B.usd_index_at(grid - step)
        k1 = np.searchsorted(gt, grid, "right") - 1
        k0 = np.searchsorted(gt, grid - step, "right") - 1
        fresh = (grid - gt[np.clip(k1, 0, len(gt) - 1)] < 10 * 60_000_000_000) & \
                (grid - step - gt[np.clip(k0, 0, len(gt) - 1)] < 10 * 60_000_000_000)
        dg = gl[np.clip(k1, 0, len(gl) - 1)] - gl[np.clip(k0, 0, len(gl) - 1)]
        du = u1 - u0
        ok = fresh & np.isfinite(dg) & np.isfinite(du) & (k0 >= 0)
        t = pd.DatetimeIndex(grid.astype("datetime64[ns]"))
        per = B.period_of(grid.astype("datetime64[ns]"))
        for sc in ["ALL"] + [p for p in ("HIST", "DEV", "VAL") if (per == p).any()]:
            m = ok if sc == "ALL" else (ok & (per == sc))
            if m.sum() < 50:
                continue
            rows.append(dict(feed=fd, window=wname, scope=sc, n=int(m.sum()),
                             corr_contemporaneous=round(float(np.corrcoef(dg[m], du[m])[0, 1]), 3)))
        # lead-lag: past dollar move over the window vs gold's NEXT DAY move (descriptive only)
        ahead = 1440 * 60_000_000_000
        k2 = np.searchsorted(gt, grid + ahead, "right") - 1
        fut = gl[np.clip(k2, 0, len(gl) - 1)] - gl[np.clip(k1, 0, len(gl) - 1)]
        m2 = ok & np.isfinite(fut) & (grid + ahead < gt[-1])
        if m2.sum() > 50:
            rows.append(dict(feed=fd, window=wname + "_leadlag_vs_next_day_gold", scope="ALL",
                             n=int(m2.sum()), corr_contemporaneous=round(float(np.corrcoef(fut[m2], du[m2])[0, 1]), 3)))
        log(f"  {fd} {wname}: contemporaneous corr done")
    df = pd.DataFrame(rows)
    df.to_csv(f, index=False)
    return df


# ------------------------------------------------------------------ phase 5: news volatility (descriptive)
def phase_newsvol(b, fd, a, z):
    f = ck(f"newsvol_{fd}.csv")
    if os.path.exists(f):
        return pd.read_csv(f)
    ev = B.news_times()
    bb = b[b.time < pd.Timestamp(B.LAST_OPP)]              # TEST is LOCKED, including for the control set
    t1 = bb.time.values.astype("datetime64[ns]")
    c = bb.close.values
    rows = []
    for e in B.EVENTS:
        tt = ev.t[ev.event == e].values.astype("datetime64[ns]")
        tt = tt[(tt >= np.datetime64(pd.Timestamp(a))) & (tt < np.datetime64(pd.Timestamp(z)))]
        k = np.clip(np.searchsorted(t1, tt, "left"), 0, len(t1) - 61)
        gap = (t1[k] - tt) / np.timedelta64(1, "m")
        k = k[(gap >= 0) & (gap <= 2)]
        mv = np.abs(c[k + 60] - c[k])
        # same clock minute on NON-event days, as the control
        mins = pd.DatetimeIndex(t1).hour.values * 60 + pd.DatetimeIndex(t1).minute.values
        want = np.unique((pd.DatetimeIndex(tt).hour.values * 60 + pd.DatetimeIndex(tt).minute.values))
        ctl_idx = np.where(np.isin(mins, want))[0]
        ctl_idx = ctl_idx[ctl_idx < len(c) - 61]
        evdays = set(pd.DatetimeIndex(tt).normalize().asi8)
        keep = ~np.isin(pd.DatetimeIndex(t1[ctl_idx]).normalize().asi8, list(evdays))
        ctl = np.abs(c[ctl_idx[keep] + 60] - c[ctl_idx[keep]])
        rows.append(dict(feed=fd, event=e, n_events=len(mv), avg_move_60min_after=round(float(mv.mean()), 3),
                         n_control=len(ctl), avg_move_60min_control=round(float(ctl.mean()), 3),
                         ratio=round(float(mv.mean() / ctl.mean()), 2)))
        log(f"  {fd} {e}: vol x{rows[-1]['ratio']}")
    df = pd.DataFrame(rows)
    df.to_csv(f, index=False)
    return df


# ------------------------------------------------------------------ phase 6: the placebo bar
def phase_placebo(fd, sides, pnl, t_open, draws=200, seed=16):
    f = ck(f"placebo_{fd}.csv")
    if os.path.exists(f):
        log(f"{fd}: placebo cached")
        return pd.read_csv(f)
    names = [k for k in sides if not k.startswith("REF")]
    news = np.load(ck(f"newsarr_{fd}.npz"))
    npnl, mom, nev, nwin = news["pnl"], news["mom"], news["event"], news["window"]
    nt = news["t_open"].astype("datetime64[ns]")
    rng = np.random.default_rng(seed)
    n = len(t_open)
    week = int(7 * 24 * 60 / B.STRIDE_MIN)                 # one nominal week of grid steps
    rows = []
    for d in range(draws):
        # the two families are kept apart: the grid readings run over ~50,000 chances, the news cells over ~50.
        # One bar for both would be meaningless, because a 50-chance cell wanders by 7 points on its own.
        g_acc, g_money, best_acc, best_money = -1e9, -1e9, -1e9, -1e9
        for k in names:
            r = int(rng.integers(1, max(2, n // week))) * week
            sc = B.score(np.roll(sides[k], r), pnl, t_open, fast=True)
            if np.isfinite(sc["acc"]):
                g_acc = max(g_acc, sc["acc"])
                g_money = max(g_money, sc["money"])
        best_acc, best_money = g_acc, g_money
        for e in B.EVENTS:
            for wname, _ in B.WINDOWS:
                m = np.where((nev == e) & (nwin == wname))[0]
                if len(m) < 20:
                    continue
                for rname in B.NEWS_RULES:
                    base = mom[m] if rname.startswith("M") else np.ones(len(m), np.int8)
                    # rotate WITHIN this event/window block, by a random whole number of releases
                    s = np.zeros(len(mom), np.int8)
                    s[m] = np.roll(base, int(rng.integers(1, len(m))))
                    sc = B.score(s, npnl, nt, fast=True)
                    if np.isfinite(sc["acc"]):
                        best_acc = max(best_acc, sc["acc"])
                        best_money = max(best_money, sc["money"])
        rows.append(dict(draw=d, best_acc_grid=g_acc, best_money_grid=g_money,
                         best_acc_all=best_acc, best_money_all=best_money))
        if (d + 1) % 20 == 0:
            log(f"  placebo {d+1}/{draws}  grid best {g_acc:.2f}  with news {best_acc:.2f}")
    df = pd.DataFrame(rows)
    df.to_csv(f, index=False)
    return df


# ------------------------------------------------------------------ phase 7: hour of day CROSSED with period
def phase_hours(fd, sides, pnl, t_open):
    """The user asked for hour of day. The guard (BT7, BT14) is that an hour must be consistent across all
    three date periods to mean anything - so hour has to be crossed with period, not reported pooled."""
    f = ck(f"hours_{fd}.csv")
    if os.path.exists(f):
        return pd.read_csv(f)
    per = B.period_of(t_open)
    hrs = pd.DatetimeIndex(t_open).hour.values
    rows = []
    for name, s in sides.items():
        for p in ("HIST", "DEV", "VAL"):
            mp = per == p
            if not mp.any():
                continue
            for h in range(24):
                sc = B.score(s, pnl, t_open, mp & (hrs == h), fast=True)
                rows.append(dict(feed=fd, reading=name, period=p, hour=h, n=sc["n"], n_dec=sc["n_dec"],
                                 acc=None if sc["n_dec"] == 0 else round(sc["acc"], 2),
                                 money=None if sc["n"] == 0 else round(sc["money"], 4)))
    df = pd.DataFrame(rows)
    df.to_csv(f, index=False)
    log(f"{fd}: hour x period table written ({len(df)} cells)")
    return df


def main():
    B.setup()
    fd = B.feed()
    a, z = B.span()
    b = B.bars(B.GOLD, "M1")
    idx, pnl, texit = phase_grid(b, fd, a, z)
    bl = B.baselines(pnl)
    log(f"{fd}: break-even accuracy at $15/$20 with his costs = {bl['xstar']:.2f}%  "
        f"(coin flip pays ${bl['E_coin']:.2f}/basket, oracle ${bl['E_best']:.2f})")
    pd.DataFrame([dict(feed=fd, **bl)]).to_csv(ck(f"baseline_{fd}.csv"), index=False)
    df, sides, t_open = phase_readings(b, fd, idx, pnl)
    phase_news(b, fd, a, z)
    phase_corr(b, fd)
    phase_newsvol(b, fd, a, z)
    phase_placebo(fd, sides, pnl, t_open)
    phase_hours(fd, sides, pnl, t_open)
    # day-by-day money for the reference + the best readings, through the robot's own mechanics
    f = ck(f"days_{fd}.csv")
    if not os.path.exists(f):
        t_ns = b.time.values[idx].astype("datetime64[ns]").astype(np.int64)
        rows = []
        top = df[(df.scope == "ALL") & (df.family != "ref")].sort_values("acc", ascending=False).reading.head(6)
        for name in list(top) + ["REF_always_long"]:
            bk = L.engine(t_ns, pnl, texit, sides[name].astype(int))
            rows.append(dict(feed=fd, reading=name, **L.day_stats(bk, balance=350.0)))
        for X, nm in [(0.50, "COINFLIP_50"), (0.529, "BREAKEVEN_52.9"), (0.55, "SKILL_55"), (0.60, "SKILL_60")]:
            bk = L.engine(t_ns, pnl, texit, L.skill_sides(pnl, X, seed=7))
            rows.append(dict(feed=fd, reading=nm, **L.day_stats(bk, balance=350.0)))
        pd.DataFrame(rows).to_csv(f, index=False)
        log(f"{fd}: day statistics written")
    log(f"{fd}: DONE. latest entry chance touched = {pd.Timestamp(b.time.values[idx][-1])}")


if __name__ == "__main__":
    main()
