# Forex Hunter AI

Telegram-first market analysis bot. Manual-only and read-only: it does not connect to brokers or place orders.

## Economic Event Intelligence

The main Telegram menu contains **Economic Events**. Its submenu provides upcoming events, event history, personal event creation, official calendar synchronization and post-release reports.

### Calendar and research data sources

- Free official calendar sources are always checked first: BLS ICS, Federal Reserve FOMC meeting dates and the BEA release schedule. These sources continue to run whether or not a paid provider is configured.
- An optional Trading Economics adapter enriches official events with global high-impact calendar coverage, consensus forecasts, actuals, previous/revised values, country news and 10-year government-bond yield context. It does not replace the free official calendar imports.
- The Trading Economics adapter is disabled unless both `TRADING_ECONOMICS_API_KEY` and `TRADING_ECONOMICS_DISTRIBUTION_LICENSED=true` are configured. The latter is an explicit operator attestation that the account's contract permits data distribution to Telegram users. The provider's ordinary Standard plan is single-user; use a contract with redistribution rights (the Enterprise offering advertises data distribution). Never enable the flag without the required rights.
- With no licensed global provider, the bot uses BLS official actual data and a clearly labelled BLS historical-trend estimate for payrolls, unemployment, CPI and average hourly earnings. It does not invent consensus values.

### Checkpoints and analysis

The scheduler sends exactly four pre-release checkpoints: 24 hours, 1 hour, 15 minutes and 5 minutes. Event times are shown in Africa/Lagos (WAT).

- 24h/1h: refresh available consensus/model context, provider news headlines (when licensed), event figures and technical context.
- 15m: final directional review.
- 5m: verification against the saved 15m baseline, market by market. Each affected active market is compared with its own 15m snapshot. The exact 15m directional outlook is preserved if no material new completed-candle evidence is found. A genuine material change is recorded with the market and reason. If any required market data is unavailable, verification is marked unavailable rather than falsely confirmed.

Technical checks independently cover each affected active market using completed candles and the existing EMA/ADX/RSI/ATR and breakout context. Each event checkpoint retains its per-market technical snapshots and a market chart when price data is available. Event requests use the same Twelve Data local 800-request daily safety ceiling, Gold allocation, other-background allocation and manual reserve; they cannot bypass the shared budget.

### Post-release

- When a licensed global provider is configured, the bot polls the event's provider calendar record for actual, previous and revised values.
- Without that provider, BLS payroll, unemployment, CPI and average hourly earnings actuals are checked against the official BLS API. Other actuals are not marked verified unless a supported source returns them; a user can manually record a published figure.
- Price reaction is sampled independently for every affected active market at +1, +5, +15, +30, +60, +120, +240 and +1440 minutes (24 hours). The report stores each market's direction, pair direction, ADX, RSI, volatility and breakout state, plus per-window coverage. A missed observation window is labelled missed; a later quote is never presented as an earlier reaction. The report distinguishes consensus/model forecast alignment from directional alignment where no consensus forecast exists.
- Event-specific artwork, market charts, per-market technical snapshots, event research/checkpoint history, actual values, revisions, post-release reactions and reaction-coverage records are saved in the bot's persistent scanner state.

### Scope and safeguards

- The optional Trading Economics API covers global high-impact events, consensus, actuals and news; its use for multi-user Telegram delivery requires a suitable redistribution licence.
- Without that key, official calendar fallback is currently focused on U.S. BLS, FOMC and BEA events; other currencies can be added as personal events.
- Event research is restricted to exactly four active platform markets: XAU/USD, GBP/USD, USD/JPY and BTC/USD. USD releases are analysed across all four; GBP, JPY and Gold-specific events use their directly affected active market. No retired pair is used as a technical proxy.
- No broker connection, order placement or automatic trade execution is implemented.

## Trading styles

Manual **Analyze Market** starts by asking the user to choose a style, then only that style's entry timeframes, then the market, followed by an explicit **Generate Analysis** confirmation button:

| Style | Entry timeframes | Required completed higher-timeframe confirmation |
|---|---|---|
| Scalping Trade | 1m, 5m | 1m: 5m + 15m; 5m: 15m + 30m |
| Day Trade | 15m, 30m | 1h + 4h |
| Swing Trade | 1h, 4h, 1day | 1h: 4h + Daily; 4h: Daily; Daily: Weekly |

If a required higher timeframe is missing, neutral or opposed, the engine returns **NO TRADE** rather than forcing a direction. Weekly context is derived only from enough completed daily candles; otherwise the Swing setup is blocked.

All existing strategy calculations are retained and reused across the three styles. The upgrade does not delete or replace the current EMA trend, breakout/retest, Early Trend Entry, ADX, RSI, ATR or market-structure logic. Charts continue to display candlesticks, confirmed Supply and Demand zones and applicable trade-plan levels; indicator overlays remain hidden.

Scalping signals deliberately contain no Stop Loss or TP1/TP2/TP3 fields. Their tracking uses completed-candle weakening/reversal monitoring and marks a setup invalidated when the existing reversal check confirms an opposing reversal. Day and Swing signals retain structural Stop Loss and TP1/TP2/TP3 management.

The **Automatic Scanner** and **Strong Signals** are restricted to **DAY TRADE** only. Every new automatic signal, Strong Signal card, tracked record, monitoring alert and chart carries its trading-style label. Scalping and Swing remain available through manual Analyze Market only.

The active platform universe is XAU/USD, GBP/USD, USD/JPY and BTC/USD. Existing session eligibility is preserved: Gold and the two forex pairs are weekday markets; BTC/USD is the weekend market.

## Existing safety controls

- Twelve Data local ceiling: 800 requests per Nigeria calendar day.
- Twelve Data requests share one cache and per-symbol/timeframe request coalescing; the eight-second cooldown is respected across event, scanner, Gold and tracked-signal work.
- The 700-request background allowance is split into a protected 350-request Gold-monitor pool and a 350-request other-background pool. The separate 100-request reserve remains available for manual analysis.
- No provider API key or data-distribution licence is assumed to exist.
