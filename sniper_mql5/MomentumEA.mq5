//+------------------------------------------------------------------+
//| MomentumEA.mq5                                                   |
//| Native MQL5 port of the AUT16 "trend + momentum candle" variants. |
//|                                                                    |
//| WHAT IT IS: smc_bot/results/AUT16_layers (rules: PREREG.md, sealed |
//| test: EXAM_RESULT.md). Gold only. Trade WITH the bigger-picture    |
//| trend on any 1-minute candle that closes with it - no pullback.    |
//| Pick the variant with InpVariant (1..5). Default 4.                |
//|                                                                    |
//|  #  trend (last CLOSED bar)       trigger (last closed M1 candle)  |
//|  1  M15 close vs EMA200           candle with the trend            |
//|  2  M15 close vs EMA200           candle + close beyond M1 EMA20   |
//|  3  M15 close vs EMA200           candle + close beyond M5 EMA20   |
//|  4  M30 close vs EMA100           candle + close beyond M1 EMA20   |
//|  5  M30 close vs EMA50            candle + close beyond M5 EMA20   |
//|  Exit: stop 6.89 price units; target 3.11 (variants 1-2) or 5.11   |
//|  (variants 3-5), measured from the FILL price. These are the       |
//|  research's -$70 / +$30 / +$50 at 0.10 lot, in price units, so     |
//|  they do not change with lot size.                                 |
//|                                                                    |
//| Filters from the research: no new entries 20:00-24:00 UTC, none on |
//| Sunday, none on Monday before 07:00 UTC. One trade at a time.      |
//| Closed before the Friday 20:45 UTC weekend close, and after 1 day. |
//|                                                                    |
//| NOT PROVEN LIVE. The simulator assumes a fill at the next candle's |
//| open with a flat spread. This EA writes every trade to a CSV with  |
//| its entry delay and spread so the demo can be compared to the      |
//| research. Do not put real money on it until the demo agrees.       |
//| No daily profit/loss limit (owner's choice for this test).         |
//+------------------------------------------------------------------+
#property copyright "AUT16 research port"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//================================================================ inputs
input int    InpVariant            = 4;          // 1..5 (see header)
input string InpSymbol             = "XAUUSD";   // base symbol; suffix (m, z, .raw) auto-detected
input double InpLot                = 0.01;       // fixed lot, no compounding
input long   InpMagicBase          = 990100;     // magic = base + variant
input int    InpSlippagePoints     = 200;

input double InpStopDist           = 6.89;       // price units from fill (the research's -$70 at 0.10 lot)
input double InpTargetDistShort    = 3.11;       // variants 1-2 (+$30)
input double InpTargetDistLong     = 5.11;       // variants 3-5 (+$50)
input int    InpMaxHoldMinutes     = 1440;       // close after this long

input int    InpBlockFromHourUTC   = 20;         // no new entries from this UTC hour ...
input int    InpBlockToHourUTC     = 24;         // ... up to (not including) this one
input int    InpMondayStartHourUTC = 7;          // no entries on Monday before this UTC hour
input bool   InpNoSunday           = true;
input int    InpWeekendFlatHour    = 20;         // Friday UTC: close everything from this time
input int    InpWeekendFlatMin     = 45;

input double InpMaxSpreadPoints    = 0.0;        // 0 = off (the research used a flat 0.05 price units)
input bool   InpDryRun             = false;      // true = log signals, place no orders

input string InpTelegramToken      = "";
input string InpTelegramChatId     = "";
input bool   InpTelegramAlerts     = true;
input bool   InpSendStartupPing    = true;

input string InpTradeLogFile       = "MomentumEA_trades.csv";   // MQL5/Files/

//================================================================ state
CTrade   trade;
string   g_symbol;
long     g_magic;
datetime g_last_bar;               // last M1 bar-open time we evaluated
ENUM_TIMEFRAMES g_trendTF;
int      g_trendEma;
int      g_trigType;               // 0 candle only, 1 beyond M1 EMA20, 2 beyond M5 EMA20
double   g_targetDist;
int      h_trend, h_e20m1, h_e20m5;

