"""Sniper Spark clone - configuration, read from .env.

Every setting from the vendor's Inputs tab lives in the project's .env next to the MT5 credentials, so
there is one file to edit. Each key is prefixed SNIPER_ and each has a safe default here, so .env only
needs the lines you actually want to change.

    from sniper.config import Config
    cfg = Config.load()                       # reads .env
    cfg = Config.load(symbol="BTCUSD")        # .env, then this override (what the CLI flags use)

Account independence: SNIPER_SYMBOL is written WITHOUT the broker suffix ("XAUUSD", "BTCUSD"). The broker
layer resolves it against whatever the logged-in account actually offers - XAUUSDz on an Exness Zero
account, XAUUSDm on Standard - so the same .env works on either without editing.
"""
import os
import re
from dataclasses import dataclass, asdict, fields

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENV_PATH = os.path.join(ROOT, ".env")
PREFIX = "SNIPER_"
TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}


def _coerce(raw, typ, key):
    s = raw.strip()
    # python-dotenv strips an inline "# comment" only when there is a value in front of it. On an EMPTY
    # setting the comment becomes the value, so "KEY=    # fill this in" would arrive as the help text.
    if s.startswith("#"):
        s = ""
    try:
        if typ is bool:
            low = s.lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
            raise ValueError(f"expected true/false, got {s!r}")
        if typ is int:
            return int(float(s))          # tolerate "5.0" for an int setting
        if typ is float:
            return float(s)
        return s
    except ValueError as e:
        raise ValueError(f"{PREFIX}{key.upper()} in .env: {e}") from None


