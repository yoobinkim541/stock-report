#!/usr/bin/env python3
"""
test_notion_archive.py — notion_archive 월/주 계층 아카이빙 테스트 (무네트워크).

검증:
  - week_of_month 경계(월요일 시작, 1일 포함 주 = 1주차)
  - 블록 빌더 형태
  - NOTION_TOKEN 없을 때 graceful skip
  - 인메모리 Notion 시뮬레이터로 트리 생성 + 일별 멱등 upsert
    (같은 날 2회 실행 → 주차 페이지에 토글이 정확히 1개)
"""
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))
import notion_archive as na  # noqa: E402


# ── 1. week_of_month ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("ds,expected", [
    ("2026-06-01", 1),   # 1일=월요일
    ("2026-06-07", 1),   # 일요일 (1주차 마지막)
    ("2026-06-08", 2),   # 월요일 → 2주차
    ("2026-06-23", 4),
    ("2026-06-30", 5),
    ("2026-03-01", 1),   # 1일=일요일 → 단독 1주차
    ("2026-03-02", 2),   # 월요일 → 2주차
])
def test_week_of_month(ds, expected):
    assert na.week_of_month(date.fromisoformat(ds)) == expected


# ── 2. 블록 빌더 ──────────────────────────────────────────────────────────────
def test_builders():
    assert na._para("x")["type"] == "paragraph"
    assert len(na._para("a" * 5000)["paragraph"]["rich_text"][0]["text"]["content"]) == 2000
    assert na._toggle("t", [na._para("y")])["toggle"]["children"][0]["type"] == "paragraph"
    assert na._callout("c", "📚")["callout"]["icon"]["emoji"] == "📚"
    lp = na.link_to_page("abc")
    assert lp["type"] == "link_to_page" and lp["link_to_page"]["page_id"] == "abc"
    assert na._divider()["type"] == "divider"


def _block_text(block):
    btype = block.get("type")
    rich = (block.get(btype) or {}).get("rich_text", [])
    return "".join(rt.get("plain_text") or rt.get("text", {}).get("content", "") for rt in rich)


def test_full_report_blocks_preserve_readable_structure():
    blocks = na._full_report_blocks(
        "2026-06-23",
        "# 리포트 제목\n"
        "## 핵심 요약\n"
        "### 세부 근거\n"
        "- 첫 번째 항목\n"
        f"{'x' * 2500}\n",
    )

    types = [b["type"] for b in blocks]
    assert types[:5] == ["callout", "heading_2", "heading_2", "heading_3", "bulleted_list_item"]
    assert "원문 Markdown 전문" in _block_text(blocks[0])
    assert _block_text(blocks[1]) == "리포트 제목"
    assert _block_text(blocks[4]) == "첫 번째 항목"
    assert all(len(_block_text(b)) <= 1800 for b in blocks if b["type"] == "paragraph")


