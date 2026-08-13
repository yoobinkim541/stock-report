from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from agent_console import agent
from dashboard import cached, charts, data, theme, views
from ml.strategy_studio import StrategySpec, apply_strategy_patch, builtin_strategy_presets


def render_strategy_lab(
    key: str,
    pack: dict[str, Any] | None,
    *,
    mode: str = "research",
    catalog: dict[str, Any] | None = None,
    selected_spec: dict[str, Any] | None = None,
    preview: dict[str, Any] | None = None,
    versions: list[dict[str, Any]] | None = None,
    patch_preview: dict[str, Any] | None = None,
) -> None:
    prefix = _state_prefix(key, mode)
    pack = pack or {}
    catalog = _safe_catalog(catalog)
    presets = builtin_strategy_presets()

    st.markdown("##### 전략 스튜디오")
    st.caption(
        "전략 스펙을 저장하고, 백테스트를 돌리고, AI 대화로 패치를 제안받아 바로 다시 미리볼 수 있습니다."
    )

    studio_pack = pack.get("strategy_studio") or {}
    top_cols = st.columns(4)
    top_cols[0].metric("저장 전략", str(studio_pack.get("spec_count", catalog.get("count", 0)) or 0))
    top_cols[1].metric("버전", str(studio_pack.get("version_count", catalog.get("version_total", 0)) or 0))
    top_cols[2].metric("프리셋", str(len(presets)))
    latest_name = (studio_pack.get("latest") or {}).get("name") if isinstance(studio_pack, dict) else None
    top_cols[3].metric("최신", latest_name or (catalog.get("latest") or {}).get("name") or "—")

    selected_record = _ensure_selected_record(prefix, catalog, selected_spec)
    selected_record = _catalog_record_or_fallback(selected_record, catalog, presets)

    if selected_record:
        st.session_state.setdefault(_state_key(prefix, "selected_id"), selected_record.get("id") or "")

    left, right = st.columns([1.08, 1.22], gap="large")
    with left:
        _render_library_panel(prefix, catalog, presets, selected_record)
        st.divider()
        _render_editor_panel(prefix, selected_record, preview, mode=mode)

    with right:
        _render_preview_panel(prefix, selected_record, preview, mode=mode)
        st.divider()
        _render_versions_panel(prefix, selected_record, versions)
        st.divider()
        _render_patch_panel(prefix, selected_record, patch_preview)

    st.divider()
    _render_conversation_panel(prefix, pack, selected_record, mode=mode)


def _render_library_panel(
    prefix: str,
    catalog: dict[str, Any],
    presets: dict[str, dict],
    selected_record: dict[str, Any] | None,
) -> None:
    st.markdown("##### 전략 선택")
    specs = list(catalog.get("specs") or [])
    default_idx = 0
    if selected_record and selected_record.get("id"):
        for idx, row in enumerate(specs):
            if str(row.get("id") or "") == str(selected_record.get("id") or ""):
                default_idx = idx
                break
    if specs:
        idx = st.selectbox(
            "저장된 전략",
            options=list(range(len(specs))),
            index=min(default_idx, len(specs) - 1),
            key=_state_key(prefix, "catalog_index"),
            format_func=lambda i: _record_label(specs[i]),
        )
        picked = specs[idx]
        if st.button("선택 전략 불러오기", key=_state_key(prefix, "load_selected"), width="stretch"):
            _set_draft_record(prefix, picked)
            _set_preview(prefix, None)
            _set_patch(prefix, None)
            st.rerun()
    else:
        st.info("저장된 전략이 아직 없습니다. 아래 프리셋이나 새 JSON으로 시작해 보세요.")

    preset_keys = list(presets)
    if preset_keys:
        key = st.selectbox(
            "프리셋",
            options=preset_keys,
            key=_state_key(prefix, "preset_key"),
            format_func=lambda k: presets.get(k, {}).get("name", k),
        )
        if st.button("프리셋 적용", key=_state_key(prefix, "load_preset"), width="stretch"):
            _set_draft_record(prefix, {"name": presets[key].get("name", key), "spec": presets[key]})
            _set_preview(prefix, None)
            _set_patch(prefix, None)
            st.rerun()

    if selected_record:
        st.caption(
            f"현재 선택: {selected_record.get('name', '—')} · v{selected_record.get('version', 1)}"
            f"{' · ' + selected_record.get('id') if selected_record.get('id') else ''}"
        )


