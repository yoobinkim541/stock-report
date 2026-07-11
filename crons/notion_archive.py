#!/usr/bin/env python3
"""
notion_archive.py — 일일 리포트를 Notion 월/주 계층 페이지에 누적 아카이빙.

계층:  [대시보드 부모] → 📚 리포트 아카이브 → 26/06(월) → 1주차(주)
       → 일별 요약 토글 + 일별 풀 리포트 페이지

설계 원칙:
  - 페이지는 find-or-create (제목 매칭, 자식 리스팅 기반 → search 의 지연
    일관성으로 인한 중복 생성 회피).
  - 같은 날 재실행 시 그날 토글만 교체(멱등 upsert).
  - 라이브 대시보드(notion_sync.update_page)와 완전 독립 — 여기서 예외가
    나도 대시보드 동기화는 계속된다(호출부에서 best-effort 로 감싼다).
  - 통합 토큰 쓰기 권한 범위 안에서만 동작(부모 페이지가 통합에 공유돼야 함).

env:
    NOTION_TOKEN            — 필수
    NOTION_ARCHIVE_ROOT_ID  — (선택) 아카이브 루트 페이지 강제 지정(자동탐색 생략)
    NOTION_ARCHIVE_PARENT_ID— (선택) 루트를 만들 부모(기본: 대시보드의 부모)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

KST            = timezone(timedelta(hours=9))
API            = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
ROOT_TITLE     = "리포트 아카이브"   # 표시 이모지는 페이지 아이콘(📚)으로 — 제목 텍스트엔 미포함(중복 방지)
ROOT_CACHE     = Path(os.path.expanduser("~/.cache/notion_archive_root.json"))
_WEEKDAY_KR    = ["월", "화", "수", "목", "금", "토", "일"]


# ── HTTP ────────────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {os.getenv('NOTION_TOKEN')}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION}


class NotionListError(Exception):
    """자식 조회(GET children) 실패 — '비어있음'과 구분해 중복 생성을 막는다."""


def _request(method: str, url: str, *, retries: int = 3, **kw) -> requests.Response:
    """429/5xx/409 는 Retry-After(또는 점증 백오프)로 재시도. 그 외는 그대로 반환.

    409(conflict)는 동시 저장 충돌로 일시적 — 재시도 대상에 포함.
    """
    kw.setdefault("timeout", 20)
    last = None
    for attempt in range(retries):
        r = requests.request(method, url, headers=_headers(), **kw)
        if r.status_code not in (409, 429, 500, 502, 503, 504):
            return r
        last = r
        wait = float(r.headers.get("Retry-After", 1.5 * (attempt + 1)))
        logger.warning("Notion %s %s → %s, %.1fs 후 재시도 (%d/%d)",
                       method, url.rsplit("/", 1)[-1], r.status_code, wait, attempt + 1, retries)
        time.sleep(min(wait, 8.0))
    return last


# ── 블록 빌더 (자체 포함 — notion_sync 와 순환 import 회피) ───────────────────────

def _rt(text: str, bold: bool = False) -> list[dict]:
    return [{"type": "text", "text": {"content": text[:2000]},
             "annotations": {"bold": bold}}]


def _para(text: str, bold: bool = False, color: str = "default") -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": _rt(text, bold), "color": color}}


def _h3(text: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": _rt(text)}}


def _h2(text: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": _rt(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rt(text)}}


def _callout(text: str, emoji: str = "💡", color: str = "gray_background") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": _rt(text), "icon": {"type": "emoji", "emoji": emoji},
                        "color": color}}


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _toggle(title: str, children: list[dict], color: str = "default") -> dict:
    return {"object": "block", "type": "toggle",
            "toggle": {"rich_text": _rt(title), "color": color, "children": children}}


def link_to_page(page_id: str) -> dict:
    return {"object": "block", "type": "link_to_page",
            "link_to_page": {"type": "page_id", "page_id": page_id}}


# ── 페이지 트리 헬퍼 ─────────────────────────────────────────────────────────────

def _iter_children(parent_id: str):
    """parent_id 의 직속 블록을 페이지네이션하며 순회. 조회 실패 시 NotionListError.

    (실패를 '빈 결과'로 흘리면 find_or_create 가 중복 페이지를 만든다 → 반드시 구분.)
    """
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = _request("GET", f"{API}/blocks/{parent_id}/children", params=params)
        if not r.ok:
            raise NotionListError(f"GET children {parent_id[:8]} → {r.status_code}: {r.text[:120]}")
        data = r.json()
        for b in data.get("results", []):
            yield b
        if not data.get("has_more"):
            return
        cursor = data.get("next_cursor")


def _child_page_title(block: dict) -> str:
    return (block.get("child_page") or {}).get("title", "") if block.get("type") == "child_page" else ""


def find_child_page(parent_id: str, title: str) -> str | None:
    """parent_id 의 자식 중 제목이 일치하는 (살아있는) child_page id.

    휴지통(archived/in_trash) child_page 는 건너뛴다 — 그래야 사용자가 월/주
    페이지를 휴지통에 보내도 '없음'으로 보고 새로 만든다(휴지통 페이지에 쓰면 400 실패).
    NotionListError 는 호출부로 전파(생성 보류 판단용).
    """
    for b in _iter_children(parent_id):
        if b.get("type") != "child_page":
            continue
        if b.get("archived") or b.get("in_trash"):
            continue
        if _child_page_title(b).strip() == title.strip():
            return b["id"]
    return None


def create_page(parent_id: str, title: str, icon: str | None = None) -> str | None:
    body: dict = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "properties": {"title": {"title": _rt(title)}},
    }
    if icon:
        body["icon"] = {"type": "emoji", "emoji": icon}
    r = _request("POST", f"{API}/pages", data=json.dumps(body))
    if not r.ok:
        logger.error("페이지 생성 실패 '%s' %s: %s", title, r.status_code, r.text[:200])
        return None
    return r.json().get("id")


def find_or_create_page(parent_id: str, title: str, icon: str | None = None) -> str | None:
    """find-or-create. 자식 조회가 실패하면(NotionListError) 생성하지 않고 None 반환.

    조회 실패를 '없음'으로 오인해 생성하면, 일시 장애마다 매번 중복(고아) 페이지가
    쌓인다 → 모호하면 생성 보류가 안전(상위가 None 을 tolerate).
    """
    try:
        pid = find_child_page(parent_id, title)
    except NotionListError as e:
        logger.error("자식 조회 실패 — '%s' 생성 보류(중복 방지): %s", title, e)
        return None
    if pid:
        return pid
    logger.info("아카이브 페이지 생성: %s (parent=%s)", title, parent_id[:8])
    return create_page(parent_id, title, icon)


def append_children(page_id: str, blocks: list[dict]) -> bool:
    for i in range(0, len(blocks), 100):
        r = _request("PATCH", f"{API}/blocks/{page_id}/children",
                     data=json.dumps({"children": blocks[i:i + 100]}), timeout=40)
        if not r.ok:
            logger.error("블록 추가 실패 %s: %s", r.status_code, r.text[:200])
            return False
    return True


def _find_toggle_ids(page_id: str, marker: str) -> list[str]:
    """page_id 직속 토글 중 제목에 marker 가 든 블록 id 목록 (NotionListError 전파)."""
    ids = []
    for b in _iter_children(page_id):
        if b.get("type") != "toggle":
            continue
        txt = "".join(t.get("plain_text", "") for t in (b.get("toggle") or {}).get("rich_text", []))
        if marker in txt:
            ids.append(b["id"])
    return ids


def _delete_blocks(block_ids: list[str]) -> tuple[int, int]:
    """블록 id 들을 삭제. (성공, 실패) 반환. 404(이미 없음)는 성공으로 간주."""
    ok = fail = 0
    for bid in block_ids:
        r = _request("DELETE", f"{API}/blocks/{bid}")
        if r.ok or r.status_code == 404:
            ok += 1
        else:
            fail += 1
            logger.warning("토글 삭제 실패 %s: %s", bid[:8], r.status_code)
    return ok, fail


def _chunks(text: str, size: int = 1800) -> list[str]:
    """Notion rich_text 2,000자 제한을 피하는 안전 분할."""
    s = str(text or "")
    if len(s) <= size:
        return [s]
    return [s[i:i + size] for i in range(0, len(s), size)]


def _full_report_blocks(report_date: str, markdown_text: str) -> list[dict]:
    """풀 Markdown 리포트 전문을 Notion 블록으로 변환한다.

    Markdown을 완전 렌더링하려 들기보다, 헤더/불릿 정도만 살리고 표·문단은 원문
    라인 그대로 보존한다. 긴 라인은 1,800자 단위로 쪼개 Notion 제한을 피한다.
    """
    raw = markdown_text or ""
    blocks: list[dict] = [
        _callout(
            f"원문 Markdown 전문 · {report_date} · {len(raw):,}자",
            "📄",
            "blue_background",
        )
    ]
    in_code = False
    for line in raw.splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        if s.strip().startswith("```"):
            in_code = not in_code
            blocks.append(_para(s))
            continue
        if in_code:
            for chunk in _chunks(s):
                blocks.append(_para(chunk))
            continue
        if s.startswith("## "):
            blocks.append(_h2(s[3:].strip()))
            continue
        if s.startswith("### "):
            blocks.append(_h3(s[4:].strip()))
            continue
        if s.startswith("# "):
            blocks.append(_h2(s[2:].strip()))
            continue
        if s[:2] in ("- ", "* "):
            for chunk in _chunks(s[2:].strip()):
                blocks.append(_bullet(chunk))
            continue
        for chunk in _chunks(s):
            blocks.append(_para(chunk))
    return blocks or [_para("(풀 리포트 없음)")]


def _full_report_title(report_date: str, weekday: str) -> str:
    return f"📄 {report_date} ({weekday}) · 풀 리포트"


def _replace_page_children(page_id: str, blocks: list[dict]) -> bool:
    """페이지 본문 안전 교체: append 성공 후 기존 블록 삭제."""
    try:
        old_ids = [b["id"] for b in _iter_children(page_id)
                   if b.get("type") != "child_database"]
    except NotionListError as e:
        old_ids = []
        logger.warning("풀 리포트 기존 블록 조회 실패 — 삭제 생략: %s", e)
    if not append_children(page_id, blocks):
        return False
    deleted, failed = _delete_blocks(old_ids)
    if deleted or failed:
        logger.info("풀 리포트 기존 블록 정리: 삭제 %d건%s",
                    deleted, f", 실패 {failed}건" if failed else "")
    return True


def _upsert_full_report_page(week_id: str, report_date: str,
                             weekday: str, markdown_text: str) -> bool:
    title = _full_report_title(report_date, weekday)
    page_id = find_or_create_page(week_id, title, icon="📄")
    if not page_id:
        return False
    return _replace_page_children(page_id, _full_report_blocks(report_date, markdown_text))


# ── 루트 페이지 해석(캐시) ───────────────────────────────────────────────────────

def _dashboard_parent_id() -> str | None:
    """대시보드 부모 페이지 id (루트를 형제로 생성할 위치).

    대시보드가 워크스페이스/DB 루트에 직접 달려 있어 부모가 page 가 아니면
    형제 생성이 불가 → NOTION_ARCHIVE_PARENT_ID 를 지정하도록 안내(아카이브 비활성).
    (대시보드 자신을 부모로 쓰면 매일 delete-all-then-append 에 지워지므로 금물.)
    """
    if os.getenv("NOTION_ARCHIVE_PARENT_ID"):
        return os.getenv("NOTION_ARCHIVE_PARENT_ID")
    dash = os.getenv("NOTION_DASHBOARD_PAGE_ID", "378a13e7-df00-815a-9fe7-feac02ee5dc6")
    r = _request("GET", f"{API}/pages/{dash}")
    if not r.ok:
        logger.error("대시보드 페이지 조회 실패 %s", r.status_code)
        return None
    parent = r.json().get("parent", {})
    if parent.get("type") != "page_id" or not parent.get("page_id"):
        logger.error("대시보드 부모가 페이지가 아님(type=%s) — NOTION_ARCHIVE_PARENT_ID 를 지정하세요. 아카이브 생략.",
                     parent.get("type"))
        return None
    return parent.get("page_id")


def _load_root_cache() -> str | None:
    try:
        return json.loads(ROOT_CACHE.read_text(encoding="utf-8")).get("root_id")
    except Exception:
        return None


def _save_root_cache(root_id: str) -> None:
    try:
        ROOT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ROOT_CACHE.write_text(json.dumps({"root_id": root_id}), encoding="utf-8")
    except Exception as e:
        logger.warning("루트 캐시 저장 실패: %s", e)


def _page_alive(page_id: str) -> bool:
    r = _request("GET", f"{API}/pages/{page_id}")
    return r.ok and not r.json().get("archived", False)


def resolve_root_id() -> str | None:
    """아카이브 루트 페이지 id 해석: env > 캐시(검증) > 부모 자식 탐색 > 생성."""
    if os.getenv("NOTION_ARCHIVE_ROOT_ID"):
        return os.getenv("NOTION_ARCHIVE_ROOT_ID")

    cached = _load_root_cache()
    if cached and _page_alive(cached):
        return cached

    parent_id = _dashboard_parent_id()
    if not parent_id:
        return None
    root_id = find_or_create_page(parent_id, ROOT_TITLE, icon="📚")
    if root_id:
        _save_root_cache(root_id)
    return root_id


# ── 주차 계산 ────────────────────────────────────────────────────────────────────

def week_of_month(d: date) -> int:
    """1일이 속한 주 = 1주차 (월요일 시작, 한국 관례).

    예) 1일이 목요일이면 그 주(1~?)가 1주차, 다음 월요일부터 2주차.
    """
    first = d.replace(day=1)
    return (d.day + first.weekday() - 1) // 7 + 1


# ── 메인: 일별 리포트 upsert ─────────────────────────────────────────────────────

def archive_report(report_date: str, summary_text: str, full_text: str | None = None) -> str | None:
    """리포트 요약과 풀 리포트를 월/주 계층에 누적. 아카이브 루트 id 반환.

    report_date: 'YYYY-MM-DD' (리포트 자체 날짜)
    summary_text: 일일 요약 본문(여러 줄). 비어 있으면 일별 항목은 추가하지 않고
                  루트만 보장한다(대시보드 링크가 항상 살아 있도록).
    full_text: investment-report-YYYY-MM-DD.md 전문. 있으면 주차 페이지 아래 별도
               일별 풀 리포트 페이지로 멱등 upsert 한다.
    """
    if not os.getenv("NOTION_TOKEN"):
        logger.warning("NOTION_TOKEN 없음 — 아카이브 생략")
        return None

    root_id = resolve_root_id()
    if not root_id:
        logger.error("아카이브 루트 해석 실패 — 아카이브 생략")
        return None

    has_summary = bool(summary_text and summary_text.strip())
    has_full = bool(full_text and full_text.strip())
    if not ((has_summary or has_full) and report_date):
        logger.info("리포트 본문 없음 — 루트만 보장, 일별 항목 생략")
        return root_id

    try:
        d = datetime.strptime(report_date, "%Y-%m-%d").date()
    except ValueError:
        logger.warning("report_date 파싱 실패: %r — 일별 항목 생략", report_date)
        return root_id

    # 월 → 주 페이지 find-or-create
    month_title = f"{d.strftime('%y')}/{d.strftime('%m')}"           # 26/06
    wk          = week_of_month(d)
    week_title  = f"{wk}주차"
    month_id = find_or_create_page(root_id, month_title, icon="🗓️")
    if not month_id:
        return root_id
    week_id = find_or_create_page(month_id, week_title, icon="📂")
    if not week_id:
        return root_id

    # 일별 토글 멱등 교체 — append-then-delete:
    #   ① append 전에 기존 같은 날 토글 id 수집  ② 새 토글 추가  ③ '기존' 것만 삭제.
    # 이 순서라야 append 실패 시에도 기존본이 남고(데이터 보존), 삭제 실패해도 새 본은
    # 항상 존재한다(중복은 다음 5일 재아카이빙 때 자가치유).
    weekday = _WEEKDAY_KR[d.weekday()]
    title    = f"📅 {report_date} ({weekday}) · 투자 리포트"
    lines    = [l for l in (summary_text or "").strip().split("\n") if l.strip()][:90]

    deleted = failed = 0
    if has_summary:
        try:
            old_ids = _find_toggle_ids(week_id, report_date)
        except NotionListError as e:
            old_ids = []   # 조회 실패 → 삭제 생략(중복 가능하나 데이터 보존 우선, 다음 회차 정리)
            logger.warning("기존 토글 조회 실패 — 중복 제거 생략: %s", e)

        ok = append_children(week_id, [_toggle(title, [_para(l) for l in lines])])
        if not ok:
            logger.error("아카이브 일별 요약 추가 실패: %s (기존본 유지)", report_date)
            return root_id
        deleted, failed = _delete_blocks(old_ids)

    full_ok = False
    if has_full:
        full_ok = _upsert_full_report_page(week_id, report_date, weekday, full_text or "")
        if not full_ok:
            logger.error("아카이브 풀 리포트 추가 실패: %s", report_date)

    logger.info("아카이브 완료: %s → %s / %s (요약 %d줄%s, 교체 %d건%s)",
                report_date, month_title, week_title, len(lines),
                " · 풀 리포트" if full_ok else "", deleted,
                f", 삭제실패 {failed}" if failed else "")
    return root_id
