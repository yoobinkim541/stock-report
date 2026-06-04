"""
kiwoom_sync.py — 키움 Open API+ 해외주식 잔고 → Ubuntu 서버 동기화

필수 환경:
    - Windows (32bit Python 필수)
    - pip install PyQt5 pywin32 requests python-dotenv

실행:
    python kiwoom_sync.py

TR 코드 확인 방법:
    KOA Studio (키움 Open API+ 설치 시 함께 설치됨)
    → 좌측 "TR목록" 탭 → 검색창에 "해외" 입력
    → 해외주식 잔고 TR 코드와 입출력 필드명 확인
    → 아래 TODO 주석 부분을 실제 값으로 교체
"""

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv

# 32bit Python 여부 확인
if sys.maxsize > 2**32:
    print("⚠️  경고: 64bit Python입니다. 키움 Open API+는 32bit Python이 필요합니다.")
    print("   https://www.python.org/downloads/ 에서 32bit(x86) Python 설치 필요")

try:
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QAxContainer import QAxWidget
    from PyQt5.QtCore import QEventLoop
except ImportError:
    print("❌ PyQt5 미설치: pip install PyQt5")
    sys.exit(1)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 환경변수 ────────────────────────────────────────────────────────────
ACCOUNT_GENERAL    = os.getenv("KIWOOM_ACCOUNT_GENERAL")    # 해외주식 일반계좌번호
ACCOUNT_FRACTIONAL = os.getenv("KIWOOM_ACCOUNT_FRACTIONAL") # 소수점 계좌번호
KIWOOM_PASSWORD    = os.getenv("KIWOOM_PASSWORD", "")       # 계좌 비밀번호
SYNC_SERVER_URL    = os.getenv("SYNC_SERVER_URL")           # https://서버IP:8765/sync
SYNC_TOKEN         = os.getenv("SYNC_TOKEN")

# ── TR 코드 ─────────────────────────────────────────────────────────────
# TODO: KOA Studio에서 아래 값들을 확인하고 교체하세요
#
# KOA Studio 사용법:
#   1. 키움 Open API+ 설치 후 KOA Studio 실행
#   2. 좌측 TR목록 탭 → "해외" 검색
#   3. 해외주식 잔고 관련 TR 클릭 → 입력/출력 필드명 확인
#
TR_OVERSEAS_BALANCE = "opw07012"   # TODO: 실제 TR 코드로 교체

# TODO: KOA Studio에서 opw07012 TR의 입력 필드명 확인
INPUT_ACCOUNT    = "계좌번호"            # 계좌번호 입력 필드
INPUT_PASSWORD   = "비밀번호"            # 비밀번호 입력 필드
INPUT_PWD_TYPE   = "비밀번호입력매체구분"  # "00" 고정
INPUT_QUERY_TYPE = "조회구분"            # "1" 전체조회

# TODO: KOA Studio에서 opw07012 TR의 출력 필드명 확인
FIELD_TICKER = "종목코드"     # 미국 티커 (e.g. "MSFT")
FIELD_NAME   = "종목명"       # 종목명
FIELD_SHARES = "보유수량"     # 보유 수량
FIELD_AVG    = "매입평균가"   # 평균 매입 단가 (USD)
FIELD_CURR   = "현재가격"     # 현재가 (USD)


