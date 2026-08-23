from __future__ import annotations


class _Response:
    def __init__(self, payload, *, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            error = requests.HTTPError(f"{self.status_code} upstream")
            error.response = self
            raise error

    def json(self):
        return self._payload


def _market(**overrides):
    row = {
        "ticker": "KXFED-26SEP-CUT",
        "event_ticker": "KXFED-26SEP",
        "title": "Will the Federal Reserve cut rates in September?",
        "yes_bid_dollars": "0.4200",
        "yes_ask_dollars": "0.4600",
        "last_price_dollars": "0.4300",
        "liquidity_dollars": "125000.00",
        "volume_fp": "580000.00",
        "volume_24h_fp": "22000.00",
        "open_interest_fp": "99000.00",
        "close_time": "2026-09-16T18:00:00Z",
        "status": "active",
    }
    row.update(overrides)
    return row


def test_kalshi_normalizes_finance_market_with_midpoint_probability():
    from reports import prediction_markets

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response({"markets": [_market()], "cursor": ""})

    events = prediction_markets.fetch_kalshi_events(
        limit=10,
        min_volume=1000,
        get=fake_get,
    )

    assert len(events) == 1
    event = events[0]
    assert event["source"] == "kalshi"
    assert event["type"] == "prediction_market"
    assert event["record_kind"] == "observation"
    assert event["entity_id"] == "kalshi:KXFED-26SEP-CUT"
    assert event["metrics"]["yes_probability"] == 0.44
    assert event["metrics"]["yes_bid"] == 0.42
    assert event["metrics"]["yes_ask"] == 0.46
    assert event["metrics"]["volume"] == 580000.0
    assert event["metrics"]["open_interest"] == 99000.0
    assert "not verified facts" in event["body"]
    assert calls[0][0] == "https://external-api.kalshi.com/trade-api/v2/markets"
    assert calls[0][1]["params"]["status"] == "open"
    assert calls[0][1]["params"]["mve_filter"] == "exclude"


def test_kalshi_uses_last_price_when_orderbook_is_missing():
    from reports import prediction_markets

    row = _market(yes_bid_dollars="0.0000", yes_ask_dollars="0.0000", last_price_dollars="0.6100")
    events = prediction_markets.fetch_kalshi_events(
        get=lambda *_args, **_kwargs: _Response({"markets": [row], "cursor": ""}),
        min_volume=1,
    )

    assert events[0]["metrics"]["yes_probability"] == 0.61


def test_kalshi_excludes_sports_only_market_by_default():
    from reports import prediction_markets

    sports = _market(
        ticker="KXNFLGAME-26SEP-NYG-DAL",
        event_ticker="KXNFLGAME-26SEP",
        title="Will the New York Giants beat the Dallas Cowboys?",
    )
    events = prediction_markets.fetch_kalshi_events(
        get=lambda *_args, **_kwargs: _Response({"markets": [sports], "cursor": ""}),
        min_volume=1,
    )

    assert events == []


def test_kalshi_paginates_but_respects_output_limit():
    from reports import prediction_markets

    calls = []

    def fake_get(_url, **kwargs):
        calls.append(kwargs["params"].copy())
        cursor = kwargs["params"].get("cursor")
        if not cursor:
            return _Response({
                "markets": [_market(ticker="KXFED-A"), _market(ticker="KXFED-B")],
                "cursor": "next-page",
            })
        return _Response({"markets": [_market(ticker="KXFED-C")], "cursor": ""})

    events = prediction_markets.fetch_kalshi_events(
        limit=3,
        min_volume=1,
        get=fake_get,
        max_pages=2,
    )

    assert [row["metrics"]["market_ticker"] for row in events] == ["KXFED-A", "KXFED-B", "KXFED-C"]
    assert calls[1]["cursor"] == "next-page"


def test_kalshi_discovers_relevant_series_when_market_feed_is_sports_dominated():
    from reports import prediction_markets

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs["params"].copy()))
        params = kwargs["params"]
        if url.endswith("/series"):
            return _Response({"series": [
                {"ticker": "KXFED", "title": "Federal Reserve decisions", "category": "Economics", "tags": ["Fed"], "volume_fp": "90000"},
                {"ticker": "KXNFL", "title": "NFL games", "category": "Sports", "tags": ["football"], "volume_fp": "999999"},
            ]})
        if params.get("series_ticker") == "KXFED":
            return _Response({"markets": [_market(volume_fp="1500")], "cursor": ""})
        return _Response({"markets": [], "cursor": ""})

    events = prediction_markets.fetch_kalshi_events(limit=5, min_volume=1000, get=fake_get, max_pages=1)

    assert len(events) == 1
    assert events[0]["metrics"]["market_ticker"] == "KXFED-26SEP-CUT"
    assert any(url.endswith("/series") for url, _params in calls)
    assert any(params.get("series_ticker") == "KXFED" for _url, params in calls)
    assert not any(params.get("series_ticker") == "KXNFL" for _url, params in calls)


def test_kalshi_series_discovery_isolates_one_series_failure():
    from reports import prediction_markets

    def fake_get(url, **kwargs):
        params = kwargs["params"]
        if url.endswith("/series"):
            return _Response({"series": [
                {"ticker": "KXFAIL", "title": "Federal Reserve failure", "category": "Economics", "volume_fp": "90000"},
                {"ticker": "KXFED", "title": "Federal Reserve decisions", "category": "Economics", "volume_fp": "80000"},
            ]})
        if params.get("series_ticker") == "KXFAIL":
            return _Response({}, status_code=503)
        if params.get("series_ticker") == "KXFED":
            return _Response({"markets": [_market(volume_fp="1500")], "cursor": ""})
        return _Response({"markets": [], "cursor": ""})

    events = prediction_markets.fetch_kalshi_events(limit=5, min_volume=1000, get=fake_get, max_pages=1)

    assert len(events) == 1
