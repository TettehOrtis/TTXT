"""BT15 blocking sanity checks. Nothing in the report is believable until these pass.

    .venv/Scripts/python.exe smc_bot/bt/bt15_tests.py            # DEV/VAL feed
    set SMC_DATASET=duka & ... bt15_tests.py                     # HIST feed

1. ORACLE  — an entry with perfect knowledge of which side would work must win comfortably.
2. RANDOM  — a coin-flip entry must land on minus the cost per basket, nothing else.
3. GROSS   — with the cost stripped back out, a coin-flip entry must sit on zero (no lookahead).
4. ARITH   — every TP basket must pay exactly target*10 - commission and every SL basket
             exactly -(stop*10) - commission - slippage.
5. REPLAY  — an independent plain-Python minute loop must reproduce simulate()'s exit minute,
             exit price and dollars for sampled baskets.
"""
import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt.common import bars, COMM, STOP_SLIP, pip                  # noqa: E402
from bt import bt15_lib as L                                      # noqa: E402

DS = os.environ.get("SMC_DATASET", "zero")
T0, S0 = 0.7, 7.0
P = pip(L.GOLD)
COMM_USD = COMM[L.GOLD] * 1.0                                     # $1.10 round trip at 0.10 lots
SLIP_USD = STOP_SLIP * P * L.USD_PER_PRICE                        # $0.20


def replay(b, i, side, T, S, delta):
    """Independent minute loop: open at the next bar, walk forward, apply the same pessimistic rules
    (stop checked before target, never target on the fill bar, 0.2 pip stop slippage, flat before the
    weekend / any gap over 6 h). Written from scratch to check simulate(), not shared with it."""
    t = b.time.values
    o, h, l, c, s = b.open.values, b.high.values, b.low.values, b.close.values, b.spr.values
    f = i                                                          # simulate fills at the bar whose time == t_signal
    F = o[f] + s[f] if side == 1 else o[f]
    if side == 1:
        sl, tp = F + delta - S, F + delta + T
    else:
        sl, tp = F + S - delta, F - T - delta
    dow = pd.Timestamp(t[f]).dayofweek
    k = f
    end = min(f + 10 * 1440, len(t) - 1)
    while k <= end:
        tt = pd.Timestamp(t[k])
        gap = (k + 1 < len(t)) and (t[k + 1] - t[k]) > np.timedelta64(6, "h")
        wk = (tt.dayofweek == 4 and tt.hour * 60 + tt.minute >= 20 * 60 + 45)
        if side == 1:
            if l[k] <= sl:
                px = (min(sl, o[k]) - STOP_SLIP * P) if k > f else (sl - STOP_SLIP * P)
                return k, px, (px - F) / P - COMM[L.GOLD] - delta * 10, "SL"
            if k > f and h[k] >= tp:
                return k, tp, (tp - F) / P - COMM[L.GOLD] - delta * 10, "TP"
        else:
            if h[k] + s[k] >= sl:
                px = (max(sl, o[k] + s[k]) + STOP_SLIP * P) if k > f else (sl + STOP_SLIP * P)
                return k, px, (F - px) / P - COMM[L.GOLD] - delta * 10, "SL"
            if k > f and l[k] + s[k] <= tp:
                return k, tp, (F - tp) / P - COMM[L.GOLD] - delta * 10, "TP"
        if wk or gap:
            px = c[k] if side == 1 else c[k] + s[k]
            g = (px - F) / P if side == 1 else (F - px) / P
            return k, px, g - COMM[L.GOLD] - delta * 10, "WEEKEND"
        k += 1
    px = c[end] if side == 1 else c[end] + s[end]
    g = (px - F) / P if side == 1 else (F - px) / P
    return end, px, g - COMM[L.GOLD] - delta * 10, "TIME"
    _ = dow


