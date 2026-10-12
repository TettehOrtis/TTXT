//+------------------------------------------------------------------+
//| SniperEA.mq5                                                     |
//| Native MQL5 port of sniper/strategy.py + sniper/engine.py.       |
//|                                                                    |
//| PURPOSE: a wiring test. This reproduces the entry rule and basket |
//| management the Python "Sniper" bot runs, so it can be proven live |
//| on this Oracle/Wine MT5 install without the Python-bridge/Wine-   |
//| networking problem that blocked the EA-HTTP-bridge approach.      |
//|                                                                    |
//| RESEARCH CONTEXT (do not deploy real money on this rule without   |
//| re-reading this): smc_bot/results/AUT01_emapull/report.md found   |
//| this exact rule has NO edge in any of 2009-2025: "it fails, in    |
//| all three periods, at every shape, on both sides, on gold and on  |
//| nine FX majors." This port exists to prove the PLUMBING works     |
//| (compiles, attaches, takes a demo trade, sends Telegram), not     |
//| because the rule is expected to make money.                       |
//|                                                                    |
//| Simplifications vs the Python engine (documented, not hidden):    |
//|  - Losing-streak direction block and basket history are kept in   |
//|    memory (GlobalVariable-backed) per EA run, not in a durable    |
//|    state file re-read from the broker like engine.py does.        |
//|  - Daily realized P/L is read from today's closed deals with this |
//|    magic number via HistorySelect, same idea as Python's          |
//|    realized_since(), not identical code.                          |
//|  - Trading-window clock uses TimeGMT(); engine.py uses true UTC   |
//|    from the OS clock. Verify these agree on this server before    |
//|    trusting the window boundaries.                                |
//+------------------------------------------------------------------+
#property copyright "ported from sniper/ (Python)"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//================================================================ inputs
input string   InpSymbol               = "XAUUSD";   // base symbol; suffix is auto-detected
input ENUM_TIMEFRAMES InpTrendTF       = PERIOD_M15;
input ENUM_TIMEFRAMES InpEntryTF       = PERIOD_M1;
input int      InpTrendEmaFast         = 50;
input int      InpTrendEmaSlow         = 200;
input int      InpEntryEma             = 20;
input int      InpEntryRsiPeriod       = 14;
input double   InpEntryRsiBuyBelow     = 45.0;
input double   InpEntryRsiSellAbove    = 55.0;
input bool     InpRequireTrend         = true;
input bool     InpRequireBodyHold      = false;

input double   InpLotSize              = 0.02;
input int      InpPositionsPerEntry    = 5;
input double   InpBasketProfitTargetUSD= 15.0;
input double   InpStopLossUSD          = 70.0;
input int      InpCooldownSeconds      = 60;
input double   InpRewardMultiplier     = 2.0;
input int      InpBlockDirectionAfterSL= 2;
input double   InpDailyProfitTargetUSD = 200.0;
input double   InpDailyLossLimitUSD    = 170.0;
input double   InpHardSlPoints         = 0.0;        // 0 = auto: 1.5x stop_loss_usd in price

input bool     InpUseWindow            = true;
input int      InpWindowStartHour      = 7;
input int      InpWindowStartMin       = 0;
input int      InpWindowEndHour        = 12;
input int      InpWindowEndMin         = 0;
input int      InpWinddownMinutes      = 30;
input bool     InpFlatAtWindowEnd      = true;

input bool     InpFlatBeforeWeekend    = true;
input int      InpWeekendFlatDOW       = 5;           // 1=Mon..5=Fri (Friday)
input int      InpWeekendFlatHour      = 20;
input int      InpWeekendFlatMin       = 45;

input double   InpMaxSpreadPoints      = 0.0;         // 0 = off

input long     InpMagic                = 990077;
input int      InpSlippagePoints       = 200;

input string   InpTelegramToken        = "";          // paste SNIPER_TELEGRAM_TOKEN
input string   InpTelegramChatId       = "";           // paste SNIPER_TELEGRAM_CHAT_ID
input bool     InpTelegramAlerts       = true;
input bool     InpSendStartupPing      = true;         // send one Telegram message on EA init (works any day, proves the pipeline)

