"""The entry rule: trend on the higher timeframe, retracement on the lower one.

HONEST LABEL: the vendor does NOT publish their rule. Their page says only "identifies current trend
direction using built-in indicators" and "waits for retracement meeting defined entry criteria". This is
our reconstruction of that description, not their code, and it will not fire on exactly the same minutes.
Every threshold is configurable from .env so it can be tuned to match whatever their EA actually does.

Research context, so nobody mistakes this for an edge: 20 backtests over 2009-2025 found no entry rule on
gold that beats a coin flip after costs, this family included. The bot is built to be correct and safe,
not because the rule is expected to make money.
"""
import numpy as np

BUY, SELL, NONE = 1, -1, 0


def ema(x, n):
    x = np.asarray(x, float)
    a = 2.0 / (n + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def rsi(close, n=14):
    c = np.asarray(close, float)
    d = np.diff(c, prepend=c[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    au, ad = np.empty_like(c), np.empty_like(c)
    au[0] = up[:n].mean() if len(up) >= n else up[0]
    ad[0] = dn[:n].mean() if len(dn) >= n else dn[0]
    for i in range(1, len(c)):                      # Wilder smoothing
        au[i] = (au[i - 1] * (n - 1) + up[i]) / n
        ad[i] = (ad[i - 1] * (n - 1) + dn[i]) / n
    rs = np.where(ad > 0, au / np.where(ad == 0, 1e-12, ad), np.inf)
    return 100.0 - 100.0 / (1.0 + rs)


def trend_of(cfg, trend_bars):
    """+1 up, -1 down, 0 undecided - from the fast/slow EMA on the trend timeframe."""
    c = trend_bars["close"]
    f, s = ema(c, cfg.trend_ema_fast), ema(c, cfg.trend_ema_slow)
    if f[-1] > s[-1]:
        return BUY
    if f[-1] < s[-1]:
        return SELL
    return NONE


def entry_signal(cfg, trend_bars, entry_bars):
    """The side to trade on the latest CLOSED entry bar, or NONE.

    A BUY needs all of: the trend is up; price pulled back to or through the entry EMA; the pullback was
    deep enough on RSI; and the bar closed back up again, i.e. the retracement is finishing rather than
    still falling. SELL is the mirror.

    ENTRY_REQUIRE_BODY_HOLD adds one more: the body must close back on the trend's side of the EMA, so
    the wick pierced the EMA and the close recovered above it. Beware - that condition and a low RSI
    threshold are very nearly mutually exclusive, because a bar that closed up and above the EMA has
    just been pushed up and so cannot have a low RSI. Config.__post_init__ warns when both are set.
    """
    t = trend_of(cfg, trend_bars) if cfg.require_trend else NONE
    c, o, h, l = (entry_bars["close"], entry_bars["open"], entry_bars["high"], entry_bars["low"])
    e = ema(c, cfg.entry_ema)
    r = rsi(c, cfg.entry_rsi_period)
    resumed_up = c[-1] > o[-1]
    resumed_dn = c[-1] < o[-1]
    hold = getattr(cfg, "entry_require_body_hold", False)
    body_up = (not hold) or c[-1] > e[-1]        # body closed back ABOVE the EMA
    body_dn = (not hold) or c[-1] < e[-1]        # body closed back BELOW the EMA
    held = ", EMA held" if hold else ""

    if cfg.require_trend:
        if t == BUY and l[-1] <= e[-1] and r[-1] <= cfg.entry_rsi_buy_below and resumed_up and body_up:
            return BUY, f"trend up, pullback to EMA{cfg.entry_ema}{held}, RSI {r[-1]:.1f}"
        if t == SELL and h[-1] >= e[-1] and r[-1] >= cfg.entry_rsi_sell_above and resumed_dn and body_dn:
            return SELL, f"trend down, pullback to EMA{cfg.entry_ema}{held}, RSI {r[-1]:.1f}"
        return NONE, ""
    # trend filter off: pure mean reversion on the entry timeframe
    if l[-1] <= e[-1] and r[-1] <= cfg.entry_rsi_buy_below and resumed_up and body_up:
        return BUY, f"no-trend mode{held}, RSI {r[-1]:.1f}"
    if h[-1] >= e[-1] and r[-1] >= cfg.entry_rsi_sell_above and resumed_dn and body_dn:
        return SELL, f"no-trend mode{held}, RSI {r[-1]:.1f}"
    return NONE, ""


def warmup_bars(cfg):
    """How many bars each timeframe needs before the indicators are trustworthy."""
    return (max(cfg.trend_ema_slow * 3, 300), max(cfg.entry_ema * 5, cfg.entry_rsi_period * 10, 200))
