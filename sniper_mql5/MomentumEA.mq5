//+------------------------------------------------------------------+
//| MomentumEA.mq5                                                   |
//| Native MQL5 port of the AUT16 "trend + momentum-candle" variants. |
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
//|  (variants 3-5), measured from each leg's FILL price. At 0.10 lot  |
//|  in total these are the research's -$70 / +$30 / +$50.             |
//|                                                                    |
//| BUCKET MODE (like the old SniperEA): InpLegs legs of InpLot each   |
//| open together at the same moment; all legs carry the same stop and |
//| target at the BROKER, so they close together at the same price and |
//| the bucket wins or loses as one trade of InpLegs x InpLot lots.    |
//| Optional EA-side bucket dollar exits (InpBasketTargetUSD /         |
//| InpBasketStopUSD, 0 = off) close all legs when the total reaches   |
//| the number. Default 5 legs x 0.02 = 0.10 lot (the research size).  |
//|                                                                    |
//| Filters from the research: no new entries 20:00-24:00 UTC, none on |
//| Sunday, none on Monday before 07:00 UTC. One bucket at a time.     |
//| Closed before the Friday 20:45 UTC weekend close, and after 1 day. |
//|                                                                    |
//| NOT PROVEN LIVE. The simulator assumes a fill at the next candle's |
//| open with a flat spread. Every bucket is written to a CSV with its |
//| entry delay and spread so the demo can be compared to the research.|
//| No daily profit/loss limit (owner's choice for this test).         |
//+------------------------------------------------------------------+
#property copyright "AUT16 research port"
#property version   "2.10"
#property strict

#include <Trade\Trade.mqh>

//================================================================ inputs
input int    InpVariant            = 4;          // 1..5 (see header)
input string InpSymbol             = "XAUUSD";   // base symbol; suffix (m, z, .raw) auto-detected
input int    InpLegs               = 5;          // trades in one bucket, all opened together
input double InpLot                = 0.02;       // lot of EACH leg (total = legs x lot)
input long   InpMagicBase          = 990100;     // magic = base + variant
input int    InpSlippagePoints     = 200;

input double InpStopDist           = 6.89;       // price units from fill (the research's -$70 at 0.10 lot)
input double InpTargetDistShort    = 3.11;       // variants 1-2 (+$30 at 0.10 lot)
input double InpTargetDistLong     = 5.11;       // variants 3-5 (+$50 at 0.10 lot)
input int    InpMaxHoldMinutes     = 1440;       // close the bucket after this long

input double InpBasketTargetUSD    = 0.0;        // optional: close all legs when the bucket is up this much (0 = off)
input double InpBasketStopUSD      = 0.0;        // optional: close all legs when the bucket is down this much (0 = off)

input int    InpBlockFromHourUTC   = 20;         // no new entries from this UTC hour ...
input int    InpBlockToHourUTC     = 24;         // ... up to (not including) this one
input int    InpMondayStartHourUTC = 7;          // no entries on Monday before this UTC hour
input bool   InpNoSunday           = true;
input int    InpWeekendFlatHour    = 20;         // Friday UTC: close everything from this time
input int    InpWeekendFlatMin     = 45;

input int    InpMaxEntryDelaySec   = 10;         // only enter in the first N seconds of a new M1 bar (0 = off); skips stale mid-bar re-entries
input double InpMaxSpreadPoints    = 0.0;       // 0 = off (the research used a flat 0.05 price units)
input bool   InpDryRun             = false;      // true = log signals, place no orders

input string InpTelegramToken      = "";
input string InpTelegramChatId     = "";
input bool   InpTelegramAlerts     = true;
input bool   InpSendStartupPing    = true;

input string InpTradeLogFile       = "MomentumEA_bucket_trades.csv";   // MQL5/Files/

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

ulong    g_ids[];                  // POSITION_IDENTIFIERs of the legs of the bucket we opened
string   g_close_reason;           // set by us before a TIME / WEEKEND / BASKET close
int      g_side;                   // side of the open bucket

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

// One row per bucket OPEN and CLOSE, enough to compare with the simulator afterwards.
void LogRow(string event, int side, int legs, double price, double sl, double tp, double spread_pts,
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
      FileWrite(h, "time_utc", "event", "variant", "symbol", "side", "legs", "lot_per_leg", "price", "sl", "tp",
                "spread_points", "entry_delay_s", "profit_usd", "reason");
   FileSeek(h, 0, SEEK_END);
   int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   FileWrite(h, TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS), event, InpVariant, g_symbol,
             (side == 1 ? "BUY" : (side == -1 ? "SELL" : "")), legs, InpLot,
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
int CollectPositions(ulong &tickets[])
  {
   ArrayResize(tickets, 0);
   int n = PositionsTotal();
   for(int i = 0; i < n; i++)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || !PositionSelectByTicket(t)) continue;
      if(PositionGetString(POSITION_SYMBOL) != g_symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != g_magic) continue;
      int sz = ArraySize(tickets);
      ArrayResize(tickets, sz + 1);
      tickets[sz] = t;
     }
   return ArraySize(tickets);
  }

