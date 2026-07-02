#!/usr/bin/env python3
"""
notion_sync.py — stock-report → Notion 대시보드 자동 동기화 + 리포트 아카이빙

두 가지를 한다:
  1) 라이브 대시보드(DASHBOARD_PAGE_ID) 매일 덮어쓰기 — 현재 시황·수익률·랭킹 스냅샷.
  2) 당일 리포트를 월/주 계층 페이지에 누적 아카이빙(crons/notion_archive.py) —
     📚 리포트 아카이브 → 26/06 → 4주차 → 일별 토글. 멱등 upsert, 대시보드와 독립
     (아카이브 실패해도 대시보드 동기화는 계속). 대시보드 하단엔 아카이브 링크를 단다.

크론 (평일 23:30 UTC = 08:30 KST — 리포트 23:00 이후, 당일 요약 반영):
    30 23 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python crons/notion_sync.py >> /tmp/notion_sync.log 2>&1

환경변수:
    NOTION_TOKEN         — Notion Integration Token (필수)
    NOTION_ARCHIVE_ROOT_ID / NOTION_ARCHIVE_PARENT_ID — 아카이브 위치 (선택, notion_archive 참고)
    STOCK_BOT_TOKEN      — 텔레그램 봇 (실패 알림용, 선택)
    STOCK_BOT_CHAT_ID    — 텔레그램 채팅 ID
"""
from __future__ import annotations

import json
import logging
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ticker_names  # 종목명 resolver (루트 모듈 — sys.path 세팅 이후)
import urllib.parse
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NOTION_TOKEN     = os.getenv("NOTION_TOKEN")
DASHBOARD_PAGE_ID = "378a13e7-df00-815a-9fe7-feac02ee5dc6"
KST = timezone(timedelta(hours=9))


# ── Notion API ────────────────────────────────────────────────────────────────

def _h(timeout: int = 15):
    return {"Authorization": f"Bearer {NOTION_TOKEN}",
            "Content-Type": "application/json", "Notion-Version": "2022-06-28"}


def update_page(page_id: str, blocks: list[dict]) -> bool:
    """안전 스왑: 새 블록을 먼저 append 한 뒤 구 블록을 삭제 (N4).

    delete-then-rebuild 는 패치 실패 시 페이지가 빈 채로 남는다. 여기선
    ① 구 children id 수집(페이지네이션) → ② 새 블록 append(여기서 실패하면
    구 대시보드가 그대로 보존됨) → ③ 구 블록 삭제. child_database(보유종목
    DB 등 영속 객체)는 삭제 대상에서 제외.
    """
    import requests
    headers = _h()

    # ① 기존 children id 수집 (100개 초과 대비 페이지네이션)
    old: list[tuple[str, str]] = []
    cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            logger.error("블록 조회 실패 %s", r.status_code)
            return False
        j = r.json()
        old += [(b["id"], b.get("type", "")) for b in j.get("results", [])]
        if not j.get("has_more"):
            break
        cursor = j.get("next_cursor")

    # ② 새 블록 먼저 추가 (100개씩 배치) — 실패해도 구 대시보드는 그대로
    for i in range(0, len(blocks), 100):
        r2 = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=headers, json={"children": blocks[i:i+100]}, timeout=30,
        )
        if not r2.ok:
            logger.error("블록 추가 실패 %s: %s — 기존 대시보드 보존", r2.status_code, r2.text[:200])
            return False

    # ③ 구 블록 삭제 (child_database 는 영속 — 제외)
    for bid, btype in old:
        if btype == "child_database":
            continue
        requests.delete(f"https://api.notion.com/v1/blocks/{bid}", headers=headers, timeout=8)
    return True


# ── Notion 블록 빌더 ──────────────────────────────────────────────────────────

def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _h1(text: str) -> dict:
    return {"object": "block", "type": "heading_1",
            "heading_1": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _h2(text: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _h3(text: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _para(text: str, bold: bool = False, color: str = "default") -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text},
                                         "annotations": {"bold": bold}}], "color": color}}


def _callout(text: str, emoji: str = "💡", color: str = "gray_background") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": [{"type": "text", "text": {"content": text}}],
                        "icon": {"type": "emoji", "emoji": emoji}, "color": color}}


