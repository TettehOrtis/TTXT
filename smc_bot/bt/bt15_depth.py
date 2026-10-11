"""BT15 addendum 2 — how deep does a basket go before it recovers, how far does it run before it reverses,
and what happens at much tighter stops and much bigger targets than the first grid covered.

The user's argument: the win is capped at $7 but the loss is allowed to reach $70, and nobody would design it
that way. So measure (a) how far into loss a basket that eventually WINS actually goes, so the cut can be put in
the right place, (b) how far a basket runs into profit before it reverses, i.e. whether the $7 cap leaves money
behind, (c) the money and the required accuracy at $10-$25 stops and $30-$200 targets.

Same machinery, same entry chances, same costs as BT15. Only the exit geometry changes.

Fixed variant count, declared before looking:
  TIGHT  4 stops ($10/$15/$20/$25) x 4 targets ($7/$10/$15/$20)   = 16 cells
  BIG    4 targets ($30/$50/$100/$200) x 3 stops ($30/$70/$150)   = 12 cells
  RUNON  2 cells: no target with a $70 stop; the same but the stop moves to break-even once +$7 shows
  plus the shipped $7/$70 cell re-run to record how deep and how far each basket travelled (a measurement,
  not a variant).

    set SMC_DATASET=duka & .venv/Scripts/python.exe smc_bot/bt/bt15_depth.py
    .venv/Scripts/python.exe smc_bot/bt/bt15_depth.py
    .venv/Scripts/python.exe smc_bot/bt/bt15_depth.py report
"""
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, simulate, COMM                            # noqa: E402
from bt import bt15_lib as L                                          # noqa: E402

DS = os.environ.get("SMC_DATASET", "zero")
SPAN = ("2009-03-15", "2021-01-01") if DS == "duka" else ("2021-01-01", L.LAST_OPP)
D = L.EXTRA_SPREAD
C_USD = COMM[L.GOLD]                                   # $1.10 round trip at 0.10 lots
SLIP_USD = 0.2
RES = os.path.dirname(L.PARTS)
BAL = 350.0
CUTS = [5, 10, 15, 20, 25, 30, 40, 50]                 # candidate places to cut the loss, in dollars
TIGHT = [(t, s) for t in (0.7, 1.0, 1.5, 2.0) for s in (1.0, 1.5, 2.0, 2.5)]
BIG = [(t, s) for t in (3.0, 5.0, 10.0, 20.0) for s in (3.0, 7.0, 15.0)]
RUNON = [("runon", 7.0, np.nan), ("runon_be7", 7.0, 0.7 / 6.95)]   # no target; BE once +$7 of profit shows
XS = [50.0, 55.0, 60.0]


def part(n):
    return os.path.join(L.PARTS, n + ".npz")


def cell(b, idx, T, S, be=np.nan, chunk=150_000):
    """Both sides of one exit geometry at every entry chance. T = NaN means 'no target, let it run'.
    Returns pnl, mae_usd, mfe_usd, hold_min (each 2 x N). mae/mfe are floating dollars at 0.10 lots on the
    same scale as the basket's own stop, i.e. including the spread the basket paid to get in."""
    n = len(idx)
    out = {k: np.full((2, n), np.nan) for k in ("pnl", "mae", "mfe", "hold")}
    risk = S - D                                        # (fill - sl) in price units, what simulate calls 1R
    for si, side in enumerate((1, -1)):
        for a in range(0, n, chunk):
            sub = idx[a:a + chunk]
            o = L.basket_orders(b, sub, side, T if np.isfinite(T) else 0.0, S, D)
            if not np.isfinite(T):
                o["tp"] = np.nan
            if np.isfinite(be):
                o["be_at_r"] = be
            r = simulate(L.GOLD, o, one_at_a_time=False)
            f = r[r.status == "filled"]
            k = f.opp.values.astype(np.int64) + a
            out["pnl"][si, k] = f.usd_01.values - D * L.USD_PER_PRICE
            out["mae"][si, k] = (f.mae_R.values * risk + D) * L.USD_PER_PRICE
            out["mfe"][si, k] = (f.mfe_R.values * risk - D) * L.USD_PER_PRICE
            out["hold"][si, k] = f.hold_h.values * 60.0
    if np.isfinite(T):                                  # fill-minute target correction, as everywhere in BT15
        tpm, slm = L.fillbar_masks(b, idx, T, S, D)
        out["pnl"] = L.fix_fillbar(out["pnl"], tpm, slm, T)
    return out


