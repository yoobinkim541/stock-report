from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

from agent_console import agent
from dashboard import cached, charts, data, theme, views
from ml.strategy_studio import StrategySpec, apply_strategy_patch, builtin_strategy_presets, strategy_spec_hash
from ml.strategy_studio.validation import _cpcv_chronology_payload_ok as _strict_cpcv_chronology_payload_ok


_DATA_PROFILES = ("generic", "global_swing", "kr_intraday", "extended_us")
_EXECUTION_PROFILES = ("bar", "global_swing", "kr_intraday", "extended_us")
_SIGNAL_TYPES = ("rule", "factor", "model", "ensemble")
_SIGNAL_PROVIDERS = ("rule", "momentum", "volatility", "cross_sectional_rank", "model", "ensemble")
_PORTFOLIO_OPTIMIZERS = ("legacy_fixed", "cost_aware_risk_budget", "equal_weight", "risk_budget")
_VALIDATION_MODES = ("single_pass", "walk_forward", "purged_walk_forward", "cpcv")
_COST_SCENARIOS = ("default", "low", "high")

_COST_SCENARIO_VALUES = {
    "default": {"fees_bps": 5.0, "slippage_bps": 5.0, "spread_bps": 3.0},
    "low": {"fees_bps": 2.0, "slippage_bps": 2.0, "spread_bps": 1.0},
    "high": {"fees_bps": 8.0, "slippage_bps": 10.0, "spread_bps": 6.0},
}


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

    _render_environment_state(prefix, selected_record, preview)

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


def _render_editor_controls(prefix: str, defaults: dict[str, str]) -> dict[str, str]:
    """Render declarative controls that feed the next strategy action."""

    st.markdown("##### 전략·실행 설정")
    st.caption("데이터 프로필 · 실행 프로필 · 전략 유형 · 신호 공급자 · 포트폴리오 최적화 · 검증 모드 · 비용 시나리오")
    first = st.columns(3)
    data_profile = first[0].selectbox(
        "데이터 프로필",
        options=list(_DATA_PROFILES),
        index=_option_index(_DATA_PROFILES, defaults.get("data_profile"), "generic"),
        key=_state_key(prefix, "data_profile"),
    )
    execution_profile = first[1].selectbox(
        "실행 프로필",
        options=list(_EXECUTION_PROFILES),
        index=_option_index(_EXECUTION_PROFILES, defaults.get("execution_profile"), "bar"),
        key=_state_key(prefix, "execution_profile"),
    )
    validation_mode = first[2].selectbox(
        "검증 모드",
        options=list(_VALIDATION_MODES),
        index=_option_index(_VALIDATION_MODES, defaults.get("validation_mode"), "single_pass"),
        key=_state_key(prefix, "validation_mode"),
    )

    second = st.columns(3)
    strategy_type = second[0].selectbox(
        "전략 유형",
        options=list(_SIGNAL_TYPES),
        index=_option_index(_SIGNAL_TYPES, defaults.get("strategy_type"), "rule"),
        key=_state_key(prefix, "strategy_type"),
    )
    provider = second[1].selectbox(
        "신호 공급자",
        options=list(_SIGNAL_PROVIDERS),
        index=_option_index(_SIGNAL_PROVIDERS, defaults.get("provider"), "rule"),
        key=_state_key(prefix, "provider"),
    )
    portfolio_optimizer = second[2].selectbox(
        "포트폴리오 최적화",
        options=list(_PORTFOLIO_OPTIMIZERS),
        index=_option_index(_PORTFOLIO_OPTIMIZERS, defaults.get("portfolio_optimizer"), "legacy_fixed"),
        key=_state_key(prefix, "portfolio_optimizer"),
    )

    cost_scenario = st.selectbox(
        "비용 시나리오",
        options=list(_COST_SCENARIOS),
        index=_option_index(_COST_SCENARIOS, defaults.get("cost_scenario"), "default"),
        key=_state_key(prefix, "cost_scenario"),
        format_func=lambda value: {"default": "기본 비용", "low": "낮은 비용", "high": "높은 비용"}.get(value, value),
    )
    return {
        "data_profile": data_profile,
        "execution_profile": execution_profile,
        "validation_mode": validation_mode,
        "strategy_type": strategy_type,
        "provider": provider,
        "portfolio_optimizer": portfolio_optimizer,
        "cost_scenario": cost_scenario,
    }


def _option_index(options: tuple[str, ...], value: object, fallback: str) -> int:
    value_text = str(value or fallback).strip().lower()
    try:
        return options.index(value_text)
    except ValueError:
        return options.index(fallback)


def _editor_control_defaults(spec: dict[str, Any]) -> dict[str, str]:
    signal = spec.get("signal") if isinstance(spec.get("signal"), Mapping) else {}
    portfolio = spec.get("portfolio") if isinstance(spec.get("portfolio"), Mapping) else {}
    validation = spec.get("validation") if isinstance(spec.get("validation"), Mapping) else {}
    execution = spec.get("execution") if isinstance(spec.get("execution"), Mapping) else {}
    provider = signal.get("plugin") or signal.get("provider") or signal.get("ref") or signal.get("type") or "rule"
    return {
        "data_profile": str(spec.get("data_profile") or "generic").strip().lower(),
        "execution_profile": str(spec.get("execution_profile") or execution.get("profile") or "bar").strip().lower(),
        "validation_mode": str(validation.get("mode") or "single_pass").strip().lower(),
        "strategy_type": str(signal.get("type") or "rule").strip().lower(),
        "provider": str(provider).strip().lower(),
        "portfolio_optimizer": str(portfolio.get("optimizer") or "legacy_fixed").strip().lower(),
        "cost_scenario": _cost_scenario_for_spec(spec),
    }


def _cost_scenario_for_spec(spec: dict[str, Any]) -> str:
    costs = spec.get("costs") if isinstance(spec.get("costs"), Mapping) else {}
    if not costs:
        return "default"
    for name in ("low", "high"):
        try:
            if all(float(costs.get(key, -1)) == value for key, value in _COST_SCENARIO_VALUES[name].items()):
                return name
        except (TypeError, ValueError):
            continue
    return "default"


