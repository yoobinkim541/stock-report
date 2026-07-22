# Agent Console Intent Routing Design

## Goal
AI 콘솔이 화면 맥락이나 로컬 메모리 부족 때문에 답변을 중단하지 않고, 질문 의도별로 필요한 데이터 수집과 답변 형식을 먼저 고정한다.

## Chosen Approach
의도 분류를 답변 생성 라우터가 아니라 LLM 프롬프트 계약으로 사용한다. 최종 문장은 LLM이 만들되, `peer_compare`, `portfolio_review`, `market_brief`, `technical_analysis`, `ticker_research`, `meta`, `general` 같은 의도별로 검색 필요성, 기본 비교 대상, 금지 템플릿, 중단 조건을 명시한다.

## Extension Point
이번 변경은 프롬프트 계약까지만 구현하지만, 각 의도 객체에 `retrieval_plan` 필드를 둔다. 나중에 Yahoo Finance, IR 자료, 뉴스 검색, 재무제표 provider를 실제 로컬 수집기로 붙일 때 이 필드를 그대로 실행 계획으로 사용할 수 있다.

## Guardrails
- 컨텍스트 부족은 답변 중단 사유가 아니라 검색 트리거다.
- 검색 도구가 없거나 최신 데이터 접근이 실패한 경우에만 접근 실패를 명시한다.
- 종목명이 불명확하거나 비교축이 불명확할 때만 짧게 확인 질문을 한다.
- 시장 상황 인식, MIXED, 시장 신호 점수 템플릿은 시장 리포트 의도에서만 허용한다.

## Test Targets
- JP모건 다른 IB 비교 질문은 JPM, GS, MS, BAC, C 기본 피어와 최신 검색 계약을 포함한다.
- 메타 질문은 시장 템플릿을 금지한다.
- 포트폴리오 질문은 보유 비중, 손실, 리스크를 우선한다.
- 기술적 분석만 요청하면 뉴스와 거시를 제외한다.
