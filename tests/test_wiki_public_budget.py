import json
import multiprocessing
import re
from datetime import datetime, timezone

import pytest
from contextlib import contextmanager

from reports import wiki_public_budget


def test_clock_is_captured_after_lock_acquisition(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 13, 14, 59, tzinfo=timezone.utc)]
    monkeypatch.setattr(wiki_public_budget, '_now', lambda value: value or clock[0])
    @contextmanager
    def midnight_lock(path):
        clock[0] = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)
        yield
    monkeypatch.setattr(wiki_public_budget, 'file_write_lock', midnight_lock)
    path = tmp_path / 'budget.json'
    wiki_public_budget.reserve(path, 'a' * 64)
    assert set(json.loads(path.read_text())['days']) == {'2026-10-14'}


def _reserve_worker(path, revision, output):
    output.put(wiki_public_budget.reserve(path, revision))


def test_reservations_count_attempts_and_stop_at_limit(tmp_path):
    path = tmp_path / "budget.json"
    now = datetime(2026, 10, 13, 2, tzinfo=timezone.utc)

    first = wiki_public_budget.reserve(path, "a" * 64, now=now, limit=2)
    second = wiki_public_budget.reserve(path, "b" * 64, now=now, limit=2)

    assert first["revision_id"] == "a" * 64
    assert first["at"].endswith("+00:00")
    assert re.fullmatch(r"[0-9a-f-]{36}", first["id"])
    assert second is not None
    assert wiki_public_budget.reserve(path, "c" * 64, now=now, limit=2) is None
    assert wiki_public_budget.status(path, now=now)["count"] == 2


def test_day_rolls_at_utc_1500_and_retains_prior_days(tmp_path):
    path = tmp_path / "budget.json"
    before_midnight_kst = datetime(2026, 10, 13, 14, 59, tzinfo=timezone.utc)
    at_midnight_kst = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)

    wiki_public_budget.reserve(path, "a" * 64, now=before_midnight_kst)
    result = wiki_public_budget.reserve(path, "b" * 64, now=at_midnight_kst)

    assert set(result) == {"id", "revision_id", "at"}
    data = json.loads(path.read_text())
    assert set(data["days"]) == {"2026-10-13", "2026-10-14"}


def test_status_does_not_initialize_missing_file(tmp_path):
    path = tmp_path / "missing.json"

    now = datetime(2026, 10, 13, 14, 59, tzinfo=timezone.utc)
    assert wiki_public_budget.status(path, now=now) == {"day": "2026-10-13", "count": 0, "limit": 20}
    assert not path.exists()


@pytest.mark.parametrize("revision", ["", "A" * 64, "g" * 64, "a" * 63])
def test_invalid_revision_is_rejected_without_writing(tmp_path, revision):
    path = tmp_path / "budget.json"

    with pytest.raises(ValueError):
        wiki_public_budget.reserve(path, revision)
    assert not path.exists()


@pytest.mark.parametrize("limit", [0, 21, True, 1.5])
def test_invalid_limit_is_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        wiki_public_budget.reserve(tmp_path / "budget.json", "a" * 64, limit=limit)


@pytest.mark.parametrize("payload", ["{", "[]", '{"schema":2,"days":{}}',
                                     '{"schema":true,"days":{}}', '{"schema":1,"days":[]}'])
def test_corrupt_or_unknown_state_is_rejected_not_reset(tmp_path, payload):
    path = tmp_path / "budget.json"
    path.write_text(payload)

    with pytest.raises((ValueError, json.JSONDecodeError)):
        wiki_public_budget.reserve(path, "a" * 64)
    assert path.read_text() == payload


def test_reservations_are_atomic_across_processes_at_limit(tmp_path):
    path = str(tmp_path / "budget.json")
    ctx = multiprocessing.get_context("fork")
    output = ctx.Queue()
    workers = [ctx.Process(target=_reserve_worker, args=(path, f"{i:064x}", output))
               for i in range(24)]

    for worker in workers:
        worker.start()
    results = [output.get(timeout=20) for _ in workers]
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0

    assert sum(result is not None for result in results) == 20
    assert wiki_public_budget.status(path)["count"] == 20
