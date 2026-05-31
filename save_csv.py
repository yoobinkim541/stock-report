#!/usr/bin/env python3
"""Convert investment summary JSON to Excel-friendly CSV."""
import json, csv, os, sys
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
today_str = datetime.now(KST).strftime("%Y-%m-%d")
reports_dir = os.path.expanduser("~/reports")

json_path = os.path.join(reports_dir, f"investment-summary-{today_str}.json")
csv_path = os.path.join(reports_dir, f"investment-data-{today_str}.csv")

if not os.path.exists(json_path):
    print(f"JSON not found: {json_path}")
    sys.exit(1)

with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)

    # Portfolio
    w.writerow(["=== Portfolio Analysis ==="])
    w.writerow(["Ticker", "Company", "Score", "Grade", "Signal", "Judgment",
                 "Price", "1D%", "1M%", "Vol%", "Top Reasons", "Top Risks"])
    for r in data.get("portfolio_summary", []):
        reasons = " | ".join(r.get("top_reasons", [])[:2])
        risks = " | ".join(r.get("top_risks", [])[:2])
        w.writerow([
            r.get("ticker", ""), r.get("company", ""),
            r.get("score", ""), r.get("grade", ""),
            r.get("signal", ""), r.get("judgment", ""),
            r.get("price", ""), r.get("change_1d_pct", ""),
            r.get("change_1mo_pct", ""), r.get("volume_vs_20d_avg_pct", ""),
            reasons, risks,
        ])

    # NASDAQ Top Buy
    w.writerow([])
    w.writerow(["=== NASDAQ 100 - Top Buy Candidates ==="])
    w.writerow(["Ticker", "Company", "Score", "Grade", "Signal"])
    for r in data.get("nasdaq_top_buy", []):
        w.writerow([r.get("ticker",""), r.get("company",""),
                     r.get("score",""), r.get("grade",""), r.get("signal","")])

    # NASDAQ Warnings
    w.writerow([])
    w.writerow(["=== NASDAQ 100 - Warnings ==="])
    w.writerow(["Ticker", "Company", "Score", "Grade", "Signal"])
    for r in data.get("nasdaq_warnings", []):
        w.writerow([r.get("ticker",""), r.get("company",""),
                     r.get("score",""), r.get("grade",""), r.get("signal","")])

print(f"CSV saved: {csv_path}")