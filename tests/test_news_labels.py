#!/usr/bin/env python3
"""test_news_labels.py — LLM 뉴스 구조화 라벨 + news 축 (무네트워크).

검증 핵심: 환각 방어(입력 밖 티커 폐기)·enum/범위 검증·point-in-time(미래 라벨 차단)·
방향/감쇠 부호·크론 선별(pick_events 비용 캡).
"""
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

from providers import news_labels as NL


def _events():
    return [
        {"id": "e1", "title": "엔비디아 실적 서프라이즈", "tags": ["$NVDA", "속보"],
         "published_at": "2026-07-06T10:00:00+09:00"},
        {"id": "e2", "title": "MSFT 반독점 조사", "tags": ["$MSFT"],
         "published_at": "2026-07-06T11:00:00+09:00"},
    ]


# ── parse_labels: 환각·형식 방어 ────────────────────────────────────────────

def test_parse_valid_label():
    out = NL.parse_labels(
        '{"id": "e1", "tickers": ["NVDA"], "event_type": "실적", "direction": 1, "strength": 4}',
        _events())
    assert len(out) == 1
    lb = out[0]
    assert lb["tickers"] == ["NVDA"] and lb["direction"] == 1 and lb["strength"] == 4
    # published_at 은 LLM 출력이 아니라 **입력 이벤트에서** — point-in-time 무결성
    assert lb["published_at"] == "2026-07-06T10:00:00+09:00"


def test_parse_rejects_hallucinated_ticker():
    # e1 태그는 NVDA 뿐 — TSLA 라벨은 환각 → 폐기 (fact guard 철학)
    out = NL.parse_labels(
        '{"id": "e1", "tickers": ["TSLA"], "event_type": "실적", "direction": 1, "strength": 3}',
        _events())
    assert out == []


def test_parse_rejects_bad_enum_and_ranges():
    bad = [
        '{"id": "e1", "tickers": ["NVDA"], "event_type": "루머", "direction": 1, "strength": 3}',
        '{"id": "e1", "tickers": ["NVDA"], "event_type": "실적", "direction": 2, "strength": 3}',
        '{"id": "e1", "tickers": ["NVDA"], "event_type": "실적", "direction": 1, "strength": 9}',
        '{"id": "없는id", "tickers": [], "event_type": "실적", "direction": 0, "strength": 1}',
        'not json at all',
    ]
    assert NL.parse_labels("\n".join(bad), _events()) == []


def test_parse_dedups_by_id():
    line = '{"id": "e1", "tickers": ["NVDA"], "event_type": "실적", "direction": 1, "strength": 3}'
    out = NL.parse_labels(line + "\n" + line, _events())
    assert len(out) == 1


# ── append / load / labeled_ids ──────────────────────────────────────────────

def test_append_load_roundtrip(tmp_path):
    p = tmp_path / "labels.jsonl"
    labels = NL.parse_labels(
        '{"id": "e2", "tickers": ["MSFT"], "event_type": "규제", "direction": -1, "strength": 4}',
        _events())
    assert NL.append_labels(labels, path=p) == 1
    rows = NL.load_labels(path=p)
    assert len(rows) == 1 and rows[0]["labeled_at"]          # labeled_at 스탬프
    assert NL.labeled_ids(path=p) == {"e2"}


# ── news_axis: 방향·감쇠·point-in-time ───────────────────────────────────────

def _label(ticker, direction, strength, pub, lab=None):
    return {"id": f"x-{ticker}-{pub}", "tickers": [ticker], "event_type": "실적",
            "direction": direction, "strength": strength,
            "published_at": pub, "labeled_at": lab or pub}


def test_axis_direction_sign():
    now = datetime.now(NL.KST)
    pub = (now - timedelta(hours=6)).isoformat()
    up = NL.news_axis("NVDA", labels=[_label("NVDA", 1, 5, pub)], asof=now)
    dn = NL.news_axis("NVDA", labels=[_label("NVDA", -1, 5, pub)], asof=now)
    assert up > 0.5 > dn
    assert 0.0 <= dn and up <= 1.0


