#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fmt.py — 텔레그램 출력 공통 포맷 (모바일 안전·일관) — 단일 진실원

배경 (왜 이 모듈이 필요한가):
- 명령마다 출력을 손으로 포맷팅 → 구분선 길이·부호 규칙·숫자 자릿수가 제각각.
- `━ ─ ═` 박스문자는 East-Asian **Ambiguous width** → 한글 폰트 컨텍스트에서 2칸 렌더.
- 텔레그램 일반 텍스트는 **비등폭(proportional)** → 공백으로 만든 열 정렬은 원래 안 맞는다.

원칙:
1. 부호·% 는 `pct()`/`spct()` 한 곳으로 — `+-0.50%`·`-0%`·`+0.00` 같은 버그 원천 차단.
2. 구분선은 짧게(`SEP`) — ambiguous 2칸이어도 모바일(~38칸) 안 넘김.
3. **꼭 정렬돼야 하는 표는 `code()` 로 ```감싸 등폭```** (정렬 유지 + 가로스크롤, 줄바꿈 X).
   그 안에서만 `wpad()`(=ambiguous 2칸 인식)로 열을 맞춘다.
4. 그 외엔 공백 정렬을 쓰지 말고 `headline()`(핵심 1줄) + 라벨·값 한 줄/2줄.
5. 통화·종목명도 단일 규칙(`money()`·`name()`).

