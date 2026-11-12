"""대시보드 화면 동작 — 종목 이동·파괴적 확인.

페이지가 공유하는 계약만 둔다. 데이터 조회는 dashboard.data / cached.
"""
from __future__ import annotations

import streamlit as st

def user_message(value, fallback: str) -> str:
    """사용자에게 보여줄 문구. 예외·경로·엔진 코드는 대체 문구로 바꾼다."""
    text = str(value or "").strip()
    leaked = ("Error", "Exception", "Traceback", "File ", "/home/", "call failed")
    if not text or any(token in text for token in leaked):
        return fallback
    return text



def open_ticker(ticker: str, *, toast: str | None = None) -> bool:
    """종목 분석으로 이동.

    `_ticker_page` 가 있으면 이미 선택된 티커여도 switch_page 한다.
    홈·관심종목의 기본 티커는 표의 첫 행인 경우가 많아, '다를 때만 이동'이면
    캡션이 약속한 행 클릭이 가장 흔한 행에서 아무 일도 안 한다.
    페이지 객체가 없는 단독 렌더는 티커가 바뀐 경우에만 rerun 해서
    남은 dataframe 선택이 루프하지 않게 한다.
    """
    symbol = str(ticker or "").strip()
    if not symbol or symbol == "—":
        return False
    import ticker_names
    norm = ticker_names.normalize_input(symbol)
    if norm:
        symbol = norm
    changed = symbol != st.session_state.get("ticker")
    st.session_state["ticker"] = symbol
    page = st.session_state.get("_ticker_page")
    if page is not None:
        if toast:
            st.toast(toast)
        st.switch_page(page)
        return True
    if changed:
        if toast:
            st.toast(toast)
        st.rerun()
        return True
    return False


def mentioned_tickers(items, *, limit: int = 8) -> list[str]:
    """뉴스 항목의 티커 필드와 제목 언급을 차트 점프용으로 모은다.

    제목은 `ticker_names.mentions_in` 만 파싱한다. 등장 순서·중복 제거.
    limit 가 0 이하면 빈 목록.
    """
    if limit <= 0:
        return []
    import ticker_names

    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        raw = [str(t) for t in (item.get("tickers") or []) if t]
        title = str(item.get("title") or "")
        raw.extend(ticker_names.mentions_in(title, limit=limit) or [])
        for token in raw:
            sym = ticker_names.normalize_input(str(token).strip().lstrip("$")) or ""
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
            if len(out) >= limit:
                return out
    return out


def render_ticker_jumps(tickers: list[str], *, key_prefix: str) -> None:
    """종목 버튼을 눌러 차트(종목 분석)로 이동."""
    import ticker_names

    symbols = []
    seen = set()
    for raw in tickers or []:
        sym = str(raw or "").strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        symbols.append(sym)
        if len(symbols) >= 8:
            break
    if not symbols:
        return
    cols = st.columns(len(symbols))
    for col, sym in zip(cols, symbols):
        short = sym.split(".")[0]
        if col.button(
            short,
            key=f"{key_prefix}_{sym.replace('.', '_')}",
            width="stretch",
            help=ticker_names.label(sym, allow_net=False),
        ):
            open_ticker(sym, toast=f"종목 분석 → {short}")



def confirm_delete(page_id: str, *, button_key: str, label: str = "삭제") -> bool:
    """삭제 버튼을 두 단계로. 확인 클릭에서만 True.

    첫 화면에는 `label` 버튼만 있다. 누르면 같은 페이지 id 에 대해
    '정말 삭제' / '취소' 가 나타난다. 다른 문서를 고르면 확인은 사라진다.
    """
    page_id = str(page_id or "")
    if not page_id:
        return False
    pending_key = "_ui_delete_pending"
    if st.button(label, key=button_key, width="stretch"):
        st.session_state[pending_key] = page_id
        st.rerun()
    if st.session_state.get(pending_key) != page_id:
        return False
    st.caption("삭제하면 되돌릴 수 없습니다.")
    yes, no = st.columns(2)
    if yes.button("정말 삭제", key=f"{button_key}_yes", type="primary", width="stretch"):
        st.session_state.pop(pending_key, None)
        return True
    if no.button("취소", key=f"{button_key}_no", width="stretch"):
        st.session_state.pop(pending_key, None)
        st.rerun()
    return False
