#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
role_gating_test.py — 게스트 읽기전용 역할 게이팅 보안 경계 테스트 (네트워크 불필요)

핵심 검증: 게스트 계정이 처방형/주문 명령(/order·/holding·/tax·/ask 등)에
접근할 수 없고, 사실형 정보 명령(/market·/indicators·/help)만 허용되는지.
guest_report 출력에 처방형 표현이 없고 면책 문구가 포함되는지.
"""
import os
import sys

# 역할 환경변수는 telegram_bot import 전에 설정 (import 시점에 평가됨)
os.environ["STOCK_BOT_CHAT_ID"] = "owner_xyz"
os.environ["STOCK_BOT_GUEST_IDS"] = "guest_a, guest_b"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = "✅", "❌"
_results: list[tuple[bool, str]] = []


def check(cond: bool, label: str):
    _results.append((bool(cond), label))
    print(f"{PASS if cond else FAIL} {label}")


def main() -> int:
    import telegram_bot as t
    from bot import guest_report as gr

    # ── 역할 해석 ──────────────────────────────────────────────────────
    check(t._role_for("owner_xyz") == "owner", "owner chat_id → owner")
    check(t._role_for("guest_a") == "guest", "guest chat_id → guest")
    check(t._role_for("guest_b") == "guest", "guest chat_id(2) → guest")
    check(t._role_for("random999") is None, "미등록 chat_id → 차단(None)")

    # ── 보안 경계: 게스트는 처방/주문 명령 전면 차단 ─────────────────
    owner_only = ["/order", "/holding", "/tax", "/ask", "/dca", "/rebalance",
                  "/sgov", "/alert", "/report", "/status", "/leverage",
                  "/entry", "/meta", "/apply_snapshot", "/portfolio", "/sim"]
    blocked_ok = all(not t._command_allowed("guest", c) for c in owner_only)
    check(blocked_ok, f"게스트는 소유자 전용 {len(owner_only)}개 명령 전부 차단")

    # ── 게스트 허용 명령 ─────────────────────────────────────────────
    check(t._command_allowed("guest", "/market"), "게스트 /market 허용")
    check(t._command_allowed("guest", "/indicators"), "게스트 /indicators 허용")
    check(t._command_allowed("guest", "/help"), "게스트 /help 허용")

    # ── 소유자는 전부 허용 ───────────────────────────────────────────
    check(all(t._command_allowed("owner", c) for c in owner_only + ["/market"]),
          "소유자는 모든 명령 허용")

    # ── 차단 역할(None)은 아무것도 ───────────────────────────────────
    check(not t._command_allowed(None, "/market"), "차단 역할은 /market도 불가")

    # ── 게스트 허용 집합이 처방 명령을 포함하지 않음 ─────────────────
    leak = t._GUEST_COMMANDS & set(owner_only)
    check(not leak, f"게스트 허용집합에 처방 명령 누출 없음 (누출: {leak or '없음'})")

    # ── guest_report: 처방형 표현/매매신호 없음 + 면책 포함 ──────────
    fake_market = {
        "qqq": {"current": 500.0, "drawdown_pct": -16.5},
        "benchmarks": {"QQQ": {"ytd_pct": 8.1}, "SPY": {"ytd_pct": 6.0}},
        "rsi": 38, "vix": 24, "market_type": "bear",
    }
    brief = gr.build_market_brief(fake_market)
    banned = ["매수", "매도", "사라", "팔아", "목표가", "손절", "DCA", "레버리지", "추천"]
    found = [w for w in banned if w in brief]
    check(not found, f"시황 브리핑에 처방형 표현 없음 (발견: {found or '없음'})")
    check("책임은 본인" in brief, "시황 브리핑 면책 문구 포함")
    check("-16.5%" in brief and "RSI" in brief, "시황 브리핑 사실 데이터 포함")

    help_txt = gr.guest_help()
    check("/order" not in help_txt and "/ask" not in help_txt,
          "게스트 도움말에 처방 명령 노출 없음")
    check("/market" in help_txt and "/indicators" in help_txt,
          "게스트 도움말에 허용 명령 안내")

    # ── 결과 ──────────────────────────────────────────────────────────
    n_fail = sum(1 for ok, _ in _results if not ok)
    total = len(_results)
    print("\n" + "━" * 40)
    print(f"  {total - n_fail}/{total} 통과")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
