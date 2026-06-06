# LLM 데이터 수집 & 프롬프트 구조 분석 리포트

> 작성일: 2026-06-05 | 실데이터 기반 | stock-report 프로젝트

---

## 1. 데이터 소스 맵

### 1-1. barbell_strategy.py — 시장 + 포트폴리오

| 함수 | 수집 항목 | 출력 형식 | 실측 크기 |
|------|----------|----------|----------|
| `fetch_qqq_data()` | current, high_52w, low_52w, drawdown_pct, position_52w_pct, mom_1m_pct, mom_3m_pct | dict | ~148자 |
| `fetch_rsi("QQQ")` | RSI (14일, Wilder smoothing) | float | 4자 |
| `fetch_vix()` | VIX 현재값 | float | 5자 |
| `fetch_fear_greed()` | score, rating, prev_close, prev_week, prev_month | dict | ~100자 |
| `fetch_ma200("QQQ")` | above_ma200, ma200, current, gap_pct | dict | ~70자 |
| `fetch_exchange_rate()` | USD/KRW 환율 | float | 6자 |
| `classify_market()` | market_type (bull/neutral/bear), phase_key (0~5, bull_1, bull_2) | tuple | — |
| `fetch_portfolio_value()` | total_usd, cost_usd, pnl_usd, return_pct, sgov_usd, qqqi_usd, prices, holdings | dict | ~500자 |
| `calculate_dca()` | total_krw, total_usd, multiplier, by_ticker | dict | ~300자 |
| `calculate_sgov_target()` | target_pct, target_usd, current_usd, diff_usd, action, direction | dict | ~200자 |
| `estimate_qqqi_monthly_dividend()` | monthly_usd, annual_yield_pct, per_share, note | dict | ~100자 |
| `calculate_position_analysis()` | 종목별 current_pct, target_pct, diff_pct, pnl, action | list[dict] | ~1,200자 |
| `calculate_safety_margin()` | score, grade, emoji, factors, multiplier | dict | ~400자 |

**2026-06-05 실측 시장 데이터:**
```json
{
  "qqq": { "current": 726.43, "high_52w": 748.65, "drawdown_pct": -2.97,
           "position_52w_pct": 90.3, "mom_1m_pct": 4.53, "mom_3m_pct": 19.68 },
  "rsi": 61.9, "vix": 16.77, "fx": 1554.9,
  "fg": { "score": 49.9, "rating": "neutral", "prev_week": 59.5 },
  "ma": { "above_ma200": true, "ma200": 620.96, "gap_pct": 16.99 },
  "market_type": "neutral", "phase_key": "0"
}
```

---

### 1-2. investment_report.py — 종목 분석 + 스캔

| 함수/데이터 | 수집 항목 | 실측 크기 |
|------------|----------|----------|
| `score_ticker(ticker)` (fundamental_score.py) | total_score(0~100), grade(A~D), sections별 세부점수, notes | 종목당 ~500자 |
| `detect_signals(ticker)` (daily_signals.py) | overall_signal, signals_found, warnings, critical, price_info, volume_info, news_items, analyst_info | 종목당 ~600자 |
| `_decision_v2()` | action, one_line_reason, confidence, financial, timing, news, risk | 종목당 ~300자 |
| `_build_etf_comparison()` | expense_ratio, peers, periods별 TR/PR return, diff vs benchmark | 종목당 ~800자 |
| portfolio 12종목 결과 | ticker, judgment, fundamental, signal, decision_v2, reasons, risks, etf_comparison | **12개 × ~1,137자 = 13,644자** |
| NASDAQ 100 스캔 결과 | ticker, total_score, grade, company_name, signal, decision_v2 | **top5: 2,892자 / 전체100: ~57,997자** |
| KOSPI Top30 스캔 결과 | ticker, total_score, grade, company_name, signal, decision_v2 | **top5: 2,923자 / 전체30: ~17,582자** |
| `_fetch_arca_posts()` | id, url, category, title, author, when, views, likes | 글당 ~200자, 최대 6건 |
| `_build_llm_overlay_prompt()` | market_summary + portfolio[:8] + top3/warn3 + source_digest(1000자) | **실측 ~3,850자** |

