"""holding_manager.undo_trade — 최신 수동 기록 역산 복원 (격리 tmp store·스냅샷).

conftest 가 STOCK_REPORT_DB 를 tmp 로 리다이렉트. PORTFOLIO_PATH 는 테스트별 tmp 파일.
refresh_portfolio_prices 는 네트워크라 무력화.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import holding_manager as hm  # noqa: E402
from lib import trade_events as te  # noqa: E402


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """격리 환경 — tmp 스냅샷 + trade_events 비우기 + refresh/shadow_doc 무력화.

    shadow_doc 차단: _save_locked 가 공유 테스트 DB 의 portfolio_snapshot 문서를
    덮어써 다른 테스트(포트폴리오 합계 등)를 오염시키는 것 방지.
    """
    import store
    p = tmp_path / "portfolio_snapshot.json"
    p.write_text(json.dumps({"overseas_general": {"holdings_usd": []},
                             "overseas_fractional": {"holdings": []}}))
    monkeypatch.setattr(hm, "PORTFOLIO_PATH", str(p))
    monkeypatch.setattr(hm, "refresh_portfolio_prices", lambda: "(가격 갱신 생략)")
    monkeypatch.setattr(store, "shadow_doc", lambda *a, **k: None)
    store.replace_all(te.COLLECTION, [])
    yield p
    store.replace_all(te.COLLECTION, [])               # 뒷정리 — 원장 잔재 제거


def _holding(p, section="overseas_general", key="holdings_usd", ticker="NVDA"):
    snap = json.loads(open(p).read())
    return next((h for h in snap.get(section, {}).get(key, [])
                 if h.get("ticker") == ticker), None)


def test_buy_holding_forces_fractional_for_non_integer_shares(iso):
    """소수점 주수인데 fractional 인자를 안 넘기면(기본 False) general 이 아니라
    fractional 계좌로 강제 기록돼야 한다.

    2026-07-29 버그: 대시보드 적립/추가 버튼이 fractional= 를 안 넘겨 소수점 매수가
    overseas_general(정수 전용)에 기록됐고, 다음 브로커 동기화가 general 을 통째로
    덮어쓰며 그 포지션이 조용히 사라졌다.
    """
    hm.buy_holding("AVGO", 0.1468, 378.32)              # fractional 인자 생략 — 옛날엔 general 로 감

    assert _holding(iso, section="overseas_general", ticker="AVGO") is None
    h = _holding(iso, section="overseas_fractional", key="holdings", ticker="AVGO")
    assert h is not None
    assert h["shares"] == pytest.approx(0.1468, abs=1e-6)
    assert h["avg_price_usd"] == pytest.approx(378.32, abs=1e-2)


def test_buy_holding_keeps_whole_shares_in_general_by_default(iso):
    """정수 주수는 fractional 인자를 안 줬을 때 여전히 general 로 간다 (회귀 방지)."""
    hm.buy_holding("NVDA", 2.0, 100.0)

    assert _holding(iso, section="overseas_fractional", key="holdings", ticker="NVDA") is None
    h = _holding(iso, section="overseas_general", ticker="NVDA")
    assert h is not None and h["shares"] == 2.0


def test_buy_undo_partial_restores_avg(iso):
    hm.buy_holding("NVDA", 2.0, 100.0)                 # 평단 100 × 2주
    hm.buy_holding("NVDA", 1.0, 190.0)                 # → 평단 130 × 3주
    ev = te.latest_manual_event("NVDA")
    assert ev["side"] == "buy" and ev["qty"] == 1.0
    msg = hm.undo_trade(ev["event_id"])
    assert msg.startswith("↩️"), msg
    h = _holding(iso)
    assert h["shares"] == 2.0 and h["avg_price_usd"] == pytest.approx(100.0, abs=0.01)
    assert te.latest_manual_event("NVDA")["qty"] == 2.0   # 이전 이벤트가 최신으로


def test_buy_undo_new_position_removes(iso):
    hm.buy_holding("AMD", 3.0, 150.0)
    ev = te.latest_manual_event("AMD")
    msg = hm.undo_trade(ev["event_id"])
    assert msg.startswith("↩️")
    assert _holding(iso, ticker="AMD") is None            # 신규 매수 취소 → 포지션 제거
    assert te.latest_manual_event("AMD") is None          # 원장에서도 제거


def test_sell_undo_partial_and_full(iso):
    hm.buy_holding("MSFT", 4.0, 200.0)
    hm.sell_holding("MSFT", 1.0, 250.0)                   # 부분 매도
    ev = te.latest_manual_event("MSFT")
    assert ev["side"] == "sell"
    assert hm.undo_trade(ev["event_id"]).startswith("↩️")
    h = _holding(iso, ticker="MSFT")
    assert h["shares"] == 4.0 and h["avg_price_usd"] == pytest.approx(200.0)
    hm.sell_holding("MSFT")                               # 전량 매도 → 포지션 제거
    ev2 = te.latest_manual_event("MSFT")
    assert _holding(iso, ticker="MSFT") is None
    assert hm.undo_trade(ev2["event_id"]).startswith("↩️")
    h2 = _holding(iso, ticker="MSFT")
    assert h2 and h2["shares"] == 4.0 and h2["avg_price_usd"] == pytest.approx(200.0)


def test_undo_middle_buy_replays_later(iso):
    """중간 매수 취소 — 이후 기록 롤백→재적용으로 평단 정합 + 원장 avg 재기록."""
    hm.buy_holding("NVDA", 2.0, 100.0)                 # A
    hm.buy_holding("NVDA", 1.0, 190.0)                 # B (취소 대상) → 평단 130
    hm.buy_holding("NVDA", 1.0, 130.0)                 # C → 평단 130 × 4주
    events = [r for r in te.trades_for_ticker("NVDA") if r["source"] == "manual_holding"]
    b = events[1]
    msg = hm.undo_trade(b["event_id"])
    assert msg.startswith("↩️") and "이후 1건 평단 재계산" in msg
    h = _holding(iso)
    # B 없이 재적용: (2×100 + 1×130) / 3 = 110
    assert h["shares"] == 3.0 and h["avg_price_usd"] == pytest.approx(110.0, abs=0.01)
    # C 이벤트의 기록 평단도 110 으로 갱신 → C 재취소가 정합 통과
    c = te.latest_manual_event("NVDA")
    assert c["avg_price"] == pytest.approx(110.0, abs=0.01)
    assert hm.undo_trade(c["event_id"]).startswith("↩️")
    h2 = _holding(iso)
    assert h2["shares"] == 2.0 and h2["avg_price_usd"] == pytest.approx(100.0, abs=0.01)


def test_undo_middle_buy_contradiction_refused(iso):
    """유일 매수 취소 시 이후 매도가 보유 초과 — 모순 정직 거부."""
    hm.buy_holding("AMD", 3.0, 150.0)
    hm.sell_holding("AMD", 1.0, 170.0)
    first = [r for r in te.trades_for_ticker("AMD")
             if r["source"] == "manual_holding"][0]
    msg = hm.undo_trade(first["event_id"])              # 매수 취소 → 매도가 모순
    assert msg.startswith("❌ 취소 불가")
    h = _holding(iso, ticker="AMD")
    assert h["shares"] == 2.0                           # 원상 불변


def test_undo_guards(iso):
    hm.buy_holding("NVDA", 2.0, 100.0)
    # 미존재 거부
    assert hm.undo_trade("nope").startswith("❌ 기록")
    # 모의/동기화 source 거부
    te.record_trade(ticker="NVDA", side="buy", qty=1, price=200.0,
                    account="kr_mock", source="intraday_mock_track")
    mock_ev = [r for r in te.all_trades() if r["source"] == "intraday_mock_track"][0]
    assert hm.undo_trade(mock_ev["event_id"]).startswith("❌ 수동")


def test_undo_double_click_blocked(iso):
    """이중 undo — 평단 일치 검증이 차단 (멱등)."""
    hm.buy_holding("NVDA", 2.0, 100.0)
    hm.buy_holding("NVDA", 1.0, 190.0)
    ev = te.latest_manual_event("NVDA")
    assert hm.undo_trade(ev["event_id"]).startswith("↩️")
    again = hm.undo_trade(ev["event_id"])                 # 원장서 제거됨 → 미존재
    assert again.startswith("❌")
    # 원장 제거가 실패했다고 가정한 시나리오: 같은 내용 이벤트 재주입 후 undo 시도
    te.record_trade(ticker="NVDA", side="buy", qty=1, price=190.0, avg_price=130.0,
                    account="overseas_general", source="manual_holding")
    ev2 = te.latest_manual_event("NVDA")
    assert "평단" in hm.undo_trade(ev2["event_id"])        # avg 불일치 차단
