//+------------------------------------------------------------------+
//| ExportM1.mq5 - writes the last N days of M1 bars (with spread)    |
//| to MQL5/Files/<InpFile>, for replaying the EA offline.            |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input string InpSymbol = "XAUUSDm";
input int    InpDays   = 40;
input string InpFile   = "XAUUSDm_M1_export.csv";

void OnStart()
  {
   Print("ExportM1: start ", InpSymbol);
   SymbolSelect(InpSymbol, true);
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int want = InpDays * 1440;
   int n = CopyRates(InpSymbol, PERIOD_M1, 0, want, r);
   Print("ExportM1: CopyRates returned ", n, " error=", GetLastError());
   if(n <= 0)
      return;
   int h = FileOpen(InpFile, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE)
     {
      Print("ExportM1: cannot open file, error=", GetLastError());
      return;
     }
   FileWrite(h, "time", "open", "high", "low", "close", "tick_volume", "spread");
   int digits = (int)SymbolInfoInteger(InpSymbol, SYMBOL_DIGITS);
   for(int i = 0; i < n; i++)
      FileWrite(h, TimeToString(r[i].time, TIME_DATE | TIME_MINUTES), DoubleToString(r[i].open, digits), DoubleToString(r[i].high, digits),
                DoubleToString(r[i].low, digits), DoubleToString(r[i].close, digits), (string)r[i].tick_volume, (string)r[i].spread);
   FileClose(h);
   Print("ExportM1: wrote ", n, " bars from ", TimeToString(r[0].time), " to ", TimeToString(r[n - 1].time));
  }
