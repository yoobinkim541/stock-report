"""기관 매집 추적 Telegram 명령어 — /accum."""
import logging
import os
import sys

# 직접 실행(__main__) 시 프로젝트 루트를 import 경로에 추가
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml.data_pipeline import US_TOP100, KR_TOP10_META
from portfolio_universe import load_portfolio_tickers
from reports.institutional_flow import rank_accumulation, accumulation_mobile_block

logger = logging.getLogger(__name__)


def _name_fn(ticker: str) -> str:
    """KOSPI 티커는 한글명, 그 외는 티커 그대로."""
    if ticker.endswith(".KS"):
        meta = KR_TOP10_META.get(ticker)
        if meta:
            return meta[0]
    return ticker


def cmd_accum(chat_id: str, args: list, send_fn):
    """기관 매집 추적 랭킹 — OBV·CMF·13F 기반 매집 강도."""
    try:
        if not args:
            universe = sorted(
                set(load_portfolio_tickers()) | set(US_TOP100) | set(KR_TOP10_META)
            )
            picks = rank_accumulation(universe, limit=10, min_score=60)
        elif args[0].lower() == "us":
            picks = rank_accumulation(list(US_TOP100), limit=10, min_score=60)
        elif args[0].lower() == "kr":
            picks = rank_accumulation(list(KR_TOP10_META), limit=10, min_score=60)
        else:
            tickers = [t.upper() for t in args]
            picks = rank_accumulation(tickers, limit=len(tickers), min_score=0)

        if not picks:
            lines = ["🏛️ 기관 매집 추적", "매집 강도 60+ 종목 없음(시장 중립/분산)"]
        else:
            # accumulation_mobile_block 이 첫 줄에 title 을 넣으므로 별도 헤더 중복 금지
            lines = accumulation_mobile_block(
                picks, title="🏛️ 기관 매집 추적 (매집 강도 상위)", limit=10, name_fn=_name_fn)
        lines.append("")
        lines.append("⚠️ 거래량 방향성(OBV·CMF) 기반 추정 — 실제 기관 순매수가 아님")
        lines.append("   (13F 지분은 분기 지연 공시 교차검증용) · 참고용")
        send_fn(chat_id, "\n".join(lines))
    except Exception as e:
        logger.exception("/accum 처리 실패")
        send_fn(chat_id, f"⚠️ 기관 매집 추적 실패: {e}")


if __name__ == "__main__":
    def _print_send(_chat_id, text):
        print(text)

    _args = sys.argv[1:]
    cmd_accum("local-test", _args, _print_send)
