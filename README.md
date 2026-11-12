# Forex Hunter AI

Telegram-first market analysis bot. Manual-only and read-only: it does not connect to brokers or place orders.

## Economic Event Intelligence

The main Telegram menu contains **Economic Events**. Its submenu provides upcoming events, event history, personal event creation, official calendar synchronization and post-release reports.

### Calendar and research data sources

- Without a paid data-provider key, the bot imports the official BLS ICS calendar, Federal Reserve FOMC meeting dates and BEA release schedule.
- An optional Trading Economics adapter provides global high-impact calendars, consensus forecasts, actuals, previous/revised values, country news and 10-year government-bond yield context.
- The Trading Economics adapter is disabled unless both `TRADING_ECONOMICS_API_KEY` and `TRADING_ECONOMICS_DISTRIBUTION_LICENSED=true` are configured. The latter is an explicit operator attestation that the account's contract permits data distribution to Telegram users. The provider's ordinary Standard plan is single-user; use a contract with redistribution rights (the Enterprise offering advertises data distribution). Never enable the flag without the required rights.
- With no licensed global provider, the bot uses BLS official actual data and a clearly labelled BLS historical-trend estimate for payrolls, unemployment, CPI and average hourly earnings. It does not invent consensus values.

### Checkpoints and analysis

The scheduler sends exactly four pre-release checkpoints: 24 hours, 1 hour, 15 minutes and 5 minutes. Event times are shown in Africa/Lagos (WAT).

- 24h/1h: refresh available consensus/model context, provider news headlines (when licensed), event figures and technical context.
- 15m: final directional review.
- 5m: verification against the saved 15m baseline. The exact baseline is preserved if no material new completed-candle evidence is found. A genuine material change is recorded with reasons. If fresh data is unavailable, the alert says verification is unavailable rather than falsely confirming it.

Technical checks use completed candles and the existing EMA/ADX/RSI/ATR and breakout context. Event requests use the same Twelve Data local 800-request daily safety ceiling and the automatic-scanner reserve; they cannot bypass the shared budget. A market chart is attached when price data is available.

### Post-release

- When a licensed global provider is configured, the bot polls the event's provider calendar record for actual, previous and revised values.
- Without that provider, BLS payroll, unemployment, CPI and average hourly earnings actuals are checked against the official BLS API. Other actuals are not marked verified unless a supported source returns them; a user can manually record a published figure.
- Price reaction is sampled at +5, +15 and +30 minutes when data is available. The report distinguishes consensus/model forecast alignment from directional alignment where no consensus forecast exists.
- Event analyses, checkpoint history, actual values, revisions and reaction scores are saved in the bot's persistent scanner state.

### Scope and safeguards

- The optional Trading Economics API covers global high-impact events, consensus, actuals and news; its use for multi-user Telegram delivery requires a suitable redistribution licence.
- Without that key, official calendar fallback is currently focused on U.S. BLS, FOMC and BEA events; other currencies can be added as personal events.
- The technical snapshot uses one representative proxy pair per event currency; affected pair biases are mapped from currency strength rather than independently scanning every pair.
- No broker connection, order placement or automatic trade execution is implemented.

## Trading styles

Manual **Analyze Market** starts by asking the user to choose a style, then only that style's entry timeframes, then the market:

| Style | Entry timeframes | Required completed higher-timeframe confirmation |
|---|---|---|
| Scalping Trade | 1m, 5m | 1m: 5m + 15m; 5m: 15m + 30m |
| Day Trade | 15m, 30m | 1h + 4h |
| Swing Trade | 1h, 4h, 1day | 1h: 4h + Daily; 4h: Daily; Daily: Weekly |

If a required higher timeframe is missing, neutral or opposed, the engine returns **NO TRADE** rather than forcing a direction. Weekly context is derived only from enough completed daily candles; otherwise the Swing setup is blocked.

All existing strategy calculations are retained and reused across the three styles. The upgrade does not delete or replace the current EMA trend, breakout/retest, Early Trend Entry, ADX, RSI, ATR or market-structure logic. Charts continue to display candlesticks, confirmed Supply and Demand zones and applicable trade-plan levels; indicator overlays remain hidden.

Scalping signals deliberately contain no Stop Loss or TP1/TP2/TP3 fields. Their tracking uses completed-candle weakening/reversal monitoring and marks a setup invalidated when the existing reversal check confirms an opposing reversal. Day and Swing signals retain structural Stop Loss and TP1/TP2/TP3 management.

The **Automatic Scanner** and **Strong Signals** are restricted to **DAY TRADE** only. Every new automatic signal, Strong Signal card, tracked record, monitoring alert and chart carries its trading-style label. Scalping and Swing remain available through manual Analyze Market only.

BTC/USD is eligible on weekdays and weekends. On weekends, BTC/USD is the only market enabled; the other configured markets remain excluded.

## Existing safety controls

- Twelve Data local ceiling: 800 requests per Nigeria calendar day.
- The automatic scanner reserves 100 requests for manual analysis and monitoring.
- No provider API key or data-distribution licence is assumed to exist.
