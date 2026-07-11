"""ticker_names.py 단위 테스트 — resolve(한/영/티커)·display_name·label·캐시 (무네트워크).

allow_net=False 로 큐레이트 시드 + 디스크캐시만 검증(yfinance 미호출). 캐시 경로는 tmp 로 격리.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ticker_names  # noqa: E402


# ── resolve: 한글명·영문명·티커 어느 것으로도 동일 종목 ──────────────────
def test_resolve_micron_all_three():
    # 유빈님 예시: 마이크론 · micron · MU 모두 MU
    assert ticker_names.resolve("마이크론") == "MU"
    assert ticker_names.resolve("micron") == "MU"
    assert ticker_names.resolve("MICRON") == "MU"
    assert ticker_names.resolve("MU") == "MU"
    assert ticker_names.resolve("mu") == "MU"


def test_resolve_nvidia():
    assert ticker_names.resolve("엔비디아") == "NVDA"
    assert ticker_names.resolve("nvidia") == "NVDA"
    assert ticker_names.resolve("NVDA") == "NVDA"


def test_resolve_alias_and_kr():
    assert ticker_names.resolve("구글") == "GOOGL"
    assert ticker_names.resolve("google") == "GOOGL"
    assert ticker_names.resolve("삼성전자") == "005930.KS"
    assert ticker_names.resolve("005930.KS") == "005930.KS"


def test_resolve_unknown_returns_none():
    assert ticker_names.resolve("존재하지않는종목") is None
    assert ticker_names.resolve("") is None
    assert ticker_names.resolve("   ") is None


# ── K1 대형주 확장: 버크셔·월마트·비자 등 (한/영/티커) ────────────────────
def test_resolve_expanded_largecaps():
    cases = {
        "버크셔": "BRK-B", "버크셔 해서웨이": "BRK-B", "berkshire": "BRK-B", "BRK-B": "BRK-B",
        "월마트": "WMT", "walmart": "WMT", "WMT": "WMT",
        "비자": "V", "visa": "V",
        "코카콜라": "KO", "존슨앤존슨": "JNJ", "제이피모건": "JPM", "골드만삭스": "GS",
        "리비안": "RIVN", "코인베이스": "COIN", "록히드마틴": "LMT", "슈왑 배당": "SCHD",
    }
    for q, t in cases.items():
        assert ticker_names.resolve(q) == t, f"{q} → {ticker_names.resolve(q)} (기대 {t})"


def test_single_letter_tickers_exact_not_partial():
    # 단문자 티커는 정확 티커로 resolve — 부분매칭으로 NVDA 등에 흡수되면 안 됨
    for t in ("V", "C", "F", "T"):
        assert ticker_names.resolve(t) == t


def test_universe_expanded_covers_largecaps():
    u = set(ticker_names.universe())
    for t in ("BRK-B", "WMT", "V", "JNJ", "COIN", "RIVN", "SCHD", "JEPQ"):
        assert t in u
    assert len(ticker_names.universe()) >= 110  # 55 → 확장(대형주 시드)


def test_label_expanded_names():
    assert ticker_names.label("BRK-B") == "Berkshire Hathaway (BRK-B)"
    assert ticker_names.label("V") == "Visa (V)"
    assert ticker_names.label("WMT") == "Walmart (WMT)"


# ── K2 normalize_input: 자유입력(티커/이름/garbage) → 정규 티커|None ──────
def test_normalize_input_names_and_tickers():
    assert ticker_names.normalize_input("버크셔") == "BRK-B"
    assert ticker_names.normalize_input("berkshire") == "BRK-B"
    assert ticker_names.normalize_input("월마트") == "WMT"
    assert ticker_names.normalize_input("visa") == "V"
    assert ticker_names.normalize_input("삼성전자") == "005930.KS"
    assert ticker_names.normalize_input("A069500") == "069500.KS"
    assert ticker_names.normalize_input("069500") == "069500.KS"
    assert ticker_names.normalize_input("KODEX 200") == "069500.KS"


def test_normalize_input_freeform_literal_ticker():
    # 시드 밖 티커도 티커 형태면 리터럴 통과(롱테일) · 대소문자 정규화
    assert ticker_names.normalize_input("DDOG") == "DDOG"       # 시드 밖
    assert ticker_names.normalize_input("coin") == "COIN"       # 시드 → 대문자
    assert ticker_names.normalize_input("brk-b") == "BRK-B"


def test_normalize_input_no_partial_pollution():
    # 티커 형태 입력은 부분매칭 오염 없음 ('NET'→NFLX 아님)
    assert ticker_names.normalize_input("NET") == "NET"


def test_normalize_input_invalid_returns_none():
    for q in ("", "   ", "ㅁㄴㅇㄹ", "123!!", "이건진짜없는종목이름"):
        assert ticker_names.normalize_input(q) is None


# ── L1 S&P500 유니버스 (미국 시총 상위 ~500 검색) ────────────────────────
def test_universe_covers_sp500():
    u = set(ticker_names.universe())
    assert len(u) >= 500                       # 118 → S&P500 병합
    for t in ("A", "ACGL"):                    # 큐레이트 밖 롱테일도 유니버스에
        assert t in u, t


def test_resolve_sp500_by_name_and_ticker():
    # 영문 회사명·티커로 S&P500 롱테일 resolve (한글은 큐레이트 인기주만)
    assert ticker_names.resolve("Agilent") == "A"
    assert ticker_names.resolve("Arch Capital") == "ACGL"
    assert ticker_names.resolve("ACGL") == "ACGL"
    assert ticker_names.normalize_input("Agilent Technologies") == "A"


def test_curated_names_take_precedence_over_sp500():
    # 큐레이트 EN 이 S&P500 시드보다 우선(깔끔한 표시명 유지)
    assert ticker_names.display_name("AAPL", allow_net=False) == "Apple"
    assert ticker_names.display_name("BRK-B", allow_net=False) == "Berkshire Hathaway"


# ── display_name: US=영문 · KR(.KS)=한글 ────────────────────────────────
def test_display_name_us_english():
    assert ticker_names.display_name("MSFT", allow_net=False) == "Microsoft"
    assert ticker_names.display_name("NVDA", allow_net=False) == "NVIDIA"
    assert ticker_names.display_name("MU", allow_net=False) == "Micron Technology"


def test_display_name_kr_korean():
    assert ticker_names.display_name("005930.KS", allow_net=False) == "삼성전자"
    assert ticker_names.display_name("000660.KS", allow_net=False) == "SK하이닉스"


def test_display_name_unknown_offline_none():
    # 미큐레이트 + 캐시 없음 + 무네트워크 → None (예외 없이)
    assert ticker_names.display_name("ZZZZ", allow_net=False) is None
    assert ticker_names.display_name("", allow_net=False) is None


# ── label: `회사명 (티커)` ─────────────────────────────────────────────
def test_label_format():
    assert ticker_names.label("MSFT") == "Microsoft (MSFT)"
    assert ticker_names.label("NVDA", "NVIDIA") == "NVIDIA (NVDA)"
    assert ticker_names.label("005930.KS") == "삼성전자 (005930.KS)"


def test_label_ticker_only_when_no_name():
    # 이름 미상 → 티커만
    assert ticker_names.label("ZZZZ", allow_net=False) == "ZZZZ"
    # 이름 == 티커 → 티커만 (SAP (SAP) 방지)
    assert ticker_names.label("SAP", "SAP") == "SAP"
    assert ticker_names.label("") == ""


def test_label_maxlen_truncates():
    out = ticker_names.label("MU", "Micron Technology", maxlen=8)
    assert out.endswith(" (MU)") and "…" in out
    assert len(out.split(" (")[0]) <= 8


# ── search: 부분일치 후보 ──────────────────────────────────────────────
def test_search_candidates():
    hits = ticker_names.search("마이", limit=5)
    tickers = [t for t, _ in hits]
    assert "MU" in tickers  # 마이크론


# ── universe / search_label (대시보드 통합 검색) ────────────────────────
def test_universe_contains_holdings_and_popular():
    u = ticker_names.universe()
    assert "MU" in u and "NVDA" in u and "005930.KS" in u
    assert "NVDL" in u and "TSLL" in u
    assert u == sorted(u)  # 정렬됨


def test_single_stock_leverage_names_and_aliases():
    assert ticker_names.resolve("엔비디아 2배") == "NVDL"
    assert ticker_names.resolve("테슬라 레버리지") == "TSLL"
    assert ticker_names.resolve("MSTR 2배") == "MSTU"
    assert ticker_names.label("NVDL") == "GraniteShares 2x Long NVDA (NVDL)"


def test_search_label_appends_korean_for_typeahead():
    # US: 영문 (티커) · 한글 → 한글 타입어헤드 매칭
    lab = ticker_names.search_label("MU")
    assert "Micron Technology (MU)" in lab and "마이크론" in lab
    # KR: 이미 한글명 → 중복 별칭 안 붙음
    assert ticker_names.search_label("005930.KS") == "삼성전자 (005930.KS)"


# ── yfinance 디스크캐시 R/W (tmp 격리·무네트워크) ──────────────────────
def test_cache_roundtrip(monkeypatch, tmp_path):
    import time
    p = str(tmp_path / "ticker_names.json")
    monkeypatch.setattr(ticker_names, "_CACHE_PATH", p)
    monkeypatch.setattr(ticker_names, "_yf_cache", None)
    ticker_names._save_cache({"ZZZZ": {"name": "Zeta Corp", "ts": time.time()}})
    monkeypatch.setattr(ticker_names, "_yf_cache", None)  # 강제 재로드
    # 캐시된 이름은 allow_net=False 여도 반환
    assert ticker_names.display_name("ZZZZ", allow_net=False) == "Zeta Corp"


def test_cache_expired_offline_returns_stale(monkeypatch, tmp_path):
    # 만료된 캐시라도 무네트워크면 stale 반환(회사명은 안정적 — None보다 나음).
    # 네트워크 허용 시에만 재조회 시도.
    p = str(tmp_path / "tn.json")
    monkeypatch.setattr(ticker_names, "_CACHE_PATH", p)
    monkeypatch.setattr(ticker_names, "_yf_cache", None)
    ticker_names._save_cache({"ZZZZ": {"name": "Old", "ts": 0}})  # 만료(ts=0)
    monkeypatch.setattr(ticker_names, "_yf_cache", None)
    assert ticker_names.display_name("ZZZZ", allow_net=False) == "Old"


def test_kr_etf_seed_search_and_display():
    """국내 ETF 시드 병합 — 한글명 검색·resolve·display (KODEX/TIGER 등 ~1,100)."""
    import ticker_names as tn
    assert tn.resolve("KODEX 200") == "069500.KS"
    assert tn.resolve("TIGER 미국S&P500") == "360750.KS"
    assert tn.display_name("069500.KS", allow_net=False) == "KODEX 200"
    assert tn.normalize_input("069500.KS") == "069500.KS"
    hits = dict(tn.search("KODEX", limit=8))
    assert "069500.KS" in hits
