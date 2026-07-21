# SaveTicker Raw Retention and Report OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve SaveTicker originals, extract report text from PDFs, and expire only raw artifacts while leaving derived wiki and memory data intact.

**Architecture:** Keep raw artifacts in a dedicated filesystem tree under `~/reports/raw/`, keep extracted text in a separate derived tree under `~/reports/text/`, and record the link between them in a small catalog layer. Extend the existing SaveTicker collector to save fuller article bodies and original payloads, then add a cron entry that downloads report PDFs, extracts text with the existing parser helpers, and runs retention cleanup.

**Tech Stack:** Python 3.11, `requests`, `pypdf`, `pymupdf`, `tesseract`, existing `pytest` test suite, existing `crons/` job pattern.

## Global Constraints

- Preserve raw source originals before summarization or indexing.
- Keep derived text and wiki entries after raw originals are deleted.
- Default raw PDF retention is 30 days.
- Do not move existing root modules that are directly imported by cron or bot code.
- Reuse existing PDF text extraction and OCR helpers instead of adding a second parser stack.

---

### Task 1: Add a raw artifact helper module

**Files:**
- Create: `reports/raw_archive.py`
- Create: `tests/test_raw_archive.py`

**Interfaces:**
- Consumes: `Path`, `datetime`, and local filesystem paths only.
- Produces:
  - `raw_root() -> Path`
  - `text_root() -> Path`
  - `save_raw_artifact(source: str, kind: str, fetched_at: datetime, title: str, url: str, payload: bytes | str, suffix: str, ttl_days: int = 30) -> dict`
  - `save_extracted_text(raw_record: dict, text: str) -> dict`
  - `cleanup_expired_raw_artifacts(now: datetime | None = None, ttl_days: int = 30) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
def test_save_raw_artifact_writes_original_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    rec = save_raw_artifact(
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

def test_cleanup_expired_raw_artifacts_keeps_text_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    rec = save_raw_artifact(...)
    save_extracted_text(rec, "hello world")
    # make raw expired
    cleanup = cleanup_expired_raw_artifacts(now=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc), ttl_days=30)
    assert cleanup["deleted_raw"] >= 1
    assert Path(rec["text_path"]).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_raw_archive.py -q`
Expected: fail because `reports.raw_archive` does not exist yet.

- [ ] **Step 3: Write the minimal implementation**

Implement a small filesystem helper that:
- uses `STOCK_REPORT_REPORTS_DIR` when set, otherwise `~/reports`
- stores raw files under `raw/<source>/<YYYY>/<MM>/<DD>/`
- stores extracted text under `text/<source>/<YYYY>/<MM>/<DD>/`
- writes a JSON manifest with `raw_path`, `text_path`, `source_url`, `expires_at`, and `sha256`
- deletes only expired raw files and manifests, not derived text

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_raw_archive.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add reports/raw_archive.py tests/test_raw_archive.py
git commit -m "add) SaveTicker 원본 보관용 raw 아카이브 헬퍼 추가"
```

### Task 2: Extend SaveTicker ingestion to keep fuller bodies and raw payloads

**Files:**
- Modify: `reports/source_collector.py`
- Modify: `tests/test_source_collector.py`

**Interfaces:**
- Consumes:
  - `fetch_saveticker_events()`
  - `save_raw_artifact(...)`
  - `save_extracted_text(...)`
- Produces:
  - `fetch_saveticker_events()` entries that include `raw_path`, `text_path`, and the full `body_raw`
  - a private helper that tries the article page when the API payload is incomplete:
    - `_fetch_saveticker_article_body(url: str) -> str`
    - `_saveticker_article_record(item: dict, base: str) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
def test_fetch_saveticker_events_keeps_full_body_and_raw_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    # fake top-stories payload with content, url, created_at, tickers
    # assert events[0]["body_raw"] keeps the full content
    # assert events[0]["raw_path"] and events[0]["text_path"] are present
    # assert raw JSON manifest is written once
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_source_collector.py -q`
Expected: fail on the new raw-path assertions.

- [ ] **Step 3: Write the minimal implementation**

Update `fetch_saveticker_events()` so it:
- keeps the existing `body_raw`, `body`, and `body_excerpt` fields
- saves the original API item as raw JSON
- if the API content is thin, fetches the article page and merges the fuller article body into `body_raw`
- preserves the old normalization behavior for `tickers` and `tags`

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_source_collector.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add reports/source_collector.py tests/test_source_collector.py
git commit -m "fix) SaveTicker 뉴스 원문과 본문을 같이 보관하도록 수집 보강"
```

### Task 3: Add SaveTicker report PDF download and OCR ingest

**Files:**
- Create: `crons/saveticker_report_archive.py`
- Modify: `bot/attachment_parser.py`
- Modify: `requirements.txt`
- Create: `tests/test_saveticker_report_archive.py`
- Modify: `tests/test_attachment_parser.py`

**Interfaces:**
- Consumes:
  - `_discover_report_pdf_urls_from_html(html_text: str) -> list[str]`
  - `discover_report_pdf_urls(report_page_url: str = "https://saveticker.com/report") -> list[str]`
  - `download_report_pdf(url: str) -> Path`
  - `extract_text_from_pdf_or_ocr(path: str) -> str | None`
  - `save_raw_artifact(...)`
  - `save_extracted_text(...)`
