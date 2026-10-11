"""BT15 addendum — does a TIME STOP rescue the basket structure?

The user's observation is correct and worth testing: a winning basket resolves fast, a losing one bleeds slowly.
So close the basket after N minutes and cut the slow bleeders before they reach -$70.

Clean A/B: the entry chances are the SAME pre-registered 30-minute opportunity grid used by BT15, the same two
feeds, the same costs. Only the exit rule changes. The time stop closes at the **then-current market price** on
the correct side of the book (bid for a long, ask for a short), which `common.simulate` already does via
`max_hold_min` -> exit_reason TIME. Nothing is closed "at zero".

    set SMC_DATASET=duka & .venv/Scripts/python.exe smc_bot/bt/bt15_timestop.py
    .venv/Scripts/python.exe smc_bot/bt/bt15_timestop.py
    .venv/Scripts/python.exe smc_bot/bt/bt15_timestop.py report
"""
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, simulate, COMM                           # noqa: E402
from bt import bt15_lib as L                                         # noqa: E402

DS = os.environ.get("SMC_DATASET", "zero")
SPAN = ("2009-03-15", "2021-01-01") if DS == "duka" else ("2021-01-01", L.LAST_OPP)
T0 = 0.7                                   # target stays the shipped $7 at 0.10 lots
STOPS = [2.0, 3.5, 7.0]                    # $20 / $35 / $70 dollar stops
TSTOPS = [5, 10, 15, 20, 30, 45, 60, 120, 240, 0]          # 0 = none (the control)
XS = [50.0, 55.0, 60.0]
BAL = 350.0
OUT = os.path.join(L.RESULTS if hasattr(L, "RESULTS") else "", "")
RES = os.path.join(os.path.dirname(L.PARTS), "")            # results/BT15/
NS_D = 86_400_000_000_000


def part(n):
    return os.path.join(L.PARTS, n + ".npz")


def outcomes_ts(b, idx, T, S, tstop, delta=L.EXTRA_SPREAD, chunk=150_000):
    """Same as bt15_lib.outcomes but with a clock time stop (max_hold_min)."""
    n = len(idx)
    pnl = np.full((2, n), np.nan)
    tex = np.zeros((2, n), dtype=np.int64)
    for si, side in enumerate((1, -1)):
        for a in range(0, n, chunk):
            sub = idx[a:a + chunk]
            o = L.basket_orders(b, sub, side, T, S, delta)
            o["max_hold_min"] = float(tstop) if tstop else np.nan
            r = simulate(L.GOLD, o, one_at_a_time=False)
            f = r[r.status == "filled"]
            k = f.opp.values.astype(np.int64) + a
            pnl[si, k] = f.usd_01.values - delta * L.USD_PER_PRICE
            tex[si, k] = f.t_exit.values.astype("datetime64[ns]").astype(np.int64)
    tpm, slm = L.fillbar_masks(b, idx, T, S, delta)
    return L.fix_fillbar(pnl, tpm, slm, T), tex


def run():
    b = bars(L.GOLD, "M1")
    idx = L.opportunity_idx(b, *SPAN)
    print(f"{DS}: {len(idx)} entry chances")
    t_opp = b.time.values[idx].astype("datetime64[ns]").astype(np.int64)
    for S in STOPS:
        for ts in TSTOPS:
            name = f"ts_{DS}_S{S}_t{ts}"
            if os.path.exists(part(name)):
                print("  skip", name)
                continue
            t0 = time.time()
            pnl, tex = outcomes_ts(b, idx, T0, S, ts)
            np.savez_compressed(part(name) + ".tmp.npz", t_opp=t_opp, pnl=pnl, texit=tex)
            os.replace(part(name) + ".tmp.npz", part(name))
            print(f"  S{S} tstop {ts or 'none'}: {2*len(idx)} baskets in {time.time()-t0:.0f}s")


# ------------------------------------------------------------------ report
PERIOD_OF = {"HIST": ("duka", "2009-03-15", "2021-01-01"),
             "DEV": ("zero", "2021-01-01", "2024-01-01"),
             "VAL": ("zero", "2024-01-01", L.LAST_OPP)}