input string   InpTradeLogFile         = "SniperEA_trades.csv";  // written under MQL5/Files/; one row per basket open/close

//================================================================ state
CTrade   trade;
string   g_symbol;              // resolved broker symbol (suffix applied)
datetime g_last_entry_bar;      // last M1 bar time we evaluated, so one decision per closed bar
datetime g_cooldown_until;      // unix time
int      g_streak_buy;
int      g_streak_sell;
string   g_last_halt_reason;
string   g_last_window_phase;
bool     g_daily_target_alerted;
bool     g_daily_loss_alerted;
string   g_today_key;           // "YYYY.MM.DD" GMT, used to reset daily counters

//================================================================ utility: resolve broker symbol
bool ResolveSymbol()
  {
   string candidates[4];
   candidates[0] = InpSymbol;
   candidates[1] = InpSymbol + "m";
   candidates[2] = InpSymbol + "z";
   candidates[3] = InpSymbol + ".raw";
   for(int i = 0; i < 4; i++)
     {
      if(SymbolSelect(candidates[i], true))
        {
         g_symbol = candidates[i];
         return true;
        }
     }
   return false;
  }

//================================================================ Telegram
bool TelegramSend(string text)
  {
   if(StringLen(InpTelegramToken) == 0 || StringLen(InpTelegramChatId) == 0)
      return false;
   string url = "https://api.telegram.org/bot" + InpTelegramToken + "/sendMessage";
   string body = "chat_id=" + InpTelegramChatId + "&parse_mode=HTML&text=" + UrlEncode(text);
   char   post[];
   StringToCharArray(body, post, 0, StringLen(body));
   char   result[];
   string result_headers;
   ResetLastError();
   int res = WebRequest("POST", url, "Content-Type: application/x-www-form-urlencoded\r\n",
                         5000, post, result, result_headers);
   if(res == -1)
     {
      Print("TelegramSend WebRequest failed, error=", GetLastError(),
            " (add https://api.telegram.org to Tools>Options>Expert Advisors>Allow WebRequest)");
      return false;
     }
   return (res == 200);
  }

string UrlEncode(string s)
  {
   string out = "";
   int len = StringLen(s);
   for(int i = 0; i < len; i++)
     {
      ushort c = StringGetCharacter(s, i);
      if((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
         c == '-' || c == '_' || c == '.' || c == '~')
         out += ShortToString(c);
      else if(c == ' ')
         out += "+";
      else
         out += StringFormat("%%%02X", c);
     }
   return out;
  }

void Notify(string text)
  {
   if(InpTelegramAlerts)
      TelegramSend(text);
   Print(text);
  }

//================================================================ CSV trade log (MQL5/Files/<InpTradeLogFile>)
// One row per basket OPEN and CLOSE, so sessions/results can be analyzed later without scrolling
// Telegram. Appends (FileSeek to end); writes a header once if the file does not already exist.
void LogTradeEvent(string event, int side, string why, double legs_or_opened, double lot,
                    double ref_price, double money)
  {
   bool isNew = !FileIsExist(InpTradeLogFile);
   int h = FileOpen(InpTradeLogFile, FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE)
     {
      Print("LogTradeEvent: could not open ", InpTradeLogFile, ", error=", GetLastError());
      return;
     }
   if(isNew)
      FileWrite(h, "time_utc", "event", "symbol", "side", "legs", "lot", "ref_price", "money", "why");
   FileSeek(h, 0, SEEK_END);
   datetime now = TimeGMT();
   FileWrite(h, TimeToString(now, TIME_DATE | TIME_SECONDS), event, g_symbol,
             (side == 1 ? "BUY" : "SELL"), legs_or_opened, lot,
             DoubleToString(ref_price, (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS)),
             DoubleToString(money, 2), why);
   FileClose(h);
  }

//================================================================ indicators (match strategy.py exactly)
// strategy.py's ema() is a plain recursive EWM with alpha=2/(n+1), seeded at the first sample -
// same as MQL5's iMA MODE_EMA over the same closes, so we use the built-in handle.
double EmaLast(string sym, ENUM_TIMEFRAMES tf, int period, int shift = 0)
  {
   int h = iMA(sym, tf, period, 0, MODE_EMA, PRICE_CLOSE);
   if(h == INVALID_HANDLE)
      return EMPTY_VALUE;
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(h, 0, shift, 1, buf) != 1)
      return EMPTY_VALUE;
   IndicatorRelease(h);
   return buf[0];
  }

