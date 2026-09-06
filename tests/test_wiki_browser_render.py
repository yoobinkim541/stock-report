from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_wiki_browser_render_smoke():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from agent_console import wiki
from dashboard import wiki_browser
# 다른 테스트가 채운 st.cache_data 캐시(list_pages/context_section)가 남아있으면
# 이 테스트의 스텁 대신 그 스테일 데이터를 돌려준다(테스트끼리 프로세스 공유 캐시
# 오염) — 각 테스트는 항상 자기 스텁으로만 시작하도록 먼저 비운다.
wiki_browser._cached_wiki_snapshot.clear()
wiki_browser._cached_context_section.clear()
_wiki_stubs = {{
    "stats": wiki.stats,
    "list_pages": wiki.list_pages,
    "build_context_section": wiki.build_context_section,
    "delete_page": wiki.delete_page,
    "upsert_page": wiki.upsert_page,
    "capture_from_chat": wiki.capture_from_chat,
}}
try:
    wiki.stats = lambda: {{"total": 2, "status_counts": {{"draft": 1, "reviewed": 1, "stable": 0, "archived": 0}}, "latest": {{"title": "손실한도와 레버리지"}}}}
    wiki.list_pages = lambda *args, **kwargs: [
        {{"id": "p1", "title": "손실한도와 레버리지", "summary": "QQQ와 TQQQ를 손실한도 1% 안에서 비교한다.", "body": "QQQ는 기본, TQQQ는 예산을 더 크게 써야 한다.", "tags": ["risk", "portfolio"], "status": "stable", "surface": "portfolio", "kind": "playbook", "source_refs": ["conversation:001"], "updated_at": "2026-07-13T01:00:00+00:00"}},
        {{"id": "p2", "title": "AI 콘솔 위키 브라우저", "summary": "문서 브라우저와 관련 문서를 보여준다.", "body": "문서 브라우저는 대화와 메모를 다시 읽게 한다.", "tags": ["wiki", "browser"], "status": "reviewed", "surface": "portfolio", "kind": "concept", "source_refs": ["conversation:002"], "updated_at": "2026-07-13T02:00:00+00:00"}},
    ]
    wiki.build_context_section = lambda **kwargs: "[위키 지식]\\n- stub"
    wiki.delete_page = lambda page_id: True
    wiki.upsert_page = lambda payload: dict(payload, id=payload.get("id") or "p1")
    wiki.capture_from_chat = lambda *args, **kwargs: {{"id": "p1", "title": "captured"}}
    wiki_browser.render_wiki_tab('market', {{"chat_rows": [{{"role": "user", "content": "질문"}}, {{"role": "assistant", "content": "답변"}}]}})
finally:
    for _name, _value in _wiki_stubs.items():
        setattr(wiki, _name, _value)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "AI 위키" in body
    assert "탐색기" in body  # Obsidian 식 좌측 파일 트리 패널 헤더
    assert "문서 읽기" in body
    assert "관련 문서" in body
    surface_filter = at.selectbox(key="agent_wiki_surface_filter")
    assert surface_filter.value == "all"