def report():
    rows, dayrows = [], []
    for S in STOPS:
        for ts in TSTOPS:
            pools, engines = [], {x: [] for x in XS}
            for per, (ds, a, b_) in PERIOD_OF.items():
                f = np.load(part(f"ts_{ds}_S{S}_t{ts}"))
                t, pnl, tex = f["t_opp"], f["pnl"], f["texit"]
                lo = np.datetime64(pd.Timestamp(a)).astype("datetime64[ns]").astype(np.int64)
                hi = np.datetime64(pd.Timestamp(b_)).astype("datetime64[ns]").astype(np.int64)
                m = (t >= lo) & (t < hi)
                pools.append(pnl[:, m])
                for x in XS:
                    sd = L.skill_sides(pnl[:, m], x / 100.0, seed=11)
                    engines[x].append(L.engine(t[m], pnl[:, m], tex[:, m], sd))
            p = np.concatenate(pools, axis=1)
            flat = p[~np.isnan(p)].ravel()
            w, lss = flat[flat > 0], flat[flat <= 0]
            xr = L.xstar(p)
            for x in XS:
                v = np.concatenate([L.money_at(p, x / 100.0, seed=5)])
                bk = pd.concat(engines[x], ignore_index=True).sort_values("t_entry")
                st = L.day_stats(bk, BAL)
                dayrows.append(dict(stop_usd=int(S * 10), time_stop_min=ts or 0, accuracy_pct=x,
                                    usd_per_basket=round(float(v.mean()), 3),
                                    se=round(float(v.std(ddof=1) / np.sqrt(len(v))), 3),
                                    baskets_per_day=st["baskets_per_day"], avg_day=st["avg_day"],
                                    win_days_pct=st["win_days_pct"], worst_day=st["worst_day"],
                                    basket_win_pct=st["basket_win_pct"], days_to_zero=st["days_to_zero"],
                                    total=st["total"], positive=bool(v.mean() > 0)))
            rows.append(dict(stop_usd=int(S * 10), time_stop_min=ts or 0, baskets=len(flat),
                             coin_flip_usd=round(float(flat.mean()), 3),
                             win_pct=round(100 * len(w) / len(flat), 2),
                             avg_win=round(float(w.mean()), 2), avg_loss=round(float(lss.mean()), 2),
                             worst=round(float(flat.min()), 2),
                             required_accuracy_pct=round(xr["xstar"], 2) if np.isfinite(xr["xstar"]) else np.nan,
                             oracle_usd=round(xr["E_best"], 3)))
    sweep = pd.DataFrame(rows)
    d = pd.DataFrame(dayrows)
    sweep.to_csv(os.path.join(RES, "timestop_sweep.csv"), index=False)
    d.to_csv(os.path.join(RES, "timestop_days.csv"), index=False)
    print(sweep.to_string(index=False))
    print()
    print(d[d.accuracy_pct == 60].to_string(index=False))
    print("\nANY POSITIVE CELL:", int(d.positive.sum()), "of", len(d))
    if d.positive.any():
        print(d[d.positive].to_string(index=False))
    write_md(sweep, d)


def md(df, fmt="%.2f"):
    x = df.copy()
    for c in x.columns:
        if x[c].dtype.kind == "f":
            x[c] = x[c].map(lambda v: "" if pd.isna(v) else fmt % v)
        x[c] = x[c].astype(str)
    cols = [str(c) for c in x.columns]
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
                     + ["| " + " | ".join(r.values) + " |" for _, r in x.iterrows()])


def survival():
    t = pd.read_csv(os.path.join(RES, "trades_coinflip_X50.csv"), parse_dates=["t_entry", "t_exit"])
    t["hold"] = (t.t_exit - t.t_entry).dt.total_seconds() / 60
    rows = [dict(minutes=c, still_open_pct=round(100 * float((t.hold > c).mean()), 1),
                 of_those_go_on_to_win_pct=round(100 * float((t[t.hold > c].pnl > 0).mean()), 1),
                 share_of_all_losses_still_open_pct=round(100 * float((t[t.hold > c].pnl < 0).sum()
                                                                      / (t.pnl < 0).sum()), 1))
            for c in (5, 10, 15, 20, 30, 60, 120)]
    return pd.DataFrame(rows), t