double RsiLast(string sym, ENUM_TIMEFRAMES tf, int period, int shift = 0)
  {
   int h = iRSI(sym, tf, period, PRICE_CLOSE);
   if(h == INVALID_HANDLE)
      return EMPTY_VALUE;
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(h, 0, shift, 1, buf) != 1)
      return EMPTY_VALUE;
   IndicatorRelease(h);
   return buf[0];
  }

//================================================================ trend + entry (mirrors strategy.py)
// +1 up, -1 down, 0 undecided
int TrendOf()
  {
   double f = EmaLast(g_symbol, InpTrendTF, InpTrendEmaFast, 1);   // last CLOSED trend bar
   double s = EmaLast(g_symbol, InpTrendTF, InpTrendEmaSlow, 1);
   if(f == EMPTY_VALUE || s == EMPTY_VALUE)
      return 0;
   if(f > s) return 1;
   if(f < s) return -1;
   return 0;
  }

// returns 1=BUY, -1=SELL, 0=NONE ; why is filled in for logging/Telegram
int EntrySignal(string &why)
  {
   why = "";
   int trend = InpRequireTrend ? TrendOf() : 0;

   MqlRates r[];
   ArraySetAsSeries(r, true);
   if(CopyRates(g_symbol, InpEntryTF, 1, 3, r) < 3)   // shift 1 = last CLOSED entry bar
      return 0;
   double c1 = r[0].close, o1 = r[0].open, h1 = r[0].high, l1 = r[0].low;

   double ema1 = EmaLast(g_symbol, InpEntryTF, InpEntryEma, 1);
   double rsi1 = RsiLast(g_symbol, InpEntryTF, InpEntryRsiPeriod, 1);
   if(ema1 == EMPTY_VALUE || rsi1 == EMPTY_VALUE)
      return 0;

   bool resumed_up = c1 > o1;
   bool resumed_dn = c1 < o1;
   bool body_up = (!InpRequireBodyHold) || (c1 > ema1);
   bool body_dn = (!InpRequireBodyHold) || (c1 < ema1);

   if(InpRequireTrend)
     {
      if(trend == 1 && l1 <= ema1 && rsi1 <= InpEntryRsiBuyBelow && resumed_up && body_up)
        {
         why = StringFormat("trend up, pullback to EMA%d, RSI %.1f", InpEntryEma, rsi1);
         return 1;
        }
      if(trend == -1 && h1 >= ema1 && rsi1 >= InpEntryRsiSellAbove && resumed_dn && body_dn)
        {
         why = StringFormat("trend down, pullback to EMA%d, RSI %.1f", InpEntryEma, rsi1);
         return -1;
        }
      return 0;
     }
   // trend filter off: pure mean reversion
   if(l1 <= ema1 && rsi1 <= InpEntryRsiBuyBelow && resumed_up && body_up)
     {
      why = StringFormat("no-trend mode, RSI %.1f", rsi1);
      return 1;
     }
   if(h1 >= ema1 && rsi1 >= InpEntryRsiSellAbove && resumed_dn && body_dn)
     {
      why = StringFormat("no-trend mode, RSI %.1f", rsi1);
      return -1;
     }
   return 0;
  }