# ── 3. NOTION_TOKEN 없을 때 graceful skip ─────────────────────────────────────
def test_no_token(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    assert na.archive_report("2026-06-23", "내용") is None


# ── 4. 인메모리 Notion 시뮬레이터 ─────────────────────────────────────────────
class FakeNotion:
    """archive_report 가 호출하는 Notion REST 부분집합을 인메모리로 시뮬레이트.

    실제 Notion 규칙 반영: child_page 블록의 id == 그 페이지의 id (동일).
    """

    def __init__(self, dash_id, parent_id):
        self._n = 0
        self.pages = {}     # page_id -> {"parent":..., "archived":bool}
        self.children = {}  # page_id -> [block,...]
        self.dash_id = dash_id
        self.faults = []    # [(method, url_substr, status), ...] 장애 주입
        self.pages[parent_id] = {"parent": {"type": "workspace"}, "archived": False}
        self.children[parent_id] = []
        self.pages[dash_id] = {"parent": {"type": "page_id", "page_id": parent_id}, "archived": False}
        self.children[dash_id] = []

    def _nid(self, p):
        self._n += 1
        return f"{p}-{self._n}"

    def add_fault(self, method, url_substr, status):
        self.faults.append((method, url_substr, status))

    def request(self, method, url, headers=None, params=None, data=None, timeout=None):
        body = json.loads(data) if data else {}
        parts = url.split("/v1/")[1].split("/")

        class _FR:
            def __init__(s, code):
                s.status_code = code
                s.ok = 200 <= code < 300
                s.headers = {}
                s.text = '{"error":"injected"}'

            def json(s):
                return {"error": "injected"}

        for fm, fsub, fst in self.faults:
            if method == fm and fsub in url:
                return _FR(fst)

        class R:
            def __init__(s, code, payload=None):
                s.status_code = code
                s.ok = 200 <= code < 300
                s._p = payload or {}
                s.headers = {}
                s.text = json.dumps(s._p)

            def json(s):
                return s._p

        if method == "GET" and parts[0] == "pages":
            pid = parts[1]
            if pid in self.pages:
                pg = self.pages[pid]
                return R(200, {"id": pid, "parent": pg["parent"], "archived": pg["archived"]})
            return R(404)

        if method == "POST" and parts[0] == "pages":
            parent_id = body["parent"]["page_id"]
            title = body["properties"]["title"]["title"][0]["text"]["content"]
            new_id = self._nid("page")
            self.pages[new_id] = {"parent": {"type": "page_id", "page_id": parent_id}, "archived": False}
            self.children[new_id] = []
            self.children.setdefault(parent_id, []).append(
                {"id": new_id, "type": "child_page", "child_page": {"title": title}})
            return R(200, {"id": new_id})

        if method == "GET" and parts[0] == "blocks" and parts[-1] == "children":
            pid = parts[1]
            return R(200, {"results": list(self.children.get(pid, [])), "has_more": False, "next_cursor": None})

        if method == "PATCH" and parts[0] == "blocks" and parts[-1] == "children":
            pid = parts[1]
            for blk in body["children"]:
                b = dict(blk)
                b["id"] = self._nid("blk")
                if b.get("type") == "toggle":
                    for rt in b["toggle"]["rich_text"]:
                        rt.setdefault("plain_text", rt["text"]["content"])
                self.children.setdefault(pid, []).append(b)
            return R(200, {"results": []})

        if method == "DELETE" and parts[0] == "blocks":
            bid = parts[1]
            for lst in self.children.values():
                lst[:] = [b for b in lst if b.get("id") != bid]
            return R(200, {"id": bid})

        return R(400, {"error": f"unhandled {method} {url}"})


def _toggle_labels(blocks):
    return ["".join(rt.get("plain_text", "") for rt in b["toggle"]["rich_text"])
            for b in blocks if b.get("type") == "toggle"]


def _first_week_id(fake, root_id):
    month_id = next(b["id"] for b in fake.children[root_id]
                    if b.get("type") == "child_page" and not b.get("archived"))
    return next(b["id"] for b in fake.children[month_id] if b.get("type") == "child_page")


def test_tree_and_idempotency(monkeypatch, tmp_path):
    dash = "378a13e7-df00-815a-9fe7-feac02ee5dc6"
    parent = "PARENT-ROOT"
    fake = FakeNotion(dash, parent)

    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.delenv("NOTION_ARCHIVE_ROOT_ID", raising=False)
    monkeypatch.delenv("NOTION_ARCHIVE_PARENT_ID", raising=False)
    monkeypatch.setattr(na, "ROOT_CACHE", tmp_path / "root.json")
    monkeypatch.setattr(na.requests, "request", fake.request)

    # 1회차 → 2회차(같은 날, 멱등) → 3회차(다른 날, 같은 주)
    root1 = na.archive_report("2026-06-23", "줄1\n줄2\n줄3")
    root2 = na.archive_report("2026-06-23", "줄1-수정\n줄2")
    na.archive_report("2026-06-24", "다른날")

    assert root1 is not None and root1 == root2, "루트 id 캐시 재사용 일관"

    # 월 페이지 1개(26/06)
    months = [b for b in fake.children[root1] if b.get("type") == "child_page"]
    assert len(months) == 1 and months[0]["child_page"]["title"] == "26/06"
    month_id = months[0]["id"]

    # 주차 페이지 1개(4주차)
    weeks = [b for b in fake.children[month_id] if b.get("type") == "child_page"]
    assert len(weeks) == 1 and weeks[0]["child_page"]["title"] == "4주차"
    week_id = weeks[0]["id"]

    # 일별 토글: 23(멱등 교체)·24 → 정확히 2개, 23은 1개만, 내용은 최신본
    toggles = [b for b in fake.children[week_id] if b.get("type") == "toggle"]
    labels = _toggle_labels(fake.children[week_id])
    assert len(toggles) == 2
    assert sum("2026-06-23" in l for l in labels) == 1, "06-23 중복 없음"
    assert any("2026-06-24" in l for l in labels), "06-24 존재"

    t23 = next(t for t in toggles
               if "2026-06-23" in "".join(rt.get("plain_text", "") for rt in t["toggle"]["rich_text"]))
    body23 = [c["paragraph"]["rich_text"][0]["text"]["content"] for c in t23["toggle"]["children"]]
    assert "줄1-수정" in body23, "멱등 교체 후 최신 내용 반영"


def test_full_report_page_created_and_replaced(monkeypatch, tmp_path):
    dash = "378a13e7-df00-815a-9fe7-feac02ee5dc6"
    parent = "PARENT-ROOT"
    fake = FakeNotion(dash, parent)
    _setup(monkeypatch, tmp_path, fake)

    root = na.archive_report(
        "2026-06-23",
        "요약1",
        full_text="# 제목\n본문1\n" + ("x" * 2500),
    )
    assert root is not None

    week_id = _first_week_id(fake, root)
    full_pages = [b for b in fake.children[week_id]
                  if b.get("type") == "child_page" and "풀 리포트" in b["child_page"]["title"]]
    assert len(full_pages) == 1
    page_id = full_pages[0]["id"]
    body = fake.children[page_id]
    assert any(b["type"] == "callout" and "원문 Markdown 전문" in _block_text(b) for b in body)
    assert any(b["type"] == "heading_2" and _block_text(b) == "제목" for b in body)
    assert any("본문1" in _block_text(b) for b in body)
    assert all(len(_block_text(b)) <= 1800 for b in body)

    na.archive_report("2026-06-23", "요약2", full_text="# 제목\n본문2")
    full_pages_after = [b for b in fake.children[week_id]
                        if b.get("type") == "child_page" and "풀 리포트" in b["child_page"]["title"]]
    assert len(full_pages_after) == 1
    body_text = "\n".join(_block_text(b) for b in fake.children[page_id])
    assert "본문2" in body_text
    assert "본문1" not in body_text


def test_resolve_root_uses_cache(monkeypatch, tmp_path):
    """캐시에 살아있는 루트 id 가 있으면 부모 탐색 없이 즉시 반환."""
    cache = tmp_path / "root.json"
    cache.write_text(json.dumps({"root_id": "cached-root"}), encoding="utf-8")
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.delenv("NOTION_ARCHIVE_ROOT_ID", raising=False)
    monkeypatch.setattr(na, "ROOT_CACHE", cache)

    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url))

        class R:
            status_code = 200
            ok = True
            headers = {}
            text = "{}"

            def json(self):
                return {"id": "cached-root", "archived": False, "parent": {}}
        return R()

    monkeypatch.setattr(na.requests, "request", fake_request)
    assert na.resolve_root_id() == "cached-root"
    # 캐시 검증용 GET /pages/{id} 한 번만, POST(생성) 없음
    assert all(m != "POST" for m, _ in calls)