class KiwoomSync:
    def __init__(self):
        self.app  = QApplication.instance() or QApplication(sys.argv)
        self.ocx  = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.loop = QEventLoop()

        self.ocx.OnEventConnect.connect(self._on_login)
        self.ocx.OnReceiveTrData.connect(self._on_tr_data)

        self._result: list | None = None
        self._pending_rqname: str | None = None

    # ── 로그인 ─────────────────────────────────────────────────────────
    def login(self) -> bool:
        logger.info("키움증권 로그인 시도...")
        self.ocx.dynamicCall("CommConnect()")
        self.loop.exec_()
        ok = self.ocx.dynamicCall("GetConnectState()") == 1
        logger.info("로그인 %s", "성공" if ok else "실패")
        return ok

    def _on_login(self, err_code: int):
        if err_code != 0:
            logger.error("로그인 에러 코드: %d", err_code)
        self.loop.quit()

    # ── TR 요청 헬퍼 ───────────────────────────────────────────────────
    def _set(self, key: str, val: str):
        self.ocx.dynamicCall("SetInputValue(QString, QString)", key, val)

    def _request(self, rqname: str, trcode: str, screen: str = "0101") -> list:
        self._pending_rqname = rqname
        self._result = None
        self.ocx.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            rqname, trcode, 0, screen,
        )
        self.loop.exec_()
        return self._result or []

    def _get(self, trcode: str, record: str, idx: int, field: str) -> str:
        return self.ocx.dynamicCall(
            "GetCommData(QString, QString, int, QString)",
            trcode, record, idx, field,
        ).strip()

    def _count(self, trcode: str, record: str) -> int:
        return int(self.ocx.dynamicCall("GetRepeatCnt(QString, QString)", trcode, record))

    def _on_tr_data(self, screen_no, rqname, trcode, record_name,
                    prev_next, data_len, err_code, msg, spl_msg):
        if rqname != self._pending_rqname:
            return

        holdings = []
        n = self._count(trcode, record_name)
        logger.info("  TR 수신: %s — %d개 행", rqname, n)

        for i in range(n):
            ticker = self._get(trcode, record_name, i, FIELD_TICKER).replace(" ", "")
            name   = self._get(trcode, record_name, i, FIELD_NAME)
            shares = self._get(trcode, record_name, i, FIELD_SHARES).replace(",", "")
            avg    = self._get(trcode, record_name, i, FIELD_AVG).replace(",", "")
            curr   = self._get(trcode, record_name, i, FIELD_CURR).replace(",", "")

            if not ticker:
                continue
            try:
                holdings.append({
                    "ticker":            ticker,
                    "name":              name,
                    "shares":            float(shares),
                    "avg_price_usd":     float(avg),
                    "current_price_usd": float(curr) if curr else 0.0,
                })
            except ValueError as e:
                logger.warning("  %s 파싱 실패: %s (shares=%s avg=%s)", ticker, e, shares, avg)

        self._result = holdings
        self.loop.quit()

    # ── 잔고 조회 ──────────────────────────────────────────────────────
    def get_balance(self, account_no: str, label: str = "") -> list:
        logger.info("잔고 조회 [%s] %s****", label or "계좌", account_no[:4])
        self._set(INPUT_ACCOUNT,    account_no)
        self._set(INPUT_PASSWORD,   KIWOOM_PASSWORD)
        self._set(INPUT_PWD_TYPE,   "00")
        self._set(INPUT_QUERY_TYPE, "1")
        result = self._request(f"해외잔고_{label}", TR_OVERSEAS_BALANCE, screen="0101")
        logger.info("  → %d개 종목", len(result))
        return result


def send_to_server(general: list, fractional: list):
    payload = {
        "overseas_general":    general,
        "overseas_fractional": fractional,
        "synced_at":           datetime.now().isoformat(),
    }
    logger.info("서버 전송 중: %s", SYNC_SERVER_URL)
    resp = requests.post(
        SYNC_SERVER_URL,
        json=payload,
        headers={"Authorization": f"Bearer {SYNC_TOKEN}"},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()
    logger.info("✅ 동기화 완료: %s", result.get("summary", ""))


def main():
    for var, name in [
        (ACCOUNT_GENERAL,    "KIWOOM_ACCOUNT_GENERAL"),
        (ACCOUNT_FRACTIONAL, "KIWOOM_ACCOUNT_FRACTIONAL"),
        (SYNC_SERVER_URL,    "SYNC_SERVER_URL"),
        (SYNC_TOKEN,         "SYNC_TOKEN"),
    ]:
        if not var:
            logger.error(".env에 %s가 없습니다.", name)
            sys.exit(1)

    kw = KiwoomSync()

    if not kw.login():
        logger.error("로그인 실패 — 키움 HTS가 실행 중인지 확인하세요.")
        sys.exit(1)

    time.sleep(1)  # 로그인 안정화

    general    = kw.get_balance(ACCOUNT_GENERAL,    "일반")
    fractional = kw.get_balance(ACCOUNT_FRACTIONAL, "소수점")

    if not general and not fractional:
        logger.warning("조회된 잔고 없음 — TR 코드나 필드명을 KOA Studio에서 확인하세요.")
    else:
        send_to_server(general, fractional)

    kw.app.quit()
    logger.info("완료")


if __name__ == "__main__":
    main()
