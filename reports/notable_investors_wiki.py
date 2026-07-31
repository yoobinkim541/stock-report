#!/usr/bin/env python3
"""reports/notable_investors_wiki.py — 유명 투자자(현재: 워런 버핏/버크셔) 13F → 위키 카드.

SEC EDGAR 13F-HR 을 무료·API키 없이 직접 파싱(providers/thirteenf.py)해 분기 포트폴리오를
위키 페이지 1건(필러당, 매 분기 갱신)으로 기록한다. 이전 스냅샷과 비교해 신규 편입/청산
종목을 감지 — 신규 편입은 "관심종목 후보" 신호로 텔레그램 알림도 보낸다.

과거 스냅샷은 ~/reports/ml-data/notable_investors_13f.jsonl 에 append-only 로 쌓인다
(다음 분기 필링과 비교할 기준선 + 학습/분석용 이력).

사용법:
    uv run python -m reports.notable_investors_wiki --dry-run
    uv run python -m reports.notable_investors_wiki
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
HISTORY_PATH = Path.home() / "reports" / "ml-data" / "notable_investors_13f.jsonl"

# 추적 대상 — providers.thirteenf.FILERS 에 등록된 필러 중 여기 나열된 것만 처리
TRACKED_FILERS = ["berkshire"]


def _load_history(filer_key: str) -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    rows = []
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("filer") == filer_key:
                    rows.append(rec)
    except Exception as e:
        logger.warning("이력 로드 실패(무시): %s", e)
    return rows


def _append_history(rec: dict) -> None:
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("이력 기록 실패(무시): %s", e)


def diff_holdings(prev: list[dict] | None, cur: list[dict]) -> dict:
    """이전(없으면 빈) vs 현재 보유 — cusip 기준 신규 편입/청산."""
    prev_by_cusip = {h["cusip"]: h for h in (prev or [])}
    cur_by_cusip = {h["cusip"]: h for h in cur}
    new_positions = [h for c, h in cur_by_cusip.items() if c not in prev_by_cusip]
    exited_positions = [h for c, h in prev_by_cusip.items() if c not in cur_by_cusip]
    new_positions.sort(key=lambda h: -h.get("weight_pct", 0))
    exited_positions.sort(key=lambda h: -h.get("weight_pct", 0))
    return {"new": new_positions, "exited": exited_positions}


def _fmt_holding(h: dict) -> str:
    tk = f" ({h['ticker']})" if h.get("ticker") else ""
    return f"{h['issuer']}{tk} — {h.get('weight_pct', 0):.1f}% · ${h.get('value_usd', 0) / 1e9:.2f}B"


def build_wiki_page(snapshot: dict, diff: dict) -> dict:
    filer_key = snapshot["filer"]
    holdings = snapshot["holdings"]
    top = holdings[:10]
    lines = [
        f"필링일: {snapshot['filing_date']} · 총 {len(holdings)}종목 · "
        f"총액 ${snapshot['total_value_usd'] / 1e9:.1f}B",
        "",
        "상위 10 종목:",
        *[f"- {_fmt_holding(h)}" for h in top],
    ]
    if diff["new"]:
        lines += ["", "🆕 신규 편입:", *[f"- {_fmt_holding(h)}" for h in diff["new"]]]
    if diff["exited"]:
        lines += ["", "📤 청산(전량 매도):", *[f"- {_fmt_holding(h)}" for h in diff["exited"]]]
    lines += [
        "",
        f"출처: SEC EDGAR 13F-HR (accession {snapshot['accession']})",
        "정보·표시용 — 13F 는 분기말 기준 45일 지연 공시라 현재 포지션과 다를 수 있음",
    ]
    top_tickers = [h["ticker"] for h in top if h.get("ticker")]
    tags = ["wiki", "market", "source_digest", "notable_investor", "13f", filer_key,
            *(f"ticker:{t}" for t in top_tickers[:8])]
    return {
        "id": f"notable-investor-{filer_key}",
        "title": f"기관투자자 위키: {snapshot['filer_name']}",
        "surface": "market",
        "kind": "source_digest",
        "status": "reviewed",
        "tags": tags,
        "summary": (f"{snapshot['filing_date']} 13F 기준 {len(holdings)}종목·"
                    f"${snapshot['total_value_usd'] / 1e9:.1f}B · "
                    f"신규편입 {len(diff['new'])}·청산 {len(diff['exited'])}"),
        "body": "\n".join(lines),
        "source_refs": [
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={snapshot['cik']}"
            f"&type=13F-HR&dateb=&owner=include&count=10"
        ],
        "staleness_policy": "refresh_after_90d",
        "confidence": 0.9,
    }


def run(filer_key: str, *, dry_run: bool = False) -> dict:
    from reports import institution_watch

    institution_watch.HISTORY_PATH = HISTORY_PATH
    return institution_watch.run(filer_key, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    notify_lines = []
    for filer_key in TRACKED_FILERS:
        result = run(filer_key, dry_run=args.dry_run)
        if not result.get("ok"):
            logger.warning("13F 조회 실패: %s", filer_key)
            continue
        if result["status"] == "unchanged":
            logger.info("%s — 신규 필링 없음", filer_key)
            continue
        logger.info("%s 위키 갱신 — 신규 %d · 청산 %d", filer_key,
                    len(result["new"]), len(result["exited"]))
        if result["new"]:
            names = ", ".join(_fmt_holding(h) for h in result["new"][:5])
            notify_lines.append(f"🆕 {result['filer_name']} 신규 편입: {names}")
        if result["exited"]:
            names = ", ".join(_fmt_holding(h) for h in result["exited"][:5])
            notify_lines.append(f"📤 {result['filer_name']} 청산: {names}")

    if notify_lines and not args.dry_run:
        try:
            import notify
            text = "🏛️ 유명 투자자 포트폴리오 변경 감지\n" + "\n".join(notify_lines)
            notify.send_telegram(text, token=os.getenv("STOCK_BOT_TOKEN"),
                                 chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
        except Exception as e:
            logger.warning("텔레그램 발송 실패: %s", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
