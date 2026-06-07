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

# 노션 — 모든 동기화는 대시보드 단일 페이지에 섹션으로 작성
# https://app.notion.com/p/Stock-Report-Dashboard-378a13e7df00815a9fe7feac02ee5dc6
DASHBOARD_PAGE_ID = "378a13e7-df00-815a-9fe7-feac02ee5dc6"

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
            "종목":   {"title":  [{"text": {"content": str(row["ticker"])}}]},
            "날짜":   {"date":   {"start": today}},
            "순위":   {"number": int(row["rank"])},
            "점수":   {"number": round(float(row["score"]), 6)},
            "OOS_IC": {"number": oos_ic},
            "OOS_ICIR": {"number": oos_icir},
        }
        for notion_key, df_key in [
            ("초과모멘텀_60d", "excess_mom_60d"),
            ("베타_60d",       "beta_60d"),
            ("RSI_14",         "rsi_14"),
            ("변동성_20d",     "vol_20d"),
        ]:
            if df_key in row:
                v = float(row[df_key])
                if v == v:
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
            fetch_qqq_data, fetch_rsi, fetch_vix,
            classify_market, fetch_fear_greed,
            calculate_dca, DCA_DAILY_BASE_KRW,
            fetch_portfolio_value,
        )
    except Exception as e:
        logger.warning("포트폴리오 모듈 로드 실패: %s", e)
        return False

    try:
        qqq  = fetch_qqq_data()
        rsi  = fetch_rsi("QQQ")
        vix  = fetch_vix()
        mt, pk = classify_market(qqq, rsi, vix)
        fg   = fetch_fear_greed()
        dca  = calculate_dca(mt, pk)
        port = fetch_portfolio_value()
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
        from barbell_strategy import fetch_qqq_data, fetch_rsi, fetch_vix, classify_market, fetch_fear_greed
        qqq_data = fetch_qqq_data()
        rsi_val  = fetch_rsi("QQQ")
        vix_val  = fetch_vix()
        mt, pk   = classify_market(qqq_data, rsi_val, vix_val)
        fg       = fetch_fear_greed()
        qqq      = qqq_data
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

