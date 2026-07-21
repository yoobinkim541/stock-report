from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_wiki_browser_render_smoke():
    at = AppTest.from_string(
        "from dashboard import wiki_browser\n"
        "wiki_browser.render_wiki_tab('market', {})\n",
        default_timeout=30,
    )
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "AI 위키" in body
    assert "문서 브라우저" in body or "페이지 미리보기" in body
