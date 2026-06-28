import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from barbell_strategy import build_simulation_report, _holding_details_from_snapshot, fetch_portfolio_value
from portfolio_tracker import build_benchmark_report, build_dividend_calendar


def test_auto_intraday_alerts_default_off():
    import telegram_bot

    assert telegram_bot.AUTO_INTRADAY_ALERTS is False
    assert telegram_bot.STARTUP_NOTIFY_ENABLED is False


def test_build_simulation_report_includes_mode_and_phase():
    text = build_simulation_report("bull2")

    assert "시뮬레이션 모드: bull2" in text
    assert "Intelligence Barbell v2.1" in text
    assert "Phase" in text


def test_build_benchmark_report_compares_portfolio_against_benchmarks():
    perf = {
        "current": 100.0,
        "ret_1d": 1.0,
        "ret_7d": 2.0,
        "ret_30d": 3.0,
        "ret_90d": 4.0,
        "ret_all": 5.0,
    }
    benchmarks = {
        "QQQ": {
            "name": "QQQ — Invesco QQQ Trust",
            "ret_1d": 0.5,
            "ret_7d": 1.5,
            "ret_30d": 2.5,
            "ret_90d": 3.5,
            "ret_all": 4.5,
        },
        "QQQI": {
            "name": "QQQI — NEOS Nasdaq 100 High Income ETF",
            "ret_1d": 0.2,
            "ret_7d": 0.4,
            "ret_30d": 0.6,
            "ret_90d": 0.8,
            "ret_all": 1.0,
        },
    }

    text = build_benchmark_report(perf, benchmarks)

    assert "벤치마크 비교" in text
    assert "내 포트폴리오" in text
    assert "QQQ — Invesco QQQ Trust" in text
    assert "QQQI — NEOS Nasdaq 100 High Income ETF" in text


def test_build_dividend_calendar_estimates_next_payment():
    dividends = [
        {"date": "2024-01-15", "amount_usd": 1.00},
        {"date": "2024-02-14", "amount_usd": 1.10},
        {"date": "2024-03-15", "amount_usd": 1.20},
    ]

    text = build_dividend_calendar(dividends, shares=100)

    assert "배당 캘린더" in text
    assert "다음 예상" in text
    assert "평균 간격" in text


def test_holding_details_from_snapshot_includes_stocks_and_domestic():
    snap = {
        "overseas_general": {
            "holdings_usd": [
                {
                    "ticker": "MSFT",
                    "name": "마이크로소프트",
                    "shares": 2,
                    "value_usd": 900.48,
                    "return_pct": 11.86,
                }
            ]
        },
        "overseas_fractional": {
            "holdings": [
                {
                    "ticker": "NVDA",
                    "name": "엔비디아",
                    "shares": 0.5,
                    "value_usd": 105.57,
                    "return_pct": 14.66,
                }
            ]
        },
        "domestic": {
            "holdings": [
                {
                    "ticker": "SOL AI반도체TOP2",
                    "name": "SOL AI반도체TOP2플러스",
                    "shares": 20,
                    "current_price": 25200,
                    "return_pct": 6.38,
                }
            ]
        },
    }

    details = _holding_details_from_snapshot(snap)

    assert details[0]["ticker"] == "MSFT"
    assert details[0]["name"] == "마이크로소프트"
    assert details[0]["value_usd"] == 900.48
    assert details[1]["ticker"] == "NVDA"
    assert details[2]["value_krw"] == 504000


def test_holding_details_combines_duplicate_us_tickers():
    snap = {
        "overseas_general": {
            "holdings_usd": [
                {"ticker": "MSFT", "name": "마이크로소프트", "shares": 2, "value_usd": 900, "return_pct": 12.5}
            ]
        },
        "overseas_fractional": {
            "holdings": [
                {"ticker": "MSFT", "name": "마이크로소프트", "shares": 0.5, "value_usd": 225, "return_pct": 10.0}
            ]
        },
    }

    details = _holding_details_from_snapshot(snap)

    assert len([h for h in details if h["ticker"] == "MSFT"]) == 1
    assert details[0]["shares"] == 2.5
    assert details[0]["value_usd"] == 1125