def _render_editor_panel(
    prefix: str,
    selected_record: dict[str, Any] | None,
    preview: dict[str, Any] | None,
    *,
    mode: str,
) -> None:
    st.markdown("##### 전략 편집")
    pending_key = _state_key(prefix, "draft_text_pending")
    if pending_key in st.session_state:
        st.session_state[_state_key(prefix, "draft_text")] = st.session_state.pop(pending_key)
    draft_text = st.session_state.get(_state_key(prefix, "draft_text"))
    if not draft_text:
        draft_text = _spec_to_text(_current_spec_payload(selected_record))
        st.session_state[_state_key(prefix, "draft_text")] = draft_text

    draft_text = st.text_area(
        "전략 JSON",
        value=draft_text,
        height=330,
        key=_state_key(prefix, "draft_text"),
        help="원하는 전략 구조를 직접 편집할 수 있습니다. 지표, 규칙, 사이징, 비용, 검증을 모두 바꿔도 됩니다.",
    )

    benchmark_default = _benchmark_for_payload(_current_spec_payload(selected_record))
    benchmark = st.text_input(
        "벤치마크",
        value=st.session_state.get(_state_key(prefix, "benchmark"), benchmark_default),
        key=_state_key(prefix, "benchmark"),
        help="전략을 무엇과 비교할지 적습니다. 비워두면 base_symbol 또는 기본 벤치마크를 씁니다.",
    ).strip()
    period = st.selectbox(
        "기간",
        options=["1mo", "3mo", "6mo", "1y", "2y", "5y", "60d", "30d"],
        index=3,
        key=_state_key(prefix, "period"),
    )

    controls = st.columns(4)
    if controls[0].button("미리보기", key=_state_key(prefix, "run_preview"), type="primary", width="stretch"):
        spec = _parse_spec_text(draft_text)
        if spec is not None:
            _set_preview(prefix, _run_preview(spec, benchmark=benchmark, period=period))
            _set_draft_record(prefix, spec)
            st.rerun()
    if controls[1].button("저장", key=_state_key(prefix, "save_spec"), width="stretch"):
        spec = _parse_spec_text(draft_text)
        if spec is not None:
            saved = _save_spec(spec)
            if saved:
                _set_draft_record(prefix, saved)
                _set_preview(prefix, _run_preview(_current_spec_payload(saved), benchmark=benchmark, period=period))
                _refresh_caches()
                st.toast("전략을 저장했습니다.")
                st.rerun()
    if controls[2].button("초기화", key=_state_key(prefix, "reset_spec"), width="stretch"):
        _set_draft_record(prefix, _catalog_record_or_fallback(selected_record, _safe_catalog(None), builtin_strategy_presets()))
        _set_preview(prefix, preview)
        st.rerun()
    if controls[3].button("검증", key=_state_key(prefix, "validate_spec"), width="stretch"):
        spec = _parse_spec_text(draft_text)
        if spec is not None:
            try:
                warnings = StrategySpec.from_dict(spec).validate()
                if warnings:
                    st.warning(" · ".join(warnings))
                else:
                    st.success("전략 스펙 검증 통과")
            except Exception as exc:
                st.error(str(exc))

    if mode == "lab":
        st.caption("전략 캔버스에서는 JSON 수정과 AI 패치를 바로 이어서 쓸 수 있게 했습니다.")


