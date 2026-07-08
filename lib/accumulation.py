"""lib/accumulation.py — 주식 모으기 자동 기록 플랜 (store 문서 + 순수 판정 로직).

플랜 = {ticker, amount, currency("KRW"|"USD"), freq("매일"|"매주"|"매월"), enabled,
        last_run(YYYY-MM-DD·미 세션일), created, note}.
등록해두면 crons/accumulate_daily 가 **미국 정규장 마감 직후** 그날 종가와
확정 종가 환율로 소수점 계좌에 매수 **기록**한다 (실계좌 주문 0 — 실제 매수는
키움 주식모으기/수동. 이 시스템은 그 결과를 포트폴리오에 자동 반영하는 거울).

due 판정은 순수 함수(테스트) — 매일=세션마다 · 매주=ISO 주 첫 거래일 ·
매월=월 첫 거래일 (휴장 안전: 요일 고정이 아니라 '그 주/월의 첫 실행').
"""
from __future__ import annotations

from datetime import date, datetime

DOC = "accumulation_plans"


def _store():
    import store
    return store


def load_plans() -> list[dict]:
    try:
        doc = _store().get_doc(DOC, {}) or {}
        return list(doc.get("plans") or [])
    except Exception:
        return []


def save_plans(plans: list[dict]) -> None:
    _store().save_doc(DOC, {"plans": plans})


def upsert_plan(ticker: str, amount: float, currency: str, freq: str,
                note: str = "") -> str:
    """플랜 등록/갱신 — 같은 티커는 교체. 반환: 사람용 확인 문자열."""
    t = str(ticker).upper()
    if amount <= 0:
        return "❌ 금액이 0 이하입니다."
    if freq not in ("매일", "매주", "매월"):
        return "❌ 주기는 매일/매주/매월 중 하나여야 합니다."
    cur = "KRW" if str(currency).upper().startswith(("K", "₩", "W")) else "USD"
    plans = [p for p in load_plans() if p.get("ticker") != t]
    plans.append({"ticker": t, "amount": float(amount), "currency": cur,
                  "freq": freq, "enabled": True, "last_run": None,
                  "created": datetime.now().strftime("%Y-%m-%d"), "note": note})
    save_plans(plans)
    amt = f"₩{amount:,.0f}" if cur == "KRW" else f"${amount:,.2f}"
    return (f"🔁 자동 모으기 등록 — {t} {freq} {amt} (미 종가·확정 종가 환율로 "
            f"자동 기록 · 실주문 아님)")


def remove_plan(ticker: str) -> bool:
    t = str(ticker).upper()
    plans = load_plans()
    keep = [p for p in plans if p.get("ticker") != t]
    if len(keep) == len(plans):
        return False
    save_plans(keep)
    return True


def set_enabled(ticker: str, enabled: bool) -> None:
    plans = load_plans()
    for p in plans:
        if p.get("ticker") == str(ticker).upper():
            p["enabled"] = bool(enabled)
    save_plans(plans)


def plan_for(ticker: str) -> dict | None:
    t = str(ticker).upper()
    return next((p for p in load_plans() if p.get("ticker") == t), None)


def due_today(plan: dict, session: date) -> bool:
    """이번 미 세션에 기록해야 하나 (순수) — last_run 기준 멱등.

    매일: 세션마다 1회 · 매주: ISO 주가 바뀐 첫 거래일 · 매월: 월이 바뀐 첫 거래일.
    """
    if not plan.get("enabled", True):
        return False
    last = plan.get("last_run")
    if not last:
        return True
    try:
        last_d = date.fromisoformat(str(last))
    except ValueError:
        return True
    if last_d >= session:
        return False                                   # 이미 이 세션 기록 (멱등)
    freq = plan.get("freq", "매일")
    if freq == "매일":
        return True
    if freq == "매주":
        return last_d.isocalendar()[:2] != session.isocalendar()[:2]
    if freq == "매월":
        return (last_d.year, last_d.month) != (session.year, session.month)
    return False


def mark_run(ticker: str, session: date) -> None:
    plans = load_plans()
    for p in plans:
        if p.get("ticker") == str(ticker).upper():
            p["last_run"] = session.isoformat()
    save_plans(plans)


def run_once(*, now_et=None, get_close=None, get_fx=None, record=None) -> dict:
    """활성 플랜 일괄 기록 — 크론 본체 (의존성 주입으로 무네트워크 테스트 가능).

    get_close(ticker) -> (close, session_date) | None  — 오늘 미 세션 종가(휴장 None)
    get_fx() -> float — 확정 종가 환율 · record(ticker, qty, price, note) -> str
    반환 {recorded: [...], skipped: [...], errors: [...]}
    """
    out = {"recorded": [], "skipped": [], "errors": []}
    plans = [p for p in load_plans() if p.get("enabled", True)]
    if not plans:
        return out
    if get_close is None or get_fx is None or record is None:
        raise ValueError("의존성(get_close/get_fx/record) 필요")
    fx = float(get_fx())
    for p in plans:
        t = p.get("ticker", "")
        try:
            cp = get_close(t)
            if not cp:
                out["skipped"].append(f"{t}: 휴장/종가 없음")
                continue
            close, session = cp
            if not due_today(p, session):
                out["skipped"].append(f"{t}: 이번 {p.get('freq')} 기록 완료")
                continue
            amount = float(p.get("amount") or 0)
            usd = amount / fx if p.get("currency") == "KRW" else amount
            if close <= 0 or usd <= 0:
                out["skipped"].append(f"{t}: 금액/종가 비정상")
                continue
            qty = round(usd / close, 4)
            if qty <= 0:
                out["skipped"].append(f"{t}: 수량 0")
                continue
            amt_label = (f"₩{amount:,.0f}(@{fx:,.0f})" if p.get("currency") == "KRW"
                         else f"${amount:,.2f}")
            record(t, qty, round(float(close), 4),
                   f"주식 모으기 자동({p.get('freq')}) {amt_label} 종가")
            mark_run(t, session)
            out["recorded"].append(f"{t} {qty:.4f}주 @${close:,.2f} ({amt_label})")
        except Exception as e:
            out["errors"].append(f"{t}: {e}")
    return out


def update_plan(ticker: str, *, amount: float | None = None, freq: str | None = None,
                currency: str | None = None) -> bool:
    """기존 플랜 필드만 수정 — last_run·enabled **보존** (upsert 와 달리 재트리거 없음)."""
    t = str(ticker).upper()
    plans = load_plans()
    hit = False
    for p in plans:
        if p.get("ticker") != t:
            continue
        if amount is not None and amount > 0:
            p["amount"] = float(amount)
        if freq in ("매일", "매주", "매월"):
            p["freq"] = freq
        if currency in ("KRW", "USD"):
            p["currency"] = currency
        hit = True
    if hit:
        save_plans(plans)
    return hit