def _quote(text: str) -> dict:
    return {"object": "block", "type": "quote",
            "quote": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _image(url: str, caption: str = "") -> dict:
    block: dict = {"object": "block", "type": "image",
                   "image": {"type": "external", "external": {"url": url}}}
    if caption:
        block["image"]["caption"] = [{"type": "text", "text": {"content": caption}}]
    return block


def _table(rows: list[list[str]], header: bool = True) -> dict:
    table_rows = [
        {"object": "block", "type": "table_row",
         "table_row": {"cells": [[{"type": "text", "text": {"content": str(c)}}] for c in row]}}
        for row in rows
    ]
    return {"object": "block", "type": "table",
            "table": {"table_width": len(rows[0]) if rows else 1,
                      "has_column_header": header, "has_row_header": False,
                      "children": table_rows}}


def _toggle(title: str, children: list[dict]) -> dict:
    return {"object": "block", "type": "toggle",
            "toggle": {"rich_text": [{"type": "text", "text": {"content": title}}],
                       "children": children}}


# ── 리치 텍스트 / 히어로 / 컬럼 / 파일 임베드 (N1·N2) ─────────────────────────────

def _rt(content: str, bold: bool = False, color: str = "default") -> dict:
    """단일 rich_text 런 (굵게·색 지정 가능)."""
    return {"type": "text", "text": {"content": content},
            "annotations": {"bold": bold, "color": color}}


def _callout_rich(runs: list[dict], emoji: str = "💡", color: str = "gray_background") -> dict:
    """여러 색·굵기 런으로 구성된 콜아웃."""
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": runs, "icon": {"type": "emoji", "emoji": emoji}, "color": color}}


def _column(children: list[dict]) -> dict:
    return {"object": "block", "type": "column", "column": {"children": children}}


def _columns(cols: list[list[dict]]) -> dict:
    """column_list — 각 컬럼은 블록 리스트 (≥2 컬럼·각 ≥1 자식 필수)."""
    return {"object": "block", "type": "column_list",
            "column_list": {"children": [_column(c) for c in cols]}}


def _phase_label(mt: str, pk) -> str:
    labels = {"bull": {"bull2": "🫧 Bull-2 (버블)", "bull1": "🐂 Bull-1 (강세)"},
              "bear": {0: "🟢 0 정상", 1: "🟡 1 조정", 2: "🟠 2 중조정",
                       3: "🔴 3 심조정", 4: "🚨 4 급락", 5: "💥 5 폭락"}}
    return labels.get(mt, {}).get(pk, f"{mt}-{pk}")


def _hero_band(phase_str: str, mt: str, qqq: dict, dd: float, ret: float,
               total: float, dca: dict) -> dict:
    """상단 히어로 KPI 밴드 — Phase·QQQ낙폭·내포트·DCA 4열 컬러 콜아웃."""
    cur = qqq.get("current", 0) or 0
    phase_color = {"bull": "green_background", "neutral": "gray_background"}.get(mt, "red_background")
    dd_color  = "red_background" if dd <= -10 else "orange_background" if dd <= -5 else "green_background"
    dd_txt    = "red" if dd <= -5 else "default"
    ret_color = "green_background" if ret >= 0 else "red_background"
    ret_txt   = "green" if ret >= 0 else "red"
    phase_co = _callout_rich(
        [_rt("Phase\n", color="gray"), _rt(phase_str, bold=True)], "🎯", phase_color)
    qqq_co = _callout_rich(
        [_rt("QQQ 낙폭\n", color="gray"), _rt(f"{dd:+.1f}%", bold=True, color=dd_txt),
         _rt(f"  ${cur:,.0f}", color="gray")], "📉" if dd < 0 else "📈", dd_color)
    port_co = _callout_rich(
        [_rt("내 포트폴리오\n", color="gray"), _rt(f"{ret:+.1f}%", bold=True, color=ret_txt),
         _rt(f"  ${total:,.0f}", color="gray")], "💰", ret_color)
    dca_co = _callout_rich(
        [_rt("DCA 배율\n", color="gray"), _rt(f"{dca['multiplier']}×", bold=True),
         _rt(f"  {dca['total_krw']:,}원/일", color="gray")], "🎚️", "blue_background")
    return _columns([[phase_co], [qqq_co], [port_co], [dca_co]])


# 섹션 헤더 프리픽스 — investment_report._build_mobile_summary 의 실제 섹션 마커와 결합.
# (형식 변경 시 헤더가 평문으로 우아하게 강등될 뿐 깨지지 않음)
_HDR_PREFIXES = ("📌", "🌎", "💼", "🛒", "🔎", "🇺🇸", "🇰🇷", "🏛️", "📊", "📰", "⚠️ ", "🧠", "🗓️", "🏆", "🎯")


def _report_blocks(lines: list[str], limit: int = 45) -> list[dict]:
    """요약 텍스트 줄 → 구조화 블록 (섹션 헤더·불릿·문단). 40줄 평문 덤프 대체.

    헤더: 섹션 프리픽스로 시작 + `(티커)` 없음 + 24자 이내 → h3.
    종목 항목(`✅ NVDA(...)`·`🟢 DXCM(...) 79점`)은 `(` 가 있어 문단으로 유지.
    """
    out: list[dict] = []
    for ln in lines[:limit]:
        s = ln.strip()
        if not s:
            continue
        if set(s) <= set("─━═-=·•▪◦ ▰▱"):          # 구분선·진행바만 → 스킵
            continue
        if s[:2] in ("- ", "• ", "· ") or (s and s[0] in "•·▪◦"):
            out.append(_bullet(s.lstrip("-•·▪◦ ️").strip()))
            continue
        if any(s.startswith(p) for p in _HDR_PREFIXES) and "(" not in s and len(s) <= 24:
            out.append(_h3(s))
            continue
        out.append(_para(s))
    return out or [_para("(요약 없음)")]


def _image_upload(file_id: str, caption: str = "") -> dict:
    block: dict = {"object": "block", "type": "image",
                   "image": {"type": "file_upload", "file_upload": {"id": file_id}}}
    if caption:
        block["image"]["caption"] = [{"type": "text", "text": {"content": caption}}]
    return block


def _upload_file_to_notion(path: str) -> str | None:
    """로컬 파일 → Notion 파일 업로드 (3단계). 실패 시 None (호출부 QuickChart 폴백)."""
    import requests
    if not path or not os.path.exists(path):
        return None
    base = {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28"}
    try:
        r = requests.post("https://api.notion.com/v1/file_uploads",
                          headers={**base, "Content-Type": "application/json"},
                          json={}, timeout=20)
        if not r.ok:
            logger.warning("file_upload 생성 실패 %s: %s", r.status_code, r.text[:160])
            return None
        up = r.json()
        fid, url = up.get("id"), up.get("upload_url")
        if not fid or not url:
            return None
        with open(path, "rb") as f:
            r2 = requests.post(url, headers=base,
                               files={"file": (os.path.basename(path), f, "image/png")}, timeout=60)
        if not r2.ok:
            logger.warning("file_upload 전송 실패 %s: %s", r2.status_code, r2.text[:160])
            return None
        logger.info("PNG 업로드 완료: %s", os.path.basename(path))
        return fid
    except Exception as e:
        logger.warning("file_upload 예외: %s", e)
        return None


def _latest_chart_png() -> str | None:
    """오늘(없으면 최근) 포트폴리오 대시보드 PNG 경로."""
    import glob
    today = datetime.now(KST).strftime("%Y-%m-%d")
    p = os.path.expanduser(f"~/reports/investment-chart-{today}.png")
    if os.path.exists(p):
        return p
    cands = sorted(glob.glob(os.path.expanduser("~/reports/investment-chart-*.png")))
    return cands[-1] if cands else None


# ── 보유 종목 데이터베이스 (N3) ─────────────────────────────────────────────────
# 대시보드 자식 DB(child_database)로 1회 생성 후 매일 행 upsert. N4 의 안전 스왑이
# child_database 를 보존하므로 일일 재빌드에도 DB·뷰·정렬이 유지된다.

HOLDINGS_DB_CACHE = os.path.expanduser("~/.cache/notion_holdings_db.json")

_HOLDINGS_SCHEMA = {
    "Ticker":  {"title": {}},
    "종목명":   {"rich_text": {}},
    "통화":     {"select": {"options": [{"name": "USD", "color": "blue"},
                                       {"name": "KRW", "color": "green"}]}},
    "수량":     {"number": {"format": "number"}},
    "평단가":   {"number": {"format": "number"}},
    "현재가":   {"number": {"format": "number"}},
    "평가액":   {"number": {"format": "number"}},
    "손익률":   {"number": {"format": "percent"}},
    "비중":     {"number": {"format": "percent"}},
}


def _load_holdings() -> list[dict]:
    """portfolio_snapshot.json → 정규화 보유 리스트 (USD 해외 + KRW 국내).

    비중은 통화별 합계 기준. avg/current 결측 시 cost·value/shares 로 파생.
    """
    repo = os.getenv("STOCK_REPORT_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo, "portfolio_snapshot.json")
    try:
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception as e:
        logger.warning("snapshot 로드 실패(holdings DB): %s", e)
        return []

    rows: list[dict] = []
    usd = []
    for sec in ("overseas_general", "overseas_fractional"):
        usd += snap.get(sec, {}).get("holdings_usd", []) or []
    tot_usd = sum(h.get("value_usd", 0) or 0 for h in usd) or 1
    for h in usd:
        shares = h.get("shares", 0) or 0
        val = h.get("value_usd", 0) or 0
        avg = h.get("avg_price_usd") or (h.get("cost_usd", 0) / shares if shares else 0)
        cur = h.get("current_price_usd") or (val / shares if shares else 0)
        rows.append({"ticker": h.get("ticker", ""), "name": h.get("name", ""), "ccy": "USD",
                     "shares": shares, "avg": avg, "cur": cur, "value": val,
                     "ret": (h.get("return_pct", 0) or 0) / 100, "weight": val / tot_usd})

    dom = snap.get("domestic", {}).get("holdings", []) or []
    tot_krw = sum((d.get("current_price", 0) or 0) * (d.get("shares", 0) or 0) for d in dom) or 1
    for d in dom:
        shares = d.get("shares", 0) or 0
        cur = d.get("current_price", 0) or 0
        val = cur * shares
        rows.append({"ticker": d.get("ticker", ""), "name": d.get("name", ""), "ccy": "KRW",
                     "shares": shares, "avg": d.get("avg_price", 0) or 0, "cur": cur, "value": val,
                     "ret": (d.get("return_pct", 0) or 0) / 100, "weight": val / tot_krw})
    return rows


def _db_props(row: dict) -> dict:
    def num(x):
        return None if x is None else round(float(x), 4)
    return {
        "Ticker": {"title": [{"text": {"content": row["ticker"] or "—"}}]},
        "종목명":  {"rich_text": [{"text": {"content": (row["name"] or ticker_names.display_name(row["ticker"]) or "")[:80]}}]},
        "통화":    {"select": {"name": row["ccy"]}},
        "수량":    {"number": num(row["shares"])},
        "평단가":  {"number": num(row["avg"])},
        "현재가":  {"number": num(row["cur"])},
        "평가액":  {"number": num(row["value"])},
        "손익률":  {"number": num(row["ret"])},
        "비중":    {"number": num(row["weight"])},
    }


def _ensure_holdings_db(parent_page_id: str) -> str | None:
    """보유 종목 DB id 반환 — 캐시에 있고 살아있으면 재사용, 아니면 생성."""
    import requests
    headers = _h()
    cached = None
    try:
        with open(HOLDINGS_DB_CACHE) as f:
            cached = json.load(f).get("database_id")
    except Exception:
        pass
    if cached:
        r = requests.get(f"https://api.notion.com/v1/databases/{cached}", headers=headers, timeout=15)
        if r.ok and not r.json().get("archived"):
            return cached
    body = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "is_inline": False,
        "icon": {"type": "emoji", "emoji": "📊"},
        "title": [{"type": "text", "text": {"content": "보유 종목"}}],
        "properties": _HOLDINGS_SCHEMA,
    }
    r = requests.post("https://api.notion.com/v1/databases", headers=headers, json=body, timeout=20)
    if not r.ok:
        logger.warning("보유종목 DB 생성 실패 %s: %s", r.status_code, r.text[:200])
        return None
    did = r.json()["id"]
    try:
        os.makedirs(os.path.dirname(HOLDINGS_DB_CACHE), exist_ok=True)
        with open(HOLDINGS_DB_CACHE, "w") as f:
            json.dump({"database_id": did}, f)
    except Exception:
        pass
    logger.info("보유종목 DB 생성: %s", did)
    return did


def _sync_holdings_db(parent_page_id: str) -> None:
    """보유 종목 행 upsert (티커 매칭 update·신규 create·매도 archive). best-effort."""
    import requests
    rows = _load_holdings()
    if not rows:
        logger.info("보유종목 DB: 보유 데이터 없음 — 스킵")
        return
    did = _ensure_holdings_db(parent_page_id)
    if not did:
        return
    headers = _h()

    existing: dict[str, str] = {}
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"https://api.notion.com/v1/databases/{did}/query",
                          headers=headers, json=body, timeout=20)
        if not r.ok:
            logger.warning("보유종목 DB 조회 실패 %s", r.status_code)
            break
        j = r.json()
        for pg in j.get("results", []):
            t = pg["properties"].get("Ticker", {}).get("title", [])
            key = t[0].get("plain_text") if t else None
            if key:
                existing[key] = pg["id"]
        if not j.get("has_more"):
            break
        cursor = j.get("next_cursor")

    seen = set()
    for row in rows:
        seen.add(row["ticker"])
        props = _db_props(row)
        pid = existing.get(row["ticker"])
        if pid:
            requests.patch(f"https://api.notion.com/v1/pages/{pid}",
                           headers=headers, json={"properties": props}, timeout=15)
        else:
            requests.post("https://api.notion.com/v1/pages", headers=headers,
                          json={"parent": {"database_id": did}, "properties": props}, timeout=15)
    # 더 이상 보유하지 않는 종목 → 아카이브
    for tk, pid in existing.items():
        if tk not in seen:
            requests.patch(f"https://api.notion.com/v1/pages/{pid}",
                           headers=headers, json={"archived": True}, timeout=15)
    logger.info("보유종목 DB 동기화: %d행 (신규/갱신) · %d행 아카이브",
                len(rows), len(set(existing) - seen))


# ── QuickChart.io URL 생성 ─────────────────────────────────────────────────────

def _chart_url(config: dict, w: int = 700, h: int = 320) -> str:
    """QuickChart URL — 2000자 초과 시 /chart/create 단축 URL 사용."""
    import requests as _req
    encoded = urllib.parse.quote(json.dumps(config, ensure_ascii=False))
    direct  = f"https://quickchart.io/chart?c={encoded}&w={w}&h={h}&bkg=%23ffffff"
    if len(direct) <= 1900:
        return direct
    # 단축 URL
    try:
        r = _req.post(
            "https://quickchart.io/chart/create",
            json={"chart": config, "width": w, "height": h, "backgroundColor": "white"},
            timeout=10,
        )
        if r.ok and r.json().get("url"):
            return r.json()["url"]
    except Exception:
        pass
    # 데이터 포인트 줄여서 재시도
    return direct[:1900]


def _fear_greed_chart(fg_series) -> str:
    """Fear/Greed proxy 30일 추이 차트 URL."""
    s = fg_series.dropna().tail(30)
    labels = [d.strftime("%m/%d") for d in s.index]
    values = [round(float(v), 1) for v in s.values]
    # 색상: 0~25 빨강, 26~45 주황, 46~55 회색, 56~75 연두, 76~100 초록
    def _color(v):
        if v <= 25:   return "rgba(220,53,69,0.85)"
        if v <= 45:   return "rgba(255,140,0,0.85)"
        if v <= 55:   return "rgba(150,150,150,0.85)"
        if v <= 75:   return "rgba(100,200,100,0.85)"
        return "rgba(40,167,69,0.85)"
    colors = [_color(v) for v in values]
    config = {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{"label": "Fear/Greed Proxy", "data": values,
                          "backgroundColor": colors, "borderRadius": 3}]
        },
        "options": {
            "title": {"display": True, "text": "Fear/Greed Proxy (최근 30일)", "fontSize": 14},
            "legend": {"display": False},
            "scales": {
                "yAxes": [{"ticks": {"min": 0, "max": 100},
                           "gridLines": {"color": "rgba(0,0,0,0.05)"}}],
                "xAxes": [{"gridLines": {"display": False}}]
            },
            "plugins": {
                "annotation": {
                    "annotations": [
                        {"type": "line", "mode": "horizontal", "scaleID": "y-axis-0",
                         "value": 75, "borderColor": "rgba(40,167,69,0.4)", "borderWidth": 1,
                         "label": {"enabled": True, "content": "탐욕", "position": "right", "fontSize": 10}},
                        {"type": "line", "mode": "horizontal", "scaleID": "y-axis-0",
                         "value": 25, "borderColor": "rgba(220,53,69,0.4)", "borderWidth": 1,
                         "label": {"enabled": True, "content": "공포", "position": "right", "fontSize": 10}},
                    ]
                }
            }
        }
    }
    return _chart_url(config, w=700, h=280)


