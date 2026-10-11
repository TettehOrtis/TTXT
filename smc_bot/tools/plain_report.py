"""Turn any backtest trades CSV (common.simulate format) into a plain-language report a trader can read.

  .venv\\Scripts\\python.exe smc_bot\\tools\\plain_report.py <trades.csv> [--name "..."] [--balance 100] [--lot 0.01] [--period DEV]

Writes next to the input file:
  <stem>_READABLE.csv : one row per trade, plain column names (see smc_bot/GLOSSARY.md)
  <stem>_SUMMARY.json : totals + breakdowns (exit reason, direction, pair, session, hour, weekday, month/year)
  <stem>_SUMMARY.md   : the same summary as readable tables
Money (default, like the live bot): FIXED RISK per trade, e.g. $1 on a $100 balance (1%): profit = R x risk.
       --risk 0 switches to a FIXED lot (--lot, default 0.01) from the file's net pips. Both are after all costs.
"""
import argparse, json, os
import numpy as np
import pandas as pd

HOW = {"TP": "Take profit hit", "SL": "Stop loss hit", "GAP_SL": "Stop loss hit (price gapped through)",
       "WEEKEND": "Closed before weekend/holiday", "TIME": "Closed on time limit", "END": "Closed at end of test period",
       "BE": "Closed at breakeven"}
ZONE = {"OB": "Order Block", "FVG": "Fair Value Gap"}
SESSION = {"LDN": "London", "NY": "New York"}


def readable(t, lot, balance, risk=1.0):
    t = t[t.status == "filled"].copy() if "status" in t else t.copy()
    t = t.sort_values("t_exit").reset_index(drop=True)
    usd_lot = t.usd_01.astype(float) * (lot / 0.10)
    usd = t.R.astype(float) * risk if risk > 0 else usd_lot
    out = pd.DataFrame({
        "trade_no": np.arange(1, len(t) + 1),
        "pair": t.sym,
        "direction": np.where(t.side > 0, "BUY", "SELL"),
        "setup_zone": t.zone_kind.map(ZONE).fillna(t.zone_kind) if "zone_kind" in t else "",
        "session": t.session.map(SESSION).fillna(t.session) if "session" in t else "",
        "order_placed_utc": t.t_signal,
        "entry_time_utc": t.t_fill,
        "entry_price": t.fill,
        "stop_loss": t.sl.round(5),
        "take_profit": t.tp.round(5),
        "reward_to_risk": ("1:" + t.rr.round(1).astype(str)) if "rr" in t else "",
        "exit_time_utc": t.t_exit,
        "exit_price": t.exit.round(5),
        "how_it_closed": t.exit_reason.map(HOW).fillna(t.exit_reason),
        "result": np.where(usd > 0.005, "WIN", np.where(usd < -0.005, "LOSS", "BREAKEVEN")),
        "minutes_in_trade": ((pd.to_datetime(t.t_exit) - pd.to_datetime(t.t_fill)).dt.total_seconds() / 60).round(0),
        "stop_distance_pips": t.risk_pips.round(1),
        "pips_after_costs": t.net_pips.round(1),
        "profit_usd": usd.round(2),
        "balance_after": (balance + usd.cumsum()).round(2),
        "result_in_risks_R": t.R.round(2),
        f"profit_usd_if_fixed_{lot}_lot": usd_lot.round(2),
    })
    if "mfe_R" in t:
        out["best_open_profit_R"] = t.mfe_R.round(2)          # how far it went in our favour before closing
        out["worst_open_loss_R"] = (-t.mae_R).round(2)        # how far it went against us before closing
    return out


def group(r, key):
    g = {}
    for k, d in r.groupby(key, sort=True):
        g[str(k)] = dict(trades=int(len(d)), win_rate_pct=round(100 * (d.result == "WIN").mean(), 1),
                         net_profit=round(float(d.profit_usd.sum()), 2),
                         pct_of_total_trades=round(100 * len(d) / len(r), 1))
    return g


