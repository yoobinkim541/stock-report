from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from dashboard import chart_replay

from . import agent, chart_alert_dispatcher, chart_alert_runner, chart_alert_worker, chart_alerts, context, shared_memory, storage, strategy_studio, wiki


STATIC_DIR = Path(__file__).resolve().parent / "static"


def _json_object_payload(error: str, *, allow_empty: bool = False) -> tuple[dict[str, object] | None, str | None]:
    payload = request.get_json(silent=True)
    if payload is None:
        if allow_empty and not request.data:
            return {}, None
        return None, error
    if not isinstance(payload, dict):
        return None, error
    return payload, None


def _parse_optional_version(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("version must be a positive integer")
    if isinstance(value, int):
        version = value
    elif isinstance(value, str):
        text = value.strip()
        if not text or not text.isdigit():
            raise ValueError("version must be a positive integer")
        version = int(text)
    else:
        raise ValueError("version must be a positive integer")
    if version <= 0:
        raise ValueError("version must be a positive integer")
    return version


def _payload_version(payload: dict[str, object]) -> int | None:
    return _parse_optional_version(payload["version"]) if "version" in payload else None


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
    storage.ensure_schema()

    @app.after_request
    def add_cors_headers(response):
        origin = str(request.headers.get("Origin") or "").strip()
        allowed = {
            item.strip().rstrip("/")
            for item in str(os.getenv("AGENT_CONSOLE_CORS_ORIGINS") or "http://localhost:8501,http://127.0.0.1:8501").split(",")
            if item.strip()
        }
        if origin and origin.rstrip("/") in allowed:
            response.headers.setdefault("Access-Control-Allow-Origin", origin)
            response.headers.setdefault("Vary", "Origin")
            response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
        return response

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/health")
    def health():
        return jsonify({
            "ok": True,
            "app": "stock-report-agent-console",
            "db": str(storage.db_path()),
            "reports_dir": str(context.reports_dir()),
        })

    @app.get("/api/context/overview")
    def context_overview():
        surface = request.args.get("surface", "market")
        hours = int(request.args.get("hours", "72") or 72)
        return jsonify(context.context_pack(surface, hours=hours))

    @app.post("/api/memory/ingest")
    def memory_ingest():
        payload = request.get_json(silent=True) or {}
        hours = int(payload.get("hours") or request.args.get("hours") or 72)
        return jsonify(context.ingest_recent_memory(hours=hours))

    @app.get("/api/memory")
    def shared_memory_status():
        limit = int(request.args.get("limit", "8") or 8)
        offset = int(request.args.get("offset", "0") or 0)
        return jsonify(shared_memory.status(limit=limit, offset=offset))

    @app.post("/api/memory")
    def shared_memory_add():
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "memory record object required"}), 400
        return jsonify({"ok": True, "record": shared_memory.append_record(payload)})

    @app.post("/api/memory/context")
    def shared_memory_context():
        payload = request.get_json(silent=True) or {}
        return jsonify(shared_memory.build_context_packet(payload))

    @app.delete("/api/memory")
    def shared_memory_delete():
        record_id = request.args.get("id", "")
        deleted = shared_memory.delete_record(record_id)
        return jsonify({"ok": deleted, "deleted": deleted})

    @app.get("/api/memory/events")
    def memory_events():
        limit = int(request.args.get("limit", "80") or 80)
        return jsonify({"ok": True, "events": storage.list_memory_events(limit=limit)})

    @app.post("/api/memory/events")
    def memory_add():
        payload = request.get_json(force=True)
        event = payload.get("event") if isinstance(payload, dict) else payload
        if not isinstance(event, dict):
            return jsonify({"ok": False, "error": "event object required"}), 400
        changed = storage.upsert_memory_events([event])
        return jsonify({"ok": True, "changed": changed})

    @app.get("/api/wiki/pages")
    def wiki_pages():
        query = request.args.get("query", "")
        surface = request.args.get("surface", "all")
        status = request.args.get("status", "all")
        limit = int(request.args.get("limit", "20") or 20)
        return jsonify({
            "ok": True,
            "pages": wiki.list_pages(query=query, surface=surface, status=status, limit=limit),
            "stats": wiki.stats(),
        })

    @app.get("/api/wiki/pages/<page_id>")
    def wiki_page_get(page_id: str):
        page = wiki.get_page(page_id)
        if not page:
            return jsonify({"ok": False, "error": "page not found"}), 404
        return jsonify({"ok": True, "page": page})

    @app.post("/api/wiki/pages")
    def wiki_page_upsert():
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "page object required"}), 400
        return jsonify({"ok": True, "page": wiki.upsert_page(payload)})

    @app.post("/api/wiki/capture")
    def wiki_capture():
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "payload object required"}), 400
        page = wiki.capture_from_chat(
            payload.get("question", ""),
            payload.get("answer", ""),
            surface=payload.get("surface", "market"),
            title=payload.get("title"),
            status=payload.get("status", "draft"),
            kind=payload.get("kind", "playbook"),
            tags=payload.get("tags") or [],
            source_refs=payload.get("source_refs") or [],
        )
        return jsonify({"ok": True, "page": page})

    @app.delete("/api/wiki/pages/<page_id>")
    def wiki_page_delete(page_id: str):
        deleted = wiki.delete_page(page_id)
        if not deleted:
            return jsonify({"ok": False, "error": "page not found"}), 404
        return jsonify({"ok": True, "deleted": True})

    @app.post("/api/agent/chat")
    def agent_chat():
        payload, error = _json_object_payload("chat payload object required")
        if error:
            return jsonify({"ok": False, "error": error}), 400
        assert payload is not None
        return jsonify(agent.answer(payload.get("message", ""), payload.get("surface", "market")))

    @app.get("/api/agent/context-prompt")
    def context_prompt():
        surface = request.args.get("surface", "market")
        return jsonify({"ok": True, "prompt": agent.build_context_prompt(surface)})

    @app.get("/api/portfolio-lab/scenarios")
    def scenarios():
        return jsonify({"ok": True, "scenarios": storage.list_scenarios()})

    @app.post("/api/portfolio-lab/scenarios")
    def scenario_save():
        payload = request.get_json(force=True)
        return jsonify({"ok": True, "scenario": storage.save_scenario(payload)})

    @app.get("/api/strategy-studio/specs")
    def strategy_specs():
        limit = int(request.args.get("limit", "50") or 50)
        return jsonify({"ok": True, "specs": strategy_studio.list_strategy_specs(limit=limit)})

    @app.post("/api/strategy-studio/specs")
    def strategy_spec_save():
        payload, error = _json_object_payload("spec object required")
        if error:
            return jsonify({"ok": False, "error": error}), 400
        assert payload is not None
        try:
            saved = strategy_studio.save_strategy_spec(payload)
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "spec": saved})

    @app.get("/api/strategy-studio/specs/<spec_id>")
    def strategy_spec_get(spec_id: str):
        try:
            version = _parse_optional_version(request.args.get("version"))
            spec = strategy_studio.get_strategy_spec(spec_id, version=version)
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not spec:
            return jsonify({"ok": False, "error": "strategy spec not found"}), 404
        return jsonify({"ok": True, "spec": spec})

    @app.get("/api/strategy-studio/specs/<spec_id>/versions")
    def strategy_spec_versions(spec_id: str):
        limit = int(request.args.get("limit", "20") or 20)
        return jsonify({"ok": True, "versions": strategy_studio.list_strategy_versions(spec_id, limit=limit)})

    @app.post("/api/strategy-studio/specs/<spec_id>/preview")
    def strategy_spec_preview(spec_id: str):
        payload, error = _json_object_payload("preview options object required", allow_empty=True)
        if error:
            return jsonify({"ok": False, "error": error}), 400
        assert payload is not None
        try:
            spec = strategy_studio.get_strategy_spec(spec_id, version=_payload_version(payload))
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not spec:
            return jsonify({"ok": False, "error": "strategy spec not found"}), 404
        benchmark = payload.get("benchmark") or payload.get("benchmark_symbol")
        period = payload.get("period")
        return jsonify(strategy_studio.preview_strategy_spec(spec.get("spec") or spec, benchmark=benchmark, period=period))

    @app.post("/api/strategy-studio/specs/<spec_id>/run")
    def strategy_spec_run(spec_id: str):
        try:
            payload, error = _json_object_payload("run options object required")
            if error:
                return jsonify({"ok": False, "error": error}), 400
            assert payload is not None
            spec = strategy_studio.get_strategy_spec(spec_id, version=_payload_version(payload))
            if not spec:
                return jsonify({"ok": False, "error": "strategy spec not found"}), 404
            result = strategy_studio.run_strategy_spec(
                spec.get("spec") or spec,
                period=payload.get("period"),
                validation_mode=payload.get("validation_mode") or payload.get("mode"),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/strategy-studio/specs/<spec_id>/validate")
    def strategy_spec_validate(spec_id: str):
        try:
            payload, error = _json_object_payload("validation options object required")
            if error:
                return jsonify({"ok": False, "error": error}), 400
            assert payload is not None
            spec = strategy_studio.get_strategy_spec(spec_id, version=_payload_version(payload))
            if not spec:
                return jsonify({"ok": False, "error": "strategy spec not found"}), 404
            result = strategy_studio.run_strategy_spec(
                spec.get("spec") or spec,
                period=payload.get("period"),
                validation_mode=payload.get("validation_mode") or payload.get("mode") or "purged_walk_forward",
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        if payload.get("save_sandbox") is True and result.get("ok") is True:
            sandbox_spec = dict(result.get("spec") or spec.get("spec") or spec)
            promotion = dict(sandbox_spec.get("promotion") or {})
            promotion["environment"] = "sandbox"
            sandbox_spec["promotion"] = promotion
            try:
                strategy_studio.StrategySpec.from_dict(sandbox_spec)
                result["sandbox"] = {
                    "saved": True,
                    "version": strategy_studio.save_strategy_version(
                        spec_id,
                        sandbox_spec,
                        patch={"parameters": {"validation": {"mode": result.get("validation_mode")}}},
                        source="validation_sandbox",
                    ),
                }
            except (TypeError, ValueError) as exc:
                return jsonify({"ok": False, "error": str(exc), "result": result}), 400
        else:
            result["sandbox"] = {"saved": False}
        return jsonify(result)

    @app.post("/api/strategy-studio/specs/<spec_id>/patch")
    def strategy_spec_patch(spec_id: str):
        payload, error = _json_object_payload("patch options object required")
        if error:
            return jsonify({"ok": False, "error": error}), 400
        assert payload is not None
        try:
            spec = strategy_studio.get_strategy_spec(spec_id, version=_payload_version(payload))
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not spec:
            return jsonify({"ok": False, "error": "strategy spec not found"}), 404
        context_payload = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        context_payload = {
            **context_payload,
            "period": payload.get("period") or context_payload.get("period"),
            "validation": context_payload.get("validation") or {},
            "data_quality": context_payload.get("data_quality") or {},
        }
        result = strategy_studio.propose_strategy_patch_with_llm(
            str(payload.get("question") or ""),
            spec.get("spec") or spec,
            context_payload,
        )
        if result.get("ok") is True:
            current_payload = spec.get("spec") or spec
            patch = result.get("patch")
            patch_errors = strategy_studio.validate_strategy_patch(
                patch if isinstance(patch, dict) else {},
                current_payload,
            )
            try:
                supplied_spec = strategy_studio.StrategySpec.from_dict(result.get("patched_spec") or {}).to_dict()
                if not patch_errors and isinstance(patch, dict):
                    controlled_patch, shape_errors = strategy_studio._controlled_spec_patch(patch)
                    patch_errors.extend(shape_errors)
                    expected_spec = strategy_studio.apply_strategy_patch(current_payload, controlled_patch)
                    if supplied_spec != expected_spec:
                        patch_errors.append("patched strategy spec does not match the allowlisted patch")
            except (TypeError, ValueError) as exc:
                patch_errors.append(f"patched strategy spec is invalid: {exc}")
            if patch_errors:
                result = {
                    **result,
                    "ok": False,
                    "error": "patch_rejected",
                    "errors": list(dict.fromkeys(patch_errors)),
                    "diagnostics": [
                        {"type": "patch_rejected", "message": item}
                        for item in dict.fromkeys(patch_errors)
                    ],
                    "sandbox": {"saved": False},
                }
        if payload.get("save_sandbox") is True and result.get("ok") is True:
            sandbox_spec = dict(result.get("patched_spec") or {})
            promotion = dict(sandbox_spec.get("promotion") or {})
            promotion["environment"] = "sandbox"
            sandbox_spec["promotion"] = promotion
            try:
                strategy_studio.StrategySpec.from_dict(sandbox_spec)
                result["sandbox"] = {
                    "saved": True,
                    "version": strategy_studio.save_strategy_version(
                        spec_id,
                        sandbox_spec,
                        patch=result.get("patch") if isinstance(result.get("patch"), dict) else {},
                        source="ai_sandbox",
                    ),
                }
            except (TypeError, ValueError) as exc:
                return jsonify({"ok": False, "error": str(exc), "result": result}), 400
        else:
            result["sandbox"] = {"saved": False}
        status = 400 if not result.get("ok") and result.get("error") in {"patch_rejected", "invalid_llm_patch"} else 200
        return jsonify(result), status

    @app.post("/api/strategy-studio/specs/<spec_id>/patch-preview")
    def strategy_spec_patch_preview(spec_id: str):
        payload, error = _json_object_payload("patch preview options object required", allow_empty=True)
        if error:
            return jsonify({"ok": False, "error": error}), 400
        assert payload is not None
        try:
            spec = strategy_studio.get_strategy_spec(spec_id, version=_payload_version(payload))
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not spec:
            return jsonify({"ok": False, "error": "strategy spec not found"}), 404
        question = str(payload.get("question") or "").strip()
        history = payload.get("history") if isinstance(payload.get("history"), list) else []
        pack = payload.get("pack") if isinstance(payload.get("pack"), dict) else {}
        if payload.get("structured") is True or payload.get("use_llm") is True:
            context_payload = {
                **pack,
                "history": history,
                "period": payload.get("period") or pack.get("period"),
            }
            return jsonify(strategy_studio.propose_strategy_patch_with_llm(
                question,
                spec.get("spec") or spec,
                context_payload,
            ))
        return jsonify(strategy_studio.propose_strategy_patch(question, spec.get("spec") or spec, history=history, pack=pack))

    @app.post("/api/strategy-studio/specs/<spec_id>/activate")
    def strategy_spec_activate(spec_id: str):
        try:
            payload, error = _json_object_payload("activation options object required")
            if error:
                return jsonify({"ok": False, "error": error}), 400
            assert payload is not None
            result = strategy_studio.activate_strategy_spec(
                spec_id,
                environment=payload.get("environment"),
                confirm_live=payload.get("confirm_live", False),
                period=payload.get("period"),
                validation_mode=payload.get("validation_mode") or payload.get("mode"),
                version=_payload_version(payload),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if result.get("error") == "strategy spec not found":
            return jsonify(result), 404
        if result.get("ok") is True:
            return jsonify(result)
        activation = result.get("activation") if isinstance(result.get("activation"), dict) else {}
        status = 400 if "confirm_live" in (activation.get("failed_checks") or []) or "activation_environment" in (activation.get("failed_checks") or []) else 409
        return jsonify(result), status

    @app.get("/api/chart-workspaces/<workspace_id>/drawings")
    def chart_workspace_drawing_get(workspace_id: str):
        store_key = str(request.args.get("store_key") or "").strip()
        if not store_key:
            return jsonify({"ok": False, "error": "store_key is required"}), 400
        snapshot = storage.get_chart_drawing_snapshot(workspace_id, store_key)
        if not snapshot:
            return jsonify({"ok": False, "error": "drawing snapshot not found", "snapshot": None}), 404
        return jsonify({"ok": True, "snapshot": snapshot})

    @app.get("/api/chart-workspaces/<workspace_id>/drawings/list")
    def chart_workspace_drawing_list(workspace_id: str):
        limit = int(request.args.get("limit", "50") or 50)
        return jsonify({
            "ok": True,
            "snapshots": storage.list_chart_drawing_snapshots(workspace_id, limit=limit),
        })

    @app.post("/api/chart-workspaces/<workspace_id>/drawings")
    def chart_workspace_drawing_save(workspace_id: str):
        payload = request.get_json(silent=True) or {}
        store_key = str(payload.get("store_key") or "").strip()
        drawing = payload.get("drawing")
        if not store_key:
            return jsonify({"ok": False, "error": "store_key is required"}), 400
        if not isinstance(drawing, dict):
            return jsonify({"ok": False, "error": "drawing object required"}), 400
        try:
            snapshot = storage.save_chart_drawing_snapshot(
                workspace_id,
                store_key,
                drawing,
                source=str(payload.get("source") or "browser"),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "snapshot": snapshot})

    @app.get("/api/chart-alerts/rules")
    def chart_alert_rules():
        limit = int(request.args.get("limit", "50") or 50)
        workspace_id = request.args.get("workspace_id")
        symbol = request.args.get("symbol")
        enabled_arg = request.args.get("enabled")
        enabled = None
        if enabled_arg is not None:
            enabled = str(enabled_arg).strip().lower() not in {"0", "false", "no", "off"}
        return jsonify({
            "ok": True,
            "rules": storage.list_chart_alert_rules(
                workspace_id=workspace_id,
                symbol=symbol,
                enabled=enabled,
                limit=limit,
            ),
        })

    @app.get("/api/chart-alerts/runs")
    def chart_alert_runs():
        limit = int(request.args.get("limit", "20") or 20)
        workspace_id = request.args.get("workspace_id")
        return jsonify({
            "ok": True,
            "runs": storage.list_chart_alert_runs(workspace_id=workspace_id, limit=limit),
        })

    @app.post("/api/chart-alerts/run")
    def chart_alert_run_now():
        payload = request.get_json(silent=True) or {}
        symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
        result = chart_alert_worker.run_chart_alert_cycle(
            workspace_id=str(payload.get("workspace_id") or "").strip() or None,
            symbols=[str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()],
            notify=bool(payload.get("notify")),
            limit=int(payload.get("limit") or 200),
        )
        return jsonify(result)

    @app.post("/api/chart-alerts/rules")
    def chart_alert_rule_save():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "alert rule object required"}), 400
        try:
            rule = storage.save_chart_alert_rule(payload)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "rule": rule})

    @app.post("/api/chart-alerts/rules/<rule_id>/evaluate")
    def chart_alert_rule_evaluate(rule_id: str):
        payload = request.get_json(silent=True) or {}
        rule = storage.get_chart_alert_rule(rule_id)
        if not rule:
            return jsonify({"ok": False, "error": "chart alert rule not found"}), 404
        result = chart_alerts.evaluate_chart_alert(
            rule,
            previous_price=payload.get("previous_price"),
            current_price=payload.get("current_price"),
            previous_values=payload.get("previous_values") if isinstance(payload.get("previous_values"), dict) else {},
            current_values=payload.get("current_values") if isinstance(payload.get("current_values"), dict) else {},
            as_of=payload.get("as_of"),
        )
        if result.get("triggered"):
            storage.update_chart_alert_state(rule_id, {
                "triggered": True,
                "event": result.get("event"),
                "last_price": payload.get("current_price"),
                "last_checked_at": payload.get("as_of"),
            })
        return jsonify({"ok": True, **result})

    @app.post("/api/chart-alerts/evaluate-batch")
    def chart_alert_batch_evaluate():
        payload = request.get_json(silent=True) or {}
        workspace_id = str(payload.get("workspace_id") or "").strip() or None
        symbol = str(payload.get("symbol") or "").strip().upper() or None
        bars = payload.get("bars") if isinstance(payload.get("bars"), dict) else {}
        rules = storage.list_chart_alert_rules(
            workspace_id=workspace_id,
            symbol=symbol,
            enabled=True,
            limit=int(payload.get("limit") or 200),
        )
        evaluations: list[dict] = []
        events = chart_alert_runner.evaluate_alert_rules(
            rules, bars, as_of=payload.get("as_of"), state_sink=evaluations,
        )
        for state in evaluations:
            alert_id = str(state.get("rule_id") or "").strip()
            if alert_id:
                storage.update_chart_alert_state(alert_id, state)
        notification = {"attempted": 0, "delivered": 0, "failed": 0, "failures": []}
        if bool(payload.get("notify")) and events:
            notification = chart_alert_dispatcher.dispatch_alert_events(events)
        return jsonify({
            "ok": True,
            "event_count": len(events),
            "events": events,
            "evaluations": evaluations,
            "notification": notification,
        })

    @app.get("/api/chart-replay/sessions")
    def chart_replay_session_list():
        return jsonify({
            "ok": True,
            "sessions": storage.list_chart_replay_sessions(
                workspace_id=request.args.get("workspace_id"),
                limit=int(request.args.get("limit", "50") or 50),
            ),
        })

    @app.get("/api/chart-replay/sessions/<session_id>")
    def chart_replay_session_get(session_id: str):
        replay = storage.get_chart_replay_session(session_id)
        if replay is None:
            return jsonify({"ok": False, "error": "replay session not found"}), 404
        return jsonify({"ok": True, "replay": replay})

    @app.post("/api/chart-replay/sessions")
    def chart_replay_session_save():
        payload = request.get_json(silent=True) or {}
        session = payload.get("session")
        if not isinstance(session, dict):
            return jsonify({"ok": False, "error": "session object required"}), 400
        try:
            replay = storage.save_chart_replay_session(
                session,
                workspace_id=str(payload.get("workspace_id") or ""),
                expected_revision=payload.get("expected_revision"),
                request_id=payload.get("request_id"),
            )
        except storage.ReplayRevisionConflict as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "replay": replay})

    @app.post("/api/chart-replay/sessions/<session_id>/branch")
    def chart_replay_session_branch(session_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            replay = storage.branch_chart_replay_session(
                session_id,
                cursor=int(payload.get("cursor", -1)),
                session_id=str(payload.get("session_id") or "").strip() or None,
            )
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "replay": replay})

    @app.delete("/api/chart-replay/sessions/<session_id>")
    def chart_replay_session_delete(session_id: str):
        deleted = storage.delete_chart_replay_session(session_id)
        return jsonify({"ok": True, "deleted": deleted})

    @app.post("/api/chart-replay/sessions/<session_id>/orders/<order_id>/price")
    def chart_replay_order_price_patch(session_id: str, order_id: str):
        payload = request.get_json(silent=True) or {}
        replay = storage.get_chart_replay_session(session_id)
        if replay is None:
            return jsonify({"ok": False, "error": "replay session not found"}), 404
        try:
            preview = chart_replay.preview_order_price_patch(
                replay["session"], order_id, payload.get("price"),
            )
            if bool(payload.get("preview_only")):
                return jsonify({"ok": True, "preview": preview, "revision": replay["revision"]})
            if payload.get("expected_revision") is None:
                return jsonify({"ok": False, "error": "expected_revision is required"}), 400
            patched = chart_replay.apply_order_price_patch(
                replay["session"], order_id, payload.get("price"),
            )
            saved = storage.save_chart_replay_session(
                patched,
                workspace_id=replay["workspace_id"],
                expected_revision=int(payload["expected_revision"]),
                request_id=payload.get("request_id"),
                request_fingerprint={
                    "operation": "order_price_patch",
                    "order_id": order_id,
                    "price": preview["after"],
                },
            )
        except storage.ReplayRevisionConflict as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "preview": preview, "replay": saved})

    @app.get("/api/local-install-prompt")
    def local_install_prompt():
        path = Path(__file__).resolve().parent.parent / "docs" / "local-agent-console-install-prompt.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return jsonify({"ok": True, "prompt": text})

    return app


def main() -> int:
    host = os.getenv("AGENT_CONSOLE_HOST", "127.0.0.1")
    port = int(os.getenv("AGENT_CONSOLE_PORT", "8797"))
    app = create_app()
    app.run(host=host, port=port, debug=os.getenv("AGENT_CONSOLE_DEBUG", "0") == "1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
