import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_registry_exposes_13f_and_seed_institutions():
    from reports import institution_watch as iw

    keys = {row["key"] for row in iw.list_institutions()}

    assert {"berkshire", "bridgewater", "scion", "duquesne", "founders_fund", "nps"} <= keys


def test_registry_expands_to_more_named_proxy_institutions():
    from reports import institution_watch as iw

    rows = {row["key"]: row for row in iw.list_institutions()}

    assert {"citadel", "pershing_square", "point72", "third_point", "tudor"} <= rows.keys()
    assert rows["citadel"]["category"] == "hedge_fund"
    assert rows["nps"]["category"] == "pension"


def test_registry_expanded_investors_are_real_13f_not_seed_placeholders():
    """감사 후속 — citadel 등 7곳은 실 13F CIK 연결로 source_kind='13f' 가 됨.

    founders_fund 만 펀드 빈티지별 8개 별도 필러라 단일 대표 불가해 여전히 seed."""
    from reports import institution_watch as iw

    rows = {row["key"]: row for row in iw.list_institutions()}
    for key in ("citadel", "duquesne", "pershing_square", "point72", "third_point", "tudor", "nps"):
        assert rows[key]["source_kind"] == "13f", f"{key} 가 아직 seed 임 — FILERS 배선 안 됨"
        assert rows[key]["freshness"] == "fresh"
    assert rows["founders_fund"]["source_kind"] == "seed"
    assert rows["duquesne"]["category"] == "family_office"


def test_latest_snapshot_marks_unavailable_metrics(monkeypatch):
    from reports import institution_watch as iw

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", lambda key, skip=0: {
        "filer": "berkshire",
        "filer_name": "Berkshire Hathaway (Warren Buffett)",
        "cik": "0001067983",
        "accession": "acc-1",
        "filing_date": "2026-05-15",
        "total_value_usd": 100.0,
        "holdings": [{
            "issuer": "APPLE",
            "cusip": "111111111",
            "ticker": "AAPL",
            "weight_pct": 50.0,
            "value_usd": 50.0,
            "shares": 1.0,
        }],
    })

    snapshot = iw.latest_snapshot("berkshire")

    assert snapshot["institution_key"] == "berkshire"
    assert snapshot["availability_flags"]["cash_ratio"] == "unavailable"
    assert snapshot["availability_flags"]["options_exposure"] == "unavailable"
    assert snapshot["cash_ratio"] is None
    assert snapshot["options_exposure"] is None


def test_compute_return_proxy_weights_continuously_held_price_return():
    """감사 후속 — 유빈님 아이디어: 보유종목 변동으로 수익률 추정.

    13F 자체의 value/shares 로 주당가를 역산해 연속 보유 종목만 가중평균(외부 가격
    API 불필요). 신규편입(CCC)은 수익으로 오인하지 않도록 제외."""
    from reports import institution_watch as iw

    prior = [
        {"cusip": "AAA", "value_usd": 100.0, "shares": 10.0},   # 주당 10
        {"cusip": "BBB", "value_usd": 50.0, "shares": 5.0},     # 주당 10
    ]
    current = [
        {"cusip": "AAA", "value_usd": 120.0, "shares": 10.0},   # 주당 12 (+20%)
        {"cusip": "BBB", "value_usd": 45.0, "shares": 5.0},     # 주당 9  (-10%)
        {"cusip": "CCC", "value_usd": 30.0, "shares": 3.0},     # 신규편입 — 제외
    ]
    # 가중치: AAA 100/150=2/3, BBB 50/150=1/3 → 2/3*0.20 + 1/3*(-0.10) = 0.10 (분수 단위)
    out = iw._compute_return_proxy(current, prior)
    assert out == pytest.approx(0.10, abs=0.0001)


def test_compute_return_proxy_none_when_no_cusip_overlap():
    from reports import institution_watch as iw

    prior = [{"cusip": "AAA", "value_usd": 100.0, "shares": 10.0}]
    current = [{"cusip": "ZZZ", "value_usd": 50.0, "shares": 5.0}]
    assert iw._compute_return_proxy(current, prior) is None


