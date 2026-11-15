"""데일리 PDF 리포트 — 모델 조립, 그림, 고정 양식 렌더, 인쇄 실패 처리."""
from __future__ import annotations

import io
import stat

import pandas as pd
import pytest

from reports import report_pdf, report_pdf_charts, report_pdf_data

REPORT_DATE = "2026-11-13"

INVESTMENT_MD = """# 투자 리포트

## 0. 오늘 한눈에

- Phase 2 · QQQ -10.1%
- **신호 분포:** 1 / 1 / 0 / 0

**오늘 결론**
- 하락 심화: 계획된 분할만
- 우선 확인: MSFT

**실행 우선순위**

| 우선 | 종목 | 액션 | 확인 포인트 |
|---|---|---|---|
| 매수관심 | Microsoft (MSFT) | 분할매수 검토 | 소량 분할매수만 검토 |

---

- **KOSPI:** N/A | **USD/KRW:** 1,334.55

💱 환전 타이밍 (원화→달러)
- 현재 1,334.6원 · 12%ile

## 1. 전체 요약

**주요 위험 신호:**

- NVDA: 1개월 -23.5% 하락

## LLM 애널리스트 코멘트

### 오늘의 해석

- SPY 약세 <b>확인</b> & 관망
"""

MARKET_MD = """# 시장 리포트

## 0. 오늘 요약

- 시장: 시장 혼조/보합권
- 섹터: 섹터: 강세 Energy +1.5%

## 5. 공포·탐욕 지수

- **추정 공포·탐욕 지수**: 75 - 탐욕 (Greed)

## 10. 경제 일정

**오늘**

- 🔴 11/13 09:00 · 10월 기존주택판매 ★★★

**향후 7일**

- 11/14 07:30 · 10월 PPI 상승률 ★★
"""


def _frame(end: str, sessions: int = 160, start_price: float = 100.0) -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=sessions)
    closes = [start_price + i * 0.5 for i in range(sessions)]
    return pd.DataFrame({"Close": closes, "Volume": [1_000_000 + i for i in range(sessions)]}, index=index)


def _data() -> dict:
    stock = {
        "ticker": "MSFT", "company_name": "Microsoft", "overall_signal": "Positive",
        "fundamental_score": 71, "fundamental_grade": "B",
        "decision_v2": {"action": "관심/분할매수", "today_action": "소량 진입 후 관찰",
                        "one_line_reason": "재무 71점(B)", "confidence": 82,
                        "financial": {"status": "양호"}},
        "decision_context": {"role": "성장", "recheck_triggers": ["재고 증가율 확인", "실적 가이던스"]},
        "holding_context": {"weight_pct": 60.0, "value_usd": 600.0, "cost_usd": 500.0,
                            "return_pct": 20.0, "avg_price_usd": 150.0},
        "earnings_context": {"per": 27.3, "forward_pe": 21.6, "roe": 0.342, "days_until": 5},
        "price_info": {"current_price": 179.0, "1d_change_pct": 0.4, "5d_change_pct": -2.4, "1mo_change_pct": -3.0},
        "reasons": ["재무 건강도 71점(B) — 양호"], "risks": ["재고 증가율 확인"],
    }
    fund = {
        "ticker": "QLD", "company_name": "ProShares Ultra QQQ", "overall_signal": "Neutral",
        "fundamental_score": 0, "fundamental_grade": "N/A",
        "decision_v2": {"action": "관심 유지", "today_action": "현 보유 유지"},
        "decision_context": {"role": "ETF", "recheck_triggers": ["ETF/ETN — 재무 분석 해당 없음"]},
        "holding_context": {"weight_pct": 40.0, "value_usd": 400.0, "cost_usd": 420.0, "return_pct": -4.8},
        "price_info": {"current_price": 81.3, "1d_change_pct": -0.7},
        "etf_comparison": {"peers": ["SPY", "QQQ"], "periods": [
            {"label": "1M", "tr_return_pct": -12.2, "vs": {"SPY": {"tr_diff_pct": -10.3}, "QQQ": {"tr_diff_pct": -6.7}}}]},
        "reasons": ["ETF/ETN — 재무 점수 불필요"], "risks": ["ETF/ETN — 재무 분석 해당 없음"],
    }
    scan = {"ticker": "AAPL", "company_name": "Apple", "total_score": 77, "grade": "A", "signal": "Positive",
            "decision_v2": {"action": "강한 매수후보"}}
    return {
        "date": REPORT_DATE, "generated_at": "2026-11-13T08:08:00",
        "market": {"spy_price": 757.12, "spy_change": -0.35, "qqq_price": 688.17, "qqq_change": -0.32},
        "portfolio": [fund, stock],
        "nasdaq_100_scan": {"all": [scan], "top_buy": [scan], "top_warning": []},
        "institutional_accumulation": [{"ticker": "KDP", "company": "Keurig Dr Pepper", "accum_score": 99,
                                        "verdict": "강한 매집", "signals": {"obv_norm": 0.33},
                                        "institutional": {"net_change": 0.044, "buyers": 6, "sellers": 3}}],
    }


