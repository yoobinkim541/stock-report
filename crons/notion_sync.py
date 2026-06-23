#!/usr/bin/env python3
"""
notion_sync.py — stock-report → Notion 대시보드 자동 동기화

크론 (평일 22:30 UTC = 07:30 KST):
    30 22 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python notion_sync.py >> /tmp/notion_sync.log 2>&1

환경변수:
    NOTION_TOKEN         — Notion Integration Token (필수)
    STOCK_BOT_TOKEN      — 텔레그램 봇 (실패 알림용, 선택)
    STOCK_BOT_CHAT_ID    — 텔레그램 채팅 ID
"""
from __future__ import annotations

import json
import logging
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import urllib.parse
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NOTION_TOKEN     = os.getenv("NOTION_TOKEN")
DASHBOARD_PAGE_ID = "378a13e7-df00-815a-9fe7-feac02ee5dc6"
KST = timezone(timedelta(hours=9))


# ── Notion API ────────────────────────────────────────────────────────────────

def _h(timeout: int = 15):
    return {"Authorization": f"Bearer {NOTION_TOKEN}",
            "Content-Type": "application/json", "Notion-Version": "2022-06-28"}


def update_page(page_id: str, blocks: list[dict]) -> bool:
    import requests
    headers = _h()

    # 기존 블록 조회
    r = requests.get(f"https://api.notion.com/v1/blocks/{page_id}/children", headers=headers, timeout=15)
    if r.status_code != 200:
        logger.error("블록 조회 실패 %s", r.status_code)
        return False

    # 기존 블록 삭제
    for b in r.json().get("results", []):
        requests.delete(f"https://api.notion.com/v1/blocks/{b['id']}", headers=headers, timeout=8)

    # 새 블록 추가 (100개씩 배치)
    for i in range(0, len(blocks), 100):
        batch = blocks[i:i+100]
        r2 = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=headers, json={"children": batch}, timeout=30,
        )
        if not r2.ok:
            logger.error("블록 추가 실패 %s: %s", r2.status_code, r2.text[:200])
            return False
    return True


# ── Notion 블록 빌더 ──────────────────────────────────────────────────────────

def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _h1(text: str) -> dict:
    return {"object": "block", "type": "heading_1",
            "heading_1": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _h2(text: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _h3(text: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _para(text: str, bold: bool = False, color: str = "default") -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text},
                                         "annotations": {"bold": bold}}], "color": color}}


def _callout(text: str, emoji: str = "💡", color: str = "gray_background") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": [{"type": "text", "text": {"content": text}}],
                        "icon": {"type": "emoji", "emoji": emoji}, "color": color}}