//================================================================ trading window (mirrors engine.py)
// "ALL_DAY","BEFORE","OPEN","WINDDOWN","AFTER"
string WindowPhase()
  {
   if(!InpUseWindow)
      return "ALL_DAY";
   datetime now = TimeGMT();
   MqlDateTime dt; TimeToStruct(now, dt);
   int nowMin = dt.hour * 60 + dt.min;
   int start  = InpWindowStartHour * 60 + InpWindowStartMin;
   int end    = InpWindowEndHour * 60 + InpWindowEndMin;
   if(nowMin < start) return "BEFORE";
   if(nowMin >= end)  return "AFTER";
   if(nowMin >= end - InpWinddownMinutes) return "WINDDOWN";
   return "OPEN";
  }

bool MayOpen()
  {
   string p = WindowPhase();
   return (p == "ALL_DAY" || p == "OPEN");
  }

bool WindowFlatDue()
  {
   if(!InpUseWindow || !InpFlatAtWindowEnd)
      return false;
   return WindowPhase() == "AFTER";
  }

bool WeekendFlatDue()
  {
   if(!InpFlatBeforeWeekend)
      return false;
   datetime now = TimeGMT();
   MqlDateTime dt; TimeToStruct(now, dt);
   int dow = dt.day_of_week;                 // 0=Sun..6=Sat
   int wantDow = InpWeekendFlatDOW;           // 1=Mon..5=Fri
   if(dow > wantDow || dow == 0)
      return true;
   if(dow == wantDow)
     {
      int nowMin = dt.hour * 60 + dt.min;
      int cutMin = InpWeekendFlatHour * 60 + InpWeekendFlatMin;
      return nowMin >= cutMin;
     }
   return false;
  }

//================================================================ daily P/L (mirrors realized_since(day_start))
double RealizedToday()
  {
   datetime now = TimeGMT();
   MqlDateTime dt; TimeToStruct(now, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime dayStart = StructToTime(dt);

   if(!HistorySelect(dayStart, now + 60))
      return 0.0;
   double sum = 0.0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if((long)HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagic) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != g_symbol) continue;
      sum += HistoryDealGetDouble(ticket, DEAL_PROFIT)
           + HistoryDealGetDouble(ticket, DEAL_SWAP)
           + HistoryDealGetDouble(ticket, DEAL_COMMISSION);
     }
   return sum;
  }

//================================================================ open basket positions (ours only)
int CollectPositions(ulong &tickets[])
  {
   ArrayResize(tickets, 0);
   int n = PositionsTotal();
   for(int i = 0; i < n; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != g_symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      int sz = ArraySize(tickets);
      ArrayResize(tickets, sz + 1);
      tickets[sz] = ticket;
     }
   return ArraySize(tickets);
  }

double BasketFloating(const ulong &tickets[])
  {
   double sum = 0.0;
   for(int i = 0; i < ArraySize(tickets); i++)
     {
      if(!PositionSelectByTicket(tickets[i])) continue;
      sum += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
     }
   return sum;
  }

void CloseAll(const ulong &tickets[], string why, int side)
  {
   double money = BasketFloating(tickets);
   int closed = 0;
   for(int i = 0; i < ArraySize(tickets); i++)
     {
      if(trade.PositionClose(tickets[i]))
         closed++;
      else
         Print("close failed for ", tickets[i], " retcode=", trade.ResultRetcode());
     }
   bool won = money > 0;
   if(side == 1) { g_streak_buy = won ? 0 : g_streak_buy + 1; }
   else          { g_streak_sell = won ? 0 : g_streak_sell + 1; }
   g_cooldown_until = TimeGMT() + InpCooldownSeconds;

   string label = (side == 1) ? "BUY" : "SELL";
   LogTradeEvent("CLOSE", side, why, closed, InpLotSize, 0.0, money);
   Notify(StringFormat("%s <b>BASKET CLOSED</b> - %s\n%s %d x %.2f %s\nResult: %s$%.2f",
          (won ? "\xE2\x9C\x85" : "\xE2\x9D\x8C"), why, label, closed, InpLotSize, g_symbol,
          (money >= 0 ? "+" : "-"), MathAbs(money)));
  }