순수 함수만 — 네트워크·파일 의존 없음(unicodedata 만 사용). 전 모듈 공용.
"""

from __future__ import annotations

import html as _html
import unicodedata

# ── 구분선 ────────────────────────────────────────────────────────────
# 13칸(ambiguous 2칸 가정 시 ~26 display) — 모바일 폭(~38) 안전.
SEP = "─" * 13


def sep(title: str | None = None) -> str:
    """구분선. title 주면 섹션 헤더(`── 제목 ──`), 없으면 가로줄(SEP)."""
    if title:
        return f"── {title} ──"
    return SEP


# ── 부호·퍼센트 ───────────────────────────────────────────────────────
def pct(x, digits: int = 1) -> str:
    """일관 퍼센트. `+1.5%` / `-0.5%` / `0.0%`.

    0(또는 자릿수 반올림 0)은 부호 없이 `0.0%` → `+-0.50%`·`-0%`·`+0.00%` 버그 차단.
    """
    if x is None:
        return "—"
    v = float(x)
    if round(v, digits) == 0:                 # -0.0 / +0.0 모두 여기로
        return f"{0.0:.{digits}f}%"
    return f"{v:+.{digits}f}%"


def signed(x, digits: int = 1) -> str:
    """부호 있는 숫자(%/단위 없음). `+1.5` / `-0.5` / `0.0`."""
    if x is None:
        return "—"
    v = float(x)
    if round(v, digits) == 0:
        return f"{0.0:.{digits}f}"
    return f"{v:+.{digits}f}"


def arrow(x) -> str:
    """등락 화살표 ▲ / ▼ / ─ (보합·None 은 ─, U+2500 통일)."""
    if x is None:
        return "─"
    v = float(x)
    return "▲" if v > 0 else ("▼" if v < 0 else "─")


def spct(x, digits: int = 1) -> str:
    """화살표 + 절대% — `▲1.5%` / `▼0.5%` / `─`(보합)."""
    if x is None:
        return "─"
    v = float(x)
    if round(v, digits) == 0:
        return "─"
    return f"{arrow(v)}{abs(v):.{digits}f}%"


# ── 통화 ──────────────────────────────────────────────────────────────
def money(x, ccy: str = "$", abbrev: bool = False, digits: int = 0) -> str:
    """통화 표기. abbrev=True 면 큰 금액 축약($→M/K, ₩→억/만)."""
    if x is None:
        return "—"
    v = float(x)
    if abbrev:
        a = abs(v)
        if ccy == "₩":
            if a >= 1e8:
                return f"{ccy}{v/1e8:.2f}억"
            if a >= 1e4:
                return f"{ccy}{v/1e4:,.0f}만"
        else:
            if a >= 1e6:
                return f"{ccy}{v/1e6:.2f}M"
            if a >= 1e3:
                return f"{ccy}{v/1e3:.1f}K"
    return f"{ccy}{v:,.{digits}f}"


# ── 표시폭(ambiguous=2) 인식 정렬 — code() 블록 내부 표 전용 ──────────────
def disp_width(s: str) -> int:
    """문자열 표시폭. East-Asian Wide/Full/**Ambiguous** 는 2칸(한글 모바일 가정)."""
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
    return w


def wpad(s: str, width: int, align: str = "<") -> str:
    """표시폭 기준 패딩(등폭 code 블록 내 열 정렬용). align: '<' 좌·'>' 우·'^' 중앙."""
    s = str(s)
    gap = max(0, width - disp_width(s))
    if align == ">":
        return " " * gap + s
    if align == "^":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def wtrunc(s: str, width: int) -> str:
    """표시폭 기준 자르기(초과 시 끝에 …). … 자체 폭(ambiguous=2)도 예산에서 차감."""
    s = str(s)
    if disp_width(s) <= width:
        return s
    ell = "…"
    budget = width - disp_width(ell)
    out = ""
    for ch in s:
        if disp_width(out + ch) > budget:
            break
        out += ch
    return out + ell


def code(text: str) -> str:
    """등폭 보장이 필요한 표를 코드블록으로 감싼다(정렬 유지·가로스크롤·줄바꿈 X)."""
    return f"```\n{text}\n```"


# ── 종목명·헤드라인·약어 ──────────────────────────────────────────────
def name(ticker: str, label: str | None = None, maxlen: int | None = None) -> str:
    """`회사명 (티커)` 통일(CLAUDE.md 규칙). label 없으면 ticker_names 자동해석·이름 없으면 티커만.

    maxlen: 회사명 최대 길이(좁은칸·등폭표 절단). 렌더 경로라 무네트워크(큐레이트+디스크캐시).
    """
    t = (ticker or "").strip()
    if not t:
        return ""
    try:
        import ticker_names
        return ticker_names.label(t, name=label, maxlen=maxlen, allow_net=False)
    except Exception:
        # ticker_names 미가용 폴백 — 최소 포맷
        if label and str(label).strip() and str(label).strip() != t:
            nm = str(label).strip()
            if maxlen and len(nm) > maxlen:
                nm = nm[:max(1, maxlen - 1)].rstrip() + "…"
            return f"{nm} ({t})"
        return t


def headline(*parts) -> str:
    """핵심 1줄 — 빈 항목 제외하고 ` · ` 로 연결."""
    return " · ".join(str(p) for p in parts if p not in (None, "", "—"))


# 약어 풀이(첫 등장 섹션에만 1줄 부착). gloss("MDD","IC") → "MDD=최대낙폭 · IC=예측상관"
GLOSSARY = {
    "MDD": "최대낙폭",
    "IC": "예측상관(±0=무변별)",
    "ICIR": "IC안정성",
    "OBV": "누적거래량",
    "CMF": "자금흐름",
    "P25": "하위25%수익",
    "P75": "상위25%수익",
    "VWAP": "거래량가중평균가",
    "PEAD": "실적후 주가표류",
    "PER": "주가수익비율",
    "PBR": "주가순자산비율",
    "PSR": "주가매출비율",
    "ROE": "자기자본이익률",
    "ER": "효율비(추세강도)",
}


def gloss(*terms) -> str:
    """약어 풀이 1줄. 알 수 없는 용어는 건너뜀."""
    out = [f"{t}={GLOSSARY[t]}" for t in terms if t in GLOSSARY]
    return " · ".join(out)


# ── Telegram HTML (parse_mode=HTML) — 리치텍스트 위계 ────────────────────
# 위계: 핵심 b(굵게) · 표 pre(등폭·정렬유지) · 긴 상세 expand(접기). 모두 입력 이스케이프.
def esc(s) -> str:
    """HTML 이스케이프 (parse_mode=HTML 안전 — < > & 만, 본문 따옴표 보존)."""
    return _html.escape(str(s), quote=False)


def b(s) -> str:
    """굵게 (입력 자동 이스케이프)."""
    return f"<b>{esc(s)}</b>"


def code_inline(s) -> str:
    """인라인 등폭 (입력 이스케이프)."""
    return f"<code>{esc(s)}</code>"


def pre(s) -> str:
    """등폭 블록 — 표 정렬 유지·모바일 줄바꿈 방지 (입력 이스케이프)."""
    return f"<pre>{esc(s)}</pre>"


def expand(summary_html: str, detail_html: str) -> str:
    """접을 수 있는 상세 — summary 는 보이고 detail 은 expandable blockquote.

    summary_html·detail_html 은 **이미 HTML-안전**(b/esc/pre 로 구성)이어야 한다.
    blockquote 중첩 금지.
    """
    return f"{summary_html}\n<blockquote expandable>{detail_html}</blockquote>"


_SPARK = "▁▂▃▄▅▆▇█"


def spark(series) -> str:
    """유니코드 스파크라인 (값 시계열 한 줄). 값 2개 미만이면 빈 문자열."""
    vals = [float(v) for v in series if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    return "".join(_SPARK[min(7, max(0, int((v - lo) / rng * 7)))] for v in vals)
