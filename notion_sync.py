#!/usr/bin/env python3
"""
notion_sync.py — stock-report → Notion 자동 동기화

동기화 항목:
  1. NASDAQ100 일일 랭킹 (DB 레코드 추가)
  2. 포트폴리오 현황 페이지 업데이트
  3. ML 전략 성과 페이지 업데이트

크론 (미국 장 마감 후, 평일 22:30 UTC = 07:30 KST):
    30 22 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python notion_sync.py >> /tmp/notion_sync.log 2>&1

환경변수:
    NOTION_TOKEN         — Notion Integration Token (필수)
    STOCK_BOT_TOKEN      — 텔레그램 봇 (실패 알림용, 선택)
    STOCK_BOT_CHAT_ID    — 텔레그램 채팅 ID
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NOTION_TOKEN = os.getenv("NOTION_TOKEN")

# 노션 페이지/DB ID (notion_sync.py 설치 시 고정)
RANKING_DB_ID      = "e91906d9-cced-4049-a62a-cdac348127a9"   # NASDAQ100 랭킹 DB (data source ID)
PORTFOLIO_PAGE_ID  = "378a13e7-df00-8159-a568-ed25c4351a17"   # 포트폴리오 현황
ML_PAGE_ID         = "378a13e7-df00-8139-b4d2-f36d265fc966"   # ML 전략 성과
DASHBOARD_PAGE_ID  = "378a13e7-df00-815a-9fe7-feac02ee5dc6"   # 메인 대시보드

KST = timezone(timedelta(hours=9))


# ── Notion API 헬퍼 ───────────────────────────────────────────────────────────

def _notion_headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def _create_page(parent_id: str, is_db: bool, properties: dict) -> dict | None:
    """노션 DB에 페이지(레코드) 생성."""
    import requests
    parent = {"database_id": parent_id} if is_db else {"page_id": parent_id}
    payload = {"parent": parent, "properties": properties}
    try:
        r = requests.post(
            "https://api.notion.com/v1/pages",
            headers=_notion_headers(),
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("Notion 페이지 생성 실패: %s", e)
        return None


def _update_page_blocks(page_id: str, markdown_content: str) -> bool:
    """노션 페이지 블록을 새 마크다운으로 교체."""
    import requests

    # 1) 기존 블록 목록 조회
    try:
        r = requests.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=_notion_headers(),
            timeout=15,
        )
        r.raise_for_status()
        blocks = r.json().get("results", [])
    except Exception as e:
        logger.error("블록 조회 실패: %s", e)
        return False

    # 2) 기존 블록 삭제
    for block in blocks:
        try:
            requests.delete(
                f"https://api.notion.com/v1/blocks/{block['id']}",
                headers=_notion_headers(),
                timeout=10,
            )
        except Exception:
            pass

    # 3) 새 블록 추가 (단순 paragraph 분할)
    new_blocks = _markdown_to_blocks(markdown_content)
    if not new_blocks:
        return True
    try:
        r = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=_notion_headers(),
            json={"children": new_blocks},
            timeout=20,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error("블록 업데이트 실패: %s", e)
        return False


def _markdown_to_blocks(text: str) -> list[dict]:
    """간단한 마크다운 → Notion block 변환."""
    blocks = []
    for line in text.split("\n"):
        line = line.rstrip()
        if line.startswith("# "):
            blocks.append({"object": "block", "type": "heading_1",
                           "heading_1": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}})
        elif line.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]}})
        elif line.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]}})
        elif line.startswith("- ") or line.startswith("• "):
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}})
        elif line.startswith("> "):
            blocks.append({"object": "block", "type": "quote",
                           "quote": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}})
        elif line == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif line:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}]}})
        else:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": []}})
    return blocks[:100]   # Notion API 한 번에 최대 100 블록


# ── 1. 랭킹 DB 동기화 ─────────────────────────────────────────────────────────

def sync_ranking() -> bool:
    """오늘 NASDAQ100 랭킹을 Notion DB에 추가."""
    import warnings; warnings.filterwarnings("ignore")
    try:
        from ml.ranker import rank_today, load_ranker
        ranking = rank_today(mode="nasdaq100", top_n=15)
        result  = load_ranker()
        if ranking.empty or result is None:
            logger.warning("랭킹 데이터 없음")
            return False
    except Exception as e:
        logger.error("랭킹 생성 실패: %s", e)
        return False

    today = datetime.now(KST).date().isoformat()
    oos_ic   = round(result.oos_ic, 4)
    oos_icir = round(result.oos_icir, 3)

    ok_count = 0
    for _, row in ranking.iterrows():
        props = {
            "종목":            {"title":  [{"text": {"content": str(row["ticker"])}}]},
            "날짜":            {"date":   {"start": today}},
            "순위":            {"number": int(row["rank"])},
            "점수":            {"number": round(float(row["score"]), 6)},
            "OOS_IC":          {"number": oos_ic},
            "OOS_ICIR":        {"number": oos_icir},
        }
        # 선택 피처 (컬럼이 없으면 건너뜀)
        for notion_key, df_key in [
            ("초과모멘텀_60d", "excess_mom_60d"),
            ("베타_60d",       "beta_60d"),
            ("RSI_14",         "rsi_14"),
            ("변동성_20d",     "vol_20d"),
        ]:
            if df_key in row:
                v = float(row[df_key])
                if v == v:   # NaN 제외
                    props[notion_key] = {"number": round(v, 6)}

        res = _create_page(RANKING_DB_ID, is_db=True, properties=props)
        if res:
            ok_count += 1

    logger.info("랭킹 DB 동기화: %d/%d 레코드", ok_count, len(ranking))
    return ok_count > 0


# ── 2. 포트폴리오 현황 페이지 ─────────────────────────────────────────────────

def sync_portfolio() -> bool:
    """포트폴리오 현황 페이지 업데이트."""
    try:
        from barbell_strategy import (
            fetch_market_data, classify_market, fetch_fear_greed,
            calculate_dca, DCA_DAILY_BASE_KRW,
        )
        from holding_manager import load_portfolio_snapshot
        from telegram_bot import _format_phase_info
    except Exception as e:
        logger.warning("포트폴리오 모듈 로드 실패: %s", e)
        return False

    try:
        md   = fetch_market_data()
        mt, pk = classify_market(md)
        fg   = fetch_fear_greed()
        dca  = calculate_dca(mt, pk)
        snap = load_portfolio_snapshot()
        port = snap.get("overseas_fractional", {})
        total_usd = port.get("total_usd", 0)
        ret_pct   = port.get("return_pct", 0) or 0
        now_kst   = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    except Exception as e:
        logger.error("포트폴리오 데이터 수집 실패: %s", e)
        return False

    fg_sc    = fg.get("score", 50)
    fg_proxy = fg.get("proxy_score", -1)
    proxy_s  = f"{fg_proxy:.0f}" if fg_proxy >= 0 else "n/a"

    phase_label = {
        "bull": {"bull2": "🫧 Bull-2", "bull1": "🐂 Bull-1"}.get(str(pk), "🟢 Bull"),
        "bear": {0: "🟢 0", 1: "🟡 1", 2: "🟠 2", 3: "🔴 3", 4: "🚨 4", 5: "💥 5"}.get(pk, f"Bear-{pk}"),
        "neutral": "⚪ Neutral",
    }.get(mt, mt)

    content = f"""# 💼 포트폴리오 현황

