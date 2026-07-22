"""두 writer 가 같은 events.jsonl 에 붙는 상황에서 레코드 유실이 없어야 한다.

shared_memory 의 전체 재작성(delete) 도중 lib/agent_memory 나 다른 프로세스가
append 하면, 락이 없을 때 그 레코드가 사라진다. 서브프로세스로 재현한다.
(monkeypatch 는 자식 프로세스에 전달되지 않으므로 환경변수로 저장소를 지정한다.)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

WORKER = """
import sys
sys.path.insert(0, {root!r})
from agent_console import shared_memory

mode = sys.argv[1]
if mode == "append":
    for i in range(40):
        shared_memory.append_record({{"title": f"w-{{i}}", "summary": "s", "tags": ["chat"]}})
else:
    for rid in sys.argv[2:]:
        shared_memory.delete_record(rid)
"""

SEED = """
import sys, json
sys.path.insert(0, {root!r})
from agent_console import shared_memory

ids = [shared_memory.append_record(
    {{"title": f"seed-{{i}}", "summary": "s", "tags": ["chat"]}})["id"] for i in range(30)]
print(json.dumps(ids))
"""

# 진짜 위험 지점: appender 가 lib/agent_memory 인 경우.
# shared_memory 가 전체 재작성하는 동안 agent_memory 가 append 하면,
# 두 모듈이 같은 락을 공유하지 않는 한 그 레코드는 사라진다.
LIB_WORKER = """
import sys
sys.path.insert(0, {root!r})
from lib import agent_memory

for i in range(40):
    agent_memory._append_event({{"title": f"lib-{{i}}", "summary": "s"}})
"""


def test_concurrent_append_and_rewrite_lose_no_records(tmp_path):
    store = tmp_path / "shared-memory"
    env = dict(os.environ)
    env["AGENT_CONSOLE_SHARED_MEMORY_DIR"] = str(store)
    env["AGENT_MEMORY_DIR"] = str(store)

    seed = tmp_path / "seed.py"
    seed.write_text(SEED.format(root=str(PROJECT_ROOT)), encoding="utf-8")
    out = subprocess.run([sys.executable, str(seed)], env=env,
                         capture_output=True, text=True, check=True)
    seed_ids = json.loads(out.stdout.strip().splitlines()[-1])
    victims = seed_ids[:10]

    script = tmp_path / "worker.py"
    script.write_text(WORKER.format(root=str(PROJECT_ROOT)), encoding="utf-8")

    p1 = subprocess.Popen([sys.executable, str(script), "append"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen([sys.executable, str(script), "delete", *victims], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p1.wait(timeout=300)
    p2.wait(timeout=300)
    assert p1.returncode == 0, p1.stderr.read().decode()
    assert p2.returncode == 0, p2.stderr.read().decode()

    rows = [json.loads(line) for line in
            (store / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    # 시드 30 - 삭제 10 + append 40 = 60
    assert len(rows) == 60, f"레코드 유실/중복: {len(rows)}건"


def test_lib_agent_memory_append_survives_concurrent_rewrite(tmp_path):
    """lib/agent_memory 의 append 가 shared_memory 전체 재작성에 지워지면 안 된다."""
    store = tmp_path / "shared-memory"
    env = dict(os.environ)
    env["AGENT_CONSOLE_SHARED_MEMORY_DIR"] = str(store)
    env["AGENT_MEMORY_DIR"] = str(store)

    seed = tmp_path / "seed.py"
    seed.write_text(SEED.format(root=str(PROJECT_ROOT)), encoding="utf-8")
    out = subprocess.run([sys.executable, str(seed)], env=env,
                         capture_output=True, text=True, check=True)
    seed_ids = json.loads(out.stdout.strip().splitlines()[-1])
    victims = seed_ids[:10]

    worker = tmp_path / "worker.py"
    worker.write_text(WORKER.format(root=str(PROJECT_ROOT)), encoding="utf-8")
    lib_worker = tmp_path / "lib_worker.py"
    lib_worker.write_text(LIB_WORKER.format(root=str(PROJECT_ROOT)), encoding="utf-8")

    p1 = subprocess.Popen([sys.executable, str(lib_worker)], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen([sys.executable, str(worker), "delete", *victims], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p1.wait(timeout=300)
    p2.wait(timeout=300)
    assert p1.returncode == 0, p1.stderr.read().decode()
    assert p2.returncode == 0, p2.stderr.read().decode()

    rows = [json.loads(line) for line in
            (store / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    lib_rows = [r for r in rows if str(r.get("title", "")).startswith("lib-")]
    assert len(lib_rows) == 40, f"agent_memory 레코드 유실: {len(lib_rows)}/40"
    assert len(rows) == 60, f"전체 레코드 유실/중복: {len(rows)}건"


def test_index_keeps_both_writer_schemas(tmp_path, monkeypatch):
    """shared_memory 와 lib.agent_memory 가 번갈아 써도 서로의 키를 안 지운다."""
    store = tmp_path / "shared-memory"
    store.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(store))

    from agent_console import shared_memory
    from lib import agent_memory

    monkeypatch.setattr(agent_memory, "MEMORY_DIR", store)
    monkeypatch.setattr(agent_memory, "EVENTS_PATH", store / "events.jsonl")
    monkeypatch.setattr(agent_memory, "INDEX_PATH", store / "index.json")

    shared_memory.append_record({"title": "a", "summary": "s", "tags": ["chat"]})
    agent_memory._append_event({"title": "b", "summary": "s2"})
    shared_memory.append_record({"title": "c", "summary": "s3", "tags": ["chat"]})

    payload = json.loads((store / "index.json").read_text(encoding="utf-8"))
    assert payload.get("recordCount") == 3, "shared_memory 키가 사라짐"
    assert payload.get("count") == 1, "agent_memory 키가 사라짐"