def test_wiki_browser_load_button_refreshes_preview_body():
    script = f"""
import os, sys, streamlit as st
sys.path.insert(0, {ROOT!r})
from agent_console import wiki
from dashboard import wiki_browser
# 다른 테스트가 채운 st.cache_data 캐시(list_pages/context_section)가 남아있으면
# 이 테스트의 스텁 대신 그 스테일 데이터를 돌려준다(테스트끼리 프로세스 공유 캐시
# 오염) — 각 테스트는 항상 자기 스텁으로만 시작하도록 먼저 비운다.
wiki_browser._cached_wiki_snapshot.clear()
wiki_browser._cached_context_section.clear()
_wiki_stubs = {{
    "stats": wiki.stats,
    "list_pages": wiki.list_pages,
    "build_context_section": wiki.build_context_section,
    "delete_page": wiki.delete_page,
    "upsert_page": wiki.upsert_page,
    "capture_from_chat": wiki.capture_from_chat,
}}
try:
    st.session_state["agent_wiki_selected_page_id"] = "p1"
    wiki.stats = lambda: {{"total": 2, "status_counts": {{"draft": 1, "reviewed": 1, "stable": 0, "archived": 0}}, "latest": {{"title": "문서 A"}}}}
    wiki.list_pages = lambda *args, **kwargs: [
        {{"id": "p1", "title": "문서 A", "summary": "A 요약", "body": "A 본문", "tags": ["wiki"], "status": "draft", "surface": "portfolio", "kind": "note", "source_refs": ["conversation:001"], "updated_at": "2026-07-13T01:00:00+00:00"}},
        {{"id": "p2", "title": "문서 B", "summary": "B 요약", "body": "B 본문", "tags": ["wiki"], "status": "reviewed", "surface": "portfolio", "kind": "note", "source_refs": ["conversation:002"], "updated_at": "2026-07-13T02:00:00+00:00"}},
    ]
    wiki.build_context_section = lambda **kwargs: "[위키 지식]\\n- stub"
    wiki.delete_page = lambda page_id: True
    wiki.upsert_page = lambda payload: dict(payload, id=payload.get("id") or "p1")
    wiki.capture_from_chat = lambda *args, **kwargs: {{"id": "p1", "title": "captured"}}
    wiki_browser.render_wiki_tab('market', {{"chat_rows": [{{"role": "user", "content": "질문"}}, {{"role": "assistant", "content": "답변"}}]}})
finally:
    for _name, _value in _wiki_stubs.items():
        setattr(wiki, _name, _value)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    initial_body = " ".join(
        str(getattr(item, "value", ""))
        for collection in (at.markdown, at.caption, getattr(at, "text", []))
        for item in collection
    )
    assert "A 본문" in initial_body
    # Obsidian 식 탐색기에서는 '불러오기' 버튼 대신 문서 제목 자체가 트리 항목 버튼이다.
    tree_buttons = [btn for btn in at.button if "문서" in str(getattr(btn, "label", ""))]
    assert len(tree_buttons) >= 2
    target = next(btn for btn in tree_buttons if "문서 B" in str(btn.label))
    target.click()
    at.run()
    assert not at.exception, str(at.exception)
    refreshed_body = " ".join(
        str(getattr(item, "value", ""))
        for collection in (at.markdown, at.caption, getattr(at, "text", []), getattr(at, "info", []), getattr(at, "warning", []))
        for item in collection
    )
    assert "B 본문" in refreshed_body
    assert "A 본문" not in refreshed_body


def test_wiki_browser_preview_uses_full_page_body_when_list_view_is_summary_only():
    script = f"""
import os, sys, streamlit as st
sys.path.insert(0, {ROOT!r})
from agent_console import wiki
from dashboard import wiki_browser
# 다른 테스트가 채운 st.cache_data 캐시(list_pages/context_section)가 남아있으면
# 이 테스트의 스텁 대신 그 스테일 데이터를 돌려준다(테스트끼리 프로세스 공유 캐시
# 오염) — 각 테스트는 항상 자기 스텁으로만 시작하도록 먼저 비운다.
wiki_browser._cached_wiki_snapshot.clear()
wiki_browser._cached_context_section.clear()
_wiki_stubs = {{
    "stats": wiki.stats,
    "list_pages": wiki.list_pages,
    "get_page": wiki.get_page,
    "build_context_section": wiki.build_context_section,
    "delete_page": wiki.delete_page,
    "upsert_page": wiki.upsert_page,
    "capture_from_chat": wiki.capture_from_chat,
}}
try:
    st.session_state["agent_wiki_selected_page_id"] = "p2"
    wiki.stats = lambda: {{"total": 2, "status_counts": {{"draft": 1, "reviewed": 1, "stable": 0, "archived": 0}}, "latest": {{"title": "문서 B"}}}}
    wiki.list_pages = lambda *args, **kwargs: [
        {{"id": "p1", "title": "문서 A", "summary": "A 요약", "tags": ["wiki"], "status": "draft", "surface": "portfolio", "kind": "note", "source_refs": ["conversation:001"], "updated_at": "2026-07-13T01:00:00+00:00"}},
        {{"id": "p2", "title": "문서 B", "summary": "B 요약", "tags": ["wiki"], "status": "reviewed", "surface": "portfolio", "kind": "note", "source_refs": ["conversation:002"], "updated_at": "2026-07-13T02:00:00+00:00"}},
    ]
    long_body = "B 전체 본문 " + ("세부 내용 " * 1400) + "[FULL_BODY_TAIL]"
    wiki.get_page = lambda page_id: {{"id": page_id, "title": "문서 B", "summary": "B 요약", "body": long_body, "tags": ["wiki"], "status": "reviewed", "surface": "portfolio", "kind": "note", "source_refs": ["conversation:002"], "updated_at": "2026-07-13T02:00:00+00:00"}} if page_id == "p2" else {{"id": page_id, "title": "문서 A", "summary": "A 요약", "body": "A 전체 본문", "tags": ["wiki"], "status": "draft", "surface": "portfolio", "kind": "note", "source_refs": ["conversation:001"], "updated_at": "2026-07-13T01:00:00+00:00"}}
    wiki.build_context_section = lambda **kwargs: "[위키 지식]\\n- stub"
    wiki.delete_page = lambda page_id: True
    wiki.upsert_page = lambda payload: dict(payload, id=payload.get("id") or "p1")
    wiki.capture_from_chat = lambda *args, **kwargs: {{"id": "p1", "title": "captured"}}
    wiki_browser.render_wiki_tab('market', {{"chat_rows": [{{"role": "user", "content": "질문"}}, {{"role": "assistant", "content": "답변"}}]}})
