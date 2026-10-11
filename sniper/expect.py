"""What to expect from the CURRENT .env settings: trades per day, and what they would have paid.

This runs the bot's own entry rule and its own basket maths over real history, with the robot's
throughput rules (one basket at a time, cooldown), so the trade count is the real one rather than the
raw signal count. Change .env, run this again, see what it does to the numbers.

    .venv/Scripts/python.exe -m sniper.expect
    .venv/Scripts/python.exe -m sniper.expect --years 2        # recent history only

It reads the same settings the bot runs on. The money column is what the shape would have paid on past
prices - it is not a forecast, and BT20 is clear that nothing here has an edge.
"""
import os
import sys
import argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sniper.config import Config                                       # noqa: E402
from sniper import strategy as ST                                      # noqa: E402

TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

# Per-symbol contract facts. The whole point of running this on a second symbol is that $7 and $70 are
# amounts of MONEY, not distances - the same $70 stop is $7 of gold price, 70 pips of EURUSD, or a 0.7%
# move in bitcoin. Getting this table wrong produces a confident, completely wrong answer, so symbols
# that are not in it are refused rather than guessed at.
#
#   units        = contract size per 1.0 lot, in units of the QUOTE currency per 1.0 of price
#   comm_per_lot = $ round trip per 1.0 lot on Exness Zero
#   spread_price = typical spread in price units (the feed's spread column is zeroes on this data)
#   data_dir     = which folder holds the M1 parquet
#
# Only symbols quoted in USD are here. A JPY or cross pair needs the P/L converted out of the quote
# currency at the rate of the day, which this tool does not do; adding one without that conversion would
# silently overstate or understate every number.
CONTRACTS = {
    "XAUUSD": dict(units=100.0,     comm_per_lot=11.0, spread_price=0.05,  data_dir="data_zero"),
    "EURUSD": dict(units=100_000.0, comm_per_lot=7.0,  spread_price=2e-5,  data_dir="data_zero"),
    "GBPUSD": dict(units=100_000.0, comm_per_lot=7.0,  spread_price=3e-5,  data_dir="data_zero"),
    "BTCUSD": dict(units=1.0,       comm_per_lot=0.0,  spread_price=12.0,  data_dir="data"),
}
COMM_PER_LOT = CONTRACTS["XAUUSD"]["comm_per_lot"]       # kept: gold default, used by older callers
SPREAD_PRICE = CONTRACTS["XAUUSD"]["spread_price"]


def signals_vectorised(cfg, m1, trend):
    """The same rule as sniper/strategy.py, evaluated on every bar at once."""
    c, o, h, l = m1.close.values, m1.open.values, m1.high.values, m1.low.values
    e = ST.ema(c, cfg.entry_ema)
    r = ST.rsi(c, cfg.entry_rsi_period)

    if cfg.require_trend:
        tf, ts = ST.ema(trend.close.values, cfg.trend_ema_fast), ST.ema(trend.close.values, cfg.trend_ema_slow)
        up_tf = np.where(tf > ts, 1, np.where(tf < ts, -1, 0))
        # a trend bar is only usable once it has CLOSED - map each M1 bar to the last closed trend bar
        k = np.searchsorted(trend.tclose.values.astype("datetime64[ns]"),
                            m1.time.values.astype("datetime64[ns]"), "right") - 1
        trend_at = np.where(k >= 0, up_tf[np.clip(k, 0, len(up_tf) - 1)], 0)
    else:
        trend_at = np.zeros(len(c), np.int8)

    buy = (l <= e) & (r <= cfg.entry_rsi_buy_below) & (c > o)
    sell = (h >= e) & (r >= cfg.entry_rsi_sell_above) & (c < o)
    if cfg.require_trend:
        buy &= trend_at == 1
        sell &= trend_at == -1
    side = np.where(buy, 1, np.where(sell, -1, 0)).astype(np.int8)
    side[: max(cfg.trend_ema_slow * 2, 300)] = 0            # indicator warm-up
    return side


