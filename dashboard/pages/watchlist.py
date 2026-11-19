"""dashboard/pages/watchlist.py — 관심종목. 추가·삭제는 이 페이지와 봇 /watch 가 같은 lib.watchlist 저장소. 폴더 이동도 이 페이지.

토스증권식 리스트(아바타·종목명 / 현재가·등락)와 항상 펼쳐진 관리 카드·투자자 동향 섹션.
종목 행은 `?tk=` 앵커라 누르면 종목 분석으로 이동한다(app.py 가 소비).
"""
from __future__ import annotations

import os
import re
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_names
from dashboard import cached, data, theme, ui_actions
from providers import thirteenf

# 신고가 끊긴 기관은 screen_position_changes 가 스스로 뺀다
_SCREEN_13F_KEYS = tuple(thirteenf.FILERS)

_ACTION_LABELS = {"new": "신규 편입", "added": "추가 매수", "trimmed": "일부 매도",
                  "exited": "전량 매도", "held": "보유"}
_ACTION_DIR = {"new": 1, "added": 1, "trimmed": -1, "exited": -1}
_US_TICKER = re.compile(r"^[A-Z][A-Z.\-]{0,6}$")

_FRESHNESS_LABELS = {
    "fresh": "최신",
    "stale": "신고 끊김",
    "proxy": "프록시",
}

_CATEGORY_LABELS = {
    "holding_company": "지주회사",
    "asset_manager": "운용사",
    "hedge_fund": "헤지펀드",
    "family_office": "패밀리오피스",
    "venture_fund": "벤처펀드",
    "pension": "연기금",
    "foundation": "재단",
    "seed": "시드",
}

_FLAG_LABELS = {
    "available": "공개",
    "proxy": "프록시",
    "unavailable": "비공개",
}


def _fmt_pct(value, flag: str) -> str:
    if value is None:
        return f"— ({_FLAG_LABELS.get(flag, '비공개')})"
    try:
        return f"{float(value) * 100:.1f}% ({_FLAG_LABELS.get(flag, flag)})"
    except Exception:
        return f"{value} ({_FLAG_LABELS.get(flag, flag)})"


def _short(row: dict) -> str:
    return row.get("short_name") or row.get("display_name") or row.get("key") or "?"


def _move_delta(m: dict) -> str:
    action = m.get("action")
    if action == "new":
        return "신규"
    if action == "exited":
        return "전량 매도"
    chg = m.get("shares_change_pct")
    return f"주식 수 {chg * 100:+.0f}%" if chg is not None else ""


def _move_rows(moves: list[dict], limit: int = 6) -> list[dict]:
    out = []
    for m in moves[:limit]:
        exited = m.get("action") == "exited"
        w = m.get("prior_weight_pct") if exited else m.get("weight_pct")
        out.append({
            "ticker": m.get("ticker") or "", "name": _stock_name(m.get("ticker"), m.get("issuer") or m.get("name")),
            "value": f"{'직전 ' if exited else ''}{float(w or 0):.1f}%",
            "delta": _move_delta(m), "direction": _ACTION_DIR.get(m.get("action"), 0),
        })
    return out


