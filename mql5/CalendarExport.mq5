//+------------------------------------------------------------------+
//| CalendarExport.mq5                                               |
//| Exports MT5 Economic Calendar to JSON for Python integration.    |
//| Attach to any chart (e.g. EURUSD M1). Refreshes every minute.   |
//+------------------------------------------------------------------+
#property copyright "MT5 Bot"
#property version   "1.01"
#property strict

input int    InpRefreshSeconds = 60;       // Refresh interval (seconds)
input int    InpDaysAhead      = 7;        // How many days ahead to fetch
input int    InpDaysBehind     = 1;        // How many days behind to include
input int    InpMinImportance  = 3;        // 1=Low 2=Medium 3=High only
input string InpOutputFile     = "calendar_events.json"; // Output filename

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
    EventSetTimer(InpRefreshSeconds);
    ExportCalendar(); // Export immediately on attach
    Print("[CalendarExport] EA started. Refreshing every ", InpRefreshSeconds, "s → ", InpOutputFile);
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    EventKillTimer();
}

//+------------------------------------------------------------------+
//| Timer event                                                      |
//+------------------------------------------------------------------+
void OnTimer()
{
    ExportCalendar();
}

//+------------------------------------------------------------------+
//| Main export function                                             |
//+------------------------------------------------------------------+
void ExportCalendar()
{
    datetime from_time = TimeCurrent() - InpDaysBehind * 86400;
    datetime to_time   = TimeCurrent() + InpDaysAhead  * 86400;

    MqlCalendarValue values[];
    int count = CalendarValueHistory(values, from_time, to_time);

    if(count < 0)
    {
        Print("[CalendarExport] CalendarValueHistory failed. Calendar may not be available.");
        return;
    }

    string json = "[\n";
    int written = 0;

    for(int i = 0; i < count; i++)
    {
        MqlCalendarEvent event;
        if(!CalendarEventById(values[i].event_id, event))
            continue;

        // Filter by importance
        if((int)event.importance < InpMinImportance)
            continue;

        MqlCalendarCountry country;
        if(!CalendarCountryById(event.country_id, country))
            continue;

        // Get actual/forecast/prev (stored as x1,000,000 in MQL5)
        string actual_str   = "null";
        string forecast_str = "null";
        string prev_str     = "null";

        if(values[i].actual_value != LONG_MIN)
            actual_str   = DoubleToString((double)values[i].actual_value   / 1000000.0, 3);
        if(values[i].forecast_value != LONG_MIN)
            forecast_str = DoubleToString((double)values[i].forecast_value / 1000000.0, 3);
        if(values[i].prev_value != LONG_MIN)
            prev_str     = DoubleToString((double)values[i].prev_value     / 1000000.0, 3);

        // Format time as UTC string
        string time_str = TimeToString(values[i].time, TIME_DATE | TIME_MINUTES | TIME_SECONDS);
        StringReplace(time_str, ".", "-");  // "2026.02.22 13:30" → "2026-02-22 13:30"

        // Map importance int to label
        string importance_label;
        switch((int)event.importance)
        {
            case 1:  importance_label = "low";    break;
            case 2:  importance_label = "medium"; break;
            case 3:  importance_label = "high";   break;
            default: importance_label = "unknown";
        }

        // Escape event name for JSON
        string ev_name = event.name;
        StringReplace(ev_name, "\"", "'");
        StringReplace(ev_name, "\\", "/");

        if(written > 0)
            json += ",\n";

        json += "  {\n";
        json += "    \"id\": "            + IntegerToString(values[i].event_id)   + ",\n";
        json += "    \"name\": \""        + ev_name                               + "\",\n";
        json += "    \"country\": \""     + country.code                           + "\",\n";
        json += "    \"currency\": \""    + country.currency                       + "\",\n";
        json += "    \"time_utc\": \""    + time_str                               + "\",\n";
        json += "    \"importance\": "    + IntegerToString((int)event.importance) + ",\n";
        json += "    \"importance_str\": \"" + importance_label                    + "\",\n";
        json += "    \"actual\": "        + actual_str                             + ",\n";
        json += "    \"forecast\": "      + forecast_str                           + ",\n";
        json += "    \"previous\": "      + prev_str                               + "\n";
        json += "  }";

        written++;
    }

    json += "\n]\n";

    // Write atomically: temp file → rename
    string tmp_file = InpOutputFile + ".tmp";
    int handle = FileOpen(tmp_file, FILE_WRITE | FILE_TXT | FILE_ANSI, '\n');
    if(handle == INVALID_HANDLE)
    {
        Print("[CalendarExport] Cannot open file for writing: ", tmp_file);
        return;
    }
    FileWriteString(handle, json);
    FileClose(handle);

    // Rename temp → final (atomic swap)
    FileDelete(InpOutputFile);
    FileMove(tmp_file, 0, InpOutputFile, 0);

    Print("[CalendarExport] Exported ", written, " events → ", InpOutputFile,
          "  (", from_time, " to ", to_time, ")");
}

//+------------------------------------------------------------------+
//| Tick handler (not used)                                          |
//+------------------------------------------------------------------+
void OnTick() {}
//+------------------------------------------------------------------+
