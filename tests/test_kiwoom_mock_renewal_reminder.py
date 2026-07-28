from datetime import date


def test_kiwoom_mock_renewal_reminder_sends_on_target_date(tmp_path):
    from crons import kiwoom_mock_renewal_reminder as reminder

    sent = []
    state = tmp_path / "sent.json"

    code = reminder.main(
        today=reminder.TARGET_DATE,
        state_path=state,
        send_fn=lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )

    assert code == 0
    assert state.exists()
    assert len(sent) == 1
    assert "키움 국내 모의투자 갱신 알림" in sent[0][0][0]


def test_kiwoom_mock_renewal_reminder_skips_non_target_date(tmp_path):
    from crons import kiwoom_mock_renewal_reminder as reminder

    sent = []

    code = reminder.main(
        today=date(2026, 10, 27),
        state_path=tmp_path / "sent.json",
        send_fn=lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )

    assert code == 0
    assert sent == []


def test_kiwoom_mock_renewal_reminder_skips_after_sent(tmp_path):
    from crons import kiwoom_mock_renewal_reminder as reminder

    sent = []
    state = tmp_path / "sent.json"
    state.write_text("{}", encoding="utf-8")

    code = reminder.main(
        today=reminder.TARGET_DATE,
        state_path=state,
        send_fn=lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )

    assert code == 0
    assert sent == []