def run(cfg, years=None, window=None, symbol=None, flat_at_end=None):
    """window = (start_hour, end_hour) in UTC, e.g. (7, 12) for 07:00-12:00. None = all day."""
    sym = (symbol or "XAUUSD").upper()
    if sym not in CONTRACTS:
        raise SystemExit(
            f"\n{sym} is not in the contract table, so its money cannot be worked out.\n"
            f"Known: {', '.join(sorted(CONTRACTS))}\n\n"
            f"Add it to CONTRACTS in sniper/expect.py, but only if it is quoted in USD - a JPY or\n"
            f"cross pair needs the profit converted out of the quote currency, which this tool does\n"
            f"not do, and the numbers would be wrong without saying so.\n")
    spec = CONTRACTS[sym]
    if flat_at_end is None:
        flat_at_end = bool(getattr(cfg, "flat_at_window_end", True))
    os.environ.setdefault("SMC_DATASET", "zero")
    os.environ["DATA_DIR"] = spec["data_dir"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "smc_bot"))
    from bt.common import bars                                          # noqa: E402

    m1 = bars(sym, "M1")
    tf = cfg.trend_timeframe
    trend = bars(sym, tf) if tf != "M1" else m1
    if years:
        cut = m1.time.max() - pd.Timedelta(days=365 * years)
        m1 = m1[m1.time >= cut].reset_index(drop=True)
        trend = trend[trend.time >= cut - pd.Timedelta(days=5)].reset_index(drop=True)

    side = signals_vectorised(cfg, m1, trend)
    sig = np.where(side != 0)[0]
    sig = sig[(sig + 1) < len(m1) - 1]
    entry = sig + 1                                          # fill at the NEXT bar's open, like the bot

    days = len(np.unique(m1.time.values.astype("datetime64[D]")))
    months = len(np.unique(m1.time.values.astype("datetime64[M]")))

    # the basket in price terms
    lots = cfg.lot_size * cfg.positions_per_entry
    usd_per_price = lots * spec["units"]                     # gold 100 oz/lot, FX 100k units, BTC 1 coin
    tp_px = cfg.basket_profit_target_usd / usd_per_price
    sl_px = cfg.stop_loss_usd / usd_per_price
    comm = spec["comm_per_lot"] * lots if cfg.include_commission else 0.0
    spread_price = spec["spread_price"]

    hi, lo, op, cl = m1.high.values, m1.low.values, m1.open.values, m1.close.values
    n = len(m1)
    cool_bars = max(1, int(round(cfg.cooldown_seconds / 60.0)))
    HORIZON = 1440

    # The force-flat deadline, per entry bar. Without this, `--window` would only filter the ENTRY hour
    # and then happily hold the trade for 24 hours - so a market whose baskets take 5 hours would be
    # scored on exits the real bot never gets to see. That is not a small distortion: on EURUSD it is the
    # difference between a profit and a loss, because the far-away stop means the trade needs HOURS to
    # resolve and the window simply is not that long.
    deadline_ix = None
    if window and flat_at_end:
        tv = m1.time.values.astype("datetime64[m]")
        day0 = tv.astype("datetime64[D]").astype("datetime64[m]")
        ends = day0 + np.timedelta64(window[1] * 60, "m")
        # first bar at or after this bar's own window end; searchsorted keeps gaps and weekends honest
        deadline_ix = np.searchsorted(tv, ends, side="left")

    taken, wins, pnl, held = 0, 0, [], []
    forced_n, forced_money = 0, 0.0        # baskets the window end closed, and what they paid
    free = -1
    streak = {1: 0, -1: 0}
    cur_day = None
    day_money = 0.0
    blocked = 0
    hours, per_day_counts, day_end = [], {}, {"ran out of signals": 0, "daily loss limit": 0,
                                             "daily profit target": 0}
    hr = pd.DatetimeIndex(m1.time.values).hour.values
    stopped_today = None
    for k in range(len(entry)):
        i, d = int(entry[k]), int(side[sig[k]])
        if i < free or i + 2 >= n:
            continue
        if window and not (window[0] <= hr[i] < window[1]):
            continue
        day = m1.time.values[i].astype("datetime64[D]")
        if day != cur_day:
            if cur_day is not None:
                day_end[stopped_today or "ran out of signals"] += 1
            cur_day, day_money, streak = day, 0.0, {1: 0, -1: 0}
            stopped_today = None
        if cfg.daily_loss_limit_usd > 0 and day_money <= -cfg.daily_loss_limit_usd:
            stopped_today = "daily loss limit"
            continue
        if cfg.daily_profit_target_usd > 0 and day_money >= cfg.daily_profit_target_usd:
            stopped_today = "daily profit target"
            continue
        if streak[d] >= cfg.block_direction_after_sl:
            blocked += 1
            continue

        f = op[i] + (spread_price if d == 1 else -spread_price)
        j1 = min(n, i + HORIZON)
        if d == 1:
            up = np.flatnonzero(hi[i:j1] >= f + tp_px)
            dn = np.flatnonzero(lo[i:j1] <= f - sl_px)
        else:
            up = np.flatnonzero(lo[i:j1] <= f - tp_px)
            dn = np.flatnonzero(hi[i:j1] >= f + sl_px)
        tu = up[0] if len(up) else 10 ** 9
        td = dn[0] if len(dn) else 10 ** 9
        # The window end, if there is one, beats both. It is a TIME stop: the basket closes at whatever
        # price is on the screen, which is usually somewhere between the target and the stop.
        tf_ = 10 ** 9
        if deadline_ix is not None:
            tf_ = max(1, int(deadline_ix[i]) - i)
        if min(tu, td, tf_) == 10 ** 9:
            continue                                          # unresolved inside 24 h: ignore, it is rare
        if tf_ <= min(tu, td):
            k_end = min(n - 1, i + tf_)
            moved = (cl[k_end] - f) * (1 if d == 1 else -1)
            money = moved * usd_per_price - comm
            won, bars_held, forced = money > 0, tf_, True
        else:
            won = tu < td                                     # a tie inside one minute goes to the stop
            money = (cfg.basket_profit_target_usd if won else -cfg.stop_loss_usd) - comm
            bars_held, forced = int(min(tu, td)), False
        forced_n += int(forced)
        forced_money += money if forced else 0.0

        taken += 1
        wins += int(won)
        pnl.append(money)
        held.append(bars_held)
        hours.append(int(hr[i]))
        per_day_counts[str(day)] = per_day_counts.get(str(day), 0) + 1
        day_money += money
        streak[d] = 0 if won else streak[d] + 1
        free = i + bars_held + cool_bars

    if cur_day is not None:
        day_end[stopped_today or "ran out of signals"] += 1
    pnl = np.array(pnl, float)
    held = np.array(held, float)
    counts = np.array(list(per_day_counts.values()), float)
    hrs = np.bincount(np.array(hours, int), minlength=24) if hours else np.zeros(24, int)
    return dict(
        hours=hrs, day_end=day_end, active_days=len(per_day_counts),
        per_day_med=float(np.median(counts)) if len(counts) else 0.0,
        per_day_max=int(counts.max()) if len(counts) else 0,
        span=f"{m1.time.min():%Y-%m-%d} to {m1.time.max():%Y-%m-%d}", days=days, months=months,
        raw_signals=len(sig), raw_per_day=len(sig) / days,
        trades=taken, per_day=taken / days, per_month=taken / months,
        blocked=blocked,
        win_pct=100.0 * wins / max(1, taken),
        med_min=float(np.median(held)) if len(held) else 0.0,
        mean_min=float(held.mean()) if len(held) else 0.0,
        per_basket=float(pnl.mean()) if len(pnl) else 0.0,
        per_day_usd=float(pnl.sum()) / days, per_month_usd=float(pnl.sum()) / months,
        total=float(pnl.sum()), lots=lots, comm=comm, symbol=sym,
        forced=forced_n, forced_money=forced_money)


