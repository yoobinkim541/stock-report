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


# ── 차트 PNG 정지 회귀 (감사 2026-08-21) ──────────────────────────────────────
# deliver_investment_report.sh 가 PYTHON_BIN 을 맨 `python3`(시스템 인터프리터)로
# 폴백하는데, 거기엔 matplotlib 이 없다(프로젝트 .venv 에만 있음). 그래서
# reports/investment_report.py 의 차트 생성 블록이 ModuleNotFoundError 로 조용히
# 실패(try/except 로 격리돼 있음) → PNG 가 2026-07-01 이후 **한 장도 생성되지 않았고**,
# crons/notion_sync.py 의 `_latest_chart_png()` 폴백이 가장 최신 파일을 집어
# **2026-06-30 차트를 7주간 매일 노션에 업로드**했다(사용자 체감: "노션이 갱신 안 됨").
# 다른 크론 52개는 모두 `uv run python`(=프로젝트 venv)을 쓰는데 이 스크립트만 예외였다.

def test_deliver_investment_report_uses_project_interpreter_not_bare_python3():
    """맨 python3 폴백 금지 — matplotlib/lightgbm 이 있는 프로젝트 인터프리터를 써야 한다."""
    script = Path("scripts/deliver_investment_report.sh").read_text(encoding="utf-8")
    assert 'PYTHON_BIN="${PYTHON_BIN:-python3}"' not in script, (
        "맨 python3 폴백은 matplotlib 부재로 차트 생성이 조용히 죽는다"
    )
    assert ".venv/bin/python" in script, "프로젝트 venv 인터프리터를 우선해야 함"


def test_resolved_interpreter_has_chart_dependencies():
    """스크립트가 고르는 인터프리터로 실제 matplotlib import 가 되는지 확인."""
    import re
    import subprocess

    script = Path("scripts/deliver_investment_report.sh").read_text(encoding="utf-8")
    # 스크립트의 PYTHON_BIN 결정 블록을 원문 그대로 떼어내 동일 조건으로 실행한다
    m = re.search(r'^if \[ -z "\$\{PYTHON_BIN:-\}" \]; then$.*?^fi$', script, re.M | re.S)
    assert m, "PYTHON_BIN 결정 블록을 찾지 못함"

    resolved = subprocess.run(
        ["bash", "-c", f'PROJECT_DIR="$(pwd)"; {m.group(0)}; echo "$PYTHON_BIN"'],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    assert resolved, "PYTHON_BIN 이 비어 있음"

    r = subprocess.run([resolved, "-c", "import matplotlib"], capture_output=True, timeout=60)
    assert r.returncode == 0, (
        f"선택된 인터프리터({resolved})에 matplotlib 없음 — 차트가 조용히 안 만들어진다: "
        f"{r.stderr.decode()[-200:]}"
    )