def _ranking_chart(ranking) -> str:
    """NASDAQ100 랭킹 수평 바 차트 URL."""
    labels = list(reversed([str(row["ticker"]) for _, row in ranking.iterrows()]))
    scores = list(reversed([round(float(row["score"]) * 100, 2) for _, row in ranking.iterrows()]))
    config = {
        "type": "horizontalBar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": "QQQ 초과수익 예측 (%)",
                "data": scores,
                "backgroundColor": [
                    "rgba(54,162,235,0.85)" if s > 0 else "rgba(220,53,69,0.7)"
                    for s in scores
                ],
                "borderRadius": 4,
            }]
        },
        "options": {
            "title": {"display": True, "text": "NASDAQ100 LightGBM 랭킹 (상위 15)", "fontSize": 14},
            "legend": {"display": False},
            "scales": {
                "xAxes": [{"gridLines": {"color": "rgba(0,0,0,0.05)"}}],
                "yAxes": [{"gridLines": {"display": False}}]
            }
        }
    }
    return _chart_url(config, w=700, h=380)


def _equity_chart(equity_df) -> str:
    """ML 전략 이퀴티 곡선 차트 URL."""
    if equity_df.empty:
        return ""
    step = max(1, len(equity_df) // 40)
    df   = equity_df.iloc[::step]
    labels = [d.strftime("%y/%m") for d in df.index]

    datasets = []
    colors = {"ML_model": "rgba(54,162,235,1)", "overlay": "rgba(255,159,64,0.9)",
              "QQQ": "rgba(40,167,69,1)", "SPY": "rgba(150,150,150,0.7)"}
    names  = {"ML_model": "ML 전략", "overlay": "리스크오버레이",
              "QQQ": "QQQ", "SPY": "SPY"}
    for col in ["ML_model", "overlay", "QQQ", "SPY"]:
        if col not in df.columns:
            continue
        datasets.append({
            "label": names.get(col, col),
            "data": [round(float(v), 2) for v in df[col].values],
            "borderColor": colors.get(col, "gray"),
            "borderWidth": 2 if col in ("ML_model", "QQQ") else 1,
            "fill": False,
            "pointRadius": 0,
        })

    config = {
        "type": "line",
        "data": {"labels": labels, "datasets": datasets},
        "options": {
            "title": {"display": True, "text": "이퀴티 곡선 비교 (기준=100)", "fontSize": 14},
            "scales": {
                "xAxes": [{"gridLines": {"color": "rgba(0,0,0,0.04)"}}],
                "yAxes": [{"gridLines": {"color": "rgba(0,0,0,0.04)"}}]
            }
        }
    }
    return _chart_url(config, w=700, h=300)


def _qqq_momentum_chart(qqq_close) -> str:
    """QQQ 60일 가격 + MA200 차트 URL."""
    if qqq_close is None or len(qqq_close) < 60:
        return ""
    tail = qqq_close.tail(60)
    ma60 = qqq_close.rolling(60).mean().tail(60)
    labels = [d.strftime("%m/%d") for d in tail.index]
    config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {"label": "QQQ", "data": [round(float(v), 2) for v in tail.values],
                 "borderColor": "rgba(54,162,235,1)", "borderWidth": 2, "fill": False, "pointRadius": 0},
                {"label": "MA60", "data": [round(float(v), 2) if not __import__("math").isnan(float(v)) else None for v in ma60.values],
                 "borderColor": "rgba(255,99,132,0.8)", "borderWidth": 1,
                 "fill": False, "pointRadius": 0, "borderDash": [5, 5]},
            ]
        },
        "options": {
            "title": {"display": True, "text": "QQQ 가격 + MA60 (최근 60일)", "fontSize": 14},
            "scales": {"xAxes": [{"gridLines": {"display": False}}],
                       "yAxes": [{"gridLines": {"color": "rgba(0,0,0,0.05)"}}]}
        }
    }
    return _chart_url(config, w=700, h=280)