**2026-06-04 investment-data.json 구조별 크기:**
```
전체 파일:       109,490자  (~27,372 토큰)
portfolio (12개): 13,644자  (~3,411 토큰)  — 종목당 1,137자
nasdaq_100_scan:
  - all (100개):  57,997자  (~14,499 토큰)
  - top_buy (5):   2,892자  (~  723 토큰)
  - top_warning:   3,052자  (~  763 토큰)
kospi_top30_scan:
  - all (30개):   17,582자  (~ 4,396 토큰)
  - top_buy (5):   2,923자  (~  731 토큰)
  - top_warning:   3,079자  (~  770 토큰)
```

---

### 1-3. source_collector.py — 뉴스 + 매크로

| 함수 | 소스 | 수집 항목 | 2026-06-05 실측 |
|------|------|----------|----------------|
| `fetch_saveticker_events()` | SaveTicker API | title, url, published_at, tickers, tags | **605건** |
| `fetch_arca_events()` | Arca Live 주식 채널 | title, url, category, tickers | **5건** |
| `fetch_telegram_channel_events()` | yuzukinaok1 텔레그램 | title, url, tickers, tags | **372건** |
| `fetch_market_snapshot_events()` | Yahoo Finance (50 티커) | current, return_1d/5d/1m/1y | **50건** |
| `fetch_fred_macro_events()` | FRED (12 시리즈) | series_id, current, delta | **12건** |
| `fetch_world_gov_bond_events()` | WorldGovernmentBonds | country, maturity, yield_pct | **12건** |
| `build_digest()` | 캐시에서 집계 | 소스별 건수, 반복 종목/테마, 최신 12건 요약 | **~600자** |

**2026-06-05 source-cache 실측:**
```
파일: events-2026-06-05.jsonl
총 이벤트: 1,044개
전체 크기: 284,358자  (~71,090 토큰)

소스별:
  saveticker:          605건 (이벤트당 ~279자)
  telegram:yuzukinaok1: 372건
  yahoo_finance:         50건 (이벤트당 ~447자)
  worldgovernmentbonds:  12건 (이벤트당 ~402자)
  arca:                   5건 (이벤트당 ~299자)
```

---

### 1-4. portfolio_tracker.py + holding_manager.py — 히스토리 + 포지션

| 데이터 | 경로 | 실측 크기 |
|--------|------|----------|
| `portfolio_snapshot.json` | `~/projects/stock-report/` | **4,071자** (해외일반11+소수점6+국내1) |
| `portfolio_history.json` | `~/.local/share/stock-report/` | 1,494자 (6개 레코드) |
| `qqqi_dividends.json` | `~/.local/share/stock-report/` | 150자 (1건) |
| `tax_records.json` | `~/.local/share/stock-report/` | 2자 (빈 배열) |
| `barbell_state.json` | `~/.cache/` | ~100자 (Phase 캐시) |
| `dca_weights.json` | `~/projects/stock-report/` | ~300자 |
| `leverage_state.json` | `~/projects/stock-report/` | ~100자 |

**portfolio_snapshot.json 섹션별:**
```
snapshot_date:           12자
overseas_general (11):  2,195자
overseas_fractional (6): 1,001자
domestic (1):             283자
portfolio_analysis:       436자
```

---

## 2. LLM Prompt 템플릿

### 전체 프롬프트 구조

```
[시스템 지시문] + [A. 시장현황 JSON] + [B. 포트폴리오 JSON]
+ [C. 스캔 JSON] + [D. 소스 이벤트] + [E. 히스토리 JSON]
+ [출력 형식 지시]
```

---

### A. 시장현황 (Market Context)