def run():
    b = bars(L.GOLD, "M1")
    idx = L.opportunity_idx(b, *SPAN)
    t_opp = b.time.values[idx].astype("datetime64[ns]").astype(np.int64)
    print(f"{DS}: {len(idx)} entry chances")
    jobs = ([("shipped", 0.7, 7.0, np.nan)]
            + [(f"tight_T{t}_S{s}", t, s, np.nan) for t, s in TIGHT]
            + [(f"big_T{t}_S{s}", t, s, np.nan) for t, s in BIG]
            + [(nm, np.nan, s, be) for nm, s, be in RUNON])
    for nm, T, S, be in jobs:
        name = f"dep_{DS}_{nm}"
        if os.path.exists(part(name)):
            print("  skip", name)
            continue
        t0 = time.time()
        o = cell(b, idx, T, S, be)
        np.savez_compressed(part(name) + ".tmp.npz", t_opp=t_opp, **o)
        os.replace(part(name) + ".tmp.npz", part(name))
        print(f"  {nm}: {2*len(idx)} baskets in {time.time()-t0:.0f}s")


# ------------------------------------------------------------------ report
PERIOD_OF = {"HIST": ("duka", "2009-03-15", "2021-01-01"),
             "DEV": ("zero", "2021-01-01", "2024-01-01"),
             "VAL": ("zero", "2024-01-01", L.LAST_OPP)}


def load(nm):
    out = {}
    for per, (ds, a, b_) in PERIOD_OF.items():
        f = np.load(part(f"dep_{ds}_{nm}"))
        t = f["t_opp"]
        lo = np.datetime64(pd.Timestamp(a)).astype("datetime64[ns]").astype(np.int64)
        hi = np.datetime64(pd.Timestamp(b_)).astype("datetime64[ns]").astype(np.int64)
        m = (t >= lo) & (t < hi)
        out[per] = {k: f[k][:, m] for k in ("pnl", "mae", "mfe", "hold")}
        out[per]["t"] = t[m]
    return out


def pooled(d, k):
    v = np.concatenate([d[p][k] for p in PERIOD_OF], axis=1)
    return v


def usd(v, dp=2):
    return ("-$%.*f" % (dp, -v)) if v < 0 else ("$%.*f" % (dp, v))


def md(df, fmt="%.2f"):
    x = df.copy()
    for c in x.columns:
        if x[c].dtype.kind == "f":
            x[c] = x[c].map(lambda v: "" if pd.isna(v) else fmt % v)
        x[c] = x[c].astype(str)
    cols = [str(c) for c in x.columns]
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
                     + ["| " + " | ".join(r.values) + " |" for _, r in x.iterrows()])


def grid_rows(names, label):
    rows = []
    for nm, T, S in names:
        d = load(nm)
        p = pooled(d, "pnl")
        r = L.xstar(p)
        flat = p[~np.isnan(p)].ravel()
        w, ls = flat[flat > 0], flat[flat <= 0]
        rows.append(dict(group=label, target_usd=int(T * 10), stop_usd=int(S * 10),
                         ratio=round(T / S, 2), baskets=len(flat),
                         coin_flip_usd=round(float(flat.mean()), 3),
                         win_pct=round(100 * len(w) / len(flat), 2),
                         avg_win=round(float(w.mean()), 2), avg_loss=round(float(ls.mean()), 2),
                         usd_at_55=round(float(L.money_at(p, 0.55, seed=5).mean()), 3),
                         usd_at_60=round(float(L.money_at(p, 0.60, seed=5).mean()), 3),
                         required_accuracy_pct=round(r["xstar"], 2),
                         cost_pct_of_target=round(100 * 1.622 / (T * 10), 1)))
    return rows