- Produces:
  - `download_latest_saveticker_report() -> dict | None`
  - `extract_text_from_pdf_or_ocr(path: str) -> str | None`

- [ ] **Step 1: Write the failing tests**

```python
def test_discover_report_pdf_urls_finds_pdf_links():
    html = '<a href="/reports/2026-07-21.pdf">PDF</a>'
    assert _discover_report_pdf_urls_from_html(html) == ["https://saveticker.com/reports/2026-07-21.pdf"]

def test_extract_text_from_pdf_or_ocr_prefers_pdf_text(monkeypatch, tmp_path):
    monkeypatch.setattr(ap, "extract_text_from_pdf", lambda path: "REPORT TEXT")
    monkeypatch.setattr(ap, "_render_pdf_pages_to_images", lambda path: pytest.fail("render should not run"))
    assert ap.extract_text_from_pdf_or_ocr(str(tmp_path / "report.pdf")) == "REPORT TEXT"

def test_extract_text_from_pdf_or_ocr_runs_ocr_when_pdf_text_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ap, "extract_text_from_pdf", lambda path: None)
    monkeypatch.setattr(ap, "_render_pdf_pages_to_images", lambda path: ["/tmp/page-1.png", "/tmp/page-2.png"])
    monkeypatch.setattr(ap, "extract_text_from_image", lambda path: "PAGE")
    assert ap.extract_text_from_pdf_or_ocr(str(tmp_path / "report.pdf")) == "PAGE\nPAGE"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_saveticker_report_archive.py tests/test_attachment_parser.py -q`
Expected: fail because the new cron module and OCR wrapper do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

Implement the cron job so it:
- fetches the SaveTicker report page
- extracts PDF links from the page HTML
- downloads the newest PDF into `~/reports/raw/saveticker/reports/...`
- extracts text with `extract_text_from_pdf_or_ocr()`
- stores the extracted text in `~/reports/text/saveticker/reports/...`
- writes a manifest that points both files back to the report URL

In `bot/attachment_parser.py`, add a PDF OCR wrapper that:
- tries `extract_text_from_pdf(path)` first
- if that returns empty, converts the PDF to page images with a local binary or library already available in the environment
- runs the existing `extract_text_from_image(path)` helper on each page image
- returns the concatenated text

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_saveticker_report_archive.py tests/test_attachment_parser.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crons/saveticker_report_archive.py bot/attachment_parser.py tests/test_saveticker_report_archive.py tests/test_attachment_parser.py
git commit -m "add) SaveTicker 데일리 리포트 PDF 저장과 OCR 추출 추가"
```

### Task 4: Wire retention cleanup into the daily job flow

**Files:**
- Modify: `crons/saveticker_report_archive.py`
- Create: `crons/raw_archive_cleanup.py`
- Modify: `tests/test_raw_archive.py`

**Interfaces:**
- Consumes:
  - `cleanup_expired_raw_artifacts(now: datetime | None = None, ttl_days: int = 30) -> dict`
- Produces:
  - `main() -> int` in `crons/raw_archive_cleanup.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_cleanup_main_returns_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    # create one expired raw artifact and one fresh text sidecar
    # assert cleanup main reports deleted_raw > 0 and keeps extracted text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_raw_archive.py -q`
Expected: fail on the new cleanup entry point.

- [ ] **Step 3: Write the minimal implementation**

Make the daily archive job call cleanup at the end, and add a separate cleanup cron module so raw files are also purged on days when no new report is downloaded.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_raw_archive.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crons/saveticker_report_archive.py crons/raw_archive_cleanup.py tests/test_raw_archive.py
git commit -m "fix) SaveTicker 원본 파일 TTL 청소를 별도 크론으로 분리"
```

### Task 5: Update docs and verify the live server

**Files:**
- Modify: `docs/agent-console.md`
- Modify: `docs/local-agent-console-install-prompt.md`
- Modify: `docs/project-structure.md`
- Test: `tests/test_investment_report_smoke.py` or `tests/bot_healthcheck.py` only if the new archive job affects report freshness assumptions

**Interfaces:**
- Consumes:
  - the new raw archive layout and cleanup behavior
- Produces:
  - documentation that points operators to `~/reports/raw/` and `~/reports/text/`

- [ ] **Step 1: Write the failing/adjustment checks**

```bash
pytest tests/test_investment_report_smoke.py -q
```

Expected: existing report generation behavior still passes; no regression in the current report files.

- [ ] **Step 2: Update the docs**

Document:
- raw originals live under `~/reports/raw/`
- extracted text lives under `~/reports/text/`
- raw PDFs expire after 30 days by default
- derived wiki and memory data stay after raw cleanup

- [ ] **Step 3: Run the focused tests**

Run:
```bash
pytest tests/test_source_collector.py tests/test_attachment_parser.py tests/test_raw_archive.py tests/test_saveticker_report_archive.py -q
```

Expected: PASS

- [ ] **Step 4: Restart the dashboard server**

Restart the existing local Streamlit process so the live server reads the new code.

- [ ] **Step 5: Commit**

```bash
git add docs/agent-console.md docs/local-agent-console-install-prompt.md docs/project-structure.md
git commit -m "docs) SaveTicker 원본 보관과 OCR 저장 구조를 문서화"
```
