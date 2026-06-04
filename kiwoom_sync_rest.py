#!/usr/bin/env python3
"""
kiwoom_sync_rest.py — 키움 REST API 국내주식 잔고 → portfolio_snapshot.json 동기화

크론 등록 (매일 08:35 KST = 23:35 UTC):
    35 23 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python kiwoom_sync_rest.py

환경변수 (.env):
    KIWOOM_API_KEY    — openapi.kiwoom.com 에서 발급
    KIWOOM_API_SECRET — openapi.kiwoom.com 에서 발급
    KIWOOM_ACCOUNT_NO — 국내주식 계좌번호 (선택, 다계좌 구분 시)
"""

import os
import json
import shutil
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_DIR    = os.getenv("STOCK_REPORT_PROJECT_DIR", os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_PATH = os.path.join(PROJECT_DIR, "portfolio_snapshot.json")

# 텔레그램 알림
_BOT_TOKEN = os.getenv("STOCK_BOT_TOKEN")
_CHAT_ID   = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")


def _notify(msg: str):
    if not _BOT_TOKEN:
        return
    try:
        import requests as _req
        _req.post(
            f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


def fetch_domestic_balance() -> list[dict]:
    """키움 REST API로 국내주식 보유잔고 조회."""
    try:
        import requests as _req
        from kiwoom_rest_api.auth.token import TokenManager
    except ImportError:
        logger.error("kiwoom-rest-api 미설치: uv pip install kiwoom-rest-api")
        return []

    if not os.getenv("KIWOOM_API_KEY") or not os.getenv("KIWOOM_API_SECRET"):
        logger.error(".env에 KIWOOM_API_KEY / KIWOOM_API_SECRET 없음")
        return []

    try:
        tok = TokenManager().access_token
        if not tok:
            logger.error("키움 토큰 발급 실패 — API Key/Secret 확인 필요")
            return []

        resp = _req.post(
            "https://api.kiwoom.com/api/dostk/acnt",
            headers={
                "content-type": "application/json;charset=UTF-8",
                "Authorization": f"Bearer {tok}",
                "api-id": "kt00018",
            },
            json={"qry_tp": "2", "dmst_stex_tp": "KRX"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        logger.error("키움 API 호출 실패: %s", e)
        return []

    if result.get("return_code", -1) != 0:
        logger.error("API 오류: %s", result.get("return_msg", "unknown"))
        return []

    def _num(item: dict, key: str) -> float:
        """숫자 문자열 → float (쉼표·퍼센트 제거, 빈값=0)."""
        raw = item.get(key, "") or ""
        return float(raw.replace(",", "").replace("%", "").strip() or "0")

    holdings = []
    for item in result.get("acnt_evlt_remn_indv_tot", []):
        ticker = item.get("stk_cd", "").strip()
        if not ticker:
            continue

        holdings.append({
            "ticker":            ticker,
            "name":              item.get("stk_nm", "").strip(),
            "shares":            _num(item, "rmnd_qty"),
            "avg_price_krw":     _num(item, "pur_pric"),
            "current_price_krw": _num(item, "cur_prc"),
            "cost_krw":          _num(item, "pur_amt"),
            "value_krw":         _num(item, "evlt_amt"),
            "pnl_krw":           _num(item, "evltv_prft"),
            "return_pct":        _num(item, "prft_rt"),
        })

    logger.info("잔고 조회 완료: %d개 종목", len(holdings))
    return holdings


def update_portfolio(holdings: list[dict]) -> str:
    """portfolio_snapshot.json의 domestic 섹션 업데이트."""
    shutil.copy2(PORTFOLIO_PATH, PORTFOLIO_PATH + ".bak")

    with open(PORTFOLIO_PATH, encoding="utf-8") as f:
        snap = json.load(f)

    # 기존 국내주식 딕셔너리 (ticker → entry)
    existing = {
        h["ticker"]: h
        for h in snap.get("domestic", {}).get("holdings", [])
    }

    for h in holdings:
        existing[h["ticker"]] = h

    snap.setdefault("domestic", {})["holdings"] = list(existing.values())
    snap["last_domestic_sync"] = datetime.now().isoformat()

    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    lines = [f"  {h['ticker']} {h['name']} {h['shares']}주  {h['return_pct']:+.1f}%" for h in holdings]
    return "\n".join(lines)


def main():
    logger.info("키움 국내주식 동기화 시작")

    holdings = fetch_domestic_balance()
    if not holdings:
        logger.warning("조회된 종목 없음 — API 키 또는 계좌 확인 필요")
        return

    summary = update_portfolio(holdings)
    logger.info("업데이트 완료:\n%s", summary)

    total_value = sum(h.get("value_krw", 0) for h in holdings)
    _notify(
        f"📊 키움 국내주식 동기화\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{summary}\n\n"
        f"  총평가  ₩{total_value:,.0f}"
    )


if __name__ == "__main__":
    main()