def _investor_detail(row: dict) -> None:
    """한 투자자의 분기 포트폴리오 — 요약 지표, 보유 상위, 옵션, 분기 매매 네 갈래."""
    stale = row.get("freshness") == "stale"
    when = " · ".join(p for p in (
        f"{row['report_date']} 분기말" if row.get("report_date") else "",
        f"{row['filing_date']} 공시" if row.get("filing_date") else "") if p)
    theme.render(theme.section_head_html(_short(row), " · ".join(p for p in (
        row.get("display_name") or "", _CATEGORY_LABELS.get(row.get("category"), ""), when) if p)))
    if row.get("source_kind") != "13f":
        st.info("13F 단일 신고 주체가 없어 공개 자료로만 추적합니다. "
                + " ".join(str(n) for n in (row.get("notes") or [])[:1]))
        return
    if stale:
        st.warning(f"{row.get('filing_date') or '—'} 이후 13F 신고가 없습니다. 과거 포트폴리오라서 "
                   "공통 움직임·내 종목 보유자 집계에서는 뺐습니다.")

    put_v, call_v = float(row.get("put_value_usd") or 0), float(row.get("call_value_usd") or 0)
    opt = row.get("options_exposure")
    conc = row.get("portfolio_concentration")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("롱 보유액", _usd_compact(row.get("total_value_usd")),
              help="13F 현물 주식·채권 롱 합계. 옵션·공매도·현금은 빠집니다.")
    c2.metric("보유 종목", f"{int(row.get('holdings_count') or 0)}개")
    c3.metric("상위 5 집중도", f"{conc * 100:.1f}%" if conc is not None else "—")
    c4.metric("옵션 비중", f"{opt * 100:.1f}%" if opt is not None else "—",
              help=f"풋 {_usd_compact(put_v)} · 콜 {_usd_compact(call_v)} — 기초자산 명목, 전체 신고액 대비")
    if row.get("return_proxy") is not None:
        st.caption(f"연속 보유 종목 가격 변화(대체 수익률) {row['return_proxy'] * 100:+.1f}% · "
                   "13F 신고가로 추정, 매매·현금 제외")

    top = list(row.get("top_holdings") or [])
    top_rows = [{
        "ticker": h.get("ticker") or "", "name": _stock_name(h.get("ticker"), h.get("issuer")),
        "tags": [_ACTION_LABELS[h["action"]]] if h.get("action") in _ACTION_DIR else [],
        "value": f"{float(h.get('weight_pct') or 0):.1f}%",
        "delta": _usd_compact(h.get("value_usd")),
    } for h in top]
    options = list(row.get("options_top") or [])
    left, right = st.columns(2)
    with left:
        theme.render(theme.stock_list_html(top_rows, title="보유 상위 10", meta="롱 대비 비중 · 평가액",
                                           empty="보유 종목 없음"))
    with right:
        theme.render(theme.stock_list_html([{
            "ticker": o.get("ticker") or "", "name": _stock_name(o.get("ticker"), o.get("issuer")),
            "tags": ["풋" if o.get("put_call") == "Put" else "콜"],
            "value": _usd_compact(o.get("value_usd")), "delta": f"{float(o.get('weight_pct') or 0):.1f}%",
            "direction": -1 if o.get("put_call") == "Put" else 1,
        } for o in options], title="옵션 포지션", meta="기초자산 명목 · 전체 신고액 대비",
            empty="신고한 풋·콜 없음"))

    changes = row.get("changes")
    if not changes:
        st.caption("직전 분기 13F 가 없어 분기 매매를 계산하지 못했습니다.")
        return
    counts = changes.get("counts") or {}
    theme.render(theme.section_label_html(
        f"{row.get('prior_report_date') or '직전 분기'} → {row.get('report_date') or '최신'} 매매"))
    for i, action in enumerate(("new", "added", "trimmed", "exited")):
        if i % 2 == 0:   # 행 단위 2열 — 모바일에서 신규·추가·일부·전량 순으로 쌓이게
            grid = st.columns(2)
        with grid[i % 2]:
            theme.render(theme.stock_list_html(
                _move_rows(changes.get(action) or []), title=_ACTION_LABELS[action],
                meta=f"{counts.get(action, 0)}종목", empty="없음"))
    st.caption("주식 수 ±5% 이상 변화만 매매로 봅니다(가격 변동으로 비중만 바뀐 건 제외).")


