"""dashboard/views.py — 모듈별 표시 데이터 (기존 provider 래퍼·graceful).

전부 try/except 로 감싸 한 모듈 실패가 화면을 깨지 않게 한다. 네트워크 호출이라
app.py 에서 st.cache_data 로 감싼다. provider 는 함수 내부 import(테스트서 monkeypatch 용).
"""
from __future__ import annotations

import os
import re
from datetime import timedelta
from typing import Any

from agent_console import storage
from dashboard import chart_workspace
from ohlc_utils import normalize_ohlc_frame


class _StrategyStudioProxy:
    def __init__(self):
        self._module = None

    def _load(self):
        if self._module is None:
            from agent_console import strategy_studio as _strategy_studio

            self._module = _strategy_studio
        return self._module

    def __getattr__(self, name):
        return getattr(self._load(), name)

    def __setattr__(self, name, value):
        if name == "_module":
            object.__setattr__(self, name, value)
            return
        setattr(self._load(), name, value)


strategy_studio = _StrategyStudioProxy()


def _strip_html(s: str) -> str:
    """텔레그램용 HTML 태그 제거 (st.code 모노스페이스 렌더용)."""
    return re.sub(r"<[^>]+>", "", s or "")


def valuation(ticker: str) -> dict:
    """상대가치 + 컨센서스 + 실적 서프라이즈 이력."""
    from providers import earnings_data
    out: dict = {"ticker": ticker}
    for key, fn in (("metrics", lambda: earnings_data.valuation_metrics(ticker)),
                    ("consensus", lambda: earnings_data.consensus(ticker)),
                    ("history", lambda: earnings_data.earnings_history(ticker, limit=8))):
        try:
            out[key] = fn()
        except Exception as e:
            out[key + "_error"] = str(e)
    return out


def financials(ticker: str) -> dict:
    """펀더멘털 추세 (美 SEC EDGAR / 韓 DART)."""
    try:
        if str(ticker or "").upper().endswith((".KS", ".KQ")):
            from providers import kr_fundamentals
            return kr_fundamentals.financial_trends(ticker)
        from providers import edgar
        return {"trends": edgar.fundamental_trends(ticker)}
    except Exception as e:
        return {"error": str(e)}


def risk_report_text(weights: dict) -> str:
    """포트폴리오 리스크 리포트 (format_risk_report → HTML 태그 제거 평문)."""
    from ml import risk_model
    if not weights:
        return "보유 데이터 없음 — portfolio_snapshot 확인 필요"
    try:
        summ = risk_model.portfolio_risk_summary(weights)
        return _strip_html(risk_model.format_risk_report(summ))
    except Exception as e:
        return f"리스크 분석 실패: {e}"


def risk_summary(weights: dict) -> dict:
    """구조화 리스크 요약 (위험기여·팩터β·레버리지 — 차트용). graceful."""
    from ml import risk_model
    if not weights:
        return {"error": "보유 데이터 없음 — portfolio_snapshot 확인 필요"}
    try:
        summ = risk_model.portfolio_risk_summary(weights)
        return summ if isinstance(summ, dict) else {"error": "리스크 계산 데이터 부족"}
    except Exception as e:
        return {"error": str(e)}


def strategy_studio_catalog(limit: int = 50) -> dict[str, Any]:
    """전략 스튜디오 상태 + 카탈로그."""
    try:
        try:
            return strategy_studio.strategy_lab_state(limit=limit)
        except TypeError as exc:
            if "unexpected keyword argument" in str(exc) or "positional argument" in str(exc):
                return strategy_studio.strategy_lab_state()
            raise
    except Exception as e:
        return {"ok": False, "error": str(e), "catalog": {"count": 0, "specs": []}, "presets": {}}


def strategy_studio_spec(spec_id: str, version: int | None = None) -> dict[str, Any]:
    try:
        return strategy_studio.get_strategy_spec(spec_id, version=version) or {"ok": False, "error": "spec not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def strategy_studio_preview(spec: dict[str, Any], *, benchmark: str | None = None,
                            period: str | None = None) -> dict[str, Any]:
    try:
        return strategy_studio.preview_strategy_spec(spec, benchmark=benchmark, period=period)
    except Exception as e:
        return {"ok": False, "error": str(e), "report": {}, "metrics": {}, "benchmark": {}}


def strategy_studio_versions(spec_id: str, limit: int = 20) -> list[dict[str, Any]]:
    try:
        return strategy_studio.list_strategy_versions(spec_id, limit=limit)
    except Exception:
        return []


def institutional(ticker: str) -> dict:
    """선택 종목 기관·수급 데이터 — 매집 강도(공통) + 시장별 분기.

    美: 13F 기관 지분(SEC). 국내(.KS/.KQ): 13F 대신 Naver 외인·기관 수급
    (외인/기관 순매수 5일·스마트머니 20일·외인보유율·외인 연속매수일) — 13F/Form4 는
    구조적으로 한국 종목엔 적용되지 않아 시도조차 안 함(2026-07-29, 국내주 분석 보강).
    """
    from reports import institutional_flow
    is_kr = str(ticker or "").upper().endswith((".KS", ".KQ"))
    out: dict = {"ticker": ticker, "is_kr": is_kr}
    if is_kr:
        try:
            from providers import kr_market_data, naver_kr
            out["kr_flow"] = naver_kr.investor_flow_features(kr_market_data.norm_code(ticker))
        except Exception as e:
            out["error_kr_flow"] = str(e)
    else:
        try:
            out["inst13f"] = institutional_flow.fetch_13f(ticker)
        except Exception as e:
            out["error_13f"] = str(e)
    try:
        ranked = institutional_flow.rank_accumulation([ticker], enrich_top=1)
        out["accum"] = ranked[0] if ranked else None
    except Exception as e:
        out["error_accum"] = str(e)
    return out


def news_digest(ticker: str, hours: int = 72, limit: int = 10) -> str:
    """최근 뉴스 다이제스트 (티커 관련 우선, 없으면 전체)."""
    from reports import source_collector
    try:
        ev = source_collector.load_recent_events(hours=hours)
        if ticker:
            rel = [e for e in ev
                   if ticker in (e.get("symbols") or e.get("tickers") or [])]
            ev = rel or ev
        return source_collector.build_digest(ev, limit=limit)
    except Exception as e:
        return f"뉴스 로드 실패: {e}"


def earnings_calendar(ticker: str) -> dict:
    """실적 서프라이즈 이력 (종목별)."""
    from providers import earnings_data
    try:
        return {"history": earnings_data.earnings_history(ticker, limit=6)}
    except Exception as e:
        return {"error": str(e)}


def intrinsic_value(ticker: str) -> dict:
    """DDM·RIM 내재가치 밴드 (QT2)."""
    from providers import intrinsic
    try:
        return intrinsic.intrinsic(ticker)
    except Exception as e:
        return {"error": str(e)}


def econ_events(days: int = 14) -> list[dict]:
    """경제 일정 (saveticker /calendar/events, QT2)."""
    from providers import econ_calendar
    try:
        return econ_calendar.upcoming_events(days=days)
    except Exception:
        return []


def insider_trades(ticker: str) -> dict:
    """내부자거래 (SEC Form 4, 美·키불요) — QT2b."""
    from providers import insider
    try:
        return insider.recent_insider(ticker)
    except Exception as e:
        return {"error": str(e), "transactions": []}


def disclosures(ticker: str) -> dict:
    """공시 — 美: SEC filings · 韓(.KS): DART. 키 없으면 graceful. QT2b."""
    from providers import dart, insider
    if dart.stock_code(ticker):
        d = dart.recent_disclosures(ticker)
        return {"market": "KR", **d}
    f = insider.recent_filings(ticker)
    return {"market": "US", "list": f.get("filings", []), "error": f.get("error")}


def screener(top_n: int = 20) -> dict:
    """NASDAQ100 LightGBM 랭킹 스크리너 (무엣지·정보용). QT3."""
    from ml.ranker import load_ranker, rank_today
    try:
        import ticker_names
        from dashboard.data import screener_drivers
        df = rank_today(mode="nasdaq100", top_n=top_n)
        res = load_ranker()
        raw = df.to_dict("records") if (df is not None and not df.empty) else []
        imp = {}
        if res is not None and getattr(res, "feature_importance", None) is not None:
            try:
                imp = res.feature_importance.to_dict()
            except Exception:
                imp = {}
        core = ("rank", "ticker", "score", "price", "tech_rating", "surv_flag")
        rows, feats = [], {}
        for r in raw:
            t = r.get("ticker", "")
            f = {k: v for k, v in r.items() if k not in core}
            feats[t] = f
            rows.append({
                "rank": r.get("rank"), "ticker": t,
                "name": ticker_names.display_name(t, allow_net=False) or "",
                "score": r.get("score"), "price": r.get("price"),
                "tech_rating": r.get("tech_rating"), "surv_flag": r.get("surv_flag"),
                "reason": screener_drivers(f, imp),
                "rsi_14": f.get("rsi_14"), "close_vs_52w_high": f.get("close_vs_52w_high"),
                "mom_126d": f.get("mom_126d"), "excess_mom_60d": f.get("excess_mom_60d"),
                "fund_score": f.get("fund_score"),
            })
        meta = {}
        if res is not None:
            meta = {"ic": getattr(res, "oos_ic", None), "icir": getattr(res, "oos_icir", None),
                    "top_decile": getattr(res, "oos_top_decile_ret", None),
                    "train_end": getattr(res, "train_end_date", None),
                    "importance": dict(sorted(imp.items(), key=lambda x: -x[1])[:15])}
        out = {"rows": rows, "feats": feats, "meta": meta}
        if rows:                                      # 마지막 실행 디스크 영속 (재방문 즉시 표시)
            try:
                import json
                from datetime import datetime
                try:                                  # 직전 실행 순위 → 변동(▲▼NEW) 표시용
                    prev = json.loads(_screener_last_path().read_text())
                    out["prev_ranks"] = {r0.get("ticker"): r0.get("rank")
                                         for r0 in (prev.get("rows") or [])}
                    out["prev_asof"] = prev.get("asof")
                except Exception:
                    pass
                out2 = dict(out)
                out2["asof"] = datetime.now().strftime("%Y-%m-%d %H:%M KST")
                out2["topn"] = top_n
                pth = _screener_last_path()
                pth.parent.mkdir(parents=True, exist_ok=True)
                tmp = pth.with_suffix(".tmp")
                tmp.write_text(json.dumps(out2, ensure_ascii=False))
                tmp.replace(pth)
            except Exception:
                pass
        return out
    except Exception as e:
        return {"error": str(e), "rows": [], "feats": {}, "meta": {}}


def backtest_summary() -> dict:
    """ML 전략 백테스트 (QQQ 3년 실데이터) + 채택 판정. QT3."""
    from ml.data_pipeline import build_real_sweetspot_data
    from ml.reporting import _ml_adoption_verdict
    from ml.sweet_spot import optimize_sweet_spot

    def _m(x):
        return {"cagr": getattr(x, "cagr", None), "sharpe": getattr(x, "sharpe", None),
                "mdd": getattr(x, "max_drawdown", None)}

    try:
        data = build_real_sweetspot_data("QQQ", days=756)
        r = optimize_sweet_spot(data)
        verdict, reasons = _ml_adoption_verdict(r.ml_result, r.qqq_result)
        out = {"ml": _m(r.ml_result), "overlay": _m(r.overlay_result), "qqq": _m(r.qqq_result),
               "verdict": verdict, "reasons": list(reasons or []),
               "equity": getattr(r, "equity", None), "wf": getattr(r, "wf_summary", {})}
        _backtest_persist(out)                       # 마지막 실행 디스크 영속 (재방문 즉시 표시)
        return out
    except Exception as e:
        return {"error": str(e)}