def test_compute_return_proxy_none_when_prior_missing():
    from reports import institution_watch as iw

    assert iw._compute_return_proxy([{"cusip": "AAA", "value_usd": 1.0, "shares": 1.0}], []) is None
    assert iw._compute_return_proxy([], None) is None
    assert iw._compute_return_proxy(None, None) is None


def test_compute_return_proxy_none_when_coverage_too_thin():
    """연속보유 비중이 대부분 물갈이(20% 미만)면 근사치를 신뢰할 수 없어 None."""
    from reports import institution_watch as iw

    prior = [
        {"cusip": "AAA", "value_usd": 10.0, "shares": 1.0},
        {"cusip": "BBB", "value_usd": 90.0, "shares": 9.0},
    ]
    current = [
        {"cusip": "AAA", "value_usd": 11.0, "shares": 1.0},    # prior 의 10%만 연속보유
        {"cusip": "CCC", "value_usd": 200.0, "shares": 20.0},  # BBB 전량청산, CCC 신규
    ]
    assert iw._compute_return_proxy(current, prior) is None


def test_normalize_13f_snapshot_populates_return_proxy_when_prior_given():
    from reports import institution_watch as iw

    meta = {"key": "berkshire", "display_name": "Berkshire", "category": "holding_company",
            "freshness": "fresh", "primary_sources": ["13f"],
            "metric_capabilities": ["holdings"], "refresh_policy": "quarterly", "confidence": 0.95}
    raw = {"filer_name": "Berkshire", "filing_date": "2026-08-14", "accession": "acc-2", "cik": "0001067983",
          "total_value_usd": 120.0,
          "holdings": [{"issuer": "APPLE", "cusip": "AAA", "ticker": "AAPL",
                        "value_usd": 120.0, "shares": 10.0, "weight_pct": 100.0}]}
    prior = {"holdings": [{"issuer": "APPLE", "cusip": "AAA", "value_usd": 100.0, "shares": 10.0}]}

    snap = iw._normalize_13f_snapshot(meta, raw, prior=prior)

    assert snap["return_proxy"] == pytest.approx(0.20)
    assert snap["availability_flags"]["return_proxy"] == "proxy"
    assert any("Return proxy" in n for n in snap["notes"])


def test_normalize_13f_snapshot_return_proxy_none_without_prior():
    from reports import institution_watch as iw

    meta = {"key": "berkshire", "display_name": "Berkshire", "category": "holding_company",
            "freshness": "fresh", "primary_sources": ["13f"],
            "metric_capabilities": ["holdings"], "refresh_policy": "quarterly", "confidence": 0.95}
    raw = {"filer_name": "Berkshire", "filing_date": "2026-08-14",
          "holdings": [{"issuer": "APPLE", "cusip": "AAA", "value_usd": 120.0, "shares": 10.0, "weight_pct": 100.0}]}

    snap = iw._normalize_13f_snapshot(meta, raw)

    assert snap["return_proxy"] is None
    assert snap["availability_flags"]["return_proxy"] == "unavailable"