def _render_preview_panel(
    prefix: str,
    selected_record: dict[str, Any] | None,
    preview: dict[str, Any] | None,
    *,
    mode: str,
) -> None:
    st.markdown("##### 미리보기")
    spec_payload = _current_spec_payload(selected_record)
    selected_preview = _ensure_preview(prefix, preview)
    if not selected_preview:
        st.info("전략을 미리보기하면 수익률, 거래수, 이퀴티 곡선, 거래 표식을 한 번에 볼 수 있습니다.")
        return

    if not selected_preview.get("ok", True):
        st.warning(selected_preview.get("errors") or selected_preview.get("error") or "미리보기 실패")
    report = selected_preview.get("report") or {}
    summary = report.get("summary") or {}
    metrics = selected_preview.get("metrics") or {}

    cols = st.columns(4)
    cols[0].metric("거래 수", str(summary.get("trade_count") or selected_preview.get("trade_count") or metrics.get("trade_count") or 0))
    cols[1].metric("CAGR", data.f_frac_pct_s(summary.get("cagr") or metrics.get("cagr")))
    cols[2].metric("MDD", data.f_frac_pct(summary.get("max_drawdown") or metrics.get("max_drawdown")))
    cols[3].metric("Sharpe", data.f_ratio(summary.get("sharpe") or metrics.get("sharpe"), 2))

    if summary.get("name"):
        st.caption(
            f"{summary.get('name')} · {summary.get('market', '')}/{summary.get('timeframe', '')} · "
            f"기준 {summary.get('base_symbol') or '—'}"
        )

    if selected_preview.get("ok"):
        if st.button("차트 리플레이로 보내기", key=_state_key(prefix, "send_to_replay"), width="stretch"):
            try:
                from dashboard import chart_replay_rules
                packet = chart_replay_rules.strategy_packet(selected_preview.get("spec") or spec_payload)
                st.session_state["_chart_replay_handoff"] = {
                    "packet": packet,
                    "summary": dict(summary),
                    "metrics": dict(metrics),
                }
                st.toast("전략 규칙을 차트 리플레이에 준비했습니다.")
            except Exception as exc:
                st.error(str(exc))

    warnings = list(selected_preview.get("warnings") or [])
    errors = list(selected_preview.get("errors") or [])
    if warnings:
        st.caption("경고: " + " · ".join(str(w) for w in warnings[:4]))
    if errors:
        st.caption("오류: " + " · ".join(str(e) for e in errors[:4]))

    equity = _frame_from_payload(report.get("equity"))
    if not equity.empty:
        st.plotly_chart(charts.equity_curve(equity), width="stretch", config=_chart_cfg())

    trades = list(report.get("trades") or [])
    base_symbol = str(summary.get("base_symbol") or spec_payload.get("base_symbol") or "").upper().strip()
    if base_symbol and trades:
        hist = _price_history(base_symbol, spec_payload)
        if hist is not None and not hist.empty:
            st.plotly_chart(
                charts.price_line(hist, ticker=base_symbol, trades=_trade_markers(trades), view_days=_view_days(spec_payload)),
                width="stretch",
                config=_chart_cfg(),
            )

    if report.get("weights"):
        latest_weights = _frame_from_payload(report.get("weights"))
        if not latest_weights.empty:
            last_row = latest_weights.tail(1).T.reset_index()
            last_row.columns = ["종목", "비중"]
            st.dataframe(last_row, hide_index=True, width="stretch", height=min(240, 44 + 32 * len(last_row)))


def _render_versions_panel(
    prefix: str,
    selected_record: dict[str, Any] | None,
    versions: list[dict[str, Any]] | None,
) -> None:
    st.markdown("##### 버전")
    spec_id = str((selected_record or {}).get("id") or "").strip()
    if not spec_id:
        st.caption("저장된 전략을 불러오면 버전 히스토리를 볼 수 있습니다.")
        return

    version_rows = versions if versions is not None else _safe_versions(spec_id)
    if not version_rows:
        st.caption("버전 기록이 없습니다.")
        return

    idx = st.selectbox(
        "버전 선택",
        options=list(range(len(version_rows))),
        key=_state_key(prefix, "version_index"),
        format_func=lambda i: f"v{version_rows[i].get('version')} · {version_rows[i].get('created_at', '')[:19]}",
    )
    chosen = version_rows[idx]
    st.dataframe(
        pd.DataFrame([{
            "버전": row.get("version"),
            "이름": row.get("name"),
            "출처": row.get("source"),
            "시각": row.get("created_at"),
        } for row in version_rows]),
        hide_index=True,
        width="stretch",
        height=min(280, 44 + 32 * len(version_rows)),
    )
    if st.button("이 버전으로 복원", key=_state_key(prefix, "revert_version"), width="stretch"):
        restored = _revert_spec(spec_id, int(chosen.get("version") or 0))
        if restored:
            _set_draft_record(prefix, restored)
            _set_preview(prefix, None)
            _refresh_caches()
            st.toast("버전을 복원했습니다.")
            st.rerun()