def _render_comparison_table(comparison: dict):
    rows = list(comparison.get("rows") or [])
    if not rows:
        st.info("비교 가능한 기관 스냅샷이 아직 충분하지 않습니다.")
        return
    table = pd.DataFrame([{
        "투자자": _short(row),
        "기관": row.get("display_name"),
        "카테고리": _CATEGORY_LABELS.get(row.get("category"), row.get("category")),
        "신선도": _FRESHNESS_LABELS.get(row.get("freshness"), row.get("freshness")),
        "공시일": row.get("filing_date") or "—",
        "롱 보유액": _usd_compact(row.get("total_value_usd")) if row.get("total_value_usd") else "—",
        "보유 종목 수": row.get("holdings_count"),
        "집중도": _fmt_pct(row.get("portfolio_concentration"), row.get("portfolio_concentration_flag")),
        "옵션 노출": _fmt_pct(row.get("options_exposure"), row.get("options_exposure_flag")),
        "대체 수익률": _fmt_pct(row.get("return_proxy"), row.get("return_proxy_flag")),
    } for row in rows])
    st.dataframe(table, hide_index=True, width="stretch")


def _render_common_moves(analysis: dict):
    shared_moves = list(analysis.get("shared_moves") or [])
    divergences = list(analysis.get("divergences") or [])
    confidence = float(analysis.get("confidence") or 0.0)
    heading = "LLM 공통 패턴 요약" if analysis.get("mode") == "llm" else "공통 패턴 요약"
    st.markdown(f"**{heading}**")
    if analysis.get("summary"):
        st.markdown(analysis["summary"])
    st.caption(f"신뢰도 {confidence:.2f}")
    if shared_moves:
        st.markdown("\n".join(["**공통 움직임**", *[f"- {item}" for item in shared_moves]]))
    if divergences:
        st.markdown("\n".join(["**차이점**", *[f"- {item}" for item in divergences]]))


def _fmt_price(ticker: str, q: dict | None) -> str:
    if not q or q.get("price") is None:
        return "—"
    price = float(q["price"])
    if q.get("currency") == "KRW":
        return f"₩{price:,.0f}"
    return f"${price:,.2f}"


def _fmt_chg(q: dict | None) -> str:
    if not q or q.get("chg_pct") is None:
        return "—"
    return f"{q['chg_pct']:+.2f}%"


def _fmt_basis(q: dict | None) -> str:
    """시세 기준 — 일봉 날짜·출처, 실패면 사유."""
    if not q:
        return "⚠️ 시세 없음"
    if q.get("error"):
        return f"⚠️ {q['error']}"
    parts = [f"{q['as_of']} 일봉" if q.get("as_of") else "", str(q.get("source") or "")]
    return " · ".join(p for p in parts if p) or "—"


def _sign(value) -> int:
    if value is None:
        return 0
    return 1 if value > 0 else -1 if value < 0 else 0


_SORTS = ("추가순", "상승률", "하락률")


def _sort_rows(rows: list[dict], quotes: dict, how: str | None) -> list[dict]:
    """추가순은 저장소 순서(최근 추가 먼저). 등락률 정렬에서 시세 없는 종목은 맨 뒤."""
    if how not in ("상승률", "하락률"):
        return rows

    def _chg(r):
        return (quotes.get(r["ticker"]) or {}).get("chg_pct")
    known = [r for r in rows if _chg(r) is not None]
    unknown = [r for r in rows if _chg(r) is None]
    return sorted(known, key=_chg, reverse=how == "상승률") + unknown