def _apply_editor_controls(
    spec: dict[str, Any],
    controls: dict[str, str],
    *,
    benchmark: str | None = None,
) -> dict[str, Any]:
    """Apply UI choices while retaining legacy rule specs until opted in."""

    payload = deepcopy(spec)
    data_profile = controls.get("data_profile", "generic")
    execution_profile = controls.get("execution_profile", "bar")
    validation_mode = controls.get("validation_mode", "single_pass")
    strategy_type = controls.get("strategy_type", "rule")
    provider = controls.get("provider", "rule")
    portfolio_optimizer = controls.get("portfolio_optimizer", "legacy_fixed")
    cost_scenario = controls.get("cost_scenario", "default")

    if "data_profile" in payload or data_profile != "generic":
        payload["data_profile"] = data_profile
    if "execution_profile" in payload or execution_profile != "bar":
        payload["execution_profile"] = execution_profile

    modern_signal_selected = bool(payload.get("signal")) or strategy_type != "rule" or provider != "rule"
    if modern_signal_selected:
        signal = dict(payload.get("signal") or {})
        signal["type"] = strategy_type
        if strategy_type == "rule":
            signal.pop("plugin", None)
        else:
            signal["plugin"] = provider if provider != "rule" else strategy_type
        payload["signal"] = signal
    elif "signal" in payload:
        payload["signal"] = {}

    if portfolio_optimizer != "legacy_fixed" or payload.get("portfolio"):
        portfolio = dict(payload.get("portfolio") or {})
        if portfolio_optimizer == "legacy_fixed":
            portfolio.pop("optimizer", None)
        else:
            portfolio["optimizer"] = portfolio_optimizer
        payload["portfolio"] = portfolio

    if execution_profile != "bar" or payload.get("execution"):
        execution = dict(payload.get("execution") or {})
        execution["profile"] = execution_profile
        payload["execution"] = execution

    if "validation" in payload or validation_mode != "single_pass":
        validation = dict(payload.get("validation") or {})
        validation["mode"] = validation_mode
        payload["validation"] = validation

    if cost_scenario != "default" or not payload.get("costs"):
        payload["costs"] = dict(_COST_SCENARIO_VALUES.get(cost_scenario, _COST_SCENARIO_VALUES["default"]))
    if benchmark is not None:
        benchmark_value = benchmark.strip().upper()
        if benchmark_value:
            payload["benchmark"] = benchmark_value
        else:
            payload.pop("benchmark", None)
    return payload


def _render_environment_state(
    prefix: str,
    selected_record: dict[str, Any] | None,
    preview: dict[str, Any] | None,
) -> None:
    spec = _current_spec_payload(selected_record)
    selected_preview = _ensure_preview(prefix, preview, **_preview_context(prefix, selected_record))
    activation = st.session_state.get(_state_key(prefix, "activation"))
    if isinstance(activation, Mapping) and activation.get("activated") is True:
        state = "live 활성"
    else:
        promotion = spec.get("promotion") if isinstance(spec.get("promotion"), Mapping) else {}
        requested = str(promotion.get("environment") or "draft").strip().lower()
        if requested == "live":
            state = "live 차단"
        elif requested in {"sandbox", "paper"}:
            state = requested
        else:
            state = "draft"
        if _strict_validation_ready(selected_preview):
            state = "검증 통과 · 활성화 전" if state == "live 차단" else state
    st.markdown(f"**상태** · `{state}` · 드래프트 편집")
    st.caption("상태 구분: 드래프트 · sandbox · live")
    if state == "live 차단":
        st.caption("실거래 상태는 엄격한 시계열 검증, provenance, 데이터 품질, 서버 승인 capability가 모두 필요합니다.")


def _parse_spec_quiet(text: object) -> dict[str, Any] | None:
    try:
        payload = json.loads(str(text or "{}"))
        if not isinstance(payload, dict):
            return None
        return StrategySpec.from_dict(payload).to_dict()
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _run_strategy(
    spec_payload: dict[str, Any],
    *,
    period: str | None,
    validation_mode: str | None,
) -> dict[str, Any]:
    try:
        result = views.strategy_studio.run_strategy_spec(
            spec_payload,
            period=period,
            validation_mode=validation_mode,
        )
        return dict(result) if isinstance(result, Mapping) else {"ok": False, "error": "전략 실행 결과 형식이 잘못되었습니다"}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "errors": [str(exc)], "diagnostics": [{"type": "run_error", "message": str(exc)}]}


def _draft_spec_from_state(prefix: str, selected_record: dict[str, Any] | None) -> dict[str, Any] | None:
    draft_key = _state_key(prefix, "draft_text")
    draft = st.session_state.get(draft_key)
    parsed = _parse_spec_quiet(draft) if draft else None
    return parsed or _current_spec_payload(selected_record)