//================================================================ open a new basket (mirrors _open_basket)
double UsdToPrice(double usd, double lots)
  {
   double tickValue = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize <= 0) return 0.0;
   double perPrice = (tickValue / tickSize) * lots;
   if(perPrice <= 0) return 0.0;
   return usd / perPrice;
  }

void OpenBasket(int side, string why)
  {
   double lot = InpLotSize;
   double totalLots = lot * InpPositionsPerEntry;
   double point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);

   double dist;
   if(InpHardSlPoints > 0)
      dist = InpHardSlPoints * point;
   else
      dist = UsdToPrice(InpStopLossUSD * 1.5, totalLots);   // disaster stop, far wider than the basket's own $ stop

   MqlTick tick;
   if(!SymbolInfoTick(g_symbol, tick))
     {
      Print("no tick for ", g_symbol);
      return;
     }
   double ref = (side == 1) ? tick.ask : tick.bid;
   double slPrice = 0.0, tpPrice = 0.0;
   if(dist > 0)
     {
      slPrice = (side == 1) ? ref - dist : ref + dist;
      if(InpRewardMultiplier > 0)
         tpPrice = (side == 1) ? ref + dist * InpRewardMultiplier : ref - dist * InpRewardMultiplier;
     }
   slPrice = NormalizeDouble(slPrice, digits);
   tpPrice = NormalizeDouble(tpPrice, digits);

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);

   string label = (side == 1) ? "BUY" : "SELL";
   int opened = 0;
   for(int i = 0; i < InpPositionsPerEntry; i++)
     {
      bool ok;
      string comment = StringFormat("SNIPER-%s-%d", label, i + 1);
      if(side == 1)
         ok = trade.Buy(lot, g_symbol, 0.0, slPrice, tpPrice, comment);
      else
         ok = trade.Sell(lot, g_symbol, 0.0, slPrice, tpPrice, comment);
      if(ok)
         opened++;
      else
        {
         Print("leg ", i + 1, "/", InpPositionsPerEntry, " failed: retcode=", trade.ResultRetcode());
         break;
        }
     }
   if(opened == 0)
     {
      Print("no legs opened - basket abandoned");
      return;
     }
   LogTradeEvent("OPEN", side, why, opened, lot, ref, 0.0);
   Notify(StringFormat("%s <b>BASKET OPEN</b> - %s\n%s %d x %.2f %s @ %.*f\nTarget: +$%.2f  Stop: -$%.2f\n%s",
          (side == 1 ? "\xF0\x9F\x9F\xA2" : "\xF0\x9F\x94\xB4"), label, label, opened, lot, g_symbol,
          digits, ref, InpBasketProfitTargetUSD, InpStopLossUSD, why));
  }

//================================================================ main decision, no basket open
void MaybeOpen()
  {
   datetime now = TimeGMT();

   if(WeekendFlatDue())
      return;

   if(!MayOpen())
      return;

   double realized = RealizedToday();
   if(InpDailyProfitTargetUSD > 0 && realized >= InpDailyProfitTargetUSD)
     {
      if(!g_daily_target_alerted)
        {
         g_daily_target_alerted = true;
         Notify(StringFormat("\xF0\x9F\x8E\xAF <b>DAILY TARGET HIT</b>\nToday: +$%.2f", realized));
        }
      return;
     }
   if(InpDailyLossLimitUSD > 0 && realized <= -InpDailyLossLimitUSD)
     {
      if(!g_daily_loss_alerted)
        {
         g_daily_loss_alerted = true;
         Notify(StringFormat("\xF0\x9F\x9B\x91 <b>DAILY LOSS LIMIT HIT</b>\nToday: -$%.2f", MathAbs(realized)));
        }
      return;
     }

   if(now < g_cooldown_until)
      return;

   if(InpMaxSpreadPoints > 0)
     {
      long spread = SymbolInfoInteger(g_symbol, SYMBOL_SPREAD);
      if(spread > InpMaxSpreadPoints)
         return;
     }

   // one decision per closed entry bar
   datetime barTime = iTime(g_symbol, InpEntryTF, 1);
   if(barTime == g_last_entry_bar)
      return;
   g_last_entry_bar = barTime;

   string why;
   int side = EntrySignal(why);
   if(side == 0)
      return;

   int streak = (side == 1) ? g_streak_buy : g_streak_sell;
   if(streak >= InpBlockDirectionAfterSL)
     {
      Print("signal ", (side == 1 ? "BUY" : "SELL"), " suppressed: ", streak, " losing baskets in a row");
      return;
     }

   OpenBasket(side, why);
  }