```json
{
  "section": "market_context",
  "date": "2026-06-05",
  "phase": {
    "emoji": "🟢",
    "label": "Phase 0 — 정상 모드",
    "market_type": "neutral",
    "phase_key": "0",
    "dca_multiplier": 1.0,
    "description": "정상 DCA 유지. 변화 없음."
  },
  "qqq": {
    "current": 726.43,
    "high_52w": 748.65,
    "low_52w": 520.14,
    "drawdown_pct": -2.97,
    "position_52w_pct": 90.3,
    "mom_1m_pct": 4.53,
    "mom_3m_pct": 19.68
  },
  "indicators": {
    "rsi": 61.9,
    "vix": 16.77,
    "ma200_gap_pct": 16.99,
    "above_ma200": true
  },
  "fear_greed": {
    "score": 49.9,
    "rating": "neutral",
    "prev_close": 54.7,
    "prev_week": 59.5,
    "trend": "하락 (1주 -9.6)"
  },
  "fx": {
    "usd_krw": 1554.9
  },
  "dca_today": {
    "total_krw": 40000,
    "total_usd": 25.72,
    "multiplier": 1.0
  }
}
```

**한국어 지시문:**
> 위 시장 현황 데이터를 바탕으로, Phase가 의미하는 전략적 포지션과 주요 기술 지표(RSI, VIX, F&G)의 신호를 해석하라. QQQ 모멘텀 방향과 200일 MA 위치를 함께 평가하라. 모든 수치는 입력값에서만 인용할 것.

---

### B. 포트폴리오 현황 (Portfolio)

```json
{
  "section": "portfolio",
  "snapshot_date": "2026-06-05",
  "summary": {
    "total_usd": 28886.74,
    "total_krw": 44170714,
    "cost_usd": 24500.0,
    "pnl_usd": 4386.74,
    "return_pct": 17.9,
    "sgov_usd": 1004.10,
    "sgov_pct": 3.5,
    "sgov_target_pct": 8.0,
    "qqqi_usd": 2032.85,
    "qqqi_pct": 7.0
  },
  "holdings": [
    {
      "ticker": "QQQI",
      "name": "나스닥100 고배당 네오스 ETF",
      "shares": 35.4069,
      "avg_price_usd": 53.45,
      "current_price_usd": 56.34,
      "value_usd": 1994.82,
      "pnl_usd": 102.29,
      "return_pct": 5.41,
      "signal": "Neutral",
      "action": "인컴 유지",
      "current_weight_pct": 6.9,
      "target_weight_pct": 0.0
    },
    {
      "ticker": "ORCL",
      "name": "오라클",
      "shares": 6.3112,
      "avg_price_usd": 180.83,
      "current_price_usd": 221.72,
      "value_usd": 1399.32,
      "pnl_usd": 258.06,
      "return_pct": 22.61,
      "signal": "Positive",
      "action": "관심 유지",
      "current_weight_pct": 4.8,
      "target_weight_pct": 7.0
    }
    /* ... 나머지 10개 종목 ... */
  ],
  "dca_allocation": {
    "NOW":  7200, "ORCL": 7200, "NVDA": 5600,
    "MSFT": 5600, "GOOGL": 4000, "UNH": 4000,
    "CRM":  4000, "SAP": 1200,  "SPMO": 1200
  },
  "sgov_action": "SGOV 매수 필요: +$1,286 (목표 $3,111)",
  "qqqi_dividend": {
    "monthly_usd": 22.15,
    "annual_yield_pct": 12.0,
    "action": "배당 전액 → 소수점 DCA 재투자"
  }
}
```

**한국어 지시문:**
> 포트폴리오 현황을 종목별로 분석하라. SGOV 실탄 부족 여부, 특정 종목 비중 과/부족을 진단하고, DCA 배분의 우선순위를 제시하라. 개별 종목의 P&L과 target_weight 대비 차이를 기준으로 행동 우선순위를 정렬하라.

---

### C. 스캔 결과 (Market Scan)

