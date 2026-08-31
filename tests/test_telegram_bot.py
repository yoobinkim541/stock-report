import sys

sys.path.insert(0, ".")

from ml.entry_analyzer import EntryScore


def _score(ticker: str, score: float) -> EntryScore:
    return EntryScore(
        ticker=ticker, category="stock", underlying=ticker,
        current_drawdown=-0.08, current_rsi=42.0, current_vix=18.0,
        current_mom_20d=0.02, current_mom_60d=0.04, current_price=100.0,
        n_similar=30, win_prob_20d=0.7, win_prob_60d=0.72,
        expected_ret_20d=0.05, expected_ret_60d=0.08,
        downside_p25_20d=-0.03, upside_p75_20d=0.08,
        score=score, signal="enter", timestamp="2026-08-31 09:30 KST",
    )


def test_notify_entry_signals_groups_alerts_and_keeps_price_registration(monkeypatch):
    import ml.entry_analyzer as analyzer
    import ml.entry_feedback as feedback
    import telegram_bot

    sent = []
    registered = []
    recorded = []
    alerts = [_score("NVDA", 0.91), _score("MSFT", 0.81)]
    monkeypatch.setattr(telegram_bot, "ALLOWED_CHAT_ID", "chat-1")
    monkeypatch.setattr(telegram_bot, "send", lambda chat_id, text: sent.append((chat_id, text)))
    monkeypatch.setattr(analyzer, "analyze_all_entries", lambda **_: alerts)
    monkeypatch.setattr(analyzer, "check_alert_signals", lambda scores: alerts)
    monkeypatch.setattr(feedback, "record_entry_scores", lambda *args, **kwargs: recorded.append((args, kwargs)))
    monkeypatch.setattr(telegram_bot, "_register_trade_level_alerts", lambda score: registered.append(score.ticker))

    telegram_bot.notify_entry_signals()

    assert len(sent) == 1
    assert "NVDA" in sent[0][1] and "MSFT" in sent[0][1]
    assert any(item[1].get("evaluation_profile") == "short" for item in recorded)
    assert registered == ["NVDA", "MSFT"]


def test_notify_entry_signals_does_not_send_empty_digest(monkeypatch):
    import ml.entry_analyzer as analyzer
    import telegram_bot

    sent = []
    monkeypatch.setattr(telegram_bot, "send", lambda *args: sent.append(args))
    monkeypatch.setattr(analyzer, "analyze_all_entries", lambda **_: [])
    monkeypatch.setattr(analyzer, "check_alert_signals", lambda scores: [])

    telegram_bot.notify_entry_signals()

    assert sent == []