def streak(x):
    best = cur = 0
    for v in x:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def summary(r, name, balance, lot, risk=1.0):
    p = r.profit_usd
    wins, losses = p[p > 0.005], p[p < -0.005]
    bal = balance + p.cumsum()
    peak = np.maximum.accumulate(np.r_[balance, bal.values])[1:]
    dd = peak - bal.values
    i = int(np.argmax(dd)) if len(dd) else 0
    t_in = pd.to_datetime(r.entry_time_utc)
    weeks = max((pd.to_datetime(r.exit_time_utc).max() - t_in.min()).days / 7, 1)
    months = r.entry_time_utc.astype(str).str[:7]
    s = {
        "name": name,
        "symbols": sorted(r.pair.unique().tolist()),
        "period": f"{str(t_in.min())[:10]} to {str(pd.to_datetime(r.exit_time_utc).max())[:10]}",
        "sizing": f"fixed risk ${risk:g} per trade ({100 * risk / balance:.1f}% of starting balance)" if risk > 0 else f"fixed lot {lot}",
        "total_trades": int(len(r)),
        "trades_per_week": round(len(r) / weeks, 2),
        "wins": int(len(wins)), "losses": int(len(losses)), "breakeven": int(len(r) - len(wins) - len(losses)),
        "win_rate_pct": round(100 * len(wins) / max(len(r), 1), 1),
        "net_profit": round(float(p.sum()), 2),
        "gross_profit": round(float(wins.sum()), 2),
        "gross_loss": round(float(-losses.sum()), 2),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) else None,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0,
        "payoff_ratio": round(float(wins.mean() / -losses.mean()), 3) if len(wins) and len(losses) else None,
        "expectancy_per_trade": round(float(p.mean()), 3),
        "avg_result_in_risks_R": round(float(r.result_in_risks_R.mean()), 3),
        "largest_win": round(float(p.max()), 2), "largest_loss": round(float(p.min()), 2),
        "starting_balance": balance, "final_balance": round(float(bal.iloc[-1]), 2) if len(bal) else balance,
        "max_drawdown_dollars": round(float(dd.max()), 2) if len(dd) else 0,
        "max_drawdown_pct": round(float(100 * dd[i] / peak[i]), 1) if len(dd) else 0,
        "max_consecutive_wins": streak(r.result == "WIN"),
        "max_consecutive_losses": streak(r.result == "LOSS"),
        "duration_stats": {"avg_minutes": round(float(r.minutes_in_trade.mean()), 1),
                           "median_minutes": float(r.minutes_in_trade.median()),
                           "min_minutes": float(r.minutes_in_trade.min()),
                           "max_minutes": float(r.minutes_in_trade.max())},
        "has_cost_model": True,
        "cost_model": "spread from data + real Zero commission + 0.2 pip stop slippage",
        "by_exit_reason": group(r, "how_it_closed"),
        "by_direction": group(r, "direction"),
        "by_pair": group(r, "pair"),
        "by_session": group(r, "session"),
        "by_hour_of_day_utc": group(r.assign(h=pd.to_datetime(r.entry_time_utc).dt.hour), "h"),
        "by_weekday": group(r.assign(d=pd.to_datetime(r.entry_time_utc).dt.day_name()), "d"),
    }
    if months.nunique() <= 36:
        s["by_month"] = group(r.assign(m=months), "m")
    s["by_year"] = group(r.assign(y=r.entry_time_utc.astype(str).str[:4]), "y")
    return s


def md(s):
    L = [f"# {s['name']}", "", f"{', '.join(s['symbols'])} · {s['period']} · {s['sizing']} · costs: {s['cost_model']}", "",
         "| | |", "|---|---|"]
    keys = ["total_trades", "trades_per_week", "wins", "losses", "win_rate_pct", "net_profit", "gross_profit", "gross_loss",
            "profit_factor", "avg_win", "avg_loss", "payoff_ratio", "expectancy_per_trade", "avg_result_in_risks_R",
            "largest_win", "largest_loss", "starting_balance", "final_balance", "max_drawdown_dollars", "max_drawdown_pct",
            "max_consecutive_wins", "max_consecutive_losses"]
    L += [f"| {k.replace('_', ' ')} | {s[k]} |" for k in keys]
    d = s["duration_stats"]
    L += [f"| time in trade (avg / median / max, minutes) | {d['avg_minutes']} / {d['median_minutes']} / {d['max_minutes']} |", ""]
    for sec in ["by_exit_reason", "by_direction", "by_pair", "by_session", "by_year", "by_month", "by_weekday", "by_hour_of_day_utc"]:
        if sec not in s:
            continue
        L += [f"## {sec.replace('_', ' ')}", "", "| | trades | win rate % | net profit $ | % of trades |", "|---|---|---|---|---|"]
        items = s[sec].items()
        if sec == "by_hour_of_day_utc":
            items = sorted(items, key=lambda kv: int(kv[0]))
        L += [f"| {k} | {v['trades']} | {v['win_rate_pct']} | {v['net_profit']} | {v['pct_of_total_trades']} |" for k, v in items]
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--name", default="")
    ap.add_argument("--balance", type=float, default=100.0)
    ap.add_argument("--lot", type=float, default=0.01)
    ap.add_argument("--risk", type=float, default=1.0, help="$ risked per trade; 0 = use fixed --lot instead")
    ap.add_argument("--period", default="")
    ap.add_argument("--pairs", default="", help="comma list to keep, e.g. the 9 traded pairs")
    a = ap.parse_args()
    t = pd.read_csv(a.csv)
    if a.period and "period" in t:
        t = t[t.period == a.period]
    if a.pairs:
        t = t[t.sym.isin(a.pairs.split(","))]
    if "excluded_cov" in t:
        t = t[~t.excluded_cov.astype(bool)]
    r = readable(t, a.lot, a.balance, a.risk)
    stem = os.path.splitext(a.csv)[0] + (f"_{a.period}" if a.period else "")
    r.to_csv(stem + "_READABLE.csv", index=False)
    s = summary(r, a.name or os.path.basename(stem), a.balance, a.lot, a.risk)
    with open(stem + "_SUMMARY.json", "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    with open(stem + "_SUMMARY.md", "w", encoding="utf-8") as f:
        f.write(md(s))
    print(json.dumps({k: s[k] for k in ["total_trades", "wins", "losses", "win_rate_pct", "net_profit", "profit_factor",
                                        "final_balance", "max_drawdown_pct"]}))
