#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_fmt_html.py — fmt.py Telegram HTML 헬퍼 폐형해(무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fmt


def test_esc_specials():
    assert fmt.esc("a < b & c > d") == "a &lt; b &amp; c &gt; d"
    assert fmt.esc("AT&T") == "AT&amp;T"          # 회사명 & 안전


def test_b_escapes_and_wraps():
    assert fmt.b("MSFT") == "<b>MSFT</b>"
    assert fmt.b("a & b") == "<b>a &amp; b</b>"   # 굵게도 이스케이프


def test_pre_and_code():
    assert fmt.pre("a\nb") == "<pre>a\nb</pre>"
    assert fmt.pre("x<y") == "<pre>x&lt;y</pre>"
    assert fmt.code_inline("$1,000") == "<code>$1,000</code>"


def test_expand_wraps_blockquote():
    out = fmt.expand("요약", "상세 내용")
    assert out == "요약\n<blockquote expandable>상세 내용</blockquote>"


def test_spark_basic():
    s = fmt.spark([1, 2, 3, 4, 5, 6, 7, 8])
    assert len(s) == 8
    assert all(ch in fmt._SPARK for ch in s)
    assert s[0] == "▁" and s[-1] == "█"          # 최저→최고


def test_spark_too_short_or_flat():
    assert fmt.spark([1]) == ""
    assert fmt.spark([]) == ""
    flat = fmt.spark([5, 5, 5])                    # 평탄(rng=0) 도 예외 없이
    assert len(flat) == 3


def test_spark_skips_none():
    s = fmt.spark([1, None, 8])
    assert len(s) == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