def _watch_list_rows(rows: list[dict], quotes: dict) -> tuple[list[dict], dict]:
    """리스트 행과 요약(상승·하락·시세 미확인 수, 대표 일봉 날짜)."""
    dates = Counter(q.get("as_of") for q in quotes.values() if q and q.get("as_of"))
    common = dates.most_common(1)[0][0] if dates else None
    out, up, down, failed = [], 0, 0, 0
    for r in rows:
        q = quotes.get(r["ticker"])
        name = r.get("name") or r["ticker"]
        tags = [r.get("folder") or "미분류"]
        if q and q.get("price") is not None:
            direction = _sign(q.get("chg_pct"))
            up += direction > 0
            down += direction < 0
            if q.get("as_of") and q["as_of"] != common:
                tags.append(f"{q['as_of']} 기준")
            value, delta = _fmt_price(r["ticker"], q), _fmt_chg(q)
        else:
            failed += 1
            direction, value, delta = 0, "—", "시세 없음"
        out.append({"ticker": r["ticker"], "name": name, "tags": tags, "memo": r.get("reason") or "",
                    "value": value, "delta": delta, "direction": direction,
                    "title": f"{name} · {_fmt_basis(q)} · 누르면 종목 분석"})
    return out, {"up": up, "down": down, "failed": failed, "as_of": common}


def _summary_meta(summary: dict) -> str:
    parts = [f"상승 {summary['up']} · 하락 {summary['down']}"]
    if summary["failed"]:
        parts.append(f"⚠️ 시세 미확인 {summary['failed']}개")
    if summary["as_of"]:
        parts.append(f"{summary['as_of']} 일봉")
    return " · ".join(parts)


def _ticker_label(rows: list[dict]):
    names = {r["ticker"]: r.get("name") or r["ticker"] for r in rows}
    return lambda t: f"{names.get(t, t)} ({t})" if names.get(t, t) != t else t


def _add_card() -> None:
    with st.container(border=True, key="tn-card-wl-add"):
        st.markdown("**종목 추가**")
        ticker = st.text_input("티커", key="_watchlist_add_ticker", placeholder="PLTR · 005930")
        note = st.text_input("메모 (선택)", key="_watchlist_add_note", placeholder="실적 기대")
        if st.button("추가", key="_watchlist_add_btn", type="primary", width="stretch"):
            if not ticker.strip():
                st.warning("티커를 입력하세요.")
            else:
                ok, msg = data.add_watchlist_ticker(ticker, note=note)
                if ok:
                    st.toast(f"⭐ 관심종목 추가: {msg}")
                    st.rerun()
                else:
                    st.error(msg)


def _move_card(rows: list[dict], folders: list[str]) -> None:
    with st.container(border=True, key="tn-card-wl-move"):
        st.markdown("**폴더 이동**")
        pick_ticker = st.selectbox("종목", options=[r["ticker"] for r in rows],
                                   format_func=_ticker_label(rows), key="_watchlist_move_ticker")
        target = st.selectbox("이동할 폴더", options=folders + ["+ 새 폴더"],
                              key="_watchlist_move_folder_pick")
        if target == "+ 새 폴더":
            target = st.text_input("새 폴더 이름", key="_watchlist_move_folder_new").strip()
        if st.button("이동", key="_watchlist_move_btn", width="stretch") and target:
            if data.move_watchlist_folder(pick_ticker, target):
                st.toast(f"{pick_ticker} → {target} 이동 완료")
                st.rerun()
            else:
                st.error(f"{pick_ticker} → {target} 이동 실패. 관심종목 목록에 없는 종목입니다.")


def _remove_card(rows: list[dict]) -> None:
    with st.container(border=True, key="tn-card-wl-remove"):
        st.markdown("**삭제**")
        target = st.selectbox("삭제할 종목", options=[r["ticker"] for r in rows],
                              format_func=_ticker_label(rows), key="_watchlist_remove_ticker")
        if target and ui_actions.confirm_delete(
                target,
                button_key="_watchlist_remove_btn",
                label="삭제"):
            if data.remove_watchlist_ticker(target):
                st.toast(f"🗑️ 관심종목 삭제: {target}")
                st.rerun()
            else:
                st.error(f"관심종목에 없습니다: {target}")