def test_axis_none_when_no_relevant():
    now = datetime.now(NL.KST)
    pub = (now - timedelta(hours=6)).isoformat()
    assert NL.news_axis("AAPL", labels=[_label("NVDA", 1, 5, pub)], asof=now) is None
    assert NL.news_axis("NVDA", labels=[], asof=now) is None


def test_axis_excludes_future_label_point_in_time():
    """labeled_at 이 asof 이후인 라벨은 사용 금지 — 과거 재계산 시 미래정보 유입 차단."""
    now = datetime.now(NL.KST)
    pub = (now - timedelta(hours=6)).isoformat()
    future_lab = (now + timedelta(hours=3)).isoformat()
    assert NL.news_axis("NVDA", labels=[_label("NVDA", 1, 5, pub, lab=future_lab)],
                        asof=now) is None


def test_axis_excludes_beyond_window():
    now = datetime.now(NL.KST)
    old = (now - timedelta(days=NL.AXIS_WINDOW_DAYS + 2)).isoformat()
    assert NL.news_axis("NVDA", labels=[_label("NVDA", 1, 5, old)], asof=now) is None


def test_axis_recency_decay():
    now = datetime.now(NL.KST)
    fresh = NL.news_axis("NVDA", labels=[_label("NVDA", 1, 5, (now - timedelta(hours=2)).isoformat())], asof=now)
    stale = NL.news_axis("NVDA", labels=[_label("NVDA", 1, 5, (now - timedelta(days=6)).isoformat())], asof=now)
    assert fresh > stale > 0.5


def test_axis_ks_suffix_base_match():
    now = datetime.now(NL.KST)
    pub = (now - timedelta(hours=6)).isoformat()
    assert NL.news_axis("005930.KS", labels=[_label("005930", 1, 4, pub)], asof=now) > 0.5


# ── 프롬프트: 인젝션 방어 + DATA 경계 ────────────────────────────────────────

def test_prompt_contains_defense_and_markers():
    p = NL.build_label_prompt(_events())
    assert "<<<DATA_START>>>" in p and "<<<DATA_END>>>" in p
    assert "따르지 말" in p                       # 인젝션 방어문
    assert "입력 tickers 목록에 없는 티커 금지" in p


# ── 크론 선별 (pick_events) ──────────────────────────────────────────────────

class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def test_label_events_heuristic_fallback(monkeypatch):
    def runner(cmd, **kw):
        return FakeCompleted(returncode=1, stderr="402")

    labels = NL.label_events(_events(), runner=runner)
    assert len(labels) == 2
    assert labels[0]["id"] == "e1" and labels[0]["event_type"] == "실적"
    assert labels[0]["direction"] == 1
    assert labels[1]["id"] == "e2" and labels[1]["event_type"] == "규제"
    assert labels[1]["direction"] == -1


def test_pick_events_filters_and_caps():
    import news_llm_snapshot as S
    events = [
        {"id": "a", "title": "t1", "tags": ["$NVDA"], "published_at": "2026-07-06T10:00:00+09:00"},
        {"id": "b", "title": "t2", "tags": ["속보"], "published_at": "2026-07-06T11:00:00+09:00"},   # 무티커 → 제외
        {"id": "c", "title": "t3", "tags": ["$MSFT"], "published_at": "2026-07-06T12:00:00+09:00"},
        {"id": "d", "title": "t4", "tags": ["$AAPL"], "published_at": "2026-07-06T13:00:00+09:00"},
    ]
    out = S.pick_events(events, already={"c"}, cap=1)
    assert len(out) == 1 and out[0]["id"] == "d"             # 최신순·미라벨·티커태그만


# ── 학습 게이트 배선: news 축이 신규 축으로 등록됐는지 ───────────────────────

def test_news_axis_registered_in_learners_and_policies():
    from ml import kr_policy, us_policy
    assert kr_policy.DEFAULT_POLICY.get("w_news") == 0.0     # 기본 가중 0 = 라이브 무영향
    assert us_policy.DEFAULT_POLICY.get("w_news") == 0.0
    assert "w_news" in kr_policy.BOUNDS and "w_news" in us_policy.BOUNDS
    import kr_mock_learn, us_mock_learn
    assert "news" in kr_mock_learn._FEATS and "news" in us_mock_learn._FEATS