# ── 데이터 수집 ────────────────────────────────────────────────────────────────

def _collect_market() -> dict:
    from barbell_strategy import (
        fetch_qqq_data, fetch_rsi, fetch_vix, fetch_ma200,
        classify_market, fetch_fear_greed, calculate_dca, fetch_portfolio_value,
    )
    qqq_d  = fetch_qqq_data()
    rsi_v  = fetch_rsi("QQQ")
    vix_v  = fetch_vix()
    ma_d   = fetch_ma200("QQQ")
    mt, pk = classify_market(qqq_d, rsi_v, vix_v)
    fg     = fetch_fear_greed()
    dca    = calculate_dca(mt, pk, drawdown_pct=qqq_d.get("drawdown_pct"))
    port   = fetch_portfolio_value()
    return {"qqq": qqq_d, "rsi": rsi_v, "vix": vix_v, "ma": ma_d,
            "mt": mt, "pk": pk, "fg": fg, "dca": dca, "port": port}


def _collect_ml():
    import warnings; warnings.filterwarnings("ignore")
    from ml.data_pipeline import build_real_sweetspot_data, build_fear_greed_proxy, fetch_prices
    from ml.sweet_spot import optimize_sweet_spot
    from ml.reporting import _ml_adoption_verdict
    from ml.ranker import rank_today, load_ranker

    data   = build_real_sweetspot_data("QQQ", days=756)
    result = optimize_sweet_spot(data)
    verdict, reasons = _ml_adoption_verdict(result.ml_result, result.qqq_result)

    ranking    = rank_today(mode="nasdaq100", top_n=15)
    ranker_res = load_ranker()

    fg    = build_fear_greed_proxy(days=60)
    prices = fetch_prices(["QQQ"], days=120)
    qqq_close = prices.get("QQQ", __import__("pandas").DataFrame()).get("Close")

    return {"sweet": result, "verdict": verdict, "reasons": reasons,
            "ranking": ranking, "ranker": ranker_res,
            "fg": fg, "qqq_close": qqq_close}


