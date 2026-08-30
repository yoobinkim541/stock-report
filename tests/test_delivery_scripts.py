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


def test_daily_investment_report_has_one_scheduler_owner():
    """Hermes가 일일 발송을 소유할 때 OS crontab은 같은 작업을 재실행하지 않는다."""
    cron = Path("deploy/crontab.stock-report").read_text(encoding="utf-8")
    active_lines = [
        line.strip()
        for line in cron.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    repo_delivery_lines = [
        line for line in active_lines
        if "scripts/deliver_investment_report.sh" in line
    ]
    assert repo_delivery_lines == [], (
        "Hermes stock-investment-report와 OS crontab이 일일 리포트를 중복 발송하지 않아야 함"
    )
    assert "stock-investment-report" in cron


def test_investment_delivery_is_single_flight_and_idempotent_per_day():
    """남아 있는 구형 OS cron과 Hermes가 겹쳐도 같은 보고서를 두 번 보내지 않는다."""
    script = Path("scripts/deliver_investment_report.sh").read_text(encoding="utf-8")

    assert "INVESTMENT_REPORT_DELIVERY_LOCK" in script
    assert "investment-report-delivery.lock" in script
    assert "investment-report.lock}" not in script
    assert "flock -n" in script
    assert "INVESTMENT_REPORT_FORCE_DELIVERY" in script
    assert 'investment-report-${DATE}.sent' in script


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


def test_cloudflared_restart_notifications_are_throttled():
    """터널이 불안정해도 매 재기동마다 Telegram을 보내지 않는다."""
    script = Path("scripts/cloudflared_watchdog.sh").read_text(encoding="utf-8")

    assert "CLOUDFLARED_NOTIFY_COOLDOWN_SECONDS" in script
    assert "cloudflared_notify.state" in script
    assert "should_notify_tunnel_restart" in script


def test_cloudflared_watchdog_has_probe_grace_before_killing_tunnel():
    """일시적인 외부 probe 실패가 살아 있는 터널 재기동으로 번지지 않는다."""
    script = Path("scripts/cloudflared_watchdog.sh").read_text(encoding="utf-8")

    assert "CLOUDFLARED_PROBE_RETRIES" in script
    assert "CLOUDFLARED_PROBE_RETRY_DELAY" in script
    assert "probe_tunnel_with_retries" in script
    assert "CLOUDFLARED_STARTUP_GRACE_SECONDS" in script


def test_cloudflared_watchdog_does_not_reannounce_already_pushed_gateway():
    """detached worktree push 후 원래 checkout이 낡아도 매 분 URL 변경으로 오판하지 않는다."""
    script = Path("scripts/cloudflared_watchdog.sh").read_text(encoding="utf-8")

    assert "LANDING_STATE_FILE" in script
    assert "dashboard_tunnel_landing_url.txt" in script
    assert "write_landing_state" in script