def _model(**overrides) -> dict:
    kwargs = {
        "report_date": REPORT_DATE, "data": _data(), "investment_md": INVESTMENT_MD, "market_md": MARKET_MD,
        "prices": {s: _frame(REPORT_DATE) for s in ("SPY", "QQQ", "MSFT", "QLD")},
        "portfolio_history": [{"date": "2026-11-11", "total_usd": 990.0}, {"date": "2026-11-12", "total_usd": 1000.0,
                                                                          "total_krw": 1_330_000, "phase": 2}],
    }
    kwargs.update(overrides)
    return report_pdf_data.build_model(**kwargs)


def test_price_history_stops_before_report_date():
    # 리포트는 KST 아침에 나온다 — 그 날짜의 봉이 섞이면 과거 날짜 재생성 값이 달라진다.
    frame = _frame(REPORT_DATE)
    clipped = report_pdf_data.clip_history({"SPY": frame}, REPORT_DATE)["SPY"]

    assert clipped.index[-1].date().isoformat() == "2026-11-12"
    assert len(clipped) == len(frame) - 1


def test_model_reads_text_blocks_from_generated_markdown():
    model = _model()

    assert model["headline"] == "하락 심화: 계획된 분할만"
    assert model["conclusion"] == ["우선 확인: MSFT"]
    assert model["glance"] == ["Phase 2 · QQQ -10.1%"]
    assert model["priorities"][1] == ["매수관심", "Microsoft (MSFT)", "분할매수 검토", "소량 분할매수만 검토"]
    assert model["fx"] == ["현재 1,334.6원 · 12%ile"]
    assert model["risk_flags"] == ["NVDA: 1개월 -23.5% 하락"]
    assert model["market"]["summary"] == ["시장: 시장 혼조/보합권", "섹터: 강세 Energy +1.5%"]
    assert model["market"]["fear_greed"] == {"value": 75, "label": "탐욕 (Greed)"}
    assert model["market"]["calendar"] == [
        {"group": "오늘", "date": "11/13", "time": "09:00", "event": "10월 기존주택판매", "importance": 3},
        {"group": "예정", "date": "11/14", "time": "07:30", "event": "10월 PPI 상승률", "importance": 2},
    ]


def test_model_orders_holdings_by_weight_and_separates_funds():
    model = _model()
    stock, fund = model["portfolio"]["holdings"]

    assert (stock["ticker"], fund["ticker"]) == ("MSFT", "QLD")
    assert stock["roe_pct"] == pytest.approx(34.2)
    assert stock["triggers"] == ["실적 가이던스"]          # 리스크와 같은 문장은 되풀이하지 않는다
    assert fund["score"] is None and fund["grade"] == "ETF"
    assert fund["reasons"] == [] and fund["risks"] == []   # 'ETF/ETN — …' 자리 채움 문구 제거
    assert fund["fund_compare"] == {"peers": ["SPY", "QQQ"], "rows": [{"label": "1M", "ret": -12.2,
                                                                      "diffs": [-10.3, -6.7]}]}
    assert model["portfolio"]["value_usd"] == 1000.0
    assert model["portfolio"]["pnl_pct"] == pytest.approx((1000 / 920 - 1) * 100)
    assert model["portfolio"]["weighted_d1"] == pytest.approx(0.6 * 0.4 + 0.4 * -0.7)
    assert model["portfolio"]["total_krw"] == 1_330_000


def test_html_renders_fixed_sections_and_escapes_report_text():
    html = report_pdf.render_html(_model())

    for title in ("DAILY INVESTMENT BRIEF", "시장 동향", "포트폴리오 현황", "종목별 점검", "유니버스 스크리닝",
                  "Compliance Notice"):
        assert title in html
    assert html.count('<article class="stock">') == 2
    assert "data:image/svg+xml;base64," in html
    assert "SPY 약세 확인 &amp; 관망" in html          # 마크다운 속 태그는 걷어내고 & 는 이스케이프
    assert "<script" not in html


