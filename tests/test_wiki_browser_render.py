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
wiki.stats = lambda: {{"total": 2, "status_counts": {{"draft": 1, "reviewed": 1, "stable": 0, "archived": 0}}, "latest": {{"title": "손실한도와 레버리지"}}}}
wiki.list_pages = lambda *args, **kwargs: [
    {{"id": "p1", "title": "손실한도와 레버리지", "summary": "QQQ와 TQQQ를 손실한도 1% 안에서 비교한다.", "body": "QQQ는 기본, TQQQ는 예산을 더 크게 써야 한다.", "tags": ["risk", "portfolio"], "status": "stable", "surface": "portfolio", "kind": "playbook", "source_refs": ["conversation:001"], "updated_at": "2026-07-13T01:00:00+00:00"}},
    {{"id": "p2", "title": "AI 콘솔 위키 브라우저", "summary": "문서 브라우저와 관련 문서를 보여준다.", "body": "문서 브라우저는 대화와 메모를 다시 읽게 한다.", "tags": ["wiki", "browser"], "status": "reviewed", "surface": "portfolio", "kind": "concept", "source_refs": ["conversation:002"], "updated_at": "2026-07-13T02:00:00+00:00"}},
]
wiki.build_context_section = lambda **kwargs: "[위키 지식]\n- stub"
wiki.delete_page = lambda page_id: True
wiki.upsert_page = lambda payload: dict(payload, id=payload.get("id") or "p1")
wiki.capture_from_chat = lambda *args, **kwargs: {{"id": "p1", "title": "captured"}}
wiki_browser.render_wiki_tab('market', {{"chat_rows": [{{"role": "user", "content": "질문"}}, {{"role": "assistant", "content": "답변"}}]}})
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "AI 위키" in body
    assert "문서 브라우저" in body
    assert "페이지 미리보기" in body
    assert "관련 문서" in body