ulong    g_pos_id;                 // POSITION_IDENTIFIER of the trade we opened (0 = none)
string   g_close_reason;           // set by us before a TIME / WEEKEND close
datetime g_open_time;

//================================================================ helpers
bool ResolveSymbol()
  {
   string cand[4];
   cand[0] = InpSymbol;
   cand[1] = InpSymbol + "m";
   cand[2] = InpSymbol + "z";
   cand[3] = InpSymbol + ".raw";
   for(int i = 0; i < 4; i++)
      if(SymbolSelect(cand[i], true))
        {
         g_symbol = cand[i];
         return true;
        }
   return false;
  }

double Buf(int handle, int shift)
  {
   double b[];
   ArraySetAsSeries(b, true);
   if(CopyBuffer(handle, 0, shift, 1, b) != 1)
      return EMPTY_VALUE;
   return b[0];
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

bool TelegramSend(string text)
  {
   if(StringLen(InpTelegramToken) == 0 || StringLen(InpTelegramChatId) == 0)
      return false;
   string url = "https://api.telegram.org/bot" + InpTelegramToken + "/sendMessage";
   string body = "chat_id=" + InpTelegramChatId + "&parse_mode=HTML&text=" + UrlEncode(text);
   char post[];
   StringToCharArray(body, post, 0, StringLen(body));
   char result[];
   string rh;
   ResetLastError();
   int res = WebRequest("POST", url, "Content-Type: application/x-www-form-urlencoded\r\n", 5000, post, result, rh);
   if(res == -1)
     {
      Print("TelegramSend failed, error=", GetLastError(), " (allow https://api.telegram.org in Tools>Options>Expert Advisors)");
      return false;
     }
   return (res == 200);
  }

void Notify(string text)
  {
   // every Telegram message is headed "Momentum EA vN" so it is never confused with the old Sniper EA
   string msg = "<b>Momentum EA v" + IntegerToString(InpVariant) + "</b>\n" + text;
   if(InpTelegramAlerts)
      TelegramSend(msg);
   Print(text);
  }

// One row per OPEN and CLOSE, enough to compare with the simulator afterwards.
void LogRow(string event, int side, double price, double sl, double tp, double spread_pts,
            double delay_s, double profit, string reason)
  {
   bool isNew = !FileIsExist(InpTradeLogFile);
   int h = FileOpen(InpTradeLogFile, FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE)
     {
      Print("LogRow: cannot open ", InpTradeLogFile, " error=", GetLastError());
      return;
     }
   if(isNew)
      FileWrite(h, "time_utc", "event", "variant", "symbol", "side", "lot", "price", "sl", "tp",
                "spread_points", "entry_delay_s", "profit_usd", "reason");
   FileSeek(h, 0, SEEK_END);
   int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   FileWrite(h, TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS), event, InpVariant, g_symbol,
             (side == 1 ? "BUY" : (side == -1 ? "SELL" : "")), InpLot,
             DoubleToString(price, digits), DoubleToString(sl, digits), DoubleToString(tp, digits),
             DoubleToString(spread_pts, 0), DoubleToString(delay_s, 1), DoubleToString(profit, 2), reason);
   FileClose(h);
  }

//================================================================ time filters (all UTC; Ghana = UTC)
bool EntriesAllowed(string &why)
  {
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   int dow = dt.day_of_week;   // 0 = Sunday
   int hr  = dt.hour;
   if(dow == 6) { why = "Saturday"; return false; }
   if(dow == 0 && InpNoSunday) { why = "Sunday"; return false; }
   if(hr >= InpBlockFromHourUTC && hr < InpBlockToHourUTC) { why = "evening block"; return false; }
   if(dow == 1 && hr < InpMondayStartHourUTC) { why = "Monday early"; return false; }
   if(dow == 5 && (hr * 60 + dt.min) >= (InpWeekendFlatHour * 60 + InpWeekendFlatMin)) { why = "weekend close"; return false; }
   return true;
  }

