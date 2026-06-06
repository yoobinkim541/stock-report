# Quant ML Strategy Plan

목표: NASDAQ100 + S&P500 전 종목, 현재 보유종목, ETF/매크로/뉴스 피처를 이용해 QQQ/SPY/기존 Intelligence Barbell 대비 초과 성과가 있는 운영 로직을 walk-forward 방식으로 검증한다.

## 원칙

- 한 번에 구현하지 않는다. 단계별 산출물과 테스트를 닫고 다음 단계로 간다.
- 데이터 누수 방지: 피처는 해당 시점까지 관측 가능한 값만 사용한다.
- 생존자편향 경고: 초기 버전은 현재 구성종목 기반으로 시작하되 리포트에 한계를 명시한다.
- 뉴스 원문은 직접 모델에 넣지 않고, 날짜/티커별 count, sentiment, theme, event 피처로 압축한다.
- PyTorch는 후순위다. LightGBM + Optuna + walk-forward baseline을 먼저 이겨야 운영 후보가 된다.

## 단계

1. 기존 봇/백테스트 안정화
   - `/holding buy`, `/holding sell`, `/holding apply` 검증
   - 보유종목 변경 시 자동 백테스트 트리거 검증
   - `py_compile`, `pytest`, 봇 재시작 확인

2. Universe builder
   - NASDAQ100 + S&P500 전 종목
   - 현재 보유종목
   - QQQ/SPY/SGOV/TLT/IEF/SHY/GLD 등 ETF·매크로 proxy

3. Data source layer
   - Stooq/Yahoo 가격 fallback
   - FRED 금리/스프레드
   - CBOE Put/Call
   - Fear & Greed proxy/CNN
   - source-cache 뉴스 시점 고정

4. Feature dataset
   - MA, RSI, MACD, Bollinger, Ichimoku, momentum, volatility
   - 금리/채권 상대수익률
   - Fear/Greed, Put/Call
   - 뉴스 sentiment/theme/event 집계 피처

5. Baseline
   - buy & hold QQQ/SPY
   - 기존 Intelligence Barbell
   - rule/grid
   - sklearn 선형/트리 baseline

6. LightGBM
   - market risk score
   - ticker excess-return/ranking model
   - 뉴스 피처 ablation 비교

7. Optuna
   - threshold, model, portfolio parameter 최적화
   - 목적함수: CAGR, MDD, turnover, QQQ/SPY 대비 초과수익 조합

8. Walk-forward 검증
   - train/validation/test 분리
   - 리밸런싱 주기별 검증
   - 누수/편향 경고 리포트 포함

9. Portfolio construction
   - SGOV/채권/QQQ/상위종목 비중 제약
   - 리밸런싱 로직

10. Telegram 연동
    - QQQ/SPY/기존전략 대비 성과표
    - 추천 운영 로직 요약 발송

## 산출물 위치

- 계획/설계: `docs/plans/`
- ML 코드: `ml/`
- 운영 스크립트: `scripts/`
- 런타임/백업 데이터: `data/` 또는 `~/.local/share/stock-report/`
- 테스트: `tests/`
