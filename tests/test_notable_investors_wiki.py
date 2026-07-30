"""
test_notable_investors_wiki.py — 유명 투자자(버핏 등) 13F → 위키 카드 + 신규/청산 감지.

diff_holdings/build_wiki_page 는 순수 함수. run()/main() 은 thirteenf.latest_holdings·
agent_console.wiki·이력 파일을 모두 모킹/격리해 무네트워크로 검증한다.
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from reports import notable_investors_wiki as niw  # noqa: E402


def _holding(issuer, cusip, ticker=None, weight=10.0, value=1e9):
    return {"issuer": issuer, "cusip": cusip, "ticker": ticker, "weight_pct": weight, "value_usd": value}


def test_diff_holdings_detects_new_and_exited_by_cusip():
    prev = [_holding("APPLE INC", "AAA", "AAPL"), _holding("COCA COLA CO", "BBB", "KO")]
    cur = [_holding("APPLE INC", "AAA", "AAPL"), _holding("CHEVRON CORP", "CCC", "CVX")]

    diff = niw.diff_holdings(prev, cur)

    assert [h["cusip"] for h in diff["new"]] == ["CCC"]
    assert [h["cusip"] for h in diff["exited"]] == ["BBB"]


def test_diff_holdings_first_snapshot_has_no_exits():
    """이전 이력이 없으면(첫 실행) 전부 '신규'가 아니라 그냥 청산 0건 — 알림 폭탄 방지 판단은 상위(run)."""
    cur = [_holding("APPLE INC", "AAA", "AAPL")]
    diff = niw.diff_holdings(None, cur)

    assert diff["exited"] == []
    assert [h["cusip"] for h in diff["new"]] == ["AAA"]


def test_diff_holdings_sorts_by_weight_descending():
    prev = []
    cur = [_holding("SMALL", "S", weight=1.0), _holding("BIG", "B", weight=20.0)]
    diff = niw.diff_holdings(prev, cur)
    assert [h["cusip"] for h in diff["new"]] == ["B", "S"]


def test_build_wiki_page_has_stable_id_per_filer():
    snapshot = {
        "filer": "berkshire", "filer_name": "Berkshire Hathaway (Warren Buffett)",
        "cik": "0001067983", "accession": "0001-26-000001", "filing_date": "2026-05-15",
        "total_value_usd": 2.631e11,
        "holdings": [_holding("APPLE INC", "AAA", "AAPL", weight=22.0, value=5.78e10)],
    }
    diff = {"new": [], "exited": []}

    page = niw.build_wiki_page(snapshot, diff)

    assert page["id"] == "notable-investor-berkshire"
    assert page["kind"] == "source_digest"
    assert "AAPL" in page["body"]
    assert "263.1" in page["summary"]


def test_build_wiki_page_includes_new_and_exited_sections():
    snapshot = {
        "filer": "berkshire", "filer_name": "Berkshire Hathaway (Warren Buffett)",
        "cik": "0001067983", "accession": "acc-2", "filing_date": "2026-08-15",
        "total_value_usd": 1e9, "holdings": [_holding("APPLE INC", "AAA", "AAPL")],
    }
    diff = {"new": [_holding("NEWCO INC", "NNN", "NEWC")], "exited": [_holding("OLDCO INC", "OOO", "OLDC")]}

    page = niw.build_wiki_page(snapshot, diff)

    assert "신규 편입" in page["body"] and "NEWCO INC" in page["body"]
    assert "청산" in page["body"] and "OLDCO INC" in page["body"]
    assert "신규편입 1" in page["summary"] and "청산 1" in page["summary"]


def test_run_skips_when_accession_already_recorded(monkeypatch, tmp_path):
    """같은 필링(accession)이 이미 이력에 있으면 위키 갱신·알림 없이 스킵."""
    history_path = tmp_path / "history.jsonl"
    monkeypatch.setattr(niw, "HISTORY_PATH", history_path)
    history_path.write_text(
        '{"filer": "berkshire", "accession": "acc-1", "holdings": []}\n', encoding="utf-8"
    )

    from providers import thirteenf
    monkeypatch.setattr(thirteenf, "latest_holdings", lambda key: {
        "filer": "berkshire", "filer_name": "Berkshire", "cik": "1", "accession": "acc-1",
        "filing_date": "2026-05-15", "total_value_usd": 1.0, "holdings": [],
    })

    calls = []
    from agent_console import wiki
    monkeypatch.setattr(wiki, "upsert_page", lambda p: calls.append(p) or p)

    result = niw.run("berkshire")

    assert result["status"] == "unchanged"
    assert calls == []


def test_run_updates_wiki_and_history_on_new_filing(monkeypatch, tmp_path):
    history_path = tmp_path / "history.jsonl"
    monkeypatch.setattr(niw, "HISTORY_PATH", history_path)

    from providers import thirteenf
    monkeypatch.setattr(thirteenf, "latest_holdings", lambda key: {
        "filer": "berkshire", "filer_name": "Berkshire Hathaway (Warren Buffett)",
        "cik": "1067983", "accession": "acc-new", "filing_date": "2026-05-15",
        "total_value_usd": 5e10, "holdings": [_holding("APPLE INC", "AAA", "AAPL", weight=100.0)],
    })

    saved_pages = []
    from agent_console import wiki
    monkeypatch.setattr(wiki, "upsert_page", lambda p: saved_pages.append(p) or p)

    result = niw.run("berkshire")

    assert result["status"] == "updated"
    assert len(saved_pages) == 1
    assert saved_pages[0]["id"] == "notable-investor-berkshire"
    assert history_path.exists()
    recorded = history_path.read_text(encoding="utf-8").strip()
    assert "acc-new" in recorded


def test_run_dry_run_does_not_write_wiki_or_history(monkeypatch, tmp_path):
    history_path = tmp_path / "history.jsonl"
    monkeypatch.setattr(niw, "HISTORY_PATH", history_path)

    from providers import thirteenf
    monkeypatch.setattr(thirteenf, "latest_holdings", lambda key: {
        "filer": "berkshire", "filer_name": "Berkshire", "cik": "1", "accession": "acc-x",
        "filing_date": "2026-05-15", "total_value_usd": 1.0,
        "holdings": [_holding("APPLE INC", "AAA", "AAPL")],
    })

    calls = []
    from agent_console import wiki
    monkeypatch.setattr(wiki, "upsert_page", lambda p: calls.append(p) or p)

    result = niw.run("berkshire", dry_run=True)

    assert result["status"] == "updated"
    assert calls == []
    assert not history_path.exists()


def test_run_first_snapshot_reports_no_new_or_exited(monkeypatch, tmp_path):
    """이력이 아예 없는 첫 실행 — 전 종목을 '신규편입'으로 오인 표시/알림하면 안 된다.

    첫 조회일 뿐인데 diff_holdings(None, cur) 를 그대로 쓰면 보유 29종목 전부가
    '신규'로 잡혀 오해를 낳는다(2026-07-29 실제로 재현됨) — run() 은 기준선만 세운다.
    """
    history_path = tmp_path / "history.jsonl"
    monkeypatch.setattr(niw, "HISTORY_PATH", history_path)

    from providers import thirteenf
    monkeypatch.setattr(thirteenf, "latest_holdings", lambda key: {
        "filer": "berkshire", "filer_name": "Berkshire", "cik": "1", "accession": "acc-first",
        "filing_date": "2026-05-15", "total_value_usd": 1.0,
        "holdings": [_holding("APPLE INC", "AAA", "AAPL"), _holding("COCA COLA CO", "BBB", "KO")],
    })
    from agent_console import wiki
    monkeypatch.setattr(wiki, "upsert_page", lambda p: p)

    result = niw.run("berkshire")

    assert result["status"] == "updated"
    assert result["new"] == []
    assert result["exited"] == []


def test_run_adds_new_positions_with_ticker_to_watchlist(monkeypatch, tmp_path):
    """신규편입(diff['new'])이 감지되면 티커가 있는 항목만 관심종목에 자동 추가돼야 한다."""
    history_path = tmp_path / "history.jsonl"
    monkeypatch.setattr(niw, "HISTORY_PATH", history_path)
    history_path.write_text(
        '{"filer": "berkshire", "accession": "acc-old", '
        '"holdings": [{"issuer": "OLD CO", "cusip": "OLD", "ticker": "OLD", "weight_pct": 1.0, "value_usd": 1.0}]}\n',
        encoding="utf-8",
    )

    from providers import thirteenf
    monkeypatch.setattr(thirteenf, "latest_holdings", lambda key: {
        "filer": "berkshire", "filer_name": "Berkshire Hathaway (Warren Buffett)",
        "cik": "1067983", "accession": "acc-new", "filing_date": "2026-08-15",
        "total_value_usd": 1e9,
        "holdings": [
            _holding("NEWCO INC", "NNN", "NEWC", weight=5.0),   # 티커 있음 — 추가돼야 함
            _holding("UNRESOLVED CO", "UUU", None, weight=1.0),  # 티커 없음 — 스킵돼야 함
        ],
    })

    from agent_console import wiki
    monkeypatch.setattr(wiki, "upsert_page", lambda p: p)

    added = []
    from lib import watchlist
    monkeypatch.setattr(watchlist, "add_ticker",
                        lambda ticker, reason, source, **kw: added.append((ticker, reason, source)) or {})

    result = niw.run("berkshire")

    assert result["status"] == "updated"
    assert len(added) == 1
    assert added[0][0] == "NEWC"
    assert "Berkshire" in added[0][1]
    assert added[0][2] == "notable_investor:berkshire"


def test_run_returns_not_ok_when_fetch_fails(monkeypatch):
    from providers import thirteenf
    monkeypatch.setattr(thirteenf, "latest_holdings", lambda key: None)

    result = niw.run("berkshire")

    assert result["ok"] is False
