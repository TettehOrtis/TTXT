//+------------------------------------------------------------------+
//| ExportEA.mq5 - one-shot: writes the last N days of M1 bars (with  |
//| spread) of InpSymbol to MQL5/Files/<InpFile>, then removes itself.|
//+------------------------------------------------------------------+
#property strict

input string InpSymbol = "XAUUSDm";
input int    InpDays   = 40;
input string InpFile   = "XAUUSDm_M1_export.csv";

int OnInit()
  {
   Print("ExportEA: start ", InpSymbol);
   SymbolSelect(InpSymbol, true);
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int n = CopyRates(InpSymbol, PERIOD_M1, 0, InpDays * 1440, r);
   Print("ExportEA: CopyRates returned ", n, " error=", GetLastError());
   if(n > 0)
     {
      int h = FileOpen(InpFile, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
      if(h == INVALID_HANDLE)
         Print("ExportEA: cannot open file, error=", GetLastError());
      else
        {
         FileWrite(h, "time", "open", "high", "low", "close", "tick_volume", "spread");
         int digits = (int)SymbolInfoInteger(InpSymbol, SYMBOL_DIGITS);
         for(int i = 0; i < n; i++)
            FileWrite(h, TimeToString(r[i].time, TIME_DATE | TIME_MINUTES), DoubleToString(r[i].open, digits), DoubleToString(r[i].high, digits),
                      DoubleToString(r[i].low, digits), DoubleToString(r[i].close, digits), (string)r[i].tick_volume, (string)r[i].spread);
         FileClose(h);
         Print("ExportEA: wrote ", n, " bars from ", TimeToString(r[0].time), " to ", TimeToString(r[n - 1].time));
        }
     }
   ExpertRemove();
   return INIT_SUCCEEDED;
  }
void OnTick() {}
