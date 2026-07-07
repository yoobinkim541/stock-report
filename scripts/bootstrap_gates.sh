#!/usr/bin/env bash
# bootstrap_gates.sh — 게이트 JSON 콜드스타트 1회 부트스트랩 (F)
#
# 신규 배포 직후 대시보드 '축 게이트'·홈 신호등·사이드바가 토요일 크론 전까지
# "검증 결과 없음"으로 비는 갭을 해소 — 재검증 크론을 지금 1회 실행해 JSON 생성.
# ⚠️ KR 첫 실행은 marcap parquet(~465MB) 다운로드 포함 — 수 분 소요.
#    US 는 yfinance 배치(수 분) — 실패해도 KR 은 독립 생성(|| true).
set -e
cd "$(dirname "$0")/.."
echo "── KR 가격축 게이트 부트스트랩 (marcap 첫 다운로드 시 수 분) ──"
uv run python crons/kr_axes_eval.py
echo "── US 가격축 게이트 부트스트랩 (yfinance 배치 — 실패해도 계속) ──"
uv run python crons/us_axes_eval.py || echo "US 게이트 생략(네트워크/데이터) — 토요일 크론이 재시도"
echo "완료 — 대시보드 리서치→축 게이트에서 확인"