```json
{
  "section": "market_scan",
  "date": "2026-06-05",
  "nasdaq_top_buy": [
    {
      "ticker": "NVDA",
      "company_name": "NVIDIA",
      "total_score": 88,
      "grade": "A",
      "signal": "Positive",
      "decision_v2": {
        "action": "강한 매수후보",
        "one_line_reason": "재무 88점(A) · 일일 신호 긍정",
        "confidence": 80
      }
    }
    /* ... top5 ... */
  ],
  "nasdaq_warnings": [
    {
      "ticker": "INTC",
      "company_name": "Intel",
      "total_score": 35,
      "grade": "D",
      "signal": "Warning",
      "decision_v2": { "action": "손절/매도검토", "one_line_reason": "재무 35점(D) · 경고 신호" }
    }
    /* ... warn5 ... */
  ],
  "kospi_top_buy": [
    {
      "ticker": "005930.KS",
      "company_name": "삼성전자",
      "total_score": 72,
      "grade": "B",
      "signal": "Neutral",
      "decision_v2": { "action": "관심 유지", "one_line_reason": "재무 72점(B)" }
    }
    /* ... top5 ... */
  ],
  "kospi_warnings": [ /* ... warn5 ... */ ],
  "etf_comparison": {
    "QQQI": {
      "expense_ratio": 0.68,
      "peers": ["JEPQ", "QYLD", "QQQ"],
      "1Y_return_pct": 14.2,
      "vs_JEPQ_diff": 2.1,
      "vs_QQQ_diff": -18.5
    },
    "SGOV": {
      "expense_ratio": 0.09,
      "1Y_return_pct": 5.2,
      "vs_BIL_diff": 0.1
    }
  }
}
```

**한국어 지시문:**
> 스캔 결과에서 포트폴리오 편입 후보(강한 매수후보, 분할매수 후보)와 경고 종목을 분리하라. ETF 비교에서 QQQI의 동종 대비 포지션을 평가하라. 점수와 신호 외 새로운 판단을 추가하지 말 것.

---

### D. 뉴스/커뮤니티 센티먼트 (Source Events)

```json
{
  "section": "source_events",
  "date": "2026-06-05",
  "digest_summary": {
    "total_events": 1044,
    "sources": {
      "saveticker": 605,
      "telegram": 372,
      "yahoo_finance": 50,
      "worldgovernmentbonds": 12,
      "arca": 5
    },
    "top_tickers_mentioned": ["NVDA", "MSFT", "TSMC", "AVGO"],
    "top_themes": ["금리/채권", "기술/AI", "중동/전쟁", "정책/재정"]
  },
  "portfolio_related_events": [
    {
      "source": "saveticker",
      "title": "[내용 추가] TSMC "칩 가격 인상 검토"",
      "tickers": ["NVDA", "MSFT"],
      "tags": ["기술/AI"],
      "published_at": "2026-06-04T16:30:51+09:00"
    },
    {
      "source": "saveticker",
      "title": "[종합 기사] 브로드컴, AI 칩 매출 전망 시장 기대 하회…",
      "tickers": ["NVDA"],
      "tags": ["기술/AI"],
      "published_at": "2026-06-04T08:03:49+09:00"
    }
  ],
  "macro_events": [
    {
      "source": "worldgovernmentbonds",
      "title": "미국 국채금리 10Y: 4.395%",
      "tags": ["금리/채권"]
    },
    {
      "source": "fred",
      "title": "DFF Fed Funds 실효금리: 2026-05-28 4.33, 직전 대비 -0.01p"
    }
  ],
  "arca_top_posts": [
    {
      "source": "arca",
      "category": "🧠분석",
      "title": "로마 시대의 지중해 무역 - 3장. 풍요로운 식탁",
      "url": "https://arca.live/b/stock/172799906"
    }
  ]
}
```

**한국어 지시문:**
> 포트폴리오 보유 종목 관련 이벤트를 우선 해석하라. 매크로(금리, 환율) 방향이 SGOV/QQQ 전략에 미치는 영향을 평가하라. 뉴스 제목 외 내용 추론 금지. 포트폴리오 외 종목 언급 금지.

