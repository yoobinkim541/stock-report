#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
barbell_strategy.py — Intelligence Barbell v2.1
유빈의 상승장/조정장/하락장 통합 자산 배분 알고리즘

전략 구조:
  ┌─────────────────────────────────────────────────┐
  │  상승장  │  중립  │  조정  │  하락  │  크래시   │
  │  SGOV↑   │  DCA   │SGOV→QLD│SGOV 전환│ TQQQ 전면│
  └─────────────────────────────────────────────────┘

  - QQQ 낙폭 기준 6단계 하락 대응 (Phase 0~5)
  - QQQ 상승 강도 기준 2단계 상승 대응 (Bull 1~2)
  - 보조 지표: RSI, VIX, 200일 MA, 모멘텀 스코어
  - 포트폴리오 실시간 총액 자동계산 (yfinance 기반)
  - USD/KRW 환율 실시간 반영
  - QQQI 월간 배당 자동 추산
  - QLD/TQQQ 레버리지 포지션 추적
  - Phase 변화 감지 → 텔레그램 자동 알림 (중복 방지)
"""

import os
import json
import logging
import time
import unicodedata
from datetime import datetime

import numpy as np
# requests·yfinance·safe_io 는 데이터 수집층(providers/market_data.py)으로 이전 — barbell 본문은 미사용.

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

import store  # SQLite 통합 저장소 (설정 블롭 권위 사본 + 파일 미러)
import notify   # 텔레그램 발송 단일 진실원

# ── 데이터 수집층 재export ───────────────────────────────────────────────
# 데이터/상태 접근 함수는 providers/market_data.py 로 분리(god-module 분해)되었다.
# 아래 명시 import 로 (a) 외부 'from barbell_strategy import fetch_qqq_data/...' 호환,
# (b) 이 모듈 내부 전략코드가 쓰는 _safe_float·_history_cached·_realized_vol_annual·
#     fetch_*·레버리지 state 함수·상수가 그대로 resolve 되게 한다.
# PORTFOLIO_PATH 는 market_data 가 portfolio_universe 에서 재export 하므로 여기서 함께 가져온다
# (portfolio_universe 직접 import 와 중복 금지 — 단일 경로).
from providers.market_data import (
    # 상수
    SGOV_SHARES_DEFAULT, QQQI_SHARES_DEFAULT,
    SGOV_FALLBACK_PRICE, QQQI_FALLBACK_PRICE, QQQI_ANNUAL_YIELD,
    PRICE_STALE_MAX_DAYS,
    LEVERAGE_FILE, ANCHOR_FILE, ANCHOR_RESET_RECOVERY,
    _HIST_CACHE, _HIST_CACHE_TTL_S, _LAST_PRICES_FILE,
    PORTFOLIO_PATH,
    # 헬퍼·캐시
    _safe_float, _holding_details_from_snapshot, _history_cached,
    _load_last_prices, _save_last_prices,
    _load_drawdown_anchor, _update_drawdown_anchor, _realized_vol_annual,
    # 외부 피드 조회
    fetch_exchange_rate, fetch_qqq_data, fetch_rsi, fetch_vix,
    fetch_fear_greed, fetch_ma200, fetch_portfolio_value,
    estimate_qqqi_monthly_dividend,
    # 레버리지 상태
    load_leverage_state, save_leverage_state, update_leverage_position,
)

TELEGRAM_TOKEN   = os.getenv("STOCK_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")
STATE_FILE = os.path.expanduser("~/.cache/barbell_state.json")

# ── 기본값 (실시간 로드 실패 시 fallback) ───────────────────────────────
# (SGOV/QQQI fallback 가격·수량·연배당률은 providers.market_data 로 이전 — 위 import 참조)
DCA_DAILY_BASE_KRW = 40_000
TARGET_SGOV_RATIO = 0.08
MAX_SGOV_RATIO = 0.20

RSI_OVERSOLD = 30
RSI_NEAR_OVERSOLD = 40
RSI_OVERBOUGHT = 70
RSI_EXTREME_OB = 75
VIX_HIGH = 30
VIX_EXTREME = 40
VIX_LOW = 15

# ── 안전장치 (레버리지/DCA 리스크 가드) ──────────────────────────────────
# 봇은 매매를 실행하지 않고 '권고'만 한다. 아래는 그 권고가 폭주하지 않도록 하는 한도.
# (PRICE_STALE_MAX_DAYS 는 데이터 신선도 판정용이라 providers.market_data 로 이전 — 위 import)
MAX_DCA_MULTIPLIER = float(os.getenv("BARBELL_MAX_DCA_MULT", "5.0"))   # 절대 배율 상한 (F&G·ML 증폭 폭주 차단)
DCA_VOL_CAP_ANNUAL = float(os.getenv("BARBELL_DCA_VOL_CAP", "0.40"))   # QQQ 연변동성 이 값 초과 시 배율 비례 축소
LEVERAGE_HALT_DRAWDOWN = float(os.getenv("BARBELL_LEV_HALT_DD", "-55.0"))  # 낙폭 이 값 이하면 레버리지 증액 정지(실탄 소진·전소 방어)

# ── 상승장 Phase 정의 ─────────────────────────────────────────────────
BULL_PHASES = {
    "bull_2": {
        "label": "Bull-2 — 과열/버블 경고",
        "emoji": "🫧",
        "trigger": "QQQ가 52주 고점 경신 + RSI > 75 + 모멘텀 과열",
        "sgov_target_ratio": MAX_SGOV_RATIO,
        "dca_multiplier": 0.5,
        "description": "버블 징후. DCA 축소, SGOV 최대로 비축.",
        "action_items": [
            "소수점 DCA 0.5배 축소 (4만 → 2만원)",
            "QQQI 배당금 → SGOV 재투자 (실탄 비축)",
            "ORCL 목표가 도달 시 5~10% 부분 익절",
            "신규 매수 중단 — 기존 포지션만 유지",
            "SGOV 목표 비중: 포트폴리오의 20%",
        ],
    },
    "bull_1": {
        "label": "Bull-1 — 강세장 유지",
        "emoji": "🐂",
        "trigger": "QQQ 52주 고점 5% 이내 + RSI 60~75",
        "sgov_target_ratio": 0.12,
        "dca_multiplier": 0.8,
        "description": "강세 지속. DCA 소폭 축소, 실탄 점진 비축.",
        "action_items": [
            "소수점 DCA 0.8배 (4만 → 3.2만원)",
            "매월 QQQI 배당 50% → SGOV, 50% → DCA",
            "SGOV 목표 비중: 포트폴리오의 12%",
            "오버웨이트 종목 일부 리밸런싱 고려",
        ],
    },
}

# ── 하락/조정장 Phase 정의 ────────────────────────────────────────────
BEAR_PHASES = {
    0: {
        "label": "Phase 0 — 정상 모드",
        "range": (0, -5),
        "emoji": "🟢",
        "sgov_target_ratio": TARGET_SGOV_RATIO,
        "sgov_sell_pct": 0,
        "leverage_target": None,
        "dca_multiplier": 1.0,
        "description": "정상 DCA 유지. 변화 없음.",
        "action_items": [
            "일일 소수점 DCA 4만원 유지 (보유 종목 분산)",
            "QQQI 배당금 → ORCL 중심 소수점 재투자",
            "SGOV 전량 보유 — 실탄 온존",
            "월 1회 포트폴리오 리밸런싱 점검",
        ],
    },
    1: {
        "label": "Phase 1 — 조정 초입 (-5~-10%)",
        "range": (-5, -10),
        "emoji": "🟡",
        "sgov_target_ratio": 0.12,  # 조정 초입은 실탄 축적 구간 — 기본 8%보다 높게
        "sgov_sell_pct": 0,
        "leverage_target": None,
        "dca_multiplier": 1.5,
        "description": "조정 시작. DCA 증액, 고확신 종목 집중.",
        "action_items": [
            "소수점 DCA 1.5배 (4만 → 6만원)",
            "ORCL, NVDA, MSFT 우선 배정",
            "SGOV 유지 — 추가 하락 대기",
            "RSI < 40 여부, VIX 추이 일일 체크",
            "예수금·SGOV는 추가 하락 대비 실탄으로 관리",
        ],
    },
    2: {
        "label": "Phase 2 — 조정장 (-10~-15%)",
        "range": (-10, -15),
        "emoji": "🟠",
        "sgov_target_ratio": 0.056,
        "sgov_sell_pct": 30,
        "leverage_target": "QLD",
        "dca_multiplier": 2.0,
        "description": "본격 조정. SGOV 30% → QLD 전환.",
        "action_items": [
            "SGOV 30% 매도 → QLD 매수",
            "소수점 DCA 2배 (4만 → 8만원)",
            "NVDA, ORCL, MSFT 비중 집중",
            "QQQI 배당금 전액 QLD 재투자",
        ],
    },
    3: {
        "label": "Phase 3 — 베어 진입 (-15~-20%)",
        "range": (-15, -20),
        "emoji": "🔴",
        "sgov_target_ratio": 0.028,
        "sgov_sell_pct": 35,
        "leverage_target": "QLD",
        "dca_multiplier": 2.5,
        "description": "베어 진입. SGOV 누적 65% 전환.",
        "action_items": [
            "SGOV 잔여분의 50% 추가 매도 → QLD",
            "총 누적 SGOV→QLD 전환율: ~65%",
            "소수점 DCA 2.5배 (4만 → 10만원)",
            "QQQI 배당 전액 + 원금 5% → QLD",
            "국내 SOL AI반도체 익절 후 미장 투입 검토",
        ],
    },
    4: {
        "label": "Phase 4 — 베어마켓 (-20~-30%)",
        "range": (-20, -30),
        "emoji": "🚨",
        "sgov_target_ratio": 0.0,
        "sgov_sell_pct": 35,
        "leverage_target": "QLD+TQQQ (7:3)",
        "dca_multiplier": 3.0,
        "description": "베어마켓. SGOV 전량 레버리지 전환.",
        "action_items": [
            "SGOV 잔여 전량 매도",
            "QLD 70% + TQQQ 30% 비율로 분할 매수",
            "소수점 DCA 3배 (4만 → 12만원)",
            "국내 주식 전량 정리 → 미장 투입",
            "QQQI 원금 10% → TQQQ 전환 검토",
        ],
    },
    5: {
        "label": "Phase 5 — 크래시 (-30%+)",
        "range": (-30, -100),
        "emoji": "💥",
        "sgov_target_ratio": 0.0,
        "sgov_sell_pct": 0,
        "leverage_target": "TQQQ",
        "dca_multiplier": 5.0,
        "description": "시장 붕괴. 전면 공격 모드. 10년 매수 기회.",
        "action_items": [
            "TQQQ 전면 배치 — 승부수",
            "QQQI 원금 20~30% → TQQQ 전환",
            "소수점 DCA 5배 (4만 → 20만원)",
            "ORCL, NVDA, MSFT 최대 적립",
            "예비 현금(적금 포함) 단계적 투입",
        ],
    },
}

# ── DCA 종목 배분 기본값 ─────────────────────────────────────────────────
# 전량 청산 종목은 DCA 제외 (은퇴 티커 목록: portfolio_universe.py)
_DCA_WEIGHTS_DEFAULT = {
    "ORCL": 0.24, "NVDA": 0.20, "MSFT": 0.18,
    "GOOGL": 0.14, "UNH": 0.12, "SAP": 0.06, "SPMO": 0.06,
}
_BEAR_DCA_WEIGHTS_DEFAULT = {
    "ORCL": 0.28, "NVDA": 0.24, "MSFT": 0.20,
    "GOOGL": 0.14, "UNH": 0.08, "SAP": 0.03, "SPMO": 0.03,
}

DCA_WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dca_weights.json")


def load_dca_weights() -> tuple[dict, dict]:
    """
    dca_weights.json 에서 DCA 비중 로드.
    파일 없으면 기본값 반환.
    Returns: (normal_weights, bear_weights)
    """
    data = store.load_doc("dca_weights", DCA_WEIGHTS_FILE, None)
    if data:
        try:
            normal = data.get("normal", _DCA_WEIGHTS_DEFAULT)
            bear   = data.get("bear",   _BEAR_DCA_WEIGHTS_DEFAULT)
            # 합계 1.0 정규화
            n_sum = sum(normal.values())
            b_sum = sum(bear.values())
            if n_sum > 0: normal = {k: round(v / n_sum, 4) for k, v in normal.items()}
            if b_sum > 0: bear   = {k: round(v / b_sum, 4) for k, v in bear.items()}
            return normal, bear
        except Exception:
            pass
    return _DCA_WEIGHTS_DEFAULT, _BEAR_DCA_WEIGHTS_DEFAULT


def save_dca_weights(normal: dict, bear: dict):
    """DCA 비중 저장 (store 권위 + 파일 미러)."""
    store.save_doc("dca_weights", {"normal": normal, "bear": bear}, DCA_WEIGHTS_FILE)


# 런타임에 dca_weights.json 로드
DCA_WEIGHTS, BEAR_DCA_WEIGHTS = load_dca_weights()

# ── 목표 비중 파일 경로 ──────────────────────────────────────────────
TARGET_WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "target_weights.json")

# ETF / 실탄 / 레버리지 — 개별 종목 목표비중 분석 제외 티커
_SKIP_TICKERS = {"SGOV", "QQQI", "QLD", "TQQQ", "BIL", "SHV", "SHY",
                 "QQQ", "SPY", "VTI", "EFA", "EEM", "TLT", "IEF", "GLD",
                 "DBC", "DBMF", "UPRO", "TMF"}

_TOTAL_STOCK_BUDGET = 0.44   # 개별주 총 목표 비중 (QQQI·SGOV 제외 포트의 44%)


def load_target_weights(portfolio: dict | None = None) -> dict:
    """
    target_weights.json 로드.
    현재 보유 종목 중 설정 없는 종목은 DCA 비중 기반으로 자동 추론.
    portfolio: fetch_portfolio_value() 반환값 (holdings, prices 포함)
    """
    # 1. store(권위)에서 명시적 목표 로드 (레거시 파일 자동 마이그레이션)
    explicit: dict = {}
    raw = store.load_doc("target_weights", TARGET_WEIGHTS_FILE, {})
    if isinstance(raw, dict):
        explicit = {k: float(v) for k, v in raw.items()
                    if not k.startswith("_") and isinstance(v, (int, float))}

    if portfolio is None:
        return explicit

    # 2. 현재 보유 종목 추출
    holdings = portfolio.get("holdings", {})
    w_normal, _ = load_dca_weights()
    dca_total    = sum(w_normal.values()) or 1.0

    result = dict(explicit)

    for ticker in holdings:
        if ticker in _SKIP_TICKERS or ticker in result:
            continue

        # DCA 비중 기반 자동 산출
        if ticker in w_normal:
            dca_share = w_normal[ticker] / dca_total
            result[ticker] = round(dca_share * _TOTAL_STOCK_BUDGET, 4)
        else:
            # DCA에도 없는 신규 종목 → 소규모 추적 포지션
            result[ticker] = 0.02

    return result


def save_target_weights(updates: dict):
    """목표 비중 저장 (기존 값 유지 + 업데이트, store 권위 + 파일 미러)."""
    raw = store.load_doc("target_weights", TARGET_WEIGHTS_FILE, {})
    existing: dict = dict(raw) if isinstance(raw, dict) else {}  # _comment 등 보존
    existing.update(updates)
    store.save_doc("target_weights", existing, TARGET_WEIGHTS_FILE)


# ══════════════════════════════════════════════════════════════════════
#  헬퍼 / 데이터 수집
# ══════════════════════════════════════════════════════════════════════
# 데이터/상태 접근층(_safe_float·_holding_details_from_snapshot·_history_cached·
# _load/_save_last_prices·_load/_update_drawdown_anchor·_realized_vol_annual·
# fetch_*·레버리지 state 함수 + 관련 상수)은 providers/market_data.py 로 이전했다.
# 파일 상단의 'from providers.market_data import (...)' 로 이 모듈 네임스페이스에
# 그대로 재export 되어, 외부 import 호환과 아래 전략코드의 심볼 resolve 가 유지된다.


def leverage_dca_guard(base_adj_mult: float, *, drawdown_pct=None, realized_vol=None) -> tuple:
    """DCA 배율에 안전 한도를 적용한다 — (조정배율, 메타 dict).

    봇은 매매를 실행하지 않고 권고만 하므로, 이 가드는 '권고 금액'이 폭주하는 것을 막는다.
      1) 변동성 캡 : QQQ 연변동성 > DCA_VOL_CAP_ANNUAL 이면 배율을 비례 축소 (벌어진 변동성에
                     마틴게일式 5배 물타기를 그대로 권고하지 않는다 — 비평 #1의 핵심).
      2) 절대 상한 : MAX_DCA_MULTIPLIER (F&G×ML 증폭이 6배+로 폭주하는 것 차단).
      3) 낙폭 정지 : drawdown <= LEVERAGE_HALT_DRAWDOWN 이면 배율 1.0 으로 정지 — 실탄 소진·
                     레버리지 전소 구간에서 추가 증액 권고를 멈추고 수동 판단을 요구.
    """
    notes = []
    mult = base_adj_mult
    if realized_vol is None:
        realized_vol = _realized_vol_annual("QQQ")
    vol_scale = 1.0
    if realized_vol and realized_vol > DCA_VOL_CAP_ANNUAL:
        vol_scale = round(DCA_VOL_CAP_ANNUAL / realized_vol, 3)
        mult *= vol_scale
        notes.append(f"변동성 캡: 연변동성 {realized_vol*100:.0f}% > {DCA_VOL_CAP_ANNUAL*100:.0f}% → 배율 ×{vol_scale}")
    halt = False
    if drawdown_pct is not None and drawdown_pct <= LEVERAGE_HALT_DRAWDOWN:
        halt = True
        if mult > 1.0:
            mult = 1.0
        notes.append(f"⛔ 낙폭 {drawdown_pct:.0f}% ≤ {LEVERAGE_HALT_DRAWDOWN:.0f}% — 레버리지 증액 정지(전소 방어). 수동 판단 필요")
    if mult > MAX_DCA_MULTIPLIER:
        notes.append(f"절대 상한 적용: ×{round(mult,2)} → ×{MAX_DCA_MULTIPLIER}")
        mult = MAX_DCA_MULTIPLIER
    # 의도적 불변식: 하한(1.0 floor)을 두지 않는다. 고점 국면의 0.5×(Bull-2)·0.8×(Bull-1)
    # 처럼 1.0 미만 배율은 '고점에서 DCA 축소'라는 설계다. 여기에 max(1.0, mult) 를 넣으면
    # 그 의도가 깨지므로 금지 (가드는 '리스크 축소'만, 최소 매수 강제는 하지 않음).
    return round(mult, 2), {
        "vol_scale": vol_scale,
        "realized_vol_pct": round(realized_vol * 100, 1) if realized_vol else 0.0,
        "dca_halt": halt,
        "safety_notes": notes,
    }


# ══════════════════════════════════════════════════════════════════════
#  Phase 상태 캐시 (중복 알림 방지)
# ══════════════════════════════════════════════════════════════════════

def load_phase_state() -> dict:
    """이전 Phase 상태 로드 (store 권위, 레거시 파일 자동 마이그레이션)."""
    data = store.load_doc("barbell_state", STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_phase_state(market_type: str, phase_key, drawdown: float):
    """현재 Phase 상태 저장 (store 권위 + 파일 미러 — healthcheck mtime 유지).
    크론·봇이 동시에 쓸 수 있으므로 store 트랜잭션 + atomic 미러."""
    state = {
        "last_run": datetime.now().isoformat(),
        "market_type": market_type,
        "phase_key": str(phase_key),
        "drawdown_pct": round(drawdown, 2),
    }
    store.save_doc("barbell_state", state, STATE_FILE)


def has_phase_changed(old_state: dict, market_type: str, phase_key) -> bool:
    """Phase 변화 감지 → True이면 텔레그램 알림 발송."""
    if not old_state:
        return False  # 첫 실행은 베이스라인만 저장, 알림 스킵
    return old_state.get("market_type") != market_type or old_state.get("phase_key") != str(phase_key)


# ══════════════════════════════════════════════════════════════════════
#  전략 로직
# ══════════════════════════════════════════════════════════════════════

# Bear Phase 진입 임계값 (drawdown %, 이하일 때 진입)
_BEAR_ENTRY_THR = {1: -5, 2: -10, 3: -15, 4: -20, 5: -30}
# 디에스컬레이션 버퍼: 진입 임계값보다 이만큼 더 회복해야 Phase 하향
_PHASE_EXIT_BUFFER_PP = 1.5

_PREV_STATE_AUTO = object()   # sentinel — 기본값이면 STATE_FILE에서 자동 로드


def _prev_bear_phase(prev_state: dict | None) -> int | None:
    """이전 상태에서 bear phase 번호 추출 (없으면 None)."""
    if not prev_state or prev_state.get("market_type") != "bear":
        return None
    try:
        return int(prev_state.get("phase_key"))
    except (TypeError, ValueError):
        return None


def classify_market(qqq_data: dict, rsi: float, vix: float,
                    prev_state: dict | None = _PREV_STATE_AUTO) -> tuple:
    """
    시장 상태 분류.
    Returns: (market_type, phase_key)
      market_type: "bull" | "neutral" | "bear"
      phase_key  : "bull_2" | "bull_1" | 0~5

    prev_state: 이전 Phase 상태 (히스테리시스용).
      - 기본값: STATE_FILE에서 자동 로드
      - None 명시: 히스테리시스 없이 원시 분류 (시뮬레이션용)
    Bear Phase 하향은 비가역 액션(SGOV 매도)을 동반하므로,
    경계값 +1.5%p 이상 회복해야만 하향한다 (whipsaw 방지).
    VIX가 공포 구간(≥30)이면 깊은 Phase(2+) 하향을 보류한다.
    """
    drawdown = qqq_data.get("drawdown_pct", 0)
    mom_1m = qqq_data.get("mom_1m_pct", 0)
    # 데이터 오류 판정은 OHLC 정합성으로만 — 낙폭 크기로 오류를 추정하면
    # 진짜 크래시(-80%대, 2000년 닷컴)를 neutral로 오분류한다
    if qqq_data.get("current", 0) <= 0 or qqq_data.get("high_52w", 0) <= 0:
        logger.warning("QQQ 데이터 비정상 — 시장 분류를 neutral로 처리: %s", qqq_data)
        return "neutral", 0

    # ── 원시 분류 ─────────────────────────────────────────────────────
    if drawdown <= -30:   raw = ("bear", 5)
    elif drawdown <= -20: raw = ("bear", 4)
    elif drawdown <= -15: raw = ("bear", 3)
    elif drawdown <= -10: raw = ("bear", 2)
    elif drawdown <= -5:  raw = ("bear", 1)
    # 고점 대비 -5% 이내: 상승/중립 판별
    elif rsi > RSI_EXTREME_OB and mom_1m > 8 and vix < VIX_LOW:
        raw = ("bull", "bull_2")
    elif rsi > RSI_OVERBOUGHT or mom_1m > 5:
        raw = ("bull", "bull_1")
    else:
        raw = ("neutral", 0)

    # ── 히스테리시스: bear phase 하향 시에만 적용 (상향·진입은 즉시) ──
    if prev_state is _PREV_STATE_AUTO:
        prev_state = load_phase_state()
    prev_phase = _prev_bear_phase(prev_state)
    if prev_phase is not None:
        raw_bear = raw[1] if raw[0] == "bear" else 0
        if raw_bear < prev_phase:
            recovered = drawdown > _BEAR_ENTRY_THR[prev_phase] + _PHASE_EXIT_BUFFER_PP
            vix_panic = prev_phase >= 2 and vix >= VIX_HIGH and drawdown <= -5
            if not recovered or vix_panic:
                return "bear", prev_phase

    return raw


def calculate_sgov_target(market_type: str, phase_key, portfolio_total_usd: float, sgov_current_usd: float) -> dict:
    """현재 시장 상태에 따른 SGOV 목표 비중 및 액션 계산."""
    if market_type == "bull":
        target_ratio = BULL_PHASES[phase_key]["sgov_target_ratio"]
    elif market_type == "neutral":
        target_ratio = TARGET_SGOV_RATIO
    else:
        target_ratio = BEAR_PHASES[phase_key]["sgov_target_ratio"]

    target_usd = portfolio_total_usd * target_ratio
    diff = target_usd - sgov_current_usd

    if diff > 50:
        action = f"SGOV 매수 필요: +${diff:.0f} (목표 ${target_usd:.0f})"
        direction = "buy"
    elif diff < -50:
        # 매도(전환) 신호는 레버리지 전환 단계(bear Phase 2+)에서만 —
        # Phase 0~1·bull·neutral의 초과분은 깊은 Phase 실탄이므로 보유
        deploying = (market_type == "bear"
                     and bool(BEAR_PHASES.get(phase_key, {}).get("leverage_target")))
        if deploying:
            action = f"SGOV 매도 필요: ${abs(diff):.0f} → 레버리지/DCA 전환"
            direction = "sell"
        else:
            action = f"SGOV 초과 ${abs(diff):.0f} — 실탄 유지 (Phase 2+ 레버리지 전환 대기)"
            direction = "hold"
    else:
        action = f"SGOV 적정 수준 유지 (현재 ${sgov_current_usd:.0f})"
        direction = "hold"

    return {
        "target_pct": round(target_ratio * 100, 1),
        "target_usd": round(target_usd, 2),
        "current_usd": round(sgov_current_usd, 2),
        "diff_usd": round(diff, 2),
        "action": action,
        "direction": direction,
    }


def _fg_dca_adjustment(fg_proxy: float) -> float:
    """Fear/Greed proxy 극단값 시 DCA 배율 조정 인자.

    극도공포(≤20): 1.2배 — 공포 구간에서 매수 증액
    극도탐욕(≥80): 0.8배 — 과열 구간에서 매수 축소
    그 외        : 1.0배 — 조정 없음
    """
    if fg_proxy < 0:       # 조회 실패
        return 1.0
    if fg_proxy <= 20:
        return 1.2
    if fg_proxy >= 80:
        return 0.8
    return 1.0


def _adaptive_advice_blend(base: float) -> float:
    """advice 적응 shadow(advice_blend_shadow.json) — **옵트인 시** OOS로 입증된 meta 배분으로 blend 상향.

    `ADAPTIVE_ADVICE_ENABLED=true` 일 때만. advice_adaptive_eval 이 meta(챌린저)가 rule(챔피언)을
    실현수익에서 이기고 하방 ≤ rule 일 때만 권고를 기록 → 그 값으로 blend 를 올리되 **기존 상한 0.6**
    내로 클램프(기존 _phase_blend_factor 가 bear 에서 내던 최대치를 넘지 않음 = 위험 envelope 불변).
    기본 off/파일 없음/오류 → base(라이브 불변). Phase 4 RL 을 end-to-end 실작동시키는 배선.
    """
    if os.getenv("ADAPTIVE_ADVICE_ENABLED", "false").lower() != "true":
        return base
    try:
        p = os.path.expanduser("~/reports/ml-cache/advice_blend_shadow.json")
        if not os.path.exists(p):
            return base
        with open(p, encoding="utf-8") as f:
            rec = float(json.load(f).get("blend", base))
        return max(base, min(0.6, rec))       # 입증된 meta 로 상향, 기존 0.6 상한 내
    except Exception as e:
        logger.warning("적응형 advice shadow 로드 실패 — blend 기본 유지: %s", e)
        return base


def _phase_blend_factor(market_type: str, phase_key) -> float:
    """Phase에 따라 ML 블렌딩 강도를 동적으로 결정.

    고평가(Bull-2)일수록 ML 영향 최소화,
    급락(Bear 3~5)일수록 ML 방향성 신뢰도 높여 강하게 반영.
    (옵트인 시 advice 적응 shadow 가 입증된 meta 쪽으로 blend 를 0.6 상한 내에서 상향.)
    """
    if market_type == "bull":
        base = {"bull_2": 0.1, "bull_1": 0.2}.get(str(phase_key), 0.25)
    elif market_type == "bear":
        base = {0: 0.3, 1: 0.35, 2: 0.45, 3: 0.55, 4: 0.60, 5: 0.60}.get(phase_key, 0.3)
    else:
        base = 0.3   # neutral
    return _adaptive_advice_blend(base)


def _ml_dca_blend(
    base_weights:  dict,
    market_type:   str = "neutral",
    phase_key      = 0,
    use_meta:      bool = True,
) -> tuple[dict, dict, float]:
    """ML 신호 통합 DCA 비중 조정.

    우선순위:
      1. MetaAllocator (5신호 통합) — weights가 있으면 우선 사용
      2. Ranker 단독 — MetaAllocator 실패 시 fallback

    use_meta=False: Ranker 단독만 사용.
    MetaAllocator 내부(_get_ranker_signal)에서 역호출할 때 필수 —
    True면 meta→ranker→meta 무한 상호 재귀가 발생한다.

    Returns (blended_weights, raw_scores, breadth_score)
    """
    import logging as _log
    _logger = _log.getLogger(__name__)
    NON_EQUITY = {"SGOV", "QQQI"}

    # ── 1. MetaAllocator 시도 ─────────────────────────────────────────────
    if use_meta:
        try:
            import numpy as np
            from ml.meta_allocator import get_meta_allocation

            alloc = get_meta_allocation(market_type, phase_key)
            meta_weights = alloc.weights   # {ticker: float}

            # MetaAllocator 비중이 있으면 blend
            if meta_weights:
                blend = _phase_blend_factor(market_type, phase_key)
                blended: dict[str, float] = {}
                for t in base_weights:
                    base = base_weights[t]
                    ml_w = meta_weights.get(t, base)
                    blended[t] = base * (1 - blend) + ml_w * blend

                total = sum(blended.values())
                if total > 0:
                    blended = {k: round(v / total, 4) for k, v in blended.items()}

                # 시장 강도: regime 방향 × 신호 일치도
                # (confidence는 신호 일치도(0~1)일 뿐 방향이 아님 — risk_off 확신 시 음수가 되어야 함)
                direction = {"risk_on": 1.0, "risk_off": -1.0}.get(alloc.regime, 0.0)
                breadth = direction * alloc.confidence * 0.01   # -0.01 ~ +0.01 스케일
                _logger.info(
                    "MetaAllocator DCA 블렌딩 완료 — 체제: %s (신뢰도 %.0f%%, blend=%.2f)",
                    alloc.regime, alloc.confidence * 100, blend,
                )
                return blended, alloc.signal_summary, breadth

        except Exception as e:
            _logger.warning("MetaAllocator 실패, Ranker fallback: %s", e)

    # ── 2. Ranker fallback ────────────────────────────────────────────────
    try:
        import numpy as np, pandas as pd
        from ml.ranker import load_ranker
        from ml.data_pipeline import fetch_prices, build_stock_features, build_fear_greed_proxy

        result = load_ranker()
        if result is None:
            return base_weights, {}, 0.0

        equity = [t for t in base_weights if t not in NON_EQUITY]
        non_eq = {t: w for t, w in base_weights.items() if t in NON_EQUITY}

        prices    = fetch_prices(equity + ["QQQ","^VIX","HYG","LQD","IEF","TLT"], days=300)
        fg        = build_fear_greed_proxy(days=300)
        mkt       = fg.to_frame("fg_score")
        if "^VIX" in prices:
            mkt["vix"] = prices["^VIX"]["Close"]
        mkt       = mkt.ffill()
        qqq_close = prices.get("QQQ", pd.DataFrame()).get("Close")

        scores: dict[str, float] = {}
        for ticker in equity:
            df = prices.get(ticker)
            if df is None or len(df) < 60:
                continue
            feat = build_stock_features(ticker, df, mkt, qqq_close=qqq_close)
            if feat.empty:
                continue
            clean = feat.dropna()
            if clean.empty:
                continue
            row = clean.iloc[-1].reindex(result.feature_names)
            if row.isna().any():
                continue
            scores[ticker] = float(result.model.predict(row.to_frame().T)[0])

        if not scores:
            return base_weights, {}, 0.0

        # breadth는 _ml_breadth_mult에서 수익률 단위(±0.5%) 임계와 비교됨 —
        # LGBMRanker(lambdarank) 점수는 임의 스케일이라 평균이 의미 없음 → 0 처리
        # (종목 간 상대 틸트는 아래 min-max 정규화로 스케일 무관하게 계속 적용)
        if type(result.model).__name__ == "LGBMRanker":
            breadth = 0.0
        else:
            breadth = float(np.mean(list(scores.values())))
        s_arr   = np.array(list(scores.values()))
        s_min, s_max = s_arr.min(), s_arr.max()
        if s_max == s_min:
            return base_weights, scores, breadth
        percs = {t: (s - s_min) / (s_max - s_min) for t, s in scores.items()}

        blend = _phase_blend_factor(market_type, phase_key)
        blended2: dict[str, float] = {}
        for t in equity:
            base = base_weights.get(t, 0.0)
            adj  = 1.0 + blend * percs.get(t, 0.5) - blend / 2
            blended2[t] = base * adj
        blended2.update(non_eq)

        total = sum(blended2.values())
        if total > 0:
            blended2 = {k: round(v / total, 4) for k, v in blended2.items()}

        return blended2, scores, breadth

    except Exception as e:
        _logger.warning("ML DCA 블렌딩 완전 실패: %s", e)
        return base_weights, {}, 0.0


def _ml_breadth_mult(breadth: float) -> tuple[float, str]:
    """ML 시장 강도 점수 → DCA 배율 보정 인자."""
    if breadth > 0.005:
        return 1.1, f"ML 강세 ({breadth*100:+.2f}%)"
    if breadth < -0.010:
        return 0.8, f"ML 약세 ({breadth*100:+.2f}%)"
    if breadth < -0.005:
        return 0.9, f"ML 약세 ({breadth*100:+.2f}%)"
    return 1.0, ""


def calculate_dca(market_type: str, phase_key, exchange_rate: float = 1380.0,
                  drawdown_pct=None) -> dict:
    """시장 상태별 DCA 금액 및 종목 배분 (원화 + USD 환산).

    보정 순서: Phase 기본배율 × F&G proxy × ML 시장강도 × (종목별 ML 비중 블렌딩)
              → 안전 가드(변동성 캡·절대 상한·낙폭 정지, leverage_dca_guard)
    drawdown_pct 를 주면 낙폭 정지(전소 방어)까지 적용된다.
    """
    w_normal, w_bear = load_dca_weights()

    if market_type == "bull":
        mult    = BULL_PHASES[phase_key]["dca_multiplier"]
        weights = w_normal
    elif market_type == "neutral":
        mult    = BEAR_PHASES[0]["dca_multiplier"]
        weights = w_normal
    else:
        mult    = BEAR_PHASES[phase_key]["dca_multiplier"]
        weights = w_bear if phase_key >= 2 else w_normal

    # Fear/Greed proxy 보정
    try:
        from ml.data_pipeline import get_fg_proxy_score
        fg_proxy = get_fg_proxy_score()
    except Exception:
        fg_proxy = -1.0
    # Phase가 이미 극단 공포/탐욕을 반영하는 구간(bear 2+, bull_2)에서는
    # F&G 보정 생략 — 상관된 두 신호를 곱하면 극단 구간에서 과잉 증폭됨
    extreme_phase = (
        (market_type == "bull" and phase_key == "bull_2")
        or (market_type == "bear" and isinstance(phase_key, int) and phase_key >= 2)
    )
    fg_adj = 1.0 if extreme_phase else _fg_dca_adjustment(fg_proxy)

    # ML 비중 블렌딩 + 시장강도 보정
    ml_weights, ml_scores, breadth = _ml_dca_blend(weights, market_type, phase_key)
    ml_mult, ml_label = _ml_breadth_mult(breadth)

    # 최종 배율 (Phase × F&G × ML강도)
    adj_mult = round(mult * fg_adj * ml_mult, 2)

    # 안전 가드: 변동성 캡 · 절대 상한 · 낙폭 정지 (비평 #1 — 레버리지 마틴게일 폭주 차단)
    adj_mult, _safety = leverage_dca_guard(adj_mult, drawdown_pct=drawdown_pct)

    total_krw  = int(DCA_DAILY_BASE_KRW * adj_mult)
    total_usd  = round(total_krw / exchange_rate, 2)

    # ML 비중 기반 배분 + 원래 비중 방향 표시
    allocation     = {t: int(total_krw * w) for t, w in ml_weights.items()}
    # int 절사로 남는 잔여 원화 → 최대 비중 종목에 가산 (합계 = total_krw 보장)
    if allocation and sum(ml_weights.values()) > 0.999:
        remainder = total_krw - sum(allocation.values())
        if remainder > 0:
            top = max(ml_weights, key=ml_weights.get)
            allocation[top] += remainder
    base_alloc     = {t: int(total_krw * w) for t, w in weights.items()}
    ml_direction   = {}   # ticker → "↑ML" / "↓ML" / ""
    for t in weights:
        base_w = weights.get(t, 0)
        ml_w   = ml_weights.get(t, 0)
        if ml_w > base_w * 1.05:
            ml_direction[t] = "↑ML"
        elif ml_w < base_w * 0.95:
            ml_direction[t] = "↓ML"
        else:
            ml_direction[t] = ""

    return {
        "total_krw":     total_krw,
        "total_usd":     total_usd,
        "multiplier":    adj_mult,
        "base_mult":     mult,
        "fg_proxy":      round(fg_proxy, 1),
        "fg_adj":        fg_adj,
        "ml_mult":       ml_mult,
        "ml_label":      ml_label,
        "ml_scores":     ml_scores,
        "ml_breadth":    round(breadth * 100, 3),   # % 단위
        "ml_direction":  ml_direction,
        "by_ticker":     allocation,
        "base_by_ticker": base_alloc,
        "exchange_rate": exchange_rate,
        # 안전 가드 메타 (리포트·주문서에 경고 노출용)
        "vol_scale":     _safety["vol_scale"],
        "realized_vol_pct": _safety["realized_vol_pct"],
        "dca_halt":      _safety["dca_halt"],
        "safety_notes":  _safety["safety_notes"],
    }


# ══════════════════════════════════════════════════════════════════════
#  스마트 리밸런싱 — 안전마진 + 종목별 비중 분석
# ══════════════════════════════════════════════════════════════════════

def calculate_position_analysis(portfolio: dict) -> list[dict]:
    """
    종목별 현재 비중 vs 목표 비중 비교.
    - target_weights.json 에서 목표 로드 (없으면 DCA 비중으로 자동 추론)
    - portfolio_snapshot.json 손익 데이터 + 실시간 가격 활용
    - 보유 중인 모든 종목 자동 포함 (신규 종목도 즉시 분석)
    """
    total    = portfolio.get("total_usd", 1)
    prices   = portfolio.get("prices", {})
    holdings = portfolio.get("holdings", {})

    # 동적 목표 비중 로드 (보유 종목 기반 자동 추론 포함)
    target_map = load_target_weights(portfolio)

    # 스냅샷에서 평단가·손익 보조
    pnl_map: dict[str, float] = {}
    avg_map: dict[str, float] = {}
    note_map: dict[str, str]  = {}
    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        for h in snap.get("overseas_general", {}).get("holdings_usd", []):
            t = h["ticker"]
            pnl_map[t]  = float(h.get("pnl_usd", 0))
            avg_map[t]  = float(h.get("avg_price_usd", 0))
            if h.get("note"):
                note_map[t] = h["note"]
        for h in snap.get("overseas_fractional", {}).get("holdings", []):
            t = h["ticker"]
            pnl_map[t] = pnl_map.get(t, 0) + float(h.get("pnl_usd", 0))
    except Exception:
        pass

    # 분석 대상: 보유 중 + 목표 설정된 모든 종목
    all_tickers = (
        set(target_map.keys())
        | set(holdings.keys())
        | set(pnl_map.keys())
    ) - _SKIP_TICKERS

    explicit_targets = load_target_weights()  # 명시적 목표만 (신규 종목 감지용)
    results = []
    for ticker in sorted(all_tickers):
        price     = prices.get(ticker, 0)
        shares    = holdings.get(ticker, 0)
        val       = shares * price if price > 0 else 0
        target_w  = target_map.get(ticker, 0.0)
        current_w = val / total if total > 0 else 0
        diff_w    = current_w - target_w
        pnl       = pnl_map.get(ticker, 0.0)
        avg_price = avg_map.get(ticker, 0.0)
        note      = note_map.get(ticker, "")

        # 행동 제안
        if target_w == 0 and val > 0:
            action, direction = "목표 없음 — 정리 또는 목표 설정 권장", "sell"
        elif diff_w > 0.04:
            action, direction = f"익절 ${diff_w * total:.0f} 검토", "sell"
        elif diff_w > 0.02:
            action, direction = "DCA 일시 중단 or 소폭 익절", "trim"
        elif diff_w < -0.03:
            action, direction = "DCA 우선 배정", "buy"
        elif diff_w < -0.015:
            action, direction = "DCA 소폭 증가", "add"
        else:
            action, direction = "적정 — 유지", "hold"

        # 신규 종목 태그
        is_new = ticker not in explicit_targets
        tag    = " 🆕" if is_new and val > 0 else ""

        results.append({
            "ticker":      ticker,
            "val":         round(val, 2),
            "current_pct": round(current_w * 100, 1),
            "target_pct":  round(target_w * 100, 1),
            "diff_pct":    round(diff_w * 100, 1),
            "pnl":         round(pnl, 2),
            "avg_price":   round(avg_price, 2),
            "action":      action,
            "direction":   direction,
            "note":        note + tag,
        })

    return sorted(results, key=lambda x: abs(x["diff_pct"]), reverse=True)


def calculate_safety_margin(portfolio: dict, market_type: str, phase_key) -> dict:
    """
    안전마진 점수 (0~100).
    전략을 얼마나 공격적으로 실행할지 판단하는 종합 계수.

    감점: 종목 집중도, 손실 포지션 보유
    가점: 미실현 이익 쿠션, SGOV 충분, 분산도
    """
    total = portfolio.get("total_usd", 1)
    prices = portfolio.get("prices", {})
    holdings = portfolio.get("holdings", {})

    score   = 70.0   # 기본점수
    factors = {}

    # ── 1. 종목 집중도 (HHI) ─────────────────────────────────────────
    stock_ws = []
    for t, sh in holdings.items():
        # SPMO는 모멘텀 팩터 ETF — 분산 효과가 제한적이므로 집중도 계산에 포함
        if t in ("SGOV", "QQQI", "QLD", "TQQQ", "BIL", "SHV"):
            continue
        p = prices.get(t, 0)
        if p > 0:
            stock_ws.append(sh * p / total)

    if stock_ws:
        hhi = sum(w ** 2 for w in stock_ws)
        if hhi > 0.15:
            penalty = min((hhi - 0.15) * 100, 20)
            score  -= penalty
            factors["집중도 과다"] = f"-{penalty:.0f}점  (HHI {hhi:.2f})"
        else:
            bonus = (0.15 - hhi) * 60
            score += min(bonus, 10)
            factors["분산 양호"] = f"+{min(bonus,10):.0f}점  (HHI {hhi:.2f})"

    # ── 2. 손실 포지션 + 3. 미실현 이익 쿠션 ────────────────────────
    snap = {}
    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception:
        pass

    overseas = snap.get("overseas_general", {}).get("holdings_usd", [])
    for h in overseas:
        pnl = float(h.get("pnl_usd", 0))
        if pnl < -40:
            penalty = min(abs(pnl) / total * 200, 12)
            score  -= penalty
            factors[f"손실포지션 {h['ticker']}"] = f"-{penalty:.0f}점 (손실 ${abs(pnl):.0f})"

    # ── 3. 미실현 이익 쿠션 ─────────────────────────────────────────
    total_gain = sum(float(h.get("pnl_usd", 0)) for h in overseas if float(h.get("pnl_usd", 0)) > 0)
    gain_ratio = total_gain / total if total > 0 else 0
    if gain_ratio > 0.05:
        bonus = min(gain_ratio * 80, 15)
        score += bonus
        factors["미실현이익 쿠션"] = f"+{bonus:.0f}점 (+${total_gain:.0f}, {gain_ratio*100:.1f}%)"

    # ── 4. SGOV 충분도 ────────────────────────────────────────────────
    if market_type == "bull":
        target_sgov_r = BULL_PHASES[phase_key]["sgov_target_ratio"]
    elif market_type == "neutral":
        target_sgov_r = TARGET_SGOV_RATIO
    else:
        target_sgov_r = BEAR_PHASES[phase_key].get("sgov_target_ratio", TARGET_SGOV_RATIO)

    sgov_r = portfolio.get("sgov_usd", 0) / total
    diff_sgov = sgov_r - target_sgov_r
    if diff_sgov > 0.01:
        bonus = min(diff_sgov * 120, 10)
        score += bonus
        factors["SGOV 충분"] = f"+{bonus:.0f}점 ({sgov_r*100:.1f}% / 목표 {target_sgov_r*100:.0f}%)"
    elif diff_sgov < -0.03:
        penalty = min(abs(diff_sgov) * 80, 10)
        score  -= penalty
        factors["SGOV 부족"] = f"-{penalty:.0f}점 ({sgov_r*100:.1f}% / 목표 {target_sgov_r*100:.0f}%)"

    score = max(20, min(100, score))

    if score >= 80:
        grade, emoji = "전략 100% 실행", "🟢"
    elif score >= 65:
        grade, emoji = "전략 80% 실행 권장", "🟡"
    elif score >= 50:
        grade, emoji = "전략 60% 실행 권장", "🟠"
    else:
        grade, emoji = "방어 우선 — 전략 완화", "🔴"

    return {
        "score":     round(score),
        "grade":     grade,
        "emoji":     emoji,
        "factors":   factors,
        # 기본점수 70점 = 1.0 (계획 100% 실행). score/100이면 평범한 날에도
        # 항상 30% 감액되는 구조적 언더슈팅이 발생함. 감액 전용 — 증액은 안 함.
        "multiplier": round(min(1.0, max(0.5, score / 70)), 2),
    }


def calculate_smart_rebalancing(
    portfolio: dict,
    market_type: str,
    phase_key,
    exchange_rate: float = 1380.0,
) -> dict:
    """
    안전마진 + 비중 분석 기반 스마트 리밸런싱.
    - 포지션 과/부족 진단
    - 안전마진 점수로 DCA 배율 조정
    - 비중 불균형 종목에 DCA 재배분
    """
    safety    = calculate_safety_margin(portfolio, market_type, phase_key)
    positions = calculate_position_analysis(portfolio)
    sgov      = calculate_sgov_target(market_type, phase_key, portfolio["total_usd"], portfolio["sgov_usd"])
    base_dca  = calculate_dca(market_type, phase_key, exchange_rate)

    # 안전마진으로 DCA 금액 조정
    adj_mult      = safety["multiplier"]
    adj_total_krw = int(base_dca["total_krw"] * adj_mult)

    # 비중 불균형 반영하여 종목별 DCA 가중치 재조정
    w_normal, _ = load_dca_weights()
    adj_weights  = dict(w_normal)

    for pos in positions:
        t = pos["ticker"]
        if t not in adj_weights:
            continue
        d = pos["diff_pct"]
        if d > 3:       # 초과 비중 → DCA 감소
            adj_weights[t] *= max(0.2, 1 - d / 25)
        elif d < -2:    # 부족 비중 → DCA 증가
            adj_weights[t] *= min(2.5, 1 + abs(d) / 12)

    w_sum = sum(adj_weights.values())
    if w_sum > 0:
        adj_weights = {k: round(v / w_sum, 4) for k, v in adj_weights.items()}

    adj_dca = {t: int(adj_total_krw * w) for t, w in adj_weights.items()}

    return {
        "safety":         safety,
        "positions":      positions,
        "sgov":           sgov,
        "base_dca_krw":   base_dca["total_krw"],
        "adj_dca_krw":    adj_total_krw,
        "adj_multiplier": adj_mult,
        "adj_weights":    adj_weights,
        "adj_dca":        adj_dca,
        "exchange_rate":  exchange_rate,
    }


def build_smart_report(portfolio: dict, market_type: str, phase_key,
                        exchange_rate: float = 1380.0) -> str:
    """스마트 리밸런싱 전용 텔레그램 출력."""
    result = calculate_smart_rebalancing(portfolio, market_type, phase_key, exchange_rate)
    s  = result["safety"]
    sg = result["sgov"]
    L  = [
        "⚖️ 스마트 리밸런싱 분석",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # ── 안전마진 점수 ─────────────────────────────────────────────────
    bar = _bar(s["score"] / 100, 12)
    L += [
        f"  {s['emoji']} 안전마진  {s['score']}점  {bar}",
        f"  → {s['grade']}",
        "",
    ]
    for factor, detail in s["factors"].items():
        L.append(f"    {factor}: {detail}")

    # ── 종목 비중 진단 ────────────────────────────────────────────────
    L += ["", "━━━ 종목별 비중 진단 ━━━━━━━━━━━━━━━━━━━"]
    for p in result["positions"]:
        diff  = p["diff_pct"]
        arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "─")
        bar_c = _bar(p["current_pct"] / 15, 6)
        bar_t = _bar(p["target_pct"] / 15, 6)
        pnl_s = f"  P&L ${p['pnl']:+.0f}" if p["pnl"] != 0 else ""
        L.append(
            f"  {p['ticker']:<6}  현재 {p['current_pct']:>4.1f}% {bar_c}  "
            f"목표 {p['target_pct']:>4.1f}% {bar_t}  "
            f"{arrow}{abs(diff):.1f}%p{pnl_s}"
        )
        if p["direction"] != "hold":
            L.append(f"    → {p['action']}")

    # ── SGOV ────────────────────────────────────────────────────────
    L += [
        "", "━━━ SGOV 실탄 ━━━━━━━━━━━━━━━━━━━━━━",
        f"  현재 ${sg['current_usd']:>7,.0f}  목표 ${sg['target_usd']:>7,.0f}  ({sg['target_pct']}%)",
        f"  → {sg['action']}",
    ]

    # ── 조정된 DCA ───────────────────────────────────────────────────
    L += [
        "", f"━━━ 조정 DCA  {result['adj_dca_krw']:,}원  "
            f"(기본 {result['base_dca_krw']:,}원 × {result['adj_multiplier']:.0%}) ━━━",
    ]
    max_amt = max(result["adj_dca"].values()) if result["adj_dca"] else 1
    for ticker, amt in result["adj_dca"].items():
        bar  = _bar(amt / max_amt, 8)
        usd  = round(amt / exchange_rate, 1)
        orig = int(result["base_dca_krw"] * result["adj_weights"].get(ticker, 0))
        diff_amt = amt - orig
        diff_s = f"  ({diff_amt:+,}원)" if abs(diff_amt) > 100 else ""
        L.append(f"  {ticker:<6}  {bar}  {amt:,}원  ${usd:.1f}{diff_s}")

    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════
#  시각화 헬퍼
# ══════════════════════════════════════════════════════════════════════

def _display_width(s: str) -> int:
    """문자열의 실제 표시 폭 (CJK=2, 이모지=2, ASCII=1)."""
    width = 0
    for ch in s:
        cp = ord(ch)
        if cp < 128:
            width += 1
        elif cp >= 0x1F000:
            width += 2
        else:
            ea = unicodedata.east_asian_width(ch)
            width += 2 if ea in ('W', 'F') else 1
    return width


def _dw_pad(s: str, target_width: int) -> str:
    """표시 폭 기준 우측 공백 패딩."""
    pad = max(0, target_width - _display_width(s))
    return s + ' ' * pad


def _bar(ratio: float, width: int = 10, fill: str = "█", empty: str = "░") -> str:
    """비율(0~1) → 채워진 막대."""
    r = max(0.0, min(1.0, ratio))
    n = round(r * width)
    return fill * n + empty * (width - n)


def _phase_meter(market_type: str, phase_key) -> str:
    """Phase 위치 표시기 (레이블 + 이모지 두 줄)."""
    LABELS = ["B2", "B1", "N0", "P1", "P2", "P3", "P4", "P5"]
    EMOJIS = ["🫧", "🐂", "🟢", "🟡", "🟠", "🔴", "🚨", "💥"]
    if market_type == "neutral":
        idx = 2
    elif market_type == "bull":
        idx = 0 if phase_key == "bull_2" else 1
    else:
        idx = int(phase_key) + 2

    label_row = "  ".join(f"[{l}]" if i == idx else f" {l} " for i, l in enumerate(LABELS))
    emoji_row = "   ".join(f"◉{e}" if i == idx else f" {e}" for i, e in enumerate(EMOJIS))
    return label_row + "\n" + emoji_row


def _drawdown_ruler(drawdown_pct: float, width: int = 22) -> str:
    """낙폭 위치를 눈금자로 표시 (-30% ~ 0%)."""
    ratio = max(0.0, min(1.0, (drawdown_pct + 30) / 30))
    pos = round(ratio * width)
    ruler = "─" * pos + "●" + "─" * (width - pos)
    return f"  ◄{ruler}►\n  -30%{'':<{width - 3}}0%"


def _rsi_visual(rsi: float) -> str:
    bar = _bar(rsi / 100, 12)
    if rsi < RSI_OVERSOLD:        label = "과매도 🔥"
    elif rsi < RSI_NEAR_OVERSOLD: label = "약세 ⚠️"
    elif rsi > RSI_EXTREME_OB:    label = "극과매수 🫧"
    elif rsi > RSI_OVERBOUGHT:    label = "과매수 🌡"
    else:                         label = "중립 ✅"
    return f"  RSI  {rsi:5.1f}  {bar}  {label}"


def _vix_visual(vix: float) -> str:
    bar = _bar(min(1.0, vix / 50), 12)
    if vix > VIX_EXTREME:  label = "극단공포 💥"
    elif vix > VIX_HIGH:   label = "공포 🚨"
    elif vix < VIX_LOW:    label = "과낙관 😴"
    else:                  label = "정상 ✅"
    return f"  VIX  {vix:5.1f}  {bar}  {label}"


def _fear_greed_visual(fg: dict) -> str:
    """CNN Fear & Greed 점수 → 시각화 한 줄."""
    score  = fg.get("score", 50.0)
    prev_w = fg.get("prev_week", score)
    diff   = score - prev_w
    d_abs  = abs(diff)
    trend  = f"▲+{d_abs:.0f}" if diff > 0.5 else (f"▼-{d_abs:.0f}" if diff < -0.5 else "─")

    if score <= 25:    emoji, label = "💀", "극단공포"
    elif score <= 45:  emoji, label = "😨", "공포"
    elif score <= 55:  emoji, label = "😐", "중립"
    elif score <= 75:  emoji, label = "😄", "탐욕"
    else:              emoji, label = "🤑", "극단탐욕"

    bar = _bar(score / 100, 12)
    return f"  F&G {score:5.1f}  {bar}  {emoji} {label} (1W:{trend})"


def _sgov_compare(current: float, target: float) -> list:
    """SGOV 현재/목표 비교 막대 두 줄."""
    scale = max(current, target, 1) * 1.05
    bar_c = _bar(current / scale, 12)
    bar_t = _bar(target / scale, 12)
    arrow = "↑ 매수" if target > current + 50 else ("↓ 매도" if current > target + 50 else "= 유지")
    return [
        f"  현재  ${current:>7,.0f}  {bar_c}",
        f"  목표  ${target:>7,.0f}  {bar_t}  {arrow}",
    ]


def _dca_rows(by_ticker: dict, total_krw: int, exchange_rate: float) -> list:
    """DCA 종목별 배분 막대."""
    if not by_ticker:
        return []
    max_amt = max(by_ticker.values())
    rows = []
    for ticker, amt in by_ticker.items():
        bar = _bar(amt / max_amt if max_amt > 0 else 0, 8)
        pct = round(amt / total_krw * 100) if total_krw > 0 else 0
        usd = round(amt / exchange_rate, 1)
        rows.append(f"  {ticker:<5}  {bar}  {amt:,}원  ${usd:.1f}  ({pct}%)")
    return rows


# ══════════════════════════════════════════════════════════════════════
#  리포트 섹션 빌더 (순수 추출 — build_report 가 L += 로 조립)
# ══════════════════════════════════════════════════════════════════════

def _section_header(now: str, old_phase_state: dict, market_type: str,
                    phase_key, drawdown: float) -> list:
    """헤더 + Phase 변화 경보 (최상단)."""
    # ── 헤더 ─────────────────────────────────────────────────────────
    L = [
        "🏋️ Intelligence Barbell v2.1",
        f"📅 {now}",
    ]

    # Phase 변화 경보 (최상단)
    if old_phase_state and has_phase_changed(old_phase_state, market_type, phase_key):
        old_t  = old_phase_state.get("market_type", "?")
        old_k  = old_phase_state.get("phase_key", "?")
        old_dd = old_phase_state.get("drawdown_pct", 0)
        L += [
            "",
            "╔══════════════════════════════╗",
            "║  ⚡ PHASE 변화 감지!           ║",
            f"║  {old_t}/{old_k} ({old_dd:+.1f}%)  →  {market_type}/{phase_key} ({drawdown:+.1f}%)  ║",
            "╚══════════════════════════════╝",
        ]
    return L


def _section_phase_meter(p_info: dict, market_type: str, phase_key,
                         drawdown: float) -> list:
    """Phase 미터."""
    # ── Phase 미터 ────────────────────────────────────────────────────
    return [
        "",
        f"📍 Phase  {p_info['emoji']} {p_info['label']}",
        _phase_meter(market_type, phase_key),
        f"  QQQ 고점 대비  {drawdown:+.2f}%   {p_info['description']}",
    ]


def _section_portfolio(portfolio: dict, total_krw: int, exchange_rate: float) -> list:
    """포트폴리오 요약 + 레버리지 포지션."""
    # ── 포트폴리오 요약 ───────────────────────────────────────────────
    sgov_ratio = portfolio["sgov_usd"] / portfolio["total_usd"] if portfolio["total_usd"] > 0 else 0
    qqqi_ratio = portfolio["qqqi_usd"] / portfolio["total_usd"] if portfolio["total_usd"] > 0 else 0

    L = [
        "",
        "━━━ 💼 포트폴리오 ━━━",
        f"  총액  ${portfolio['total_usd']:>8,.2f}   (₩{total_krw:,})",
        f"  환율  {exchange_rate:,.1f}원/USD",
        f"  SGOV  ${portfolio['sgov_usd']:>7,.2f}   {_bar(sgov_ratio, 10)}  {sgov_ratio*100:.1f}%  실탄",
        f"  QQQI  ${portfolio['qqqi_usd']:>7,.2f}   {_bar(min(qqqi_ratio / 0.35, 1), 10)}  {qqqi_ratio*100:.1f}%  배당엔진",
    ]

    # 레버리지 포지션
    leverage   = load_leverage_state()
    lev_prices = portfolio.get("prices", {})
    has_lev    = False
    for ticker, pos in leverage.items():
        sh = pos.get("shares", 0)
        if sh > 0:
            has_lev = True
            avg   = pos.get("avg_price_usd", 0)
            price = lev_prices.get(ticker, avg)
            val   = sh * price
            pnl   = (price - avg) / avg * 100 if avg > 0 else 0
            sign  = "+" if pnl >= 0 else ""
            L.append(f"  {ticker}    ${val:>7,.0f}   {sh}주 @${avg:.2f}  {sign}{pnl:.1f}%")
    if not has_lev:
        L.append("  레버리지  미보유  (Phase 2+ 진입 시 QLD 매수)")
    return L


def _section_qqq_radar(qqq_data: dict, ma_data: dict, drawdown: float,
                       rsi: float, vix: float, fear_greed: dict,
                       regime_ln: str | None = None) -> list:
    """QQQ 레이더."""
    # ── QQQ 레이더 ────────────────────────────────────────────────────
    pos_52w = qqq_data.get("position_52w_pct", 50)
    mom_1m  = qqq_data.get("mom_1m_pct", 0)
    mom_3m  = qqq_data.get("mom_3m_pct", 0)
    ma_gap  = ma_data.get("gap_pct", 0)
    ma_icon = "✅" if ma_data.get("above_ma200", True) else "❌ MA 이탈!"

    return [
        "",
        "━━━ 📈 QQQ 레이더 ━━━",
    ] + ([regime_ln] if regime_ln else []) + [
        f"  현재가  ${qqq_data.get('current', 0):>8,.2f}   52주高 ${qqq_data.get('high_52w', 0):,.2f}  低 ${qqq_data.get('low_52w', 0):,.2f}",
        f"  낙폭    {drawdown:>+7.2f}%   52주위치 {_bar(pos_52w / 100, 12)} {pos_52w:.0f}%",
        _drawdown_ruler(drawdown),
        f"  1M {mom_1m:>+6.1f}%  3M {mom_3m:>+6.1f}%",
        _rsi_visual(rsi),
        _vix_visual(vix),
        _fear_greed_visual(fear_greed or {}),
        f"  200MA   {ma_gap:>+6.1f}%  {ma_icon}",
    ]


def _section_sgov(sgov: dict) -> list:
    """SGOV 실탄."""
    # ── SGOV 실탄 ─────────────────────────────────────────────────────
    return [
        "",
        "━━━ 🛡 SGOV 실탄 ━━━",
    ] + _sgov_compare(sgov["current_usd"], sgov["target_usd"]) + [
        f"  목표 {sgov['target_pct']}%  |  차이 ${sgov['diff_usd']:+,.0f}",
        f"  → {sgov['action']}",
    ]


def _section_qqqi_dividend(qqqi_div: dict, market_type: str, phase_key) -> list:
    """QQQI 배당 파이프라인."""
    # ── QQQI 배당 파이프라인 ──────────────────────────────────────────
    per_s = f"  주당 ${qqqi_div['per_share']:.4f} |" if qqqi_div.get("per_share") else ""
    if market_type == "bull":
        div_act = "배당 50% → SGOV 비축,  50% → DCA"
    elif market_type == "bear" and isinstance(phase_key, int) and phase_key >= 2:
        div_act = "배당 전액 → QLD/TQQQ 재투자"
    else:
        div_act = "배당 전액 → 소수점 DCA 재투자"

    return [
        "",
        "━━━ 💰 QQQI 배당 ━━━",
        f"  월 ${qqqi_div['monthly_usd']:.2f}{per_s}  연 {qqqi_div['annual_yield_pct']:.1f}%  ({qqqi_div['note']})",
        f"  → {div_act}",
    ]


def _section_action_items(p_info: dict) -> list:
    """행동 지침."""
    # ── 행동 지침 ─────────────────────────────────────────────────────
    L = ["", "━━━ 📋 행동 지침 ━━━"]
    for i, act in enumerate(p_info["action_items"], 1):
        L.append(f"  {i}. {act}")
    return L


def _section_dca(dca: dict, exchange_rate: float, market_type: str, phase_key) -> list:
    """DCA 배분 막대 + 안전 가드 경고."""
    # ── DCA 배분 막대 ─────────────────────────────────────────────────
    L = [
        "",
        f"━━━ 💸 DCA  {dca['total_krw']:,}원  (${dca['total_usd']:.2f} @ {exchange_rate:,.0f}원)  [{dca['multiplier']}x] ━━━",
    ] + _dca_rows(dca["by_ticker"], dca["total_krw"], exchange_rate)
    # 안전 가드 발동 시 경고 노출 (변동성 캡·낙폭 정지 — 비평 #1)
    for _note in dca.get("safety_notes", []):
        L.append(f"  🛡 {_note}")
    if (market_type == "bear" and isinstance(phase_key, int) and phase_key >= 4):
        L.append("  ⚠️ 레버리지(QLD/TQQQ) 권고는 *수동 승인 필요* — 자동 매매 아님. "
                 "3x ETF는 변동성 끌림으로 장기보유 시 손실 누적.")
    return L


def _section_special_alerts(market_type: str, phase_key, portfolio: dict,
                            sgov: dict) -> list:
    """특수 경고."""
    # ── 특수 경고 ─────────────────────────────────────────────────────
    alerts = []
    if market_type == "bull" and phase_key == "bull_2":
        hot = [
            f"{h['ticker']} {h['return_pct']:+.0f}%"
            for h in portfolio.get("holdings_detail", [])
            if h.get("ticker") not in _SKIP_TICKERS
            and isinstance(h.get("return_pct"), (int, float))
            and (h.get("return_pct") or 0) >= 30
        ]
        if hot:
            alerts.append(f"⚡ 과열 익절 검토: {', '.join(hot[:3])} — SGOV 비축 최우선")
    if market_type == "bear" and isinstance(phase_key, int) and phase_key >= 3:
        loss = [
            f"{h['ticker']} {h['return_pct']:+.0f}%"
            for h in portfolio.get("holdings_detail", [])
            if h.get("ticker") not in _SKIP_TICKERS
            and isinstance(h.get("return_pct"), (int, float))
            and (h.get("return_pct") or 0) <= -10
        ]
        if loss:
            alerts.append(f"⚡ 손절 검토: {', '.join(loss[:3])} — 재원 QLD/TQQQ 재배치")
    if market_type == "bull" and sgov["direction"] == "buy":
        alerts.append("💡 QQQI 배당금 → SGOV 우선 비축 (강세장 실탄 적립)")
    if alerts:
        return [""] + alerts
    return []


# ══════════════════════════════════════════════════════════════════════
#  리포트 생성
# ══════════════════════════════════════════════════════════════════════

def detect_regime(drawdown_pct: float | None = None) -> dict | None:
    """현재 QQQ 추세/횡보 레짐 진단 (ml.regime_classifier — Kaufman ER 기반).

    캐시된 QQQ 1y 종가(_history_cached)를 재사용 — 핫패스에서 추가 네트워크 없음.
    종가 부족(<220)·오류 시 None (호출부에서 레짐 줄을 생략 → 기존 동작 보존).
    drawdown_pct(%) 주입 시 깊은 bear 게이트(횡보 아님)에 사용한다.

    주의: 감지·표시 전용. 라이브 배분(Phase·DCA·레버리지)은 이 결과로 바꾸지 않는다
    (Phase 1B 백테스트 게이트가 US 횡보 틸트를 NO-GO 판정 — 감지·리포트만).
    """
    try:
        from ml import regime_classifier
        hist = _history_cached("QQQ", "1y")
        if hist is None or getattr(hist, "empty", True) or "Close" not in getattr(hist, "columns", []):
            return None
        closes = hist["Close"].dropna()
        dd = (drawdown_pct / 100.0) if isinstance(drawdown_pct, (int, float)) else None
        r = regime_classifier.classify_latest(closes, drawdown=dd)
        return r if r.get("er") is not None else None
    except Exception as e:
        logger.debug("레짐 감지 실패(무시): %s", e)
        return None


def regime_line(regime: dict | None, indent: str = "  ") -> str | None:
    """레짐 dict → 한 줄 표시 문자열 (리포트·/status 공용). None → None(줄 생략)."""
    if not regime:
        return None
    if regime.get("sideways"):
        sub = "저변동·인컴" if regime.get("substate") == "sideways_calm" else "고변동·디리스크"
        icon, label = "🟰", f"횡보 ({sub})"
    else:
        icon, label = "📈", "추세/방향성"
    er = regime.get("er") or 0.0
    ret60 = regime.get("ret60") or 0.0
    return f"{indent}{icon} 레짐   {label}  ·  ER {er:.2f}  ·  3M {ret60 * 100:+.1f}%"


def build_report(
    qqq_data: dict,
    rsi: float,
    vix: float,
    ma_data: dict,
    portfolio: dict = None,
    exchange_rate: float = 1380.0,
    qqqi_div: dict = None,
    old_phase_state: dict = None,
    fear_greed: dict = None,
    show_regime: bool = True,
) -> str:
    """시각화 바벨 전략 리포트 생성."""
    if portfolio is None:
        portfolio = {"total_usd": 7940.0, "sgov_usd": 1006.7, "qqqi_usd": 2019.77,
                     "qqqi_shares": 35.2987, "prices": {}, "holdings": {}}
    if qqqi_div is None:
        qqqi_div = {"monthly_usd": 20.0, "annual_yield_pct": 12.0, "per_share": None, "note": "추산값"}

    # old_phase_state 전달 → 히스테리시스 적용 (시뮬레이션은 None → 원시 분류)
    market_type, phase_key = classify_market(qqq_data, rsi, vix, prev_state=old_phase_state)
    dca    = calculate_dca(market_type, phase_key, exchange_rate,
                           drawdown_pct=qqq_data.get("drawdown_pct"))
    sgov   = calculate_sgov_target(market_type, phase_key, portfolio["total_usd"], portfolio["sgov_usd"])
    p_info = BULL_PHASES[phase_key] if market_type == "bull" else BEAR_PHASES[phase_key]

    now      = datetime.now().strftime("%Y-%m-%d %H:%M KST")
    drawdown = qqq_data.get("drawdown_pct", 0)
    total_krw = int(portfolio["total_usd"] * exchange_rate)

    L = []

    L += _section_header(now, old_phase_state, market_type, phase_key, drawdown)
    L += _section_phase_meter(p_info, market_type, phase_key, drawdown)
    L += _section_portfolio(portfolio, total_krw, exchange_rate)
    reg_ln = regime_line(detect_regime(qqq_data.get("drawdown_pct"))) if show_regime else None
    L += _section_qqq_radar(qqq_data, ma_data, drawdown, rsi, vix, fear_greed, regime_ln=reg_ln)
    L += _section_sgov(sgov)
    L += _section_qqqi_dividend(qqqi_div, market_type, phase_key)
    L += _section_action_items(p_info)
    L += _section_dca(dca, exchange_rate, market_type, phase_key)
    L += _section_special_alerts(market_type, phase_key, portfolio, sgov)

    return "\n".join(L)


def _simulation_payload(mode: str) -> dict:
    SIM_DATA = {
        "bull2": {"qqq": {"current": 530, "high_52w": 533, "low_52w": 400, "drawdown_pct": -0.5,
                          "position_52w_pct": 98, "mom_1m_pct": 9.0, "mom_3m_pct": 18.0},
                  "rsi": 76.0, "vix": 13.0,
                  "fg": {"score": 82.0, "prev_week": 78.0}},
        "bull1": {"qqq": {"current": 500, "high_52w": 515, "low_52w": 400, "drawdown_pct": -2.9,
                          "position_52w_pct": 87, "mom_1m_pct": 5.5, "mom_3m_pct": 12.0},
                  "rsi": 65.0, "vix": 17.0,
                  "fg": {"score": 70.0, "prev_week": 65.0}},
        "0":    {"qqq": {"current": 480, "high_52w": 485, "low_52w": 380, "drawdown_pct": -1.0,
                         "position_52w_pct": 96, "mom_1m_pct": 2.0, "mom_3m_pct": 5.0},
                 "rsi": 55.0, "vix": 20.0,
                 "fg": {"score": 58.0, "prev_week": 55.0}},
        "1":    {"qqq": {"current": 450, "high_52w": 490, "low_52w": 380, "drawdown_pct": -8.2,
                         "position_52w_pct": 64, "mom_1m_pct": -3.0, "mom_3m_pct": 2.0},
                 "rsi": 42.0, "vix": 24.0,
                 "fg": {"score": 38.0, "prev_week": 45.0}},
        "2":    {"qqq": {"current": 420, "high_52w": 490, "low_52w": 380, "drawdown_pct": -14.3,
                         "position_52w_pct": 36, "mom_1m_pct": -8.0, "mom_3m_pct": -5.0},
                 "rsi": 32.0, "vix": 32.0,
                 "fg": {"score": 25.0, "prev_week": 30.0}},
        "3":    {"qqq": {"current": 400, "high_52w": 490, "low_52w": 360, "drawdown_pct": -18.4,
                         "position_52w_pct": 31, "mom_1m_pct": -10.0, "mom_3m_pct": -12.0},
                 "rsi": 27.0, "vix": 38.0,
                 "fg": {"score": 18.0, "prev_week": 22.0}},
        "4":    {"qqq": {"current": 370, "high_52w": 490, "low_52w": 340, "drawdown_pct": -24.5,
                         "position_52w_pct": 20, "mom_1m_pct": -12.0, "mom_3m_pct": -20.0},
                 "rsi": 22.0, "vix": 45.0,
                 "fg": {"score": 12.0, "prev_week": 15.0}},
        "5":    {"qqq": {"current": 330, "high_52w": 490, "low_52w": 300, "drawdown_pct": -32.7,
                         "position_52w_pct": 16, "mom_1m_pct": -18.0, "mom_3m_pct": -28.0},
                 "rsi": 18.0, "vix": 55.0,
                 "fg": {"score": 8.0, "prev_week": 11.0}},
    }
    return SIM_DATA.get(mode, SIM_DATA["bull2"])


def build_simulation_report(mode: str = "bull2") -> str:
    d = _simulation_payload(mode)
    ma_sim = {"above_ma200": d["qqq"]["drawdown_pct"] > -15, "gap_pct": -5.0 if d["qqq"]["drawdown_pct"] < -15 else 8.0}
    sim_portfolio = {"total_usd": 7940.0, "sgov_usd": 1006.7, "qqqi_usd": 2019.77, "qqqi_shares": 35.2987, "prices": {}, "holdings": {}}
    sim_div = {"monthly_usd": 20.20, "annual_yield_pct": 12.0, "per_share": 0.5727, "note": "시뮬레이션 추산값"}
    return (
        f"\n{'=' * 50}\n"
        f"[시뮬레이션 모드: {mode}]\n"
        f"{'=' * 50}\n\n"
        + build_report(d["qqq"], d["rsi"], d["vix"], ma_sim, sim_portfolio, 1380.0, sim_div,
                      fear_greed=d.get("fg"), show_regime=False)
    )


# ══════════════════════════════════════════════════════════════════════
#  리밸런싱 계산기
# ══════════════════════════════════════════════════════════════════════

def calculate_rebalancing(
    market_type: str,
    phase_key,
    portfolio: dict,
    exchange_rate: float = 1380.0,
) -> dict:
    """
    현재 포트폴리오 vs Phase 목표 비중 비교 → 구체적 매수/매도 금액 제시.

    반환:
      sgov_action   : SGOV 매수/매도 금액 및 방향
      leverage_action: QLD/TQQQ 현황 및 권고
      dca_weights   : 오늘 DCA 배분 비중
      summary_lines : 텔레그램 출력용 텍스트 리스트
    """
    total = portfolio["total_usd"]
    sgov  = portfolio["sgov_usd"]
    qqqi  = portfolio["qqqi_usd"]

    # SGOV 목표 비중
    if market_type == "bull":
        sgov_target_r = BULL_PHASES[phase_key]["sgov_target_ratio"]
    elif market_type == "neutral":
        sgov_target_r = TARGET_SGOV_RATIO
    else:
        sgov_target_r = BEAR_PHASES[phase_key]["sgov_target_ratio"]

    sgov_target  = total * sgov_target_r
    sgov_diff    = sgov_target - sgov
    sgov_pct_now = sgov / total * 100 if total > 0 else 0

    if sgov_diff > 50:
        sgov_act = f"매수 ${sgov_diff:.0f}  ({int(sgov_diff / 100.67)}주 SGOV)"
        sgov_dir = "buy"
    elif sgov_diff < -50:
        sgov_act = f"매도 ${abs(sgov_diff):.0f}  → DCA/레버리지 전환"
        sgov_dir = "sell"
    else:
        sgov_act = "적정 수준 유지"
        sgov_dir = "hold"

    # 레버리지 현황
    leverage = load_leverage_state()
    lev_lines = []
    prices    = portfolio.get("prices", {})
    for ticker, pos in leverage.items():
        sh = pos.get("shares", 0)
        if sh > 0:
            avg   = pos.get("avg_price_usd", 0)
            price = prices.get(ticker, avg)
            val   = sh * price
            pnl   = (price - avg) / avg * 100 if avg > 0 else 0
            sign  = "+" if pnl >= 0 else ""
            # Bull/중립 복귀 시 레버리지 정리 권고
            if market_type in ("bull", "neutral") and val > 100:
                lev_lines.append(f"  {ticker}  ${val:.0f}  {sign}{pnl:.1f}%  → ⚠️ 복귀 구간, 일부 익절 고려")
            else:
                lev_lines.append(f"  {ticker}  ${val:.0f}  {sign}{pnl:.1f}%  (보유 유지)")

    # DCA 비중 — Phase 2+ 는 BEAR 가중치
    w_normal_r, w_bear_r = load_dca_weights()
    use_bear = market_type == "bear" and isinstance(phase_key, int) and phase_key >= 2
    dca_w    = w_bear_r if use_bear else w_normal_r
    dca_mult = (BULL_PHASES[phase_key]["dca_multiplier"] if market_type == "bull"
                else BEAR_PHASES[phase_key]["dca_multiplier"] if market_type == "bear"
                else BEAR_PHASES[0]["dca_multiplier"])
    daily_krw = int(DCA_DAILY_BASE_KRW * dca_mult)

    # QQQI 비중 (참고용)
    qqqi_pct = qqqi / total * 100 if total > 0 else 0

    lines = [
        "⚖️ 리밸런싱 계산기",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"  총 포트폴리오  ${total:,.2f}",
        "",
        "━━━ SGOV 실탄 ━━━━━━━━━━━━━━━━━━━━━",
        f"  현재  ${sgov:>7,.2f}  ({sgov_pct_now:.1f}%)",
        f"  목표  ${sgov_target:>7,.2f}  ({sgov_target_r*100:.1f}%)",
        f"  차이  ${sgov_diff:>+7,.0f}",
        f"  → {sgov_act}",
        "",
        "━━━ QQQI 비중 ━━━━━━━━━━━━━━━━━━━━━",
        f"  현재  ${qqqi:>7,.2f}  ({qqqi_pct:.1f}%)",
        f"  역할: 배당 현금흐름 엔진 (매도 불필요)",
    ]

    if lev_lines:
        lines += ["", "━━━ 레버리지 포지션 ━━━━━━━━━━━━━━━━━"] + lev_lines
    else:
        lines += ["", "  레버리지  미보유  (Phase 2+ 진입 시 QLD 매수)"]

    lines += [
        "",
        f"━━━ DCA 배분  {daily_krw:,}원/일  [{dca_mult}x] ━━━━━━━━",
    ]
    max_w = max(dca_w.values(), default=1.0)
    for ticker, w in dca_w.items():
        amt = int(daily_krw * w)
        bar = _bar(w / max_w, 8)
        lines.append(f"  {ticker:<5}  {bar}  {amt:,}원  ({int(w*100)}%)")

    return {
        "sgov_diff":    round(sgov_diff, 2),
        "sgov_dir":     sgov_dir,
        "sgov_action":  sgov_act,
        "daily_dca_krw": daily_krw,
        "summary_lines": lines,
    }


# ══════════════════════════════════════════════════════════════════════
#  텔레그램
# ══════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    """텔레그램 발송 — notify 단일 진실원에 위임 (4096 분할·토큰 마스킹 공통)."""
    return notify.send_telegram(message, token=TELEGRAM_TOKEN, chat_id=TELEGRAM_CHAT_ID)


def send_phase5_emergency(
    drawdown_pct: float, exchange_rate: float, portfolio: dict | None = None
) -> bool:
    """Phase 5 전용 긴급 에스컬레이션 — 포트폴리오 기반 구체적 금액 포함."""
    portfolio = portfolio or {}
    sgov_usd   = portfolio.get("sgov_usd", 0.0)
    qqqi_usd   = portfolio.get("qqqi_usd", 0.0)
    total_usd  = portfolio.get("total_usd", 0.0)

    sgov_krw       = int(sgov_usd * exchange_rate)
    qqqi_20pct_usd = round(qqqi_usd * 0.20, 2)
    qqqi_30pct_usd = round(qqqi_usd * 0.30, 2)
    qqqi_20pct_krw = int(qqqi_20pct_usd * exchange_rate)
    qqqi_30pct_krw = int(qqqi_30pct_usd * exchange_rate)
    dca_krw        = int(DCA_DAILY_BASE_KRW * 5.0)   # 200,000원
    dca_usd        = round(dca_krw / exchange_rate, 1)
    total_krw      = int(total_usd * exchange_rate)

    msg = (
        "💥💥💥 Phase 5 크래시 에스컬레이션 💥💥💥\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"QQQ 고점 대비 {drawdown_pct:+.1f}% — 시장 붕괴 구간 진입\n"
        f"포트폴리오 총액: ${total_usd:,.0f}  (₩{total_krw:,})\n"
        f"환율: {exchange_rate:,.0f}원/USD\n"
        "\n"
        "⚡ 지금 당장 이렇게 하세요:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. SGOV 전량 → QLD(2x) 또는 TQQQ(3x) 매수\n"
        f"   {_dw_pad('투입 가능 금액:', 15)} ${sgov_usd:,.0f}  (₩{sgov_krw:,})\n"
        "2. QQQI 원금 20~30% → QLD 또는 TQQQ 전환\n"
        f"   {_dw_pad('20% 기준:', 15)} ${qqqi_20pct_usd:,.0f}  (₩{qqqi_20pct_krw:,})\n"
        f"   {_dw_pad('30% 기준:', 15)} ${qqqi_30pct_usd:,.0f}  (₩{qqqi_30pct_krw:,})\n"
        f"3. DCA 5배 즉시 실행: {dca_krw:,}원/일  (${dca_usd:.1f})\n"
        "4. MSFT/ORCL/NVDA 핵심 성장주 비중 유지\n"
        "5. 예비 현금(적금 포함) 단계적 투입 준비\n"
        "\n"
        "📱 /order 로 주문서 즉시 생성\n"
        "📊 /phase 로 전체 Phase 리포트 확인\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "10년에 한 번 오는 매수 기회. 공포에 팔지 말 것."
    )
    return send_telegram(msg)


# ══════════════════════════════════════════════════════════════════════
#  메인 실행
# ══════════════════════════════════════════════════════════════════════

def run(send_alert: bool = False) -> dict | None:
    """바벨 전략 실행."""
    logger.info("Intelligence Barbell v2.1 분석 시작...")

    # 이전 Phase 상태 로드
    old_state = load_phase_state()

    # 시장 데이터 수집
    qqq = fetch_qqq_data()
    if not qqq:
        logger.error("QQQ 데이터 수집 실패")
        return None

    rsi = fetch_rsi("QQQ")
    vix = fetch_vix()
    ma = fetch_ma200("QQQ")
    fg = fetch_fear_greed()

    # 신규: 환율 + 포트폴리오 실시간 + 배당 추산
    exchange_rate = fetch_exchange_rate()
    portfolio = fetch_portfolio_value()
    qqqi_div = estimate_qqqi_monthly_dividend(portfolio["qqqi_shares"], portfolio["qqqi_usd"])

    # Phase 분류 (이전 상태 기반 히스테리시스 적용)
    market_type, phase_key = classify_market(qqq, rsi, vix, prev_state=old_state)

    # Phase 변화 감지
    phase_changed = has_phase_changed(old_state, market_type, phase_key)

    # 리포트 생성 및 출력
    report = build_report(qqq, rsi, vix, ma, portfolio, exchange_rate, qqqi_div, old_state, fg)
    print(report)

    # 가격 stale 시 — 묵은 데이터 기반 Phase 에스컬레이션(특히 Phase 5 전면 매수)은 보류.
    # 잘못된 신호로 레버리지 권고가 나가는 것을 막고, 대신 데이터 경보만 보낸다 (비평 #2).
    data_stale = bool(qqq.get("stale"))

    # 텔레그램: Phase 변화 시 또는 강제 발송 시
    if send_alert or phase_changed:
        if data_stale:
            logger.warning("가격 stale(%d일 전) — Phase 에스컬레이션 알림 보류, 데이터 경보 발송",
                           qqq.get("data_age_days", 0))
            send_telegram(
                f"⚠️ 데이터 신선도 경고\n━━━━━━━━━━━━━━\nQQQ 최신 종가가 "
                f"{qqq.get('data_age_days', 0)}일 전입니다 (>{PRICE_STALE_MAX_DAYS:.0f}일).\n"
                f"yfinance 피드 지연·장애 가능 — Phase 분류/레버리지 권고를 신뢰하지 마세요. "
                f"데이터 복구 후 재확인 권장."
            )
        # Phase 5 크래시 진입: 긴급 알림 3회 반복 발송 (단, 데이터가 신선할 때만)
        elif market_type == "bear" and phase_key == 5 and phase_changed:
            for i in range(3):
                send_phase5_emergency(qqq.get("drawdown_pct", 0), exchange_rate, portfolio)
                if i < 2:
                    time.sleep(3)
            logger.warning("Phase 5 긴급 에스컬레이션 3회 발송 완료")

        if data_stale:
            sent = False  # stale 시 본 리포트는 보내지 않음 (경보로 대체)
        else:
            sent = send_telegram(report)
        if sent:
            reason = "강제 발송" if send_alert else f"Phase 변화 ({old_state.get('phase_key', '?')} → {phase_key})"
            logger.info(f"텔레그램 알림 발송 완료 [{reason}]")
    else:
        logger.info(f"Phase 변화 없음 ({market_type}/{phase_key}) — 텔레그램 스킵")

    # 현재 Phase 상태 저장
    save_phase_state(market_type, phase_key, qqq.get("drawdown_pct", 0))

    return {
        "market_type": market_type,
        "phase": phase_key,
        "drawdown_pct": qqq.get("drawdown_pct", 0),
        "rsi": rsi,
        "vix": vix,
        "exchange_rate": exchange_rate,
        "portfolio_total_usd": portfolio["total_usd"],
        "sgov_usd": portfolio["sgov_usd"],
        "qqqi_monthly_div": qqqi_div["monthly_usd"],
        "above_ma200": ma.get("above_ma200", True),
        "phase_changed": phase_changed,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Intelligence Barbell v2.1")
    parser.add_argument("--send", action="store_true", help="텔레그램 알림 강제 발송")
    parser.add_argument("--sim", choices=["bull2", "bull1", "0", "1", "2", "3", "4", "5"],
                        help="시장 상태 시뮬레이션 (오프라인)")
    parser.add_argument("--update-leverage", nargs=3, metavar=("TICKER", "SHARES", "AVG_PRICE"),
                        help="레버리지 포지션 업데이트. 예: --update-leverage QLD 5 75.50")
    args = parser.parse_args()

    if args.update_leverage:
        ticker, shares, avg_price = args.update_leverage
        update_leverage_position(ticker, float(shares), float(avg_price))

    elif args.sim:
        print(build_simulation_report(args.sim))

    else:
        run(send_alert=args.send)