def _manage_section(rows: list[dict], folders: list[str]) -> None:
    """추가·폴더 이동·삭제 카드 — 접지 않고 항상 보인다 (봇 /watch add|remove 와 같은 저장소)."""
    theme.render(theme.section_head_html("관심종목 관리", "봇 /watch add·remove 와 같은 목록 · 삭제는 확인 후"))
    if not rows:
        _add_card()
        return
    c_add, c_move, c_del = st.columns(3)
    with c_add:
        _add_card()
    with c_move:
        _move_card(rows, folders)
    with c_del:
        _remove_card(rows)


def _watchlist_section(rows: list[dict]) -> None:
    if not rows:
        st.info("관심종목이 비어 있습니다 — 아래에서 추가하거나, 종목 분석의 ☆ · "
                "봇 `/watch add 종목코드` 로 추가하세요.")
        _manage_section(rows, [])
        return

    folders = data.watchlist_folders()
    counts = Counter(r.get("folder") or "미분류" for r in rows)
    folder_pick = st.pills(
        "폴더", options=["전체"] + folders, default="전체", required=True,
        format_func=lambda f: f"{f} {len(rows) if f == '전체' else counts.get(f, 0)}",
        key="_watchlist_folder_filter", label_visibility="collapsed") or "전체"
    folder_rows = rows if folder_pick == "전체" else [r for r in rows if r.get("folder") == folder_pick]

    c_search, c_sort = st.columns([3, 2], vertical_alignment="center")
    query = c_search.text_input("검색", key="_watchlist_search", label_visibility="collapsed",
                                placeholder="🔍 종목명 · 티커 · 메모 검색").strip().lower()
    sort = c_sort.segmented_control("정렬", _SORTS, default="추가순", key="_watchlist_sort",
                                    label_visibility="collapsed")
    filtered = folder_rows
    if query:
        filtered = [r for r in folder_rows if query in r["ticker"].lower()
                    or query in (r["name"] or "").lower() or query in (r["reason"] or "").lower()]

    quotes: dict = {}
    if filtered:
        try:
            quotes = cached.watchlist_quotes(tuple(sorted({r["ticker"] for r in filtered})))
        except Exception:
            quotes = {}
    list_rows, summary = _watch_list_rows(_sort_rows(filtered, quotes, sort), quotes)
    empty = (f"'{query}' 검색 결과 없음 ({len(folder_rows)}개 중)" if query
             else f"'{folder_pick}' 폴더에 종목이 없습니다.")
    theme.render(theme.stock_list_html(
        list_rows, title=f"{folder_pick} {len(filtered)}",
        meta=_summary_meta(summary) if filtered else "", empty=empty))
    st.caption("종목을 누르면 상세 분석으로 이동 · 가격은 yfinance 일봉 마지막 값"
               "(장중엔 지연 체결가, 5분 캐시 · 국내 ₩·미국 $)")

    _manage_section(rows, folders)


_TX_TYPE_LABELS = {"Purchase": "매수", "Sale": "매도", "Exchange": "교환"}


def _usd_compact(value) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:,.1f}{unit}"
    return f"${v:,.0f}"


def _my_us_tickers(rows: list[dict]) -> tuple[tuple[str, ...], dict]:
    """관심종목 + 해외 보유 중 미국 티커와 표시명 — 13F 는 미국 상장 종목만 신고한다."""
    items = [(r.get("ticker"), r.get("name")) for r in rows]
    try:
        items += [(h.get("ticker"), h.get("name")) for h in cached.holdings() or []]
    except Exception:
        pass
    names: dict[str, str] = {}
    for t, name in items:
        t = str(t or "").strip().upper()
        if _US_TICKER.match(t):
            names[t] = names.get(t) or name or t
    return tuple(sorted(names)), names


