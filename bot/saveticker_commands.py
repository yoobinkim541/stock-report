"""Owner-only notice that SaveTicker news recovery is retired."""
def handle(chat_id, args, *, send_fn):
    send_fn(chat_id,
            "SaveTicker 뉴스 수집은 기존 API 종료로 사용을 종료했습니다.\n"
            "대체 소스: Investing.com 한국어 주식·경제 RSS, Yahoo Finance 종목별 RSS.\n"
            "제목·제공된 요약·원문 링크를 수집하며 기사 전문을 복원한 것은 아닙니다.\n"
            "이전 원격 화면·복구 확인 버튼은 더 이상 사용하지 않습니다.")