> 최종 업데이트: {now_kst}

## 하이라이트

| 항목 | 값 |
|------|-----|
| 최종 업데이트 | {now_kst} |
| 총액 (USD) | ${total_usd:,.2f} |
| 수익률 | {ret_pct:+.2f}% |
| Phase | {phase_label} |
| DCA 배율 | {dca['multiplier']}× ({dca['total_krw']:,}원/일) |
| Fear/Greed CNN | {fg_sc:.1f} |
| Fear/Greed Proxy | {proxy_s} |

---

## DCA 배분

{chr(10).join(f"- {ticker}: {int(amt):,}원" for ticker, amt in dca.get('by_ticker', {}).items())}

---

*Phase: {mt} / {pk} | 기준: QQQ 낙폭 + RSI + VIX*
"""

    ok = _update_page_blocks(PORTFOLIO_PAGE_ID, content)
    if ok:
        logger.info("포트폴리오 페이지 업데이트 완료")
    return ok


# ── 3. ML 전략 성과 페이지 ────────────────────────────────────────────────────

def sync_ml_report() -> bool:
    """ML 전략 성과 페이지 업데이트."""
    import warnings; warnings.filterwarnings("ignore")
    try:
        from ml.data_pipeline import build_real_sweetspot_data
        from ml.sweet_spot import optimize_sweet_spot
        from ml.reporting import _ml_adoption_verdict
    except Exception as e:
        logger.warning("ML 모듈 로드 실패: %s", e)
        return False

    try:
        data   = build_real_sweetspot_data("QQQ", days=756)
        result = optimize_sweet_spot(data)
    except Exception as e:
        logger.error("ML 최적화 실패: %s", e)
        return False

    ml  = result.ml_result
    qqq = result.qqq_result
    spy = result.spy_result
    wf  = result.wf_summary
    verdict, reasons = _ml_adoption_verdict(ml, qqq)
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    fi_lines = "\n".join(
        f"- {name}: {int(imp)}"
        for name, imp in result.ml_result.extra.get("feature_importance", {}).items()
    ) if hasattr(result.ml_result, 'extra') else ""

    content = f"""# 🧠 ML 전략 성과