def _load_investor_data(my_tickers: tuple[str, ...]) -> tuple[dict, dict, dict, dict]:
    """기관 허브·13F 스크리닝·정치인 상위·내 종목 보유자를 동시에 불러온다.

    각각 따로 캐시되며 콜드일 때 직렬 합이 수 초라, 접지 않고 늘 그리는 대신
    병렬로 가장 느린 것 하나만큼만 기다린다. 워커가 캐시를 쓰도록 ScriptRunContext 를 붙인다.
    """
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
    jobs = (cached.institution_watch,
            lambda: cached.institution_screener(_SCREEN_13F_KEYS),
            lambda: cached.congress_top_traded(90),
            lambda: cached.watchlist_investor_moves(my_tickers) if my_tickers else {})
    with ThreadPoolExecutor(max_workers=len(jobs), initializer=add_script_run_ctx,
                            initargs=(None, get_script_run_ctx())) as ex:
        hub, screen, congress, moves = (f.result() for f in [ex.submit(job) for job in jobs])
    return hub or {}, screen or {}, congress or {}, moves or {}


def _my_stocks_section(my_tickers: tuple[str, ...], moves: dict, names: dict) -> None:
    """내 관심·보유 종목을 들고 있거나 이번 분기 사고판 유명 투자자."""
    theme.render(theme.section_head_html(
        "내 종목 × 유명 투자자", f"관심·보유 미국 종목 {len(my_tickers)}개 · 최신 13F 와 직전 분기 비교 · 누르면 종목 분석"))
    if not my_tickers:
        st.caption("관심종목·보유에 미국 종목이 없어 비교할 13F 가 없습니다.")
        return

    def _who(entries: list[dict]) -> list[str]:
        tags = [e.get("short_name") or e.get("key") for e in entries[:3]]
        return tags + ([f"외 {len(entries) - 3}"] if len(entries) > 3 else [])

    holders, buckets = [], {a: [] for a in ("new", "added", "trimmed", "exited")}
    for t in my_tickers:
        entries = moves.get(t) or []
        name = names.get(t) or t
        holding = [e for e in entries if e.get("action") not in (None, "exited")]
        if holding:
            top = holding[0]
            holders.append({"ticker": t, "name": name, "tags": _who(holding), "n": len(holding),
                            "value": f"{len(holding)}명",
                            "delta": f"최대 비중 {float(top.get('weight_pct') or 0):.1f}%",
                            "title": ", ".join(f"{e.get('short_name')} {float(e.get('weight_pct') or 0):.1f}%"
                                               for e in holding)})
        for action, rows in buckets.items():
            hit = [e for e in entries if e.get("action") == action]
            if not hit:
                continue
            if action == "exited":
                delta = f"직전 최대 {max(float(e.get('prior_weight_pct') or 0) for e in hit):.1f}%"
            elif action == "new":
                delta = f"최대 비중 {max(float(e.get('weight_pct') or 0) for e in hit):.1f}%"
            else:   # 평균은 소량 보유에서 크게 늘린 한 명에 끌려간다(MSFT +2199%) — 중앙값
                chg = [e["shares_change_pct"] for e in hit if e.get("shares_change_pct") is not None]
                delta = f"주식 {statistics.median(chg) * 100:+.0f}%" if chg else ""
            title = ", ".join(f"{e.get('short_name')} {e['shares_change_pct'] * 100:+.0f}%"
                              for e in hit if e.get("shares_change_pct") is not None)
            rows.append({"ticker": t, "name": name, "tags": _who(hit), "value": f"{len(hit)}명",
                         "delta": delta, "direction": _ACTION_DIR[action], **({"title": title} if title else {})})
    holders.sort(key=lambda r: -r["n"])
    theme.render(theme.stock_list_html(holders, title="보유 중인 유명 투자자", meta=f"{len(holders)}종목",
                                       empty="내 종목을 보유한 추적 투자자가 없습니다."))
    for i, (action, rows) in enumerate(buckets.items()):
        if i % 2 == 0:
            grid = st.columns(2)
        with grid[i % 2]:
            unit = "" if action in ("new", "exited") else " · 주식 수 변화 중앙값"
            theme.render(theme.stock_list_html(rows, title=_ACTION_LABELS[action],
                                               meta=f"{len(rows)}종목{unit}", empty="없음"))
    st.caption("13F 는 분기말 보유를 45일 안에 신고하는 지연 자료 · 주식 수 ±5% 이상 변화만 매매로 봄 "
               "· 정보·표시용, 매매 신호 아님")