bool WeekendFlatDue()
  {
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   if(dt.day_of_week == 6 || dt.day_of_week == 0)
      return true;
   if(dt.day_of_week == 5 && (dt.hour * 60 + dt.min) >= (InpWeekendFlatHour * 60 + InpWeekendFlatMin))
      return true;
   return false;
  }

//================================================================ the signal (same as the research, closed bars only)
// returns +1 buy, -1 sell, 0 none
int Signal(string &why)
  {
   why = "";
   MqlRates tr[];
   ArraySetAsSeries(tr, true);
   if(CopyRates(g_symbol, g_trendTF, 1, 1, tr) != 1)
      return 0;
   double tema = Buf(h_trend, 1);
   if(tema == EMPTY_VALUE)
      return 0;
   int trend = (tr[0].close > tema) ? 1 : -1;

   MqlRates m1[];
   ArraySetAsSeries(m1, true);
   if(CopyRates(g_symbol, PERIOD_M1, 1, 1, m1) != 1)
      return 0;
   double c = m1[0].close, o = m1[0].open;
   bool up = c > o, dn = c < o;

   double ref = 0.0;
   if(g_trigType == 1) ref = Buf(h_e20m1, 1);
   if(g_trigType == 2) ref = Buf(h_e20m5, 1);
   if(g_trigType != 0 && ref == EMPTY_VALUE)
      return 0;

   if(trend == 1 && up && (g_trigType == 0 || c > ref))
     {
      why = StringFormat("v%d trend up, up-candle", InpVariant);
      return 1;
     }
   if(trend == -1 && dn && (g_trigType == 0 || c < ref))
     {
      why = StringFormat("v%d trend down, down-candle", InpVariant);
      return -1;
     }
   return 0;
  }

//================================================================ positions (ours only: symbol + magic)
bool FindPosition(ulong &ticket)
  {
   int n = PositionsTotal();
   for(int i = 0; i < n; i++)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || !PositionSelectByTicket(t)) continue;
      if(PositionGetString(POSITION_SYMBOL) != g_symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != g_magic) continue;
      ticket = t;
      return true;
     }
   return false;
  }

