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
    assert "문서 브라우저" in body
    assert "페이지 미리보기" in body
    assert "관련 문서" in body


def test_wiki_browser_load_button_refreshes_preview_body():
    script = f"""
import os, sys, streamlit as st
sys.path.insert(0, {ROOT!r})
from agent_console import wiki
from dashboard import wiki_browser
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
    load_buttons = [btn for btn in at.button if str(getattr(btn, "label", "")) == "불러오기"]
    assert len(load_buttons) >= 2
    load_buttons[0].click()
    at.run()
    assert not at.exception, str(at.exception)
    refreshed_body = " ".join(
        str(getattr(item, "value", ""))
        for collection in (at.markdown, at.caption, getattr(at, "text", []), getattr(at, "info", []), getattr(at, "warning", []))
        for item in collection
    )
    assert "B 본문" in refreshed_body
    assert "A 본문" not in refreshed_body