# ── 모의 페이퍼트레이딩 (자동 모의투자 페이지) ─────────────────────────────────

_PAPER = {  # surface → (히스토리 컬렉션, 벤치마크 심볼·이름, 통화, 시드 env·기본)
    "kr_mock": ("kr_mock_history", "^KS11", "KOSPI", "₩", "KIWOOM_MOCK_SEED", 10_000_000.0),
    "us_mock": ("us_mock_history", "QQQ", "QQQ", "$", "KOREA_MOCK_SEED", 100_000.0),
}


def join_decisions(decisions: list[dict], outcomes: list[dict]) -> list[dict]:
    """결정 원장 ⋈ 결과 원장 (decision_id) → 표시행. 최신 날짜 우선. 순수.

    각 행: {date, side, ticker, name?, qty, price, policy_score, reason, ok,
            fwd_excess?, correct?, matured_at?}. 결과 미성숙 결정도 포함(fwd_excess=None).
    """
    by_id = {o.get("decision_id"): o for o in (outcomes or []) if o.get("decision_id")}
    rows = []
    for d in (decisions or []):
        o = by_id.get(d.get("id")) or {}
        correct = o.get("correct")
        if correct is None:                     # KR 결과는 success 만 기록 (kr_mock_learn)
            correct = o.get("success")
        rows.append({
            "date": d.get("date", ""), "side": d.get("side", ""),
            "ticker": d.get("ticker") or d.get("code") or "",
            "qty": d.get("qty"), "price": d.get("price"),
            "base_score": d.get("base_score"),
            "policy_score": d.get("policy_score"),
            "selection_score": d.get("selection_score"),
            "momentum_score": d.get("momentum_score"),
            "momentum_tilt": d.get("momentum_tilt"),
            "momentum_multiplier": d.get("momentum_multiplier"),
            "momentum_state": d.get("momentum_state"),
            "overlay_active": d.get("overlay_active"),
            "regime": d.get("regime"),
            "market": d.get("market"),
            "reason_codes": d.get("reason_codes") or d.get("momentum_reason_codes"),
            "reason": (d.get("rationale") or {}).get("one_line_reason", ""),
            "ok": d.get("ok"),
            "features": d.get("features") or {},   # 새 축(mom12·hi52·lowvol·pead·news) 가시화용
            "fwd_excess": o.get("fwd_excess"), "correct": correct,
            "matured_at": o.get("matured_at"),
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def paper_scorecard(rows: list[dict]) -> dict:
    """조인행 → 편입/퇴출 적중률 (correct/success 판정분만). 순수.

    IC·누적엣지는 evolution.snapshot(learning_evolution)이 단일 소스 — 여기선 퇴출 보완만.
    """
    def hit(rs):
        judged = [r for r in rs if r.get("correct") is not None]
        return (round(sum(1 for r in judged if r["correct"]) / len(judged) * 100.0, 1),
                len(judged)) if judged else (None, 0)

    buy_hit, n_buy = hit([r for r in rows if r.get("side") in ("편입", "증액")])
    sell_hit, n_sell = hit([r for r in rows if r.get("side") in ("퇴출", "감액")])
    return {"buy_hit": buy_hit, "n_buy": n_buy, "sell_hit": sell_hit, "n_sell": n_sell}


def _paper_position_key(row: dict) -> str:
    raw = str(row.get("code") or row.get("ticker") or row.get("symbol") or "").strip()
    return raw.removesuffix(".KS").removesuffix(".KQ").upper()


def _paper_position_ticker(code: str, currency: str) -> str:
    if currency == "₩" and code.isdigit():
        return f"{code}.KS"
    return code


def _paper_position_name(code: str, currency: str, name: str | None = None) -> str:
    base = (name or "").strip()
    if base and base != code:
        return base
    ticker = _paper_position_ticker(code, currency)
    try:
        import ticker_names
        return ticker_names.display_name(ticker, allow_net=False) \
            or ticker_names.display_name(code, allow_net=False) \
            or ticker
    except Exception:
        return ticker


def _reconstruct_positions_from_history(hist: list[dict], decisions: list[dict], currency: str) -> list[dict]:
    """잔고 API 실패 시 성공 주문 로그로 보유 내역을 복원한다.

    과거 스냅샷은 보유 종목 수만 저장하므로, 수량은 order append-log에서 재구성하고
    가격은 point-in-time 결정 원장의 최신/동일일 가격을 사용한다.
    """
    by_key: dict[str, list[dict]] = {}
    for row in decisions or []:
        key = _paper_position_key(row)
        if key:
            by_key.setdefault(key, []).append(row)

    def price_for(code: str, date: str) -> float | None:
        rows = by_key.get(code, [])
        same_day = [r for r in rows if str(r.get("date") or "")[:10] == str(date or "")[:10]]
        candidates = same_day or rows
        for row in candidates:
            try:
                px = float(row.get("price") or 0)
            except (TypeError, ValueError):
                px = 0.0
            if px > 0:
                return px
        return None

    positions: dict[str, dict] = {}
    for row in sorted(hist or [], key=lambda r: str(r.get("date") or "")):
        if row.get("kind") != "order" or row.get("ok") is not True:
            continue
        code = _paper_position_key(row)
        if not code:
            continue
        try:
            qty = int(row.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        pos = positions.setdefault(code, {"shares": 0, "cost": 0.0})
        side = str(row.get("side") or "").lower()
        px = price_for(code, str(row.get("date") or ""))
        if side == "buy":
            pos["shares"] += qty
            if px:
                pos["cost"] += px * qty
        elif side == "sell":
            if pos["shares"] <= 0:
                continue
            avg = pos["cost"] / pos["shares"] if pos["shares"] and pos["cost"] else 0.0
            sell_qty = min(qty, int(pos["shares"]))
            pos["shares"] -= sell_qty
            pos["cost"] = max(0.0, pos["cost"] - avg * sell_qty)

    out = []
    for code, pos in positions.items():
        shares = int(pos.get("shares") or 0)
        if shares <= 0:
            continue
        latest = (by_key.get(code) or [{}])[0]
        cur_price = price_for(code, str(latest.get("date") or "")) or (
            pos["cost"] / shares if pos.get("cost") else 0.0
        )
        avg = pos["cost"] / shares if shares and pos.get("cost") else cur_price
        name = _paper_position_name(code, currency, latest.get("name"))
        value = cur_price * shares
        ret = ((cur_price / avg - 1.0) * 100.0) if avg else None
        out.append({"symbol": code, "name": name, "shares": shares, "avg": avg,
                    "cur": cur_price, "value": value, "ret": ret})
    out.sort(key=lambda r: -(r.get("value") or 0))
    return out


def paper_summary(surface: str = "kr_mock") -> dict:
    """자동 모의투자 계좌 요약 — NAV 시계열·벤치마크·MDD·보유·비용·결정 원장. read-only·graceful.

    잔고 API(모의 도메인) 실패/비활성 시 마지막 EOD 스냅샷 NAV 로 폴백(balance_ok=False).
    표시 전용 — 주문 경로 0. 크론 리포트(kiwoom_mock_report·us_mock_report)와 동일 데이터원.
    """
    hist_name, bench_sym, bench_name, cur, seed_env, seed_def = _PAPER.get(surface, _PAPER["kr_mock"])
    seed = float(os.getenv(seed_env, str(seed_def)))
    out: dict = {"surface": surface, "currency": cur, "bench_name": bench_name,
                 "balance_ok": False, "nav": None, "cash": None, "positions": [],
                 "nav_series": [], "inception_date": None, "cum_ret": None, "day_ret": None,
                 "strat_mdd": None, "bench_ret": None, "bench_mdd": None,
                 "generation_count": 1, "generation": 1,
                 "lifetime_inception_date": None, "lifetime_cum_ret": None,
                 "lifetime_strat_mdd": None, "lifetime_bench_ret": None,
                 "lifetime_bench_mdd": None, "lifetime_nav_series": [],
                 "cost": None, "scorecard": {}, "decisions": [], "order_history": []}

    # 1) EOD NAV 스냅샷 시계열 (store — 오프라인에서도 가용)
    snaps: list[dict] = []
    try:
        import store
        hist = store.all(hist_name)
        out["order_history"] = [
            r for r in sorted(
                [row for row in hist if row.get("kind") == "order"],
                key=lambda row: str(row.get("date") or ""),
                reverse=True,
            )
        ][:50]
    except Exception:
        hist = []

    try:
        from lib import mock_generations
        snaps = mock_generations.active_snapshots(hist_name)
    except Exception:
        snaps = [r for r in hist if r.get("kind") == "snapshot" and r.get("nav") is not None]
    out["nav_series"] = [{"date": str(r.get("date", ""))[:10], "nav": float(r["nav"])} for r in snaps]

    # 2) 라이브 잔고 (모의 API — 비활성/장애 시 마지막 스냅샷 폴백)
    nav = None
    try:
        mock = __import__("kiwoom_mock" if surface == "kr_mock" else "kis_mock")
        bal = mock.get_balance()
        if bal.get("ok"):
            out["balance_ok"] = True
            nav = bal.get("nav") or ((bal.get("pos_value") or 0.0)
                                     + (bal.get("cash_krw" if surface == "kr_mock" else "cash_usd") or 0.0))
            out["cash"] = bal.get("cash_krw" if surface == "kr_mock" else "cash_usd")
            if surface == "us_mock":
                # 통화 구성 분해 — KIS 모의는 통합증거금(USD 예수금 0·원화가 증거금)이라
                # NAV=원화총자산 환산·'현금'=파생값. 표시 레이어가 정직하게 라벨링(달러/원화 혼동 방지).
                out["fx"] = bal.get("fx")
                out["krw_asset"] = bal.get("krw_asset")
                out["usd_deposit"] = bal.get("usd_deposit")
                out["cash_derived"] = bool(bal.get("cash_derived"))
            for sym, p in (bal.get("positions") or {}).items():
                sh = int(p.get("shares", 0) or 0)
                if sh <= 0:
                    continue
                avg = p.get("avg_price", 0) or 0
                curp = p.get("cur_price", 0) or 0
                ret = p.get("return_pct")
                if ret is None:
                    ret = (curp - avg) / avg * 100.0 if avg > 0 else 0.0
                out["positions"].append({"symbol": sym, "name": _paper_position_name(sym, cur, p.get("name")),
                                         "shares": sh, "avg": avg, "cur": curp,
                                         "value": p.get("value", 0) or 0, "ret": ret})
            out["positions"].sort(key=lambda r: -(r["value"] or 0))
    except Exception:
        pass
    if nav is None and snaps:                    # 폴백: 마지막 EOD 스냅샷
        nav = float(snaps[-1]["nav"])
        out["cash"] = snaps[-1].get("cash")
    out["nav"] = nav

    # 3) 성과 — 누적·전일·전략 MDD (크론 리포트와 동일 산식)
    if nav is not None:
        inception_nav = float(snaps[0]["nav"]) if snaps else seed
        out["inception_date"] = str(snaps[0]["date"])[:10] if snaps else None
        try:
            from ml.adaptive import reward as _reward
            out["strat_mdd"] = _reward.max_drawdown([float(s["nav"]) for s in snaps] + [float(nav)]) * 100.0
        except Exception:
            pass
        out["cum_ret"] = (nav / inception_nav - 1.0) * 100.0 if inception_nav else None
        if len(snaps) >= 2:
            prev_nav = float(snaps[-2]["nav"])
            out["day_ret"] = (nav / prev_nav - 1.0) * 100.0 if prev_nav else None

    # 3a) 세대 롤업 — 현재 세대 + 전체 누적 곡선(리셋 구간 stitch) 분리 계산
    try:
        from lib import mock_generations
        roll = mock_generations.generation_rollup(hist_name)
        out["generation_count"] = roll.get("generation_count") or 1
        out["generation"] = out["generation_count"]
        current_roll = roll.get("current") or {}
        lifetime_roll = roll.get("lifetime") or {}
        out["lifetime_inception_date"] = lifetime_roll.get("start_date")
        out["lifetime_cum_ret"] = lifetime_roll.get("cum_return_pct")
        out["lifetime_strat_mdd"] = lifetime_roll.get("mdd_pct")
        out["lifetime_nav_series"] = [
            {"date": str(r.get("date", ""))[:10], "nav": float(r["nav"])}
            for r in (lifetime_roll.get("nav_series") or [])
        ]
        if lifetime_roll.get("start_date"):
            try:
                from providers import market_data
                bm_life = market_data.fetch_kospi_stats(
                    lifetime_roll["start_date"], symbol=bench_sym)
                out["lifetime_bench_ret"] = bm_life.get("return_pct")
                out["lifetime_bench_mdd"] = (
                    bm_life["mdd"] * 100.0 if bm_life.get("mdd") is not None else None
                )
            except Exception:
                pass
        # 현재 세대 스냅샷은 이미 상단에서 active_snapshots() 기준으로 반영된다.
    except Exception:
        pass

    # 3b) 🏗️ Tier3 구조레버 슬리브 상태 (US 모의 — 게이트·목표 vs 보유 가시화)
    if surface == "us_mock":
        try:
            from crons.us_mock_track import (LEV_SLEEVE_ENABLED, LEV_SLEEVE_SYMBOL,
                                             load_lev_shadow)
            lev_pos = next((p for p in out["positions"] if p["symbol"] == LEV_SLEEVE_SYMBOL), None)
            if LEV_SLEEVE_ENABLED or lev_pos:
                out["sleeve"] = {
                    "enabled": LEV_SLEEVE_ENABLED, "symbol": LEV_SLEEVE_SYMBOL,
                    "reco": load_lev_shadow(),
                    "shares": (lev_pos or {}).get("shares", 0),
                    "frac": ((lev_pos or {}).get("value", 0) / nav * 100.0) if nav else 0.0}
        except Exception:
            pass

    # 4) 벤치마크 (인셉션~오늘 — 네트워크·graceful)
    try:
        from providers import market_data
        bm = market_data.fetch_kospi_stats(out["inception_date"], symbol=bench_sym)
        out["bench_ret"] = bm.get("return_pct")
        out["bench_mdd"] = bm["mdd"] * 100.0 if bm.get("mdd") is not None else None
    except Exception:
        pass

    # 5) 거래비용 계기 (누적 수수료·세금 → 회전율·드래그)
    try:
        crows = [r for r in hist if r.get("kind") == "cost"]
        tot_cost = sum(float(r.get("cost", 0) or 0) for r in crows)
        tot_notional = sum(float(r.get("notional", 0) or 0) for r in crows)
        if tot_cost > 0:
            inception_nav = float(snaps[0]["nav"]) if snaps else seed
            avg_nav = (sum(float(s["nav"]) for s in snaps) / len(snaps)) if snaps else inception_nav
            out["cost"] = {"total": tot_cost,
                           "turnover": (tot_notional / avg_nav * 100.0) if avg_nav else 0.0,
                           "drag": (tot_cost / inception_nav * 100.0) if inception_nav else 0.0}
    except Exception:
        pass

    # 6) 결정 원장 ⋈ 결과 (판단 근거 — append-only ledger read-only)
    try:
        from ml.adaptive import Ledger
        led = Ledger(surface)
        rows = join_decisions(led.read_decisions(), led.read_outcomes())
        try:
            import ticker_names
            for r in rows:
                r["name"] = ticker_names.display_name(r["ticker"], allow_net=False) or r["ticker"]
        except Exception:
            for r in rows:
                r["name"] = r["ticker"]
        out["decisions"] = rows
        out["scorecard"] = paper_scorecard(rows)
        if not out["positions"]:
            out["positions"] = _reconstruct_positions_from_history(hist, rows, cur)
    except Exception:
        pass
    return out


def paper_glance() -> list[dict]:
    """사이드바용 모의 계좌 초경량 요약 — store EOD 스냅샷만 읽음 (잔고 API·벤치·원장 X).

    전 페이지 사이드바에서 매 rerun 호출되므로 네트워크 0·로컬 DB 만. 스냅샷 없는
    surface 는 제외(크론 미실행 환경 → 빈 리스트 = 레일 숨김). graceful.
    [{surface, label, currency, nav, cum_ret, day_ret, n_days}]
    """
    specs = (("kr_mock", "🇰🇷 국내", "₩", "kr_mock_history"),
             ("us_mock", "🇺🇸 미국", "$", "us_mock_history"))
    out = []
    for surface, label, cur, hist_name in specs:
        try:
            from lib import mock_generations
            roll = mock_generations.generation_rollup(hist_name)
            current = roll.get("current") or {}
            lifetime = roll.get("lifetime") or {}
            snaps = current.get("nav_series") or []
            if not snaps:
                continue
            nav = float(current.get("nav")) if current.get("nav") is not None else float(snaps[-1])
            out.append({
                "surface": surface,
                "label": label,
                "currency": cur,
                "nav": nav,
                "cum_ret": lifetime.get("cum_return_pct") if lifetime.get("cum_return_pct") is not None
                else current.get("cum_return_pct"),
                "current_cum_ret": current.get("cum_return_pct"),
                "day_ret": current.get("day_return_pct"),
                "n_days": current.get("n_snapshots") or len(snaps),
                "generation": roll.get("generation_count") or 1,
                "lifetime_n_days": lifetime.get("n_snapshots") or len(snaps),
                "lifetime_inception_date": lifetime.get("start_date"),
            })
        except Exception:
            continue
    return out


# ── ML 게이트 현황 (가격축 ★게이트·Tier3 구조레버 — 로컬 파일 read-only·graceful) ──

_GATE_FILES = {
    "kr": (os.path.expanduser("~/reports/ml-cache/kr_policy_backtest.json"),
           os.path.expanduser("~/reports/ml-cache/kr_policy_axes_shadow.json"),
           "ADAPTIVE_KR_AXES_ENABLED"),
    "us": (os.path.expanduser("~/reports/ml-cache/us_policy_backtest.json"),
           os.path.expanduser("~/reports/ml-cache/us_policy_axes_shadow.json"),
           "ADAPTIVE_US_AXES_ENABLED"),
}
_TIER3_SHADOW = os.path.expanduser("~/reports/ml-cache/structural_leverage_shadow.json")
_GATE_FRESH_D = 21     # 주간 크론 2회 이상 누락 시 stale (axes_shadow 정합)


def _read_json(path: str):
    import json
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _days_since(date_str: str) -> int | None:
    from datetime import datetime
    try:
        return (datetime.now() - datetime.strptime(str(date_str)[:10], "%Y-%m-%d")).days
    except Exception:
        return None


def axes_gate_summary() -> dict:
    """KR·US 가격축 ★게이트 최근 검증 + shadow 반영 상태 — {kr:{...}, us:{...}}.

    kr/us_axes_eval 이 주간 저장하는 JSON 을 그대로 표시(파일 없으면 available=False —
    크론 미실행 안내). applied = env on ∧ shadow 신선 = load_params 가 실제 반영 중.
    """
    out = {}
    for mk, (bt_path, sh_path, env_key) in _GATE_FILES.items():
        d = _read_json(bt_path)
        entry = {"available": bool(d),
                 "env_on": os.getenv(env_key, "false").lower() == "true"}
        if d:
            entry.update({"asof": d.get("asof"), "period": d.get("period"),
                          "verdict": d.get("verdict") or {},
                          "recommendation": d.get("recommendation"),
                          "chosen_history": d.get("chosen_history") or {},
                          "coverage": d.get("coverage"),
                          "regime_overlay": d.get("regime_overlay"),        # 방어 오버레이(KR)
                          "cost_sensitivity": d.get("cost_sensitivity")})   # 비용 스윕(KR)
        sh = _read_json(sh_path)
        if sh:
            days = _days_since(sh.get("asof", ""))
            fresh = days is not None and days <= _GATE_FRESH_D
            entry["shadow"] = {"asof": sh.get("asof"), "chosen": sh.get("chosen"),
                               "policy_weights": sh.get("policy_weights"),
                               "fresh": fresh, "applied": entry["env_on"] and fresh}
        out[mk] = entry
    return out


def tier3_gate_status() -> dict:
    """Tier3 구조적 레버리지 게이트 shadow 상태 — 포트폴리오/홈 배지용. graceful."""
    d = _read_json(_TIER3_SHADOW)
    out = {"available": bool(d),
           "sleeve_env": os.getenv("US_MOCK_LEV_SLEEVE", "false").lower() == "true"}
    if d:
        at = str((d.get("_meta") or {}).get("at", ""))[:10]
        days = _days_since(at)
        out.update({"reco_lev": d.get("reco_lev"), "verdict": d.get("verdict"),
                    "at": at, "fresh": days is not None and days <= _GATE_FRESH_D})
    return out


def learning_evolution(surface: str = "kr_mock") -> dict:
    """모의 자기개선 진화 — 주간 학습 이력 + 라이브 스냅샷 verdict. read-only·graceful."""
    from ml.adaptive import Ledger, evolution
    try:
        rows = Ledger(surface).training_set()
    except Exception:
        rows = []
    try:
        return evolution.evolution_summary(surface, rows)
    except Exception as e:
        return {"error": str(e), "snapshot": {}, "verdict": {}, "series": [], "adoptions": [], "n_runs": 0}


def realtime_quote(ticker: str) -> dict | None:
    """실시간 시세+호가 (KIS — 캐시 seam 우선·REST 온디맨드 폴백). read-only·graceful None.

    반환 {price, bids, asks, ts, source, market}. REALTIME_ENABLED off/미보유/장애 시 None →
    호출부 yfinance 폴백. 주문 경로 없음(kis_quote 는 read-only).
    """
    t = (ticker or "").strip()
    if not t:
        return None
    tu = t.upper()
    if tu.endswith(".KS") or tu.endswith(".KQ"):
        sym, market = t[:-3], "KR"
    else:
        sym, market = tu, "US"
    price = None
    try:                                    # 1) 캐시 seam (워치리스트 종목 = 즉시)
        from providers import market_data
        price = market_data._realtime_current(t)
    except Exception:
        price = None
    try:                                    # 2) WS 실시간 캐시 호가 (워치리스트 = 1초 갱신·진짜 실시간)
        from providers import realtime_quotes
        ob = realtime_quotes.get_orderbook(sym, max_age_s=15)
        if ob and (ob.get("bids") or ob.get("asks")):
            return {"price": price or realtime_quotes.get_price(sym),
                    "bids": ob.get("bids") or [], "asks": ob.get("asks") or [],
                    "ts": ob.get("ts"), "source": "kis_ws", "market": market}
    except Exception:
        pass
    snap = None
    try:                                    # 3) REST 온디맨드 (임의 티커·호가 포함 — 폴백)
        from providers import kis_quote
        snap = kis_quote.get_snapshot(sym, market=market)
    except Exception:
        snap = None
    if snap and snap.get("price"):
        return {"price": price or snap.get("price"), "bids": snap.get("bids") or [],
                "asks": snap.get("asks") or [], "ts": snap.get("ts"),
                "source": snap.get("source", "kis_rest"), "market": market}
    if price and price > 0:
        return {"price": price, "bids": [], "asks": [], "ts": None, "source": "kis_ws", "market": market}
    return None


_HEATMAP_SNAP = os.path.expanduser("~/reports/ml-cache/sp500_heatmap.json")


def sp500_heatmap() -> list[dict]:
    """S&P500 시장 맵 rows — 크론 JSON 스냅샷(<90분) 우선(즉시) → 없으면 라이브 후 스냅샷 기록(self-heal).

    콜드로드 ~60초(503 배치)를 스냅샷 파일읽기로 즉시화. crons/sp500_heatmap_snapshot.py 가 20분마다 갱신.
    """
    import json
    import time
    try:
        if time.time() - os.stat(_HEATMAP_SNAP).st_mtime < 5400:      # 90분 이내 신선
            with open(_HEATMAP_SNAP, encoding="utf-8") as f:
                rows = json.load(f)
            if rows:
                return rows
    except Exception:
        pass
    rows = _sp500_heatmap_live()
    if rows:
        try:
            from safe_io import atomic_write_json
            atomic_write_json(_HEATMAP_SNAP, rows)
        except Exception:
            pass
    return rows


def _sp500_heatmap_live() -> list[dict]:
    """S&P500 시장 맵 라이브 조립 — [{ticker,name,sector_kr,market_cap,pct}]. 표시·graceful.

    섹터·시총 = 정적 시드(sp500_seed·sp500_meta), 당일 등락% = 라이브 배치(yf.download 2일 종가).
    결측(시총·pct 없음) 스킵. 네트워크/모듈 실패 시 빈 리스트. (크론·스냅샷 미스 시 폴백)
    """
    try:
        import sp500_meta
        import sp500_seed
    except Exception:
        return []
    tickers = list(sp500_seed.SP500)
    sec_map = getattr(sp500_meta, "SECTOR", {})
    cap_map = getattr(sp500_meta, "MARKET_CAP", {})
    kr_map = getattr(sp500_meta, "SECTOR_KR", {})
    pct: dict[str, float] = {}
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        df = yf.download(tickers, period="2d", progress=False, group_by="ticker", threads=True)
        for t in tickers:
            try:
                c = df[t]["Close"].dropna()
                if len(c) >= 2 and c.iloc[-2]:
                    pct[t] = round((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2)
            except Exception:
                pass
    except Exception:
        return []
    rows: list[dict] = []
    for t in tickers:
        cap = cap_map.get(t) or 0
        p = pct.get(t)
        if cap <= 0 or p is None:
            continue
        rows.append({
            "ticker": t, "name": sp500_seed.SP500.get(t) or t,
            "sector_kr": kr_map.get(sec_map.get(t) or "") or "기타",
            "sub": tech_subsector(sec_map.get(t), getattr(sp500_meta, "INDUSTRY", {}).get(t)),
            "market_cap": float(cap), "pct": p})
    return rows


# 기술 섹터 세부 카테고리 — yfinance industry → 한글 버킷 (트리맵 3계층)
_TECH_SUB = {
    "Semiconductors": "반도체", "Semiconductor Equipment & Materials": "반도체",
    "Software - Application": "소프트웨어·클라우드", "Software - Infrastructure": "소프트웨어·클라우드",
    "Information Technology Services": "IT서비스",
    "Computer Hardware": "하드웨어·장비", "Communication Equipment": "하드웨어·장비",
    "Scientific & Technical Instruments": "하드웨어·장비", "Electronic Components": "하드웨어·장비",
    "Consumer Electronics": "하드웨어·장비", "Solar": "하드웨어·장비",
}


def tech_subsector(sector, industry) -> str | None:
    """기술 섹터만 세부 카테고리 반환 (그 외 None — 2계층 유지). 순수."""
    if sector != "Technology" or not industry:
        return None
    return _TECH_SUB.get(industry, "기타 기술")


def market_indicators() -> dict:
    """홈 시장 지표 — 공포·탐욕지수 + S&P500·나스닥 일/주봉 RSI. 표시·graceful.

    반환 {fear_greed:{score,rating,prev_week,prev_month}|None,
          indices:[{ticker,name,price,chg,rsi_d,rsi_w}]}. 네트워크 실패는 None/빈으로 흡수.
    """
    from dashboard import data
    out: dict = {"fear_greed": None, "indices": []}
    try:
        from providers import market_data
        fg = market_data.fetch_fear_greed()
        if isinstance(fg, dict) and fg.get("score") is not None:
            out["fear_greed"] = {"score": float(fg["score"]), "rating": fg.get("rating"),
                                 "prev_week": fg.get("prev_week"), "prev_month": fg.get("prev_month")}
    except Exception:
        pass
    specs = [("^GSPC", "S&P 500"), ("^IXIC", "나스닥")]
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        tks = [s[0] for s in specs]
        hd = yf.download(tks, period="4mo", progress=False, group_by="ticker", threads=True)
        hw = yf.download(tks, period="2y", interval="1wk", progress=False, group_by="ticker", threads=True)
    except Exception:
        return out
    for tk, name in specs:
        row = {"ticker": tk, "name": name, "price": None, "chg": None, "rsi_d": None, "rsi_w": None}
        try:
            cd = hd[tk]["Close"].dropna()
            rd = data.rsi(cd)
            row["rsi_d"] = round(rd, 1) if rd is not None else None
            if len(cd) >= 2 and cd.iloc[-2]:
                row["price"] = float(cd.iloc[-1])
                row["chg"] = round((cd.iloc[-1] / cd.iloc[-2] - 1) * 100, 2)
        except Exception:
            pass
        try:
            rw = data.rsi(hw[tk]["Close"].dropna())
            row["rsi_w"] = round(rw, 1) if rw is not None else None
        except Exception:
            pass
        out["indices"].append(row)
    return out


# ── 매크로 자산 (환율·금·원자재·암호화폐·금리) ────────────────────────────────

# (yf 심볼, 라벨, 이모지, 소수자리) — 단위는 `ticker_names.macro_unit` 단일 소스.
# 카드 클릭은 `?tk=<심볼>` 앵커 → 종목분석(매크로 전용 뷰). 전부 yfinance OHLC 조회 가능.
_MACRO_SPECS = [
    ("KRW=X", "달러/원 환율", "💱", 2),
    ("GC=F", "금", "🥇", 1),
    ("BTC-USD", "비트코인", "₿", 0),
    ("ETH-USD", "이더리움", "Ξ", 0),
    ("SI=F", "은", "🥈", 2),
    ("CL=F", "WTI 유가", "🛢️", 2),
    ("^TNX", "미 10년물 금리", "🏦", 2),
    ("DX-Y.NYB", "달러 인덱스", "💵", 2),
]


def macro_assets() -> list[dict]:
    """홈 매크로 자산 카드 — [{symbol,label,emoji,price,chg,pct,unit,spark,ticker}].

    yfinance 1개월 일봉 배치 1회 → 최신가·전일대비·30일 스파크라인. 개별 심볼 실패는
    조용히 스킵(부분 성공 허용), 전체 실패는 []. 표시·참고용(주문 경로 없음).
    """
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        df = yf.download([s[0] for s in _MACRO_SPECS], period="1mo", progress=False,
                         group_by="ticker", threads=True)
    except Exception:
        return []
    import ticker_names
    out = []
    for sym, label, emoji, dec in _MACRO_SPECS:
        try:
            c = df[sym]["Close"].dropna()
            if len(c) < 2 or not float(c.iloc[-2]):
                continue
            last, prev = float(c.iloc[-1]), float(c.iloc[-2])
            out.append({
                "symbol": sym, "label": label, "emoji": emoji,
                "unit": ticker_names.macro_unit(sym), "ticker": sym,
                "price": round(last, dec), "chg": round(last - prev, dec),
                "pct": round((last / prev - 1) * 100, 2),
                "spark": [float(v) for v in c.tail(30).tolist()],
            })
        except Exception:
            continue
    return out


# 매크로 자산별 연관 세트 — (심볼, 라벨, 관계 설명)
_MACRO_REL = {
    "KRW=X": [("DX-Y.NYB", "달러 인덱스", "달러 강세 ↔ 원화 약세"),
              ("^KS11", "코스피", "원화 약세 시 외인 수급 압박"),
              ("^TNX", "미 10년물", "금리차 → 환율 압력")],
    "DX-Y.NYB": [("KRW=X", "달러/원", "달러 강세 ↔ 원화 약세"),
                 ("GC=F", "금", "달러 강세 시 금 압박(역상관 경향)"),
                 ("^TNX", "미 10년물", "미 금리 ↑ → 달러 강세 경향")],
    "GC=F": [("^TNX", "미 10년물", "실질금리 ↑ = 무이자 자산 금 압박(역상관 경향)"),
             ("DX-Y.NYB", "달러 인덱스", "달러 표시 자산 — 역상관 경향"),
             ("SI=F", "은", "귀금속 동행 (금은비 확인)")],
    "SI=F": [("GC=F", "금", "귀금속 동행"), ("DX-Y.NYB", "달러 인덱스", "역상관 경향")],
    "BTC-USD": [("^IXIC", "나스닥", "리스크온 자산 동행 경향"),
                ("GC=F", "금", "'디지털 금' 서사 — 국면 따라 상이"),
                ("DX-Y.NYB", "달러 인덱스", "유동성 국면 역상관 경향")],
    "ETH-USD": [("BTC-USD", "비트코인", "암호화폐 동행(베타 높음)"),
                ("^IXIC", "나스닥", "리스크온 동행 경향")],
    "CL=F": [("DX-Y.NYB", "달러 인덱스", "달러 표시 원자재 — 역상관 경향"),
             ("^GSPC", "S&P500", "경기 수요 프록시")],
    "^TNX": [("^IXIC", "나스닥", "금리 ↑ = 성장주 할인율 압박"),
             ("GC=F", "금", "실질금리 프록시 — 역상관 경향"),
             ("DX-Y.NYB", "달러 인덱스", "금리차 → 달러")],
    "^VIX": [("^GSPC", "S&P500", "공포지수 — 강한 역상관")],
    "^GSPC": [("^IXIC", "나스닥", "미 대형주 동행"), ("^KS11", "코스피", "글로벌 동조")],
    "^IXIC": [("^GSPC", "S&P500", "미 대형주 동행"), ("^TNX", "미 10년물", "금리 민감(성장주)")],
}


def macro_correlations(ticker: str) -> list[dict]:
    """매크로 자산 연관 카드 — 관련 자산들과의 90일 상관 + 30일 등락. graceful [].

    [{symbol, label, note, corr90, chg30}]. yf 6mo 배치 1회. 상관은 일수익률 피어슨.
    """
    rel = _MACRO_REL.get((ticker or "").upper())
    if not rel:
        return []
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        syms = [ticker] + [s for s, _, _ in rel]
        df = yf.download(syms, period="6mo", progress=False, group_by="ticker", threads=True)
        base = df[ticker]["Close"].dropna().pct_change().dropna()
    except Exception:
        return []
    out = []
    for sym, label, note in rel:
        try:
            c = df[sym]["Close"].dropna()
            r = c.pct_change().dropna()
            joined = base.tail(90).align(r.tail(90), join="inner")
            corr = float(joined[0].corr(joined[1])) if len(joined[0]) >= 30 else None
            m30 = c[c.index >= c.index[-1] - __import__("pandas").Timedelta(days=30)]
            chg30 = ((float(c.iloc[-1]) / float(m30.iloc[0]) - 1) * 100) if len(m30) >= 2 else None
            out.append({"symbol": sym, "label": label, "note": note,
                        "corr90": round(corr, 2) if corr == corr and corr is not None else None,
                        "chg30": round(chg30, 2) if chg30 is not None else None})
        except Exception:
            continue
    return out


def llm_related_tickers(ticker: str, force: bool = False):
    """🤖 LLM 연관 종목 추천 — (목록|None, 상태). 환각 방어·24h 디스크 캐시는 provider 가.

    표시 전용(정직 라벨은 UI 가) — graceful: 실패 시 (None, 사유).
    """
    try:
        import ticker_names
        from providers import llm_related
        name = ticker_names.display_name(ticker, allow_net=False) or ticker
        items, status = llm_related.related_tickers(ticker, name=name, force=force)
        if items:
            return items, status
        fallback = llm_related._fallback_related(ticker, name)
        if fallback:
            return fallback, f"fallback ({status})" if status and status != "fallback" else "fallback"
        return items, status
    except Exception as exc:
        try:
            import ticker_names
            from providers import llm_related
            name = ticker_names.display_name(ticker, allow_net=False) or ticker
            fallback = llm_related._fallback_related(ticker, name)
            if fallback:
                return fallback, f"fallback (call failed: {str(exc)[:40]})"
        except Exception:
            pass
        return None, f"call failed: {str(exc)[:80]}"


def ai_briefing() -> dict | None:
    """🌅 포트폴리오 AI 브리핑 — 크론이 저장한 JSON (홈 카드 · 없으면 None graceful)."""
    try:
        from providers.llm_analysis import BRIEF_PATH
        import json as _j
        with open(BRIEF_PATH, encoding="utf-8") as fp:
            d = _j.load(fp)
        return d if isinstance(d, dict) and d.get("summary") else None
    except Exception:
        return None


def llm_stock_analysis(ticker: str, facts: dict, force: bool = False):
    """🤖 LLM 종목 분석 해설 — (dict|None, 상태). 금지어 필터·24h 캐시는 provider 가.

    facts 는 호출부(ticker 페이지)가 **이미 계산된 지표**로 조립 — 여기선 추가 네트워크 0.
    표시 전용(정직 라벨은 UI 가) — graceful: 실패 시 (None, 사유).
    """
    try:
        import ticker_names
        from providers import llm_analysis
        name = ticker_names.display_name(ticker, allow_net=False) or ticker
        ana, status = llm_analysis.analyze(ticker, name, facts or {}, force=force)
        if ana:
            return ana, status
        fallback = llm_analysis._analysis_fallback(ticker, name, facts or {})
        return fallback, f"fallback ({status})" if status and status != "fallback" else "fallback"
    except Exception as exc:
        try:
            import ticker_names
            from providers import llm_analysis
            name = ticker_names.display_name(ticker, allow_net=False) or ticker
            fallback = llm_analysis._analysis_fallback(ticker, name, facts or {})
            return fallback, f"fallback (call failed: {str(exc)[:40]})"
        except Exception:
            pass
        return None, f"call failed: {str(exc)[:80]}"


def chart_news_events(ticker: str, limit: int = 120) -> list[dict]:
    """차트 뉴스 이벤트 마커 — LLM 구조화 라벨(point-in-time JSONL)에서 해당 종목 추출.

    라벨 tickers 는 base 심볼(무 .KS 접미) — 입력 티커도 base 로 매칭. 파일 없음(라벨
    크론 미가동)·실패는 [] (graceful). [{date, direction, strength, event_type, title}].
    """
    try:
        from providers import news_labels
        base = (ticker or "").upper().split(".")[0]
        out = []
        for r in news_labels.load_labels():
            if base not in (r.get("tickers") or []):
                continue
            d = str(r.get("published_at") or "")[:10]
            if not d:
                continue
            out.append({"date": d, "direction": r.get("direction"),
                        "strength": r.get("strength"),
                        "event_type": r.get("event_type") or "",
                        "title": r.get("title_head") or ""})
        return out[-limit:]
    except Exception:
        return []


def chart_fundamentals(ticker: str) -> dict:
    """차트 펀더멘털 서브패널 — 분기(+연간) 매출·순이익·마진 (graceful 빈 dict)."""
    try:
        from providers import earnings_data
        return earnings_data.quarterly_fundamentals(ticker) or {}
    except Exception:
        return {}


# ── 수집 뉴스 (시장·캘린더 — 출처별·중요도순) ─────────────────────────────────

# 출처 표시 순서·라벨 (뉴스성 소스 우선, 수치성 스냅샷 후순위)
NEWS_SOURCE_ORDER = ["saveticker", "telegram", "arca", "fred", "worldgovernmentbonds", "yahoo_finance"]
NEWS_SOURCE_LABEL = {
    "saveticker": "📰 SaveTicker",
    "telegram": "✈️ 텔레그램",
    "arca": "💬 아카라이브",
    "fred": "🏛️ FRED 매크로",
    "worldgovernmentbonds": "🏦 국채금리",
    "yahoo_finance": "📈 시장 스냅샷",
}


def news_source_key(source) -> str:
    """'telegram:yuzukinaok1' → 'telegram' (채널별이 아닌 소스별 그룹)."""
    return (str(source or "기타")).split(":")[0]


def _news_rule_scorer():
    """속보 크론의 규칙 중요도(_rule_score) 재사용 — 단일 진실원. 실패 시 균등 5점."""
    try:
        import sys
        crons = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crons")
        if crons not in sys.path:
            sys.path.insert(0, crons)
        from news_spike_detector import _rule_score
        return _rule_score
    except Exception:
        return lambda e: (5, "")


def group_news(events: list, label_by_id: dict | None = None, score_fn=None) -> dict:
    """수집 이벤트 → 출처별 그룹 + 중요도순 정렬 (순수 — 테스트 가능).

    중요도 = 속보 규칙 점수(포트폴리오 종목 8·핵심 키워드 7·노이즈 3·기본 5).
    LLM 구조화 라벨(news_labels)이 있으면 방향/강도를 병기하고 강도로 하한 보정
    (score ≥ 3+strength — LLM 도 표시·정렬 보조일 뿐 사실 생성 없음).
    반환: {source_key: [{title,url,score,reason,time_str,tickers,llm}...]} — 점수↓·최신↑.
    """
    score_fn = score_fn or (lambda e: (5, ""))
    label_by_id = label_by_id or {}
    groups: dict[str, list[dict]] = {}
    seen = set()
    for e in events or []:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        eid = str(e.get("id") or f"{e.get('source')}|{title}|{e.get('published_at')}")
        if eid in seen:
            continue
        seen.add(eid)
        try:
            score, reason = score_fn(e)[:2]
        except Exception:
            score, reason = 5, ""
        llm = None
        lb = label_by_id.get(eid) or label_by_id.get(str(e.get("id")))
        if lb:
            try:
                d, s = int(lb.get("direction", 0)), int(lb.get("strength", 0))
                llm = {"direction": d, "strength": s, "event_type": lb.get("event_type")}
                score = max(int(score), 3 + s)
            except (TypeError, ValueError):
                pass
        tickers = [str(t).lstrip("$") for t in (e.get("tags") or []) if str(t).startswith("$")]
        pub = str(e.get("published_at") or "")
        groups.setdefault(news_source_key(e.get("source")), []).append({
            "title": title, "url": e.get("url"), "score": int(score), "reason": reason,
            "published_at": pub,
            "time_str": pub[5:16].replace("T", " ") if len(pub) >= 16 else pub,
            "tickers": tickers[:4], "llm": llm,
        })
    for lst in groups.values():
        lst.sort(key=lambda x: x["published_at"], reverse=True)   # 동점 내 최신 우선
        lst.sort(key=lambda x: -x["score"])                       # 1차: 중요도
    return groups


def collected_news(hours: int = 48) -> dict:
    """source-cache 수집 뉴스 → 출처별·중요도순 (+LLM 라벨 방향 병기). graceful."""
    try:
        from reports.source_collector import load_recent_events, event_id
        events = load_recent_events(hours=hours)
        for e in events:
            if not e.get("id"):
                try:
                    e["id"] = event_id(e)
                except Exception:
                    pass
    except Exception as e:
        return {"error": str(e), "groups": {}}
    label_by_id: dict = {}
    try:
        from providers import news_labels
        label_by_id = {str(r.get("id")): r for r in news_labels.load_labels() if r.get("id")}
    except Exception:
        pass
    return {"groups": group_news(events, label_by_id, _news_rule_scorer()), "hours": hours}


def source_health_summary() -> dict:
    """수집 소스 헬스 (source_collector 헬스 파일 read-only) — 대시보드 배너용. graceful."""
    try:
        from reports.source_collector import load_source_health, stale_sources
        health = load_source_health()
        return {"health": health, "stale": stale_sources(health) if health else []}
    except Exception as e:
        return {"health": {}, "stale": [], "error": str(e)}


def wiki_pipeline_health_summary() -> dict:
    """LLM 위키 파이프라인 헬스 (source/wiki/curation 요약) — AI 콘솔용."""
    try:
        from reports.wiki_pipeline_health import build_pipeline_health_report
        return build_pipeline_health_report(dry_run=True)
    except Exception as e:
        return {
            "dry_run": True,
            "generated_at": "",
            "source_health": {"overall": {}, "sources": [], "stale_sources": [], "recent": {}},
            "wiki_health": {},
            "curation_health": {},
            "recommendations": [],
            "error": str(e),
        }


def _institution_analysis(snapshots: list[dict], comparison: dict) -> dict:
    try:
        from reports import institution_watch
        analysis = institution_watch.build_common_moves_analysis(snapshots, comparison)
        if isinstance(analysis, dict):
            return analysis
    except Exception:
        pass
    return {
        "summary": "표시할 기관 데이터가 아직 없습니다." if not snapshots else "기관 데이터를 요약하는 중 문제가 발생했습니다.",
        "shared_moves": [],
        "divergences": [],
        "confidence": 0.0,
        "mode": "heuristic",
    }


def institution_watch_summary(keys=None) -> dict:
    """기관투자자 허브용 비교 스냅샷 (watchlist UI 전용)."""
    try:
        from reports import institution_watch

        registry = institution_watch.list_institutions()
        if keys is not None:
            wanted = {str(key) for key in keys}
            registry = [row for row in registry if row.get("key") in wanted]
        selected_keys = [row.get("key") for row in registry if row.get("key")]
        snapshots: dict[str, dict] = {}
        institutions: list[dict] = []
        for key in selected_keys:
            snapshot = institution_watch.latest_snapshot(key)
            if snapshot is None:
                continue
            snapshots[key] = snapshot
            institutions.append({
                "key": snapshot["institution_key"],
                "display_name": snapshot["display_name"],
                "source_kind": snapshot.get("source_kind", ""),
                "category": snapshot.get("category", ""),
                "freshness": snapshot.get("freshness", ""),
                "holdings_count": snapshot.get("holdings_count", 0),
                "availability_flags": dict(snapshot.get("availability_flags") or {}),
                "top_holdings": list(snapshot.get("top_holdings") or []),
                "notes": list(snapshot.get("notes") or []),
                "primary_sources": list(snapshot.get("primary_sources") or []),
                "metric_capabilities": list(snapshot.get("metric_capabilities") or []),
                "refresh_policy": snapshot.get("refresh_policy", ""),
                "confidence": snapshot.get("confidence"),
            })
        comparison = institution_watch.compare_institutions(selected_keys, snapshots=snapshots)
        analysis = _institution_analysis(list(snapshots.values()), comparison)
        return {
            "institutions": institutions,
            "comparison": comparison,
            "analysis": analysis,
            "selected_keys": comparison.get("selected_keys") or [],
        }
    except Exception as e:
        return {
            "institutions": [],
            "comparison": {"rows": [], "selected_keys": []},
            "analysis": {
                "summary": "기관 허브 데이터를 불러오지 못했습니다.",
                "shared_moves": [],
                "divergences": [],
                "confidence": 0.0,
            },
            "selected_keys": [],
            "error": str(e),
        }


def etf_overview(ticker: str) -> dict:
    """ETF 전용 요약 (providers.etf_data) — 비ETF {"is_etf": False}. graceful."""
    try:
        from providers import etf_data
        return etf_data.etf_summary(ticker)
    except Exception as e:
        # 판정 실패 시에도 알려진 ETF 는 ETF 레이아웃 유지(주식 뷰 오표시 방지)
        try:
            from providers.etf_data import is_etf
            return {"ticker": ticker, "is_etf": is_etf(ticker), "error": str(e)}
        except Exception:
            return {"ticker": ticker, "is_etf": False, "error": str(e)}


def social_sentiment(hours: int = 72) -> dict:
    """레딧/WSB 심리 카드 — insidertracking 분석 포스트 구조화 (표시·컨텍스트 전용).

    판단 반영은 news_labels → news 축(게이트) 단일 경로 — 이 카드는 신호 아님.
    """
    try:
        from reports.source_collector import load_recent_events
        from reports.social_sentiment import sentiment_summary
        events = [e for e in load_recent_events(hours=hours)
                  if str(e.get("source", "")).startswith("telegram:")]
        return {"summary": sentiment_summary(events)}
    except Exception as e:
        return {"summary": None, "error": str(e)}


# ── 단기(1분봉) 모의 트레이딩 (표시 전용·read-only) ──────────────────────────

def intraday_overview(market: str) -> dict:
    """단기 슬리브 개요 — 요약 KPI(state+원장) + bar 날짜 목록. 미사용 시 데이터 없음."""
    out: dict = {"market": market}
    try:
        from lib.intraday_status import intraday_summary
        out["summary"] = intraday_summary(market)
    except Exception as e:
        out["summary"], out["summary_error"] = None, str(e)
    try:
        from providers import intraday_bars
        out["dates"] = intraday_bars.available_dates()[-30:]
    except Exception as e:
        out["dates"], out["dates_error"] = [], str(e)
    return out


def intraday_day(market: str, date: str) -> dict:
    """그날 단기 트레이드 원장(결정⋈결과) + 심볼 목록 (트레이드 심볼 우선)."""
    mk = market.upper()
    out: dict = {"rows": [], "symbols": []}
    try:
        from ml.adaptive import Ledger
        led = Ledger(f"{mk.lower()}_intraday")
        outs = {o["decision_id"]: o for o in led.read_outcomes() if o.get("decision_id")}
        out["rows"] = [{**d, **(outs.get(d["id"]) or {})}
                       for d in led.read_decisions() if d.get("date") == date]
        out["symbols"] = list(dict.fromkeys(r.get("ticker") for r in out["rows"] if r.get("ticker")))
    except Exception as e:
        out["error"] = str(e)
    if not out["symbols"]:
        try:
            from providers import intraday_bars
            out["symbols"] = intraday_bars.list_symbols(date, market=mk)[:10]
        except Exception:
            pass
    return out


def intraday_chart(symbol: str, market: str, date: str, interval: str = "1m") -> dict:
    """분봉(자체 bar store 우선·yfinance 폴백) + VWAP·OR 박스 + 그날 트레이드 마커."""
    mk = market.upper()
    out: dict = {"symbol": symbol, "src": "none"}
    try:
        from providers import intraday_bars
        df, src = intraday_bars.load_bars_with_fallback(symbol, mk, date, interval=interval)
        out["bars"], out["src"] = df, src
        if df is not None and not df.empty:
            typ = (df["High"] + df["Low"] + df["Close"]) / 3
            cv = df["Volume"].cumsum().replace(0, float("nan"))
            out["vwap"] = list((typ * df["Volume"]).cumsum() / cv)
            out["bar_count"] = len(df)
            out["bar_first"] = df.index[0].isoformat() if len(df.index) else None
            out["bar_last"] = df.index[-1].isoformat() if len(df.index) else None
            try:
                out["session_count"] = len({str(ts.date()) for ts in df.index})
            except Exception:
                out["session_count"] = None
            open_min = 9 * 60 if mk == "KR" else 9 * 60 + 30    # OR 은 세션 개장분만
            fmin = df.index[0].hour * 60 + df.index[0].minute
            if fmin <= open_min + 1 and len(df) >= 15:
                head = df.iloc[:15]
                out["or_range"] = (float(head["High"].max()), float(head["Low"].min()),
                                   df.index[min(14, len(df) - 1)])
    except Exception as e:
        out["bars_error"] = str(e)
    try:
        from lib import trade_events
        out["trades"] = [t for t in trade_events.trades_for_ticker(symbol)
                         if t.get("source") == "intraday_mock"
                         and str(t.get("timestamp", ""))[:10] == date]
    except Exception as e:
        out["trades"], out["trades_error"] = [], str(e)
    return out


def world_timeline(ticker: str, limit: int = 10) -> dict:
    """월드 메모리 축적 맥락 — 종목 관련 이슈 타임라인 + 스토리 체인 (read-only). graceful."""
    try:
        from lib.world_memory import stats, story_chain, timeline
        base = str(ticker or "").split(".")[0].upper()
        return {"issues": timeline(base, limit=limit),
                "chain": story_chain(f"ticker:{base}", limit=8),
                "stats": stats()}
    except Exception as e:
        return {"issues": [], "chain": [], "error": str(e)}


def _ohlc_cache_candidates(tf: str, *, include_base: bool = False) -> tuple[str, ...]:
    tf = str(tf or "1d").lower().strip() or "1d"
    if tf == "1d":
        return ("1d", "max") if include_base else ("1d",)
    if tf in ("1wk", "1mo"):
        return (tf, "max", "1d") if include_base else (tf,)
    return (tf,)


_TF_MEDIAN_DELTA_BOUNDS = {
    "1d": (timedelta(hours=18), timedelta(days=4)),
    "1wk": (timedelta(days=4), timedelta(days=10)),
    "1mo": (timedelta(days=24), timedelta(days=40)),
    "5m": (timedelta(minutes=2), timedelta(minutes=15)),
    "1h": (timedelta(minutes=30), timedelta(hours=3)),
    "2h": (timedelta(minutes=75), timedelta(hours=5)),
    "4h": (timedelta(hours=2), timedelta(hours=9)),
}

_TF_EXPECTED_DELTA = {
    "1d": timedelta(days=1),
    "1wk": timedelta(days=7),
    "1mo": timedelta(days=30),
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
}


def _ohlc_median_delta(hist):
    import pandas as pd

    try:
        idx = pd.DatetimeIndex(hist.index).dropna().sort_values()
    except Exception:
        return None
    if len(idx) < 2:
        return None
    try:
        return idx.to_series().diff().median()
    except Exception:
        return None


def _ohlc_tf_matches(hist, tf: str) -> bool:
    tf = str(tf or "1d").lower().strip() or "1d"
    bounds = _TF_MEDIAN_DELTA_BOUNDS.get(tf)
    if bounds is None:
        return True
    med = _ohlc_median_delta(hist)
    if med is None:
        # 짧은 구간(예: 1개월 미만)에서 주·월봉은 봉 1개만 남을 수 있다.
        # 이 경우까지 거절하면 정상 리샘플을 잘못 버리게 되므로, 장기 주기만
        # 최소 1개 이상이면 허용하고 인트라데이는 최소 간격 확인을 유지한다.
        return tf in {"1d", "1wk", "1mo"} and len(getattr(hist, "index", [])) > 0
    lo, hi = bounds
    return lo <= med <= hi


def _detected_ohlc_timeframe(hist) -> str | None:
    """Identify a loaded frame's cadence for explicit mismatch metadata."""
    med = _ohlc_median_delta(hist)
    if med is None:
        return None
    candidates = [
        timeframe
        for timeframe, (lower, upper) in _TF_MEDIAN_DELTA_BOUNDS.items()
        if lower <= med <= upper
    ]
    if not candidates:
        return None

    def cadence_ratio(timeframe: str) -> float:
        expected = _TF_EXPECTED_DELTA[timeframe]
        ratio = med / expected
        return max(float(ratio), 1.0 / float(ratio))

    return min(candidates, key=cadence_ratio)


def _load_cached_ohlc_tf(ticker: str, tf: str, *, include_base: bool = False):
    """디스크 OHLC 캐시를 먼저 읽는다."""
    try:
        from providers import market_data
    except Exception:
        return None
    for period in _ohlc_cache_candidates(tf, include_base=include_base):
        try:
            hist = normalize_ohlc_frame(market_data.load_cached_ohlc(ticker, period))
        except Exception:
            hist = None
        if hist is not None and not getattr(hist, "empty", True) and _ohlc_tf_matches(hist, tf):
            return hist
    return None


def _save_cached_ohlc_tf(ticker: str, period: str, hist) -> None:
    try:
        from providers import market_data
        market_data.save_cached_ohlc(ticker, period, hist)
    except Exception:
        pass


def _resample_ohlc_frame(hist, rule: str, *, market: str | None = None,
                         session_aligned: bool = False):
    import pandas as pd

    hist = normalize_ohlc_frame(hist)
    if hist is None or getattr(hist, "empty", True):
        return hist
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in getattr(hist, "columns", []):
        agg["Volume"] = "sum"
    kwargs = {}
    if session_aligned:
        offset_min = 9 * 60 if market == "KR" else 9 * 60 + 30
        kwargs["origin"] = "start_day"
        kwargs["offset"] = pd.Timedelta(minutes=offset_min)
    try:
        out = hist.resample(rule, **kwargs).agg(agg).dropna(subset=["Open"])
    except Exception:
        return hist
    return normalize_ohlc_frame(out)


def _load_intraday_store_tf(ticker: str, tf: str):
    """로컬 1m bar store 를 이어 붙여 5m/1h/2h/4h 를 만든다."""
    import pandas as pd

    try:
        from providers import intraday_bars
    except Exception:
        return None

    dates = intraday_bars.available_dates()
    if not dates:
        return None

    session_limits = {"5m": 90, "1h": 180, "2h": 180, "4h": 180}
    limit = session_limits.get(tf, 90)
    frames = []
    for date in dates[-limit:]:
        frame = intraday_bars.load_bars(ticker, date, interval="1m")
        if frame is None or getattr(frame, "empty", True):
            frame = intraday_bars.load_bars(ticker, date, interval="5m")
        if frame is not None and not getattr(frame, "empty", True):
            frames.append(frame)
    if not frames:
        return None

    hist = normalize_ohlc_frame(pd.concat(frames))
    if hist is None or getattr(hist, "empty", True):
        return None
    market = intraday_bars.market_of(ticker)
    rule = {"5m": "5min", "1h": "1h", "2h": "2h", "4h": "4h"}.get(tf, tf)
    return _resample_ohlc_frame(hist, rule, market=market, session_aligned=True)


def ohlc_tf(ticker: str, tf: str = "1d"):
    """타임프레임별 OHLCV — 1d/1wk/1mo 는 전체(주·월봉은 일봉 리샘플·무추가호출),
    5m 은 최근 60일·1h 는 최근 2년 (yfinance 인트라데이 보존 한계).
    2h/4h 는 yfinance 미지원 → 1h 를 리샘플(같은 2년 한계). 실패 None."""
    try:
        tf = str(tf or "1d").lower().strip() or "1d"
        from providers.market_data import _history_cached
        if tf == "1d":
            cached = _load_cached_ohlc_tf(ticker, tf, include_base=True)
            if cached is not None and _ohlc_tf_matches(cached, tf):
                return cached
            d = normalize_ohlc_frame(_history_cached(ticker, period="max"))
            if d is not None and not getattr(d, "empty", True) and _ohlc_tf_matches(d, tf):
                _save_cached_ohlc_tf(ticker, "max", d)
                _save_cached_ohlc_tf(ticker, "1d", d)
                return d
            return None

        if tf in ("1wk", "1mo"):
            cached = _load_cached_ohlc_tf(ticker, tf)
            if cached is not None and _ohlc_tf_matches(cached, tf):
                return cached
            base = _load_cached_ohlc_tf(ticker, "1d", include_base=True)
            if base is None or getattr(base, "empty", True):
                base = normalize_ohlc_frame(_history_cached(ticker, period="max"))
            if base is None or getattr(base, "empty", True):
                return None
            rule = "W-FRI" if tf == "1wk" else "ME"
            resampled = _resample_ohlc_frame(base, rule)
            if (resampled is not None and not getattr(resampled, "empty", True)
                    and _ohlc_tf_matches(resampled, tf)):
                _save_cached_ohlc_tf(ticker, tf, resampled)
                return resampled
            return None

        cached = _load_cached_ohlc_tf(ticker, tf)
        if cached is not None and _ohlc_tf_matches(cached, tf):
            return cached
        hist = _load_intraday_store_tf(ticker, tf)
        if hist is not None and not getattr(hist, "empty", True) and _ohlc_tf_matches(hist, tf):
            _save_cached_ohlc_tf(ticker, tf, hist)
            return hist

        import yfinance as yf
        resample_rule = {"2h": "2h", "4h": "4h"}.get(tf)
        interval = "1h" if resample_rule else tf
        period = "60d" if tf == "5m" else "730d"
        df = normalize_ohlc_frame(yf.Ticker(ticker).history(period=period, interval=interval))
        if df is None or df.empty:
            return None
        if resample_rule:
            df = _resample_ohlc_frame(df, resample_rule)
        if df is not None and not getattr(df, "empty", True) and _ohlc_tf_matches(df, tf):
            _save_cached_ohlc_tf(ticker, tf, df)
            return df
        return None
    except Exception:
        return None


def chart_data_bundle(ticker: str, timeframe: str, session_policy: str = "regular") -> dict:
    """Load requested bars with explicit session and provenance metadata.

    This intentionally rejects a mismatched frame instead of substituting daily
    bars for an unavailable intraday request.
    """
    from dashboard import chart_data_policy

    requested = str(timeframe or "1d").lower().strip() or "1d"
    market = "kr" if str(ticker or "").upper().endswith((".KS", ".KQ")) else "us"
    exchange_timezone = "Asia/Seoul" if market == "kr" else "America/New_York"
    hist = ohlc_tf(ticker, requested)
    prepared = None
    actual = requested
    if hist is not None and not getattr(hist, "empty", True):
        prepared = hist.copy(deep=True)
        prepared.attrs.update(getattr(hist, "attrs", {}) or {})
        provider_timezone = (prepared.attrs.get("provider_timezone")
                             or prepared.attrs.get("source_timezone")
                             or prepared.attrs.get("timezone"))
        if provider_timezone:
            prepared.attrs["provider_timezone"] = provider_timezone
        prepared.attrs.pop("timezone", None)
        prepared.attrs.update({"market": market, "exchange_timezone": exchange_timezone})
        actual = _detected_ohlc_timeframe(prepared)
        if actual is None:
            actual = requested if _ohlc_tf_matches(prepared, requested) else "unknown"

    if prepared is None or actual != requested:
        if prepared is None:
            session = chart_data_policy.apply_session_policy(
                None, market=market, timeframe=requested, policy=session_policy, timezone=exchange_timezone,
            ).metadata
            source = chart_data_policy.chart_data_status(
                None, requested_timeframe=requested, actual_timeframe=requested, source="ohlc_tf",
            )
        else:
            session = chart_data_policy.apply_session_policy(
                prepared, market=market, timeframe=actual, policy=session_policy, timezone=exchange_timezone,
            ).metadata
            source = chart_data_policy.chart_data_status(
                prepared, requested_timeframe=actual, actual_timeframe=actual, source="ohlc_tf",
            )
            session = {**session, "decision": "timeframe_mismatch", "actual_timeframe": actual}
            source.update({"requested_timeframe": requested, "actual_timeframe": actual,
                           "timeframe_mismatch": True})
        source.update({"market": market, "timezone": exchange_timezone})
        return {
            "frame": None,
            "requested_timeframe": requested,
            "actual_timeframe": actual,
            "session": session,
            "source": source,
        }

    session_result = chart_data_policy.apply_session_policy(
        prepared, market=market, timeframe=requested, policy=session_policy, timezone=exchange_timezone,
    )
    frame = session_result.frame
    frame.attrs.update(prepared.attrs)
    source = chart_data_policy.chart_data_status(
        frame, requested_timeframe=requested, actual_timeframe=requested, source="ohlc_tf",
    )
    return {
        "frame": frame,
        "requested_timeframe": requested,
        "actual_timeframe": requested,
        "session": session_result.metadata,
        "source": source,
    }


# ── 코스피200·러셀2000 시장 맵 (sp500_heatmap 패턴 — 스냅샷 우선·라이브 self-heal) ──

_KR200_SNAP = os.path.expanduser("~/reports/ml-cache/kr200_heatmap.json")
_RUSSELL_SNAP = os.path.expanduser("~/reports/ml-cache/russell2000_heatmap.json")

_NASDAQ_SECTOR_KR = {
    "Technology": "기술", "Telecommunications": "커뮤니케이션", "Health Care": "헬스케어",
    "Finance": "금융", "Real Estate": "부동산", "Consumer Discretionary": "경기소비재",
    "Consumer Staples": "필수소비재", "Industrials": "산업재", "Basic Materials": "소재",
    "Energy": "에너지", "Utilities": "유틸리티",
}
_NON_COMMON = ("Warrant", "Right", "Unit", "Preferred", "Depositary", "Notes")


def _snap_or(build, snap_path: str, max_age_s: int = 5400) -> list[dict]:
    """스냅샷(<max_age) 우선 → 없으면 build() 후 self-heal 기록 (sp500 패턴 공용)."""
    import json
    import time
    try:
        if time.time() - os.stat(snap_path).st_mtime < max_age_s:
            with open(snap_path, encoding="utf-8") as f:
                rows = json.load(f)
            if rows:
                return rows
    except Exception:
        pass
    rows = build()
    if rows:
        try:
            from safe_io import atomic_write_json
            atomic_write_json(snap_path, rows)
        except Exception:
            pass
    return rows


def kr200_heatmap() -> list[dict]:
    """코스피200 시장 맵 rows — 크론 스냅샷 우선(즉시) → 라이브(199종목 배치 ~30초)."""
    return _snap_or(_kr200_heatmap_live, _KR200_SNAP)


def _kr200_heatmap_live() -> list[dict]:
    """kr200_meta(업종·시총·이름) + yf 배치 당일 등락%. 타일=한글명·라벨=티커(클릭 계약)."""
    try:
        import kr200_meta
    except Exception:
        return []
    codes = sorted(kr200_meta.MARKET_CAP)
    tickers = [f"{c}.KS" for c in codes]
    pct: dict[str, float] = {}
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        df = yf.download(tickers, period="2d", progress=False, group_by="ticker", threads=True)
        for t in tickers:
            try:
                c = df[t]["Close"].dropna()
                if len(c) >= 2 and c.iloc[-2]:
                    pct[t] = round((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2)
            except Exception:
                pass
    except Exception:
        return []
    rows = []
    for c in codes:
        t = f"{c}.KS"
        p = pct.get(t)
        cap = kr200_meta.MARKET_CAP.get(c) or 0
        if p is None or cap <= 0:
            continue
        nm = kr200_meta.NAME.get(c) or c
        rows.append({"ticker": t, "name": nm, "tile": nm[:7],
                     "sector_kr": kr200_meta.SECTOR.get(c) or "기타",
                     "market_cap": float(cap), "pct": p})
    return rows


def russell2000_heatmap() -> list[dict]:
    """러셀2000 근사 시장 맵 — 美 보통주 시총 1001~3000위 (NASDAQ 스크리너 1콜·정직 라벨)."""
    return _snap_or(_russell2000_live, _RUSSELL_SNAP)


def _russell2000_live() -> list[dict]:
    """NASDAQ 스크리너(전 종목 시총·섹터·당일%) → 시총 1001~3000위. graceful []."""
    try:
        import requests
        r = requests.get("https://api.nasdaq.com/api/screener/stocks",
                         params={"tableonly": "true", "limit": "0", "download": "true"},
                         headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
                                  "Accept": "application/json"}, timeout=45)
        r.raise_for_status()
        raw = (r.json().get("data") or {}).get("rows") or []
    except Exception:
        return []
    stocks = []
    for it in raw:
        sym = (it.get("symbol") or "").strip()
        name = (it.get("name") or "").strip()
        if not sym or "^" in sym or "/" in sym or any(x in name for x in _NON_COMMON):
            continue
        try:
            cap = float(it.get("marketCap") or 0)
            p = float(str(it.get("pctchange") or "").replace("%", "") or "nan")
        except ValueError:
            continue
        if cap <= 0 or p != p:
            continue
        stocks.append((cap, sym, name, (it.get("sector") or "").strip(), p))
    stocks.sort(reverse=True)
    return [{"ticker": sym, "name": name[:40],
             "sector_kr": _NASDAQ_SECTOR_KR.get(sec, "기타"),
             "market_cap": cap, "pct": p}
            for cap, sym, name, sec, p in stocks[1000:3000]]


def trendlines_for(ticker: str, tf: str = "1d", *, lines: bool = True,
                   channels: tuple[str, ...] = ()) -> list[dict]:
    """자동 추세선·채널 감지 (dashboard.trendlines) — 표시·참고용. graceful []."""
    try:
        from dashboard import trendlines as tl
        df = ohlc_tf(ticker, tf)
        return tl.detect_trendlines(df, channels=channels, lines=lines)
    except Exception:
        return []


_TAPE_SYMS = [("^VIX", "VIX", 2), ("DX-Y.NYB", "달러 인덱스", 2), ("KRW=X", "달러 환율", 2),
              ("^KS11", "코스피", 2), ("^KQ11", "코스닥", 2), ("^IXIC", "나스닥", 2),
              ("^GSPC", "S&P500", 2), ("NQ=F", "나스닥100 선물", 1), ("ES=F", "S&P 선물", 1),
              ("GC=F", "금", 1), ("BTC-USD", "비트코인", 0), ("^TNX", "미10년물", 2)]


def market_tape() -> list[dict]:
    """하단 마퀴 띠 데이터 — [{label, value, chg, pct}]. yf 2d 배치·graceful []."""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        df = yf.download([s for s, _, _ in _TAPE_SYMS], period="2d", progress=False,
                         group_by="ticker", threads=True)
    except Exception:
        return []
    out = []
    for sym, label, dec in _TAPE_SYMS:
        try:
            c = df[sym]["Close"].dropna()
            if len(c) < 2 or not c.iloc[-2]:
                continue
            last, prev = float(c.iloc[-1]), float(c.iloc[-2])
            out.append({"label": label, "value": round(last, dec),
                        "chg": round(last - prev, dec),
                        "pct": round((last / prev - 1) * 100, 2)})
        except Exception:
            continue
    return out


def etf_tr_pr(ticker: str, years: int = 5):
    """ETF TR(배당재투자)/PR(가격) 시리즈 — {"tr","pr","asof"} | None. graceful."""
    try:
        from providers import etf_compare
        return etf_compare.tr_pr_series(ticker, years)
    except Exception:
        return None


def etf_peer_compare(ticker: str) -> dict:
    """동종그룹 지표+점수 — {"group","rows","asof"} | {} (그룹 없음/실패). graceful."""
    try:
        from providers import etf_compare
        return etf_compare.peer_report(ticker)
    except Exception:
        return {}


def accumulation_plan() -> dict:
    """주식 모으기(소수점 DCA) 계획 — bot.order_generator.build() (graceful {})."""
    try:
        from bot import order_generator
        return order_generator.build() or {}
    except Exception:
        return {}


def fx_now() -> float | None:
    """USD/KRW 실시간 환율 (graceful None) — 적립 폼 적용 환율 자동 채움."""
    try:
        from providers.market_data import fetch_exchange_rate
        return fetch_exchange_rate()
    except Exception:
        return None


def fx_timing() -> dict:
    """환전 타이밍 판정 (providers.fx_timing — 아침 리포트와 동일 산식). graceful {}."""
    try:
        from providers.fx_timing import fetch_fx_timing
        return fetch_fx_timing() or {}
    except Exception:
        return {}


def portfolio_history() -> list:
    """일별 포트 히스토리 (portfolio_tracker — store 경유). graceful []."""
    try:
        import portfolio_tracker
        return portfolio_tracker.load_history() or []
    except Exception:
        return []


def target_weights_map() -> dict:
    """목표 비중 {ticker: 분수} (봇 /rebalance 와 동일 소스). graceful {}."""
    try:
        from barbell_strategy import load_target_weights
        return load_target_weights() or {}
    except Exception:
        return {}


def income_summary(qqqi_shares: float = 0.0, qqqi_usd: float = 0.0) -> dict:
    """인컴 트래커 — 분배금 기록 누적 + QQQI 월 예상(추정·네트워크 graceful)."""
    out = {"records": [], "total": 0.0, "est_monthly": None}
    try:
        import store
        recs = store.all("qqqi_dividends") or []
        out["records"] = recs
        out["total"] = sum(float(r.get("amount") or r.get("amount_usd") or 0) for r in recs)
    except Exception:
        pass
    try:
        if qqqi_shares > 0:
            from providers.market_data import estimate_qqqi_monthly_dividend
            est = estimate_qqqi_monthly_dividend(qqqi_shares, qqqi_usd) or {}
            out["est_monthly"] = est.get("monthly_usd") or est.get("estimated_monthly_usd")
            out["est_detail"] = est
    except Exception:
        pass
    return out


def sp500_valuation() -> dict:
    """S&P500 지수 밸류 (상위 100 시총가중 집계 — market_valuation). graceful {}."""
    try:
        from providers.market_valuation import sp500_valuation as _v
        return _v() or {}
    except Exception:
        return {}


_SCREENER_LAST = None                       # 경로 상수 (홈 기준 — 워크트리 무관)


def _screener_last_path():
    from pathlib import Path
    return Path.home() / "reports" / "ml-cache" / "screener_last.json"


def screener_last() -> dict | None:
    """마지막 스크리너 실행 결과 (디스크 영속 — 나이 무관 표시·asof 병기). 없으면 None."""
    try:
        import json
        p = _screener_last_path()
        return json.loads(p.read_text()) if p.exists() else None
    except Exception:
        return None


def _backtest_last_path():
    from pathlib import Path
    return Path.home() / "reports" / "ml-cache" / "backtest_last.json"


def _backtest_persist(out: dict) -> None:
    """백테스트 결과 JSON 영속 (equity 는 컬럼별 값 배열 — graceful·실패 무시)."""
    try:
        import json
        from datetime import datetime

        import pandas as pd
        eq, eq_j = out.get("equity"), None
        if eq is not None:
            try:
                df = eq if isinstance(eq, pd.DataFrame) else pd.DataFrame({"equity": pd.Series(eq)})
                eq_j = {str(c): [None if pd.isna(x) else float(x) for x in df[c].tolist()]
                        for c in df.columns}
            except Exception:
                eq_j = None

        def _mm(d):
            try:
                return {k: (None if v is None else float(v)) for k, v in (d or {}).items()}
            except Exception:
                return {}

        payload = {"ml": _mm(out.get("ml")), "overlay": _mm(out.get("overlay")),
                   "qqq": _mm(out.get("qqq")), "verdict": str(out.get("verdict") or ""),
                   "reasons": [str(x) for x in out.get("reasons") or []], "equity": eq_j,
                   "asof": datetime.now().strftime("%Y-%m-%d %H:%M")}
        p = _backtest_last_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        tmp.replace(p)
    except Exception:
        pass


def backtest_last() -> dict | None:
    """마지막 백테스트 결과 (디스크 영속 — 나이 무관 표시·asof 병기). 없으면 None."""
    try:
        import json
        p = _backtest_last_path()
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        if d.get("equity"):
            import pandas as pd
            d["equity"] = pd.DataFrame(d["equity"])
        return d
    except Exception:
        return None


def portfolio_flows() -> dict:
    """일자별 외부 현금흐름(USD) — 거래 원장 합산 (적립/추가 +·축소 −). graceful {}."""
    try:
        from lib import trade_events as te
        out: dict = {}
        for r in te.all_trades():
            if str(r.get("currency") or "USD") != "USD":
                continue
            src = str(r.get("source") or "")
            if "mock" in src or "mock" in str(r.get("account") or ""):
                continue
            qty, px = r.get("qty"), r.get("price")
            if not qty or not px:
                continue
            amt = float(qty) * float(px)
            d = str(r.get("date") or "")[:10]
            if not d:
                continue
            out[d] = out.get(d, 0.0) + (amt if r.get("side") == "buy" else -amt)
        return out
    except Exception:
        return {}


def market_temp_history(limit: int = 30) -> list:
    """시장 온도계 일별 이력 (store — 크론 적재). graceful []."""
    try:
        import store
        rows = store.all("market_temp_history") or []
        return rows[-limit:]
    except Exception:
        return []


def next_earnings(ticker: str):
    """다음 실적 발표일 (yfinance calendar) — date | None. graceful."""
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar or {}
        ed = cal.get("Earnings Date")
        if isinstance(ed, (list, tuple)) and ed:
            return ed[0]
        return ed or None
    except Exception:
        return None


def chart_workspace_catalog(limit: int = 50) -> dict:
    """저장된 차트 워크스페이스 목록."""
    try:
        rows = storage.list_chart_workspaces(limit=limit)
        return {
            "ok": True,
            "count": len(rows),
            "workspaces": rows,
            "latest": rows[0] if rows else None,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "count": 0, "workspaces": []}


def chart_workspace_detail(workspace_id: str | None = None, version: int | None = None) -> dict:
    """차트 워크스페이스 단건. 없으면 기본 워크스페이스를 반환."""
    try:
        if workspace_id:
            row = storage.get_chart_workspace(workspace_id, version=version)
            if row:
                return row
        ws = chart_workspace.default_workspace()
        return {
            "id": ws["id"],
            "name": ws["name"],
            "layout": ws["layout"],
            "version": 0,
            "workspace": ws,
        }
    except Exception as exc:
        ws = chart_workspace.default_workspace()
        return {"id": ws["id"], "version": 0, "workspace": ws, "error": str(exc)}


def chart_workspace_versions(workspace_id: str, limit: int = 30) -> list[dict]:
    """차트 워크스페이스 버전 이력."""
    try:
        return storage.list_chart_workspace_versions(workspace_id, limit=limit)
    except Exception:
        return []


def chart_workspace_save(workspace: dict) -> dict:
    """차트 워크스페이스 저장."""
    return storage.save_chart_workspace(workspace)


def chart_templates(kind: str | None = None, limit: int = 50) -> list[dict]:
    """차트 템플릿 목록."""
    try:
        return storage.list_chart_templates(kind=kind, limit=limit)
    except Exception:
        return []


def chart_template_save(template: dict) -> dict:
    """차트 템플릿 저장."""
    return storage.save_chart_template(template)


def chart_alert_rules(workspace_id: str, limit: int = 50) -> list[dict]:
    """차트 알림 룰 목록."""
    try:
        return storage.list_chart_alert_rules(workspace_id=workspace_id, limit=limit)
    except Exception:
        return []


def chart_alert_runs(workspace_id: str, limit: int = 5) -> list[dict]:
    """차트 알림 워커 실행 이력."""
    try:
        return storage.list_chart_alert_runs(workspace_id=workspace_id, limit=limit)
    except Exception:
        return []


def chart_alert_run_once(workspace_id: str, *, notify: bool = False, symbols: list[str] | None = None) -> dict:
    """차트 알림 워커 수동 실행."""
    from agent_console import chart_alert_worker
    return chart_alert_worker.run_chart_alert_cycle(
        workspace_id=workspace_id,
        symbols=symbols or [],
        notify=notify,
    )


def chart_alert_rule_save(rule: dict) -> dict:
    """차트 알림 룰 저장."""
    return storage.save_chart_alert_rule(rule)


def chart_workspace_patch_preview(
    workspace: dict,
    *,
    patch: dict | None = None,
) -> dict:
    """차트 워크스페이스 patch 미리보기."""
    before = chart_workspace.normalize_workspace(workspace)
    after = chart_workspace.apply_workspace_patch(before, patch or {})
    return {
        "ok": True,
        "before": before,
        "after": after,
        "patch": patch or {},
        "diff": chart_workspace.diff_workspaces(before, after),
    }