def _setup(monkeypatch, tmp_path, fake):
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.delenv("NOTION_ARCHIVE_ROOT_ID", raising=False)
    monkeypatch.delenv("NOTION_ARCHIVE_PARENT_ID", raising=False)
    monkeypatch.setattr(na, "ROOT_CACHE", tmp_path / "root.json")
    monkeypatch.setattr(na.requests, "request", fake.request)


def test_skips_trashed_month_page(monkeypatch, tmp_path):
    """사용자가 월 페이지를 휴지통에 보내면(archived) 새로 만든다 — 휴지통에 쓰지 않음."""
    dash, parent = "378a13e7-df00-815a-9fe7-feac02ee5dc6", "PARENT-ROOT"
    fake = FakeNotion(dash, parent)
    _setup(monkeypatch, tmp_path, fake)

    root = na.archive_report("2026-06-23", "원본")          # 트리 생성
    months = [b for b in fake.children[root] if b.get("type") == "child_page"]
    assert len(months) == 1
    # 월 페이지를 휴지통으로(archived=True)
    months[0]["archived"] = True

    na.archive_report("2026-06-23", "재시도")               # 휴지통 건너뛰고 새 월 생성
    alive = [b for b in fake.children[root]
             if b.get("type") == "child_page" and not b.get("archived")]
    assert len(alive) == 1 and alive[0]["child_page"]["title"] == "26/06"
    assert alive[0]["id"] != months[0]["id"], "휴지통 페이지를 재사용하지 않고 새로 만듦"


