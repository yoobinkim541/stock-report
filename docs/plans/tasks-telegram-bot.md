# Telegram Two-Way Bot — Implementation Tasks

## Task 1: telegram_bot.py
~/projects/stock-report/telegram_bot.py

A polling-based Telegram bot that handles commands:

### Commands to implement:
- /status — Read phase_state.json + investment-summary-{date}.json, display summary
- /phase — Read barbell_strategy.py phase state + SGOV/DCA targets
- /portfolio — Read investment-summary-{date}.json, list all 12 tickers with scores
- /alert add <ticker> <price> <buy|sell> — Store in price_alerts.json
- /alert list — Show all alerts
- /alert remove <id> — Delete alert

### Polling logic:
1. getUpdates via Telegram API (offset tracking in telegram_bot_state.json)
2. Only process messages from CHAT_ID=5771238245
3. Respond to each command via sendMessage
4. After processing commands, check price_alerts.json against yfinance prices
5. If alert triggered, send Telegram + mark triggered=true
6. Load STOCK_BOT_TOKEN from .env

### Data sources:
- Phase: Run `python3 -c "from barbell_strategy import load_phase_state; print(load_phase_state())"`
- Portfolio: read ~/reports/investment-summary-{today}.json
- SGOV target: Run `python3 -c "from barbell_strategy import classify_market,calculate_sgov_target; ..."`
- Real-time prices: yfinance

### File:
price_alerts.json (list of alert objects)
telegram_bot_state.json ({"last_update_id": N})

## Task 2: price_alerts.py
~/projects/stock-report/price_alerts.py

Helper module:
- load_alerts() / save_alerts()
- add_alert(ticker, price, type) -> id
- remove_alert(id) -> bool
- check_alerts() -> list of triggered alerts (checks yfinance prices)

## Rules
- All responses in Korean
- Use STOCK_BOT_TOKEN from .env (python-dotenv or manual parsing)
- Markdown parse_mode for Telegram messages
- Error handling for yfinance failures (show "데이터 없음")
- `python3 telegram_bot.py` must run without errors
- Phase state file: ~/projects/stock-report/phase_state.json

Print 'ALL_DONE' at end when both tasks complete.
