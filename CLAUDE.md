# Stock Report Pipeline

## Project Location
~/projects/stock-report/

## Scripts
- `investment_report.py` — Main report generator. Combines fundamental_score + daily_signals. Outputs markdown report + JSON data.
- `fundamental_score.py` — 100pt fundamental scoring model (yfinance based)
- `daily_signals.py` — Daily signal detection (price/volume changes)
- `market_report.py` — Daily market news report (SaveTicker API + Arca Live)
- `save_csv.py` — CSV data export helper

## Cron
- `stock-investment-report`: Runs at 23:00 UTC (8AM KST) Mon-Fri
- Script: `~/.hermes/scripts/deliver_investment_report.sh`
- Delivers to @Stock_botbot (Telegram)

## Portfolio
MSFT, QQQI, ORCL, NOW, CRM, SAP, UNH, SGOV, CPNG, NVDA, GOOGL, SPMO

## Telegram Bot
- @Stock_botbot (chat_id: 5771238245)
- Token in: .env file -> STOCK_BOT_TOKEN

## Output Files
- ~/reports/investment-report-{date}.md
- ~/reports/investment-data-{date}.json
- ~/reports/investment-summary-{date}.json

## Python
python3 (no pip module), uv available
yfinance, numpy, requests, beautifulsoup4 installed

## Safety
- NEVER commit .env or secrets
- Always show company name with ticker (e.g. "MSFT — Microsoft Corporation")
- Korean language output preferred