"""Full analysis of the Oracle SniperEA trade log (SniperEA_trades.csv). Read-only.

  python tools/oracle_ea_report.py <SniperEA_trades.csv>
The 'why' column holds unquoted commas on OPEN rows, so rows are split by hand (8 fields, rest is 'why').
"""
import sys
import numpy as np
import pandas as pd


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        next(f)
        for ln in f:
            p = ln.rstrip("\n").split(",", 8)
            if len(p) == 9:
                rows.append(p)
    d = pd.DataFrame(rows, columns="time_utc event symbol side legs lot ref_price money why".split())
    d["time_utc"] = pd.to_datetime(d.time_utc, format="%Y.%m.%d %H:%M:%S")
    d["money"] = d.money.astype(float)
    return d


def streak(flags):
    best = cur = 0
    for x in flags:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def grp(b, key):
    r = b.groupby(key).agg(trades=("money", "size"), wins=("money", lambda x: int((x > 0).sum())),
                           net=("money", "sum"), avg=("money", "mean"))
    r["win_pct"] = (100 * r.wins / r.trades).round(1)
    r["net"], r["avg"] = r.net.round(2), r.avg.round(2)
    return r[["trades", "win_pct", "net", "avg"]]


def main(path):
    d = load(path)
    opens = d[d.event == "OPEN"].reset_index(drop=True)
    closes = d[d.event == "CLOSE"].reset_index(drop=True)
    # pair each close with the open right before it
    b = closes.copy()
    ot = []
    oi = 0
    olist = opens.time_utc.tolist()
    for t in b.time_utc:
        while oi + 1 < len(olist) and olist[oi + 1] <= t:
            oi += 1
        ot.append(olist[oi] if olist and olist[oi] <= t else pd.NaT)
    b["opened"] = ot
    b["hold_min"] = (b.time_utc - b.opened).dt.total_seconds() / 60
    p = b.money
    w, l = p[p > 0], p[p < 0]
    bal = p.cumsum()
    dd = np.maximum.accumulate(np.r_[0, bal.values])[1:] - bal.values
    days = max((b.time_utc.max() - b.time_utc.min()).days, 1)
    print("=" * 62)
    print(f"ORACLE SNIPER EA  |  {b.time_utc.min():%Y-%m-%d} to {b.time_utc.max():%Y-%m-%d}  |  {b.symbol.iloc[0]}")
    print("=" * 62)
    print(f"baskets (trades):      {len(b)}   (open rows {len(opens)})   ~{len(b)/days:.1f} a day over {days} days")
    print(f"wins / losses:         {len(w)} / {len(l)}   win rate {100*len(w)/len(b):.1f}%")
    print(f"net profit:            ${p.sum():.2f}")
    print(f"gross profit / loss:   ${w.sum():.2f} / ${l.sum():.2f}")
    print(f"profit factor:         {w.sum()/-l.sum():.2f}")
    print(f"avg win / avg loss:    ${w.mean():.2f} / ${l.mean():.2f}")
    print(f"payoff (win/loss):     {w.mean()/-l.mean():.3f}")
    print(f"expectancy per trade:  ${p.mean():.3f}")
    print(f"largest win / loss:    ${p.max():.2f} / ${p.min():.2f}")
    print(f"max drawdown:          ${dd.max():.2f}")
    print(f"max win / loss streak: {streak(p>0)} / {streak(p<0)}")
    print(f"break-even win rate at this payoff: {100*(-l.mean())/(w.mean()-l.mean()):.1f}%")
    print(f"one loss erases ~{-l.mean()/w.mean():.1f} wins")
    print(f"time in trade (min):   avg {b.hold_min.mean():.1f} / median {b.hold_min.median():.1f} / max {b.hold_min.max():.1f}")
    w_h = b[b.money > 0].hold_min.median()
    l_h = b[b.money < 0].hold_min.median()
    print(f"median hold: wins {w_h:.1f} min, losses {l_h:.1f} min")
    print("\nBY HOW IT CLOSED\n" + grp(b, "why").to_string())
    print("\nBY DIRECTION\n" + grp(b, "side").to_string())
    b["hour"] = b.opened.dt.hour
    print("\nBY HOUR OPENED (UTC)\n" + grp(b, "hour").to_string())
    b["window"] = np.where((b.opened.dt.hour * 60 + b.opened.dt.minute >= 420) &
                           (b.opened.dt.hour * 60 + b.opened.dt.minute < 690), "IN 07:00-11:30 window", "OUTSIDE window")
    print("\nIN vs OUT of the live 07:00-11:30 UTC window\n" + grp(b, "window").to_string())
    print("\nBY WEEKDAY\n" + grp(b.assign(d=b.time_utc.dt.day_name()), "d").to_string())
    print("\nBY DAY\n" + grp(b.assign(d=b.time_utc.dt.strftime("%Y-%m-%d")), "d").to_string())
    b["holdbin"] = pd.cut(b.hold_min, [0, 5, 15, 30, 60, 120, 1e9], labels=["<5m", "5-15m", "15-30m", "30-60m", "1-2h", ">2h"])
    print("\nBY TIME IN TRADE\n" + grp(b, "holdbin").to_string())
    out = path.replace(".csv", "_BASKETS.csv")
    b.to_csv(out, index=False)
    print("\nsaved baskets ->", out)


if __name__ == "__main__":
    main(sys.argv[1])