def _render_patch_panel(prefix: str, selected_record: dict[str, Any] | None, patch_preview: dict[str, Any] | None) -> None:
    st.markdown("##### 패치")
    current_patch = patch_preview if patch_preview is not None else st.session_state.get(_state_key(prefix, "patch"))
    if not current_patch:
        st.caption("AI 대화로 받은 수정안을 여기서 확인하고 바로 적용할 수 있습니다.")
        return

    diff_rows = list(current_patch.get("diff") or [])
    if diff_rows:
        st.dataframe(
            pd.DataFrame(diff_rows),
            hide_index=True,
            width="stretch",
            height=min(240, 44 + 32 * len(diff_rows)),
        )

    preview = current_patch.get("preview") or {}
    if preview.get("ok"):
        summary = (preview.get("report") or {}).get("summary") or {}
        st.caption(
            f"패치 미리보기 · {summary.get('name', '—')} · 거래 {summary.get('trade_count', preview.get('trade_count', 0))}"
        )
    else:
        st.warning(preview.get("error") or "패치 미리보기 실패")

    patch = current_patch.get("patch") or {}
    if patch:
        st.json(patch)
        spec_payload = _current_spec_payload(selected_record)
        if st.button("패치 반영", key=_state_key(prefix, "apply_patch"), width="stretch"):
            patched = apply_strategy_patch(spec_payload, patch)
            _set_draft_record(prefix, patched)
            _set_preview(prefix, None)
            _refresh_caches()
            st.toast("패치를 전략 JSON에 반영했습니다.")
            st.rerun()


def _render_conversation_panel(prefix: str, pack: dict[str, Any], selected_record: dict[str, Any] | None, *, mode: str) -> None:
    st.markdown("##### AI 대화")
    history_key = _state_key(prefix, "history")
    history = st.session_state.setdefault(history_key, [])
    if not history:
        st.caption("전략 문장을 적으면 에이전트가 설명을 붙이고, 동시에 패치 초안을 계산합니다.")

    for msg in history[-8:]:
        role = "assistant" if str(msg.get("role", "")).lower() == "assistant" else "user"
        with st.chat_message(role):
            st.markdown(msg.get("content") or "")
            if msg.get("meta"):
                st.caption(msg.get("meta"))

    prompt = st.chat_input(
        "예: ATR 손절을 넣고 EMA20/50 추세 필터로 바꿔줘",
        key=_state_key(prefix, "chat_input"),
    )
    if not prompt:
        return

    spec_payload = _current_spec_payload(selected_record)
    history.append({"role": "user", "content": prompt})

    agent_prompt = _build_agent_prompt(prompt, spec_payload, pack=pack, mode=mode)
    try:
        reply = agent.answer(agent_prompt, "lab", async_postprocess=True)
        answer_text = reply.get("answer", "") if reply.get("ok") else reply.get("error", "응답 실패")
        meta = f"엔진 {reply.get('context', {}).get('engine', 'unknown')}"
    except Exception as exc:
        answer_text = f"AI 응답 실패: {exc}"
        meta = "error"

    patch_result = None
    try:
        patch_result = views.strategy_studio.propose_strategy_patch(prompt, spec_payload, history=history, pack=pack)
    except Exception as exc:
        patch_result = {"ok": False, "error": str(exc), "patch": {}, "diff": [], "preview": {}}

    if patch_result:
        _set_patch(prefix, patch_result)
        if patch_result.get("preview"):
            _set_preview(prefix, patch_result.get("preview"))

    if patch_result and patch_result.get("patch"):
        patch_bits = []
        if patch_result.get("diff"):
            patch_bits.append(f"diff {len(patch_result['diff'])}개")
        if patch_result.get("preview"):
            summary = (patch_result["preview"].get("report") or {}).get("summary") or {}
            patch_bits.append(f"preview {summary.get('trade_count', 0)}건")
        if patch_bits:
            meta += " · " + " · ".join(patch_bits)

    history.append({"role": "assistant", "content": answer_text, "meta": meta})
    st.rerun()


def _safe_catalog(catalog: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(catalog, dict) and catalog:
        return catalog
    try:
        return cached.strategy_studio_catalog()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "count": 0, "specs": [], "version_total": 0, "latest": None}


def _safe_versions(spec_id: str) -> list[dict[str, Any]]:
    try:
        return cached.strategy_studio_versions(spec_id)
    except Exception:
        try:
            return views.strategy_studio_versions(spec_id)
        except Exception:
            return []