void OpenTrade(int side, string why)
  {
   int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   MqlTick tk;
   if(!SymbolInfoTick(g_symbol, tk))
     {
      Print("no tick");
      return;
     }
   double ref = (side == 1) ? tk.ask : tk.bid;
   double sl = NormalizeDouble((side == 1) ? ref - InpStopDist : ref + InpStopDist, digits);
   double tp = NormalizeDouble((side == 1) ? ref + g_targetDist : ref - g_targetDist, digits);
   double spread_pts = (tk.ask - tk.bid) / SymbolInfoDouble(g_symbol, SYMBOL_POINT);

   if(InpMaxSpreadPoints > 0 && spread_pts > InpMaxSpreadPoints)
     {
      Print("signal skipped: spread ", spread_pts, " points > limit");
      return;
     }
   if(InpDryRun)
     {
      LogRow("SIGNAL", side, ref, sl, tp, spread_pts, 0, 0, why);
      return;
     }

   datetime t0 = TimeGMT();
   bool ok = (side == 1) ? trade.Buy(InpLot, g_symbol, 0.0, sl, tp, "MOM" + IntegerToString(InpVariant))
                         : trade.Sell(InpLot, g_symbol, 0.0, sl, tp, "MOM" + IntegerToString(InpVariant));
   if(!ok)
     {
      Print("order failed retcode=", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
      return;
     }
   ulong ticket;
   if(!FindPosition(ticket))
     {
      Print("order sent but position not found yet");
      return;
     }
   double fill = PositionGetDouble(POSITION_PRICE_OPEN);
   // re-anchor the exits to the REAL fill, as the research did
   double sl2 = NormalizeDouble((side == 1) ? fill - InpStopDist : fill + InpStopDist, digits);
   double tp2 = NormalizeDouble((side == 1) ? fill + g_targetDist : fill - g_targetDist, digits);
   if(MathAbs(sl2 - sl) > SymbolInfoDouble(g_symbol, SYMBOL_POINT) || MathAbs(tp2 - tp) > SymbolInfoDouble(g_symbol, SYMBOL_POINT))
     {
      if(!trade.PositionModify(ticket, sl2, tp2))
         Print("re-anchor failed retcode=", trade.ResultRetcode());
     }
   g_pos_id = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
   g_open_time = (datetime)PositionGetInteger(POSITION_TIME);
   g_close_reason = "";
   // seconds since the current M1 bar opened = how late we entered versus the research's "next open"
   double delay = (double)(TimeCurrent() - iTime(g_symbol, PERIOD_M1, 0));
   LogRow("OPEN", side, fill, sl2, tp2, spread_pts, delay, 0.0, why);
   Notify(StringFormat("%s <b>%s</b> %.2f %s @ %.*f\nSL %.*f  TP %.*f\n%s",
          (side == 1 ? "\xF0\x9F\x9F\xA2" : "\xF0\x9F\x94\xB4"), (side == 1 ? "BUY" : "SELL"),
          InpLot, g_symbol, digits, fill, digits, sl2, digits, tp2, why));
  }

// Called once when our position is gone: read the close from history and log it.
void LogCloseFromHistory()
  {
   if(g_pos_id == 0)
      return;
   if(!HistorySelectByPosition(g_pos_id))
      return;
   double profit = 0.0, px = 0.0;
   string reason = g_close_reason;
   int side = 0;
   bool found = false;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0) continue;
      long entry = HistoryDealGetInteger(d, DEAL_ENTRY);
      profit += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP) +
                HistoryDealGetDouble(d, DEAL_COMMISSION);
      if(entry == DEAL_ENTRY_IN)
         side = (HistoryDealGetInteger(d, DEAL_TYPE) == DEAL_TYPE_BUY) ? 1 : -1;
      if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
        {
         found = true;
         px = HistoryDealGetDouble(d, DEAL_PRICE);
         if(reason == "")
           {
            long r = HistoryDealGetInteger(d, DEAL_REASON);
            reason = (r == DEAL_REASON_TP) ? "TP" : (r == DEAL_REASON_SL) ? "SL" : "OTHER";
           }
        }
     }
   if(!found)
      return;
   LogRow("CLOSE", side, px, 0, 0, 0, 0, profit, reason);
   Notify(StringFormat("%s <b>CLOSED</b> %s\nResult: %s$%.2f", (profit > 0 ? "\xE2\x9C\x85" : "\xE2\x9D\x8C"),
          reason, (profit >= 0 ? "+" : "-"), MathAbs(profit)));
   g_pos_id = 0;
  }

//================================================================ one step (called by the timer and by ticks)
void Step()
  {
   ulong ticket;
   bool have = FindPosition(ticket);

   if(have)
     {
      // exits set by us: the 1-day limit and the weekend close (TP and SL are on the position)
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      if(WeekendFlatDue())
        {
         g_close_reason = "WEEKEND";
         trade.PositionClose(ticket);
        }
      else if(TimeCurrent() - opened >= InpMaxHoldMinutes * 60)
        {
         g_close_reason = "TIME";
         trade.PositionClose(ticket);
        }
      return;
     }

   if(g_pos_id != 0)
      LogCloseFromHistory();

   // one decision per newly opened M1 bar
   datetime bar = iTime(g_symbol, PERIOD_M1, 0);
   if(bar == 0 || bar == g_last_bar)
      return;
   g_last_bar = bar;

   string why;
   if(!EntriesAllowed(why))
      return;
   int side = Signal(why);
   if(side == 0)
      return;
   OpenTrade(side, why);
  }