def test_listing_failure_aborts_create(monkeypatch, tmp_path):
    """루트 자식 조회 실패 시 월 페이지를 만들지 않는다(고아 중복 방지)."""
    dash, parent = "378a13e7-df00-815a-9fe7-feac02ee5dc6", "PARENT-ROOT"
    fake = FakeNotion(dash, parent)
    _setup(monkeypatch, tmp_path, fake)

    root = na.resolve_root_id()                              # 루트 먼저 확보(캐시)
    assert root is not None
    before = len(fake.children[root])
    fake.add_fault("GET", f"/blocks/{root}/children", 403)   # 루트 자식 조회 실패 주입

    result = na.archive_report("2026-06-23", "내용")
    assert result == root, "조회 실패해도 루트 id 는 반환(대시보드 링크 유지)"
    months = [b for b in fake.children[root] if b.get("type") == "child_page"]
    assert len(months) == 0, "조회 실패 시 월 페이지 생성 안 함"
    assert len(fake.children[root]) == before


def test_delete_failure_keeps_new_toggle(monkeypatch, tmp_path):
    """기존 토글 DELETE 가 실패해도 새 토글(최신본)은 항상 존재한다(데이터 보존)."""
    dash, parent = "378a13e7-df00-815a-9fe7-feac02ee5dc6", "PARENT-ROOT"
    fake = FakeNotion(dash, parent)
    _setup(monkeypatch, tmp_path, fake)

    na.archive_report("2026-06-23", "원본내용")
    fake.add_fault("DELETE", "/blocks/", 400)               # 모든 DELETE 실패
    na.archive_report("2026-06-23", "수정내용")             # 새 토글 추가, 기존 삭제 실패

    # 트리 따라 주차 페이지 찾기
    root = na.resolve_root_id()
    month_id = next(b["id"] for b in fake.children[root]
                    if b.get("type") == "child_page" and not b.get("archived"))
    week_id = next(b["id"] for b in fake.children[month_id] if b.get("type") == "child_page")
    bodies = []
    for t in fake.children[week_id]:
        if t.get("type") == "toggle":
            bodies += [c["paragraph"]["rich_text"][0]["text"]["content"] for c in t["toggle"]["children"]]
    assert "수정내용" in bodies, "삭제 실패에도 최신본은 반드시 존재(데이터 손실 없음)"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