def test_screen_position_changes_classifies_new_increased_decreased(monkeypatch):
    """감사 후속 — 유빈님 요청: 여러 기관이 공통으로 신규편입/증가/감소한 종목 교차 스크리닝."""
    from reports import institution_watch as iw

    def fake_latest_holdings(key, skip=0):
        data = {
            "alpha": {
                0: [{"cusip": "NVDA1", "ticker": "NVDA", "issuer": "NVIDIA",
                     "value_usd": 150.0, "shares": 10.0, "weight_pct": 15.0},
                    {"cusip": "AAPL1", "ticker": "AAPL", "issuer": "APPLE",
                     "value_usd": 50.0, "shares": 5.0, "weight_pct": 5.0}],
                1: [{"cusip": "NVDA1", "ticker": "NVDA", "issuer": "NVIDIA",
                     "value_usd": 100.0, "shares": 10.0, "weight_pct": 10.0},
                    {"cusip": "AAPL1", "ticker": "AAPL", "issuer": "APPLE",
                     "value_usd": 100.0, "shares": 10.0, "weight_pct": 10.0}],
            },
            "beta": {
                0: [{"cusip": "NVDA1", "ticker": "NVDA", "issuer": "NVIDIA",
                     "value_usd": 200.0, "shares": 20.0, "weight_pct": 20.0},
                    {"cusip": "TSLA1", "ticker": "TSLA", "issuer": "TESLA",
                     "value_usd": 50.0, "shares": 5.0, "weight_pct": 5.0}],
                1: [{"cusip": "NVDA1", "ticker": "NVDA", "issuer": "NVIDIA",
                     "value_usd": 140.0, "shares": 20.0, "weight_pct": 14.0}],
            },
        }
        return {"holdings": data[key][skip]}

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", fake_latest_holdings)

    out = iw.screen_position_changes(["alpha", "beta"])

    new_tickers = {r["ticker"] for r in out["new_buys"]}
    inc_tickers = {r["ticker"] for r in out["increased"]}
    dec_tickers = {r["ticker"] for r in out["decreased"]}
    assert new_tickers == {"TSLA"}
    assert inc_tickers == {"NVDA"}
    assert dec_tickers == {"AAPL"}

    nvda = next(r for r in out["increased"] if r["ticker"] == "NVDA")
    assert nvda["count"] == 2
    assert set(nvda["institutions"]) == {"alpha", "beta"}
    assert nvda["avg_delta_pct"] > 0


def test_screen_position_changes_ignores_small_moves_below_threshold(monkeypatch):
    from reports import institution_watch as iw

    def fake_latest_holdings(key, skip=0):
        rows = {
            0: [{"cusip": "X1", "ticker": "X", "issuer": "X CORP",
                 "value_usd": 100.3, "shares": 10.0, "weight_pct": 10.03}],
            1: [{"cusip": "X1", "ticker": "X", "issuer": "X CORP",
                 "value_usd": 100.0, "shares": 10.0, "weight_pct": 10.0}],
        }
        return {"holdings": rows[skip]}

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", fake_latest_holdings)

    out = iw.screen_position_changes(["alpha"])

    assert out["increased"] == []
    assert out["decreased"] == []
    assert out["new_buys"] == []


def test_screen_position_changes_skips_institution_with_no_data(monkeypatch):
    from reports import institution_watch as iw

    def fake_latest_holdings(key, skip=0):
        if key == "broken":
            return None
        return {"holdings": [{"cusip": "A1", "ticker": "A", "issuer": "A CORP",
                              "value_usd": 100.0, "shares": 10.0, "weight_pct": 100.0}]}

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", fake_latest_holdings)

    out = iw.screen_position_changes(["broken", "alpha"])

    assert out == {"new_buys": [], "increased": [], "decreased": []}


def test_screen_position_changes_caps_each_bucket_at_ten(monkeypatch):
    from reports import institution_watch as iw

    def fake_latest_holdings(key, skip=0):
        if skip == 1:
            return {"holdings": []}
        rows = [{"cusip": f"C{i}", "ticker": f"T{i}", "issuer": f"CO{i}",
                 "value_usd": 100.0, "shares": 10.0, "weight_pct": 100.0 / 15}
                for i in range(15)]
        return {"holdings": rows}

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", fake_latest_holdings)

    out = iw.screen_position_changes(["alpha"])

    assert len(out["new_buys"]) == 10


def test_explain_screen_uses_llm_when_available(monkeypatch):
    """감사 후속 — 스크리닝 결과를 LLM이 '왜 이럴 수 있는지' 해설(버튼 게이팅용)."""
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "_try_llm_prompt_for_screen",
                        lambda prompt: '{"summary": "AI 랠리 지속 베팅", "confidence": 0.7}')

    screen = {"new_buys": [{"ticker": "SMCI", "count": 3}], "increased": [], "decreased": []}
    congress = {"bought": [{"ticker": "NVDA", "member_count": 5}], "sold": []}

    out = iw.explain_screen(screen, congress)

    assert out["mode"] == "llm"
    assert out["summary"] == "AI 랠리 지속 베팅"
    assert out["confidence"] == 0.7


