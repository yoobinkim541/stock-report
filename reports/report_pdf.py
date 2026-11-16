"""reports/report_pdf.py — 통합 데일리 리포트를 증권사 양식 PDF 로 만든다.

    python -m reports.report_pdf --date 2026-11-13 [--reports-dir ~/reports] [--out FILE]

흐름: 이미 생성된 산출물(investment-data JSON, 투자·시장 마크다운) → 모델(report_pdf_data)
→ 그림(report_pdf_charts, SVG) → 고정 HTML 양식(templates/daily_report.html.j2)
→ 헤드리스 Chromium 인쇄. 양식과 그림은 코드가 정하고, 언어모델은 관여하지 않는다 —
그래서 매일 같은 자리, 같은 모양으로 나오고 숫자가 바뀔 여지가 없다.

PDF 를 만들지 못하면 0 이 아닌 코드로 끝난다. 발송 스크립트는 그때 마크다운을 그대로 보낸다.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reports import account_performance
from reports import report_pdf_charts as charts
from reports import report_pdf_data as pdf_data

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
RENDER_TIMEOUT_SECONDS = 120
RELATIVE_SESSIONS = 63      # 표지의 계좌·벤치마크 비교 구간(3개월)


# ── 템플릿 필터 ──────────────────────────────────────────────────────────────

def _fmt_num(value, digits: int = 2) -> str:
    return "–" if value is None else f"{float(value):,.{digits}f}"


def _fmt_price(value) -> str:
    """지수·가격 칸. 만 단위부터는 소수 자리를 뺀다(좁은 표에서 줄바꿈 방지)."""
    if value is None:
        return "–"
    return f"{float(value):,.0f}" if abs(float(value)) >= 10000 else f"{float(value):,.2f}"


def _fmt_pct(value, digits: int = 2) -> str:
    return "–" if value is None else f"{float(value):+.{digits}f}%"


def _fmt_money(value, unit: str = "$") -> str:
    if value is None:
        return "–"
    if unit == "$":
        return f"${float(value):,.2f}"
    if unit == "원":
        return f"{float(value):,.1f}원"
    return f"{float(value):,.2f}"


def _fmt_multiple(value) -> str:
    return "–" if value is None else f"{float(value):,.1f}x"


def _tone(value) -> str:
    if value is None or abs(float(value)) < 0.005:
        return "flat"
    return "up" if float(value) > 0 else "down"


def _bar_width(value, scale: float = 5.0) -> str:
    """표 안 막대의 너비(%). 0 기준선에서 좌우로 뻗으므로 최대 50."""
    if value is None or not scale:
        return "0"
    return f"{min(abs(float(value)) / float(scale), 1.0) * 50.0:.1f}"


def _sparkline(values, width: float = 60.0, height: float = 14.0):
    """표 안에 넣는 추이선(인라인 SVG). 값이 모자라면 빈 문자열."""
    from markupsafe import Markup

    points = [float(v) for v in values or [] if v is not None]
    if len(points) < 5:
        return Markup("")
    low, high = min(points), max(points)
    span = (high - low) or 1.0
    step = width / (len(points) - 1)
    path = " ".join(f"{i * step:.1f},{1.0 + (height - 2.0) * (1.0 - (v - low) / span):.1f}"
                    for i, v in enumerate(points))
    return Markup(f'<svg class="spark {_tone(points[-1] - points[0])}" viewBox="0 0 {width:g} {height:g}" '
                  f'preserveAspectRatio="none"><polyline points="{path}"/></svg>')


def _environment():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True,
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(num=_fmt_num, price=_fmt_price, pct=_fmt_pct, money=_fmt_money, mult=_fmt_multiple, tone=_tone,
                       barw=_bar_width, spark=_sparkline)
    return env


# ── 그림 ─────────────────────────────────────────────────────────────────────

def build_figures(model: dict) -> dict:
    """모델에서 그림을 만든다. 데이터가 없는 그림은 None — 템플릿이 자리를 접는다."""
    prices = model.get("prices") or {}
    holdings = model["portfolio"]["holdings"]
    account = model["portfolio"]["account"]

    relative = equity = None
    if account:
        index = account["index"][-RELATIVE_SESSIONS - 1:]
        relative = charts.relative_chart(
            [("계좌", account["twr"][-len(index):]),
             *[(symbol, pdf_data.aligned(prices, symbol, index)) for symbol in pdf_data.BENCHMARKS]],
            index, size=(118, 42))
        equity = charts.equity_chart(account["index"], account["value"], account["net_invested"], size=(88, 44))

    minis = []
    for title, symbol, digits in pdf_data.MINI_CHARTS:
        src = charts.line_chart(prices[symbol], digits=digits) if symbol in prices else None
        if src:
            minis.append({"title": title, "src": src})
    return {
        "relative": relative,
        "minis": minis,
        "equity": equity,
        "spy": charts.price_chart(prices["SPY"], averages=(20, 60, 120), size=(88, 42)) if "SPY" in prices else None,
        "qqq": charts.price_chart(prices["QQQ"], averages=(20, 60, 120), size=(88, 42)) if "QQQ" in prices else None,
        "allocation": charts.donut_chart(
            [(h["ticker"], h["weight"]) for h in holdings],
            center=("평가액", f"${model['portfolio']['value_usd']:,.0f}") if model["portfolio"]["value_usd"] else None,
            size=(88, 44)),
        "scores": charts.histogram_chart(model["screens"]["nasdaq_scores"]),
        "accumulation": charts.bar_chart(
            [(a["ticker"], a["score"]) for a in model["accumulation"][:8]], limit=100, size=(88, 46)),
        "stocks": {
            h["ticker"]: (charts.price_chart(prices[h["ticker"]], size=(66, 33),
                                             reference=("평균단가", h["avg_price"]) if h["avg_price"] else None)
                          if h["ticker"] in prices else None)
            for h in holdings
        },
    }


def render_html(model: dict, figures: dict | None = None) -> str:
    figures = build_figures(model) if figures is None else figures
    return _environment().get_template("daily_report.html.j2").render(
        m=model, f=figures, date_label=model["date_label"], font_base=charts.FONT_DIR.as_uri())


# ── 인쇄 ─────────────────────────────────────────────────────────────────────

def find_browser() -> str | None:
    """인쇄에 쓸 Chromium 계열 실행 파일. REPORT_PDF_BROWSER → Playwright 번들 → 시스템 브라우저."""
    override = os.getenv("REPORT_PDF_BROWSER")
    if override:
        return override if os.access(override, os.X_OK) else None
    cache = Path(os.getenv("PLAYWRIGHT_BROWSERS_PATH") or "~/.cache/ms-playwright").expanduser()
    bundled = {}
    for path in cache.glob("chromium_headless_shell-*/chrome-*/headless_shell"):
        revision = path.parts[-3].rsplit("-", 1)[-1]
        if revision.isdigit() and os.access(path, os.X_OK):
            bundled[int(revision)] = str(path)
    if bundled:
        return bundled[max(bundled)]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def html_to_pdf(html: str, out_path: Path, *, browser: str | None = None,
                timeout: int = RENDER_TIMEOUT_SECONDS) -> Path:
    """HTML 을 A4 PDF 로 인쇄한다. 쪽번호·꼬리말은 CSS @page 가 그린다. 실패하면 RuntimeError."""
    browser = browser or find_browser()
    if not browser:
        raise RuntimeError("Chromium 실행 파일을 찾지 못했습니다 (REPORT_PDF_BROWSER 로 지정)")
    out_path = Path(out_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # snap Chromium 은 /tmp 와 숨김 폴더를 읽지 못하므로 작업 폴더를 결과 파일 옆에 둔다.
    with tempfile.TemporaryDirectory(prefix="report-pdf-", dir=out_path.parent) as work:
        source = Path(work) / "report.html"
        target = Path(work) / "report.pdf"
        source.write_text(html, encoding="utf-8")
        # 입력은 이 프로세스가 만든 로컬 파일 하나뿐이고, 양식의 CSP 가 스크립트·원격 자원을 막는다.
        # 샌드박스는 서버 커널의 비특권 userns 제한 때문에 켜면 뜨지 않는다 — Playwright 기본값과 같다.
        cmd = [browser, "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
               "--no-pdf-header-footer",
               f"--user-data-dir={Path(work) / 'profile'}", f"--print-to-pdf={target}", source.as_uri()]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        try:
            _out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            raise RuntimeError(f"PDF 인쇄가 {timeout}초 안에 끝나지 않았습니다") from None
        if proc.returncode != 0 or not target.exists() or target.stat().st_size < 1024:
            detail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            raise RuntimeError(f"PDF 인쇄 실패 (rc={proc.returncode}): {detail[0][:200]}")
        os.replace(target, out_path)
    return out_path


def _kill_group(proc: subprocess.Popen) -> None:
    import signal

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def _portfolio_history() -> list[dict]:
    try:
        import portfolio_tracker
        return list(portfolio_tracker.load_history() or [])
    except Exception as exc:   # 히스토리가 없어도 나머지 리포트는 만든다.
        print(f"[WARN] 포트폴리오 히스토리 없음: {type(exc).__name__}", file=sys.stderr)
        return []


def _transactions() -> list[dict]:
    try:
        return account_performance.load_transactions()
    except Exception as exc:   # 거래내역이 없으면 계좌 수익률·평가액 추이만 빠진다.
        print(f"[WARN] 거래내역 없음: {type(exc).__name__}", file=sys.stderr)
        return []


def build_pdf(report_date: str, reports_dir: Path, out_path: Path, *, fetch=pdf_data.fetch_price_history,
              history_loader=_portfolio_history, transactions_loader=_transactions,
              browser: str | None = None) -> Path:
    data, investment_md, market_md = pdf_data.load_inputs(report_date, reports_dir)
    transactions = transactions_loader()
    try:
        prices = fetch(pdf_data.history_symbols(data, transactions))
    except Exception as exc:   # 시세 조회가 막혀도 표·문장만으로 발행한다.
        print(f"[WARN] 가격 이력 조회 실패, 그림 없이 발행: {type(exc).__name__}", file=sys.stderr)
        prices = {}
    model = pdf_data.build_model(report_date=report_date, data=data, investment_md=investment_md,
                                 market_md=market_md, prices=prices, portfolio_history=history_loader(),
                                 transactions=transactions)
    basis = model["price_basis"]
    if basis["reused"]:
        print(f"[WARN] 시세 {basis['reused']}개 심볼은 {basis['fetched']} 캐시 사용", file=sys.stderr)
    return html_to_pdf(render_html(model), out_path, browser=browser)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="통합 데일리 리포트 PDF 생성")
    parser.add_argument("--date", required=True, help="리포트 날짜 (YYYY-MM-DD, KST)")
    parser.add_argument("--reports-dir", default="~/reports")
    parser.add_argument("--out", help="기본: <reports-dir>/daily-investment-brief-<date>.pdf")
    args = parser.parse_args(argv)
    reports_dir = Path(args.reports_dir).expanduser()
    out_path = Path(args.out).expanduser() if args.out else reports_dir / f"daily-investment-brief-{args.date}.pdf"
    try:
        path = build_pdf(args.date, reports_dir, out_path)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[FAIL] PDF 리포트 생성 실패: {exc}", file=sys.stderr)
        return 1
    print(f"pdf report: {path} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