---

### E. 히스토리/성과 (History & Tax)

```json
{
  "section": "history_performance",
  "date": "2026-06-05",
  "performance": {
    "current_usd": 28886.74,
    "current_krw": 44170714,
    "ret_1d_pct": -1.07,
    "ret_7d_pct": 2.3,
    "ret_30d_pct": 8.1,
    "ret_90d_pct": null,
    "peak_usd": 29201.81,
    "peak_date": "2026-06-03",
    "drawdown_from_peak_pct": -1.08,
    "tracking_days": 6
  },
  "dividend": {
    "total_usd": 22.15,
    "count": 1,
    "last": {
      "date": "2026-05-31",
      "amount_usd": 22.15,
      "reinvested_in": "ORCL"
    }
  },
  "tax": {
    "year": 2026,
    "total_gain_krw": 0,
    "taxable_krw": 0,
    "tax_krw": 0,
    "count": 0,
    "exemption_krw": 2500000
  }
}
```

**한국어 지시문:**
> 단기 성과 추이와 고점 대비 낙폭을 분석하라. 배당 재투자 현황을 DCA 전략과 연결해 평가하라. 세금은 올해 과세 여부만 언급하라. 추적 기간이 짧으므로 장기 결론 도출을 자제할 것.

---

### 통합 한국어 시스템 프롬프트

```
당신은 Intelligence Barbell 투자 전략의 일일 애널리스트 AI입니다.

역할:
- 입력된 JSON 데이터만 사용해 한국어 투자 리포트를 작성한다
- 입력에 없는 숫자, 티커, 뉴스, 인과관계, 전망을 절대 추가하지 않는다
- 모르거나 데이터가 없으면 "확인 필요"라고 표기한다
- 모든 수치는 입력 JSON에서 직접 인용한다

출력 형식:
## [A] 시장 현황 해석
- (1~2 bullet)
## [B] 포트폴리오 진단
- (2~3 bullet, 우선순위 종목 포함)
## [C] 스캔 하이라이트
- (1~2 bullet, 편입후보 + 경고)
## [D] 센티먼트 & 매크로
- (1~2 bullet, 포트폴리오 관련만)
## [E] 오늘 할 일
- (구체적 액션 2~3개, 입력 데이터 기반)
```

---

## 3. 토큰 비용 추정표

### 모델별 변환 기준
- **claude-sonnet-4-6 / gpt-5-mini**: 약 4.5자/토큰 (한국어+영어 혼합)
- **claude-opus-4-8 / gpt-5.5**: 약 4.0자/토큰

### 시나리오별 크기 근거 (실측치)

| 데이터 | 실측 크기 | 비고 |
|--------|----------|------|
| QQQ + 지표 JSON | 733자 | fetch_qqq_data + rsi + vix + fg + ma + fx + phase |
| portfolio_snapshot.json | 4,071자 | 17개 종목, 실시간 P&L |
| portfolio 분석 결과 12개 | 13,644자 | 종목당 1,137자 |
| NASDAQ top5 + warn5 | 5,944자 | 2,892 + 3,052자 |
| NASDAQ 전체 100개 | 57,997자 | investment-data 기준 |
| KOSPI top5 + warn5 | 6,002자 | 2,923 + 3,079자 |
| KOSPI 전체 30개 | 17,582자 | investment-data 기준 |
| source-cache 전체 | 284,358자 | 1,044개 이벤트 |
| build_digest (12건 요약) | ~600자 | build_digest() 출력 |
| portfolio_history (6레코드) | 1,494자 | |
| 배당+세금 | ~200자 | qqqi_dividends + tax_records |
| 시스템 지시문 | ~500자 | 역할+규칙+출력형식 |

---

### 섹션별 토큰 비용

| 섹션 | Full 데이터 | 현재 LLM overlay | Smart 요약 |
|------|------------|-----------------|-----------|
| 입력 크기 (자) | 입력 크기 (자) | 입력 크기 (자) | |