//================================================================ manage an open basket
void Manage(const ulong &tickets[])
  {
   double money = BasketFloating(tickets);
   if(!PositionSelectByTicket(tickets[0])) return;
   int side = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;

   bool hitTp = money >= InpBasketProfitTargetUSD;
   bool hitSl = money <= -InpStopLossUSD;
   bool weekend = WeekendFlatDue();
   bool shut = WindowFlatDue();

   if(!(hitTp || hitSl || weekend || shut))
      return;

   string why = hitTp ? "TARGET" : hitSl ? "STOP" : weekend ? "WEEKEND" : "WINDOW END";
   CloseAll(tickets, why, side);
  }

//================================================================ window-change Telegram alerts
void WindowAlerts()
  {
   string phase = WindowPhase();
   if(phase == "ALL_DAY")
      return;
   if(g_last_window_phase != "" && g_last_window_phase != phase)
     {
      if(g_last_window_phase == "BEFORE" && (phase == "OPEN" || phase == "WINDDOWN"))
         Notify(StringFormat("\xF0\x9F\x9F\xA2 <b>WINDOW OPEN</b>\nTrading %s.", g_symbol));
      else if((g_last_window_phase == "OPEN" || g_last_window_phase == "WINDDOWN") && phase == "AFTER")
         Notify("\xF0\x9F\x94\xB4 <b>WINDOW CLOSED</b>\nNo more trades today.");
      else if(g_last_window_phase == "OPEN" && phase == "WINDDOWN")
         Notify("\xF0\x9F\x9F\xA0 <b>WINDING DOWN</b> - no new baskets.");
     }
   g_last_window_phase = phase;
  }

//================================================================ daily rollover
void RollDay()
  {
   datetime now = TimeGMT();
   MqlDateTime dt; TimeToStruct(now, dt);
   string key = StringFormat("%04d.%02d.%02d", dt.year, dt.mon, dt.day);
   if(key != g_today_key)
     {
      g_today_key = key;
      g_streak_buy = 0;
      g_streak_sell = 0;
      g_daily_target_alerted = false;
      g_daily_loss_alerted = false;
      Print("new UTC day ", key, " - daily limits and direction blocks reset");
     }
  }

//================================================================ lifecycle
int OnInit()
  {
   if(!ResolveSymbol())
     {
      Print("could not resolve a tradeable symbol from base '", InpSymbol, "'");
      return(INIT_FAILED);
     }
   PrintFormat("SniperEA resolved symbol: %s", g_symbol);

   g_last_entry_bar = 0;
   g_cooldown_until = 0;
   g_streak_buy = 0;
   g_streak_sell = 0;
   g_last_halt_reason = "";
   g_last_window_phase = "";
   g_daily_target_alerted = false;
   g_daily_loss_alerted = false;
   g_today_key = "";

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);

   EventSetTimer(5);

   if(InpSendStartupPing)
      Notify(StringFormat("\xF0\x9F\xA4\x96 SniperEA started on %s (%s). This is a wiring test - see "
             "smc_bot/results/AUT01_emapull/report.md for the research verdict on this rule.",
             g_symbol, EnumToString(InpEntryTF)));

   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTimer()
  {
   RollDay();

   ulong tickets[];
   int n = CollectPositions(tickets);
   WindowAlerts();

   if(n > 0)
      Manage(tickets);
   else
      MaybeOpen();
  }

void OnTick()
  {
   // decisions are timer-driven (one per closed bar, checked every 5s); OnTick left empty on
   // purpose so a quiet symbol does not stall the loop the way a pure OnTick EA would.
  }
//+------------------------------------------------------------------+