def test_explain_screen_falls_back_to_facts_when_llm_unavailable(monkeypatch):
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "_try_llm_prompt_for_screen", lambda prompt: None)

    screen = {"new_buys": [{"ticker": "SMCI", "count": 3, "name": "Super Micro",
                            "institutions": ["berkshire"]}],
             "increased": [], "decreased": []}
    congress = {"bought": [], "sold": []}

    out = iw.explain_screen(screen, congress)

    assert out["mode"] == "heuristic"
    assert "SMCI" in out["summary"] or "Super Micro" in out["summary"]


def test_explain_screen_empty_inputs_stay_heuristic(monkeypatch):
    from reports import institution_watch as iw

    def _boom(prompt):
        raise AssertionError("빈 입력인데 LLM 호출됨")
    monkeypatch.setattr(iw, "_try_llm_prompt_for_screen", _boom)

    out = iw.explain_screen({"new_buys": [], "increased": [], "decreased": []},
                            {"bought": [], "sold": []})

    assert out["mode"] == "heuristic"


def test_latest_snapshot_fetches_prior_quarter_for_return_proxy(monkeypatch):
    """latest_snapshot 이 13F 필러에 대해 직전 분기(skip=1)도 함께 가져와 return_proxy 계산."""
    from reports import institution_watch as iw

    calls = []

    def fake_latest_holdings(key, skip=0):
        calls.append(skip)
        if skip == 0:
            return {"filer_name": "Berkshire", "filing_date": "2026-08-14",
                    "holdings": [{"cusip": "AAA", "value_usd": 120.0, "shares": 10.0, "weight_pct": 100.0}]}
        return {"filer_name": "Berkshire", "filing_date": "2026-05-15",
                "holdings": [{"cusip": "AAA", "value_usd": 100.0, "shares": 10.0, "weight_pct": 100.0}]}

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", fake_latest_holdings)

    snapshot = iw.latest_snapshot("berkshire")

    assert sorted(calls) == [0, 1]
    assert snapshot["return_proxy"] == pytest.approx(0.20)


def test_compare_institutions_keeps_missing_metrics_explicit():
    from reports import institution_watch as iw

    comparison = iw.compare_institutions(
        ["berkshire", "nps"],
        snapshots={
            "berkshire": {
                "institution_key": "berkshire",
                "display_name": "Berkshire Hathaway",
                "source_kind": "13f",
                "freshness": "fresh",
                "holdings_count": 2,
                "top_holdings": [],
                "portfolio_concentration": 0.5,
                "cash_ratio": None,
                "options_exposure": None,
                "reported_return": None,
                "return_proxy": None,
                "availability_flags": {
                    "cash_ratio": "unavailable",
                    "options_exposure": "unavailable",
                    "reported_return": "unavailable",
                    "return_proxy": "unavailable",
                },
                "notes": [],
            },
            "nps": {
                "institution_key": "nps",
                "display_name": "National Pension Service",
                "source_kind": "seed",
                "freshness": "proxy",
                "holdings_count": 3,
                "top_holdings": [],
                "portfolio_concentration": None,
                "cash_ratio": None,
                "options_exposure": None,
                "reported_return": None,
                "return_proxy": 8.2,
                "availability_flags": {
                    "cash_ratio": "unavailable",
                    "options_exposure": "unavailable",
                    "reported_return": "unavailable",
                    "return_proxy": "proxy",
                },
                "notes": [],
            },
        },
    )

    rows = {row["institution_key"]: row for row in comparison["rows"]}
    assert rows["berkshire"]["cash_ratio"] is None
    assert rows["berkshire"]["cash_ratio_flag"] == "unavailable"
    assert rows["nps"]["return_proxy"] == 8.2
    assert rows["nps"]["return_proxy_flag"] == "proxy"