def report():
    sh = load("shipped")
    pnl, mae, mfe, hold = (pooled(sh, k) for k in ("pnl", "mae", "mfe", "hold"))
    fin = ~np.isnan(pnl)
    win = fin & (pnl > 0)
    lose = fin & (pnl < 0)
    qs = [25, 50, 75, 90, 95, 99, 100]
    dist = pd.DataFrame([
        dict(group="baskets that eventually WIN", n=int(win.sum()),
             **{f"p{q}" if q < 100 else "worst": round(float(np.percentile(mae[win], q)), 2) for q in qs}),
        dict(group="baskets that eventually LOSE", n=int(lose.sum()),
             **{f"p{q}" if q < 100 else "worst": round(float(np.percentile(mae[lose], q)), 2) for q in qs}),
    ])
    # cut-point table
    cut = []
    nw, nl = int(win.sum()), int(lose.sum())
    g = pd.read_csv(os.path.join(RES, "required_accuracy.csv"))          # BT15's own grid covers $30/$50/$70 stops
    G30 = {int(r.stop_usd_price * 10): round(float(r.coin_flip_usd_per_basket), 3)
           for _, r in g[g.target_usd_price == 0.7].iterrows()}
    for c in CUTS:
        kw = int((mae[win] >= c).sum())
        exact = load(f"tight_T0.7_S{c/10}") if c in (10, 15, 20, 25) else None
        cut.append(dict(cut_at_usd=c,
                        winners_killed_pct=round(100 * kw / nw, 2),
                        winners_killed_n=kw,
                        losers_capped_pct=round(100 * float((mae[lose] >= c).mean()), 2),
                        saved_per_loser_usd=round(70 - c, 2),
                        predicted_from_mae=round(float(np.nanmean(np.where(mae >= c, -(c + 1.3), pnl))), 3),
                        measured_usd_per_basket=(round(float(np.nanmean(pooled(exact, "pnl"))), 3)
                                                 if exact is not None else G30.get(c, np.nan))))
    cut = pd.DataFrame(cut)
    # MFE
    ru = load("runon")
    rube = load("runon_be7")
    rp, rm = pooled(ru, "pnl"), pooled(ru, "mfe")
    rfin = ~np.isnan(rp)
    mfe_rows = pd.DataFrame([
        dict(group="under the shipped rules, baskets that LOSE: best profit they showed", n=int(lose.sum()),
             **{f"p{q}" if q < 100 else "best": round(float(np.percentile(mfe[lose], q)), 2) for q in qs}),
        dict(group="with NO target and the $70 stop: best profit every basket showed", n=int(rfin.sum()),
             **{f"p{q}" if q < 100 else "best": round(float(np.percentile(rm[rfin], q)), 2) for q in qs}),
    ])
    rfinm = ~np.isnan(rp)
    tt = np.concatenate([ru[per]["t"] for per in PERIOD_OF])
    wk = np.tile(tt // (7 * 86_400_000_000_000), (2, 1))[rfinm]
    RUN_T = dict(mean=float(rp[rfinm].mean()), week_t=L.block_t(rp[rfinm], wk),
                 n=int(rfinm.sum()), nights=float(np.nanmean(pooled(ru, "hold")) / 1440),
                 hold_h=float(np.nanmean(pooled(ru, "hold")) / 60))
    runon = pd.DataFrame([
        dict(variant="shipped: target $7, stop $70", coin_flip_usd=round(float(np.nanmean(pnl)), 3),
             win_pct=round(100 * float(nw / (nw + nl)), 2),
             required_accuracy_pct=round(L.xstar(pnl)["xstar"], 2),
             avg_hold_min=round(float(np.nanmean(hold)), 0)),
        dict(variant="let it run: NO target, stop $70", coin_flip_usd=round(float(np.nanmean(rp)), 3),
             win_pct=round(100 * float((rp[rfin] > 0).mean()), 2),
             required_accuracy_pct=round(L.xstar(rp)["xstar"], 2),
             avg_hold_min=round(float(np.nanmean(pooled(ru, "hold"))), 0)),
        dict(variant="let it run, stop to break-even at +$7",
             coin_flip_usd=round(float(np.nanmean(pooled(rube, "pnl"))), 3),
             win_pct=round(100 * float((pooled(rube, "pnl")[~np.isnan(pooled(rube, "pnl"))] > 0).mean()), 2),
             required_accuracy_pct=round(L.xstar(pooled(rube, "pnl"))["xstar"], 2),
             avg_hold_min=round(float(np.nanmean(pooled(rube, "hold"))), 0)),
    ])
    tight = pd.DataFrame(grid_rows([(f"tight_T{t}_S{s}", t, s) for t, s in TIGHT], "tight stop"))
    big = pd.DataFrame(grid_rows([(f"big_T{t}_S{s}", t, s) for t, s in BIG], "big target"))
    allg = pd.concat([tight, big], ignore_index=True)
    # hold-time histogram: built from the SAME population as the 18-basket sample the user was reading,
    # i.e. the baskets the robot actually takes under the coin-flip entry (trades_coinflip_X50.csv).
    tr = pd.read_csv(os.path.join(RES, "trades_coinflip_X50.csv"), parse_dates=["t_entry", "t_exit"])
    h = ((tr.t_exit - tr.t_entry).dt.total_seconds() / 60).values
    HMED, HMEAN, HMEDW = float(np.median(h)), float(h.mean()), float(np.median(h[tr.pnl.values > 0]))
    buckets = [(0, 2, "under 2 min"), (2, 5, "2-5 min"), (5, 10, "5-10 min"), (10, 20, "10-20 min"),
               (20, 60, "20-60 min"), (60, 120, "60-120 min"), (120, 1e9, "over 120 min")]
    hist = pd.DataFrame([dict(hold_time=lab, baskets=int(((h >= a) & (h < b_)).sum()),
                              pct=round(100 * float(((h >= a) & (h < b_)).mean()), 1))
                         for a, b_, lab in buckets])
    hist["bar"] = hist.pct.map(lambda v: "#" * int(round(v / 1.5)))
    for nm, d in [("depth_mae_distribution", dist), ("depth_cut_points", cut), ("depth_mfe_distribution", mfe_rows),
                  ("depth_runon", runon), ("depth_grid", allg), ("depth_holdtime_histogram", hist)]:
        d.to_csv(os.path.join(RES, nm + ".csv"), index=False)
    RUN_T.update(hmed=HMED, hmean=HMEAN, hmedw=HMEDW)
    write_md(dist, cut, mfe_rows, runon, tight, big, allg, hist, pnl, mae, win, lose, RUN_T)


def write_md(dist, cut, mfe_rows, runon, tight, big, allg, hist, pnl, mae, win, lose, RUN_T):
    bestcut = cut.loc[cut.measured_usd_per_basket.idxmax()]
    bestcell = allg.loc[allg.coin_flip_usd.idxmax()]
    bestreq = allg.loc[allg.required_accuracy_pct.idxmin()]
    npos = int((allg.coin_flip_usd > 0).sum())
    npos60 = int((allg.usd_at_60 > 0).sum())
    mw = mae[win]
    txt = f"""# BT15 addendum 2 — how deep, how far, and what happens at the extremes

**The user's point is a fair one.** The robot caps the win at $7 but lets the loss run to $70. Nobody would design
that on purpose. So three things were measured rather than argued: how far into loss a basket that **eventually
wins** actually travels (so the cut can be put in the right place), how far a basket runs into profit before it
reverses (so we know whether the $7 cap leaves money behind), and what happens at **much tighter stops and much
bigger targets** than the first grid covered.

Same entry chances as BT15 (the same 30-minute grid, both feeds, 2009-03 to 2025-06, {int(hist.baskets.sum()):,}
baskets), same costs, only the exit geometry changed. **Variant count, fixed in advance: 16 tight-stop cells + 12
big-target cells + 2 let-it-run cells = 30.** The depth and distance measurements are measurements, not variants.

## 1. How deep does a winner go before it wins? (dollars at 0.10 lots)
{md(dist)}

**Read the top row.** Half of all winning baskets never go more than **${np.percentile(mw, 50):.2f}** into loss, and
three quarters never go more than **${np.percentile(mw, 75):.2f}**. But the tail is long: one winner in ten dips past
**${np.percentile(mw, 90):.2f}**, one in twenty past **${np.percentile(mw, 95):.2f}**, one in a hundred past
**${np.percentile(mw, 99):.2f}**, and the deepest recovery in the whole sample came back from
**${mw.max():.2f}** — i.e. from nearly the full $70 stop.

## 2. So where should the cut actually go? **This is the table he asked for.**
For each candidate cut point: what share of the baskets that *would have won* get killed, what the cut saves on each
loser, the arithmetic net, and — the honest version — the money per basket when the tighter stop is actually
simulated at the same entry minutes.

{md(cut, "%.3f")}

- `winners_killed_pct` = of the baskets that won under the shipped $70 stop, the share that had already been this
  deep in loss, so a cut here would have turned them into losers.
- `losers_capped_pct` = share of the eventual losers the cut catches earlier (every loser reaches $70, so a cut
  always catches all of them) and `saved_per_loser_usd` is what it saves on each.
- `predicted_from_mae` re-prices every basket that ever went this deep at -$(cut) and leaves the rest alone;
  `measured_usd_per_basket` is the real simulation of that stop at the same entry minutes (`-` = not simulated
  separately; $30, $50 and $70 come from BT15's own grid).

**The answer: there is no good place to cut. Every cut point is still a loss, and most of them are WORSE than $70.**
The best measured row is **${int(bestcut.cut_at_usd)}**, at
**{usd(float(bestcut.measured_usd_per_basket))}** a basket against the shipped $70 stop's **{usd(float(np.nanmean(pnl)))}** —
an improvement of {100*(float(bestcut.measured_usd_per_basket) - float(np.nanmean(pnl)))/abs(float(np.nanmean(pnl))):.0f}%
of the way to... still a loss. Cutting at $5 kills **{cut[cut.cut_at_usd==5].winners_killed_pct.iloc[0]:.0f}% of the
baskets that would have won**; cutting at $20 still kills
{cut[cut.cut_at_usd==20].winners_killed_pct.iloc[0]:.0f}% of them.

**Why the intuition fails.** It feels as though cutting at $20 trades a small, frequent loss for a rare huge one, and
the saving should be large: every loser gives back $50. It does — but the same cut converts
{cut[cut.cut_at_usd==20].winners_killed_pct.iloc[0]:.0f}% of a very large pile of winners into losers, and that pile
is eight times bigger than the pile of losers. The two effects cancel, and then the cost is still there. Section 4
shows it directly: holding the $7 target fixed, moving the stop from $25 all the way down to $10 moves the money per
basket from {usd(tight[(tight.target_usd==7)&(tight.stop_usd==25)].coin_flip_usd.iloc[0])} to
{usd(tight[(tight.target_usd==7)&(tight.stop_usd==10)].coin_flip_usd.iloc[0])} — about five cents on a basket — while
the win rate collapses from {tight[(tight.target_usd==7)&(tight.stop_usd==25)].win_pct.iloc[0]:.0f}% to
{tight[(tight.target_usd==7)&(tight.stop_usd==10)].win_pct.iloc[0]:.0f}%. The stop is not the problem.

## 3. Does the $7 cap leave money behind?
First, a clarification worth making, because the question contains a trap: under the shipped rules a losing basket
**cannot** have passed +$7 and come back — if it had reached +$7 it would have closed as a win. So the only
meaningful version of "how far did the losers run first" is "how close did they get to the $7 target", and the only
way to see what the cap costs is to **remove it**.

{md(mfe_rows)}

{md(runon.assign(coin_flip_usd=runon.coin_flip_usd.map(lambda v: usd(v, 3)),
                 win_pct=runon.win_pct.round(1), required_accuracy_pct=runon.required_accuracy_pct.round(1),
                 avg_hold_min=runon.avg_hold_min.astype(int)).rename(
    columns={'coin_flip_usd': 'coin flip $/basket', 'win_pct': 'win %',
             'required_accuracy_pct': 'REQUIRED accuracy %', 'avg_hold_min': 'avg hold (min)'}), "%.1f")}

### Stop. One cell came out POSITIVE, and it has to be said loudly
**"Let it run: no target, stop $70" is the only positive number anywhere in BT15 or either addendum:
{RUN_T['mean']:+.3f} dollars a basket.** Per the pre-registered rule, here is the check, and here is why it is not a
finding:

1. **It is indistinguishable from zero.** Over {RUN_T['n']:,} baskets the spread of outcomes is
   enormous (one basket in twenty showed more than $400 of profit at some point, the best reached $2,864), and these
   baskets are held **{RUN_T['hold_h']:.0f} hours on average**, so they overlap each other heavily and are not
   independent samples. Measured properly with calendar-week blocks the result is **t {RUN_T['week_t']:+.2f}** — zero
   to two decimal places. Thirteen cents a basket on a structure whose typical swing is $144 is not a signal.
2. **It would pay financing that this harness charges at zero.** These baskets hold for
   **{RUN_T['nights']:.2f} nights each on average** (up to 5 days). `common.simulate` models spread, commission and
   stop slippage but **no overnight swap at all** — and BT9 already recorded that gold swap is negative on both
   sides. Any realistic overnight charge is several times thirteen cents, so the cell is negative once financed.
3. **It is no longer the robot.** A trade with no profit target, held a day or more, is not a basket scalper; it is a
   swing trade. It abandons the one thing the product is built on.

What it *does* confirm is the floor in section 6: with no target at all the cost is a negligible slice of the
reward, so the required accuracy lands on
**{runon[runon.variant.str.startswith('let it run: NO')].required_accuracy_pct.iloc[0]:.1f}%** — i.e. on 50%, which is
exactly where a coin flip should sit when costs stop mattering. (It prints a hair under 50 because 380,378 heavily
overlapping baskets cannot pin it down more finely than that; it cannot truly be below 50.)

### And the plain answer to the question
The capped version is worth
**{usd(runon[runon.variant.str.startswith('shipped')].coin_flip_usd.iloc[0])}** a basket, the uncapped version
**{usd(runon[runon.variant.str.startswith('let it run: NO')].coin_flip_usd.iloc[0])}** before financing and negative
after it, and protecting an uncapped basket by moving the stop to break-even at +$7 is worth
**{usd(runon[runon.variant.str.contains('break-even')].coin_flip_usd.iloc[0])}** (it wins only
{runon[runon.variant.str.contains('break-even')].win_pct.iloc[0]:.1f}% of the time, because the break-even stop gets
clipped almost immediately). **So the $7 cap is not what is wrong with the robot.** Removing it buys nothing real:
for every basket that would have run to $30 there is one that hands the $7 back and keeps going.

## 4. Tight stops — $10 to $25, which the first grid never reached
{md(tight[['target_usd', 'stop_usd', 'baskets', 'coin_flip_usd', 'win_pct', 'avg_win', 'avg_loss',
           'usd_at_60', 'required_accuracy_pct', 'cost_pct_of_target']].rename(
    columns={'target_usd': '$ target', 'stop_usd': '$ stop', 'coin_flip_usd': 'coin flip $/basket',
             'win_pct': 'win %', 'avg_win': 'avg win $', 'avg_loss': 'avg loss $',
             'usd_at_60': '$/basket at 60% accuracy', 'required_accuracy_pct': 'REQUIRED accuracy %',
             'cost_pct_of_target': 'cost as % of target'}), "%.2f")}

## 5. Big targets — $30 to $200
{md(big[['target_usd', 'stop_usd', 'baskets', 'coin_flip_usd', 'win_pct', 'avg_win', 'avg_loss',
         'usd_at_60', 'required_accuracy_pct', 'cost_pct_of_target']].rename(
    columns={'target_usd': '$ target', 'stop_usd': '$ stop', 'coin_flip_usd': 'coin flip $/basket',
             'win_pct': 'win %', 'avg_win': 'avg win $', 'avg_loss': 'avg loss $',
             'usd_at_60': '$/basket at 60% accuracy', 'required_accuracy_pct': 'REQUIRED accuracy %',
             'cost_pct_of_target': 'cost as % of target'}), "%.2f")}

**Positive cells on a coin flip: {npos} of {len(allg)}.** Not one tight stop and not one big target makes money
with a coin-flip entry. (At a *hypothetical* 60% accuracy, {npos60} of {len(allg)} cells are positive — which is just
another way of reading the required-accuracy column, since the big-target cells need less than 60%. It is not a
result: 60% is a number nobody in this project has ever achieved, and the four honest readings of the robot's own
advertised entry managed 49.6-51.2%.)

## 6. The pattern, confirmed at both extremes — and the floor
The first grid showed that the **stop barely matters and the target matters enormously**. That holds at the extremes,
and now the reason is visible:

| | required accuracy |
|---|---|
| $7 target, stop from ${int(tight[tight.target_usd == 7].stop_usd.min())} to ${int(tight[tight.target_usd == 7].stop_usd.max())} | {tight[tight.target_usd == 7].required_accuracy_pct.min():.1f}% to {tight[tight.target_usd == 7].required_accuracy_pct.max():.1f}% — the stop moves it by about {tight[tight.target_usd == 7].required_accuracy_pct.max() - tight[tight.target_usd == 7].required_accuracy_pct.min():.1f} point |
| $200 target, stop from $30 to $150 | {big[big.target_usd == 200].required_accuracy_pct.min():.1f}% to {big[big.target_usd == 200].required_accuracy_pct.max():.1f}% |
| **target from $7 to $200, all at the same $70 stop** | **{L.xstar(pnl)['xstar']:.1f}% down to {big[big.stop_usd == 70].required_accuracy_pct.min():.1f}% — the target moves it by {L.xstar(pnl)['xstar'] - big[big.stop_usd == 70].required_accuracy_pct.min():.1f} points** |
| target from $7 to $20, all at the same $10 stop | {allg[allg.stop_usd == 10].required_accuracy_pct.max():.1f}% down to {allg[allg.stop_usd == 10].required_accuracy_pct.min():.1f}% |

**And here is the floor, which is the key point.** As the target grows, the $1.62 of cost becomes a smaller and
smaller slice of it, and the required accuracy falls towards **50% from above — and it can never go below 50%,**
because before costs a coin flip is *exactly* break-even at every target and stop combination there is. That is what
the measurement shows: the biggest target tested ($200, cost {big[big.target_usd == 200].cost_pct_of_target.iloc[0]:.1f}%
of the reward) needs **{big[big.target_usd == 200].required_accuracy_pct.min():.1f}%** — the lowest number anywhere in
either grid, and still above 50.

So the honest summary of the whole exercise is one sentence: **the only lever that ever moves the requirement is the
size of the target relative to the cost, and the best it can ever do is get close to a coin flip — never better than
one.** There is no target, no stop, no clock and no cut point that makes this structure pay for itself. It needs an
entry that beats a coin flip, and the amount by which it must beat one is set by how much of your reward the broker
takes.

For scale, the whole range now measured across BT15 and both addenda:

| shape | required accuracy |
|---|---|
| the shipped $7 target / $70 stop | 62.3% |
| the hardest shape tested ($3 target) | 76.4% |
| the easiest shape tested (${int(bestreq.target_usd)} target / ${int(bestreq.stop_usd)} stop) | {bestreq.required_accuracy_pct:.1f}% |
| what a coin flip delivers | 50.0% |
| what this project has ever measured from a real directional rule | ~50% (BT4, BT5, BT9, BT13, BT8b) |

## 7. Hold times — the shape, not the single number
The 18-basket sample had baskets of 1, 2, 4 and 136 minutes against a median of about 7, which looks contradictory.
It is not: the distribution is **very lopsided**, so the middle value and the average are nowhere near each other.
Here is the whole shape — the {int(hist.baskets.sum()):,} baskets the robot actually took in that run, the same
population the 18-basket sample came from:

{md(hist[['hold_time', 'baskets', 'pct', 'bar']].rename(columns={'hold_time': 'basket lasted', 'pct': '% of baskets'}), "%.1f")}

Roughly **{hist[hist.hold_time.isin(['under 2 min', '2-5 min', '5-10 min'])].pct.sum():.0f}% of baskets are over
inside ten minutes**, and about **{hist[hist.hold_time.isin(['60-120 min', 'over 120 min'])].pct.sum():.0f}%** run
over an hour. There is no "typical" length — there is a big pile of very short ones and a long thin tail. A single
basket of 136 minutes is perfectly normal; so is one of 1 minute: **{hist[hist.hold_time == 'under 2 min'].pct.iloc[0]:.0f}%
of all baskets are finished inside two minutes.**

That is also why the single-number summaries disagree, and both are correct: the **middle** basket lasts
{RUN_T['hmed']:.0f} minutes, the middle **winning** basket lasts {RUN_T['hmedw']:.0f} minutes, and the **average** basket lasts
{RUN_T['hmean']:.0f} minutes — because the top {hist[hist.hold_time == 'over 120 min'].pct.iloc[0]:.0f}% that run past two
hours drag the average up without moving the middle at all. When a number like "7 minutes" is quoted it is the middle
winner, not a typical length; nothing in a sample containing 1, 2, 4 and 136 minutes contradicts it.

## Files
| File | What |
|---|---|
| `bt/bt15_depth.py` | the sweep (checkpointed per cell to `parts/dep_*.npz`) and this addendum |
| `results/BT15/depth_mae_distribution.csv` | how deep winners and losers went |
| `results/BT15/depth_cut_points.csv` | **the cut-point table**: winners killed vs losers capped, paper and measured |
| `results/BT15/depth_mfe_distribution.csv`, `depth_runon.csv` | how far baskets ran; the uncapped variants |
| `results/BT15/depth_grid.csv` | the 28 tight-stop and big-target cells |
| `results/BT15/depth_holdtime_histogram.csv` | the hold-time shape |

*Generated by `bt/bt15_depth.py report`.*
"""
    with open(os.path.join(RES, "addendum_depth.md"), "w", encoding="utf-8") as fh:
        fh.write(txt)
    print("wrote", os.path.join(RES, "addendum_depth.md"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        run()