def _congress_trading_section() -> None:
    theme.render(theme.section_head_html("정치인 거래 검색", "미 하원 공시 · 상원은 아직 미지원"))
    name = st.text_input("의원 이름 검색 (영문)", key="_congress_member_search",
                         placeholder="예: Pelosi")
    if not name.strip():
        st.caption("의원 이름을 입력하면 최근 공시 거래를 보여줍니다 · 상원은 아직 미지원")
        return
    rows = cached.congress_trading(name.strip())
    if not rows:
        st.caption(f"'{name}' 에 해당하는 하원의원 공시 거래를 찾지 못했습니다.")
        return
    table = pd.DataFrame([{
        "일자": r.get("date"), "티커": r.get("ticker") or "—", "종목": r.get("asset"),
        "구분": _TX_TYPE_LABELS.get(r.get("type"), r.get("type")),
        "금액(구간)": r.get("amount"), "명의": r.get("owner"),
    } for r in rows])
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("금액은 정확한 액수가 아니라 신고 구간 · 공시 지연(최대 45일) 반영 "
              "· 정보·표시용 — 매매 신호 아님")


def _investor_short(key: str) -> str:
    return (thirteenf.FILERS.get(key) or {}).get("short") or key


def _stock_name(ticker: str | None, fallback: str | None = None) -> str:
    return (ticker_names.display_name(ticker, allow_net=False) if ticker else None) or fallback or ticker or "—"


def _bucket_list(title: str, rows: list[dict]) -> str:
    def _tags(keys: list[str]) -> list[str]:
        return [_investor_short(k) for k in keys[:3]] + ([f"외 {len(keys) - 3}"] if len(keys) > 3 else [])

    items = [{
        "ticker": r.get("ticker") or "", "name": _stock_name(r.get("ticker"), r.get("name")),
        "tags": _tags(r.get("institutions") or []),
        "value": f"기관 {r.get('count') or 0}곳",
        "delta": f"{(r.get('avg_delta_pct') or 0) * 100:+.1f}%p",
        "direction": _sign(r.get("avg_delta_pct")),
    } for r in rows]
    return theme.stock_list_html(items, title=title, meta=f"{len(items)}종목 · 평균 비중 변화", empty="해당 없음")


def _congress_list(title: str, rows: list[dict]) -> str:
    items = [{
        "ticker": r.get("ticker") or "—", "name": _stock_name(r.get("ticker")),
        "value": _usd_compact(r.get("total_amount_mid")),
        "delta": f"의원 {r.get('member_count') or 0}명",
    } for r in rows]
    return theme.stock_list_html(items, title=title, meta="추정금액 합 · 거래 의원수", empty="해당 없음")


