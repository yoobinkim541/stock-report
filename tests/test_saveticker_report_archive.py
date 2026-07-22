import json
from pathlib import Path

import crons.saveticker_report_archive as arc


def test_discover_report_pdf_urls_finds_pdf_links():
    html = '''
    <html>
      <body>
        <a href="/reports/2026-07-21.pdf">PDF</a>
        <a href="https://cdn.saveticker.com/ignore.txt">TXT</a>
      </body>
    </html>
    '''
    assert arc._discover_report_pdf_urls_from_html(html) == ["https://saveticker.com/reports/2026-07-21.pdf"]


def test_download_latest_saveticker_report_saves_raw_and_text(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))

    class FakeResponse:
        def __init__(self, *, text="", content=b""):
            self.text = text
            self.content = content

        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, timeout=None):
        if url == arc.REPORT_PAGE_URL:
            return FakeResponse(text='<a href="/reports/2026-07-21.pdf">PDF</a>')
        if url.endswith(".pdf"):
            return FakeResponse(content=b"%PDF-1.4 fake report")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(arc.requests, "get", fake_get)
    monkeypatch.setattr(arc, "extract_text_from_pdf_or_ocr", lambda path: "REPORT TEXT")

    result = arc.download_latest_saveticker_report()

    assert result is not None
    assert result["source"] == "saveticker_report_pdf"
    assert Path(result["raw_path"]).exists()
    assert Path(result["manifest_path"]).exists()
    assert Path(result["text_path"]).exists()
    assert Path(result["text_path"]).read_text(encoding="utf-8") == "REPORT TEXT"
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["source"] == "saveticker_report_pdf"
    assert manifest["ttl_days"] == 180
    assert result["expires_at"].startswith("2027-")
    assert result["downloaded_url"].endswith(".pdf")
