"""tests/test_notion_dashboard.py — notion_sync 순수 블록 빌더 구조 검증 (무네트워크).

build_blocks / update_page 는 네트워크가 필요하지만, 히어로·리포트 파싱·컬럼·
파일임베드 빌더는 순수 함수라 오프라인 검증 가능. N1·N2·N4 가드.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crons.notion_sync as ns


def test_hero_band_is_4col_callout_band():
    qqq = {"current": 512.34, "drawdown_pct": -3.2}
    dca = {"multiplier": 1.5, "total_krw": 15000}
    block = ns._hero_band("🟢 0 정상", "bear", qqq, -3.2, 4.1, 12345.0, dca)

    assert block["type"] == "column_list"
    cols = block["column_list"]["children"]
    assert len(cols) == 4                       # Phase·QQQ·포트·DCA
    for c in cols:
        assert c["type"] == "column"
        kids = c["column"]["children"]
        assert len(kids) == 1 and kids[0]["type"] == "callout"

    # 포트 수익(+) → 초록 배경 + 초록 굵은 숫자
    port_co = cols[2]["column"]["children"][0]["callout"]
    assert port_co["color"] == "green_background"
    bold_runs = [r for r in port_co["rich_text"] if r["annotations"]["bold"]]
    assert bold_runs and bold_runs[0]["annotations"]["color"] == "green"


def test_hero_band_negative_return_is_red():
    qqq = {"current": 400.0, "drawdown_pct": -12.0}
    dca = {"multiplier": 2.0, "total_krw": 20000}
    block = ns._hero_band("🔴 3 심조정", "bear", qqq, -12.0, -8.5, 9000.0, dca)
    cols = block["column_list"]["children"]
    qqq_co  = cols[1]["column"]["children"][0]["callout"]
    port_co = cols[2]["column"]["children"][0]["callout"]
    assert qqq_co["color"] == "red_background"          # 낙폭 -12% → red
    assert port_co["color"] == "red_background"          # 수익 -8.5% → red


def test_report_blocks_structures_lines():
    lines = [
        "🟢 Phase 0 · QQQ 낙폭 -3.2% · DCA 1.5×",
        "",
        "📌 오늘 할 일",
        "- QQQI 분할매수 점검",
        "- SGOV 실탄 확인",
        "────────────",
        "💰 내 포트폴리오 +4.1%",
        "일반 문단 텍스트입니다.",
    ]
    blocks = ns._report_blocks(lines, limit=45)
    types = [b["type"] for b in blocks]

    assert "heading_3" in types          # "📌 오늘 할 일" (짧은 섹션 헤더)
    assert types.count("bulleted_list_item") == 2
    assert "paragraph" in types
    # 구분선 줄은 블록으로 안 들어감
    assert not any(
        set(b.get("paragraph", {}).get("rich_text", [{}])[0]
            .get("text", {}).get("content", "x")) <= set("─-= ")
        for b in blocks if b["type"] == "paragraph"
    )


def test_image_upload_block_shape():
    blk = ns._image_upload("file-abc-123", "캡션")
    assert blk["type"] == "image"
    assert blk["image"]["type"] == "file_upload"
    assert blk["image"]["file_upload"]["id"] == "file-abc-123"
    assert blk["image"]["caption"][0]["text"]["content"] == "캡션"


def test_columns_requires_children():
    block = ns._columns([[ns._para("a")], [ns._para("b")]])
    assert block["type"] == "column_list"
    assert len(block["column_list"]["children"]) == 2
    for col in block["column_list"]["children"]:
        assert col["column"]["children"]            # 각 컬럼 ≥1 자식
