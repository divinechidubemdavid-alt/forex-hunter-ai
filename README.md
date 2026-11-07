# Forex Hunter AI

Telegram-first market analysis bot. Manual-only and read-only: it does not connect to brokers or place orders.

## Economic Event Intelligence

The main Telegram menu contains **Economic Events**. Its submenu provides upcoming events, event history, personal event creation, and official calendar synchronization.

### Official calendars

- U.S. Bureau of Labor Statistics (BLS) official ICS calendar — imported automatically by default.
- Federal Reserve FOMC official meeting calendar.
- U.S. Bureau of Economic Analysis (BEA) official release schedule.

Calendar times are converted to Africa/Lagos (WAT) for Telegram messages. You can override the BLS ICS feed with `ECONOMIC_CALENDAR_ICS_URL`, but only use an HTTPS feed whose terms allow the intended use.

### Research and checkpoints

For high-impact events, the scheduler sends the four configured checkpoints: 24 hours, 1 hour, 15 minutes and 5 minutes before release. The 24h/1h BLS baseline is calculated from the trailing three official releases and is explicitly labelled **model estimate**, not market consensus. If no supported baseline or consensus is available, the bot says so rather than inventing a figure.

Technical confirmation uses completed Twelve Data candles and the existing EMA/ADX/RSI/ATR analysis context. Event requests use the same local 800-request daily safety ceiling and automatic-scanner reserve; they cannot bypass the shared budget. The 5-minute check preserves the exact 15-minute analysis unless new completed-candle evidence meets material-change criteria. If fresh data is unavailable, it reports verification unavailable, not confirmed.

### Automatic post-release verification

BLS employment and CPI releases are checked against the official BLS public API. Other releases are not marked automatically verified unless a supported official adapter returns the actual data; the Telegram event detail allows a user to record a published figure. Post-release market reaction is sampled at 5, 15 and 30 minutes when price data is available. Directional alignment is labelled separately from a consensus forecast score.

### Scope and limitations

- The current automatic actual-data adapter covers BLS payrolls, unemployment, CPI and average hourly earnings. FOMC and BEA calendars are imported, but their numeric actuals are not automatically extracted yet.
- The current technical proxy is one configured representative pair for the event currency, not a full independent technical scan of every affected pair.
- News headlines, Treasury-yield feeds, market consensus from a licensed provider, and non-U.S. central-bank calendars require additional permitted data sources; no redistribution rights or consensus source is assumed.
- Economic analysis is informational, not a confirmed trade entry or guaranteed outcome.

## Existing safety controls

- Twelve Data local ceiling: 800 requests per Nigeria calendar day.
- The automatic scanner reserves 100 requests for manual analysis and monitoring.
- No broker connection or order execution.