double BucketFloating(const ulong &tickets[])
  {
   double sum = 0.0;
   for(int i = 0; i < ArraySize(tickets); i++)
     {
      if(!PositionSelectByTicket(tickets[i])) continue;
      sum += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
     }
   return sum;
  }

void CloseBucket(const ulong &tickets[], string why)
  {
   g_close_reason = why;
   for(int i = 0; i < ArraySize(tickets); i++)
      if(!trade.PositionClose(tickets[i]))
         Print("close failed for ", tickets[i], " retcode=", trade.ResultRetcode());
  }

void RememberLegs(const ulong &tickets[])
  {
   ArrayResize(g_ids, 0);
   for(int i = 0; i < ArraySize(tickets); i++)
     {
      if(!PositionSelectByTicket(tickets[i])) continue;
      int sz = ArraySize(g_ids);
      ArrayResize(g_ids, sz + 1);
      g_ids[sz] = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
     }
  }

void OpenBucket(int side, string why)
  {
   int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   MqlTick tk;
   if(!SymbolInfoTick(g_symbol, tk))
     {
      Print("no tick");
      return;
     }
   double ref = (side == 1) ? tk.ask : tk.bid;
   double sl = NormalizeDouble((side == 1) ? ref - InpStopDist : ref + InpStopDist, digits);
   double tp = NormalizeDouble((side == 1) ? ref + g_targetDist : ref - g_targetDist, digits);
   double spread_pts = (tk.ask - tk.bid) / point;

   if(InpMaxSpreadPoints > 0 && spread_pts > InpMaxSpreadPoints)
     {
      Print("signal skipped: spread ", spread_pts, " points > limit");
      return;
     }
   if(InpDryRun)
     {
      LogRow("SIGNAL", side, InpLegs, ref, sl, tp, spread_pts, 0, 0, why);
      return;
     }

   // enough free margin for the whole bucket?
   double need = 0.0;
   if(OrderCalcMargin((side == 1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, g_symbol, InpLot * InpLegs, ref, need) &&
      need > AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.9)
     {
      Print("bucket skipped: margin needed ", need, " > 90% of free margin ", AccountInfoDouble(ACCOUNT_MARGIN_FREE));
      return;
     }

   int opened = 0;
   for(int i = 0; i < InpLegs; i++)
     {
      string cm = "MOM" + IntegerToString(InpVariant) + "-" + IntegerToString(i + 1);
      bool ok = (side == 1) ? trade.Buy(InpLot, g_symbol, 0.0, sl, tp, cm)
                            : trade.Sell(InpLot, g_symbol, 0.0, sl, tp, cm);
      if(!ok)
        {
         Print("leg ", i + 1, "/", InpLegs, " failed retcode=", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
         break;
        }
      opened++;
     }
   if(opened == 0)
     {
      Print("no legs opened - bucket abandoned");
      return;
     }

   // re-anchor every leg's exits to ITS real fill, as the research did
   ulong legs[];
   int n = CollectPositions(legs);
   double sumfill = 0.0;
   for(int i = 0; i < n; i++)
     {
      if(!PositionSelectByTicket(legs[i])) continue;
      double fill = PositionGetDouble(POSITION_PRICE_OPEN);
      sumfill += fill;
      double sl2 = NormalizeDouble((side == 1) ? fill - InpStopDist : fill + InpStopDist, digits);
      double tp2 = NormalizeDouble((side == 1) ? fill + g_targetDist : fill - g_targetDist, digits);
      if(MathAbs(sl2 - PositionGetDouble(POSITION_SL)) > point || MathAbs(tp2 - PositionGetDouble(POSITION_TP)) > point)
         if(!trade.PositionModify(legs[i], sl2, tp2))
            Print("re-anchor failed for leg ", legs[i], " retcode=", trade.ResultRetcode());
     }
   RememberLegs(legs);
   g_side = side;
   g_close_reason = "";
   double avg = (n > 0) ? sumfill / n : ref;
   // seconds since the current M1 bar opened = how late we entered versus the research's "next open"
   double delay = (double)(TimeCurrent() - iTime(g_symbol, PERIOD_M1, 0));
   LogRow("OPEN", side, n, avg, sl, tp, spread_pts, delay, 0.0, why);

   double win_usd = 0.0, loss_usd = 0.0;
   if(!OrderCalcProfit((side == 1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, g_symbol, InpLot * n, avg,
                       (side == 1) ? avg + g_targetDist : avg - g_targetDist, win_usd))
      win_usd = 0.0;
   if(!OrderCalcProfit((side == 1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, g_symbol, InpLot * n, avg,
                       (side == 1) ? avg - InpStopDist : avg + InpStopDist, loss_usd))
      loss_usd = 0.0;
   Notify(StringFormat("%s <b>BUCKET %s</b> %d x %.2f %s @ %.*f\nTarget %.*f (+$%.2f)  Stop %.*f (-$%.2f)\n%s",
          (side == 1 ? "\xF0\x9F\x9F\xA2" : "\xF0\x9F\x94\xB4"), (side == 1 ? "BUY" : "SELL"), n, InpLot, g_symbol,
          digits, avg, digits, tp, MathAbs(win_usd), digits, sl, MathAbs(loss_usd), why));
  }

// Called once when the bucket is gone: read every leg from history and log one summary row.
void LogCloseFromHistory()
  {
   int nid = ArraySize(g_ids);
   if(nid == 0)
      return;
   double profit = 0.0, pxsum = 0.0;
   int outs = 0;
   string reason = g_close_reason;
   for(int k = 0; k < nid; k++)
     {
      if(!HistorySelectByPosition(g_ids[k]))
         continue;
      int total = HistoryDealsTotal();
      for(int i = 0; i < total; i++)
        {
         ulong d = HistoryDealGetTicket(i);
         if(d == 0) continue;
         long entry = HistoryDealGetInteger(d, DEAL_ENTRY);
         profit += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP) +
                   HistoryDealGetDouble(d, DEAL_COMMISSION);
         if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
           {
            outs++;
            pxsum += HistoryDealGetDouble(d, DEAL_PRICE);
            if(reason == "")
              {
               long r = HistoryDealGetInteger(d, DEAL_REASON);
               reason = (r == DEAL_REASON_TP) ? "TP" : (r == DEAL_REASON_SL) ? "SL" : "OTHER";
              }
           }
        }
     }
   if(outs == 0)
      return;
   LogRow("CLOSE", g_side, outs, pxsum / outs, 0, 0, 0, 0, profit, reason);
   Notify(StringFormat("%s <b>BUCKET CLOSED</b> %s\n%d legs, result: %s$%.2f", (profit > 0 ? "\xE2\x9C\x85" : "\xE2\x9D\x8C"),
          reason, outs, (profit >= 0 ? "+" : "-"), MathAbs(profit)));
   ArrayResize(g_ids, 0);
  }

//================================================================ one step (called by the timer and by ticks)
void Step()
  {
   ulong tickets[];
   int n = CollectPositions(tickets);

   if(n > 0)
     {
      // exits set by us: the 1-day limit, the weekend close and the optional bucket dollar exits
      // (TP and SL are already on every leg at the broker)
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      double floating = BucketFloating(tickets);
      if(WeekendFlatDue())
         CloseBucket(tickets, "WEEKEND");
      else if(TimeCurrent() - opened >= InpMaxHoldMinutes * 60)
         CloseBucket(tickets, "TIME");
      else if(InpBasketTargetUSD > 0 && floating >= InpBasketTargetUSD)
         CloseBucket(tickets, "BASKET_TP");
      else if(InpBasketStopUSD > 0 && floating <= -InpBasketStopUSD)
         CloseBucket(tickets, "BASKET_SL");
      return;
     }

   if(ArraySize(g_ids) > 0)
      LogCloseFromHistory();

   // one decision per newly opened M1 bar
   datetime bar = iTime(g_symbol, PERIOD_M1, 0);
   if(bar == 0 || bar == g_last_bar)
      return;
   g_last_bar = bar;

   // the research entered at the open of a new bar; a signal seen later in the bar (for example right
   // after the previous bucket closed) is stale, so skip it and wait for the next bar
   if(InpMaxEntryDelaySec > 0 && TimeCurrent() - bar > InpMaxEntryDelaySec)
      return;

   string why;
   if(!EntriesAllowed(why))
      return;
   int side = Signal(why);
   if(side == 0)
      return;
   OpenBucket(side, why);
  }

//================================================================ lifecycle
int OnInit()
  {
   if(InpVariant < 1 || InpVariant > 5)
     {
      Print("InpVariant must be 1..5");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpLegs < 1 || InpLegs > 10)
     {
      Print("InpLegs must be 1..10");
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
   g_close_reason = "";
   g_side = 0;
   ArrayResize(g_ids, 0);

   // pick up a bucket left open by an earlier run of this same variant
   ulong tk[];
   if(CollectPositions(tk) > 0)
     {
      RememberLegs(tk);
      if(PositionSelectByTicket(tk[0]))
         g_side = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
     }

   EventSetTimer(1);

   MqlDateTime a, b, c;
   TimeToStruct(TimeGMT(), a);
   TimeToStruct(TimeCurrent(), b);
   TimeToStruct(TimeTradeServer(), c);
   PrintFormat("MomentumEA v%d on %s | TimeGMT %02d:%02d  TimeCurrent %02d:%02d  TimeTradeServer %02d:%02d | %d legs x %.2f lot | dryrun=%s",
               InpVariant, g_symbol, a.hour, a.min, b.hour, b.min, c.hour, c.min, InpLegs, InpLot, InpDryRun ? "yes" : "no");
   if(InpSendStartupPing)
      Notify(StringFormat("\xF0\x9F\xA4\x96 Started on %s, bucket of %d x %.2f lot (%.2f total)%s.\nSame exit as the research: target %.2f, stop %.2f of price.",
             g_symbol, InpLegs, InpLot, InpLegs * InpLot, InpDryRun ? " (DRY RUN)" : "", g_targetDist, InpStopDist));
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
