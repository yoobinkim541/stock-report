#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_fmt.py — 공통 포맷 레이어 fmt.py 폐형해(무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fmt


# ── pct: 부호/0 버그 차단 (F1 의 핵심) ────────────────────────────────
def test_pct_basic_signs():
    assert fmt.pct(1.5) == "+1.5%"
    assert fmt.pct(-0.5) == "-0.5%"


def test_pct_zero_no_sign():
    # 0 / 반올림 0 / 음수 0 모두 부호 없는 0.0% (─0%·+0.00%·+-0% 버그 차단)
    assert fmt.pct(0) == "0.0%"
    assert fmt.pct(-0.04) == "0.0%"
    assert fmt.pct(0.04) == "0.0%"
    assert fmt.pct(-0.0) == "0.0%"


def test_pct_no_plusminus_bug():
    # 구버그: ">+8.2f" 가 음수에 +- 를 만들던 것 — 절대 재발 없어야
    assert "+-" not in fmt.pct(-0.5)
    assert "+-" not in fmt.pct(-12.34, digits=2)


def test_pct_digits_and_none():
    assert fmt.pct(12.345, digits=2) == "+12.34%" or fmt.pct(12.345, digits=2) == "+12.35%"
    assert fmt.pct(None) == "—"


def test_signed_zero_and_sign():
    assert fmt.signed(0) == "0.0"
    assert fmt.signed(-0.02) == "0.0"       # 반올림 0 → 부호 없음 (+0.00 차단)
    assert fmt.signed(1.2) == "+1.2"


# ── arrow / spct ──────────────────────────────────────────────────────
def test_arrow():
    assert fmt.arrow(1) == "▲"
    assert fmt.arrow(-1) == "▼"
    assert fmt.arrow(0) == "─"
    assert fmt.arrow(None) == "─"


def test_spct_zero_is_dash():
    assert fmt.spct(2.3) == "▲2.3%"
    assert fmt.spct(-1.0) == "▼1.0%"
    assert fmt.spct(0) == "─"
    assert fmt.spct(-0.04) == "─"           # 반올림 0


# ── money: 전체/축약 ──────────────────────────────────────────────────
def test_money_full():
    assert fmt.money(7940) == "$7,940"
    assert fmt.money(10957200, ccy="₩") == "₩10,957,200"
    assert fmt.money(1820.5, digits=2) == "$1,820.50"


def test_money_abbrev_usd():
    assert fmt.money(102340, ccy="$", abbrev=True) == "$102.3K"
    assert fmt.money(2_500_000, ccy="$", abbrev=True) == "$2.50M"
    assert fmt.money(500, ccy="$", abbrev=True) == "$500"   # 1000 미만은 그대로


def test_money_abbrev_krw():
    assert fmt.money(10_957_200, ccy="₩", abbrev=True) == "₩1,096만"
    assert fmt.money(523_000_000, ccy="₩", abbrev=True) == "₩5.23억"


def test_money_none():
    assert fmt.money(None) == "—"


# ── disp_width: ambiguous=2 ───────────────────────────────────────────
def test_disp_width_ascii_vs_korean():
    assert fmt.disp_width("MSFT") == 4
    assert fmt.disp_width("삼성") == 4          # 한글 2칸 ×2
    assert fmt.disp_width("A삼B") == 4          # 1+2+1


def test_disp_width_ambiguous_box_char():
    # ─ (U+2500) 는 Ambiguous → 2칸으로 계산(모바일 한글 가정)
    assert fmt.disp_width("─") == 2


# ── wpad / wtrunc (code 블록 표 정렬) ─────────────────────────────────
def test_wpad_left_right_width():
    assert fmt.disp_width(fmt.wpad("MSFT", 8)) == 8
    assert fmt.disp_width(fmt.wpad("삼성전자", 12)) == 12
    assert fmt.wpad("AB", 5, ">").endswith("AB")
    assert fmt.wpad("AB", 5, ">").startswith(" ")


def test_wtrunc():
    assert fmt.wtrunc("MSFT", 10) == "MSFT"
    out = fmt.wtrunc("VeryLongTicker", 6)
    assert out.endswith("…") and fmt.disp_width(out) <= 6


# ── sep / code / name / headline / gloss ─────────────────────────────
def test_sep():
    assert fmt.sep() == fmt.SEP
    assert fmt.sep("수익률") == "── 수익률 ──"


def test_code_block_wrap():
    out = fmt.code("a\nb")
    assert out.startswith("```\n") and out.endswith("\n```")


def test_name_rule():
    assert fmt.name("MSFT", "Microsoft") == "MSFT — Microsoft"
    assert fmt.name("MSFT") == "MSFT"


def test_headline_skips_empty():
    assert fmt.headline("🟢 Phase 0", "", None, "낙폭 -0.5%") == "🟢 Phase 0 · 낙폭 -0.5%"


def test_gloss():
    g = fmt.gloss("MDD", "IC")
    assert "MDD=최대낙폭" in g and "IC=" in g
    assert fmt.gloss("UNKNOWN") == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