def _quote(text: str) -> dict:
    return {"object": "block", "type": "quote",
            "quote": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _image(url: str, caption: str = "") -> dict:
    block: dict = {"object": "block", "type": "image",
                   "image": {"type": "external", "external": {"url": url}}}
    if caption:
        block["image"]["caption"] = [{"type": "text", "text": {"content": caption}}]
    return block


def _table(rows: list[list[str]], header: bool = True) -> dict:
    table_rows = [
        {"object": "block", "type": "table_row",
         "table_row": {"cells": [[{"type": "text", "text": {"content": str(c)}}] for c in row]}}
        for row in rows
    ]
    return {"object": "block", "type": "table",
            "table": {"table_width": len(rows[0]) if rows else 1,
                      "has_column_header": header, "has_row_header": False,
                      "children": table_rows}}


def _toggle(title: str, children: list[dict]) -> dict:
    return {"object": "block", "type": "toggle",
            "toggle": {"rich_text": [{"type": "text", "text": {"content": title}}],
                       "children": children}}


# ── QuickChart.io URL 생성 ─────────────────────────────────────────────────────

def _chart_url(config: dict, w: int = 700, h: int = 320) -> str:
    """QuickChart URL — 2000자 초과 시 /chart/create 단축 URL 사용."""
    import requests as _req
    encoded = urllib.parse.quote(json.dumps(config, ensure_ascii=False))
    direct  = f"https://quickchart.io/chart?c={encoded}&w={w}&h={h}&bkg=%23ffffff"
    if len(direct) <= 1900:
        return direct
    # 단축 URL
    try:
        r = _req.post(
            "https://quickchart.io/chart/create",
            json={"chart": config, "width": w, "height": h, "backgroundColor": "white"},
            timeout=10,
        )
        if r.ok and r.json().get("url"):
            return r.json()["url"]
    except Exception:
        pass
    # 데이터 포인트 줄여서 재시도
    return direct[:1900]


def _fear_greed_chart(fg_series) -> str:
    """Fear/Greed proxy 30일 추이 차트 URL."""
    s = fg_series.dropna().tail(30)
    labels = [d.strftime("%m/%d") for d in s.index]
    values = [round(float(v), 1) for v in s.values]
    # 색상: 0~25 빨강, 26~45 주황, 46~55 회색, 56~75 연두, 76~100 초록
    def _color(v):
        if v <= 25:   return "rgba(220,53,69,0.85)"
        if v <= 45:   return "rgba(255,140,0,0.85)"
        if v <= 55:   return "rgba(150,150,150,0.85)"
        if v <= 75:   return "rgba(100,200,100,0.85)"
        return "rgba(40,167,69,0.85)"
    colors = [_color(v) for v in values]
    config = {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{"label": "Fear/Greed Proxy", "data": values,
                          "backgroundColor": colors, "borderRadius": 3}]
        },
        "options": {
            "title": {"display": True, "text": "Fear/Greed Proxy (최근 30일)", "fontSize": 14},
            "legend": {"display": False},
            "scales": {
                "yAxes": [{"ticks": {"min": 0, "max": 100},
                           "gridLines": {"color": "rgba(0,0,0,0.05)"}}],
                "xAxes": [{"gridLines": {"display": False}}]
            },
            "plugins": {
                "annotation": {
                    "annotations": [
                        {"type": "line", "mode": "horizontal", "scaleID": "y-axis-0",
                         "value": 75, "borderColor": "rgba(40,167,69,0.4)", "borderWidth": 1,
                         "label": {"enabled": True, "content": "탐욕", "position": "right", "fontSize": 10}},
                        {"type": "line", "mode": "horizontal", "scaleID": "y-axis-0",
                         "value": 25, "borderColor": "rgba(220,53,69,0.4)", "borderWidth": 1,
                         "label": {"enabled": True, "content": "공포", "position": "right", "fontSize": 10}},
                    ]
                }
            }
        }
    }
    return _chart_url(config, w=700, h=280)


def _ranking_chart(ranking) -> str:
    """NASDAQ100 랭킹 수평 바 차트 URL."""
    labels = list(reversed([str(row["ticker"]) for _, row in ranking.iterrows()]))
    scores = list(reversed([round(float(row["score"]) * 100, 2) for _, row in ranking.iterrows()]))
    config = {
        "type": "horizontalBar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": "QQQ 초과수익 예측 (%)",
                "data": scores,
                "backgroundColor": [
                    "rgba(54,162,235,0.85)" if s > 0 else "rgba(220,53,69,0.7)"
                    for s in scores
                ],
                "borderRadius": 4,
            }]
        },
        "options": {
            "title": {"display": True, "text": "NASDAQ100 LightGBM 랭킹 (상위 15)", "fontSize": 14},
            "legend": {"display": False},
            "scales": {
                "xAxes": [{"gridLines": {"color": "rgba(0,0,0,0.05)"}}],
                "yAxes": [{"gridLines": {"display": False}}]
            }
        }
    }
    return _chart_url(config, w=700, h=380)