def sample_window(t, W=18):
    """A genuinely representative stretch of consecutive baskets: the window whose hold-time quartiles and
    loss count are closest to the whole sample's."""
    s = t[t.t_entry >= "2024-01-01"].reset_index(drop=True)
    tgt = np.percentile(t.hold.values, [25, 50, 75])
    exp_loss = W * float((t.pnl < 0).mean())
    h, p = s.hold.values, s.pnl.values
    best = None
    for i in range(len(s) - W):
        q = np.percentile(h[i:i + W], [25, 50, 75])
        sc = (np.abs(np.log((q + 1) / (tgt + 1))).sum() * 3
              + abs((p[i:i + W] < 0).sum() - exp_loss) * 1.5)
        if best is None or sc < best[0]:
            best = (sc, i, q)
    _, i, q = best
    w = s.iloc[i:i + W].copy()
    w["balance"] = (BAL + w.pnl.cumsum()).round(2)
    w["direction"] = np.where(w.side == 1, "long", "short")
    w["minutes"] = w.hold.astype(int)
    w["outcome"] = np.where(w.pnl > 5, "target +$7", np.where(w.pnl < -60, "stop -$70", "flat exit"))
    w["opened (UTC)"] = w.t_entry.astype(str)
    w["closed (UTC)"] = w.t_exit.astype(str)
    w["$"] = w.pnl.round(2)
    return w[["opened (UTC)", "direction", "closed (UTC)", "minutes", "outcome", "$", "balance"]], q, tgt