def test_latest_snapshot_real_seed_path_keeps_null_metrics_unavailable():
    """nps 는 감사 후속으로 실 13F 연결됨 — founders_fund 가 남은 seed 대표 사례
    (펀드 빈티지별 8개 별도 필러라 단일 CIK 대표 불가, 2026-08-15)."""
    from reports import institution_watch as iw

    snapshot = iw.latest_snapshot("founders_fund")

    assert snapshot["institution_key"] == "founders_fund"
    assert snapshot["source_kind"] == "seed"
    assert snapshot["freshness"] == "proxy"
    assert snapshot["cash_ratio"] is None
    assert snapshot["reported_return"] is None
    assert snapshot["return_proxy"] is None
    assert snapshot["availability_flags"]["cash_ratio"] == "unavailable"
    assert snapshot["availability_flags"]["reported_return"] == "unavailable"
    assert snapshot["availability_flags"]["return_proxy"] == "unavailable"


def test_build_snapshot_digest_keeps_unverified_seed_pages_as_draft():
    from reports import institution_watch as iw

    snapshot = iw.latest_snapshot("founders_fund")
    page = iw.build_snapshot_digest(snapshot, {"new": [], "exited": []})

    assert page["kind"] == "note"
    assert page["status"] == "draft"
    assert page["source_refs"] == []
    assert "source_digest" not in page["tags"]


def test_build_snapshot_digest_attaches_sec_provenance_for_13f(monkeypatch):
    from reports import institution_watch as iw

    monkeypatch.setattr(iw.thirteenf, "latest_holdings", lambda key, skip=0: {
        "filer": "berkshire",
        "filer_name": "Berkshire Hathaway (Warren Buffett)",
        "cik": "0001067983",
        "accession": "0000950123-26-003958",
        "filing_date": "2026-05-15",
        "total_value_usd": 100.0,
        "holdings": [{
            "issuer": "APPLE",
            "cusip": "111111111",
            "ticker": "AAPL",
            "weight_pct": 50.0,
            "value_usd": 50.0,
            "shares": 1.0,
        }],
    })

    snapshot = iw.latest_snapshot("berkshire")
    page = iw.build_snapshot_digest(snapshot, {"new": [], "exited": []})

    assert page["kind"] == "source_digest"
    assert page["status"] == "reviewed"
    assert len(page["source_refs"]) == 2
    assert page["source_refs"][0].startswith("https://www.sec.gov/cgi-bin/browse-edgar")
    assert page["source_refs"][1].endswith("/000095012326003958/")


def test_build_common_moves_digest_is_distillable_and_carries_provenance():
    from reports import institution_watch as iw

    snapshots = [
        {
            "institution_key": "berkshire",
            "display_name": "Berkshire Hathaway",
            "source_kind": "13f",
            "cik": "0001067983",
            "accession": "0000950123-26-003958",
        },
        {
            "institution_key": "bridgewater",
            "display_name": "Bridgewater Associates",
            "source_kind": "13f",
            "cik": "0001350694",
            "accession": "0000950123-26-003959",
        },
    ]
    page = iw.build_common_moves_digest(
        snapshots,
        {"rows": [{"institution_key": "berkshire"}, {"institution_key": "bridgewater"}]},
        {
            "summary": "Both institutions look defensive.",
            "shared_moves": ["Trim cyclical exposure"],
            "divergences": ["Only Berkshire has disclosed holdings detail"],
            "confidence": 0.9,
        },
    )

    assert page["kind"] == "source_digest"
    assert page["status"] == "reviewed"
    assert "wiki:institution-watch-berkshire" in page["source_refs"]
    assert "wiki:institution-watch-bridgewater" in page["source_refs"]
    assert len(page["source_refs"]) == 6
    assert "source_digest" in page["tags"]
    assert "llm_synthesis" in page["tags"]
    assert page["confidence"] == 0.6