//================================================================ lifecycle
int OnInit()
  {
   if(InpVariant < 1 || InpVariant > 5)
     {
      Print("InpVariant must be 1..5");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(!ResolveSymbol())
     {
      Print("cannot resolve a tradeable symbol from '", InpSymbol, "'");
      return INIT_FAILED;
     }
   g_magic = InpMagicBase + InpVariant;

   switch(InpVariant)
     {
      case 1: g_trendTF = PERIOD_M15; g_trendEma = 200; g_trigType = 0; g_targetDist = InpTargetDistShort; break;
      case 2: g_trendTF = PERIOD_M15; g_trendEma = 200; g_trigType = 1; g_targetDist = InpTargetDistShort; break;
      case 3: g_trendTF = PERIOD_M15; g_trendEma = 200; g_trigType = 2; g_targetDist = InpTargetDistLong;  break;
      case 4: g_trendTF = PERIOD_M30; g_trendEma = 100; g_trigType = 1; g_targetDist = InpTargetDistLong;  break;
      case 5: g_trendTF = PERIOD_M30; g_trendEma = 50;  g_trigType = 2; g_targetDist = InpTargetDistLong;  break;
     }

   h_trend = iMA(g_symbol, g_trendTF, g_trendEma, 0, MODE_EMA, PRICE_CLOSE);
   h_e20m1 = iMA(g_symbol, PERIOD_M1, 20, 0, MODE_EMA, PRICE_CLOSE);
   h_e20m5 = iMA(g_symbol, PERIOD_M5, 20, 0, MODE_EMA, PRICE_CLOSE);
   if(h_trend == INVALID_HANDLE || h_e20m1 == INVALID_HANDLE || h_e20m5 == INVALID_HANDLE)
     {
      Print("could not create indicator handles");
      return INIT_FAILED;
     }

   double vmin = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   if(InpLot < vmin)
     {
      Print("InpLot below broker minimum ", vmin);
      return INIT_PARAMETERS_INCORRECT;
     }

   trade.SetExpertMagicNumber(g_magic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   g_last_bar = 0;
   g_pos_id = 0;
   g_close_reason = "";

   // pick up a trade left open by an earlier run of this same variant
   ulong tk;
   if(FindPosition(tk))
     {
      g_pos_id = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
      g_open_time = (datetime)PositionGetInteger(POSITION_TIME);
     }

   EventSetTimer(1);

   MqlDateTime a, b, c;
   TimeToStruct(TimeGMT(), a);
   TimeToStruct(TimeCurrent(), b);
   TimeToStruct(TimeTradeServer(), c);
   PrintFormat("MomentumEA v%d on %s | TimeGMT %02d:%02d  TimeCurrent %02d:%02d  TimeTradeServer %02d:%02d | lot %.2f | dryrun=%s",
               InpVariant, g_symbol, a.hour, a.min, b.hour, b.min, c.hour, c.min, InpLot, InpDryRun ? "yes" : "no");
   if(InpSendStartupPing)
      Notify(StringFormat("\xF0\x9F\xA4\x96 Started on %s, lot %.2f%s.\nFirst trade Monday 07:00 UTC at the earliest. Research port, not proven live.",
             g_symbol, InpLot, InpDryRun ? " (DRY RUN)" : ""));
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   if(h_trend != INVALID_HANDLE) IndicatorRelease(h_trend);
   if(h_e20m1 != INVALID_HANDLE) IndicatorRelease(h_e20m1);
   if(h_e20m5 != INVALID_HANDLE) IndicatorRelease(h_e20m5);
  }

void OnTimer() { Step(); }
void OnTick()  { Step(); }
//+------------------------------------------------------------------+