@dataclass
class Config:
    # ---- what to trade -------------------------------------------------------------------
    symbol: str = "XAUUSD"              # no broker suffix; resolved per account
    account: str = "ZERO"               # which .env credential block: MT5 | ZERO | Z
    run_mode: str = "MODE_DEMO"         # MODE_DEMO refuses to attach to a real account. MODE_LIVE allows it.

    # ---- the vendor's Inputs tab ---------------------------------------------------------
    lot_size: float = 0.02
    positions_per_entry: int = 5
    basket_profit_target_usd: float = 7.0
    stop_loss_usd: float = 70.0
    cooldown_seconds: int = 60
    trend_timeframe: str = "M15"
    entry_timeframe: str = "M1"
    reward_multiplier: float = 2.0      # per-position broker-side TP distance = multiplier x stop distance
    block_direction_after_sl: int = 2   # consecutive losing baskets before that direction pauses for the day
    daily_profit_target_usd: float = 200.0   # 0 = off
    daily_loss_limit_usd: float = 50.0       # 0 = off

    # ---- the entry rule (the vendor does not disclose theirs; this is our reconstruction) --
    trend_ema_fast: int = 50
    trend_ema_slow: int = 200
    entry_ema: int = 20                 # price must pull back to this EMA on the entry timeframe
    entry_rsi_period: int = 14
    entry_rsi_buy_below: float = 45.0   # pullback depth required for a BUY in an uptrend
    entry_rsi_sell_above: float = 55.0
    require_trend: bool = True          # False = take both directions, trend filter off
    # Which side of the entry EMA the BODY must close on. The wick always has to touch the EMA.
    #   False (default, as shipped): no condition on the close - in practice almost every signal is a
    #          bar whose body closed THROUGH the EMA, because of the RSI clash described below.
    #   True:  the body must close back on the trend's side of the EMA - price wicked into the EMA and
    #          bounced, the EMA holding as support. Measured 2026-10-06: this is a DIFFERENT set of
    #          bars from the ones the shipped rule takes, not a subset of them.
    entry_require_body_hold: bool = False

    # ---- basket shape --------------------------------------------------------------------
    # Every leg opens at market at the same price with the same stop, so the basket is arithmetically
    # one trade of the combined size. That is the vendor's confirmed behaviour; there is no laddering.
    hard_sl_points: float = 0.0         # 0 = derive the disaster stop from stop_loss_usd x 1.5
    include_commission: bool = True     # count commission in the basket's floating money (raw-spread accounts)

    # ---- telegram (optional; leave the token blank to run without it) ---------------------
    telegram_token: str = ""            # from @BotFather. Use a bot of its OWN - two bots cannot share one
    telegram_chat_id: str = ""          # only this chat may send commands
    telegram_alerts: bool = True        # trade open/close alerts. False = commands only, no chatter

    # ---- safety and plumbing -------------------------------------------------------------
    magic: int = 990077
    poll_seconds: float = 1.0
    max_slippage_points: int = 200      # gold's spread alone is ~40 points; too tight here means rejections
    max_spread_points: float = 0.0      # 0 = off; skip entries when the spread is wider than this
    flat_before_weekend: bool = True    # close everything Friday 20:45 UTC
    weekend_flat_utc: str = "FRI 20:45"

    # ---- the trading window (V2) ---------------------------------------------------------
    # The laptop cannot stay on 24/7, so the bot must finish its day on purpose rather than be
    # abandoned mid-basket when the lid closes. Blank = all day, which is how V1 behaved.
    #   before start          idle
    #   start -> end-winddown normal trading
    #   that -> end           manage what is open, open nothing new
    #   end                   close everything, whatever the P/L
    # Ghana is GMT+0, so these UTC hours are also clock hours here.
    trading_hours: str = ""             # "07:00-12:00". Blank = all day
    winddown_minutes: int = 30          # stop opening new baskets this long before the end
    flat_at_window_end: bool = True     # close everything at the end. Leave ON for laptop use
    reconnect_backoff_s: float = 5.0
    reconnect_max_s: float = 120.0
    state_file: str = "logs/sniper/state.json"
    log_dir: str = "logs/sniper"

    def __post_init__(self):
        if self.trend_timeframe not in TIMEFRAMES:
            raise ValueError(f"SNIPER_TREND_TIMEFRAME must be one of {TIMEFRAMES}")
        if self.entry_timeframe not in TIMEFRAMES:
            raise ValueError(f"SNIPER_ENTRY_TIMEFRAME must be one of {TIMEFRAMES}")
        if self.run_mode not in ("MODE_DEMO", "MODE_LIVE"):
            raise ValueError("SNIPER_RUN_MODE must be MODE_DEMO or MODE_LIVE")
        if self.positions_per_entry < 1:
            raise ValueError("SNIPER_POSITIONS_PER_ENTRY must be at least 1")
        if self.lot_size <= 0:
            raise ValueError("SNIPER_LOT_SIZE must be positive")
        if self.basket_profit_target_usd <= 0:
            raise ValueError("SNIPER_BASKET_PROFIT_TARGET_USD must be positive")
        if self.stop_loss_usd <= 0:
            raise ValueError("SNIPER_STOP_LOSS_USD must be positive")
        if self.account.upper() not in ("MT5", "ZERO", "Z"):
            raise ValueError("SNIPER_ACCOUNT must be MT5, ZERO or Z")
        self.account = self.account.upper()
        # window = (start, end) in minutes past UTC midnight, or None for all day. Parsed once here so
        # a typo is a startup error rather than a bot that silently never trades.
        self.window = self._parse_window(self.trading_hours)
        if self.window and self.winddown_minutes < 0:
            raise ValueError("SNIPER_WINDDOWN_MINUTES cannot be negative")
        if self.window and self.winddown_minutes >= self._window_length():
            raise ValueError(
                f"SNIPER_WINDDOWN_MINUTES={self.winddown_minutes} is as long as the whole "
                f"{self.trading_hours} window ({self._window_length()} min), so no basket could ever "
                f"open. Lower it.")
        # A Telegram token is "<digits>:<secret>". Pasting only the half after the colon is an easy
        # mistake and otherwise shows up as a silent 404 loop long after startup.
        # A Telegram token is "<digits>:<secret>". Pasting only the half after the colon is an easy
        # mistake that otherwise shows up as a silent 404 loop. Warn loudly and switch Telegram OFF -
        # never refuse to start, because a chat problem must never stop the bot from trading.
        self.warnings = []
        t = self.telegram_token.strip()
        if t and not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", t):
            self.warnings.append(
                "SNIPER_TELEGRAM_TOKEN does not look like a Telegram token - Telegram is DISABLED. "
                "It must be the WHOLE token from @BotFather, both halves joined by a colon, like "
                f"8796893471:AAH... (yours has {'no colon' if ':' not in t else 'the wrong shape'}, "
                f"{len(t)} characters). Trading is unaffected.")
            t = ""
        self.telegram_token = t
        self.telegram_chat_id = str(self.telegram_chat_id).strip()
        if t and not self.telegram_chat_id:
            self.warnings.append("SNIPER_TELEGRAM_CHAT_ID is empty - Telegram is DISABLED.")

        # The RSI clash. A bar that closes UP and back ABOVE the entry EMA has just been pushed up, so
        # its RSI cannot be low: measured over 2021-2026 on gold M1, the 10th percentile of RSI14 on
        # such bars is 49.5. So ENTRY_REQUIRE_BODY_HOLD and a buy threshold below ~50 are very nearly
        # mutually exclusive - 107,101 matching bars in five years, 19 of which cleared RSI<=45. Warn
        # rather than refuse: the combination is legal, it just fires almost never, and silently taking
        # no trades is the worst possible failure.
        if self.entry_require_body_hold and self.entry_rsi_buy_below < 50.0:
            self.warnings.append(
                f"SNIPER_ENTRY_REQUIRE_BODY_HOLD=True needs the body to close back above the EMA, but "
                f"SNIPER_ENTRY_RSI_BUY_BELOW={self.entry_rsi_buy_below:g} demands a deep pullback. Those "
                f"two almost never happen on the same bar (RSI on such bars is ~49.5 at the 10th "
                f"percentile). Expect almost no BUY signals. Raise the RSI thresholds to ~60/40, or turn "
                f"them off with 100/0, to actually trade this pattern.")
        if self.entry_require_body_hold and self.entry_rsi_sell_above > 50.0:
            self.warnings.append(
                f"SNIPER_ENTRY_REQUIRE_BODY_HOLD=True with SNIPER_ENTRY_RSI_SELL_ABOVE="
                f"{self.entry_rsi_sell_above:g} has the same clash on the SELL side. Expect almost no "
                f"SELL signals.")

        # A daily loss limit smaller than one basket's stop can never do its job: the first loss of the
        # day blows straight through it, so the limit is really "one basket a day" wearing a disguise.
        if 0 < self.daily_loss_limit_usd < self.stop_loss_usd:
            self.warnings.append(
                f"SNIPER_DAILY_LOSS_LIMIT_USD=${self.daily_loss_limit_usd:g} is SMALLER than one "
                f"basket's stop of ${self.stop_loss_usd:g}. A single losing basket ends the day, so the "
                f"limit never actually limits anything. Raise it above ${self.stop_loss_usd:g}.")

        # A window with no force-flat is the exact hazard the window was built to remove.
        if self.window and not self.flat_at_window_end:
            self.warnings.append(
                f"SNIPER_TRADING_HOURS={self.trading_hours} is set but SNIPER_FLAT_AT_WINDOW_END is "
                f"False, so a basket still open at {self.trading_hours.split('-')[1]} stays open with "
                f"nothing managing it. Only safe if the machine stays on.")

    @staticmethod
    def _parse_window(raw):
        """"HH:MM-HH:MM" -> (start_min, end_min) past UTC midnight. "" or None -> None (all day).

        Overnight windows ("22:00-02:00") are refused rather than wrapped. The whole point of the window
        is that the laptop can be shut when it ends, so a window spanning midnight would defeat it, and
        quietly wrapping one would be a nasty surprise.
        """
        s = (raw or "").strip()
        if not s:
            return None
        m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", s)
        if not m:
            raise ValueError(
                f"SNIPER_TRADING_HOURS must look like 07:00-12:00 (24-hour UTC), got {raw!r}. "
                f"Leave it blank to trade all day.")
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        for h, mi in ((h1, m1), (h2, m2)):
            if h > 23 or mi > 59:
                raise ValueError(f"SNIPER_TRADING_HOURS has an impossible time in {raw!r}")
        a, b = h1 * 60 + m1, h2 * 60 + m2
        if b <= a:
            raise ValueError(
                f"SNIPER_TRADING_HOURS {raw!r} ends at or before it starts. Windows that cross midnight "
                f"are not supported - the window exists so the laptop can be shut at the end of it.")
        return (a, b)

    def _window_length(self):
        return self.window[1] - self.window[0] if self.window else 24 * 60

    @classmethod
    def load(cls, env_path=ENV_PATH, **overrides):
        """Defaults, then SNIPER_* keys from .env, then explicit overrides (the CLI flags)."""
        load_dotenv(env_path, override=False)
        vals = {}
        for f in fields(cls):
            raw = os.environ.get(PREFIX + f.name.upper())
            if raw is not None and raw.strip() != "":
                vals[f.name] = _coerce(raw, f.type if isinstance(f.type, type) else type(f.default), f.name)
        vals.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**vals)

    def as_dict(self):
        return asdict(self)

    def env_block(self):
        """The full .env block for these settings - what `python -m sniper.run --print-env` prints."""
        out = []
        for f in fields(self):
            out.append(f"{PREFIX}{f.name.upper()}={getattr(self, f.name)}")
        return "\n".join(out)