def test_build_common_moves_digest_stays_draft_for_mixed_source_inputs():
    from reports import institution_watch as iw

    snapshots = [
        {
            "institution_key": "berkshire",
            "display_name": "Berkshire Hathaway",
            "source_kind": "13f",
            "cik": "0001067983",
            "accession": "0000950123-26-003958",
        },
        {
            "institution_key": "nps",
            "display_name": "National Pension Service",
            "source_kind": "seed",
        },
    ]
    page = iw.build_common_moves_digest(
        snapshots,
        {"rows": [{"institution_key": "berkshire"}, {"institution_key": "nps"}]},
        {
            "summary": "Mixed provenance should stay draft.",
            "shared_moves": ["Trim cyclical exposure"],
            "divergences": ["Only Berkshire has disclosed holdings detail"],
            "confidence": 0.9,
        },
    )

    assert page["kind"] == "note"
    assert page["status"] == "draft"
    assert page["source_refs"] == []
    assert "source_digest" not in page["tags"]


def test_run_uses_analysis_keys_for_source_backed_pattern_digest(monkeypatch, tmp_path):
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "HISTORY_PATH", tmp_path / "history.jsonl")

    snapshots = {
        "berkshire": {
            "institution_key": "berkshire",
            "display_name": "Berkshire Hathaway",
            "source_kind": "13f",
            "category": "holding_company",
            "freshness": "fresh",
            "cik": "0001067983",
            "accession": "0000950123-26-003958",
            "holdings_count": 1,
            "top_holdings": [{"ticker": "AAPL", "issuer": "APPLE", "weight_pct": 10.0, "value_usd": 100.0}],
            "portfolio_concentration": 0.4,
            "cash_ratio": None,
            "options_exposure": None,
            "reported_return": None,
            "return_proxy": None,
            "primary_sources": ["13f"],
            "metric_capabilities": ["holdings", "concentration", "source_refs"],
            "refresh_policy": "quarterly",
            "confidence": 0.9,
            "availability_flags": {
                "cash_ratio": "unavailable",
                "options_exposure": "unavailable",
                "reported_return": "unavailable",
                "return_proxy": "unavailable",
            },
            "notes": [],
        },
        "bridgewater": {
            "institution_key": "bridgewater",
            "display_name": "Bridgewater Associates",
            "source_kind": "13f",
            "category": "hedge_fund",
            "freshness": "fresh",
            "cik": "0001350694",
            "accession": "0000950123-26-003959",
            "holdings_count": 1,
            "top_holdings": [{"ticker": "MSFT", "issuer": "MICROSOFT", "weight_pct": 10.0, "value_usd": 100.0}],
            "portfolio_concentration": 0.4,
            "cash_ratio": None,
            "options_exposure": None,
            "reported_return": None,
            "return_proxy": None,
            "primary_sources": ["13f"],
            "metric_capabilities": ["holdings", "concentration", "source_refs"],
            "refresh_policy": "quarterly",
            "confidence": 0.9,
            "availability_flags": {
                "cash_ratio": "unavailable",
                "options_exposure": "unavailable",
                "reported_return": "unavailable",
                "return_proxy": "unavailable",
            },
            "notes": [],
        },
        "nps": {
            "institution_key": "nps",
            "display_name": "National Pension Service",
            "source_kind": "seed",
            "category": "pension",
            "freshness": "proxy",
            "holdings_count": 0,
            "top_holdings": [],
            "portfolio_concentration": None,
            "cash_ratio": None,
            "options_exposure": None,
            "reported_return": None,
            "return_proxy": None,
            "primary_sources": ["annual_report"],
            "metric_capabilities": ["holdings", "reported_return", "return_proxy"],
            "refresh_policy": "manual",
            "confidence": 0.45,
            "availability_flags": {
                "cash_ratio": "unavailable",
                "options_exposure": "unavailable",
                "reported_return": "unavailable",
                "return_proxy": "unavailable",
            },
            "notes": [],
        },
    }
    monkeypatch.setattr(iw, "latest_snapshot", lambda key: snapshots.get(key))
    monkeypatch.setattr(iw, "build_common_moves_analysis", lambda snapshots, comparison: {
        "summary": "공통 보유를 요약했습니다.",
        "shared_moves": ["AAPL이 반복됩니다."],
        "divergences": ["기관별 공개 범위가 다릅니다."],
        "confidence": 0.5,
        "mode": "heuristic",
    })

    saved_pages = []
    from agent_console import wiki
    monkeypatch.setattr(wiki, "upsert_page", lambda page: saved_pages.append(page) or page)
    monkeypatch.setattr(wiki, "rebuild_artifacts", lambda: None)

    result = iw.run(["berkshire", "bridgewater", "nps"], dry_run=False, analysis_keys=["berkshire", "bridgewater"])

    assert result["ok"] is True
    assert any(page["kind"] == "source_digest" and page["id"] == "institution-watch-common-moves"
               for page in saved_pages)
    assert any(page["id"] == "institution-watch-berkshire" for page in saved_pages)
    assert any(page["id"] == "institution-watch-nps" for page in saved_pages)


