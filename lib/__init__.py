"""lib/ — 교차 모듈 공유 유틸 (리팩토링: 중복 제거, 행위 보존).

http_utils  : urllib GET + User-Agent 상수 (providers 공용)
price_utils : yfinance 종가 fetch + 실적전 모멘텀/변동성 윈도 피처 (ml 공용)
cron_common : 크론 텔레그램 발송 통일(send_cron_telegram — 11 _send 변종 통합; crons 공용)
"""
