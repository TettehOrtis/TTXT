"""Full win/loss analysis of the live sniper bot, from a broker deals CSV (MT5 history_deals_get dump).

  python tools/sniper_live_report.py <deals.csv> [--magic 990077] [--gap 5]

Closing deals are grouped into baskets (deals closing within --gap seconds, same direction).
Money per deal = profit + commission + swap (what the account really moved).
"""
import argparse
import numpy as np
import pandas as pd


def streak(flags):
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def baskets(df, gap):
    out = df[df.entry == 1].sort_values("time").copy()
    out["money"] = out.profit + out.commission + out.swap
    # a closing BUY deal (type 1 = sell) closes a long; side of the basket = long when closing deal is a sell
    out["side"] = np.where(out.type == 1, "BUY", "SELL")
    gid, last_t, last_side, g = [], None, None, 0
    for t, s in zip(out.time, out.side):
        if last_t is None or t - last_t > gap or s != last_side:
            g += 1
        gid.append(g)
        last_t, last_side = t, s
    out["gid"] = gid
    b = out.groupby("gid").agg(closed=("time_utc", "max"), side=("side", "first"), legs=("money", "size"),
                               money=("money", "sum"), lots=("volume", "sum")).reset_index(drop=True)
    b["closed"] = pd.to_datetime(b.closed)
    return b


def block(title, d):
    if not len(d):
        return
    print(f"\n{title}")
    g = d.groupby(level=0) if False else d
    print(g.to_string())


def grp(b, key):
    r = b.groupby(key).agg(trades=("money", "size"), wins=("money", lambda x: int((x > 0).sum())),
                           net=("money", "sum"))
    r["win_pct"] = (100 * r.wins / r.trades).round(1)
    r["net"] = r.net.round(2)
    return r[["trades", "win_pct", "net"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--magic", type=int, default=990077)
    ap.add_argument("--gap", type=int, default=5)
    a = ap.parse_args()
    df = pd.read_csv(a.csv)
    df = df[df.magic == a.magic].copy()
    df["time_utc"] = pd.to_datetime(df.time_utc)
    b = baskets(df, a.gap)
    if not len(b):
        print("no closed baskets for magic", a.magic)
        return
    p = b.money
    w, l = p[p > 0], p[p < 0]
    bal = p.cumsum()
    dd = (np.maximum.accumulate(np.r_[0, bal.values])[1:] - bal.values)
    first, last = b.closed.min(), b.closed.max()
    days = max((last - first).days, 1)
    print("=" * 60)
    print(f"LIVE SNIPER, magic {a.magic}  |  {first:%Y-%m-%d} to {last:%Y-%m-%d}")
    print("=" * 60)
    print(f"baskets (trades):      {len(b)}   (~{len(b) / days:.1f} a day, {b.legs.mean():.1f} legs each)")
    print(f"wins / losses:         {len(w)} / {len(l)}   win rate {100 * len(w) / len(b):.1f}%")
    print(f"net profit:            ${p.sum():.2f}")
    print(f"gross profit / loss:   ${w.sum():.2f} / ${l.sum():.2f}")
    print(f"profit factor:         {w.sum() / -l.sum():.2f}" if len(l) else "profit factor: n/a")
    print(f"avg win / avg loss:    ${w.mean():.2f} / ${l.mean():.2f}")
    print(f"payoff (win/loss):     {w.mean() / -l.mean():.3f}" if len(w) and len(l) else "")
    print(f"expectancy per trade:  ${p.mean():.3f}")
    print(f"largest win / loss:    ${p.max():.2f} / ${p.min():.2f}")
    print(f"max drawdown:          ${dd.max():.2f}")
    print(f"max win / loss streak: {streak(p > 0)} / {streak(p < 0)}")
    be = 100 * (-l.mean()) / (w.mean() + -l.mean()) if len(w) and len(l) else float('nan')
    print(f"win rate needed to break even at this payoff: {be:.1f}%")
    print("\nlosses needed to wipe out one day of wins: "
          f"{(-l.mean() / w.mean()):.1f} wins per 1 loss" if len(w) and len(l) else "")

    b["kind"] = np.where(p >= 5, "TARGET (win)", np.where(p <= -40, "STOP (big loss)", "other"))
    print("\nBY HOW IT CLOSED\n" + grp(b, "kind").to_string())
    print("\nBY DIRECTION\n" + grp(b, "side").to_string())
    print("\nBY HOUR (UTC)\n" + grp(b.assign(h=b.closed.dt.hour), "h").to_string())
    print("\nBY WEEKDAY\n" + grp(b.assign(d=b.closed.dt.day_name()), "d").to_string())
    print("\nBY DAY\n" + grp(b.assign(d=b.closed.dt.strftime("%Y-%m-%d")), "d").to_string())
    b.to_csv(a.csv.replace(".csv", "_BASKETS.csv"), index=False)


if __name__ == "__main__":
    main()
