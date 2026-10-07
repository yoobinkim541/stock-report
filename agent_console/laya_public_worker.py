"""Offline JSONL worker for the public multilingual Laya checkpoint."""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import math
import os
from pathlib import Path
import sys


MAX_REQUEST_BYTES = 65536
MAX_CRITERIA = 8
MAX_CRITERION_ID = 120
MAX_DOCUMENT_TEXT = 600
MAX_CRITERION_TEXT = 180
EXPECTED_ENCODER = "jhu-clsp/mmBERT-base"
EXPECTED_SDK_VERSION = "0.3.29"
PROFILE = "public_multilingual"


class WorkerError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="absolute path to a local Laya snapshot")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--max-len", type=int, default=2048)
    parser.add_argument("--head-max-len", type=int, default=512)
    return parser


def _validate_args(args: argparse.Namespace) -> Path:
    if not 1 <= args.threads <= 4:
        raise WorkerError("invalid_configuration")
    if not 1 <= args.max_len <= 8192:
        raise WorkerError("invalid_configuration")
    if not 1 <= args.head_max_len <= min(512, args.max_len):
        raise WorkerError("invalid_configuration")
    model = Path(args.model)
    if not model.is_absolute():
        raise WorkerError("model_assets_invalid")
    try:
        model = model.resolve(strict=True)
    except OSError:
        raise WorkerError("model_assets_invalid") from None
    if not model.is_dir():
        raise WorkerError("model_assets_invalid")
    _validate_snapshot_metadata(model)
    return model


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise WorkerError("model_assets_invalid") from None
    if not isinstance(value, dict):
        raise WorkerError("model_assets_invalid")
    return value


def _validate_snapshot_metadata(model: Path) -> None:
    required_files = (
        "config.json",
        "rl_agent_config.json",
        "encoder/config.json",
        "tokenizer/tokenizer.json",
        "tokenizer/tokenizer_config.json",
        "model.safetensors",
    )
    for relative in required_files:
        path = model / relative
        if not path.is_file():
            raise WorkerError("model_assets_invalid")
    if (model / "model.safetensors").stat().st_size <= 0:
        raise WorkerError("model_assets_invalid")

    root_config = _read_json(model / "config.json")
    rl_config = _read_json(model / "rl_agent_config.json")
    encoder_config = _read_json(model / "encoder/config.json")
    if root_config.get("model_type") != "laya":
        raise WorkerError("model_profile_mismatch")
    if "LayaTypedDecisions" not in root_config.get("architectures", []):
        raise WorkerError("model_profile_mismatch")
    if rl_config.get("encoder") != EXPECTED_ENCODER:
        raise WorkerError("model_profile_mismatch")
    if (encoder_config.get("model_type") != "modernbert"
            or "ModernBertForMaskedLM" not in encoder_config.get("architectures", [])):
        raise WorkerError("model_profile_mismatch")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _valid_id(value: object) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= MAX_CRITERION_ID
            and not any(char.isspace() or ord(char) < 32 for char in value))


def _validate_request(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"state", "questions"}:
        raise WorkerError("invalid_request")
    state, questions = payload["state"], payload["questions"]
    if not isinstance(state, dict) or set(state) - {"goal", "history", "read_documents"}:
        raise WorkerError("invalid_request")
    goal = state.get("goal", "")
    if not isinstance(goal, str) or len(goal) > 2000:
        raise WorkerError("invalid_request")
    history = state.get("history", [])
    if not isinstance(history, list) or len(history) > 4:
        raise WorkerError("invalid_request")
    for item in history:
        if (not isinstance(item, dict) or set(item) != {"role", "content"}
                or not isinstance(item["role"], str) or len(item["role"]) > 32
                or not isinstance(item["content"], str) or len(item["content"]) > MAX_DOCUMENT_TEXT):
            raise WorkerError("invalid_request")

    documents = state.get("read_documents", [])
    if not isinstance(documents, list) or len(documents) > MAX_CRITERIA:
        raise WorkerError("invalid_request")
    for document in documents:
        if (not isinstance(document, dict) or set(document) != {"id", "title", "text"}
                or not _valid_id(document["id"])
                or not isinstance(document["title"], str) or len(document["title"]) > 160
                or not isinstance(document["text"], str) or len(document["text"]) > MAX_DOCUMENT_TEXT):
            raise WorkerError("invalid_request")

    if not isinstance(questions, dict) or set(questions) != {"action"}:
        raise WorkerError("invalid_request")
    question = questions["action"]
    if (not isinstance(question, dict) or set(question) != {"type", "instructions", "criteria"}
            or question["type"] != "choice" or not isinstance(question["instructions"], str)
            or len(question["instructions"]) > 2000 or not isinstance(question["criteria"], dict)):
        raise WorkerError("invalid_request")
    criteria = question["criteria"]
    if not 2 <= len(criteria) <= MAX_CRITERIA:
        raise WorkerError("invalid_request")
    if any(not _valid_id(key) or not isinstance(value, str)
           or not 1 <= len(value) <= MAX_CRITERION_TEXT
           for key, value in criteria.items()):
        raise WorkerError("invalid_request")
    document_ids = [document["id"] for document in documents]
    if (len(set(document_ids)) != len(document_ids)
            or (document_ids and set(document_ids) != set(criteria))):
        raise WorkerError("invalid_request")
    return payload


