"""Public Laya worker contract tests; model inference is replaced at the SDK boundary."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "agent_console" / "laya_public_worker.py"


def load_worker():
    assert WORKER_PATH.is_file(), "public Laya worker must exist"
    spec = importlib.util.spec_from_file_location("laya_public_worker_under_test", WORKER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def model_snapshot(path: Path, *, encoder: str = "jhu-clsp/mmBERT-base") -> Path:
    (path / "encoder").mkdir(parents=True)
    (path / "tokenizer").mkdir()
    (path / "config.json").write_text(json.dumps({
        "model_type": "laya",
        "architectures": ["LayaTypedDecisions"],
    }), encoding="utf-8")
    (path / "rl_agent_config.json").write_text(json.dumps({"encoder": encoder}), encoding="utf-8")
    (path / "encoder" / "config.json").write_text(json.dumps({
        "model_type": "modernbert",
        "architectures": ["ModernBertForMaskedLM"],
    }), encoding="utf-8")
    (path / "tokenizer" / "tokenizer.json").write_text("{}", encoding="utf-8")
    (path / "tokenizer" / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"fixture")
    return path.resolve()


def request(*, count: int = 2) -> dict:
    criteria = {f"wiki:{i}": f"후보 페이지 설명 {i}" for i in range(count)}
    return {
        "state": {
            "goal": "금융 위키 후보를 골라 주세요.",
            "history": [],
            "read_documents": [
                {"id": key, "title": value, "text": value}
                for key, value in criteria.items()
            ],
        },
        "questions": {
            "action": {
                "type": "choice",
                "instructions": "질문에 가장 관련 있는 위키를 선택하세요.",
                "criteria": criteria,
            }
        },
    }


class FakeAgent:
    def __init__(self, result=None, device="cpu"):
        self.device = device
        self.result = result or {"answers": {"action": {
            "type": "choice", "choice": "wiki:1",
            "probabilities": {"wiki:0": 0.2, "wiki:1": 0.8},
        }}}
        self.calls = []

    def predict(self, state, questions, **kwargs):
        print("SDK predict diagnostic")
        self.calls.append((state, questions, kwargs))
        return self.result


def fake_importer(agent: FakeAgent, *, version: str = "0.3.29", device: str = "cpu",
                  actual_device: str | None = None, torch_version: str | None = None,
                  cuda_version: str | None = None):
    calls = []

    def import_module(name):
        calls.append(name)
        if name == "torch":
            return types.SimpleNamespace(
                __version__=torch_version or ("2.7.1+cu128" if device == "cuda" else "test"),
                version=types.SimpleNamespace(cuda=cuda_version or ("12.8" if device == "cuda" else None)),
                set_num_threads=lambda value: calls.append(("threads", value)),
                cuda=types.SimpleNamespace(is_available=lambda: device == "cuda"),
            )
        if name == "laya":
            print("SDK import diagnostic")
            return types.SimpleNamespace(
                __version__=version,
                load=lambda path, device: (calls.append(("load", path, device))
                                           or setattr(agent, "device", actual_device or device)
                                           or agent),
            )
        raise AssertionError(f"unexpected import: {name}")

    return import_module, calls


def test_worker_emits_one_ready_record_and_keeps_sdk_stdout_off_protocol(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    agent = FakeAgent()
    importer, calls = fake_importer(agent)
    stdout, stderr = io.BytesIO(), io.StringIO()

    status = worker.main(
        ["--model", str(model), "--device", "cpu"],
        stdin=io.BytesIO((json.dumps(request(), ensure_ascii=False) + "\n").encode()),
        stdout=stdout,
        stderr=stderr,
        import_module=importer,
    )

    records = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert status == 0
    assert records[0] == {
        "ready": True, "backend": "laya", "profile": "public_multilingual",
        "validated": False, "device": "cpu", "assets_status": "present",
        "head_status": "public_checkpoint",
    }
    assert records[1]["answers"]["action"]["confidence"] == 0.8
    assert records[1]["answers"]["action"]["choice"] == "wiki:1"
    assert "SDK import diagnostic" in stderr.getvalue()
    assert "SDK predict diagnostic" in stderr.getvalue()
    assert calls.count("laya") == 1
    assert agent.calls[0][2] == {"max_len": 2048, "head_max_len": 512}


def test_worker_defaults_to_cpu_without_cuda_runtime_requirements(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    agent = FakeAgent()
    importer, calls = fake_importer(agent)
    stdout = io.BytesIO()

    assert worker.main(
        ["--model", str(model)], stdin=io.BytesIO(), stdout=stdout,
        stderr=io.StringIO(), import_module=importer,
    ) == 0

    ready = json.loads(stdout.getvalue().splitlines()[0])
    assert ready["device"] == "cpu"
    assert "laya" in calls


def test_worker_preserves_native_confidence_and_original_sdk_answer_shape(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    sdk_result = {"answers": {"action": {
        "type": "choice", "choice": "wiki:1",
        "probabilities": {"wiki:0": 0.2, "wiki:1": 0.8}, "confidence": 0.75,
    }}, "usage": {"truncated": False}}
    agent = FakeAgent(sdk_result)
    importer, _ = fake_importer(agent)
    stdout = io.BytesIO()

    assert worker.main(
        ["--model", str(model), "--device", "cpu"],
        stdin=io.BytesIO((json.dumps(request()) + "\n").encode()),
        stdout=stdout, stderr=io.StringIO(), import_module=importer,
    ) == 0

    response = json.loads(stdout.getvalue().splitlines()[1])
    assert response == sdk_result


def test_worker_rounds_fallback_confidence_to_six_decimal_places(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    sdk_result = {"answers": {"action": {
        "type": "choice", "choice": "wiki:1",
        "probabilities": {"wiki:0": 0.2, "wiki:1": 0.81234567},
    }}}
    importer, _ = fake_importer(FakeAgent(sdk_result))
    stdout = io.BytesIO()

    assert worker.main(
        ["--model", str(model), "--device", "cpu"],
        stdin=io.BytesIO((json.dumps(request()) + "\n").encode()),
        stdout=stdout, stderr=io.StringIO(), import_module=importer,
    ) == 0

    response = json.loads(stdout.getvalue().splitlines()[1])
    assert response["answers"]["action"]["confidence"] == 0.812346


def test_worker_sets_offline_environment_before_sdk_import(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    agent = FakeAgent()
    calls = []

    def import_module(name):
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        assert os.environ["USE_TF"] == "0"
        calls.append(name)
        if name == "torch":
            return types.SimpleNamespace(
                set_num_threads=lambda value: calls.append(("threads", value)),
                cuda=types.SimpleNamespace(is_available=lambda: False),
            )
        if name == "laya":
            return types.SimpleNamespace(
                __version__="0.3.29",
                load=lambda path, device: (calls.append(("load", path, device)) or agent),
            )
        raise AssertionError(f"unexpected import: {name}")

    assert worker.main(
        ["--model", str(model), "--device", "cpu", "--threads", "4", "--max-len", "1024",
         "--head-max-len", "256"],
        stdin=io.BytesIO((json.dumps(request()) + "\n").encode()),
        stdout=io.BytesIO(), stderr=io.StringIO(), import_module=import_module,
    ) == 0
    assert ("threads", 4) in calls
    assert ("load", str(model), "cpu") in calls
    assert agent.calls[0][2] == {"max_len": 1024, "head_max_len": 256}


def test_cuda_readiness_reports_actual_device_and_rejects_sdk_cpu_fallback(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    request_line = (json.dumps(request()) + "\n").encode()
    gpu_agent = FakeAgent(device="cuda:0")
    importer, _ = fake_importer(gpu_agent, device="cuda", actual_device="cuda:0")
    stdout = io.BytesIO()

    assert worker.main(
        ["--model", str(model), "--device", "cuda"], stdin=io.BytesIO(request_line),
        stdout=stdout, stderr=io.StringIO(), import_module=importer,
    ) == 0
    ready = json.loads(stdout.getvalue().splitlines()[0])
    assert ready["device"] == "cuda:0"
    assert ready["validated"] is False

    fallback_agent = FakeAgent(device="cpu")
    importer, _ = fake_importer(fallback_agent, device="cuda", actual_device="cpu")
    stdout = io.BytesIO()
    stderr = io.StringIO()
    assert worker.main(
        ["--model", str(model), "--device", "cuda"], stdin=io.BytesIO(),
        stdout=stdout, stderr=stderr, import_module=importer,
    ) != 0
    assert stdout.getvalue() == b""
    assert "device_mismatch" in stderr.getvalue()


def test_cuda_unavailable_fails_before_loading_laya(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    importer, calls = fake_importer(FakeAgent(), device="cpu")
    stdout = io.BytesIO()

    assert worker.main(["--model", str(model), "--device", "cuda"], stdin=io.BytesIO(),
                       stdout=stdout, stderr=io.StringIO(), import_module=importer) != 0
    assert stdout.getvalue() == b""
    assert "laya" not in calls


def test_cuda_rejects_unapproved_torch_build_before_loading_laya(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    importer, calls = fake_importer(FakeAgent(), device="cuda", torch_version="2.8.0+cu129",
                                    cuda_version="12.9")
    stderr = io.StringIO()

    assert worker.main(["--model", str(model), "--device", "cuda"], stdin=io.BytesIO(),
                       stdout=io.BytesIO(), stderr=stderr, import_module=importer) != 0
    assert "cuda_runtime_unsupported" in stderr.getvalue()
    assert "laya" not in calls


@pytest.mark.parametrize("bad_request, expected_error", [
    (b"not-json\n", "invalid_json"),
    (json.dumps(request(count=9)).encode() + b"\n", "invalid_request"),
    (json.dumps(request(count=1)).encode() + b"\n", "invalid_request"),
])
def test_worker_returns_fixed_error_then_continues_to_next_request(tmp_path, bad_request, expected_error):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    agent = FakeAgent()
    importer, _ = fake_importer(agent)
    stdout = io.BytesIO()
    stdin = io.BytesIO(bad_request + (json.dumps(request()).encode() + b"\n"))

    assert worker.main(["--model", str(model), "--device", "cpu"], stdin=stdin, stdout=stdout,
                       stderr=io.StringIO(), import_module=importer) == 0

    records = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert records[1] == {"error": expected_error}
    assert records[2]["answers"]["action"]["choice"] == "wiki:1"
    assert len(agent.calls) == 1


@pytest.mark.parametrize("mutate", [
    lambda req: req["state"]["read_documents"][0].update(text="x" * 601),
    lambda req: req["questions"]["action"]["criteria"].update({"extra": "x" * 181}),
    lambda req: req["questions"]["action"].update(type="score"),
])
def test_worker_rejects_out_of_contract_payloads_without_silently_truncating(tmp_path, mutate):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    agent = FakeAgent()
    importer, _ = fake_importer(agent)
    payload = request()
    mutate(payload)
    stdout = io.BytesIO()

    assert worker.main(
        ["--model", str(model), "--device", "cpu"],
        stdin=io.BytesIO((json.dumps(payload) + "\n").encode()),
        stdout=stdout, stderr=io.StringIO(), import_module=importer,
    ) == 0

    assert json.loads(stdout.getvalue().splitlines()[1]) == {"error": "invalid_request"}
    assert agent.calls == []


def test_worker_requires_local_multilingual_snapshot_and_exact_sdk_version(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model", encoder="not-the-multilingual-encoder")
    importer, calls = fake_importer(FakeAgent())
    out = io.BytesIO()

    assert worker.main(["--model", str(model), "--device", "cpu"], stdin=io.BytesIO(), stdout=out,
                       stderr=io.StringIO(), import_module=importer) != 0
    assert out.getvalue() == b""
    assert calls == []

    model = model_snapshot(tmp_path / "model2")
    importer, calls = fake_importer(FakeAgent(), version="0.3.28")
    assert worker.main(["--model", str(model), "--device", "cpu"], stdin=io.BytesIO(), stdout=io.BytesIO(),
                       stderr=io.StringIO(), import_module=importer) != 0
    assert "laya" in calls


def test_worker_rejects_overlong_jsonl_line_with_bounded_read_and_continues(tmp_path):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    agent = FakeAgent()
    importer, _ = fake_importer(agent)
    out = io.BytesIO()
    stdin = io.BytesIO(b" " * 65537 + b"\n" + json.dumps(request()).encode() + b"\n")

    assert worker.main(["--model", str(model), "--device", "cpu"], stdin=stdin, stdout=out,
                       stderr=io.StringIO(), import_module=importer) == 0

    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert records[1] == {"error": "request_too_large"}
    assert records[2]["answers"]["action"]["choice"] == "wiki:1"


def test_worker_rejects_nonabsolute_model_path_before_sdk_import():
    worker = load_worker()
    importer, calls = fake_importer(FakeAgent())
    out = io.BytesIO()

    assert worker.main(["--model", "convaiinnovations/laya-multilingual"],
                       stdin=io.BytesIO(), stdout=out, stderr=io.StringIO(),
                       import_module=importer) != 0
    assert calls == []
    assert out.getvalue() == b""


@pytest.mark.parametrize("options", [
    ["--threads", "5"],
    ["--head-max-len", "513"],
    ["--max-len", "8193"],
])
def test_worker_rejects_resource_limits_before_sdk_import(tmp_path, options):
    worker = load_worker()
    model = model_snapshot(tmp_path / "model")
    importer, calls = fake_importer(FakeAgent())

    assert worker.main(["--model", str(model), *options], stdin=io.BytesIO(),
                       stdout=io.BytesIO(), stderr=io.StringIO(),
                       import_module=importer) != 0
    assert calls == []