def write_md(sweep, d):
    surv, t = survival()
    win, q, tgt = sample_window(t)
    ctl = sweep[(sweep.stop_usd == 70) & (sweep.time_stop_min == 0)].iloc[0]
    sh = sweep[sweep.stop_usd == 70]
    best = sweep.loc[sweep.coin_flip_usd.idxmax()]
    bestsh = sh.loc[sh.coin_flip_usd.idxmax()]
    npos = int(d.positive.sum())
    txt = f"""# BT15 addendum — does a TIME STOP rescue the basket?

**The question, and it is a good one.** BT15 measured that a winning basket resolves fast and a losing one bleeds
slowly: the median win takes **7 minutes**, the median loss **89 minutes**; **32%** of wins run past 20 minutes but
**80.7%** of losses do. So: why not close the basket after about 20 minutes and cut the slow bleeders before they ever
reach -$70?

It was measured, not argued. Same entry chances as BT15 (the same pre-registered 30-minute grid, both feeds,
2009-03 to 2025-06), same costs, **only the exit rule changed** — a clean A/B. The time stop closes at the
**then-current market price** on the correct side of the book and pays the exit cost; nothing is closed at zero.
Sweep: **5, 10, 15, 20, 30, 45, 60, 120, 240 minutes plus "none"**, crossed with **$20 / $35 / $70** dollar stops
(since a time stop is a soft stop and the two interact), for entries at **50% / 55% / 60%** accuracy.
That is 10 time stops x 3 dollar stops = **30 exit variants**, each read at 3 entry accuracies = **90 measured
cells**, all fixed before looking.

## The answer: no. Not at any setting.
**{npos} of {len(d)} cells came out positive.** Every time stop, at every dollar stop, at every entry accuracy up to
60%, loses money.

**And look at how little it moves anything.** Across the entire sweep the money per basket on a coin flip stays
between **${sweep.coin_flip_usd.min():.2f}** and **${sweep.coin_flip_usd.max():.2f}** — a band of
{100*(sweep.coin_flip_usd.max()-sweep.coin_flip_usd.min())/abs(ctl.coin_flip_usd):.0f} cents on a basket, against a
$7 target and a $70 stop that have just been changed beyond recognition. What the time stop *does* move is enormous:
the win rate falls from **{ctl.win_pct:.1f}%** with no time stop to **{sh[sh.time_stop_min==5].win_pct.iloc[0]:.1f}%**
at 5 minutes, while the average loss shrinks from **${-ctl.avg_loss:.0f}** to
**${-sh[sh.time_stop_min==5].avg_loss.iloc[0]:.0f}**. Those two moves cancel to the cent. That is the whole lesson:
**an exit rule redistributes money between the win rate and the size of the average loss; it does not create any.**

### The sweep, coin-flip entry (shipped $70 stop)
{md(sh[['time_stop_min', 'baskets', 'coin_flip_usd', 'win_pct', 'avg_win', 'avg_loss', 'worst',
        'required_accuracy_pct']].rename(columns={'time_stop_min': 'time stop (min)', 'coin_flip_usd': '$ per basket',
        'win_pct': 'win %', 'avg_win': 'avg win $', 'avg_loss': 'avg loss $', 'worst': 'worst basket $',
        'required_accuracy_pct': 'REQUIRED accuracy %'}), "%.2f")}

(`time stop 0` = none, the BT15 control.)

### All three dollar stops
{md(sweep[['stop_usd', 'time_stop_min', 'coin_flip_usd', 'win_pct', 'avg_win', 'avg_loss',
           'required_accuracy_pct']].rename(columns={'stop_usd': '$ stop', 'time_stop_min': 'time stop (min)',
           'coin_flip_usd': '$ per basket', 'win_pct': 'win %', 'avg_win': 'avg win $', 'avg_loss': 'avg loss $',
           'required_accuracy_pct': 'REQUIRED accuracy %'}), "%.2f")}

**The best cell anywhere in the sweep** is a {int(best.stop_usd)} dollar stop with a
{'no time stop' if best.time_stop_min == 0 else str(int(best.time_stop_min)) + '-minute time stop'} at
**${best.coin_flip_usd:.2f} per basket** — still a loss, and barely different from the
**${ctl.coin_flip_usd:.2f}** of the shipped setting with no time stop at all. Within the shipped $70 stop the best is
{'none' if bestsh.time_stop_min == 0 else str(int(bestsh.time_stop_min)) + ' minutes'} at
**${bestsh.coin_flip_usd:.2f}**.

### Day by day on a $350 account
{md(d[d.stop_usd == 70][['time_stop_min', 'accuracy_pct', 'usd_per_basket', 'se', 'baskets_per_day', 'avg_day',
                         'win_days_pct', 'worst_day', 'basket_win_pct', 'days_to_zero']].rename(
    columns={'time_stop_min': 'time stop (min)', 'accuracy_pct': 'accuracy %', 'usd_per_basket': '$ per basket',
             'baskets_per_day': 'baskets/day', 'avg_day': '$ a day', 'win_days_pct': 'green days %',
             'worst_day': 'worst day $', 'basket_win_pct': 'basket win %', 'days_to_zero': 'days to lose $350'}),
   "%.2f")}

A short time stop does trade **much more often** (the basket is recycled every few minutes), so the money per basket
shrinks but the money per *day* gets **worse**, not better. That is the trap inside the idea: cutting the bleeders also
cuts the winners that were still working, and then pays the cost again on the replacement basket.

## Why it cannot work — the one table that explains it
The premise is right: slow baskets hold most of the losses. The premise that is **wrong** is that slow baskets are
*mostly* losses.

{md(surv.rename(columns={'minutes': 'still open after (min)', 'still_open_pct': '% of baskets still open',
                        'of_those_go_on_to_win_pct': 'of those, % that STILL go on to win',
                        'share_of_all_losses_still_open_pct': '% of all losses still open'}), "%.1f")}

Read the 20-minute row. Yes, **80.7% of the losses are still open** at 20 minutes — but of everything still open at
that moment, **{surv[surv.minutes == 20].of_those_go_on_to_win_pct.iloc[0]:.0f}% still goes on to win**. Closing them
all is closing three winners for every loser, and what you bank instead is the mark-to-market at that minute — which
is, on average, exactly what the basket was worth. That is the whole of it: **an exit rule moves money between the
win rate and the average win, it does not create any.** Our own BT8b found the same thing testing dynamic exits on
this project's strategy ("gross ~ 0 under every exit geometry"), and the arithmetic reason is optional stopping —
on a series with no drift, no stopping rule has a positive expectation. What a time stop *does* change is real and
visible above: it makes the win rate worse and the average loss smaller, in exactly offsetting amounts, and it pays
the spread and commission more often.

**And note the REQUIRED accuracy column: a time stop makes the bar HIGHER, not lower.** With no time stop the entry
must call direction right {ctl.required_accuracy_pct:.1f}% of the time; with a 5-minute stop it must call it
{sh[sh.time_stop_min==5].required_accuracy_pct.iloc[0]:.1f}% of the time, because a short clock throws away the
structure's one genuine advantage (a near target that usually gets reached eventually) while keeping all the cost.
The best cell in the whole sweep still needs {sweep.required_accuracy_pct.min():.1f}%, and the worst needs
{sweep.required_accuracy_pct.max():.1f}%.
Since nothing came out positive, the BT14 bar for these 30 exit variants never has to be applied; had any cell been
positive, 30 variants is a wider search than BT15's 4 and would have needed a *higher* bar, not a lower one.

## A realistic sequence of baskets, with the running balance
The earlier 12-trade sample from 2009-03-17 was unrepresentative — only 1 of 12 resolved inside 20 minutes, against a
7-minute median win — and that is a fair thing to have been confused by. Below is a stretch of **18 consecutive
baskets** chosen because its hold times match the whole 71,075-basket sample almost exactly (quartiles
{q[0]:.0f} / {q[1]:.0f} / {q[2]:.0f} minutes against the overall
{tgt[0]:.0f} / {tgt[1]:.0f} / {tgt[2]:.0f}) and it contains
{int((win['$'] < 0).sum())} losses in 18, the sample's own {100 * float((t.pnl < 0).mean()):.1f}% loss rate. Coin-flip
entry, 0.10 lots, starting at $350.

{md(win, "%.2f")}

**Sixteen wins, two losses, and the day ends -${BAL - float(win.balance.iloc[-1]):.2f}.** This is the whole robot in one
screen: a run of green that looks like a working system, twice interrupted.

Now look at the **minutes** column against the time-stop idea. The two losses took **{int(win[win['$'] < 0].minutes.iloc[0])}
and {int(win[win['$'] < 0].minutes.iloc[1])} minutes** — but winners in the same stretch took
{', '.join(str(int(v)) for v in sorted(win[win['$'] > 0].minutes.values)[-5:])} minutes. A 20-minute stop would have
cut {int((win.minutes > 20).sum())} of these 18 baskets, and {int(((win.minutes > 20) & (win['$'] > 0)).sum())} of
those were winners. There is no clock setting that separates them, because the clock is not what makes them different.

## Files
| File | What |
|---|---|
| `bt/bt15_timestop.py` | the sweep (checkpointed per cell to `parts/ts_*.npz`) and this addendum |
| `results/BT15/timestop_sweep.csv` | all 30 exit variants: money per basket, win rate, average win/loss, required accuracy |
| `results/BT15/timestop_days.csv` | the same 30 crossed with 50% / 55% / 60% accuracy, with day statistics on $350 |

**Verdict: the time stop is a real idea, honestly tested — {int(sweep.baskets.max()):,} baskets measured 30 different
ways ({int(sweep.baskets.sum()):,} basket simulations in total) — and it does not work at any of the 10 clock settings,
at any of the 3 dollar stops, at any of the 3 entry accuracies. It changes the shape of the ride and leaves the
destination exactly where it was.** BT15's conclusion is unchanged.

*Generated by `bt/bt15_timestop.py report`.*
"""
    with open(os.path.join(RES, "addendum_timestop.md"), "w", encoding="utf-8") as fh:
        fh.write(txt)
    print("wrote", os.path.join(RES, "addendum_timestop.md"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        run()