> 최종 업데이트: {now_kst} | 실데이터 QQQ 756일 (3년)

## 채택 판정

**{verdict}**

{chr(10).join(f"- {r}" for r in reasons)}

---

## 성과 비교

| 전략 | CAGR | Sharpe | MDD |
|------|------|--------|-----|
| ML (nested OOS) | {(ml.cagr or 0):.1%} | {(ml.sharpe or 0):.2f} | {ml.max_drawdown:.1%} |
| QQQ 매수보유 | {(qqq.cagr or 0):.1%} | {(qqq.sharpe or 0):.2f} | {qqq.max_drawdown:.1%} |
| SPY 매수보유 | {(spy.cagr or 0):.1%} | {(spy.sharpe or 0):.2f} | {spy.max_drawdown:.1%} |

---

## Walk-forward 검증

| 항목 | 값 |
|------|-----|
| 폴드 수 | {wf.get('n_folds', '?')} |
| 평균 CAGR | {(wf.get('mean_cagr') or 0):.1%} |
| 평균 Sharpe | {(wf.get('mean_sharpe') or 0):.2f} ± {(wf.get('std_sharpe') or 0):.2f} |

---

*모델: LightGBM ExcessReturnModel | 피처: momentum/volatility/RSI/MA200/VIX/credit/FG proxy/beta*
"""

    ok = _update_page_blocks(ML_PAGE_ID, content)
    if ok:
        logger.info("ML 성과 페이지 업데이트 완료")
    return ok


# ── 4. 대시보드 요약 업데이트 ─────────────────────────────────────────────────

def sync_dashboard_summary() -> bool:
    """메인 대시보드 빠른 현황 업데이트."""
    try:
        from barbell_strategy import fetch_market_data, classify_market, fetch_fear_greed, fetch_vix, fetch_rsi, fetch_qqq_data
        md   = fetch_market_data()
        mt, pk = classify_market(md)
        fg   = fetch_fear_greed()
        qqq  = md.get("qqq") or {}
    except Exception as e:
        logger.warning("대시보드 데이터 수집 실패: %s", e)
        return False

    now_kst   = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    fg_sc     = fg.get("score", 50)
    fg_proxy  = fg.get("proxy_score", -1)
    proxy_s   = f"{fg_proxy:.0f}" if fg_proxy >= 0 else "n/a"
    mom_1m    = qqq.get("mom_1m_pct", 0) or 0
    phase_str = f"{mt}-{pk}"

    content = f"""# 📊 Stock Report Dashboard

> 자동 동기화: stock-report 프로젝트 → Notion
> 매일 22:30 UTC (07:30 KST) 업데이트

---

## 📌 빠른 현황

| 항목 | 값 |
|------|-----|
| 최종 업데이트 | {now_kst} |
| Fear/Greed (CNN) | {fg_sc:.1f} |
| Fear/Greed (Proxy) | {proxy_s} |
| 현재 Phase | {phase_str} |
| QQQ 1M 모멘텀 | {mom_1m:+.1f}% |

---

## 🗂️ 하위 페이지

- 📈 NASDAQ100 일일 랭킹
- 💼 포트폴리오 현황
- 🧠 ML 전략 성과

---

*이 페이지는 [stock-report](https://github.com/yoobinkim541/stock-report) 프로젝트에서 자동 생성됩니다.*
"""

    ok = _update_page_blocks(DASHBOARD_PAGE_ID, content)
    if ok:
        logger.info("대시보드 업데이트 완료")
    return ok


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    logger.info("=== notion_sync 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))

    if not NOTION_TOKEN:
        logger.error("NOTION_TOKEN 환경변수 미설정")
        return 1

    results = {
        "ranking":   sync_ranking(),
        "portfolio": sync_portfolio(),
        "ml_report": sync_ml_report(),
        "dashboard": sync_dashboard_summary(),
    }

    ok  = sum(results.values())
    all = len(results)
    logger.info("동기화 완료: %d/%d 성공 %s", ok, all, results)

    if ok < all:
        bot_token = os.getenv("STOCK_BOT_TOKEN")
        chat_id   = os.getenv("STOCK_BOT_CHAT_ID")
        if bot_token and chat_id:
            import requests
            failed = [k for k, v in results.items() if not v]
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id,
                      "text": f"⚠️ Notion 동기화 일부 실패: {', '.join(failed)}"},
                timeout=10,
            )

    return 0 if ok == all else 1


if __name__ == "__main__":
    sys.exit(main())