def test_main_routes_cron_to_source_backed_analysis_subset(monkeypatch):
    from reports import institution_watch as iw

    monkeypatch.setattr(iw, "list_institutions", lambda: [
        {"key": "berkshire", "source_kind": "13f"},
        {"key": "bridgewater", "source_kind": "13f"},
        {"key": "nps", "source_kind": "seed"},
    ])
    monkeypatch.setattr(iw, "source_backed_institution_keys", lambda: ["berkshire", "bridgewater"])

    captured = {}

    def fake_run(keys, *, dry_run=False, analysis_keys=None):
        captured["keys"] = list(keys)
        captured["dry_run"] = dry_run
        captured["analysis_keys"] = list(analysis_keys or [])
        return {
            "ok": True,
            "selected_keys": list(keys),
            "updated": [],
            "unchanged": [],
            "failed": [],
            "analysis": {},
            "pages": [{"id": "institution-watch-common-moves"}],
        }

    monkeypatch.setattr(iw, "run", fake_run)

    exit_code = iw.main(["--dry-run"])

    assert exit_code == 0
    assert captured["keys"] == ["berkshire", "bridgewater", "nps"]
    assert captured["dry_run"] is True
    assert captured["analysis_keys"] == ["berkshire", "bridgewater"]


# ── 신규 편입 텔레그램 알림 (crontab 교체 전 기능 동등성 확보) ────────────────
# notable_investors_wiki.py 는 신규 편입 감지 시 텔레그램으로 즉시 알렸는데,
# institution_watch.main() 은 관심종목 자동추가(watchlist)는 승계했지만 알림은
# 없었다. notable_investors_wiki→institution_watch 로 크론을 교체하면 이 알림이
# 조용히 사라지므로, 스왑 전에 institution_watch 에도 알림을 이식한다.

def test_build_new_position_alert_formats_updated_institutions():
    from reports import institution_watch as iw

    result = {
        "updated": [
            {"institution_key": "berkshire",
             "new": [{"issuer": "APPLE INC", "ticker": "AAPL", "weight_pct": 5.2, "value_usd": 3e9}],
             "exited": []},
        ],
        "snapshots_by_key": {
            "berkshire": {"display_name": "Berkshire Hathaway", "filing_date": "2026-08-14"},
        },
    }
    text = iw._build_new_position_alert(result)
    assert text is not None
    assert "Berkshire Hathaway" in text
    assert "AAPL" in text
    assert "🆕" in text


def test_build_new_position_alert_includes_exits():
    from reports import institution_watch as iw

    result = {
        "updated": [
            {"institution_key": "scion",
             "new": [],
             "exited": [{"issuer": "TESLA INC", "ticker": "TSLA", "weight_pct": 3.0, "value_usd": 1e9}]},
        ],
        "snapshots_by_key": {
            "scion": {"display_name": "Scion Asset Management", "filing_date": "2026-08-14"},
        },
    }
    text = iw._build_new_position_alert(result)
    assert text is not None
    assert "TSLA" in text
    assert "📤" in text


