"""위키 자동승격 제목 추출(_derive_title) — 마크다운 강조 구문 유입 방지."""

from __future__ import annotations


def test_derive_title_strips_embedded_markdown_emphasis():
    """실측(2026-09-06): 대화 승격으로 만들어진 위키 페이지 제목이
    '**방금 말은 **"오늘 시장 변화가 어디서 시작됐는지 추적해줘"**의 후속으로
    이해했습니다.' 처럼 원본 답변의 **강조** 구문을 그대로 물고 들어와, 이후
    카드 렌더링에서 `**{title}**` 로 다시 감쌀 때 별표가 중첩돼 마크다운 파싱이
    깨지고 화면에 별표가 글자 그대로 노출됐다. 후보 줄에서 강조 마크업을
    제거해야 제목이 늘 순수 텍스트로 유지된다."""
    from agent_console import wiki

    answer = (
        '방금 말은 **"오늘 시장 변화가 어디서 시작됐는지 추적해줘"**의 후속으로 이해했습니다.\n'
        "질문은 이해했습니다: 최근 대화에서 위키로 남길 판단을 정리해줘"
    )
    title = wiki._derive_title("최근 대화에서 위키로 남길 판단을 정리해줘", answer)
    assert "*" not in title
    assert "방금 말은" in title


def test_derive_title_still_prefers_bullet_line_without_markdown():
    from agent_console import wiki

    answer = "- 손실한도 초과 시 즉시 현금화한다\n나머지 설명"
    title = wiki._derive_title("손실한도 규칙 뭐야", answer)
    assert title == "손실한도 초과 시 즉시 현금화한다"