def main(argv=None):
    p = argparse.ArgumentParser(description="What to expect from the current .env settings")
    p.add_argument("--years", type=float, default=None, help="only the last N years of history")
    p.add_argument("--window", default=None, metavar="HH-HH",
                   help="only trade between these UTC hours, e.g. 7-12")
    p.add_argument("--symbol", default=None,
                   help=f"which market to measure (default XAUUSD): {', '.join(sorted(CONTRACTS))}")
    p.add_argument("--all-day", action="store_true",
                   help="ignore the window in .env entirely, to compare against trading round the clock")
    a = p.parse_args(argv)
    cfg = Config.load()
    win = None
    if a.window:
        lo, hi = (int(x) for x in a.window.split("-"))
        win = (lo, hi)
    # The window lives in .env now, so --window is only needed to try a DIFFERENT one than the bot runs.
    if win is None and not a.all_day and getattr(cfg, "window", None):
        win = (cfg.window[0] // 60, cfg.window[1] // 60)
    if a.all_day:
        win = None
    r = run(cfg, a.years, win, a.symbol)

    print(f"\nSYMBOL     {r['symbol']}" +
          ("   |   all day" if win is None else f"   |   window {win[0]:02d}:00-{win[1]:02d}:00 UTC"))
    print(f"SETTINGS   {cfg.positions_per_entry} x {cfg.lot_size} lots = {r['lots']:.2f} total | "
          f"target +${cfg.basket_profit_target_usd:.0f} | stop -${cfg.stop_loss_usd:.0f} | "
          f"cooldown {cfg.cooldown_seconds}s")
    print(f"           {cfg.trend_timeframe} trend / {cfg.entry_timeframe} entry | "
          f"daily target ${cfg.daily_profit_target_usd:.0f} | daily loss ${cfg.daily_loss_limit_usd:.0f}")
    print(f"HISTORY    {r['span']}  ({r['days']:,} trading days)\n")

    print(f"  The rule fires                 {r['raw_per_day']:>8.0f} times a day")
    print(f"  But one basket at a time means {r['per_day']:>8.1f} TRADES A DAY")
    print(f"                                 {r['per_month']:>8.0f} a month")
    print(f"                                 {r['trades']:>8,} over the whole span")
    if r["blocked"]:
        print(f"  (a further {r['blocked']:,} were skipped by the 'block direction after "
              f"{cfg.block_direction_after_sl} losses' rule)")
    print()
    print(f"  Win rate                       {r['win_pct']:>8.1f}%")
    print(f"  A trade lasts, typically       {r['med_min']:>8.0f} min   (average {r['mean_min']:.0f})")
    print()
    if r["trades"] and win is not None:
        pct = 100.0 * r["forced"] / r["trades"]
        print(f"  Cut off by the window end      {r['forced']:>8,}   ({pct:.0f}% of trades"
              + (f", {r['forced_money'] / r['forced']:+.2f} each)" if r["forced"] else ")"))
        if pct >= 25:
            print(f"  {'':>31}^^ a quarter or more of the trades never finish on their own.")
            print(f"  {'':>31}   This market needs longer than the window allows.")
    print(f"  Money per basket               {r['per_basket']:>+8.2f}   (after ${r['comm']:.2f} commission)")
    print(f"  Per day                        {r['per_day_usd']:>+8.2f}")
    print(f"  Per month                      {r['per_month_usd']:>+8.2f}")
    print(f"  On a day it trades at all:     {r['per_day_med']:>8.0f} trades (busiest day {r['per_day_max']})")
    print()
    print("  WHAT ENDS THE DAY")
    tot_d = sum(r["day_end"].values()) or 1
    for k, v in sorted(r["day_end"].items(), key=lambda x: -x[1]):
        print(f"    {k:24} {v:>6,} days  ({100*v/tot_d:>4.0f}%)")
    print()
    print("  WHEN IT TRADES (UTC hour, each block = share of all trades)")
    h = r["hours"]
    mx = max(1, h.max())
    for i in range(0, 24, 1):
        bar = "#" * int(round(28 * h[i] / mx))
        print(f"    {i:02d}:00 {bar:<28} {100*h[i]/max(1,h.sum()):>4.1f}%")
    print(f"\n  Not a forecast - this is what these settings would have done on past prices.")
    print(f"  See BT20: no entry/exit combination tested was profitable across all eras.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
