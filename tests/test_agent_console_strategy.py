"""agent_console — Strategy Studio API·검증·활성화 테스트."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from agent_console_support import _isolate

def test_strategy_profile_health_replays_direct_microstructure_snapshot(monkeypatch):
    from agent_console import context

    monkeypatch.setattr(
        context,
        "_latest_intraday_bar_timestamp",
        lambda: (_ for _ in ()).throw(AssertionError("direct snapshot should be used")),
    )

    health = context.strategy_profile_health(
        snapshot={
            "profile": "kr_intraday",
            "profile_health": {
                "status": "pause",
                "reason": "stale_intraday_bar",
                "age_seconds": 330.0,
            },
        },
        now="2026-08-28T10:05:30+09:00",
    )

    assert health["kr_intraday"]["status"] == "pause"
    assert health["kr_intraday"]["reason"] == "stale_intraday_bar"


def test_strategy_studio_version_store_round_trips(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage

    spec = {
        "name": "EMA trend",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [{"name": "ema_fast", "kind": "ema", "period": 20, "source": "close"}],
        "rules": {"entry": [{"field": "close", "op": ">", "ref": "ema_fast"}], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 1},
        "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass", "min_trades": 1},
    }

    created = storage.save_strategy_spec(spec)
    assert created["name"] == "EMA trend"
    fetched = storage.get_strategy_spec(created["id"])
    assert fetched["spec"]["name"] == "EMA trend"
    assert storage.list_strategy_specs()[0]["id"] == created["id"]
    versions = storage.list_strategy_versions(created["id"])
    assert versions[0]["version"] == 1

    updated = storage.save_strategy_version(created["id"], {**spec, "sizing": {"type": "fixed_pct", "position_pct": 0.5}})
    assert updated["version"] == 2
    assert storage.list_strategy_versions(created["id"])[0]["version"] == 2

    reverted = storage.revert_strategy_version(created["id"], 1)
    assert reverted["version"] == 3
    assert storage.list_strategy_versions(created["id"])[0]["version"] == 3


def test_strategy_version_save_is_serialized_under_concurrency(monkeypatch, tmp_path):
    """동시 저장이 같은 version 을 골라 UNIQUE 충돌하던 경쟁 — 조회+INSERT 단일 쓰기 트랜잭션."""
    import threading
    _isolate(monkeypatch, tmp_path)
    from agent_console import storage
    spec = {
        "name": "race", "market": "us", "timeframe": "1d", "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [{"name": "ema_fast", "kind": "ema", "period": 20, "source": "close"}],
        "rules": {"entry": [{"field": "close", "op": ">", "ref": "ema_fast"}], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "costs": {"fees_bps": 5}, "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass", "min_trades": 1},
    }
    created = storage.save_strategy_spec(spec)
    errors, barrier = [], threading.Barrier(6)

    def w(i):
        barrier.wait()
        try:
            storage.save_strategy_version(created["id"], {**spec, "sizing": {"type": "fixed_pct", "position_pct": 0.1 * (i + 1)}})
        except Exception as e:
            errors.append(e)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert [v["version"] for v in storage.list_strategy_versions(created["id"])] == [7, 6, 5, 4, 3, 2, 1]


def test_strategy_studio_api_routes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "preview_strategy_spec", lambda *args, **kwargs: {
        "ok": True,
        "report": {"summary": {"name": "EMA trend", "trade_count": 4}},
        "warnings": [],
    })
    monkeypatch.setattr(strategy_studio, "propose_strategy_patch", lambda *args, **kwargs: {
        "ok": True,
        "patch": {"rules": {"exit": [{"field": "close", "op": "<", "ref": "ema_fast"}]}},
        "diff": [{"path": "rules.exit[0].field", "before": None, "after": "close"}],
        "preview": {"ok": True},
    })

    app = create_app()
    client = app.test_client()
    spec = {
        "name": "EMA trend",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [{"name": "ema_fast", "kind": "ema", "period": 20, "source": "close"}],
        "rules": {"entry": [{"field": "close", "op": ">", "ref": "ema_fast"}], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 1},
        "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass", "min_trades": 1},
    }

    saved = client.post("/api/strategy-studio/specs", json=spec).json["spec"]
    assert saved["name"] == "EMA trend"
    listing = client.get("/api/strategy-studio/specs").json["specs"]
    assert listing[0]["name"] == "EMA trend"
    preview = client.post(f"/api/strategy-studio/specs/{saved['id']}/preview", json={}).json
    assert preview["ok"] is True
    patch_preview = client.post(f"/api/strategy-studio/specs/{saved['id']}/patch-preview", json={"question": "손절을 ATR로 바꿔줘"}).json
    assert patch_preview["ok"] is True
    assert patch_preview["patch"]["rules"]["exit"][0]["op"] == "<"


def test_strategy_review_contract_precedes_asset_opinion(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    pack = {
        "surface": "market",
        "sources": {"events": []},
        "memory": [],
        "portfolio": {"holdings": []},
        "paper": {},
        "models": {"items": []},
        "market_snapshot": {"quotes": []},
    }
    prompts = []
    monkeypatch.setattr(
        agent,
        "_try_llm_prompt",
        lambda prompt, **kwargs: prompts.append(prompt) or "전략 검토 답변",
    )
    monkeypatch.setattr(
        agent,
        "_compose_asset_opinion_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("asset opinion route used")),
    )

    assert agent._compose_answer("QQQ 매매 전략 검토해줘", pack, history=[]) == "전략 검토 답변"
    assert len(prompts) == 1
    assert "intent: strategy_review" in prompts[0]


def test_strategy_logic_questions_keep_dedicated_rules_composer(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    monkeypatch.setattr(agent, "_compose_trading_logic_answer", lambda *args, **kwargs: "rules strategy report")
    monkeypatch.setattr(
        agent,
        "_compose_general_chat_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("general strategy route used")),
    )

    assert agent._compose_answer("모의투자 단기투자 로직 평가해줘", {}, history=[]) == "rules strategy report"


def test_strategy_validate_route_returns_validation_and_promotion(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1", "spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "run_strategy_spec", lambda *args, **kwargs: {
        "ok": True, "run_id": "run-1", "validation": {"promotion_eligible": False},
        "promotion": {"accepted": False, "activation_safe": False},
    })

    response = create_app().test_client().post(
        "/api/strategy-studio/specs/spec-1/validate",
        json={"mode": "purged_walk_forward"},
    )

    assert response.status_code == 200
    assert response.get_json()["validation"]["promotion_eligible"] is False


def test_strategy_run_route_returns_json_safe_adapter_result(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1", "spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "run_strategy_spec", lambda *args, **kwargs: {
        "ok": True,
        "run_id": "run-1",
        "validation_mode": "single_pass",
        "validation": {"promotion_eligible": False},
        "promotion": {"accepted": False, "activation_safe": False},
        "diagnostics": [{"type": "data_quality", "message": "unknown"}],
    })

    response = create_app().test_client().post(
        "/api/strategy-studio/specs/spec-1/run",
        json={"mode": "single_pass", "period": "1y"},
    )

    assert response.status_code == 200
    assert response.get_json()["run_id"] == "run-1"
    assert response.get_json()["diagnostics"][0]["type"] == "data_quality"


def test_strategy_run_adapter_is_json_safe_and_exposes_diagnostics(monkeypatch):
    from agent_console import strategy_studio

    monkeypatch.setattr(strategy_studio, "preview_strategy_spec", lambda *args, **kwargs: {
        "ok": True,
        "run_id": "run-1",
        "report": {
            "validation": {"promotion_eligible": False, "warnings": ["preview"]},
            "promotion": {"accepted": False, "failed_checks": ["preview_only"]},
            "equity": pd.DataFrame({"nav": [100.0, 101.0]}),
        },
        "warnings": ["preview"],
        "errors": [],
    })

    result = strategy_studio.run_strategy_spec(
        {"name": "test", "base_symbol": "AAPL"},
        period="1y",
        validation_mode="single_pass",
    )

    json.dumps(result)
    assert result["validation"]["promotion_eligible"] is False
    assert result["promotion"]["failed_checks"] == ["preview_only"]
    assert result["data_quality"]["status"] == "unknown"
    assert result["diagnostics"]


def test_live_activation_requires_explicit_confirmation_before_running(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1", "version": 1, "spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "run_strategy_spec", lambda *args, **kwargs: pytest.fail("run must not execute"))
    monkeypatch.setattr(strategy_studio, "save_strategy_version", lambda *args, **kwargs: pytest.fail("save must not execute"))

    response = create_app().test_client().post(
        "/api/strategy-studio/specs/spec-1/activate",
        json={"environment": "live", "confirm_live": False},
    )

    assert response.status_code == 400
    assert "confirm_live" in response.get_json()["activation"]["failed_checks"]


def test_live_activation_blocks_incomplete_provenance_and_does_not_save(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1", "version": 1, "spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "_run_strategy_spec_internal", lambda *args, **kwargs: ({
        "ok": True,
        "validation": {
            "validation_mode": "purged_walk_forward",
            "promotion_eligible": True,
            "aggregate": {"provenance_ok": True},
            "provenance": {"ok": False, "checks": []},
        },
        "promotion": {"accepted": True, "activation_safe": True, "preview": False, "failed_checks": []},
    }, None))
    monkeypatch.setattr(strategy_studio, "save_strategy_version", lambda *args, **kwargs: pytest.fail("save must not execute"))

    response = create_app().test_client().post(
        "/api/strategy-studio/specs/spec-1/activate",
        json={"environment": "live", "confirm_live": True},
    )

    assert response.status_code == 409
    assert "provenance" in response.get_json()["activation"]["failed_checks"]


def test_live_activation_saves_a_separate_explicit_version_after_all_gates(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app
    from ml.strategy_studio import StrategyRun
    import numpy as np

    provenance = {
        "data": {
            "source": "test-feed",
            "version": "2026-01-01",
            "as_of": "2026-01-01T00:00:00Z",
            "status": "complete",
        },
        "model": {
            "model_id": "model-1",
            "feature_version": "features-1",
            "model_version": "model-1",
            "code_commit": "commit-1",
            "feature_names": ["close"],
            "metrics": {"score": 1.0},
            "train_start": "2025-12-01T00:00:00Z",
            "train_end": "2025-12-31T00:00:00Z",
            "as_of": "2026-01-01T00:00:00Z",
            "status": "complete",
            "provenance_status": "complete",
            "seed": 1,
        },
    }
    prices = pd.DataFrame(
        {"AAPL__close": np.linspace(100.0, 110.0, 80)},
        index=pd.date_range("2026-01-01", periods=80, freq="D", tz="UTC"),
    )
    prices.attrs["provenance"] = provenance
    prices.attrs["data_quality"] = {"status": "complete", "ok": True}
    monkeypatch.setattr(strategy_studio, "_load_prices", lambda *args, **kwargs: prices)
    monkeypatch.setattr(
        strategy_studio,
        "preview_strategy_spec",
        lambda *args, **kwargs: pytest.fail("live activation must use full validation"),
    )

    def validated_fold(spec, fold_prices, **kwargs):
        offset = validated_fold.calls * 50
        validated_fold.calls += 1
        index = pd.date_range("2026-03-01", periods=40, freq="D", tz="UTC") + pd.Timedelta(days=offset)
        returns = 0.001 + (np.arange(40) % 5) * 0.0001
        equity = pd.DataFrame(
            {
                "net_return": returns,
                "gross_return": returns + 0.0001,
                "cost_drag": [0.0001] * 40,
                "turnover": [0.01] * 40,
            },
            index=index,
        )
        return StrategyRun(
            ok=True,
            spec=dict(spec),
            metrics={
                "trade_count": 20,
                "turnover": 0.01,
                "cost_drag": 0.001,
                "regime_concentration": 0.2,
                "tested_configurations": 2,
                "configuration_returns": np.column_stack((returns, returns * 0.9)),
            },
            trades=[{"pnl": 0.01}] * 20,
            equity=equity,
            benchmark={"symbol": "AAPL", "available": True, "metrics": {"net_cagr": 0.0}},
            signals={"provenance": provenance},
        )

    validated_fold.calls = 0
    monkeypatch.setattr(strategy_studio, "run_strategy_backtest", validated_fold)

    draft = strategy_studio.save_strategy_spec({
        "name": "test",
        "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "validation": {
            "mode": "purged_walk_forward",
            "label_horizon": 0,
            "train_bars": 20,
            "test_bars": 10,
            "step_bars": 10,
            "embargo_bars": 0,
            "min_trades": 1,
            "min_test_periods": 1,
            "min_observations": 30,
            "max_drawdown": 0.25,
            "max_turnover": 1.0,
            "max_pbo": 1.0,
            "min_dsr": 0.0,
            "max_regime_concentration": 0.75,
            "max_data_age_seconds": 31536000,
            "max_model_age_seconds": 31536000,
        },
    })
    spec_id = draft["id"]

    response = create_app().test_client().post(
        f"/api/strategy-studio/specs/{spec_id}/activate",
        json={"environment": "live", "confirm_live": True},
    )

    assert response.status_code == 200
    assert response.get_json()["activation"]["activated"] is True
    assert any(
        version["source"] == "live_activation"
        for version in strategy_studio.list_strategy_versions(spec_id)
    )


def test_strategy_routes_reject_invalid_versions_and_non_object_json(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: pytest.fail("invalid version must stop before lookup"))
    client = create_app().test_client()

    for response in (
        client.get("/api/strategy-studio/specs/spec-1?version=0"),
        client.get("/api/strategy-studio/specs/spec-1?version=not-a-number"),
        client.post("/api/strategy-studio/specs/spec-1/run", json=[]),
        client.post("/api/strategy-studio/specs/spec-1/validate", data="null", content_type="application/json"),
        client.post("/api/agent/chat", json=[]),
    ):
        assert response.status_code == 400
        assert response.is_json
        assert response.get_json()["ok"] is False
