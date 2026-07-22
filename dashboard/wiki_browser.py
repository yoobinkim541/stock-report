        role = str(row.get("role") or "").strip().lower()
        text = str(row.get("content") or "").strip()
        if not text:
            continue
        if role == "user":
            pending = row
            continue
        if role == "assistant" and pending:
            return {
                "id": f"{id(pending)}-{id(row)}",
                "question": str(pending.get("content") or "").strip(),
                "answer": text,
            }
    return None


def _extract_selected_page_id(event: Any) -> str:
    if not event:
        return ""
    selection = None
    if isinstance(event, dict):
        selection = event.get("selection") or event.get("points") or event
    else:
        selection = getattr(event, "selection", None) or getattr(event, "points", None) or event
    points = []
    if isinstance(selection, dict):
        points = selection.get("points") or []
    elif isinstance(selection, Iterable):
        points = list(selection)
    for point in points:
        try:
            customdata = point.get("customdata") if isinstance(point, dict) else getattr(point, "customdata", None)
            if customdata:
                return str(customdata[0] if isinstance(customdata, (list, tuple)) else customdata)
        except Exception:
            continue
    return ""


def _wiki_stats() -> dict[str, Any]:
    from agent_console import wiki

    stats_fn = getattr(wiki, "stats", None)
    if callable(stats_fn):
        try:
            return stats_fn()
        except Exception:
            pass
    pages = []
    try:
        pages = wiki.list_pages(query="", surface="all", status="all", limit=400)
    except Exception:
        pages = []
    counter = Counter()
    kind_counter = Counter()
    latest = None
    for page in pages:
        counter[str(page.get("status") or "draft")] += 1
        kind_counter[str(page.get("kind") or "note")] += 1
        if latest is None or str(page.get("updated_at") or page.get("created_at") or "") > str(latest.get("updated_at") or latest.get("created_at") or ""):
            latest = page
    return {
        "total": len(pages),
        "status_counts": dict(counter),
        "kind_counts": dict(kind_counter),
        "latest": latest or {},
    }


def render_wiki_tab(surface: str, pack: dict[str, Any] | None = None) -> None:
    import pandas as pd
    import streamlit as st

    from agent_console import wiki
    from dashboard import wiki_mesh

    st.markdown("##### AI 위키")
    st.caption("대화와 메모를 카드로 승격해 챗봇이 다시 읽는 지식층입니다.")

    stats = _wiki_stats()
    cols = st.columns(4)
    cols[0].metric("페이지", f"{stats.get('total', 0)}")
    cols[1].metric("초안", f"{stats.get('status_counts', {}).get('draft', 0)}")
    cols[2].metric("검토", f"{stats.get('status_counts', {}).get('reviewed', 0)}")
    latest = stats.get("latest") or {}
    cols[3].metric("최근", latest.get("title", "—")[:20] if latest else "—")

    pages_all = wiki.list_pages(query="", surface="all", status="all", limit=400)
    if not pages_all:
        st.info("아직 위키 카드가 없습니다. 아래에서 현재 대화를 위키로 승격해 보세요.")
        return