def build_full_dashboard() -> str:
    """대시보드 전체 마크다운 컨텐츠 빌드."""
    import warnings; warnings.filterwarnings("ignore")
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    sections: list[str] = [
        f"# 📊 Stock Report Dashboard",
        f"> 최종 업데이트: {now_kst}",
        "",
        "---",
    ]

    # ── 시장 현황 & 포트폴리오 ───────────────────────────────────────────────
    try:
        from barbell_strategy import (
            fetch_qqq_data, fetch_rsi, fetch_vix,
            classify_market, fetch_fear_greed,
            calculate_dca, fetch_portfolio_value,
        )
        qqq_d  = fetch_qqq_data()
        rsi_v  = fetch_rsi("QQQ")
        vix_v  = fetch_vix()
        mt, pk = classify_market(qqq_d, rsi_v, vix_v)
        fg     = fetch_fear_greed()
        dca    = calculate_dca(mt, pk)
        port   = fetch_portfolio_value()

        fg_sc    = fg.get("score", 50)
        fg_proxy = fg.get("proxy_score", -1)
        proxy_s  = f"{fg_proxy:.0f}" if fg_proxy >= 0 else "n/a"
        total    = port.get("total_usd", 0)
        ret_pct  = port.get("return_pct", 0) or 0
        phase_str = f"{mt}-{pk}"

        sections += [
            "## 📌 시장 & 포트폴리오",
            "",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| Phase | {phase_str} |",
            f"| QQQ 현재가 | ${qqq_d.get('current',0):,.2f} |",
            f"| QQQ 1M 모멘텀 | {qqq_d.get('mom_1m_pct',0):+.1f}% |",
            f"| RSI | {rsi_v:.1f} |",
            f"| VIX | {vix_v:.1f} |",
            f"| Fear/Greed CNN | {fg_sc:.1f} |",
            f"| Fear/Greed Proxy | {proxy_s} |",
            f"| 포트폴리오 총액 | ${total:,.2f} |",
            f"| 수익률 | {ret_pct:+.2f}% |",
            f"| DCA 배율 | {dca['multiplier']}× ({dca['total_krw']:,}원/일) |",
            "",
            "---",
        ]
        logger.info("시장/포트폴리오 섹션 완료")
    except Exception as e:
        logger.warning("시장 데이터 수집 실패: %s", e)
        sections += ["## 📌 시장 & 포트폴리오", "", "> ⚠️ 데이터 수집 실패", "", "---"]

    # ── NASDAQ100 랭킹 ────────────────────────────────────────────────────────
    try:
        from ml.ranker import rank_today, load_ranker
        ranking = rank_today(mode="nasdaq100", top_n=15)
        result  = load_ranker()

        if not ranking.empty and result:
            sections += [
                "## 📈 NASDAQ100 일일 랭킹 (LightGBM)",
                "",
                f"OOS IC: {result.oos_ic:+.3f}  |  ICIR: {result.oos_icir:.2f}  |  학습 기준: {result.train_end_date}",
                "",
                "| 순위 | 종목 | 점수 | 초과모멘텀60d | 베타 |",
                "|------|------|------|--------------|------|",
            ]
            for _, row in ranking.iterrows():
                excess = f"{float(row.get('excess_mom_60d',0))*100:+.1f}%" if 'excess_mom_60d' in row else "—"
                beta   = f"{float(row.get('beta_60d',0)):.2f}" if 'beta_60d' in row else "—"
                sections.append(f"| {int(row['rank'])} | {row['ticker']} | {float(row['score'])*100:+.2f}% | {excess} | {beta} |")
            sections += ["", "⚠️ survivorship bias 있음 (현재 구성종목 기준)", "", "---"]
            logger.info("랭킹 섹션 완료 (%d종목)", len(ranking))
        else:
            sections += ["## 📈 NASDAQ100 일일 랭킹", "", "> ⚠️ 랭킹 데이터 없음", "", "---"]
    except Exception as e:
        logger.warning("랭킹 데이터 수집 실패: %s", e)
        sections += ["## 📈 NASDAQ100 일일 랭킹", "", "> ⚠️ 데이터 수집 실패", "", "---"]

    # ── ML 전략 성과 ──────────────────────────────────────────────────────────
    try:
        from ml.data_pipeline import build_real_sweetspot_data
        from ml.sweet_spot import optimize_sweet_spot
        from ml.reporting import _ml_adoption_verdict

        data   = build_real_sweetspot_data("QQQ", days=756)
        result = optimize_sweet_spot(data)
        ml     = result.ml_result
        qqq    = result.qqq_result
        wf     = result.wf_summary
        verdict, reasons = _ml_adoption_verdict(ml, qqq)

        sections += [
            "## 🧠 ML 전략 성과 (QQQ 3년 실데이터)",
            "",
            f"**채택 판정: {verdict}**",
            "",
            "| 전략 | CAGR | Sharpe | MDD |",
            "|------|------|--------|-----|",
            f"| ML (nested OOS) | {(ml.cagr or 0):.1%} | {(ml.sharpe or 0):.2f} | {ml.max_drawdown:.1%} |",
            f"| QQQ 매수보유 | {(qqq.cagr or 0):.1%} | {(qqq.sharpe or 0):.2f} | {qqq.max_drawdown:.1%} |",
            "",
            f"Walk-forward: {wf.get('n_folds','?')}폴드 | 평균 CAGR {(wf.get('mean_cagr') or 0):.1%} | 평균 Sharpe {(wf.get('mean_sharpe') or 0):.2f}",
            "",
            "---",
        ]
        logger.info("ML 성과 섹션 완료")
    except Exception as e:
        logger.warning("ML 데이터 수집 실패: %s", e)
        sections += ["## 🧠 ML 전략 성과", "", "> ⚠️ 데이터 수집 실패", "", "---"]

    sections.append("*이 페이지는 stock-report 프로젝트에서 자동 생성됩니다.*")
    return "\n".join(sections)


def main() -> int:
    logger.info("=== notion_sync 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))

    if not NOTION_TOKEN:
        logger.error("NOTION_TOKEN 환경변수 미설정")
        return 1

    content = build_full_dashboard()
    ok = _update_page_blocks(DASHBOARD_PAGE_ID, content)

    if ok:
        logger.info("노션 동기화 완료 ✅")
    else:
        logger.error("노션 동기화 실패 ❌")
        bot_token = os.getenv("STOCK_BOT_TOKEN")
        chat_id   = os.getenv("STOCK_BOT_CHAT_ID")
        if bot_token and chat_id:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": "⚠️ Notion 동기화 실패"},
                timeout=10,
            )

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