def _ensure_selected_record(
    prefix: str,
    catalog: dict[str, Any],
    selected_spec: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if selected_spec:
        return _normalize_record(selected_spec)
    specs = list(catalog.get("specs") or [])
    if not specs:
        latest = catalog.get("latest")
        return _normalize_record(latest) if latest else None
    state_id = str(st.session_state.get(_state_key(prefix, "selected_id")) or "").strip()
    if state_id:
        for row in specs:
            if str(row.get("id") or "") == state_id:
                return _normalize_record(row)
    idx = int(st.session_state.get(_state_key(prefix, "catalog_index"), 0) or 0)
    idx = max(0, min(idx, len(specs) - 1))
    return _normalize_record(specs[idx])


def _catalog_record_or_fallback(
    record: dict[str, Any] | None,
    catalog: dict[str, Any],
    presets: dict[str, dict],
) -> dict[str, Any] | None:
    if record:
        return _normalize_record(record)
    latest = catalog.get("latest")
    if latest:
        return _normalize_record(latest)
    if presets:
        name, spec = next(iter(presets.items()))
        return {"id": None, "name": spec.get("name", name), "version": spec.get("version", 1), "spec": spec}
    return None


def _set_draft_record(prefix: str, record: dict[str, Any] | None) -> None:
    record = _normalize_record(record)
    spec = _current_spec_payload(record)
    st.session_state[_state_key(prefix, "selected_id")] = str(record.get("id") or spec.get("id") or "").strip()
    st.session_state[_state_key(prefix, "draft_text_pending")] = _spec_to_text(spec)
    st.session_state[_state_key(prefix, "draft_record")] = record


def _set_preview(prefix: str, preview: dict[str, Any] | None) -> None:
    if preview is None:
        st.session_state.pop(_state_key(prefix, "preview"), None)
    else:
        st.session_state[_state_key(prefix, "preview")] = preview


def _set_patch(prefix: str, patch: dict[str, Any] | None) -> None:
    if patch is None:
        st.session_state.pop(_state_key(prefix, "patch"), None)
    else:
        st.session_state[_state_key(prefix, "patch")] = patch


def _ensure_preview(
    prefix: str,
    preview: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if preview is not None:
        st.session_state[_state_key(prefix, "preview")] = preview
        return preview
    stored = st.session_state.get(_state_key(prefix, "preview"))
    if stored is not None:
        return stored
    return None


def _run_preview(spec_payload: dict[str, Any], *, benchmark: str | None, period: str | None) -> dict[str, Any]:
    try:
        return cached.strategy_studio_preview(spec_payload, benchmark=benchmark, period=period)
    except Exception:
        return views.strategy_studio_preview(spec_payload, benchmark=benchmark, period=period)


def _save_spec(spec_payload: dict[str, Any]) -> dict[str, Any] | None:
    if not spec_payload:
        return None
    spec_id = str(spec_payload.get("id") or "").strip()
    try:
        if spec_id:
            return views.strategy_studio.save_strategy_version(spec_id, spec_payload, source="ui")
        return views.strategy_studio.save_strategy_spec(spec_payload)
    except Exception as exc:
        st.error(f"저장 실패: {exc}")
        return None


def _revert_spec(spec_id: str, version: int) -> dict[str, Any] | None:
    try:
        return views.strategy_studio.revert_strategy_version(spec_id, version)
    except Exception as exc:
        st.error(f"복원 실패: {exc}")
        return None


def _refresh_caches() -> None:
    for cache in (getattr(cached, "strategy_studio_catalog", None), getattr(cached, "strategy_studio_versions", None), getattr(cached, "strategy_studio_preview", None)):
        try:
            clear = getattr(cache, "clear", None)
            if callable(clear):
                clear()
        except Exception:
            pass


def _normalize_record(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not payload:
        return None
    record = dict(payload)
    spec = record.get("spec")
    if not isinstance(spec, dict):
        spec = {k: v for k, v in record.items() if k not in {"patch", "source", "created_at", "updated_at", "version_row_id"}}
        if "name" not in spec and record.get("name"):
            spec["name"] = record.get("name")
    record["spec"] = dict(spec)
    if not record.get("id") and record["spec"].get("id"):
        record["id"] = record["spec"]["id"]
    if not record.get("name") and record["spec"].get("name"):
        record["name"] = record["spec"]["name"]
    record["version"] = int(record.get("version") or record["spec"].get("version") or 1)
    return record


def _record_label(record: dict[str, Any]) -> str:
    normalized = _normalize_record(record) or {}
    spec = normalized.get("spec") or {}
    name = normalized.get("name") or spec.get("name") or "전략"
    version = normalized.get("version") or spec.get("version") or 1
    ident = str(normalized.get("id") or spec.get("id") or "").strip()
    tail = f" · {ident}" if ident else ""
    return f"{name} v{version}{tail}"


def _current_spec_payload(record: dict[str, Any] | None) -> dict[str, Any]:
    normalized = _normalize_record(record)
    if not normalized:
        return {}
    spec = dict(normalized.get("spec") or {})
    if normalized.get("id") and not spec.get("id"):
        spec["id"] = normalized["id"]
    if normalized.get("name") and not spec.get("name"):
        spec["name"] = normalized["name"]
    if normalized.get("version") and not spec.get("version"):
        spec["version"] = normalized["version"]
    return spec


def _spec_to_text(spec_payload: dict[str, Any]) -> str:
    try:
        return json.dumps(spec_payload or {}, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        return str(spec_payload or {})


def _parse_spec_text(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text or "{}")
        spec = StrategySpec.from_dict(payload).to_dict()
        if payload.get("id") and not spec.get("id"):
            spec["id"] = payload.get("id")
        return spec
    except Exception as exc:
        st.error(f"전략 JSON 파싱 실패: {exc}")
        return None


def _build_agent_prompt(question: str, spec_payload: dict[str, Any], *, pack: dict[str, Any], mode: str) -> str:
    return "\n".join([
        "너는 전략 스튜디오의 연구 파트너다.",
        f"화면 모드: {mode}",
        "현재 전략 스펙(JSON):",
        _spec_to_text(spec_payload),
        "",
        "사용자 요청:",
        question,
        "",
        "요구사항:",
        "- 진입/청산/손절/익절/사이징/검증 관점에서 구체적으로 답한다.",
        "- 필요하면 더 나은 규칙과 패치 방향을 제안한다.",
        "- 시장 템플릿으로 답을 흘리지 말고, 현재 전략을 어떻게 개선할지 중심으로 설명한다.",
        "- 답변 마지막에는 바로 적용할 수 있는 수정 포인트를 한 줄로 정리한다.",
        "",
        f"전략 카탈로그 요약: {json.dumps((pack.get('strategy_studio') or {}), ensure_ascii=False)}",
    ])


def _frame_from_payload(payload: Any) -> pd.DataFrame:
    if isinstance(payload, pd.DataFrame):
        return payload.copy()
    if not isinstance(payload, dict):
        return pd.DataFrame()
    rows = payload.get("rows")
    if rows is None:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    index = payload.get("index")
    if isinstance(index, list) and len(index) == len(df):
        try:
            df.index = pd.to_datetime(index)
        except Exception:
            df.index = index
    return df


def _trade_markers(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markers = []
    for row in trades or []:
        action = str(row.get("action") or "").lower()
        side = "buy" if any(token in action for token in ("enter", "buy")) else "sell" if any(token in action for token in ("exit", "sell", "trim")) else ""
        if not side:
            continue
        change = row.get("change")
        qty = abs(float(change)) if isinstance(change, (int, float)) else None
        markers.append({
            "date": row.get("date"),
            "timestamp": row.get("timestamp") or row.get("date"),
            "side": side,
            "qty": qty,
            "price": row.get("price"),
            "action": action,
            "reason": row.get("reason"),
        })
    return markers


def _price_history(symbol: str, spec_payload: dict[str, Any]) -> pd.DataFrame:
    period = _period_for_payload(spec_payload)
    try:
        return cached.ohlc(symbol, period=period)
    except Exception:
        try:
            return views.strategy_studio._load_prices(StrategySpec.from_dict(spec_payload), period=period)  # type: ignore[attr-defined]
        except Exception:
            return pd.DataFrame()


def _period_for_payload(spec_payload: dict[str, Any]) -> str:
    tf = str((spec_payload or {}).get("timeframe") or "1d").lower().strip()
    mapping = {"1d": "2y", "1wk": "5y", "1w": "5y", "5m": "60d", "1m": "30d"}
    return mapping.get(tf, "2y")


def _view_days(spec_payload: dict[str, Any]) -> int | None:
    tf = str((spec_payload or {}).get("timeframe") or "1d").lower().strip()
    mapping = {"1d": 365, "1wk": 730, "1w": 730, "5m": 30, "1m": 14}
    return mapping.get(tf, 365)


def _benchmark_for_payload(spec_payload: dict[str, Any]) -> str | None:
    base = str((spec_payload or {}).get("base_symbol") or "").strip().upper()
    if base:
        return base
    market = str((spec_payload or {}).get("market") or "us").lower()
    return "QQQ" if market != "kr" else "^KS11"


def _chart_cfg() -> dict[str, Any]:
    return dict(displayModeBar=False, responsive=True)


def _state_prefix(key: str, mode: str) -> str:
    return f"strategy_studio::{mode}::{str(key or 'default').strip().replace(' ', '_')}"


def _state_key(prefix: str, name: str) -> str:
    return f"{prefix}::{name}"
