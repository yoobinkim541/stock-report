from pathlib import Path


def test_deliver_investment_report_sends_combined_daily_report():
    script = Path("scripts/deliver_investment_report.sh").read_text(encoding="utf-8")

    assert "reports/market_report.py" in script
    assert "reports/combined_daily_report.py" in script
    assert "COMBINED_REPORT_FILE" in script
    assert "COMBINED_SUMMARY_FILE" in script
    assert "text@${COMBINED_SUMMARY_FILE}" in script
    assert "document=@${COMBINED_REPORT_FILE}" in script
    assert "document=@${REPORT_FILE}" not in script
    assert "text@${SUMMARY_FILE}" not in script


def test_market_risk_report_has_delivery_script_and_cron():
    script_path = Path("scripts/deliver_market_risk_report.sh")
    cron = Path("deploy/crontab.stock-report").read_text(encoding="utf-8")

    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")
    assert "reports/market_risk_report.py" in script
    assert "MARKET_RISK_REPORT_FILE" in script
    assert "MARKET_RISK_SUMMARY_FILE" in script
    assert "text@${MARKET_RISK_SUMMARY_FILE}" in script
    assert "document=@${MARKET_RISK_REPORT_FILE}" in script
    assert "deliver_market_risk_report.sh" in cron