def _screening_section(screen: dict, congress: dict) -> None:
    theme.render(theme.section_head_html(
        "여러 기관 공통 움직임",
        "직전 분기 13F 대비 주식 수 기준 · 신고가 끊긴 기관 제외 · 누르면 종목 분석"))
    for i, (title, key) in enumerate((("신규 편입", "new_buys"), ("추가 매수", "increased"),
                                      ("일부 매도", "decreased"), ("전량 매도", "exited"))):
        if i % 2 == 0:
            grid = st.columns(2)
        with grid[i % 2]:
            theme.render(_bucket_list(title, screen.get(key) or []))

    theme.render(theme.section_head_html(
        "정치인 매수·매도 상위", "최근 90일 미 하원 공시 · 금액은 신고 구간 중간값 추정 · 매매 신호 아님"))
    ccols = st.columns(2)
    with ccols[0]:
        theme.render(_congress_list("많이 산 종목", congress.get("bought") or []))
    with ccols[1]:
        theme.render(_congress_list("많이 판 종목", congress.get("sold") or []))

    theme.render(theme.section_label_html("왜 이런 움직임일까?"))
    if st.button("🧠 스크리닝 해설 생성", key="_watch_screen_explain_btn"):
        st.session_state["_watch_screen_explain_requested"] = True
    if st.session_state.get("_watch_screen_explain_requested"):
        with st.spinner("분석 중…"):
            explain = cached.institution_screen_explain(screen, congress)
        st.markdown(explain.get("summary") or "표시할 해설이 없습니다.")
        st.caption(f"신뢰도 {explain.get('confidence', 0.0):.2f} · "
                  f"{'자동 추정' if explain.get('mode') == 'llm' else '사실 나열(해설 모델을 쓸 수 없음)'}"
                  " — 투자 조언 아님")
    else:
        st.caption("버튼을 누르면 위 스크리닝 결과를 해설합니다. 자동으로 실행하지 않습니다.")


def _investor_section(hub: dict) -> None:
    theme.render(theme.section_head_html(
        "유명 투자자 포트폴리오", "13F 분기 공시 · 풋·콜 옵션은 롱 보유와 분리 · 투자자를 고르면 상세"))
    if hub.get("error"):
        st.warning(ui_actions.user_message(hub.get("error"), "기관 목록을 불러오지 못했습니다."))
    rows = list(hub.get("institutions") or [])
    if not rows:
        st.info("기관 허브에 표시할 스냅샷이 아직 없습니다.")
    else:
        # 13F 최신 → 신고 끊김 → 시드 순, 그 안에서는 롱 보유액 큰 순
        order = {"fresh": 0, "stale": 1}
        rows.sort(key=lambda r: (r.get("source_kind") != "13f", order.get(r.get("freshness"), 2),
                                 -float(r.get("total_value_usd") or 0)))
        by_key = {r["key"]: r for r in rows if r.get("key")}
        keys = list(by_key)
        if st.session_state.get("_investor_pick") not in by_key:
            st.session_state["_investor_pick"] = keys[0]
        pick = st.pills(
            "투자자", options=keys, required=True, key="_investor_pick", label_visibility="collapsed",
            format_func=lambda k: _short(by_key[k]) + (" · 신고 끊김" if by_key[k].get("freshness") == "stale" else ""),
        ) or keys[0]
        with st.container(border=True, key="tn-card-investor-detail"):
            _investor_detail(by_key[pick])

    theme.render(theme.section_label_html("전체 비교"))
    _render_comparison_table(hub.get("comparison") or {})

    theme.render(theme.section_label_html("공통 패턴 요약"))
    if st.button("🧠 해설 생성 (최대 20초)", key="_watch_llm_btn"):
        st.session_state["_watch_llm_requested"] = True
    if st.session_state.get("_watch_llm_requested"):
        with st.spinner("요약 생성 중…"):
            hub_llm = cached.institution_watch(None, with_llm_summary=True)
        _render_common_moves(hub_llm.get("analysis") or {})
    else:
        st.caption("버튼을 누르면 기관별 공통 패턴과 차이를 요약합니다. 자동으로 실행하지 않습니다.")


def render():
    st.title("관심종목")
    st.caption("직접 고른 종목 · 종목을 누르면 상세 분석 · 봇 `/watch add|remove` 와 같은 목록")

    rows = data.load_watchlist()
    _watchlist_section(rows)

    my_tickers, names = _my_us_tickers(rows)
    with st.spinner("기관·정치인 데이터 불러오는 중…"):
        hub_all, screen, congress, moves = _load_investor_data(my_tickers)
    _my_stocks_section(my_tickers, moves, names)
    _screening_section(screen, congress)
    _investor_section(hub_all)
    _congress_trading_section()
