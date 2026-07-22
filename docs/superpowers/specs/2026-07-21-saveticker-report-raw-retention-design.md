# SaveTicker Raw Retention and Report OCR Design

## Goal

Preserve SaveTicker article originals and daily report PDFs as durable source artifacts, extract text from those originals for wiki and memory layers, and automatically remove expired raw files without losing derived knowledge.

## Architecture

The system is split into three layers. Raw source storage keeps the original SaveTicker article body, article HTML when available, and downloaded daily report PDFs on disk. Extraction storage keeps normalized plain text and OCR output in separate files that downstream consumers can read without touching the raw artifact. Knowledge layers such as world memory and wiki entries reference the raw artifact by metadata and retain only the distilled context needed for chat and reasoning.

Cleanup is lifecycle-based rather than content-based. Raw files are stored under a dedicated `~/reports/raw/` tree with date- and source-based subdirectories, and a retention job removes only expired originals and their sidecar metadata. Derived text and wiki records remain intact so the system keeps its conversational memory even after raw files age out.

## Tech Stack

- Python 3.11
- Existing `requests`, `pypdf`, `pymupdf`, and `tesseract` helpers
- Local filesystem storage under `~/reports`
- Existing pytest test suite

## Global Constraints

- Preserve raw source originals before summarization or indexing.
- Keep derived text and wiki entries after raw originals are deleted.
- Default raw PDF retention is 30 days.
- Do not move existing root modules that are directly imported by cron or bot code.
- Reuse existing PDF text extraction and OCR helpers instead of adding a second parser stack.
- Use `PyMuPDF` for PDF-to-image rendering when OCR is needed for scanned PDFs.

## Data Model

Each retained artifact should have:

- `source`: `saveticker_article` or `saveticker_report_pdf`
- `source_url`: original article or report URL
- `fetched_at`: ISO timestamp
- `raw_path`: absolute file path to the original artifact
- `text_path`: absolute file path to extracted text or OCR output
- `content_type`: `html`, `pdf`, or `text`
- `title`: human-readable title
- `expires_at`: ISO timestamp for raw cleanup

## Behavior

### SaveTicker articles

The collector should keep the original article body and, when available, a raw HTML snapshot or enough structured payload to reconstruct the original text. The existing `body_raw` and `body_excerpt` fields remain, but the collector should also write a raw artifact record to disk so later wiki generation can trace the origin of a summary.

### SaveTicker daily reports

The system should download daily report PDFs from the SaveTicker report page, save them under the raw artifact tree, and run OCR or PDF text extraction immediately. If text extraction succeeds, the plain text is saved as a sidecar file and used by wiki and memory features. If OCR fails, the raw PDF still stays on disk until TTL expiration.

### Cleanup

A retention job removes only expired raw artifacts and their sidecar text files. It should ignore derived wiki data and any other downstream record that does not live under the raw artifact tree.

## Testing Strategy

- Verify SaveTicker article collection still returns `body_raw` and now emits raw artifact metadata.
- Verify report PDF ingestion stores a PDF and extracted text sidecar.
- Verify expired raw artifacts are removed while derived text records remain.
- Verify no existing report smoke tests regress when the new storage path is enabled.