finally:
    for _name, _value in _wiki_stubs.items():
        setattr(wiki, _name, _value)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(
        str(getattr(item, "value", ""))
        for collection in (at.markdown, at.caption, getattr(at, "text", []), getattr(at, "info", []), getattr(at, "warning", []))
        for item in collection
    )
    assert "문서 읽기" in body
    assert "[FULL_BODY_TAIL]" in body


def test_wiki_browser_shows_recent_merge_log():
    """유빈님 요청(2026-09-06): 어떤 문서가 어떤 문서로 병합됐는지 mywiki 처럼
    보고 싶다 — AI 위키 탭에 최근 병합 이력 섹션이 실제로 렌더링되는지 확인."""
    script = f"""
import os, sys, streamlit as st
sys.path.insert(0, {ROOT!r})
from agent_console import wiki
from dashboard import wiki_browser
# 다른 테스트가 채운 st.cache_data 캐시(list_pages/context_section)가 남아있으면
# 이 테스트의 스텁 대신 그 스테일 데이터를 돌려준다(테스트끼리 프로세스 공유 캐시
# 오염) — 각 테스트는 항상 자기 스텁으로만 시작하도록 먼저 비운다.
wiki_browser._cached_wiki_snapshot.clear()
wiki_browser._cached_context_section.clear()
_wiki_stubs = {{
    "stats": wiki.stats,
    "list_pages": wiki.list_pages,
    "build_context_section": wiki.build_context_section,
    "delete_page": wiki.delete_page,
    "upsert_page": wiki.upsert_page,
    "capture_from_chat": wiki.capture_from_chat,
}}
try:
    merge_event = {{
        "event_id": "merge-001", "action": "merge", "occurred_at": "2026-09-05T00:00:00+00:00",
        "target_id": "p2", "source_ids": ["p1"], "source_titles": ["옛 카드"],
        "reason": "같은 판단", "synthesis": "통합 요약", "status": "completed",
    }}
    wiki.stats = lambda: {{"total": 1, "status_counts": {{"draft": 0, "reviewed": 1, "stable": 0, "archived": 1}}, "latest": {{"title": "새 카드"}}}}
    wiki.list_pages = lambda *args, **kwargs: [
        {{"id": "p2", "title": "새 카드", "summary": "요약", "body": "본문", "tags": ["wiki"], "status": "reviewed", "surface": "portfolio", "kind": "note", "source_refs": [], "merge_history": [merge_event], "updated_at": "2026-07-13T02:00:00+00:00"}},
    ]
    wiki.build_context_section = lambda **kwargs: "[위키 지식]\\n- stub"
    wiki.delete_page = lambda page_id: True
    wiki.upsert_page = lambda payload: dict(payload, id=payload.get("id") or "p1")
    wiki.capture_from_chat = lambda *args, **kwargs: {{"id": "p1", "title": "captured"}}
    wiki_browser.render_wiki_tab('market', {{"chat_rows": []}})
finally:
    for _name, _value in _wiki_stubs.items():
        setattr(wiki, _name, _value)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(
        str(getattr(item, "value", ""))
        for collection in (at.markdown, at.caption, getattr(at, "text", []))
        for item in collection
    )
    expander_labels = " ".join(str(getattr(exp, "label", "")) for exp in at.expander)
    assert "최근 병합 이력" in expander_labels
    assert "옛 카드" in body and "새 카드" in body
    assert "같은 판단" in body


def test_wiki_browser_toggle_switches_center_pane_between_read_and_edit():
    """유빈님 요청(2026-09-06): Obsidian 처럼 한 문서 패널에서 읽기/편집을 오가고
    싶다 — 예전엔 편집 폼이 우측에 항상 떠 있었다. 이제 가운데 패널의 '편집'
    버튼으로 같은 자리에서 폼으로 전환되고, '읽기로' 버튼으로 되돌아가는지 확인."""
    script = f"""