def _render_editor_panel(
    prefix: str,
    selected_record: dict[str, Any] | None,
    preview: dict[str, Any] | None,
    *,
    mode: str,
) -> None:
    st.markdown("##### 전략 편집")
    benchmark_key = _state_key(prefix, "benchmark")
    benchmark_pending_key = _state_key(prefix, "benchmark_pending")
    if benchmark_pending_key in st.session_state:
        pending_benchmark = st.session_state.pop(benchmark_pending_key)
        if pending_benchmark:
            st.session_state[benchmark_key] = pending_benchmark
        else:
            st.session_state.pop(benchmark_key, None)
    pending_key = _state_key(prefix, "draft_text_pending")
    if pending_key in st.session_state:
        st.session_state[_state_key(prefix, "draft_text")] = st.session_state.pop(pending_key)
    controls_pending_key = _state_key(prefix, "controls_pending")
    pending_controls = st.session_state.pop(controls_pending_key, None)
    if isinstance(pending_controls, Mapping):
        for control, value in pending_controls.items():
            st.session_state[_state_key(prefix, str(control))] = str(value)
    draft_text = st.session_state.get(_state_key(prefix, "draft_text"))
    if not draft_text:
        draft_text = _spec_to_text(_current_spec_payload(selected_record))
    draft_key = _state_key(prefix, "draft_text")
    st.session_state.setdefault(draft_key, draft_text)

    draft_payload = _parse_spec_quiet(draft_text)
    control_defaults = _editor_control_defaults(draft_payload or _current_spec_payload(selected_record))
    controls = _render_editor_controls(prefix, control_defaults)

    draft_text = st.text_area(
        "전략 JSON",
        height=330,
        key=draft_key,
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

    action_cols = st.columns(4)
    if action_cols[0].button("미리보기", key=_state_key(prefix, "run_preview"), type="primary", width="stretch"):
        spec = _parse_spec_text(draft_text)
        if spec is not None:
            spec = _apply_editor_controls(spec, controls, benchmark=benchmark)
            _set_preview(prefix, _run_preview(spec, benchmark=benchmark, period=period), spec_payload=spec, controls=controls, benchmark=benchmark, period=period, version=spec.get("version"))
            _set_draft_record(prefix, spec)
            st.rerun()
    if action_cols[1].button("실행", key=_state_key(prefix, "run_strategy"), width="stretch"):
        spec = _parse_spec_text(draft_text)
        if spec is not None:
            spec = _apply_editor_controls(spec, controls, benchmark=benchmark)
            mode_to_run = str(controls["validation_mode"] or "single_pass")
            with st.spinner("전략 실행 중…"):
                _set_preview(prefix, _run_strategy(spec, period=period, validation_mode=mode_to_run), spec_payload=spec, controls=controls, benchmark=benchmark, period=period, version=spec.get("version"))
            _set_draft_record(prefix, spec)
            st.rerun()
    if action_cols[2].button("검증", key=_state_key(prefix, "validate_spec"), width="stretch"):
        spec = _parse_spec_text(draft_text)
        if spec is not None:
            spec = _apply_editor_controls(spec, controls, benchmark=benchmark)
            validation_mode = controls["validation_mode"]
            with st.spinner("시계열 검증 중…"):
                _set_preview(prefix, _run_strategy(spec, period=period, validation_mode=validation_mode), spec_payload=spec, controls=controls, benchmark=benchmark, period=period, version=spec.get("version"))
            _set_draft_record(prefix, spec)
            st.rerun()
    if action_cols[3].button("초안 저장", key=_state_key(prefix, "save_spec"), width="stretch"):
        spec = _parse_spec_text(draft_text)
        if spec is not None:
            spec = _apply_editor_controls(spec, controls, benchmark=benchmark)
            saved = _save_spec(spec)
            if saved:
                _set_draft_record(prefix, saved)
                saved_spec = _current_spec_payload(saved)
                _set_preview(prefix, _run_preview(saved_spec, benchmark=benchmark, period=period), spec_payload=saved_spec, controls=controls, benchmark=benchmark, period=period, version=saved.get("version"))
                _refresh_caches()
                st.toast("초안을 저장했습니다.")
                st.rerun()
    if st.button("초안 되돌리기", key=_state_key(prefix, "reset_spec"), width="stretch"):
        _set_draft_record(prefix, _catalog_record_or_fallback(selected_record, _safe_catalog(None), builtin_strategy_presets()))
        _set_preview(prefix, preview, **_preview_context(prefix, selected_record))
        st.rerun()

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
    selected_preview = _ensure_preview(prefix, preview, **_preview_context(prefix, selected_record))
    if not selected_preview:
        st.info("전략을 미리보기하면 수익률, 거래수, 이퀴티 곡선, 거래 표식을 한 번에 볼 수 있습니다.")
        _render_environment_actions(prefix, selected_record, None, mode=mode)
        return

    if not selected_preview.get("ok", True):
        st.error(_join_values(selected_preview.get("errors")) or str(selected_preview.get("error") or "전략 결과를 만들지 못했습니다"))
    report = selected_preview.get("report") if isinstance(selected_preview.get("report"), Mapping) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    metrics = dict(selected_preview.get("metrics")) if isinstance(selected_preview.get("metrics"), Mapping) else {}
    validation_payload = selected_preview.get("validation") if isinstance(selected_preview.get("validation"), Mapping) else {}
    if isinstance(validation_payload.get("aggregate"), Mapping):
        metrics = {**metrics, **dict(validation_payload.get("aggregate") or {})}
    if isinstance(report.get("metrics"), Mapping):
        metrics = {**metrics, **dict(report.get("metrics") or {})}

    st.markdown("##### 성과·위험")
    cols = st.columns(5)
    cols[0].metric("거래 수", str(summary.get("trade_count") or selected_preview.get("trade_count") or metrics.get("trade_count") or 0))
    cols[1].metric("순 CAGR", data.f_frac_pct_s(_first_value(summary, metrics, "net_cagr", "cagr")))
    cols[2].metric("MDD", data.f_frac_pct(_first_value(summary, metrics, "max_drawdown")))
    cols[3].metric("Sharpe", data.f_ratio(_first_value(summary, metrics, "sharpe"), 2))
    cols[4].metric("회전율 (Turnover)", data.f_frac_pct(_first_value(summary, metrics, "turnover")))

    if summary.get("name"):
        st.caption(
            f"{summary.get('name')} · {summary.get('market', '')}/{summary.get('timeframe', '')} · "
            f"기준 {summary.get('base_symbol') or '—'} · "
            f"{selected_preview.get('profile') or spec_payload.get('data_profile') or 'generic'} / "
            f"{selected_preview.get('execution_profile') or spec_payload.get('execution_profile') or 'bar'}"
        )

    execution = report.get("execution") if isinstance(report.get("execution"), Mapping) else {}
    if not execution and isinstance(selected_preview.get("execution"), Mapping):
        execution = selected_preview.get("execution") or {}
    st.markdown("##### 비용·회전율")
    execution_cols = st.columns(4)
    partial_fill_count = execution.get("partial_count", _partial_fill_count(report, selected_preview))
    execution_cols[0].metric("비용 드래그", data.f_frac_pct(_first_value(execution, summary, "cost_drag")))
    execution_cols[1].metric("노출", data.f_frac_pct(_first_value(metrics, summary, "exposure", "gross_exposure")))
    execution_cols[2].metric("부분 체결", str(partial_fill_count))
    execution_cols[3].metric("총 비용", data.f_usd(_first_value(execution, metrics, "total_cost", "cost")))
    try:
        has_partial_fills = int(partial_fill_count or 0) > 0
    except (TypeError, ValueError):
        has_partial_fills = bool(partial_fill_count)
    if has_partial_fills:
        st.warning("부분 체결 발생 · 체결 원장과 잔여 목표 비중을 함께 확인하세요.")
    st.caption(
        "결과 원장 기준 · 비용 시나리오: "
        + str(st.session_state.get(_state_key(prefix, "cost_scenario"), "default"))
    )
    benchmark = selected_preview.get("benchmark") if isinstance(selected_preview.get("benchmark"), Mapping) else report.get("benchmark")
    if isinstance(benchmark, Mapping):
        st.caption(
            f"벤치마크: {benchmark.get('symbol') or benchmark.get('name') or '—'} · "
            f"{'사용 가능' if benchmark.get('available', True) else '사용 불가'}"
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

    warnings = list(selected_preview.get("warnings") or report.get("warnings") or [])
    errors = list(selected_preview.get("errors") or report.get("errors") or [])
    if warnings:
        st.warning("경고: " + " · ".join(str(w) for w in warnings[:4]))
    if errors:
        st.error("오류: " + " · ".join(str(e) for e in errors[:4]))

    _render_validation_details(prefix, selected_preview, report, metrics)

    equity = _frame_from_payload(selected_preview.get("equity") or report.get("equity"))
    if not equity.empty:
        chart_columns = [column for column in ("nav", "gross_nav", "benchmark_nav") if column in equity.columns]
        st.plotly_chart(charts.equity_curve(equity[chart_columns] if chart_columns else equity), width="stretch", config=_chart_cfg())
        st.markdown("##### 낙폭·회전율·익스포저")
        detail_columns = [column for column in ("drawdown", "turnover", "exposure", "cost_drag") if column in equity.columns]
        if "drawdown" not in equity.columns and "nav" in equity.columns:
            equity = equity.copy()
            equity["drawdown"] = equity["nav"] / equity["nav"].cummax() - 1.0
            detail_columns.insert(0, "drawdown")
        if detail_columns:
            st.line_chart(equity[detail_columns], width="stretch")
    else:
        st.info("표시할 equity 시계열이 없습니다.")

    trades = list(selected_preview.get("trades") or report.get("trades") or [])
    if trades:
        st.markdown("##### 거래·체결")
        st.caption(f"거래 표식 {len(_trade_markers(trades))}건 · 체결 상태와 비용을 원장 그대로 표시합니다.")
        st.dataframe(pd.DataFrame(trades), hide_index=True, width="stretch", height=min(260, 44 + 32 * len(trades)))
    else:
        st.info("거래 표식이 없습니다.")
    price_payload = selected_preview.get("price_history") or report.get("price_history")
    if price_payload is not None:
        hist = _frame_from_payload(price_payload)
        base_symbol = str(summary.get("base_symbol") or spec_payload.get("base_symbol") or "").upper().strip()
        if base_symbol and not hist.empty:
            st.plotly_chart(
                charts.price_line(hist, ticker=base_symbol, trades=_trade_markers(trades), view_days=_view_days(spec_payload)),
                width="stretch",
                config=_chart_cfg(),
            )

    weights_payload = selected_preview.get("weights") or report.get("weights")
    if weights_payload:
        latest_weights = _frame_from_payload(weights_payload)
        if not latest_weights.empty:
            last_row = latest_weights.tail(1).T.reset_index()
            last_row.columns = ["종목", "비중"]
            st.dataframe(last_row, hide_index=True, width="stretch", height=min(240, 44 + 32 * len(last_row)))

    _render_environment_actions(prefix, selected_record, selected_preview, mode=mode)


def _first_value(primary: Mapping[str, Any], secondary: Mapping[str, Any], *keys: str) -> Any:
    for source in (primary, secondary):
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
    return None


def _partial_fill_count(report: Mapping[str, Any], result: Mapping[str, Any]) -> int:
    trades = result.get("trades") or report.get("trades") or []
    return sum(
        1
        for trade in trades
        if isinstance(trade, Mapping) and str(trade.get("status") or "").strip().lower() in {"partial", "partially_filled"}
    )


def _join_values(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        return " · ".join(str(item) for item in value if str(item).strip())
    return str(value or "").strip()


def _render_validation_details(
    prefix: str,
    result: dict[str, Any],
    report: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> None:
    validation = _result_mapping(result, "validation", report)
    promotion = _result_mapping(result, "promotion", report)
    data_quality = _result_mapping(result, "data_quality", report)
    provenance = _result_mapping(result, "provenance", report)
    if not provenance and isinstance(validation.get("provenance"), Mapping):
        provenance = dict(validation["provenance"])
    mode = str(result.get("validation_mode") or validation.get("validation_mode") or "single_pass")

    st.markdown("##### 검증·승격")
    if mode == "single_pass":
        st.info("single_pass 미리보기 · 실거래 승격 불가")
    elif _strict_validation_ready(result):
        st.success("엄격 검증 통과 · 서버 activation capability 확인 대기")
    else:
        failed = _join_values(promotion.get("failed_checks")) or "엄격 게이트 미충족"
        st.warning(f"승격 차단 · {failed}")
        if mode == "cpcv":
            st.warning("chronology 증거가 모든 실제 CPCV 폴드와 일치하는지 확인해야 합니다.")

    gate_rows = [
        {"게이트": "검증", "상태": "통과" if validation.get("promotion_eligible") is True else "차단"},
        {"게이트": "승격", "상태": "통과" if promotion.get("accepted") is True else "차단"},
        {"게이트": "activation safe", "상태": "통과" if promotion.get("activation_safe") is True else "차단"},
        {"게이트": "preview", "상태": "실행 결과" if promotion.get("preview") is False else "미리보기"},
    ]
    if promotion.get("failed_checks"):
        gate_rows.append({"게이트": "실패 항목", "상태": _join_values(promotion.get("failed_checks"))})
    st.dataframe(pd.DataFrame(gate_rows), hide_index=True, width="stretch", height=min(220, 44 + 32 * len(gate_rows)))

    st.markdown("##### 데이터 품질·provenance")
    quality_status = str(data_quality.get("status") or "").lower()
    if data_quality.get("stale") is True or data_quality.get("fresh") is False:
        quality_status = "stale"
    quality_status = quality_status or "unknown"
    quality_text = f"상태: {quality_status} · {'사용 가능' if data_quality.get('ok') is True else '검증 필요'}"
    if quality_status in {"stale", "unknown", "incomplete"}:
        st.warning(quality_text)
    else:
        st.caption(quality_text)
    quality_warnings = _join_values(data_quality.get("warnings"))
    if quality_warnings:
        st.caption("데이터 경고: " + quality_warnings)
    provenance_rows = _flatten_mapping(provenance)
    if provenance:
        st.caption(f"원천 provenance · {'통과' if provenance.get('ok') is True else '검증 필요'}")
    if provenance_rows:
        st.dataframe(pd.DataFrame(provenance_rows), hide_index=True, width="stretch", height=min(240, 44 + 30 * len(provenance_rows)))
    else:
        st.info("provenance 기록이 없습니다.")

    folds = result.get("folds") or validation.get("folds") or []
    st.markdown("##### 검증 폴드")
    fold_rows = []
    for fold in folds:
        if not isinstance(fold, Mapping):
            continue
        evidence = fold.get("chronology_evidence") if isinstance(fold.get("chronology_evidence"), Mapping) else {}
        fold_rows.append({
            "폴드": fold.get("path_id") or fold.get("fold_id") or fold.get("fold") or "—",
            "학습 종료": fold.get("train_max") or fold.get("train_end") or evidence.get("train_max") or "—",
            "테스트 시작": fold.get("test_min") or fold.get("test_start") or evidence.get("test_min") or "—",
            "미래 학습": fold.get("future_training", evidence.get("future_training", "—")),
            "시간순 증거": evidence.get("valid", "—"),
        })
    if fold_rows:
        st.dataframe(pd.DataFrame(fold_rows), hide_index=True, width="stretch", height=min(260, 44 + 32 * len(fold_rows)))
    else:
        st.info("검증 폴드가 없습니다.")

    diagnostics = list(result.get("diagnostics") or report.get("diagnostics") or [])
    if not diagnostics:
        diagnostics = [{"type": "promotion_failed_check", "message": item} for item in (promotion.get("failed_checks") or [])]
    st.markdown("##### 진단")
    diagnostic_text = " ".join(
        str(item.get("message") or item)
        if isinstance(item, Mapping)
        else str(item)
        for item in diagnostics
    ).lower()
    if "model" in diagnostic_text and any(token in diagnostic_text for token in ("missing", "unavailable", "provenance")):
        st.warning("모델 상태 확인 필요 · 모델 또는 모델 provenance가 없습니다.")
    if "insufficient" in diagnostic_text or "표본" in diagnostic_text:
        st.warning("표본 부족 · 통계적 검증에 필요한 관측치가 부족합니다.")
    if diagnostics:
        rows = []
        for item in diagnostics:
            if isinstance(item, Mapping):
                rows.append({"유형": item.get("type") or "diagnostic", "메시지": item.get("message") or _join_values(item)})
            else:
                rows.append({"유형": "diagnostic", "메시지": str(item)})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(260, 44 + 32 * len(rows)))
    else:
        st.caption("진단 항목이 없습니다.")

    signals = result.get("signals") if isinstance(result.get("signals"), Mapping) else report.get("signals")
    if isinstance(signals, Mapping):
        panel = signals.get("panel") if isinstance(signals.get("panel"), Mapping) else {}
        provider = panel.get("provider") or signals.get("provider")
        if provider:
            st.caption(f"신호 공급자: {provider}")


def _flatten_mapping(value: Mapping[str, Any], prefix: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, Mapping):
            rows.extend(_flatten_mapping(child, path))
        elif isinstance(child, (list, tuple)):
            rows.append({"항목": path, "값": ", ".join(str(item) for item in child)})
        else:
            rows.append({"항목": path, "값": str(child)})
    return rows


def _render_environment_actions(
    prefix: str,
    selected_record: dict[str, Any] | None,
    result: dict[str, Any] | None,
    *,
    mode: str,
) -> None:
    st.markdown("##### 환경 작업")
    draft_spec = _draft_spec_from_state(prefix, selected_record)
    spec_id = str((draft_spec or {}).get("id") or (selected_record or {}).get("id") or "").strip()
    preview_context = _preview_context(prefix, selected_record)
    bound_result = _ensure_preview(prefix, None, **preview_context)
    result = bound_result
    strict_ready = _strict_validation_ready(result)
    activation = st.session_state.get(_state_key(prefix, "activation"))
    promotion = (draft_spec or {}).get("promotion") if isinstance((draft_spec or {}).get("promotion"), Mapping) else {}
    current_environment = str(promotion.get("environment") or "draft").strip().lower()
    if isinstance(activation, Mapping) and activation.get("environment"):
        current_environment = str(activation.get("environment")).strip().lower()
    st.caption(f"현재 레일: {current_environment if current_environment in {'draft', 'sandbox', 'paper', 'live'} else 'draft'}")

    action_cols = st.columns(2)
    save_sandbox = action_cols[0].button(
        "sandbox 저장",
        key=_state_key(prefix, "save_sandbox"),
        disabled=not bool(draft_spec),
        width="stretch",
    )
    if save_sandbox and draft_spec:
        saved = _save_sandbox_spec(draft_spec, spec_id=spec_id, validation_mode=str((result or {}).get("validation_mode") or "single_pass"))
        if saved:
            _set_draft_record(prefix, saved)
            _refresh_caches()
            st.toast("sandbox 전략을 저장했습니다.")
            st.rerun()

    confirm_live = st.checkbox(
        "실거래 활성화 확인",
        key=_state_key(prefix, "confirm_live"),
        disabled=not strict_ready,
    )
    live_disabled = not (strict_ready and bool(spec_id) and confirm_live)
    activate_live = action_cols[1].button(
        "실거래 활성화",
        key=_state_key(prefix, "activate_live"),
        disabled=live_disabled,
        width="stretch",
    )
    if not strict_ready:
        st.caption("실거래 활성화 차단 · 엄격 검증과 서버 capability 승인이 필요합니다.")
    elif not spec_id:
        st.caption("실거래 활성화 차단 · 먼저 저장된 전략을 선택하거나 sandbox에 저장하세요.")

    if activate_live and spec_id:
        try:
            with st.spinner("실거래 활성화 게이트 확인 중…"):
                activation_result = views.strategy_studio.activate_strategy_spec(
                    spec_id,
                    environment="live",
                    confirm_live=True,
                    period=preview_context["period"],
                    validation_mode=(result or {}).get("validation_mode"),
                    version=(draft_spec or {}).get("version") or (selected_record or {}).get("version"),
                )
            activation_result = dict(activation_result) if isinstance(activation_result, Mapping) else {"ok": False, "error": "활성화 결과 형식이 잘못되었습니다"}
        except Exception as exc:
            activation_result = {"ok": False, "error": str(exc), "activation": {"activated": False, "warnings": [str(exc)]}}
        st.session_state[_state_key(prefix, "activation")] = activation_result.get("activation") or activation_result
        if isinstance(activation_result.get("run"), Mapping):
            _set_preview(prefix, activation_result["run"], **preview_context)
        if activation_result.get("ok") is True:
            _refresh_caches()
            st.toast("실거래 활성화가 완료되었습니다.")
        else:
            st.error(str(activation_result.get("error") or "실거래 활성화가 차단되었습니다"))
        st.rerun()


def _save_sandbox_spec(spec: dict[str, Any], *, spec_id: str, validation_mode: str) -> dict[str, Any] | None:
    sandbox_spec = deepcopy(spec)
    promotion = dict(sandbox_spec.get("promotion") or {})
    promotion["environment"] = "sandbox"
    sandbox_spec["promotion"] = promotion
    try:
        StrategySpec.from_dict(sandbox_spec)
        if spec_id:
            return views.strategy_studio.save_strategy_version(
                spec_id,
                sandbox_spec,
                patch={"parameters": {"validation": {"mode": validation_mode}}},
                source="validation_sandbox",
            )
        return views.strategy_studio.save_strategy_spec(sandbox_spec)
    except Exception as exc:
        st.error(f"sandbox 저장 실패: {exc}")
        return None


def _strict_validation_ready(result: object) -> bool:
    """Fail closed unless the public result contains complete activation evidence."""

    if not isinstance(result, Mapping) or result.get("ok") is not True:
        return False
    report = result.get("report") if isinstance(result.get("report"), Mapping) else {}
    validation = _result_mapping(result, "validation", report)
    promotion = _result_mapping(result, "promotion", report)
    data_quality = _result_mapping(result, "data_quality", report)
    provenance = _result_mapping(result, "provenance", report)
    mode_values = [
        str(value).strip().lower()
        for value in (
            result.get("validation_mode"),
            validation.get("validation_mode"),
            validation.get("mode"),
        )
        if value not in (None, "")
    ]
    if mode_values and any(value != mode_values[0] for value in mode_values[1:]):
        return False
    mode = mode_values[0] if mode_values else ""
    if mode not in {"purged_walk_forward", "cpcv"}:
        return False
    if validation.get("promotion_eligible") is not True:
        return False
    if promotion.get("accepted") is not True or promotion.get("activation_safe") is not True or promotion.get("preview") is not False:
        return False
    if promotion.get("failed_checks"):
        return False
    if data_quality.get("ok") is not True or str(data_quality.get("status") or "").strip().lower() not in {"ok", "fresh", "complete"}:
        return False
    if provenance.get("ok") is not True:
        return False
    if result.get("errors") or report.get("errors"):
        return False

    folds = result.get("folds") if isinstance(result.get("folds"), list) else validation.get("folds")
    aggregate = validation.get("aggregate") if isinstance(validation.get("aggregate"), Mapping) else {}
    fold_count = aggregate.get("fold_count")
    if (
        not isinstance(folds, list)
        or not folds
        or isinstance(fold_count, bool)
        or not isinstance(fold_count, int)
        or fold_count <= 0
        or fold_count != len(folds)
    ):
        return False
    fold_ids: set[str] = set()
    for fold in folds:
        if not isinstance(fold, Mapping):
            return False
        fold_id = _consistent_text_alias(fold, ("path_id", "fold_id", "fold"))
        if not fold_id or fold_id in fold_ids:
            return False
        fold_ids.add(fold_id)
        if mode == "cpcv" and not _strict_cpcv_fold(fold, require_proof=True):
            return False
        if mode != "cpcv" and not _strict_validation_fold(fold):
            return False

    checks = provenance.get("checks")
    if not isinstance(checks, list) or len(checks) != len(folds):
        return False
    check_ids: set[str] = set()
    for check in checks:
        if (
            not isinstance(check, Mapping)
            or check.get("ok") is not True
            or check.get("provenance_ok") is not True
        ):
            return False
        check_id = _consistent_text_alias(check, ("fold", "path_id", "fold_id"))
        if not check_id or check_id in check_ids or check_id not in fold_ids:
            return False
        check_ids.add(check_id)
    if check_ids != fold_ids:
        return False
    if mode == "cpcv":
        if aggregate.get("provenance_ok") is not True:
            return False
        if not _strict_cpcv_chronology_payload_ok(aggregate, [
            fold for fold in folds if isinstance(fold, Mapping)
        ]):
            return False
    return True


def _result_mapping(result: Mapping[str, Any], key: str, report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = result.get(key)
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(report, Mapping) and isinstance(report.get(key), Mapping):
        return dict(report[key])
    return {}


def _safe_chronology_flags(value: Mapping[str, Any], *, required: bool) -> bool:
    present = False
    if "future_training" in value:
        present = True
        if value["future_training"] is not False:
            return False
    if "no_future_training" in value:
        present = True
        if value["no_future_training"] is not True:
            return False
    return present or not required


def _strict_validation_fold(fold: Mapping[str, Any]) -> bool:
    if not _strict_cpcv_fold(fold, require_proof=False):
        return False
    return (
        _safe_boolean_aliases(fold, ("train_before_test", "train_max_before_test_min"), expected=True, required=True)
        and _safe_boolean_aliases(fold, ("valid", "proof_valid"), expected=True, required=True)
    )


def _safe_boolean_aliases(value: Mapping[str, Any], keys: tuple[str, ...], *, expected: bool, required: bool) -> bool:
    present = [value[key] for key in keys if key in value]
    if not present:
        return not required
    return all(item is expected for item in present)


def _strict_cpcv_fold(fold: Mapping[str, Any], *, require_proof: bool) -> bool:
    train = _consistent_timestamp_alias(fold, ("train_max", "train_end", "train_max_timestamp"))
    test = _consistent_timestamp_alias(fold, ("test_min", "test_start", "test_min_timestamp"))
    if train is None or test is None:
        return False
    try:
        if not pd.Timestamp(train) < pd.Timestamp(test):
            return False
    except (TypeError, ValueError):
        return False
    if not (
        fold.get("future_training") is False
        and fold.get("no_future_training") is True
    ):
        return False
    nested = fold.get("chronology_evidence")
    records = [fold]
    if nested is not None:
        if not isinstance(nested, Mapping):
            return False
        records.append(nested)

    if not any(_safe_chronology_flags(record, required=False) for record in records):
        return False
    if not any(
        _safe_boolean_aliases(record, ("train_before_test", "train_max_before_test_min"), expected=True, required=False)
        for record in records
    ):
        return False
    if not any(
        _safe_boolean_aliases(record, ("valid", "proof_valid"), expected=True, required=False)
        for record in records
    ):
        return False
    for record in records:
        if not _safe_chronology_flags(record, required=False):
            return False
        if not _safe_boolean_aliases(record, ("train_before_test", "train_max_before_test_min"), expected=True, required=False):
            return False
        if not _safe_boolean_aliases(record, ("valid", "proof_valid"), expected=True, required=False):
            return False
        record_train = _consistent_timestamp_alias(record, ("train_max", "train_end", "train_max_timestamp"))
        record_test = _consistent_timestamp_alias(record, ("test_min", "test_start", "test_min_timestamp"))
        if record_train is not None and record_train != train:
            return False
        if record_test is not None and record_test != test:
            return False
    if require_proof:
        if not isinstance(nested, Mapping):
            return False
        if not (
            nested.get("future_training") is False
            and nested.get("no_future_training") is True
            and _safe_boolean_aliases(nested, ("train_before_test", "train_max_before_test_min"), expected=True, required=True)
            and _safe_boolean_aliases(nested, ("valid", "proof_valid"), expected=True, required=True)
        ):
            return False
    return True


def _consistent_text_alias(value: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    values = [str(value[key]).strip() for key in keys if key in value and str(value[key]).strip()]
    return values[0] if values and all(item == values[0] for item in values[1:]) else None


def _consistent_bool_alias(value: Mapping[str, Any], key: str, expected: bool) -> bool:
    if key not in value:
        return False
    return value[key] is expected


def _consistent_timestamp_alias(value: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    values = [value[key] for key in keys if key in value]
    if not values or any(item is None or not str(item).strip() for item in values):
        return None
    try:
        parsed = [pd.Timestamp(item) for item in values]
        if any(pd.isna(item) for item in parsed):
            return None
        first = parsed[0]
        return first.isoformat() if all(item == first for item in parsed[1:]) else None
    except (TypeError, ValueError):
        return None


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
        # propose_strategy_patch() 가 예외를 던지면 preview 는 빈 dict {} 이고
        # 실제 오류 메시지는 current_patch["error"] (최상위)에 있음 — preview
        # 안쪽만 보면 항상 일반 메시지로 대체돼 원인이 사라짐(감사 #32).
        st.warning(preview.get("error") or current_patch.get("error") or "패치 미리보기 실패")

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
            _set_preview(prefix, patch_result.get("preview"), **_preview_context(prefix, selected_record))

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
        nested = catalog.get("catalog")
        if isinstance(nested, Mapping):
            merged = dict(nested)
            merged.setdefault("ok", catalog.get("ok", True))
            if isinstance(catalog.get("presets"), Mapping):
                merged["presets"] = dict(catalog["presets"])
            return merged
        return catalog
    try:
        loaded = cached.strategy_studio_catalog()
        if isinstance(loaded, Mapping) and isinstance(loaded.get("catalog"), Mapping):
            merged = dict(loaded["catalog"])
            merged.setdefault("ok", loaded.get("ok", True))
            return merged
        return dict(loaded) if isinstance(loaded, Mapping) else {}
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
    stored_draft = st.session_state.get(_state_key(prefix, "draft_record"))
    if isinstance(stored_draft, dict):
        normalized_draft = _normalize_record(stored_draft)
        if normalized_draft and (
            selected_spec is None or _records_match(normalized_draft, _normalize_record(selected_spec))
        ):
            return normalized_draft
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


def _records_match(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    left_id = str(left.get("id") or _current_spec_payload(left).get("id") or "").strip()
    right_id = str(right.get("id") or _current_spec_payload(right).get("id") or "").strip()
    if left_id or right_id:
        return bool(left_id and right_id and left_id == right_id)
    try:
        return strategy_spec_hash(_current_spec_payload(left)) == strategy_spec_hash(_current_spec_payload(right))
    except (TypeError, ValueError):
        return _current_spec_payload(left) == _current_spec_payload(right)


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
    st.session_state[_state_key(prefix, "controls_pending")] = _editor_control_defaults(spec)
    st.session_state[_state_key(prefix, "draft_record")] = record
    benchmark = _benchmark_for_payload(spec)
    benchmark_key = _state_key(prefix, "benchmark")
    benchmark_pending_key = _state_key(prefix, "benchmark_pending")
    try:
        if benchmark:
            st.session_state[benchmark_key] = benchmark
        else:
            st.session_state.pop(benchmark_key, None)
        st.session_state.pop(benchmark_pending_key, None)
    except StreamlitAPIException:
        st.session_state[benchmark_pending_key] = benchmark or ""


def _set_preview(
    prefix: str,
    preview: dict[str, Any] | None,
    *,
    spec_payload: dict[str, Any] | None = None,
    controls: Mapping[str, Any] | None = None,
    benchmark: str | None = None,
    period: str | None = None,
    version: object | None = None,
) -> None:
    if preview is None:
        st.session_state.pop(_state_key(prefix, "preview"), None)
        st.session_state.pop(_state_key(prefix, "preview_binding"), None)
    else:
        st.session_state[_state_key(prefix, "preview")] = preview
        if spec_payload is not None and controls is not None:
            st.session_state[_state_key(prefix, "preview_binding")] = _preview_binding(
                spec_payload, controls, benchmark=benchmark, period=period, version=version
            )
        else:
            st.session_state.pop(_state_key(prefix, "preview_binding"), None)


def _set_patch(prefix: str, patch: dict[str, Any] | None) -> None:
    if patch is None:
        st.session_state.pop(_state_key(prefix, "patch"), None)
    else:
        st.session_state[_state_key(prefix, "patch")] = patch


def _ensure_preview(
    prefix: str,
    preview: dict[str, Any] | None,
    *,
    spec_payload: dict[str, Any] | None = None,
    controls: Mapping[str, Any] | None = None,
    benchmark: str | None = None,
    period: str | None = None,
    version: object | None = None,
) -> dict[str, Any] | None:
    if preview is not None:
        _set_preview(
            prefix,
            preview,
            spec_payload=spec_payload,
            controls=controls,
            benchmark=benchmark,
            period=period,
            version=version,
        )
        return preview
    stored = st.session_state.get(_state_key(prefix, "preview"))
    if stored is not None:
        if spec_payload is not None and controls is not None:
            expected = _preview_binding(spec_payload, controls, benchmark=benchmark, period=period, version=version)
            if st.session_state.get(_state_key(prefix, "preview_binding")) != expected:
                _set_preview(prefix, None)
                return None
        return stored
    return None


def _preview_context(prefix: str, selected_record: dict[str, Any] | None) -> dict[str, Any]:
    selected_payload = _current_spec_payload(selected_record)
    stored_draft = st.session_state.get(_state_key(prefix, "draft_record"))
    pending_draft = st.session_state.get(_state_key(prefix, "draft_text_pending"))
    if isinstance(stored_draft, dict) and pending_draft is not None:
        spec_payload = _current_spec_payload(stored_draft)
    else:
        spec_payload = _draft_spec_from_state(prefix, selected_record) or selected_payload
    selected_id = str(selected_payload.get("id") or "").strip()
    draft_id = str(spec_payload.get("id") or "").strip()
    if selected_id and draft_id and selected_id != draft_id:
        spec_payload = selected_payload
    defaults = _editor_control_defaults(spec_payload)
    controls = {
        key: st.session_state.get(_state_key(prefix, key), value)
        for key, value in defaults.items()
    }
    pending_benchmark_key = _state_key(prefix, "benchmark_pending")
    if pending_benchmark_key in st.session_state:
        benchmark = str(st.session_state.get(pending_benchmark_key) or "").strip().upper()
    else:
        benchmark = st.session_state.get(_state_key(prefix, "benchmark")) or _benchmark_for_payload(spec_payload)
    period = str(st.session_state.get(_state_key(prefix, "period")) or _period_for_payload(spec_payload)).strip()
    version = spec_payload.get("version") or (selected_record or {}).get("version") or 1
    return {
        "spec_payload": spec_payload,
        "controls": controls,
        "benchmark": benchmark,
        "period": period,
        "version": version,
    }


def _preview_binding(
    spec_payload: dict[str, Any],
    controls: Mapping[str, Any],
    *,
    benchmark: str | None,
    period: str | None,
    version: object | None,
) -> dict[str, Any]:
    spec_hash = strategy_spec_hash(spec_payload)
    controls_blob = json.dumps(dict(controls), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        version_value = int(version or spec_payload.get("version") or 1)
    except (TypeError, ValueError):
        version_value = 0
    return {
        "spec_hash": spec_hash,
        "controls_hash": hashlib.sha256(controls_blob.encode("utf-8")).hexdigest(),
        "benchmark": str(benchmark or "").strip().upper(),
        "period": str(period or _period_for_payload(spec_payload)).strip(),
        "version": version_value,
    }


def _run_preview(spec_payload: dict[str, Any], *, benchmark: str | None, period: str | None) -> dict[str, Any]:
    data_watermark = _preview_data_watermark(spec_payload, period)
    try:
        return cached.strategy_studio_preview(
            spec_payload, benchmark=benchmark, period=period, data_watermark=data_watermark,
        )
    except Exception:
        return views.strategy_studio_preview(spec_payload, benchmark=benchmark, period=period)


def _preview_data_watermark(spec_payload: Mapping[str, Any], period: str | None) -> str:
    """Produce a cheap cache token that follows live-store changes."""

    profile = str((spec_payload or {}).get("data_profile") or "").strip().lower()
    timeframe = str((spec_payload or {}).get("timeframe") or "1d").strip().lower()
    if profile in {"kr_intraday", "extended_us"} or timeframe in {"1m", "5m", "15m", "1h"}:
        try:
            from providers import intraday_bars

            dates = intraday_bars.available_dates()
            if dates:
                latest = intraday_bars.bar_path(dates[-1])
                stat = latest.stat()
                return f"intraday:{dates[-1]}:{stat.st_mtime_ns}:{stat.st_size}"
        except (OSError, TypeError, ValueError):
            pass
        return f"intraday:missing:{int(time.time() // 60)}"
    # Daily/weekly/monthly provider caches are refreshed less often and do not
    # expose a single shared watermark, so a bounded time bucket prevents the
    # old one-hour stale-preview window without multiplying provider calls.
    try:
        from providers.market_data import profile_cache_watermark

        universe = (spec_payload or {}).get("universe")
        universe = universe if isinstance(universe, Mapping) else {}
        symbols = list(universe.get("symbols") or [])
        symbols.extend([
            (spec_payload or {}).get("base_symbol"),
            (spec_payload or {}).get("benchmark"),
        ])
        watermark = profile_cache_watermark(symbols, period or "1y")
        if watermark != "empty":
            return f"history:{watermark}"
    except (OSError, TypeError, ValueError):
        pass
    return f"history:{period or 'default'}:{int(time.time() // 900)}"


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
    if rows is not None:
        df = pd.DataFrame(rows)
    elif isinstance(payload.get("data"), list):
        df = pd.DataFrame(payload.get("data"), columns=payload.get("columns"))
    else:
        return pd.DataFrame()
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
    mapping = {"1d": "2y", "1wk": "5y", "1w": "5y", "1mo": "10y", "5m": "60d", "1m": "30d"}
    return mapping.get(tf, "2y")


def _view_days(spec_payload: dict[str, Any]) -> int | None:
    tf = str((spec_payload or {}).get("timeframe") or "1d").lower().strip()
    mapping = {"1d": 365, "1wk": 730, "1w": 730, "1mo": 3650, "5m": 30, "1m": 14}
    return mapping.get(tf, 365)


def _benchmark_for_payload(spec_payload: dict[str, Any]) -> str | None:
    metadata = spec_payload.get("metadata") if isinstance(spec_payload.get("metadata"), Mapping) else {}
    declared = spec_payload.get("benchmark") or metadata.get("benchmark")
    if declared:
        return str(declared).strip().upper()
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