def _bounded_lines(stream):
    while True:
        line = stream.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_REQUEST_BYTES:
            while line and not line.endswith(b"\n"):
                line = stream.readline(MAX_REQUEST_BYTES + 1)
            yield None, "request_too_large"
        else:
            yield line, None


def _decode_request(line: bytes) -> dict:
    try:
        payload = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise WorkerError("invalid_json") from None
    return _validate_request(payload)


def _confidence_if_missing(response: object, criterion_ids: set[str]) -> dict:
    if not isinstance(response, dict):
        raise WorkerError("invalid_sdk_response")
    answers = response.get("answers")
    answer = answers.get("action") if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise WorkerError("invalid_sdk_response")
    if answer.get("choice") not in criterion_ids:
        raise WorkerError("invalid_sdk_response")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != criterion_ids:
        raise WorkerError("invalid_sdk_response")
    values = list(probabilities.values())
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise WorkerError("invalid_sdk_response")
    if "confidence" not in answer:
        answer["confidence"] = round(max(values), 6)
    try:
        json.dumps(response, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise WorkerError("invalid_sdk_response") from None
    return response


def _write_json(stream, value: dict) -> None:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
        encoded = '{"error":"response_too_large"}'
    try:
        stream.write(encoded + "\n")
    except TypeError:
        stream.write((encoded + "\n").encode("utf-8"))
    stream.flush()


def _load_sdk_agent(args: argparse.Namespace, model: Path, stderr, import_module):
    with contextlib.redirect_stdout(stderr):
        torch = import_module("torch")
        torch.set_num_threads(args.threads)
        if args.device == "cuda":
            if (getattr(torch, "__version__", "") != "2.7.1+cu128"
                    or getattr(getattr(torch, "version", None), "cuda", None) != "12.8"):
                raise WorkerError("cuda_runtime_unsupported")
            if not torch.cuda.is_available():
                raise WorkerError("device_unavailable")
        laya = import_module("laya")
        if getattr(laya, "__version__", None) != EXPECTED_SDK_VERSION:
            raise WorkerError("sdk_version_mismatch")
        agent = laya.load(str(model), device=args.device)
        actual = getattr(agent, "device", None)
        actual_type = getattr(actual, "type", None)
        if actual_type is None and actual is not None:
            actual_type = str(actual).split(":", 1)[0]
        if actual_type != args.device:
            raise WorkerError("device_mismatch")
        return agent, str(actual)


def main(argv=None, *, stdin=None, stdout=None, stderr=None, import_module=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    import_module = import_module if import_module is not None else importlib.import_module

    previous_environment = {key: os.environ.get(key) for key in (
        "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "USE_TF", "TOKENIZERS_PARALLELISM",
    )}
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", USE_TF="0",
                      TOKENIZERS_PARALLELISM="false")
    try:
        model = _validate_args(args)
        agent, actual_device = _load_sdk_agent(args, model, stderr, import_module)
    except Exception as exc:
        code = exc.code if isinstance(exc, WorkerError) else "model_start_failed"
        print(code, file=stderr, flush=True)
        _restore_environment(previous_environment)
        return 1

    _write_json(stdout, {
        "ready": True,
        "backend": "laya",
        "profile": PROFILE,
        "validated": False,
        "device": actual_device,
        "assets_status": "present",
        "head_status": "public_checkpoint",
    })

    try:
        for line, line_error in _bounded_lines(stdin):
            if line_error:
                _write_json(stdout, {"error": line_error})
                continue
            try:
                payload = _decode_request(line.rstrip(b"\r\n"))
                with contextlib.redirect_stdout(stderr):
                    response = agent.predict(
                        payload["state"], payload["questions"],
                        max_len=args.max_len, head_max_len=args.head_max_len,
                    )
                response = _confidence_if_missing(
                    response, set(payload["questions"]["action"]["criteria"]),
                )
                _write_json(stdout, response)
            except WorkerError as exc:
                _write_json(stdout, {"error": exc.code})
            except Exception:
                _write_json(stdout, {"error": "inference_failed"})
    finally:
        _restore_environment(previous_environment)
    return 0


def _restore_environment(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
