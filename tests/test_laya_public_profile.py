"""Public checkpoints are inference-ready, never domain-validated profiles."""
import json
import sys

import pytest

from agent_console import laya_bridge


@pytest.fixture(autouse=True)
def clean_bridge(monkeypatch):
    for key in tuple(laya_bridge.os.environ):
        if key.startswith("LAYA_"):
            monkeypatch.delenv(key)
    laya_bridge.close()
    yield
    laya_bridge.close()


def configure_public(tmp_path, monkeypatch):
    script = tmp_path / "laya_public_worker.py"
    script.write_text("# fixture, not executed\n")
    model = tmp_path / "model"
    for name in ("model.safetensors", "rl_agent_config.json", "encoder/config.json",
                 "tokenizer/tokenizer.json", "tokenizer/tokenizer_config.json"):
        path = model / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    monkeypatch.setenv("LAYA_WIKI_ENABLED", "1")
    monkeypatch.setenv("LAYA_WIKI_PROFILE_VALIDATED", "1")
    monkeypatch.setenv("LAYA_MODEL_WORKER_COMMAND", json.dumps(
        [sys.executable, str(script), "--model", str(model), "--device", "cpu"]))
    return model


def test_public_checkpoint_reports_real_asset_readiness_without_private_head(tmp_path, monkeypatch):
    configure_public(tmp_path, monkeypatch)
    status = laya_bridge.readiness()
    assert status["assets_status"] == "present"
    assert status["head_status"] == "public_checkpoint"
    assert status["model_root_status"] == "public_assets_present"
    assert status["profile"] == "public_multilingual"
    assert status["ready"] is False
    assert status["error_code"] is None


def test_public_base_cannot_be_marked_validated_by_generic_environment_flag(tmp_path, monkeypatch):
    configure_public(tmp_path, monkeypatch)
    calls = []

    def transaction(config, request):
        calls.append(config)
        return "p2", {"p1": 0.01, "p2": 0.99}, 0.99, {}

    monkeypatch.setattr(laya_bridge._worker, "transaction", transaction)
    pages = [{"id": "p1", "title": "Rates"}, {"id": "p2", "title": "Domestic ETF"}]
    result, review = laya_bridge.review_wiki_candidates("domestic ETF?", pages)
    assert result == pages
    assert calls and calls[0].validated is False
    assert review["mode"] == "shadow"
    assert review["profile"] == "public_multilingual"
    assert review["selected_id"] == "p2"


def test_remote_public_handshake_stays_shadow_under_generic_ssh_command(monkeypatch):
    monkeypatch.setenv("LAYA_WIKI_ENABLED", "1")
    monkeypatch.setenv("LAYA_WIKI_PROFILE_VALIDATED", "1")
    monkeypatch.setenv("LAYA_MODEL_WORKER_COMMAND", '["ssh", "trusted-desktop", "bounded-command"]')

    def transaction(config, request):
        metadata = {"profile": "public_multilingual", "device": "cuda:0"}
        laya_bridge._worker.metadata = dict(metadata)
        laya_bridge.close()
        return "p2", {"p1": 0.01, "p2": 0.99}, 0.99, metadata

    monkeypatch.setattr(laya_bridge._worker, "transaction", transaction)
    pages = [{"id": "p1", "title": "Rates"}, {"id": "p2", "title": "Domestic ETF"}]
    result, review = laya_bridge.review_wiki_candidates("ETF?", pages)
    assert result == pages
    assert review["mode"] == "shadow"
    assert review["configured_policy"] == "shadow"
    assert review["profile"] == "public_multilingual"
    assert review["device"] == "cuda:0"


def test_remote_split_argv_does_not_check_remote_assets_locally(monkeypatch):
    monkeypatch.setenv("LAYA_WIKI_ENABLED", "1")
    monkeypatch.setenv("LAYA_MODEL_WORKER_COMMAND", json.dumps(
        ["ssh", "trusted-desktop", "/remote/python", "/remote/laya_public_worker.py",
         "--model", "/remote/model"]))
    config = laya_bridge._configuration()
    assert config.error is None
    assert config.assets_status == "unknown"
    assert config.model_root_status == "command_configured"


def test_transaction_retains_handshake_snapshot_after_close(monkeypatch):
    code = """
import json, sys
print(json.dumps({'ready': True, 'backend': 'laya', 'profile': 'public_multilingual'}), flush=True)
for line in sys.stdin:
    print(json.dumps({'answers': {'action': {'choice': 'p2',
        'probabilities': {'p1': 0.01, 'p2': 0.99}, 'confidence': 0.99}}}), flush=True)
"""
    monkeypatch.setenv("LAYA_WIKI_ENABLED", "1")
    monkeypatch.setenv("LAYA_MODEL_WORKER_COMMAND", json.dumps([sys.executable, "-u", "-c", code]))
    config = laya_bridge._configuration()
    request = {"questions": {"action": {"criteria": {"p1": "rates", "p2": "ETF"}}}}
    chosen, probabilities, confidence, metadata = laya_bridge._worker.transaction(config, request)
    laya_bridge.close()
    assert laya_bridge._worker.metadata == {}
    assert metadata == {"profile": "public_multilingual"}
    assert (chosen, probabilities[chosen], confidence) == ("p2", 0.99, 0.99)


@pytest.mark.parametrize("missing", ["model.safetensors", "encoder/config.json",
                                     "rl_agent_config.json", "tokenizer/tokenizer.json"])
def test_public_snapshot_missing_assets_never_starts(tmp_path, monkeypatch, missing):
    model = configure_public(tmp_path, monkeypatch)
    (model / missing).unlink()
    monkeypatch.setattr(laya_bridge.subprocess, "Popen", lambda *a, **kw: pytest.fail("started"))
    status = laya_bridge.readiness(start=True)
    assert status["assets_status"] == "missing"
    assert status["ready"] is False
    assert status["error_code"] == "model_assets_missing"


def test_public_sdk_entropy_confidence_is_not_selection_probability():
    response = {"answers": {"action": {"type": "choice", "choice": "crypto",
        "probabilities": {"etf": 0.0268, "crypto": 0.8975, "rates": 0.0757},
        "confidence": 0.6455, "answer_confidence": 0.8975}},
        "usage": {"truncated": False, "state_tokens_dropped": 0}}
    chosen, probabilities, confidence = laya_bridge._selection(response, ["etf", "crypto", "rates"])
    assert chosen == "crypto"
    assert confidence == 0.8975
    assert probabilities[chosen] == 0.8975


@pytest.mark.parametrize("answer_confidence", [True, -0.1, float("nan"), 1.1])
def test_invalid_modern_answer_confidence_fails_closed(answer_confidence):
    response = {"answers": {"action": {"choice": "p2",
        "probabilities": {"p1": 0.1, "p2": 0.9}, "confidence": 0.7,
        "answer_confidence": answer_confidence}}}
    with pytest.raises(laya_bridge._BridgeError, match="invalid_probabilities"):
        laya_bridge._selection(response, ["p1", "p2"])