def _equity_chart(equity_df) -> str:
    """ML 전략 이퀴티 곡선 차트 URL."""
    if equity_df.empty:
        return ""
    step = max(1, len(equity_df) // 40)
    df   = equity_df.iloc[::step]
    labels = [d.strftime("%y/%m") for d in df.index]

    datasets = []
    colors = {"ML_model": "rgba(54,162,235,1)", "overlay": "rgba(255,159,64,0.9)",
              "QQQ": "rgba(40,167,69,1)", "SPY": "rgba(150,150,150,0.7)"}
    names  = {"ML_model": "ML 전략", "overlay": "리스크오버레이",
              "QQQ": "QQQ", "SPY": "SPY"}
    for col in ["ML_model", "overlay", "QQQ", "SPY"]:
        if col not in df.columns:
            continue
        datasets.append({
            "label": names.get(col, col),
            "data": [round(float(v), 2) for v in df[col].values],
            "borderColor": colors.get(col, "gray"),
            "borderWidth": 2 if col in ("ML_model", "QQQ") else 1,
            "fill": False,
            "pointRadius": 0,
        })

    config = {
        "type": "line",
        "data": {"labels": labels, "datasets": datasets},
        "options": {
            "title": {"display": True, "text": "이퀴티 곡선 비교 (기준=100)", "fontSize": 14},
            "scales": {
                "xAxes": [{"gridLines": {"color": "rgba(0,0,0,0.04)"}}],
                "yAxes": [{"gridLines": {"color": "rgba(0,0,0,0.04)"}}]
            }
        }
    }
    return _chart_url(config, w=700, h=300)


def _qqq_momentum_chart(qqq_close) -> str:
    """QQQ 60일 가격 + MA200 차트 URL."""
    if qqq_close is None or len(qqq_close) < 60:
        return ""
    tail = qqq_close.tail(60)
    ma60 = qqq_close.rolling(60).mean().tail(60)
    labels = [d.strftime("%m/%d") for d in tail.index]
    config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {"label": "QQQ", "data": [round(float(v), 2) for v in tail.values],
                 "borderColor": "rgba(54,162,235,1)", "borderWidth": 2, "fill": False, "pointRadius": 0},
                {"label": "MA60", "data": [round(float(v), 2) if not __import__("math").isnan(float(v)) else None for v in ma60.values],
                 "borderColor": "rgba(255,99,132,0.8)", "borderWidth": 1,
                 "fill": False, "pointRadius": 0, "borderDash": [5, 5]},
            ]
        },
        "options": {
            "title": {"display": True, "text": "QQQ 가격 + MA60 (최근 60일)", "fontSize": 14},
            "scales": {"xAxes": [{"gridLines": {"display": False}}],
                       "yAxes": [{"gridLines": {"color": "rgba(0,0,0,0.05)"}}]}
        }
    }
    return _chart_url(config, w=700, h=280)


# ── 데이터 수집 ────────────────────────────────────────────────────────────────

def _collect_market() -> dict:
    from barbell_strategy import (
        fetch_qqq_data, fetch_rsi, fetch_vix, fetch_ma200,
        classify_market, fetch_fear_greed, calculate_dca, fetch_portfolio_value,
    )
    qqq_d  = fetch_qqq_data()
    rsi_v  = fetch_rsi("QQQ")
    vix_v  = fetch_vix()
    ma_d   = fetch_ma200("QQQ")
    mt, pk = classify_market(qqq_d, rsi_v, vix_v)
    fg     = fetch_fear_greed()
    dca    = calculate_dca(mt, pk)
    port   = fetch_portfolio_value()
    return {"qqq": qqq_d, "rsi": rsi_v, "vix": vix_v, "ma": ma_d,
            "mt": mt, "pk": pk, "fg": fg, "dca": dca, "port": port}


def _collect_ml():
    import warnings; warnings.filterwarnings("ignore")
    from ml.data_pipeline import build_real_sweetspot_data, build_fear_greed_proxy, fetch_prices
    from ml.sweet_spot import optimize_sweet_spot
    from ml.reporting import _ml_adoption_verdict
    from ml.ranker import rank_today, load_ranker

    data   = build_real_sweetspot_data("QQQ", days=756)
    result = optimize_sweet_spot(data)
    verdict, reasons = _ml_adoption_verdict(result.ml_result, result.qqq_result)

    ranking    = rank_today(mode="nasdaq100", top_n=15)
    ranker_res = load_ranker()

    fg    = build_fear_greed_proxy(days=60)
    prices = fetch_prices(["QQQ"], days=120)
    qqq_close = prices.get("QQQ", __import__("pandas").DataFrame()).get("Close")

    return {"sweet": result, "verdict": verdict, "reasons": reasons,
            "ranking": ranking, "ranker": ranker_res,
            "fg": fg, "qqq_close": qqq_close}


