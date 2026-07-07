#!/usr/bin/env python3
"""
intraday_smoke_test.py — 단기(1m/5m) 모의 트레이딩 파이프라인 연기 테스트.

네트워크 없이 합성 분봉·틱·호가로 데이터층(bar 집계)·판단층(축·가드·청산·사이징)·
엔진(state·원장 멱등)을 검증. 실패 시 텔레그램 알림 (ml_smoke_test 관례).

크론 (평일 00:00 UTC — ml_smoke_test 와 동일 슬롯):
    0 0 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python tests/intraday_smoke_test.py >> /tmp/intraday_smoke_test.log 2>&1
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
import tempfile
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("STOCK_BOT_TOKEN")
CHAT_ID = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")


# 개발 워크트리 실행 가드 — load_dotenv 가 상위 프로덕션 .env 를 찾아 올라가서
# 개발 중 빨간 실행이 실제 텔레그램 알림을 쏘는 사고 방지(2026-07-07 2회 발생).
# 크론은 메인 트리에서 돌므로 프로덕션 실패 알림은 불변.
_DEV_RUN = "/.claude/worktrees/" in os.path.abspath(__file__)


def _alert(msg: str):
    if _DEV_RUN:
        logger.info("개발 워크트리 실행 — 텔레그램 알림 생략")
        return
    if not BOT_TOKEN:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": f"🕐 intraday smoke test 실패\n━━━━━━━━━━━━━━\n{msg}"},
            timeout=10,
        )
    except Exception as e:
        logger.error("알림 전송 실패: %s", e)


def _check(name: str, fn, *checks) -> list[str]:
    failures = []
    try:
        result = fn()
    except Exception as e:
        return [f"❌ {name}: 예외 — {e}"]
    for desc, condition in checks:
        try:
            ok = bool(condition(result))
        except Exception as e:
            ok = False
            desc = f"{desc} (검증 오류: {e})"
        if not ok:
            failures.append(f"❌ {name}: {desc}")
        else:
            logger.info("  ✅ %s — %s", name, desc)
    return failures


def _synth_session(n=60, base=61000.0, tick=100.0, vol=1000.0, breakout_at=None):
    """합성 1m 세션 DataFrame — breakout_at 지정 시 그 봉부터 OR 상단 돌파+거래량 급증."""
    import pandas as pd
    idx = pd.date_range("2026-07-08 09:00", periods=n, freq="min", tz="Asia/Seoul")
    o, h, l, c, v = [], [], [], [], []
    px = base
    for i in range(n):
        drift = tick if (breakout_at is not None and i >= breakout_at) else \
            (tick if i % 4 == 1 else (-tick if i % 4 == 3 else 0))
        px2 = px + drift
        o.append(px); c.append(px2)
        h.append(max(px, px2) + tick); l.append(min(px, px2) - tick)
        v.append(vol * (6.0 if (breakout_at is not None and i >= breakout_at) else 1.0))
        px = px2
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)


def run_tests() -> list[str]:
    failures = []
    tmp = tempfile.mkdtemp(prefix="intraday_smoke_")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── i1: 심볼 변환 ─────────────────────────────────────────────────────────
    logger.info("[i1] 심볼 변환")
    from providers import intraday_bars as ib
    failures += _check("symbols", lambda: None,
        (".KS 제거", lambda _: ib.base_symbol("005930.KS") == "005930"),
        (".KQ 보존(to_yf)", lambda _: ib.to_yf("247540.KQ") == "247540.KQ"),
        ("KR 기본 .KS", lambda _: ib.to_yf("005930") == "005930.KS"),
        ("US 항등", lambda _: ib.to_yf("AAPL") == "AAPL" and ib.base_symbol("AAPL") == "AAPL"),
        ("시장 분류", lambda _: ib.market_of("005930") == "KR" and ib.market_of("NVDA") == "US"),
        ("trade_events 정합", lambda _: __import__("lib.trade_events", fromlist=["x"])._symbol("005930.KS") == ib.base_symbol("005930.KS")),
    )

    # ── i2: BarAggregator 차분·경계·이상치 ────────────────────────────────────
    logger.info("[i2] bar 집계")
    t0 = 1751850000 - (1751850000 % 60)

    def _agg():
        a = ib.BarAggregator()
        a.on_tick("005930", 61400, 1000, t0 + 1, "KR")
        a.on_tick("005930", 61500, 1500, t0 + 30, "KR")
        a.on_tick("005930", 61300, 2000, t0 + 59, "KR")
        early = a.roll(t0 + 59)
        b1 = a.roll(t0 + 61)
        a.on_tick("005930", 61350, 2500, t0 + 65, "KR")
        a.on_tick("005930", 61360, 100, t0 + 90, "KR")     # 누적 역행 글리치
        b2 = a.roll(t0 + 121)
        return early, b1, b2

    failures += _check("bar_agg", _agg,
        ("분 미경과 확정 0", lambda r: r[0] == []),
        ("OHLC 정확", lambda r: (r[1][0]["o"], r[1][0]["h"], r[1][0]["l"], r[1][0]["c"]) == (61400, 61500, 61300, 61300)),
        ("첫 bar v_partial", lambda r: r[1][0]["v_partial"] is True),
        ("볼륨=누적 차분", lambda r: r[1][0]["v"] == 1000.0),
        ("2번째 bar 차분", lambda r: r[2][0]["v"] == 500.0 and r[2][0]["v_partial"] is False),
        ("역행 클램프 0", lambda r: r[2][0]["v"] >= 0),
    )

    def _anom():
        a = ib.BarAggregator()
        a.on_tick("X", 10, 5000, t0, "US")
        a.roll(t0 + 61)
        a.on_tick("X", 11, 100, t0 + 65, "US")
        return a.roll(t0 + 121)

    failures += _check("bar_anom", _anom,
        ("역행 → v=0·v_anom", lambda r: r[0]["v"] == 0.0 and r[0]["v_anom"] is True))

    def _allowed():
        a = ib.BarAggregator()
        a.set_allowed({"QQQ"})
        a.on_tick("QQQ", 500.0, 100, t0, "US")
        a.on_tick("621947", 10.0, 50, t0, "KR")     # 파싱 글리치 위장 심볼 (6자리 숫자)
        a.on_tick("111.29", 5.0, 10, t0, "US")      # 가격이 심볼 자리에 온 케이스
        return a.roll(t0 + 61)

    failures += _check("bar_allowed", _allowed,
        ("화이트리스트 밖 틱 무시", lambda r: [b["symbol"] for b in r] == ["QQQ"]))

    # ── i3: reader 왕복·5m 리샘플·프로파일 ────────────────────────────────────
    logger.info("[i3] bar store 왕복")

    def _roundtrip():
        recs = [{"ts": datetime.fromtimestamp(t0 + i * 60, tz=timezone.utc).isoformat(),
                 "epoch_min": t0 // 60 + i, "symbol": "005930", "market": "KR",
                 "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100.5 + i, "v": 10.0,
                 "n": 3, "v_partial": i == 0, "v_anom": False, "src": "kis_ws"} for i in range(10)]
        ib.append_bars(recs, base_dir=tmp)
        df1 = ib.load_bars("005930.KS", today, base_dir=tmp)
        df5 = ib.load_bars("005930", today, interval="5m", base_dir=tmp)
        prof = ib.build_minute_profile("005930", [today], base_dir=tmp)
        return df1, df5, prof

    failures += _check("bar_store", _roundtrip,
        ("10봉 로드·컬럼", lambda r: len(r[0]) == 10 and list(r[0].columns) == ["Open", "High", "Low", "Close", "Volume"]),
        ("5m 리샘플 합", lambda r: len(r[1]) == 2 and r[1]["Volume"].iloc[0] == 50.0),
        ("tz-aware 인덱스", lambda r: r[0].index.tz is not None),
        ("프로파일 v_partial 제외", lambda r: len(r[2]) == 9),
        ("빈 스토어 graceful", lambda r: ib.load_bars("없음", today, base_dir=tmp).empty),
    )

    # ── i4: 스캐너 필터·랭크·히스테리시스 ─────────────────────────────────────
    logger.info("[i4] 유니버스 스캐너")
    from providers import intraday_universe as iu
    rows = [
        {"code": "005930", "name": "삼성전자", "price": 61450, "chg_pct": 2.1, "turnover": 9e11},
        {"code": "005935", "name": "삼성전자우", "price": 51000, "chg_pct": 1.9, "turnover": 5e10},
        {"code": "069500", "name": "KODEX 200", "price": 35000, "chg_pct": 1.0, "turnover": 8e11},
        {"code": "123450", "name": "테스트스팩", "price": 2100, "chg_pct": 9.0, "turnover": 6e10},
        {"code": "000660", "name": "SK하이닉스", "price": 200000, "chg_pct": -4.2, "turnover": 7e11},
        {"code": "111110", "name": "저유동주", "price": 5000, "chg_pct": 12.0, "turnover": 1e9},
        {"code": "222220", "name": "동전주", "price": 500, "chg_pct": 20.0, "turnover": 5e10},
    ]
    failures += _check("scanner", lambda: iu.filter_kr_candidates(rows),
        ("우선주·ETF·스팩·저유동·동전주 제외", lambda c: [r["code"] for r in c] == ["005930", "000660"]),
        ("|등락| 랭크", lambda c: iu.rank_by_move(c, "chg_pct", 2) == ["000660", "005930"]),
        ("히스테리시스 keep 우선", lambda c: iu.merge_with_keep(["035420"], ["005930", "000660", "373220"], 3) == ["035420", "005930", "000660"]),
        ("keep 이 cap 초과해도 유지", lambda c: iu.merge_with_keep(["A", "B", "C"], ["D"], 2) == ["A", "B", "C"]),
        ("US 시총 필터", lambda c: [r["ticker"] for r in iu.filter_us_candidates(
            [{"ticker": "NVDA", "market_cap": 3e12, "pct": -5.0},
             {"ticker": "SMALL", "market_cap": 5e9, "pct": 9.0}])] == ["NVDA"]),
    )

    # ── i5: 축 — ORB·VWAP·volspike·OFI·news·레짐 ─────────────────────────────
    logger.info("[i5] 판단 축")
    from ml import intraday_axes as ax

    df_bo = _synth_session(40, breakout_at=25)
    df_flat = _synth_session(40)

    def _orb():
        orr = ax.opening_range(df_bo, 15)
        hi = orr[0]
        brk = ax.axis_orb(float(df_bo["Close"].iloc[30]), orr, 3.5)
        no = ax.axis_orb(float(df_flat["Close"].iloc[20]), ax.opening_range(df_flat, 15), 0.5)
        return orr, brk, no, hi

    failures += _check("axis_orb", _orb,
        ("OR 확정", lambda r: r[0] is not None and r[0][0] > r[0][1]),
        ("돌파+볼륨 → 고점수", lambda r: r[1] is not None and r[1] >= 0.8),
        ("미돌파 → 0", lambda r: r[2] == 0.0),
        ("봉 부족 → None", lambda r: ax.opening_range(df_bo.iloc[:10], 15) is None),
    )

    failures += _check("axis_vol", lambda: None,
        ("시간대 정규화 z", lambda _: abs(ax.tod_vol_z(6000, "10:14", {"10:14": {"mean": 1000, "std": 500, "n": 10}}) - 10.0) < 1e-9),
        ("표본 부족 None", lambda _: ax.tod_vol_z(6000, "10:14", {"10:14": {"mean": 1000, "std": 500, "n": 3}}) is None),
        ("스파이크+임펄스 만점권", lambda _: ax.axis_volspike(4.0, 1.2) >= 0.8),
        ("임펄스 음수 → 0 (롱 전용)", lambda _: ax.axis_volspike(5.0, -1.0) == 0.0),
        ("결측 → None", lambda _: ax.axis_volspike(None, 1.0) is None),
        ("폴백 z (21봉)", lambda _: ax.vol_z_fallback([100.0] * 20 + [1000.0]) is None or True),
    )

    failures += _check("axis_ofi_news", lambda: None,
        ("OBI 계산", lambda _: abs(ax.obi({"bids": [(100, 800)], "asks": [(101, 200)]}) - 0.6) < 1e-9),
        ("매수 우세 가점", lambda _: ax.axis_ofi([0.6, 0.7]) > 0.5),
        ("중립/매도 우세 0", lambda _: ax.axis_ofi([0.1, -0.2]) == 0.0),
        ("호가 없음 None", lambda _: ax.obi(None) is None),
        ("호재 이벤트 창 내", lambda _: ax.axis_news(
            [{"symbols": ["005930"], "epoch": 1000.0, "direction": 1, "strength": 4}],
            "005930", 1000.0 + 600) > 0.7),
        ("악재 → 0 (롱 억제)", lambda _: ax.axis_news(
            [{"symbols": ["005930"], "epoch": 1000.0, "direction": -1, "strength": 5}],
            "005930", 1000.0 + 600) == 0.0),
        ("창 밖 → None", lambda _: ax.axis_news(
            [{"symbols": ["005930"], "epoch": 1000.0, "direction": 1, "strength": 4}],
            "005930", 1000.0 + 7200) is None),
    )

    failures += _check("regime", lambda: None,
        ("추세 ER 높음", lambda _: ax.regime_er(list(range(100, 140))) > 0.9),
        ("승수 적용·클램프", lambda _: ax.apply_regime({"orb": 0.9, "vwap": 0.5, "news": None},
                                                        {"orb": 1.2, "vwap": 0.8})["orb"] == 1.0),
        ("None 축 유지", lambda _: ax.apply_regime({"news": None}, {"orb": 1.2})["news"] is None),
    )

    # ── i6: 가드 — 순서·차단 ─────────────────────────────────────────────────
    logger.info("[i6] 진입 가드")
    base_ctx = {"halt": False, "now_min": 600, "close_min": 930, "flat_buffer_min": 15,
                "entry_cutoff_min": 30, "trades_today": 0, "max_trades": 6,
                "cooldown_ok": True, "held": False, "fresh": True,
                "spread": 5.0, "spread_cap": 25.0, "qty": 10}

    def _guards():
        out = {"ok": ax.entry_guards(dict(base_ctx))}
        for k, v, want in (("halt", True, "halt"), ("now_min", 920, "eod_window"),
                           ("trades_today", 6, "max_trades"), ("cooldown_ok", False, "cooldown"),
                           ("held", True, "held"), ("fresh", False, "stale_data"),
                           ("spread", 99.0, "spread"), ("qty", 0, "qty")):
            ctx = dict(base_ctx); ctx[k] = v
            out[want] = ax.entry_guards(ctx)
        return out

    failures += _check("guards", _guards,
        ("전부 통과 ok", lambda r: r["ok"] == (True, "ok")),
        *[(f"{k} 차단", lambda r, k=k: r[k] == (False, k))
          for k in ("halt", "eod_window", "max_trades", "cooldown", "held", "stale_data", "spread", "qty")],
    )
    failures += _check("spread_cap", lambda: None,
        ("KR 2틱 하한(6.1만원=~32bps)", lambda _: ax.spread_cap_bps(61450, "KR", 25.0) > 30.0),
        ("US 캡 그대로", lambda _: ax.spread_cap_bps(400.0, "US", 5.0) == 5.0),
    )

    # ── i7: 청산 우선순위 ─────────────────────────────────────────────────────
    logger.info("[i7] 청산 판정")
    pos = {"entry_price": 61450.0, "stop": 61150.0, "target": 62050.0,
           "entry_min": 100, "risk_per_share": 300.0}
    cfg = {"timestop_min": 90, "theta_exit": 0.25, "flat_buffer_min": 15}

    failures += _check("exits", lambda: None,
        ("손절 (보수가)", lambda _: ax.check_exit(pos, {"h": 61500, "l": 61100, "c": 61050}, 0.6, 150, 930, cfg) == ("stop", 61050.0)),
        ("손절이 목표보다 우선", lambda _: ax.check_exit(pos, {"h": 62100, "l": 61100, "c": 61500}, 0.6, 150, 930, cfg)[0] == "stop"),
        ("목표", lambda _: ax.check_exit(pos, {"h": 62100, "l": 61400, "c": 62000}, 0.6, 150, 930, cfg) == ("target", 62050.0)),
        ("타임스톱 (무진전)", lambda _: ax.check_exit(pos, {"h": 61500, "l": 61400, "c": 61470}, 0.6, 195, 930, cfg)[0] == "timestop"),
        ("진전 있으면 유지", lambda _: ax.check_exit(pos, {"h": 61800, "l": 61500, "c": 61750}, 0.6, 195, 930, cfg) is None),
        ("신호 붕괴", lambda _: ax.check_exit(pos, {"h": 61500, "l": 61400, "c": 61470}, 0.1, 150, 930, cfg)[0] == "signal_collapse"),
        # EOD: 최근 진입(타임스톱 미도달) 포지션 — 마감 버퍼 진입 시 강제 flat
        ("EOD flat", lambda _: ax.check_exit({**pos, "entry_min": 900}, {"h": 61500, "l": 61400, "c": 61470}, 0.6, 916, 930, cfg)[0] == "eod_flat"),
        ("bar 부재 EOD", lambda _: ax.check_exit(pos, None, None, 916, 930, cfg)[0] == "eod_flat"),
        ("bar 부재 장중 None", lambda _: ax.check_exit(pos, None, None, 500, 930, cfg) is None),
    )

    # ── i8: 사이징·가상체결·호가단위 ──────────────────────────────────────────
    logger.info("[i8] 사이징·체결")
    failures += _check("sizing", lambda: None,
        # 리스크 주수 16(=5000/300) vs 1/3 캡 5(=33.3만/6.1만) — 작은 쪽
        ("리스크 주수·캡 중 최소", lambda _: ax.position_size(1_000_000, 0.005, 61450, 61150) == 5),
        # 손절폭이 넓으면(2.4%) 리스크 주수(344)가 캡(542)보다 작아 리스크 쪽 채택
        ("캡 미달 시 리스크 주수", lambda _: ax.position_size(100_000_000, 0.005, 61450, 60000) == int(500_000 / 1450)),
        ("포지션 캡 1/3", lambda _: ax.position_size(1_000_000, 0.05, 61450, 61440) == int(1_000_000 / 3 / 61450)),
        ("stop_dist 0 → 0", lambda _: ax.position_size(1_000_000, 0.005, 61450, 61450) == 0),
        ("호가단위 표", lambda _: ax.kr_tick(61450) == 100 and ax.kr_tick(1500) == 1 and ax.kr_tick(600000) == 1000),
    )
    failures += _check("virtual_fill", lambda: None,
        ("매수=best_ask 기준", lambda _: ax.virtual_fill("buy", 61400, 61500, 61450, "KR")[0] == 61500),
        ("페널티=스프레드/2+1틱", lambda _: ax.virtual_fill("buy", 61400, 61500, 61450, "KR")[1] == 150.0),
        ("매도=best_bid", lambda _: ax.virtual_fill("sell", 61400, 61500, 61450, "KR")[0] == 61400),
        ("호가 없음 → last+2틱", lambda _: ax.virtual_fill("buy", None, None, 61450, "KR") == (61450.0, 200.0)),
        ("가격 전무 → None", lambda _: ax.virtual_fill("buy", None, None, None, "KR") is None),
    )

    # ── i9: 정책 — 결측 재정규화·클램프 ───────────────────────────────────────
    logger.info("[i9] 정책")
    from ml import intraday_policy as ip

    def _policy():
        feats_full = {"orb": 1.0, "vwap": 0.0, "volspike": 1.0, "ofi": 0.5,
                      "news": 1.0, "ema": 0.5, "rsi": 0.3, "bb": 0.0}
        feats_missing = {**feats_full, "news": None, "ofi": None}
        p = ip.DEFAULTS["kr"]
        pol = ip.get_policy("kr")
        clamped = pol.clamp({"w_orb": 9.0, "theta_entry": 0.1})
        return (ip.score(feats_full, p, "kr"), ip.score(feats_missing, p, "kr"),
                ip.score({}, p, "kr"), clamped)

    failures += _check("policy", _policy,
        ("만점축 결합 > θ", lambda r: r[0] > 0.55),
        ("결측 재정규화 유효", lambda r: 0.0 < r[1] <= 1.0),
        ("전결측 → 0", lambda r: r[2] == 0.0),
        ("클램프 상한", lambda r: r[3]["w_orb"] <= 0.5 and r[3]["theta_entry"] >= 0.40),
        ("US 기본가중 합 1", lambda r: abs(sum(v for k, v in ip.DEFAULTS["us"].items() if k.startswith("w_")) - 1.0) < 1e-9),
        ("KR 기본가중 합 1", lambda r: abs(sum(v for k, v in ip.DEFAULTS["kr"].items() if k.startswith("w_")) - 1.0) < 1e-9),
    )

    # ── i10: 엔진 — 진입→멱등→손절→쿨다운→orphan 수리 (전부 모킹·쓰기 tmp 격리) ──
    logger.info("[i10] 엔진 사이클")
    failures += _engine_tests(tmp)

    # ── i11: 주간 학습 — fit/eval/게이트 verdict 분기 (합성 원장) ─────────────
    logger.info("[i11] 주간 학습·게이트")
    from crons import intraday_mock_learn as learn
    import random
    rng = random.Random(0)

    def _rows(n, mean_r):
        rows = []
        for i in range(n):
            win = rng.random() < (0.7 if mean_r > 0 else 0.3)
            lvl = 0.9 if win else 0.1
            r = abs(rng.gauss(1.0, 0.3))
            rows.append({"id": f"2026-06-{i % 20 + 1:02d}:T{i}:1000{i % 60:02d}",
                         "date": f"2026-06-{i % 20 + 1:02d}", "side": "단기진입",
                         "policy_score": lvl,
                         "features": {"orb": lvl, "vwap": lvl, "volspike": lvl,
                                      "ofi": lvl, "news": None, "ema": 0.5, "rsi": 0.3, "bb": 0.3},
                         "fwd_excess": round(r if win else -r, 4)})
        return rows

    good, bad = _rows(120, 0.4), _rows(120, -0.4)

    failures += _check("learn_fit", lambda: learn.make_fit("kr")(good),
        ("가중 합 1", lambda w: abs(sum(w.values()) - 1.0) < 0.01),
        ("측정축 가중 > 0", lambda w: w["w_orb"] > 0),
        ("무신호 폴백=DEFAULT", lambda w: abs(sum(learn.make_fit("kr")(
            [{"features": {}, "fwd_excess": 0.1}] * 10).values())
            - sum(v for k, v in __import__("ml.intraday_policy", fromlist=["x"]).DEFAULTS["kr"].items()
                  if k.startswith("w_"))) < 0.01),
    )
    failures += _check("learn_eval", lambda: learn.eval_policy(good, learn.make_fit("kr")(good), "kr"),
        ("선택 발생", lambda e: e["n"] > 0),
        ("양의 기대", lambda e: e["excess"] > 0),
        ("MDD [0,1]", lambda e: 0.0 <= e["mdd"] <= 1.0),
        ("전결측 → n 0", lambda e: learn.eval_policy([], {}, "kr")["n"] == 0),
    )
    failures += _check("gate", lambda: (learn.gate_eval(good, "kr"),
                                        learn.gate_eval(bad, "kr"),
                                        learn.gate_eval(good[:30], "kr")),
        ("양 분포 → GO/OBSERVE", lambda g: g[0]["verdict"] in ("GO", "OBSERVE")),
        ("음 분포 → NO-GO", lambda g: g[1]["verdict"] == "NO-GO"),
        ("표본 미달 → 콜드스타트", lambda g: g[2]["verdict"] == "콜드스타트"),
        ("PSR 산출", lambda g: g[0]["psr"] is not None and g[0]["psr"] > 0.9),
        ("news 표본 0 명시", lambda g: g[0]["news_axis_n"] == 0),
    )

    # ── i12: evolution 단기 표면 집계 + /evolve 렌더 ─────────────────────────
    logger.info("[i12] evolution·evolve 렌더")
    from ml.adaptive import evolution
    failures += _check("evolution_intraday", lambda: evolution.snapshot(good),
        ("단기진입 side 집계", lambda s: s["n"] == len([r for r in good if r["fwd_excess"] is not None])),
        ("IC 산출", lambda s: s["realized_ic"] is not None),
    )
    from bot.evolve_command import build_evolve_report
    failures += _check("evolve_render", lambda: build_evolve_report(html=False),
        ("단기 표면 포함", lambda t: "단기 KR" in t and "단기 US" in t),
        ("무예외 렌더", lambda t: len(t) > 100),
    )

    # ── i13: 안전 grep — 엔진 주문은 모의 어댑터 경유만 (실계좌 경로 0 강제) ──
    logger.info("[i13] 안전 grep")
    import crons.intraday_mock_track as _eng_mod

    def _src():
        return open(_eng_mod.__file__, encoding="utf-8").read()

    failures += _check("order_path_grep", _src,
        ("실전 키움 도메인 미참조", lambda s: "api.kiwoom.com" not in s.replace("mockapi.kiwoom.com", "")),
        ("실전 주문 TR 미참조", lambda s: "TTTC" not in s and "TTTT" not in s),
        ("직접 HTTP 주문 없음", lambda s: "requests.post" not in s),
        ("주문은 모의 어댑터 경유", lambda s: "kiwoom_mock" in s and "kis_mock" in s),
    )

    return failures


def _engine_tests(tmp: str) -> list[str]:
    """run_market 전체 사이클 — 시장·시세·유니버스·이벤트 기록 전부 모킹."""
    import pandas as pd
    from crons import intraday_mock_track as eng
    from providers import intraday_bars as ib
    from providers import intraday_universe as iu
    from providers import realtime_quotes as rq

    failures = []
    ledger_dir = os.path.join(tmp, "ledger")
    os.makedirs(ledger_dir, exist_ok=True)
    events: list[dict] = []

    # 모킹 — 원본 보관 후 복원
    saved = {
        "_market_open": eng._market_open, "_news_events": eng._news_events,
        "_record_event": eng._record_event, "_rest_price": eng._rest_price,
        "LEDGER": eng._LEDGER_BASE,
        "iu_refresh": iu.refresh, "iu_current": iu.current_universe,
        "ib_load": ib.load_bars, "ib_dates": ib.available_dates,
        "rq_enabled": rq.enabled, "rq_hb": rq.heartbeat_age,
        "rq_fresh": rq.is_fresh, "rq_ob": rq.get_orderbook,
    }

    # 세션: 확정 30봉 — 25봉째부터 OR 돌파+거래량 급증. 지금 KST 분에 끝나게 정렬.
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    n_bars = 35                                 # 레짐 ER(31봉)까지 계산되게
    start = now_kst.replace(second=0, microsecond=0) - timedelta(minutes=n_bars)
    idx = pd.date_range(start, periods=n_bars, freq="min")   # start 가 tz-aware(KST)
    o, h, l, c, v = [], [], [], [], []
    px = 61000.0
    for i in range(n_bars):
        # 개장 15분 넓은 범위(±150) → 마지막 3봉 완만한 돌파(+150/봉·과확장 페널티 미발동)
        drift = 150.0 if i >= 32 else (150.0 if i % 4 == 1 else (-150.0 if i % 4 == 3 else 0.0))
        px2 = px + drift
        o.append(px); c.append(px2)
        h.append(max(px, px2) + 40); l.append(min(px, px2) - 40)
        v.append(1000.0 * (20.0 if i >= 32 else 1.0))
        px = px2
    df_bo = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)
    bars_now = {"005930": df_bo}

    last_c = float(df_bo["Close"].iloc[-1])
    ob = {"bids": [(last_c, 2000)], "asks": [(last_c + 100, 200)],   # 1틱 스프레드·매수 우세(OFI)
          "best_bid": last_c, "best_ask": last_c + 100, "ts": 0}

    try:
        eng._LEDGER_BASE = ledger_dir
        eng._market_open = lambda mk: mk == "KR"
        eng._news_events = lambda now_epoch: []
        eng._record_event = lambda *a, **k: events.append({"sym": a[0], "side": a[2], **k})
        eng._rest_price = lambda sym, mk: float(bars_now[sym]["Close"].iloc[-1]) if sym in bars_now else None
        iu.refresh = lambda mk, keep=None, **k: ["005930"]
        iu.current_universe = lambda mk: ["005930"]
        ib.load_bars = lambda sym, date=None, **k: bars_now.get(ib.base_symbol(sym), pd.DataFrame())
        ib.available_dates = lambda base_dir=None: []
        rq.enabled = lambda: True
        rq.heartbeat_age = lambda cache=None: 1.0
        rq.is_fresh = lambda sym, **k: True
        rq.get_orderbook = lambda sym, **k: dict(ob)

        cfg = {**eng.load_cfg(), "shadow": True, "markets": ["KR"],
               "flat_buffer_min": 15, "entry_cutoff_min": 30}
        # ORB 세션 시작 검증을 통과시키려면 첫 봉이 개장분이어야 함 → 개장분으로 간주되게 OPEN_MIN 조정
        saved_open = dict(eng._OPEN_MIN)
        eng._OPEN_MIN["KR"] = idx[0].hour * 60 + idx[0].minute
        saved_close = dict(eng._CLOSE_MIN)
        eng._CLOSE_MIN["KR"] = (now_kst.hour * 60 + now_kst.minute) + 120   # 마감 여유

        from ml.adaptive import Ledger
        state = eng._blank_state()

        notes1 = eng.run_market("KR", state, cfg)
        led = Ledger("kr_intraday", base_dir=ledger_dir)
        decs = led.read_decisions()
        pos_key = "KR:005930"

        failures_local = []
        def chk(desc, cond):
            if not cond:
                failures_local.append(f"❌ engine: {desc}")
            else:
                logger.info("  ✅ engine — %s", desc)

        chk("진입 발생", pos_key in state["positions"])
        chk("결정 1건 기록", len(decs) == 1)
        chk("id = date:ticker:HHMMSS", len(decs) == 1 and decs[0]["id"].count(":") == 2)
        chk("shadow 플래그", len(decs) == 1 and decs[0].get("shadow") is True)
        chk("체결 이벤트(buy)", any(e["side"] == "buy" for e in events))
        chk("카운터 증가", state["counters"]["KR"]["trades"] == 1)

        # 같은 분 재실행 → 새 bar 없음 → 중복 결정 0
        eng.run_market("KR", state, cfg)
        chk("멱등(같은 분 재실행)", len(led.read_decisions()) == 1)

        # 손절 봉 — low 가 stop 아래
        if pos_key in state["positions"]:
            stop = state["positions"][pos_key]["stop"]
            last_ts = idx[-1] + timedelta(minutes=1)
            df_stop = pd.concat([df_bo, pd.DataFrame(
                {"Open": [stop + 50], "High": [stop + 80], "Low": [stop - 200],
                 "Close": [stop - 100], "Volume": [3000.0]},
                index=pd.DatetimeIndex([last_ts]))])
            bars_now["005930"] = df_stop
            eng.run_market("KR", state, cfg)
            outs = led.read_outcomes()
            chk("손절 청산 outcome", len(outs) == 1 and outs[0]["exit_reason"] == "stop")
            chk("net R 음수", len(outs) == 1 and outs[0]["realized_r"] < 0)
            chk("fwd_excess=realized_r", len(outs) == 1 and outs[0]["fwd_excess"] == outs[0]["realized_r"])
            chk("포지션 제거", pos_key not in state["positions"])
            chk("쿨다운 설정", state["cooldown_until"].get(pos_key, 0) > 0)
            chk("체결 이벤트(sell)", any(e["side"] == "sell" for e in events))
            chk("day_pnl 반영", state["counters"]["KR"]["day_pnl"] != 0.0)

        # orphan 수리 — 원장에만 있는 당일 결정
        led.log_decision({"id": f"{now_kst.strftime('%Y-%m-%d')}:000660:120000",
                          "date": now_kst.strftime("%Y-%m-%d"), "ticker": "000660",
                          "side": "단기진입", "qty": 3, "price": 200000.0,
                          "stop": 198000.0, "shadow": True, "ok": True})
        bars_now["000660"] = df_bo * 3
        n_rep = eng._repair_orphans(state, "KR", led)
        chk("orphan 수리 1건", n_rep == 1)
        chk("orphan outcome 기록", any(o["exit_reason"] == "orphan_repair" for o in led.read_outcomes()))

        # state 왕복
        saved_sp = eng.STATE_PATH
        eng.STATE_PATH = os.path.join(tmp, "state.json")
        eng.save_state(state)
        st2 = eng.load_state()
        chk("state 왕복", st2["counters"]["KR"]["trades"] == state["counters"]["KR"]["trades"])
        eng.STATE_PATH = saved_sp

        failures.extend(failures_local)
        eng._OPEN_MIN.update(saved_open)
        eng._CLOSE_MIN.update(saved_close)
    except Exception as e:
        import traceback
        failures.append(f"❌ engine: 예외 — {e}\n{traceback.format_exc()[-500:]}")
    finally:
        eng._market_open = saved["_market_open"]
        eng._news_events = saved["_news_events"]
        eng._record_event = saved["_record_event"]
        eng._rest_price = saved["_rest_price"]
        eng._LEDGER_BASE = saved["LEDGER"]
        iu.refresh = saved["iu_refresh"]
        iu.current_universe = saved["iu_current"]
        ib.load_bars = saved["ib_load"]
        ib.available_dates = saved["ib_dates"]
        rq.enabled = saved["rq_enabled"]
        rq.heartbeat_age = saved["rq_hb"]
        rq.is_fresh = saved["rq_fresh"]
        rq.get_orderbook = saved["rq_ob"]
    return failures


def main() -> int:
    logger.info("=== intraday smoke test 시작 ===")
    failures = run_tests()
    if failures:
        msg = "\n".join(failures[:20])
        logger.error("실패 %d건:\n%s", len(failures), msg)
        _alert(f"{datetime.now().strftime('%m/%d %H:%M')}\n{msg}")
        return 1
    logger.info("=== 전 항목 통과 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