def test_html_renders_without_prices_markdown_or_history():
    # 시세 조회·시장 리포트·히스토리가 모두 빠진 날에도 표와 판단은 발행한다.
    model = _model(investment_md="", market_md="", prices={}, portfolio_history=[])
    html = report_pdf.render_html(model)

    assert model["headline"] == "오늘의 포트폴리오·시장 점검"
    assert [r["label"] for r in model["market"]["indices"]] == ["S&P 500 (SPY)", "NASDAQ 100 (QQQ)"]
    assert "가격 이력 없음" in html
    assert html.count('<article class="stock">') == 2
    assert "소량 진입 후 관찰" in html


def test_charts_return_none_without_data():
    assert report_pdf_charts.bar_chart([]) is None
    assert report_pdf_charts.bar_chart([("MSFT", None)]) is None
    assert report_pdf_charts.donut_chart([("MSFT", 0.0)]) is None
    assert report_pdf_charts.equity_chart([{"date": "2026-11-12", "total_usd": 1000.0}]) is None
    assert report_pdf_charts.histogram_chart([]) is None
    assert report_pdf_charts.price_chart(_frame(REPORT_DATE, sessions=1)) is None


def test_basket_series_starts_at_100_and_follows_weights():
    prices = {"A": _frame(REPORT_DATE, start_price=100.0), "B": _frame(REPORT_DATE, start_price=50.0)}
    index, basket = report_pdf_charts.basket_series(prices, {"A": 75.0, "B": 25.0, "MISSING": 10.0},
                                                    sessions=10, calendar="A")

    a = prices["A"]["Close"].iloc[-11:]
    b = prices["B"]["Close"].iloc[-11:]
    expected = 100 * (0.75 * a.iloc[-1] / a.iloc[0] + 0.25 * b.iloc[-1] / b.iloc[0])
    assert len(index) == len(basket) == 11
    assert basket[0] == pytest.approx(100.0)
    assert basket[-1] == pytest.approx(expected)


def test_print_failure_raises_and_leaves_no_partial_file(tmp_path, monkeypatch):
    out = tmp_path / "brief.pdf"
    monkeypatch.setattr(report_pdf, "find_browser", lambda: None)
    with pytest.raises(RuntimeError, match="Chromium"):
        report_pdf.html_to_pdf("<html></html>", out)

    silent = tmp_path / "fake-browser"          # 0 으로 끝나지만 PDF 를 쓰지 않는 브라우저
    silent.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    silent.chmod(silent.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(RuntimeError, match="PDF 인쇄 실패"):
        report_pdf.html_to_pdf("<html></html>", out, browser=str(silent))

    assert not out.exists()
    assert list(tmp_path.iterdir()) == [silent]     # 작업 폴더가 남지 않는다


def test_cli_fails_without_report_data(tmp_path, capsys):
    assert report_pdf.main(["--date", REPORT_DATE, "--reports-dir", str(tmp_path)]) == 1
    assert "PDF 리포트 생성 실패" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.pdf"))


@pytest.mark.skipif(report_pdf.find_browser() is None, reason="Chromium 계열 브라우저 없음")
def test_build_pdf_prints_korean_text_on_a4(tmp_path):
    import json

    from pypdf import PdfReader

    (tmp_path / f"investment-data-{REPORT_DATE}.json").write_text(json.dumps(_data()), encoding="utf-8")
    (tmp_path / f"investment-report-{REPORT_DATE}.md").write_text(INVESTMENT_MD, encoding="utf-8")
    prices = {s: _frame(REPORT_DATE) for s in ("SPY", "QQQ", "MSFT", "QLD")}

    out = report_pdf.build_pdf(REPORT_DATE, tmp_path, tmp_path / "brief.pdf",
                               fetch=lambda symbols: prices, history_loader=list)

    reader = PdfReader(io.BytesIO(out.read_bytes()))
    text = "\n".join(page.extract_text() for page in reader.pages)
    width, height = (float(v) for v in reader.pages[0].mediabox[2:])
    assert (round(width), round(height)) == (595, 842)
    assert "하락 심화: 계획된 분할만" in text
    assert "MSFT" in text and "종목별 점검" in text
    assert "$179.00" in text      # 숫자도 복사·검색된다(글리프 대체 기능을 켜면 사설 영역 문자로 뽑힌다)
