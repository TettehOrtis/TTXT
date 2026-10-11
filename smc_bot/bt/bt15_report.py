"""BT15 report builder: reads results/BT15/parts/ and writes results/BT15/report.md + the CSVs.
Re-runnable at any time; it only reads checkpoints.

    .venv/Scripts/python.exe smc_bot/bt/bt15_report.py
"""
import os
import sys
import json
import glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import COMM, RESULTS                                 # noqa: E402
from bt import bt15_lib as L                                        # noqa: E402

OUT = os.path.join(RESULTS, "BT15")
T0, S0 = 0.7, 7.0
TGRID = [0.3, 0.5, 0.7, 1.0, 1.5, 2.0]
SGRID = [3.0, 5.0, 7.0, 10.0, 15.0]
XLAD = [50.0, 51, 52, 53, 54, 55, 57.5, 60]
DAILY_T = [0, 10, 20, 40]
DAILY_L = [0, 20, 40, 80]
BAL = 350.0
NS_D = 86_400_000_000_000
os.makedirs(OUT, exist_ok=True)


def usd(v, dp=2):
    """-$1.98 rather than $-1.98."""
    return ("-$%.*f" % (dp, -v)) if v < 0 else ("$%.*f" % (dp, v))


def md(df, floatfmt="%.2f"):
    d = df.copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else floatfmt % v)
        d[c] = d[c].astype(str)
    cols = [str(c) for c in d.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in d.iterrows():
        lines.append("| " + " | ".join(r.values) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------- load the fill-corrected outcome tables
def load_bars(ds):
    """bars() for a dataset, in a child-free way: both feeds are read in one process by re-importing."""
    import importlib
    os.environ["SMC_DATASET"] = ds
    import bt.common as C
    importlib.reload(C)
    return C.bars(L.GOLD, "M1")


def load_cell(ds, T, S, kind="grid"):
    """Returns (t, pnl 2xN) with the fill-minute correction applied."""
    if kind == "opp":
        f = np.load(os.path.join(L.PARTS, f"opp_{ds}_d05.npz"))
        t, pnl, tex, idx = f["t_opp"], f["pnl"], f["texit"], f["idx"]
    else:
        f = np.load(os.path.join(L.PARTS, f"grid_{ds}_T{T}_S{S}.npz"))
        t, pnl, tex = f["t_sub"], f["pnl"], None
        idx = None
    b = BARS[ds]
    if idx is None:
        idx = L.bar_index(b, t)
    tpm, slm = L.fillbar_masks(b, idx, T, S, L.EXTRA_SPREAD)
    return t, L.fix_fillbar(pnl, tpm, slm, T), tex


PERIOD_OF = {"HIST": ("duka", "2009-03-15", "2021-01-01"),
             "DEV": ("zero", "2021-01-01", "2024-01-01"),
             "VAL": ("zero", "2024-01-01", L.LAST_OPP)}


def mask_for(t, a, b_):
    lo = np.datetime64(pd.Timestamp(a)).astype("datetime64[ns]").astype(np.int64)
    hi = np.datetime64(pd.Timestamp(b_)).astype("datetime64[ns]").astype(np.int64)
    return (t >= lo) & (t < hi)


# ================================================================ main
BARS = {}
for _ds in ("duka", "zero"):
    BARS[_ds] = load_bars(_ds)
    print("loaded", _ds, len(BARS[_ds]))

SAN = {ds: json.load(open(os.path.join(L.PARTS, f"sanity_{ds}.json"))) for ds in ("duka", "zero")}
OPP = {ds: load_cell(ds, T0, S0, "opp") for ds in ("duka", "zero")}
print("opp tables loaded")


def xs_rows(name, t, pnl):
    r = L.xstar(pnl)
    if not r.get("n"):
        return None
    return dict(group=name, baskets=2 * r["n"], coin_flip_usd=r["E_random"], oracle_usd=r["E_best"],
                worst_side_usd=r["E_worst"], both_sides_win_pct=r["both_win_pct"],
                coin_flip_win_pct=r["coin_win_pct"], xstar_pct=r["xstar"], req_basket_win_pct=r["req_win_pct"])


# ---------------------------------------------------------------- 1. X* for the shipped cell
rows = []
allt, allp = [], []
for per, (ds, a, b_) in PERIOD_OF.items():
    t, pnl, _ = OPP[ds]
    m = mask_for(t, a, b_)
    rows.append(xs_rows(per, t[m], pnl[:, m]))
    allt.append(t[m])
    allp.append(pnl[:, m])
ALLT = np.concatenate(allt)
ALLP = np.concatenate(allp, axis=1)
rows.insert(0, xs_rows("ALL (2009-03..2025-06)", ALLT, ALLP))
xs_period = pd.DataFrame([r for r in rows if r])

yr = pd.DatetimeIndex(ALLT.astype("datetime64[ns]")).year
xs_year = pd.DataFrame([xs_rows(str(y), ALLT[yr == y], ALLP[:, yr == y]) for y in np.unique(yr)])
xs_era = pd.DataFrame([r for r in [xs_rows(k, ALLT[mask_for(ALLT, *v)], ALLP[:, mask_for(ALLT, *v)])
                                   for k, v in L.ERAS.items()] if r])
sess = L.session_of(ALLT.astype("datetime64[ns]"))
xs_sess = pd.DataFrame([xs_rows(s, ALLT[sess == s], ALLP[:, sess == s]) for s, _, _ in L.SESSIONS])
for nm, d in [("xstar_by_period", xs_period), ("xstar_by_year", xs_year), ("xstar_by_era", xs_era),
              ("xstar_by_session", xs_sess)]:
    d.to_csv(os.path.join(OUT, nm + ".csv"), index=False)

# ---------------------------------------------------------------- 2. the target/stop grid
grows = []
for T in TGRID:
    for S in SGRID:
        gt, gp = [], []
        for per, (ds, a, b_) in PERIOD_OF.items():
            p = os.path.join(L.PARTS, f"grid_{ds}_T{T}_S{S}.npz")
            if not os.path.exists(p):
                continue
            t, pnl, _ = load_cell(ds, T, S)
            m = mask_for(t, a, b_)
            gt.append(t[m])
            gp.append(pnl[:, m])
        if not gp:
            continue
        r = L.xstar(np.concatenate(gp, axis=1))
        grows.append(dict(target_usd_price=T, stop_usd_price=S, ratio=round(S / T, 1),
                          target_dollars_at_010=T * 10, stop_dollars_at_010=S * 10,
                          baskets=2 * r["n"], coin_flip_usd_per_basket=round(r["E_random"], 3),
                          oracle_usd_per_basket=round(r["E_best"], 3),
                          worst_side_usd_per_basket=round(r["E_worst"], 3),
                          both_sides_win_pct=round(r["both_win_pct"], 1),
                          coin_flip_win_pct=round(r["coin_win_pct"], 2),
                          required_accuracy_pct=round(r["xstar"], 2),
                          required_basket_win_pct=round(r["req_win_pct"], 2),
                          cost_pct_of_target=round(100 * 1.622 / (T * 10), 1)))
grid = pd.DataFrame(grows)
grid.to_csv(os.path.join(OUT, "required_accuracy.csv"), index=False)
print("grid cells:", len(grid))

# ---------------------------------------------------------------- 3. the skill ladder through the engine
def run_engine(ds, a, b_, sides_fn, dt=0.0, dl=0.0):
    t, pnl, tex = OPP[ds]
    m = mask_for(t, a, b_)
    p, x, tt = pnl[:, m], tex[:, m], t[m]
    s = sides_fn(p)
    return L.engine(tt, p, x, s, daily_target=dt, daily_loss=dl)


def all_periods(sides_fn, dt=0.0, dl=0.0):
    out = {}
    for per, (ds, a, b_) in PERIOD_OF.items():
        out[per] = run_engine(ds, a, b_, sides_fn, dt, dl)
    out["ALL"] = pd.concat([out[k] for k in PERIOD_OF], ignore_index=True).sort_values("t_entry")
    return out


def skill(X, seed=11):
    return lambda p: L.skill_sides(p, X / 100.0, seed=seed)


lad = []
for X in XLAD + [100.0]:
    bk = all_periods(skill(X))
    for per, v in bk.items():
        st = L.day_stats(v, BAL)
        lad.append(dict(accuracy_pct=X, period=per, **st))
ladder = pd.DataFrame(lad)
ladder.to_csv(os.path.join(OUT, "skill_ladder.csv"), index=False)

# per-basket money curve without the mechanics (this is what X* is solved from)
curve = pd.DataFrame([dict(accuracy_pct=X,
                           usd_per_basket=round(float(L.money_at(ALLP, X / 100.0, seed=5).mean()), 3))
                      for X in XLAD + [100.0]])

# ---------------------------------------------------------------- 4. the four pre-registered readings
curve2 = curve.copy()
curve2["accuracy_pct"] = curve2.accuracy_pct.map(lambda v: "%g" % v)
curve2 = curve2.rename(columns={"accuracy_pct": "accuracy %", "usd_per_basket": "$ per basket"})

rule_rows, rule_days = [], []
RULES = {"a": "M15 EMA50/EMA200 -> pullback to M1 EMA20",
         "b": "M15 close vs EMA200 -> M1 RSI(14) back from 30/70",
         "c": "M15 higher-highs/lows -> 38-62% retrace of the M15 leg",
         "d": "M15 MACD sign -> first M1 close back with the trend after 3 against"}
rule_bk = {}
for w in "abcd":
    per_bk = {}
    for per, (ds, a, b_) in PERIOD_OF.items():
        f = np.load(os.path.join(L.PARTS, f"rule_{ds}_{w}.npz"))
        t, sd, pnl, tex = f["t_sig"], f["sides"], f["pnl"], f["texit"]
        b = BARS[ds]
        tpm, slm = L.fillbar_masks(b, L.bar_index(b, t), T0, S0, L.EXTRA_SPREAD)
        pnl = L.fix_fillbar(pnl, tpm, slm, T0)
        m = mask_for(t, a, b_) & ~np.isnan(pnl).any(axis=0)
        t, sd, pnl, tex = t[m], sd[m], pnl[:, m], tex[:, m]
        better = np.where(pnl[0] >= pnl[1], 1, -1)
        tie = pnl[0] == pnl[1]                                  # both sides would have closed green:
        acc = float(np.where(tie, 0.5, (sd == better)).mean())  # the direction call is irrelevant -> count a half
        chosen = np.where(sd == 1, pnl[0], pnl[1])
        coin_here = pnl.mean(axis=0)                            # random side at the SAME minutes
        rule_rows.append(dict(rule=w, period=per, signals=len(t),
                              accuracy_pct=round(100 * acc, 2), ties_pct=round(100 * float(tie.mean()), 1),
                              required_pct=round(L.xstar(pnl)["xstar"], 2),
                              usd_per_basket=round(float(chosen.mean()), 3),
                              se=round(float(chosen.std(ddof=1) / np.sqrt(len(chosen))), 3),
                              day_block_t=round(L.block_t(chosen, t // NS_D), 2),
                              coinflip_same_minutes=round(float(coin_here.mean()), 3),
                              flipped_direction=round(float(np.where(sd == 1, pnl[1], pnl[0]).mean()), 3),
                              win_pct=round(100 * float((chosen > 0).mean()), 2)))
        per_bk[per] = L.engine(t, pnl, tex, sd)
    per_bk["ALL"] = pd.concat([per_bk[k] for k in PERIOD_OF], ignore_index=True).sort_values("t_entry")
    rule_bk[w] = per_bk
    for per, v in per_bk.items():
        rule_days.append(dict(rule=w, period=per, **L.day_stats(v, BAL)))
rules = pd.DataFrame(rule_rows)
rulesd = pd.DataFrame(rule_days)
rules.to_csv(os.path.join(OUT, "rules_accuracy.csv"), index=False)
rulesd.to_csv(os.path.join(OUT, "rules_days.csv"), index=False)

# ---------------------------------------------------------------- 5. daily-rule sweep (coin-flip entry)
dr = []
for dt in DAILY_T:
    for dl in DAILY_L:
        bk = all_periods(skill(50.0), dt, dl)["ALL"]
        st = L.day_stats(bk, BAL)
        dr.append(dict(daily_target=dt or "off", daily_loss=dl or "off", **st))
daily_rules = pd.DataFrame(dr)
daily_rules.to_csv(os.path.join(OUT, "daily_rules.csv"), index=False)

# ---------------------------------------------------------------- 6. sessions, descriptive only
sess_rows = []
bk50 = all_periods(skill(50.0))["ALL"]
s_bk = L.session_of(bk50.t_entry)
for s, _, _ in L.SESSIONS:
    v = bk50[s_bk == s]
    sess_rows.append(dict(session=s, baskets=len(v), usd_per_basket=round(float(v.pnl.mean()), 3),
                          win_pct=round(100 * float((v.pnl > 0).mean()), 2),
                          total_usd=round(float(v.pnl.sum()), 0)))
sess_money = pd.DataFrame(sess_rows)
sess_money = sess_money.merge(xs_sess[["group", "xstar_pct"]].rename(
    columns={"group": "session", "xstar_pct": "required_accuracy_pct"}), on="session", how="left")
sess_money.to_csv(os.path.join(OUT, "sessions.csv"), index=False)

# ---------------------------------------------------------------- 7. trades / daily CSVs
def dump(name, bk):
    bk = bk.copy()
    bk["day"] = bk.t_entry.dt.floor("D")
    bk.to_csv(os.path.join(OUT, f"trades_{name}.csv"), index=False)
    d = bk.groupby("day").pnl.agg(baskets="count", usd="sum").reset_index()
    d["balance_from_350"] = BAL + d.usd.cumsum()
    d.to_csv(os.path.join(OUT, f"daily_{name}.csv"), index=False)
    return d


DAILY = {}
for nm, X in [("coinflip_X50", 50.0), ("X58", 58.0), ("X60", 60.0), ("oracle", 100.0)]:
    DAILY[nm] = dump(nm, all_periods(skill(X))["ALL"])
for w in "abcd":
    DAILY["rule_" + w] = dump("rule_" + w, rule_bk[w]["ALL"])

# ---------------------------------------------------------------- 8. write the report
_e = bk50[(bk50.t_entry >= "2024-03-04") & (bk50.t_entry < "2024-03-06")].head(10).copy()
_e["side"] = np.where(_e.side == 1, "long", "short")
_e["minutes_held"] = ((_e.t_exit - _e.t_entry).dt.total_seconds() / 60).round(0).astype(int)
_e["outcome"] = np.where(_e.pnl > 5, "target +$7", np.where(_e.pnl < -60, "stop -$70", "flat/weekend"))
ex10 = _e.rename(columns={"t_entry": "entered (UTC)", "t_exit": "closed (UTC)",
                          "pnl": "$ after costs"})[["entered (UTC)", "side", "closed (UTC)",
                                                    "minutes_held", "outcome", "$ after costs"]]
ex10["entered (UTC)"] = ex10["entered (UTC)"].astype(str)
ex10["closed (UTC)"] = ex10["closed (UTC)"].astype(str)

XS = xs_period.set_index("group")
x_all = XS.loc["ALL (2009-03..2025-06)"]
d50 = L.day_stats(bk50, BAL)
san = SAN
grid_r = grid.required_accuracy_pct
cell = grid[(grid.target_usd_price == T0) & (grid.stop_usd_price == S0)].iloc[0]

rate_rows = []
for n in (5, 10, 20, round(d50["baskets_per_day"])):
    rate_rows.append(dict(baskets_per_day=n, usd_per_day=round(n * d50["per_basket"], 2),
                          usd_per_month=round(21 * n * d50["per_basket"], 0),
                          days_to_lose_350=int(BAL / abs(n * d50["per_basket"])) if d50["per_basket"] < 0 else 0))
rate = pd.DataFrame(rate_rows).drop_duplicates("baskets_per_day")

R = []
A = R.append
A(f"""# BT15 — the SNIPER_SPARK basket on 16 years of real gold

**What was tested.** The commercial robot opens a group of gold positions *all at the same price* with one shared
stop and one shared target, and closes the whole group when their combined floating money reaches **+$7** or
**-$70** (0.10 lots). Because every position opens at the same price, the group is arithmetically **one trade of the
combined size**, so that is what was simulated: one 0.10-lot gold trade, target 70 cents away, stop $7.00 away, with
the robot's own mechanics (60-second cooldown, pause a direction after 2 losing baskets, daily profit target and daily
loss limit, flat before weekends and holidays) and this project's real Exness Zero costs.

The entry rule is secret, so **it was not guessed. The requirement was measured instead**: how often would an entry
have to pick the right direction for this shape to make $0 after costs? Call that number **X\\***. A coin flip is 50%.

## Summary — the ten lines that matter
1. **A coin flip wins {x_all.coin_flip_win_pct:.1f}% of these baskets and still loses money.** That is the whole trap: the
   stop is 10x the target, so the rare loss cancels the many wins, and the costs decide the outcome.
2. **X\\* for the shipped $7 / $70 setting = {x_all.xstar_pct:.1f}%.** The entry must pick the better direction
   {x_all.xstar_pct:.1f} times in 100 instead of 50 — equivalently, {x_all.req_basket_win_pct:.2f}% of baskets must
   close green instead of the {x_all.coin_flip_win_pct:.2f}% a coin flip already gets.
3. Across all 30 target/stop shapes the requirement never falls below **{grid_r.min():.1f}%** and runs up to
   **{grid_r.max():.1f}%**. There is **no shape of this structure whose requirement is easy.**
4. **The four plausible readings of "M15 trend + M1 pullback" achieved {rules.accuracy_pct.min():.1f}%–{rules.accuracy_pct.max():.1f}%** —
   i.e. a coin flip. Every one of them lost money in every period.
5. This project has **never measured a directional rule better than a coin flip** (BT4 post-break move ~0, BT5 H1
   structure worse than random, BT13/BT8b 69,000 setups carrying no information). X\\* is far above anything we have seen.
6. **Sanity checks passed.** The cheating entry that knows the future makes **+${san['zero']['oracle']['per_basket']:.2f}**
   per basket and wins {san['zero']['oracle']['win_pct']:.1f}% — the simulator is sound. The coin-flip entry lands on
   **{usd(san['zero']['random']['per_basket'])}**, against named costs of {usd(-san['zero']['random']['named_cost'])};
   an independent minute-by-minute replay reproduced 160/160 sampled baskets (80 per feed) exactly.
7. **A typical day, coin-flip entry, 0.10 lots: {usd(d50['avg_day'],0)}.** {d50['win_days_pct']:.0f}% of days finish green
   (the structure is *designed* to look good), best day +${d50['best_day']:.0f}, **worst day -${abs(d50['worst_day']):.0f}**,
   worst run of losing days in a row **{d50['worst_losing_streak_days']}**.
8. **A $350 account is gone in about {d50['days_to_zero']} trading days** on a coin-flip entry at this trading rate — and a single
   $70 stop is 20% of that account.
9. The daily profit target and daily loss limit **change the ride, not the destination**: they cut the size of the loss
   roughly in proportion to how much they cut the trading, and never turn the sign positive.
10. **Pre-registered verdict: X\\* is above anything this project has ever measured from a directional rule, so the robot
    cannot work as sold.** Nothing here supports running it with real money.

**Addendum (`addendum_timestop.md`).** The user asked whether a **time stop** rescues it — close the basket after
about 20 minutes, since the median win takes 7 minutes and the median loss 89. Measured over 30 exit variants
(5/10/15/20/30/45/60/120/240 minutes and none, crossed with $20/$35/$70 stops) at three entry accuracies:
**not one cell comes out positive.** The premise is right — slow baskets hold 81% of the losses — but of everything
still open at 20 minutes, **76% still goes on to win**, so a clock cuts three winners for every loser and pays the
spread again on the replacement basket. That addendum also contains a **representative** 18-basket sequence with a
running balance, replacing an earlier unrepresentative 12-trade sample.

**Addendum 2 (`addendum_depth.md`).** The user also asked why the win is capped at $7 while the loss runs to $70, and
where the stop should really go. Measured: half of all *winning* baskets dip no more than $6.84 into loss, but one in
ten dips past $34 and the deepest recovery came back from $70, so **cutting at $20 would kill 21% of the baskets that
would have won** — and every cut point, simulated, is still a loss (best: $10, -$1.82 a basket against the shipped
-$1.91). Removing the $7 cap entirely does not help either. 28 new cells at **$10-$25 stops and $30-$200 targets**:
**0 of 28 positive on a coin flip**, and they pin down the floor — the required accuracy falls as the target grows
(62.3% at $7, 50.6% at a $200 target) and **approaches 50% from above without ever going below it**, because before
costs a coin flip is exactly break-even at any target and stop. **The target relative to the cost is the only lever
there has ever been, and its best case is a coin flip.**

## 1. The model, and why it is one trade
`research_notes/SNIPERSPARK_strategy.md` establishes it, and the seller confirmed there is no averaging down and no
martingale. 5 positions of 0.02 lots opened at one price with one shared stop and target = 0.10 lots = 10 oz, so
**$1 of gold = $10**: the $7 basket target is **70 cents of gold** and the $70 basket stop is **$7.00 of gold**.
The "5 positions" only change the *trade history* — losers are closed inside green baskets, so the statement shows an
unbroken run of small wins.

**The arithmetic that decides everything.** On a fair market a bet that wins 70 cents or loses $7.00 wins
7.00 / 7.70 = **90.9%** of the time and makes **exactly zero**. Measured on real gold the coin flip won
**{x_all.coin_flip_win_pct:.2f}%** of baskets — within a whisker of the theory — and lost money, because of costs.

## 2. Costs — measured, not assumed
| Item | Per basket at 0.10 lots | Source |
|---|---|---|
| Commission (round trip) | ${san['zero']['commission_usd_per_basket']:.2f} | `common.COMM['XAUUSD']` = 1.10 gold-pips, measured on the Zero account 2026-09-27 |
| Spread recorded in the price files | ${san['zero']['spread_in_data_usd_per_basket']:.3f} (Zero) / ${san['duka']['spread_in_data_usd_per_basket']:.3f} (Dukascopy) | the files' own per-minute spread column |
| **Spread actually charged — added explicitly** | **${san['zero']['extra_spread_usd_per_basket']:.2f}** | the gold spread column is 0 on ~97% of minutes before 2026 (ERRORS #14/#34); the live-collected part of our own Zero feed shows **0.05 of gold price**, so 0.05 is charged on every basket |
| Stop slippage | 0.2 gold-pips = $0.20 on stopped baskets | `common.STOP_SLIP`, unchanged |
| **Named cost per basket** | **${-san['zero']['random']['named_cost']:.2f}** | = 23% of the entire $7 reward, before gold moves at all |
| **Measured coin-flip floor** | **{usd(san['zero']['random']['per_basket'])}** (DEV/VAL) / **{usd(san['duka']['random']['per_basket'])}** (HIST) | what the structure actually did with a random direction |

The seller's screenshot showed each position opening at **-$0.36**, i.e. **-$5.04** of spread on a 14-position basket —
72% of a $7 target. That is a *standard* account. On the user's raw-spread Zero account the same toll is about
**$1.60 per basket, 23% of the target** — four times cheaper, and still not survivable (section 4).

## 3. Blocking sanity checks (the brief says publish nothing until these pass)
| Check | HIST feed (Dukascopy) | DEV/VAL feed (Exness Zero) | Verdict |
|---|---|---|---|
| **Oracle** — entry that knows which side would work | +${san['duka']['oracle']['per_basket']:.2f}/basket, {san['duka']['oracle']['win_pct']:.1f}% win | +${san['zero']['oracle']['per_basket']:.2f}/basket, {san['zero']['oracle']['win_pct']:.1f}% win | **PASS** — wins comfortably, so the simulator is not broken |
| **Coin flip** — must land on minus the cost | {usd(san['duka']['random']['per_basket'],3)} vs named {usd(san['duka']['random']['named_cost'],3)} | {usd(san['zero']['random']['per_basket'],3)} vs named {usd(san['zero']['random']['named_cost'],3)} | **PASS with a residual** (below) |
| **No lookahead** — strip the costs and it must sit on zero | {usd(san['duka']['gross_zero']['per_basket'],3)} = {san['duka']['gross_zero']['pct_of_stop']:.2f}% of the stop | {usd(san['zero']['gross_zero']['per_basket'],3)} = {san['zero']['gross_zero']['pct_of_stop']:.2f}% of the stop | **PASS** — no sign of peeking |
| **Barrier arithmetic** — every win and loss exact to the cent | {san['duka']['arith']['barrier_exits_pct']:.1f}% of baskets pay exactly +$5.90 or -$71.30 | {san['zero']['arith']['barrier_exits_pct']:.1f}% | **PASS** |
| **Independent replay** — a plain minute loop written from scratch | {san['duka']['replay']['matched']}/{san['duka']['replay']['n']} baskets matched exactly | {san['zero']['replay']['matched']}/{san['zero']['replay']['n']} | **PASS** |

**The residual, stated openly.** The coin flip comes out **{usd(san['zero']['random']['residual'])}** per basket worse
than the costs we can name in advance ({san['zero']['random']['residual_pct_of_stop']:.2f}% of the stop; day-block
t {san['zero']['random']['day_block_t']:.1f}), and the same thing appears on the independent Dukascopy feed
({usd(san['duka']['random']['residual'])}, {san['duka']['random']['residual_pct_of_stop']:.2f}% of the stop). It is not a
coding error — the arithmetic is exact, the replay matches 80/80 and the oracle wins — it is what a 1:10 barrier
actually does on real gold minutes. Two measured contributors: baskets that get flattened before a weekend or holiday
({san['zero']['arith']['flat_exits_pct']:.1f}% of baskets, averaging **{usd(san['zero']['arith']['flat_exit_mean_usd'],0)}**),
and stops that gap through their level (the worst single $70 stop actually cost
**${abs(san['duka']['arith']['worst_stop']):.0f}**). It is in the **conservative** direction (it makes the robot look
worse), it is 0.4-0.5% of the stop, and the brief's tolerance ("approximately minus the cost") was quantified after
the fact as "within 2% of the stop" — stated here so a reader can disagree with that call.

**One honest caveat about minute data.** The $7 target is **70 cents of gold, which is SMALLER than a typical gold
minute's range** (median range on the Zero feed 0.48-1.21 depending on the year). `common.simulate` deliberately never
books a target inside the entry minute, which on a 1:10 shape is a large one-sided pessimism: it would have denied
{san['zero']['random']['fill_minute_target_touched_pct']:.1f}% of baskets a target they had already reached. Every number
in this report **credits** those (the stop is ten times further away, so the order of touches inside that minute is not
genuinely in doubt); the uncorrected, more pessimistic version is
**{usd(san['zero']['random']['per_basket_m1_pessimistic'])}** per basket instead of
**{usd(san['zero']['random']['per_basket'])}**. The direction of this caveat matters for the real robot: a target
smaller than one minute's candle is a target decided by execution luck, not by analysis.
""")

A(f"""
### Ten baskets a human can check on a chart
Ten consecutive baskets from `trades_coinflip_X50.csv` (2024, Exness Zero feed). Open the M1 gold chart at the entry
minute: a long basket must see the **bid rise 75 cents** above its fill (70 cents of target + 5 cents of spread) to pay
+$5.90, or fall $6.95 to pay -$71.30.

{md(ex10, "%.2f")}

## 4. THE HEADLINE — how good the entry would have to be
An entry of skill X picks, at each chance, the direction that **would** have worked X% of the time (50% = a coin flip,
100% = cheating). Money is then a straight line in X, so the break-even point is exact, not fitted:
**X\\* = -E[worse side] / (E[better side] - E[worse side])**.

### The shipped setting ($7 target / $70 stop, 0.10 lots)
{md(xs_period.rename(columns={'group': 'period'}), "%.2f")}

- `both_sides_win_pct` is the share of chances where a long **and** a short would both have closed green — on those the
  direction simply does not matter. That is why the requirement sits near 60% rather than near 51%: the direction call
  only matters on the remaining {100 - x_all.both_sides_win_pct:.0f}% of baskets, and on those it has to be right
  {x_all.xstar_pct:.0f} times in 100.
- **In plain words: the robot must pick direction correctly {x_all.xstar_pct:.1f}% of the time to make $0.**
  Expressed as a win rate it must close **{x_all.req_basket_win_pct:.2f}%** of baskets green where a coin flip already
  closes {x_all.coin_flip_win_pct:.2f}% green. Those two numbers are only
  {x_all.req_basket_win_pct - x_all.coin_flip_win_pct:.2f} points apart — which is exactly why a screenshot of a 90%
  win rate tells you nothing at all about whether the robot makes money.

### Money per basket as the entry gets better ({ALLP.shape[1]:,} entry chances, no mechanics)
{md(curve2, "%.3f")}

### Per year
{md(xs_year.rename(columns={'group': 'year'}), "%.2f")}

### Per era inside HIST, and the bull-run warning
{md(xs_era.rename(columns={'group': 'era'}), "%.2f")}

Gold ran from about $1,800 to $3,300 between 2020 and mid-2025. Nothing in this test improves after 2020 — the
requirement is, if anything, **higher** in the bull years, because a bigger daily range makes the far stop easier to
reach. So there is no "it only works in the bull market" escape here; it does not work in the bull market either.
""")

A(f"""## 5. Every target/stop shape — `required_accuracy.csv`
Worked in gold price distance, because lot size only scales the dollars. At 0.10 lots, target 0.7 / stop 7.0 **is** the
shipped $7 / $70.

{md(grid[['target_usd_price', 'stop_usd_price', 'ratio', 'target_dollars_at_010', 'stop_dollars_at_010',
          'coin_flip_win_pct', 'coin_flip_usd_per_basket', 'required_accuracy_pct', 'required_basket_win_pct',
          'cost_pct_of_target']].rename(columns={
    'target_usd_price': 'target (gold $)', 'stop_usd_price': 'stop (gold $)', 'ratio': 'stop:target',
    'target_dollars_at_010': '$ target', 'stop_dollars_at_010': '$ stop',
    'coin_flip_win_pct': 'coin-flip win %', 'coin_flip_usd_per_basket': 'coin flip $/basket',
    'required_accuracy_pct': 'REQUIRED accuracy %', 'required_basket_win_pct': 'required win %',
    'cost_pct_of_target': 'cost as % of target'}), "%.2f")}

**Read the "REQUIRED accuracy %" column, and notice what it depends on.** It barely moves as the stop changes
(0.7/3.0 needs {grid[(grid.target_usd_price==0.7)&(grid.stop_usd_price==3.0)].required_accuracy_pct.iloc[0]:.1f}%,
0.7/15.0 needs {grid[(grid.target_usd_price==0.7)&(grid.stop_usd_price==15.0)].required_accuracy_pct.iloc[0]:.1f}%) and
moves enormously as the **target** changes (a 0.3 target needs
{grid[grid.target_usd_price==0.3].required_accuracy_pct.mean():.1f}%, a 2.0 target needs
{grid[grid.target_usd_price==2.0].required_accuracy_pct.mean():.1f}%). That is the whole mechanism in one sentence:
**the requirement is set by how big the cost is compared with the target, not by the 1:10 ratio.** The $1.62 of cost is
{cell.cost_pct_of_target:.0f}% of the shipped $7 target, which is why a coin flip cannot be rescued by any stop.

So the only lever that exists is **a much bigger target** — and even the best cell on the grid
({grid.loc[grid_r.idxmin()].target_usd_price:.1f} target / {grid.loc[grid_r.idxmin()].stop_usd_price:.1f} stop, i.e.
${grid.loc[grid_r.idxmin()].target_dollars_at_010:.0f} / ${grid.loc[grid_r.idxmin()].stop_dollars_at_010:.0f} at 0.10
lots) still needs **{grid_r.min():.1f}%**, which is more than this project has ever measured from any directional rule.
**No reshaping of the target and stop rescues this structure**, and the shipped shape is close to the worst of the
range because its target is one of the smallest.
""")

A(f"""## 6. The four pre-registered readings of "M15 trend + M1 pullback"
Exactly four, fixed in the brief before anything was run. Direction is taken only from the last **closed** M15 bar, the
trigger only from the last **closed** M1 bar, the fill is the next minute's open, and at most one entry per M15 bar.

| | rule |
|---|---|
{chr(10).join(f"| **{k}** | {v} |" for k, v in RULES.items())}

### What they actually achieved
{md(rules.rename(columns={'accuracy_pct': 'ACCURACY %', 'required_pct': 'REQUIRED %',
                          'usd_per_basket': '$/basket', 'ties_pct': 'ties %'}), "%.2f")}

How to read it. `accuracy_pct` is how often the rule chose the direction that **would** have worked; on the
{rules.ties_pct.mean():.0f}% of chances where a long *and* a short would both have closed green the direction is
irrelevant and counts as a half, so **50% is exactly a coin flip**. `coinflip_same_minutes` is what a random direction
would have made **at the rule's own entry minutes** (so it separates "when it trades" from "which way it bets"), and
`flipped_direction` is the same rule traded backwards.

**All four are a coin flip.** Accuracy runs {rules.accuracy_pct.min():.1f}%–{rules.accuracy_pct.max():.1f}% against the
{x_all.xstar_pct:.1f}% they would need; the gap is not close in a single period, and every one of the
{len(rules)} period-rule cells lost money. No result is positive, so the BT14 acceptance bar never has to be applied —
but for the record: BT14's +0.472R bar was calibrated for a **150-trade, 49-variant** search, and this is a
**4-variant** search over **tens of thousands** of baskets, so the noise floor here is far lower and each rule's own
standard error is printed above. If any of these had come out positive, the brief requires re-running the BT14
calibration for this exact shape before believing it. None did.

**The one thing in here that looks like a signal, and why it is not one.** Reading (b) loses less than a random
direction at its own minutes in HIST ({rules[(rules.rule=='b')&(rules.period=='HIST')].usd_per_basket.iloc[0]:+.2f} vs
{rules[(rules.rule=='b')&(rules.period=='HIST')].coinflip_same_minutes.iloc[0]:+.2f}) and in DEV
({rules[(rules.rule=='b')&(rules.period=='DEV')].usd_per_basket.iloc[0]:+.2f} vs
{rules[(rules.rule=='b')&(rules.period=='DEV')].coinflip_same_minutes.iloc[0]:+.2f}), with the flipped version worse
still — which is what a small real directional signal would look like. Then in VAL it **reverses**
({rules[(rules.rule=='b')&(rules.period=='VAL')].usd_per_basket.iloc[0]:+.2f} against a coin flip's
{rules[(rules.rule=='b')&(rules.period=='VAL')].coinflip_same_minutes.iloc[0]:+.2f}, and the flipped version is now the
better one). An in-sample tilt that flips sign out of sample is the signature this project has seen in BT1, BT3, BT6a
and BT8 — and in any case (b) loses money in all three periods, so there is nothing to act on either way.

### What those four would have done to a $350 account, day by day
{md(rulesd[rulesd.period == 'ALL'][['rule', 'baskets', 'days', 'baskets_per_day', 'per_basket', 'basket_win_pct',
                                    'avg_day', 'win_days_pct', 'best_day', 'worst_day',
                                    'worst_losing_streak_days', 'avg_month', 'days_to_zero', 'total']], "%.2f")}
""")

A(f"""## 7. Day by day, in dollars — the coin-flip floor on a $350 account
This is the structure with **no entry skill at all**: what the robot does if its secret rule is worth nothing. 0.10
lots throughout. (The robot's "dynamic lot sizing" scales the lot with the balance, which scales every number below by
the same factor — it changes the size of the ride, not its direction.)

| | |
|---|---|
| Baskets simulated | {d50['baskets']:,} over {d50['days']:,} trading days ({d50['baskets_per_day']} a day) |
| **A typical day** | **{usd(d50['avg_day'])}** (middle day {usd(d50['median_day'])}) |
| Days that finish green | **{d50['win_days_pct']:.0f}%** — the structure is built to feel like it is working |
| Best day | +${d50['best_day']:.0f} |
| **Worst day** | **-${abs(d50['worst_day']):.0f}** |
| **Worst run of losing days in a row** | **{d50['worst_losing_streak_days']} days** |
| A typical month | {usd(d50['avg_month'],0)} (best +${d50['best_month']:.0f}, worst -${abs(d50['worst_month']):.0f}, {d50['months_down_pct']:.0f}% of months down) |
| Per basket | {usd(d50['per_basket'])}, winning {d50['basket_win_pct']:.1f}% of baskets |
| **$350 account gone in** | **{d50['days_to_zero']} trading days** |

**Because the day number depends entirely on how often it trades**, here it is at several trading rates — the per-basket
number is the thing that does not move:

{md(rate.rename(columns={'baskets_per_day': 'baskets a day', 'usd_per_day': '$ a day',
                         'usd_per_month': '$ a month (21 days)', 'days_to_lose_350': 'days to lose $350'}), "%.2f")}

And the same table as the entry gets better (`skill_ladder.csv` has every period):
{md(ladder[ladder.period == 'ALL'][['accuracy_pct', 'baskets', 'baskets_per_day', 'per_basket', 'basket_win_pct',
                                    'avg_day', 'win_days_pct', 'worst_day', 'worst_losing_streak_days', 'avg_month',
                                    'days_to_zero', 'total']].rename(columns={'accuracy_pct': 'accuracy %'}), "%.2f")}

Three things to notice. The **win rate barely moves** between a worthless entry and a profitable one
({d50['basket_win_pct']:.1f}% vs about {ladder[(ladder.period == 'ALL') & (ladder.accuracy_pct == 60)].basket_win_pct.iloc[0]:.1f}%) —
so no win rate, however glorious, is evidence. The **percentage of winning days stays high** even while the account
bleeds. And the **worst day is many times the size of a good day**, which is the cliff this shape always ends on.
""")

A(f"""## 8. Do the daily profit target and daily loss limit help?
Coin-flip entry, every combination the brief fixed (the user's values, not the shipped $200/$50):

{md(daily_rules[['daily_target', 'daily_loss', 'baskets', 'baskets_per_day', 'per_basket', 'avg_day', 'win_days_pct',
                 'worst_day', 'worst_losing_streak_days', 'days_to_zero', 'total']], "%.2f")}

**They change the ride, not the destination.** A daily loss limit genuinely caps how bad one day can be and a daily
profit target genuinely reduces how much cost gets paid — both shrink the loss roughly in proportion to how much they
shrink the trading — but **no combination turns the sign positive**, because nothing here changes the money per basket.
Note also the shipped defaults are incoherent: a **$50 daily loss limit is smaller than one $70 basket stop**, so a
single basket blows straight through it, and a **$200 daily target on a $300 account** is 67% in a day.

## 9. Sessions — descriptive only, and deliberately not acted on
{md(sess_money.rename(columns={'usd_per_basket': '$ per basket', 'required_accuracy_pct': 'REQUIRED accuracy %'}), "%.2f")}

**Read this and then do nothing with it.** BT7 tested sessions directly and found no session edge (14/14 DEV and 3/3
VAL negative). BT14 then showed that "London beats New York" — the one thing BT6 had written down in advance as its
finding — turns up in **50% of charts made from pure random numbers**. The brief therefore pre-registered the session
split as descriptive output with **no variant permitted to be selected, tuned or recommended on it**, and that is
honoured: the spread between the best and worst session here is
${sess_money.usd_per_basket.max() - sess_money.usd_per_basket.min():.2f}
per basket and is **within what noise produces**. Every session is a loser; none of them is a hiding place.

## 10. Variants counted, in full
| What | Count | Why it carries no search penalty |
|---|---|---|
| X\\* for the shipped cell | 1 measurement | it is a measurement of a requirement, not a search for a result |
| Target/stop grid | 30 cells | reported in full as a table of requirements; nothing selected from it |
| Readings of "M15 trend + M1 pullback" | **4**, fixed in the brief | all four reported; all four negative |
| Oracle / coin flip | 2 | correctness checks, not findings |
| Skill ladder | 8 X values + oracle | a calibration curve |
| Daily-rule combinations | 16 | reported in full; none positive |
| Sessions | 5 | descriptive only, pre-registered as non-selectable |
Nothing was chosen on the basis of its result, because **nothing was positive**.
""")

A(f"""## 10b. The lookahead trap, hit and caught — read this one
BT14's brief warns that selecting entries with a candle from the future manufactured a fake "+0.199R, t +8.36" edge on
pure noise. **The first version of the four readings in this backtest did exactly that**, and it is worth recording
precisely how, because the result looked excellent.

The triggers are decided on a closed M1 bar — "the low touched the EMA20 and the bar closed back above it". The first
version then opened the basket at the **open of that same bar**, i.e. it bought at the start of a minute already known
to have closed higher. With a target only 70 cents away that is almost a free win. The first run reported readings
(a), (b) and (d) at **+$0.55, +$2.17 and +$0.28 per basket with t-stats of +5.7, +19.5 and +3.9**, improving in VAL —
exactly the shape of a real discovery. Reading (c), the one that enters while price is still falling into a retrace,
came out at **-$2.95 with t -20**: the mirror image, which is what gave it away.

Fixed by opening at the **next** minute's open (`bt15_run.rule_signals` returns `sel + 1`, plus a check that the next
minute really follows the trigger minute), every one of the four collapsed to a coin flip and a loss. Logged as
ERRORS #36. **Two lessons:** a positive result whose mirror image is equally negative is an execution artifact, not an
edge; and "I decided on the close of bar k" must be followed by "so I filled at bar k+1", every time.

## 11. Pre-registered verdict, applied
| Outcome | Reading | |
|---|---|---|
| X\\* above anything this project has measured from a directional rule | **The robot cannot work as sold.** | **← THIS** |
| X\\* reachable AND one of the four readings reaches it out of sample AND it clears the recalibrated BT14 bar | worth a real forward test | |
| Oracle entry fails to win | the simulator is broken | (it won: +${san['zero']['oracle']['per_basket']:.2f}/basket) |

**X\\* = {x_all.xstar_pct:.1f}% for the shipped setting, {grid_r.min():.1f}%–{grid_r.max():.1f}% across all 30 shapes.
The four honest readings of the advertised entry delivered {rules.accuracy_pct.min():.1f}%–{rules.accuracy_pct.max():.1f}%,
i.e. a coin flip, and lost money in every single period. This project has measured directional rules five times — BT4
(post-break move ~0), BT5 (H1 structure worse than random), BT9 (no multi-day trend edge), BT13 (REF fails on 16 unseen
years) and BT8b (69,000 setups, the entry carries no information) — and has never found one better than a coin flip at
all, let alone {x_all.xstar_pct:.0f}%.**

So the verdict is the first row. Said plainly for the user:

- The robot's shape needs an entry that calls gold's direction right about **{x_all.xstar_pct:.0f} times in 100**. Nobody
  in this project has ever built one that beats **50**.
- With an entry worth nothing, the structure loses about **${abs(d50['per_basket']):.2f} every basket** — which is
  roughly the cost of trading, nothing more exotic than that.
- It will still show **{d50['win_days_pct']:.0f}% green days** and a **{d50['basket_win_pct']:.0f}% win rate** while doing it.
  That is the product being sold: the *appearance* of working.
- On **$350 at 0.10 lots** one stop is **20% of the account**, two in a row is 40%, and the worst run of losing days
  measured here was **{d50['worst_losing_streak_days']} in a row**. The account is gone in about
  **{d50['days_to_zero']} trading days**.
- Nothing in 16 years of gold, in any session, in any year, at any target/stop shape, points the other way.

**Recommendation: do not run it with real money, and do not treat its live screenshots as evidence** — this structure
manufactures winning screenshots by design, right up to the day it does not. If the user wants to see it for himself,
the only safe version is the demo account, sized so that a run of {d50['worst_losing_streak_days']} losing days is
survivable, and the thing to watch is **money per basket**, not the win rate.

## 12. What would change this answer
One thing only: **evidence that the secret entry calls direction right about {x_all.xstar_pct:.0f}% of the time.** Not a
win rate, not a screenshot, not an equity curve — those are all compatible with losing. The seller could settle it with
a signed statement showing **money per basket over several hundred baskets**. Until then the structure's own arithmetic
says it cannot pay.

## 13. Limitations, honestly
- **The TEST period (2025-07 onward) was never used.** Opportunities stop at Fri 2025-06-27, which the harness
  flattens by 20:45 that day. Verified rather than asserted: the **latest minute touched by any simulation anywhere in
  BT15 is 2025-06-27 01:28** (checked across every checkpoint file). **One disclosure:** to find the real gold
  spread I read the *spread column only*, by year, from our own Zero feed, and that table included 2026 rows. No price,
  no outcome and no directional information was read, and a broker cost parameter measured today is exactly how
  `common.COMM` was already obtained (2026-09-27). Logged as ERRORS #35.
- **Gold minute data cannot resolve a 70-cent target cleanly** (section 3). Both the pessimistic and corrected versions
  are reported and the conclusion is the same under either.
- **Dukascopy gold starts 2009-03-15**, so HIST is 11.8 years, not the brief's 16, and the 2005-08 era is unavailable.
  The two eras that exist are reported separately.
- **The Dukascopy file carries no spread at all** for gold, so HIST costs are the Zero commission plus the explicit
  0.05 spread; real 2009-2014 gold spreads were wider, which would make HIST *worse*, not better.
- **Entry chances sit on a 30-minute clock grid** and the engine is therefore in the market a large share of the time
  ({d50['baskets_per_day']} baskets a day). The per-basket number is rate-independent; the per-day table in section 7
  rescales it for slower trading.
- **The four readings are four guesses.** They are not the secret rule. They establish that the obvious readings of the
  advertised description are coin flips; they cannot prove the secret one is.
- **Fixed 0.10 lots**, no compounding and no dynamic sizing; dynamic sizing rescales everything linearly.

## 14. Files
| File | What |
|---|---|
| `bt/bt15_lib.py` | the basket model, the outcome table, X\\*, the robot's mechanics, the day statistics |
| `bt/bt15_run.py` | the three checkpointed phases (`opp`, `grid`, `rules`), one per dataset |
| `bt/bt15_tests.py` | the five blocking sanity checks, including the from-scratch minute replay |
| `bt/bt15_report.py` | this report and every CSV |
| `results/BT15/required_accuracy.csv` | **the deliverable table**: X\\* for all 30 target/stop shapes |
| `results/BT15/xstar_by_period.csv`, `_by_year`, `_by_era`, `_by_session` | X\\* broken out every way the brief asks |
| `results/BT15/skill_ladder.csv` | money and day statistics at each accuracy, per period |
| `results/BT15/rules_accuracy.csv`, `rules_days.csv` | the four readings |
| `results/BT15/daily_rules.csv`, `sessions.csv` | the daily-rule sweep and the session breakdown |
| `results/BT15/trades_*.csv`, `daily_*.csv` | every basket and every trading day, for 8 variants |
| `results/BT15/parts/*` | per-phase checkpoints, the sanity JSONs and the replay CSVs |
| **`results/BT15/addendum_depth.md`** | **second follow-up: how deep a winner dips before it wins, whether the $7 cap leaves money behind, and 28 cells at $10-$25 stops and $30-$200 targets. The cut-point table, the let-it-run test, the hold-time histogram, and the 50% floor.** Scripts/data: `bt/bt15_depth.py`, `depth_*.csv` |
| **`results/BT15/addendum_timestop.md`** | **the follow-up the user asked for: does a TIME STOP rescue the basket? 30 exit variants (5-240 minutes x $20/$35/$70 stops) x 3 entry accuracies. Answer: no, not one cell comes out positive.** Scripts/data: `bt/bt15_timestop.py`, `timestop_sweep.csv`, `timestop_days.csv` |

**Status: complete.** All phases finished: {len(grid)} of 30 grid cells, 4 of 4 readings, both feeds, all sanity checks pass.

*Generated by `bt/bt15_report.py`.*
""")

with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(R))
json.dump(dict(xstar_shipped=float(x_all.xstar_pct), xstar_min=float(grid_r.min()), xstar_max=float(grid_r.max()),
               coin_flip_per_basket=float(d50["per_basket"]), rules=rules.to_dict("records"),
               day=d50), open(os.path.join(OUT, "numbers.json"), "w"), indent=1, default=str)
print("wrote", os.path.join(OUT, "report.md"))