def _load_report_summary() -> tuple[str, str]:
    """투자 리포트 요약 로드 → (본문, 리포트 날짜 'YYYY-MM-DD').

    오늘(KST) 리포트가 없으면 가장 최근 리포트로 폴백한다 — notion 크론이
    리포트 생성 직전에 돌거나, 주말·생성 실패로 당일 파일이 없어도
    섹션이 영구히 '미생성'으로 비지 않도록(stale 표시는 호출부에서 처리).
    파일이 하나도 없으면 ('', '').
    """
    import glob
    import re
    from pathlib import Path
    base  = Path.home() / "reports"
    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 오늘 리포트 우선, 없으면 전체에서 최신 (파일명 날짜 = 사전식 정렬 = 시간순)
    todays     = sorted(glob.glob(str(base / f"investment-summary-{today}*.txt")))
    candidates = todays or sorted(glob.glob(str(base / "investment-summary-*.txt")))
    if not candidates:
        return "", ""

    path     = candidates[-1]
    m        = re.search(r"investment-summary-(\d{4}-\d{2}-\d{2})", path)
    date_str = m.group(1) if m else ""
    try:
        return Path(path).read_text(encoding="utf-8")[:2000], date_str
    except Exception:
        return "", date_str


def _recent_report_summaries(days: int = 5) -> list[tuple[str, str]]:
    """최근 `days` 일 이내의 실제 리포트 요약 파일들 → [(date, text)] (오래된→최신).

    notion 크론은 평일만 돌지만 리포트는 매일(주말 포함) 생성되므로, 매 실행마다
    최근 며칠치를 멱등 재아카이빙해 주말·누락 회차 갭을 메운다. **실제 존재하는
    파일만** 대상 — 폴백/stale 재아카이빙 없음(오래된 리포트를 매일 다시 박지 않음).
    """
    import glob
    import re
    from pathlib import Path
    base   = Path.home() / "reports"
    today  = datetime.now(KST).date()
    cutoff = today - timedelta(days=days)
    out: list[tuple[str, str]] = []
    for path in sorted(glob.glob(str(base / "investment-summary-*.txt"))):
        m = re.search(r"investment-summary-(\d{4}-\d{2}-\d{2})", path)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff or d > today:
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")[:4000]
        except Exception:
            continue
        if text.strip():
            out.append((m.group(1), text))
    return out


