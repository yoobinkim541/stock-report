import json
import os
from datetime import datetime, timezone
from pathlib import Path

import reports.raw_archive as ra


def test_resolve_raw_ttl_days_uses_source_policy():
    assert ra.resolve_raw_ttl_days("saveticker_report_pdf") == 180
    assert ra.resolve_raw_ttl_days("saveticker", kind="json") == 60
    assert ra.resolve_raw_ttl_days("telegram:insidertracking", kind="json") == 14
    assert ra.resolve_raw_ttl_days("arca", kind="html") == 7
    assert ra.resolve_raw_ttl_days("fred", kind="json") == 30
    assert ra.resolve_raw_ttl_days("custom", kind="json") == 30
    assert ra.resolve_raw_ttl_days("saveticker_report_pdf", ttl_days=21) == 21


def test_save_raw_artifact_writes_original_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    rec = ra.save_raw_artifact(
        source="saveticker_report_pdf",
        kind="pdf",
        fetched_at=datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc),
        title="2026-07-21 Daily Report",
        url="https://saveticker.com/report",
        payload=b"%PDF-1.4 sample",
        suffix=".pdf",
        ttl_days=30,
    )

    assert Path(rec["raw_path"]).exists()
    assert Path(rec["manifest_path"]).exists()
    assert rec["expires_at"].startswith("2026-08-")
    manifest = json.loads(Path(rec["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["raw_path"] == rec["raw_path"]
    assert manifest["text_path"] == rec["text_path"]


def test_save_raw_artifact_keeps_json_raw_and_manifest_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    rec = ra.save_raw_artifact(
        source="saveticker_article",
        kind="json",
        fetched_at=datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc),
        title="NVDA earnings",
        url="https://saveticker.com/api/news/list",
        payload='{"title":"NVDA earnings","content":"original json"}',
        suffix=".json",
    )

    assert rec["raw_path"] != rec["manifest_path"]
    assert Path(rec["raw_path"]).read_text(encoding="utf-8") == '{"title":"NVDA earnings","content":"original json"}'
    manifest = json.loads(Path(rec["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["raw_path"] == rec["raw_path"]
    assert manifest["manifest_path"] == rec["manifest_path"]




def test_save_raw_artifact_uses_source_policy_when_ttl_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    rec = ra.save_raw_artifact(
        source="saveticker_report_pdf",
        kind="pdf",
        fetched_at=datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc),
        title="2026-07-21 Daily Report",
        url="https://saveticker.com/report",
        payload=b"%PDF-1.4 sample",
        suffix=".pdf",
    )

    assert rec["expires_at"].startswith("2027-")
def test_cleanup_expired_raw_artifacts_keeps_text_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    rec = ra.save_raw_artifact(
        source="saveticker_report_pdf",
        kind="pdf",
        fetched_at=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        title="2026-07-01 Daily Report",
        url="https://saveticker.com/report",
        payload=b"%PDF-1.4 sample",
        suffix=".pdf",
        ttl_days=30,
    )
    ra.save_extracted_text(rec, "hello world")

    cleanup = ra.cleanup_expired_raw_artifacts(
        now=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc),
    )

    assert cleanup["deleted_raw"] >= 1
    assert cleanup["deleted_manifests"] >= 1
    assert not Path(rec["raw_path"]).exists()
    assert not Path(rec["manifest_path"]).exists()
    assert Path(rec["text_path"]).exists()
    assert Path(rec["text_path"]).read_text(encoding="utf-8") == "hello world"


def test_cleanup_main_removes_expired_raw_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    rec = ra.save_raw_artifact(
        source="saveticker_report_pdf",
        kind="pdf",
        fetched_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        title="2026-06-01 Daily Report",
        url="https://saveticker.com/report",
        payload=b"%PDF-1.4 sample",
        suffix=".pdf",
        ttl_days=30,
    )
    ra.save_extracted_text(rec, "hello world")

    import crons.raw_archive_cleanup as cleanup

    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 21, 9, 0, tzinfo=tz)

    monkeypatch.setattr(cleanup, "datetime", FixedDateTime)

    assert cleanup.main() == 0
    assert not Path(rec["raw_path"]).exists()
    assert not Path(rec["manifest_path"]).exists()
    assert Path(rec["text_path"]).exists()
    assert Path(rec["text_path"]).read_text(encoding="utf-8") == "hello world"


def test_compact_raw_artifacts_keeps_original_paths_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    rec = ra.save_raw_artifact(
        source="saveticker_article",
        kind="json",
        fetched_at=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        title="NVDA earnings",
        url="https://saveticker.com/news/1",
        payload=b'{"original":true}',
        suffix=".json",
        ttl_days=60,
    )
    ra.save_extracted_text(rec, "원문 텍스트")

    result = ra.compact_raw_artifacts(
        now=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
        min_age_days=2,
    )

    assert result["bundles"] == 1
    assert result["files_packed"] == 3
    assert not Path(rec["raw_path"]).exists()
    assert not Path(rec["text_path"]).exists()
    assert not Path(rec["manifest_path"]).exists()
    assert ra.read_raw_artifact(rec["raw_path"]) == b'{"original":true}'
    assert ra.read_raw_text(rec["text_path"]) == "원문 텍스트"
    manifest = json.loads(ra.read_raw_text(rec["manifest_path"]) or "{}")
    assert manifest["sha256"] == rec["sha256"]

    cleanup = ra.cleanup_expired_raw_artifacts(
        now=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
    )
    assert cleanup["deleted_bundles"] == 1
    assert ra.read_raw_artifact(rec["raw_path"]) is None


def test_compact_raw_artifacts_dry_run_does_not_remove_files(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    rec = ra.save_raw_artifact(
        source="telegram:test",
        kind="html",
        fetched_at=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        title="Telegram post",
        url="https://t.me/test/1",
        payload="<html>original</html>",
        suffix=".html",
    )
    ra.save_extracted_text(rec, "본문")

    result = ra.compact_raw_artifacts(
        now=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
        min_age_days=2,
        dry_run=True,
    )

    assert result["bundles"] == 1
    assert result["files_packed"] == 3
    assert Path(rec["raw_path"]).exists()
    assert Path(rec["text_path"]).exists()
    assert Path(rec["manifest_path"]).exists()


def test_compact_raw_artifacts_honors_file_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    for index in range(2):
        rec = ra.save_raw_artifact(
            source="telegram:test",
            kind="html",
            fetched_at=datetime(2026, 7, 1, 9, index, tzinfo=timezone.utc),
            title=f"Telegram post {index}",
            url=f"https://t.me/test/{index}",
            payload=f"<html>{index}</html>",
            suffix=".html",
        )
        ra.save_extracted_text(rec, f"본문 {index}")

    result = ra.compact_raw_artifacts(
        now=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
        min_age_days=2,
        max_files=3,
        max_bundles=1,
    )

    assert result["bundles"] == 1
    assert result["files_packed"] == 3