| 섹션 | Full (자) | 현재 overlay (자) | Smart (자) |
|------|----------|-----------------|-----------|
| A. 시장현황 | 733 | 400 (압축) | 600 |
| B. 포트폴리오 | 17,715 | 1,245 (8개 압축) | 2,000 (12개 핵심) |
| C. NASDAQ 스캔 | 57,997 | 881 (top3+warn3) | 1,200 (top3+warn3, 핵심) |
| C. KOSPI 스캔 | 17,582 | 560 (top3+warn3) | 800 (top3+warn3, 핵심) |
| D. 소스 이벤트 | 284,358 | 1,000 (digest) | 500 (digest) |
| E. 히스토리+세금 | 1,694 | 0 (미포함) | 600 |
| 시스템 지시문 | 500 | 300 | 500 |
| **합계 (자)** | **380,579** | **4,386** | **6,200** |

---

### 최종 토큰 비용 표

| 시나리오 | 섹션 | Sonnet/gpt-mini (4.5자/t) | Opus/gpt-5.5 (4.0자/t) |
|----------|------|--------------------------|------------------------|
| **Full** | A. 시장현황 | 163 t | 183 t |
| | B. 포트폴리오 | 3,937 t | 4,429 t |
| | C. NASDAQ | 12,888 t | 14,499 t |
| | C. KOSPI | 3,907 t | 4,396 t |
| | D. 소스 | 63,191 t | 71,090 t |
| | E. 히스토리 | 376 t | 424 t |
| | 지시문 | 111 t | 125 t |
| | **Full 합계** | **🔴 84,573 t** | **🔴 95,146 t** |
| **현재 overlay** | A. 시장현황 | 89 t | 100 t |
| | B. 포트폴리오 | 277 t | 311 t |
| | C. NASDAQ | 196 t | 220 t |
| | C. KOSPI | 124 t | 140 t |
| | D. 소스 digest | 222 t | 250 t |
| | E. 히스토리 | 0 t | 0 t |
| | 지시문 | 67 t | 75 t |
| | **현재 합계** | **🟡 975 t** | **🟡 1,096 t** |
| **Smart 요약** | A. 시장현황 | 133 t | 150 t |
| | B. 포트폴리오 | 444 t | 500 t |
| | C. NASDAQ | 267 t | 300 t |
| | C. KOSPI | 178 t | 200 t |
| | D. 소스 digest | 111 t | 125 t |
| | E. 히스토리 | 133 t | 150 t |
| | 지시문 | 111 t | 125 t |
| | **Smart 합계** | **🟢 1,377 t** | **🟢 1,550 t** |

**비용 참고** (2026년 6월 기준 가격):
- claude-sonnet-4-6: $3/1M input token → Full: ~$0.25/회, overlay: ~$0.003/회, Smart: ~$0.004/회
- claude-opus-4-8: $15/1M input token → Full: ~$1.43/회, overlay: ~$0.016/회, Smart: ~$0.023/회
- **일 1회 실행, 연 365회 기준**: Sonnet Smart → 연 $1.5, Full → 연 $91.3

---

## 4. 최적화 제안

### 4-1. 최대 토큰 낭비 원인 (Full 시나리오)

```
D. 소스 전체 1,044건:  284,358자 = 전체의 74.7% 차지  ← 최우선 삭감 대상
C. NASDAQ 전체 100개:  57,997자  = 전체의 15.2%       ← 2위 삭감 대상
B. 포트폴리오 상세:    17,715자  = 전체의 4.7%
```

**결론**: D + C 두 섹션만 줄여도 Full → Smart 수준(99% 절감) 달성 가능.

---

### 4-2. 섹션별 최적화 전략

#### 🔴 D. 소스 이벤트 (최우선)
- **현황**: 전체 1,044개 이벤트 → `build_digest()` 12건 요약으로도 95% 압축 가능
- **추천**: `build_digest()` 출력 사용 (포트폴리오 ticker 필터링 포함)
- **더 나은 방법**: portfolio_tickers 관련 이벤트만 사전 필터링 후 20건 한도
- **효과**: 284,358자 → 600자 (99.8% 절감)