def test_dispatch_routes_common_command_typo_to_portfolio(monkeypatch):
    import telegram_bot

    sent = []
    calls = []

    monkeypatch.setattr(telegram_bot, "refresh_portfolio_prices", lambda: calls.append("refresh"))
    monkeypatch.setattr(telegram_bot, "fetch_market", lambda force=False: calls.append(("fetch", force)) or {"portfolio": {"total_usd": 1}})
    monkeypatch.setattr(telegram_bot, "cmd_portfolio", lambda d: "포트폴리오 현황")
    monkeypatch.setattr(telegram_bot, "send", lambda chat_id, text: sent.append(text))
    monkeypatch.setattr(telegram_bot, "typing", lambda chat_id: None)

    telegram_bot.dispatch("/portpolio", "chat-1")

    assert calls == ["refresh", ("fetch", True)]
    assert len(sent) == 1 and sent[0].startswith("포트폴리오 현황")   # 끝에 신선도 한 줄(C1)


def test_dispatch_routes_plural_aliases(monkeypatch):
    import telegram_bot

    calls = []

    monkeypatch.setattr(telegram_bot, "typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "send", lambda *args: None)
    monkeypatch.setattr(telegram_bot, "cmd_holding", lambda chat_id, args, send_fn: calls.append(("holding", args)))
    monkeypatch.setattr(telegram_bot, "cmd_alert", lambda chat_id, args: calls.append(("alert", args)))
    monkeypatch.setattr(telegram_bot, "cmd_order", lambda chat_id: calls.append(("order", [])))
    monkeypatch.setattr(telegram_bot, "cmd_tax", lambda chat_id, args, send_fn: calls.append(("tax", args)))

    telegram_bot.dispatch("/holdings", "chat-1")
    telegram_bot.dispatch("/alerts list", "chat-1")
    telegram_bot.dispatch("/orders", "chat-1")
    telegram_bot.dispatch("/taxes history", "chat-1")

    assert calls == [
        ("holding", []),
        ("alert", ["list"]),
        ("order", []),
        ("tax", ["history"]),
    ]


def test_dispatch_routes_legacy_commands_to_holding_subcommands(monkeypatch):
    import telegram_bot

    calls = []

    monkeypatch.setattr(telegram_bot, "typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "send", lambda *args: None)
    monkeypatch.setattr(telegram_bot, "cmd_holding", lambda chat_id, args, send_fn: calls.append(args))

    telegram_bot.dispatch("/dividend 22.15 ORCL 5월배당", "chat-1")
    telegram_bot.dispatch("/apply_snapshot", "chat-1")

    assert calls == [
        ["dividend", "22.15", "ORCL", "5월배당"],
        ["apply"],
    ]


def test_cmd_help_groups_commands_and_mentions_merged_legacy_commands():
    import telegram_bot

    help_text = telegram_bot.cmd_help()

    assert "[시장]" in help_text
    assert "[포트폴리오]" in help_text
    assert "[보유·세금]" in help_text
    assert "[ML·신호 (참고)]" in help_text
    assert "/holding" in help_text
    # Tier B 병합 안내 (footer)
    assert "통합:" in help_text
    assert "/paper" in help_text and "/signals" in help_text
    assert "/dividend" in help_text and "/holding" in help_text


def test_dispatch_routes_plain_internal_feature_request_without_llm(monkeypatch):
    import telegram_bot

    sent = []
    asked = []

    monkeypatch.setattr(telegram_bot, "detect_content_type", lambda text, caption="": "unknown")
    monkeypatch.setattr(telegram_bot, "refresh_portfolio_prices", lambda: None)
    monkeypatch.setattr(telegram_bot, "fetch_market", lambda force=False: {"portfolio": {"total_usd": 1}})
    monkeypatch.setattr(telegram_bot, "cmd_portfolio", lambda d: "포트폴리오 현황")
    monkeypatch.setattr(telegram_bot, "ask_portfolio_advisor", lambda q, d: asked.append(q) or "LLM 답변")
    monkeypatch.setattr(telegram_bot, "send", lambda chat_id, text: sent.append(text))
    monkeypatch.setattr(telegram_bot, "typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "keep_typing", lambda chat_id: (lambda: None))

    normalized = telegram_bot._normalize_message_text("포트폴리오 보여줘")
    telegram_bot.dispatch(normalized, "chat-1")

    assert len(sent) == 1 and sent[0].startswith("포트폴리오 현황")   # 끝에 신선도 한 줄(C1)
    assert asked == []


def test_fetch_benchmark_returns_calculates_ytd():
    import pandas as pd
    import telegram_bot

    class FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        def history(self, period, auto_adjust=True):
            assert period == "ytd"
            return pd.DataFrame({"Close": [100.0, 110.0]})

    class FakeYF:
        Ticker = FakeTicker

    returns = telegram_bot.fetch_benchmark_returns(("QQQ",), yf_module=FakeYF)

    assert returns == {"QQQ": {"current": 110.0, "ytd_pct": 10.0}}


def test_dispatch_ask_fetches_market_and_sends_advice(monkeypatch):
    import telegram_bot

    calls = {"fetch_market": 0, "ask": None, "send": [], "typing_stop": 0}
    market = {"market_type": "bull", "phase_key": "bull_1"}

    def fake_fetch_market(force=False):
        calls["fetch_market"] += 1
        calls["fetch_market_force"] = force
        return market

    def fake_ask(question, data):
        calls["ask"] = (question, data)
        return "상담 답변"

    def fake_send(chat_id, text):
        calls["send"].append((chat_id, text))

    def fake_keep_typing(chat_id):
        calls["typing_chat_id"] = chat_id

        def stop():
            calls["typing_stop"] += 1

        return stop

    monkeypatch.setattr(telegram_bot, "fetch_market", fake_fetch_market)
    monkeypatch.setattr(telegram_bot, "ask_portfolio_advisor", fake_ask)
    monkeypatch.setattr(telegram_bot, "send", fake_send)
    monkeypatch.setattr(telegram_bot, "keep_typing", fake_keep_typing)

    telegram_bot.dispatch("/ask 지금 추가매수해도 돼?", "chat-1")

    assert calls["fetch_market"] == 1
    assert calls["fetch_market_force"] is True
    assert calls["ask"] == ("지금 추가매수해도 돼?", market)
    assert calls["typing_chat_id"] == "chat-1"
    assert calls["typing_stop"] == 1
    assert calls["send"] == [("chat-1", "상담 답변")]


def test_configure_bot_commands_scopes_owner_and_guest_menus(monkeypatch):
    """B5 — 소유자 채팅엔 owner 메뉴, default·all_private 엔 guest 메뉴 (게스트 숨김)."""
    import telegram_bot

    calls = []

    def fake_api(method, **kwargs):
        calls.append((method, kwargs))
        return {"result": True}

    monkeypatch.setattr(telegram_bot, "_api", fake_api)

    telegram_bot.configure_bot_commands()

    assert len(calls) == 3
    for method, _ in calls:
        assert method == "setMyCommands"
    # default + all_private_chats → 게스트 메뉴
    assert calls[0][1].get("scope") is None
    assert calls[0][1]["commands"] is telegram_bot._GUEST_MENU
    assert calls[1][1]["scope"] == {"type": "all_private_chats"}
    assert calls[1][1]["commands"] is telegram_bot._GUEST_MENU
    # 소유자 채팅 scope override → 소유자 메뉴
    assert calls[2][1]["scope"]["type"] == "chat"
    assert str(calls[2][1]["scope"]["chat_id"]) == str(telegram_bot.OWNER_CHAT_ID)
    assert calls[2][1]["commands"] is telegram_bot._OWNER_MENU
    # 게스트 전용 명령은 소유자 메뉴에 노출되지 않음
    owner = {c["command"] for c in telegram_bot._OWNER_MENU}
    assert not ({"market", "indicators", "my"} & owner)


def test_plain_text_normalized_to_ask_and_dispatched(monkeypatch):
    import telegram_bot

    # helper contracts
    assert telegram_bot._normalize_message_text("추가매수해도 돼?") == "/ask 추가매수해도 돼?"
    assert telegram_bot._normalize_message_text("/status") == "/status"
    assert telegram_bot._normalize_message_text("") == ""

    monkeypatch.setattr(telegram_bot, "detect_content_type", lambda text, caption="": "unknown")

    # dispatch with normalized plain text invokes ask_portfolio_advisor with the original question
    asked = []

    def fake_ask(question, data):
        asked.append(question)
        return "답변"

    monkeypatch.setattr(telegram_bot, "fetch_market", lambda force=False: {"market_type": "bull", "phase_key": "bull_1"})
    monkeypatch.setattr(telegram_bot, "ask_portfolio_advisor", fake_ask)
    monkeypatch.setattr(telegram_bot, "send", lambda *a: None)
    monkeypatch.setattr(telegram_bot, "keep_typing", lambda chat_id: (lambda: None))

    normalized = telegram_bot._normalize_message_text("추가매수해도 돼?")
    telegram_bot.dispatch(normalized, "chat-1")
    assert asked == ["추가매수해도 돼?"]


def test_plain_text_market_report_with_portfolio_routes_to_ask(monkeypatch):
    import telegram_bot

    sent = []
    asked = []

    monkeypatch.setattr(telegram_bot, "refresh_portfolio_prices", lambda: None)
    monkeypatch.setattr(telegram_bot, "fetch_market", lambda force=False: {"portfolio": {"total_usd": 1}})
    monkeypatch.setattr(telegram_bot, "ask_portfolio_advisor", lambda q, d: asked.append(q) or "LLM 답변")
    monkeypatch.setattr(telegram_bot, "send", lambda chat_id, text: sent.append(text))
    monkeypatch.setattr(telegram_bot, "keep_typing", lambda chat_id: (lambda: None))

    text = "오늘의 주식 현황 보고해줘 내 포트폴리오랑"

    assert telegram_bot.handle_plain_text(text, "chat-1") is False
    telegram_bot.dispatch(telegram_bot._normalize_message_text(text), "chat-1")

    assert asked == [text]
    assert sent == ["LLM 답변"]


def test_plain_text_portfolio_snapshot_is_saved_as_pending(monkeypatch):
    import telegram_bot

    saved = []
    sent = []
    text = "포트폴리오\nMSFT 마이크로소프트 2 400 450\nNVDA 엔비디아 1 100 120\nCRM 세일스포스 1 180 200"
    holdings = [
        {"ticker": "MSFT", "name": "마이크로소프트", "shares": 2, "avg_price_usd": 400, "current_price_usd": 450, "value_usd": 900},
        {"ticker": "NVDA", "name": "엔비디아", "shares": 1, "avg_price_usd": 100, "current_price_usd": 120, "value_usd": 120},
        {"ticker": "CRM", "name": "세일스포스", "shares": 1, "avg_price_usd": 180, "current_price_usd": 200, "value_usd": 200},
    ]

    monkeypatch.setattr(telegram_bot, "detect_content_type", lambda raw, caption="": "portfolio")
    monkeypatch.setattr(telegram_bot, "parse_portfolio_from_text", lambda raw: holdings)
    monkeypatch.setattr(telegram_bot, "save_pending_snapshot", lambda rows: saved.append(rows))
    monkeypatch.setattr(telegram_bot, "build_pending_snapshot_summary", lambda pending: "미리보기 /apply_snapshot")
    monkeypatch.setattr(telegram_bot, "send", lambda chat_id, message: sent.append(message))

    assert telegram_bot.handle_plain_text(text, "chat-1") is True
    assert saved == [holdings]
    assert sent == ["미리보기 /apply_snapshot"]


def test_fetch_portfolio_value_returns_total_pnl_and_return(monkeypatch, tmp_path):
    # fetch_portfolio_value·PORTFOLIO_PATH·load_leverage_state·yf 는 providers/market_data.py 로
    # 이전됨 — fetch_portfolio_value 는 그 모듈 전역을 참조하므로 md 에 패치한다.
    import providers.market_data as md

    portfolio_path = tmp_path / "portfolio_snapshot.json"
    portfolio_path.write_text(json.dumps({
        "domestic": {
            "summary": {
                "total_cost_krw": 100_000,
                "total_value_krw": 110_000,
                "total_pnl_krw": 10_000,
                "total_return_pct": 10.0,
            }
        },
        "overseas_general": {
            "holdings_usd": [{
                "ticker": "MSFT",
                "shares": 2,
                "cost_usd": 800,
                "current_price_usd": 450,
            }]
        },
        "overseas_fractional": {
            "holdings": [{
                "ticker": "NVDA",
                "shares": 1,
                "avg_price_usd": 100,
                "current_price_usd": 120,
            }]
        },
    }), encoding="utf-8")

    class FakeDownload:
        empty = True

    monkeypatch.setattr(md, "PORTFOLIO_PATH", str(portfolio_path))
    monkeypatch.setattr(md, "load_leverage_state", lambda: {})
    monkeypatch.setattr(md.yf, "download", lambda *args, **kwargs: FakeDownload())

    port = fetch_portfolio_value()

    assert port["total_usd"] == 1020.0
    assert port["cost_usd"] == 900.0
    assert port["pnl_usd"] == 120.0
    assert port["return_pct"] == 13.33
    assert port["domestic_value_krw"] == 110_000
    assert port["domestic_pnl_krw"] == 10_000


def test_cmd_portfolio_includes_total_pnl_and_overall_return():
    import telegram_bot

    text = telegram_bot.cmd_portfolio({
        "fetched_at": "06/04 14:09",
        "exchange_rate": 1400.0,
        "portfolio": {
            "total_usd": 1000.0,
            "cost_usd": 800.0,
            "pnl_usd": 200.0,
            "return_pct": 25.0,
            "sgov_usd": 100.0,
            "qqqi_usd": 200.0,
            "qqqi_shares": 4,
            "prices": {},
            "domestic_cost_krw": 100_000,
            "domestic_value_krw": 120_000,
            "domestic_pnl_krw": 20_000,
        },
        "qqqi_div": {"monthly_usd": 2.5, "annual_yield_pct": 12.0},
    })

    assert "총액  $1,000.00  +$200.00 (+25.0%)" in text
    assert "전체  ₩1,520,000  +₩300,000 (+24.6%)" in text


def test_dispatch_portfolio_refreshes_prices_and_bypasses_cache(monkeypatch):
    import telegram_bot

    calls = []
    sent = []

    def fake_refresh():
        calls.append(("refresh", None))
        return "✅ 가격 갱신"

    def fake_fetch_market(force=False):
        calls.append(("fetch_market", force))
        return {
            "portfolio": {"total_usd": 1000.0, "sgov_usd": 100.0, "qqqi_usd": 200.0, "prices": {}},
            "exchange_rate": 1400.0,
            "qqqi_div": {"monthly_usd": 2.0, "annual_yield_pct": 12.0},
            "fetched_at": "06/04 12:00",
        }

    monkeypatch.setattr(telegram_bot, "refresh_portfolio_prices", fake_refresh)
    monkeypatch.setattr(telegram_bot, "fetch_market", fake_fetch_market)
    monkeypatch.setattr(telegram_bot, "typing", lambda chat_id: None)
    monkeypatch.setattr(telegram_bot, "send", lambda chat_id, text: sent.append(text))

    telegram_bot.dispatch("/portfolio", "chat-1")

    assert calls == [("refresh", None), ("fetch_market", True)]
    assert sent and "포트폴리오" in sent[0]


def test_menus_reflect_tier_b_merges_and_have_handlers():
    """B1~B5 — 병합 후 메뉴 구성 + 모든 메뉴 명령에 핸들러 존재."""
    import telegram_bot

    owner = {c["command"] for c in telegram_bot._OWNER_MENU}
    guest = {c["command"] for c in telegram_bot._GUEST_MENU}

    assert owner >= {
        "help", "status", "phase", "report", "portfolio", "rebalance",
        "risk", "history", "order", "paper", "holding", "tax",
        "ask", "alert", "accum", "earnings", "signals",
    }
    assert guest >= {"market", "indicators", "my", "help"}
    # 병합·삭제된 구 명령은 어느 메뉴에도 없음
    gone = {"summary", "sim", "dca", "sgov", "mock", "usmock",
            "ranking", "entry", "intraday", "leverage", "meta", "mlreport",
            "myadd", "myremove", "myportfolio"}
    assert not (gone & (owner | guest))
    # 모든 메뉴 명령에 핸들러 존재
    for c in owner | guest:
        assert ("/" + c) in telegram_bot._COMMAND_HANDLERS, f"no handler for /{c}"


def test_tier_b_aliases_resolve_to_umbrella_commands():
    """B1~B4 — 구 명령어가 alias 로 우산 명령에 정확히 해석 (하위호환)."""
    import telegram_bot as t

    assert t._parse_command("/mock")             == ("/paper", ["kr"])
    assert t._parse_command("/usmock")           == ("/paper", ["us"])
    assert t._parse_command("/dca")              == ("/rebalance", ["dca"])
    assert t._parse_command("/sgov")             == ("/rebalance", ["sgov"])
    assert t._parse_command("/ranking retrain")  == ("/signals", ["rank", "retrain"])
    assert t._parse_command("/entry kr")         == ("/signals", ["entry", "kr"])
    assert t._parse_command("/intraday 1m NVDA") == ("/signals", ["intraday", "1m", "NVDA"])
    assert t._parse_command("/leverage")         == ("/signals", ["lev"])
    assert t._parse_command("/meta")             == ("/signals", ["meta"])
    assert t._parse_command("/myadd QQQ 10 500") == ("/my", ["add", "QQQ", "10", "500"])
    assert t._parse_command("/myremove QQQ")     == ("/my", ["del", "QQQ"])
    assert t._parse_command("/myportfolio")      == ("/my", ["view"])
    # /mlreport 는 하드 삭제 — alias 없음, 핸들러 없음
    assert t._parse_command("/mlreport") == ("/mlreport", [])
    assert "/mlreport" not in t._COMMAND_HANDLERS


def test_guest_permission_boundary_after_my_merge():
    """B3 — 게스트는 /my·/market·/indicators·/help 만 허용, 처방형·모의 차단."""
    import telegram_bot as t

    for allowed in ("/my", "/market", "/indicators", "/help"):
        assert t._command_allowed("guest", allowed) is True
    for blocked in ("/paper", "/portfolio", "/signals", "/rebalance",
                    "/tax", "/order", "/ask", "/holding"):
        assert t._command_allowed("guest", blocked) is False


def test_apply_snapshot_updates_derived_portfolio_values(monkeypatch, tmp_path):
    import telegram_bot
    import bot.holding_commands as holding_commands

    portfolio_path = tmp_path / "portfolio_snapshot.json"
    portfolio_path.write_text(json.dumps({
        "overseas_general": {
            "holdings_usd": [{
                "ticker": "MSFT",
                "name": "마이크로소프트",
                "shares": 2,
                "avg_price_usd": 400,
                "current_price_usd": 450,
                "cost_usd": 800,
                "value_usd": 900,
                "pnl_usd": 100,
                "return_pct": 12.5,
            }]
        }
    }), encoding="utf-8")

    pending = {
        "holdings": [{
            "ticker": "MSFT",
            "name": "마이크로소프트",
            "shares": 5.6,
            "avg_price_usd": 0.0,
            "current_price_usd": 226.4732,
            "value_usd": 1268.25,
        }]
    }
    sent = []

    monkeypatch.setattr(holding_commands, "PORTFOLIO_PATH", str(portfolio_path))
    monkeypatch.setattr(holding_commands, "load_pending_snapshot", lambda: pending)
    monkeypatch.setattr(holding_commands, "clear_pending_snapshot", lambda: None)
    monkeypatch.setattr(telegram_bot, "send", lambda chat_id, text: sent.append(text))

    def send_fn(chat_id, text):
        sent.append(text)

    telegram_bot.cmd_apply_snapshot("chat-1", send_fn)

    snap = json.loads(portfolio_path.read_text(encoding="utf-8"))
    holding = snap["overseas_general"]["holdings_usd"][0]
    assert holding["shares"] == 5.6
    assert holding["value_usd"] == 1268.25
    assert holding["cost_usd"] == 0.0
    assert holding["pnl_usd"] == 1268.25
    assert holding["return_pct"] == 0.0
    assert sent and "포트폴리오 스냅샷 반영 완료" in sent[0]


def test_handle_attachment_rejects_incomplete_portfolio_snapshot(monkeypatch):
    import telegram_bot

    sent = []
    saved = []

    monkeypatch.setattr(telegram_bot, "download_telegram_file", lambda file_id, filename: "/tmp/fake.jpg")
    monkeypatch.setattr(telegram_bot, "extract_text_from_image", lambda path: "portfolio text")
    monkeypatch.setattr(telegram_bot, "detect_content_type", lambda text, caption="": "portfolio")
    monkeypatch.setattr(telegram_bot, "parse_portfolio_from_text", lambda text: [
        {"ticker": "NVDA", "name": "엔비디아", "shares": 2.7875, "avg_price_usd": 0.0, "current_price_usd": 213.1193, "value_usd": 594.07},
        {"ticker": "SAP", "name": "SAP SE", "shares": 0.5944, "avg_price_usd": 0.0, "current_price_usd": 190.0404, "value_usd": 112.96},
    ])
    monkeypatch.setattr(telegram_bot, "save_pending_snapshot", lambda holdings: saved.append(holdings))
    monkeypatch.setattr(telegram_bot, "send", lambda chat_id, text: sent.append(text))

    telegram_bot.handle_attachment({"photo": [{"file_id": "abc123"}], "caption": "포트폴리오"}, "chat-1")

    assert not saved
    assert any("포트폴리오 인식 불완전" in text for text in sent)