# ── 블록 빌드 ──────────────────────────────────────────────────────────────────

def build_blocks() -> list[dict]:
    import warnings; warnings.filterwarnings("ignore")
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    blocks: list[dict] = []

    # ── 헤더 ─────────────────────────────────────────────────────────────────
    blocks += [
        _callout(f"📡 stock-report 자동 동기화  •  {now_kst}  •  평일 08:30 KST",
                 "📊", "blue_background"),
        _divider(),
    ]

    # ── 히어로 KPI 밴드 + 시장 체온계 ──────────────────────────────────────────
    try:
        mkt = _collect_market()
        qqq  = mkt["qqq"]
        fg   = mkt["fg"]
        port = mkt["port"]
        dca  = mkt["dca"]
        mt, pk = mkt["mt"], mkt["pk"]

        fg_sc    = fg.get("score", 50)
        fg_proxy = fg.get("proxy_score", -1)
        proxy_s  = f"{fg_proxy:.0f}" if fg_proxy >= 0 else "n/a"
        cnn_ok   = fg.get("cnn_ok", True)
        dd       = qqq.get("drawdown_pct", 0) or 0
        ret      = port.get("return_pct", 0) or 0
        total    = port.get("total_usd", 0)
        phase_str = _phase_label(mt, pk)

        fg_emoji = "💀" if fg_sc <= 25 else "😨" if fg_sc <= 45 else "😐" if fg_sc <= 55 else "😄" if fg_sc <= 75 else "🤑"
        fg_label = ("극단공포" if fg_sc <= 25 else "공포" if fg_sc <= 45 else
                    "중립" if fg_sc <= 55 else "탐욕" if fg_sc <= 75 else "극단탐욕")
        vix_v = mkt["vix"]
        vix_lbl = "💥극공포" if vix_v > 40 else "🚨공포" if vix_v > 30 else "😴과낙관" if vix_v < 15 else "✅정상"

        # 히어로 KPI 밴드 (최상단 — 한눈에 들어오는 4대 지표)
        blocks.append(_hero_band(phase_str, mt, qqq, dd, ret, total, dca))
        blocks.append(_divider())

        # 시장 체온계 상세 테이블
        blocks.append(_h2("🌡️ 시장 체온계"))
        blocks.append(_table([
            ["지표", "값", "상태"],
            ["RSI (QQQ)", f"{mkt['rsi']:.1f}", "🔥과매도" if mkt['rsi'] < 30 else "⚠️약세" if mkt['rsi'] < 40 else "🌡과매수" if mkt['rsi'] > 70 else "✅중립"],
            ["VIX", f"{vix_v:.1f}", vix_lbl],
            [f"Fear/Greed CNN{'(미작동)' if not cnn_ok else ''}", f"{fg_sc:.1f}", f"{fg_emoji} {fg_label}"],
            ["Fear/Greed Proxy", proxy_s, "🟢탐욕" if float(proxy_s) > 55 else "🔴공포" if float(proxy_s) < 45 else "⚪중립" if proxy_s != "n/a" else "—"],
            ["200MA 위치", f"{'위 ▲' if mkt['ma'].get('above_ma200') else '아래 ▽'}  {mkt['ma'].get('gap_pct',0):+.1f}%", ""],
        ]))

        logger.info("시장 섹션 완료")
    except Exception as e:
        logger.warning("시장 데이터 실패: %s", e)
        blocks.append(_callout(f"⚠️ 시장 데이터 수집 실패: {e}", "❗", "red_background"))

    blocks.append(_divider())

    # ── 차트 & 랭킹 ──────────────────────────────────────────────────────────
    blocks.append(_h2("📈 시황 차트"))
    ml_data = None   # 차트 섹션 실패 시에도 랭킹/ML 섹션이 재시도할 수 있도록 선초기화
    try:
        ml_data = _collect_ml()
        qqq_close = ml_data["qqq_close"]
        fg_proxy  = ml_data["fg"]

        # 로컬 포트폴리오 대시보드 PNG (report_charts 생성물 — 있으면 우선 임베드)
        png = _latest_chart_png()
        fid = _upload_file_to_notion(png) if png else None
        if fid:
            blocks.append(_image_upload(fid, "포트폴리오 대시보드 — 등락·벤치마크·RSI·매집강도"))
        elif qqq_close is not None:   # 폴백: QuickChart QQQ
            url = _qqq_momentum_chart(qqq_close)
            if url:
                blocks.append(_image(url, "QQQ 최근 60일 가격 + MA60"))

        # Fear/Greed Proxy 추이
        if not fg_proxy.empty:
            url = _fear_greed_chart(fg_proxy)
            blocks.append(_image(url, "Fear/Greed Proxy 최근 30일 (0=극도공포, 100=극도탐욕)"))

        logger.info("시황 차트 완료")
    except Exception as e:
        logger.warning("시황 차트 실패: %s", e)
        blocks.append(_callout(f"⚠️ 차트 생성 실패: {e}", "❗"))

    blocks.append(_divider())

    # ── NASDAQ100 랭킹 ────────────────────────────────────────────────────────
    blocks.append(_h2("🏆 NASDAQ100 일일 랭킹"))
    try:
        if ml_data is None:
            ml_data = _collect_ml()
        ranking    = ml_data["ranking"]
        ranker_res = ml_data["ranker"]

        if not ranking.empty and ranker_res:
            blocks.append(_quote(
                f"OOS IC: {ranker_res.oos_ic:+.3f}  |  ICIR: {ranker_res.oos_icir:.2f}  "
                f"|  상위10% 초과수익: {ranker_res.oos_top_decile_ret*100:+.1f}%  |  학습기준: {ranker_res.train_end_date}"
            ))

            # 랭킹 차트
            url = _ranking_chart(ranking)
            if url:
                blocks.append(_image(url, "LightGBM QQQ 초과수익 예측 기반 랭킹"))

            # 랭킹 테이블 (toggle 안에)
            rows = [["순위", "종목", "점수", "초과모멘텀60d", "베타", "RSI", "변동성"]]
            for _, row in ranking.iterrows():
                rows.append([
                    str(int(row["rank"])),
                    ticker_names.label(str(row["ticker"])),
                    f"{float(row['score'])*100:+.2f}%",
                    f"{float(row.get('excess_mom_60d',0))*100:+.1f}%" if 'excess_mom_60d' in row else "—",
                    f"{float(row.get('beta_60d',0)):.2f}" if 'beta_60d' in row else "—",
                    f"{float(row.get('rsi_14',0)):.1f}" if 'rsi_14' in row else "—",
                    f"{float(row.get('vol_20d',0))*100:.1f}%" if 'vol_20d' in row else "—",
                ])
            blocks.append(_toggle("📋 상세 랭킹 테이블 (클릭 펼치기)", [_table(rows)]))
            blocks.append(_callout("⚠️ survivorship bias 있음 — 현재 NASDAQ100 구성종목 기준", "⚠️", "yellow_background"))
        else:
            blocks.append(_callout("랭킹 데이터 없음", "⚠️"))
        logger.info("랭킹 섹션 완료")
    except Exception as e:
        logger.warning("랭킹 실패: %s", e)
        blocks.append(_callout(f"⚠️ 랭킹 데이터 실패: {e}", "❗"))

    blocks.append(_divider())

    # ── ML 전략 성과 ──────────────────────────────────────────────────────────
    blocks.append(_h2("🧠 ML 전략 성과 (QQQ 3년 실데이터)"))
    try:
        sweet = ml_data["sweet"]
        verdict = ml_data["verdict"]
        reasons = ml_data["reasons"]

        ml   = sweet.ml_result
        qqq  = sweet.qqq_result
        ov   = sweet.overlay_result
        wf   = sweet.wf_summary

        verdict_emoji = "✅" if "채택" in verdict and "비채택" not in verdict and "조건부" not in verdict else "⚠️" if "조건부" in verdict else "❌"
        verdict_color = "green_background" if verdict_emoji == "✅" else "yellow_background" if verdict_emoji == "⚠️" else "red_background"
        blocks.append(_callout(verdict, verdict_emoji, verdict_color))

        for r in reasons:
            blocks.append(_bullet(r))

        blocks.append(_table([
            ["전략", "CAGR", "Sharpe", "MDD", "비고"],
            ["ML (nested OOS)", f"{(ml.cagr or 0):.1%}", f"{(ml.sharpe or 0):.2f}", f"{ml.max_drawdown:.1%}", f"{ml.n_days}일"],
            ["리스크오버레이", f"{(ov.cagr or 0):.1%}", f"{(ov.sharpe or 0):.2f}", f"{ov.max_drawdown:.1%}", "200MA+ML크기"],
            ["QQQ 매수보유", f"{(qqq.cagr or 0):.1%}", f"{(qqq.sharpe or 0):.2f}", f"{qqq.max_drawdown:.1%}", "벤치마크"],
        ]))

        # WF 요약
        blocks.append(_quote(
            f"Walk-forward {wf.get('n_folds','?')}폴드  |  "
            f"평균 CAGR {(wf.get('mean_cagr') or 0):.1%}  |  "
            f"평균 Sharpe {(wf.get('mean_sharpe') or 0):.2f} ± {(wf.get('std_sharpe') or 0):.2f}"
        ))

        # 이퀴티 커브 차트
        url = _equity_chart(sweet.equity)
        if url:
            blocks.append(_image(url, "ML 전략 vs QQQ 이퀴티 곡선 비교"))

        logger.info("ML 성과 섹션 완료")
    except Exception as e:
        logger.warning("ML 성과 실패: %s", e)
        blocks.append(_callout(f"⚠️ ML 데이터 실패: {e}", "❗"))

    blocks.append(_divider())

    # ── 오늘의 투자 리포트 ─────────────────────────────────────────────────────
    blocks.append(_h2("📰 오늘의 투자 리포트"))
    try:
        summary, report_date = _load_report_summary()
        today_kst = datetime.now(KST).strftime("%Y-%m-%d")
        if summary:
            lines = summary.strip().split("\n")
            # 당일 리포트가 아직이면 최근 리포트임을 명시 (오해 방지)
            if report_date and report_date != today_kst:
                blocks.append(_callout(
                    f"당일({today_kst}) 리포트 생성 전 — 최근 {report_date} 리포트 표시",
                    "🕒", "yellow_background"))
            # 평문 40줄 덤프 대신 섹션 헤더·불릿·문단으로 구조화 (인라인 노출)
            blocks += _report_blocks(lines, limit=45)
            logger.info("리포트 요약 추가 완료 (%d줄, %s)", len(lines), report_date or "?")
        else:
            blocks.append(_callout(
                f"리포트 없음 ({today_kst}) — 크론 23:00 UTC 이후 자동 생성됩니다",
                "📭", "gray_background"))
    except Exception as e:
        logger.warning("리포트 로드 실패: %s", e)

    blocks.append(_divider())
    blocks.append(_para(f"🤖 stock-report 자동 생성  •  {now_kst}", color="gray"))

    return blocks


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    logger.info("=== notion_sync 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))

    if not NOTION_TOKEN:
        logger.error("NOTION_TOKEN 환경변수 미설정")
        return 1

    blocks = build_blocks()

    # ── 일일 리포트 아카이브 (월/주 계층 누적) ──────────────────────────────────
    # 대시보드와 완전 독립: 여기서 예외가 나도 대시보드 동기화는 계속한다(best-effort).
    try:
        import notion_archive
        recent = _recent_report_summaries(days=5)   # 주말·누락 회차 갭까지 멱등 보강
        archive_root_id = None
        for rdate, rtext in recent:
            archive_root_id = notion_archive.archive_report(rdate, rtext)
        if archive_root_id is None:
            # 최근 리포트가 없어도 루트는 보장(대시보드 링크용)
            archive_root_id = notion_archive.archive_report("", "")
        if recent:
            logger.info("아카이브 처리: 최근 %d일치 리포트", len(recent))
        if archive_root_id:
            # footer(마지막 divider+회색 para) 바로 위에 아카이브 링크 삽입
            blocks[-2:-2] = [
                _divider(),
                _para("📚 과거 리포트 아카이브 (월/주별)", bold=True),
                notion_archive.link_to_page(archive_root_id),
            ]
    except Exception as e:
        logger.warning("리포트 아카이브 실패(대시보드는 계속): %s", e)

    ok = update_page(DASHBOARD_PAGE_ID, blocks)

    # ── 보유 종목 DB upsert (N3) ───────────────────────────────────────────────
    # 대시보드와 독립(best-effort): 여기서 예외가 나도 동기화 결과엔 영향 없음.
    try:
        _sync_holdings_db(DASHBOARD_PAGE_ID)
    except Exception as e:
        logger.warning("보유종목 DB 동기화 실패(대시보드는 계속): %s", e)

    if ok:
        logger.info("노션 동기화 완료 ✅ (%d블록)", len(blocks))
    else:
        logger.error("노션 동기화 실패 ❌")
        # 실패 알림 — notify 단일 진실원 위임 (자체 응답검증·토큰 마스킹·실패 시 로깅만)
        import notify
        notify.send_telegram("⚠️ Notion 동기화 실패")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