import os, sys, streamlit as st
sys.path.insert(0, {ROOT!r})
from agent_console import wiki
from dashboard import wiki_browser
# 다른 테스트가 채운 st.cache_data 캐시(list_pages/context_section)가 남아있으면
# 이 테스트의 스텁 대신 그 스테일 데이터를 돌려준다(테스트끼리 프로세스 공유 캐시
# 오염) — 각 테스트는 항상 자기 스텁으로만 시작하도록 먼저 비운다.
wiki_browser._cached_wiki_snapshot.clear()
wiki_browser._cached_context_section.clear()
_wiki_stubs = {{
    "stats": wiki.stats,
    "list_pages": wiki.list_pages,
    "build_context_section": wiki.build_context_section,
    "delete_page": wiki.delete_page,
    "upsert_page": wiki.upsert_page,
    "capture_from_chat": wiki.capture_from_chat,
}}
try:
    st.session_state["agent_wiki_selected_page_id"] = "p1"
    wiki.stats = lambda: {{"total": 1, "status_counts": {{"draft": 1, "reviewed": 0, "stable": 0, "archived": 0}}, "latest": {{"title": "문서 A"}}}}
    wiki.list_pages = lambda *args, **kwargs: [
        {{"id": "p1", "title": "문서 A", "summary": "A 요약", "body": "A 본문", "tags": ["wiki"], "status": "draft", "surface": "portfolio", "kind": "note", "source_refs": ["conversation:001"], "updated_at": "2026-07-13T01:00:00+00:00"}},
    ]
    wiki.build_context_section = lambda **kwargs: "[위키 지식]\\n- stub"
    wiki.delete_page = lambda page_id: True
    wiki.upsert_page = lambda payload: dict(payload, id=payload.get("id") or "p1")
    wiki.capture_from_chat = lambda *args, **kwargs: {{"id": "p1", "title": "captured"}}
    wiki_browser.render_wiki_tab('market', {{"chat_rows": []}})
finally:
    for _name, _value in _wiki_stubs.items():
        setattr(wiki, _name, _value)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    # 초기 상태 = 읽기 모드: 본문이 보이고 편집 폼(제목 입력창)은 없다.
    body = " ".join(str(m.value) for m in at.markdown)
    assert "A 본문" in body
    assert not any(str(getattr(ti, "label", "")) == "제목" for ti in at.text_input)

    toggle = next(btn for btn in at.button if str(getattr(btn, "label", "")) == "편집")
    toggle.click()
    at.run()
    assert not at.exception, str(at.exception)
    # 편집 모드: 제목 입력창이 있고, 삭제 버튼도 나타난다.
    assert any(str(getattr(ti, "label", "")) == "제목" for ti in at.text_input)
    assert any("삭제" in str(getattr(btn, "label", "")) for btn in at.button)

    back = next(btn for btn in at.button if str(getattr(btn, "label", "")) == "읽기로")
    back.click()
    at.run()
    assert not at.exception, str(at.exception)
    assert not any(str(getattr(ti, "label", "")) == "제목" for ti in at.text_input)