def test_build_new_position_alert_none_when_nothing_new():
    from reports import institution_watch as iw

    result = {
        "updated": [{"institution_key": "berkshire", "new": [], "exited": []}],
        "snapshots_by_key": {"berkshire": {"display_name": "Berkshire Hathaway"}},
    }
    assert iw._build_new_position_alert(result) is None


def test_build_new_position_alert_none_when_no_updates():
    from reports import institution_watch as iw
    assert iw._build_new_position_alert({"updated": [], "snapshots_by_key": {}}) is None


def test_main_sends_telegram_when_new_positions_detected(monkeypatch, tmp_path):
    from reports import institution_watch as iw

    sent = {}

    def _fake_send_telegram(text, **kw):
        sent["text"] = text
        return {"ok": True}

    monkeypatch.setattr(iw, "run", lambda *a, **k: {
        "selected_keys": ["berkshire"], "updated": [
            {"institution_key": "berkshire",
             "new": [{"issuer": "APPLE INC", "ticker": "AAPL", "weight_pct": 5.2, "value_usd": 3e9}],
             "exited": []},
        ],
        "unchanged": [], "failed": [], "pages": [],
        "snapshots_by_key": {"berkshire": {"display_name": "Berkshire Hathaway", "filing_date": "2026-08-14"}},
    })
    monkeypatch.setattr(iw, "_default_run_keys", lambda: ["berkshire"])
    import notify
    monkeypatch.setattr(notify, "send_telegram", _fake_send_telegram)

    rc = iw.main([])
    assert rc == 0
    assert "AAPL" in sent.get("text", "")


def test_main_skips_telegram_in_dry_run(monkeypatch):
    from reports import institution_watch as iw

    called = []
    import notify
    monkeypatch.setattr(notify, "send_telegram", lambda *a, **k: called.append(1))
    monkeypatch.setattr(iw, "run", lambda *a, **k: {
        "selected_keys": ["berkshire"], "updated": [
            {"institution_key": "berkshire",
             "new": [{"issuer": "APPLE INC", "ticker": "AAPL", "weight_pct": 5.2, "value_usd": 3e9}],
             "exited": []},
        ],
        "unchanged": [], "failed": [], "pages": [],
        "snapshots_by_key": {"berkshire": {"display_name": "Berkshire Hathaway"}},
    })
    monkeypatch.setattr(iw, "_default_run_keys", lambda: ["berkshire"])

    iw.main(["--dry-run"])
    assert called == []


def test_main_skips_telegram_when_nothing_new(monkeypatch):
    from reports import institution_watch as iw

    called = []
    import notify
    monkeypatch.setattr(notify, "send_telegram", lambda *a, **k: called.append(1))
    monkeypatch.setattr(iw, "run", lambda *a, **k: {
        "selected_keys": ["berkshire"], "updated": [],
        "unchanged": [{"institution_key": "berkshire"}], "failed": [], "pages": [],
        "snapshots_by_key": {},
    })
    monkeypatch.setattr(iw, "_default_run_keys", lambda: ["berkshire"])

    iw.main([])
    assert called == []


def test_main_telegram_failure_does_not_crash(monkeypatch):
    from reports import institution_watch as iw

    import notify
    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(notify, "send_telegram", _boom)
    monkeypatch.setattr(iw, "run", lambda *a, **k: {
        "selected_keys": ["berkshire"], "updated": [
            {"institution_key": "berkshire",
             "new": [{"issuer": "APPLE INC", "ticker": "AAPL", "weight_pct": 5.2, "value_usd": 3e9}],
             "exited": []},
        ],
        "unchanged": [], "failed": [], "pages": [],
        "snapshots_by_key": {"berkshire": {"display_name": "Berkshire Hathaway"}},
    })
    monkeypatch.setattr(iw, "_default_run_keys", lambda: ["berkshire"])

    rc = iw.main([])
    assert rc == 0          # 알림 실패해도 크론 자체는 성공 취급(무시)
