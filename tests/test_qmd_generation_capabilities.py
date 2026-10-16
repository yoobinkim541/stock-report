from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_console.qmd_generation import probe_isolation


class ScenarioRunner:
    def __init__(self, *, wrong_index=False, wrong_help_index=False, timeout_at=None, fail_at=None):
        self.wrong_index = wrong_index
        self.wrong_help_index = wrong_help_index
        self.timeout_at = timeout_at
        self.fail_at = fail_at
        self.calls = []

    def __call__(self, argv, *, capture_output, text, timeout, env, cwd):
        argv = list(argv)
        cwd = Path(cwd)
        self.calls.append((argv, dict(env), cwd))
        assert capture_output and text
        assert timeout == 30
        assert argv[1] == "--index"
        assert argv[2] in {"qmd_probe_a", "qmd_probe_b"}
        assert "INDEX_PATH" not in env
        assert "QMD_CONFIG_DIR" not in env
        sandbox = Path(env["HOME"]).parent
        assert Path(env["HOME"]).is_relative_to(sandbox)
        assert Path(env["XDG_CONFIG_HOME"]).is_relative_to(sandbox)
        assert Path(env["XDG_CACHE_HOME"]).is_relative_to(sandbox)
        assert Path(env["TMPDIR"]).is_relative_to(sandbox)
        assert cwd.is_relative_to(sandbox)
        assert (sandbox / "workspace/a/probe-a.md").is_file()
        assert (sandbox / "workspace/b/probe-b.md").is_file()

        command = argv[3:]
        phase = command[0] if command else "help"
        if phase == "collection" and len(command) > 1:
            phase = f"collection_{command[1]}"
        if self.timeout_at == phase:
            raise subprocess.TimeoutExpired(argv, timeout)
        if self.fail_at == phase:
            return subprocess.CompletedProcess(argv, 7, "", "failure details omitted")
        if phase == "--version":
            return subprocess.CompletedProcess(argv, 0, "qmd 2.5.3", "")
        if phase == "--help":
            db_path = Path(env["XDG_CACHE_HOME"]) / "qmd" / f"{argv[2]}.sqlite"
            if self.wrong_help_index:
                db_path = Path("/outside/live-index.sqlite")
            return subprocess.CompletedProcess(argv, 0, f"--index <name>\nIndex: {db_path}", "")
        if phase == "collection_help":
            return subprocess.CompletedProcess(argv, 0, "add <path> [--name NAME]", "")
        if phase == "collection_add":
            assert command[1] == "add"
            collection_path = Path(command[2])
            assert collection_path.is_relative_to(sandbox)
            return subprocess.CompletedProcess(argv, 0, "collection indexed", "")
        if phase == "search":
            assert command[1] == "qmd-generation-probe-common"
            assert "--format" in command and "json" in command
            assert "--full-path" in command and "--full" in command
            index_name = argv[2]
            result_name = "a" if self.wrong_index and index_name == "qmd_probe_b" else index_name[-1]
            filename = f"probe-{result_name}.md"
            content = f"sentinel-{result_name} qmd-generation-probe-common\n"
            result = [{"file": f"./{result_name}/{filename}", "body": content}]
            return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")
        raise AssertionError(f"unexpected qmd command: {command}")


def test_probe_never_uses_live_home_or_index(tmp_path):
    runner = ScenarioRunner()

    result = probe_isolation(executable="qmd", workspace=tmp_path, runner=runner)

    assert set(result) == {"supported", "reason", "recipe"}
    assert result["supported"] is True
    assert result["reason"] == ""
    assert result["recipe"] is not None
    assert len([call for call in runner.calls if call[0][3:5] == ["collection", "add"]]) == 2
    searches = [call for call in runner.calls if call[0][3] == "search"]
    assert len(searches) == 2
    assert {call[0][2] for call in searches} == {"qmd_probe_a", "qmd_probe_b"}
    assert all(call[0][1] == "--index" for call in runner.calls)


def test_b_query_returning_a_sentinel_is_unsupported(tmp_path):
    result = probe_isolation(
        executable="qmd",
        workspace=tmp_path,
        runner=ScenarioRunner(wrong_index=True),
    )

    assert result["supported"] is False
    assert result["recipe"] is None
    assert result["reason"] == "synthetic_b_query_returned_wrong_identity"


def test_unconfined_index_path_is_rejected_before_collection_mutation(tmp_path):
    runner = ScenarioRunner(wrong_help_index=True)

    result = probe_isolation(executable="qmd", workspace=tmp_path, runner=runner)

    assert result["supported"] is False
    assert result["recipe"] is None
    assert result["reason"] == "qmd_help_index_path_not_confined_to_temporary_cache"
    assert not any(call[0][3:5] == ["collection", "add"] for call in runner.calls)


def test_probe_timeout_is_unsupported(tmp_path):
    result = probe_isolation(
        executable="qmd",
        workspace=tmp_path,
        runner=ScenarioRunner(timeout_at="collection_add"),
    )

    assert result == {
        "supported": False,
        "reason": "qmd_collection_add_timeout",
        "recipe": None,
    }


def test_probe_nonzero_exit_is_unsupported(tmp_path):
    result = probe_isolation(
        executable="qmd",
        workspace=tmp_path,
        runner=ScenarioRunner(fail_at="collection_add"),
    )

    assert result == {
        "supported": False,
        "reason": "qmd_collection_add_exit_7",
        "recipe": None,
    }