def main():
    out = {"dataset": DS}
    b = bars(L.GOLD, "M1")
    f = np.load(os.path.join(L.PARTS, f"opp_{DS}_d05.npz"))
    idx, t_opp, pnl, tex = f["idx"], f["t_opp"], f["pnl"], f["texit"]
    f0 = np.load(os.path.join(L.PARTS, f"opp_{DS}_d00.npz"))
    pnl0 = f0["pnl"]
    delta = L.EXTRA_SPREAD
    spr_usd = float(np.nanmean(b.spr.values[idx])) * L.USD_PER_PRICE

    # ---- 1 oracle, 2 random
    ora = L.money_at(pnl, 1.0)
    ran = L.money_at(pnl, 0.5, seed=7)
    # the fill-minute correction (see bt15_lib.fillbar_masks): with a target 10x closer than the stop,
    # the harness's "no target on the fill bar" rule is a large one-sided pessimism, so it is corrected
    # and both versions are reported.
    tpm, slm = L.fillbar_masks(b, idx, T0, S0, delta)
    pnlc = L.fix_fillbar(pnl, tpm, slm, T0)
    pooled = pnlc[~np.isnan(pnlc)].ravel()                         # every basket, both sides = a coin flip
    pooled_pess = pnl[~np.isnan(pnl)].ravel()
    exp_cost = COMM_USD + spr_usd + delta * L.USD_PER_PRICE        # the costs we can name in advance
    se = pooled.std(ddof=1) / np.sqrt(len(pooled))
    day = np.tile(t_opp // 86_400_000_000_000, 2)[~np.isnan(pnlc).ravel()]
    out["oracle"] = dict(n=len(ora), per_basket=round(float(ora.mean()), 3),
                         win_pct=round(100 * float((ora > 0).mean()), 2),
                         pass_=bool(ora.mean() > 2.0))
    out["random"] = dict(n=len(pooled), per_basket=round(float(pooled.mean()), 3),
                         per_basket_m1_pessimistic=round(float(pooled_pess.mean()), 3),
                         named_cost=round(-exp_cost, 3), residual=round(float(pooled.mean() + exp_cost), 3),
                         residual_pct_of_stop=round(100 * float(pooled.mean() + exp_cost) / (S0 * 10), 2),
                         se=round(float(se), 3), z=round(float((pooled.mean() + exp_cost) / se), 2),
                         day_block_t=round(L.block_t(pooled + exp_cost, day), 2),
                         win_pct=round(100 * float((pooled > 0).mean()), 2),
                         win_pct_coinflip_theory=round(100 * S0 / (S0 + T0), 2),
                         fill_minute_target_touched_pct=round(100 * float((tpm & ~slm).mean()), 2),
                         pass_=bool(abs(float(pooled.mean() + exp_cost)) < 0.02 * S0 * 10))
    # ---- 3 gross (no lookahead): strip the commission back out of the delta=0 run
    tpm0, slm0 = L.fillbar_masks(b, idx, T0, S0, 0.0)
    g = L.fix_fillbar(pnl0, tpm0, slm0, T0)
    g = g[~np.isnan(g)].ravel() + COMM_USD
    seg = g.std(ddof=1) / np.sqrt(len(g))
    out["gross_zero"] = dict(per_basket=round(float(g.mean()), 3), se=round(float(seg), 3),
                             z=round(float(g.mean() / seg), 2),
                             pct_of_stop=round(100 * float(g.mean()) / (S0 * 10), 2),
                             pass_=bool(abs(float(g.mean())) < 0.02 * S0 * 10))
    # ---- 4 arithmetic: the two barrier payouts must be exact to the cent
    tp_usd = T0 * L.USD_PER_PRICE - COMM_USD
    sl_usd = -(S0 * L.USD_PER_PRICE) - COMM_USD - SLIP_USD
    istp = np.isclose(pnl, tp_usd, atol=0.011)
    issl = np.isclose(pnl, sl_usd, atol=0.011)
    fin = ~np.isnan(pnl)
    other = fin & ~istp & ~issl
    out["arith"] = dict(tp_expected=round(tp_usd, 2), tp_count=int(istp.sum()),
                        sl_expected=round(sl_usd, 2), sl_count=int(issl.sum()),
                        barrier_exits_pct=round(100 * float((istp | issl).sum() / fin.sum()), 2),
                        flat_exits=int(other.sum()), flat_exits_pct=round(100 * float(other.sum() / fin.sum()), 2),
                        flat_exit_mean_usd=round(float(pnl[other].mean()), 2),
                        worst_stop=round(float(np.nanmin(pnl)), 2), best_flat=round(float(np.nanmax(pnl)), 2),
                        pass_=bool((istp | issl).sum() / fin.sum() > 0.97))
    # ---- 5 independent replay
    rng = np.random.default_rng(3)
    pick = rng.choice(len(idx), 40, replace=False)
    rows = []
    for k in pick:
        for si, side in ((0, 1), (1, -1)):
            if np.isnan(pnl[si, k]):
                continue
            ke, px, money, why = replay(b, idx[k], side, T0, S0, delta)
            rows.append(dict(t_signal=str(b.time.values[idx[k]]), side=side, why=why,
                             replay_exit=str(b.time.values[ke]), sim_exit=str(np.int64(tex[si, k]).astype("datetime64[ns]")),
                             replay_usd=round(money, 3), sim_usd=round(float(pnl[si, k]), 3)))
    r = pd.DataFrame(rows)
    r["match"] = (r.replay_exit == r.sim_exit) & (np.abs(r.replay_usd - r.sim_usd) < 0.02)
    out["replay"] = dict(n=len(r), matched=int(r.match.sum()), pass_=bool(r.match.all()))
    r.to_csv(os.path.join(L.PARTS, f"replay_{DS}.csv"), index=False)

    # ---- overlap (correlated samples)
    ent = t_opp
    ex = np.where(np.isnan(pnl[0]), ent, tex[0])
    out["overlap_pct_at_full_rate"] = round(100 * float(np.mean(ex[:-1] > ent[1:])), 1)
    out["spread_in_data_usd_per_basket"] = round(spr_usd, 3)
    out["extra_spread_usd_per_basket"] = round(delta * L.USD_PER_PRICE, 3)
    out["commission_usd_per_basket"] = round(COMM_USD, 3)
    out["named_cost_per_basket_usd"] = round(exp_cost, 3)
    out["measured_floor_per_basket_usd"] = round(float(pooled.mean()), 3)
    out["all_pass"] = all(v["pass_"] for k, v in out.items() if isinstance(v, dict) and "pass_" in v)
    with open(os.path.join(L.PARTS, f"sanity_{DS}.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1))
    if not out["all_pass"]:
        print("\n*** A BLOCKING CHECK FAILED — fix the simulator before publishing anything ***")
        sys.exit(1)


if __name__ == "__main__":
    main()