#### 🟠 C. 스캔 결과 (2순위)
- **현황**: NASDAQ 전체 100개(57,997자) vs top5+warn5(5,944자)
- **추천**: top3+warn3 만 포함하되, 각 항목에서 필요 필드만 선택
  ```json
  { "ticker": "NVDA", "score": 88, "grade": "A", "signal": "Positive",
    "action": "강한 매수후보", "reason": "재무 88점(A) · 일일 신호 긍정" }
  ```
- **효과**: 57,997자 → 1,200자 (98% 절감)

#### 🟡 B. 포트폴리오 (3순위)
- **현황**: 종목당 1,137자 (fundamental 상세 + ETF 비교 + signal 전체 포함)
- **추천**: LLM 분석에 필요한 핵심 필드만 추출
  ```json
  { "ticker": "ORCL", "pnl_usd": 258.06, "return_pct": 22.61,
    "current_weight_pct": 4.8, "target_weight_pct": 7.0,
    "signal": "Positive", "action": "관심 유지" }
  ```
- **효과**: 13,644자 → 2,000자 (85% 절감)

#### 🟢 A. 시장현황 (유지)
- 현재 overlay의 시장 요약은 이미 압축 최적화 수준
- 400~600자가 적정 범위

#### 🟢 E. 히스토리 (추가 권장)
- 현재 LLM overlay에서 **히스토리를 완전히 생략** 중 → 추가 효과 있음
- 성과 요약은 ~300자면 충분 (ret_1d, ret_7d, ret_30d + peak + current)

---

### 4-3. 최적 프롬프트 아키텍처 제안

```
📐 권장: Smart 시나리오 (~1,400 tokens, Sonnet 기준)

[시스템] 역할 + 규칙 (~500자)
[A] fetch_qqq_data + RSI + VIX + F&G + Phase 요약 (~600자)
[B] portfolio holdings: 핵심 7필드 × 12종목 (~2,000자)
    + DCA today + SGOV action (~300자)
[C] nasdaq top3 + warn3 (핵심 필드) (~600자)
    + kospi top3 + warn3 (핵심 필드) (~400자)
[D] build_digest() 12건 + portfolio 관련 이벤트 필터 (~600자)
[E] performance 1d/7d/30d + dividend + tax summary (~600자)
[출력형식] 5섹션 지시 (~200자)

총계: ~5,800자 = ~1,289 토큰 (Sonnet)
```

---

### 4-4. 구현 우선순위 체크리스트

- [x] **현재**: `_build_llm_overlay_prompt()` — market+portfolio[:8]+scan top3/warn3+digest 1000자 (~975t)
- [ ] **단기**: E. 히스토리 섹션 추가 (+133t → 합계 ~1,108t)
- [ ] **단기**: B. 포트폴리오 12개 전체로 확대 (핵심 필드 추출) (+167t → ~1,377t)
- [ ] **장기**: C. 스캔에서 full decision_v2 제거 → action+reason만 유지 (토큰 절감)
- [ ] **장기**: D. 포트폴리오 ticker 필터링 전처리 함수 구현

---

## 5. 요약

| 항목 | 수치 |
|------|------|
| 전체 수집 데이터 최대 크기 | ~380,000자 (~85,000 토큰) |
| 현재 LLM overlay 프롬프트 | ~4,400자 (~975 토큰 Sonnet) |
| 권장 Smart 프롬프트 | ~6,200자 (~1,380 토큰 Sonnet) |
| Smart vs Full 절감율 | **98.4%** |
| 최대 낭비 섹션 | D. 소스 이벤트 (전체의 74.7%) |
| 2위 낭비 섹션 | C. NASDAQ 전체 스캔 (15.2%) |
| 일 1회 Sonnet Smart 비용 | **$0.004/회 = 연 $1.46** |
| 일 1회 Sonnet Full 비용 | $0.254/회 = 연 **$92.7** |