def _load_report_summary() -> str:
    """오늘 투자 리포트 요약 로드 (없으면 빈 문자열)."""
    import glob
    from pathlib import Path
    today = datetime.now(KST).strftime("%Y-%m-%d")
    pattern = str(Path.home() / f"reports/investment-summary-{today}*.txt")
    files = sorted(glob.glob(pattern))
    if files:
        try:
            return Path(files[-1]).read_text(encoding="utf-8")[:2000]
        except Exception:
            pass
    return ""


# ── 블록 빌드 ──────────────────────────────────────────────────────────────────

def build_blocks() -> list[dict]:
    import warnings; warnings.filterwarnings("ignore")
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    blocks: list[dict] = []

    # ── 헤더 ─────────────────────────────────────────────────────────────────
    blocks += [
        _callout(f"📡 stock-report 자동 동기화  •  {now_kst}  •  매일 07:30 KST",
                 "📊", "blue_background"),
        _divider(),
    ]

    # ── 시장 & 포트폴리오 ─────────────────────────────────────────────────────
    blocks.append(_h2("🌡️ 시장 체온계 & 포트폴리오"))
    try:
        mkt = _collect_market()
        qqq  = mkt["qqq"]
        fg   = mkt["fg"]
        port = mkt["port"]
        dca  = mkt["dca"]
        mt, pk = mkt["mt"], mkt["pk"]

        fg_sc    = fg.get("score", 50)
        fg_proxy = fg.get("proxy_score", -1)
        proxy_s  = f"{fg_proxy:.0f}" if fg_proxy >= 0 else "n/a"
        cnn_ok   = fg.get("cnn_ok", True)
        mom_1m   = qqq.get("mom_1m_pct", 0) or 0
        dd       = qqq.get("drawdown_pct", 0) or 0
        ret      = port.get("return_pct", 0) or 0
        total    = port.get("total_usd", 0)

        phase_labels = {
            "bull": {"bull2": "🫧 Bull-2 (버블)", "bull1": "🐂 Bull-1 (강세)"},
            "bear": {0: "🟢 0 정상", 1: "🟡 1 조정", 2: "🟠 2 중조정",
                     3: "🔴 3 심조정", 4: "🚨 4 급락", 5: "💥 5 폭락"},
        }
        phase_str = phase_labels.get(mt, {}).get(pk, f"{mt}-{pk}")

        fg_emoji = "💀" if fg_sc <= 25 else "😨" if fg_sc <= 45 else "😐" if fg_sc <= 55 else "😄" if fg_sc <= 75 else "🤑"
        fg_label = ("극단공포" if fg_sc <= 25 else "공포" if fg_sc <= 45 else
                    "중립" if fg_sc <= 55 else "탐욕" if fg_sc <= 75 else "극단탐욕")
        vix_v = mkt["vix"]
        vix_lbl = "💥극공포" if vix_v > 40 else "🚨공포" if vix_v > 30 else "😴과낙관" if vix_v < 15 else "✅정상"

        # 핵심 지표 callout (Phase에 따라 색상 변경)
        phase_color = {"bull": "green_background", "neutral": "gray_background"}.get(mt, "red_background")
        blocks.append(_callout(
            f"Phase: {phase_str}  |  QQQ ${qqq.get('current',0):,.2f}  |  낙폭 {dd:+.1f}%  |  1M모멘텀 {mom_1m:+.1f}%",
            "🎯", phase_color))

        blocks.append(_table([
            ["지표", "값", "상태"],
            ["RSI (QQQ)", f"{mkt['rsi']:.1f}", "🔥과매도" if mkt['rsi'] < 30 else "⚠️약세" if mkt['rsi'] < 40 else "🌡과매수" if mkt['rsi'] > 70 else "✅중립"],
            ["VIX", f"{vix_v:.1f}", vix_lbl],
            [f"Fear/Greed CNN{'(미작동)' if not cnn_ok else ''}", f"{fg_sc:.1f}", f"{fg_emoji} {fg_label}"],
            ["Fear/Greed Proxy", proxy_s, "🟢탐욕" if float(proxy_s) > 55 else "🔴공포" if float(proxy_s) < 45 else "⚪중립" if proxy_s != "n/a" else "—"],
            ["200MA 위치", f"{'위 ▲' if mkt['ma'].get('above_ma200') else '아래 ▽'}  {mkt['ma'].get('gap_pct',0):+.1f}%", ""],
            ["포트폴리오 총액", f"${total:,.2f}", f"{ret:+.1f}%"],
            ["DCA 배율", f"{dca['multiplier']}×", f"{dca['total_krw']:,}원/일"],
        ]))

        logger.info("시장 섹션 완료")
    except Exception as e:
        logger.warning("시장 데이터 실패: %s", e)
        blocks.append(_callout(f"⚠️ 시장 데이터 수집 실패: {e}", "❗", "red_background"))

    blocks.append(_divider())

    # ── 차트 & 랭킹 ──────────────────────────────────────────────────────────
    blocks.append(_h2("📈 시황 차트"))
    ml_data = None   # 차트 섹션 실패 시에도 랭킹/ML 섹션이 재시도할 수 있도록 선초기화
    try:
        ml_data = _collect_ml()
        qqq_close = ml_data["qqq_close"]
        fg_proxy  = ml_data["fg"]

        # QQQ 가격 차트
        if qqq_close is not None:
            url = _qqq_momentum_chart(qqq_close)
            if url:
                blocks.append(_image(url, "QQQ 최근 60일 가격 + MA60"))

        # Fear/Greed Proxy 추이
        if not fg_proxy.empty:
            url = _fear_greed_chart(fg_proxy)
            blocks.append(_image(url, "Fear/Greed Proxy 최근 30일 (0=극도공포, 100=극도탐욕)"))

        logger.info("시황 차트 완료")
    except Exception as e:
        logger.warning("시황 차트 실패: %s", e)
        blocks.append(_callout(f"⚠️ 차트 생성 실패: {e}", "❗"))

    blocks.append(_divider())

    # ── NASDAQ100 랭킹 ────────────────────────────────────────────────────────
    blocks.append(_h2("🏆 NASDAQ100 일일 랭킹"))
    try:
        if ml_data is None:
            ml_data = _collect_ml()
        ranking    = ml_data["ranking"]
        ranker_res = ml_data["ranker"]

        if not ranking.empty and ranker_res:
            blocks.append(_quote(
                f"OOS IC: {ranker_res.oos_ic:+.3f}  |  ICIR: {ranker_res.oos_icir:.2f}  "
                f"|  상위10% 초과수익: {ranker_res.oos_top_decile_ret*100:+.1f}%  |  학습기준: {ranker_res.train_end_date}"
            ))

            # 랭킹 차트
            url = _ranking_chart(ranking)
            if url:
                blocks.append(_image(url, "LightGBM QQQ 초과수익 예측 기반 랭킹"))

            # 랭킹 테이블 (toggle 안에)
            rows = [["순위", "종목", "점수", "초과모멘텀60d", "베타", "RSI", "변동성"]]
            for _, row in ranking.iterrows():
                rows.append([
                    str(int(row["rank"])),
                    str(row["ticker"]),
                    f"{float(row['score'])*100:+.2f}%",
                    f"{float(row.get('excess_mom_60d',0))*100:+.1f}%" if 'excess_mom_60d' in row else "—",
                    f"{float(row.get('beta_60d',0)):.2f}" if 'beta_60d' in row else "—",
                    f"{float(row.get('rsi_14',0)):.1f}" if 'rsi_14' in row else "—",
                    f"{float(row.get('vol_20d',0))*100:.1f}%" if 'vol_20d' in row else "—",
                ])
            blocks.append(_toggle("📋 상세 랭킹 테이블 (클릭 펼치기)", [_table(rows)]))
            blocks.append(_callout("⚠️ survivorship bias 있음 — 현재 NASDAQ100 구성종목 기준", "⚠️", "yellow_background"))
        else:
            blocks.append(_callout("랭킹 데이터 없음", "⚠️"))
        logger.info("랭킹 섹션 완료")
    except Exception as e:
        logger.warning("랭킹 실패: %s", e)
        blocks.append(_callout(f"⚠️ 랭킹 데이터 실패: {e}", "❗"))

    blocks.append(_divider())

    # ── ML 전략 성과 ──────────────────────────────────────────────────────────
    blocks.append(_h2("🧠 ML 전략 성과 (QQQ 3년 실데이터)"))
    try:
        sweet = ml_data["sweet"]
        verdict = ml_data["verdict"]
        reasons = ml_data["reasons"]

        ml   = sweet.ml_result
        qqq  = sweet.qqq_result
        ov   = sweet.overlay_result
        wf   = sweet.wf_summary

        verdict_emoji = "✅" if "채택" in verdict and "비채택" not in verdict and "조건부" not in verdict else "⚠️" if "조건부" in verdict else "❌"
        verdict_color = "green_background" if verdict_emoji == "✅" else "yellow_background" if verdict_emoji == "⚠️" else "red_background"
        blocks.append(_callout(verdict, verdict_emoji, verdict_color))

        for r in reasons:
            blocks.append(_bullet(r))

        blocks.append(_table([
            ["전략", "CAGR", "Sharpe", "MDD", "비고"],
            ["ML (nested OOS)", f"{(ml.cagr or 0):.1%}", f"{(ml.sharpe or 0):.2f}", f"{ml.max_drawdown:.1%}", f"{ml.n_days}일"],
            ["리스크오버레이", f"{(ov.cagr or 0):.1%}", f"{(ov.sharpe or 0):.2f}", f"{ov.max_drawdown:.1%}", "200MA+ML크기"],
            ["QQQ 매수보유", f"{(qqq.cagr or 0):.1%}", f"{(qqq.sharpe or 0):.2f}", f"{qqq.max_drawdown:.1%}", "벤치마크"],
        ]))

        # WF 요약
        blocks.append(_quote(
            f"Walk-forward {wf.get('n_folds','?')}폴드  |  "
            f"평균 CAGR {(wf.get('mean_cagr') or 0):.1%}  |  "
            f"평균 Sharpe {(wf.get('mean_sharpe') or 0):.2f} ± {(wf.get('std_sharpe') or 0):.2f}"
        ))

        # 이퀴티 커브 차트
        url = _equity_chart(sweet.equity)
        if url:
            blocks.append(_image(url, "ML 전략 vs QQQ 이퀴티 곡선 비교"))

        logger.info("ML 성과 섹션 완료")
    except Exception as e:
        logger.warning("ML 성과 실패: %s", e)
        blocks.append(_callout(f"⚠️ ML 데이터 실패: {e}", "❗"))

    blocks.append(_divider())

    # ── 오늘의 투자 리포트 ─────────────────────────────────────────────────────
    blocks.append(_h2("📰 오늘의 투자 리포트"))
    try:
        summary = _load_report_summary()
        if summary:
            lines = [l for l in summary.strip().split("\n") if l.strip()]
            blocks.append(_toggle("📄 전체 요약 (클릭 펼치기)",
                                  [_para(l) for l in lines[:40]]))
            logger.info("리포트 요약 추가 완료 (%d줄)", len(lines))
        else:
            today_kst = datetime.now(KST).strftime("%Y-%m-%d")
            blocks.append(_callout(
                f"오늘 리포트 미생성 ({today_kst}) — 크론 23:00 UTC 이후 자동 생성됩니다",
                "📭", "gray_background"))
    except Exception as e:
        logger.warning("리포트 로드 실패: %s", e)

    blocks.append(_divider())
    blocks.append(_para(f"🤖 stock-report 자동 생성  •  {now_kst}", color="gray"))

    return blocks


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    logger.info("=== notion_sync 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))

    if not NOTION_TOKEN:
        logger.error("NOTION_TOKEN 환경변수 미설정")
        return 1

    blocks = build_blocks()
    ok = update_page(DASHBOARD_PAGE_ID, blocks)

    if ok:
        logger.info("노션 동기화 완료 ✅ (%d블록)", len(blocks))
    else:
        logger.error("노션 동기화 실패 ❌")
        # 실패 알림 — notify 단일 진실원 위임 (자체 응답검증·토큰 마스킹·실패 시 로깅만)
        import notify
        notify.send_telegram("⚠️ Notion 동기화 실패")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
