# Intraday Sample Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a KR/US intraday sample factory that records setup candidates and their forward outcomes while allowing only micro/normal shadow trades through stronger setup and cost gates.

**Architecture:** Add a small candidate ledger beside the existing append-only `ml.adaptive.Ledger`, then add setup detection/classification as a pure module consumed by `crons/intraday_mock_track.py`. The engine will record every detected setup as `observe_only`, `micro`, or `normal`, use micro/normal only for shadow entries, and a separate backfill task will label 5/15/30 minute candidate outcomes for learning.

**Tech Stack:** Python 3.11, pytest, pandas, existing `ml.intraday_axes`, `ml.intraday_policy`, `providers.intraday_bars`, and append-only JSONL ledgers under `~/reports/ml-data`.

## Global Constraints

- KR and US must run simultaneously through the existing `INTRADAY_MARKETS=kr,us` flow.
- Existing `kr_intraday_decisions/outcomes` and `us_intraday_decisions/outcomes` stay as actual shadow-entry ledgers.
- New candidate ledgers must be append-only and dedupe by deterministic id.
- Candidate samples must include non-traded `observe_only` rows so learning is not limited to losing entries.
- No real broker order path may be introduced; all entries remain shadow/mock only.
- Fast sampling is the objective, but daily loss and cost filters must prevent the current fee-burning pattern.
- Initial setup set is exactly `opening_range_breakout`, `vwap_reclaim`, and `volume_shock`.
- `signal_collapse` must not exit before `minimum_hold_min=3`, except hard stop, target, EOD, stale/halt flatten.
- Future extension should allow more setups and real data providers without rewriting the ledger schema.

---

## File Structure

- Create `ml/intraday_sample_factory.py`: pure setup detection, sample-mode classification, cost ratio calculation, and candidate/outcome id helpers.
- Create `ml/intraday_candidate_ledger.py`: append-only candidate and candidate-outcome JSONL ledger.
- Modify `crons/intraday_mock_track.py`: record candidates during the existing entry scan; use `sample_mode` to decide whether to call `_do_entry`; add minimum hold support for soft exits.
- Create `crons/intraday_candidate_backfill.py`: read pending candidates and write 5/15/30 minute outcome rows from stored intraday bars.
- Modify `ml/intraday_axes.py`: add optional `minimum_hold_min` behavior to `check_exit` for `signal_collapse` only.
- Create `tests/test_intraday_sample_factory.py`: pure unit tests for setup detection and mode classification.
- Create `tests/test_intraday_candidate_ledger.py`: append-only/dedupe tests for candidate ledgers.
- Modify `tests/test_intraday_leverage.py`: integration tests for candidate recording and soft-exit minimum hold.
- Create `tests/test_intraday_candidate_backfill.py`: outcome backfill tests for horizons and idempotency.

---

### Task 1: Candidate Ledger

**Files:**
- Create: `ml/intraday_candidate_ledger.py`
- Test: `tests/test_intraday_candidate_ledger.py`

**Interfaces:**
- Consumes: standard Python `json`, `pathlib.Path`.
- Produces:
  - `class CandidateLedger(market: str, base_dir: Path | None = None)`
  - `CandidateLedger.log_candidate(rec: dict) -> str`
  - `CandidateLedger.log_outcome(rec: dict) -> None`
  - `CandidateLedger.read_candidates() -> list[dict]`
  - `CandidateLedger.read_outcomes() -> list[dict]`
  - `CandidateLedger.pending(horizons: tuple[int, ...] = (5, 15, 30)) -> list[tuple[dict, int]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_intraday_candidate_ledger.py`:

```python
from pathlib import Path


def test_candidate_ledger_dedupes_candidates_and_outcomes(tmp_path):
    from ml.intraday_candidate_ledger import CandidateLedger

    ledger = CandidateLedger("kr", base_dir=tmp_path)
    rec = {
        "id": "2026-07-22:KR:005930:101500:vwap_reclaim",
        "date": "2026-07-22",
        "market": "KR",
        "ticker": "005930",
        "setup_type": "vwap_reclaim",
        "sample_mode": "observe_only",
        "entry_price": 73000.0,
        "bar_ts": "2026-07-22T10:15:00+09:00",
    }

    assert ledger.log_candidate(rec) == rec["id"]
    assert ledger.log_candidate(dict(rec)) == rec["id"]
    assert len(ledger.read_candidates()) == 1

    outcome = {
        "candidate_id": rec["id"],
        "horizon_min": 15,
        "entry_price": 73000.0,
        "exit_price": 73800.0,
        "gross_return": 0.010959,
        "net_return_est": 0.0067,
        "mfe": 0.014,
        "mae": -0.004,
        "success": True,
    }
    ledger.log_outcome(outcome)
    ledger.log_outcome(dict(outcome))

    assert len(ledger.read_outcomes()) == 1
    assert ledger.pending((5, 15)) == [(rec, 5)]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_intraday_candidate_ledger.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ml.intraday_candidate_ledger'`.

- [ ] **Step 3: Write minimal implementation**

Create `ml/intraday_candidate_ledger.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

_DATA_DIR = Path(os.path.expanduser("~/reports/ml-data"))


class CandidateLedger:
    def __init__(self, market: str, base_dir: Path | None = None):
        mk = str(market or "").lower()
        if mk not in {"kr", "us"}:
            raise ValueError("market must be kr or us")
        self.market = mk
        self.base = Path(base_dir) if base_dir else _DATA_DIR
        self.candidates_path = self.base / f"{mk}_intraday_candidates.jsonl"
        self.outcomes_path = self.base / f"{mk}_intraday_candidate_outcomes.jsonl"

    @staticmethod
    def _read_lines(path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    @staticmethod
    def _append_line(path: Path, rec: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def read_candidates(self) -> list[dict]:
        return self._read_lines(self.candidates_path)

    def read_outcomes(self) -> list[dict]:
        return self._read_lines(self.outcomes_path)

    def log_candidate(self, rec: dict) -> str:
        cid = str(rec.get("id") or "")
        if not cid:
            raise ValueError("candidate id required")
        existing = {row.get("id") for row in self.read_candidates()}
        if cid not in existing:
            self._append_line(self.candidates_path, {**rec, "id": cid})
        return cid

    def log_outcome(self, rec: dict) -> None:
        cid = str(rec.get("candidate_id") or "")
        horizon = rec.get("horizon_min")
        if not cid or horizon is None:
            raise ValueError("candidate_id and horizon_min required")
        done = {(row.get("candidate_id"), row.get("horizon_min")) for row in self.read_outcomes()}
        if (cid, horizon) not in done:
            self._append_line(self.outcomes_path, rec)

    def pending(self, horizons: tuple[int, ...] = (5, 15, 30)) -> list[tuple[dict, int]]:
        done = {(row.get("candidate_id"), row.get("horizon_min")) for row in self.read_outcomes()}
        out = []
        for rec in self.read_candidates():
            cid = rec.get("id")
            for h in horizons:
                if (cid, h) not in done:
                    out.append((rec, h))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_intraday_candidate_ledger.py -q
```

Expected: PASS, `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add ml/intraday_candidate_ledger.py tests/test_intraday_candidate_ledger.py
git commit -m "add) 단기 후보 원장 추가" -m "단기투자 표본 수집을 위해 KR/US 후보와 후보 결과를 append-only JSONL로 기록하는 CandidateLedger를 추가했습니다.\n\n성과는 실제 shadow 진입을 하지 않은 observe_only 후보도 학습 표본으로 남길 수 있는 기반을 만든 점입니다. trade-off는 아직 후보 생성과 outcome 백필이 연결되지 않아 원장 단위 기능만 먼저 제공한다는 점입니다."
```

---

### Task 2: Setup Detection And Sample Mode Classification

**Files:**
- Create: `ml/intraday_sample_factory.py`
- Test: `tests/test_intraday_sample_factory.py`

**Interfaces:**
- Consumes: feature dict from `crons.intraday_mock_track._symbol_axes`, orderbook spread, and candidate metadata.
- Produces:
  - `detect_setups(axes: dict, bars, *, market: str) -> list[dict]`
  - `estimated_cost_per_share(price: float, market: str, spread: float | None) -> float`
  - `classify_sample(setup: dict, *, market: str, confirm_bars: int, expected_move: float, estimated_cost: float) -> dict`
  - `candidate_id(date: str, market: str, ticker: str, epoch_min: int, setup_type: str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_intraday_sample_factory.py`:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


def _df(closes, volumes=None, tz="Asia/Seoul"):
    idx = pd.date_range(datetime(2026, 7, 22, 9, 30, tzinfo=ZoneInfo(tz)), periods=len(closes), freq="min")
    rows = []
    volumes = volumes or [1000] * len(closes)
    for close, vol in zip(closes, volumes):
        rows.append({"Open": close - 1, "High": close + 2, "Low": close - 2, "Close": close, "Volume": vol})
    return pd.DataFrame(rows, index=idx)


def test_detects_opening_range_breakout():
    from ml.intraday_sample_factory import detect_setups

    bars = _df([100, 101, 102, 103, 104, 105, 106, 107, 108, 112], [100] * 9 + [5000])
    axes = {"orb": 1.0, "vwap": 0.8, "volspike": 1.0, "_meta": {"close": 112.0, "atr": 1.5}}

    setups = detect_setups(axes, bars, market="KR")

    assert setups[0]["setup_type"] == "opening_range_breakout"
    assert setups[0]["expected_move"] > 0
    assert setups[0]["confirm_bars"] >= 1


def test_classify_sample_observe_micro_normal_thresholds():
    from ml.intraday_sample_factory import classify_sample

    setup = {"setup_type": "vwap_reclaim", "expected_move": 100.0}

    observe = classify_sample(setup, market="KR", confirm_bars=0, expected_move=100.0, estimated_cost=60.0)
    micro = classify_sample(setup, market="KR", confirm_bars=1, expected_move=100.0, estimated_cost=40.0)
    normal = classify_sample(setup, market="KR", confirm_bars=2, expected_move=100.0, estimated_cost=20.0)

    assert observe["sample_mode"] == "observe_only"
    assert "confirm_bars_lt_1" in observe["blocked_by"]
    assert micro["sample_mode"] == "micro"
    assert normal["sample_mode"] == "normal"
    assert normal["cost_ratio"] == 5.0


def test_candidate_id_is_deterministic_and_market_scoped():
    from ml.intraday_sample_factory import candidate_id

    assert candidate_id("2026-07-22", "KR", "005930", 12345, "vwap_reclaim") == "2026-07-22:KR:005930:12345:vwap_reclaim"
    assert candidate_id("2026-07-22", "us", "TQQQ", 12345, "volume_shock") == "2026-07-22:US:TQQQ:12345:volume_shock"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_intraday_sample_factory.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ml.intraday_sample_factory'`.

- [ ] **Step 3: Write minimal implementation**

Create `ml/intraday_sample_factory.py`:

```python
from __future__ import annotations

from typing import Any


def candidate_id(date: str, market: str, ticker: str, epoch_min: int, setup_type: str) -> str:
    return f"{date}:{str(market).upper()}:{str(ticker).upper()}:{int(epoch_min)}:{setup_type}"


def estimated_cost_per_share(price: float, market: str, spread: float | None) -> float:
    from ml import intraday_axes as ax
    return ax.friction_per_share(float(price), str(market).upper(), spread=spread)


def _last_close(bars) -> float | None:
    if bars is None or getattr(bars, "empty", True):
        return None
    return float(bars["Close"].iloc[-1])


def _prior_high(bars, n: int = 15) -> float | None:
    if bars is None or getattr(bars, "empty", True) or len(bars) < 2:
        return None
    prior = bars.iloc[:-1].tail(n)
    if prior.empty:
        return None
    return float(prior["High"].max())


def _confirm_bars_above(bars, level: float, n: int = 2) -> int:
    if bars is None or getattr(bars, "empty", True):
        return 0
    count = 0
    for value in reversed([float(x) for x in bars["Close"].tail(n)]):
        if value > level:
            count += 1
        else:
            break
    return count


def detect_setups(axes: dict, bars, *, market: str) -> list[dict]:
    meta = axes.get("_meta") or {}
    close = float(meta.get("close") or _last_close(bars) or 0.0)
    atr = float(meta.get("atr") or 0.0)
    if close <= 0 or atr <= 0:
        return []
    setups: list[dict[str, Any]] = []
    prior_high = _prior_high(bars, 15)
    if prior_high and close > prior_high and (axes.get("volspike") or 0) >= 0.7 and (axes.get("vwap") or 0) >= 0.6:
        setups.append({
            "setup_type": "opening_range_breakout",
            "expected_move": max(atr * 1.5, close - prior_high),
            "confirm_bars": _confirm_bars_above(bars, prior_high, 2),
            "reason": "range_breakout_with_volume",
        })
    if (axes.get("vwap") or 0) >= 0.7 and (axes.get("rsi") is None or axes.get("rsi") >= 0.3):
        setups.append({
            "setup_type": "vwap_reclaim",
            "expected_move": atr * 1.2,
            "confirm_bars": 1 if (axes.get("vwap") or 0) >= 0.7 else 0,
            "reason": "vwap_reclaim_or_hold",
        })
    if (axes.get("volspike") or 0) >= 0.9:
        setups.append({
            "setup_type": "volume_shock",
            "expected_move": atr * 1.0,
            "confirm_bars": 1,
            "reason": "volume_spike_momentum",
        })
    return setups


def _thresholds(market: str) -> dict:
    mk = str(market or "").upper()
    if mk == "KR":
        return {"micro_cost": 2.5, "normal_cost": 4.5}
    return {"micro_cost": 2.0, "normal_cost": 4.0}


def classify_sample(setup: dict, *, market: str, confirm_bars: int,
                    expected_move: float, estimated_cost: float) -> dict:
    expected = max(float(expected_move or 0.0), 0.0)
    cost = max(float(estimated_cost or 0.0), 1e-9)
    cost_ratio = round(expected / cost, 4)
    th = _thresholds(market)
    blocked = []
    mode = "observe_only"
    if confirm_bars < 1:
        blocked.append("confirm_bars_lt_1")
    if cost_ratio < th["micro_cost"]:
        blocked.append("cost_ratio_lt_micro")
    if not blocked:
        mode = "micro"
    normal_blocked = []
    if confirm_bars < 2:
        normal_blocked.append("confirm_bars_lt_2")
    if cost_ratio < th["normal_cost"]:
        normal_blocked.append("cost_ratio_lt_normal")
    if not normal_blocked:
        mode = "normal"
    return {**setup, "sample_mode": mode, "confirm_bars": confirm_bars,
            "expected_move": round(expected, 6), "estimated_cost": round(cost, 6),
            "cost_ratio": cost_ratio, "blocked_by": blocked + normal_blocked}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_intraday_sample_factory.py -q
```

Expected: PASS, `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add ml/intraday_sample_factory.py tests/test_intraday_sample_factory.py
git commit -m "add) 단기 셋업 표본 분류기 추가" -m "단기투자 표본을 셋업 단위로 쌓기 위해 ORB, VWAP reclaim, volume shock 감지와 observe/micro/normal 분류를 추가했습니다.\n\n성과는 기존 점수 단일 진입보다 셋업별 후보를 분리해 기록할 수 있게 된 점입니다. trade-off는 첫 버전이 보수적인 휴리스틱이며 실제 성능 조정은 후보 outcome 누적 이후에 해야 한다는 점입니다."
```

---

### Task 3: Candidate Recording In The Intraday Engine

**Files:**
- Modify: `crons/intraday_mock_track.py`
- Test: `tests/test_intraday_leverage.py`

**Interfaces:**
- Consumes:
  - `ml.intraday_sample_factory.detect_setups(axes, bars, market=mk)`
  - `ml.intraday_sample_factory.classify_sample(...)`
  - `ml.intraday_candidate_ledger.CandidateLedger`
- Produces: each detected setup writes one candidate row; only `micro` and `normal` may call `_do_entry`.

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_intraday_leverage.py`:

```python
def test_intraday_engine_records_observe_only_candidate_without_entry(monkeypatch, tmp_path):
    from crons import intraday_mock_track as eng
    from providers import intraday_bars as ib
    from providers import intraday_universe as iu
    from providers import realtime_quotes as rq
    import ml.intraday_signal as intraday_signal

    bars_df = _bars("005930", price=70000.0, tz="Asia/Seoul")
    monkeypatch.setattr(eng, "_LEDGER_BASE", str(tmp_path / "ledger"))
    monkeypatch.setattr(eng, "_market_open", lambda mk: mk == "KR")
    monkeypatch.setattr(eng, "_news_events", lambda now_epoch: [])
    monkeypatch.setattr(eng, "_record_event", lambda *a, **k: None)
    monkeypatch.setattr(iu, "refresh", lambda mk, keep=None, **k: ["005930"])
    monkeypatch.setattr(ib, "load_bars", lambda sym, date=None, **k: bars_df)
    monkeypatch.setattr(ib, "available_dates", lambda base_dir=None: [])
    monkeypatch.setattr(intraday_signal, "compute_intraday_features",
                        lambda df: pd.DataFrame({"atr": [10.0] * len(df)}, index=df.index))
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "heartbeat_age", lambda cache=None: 1.0)
    monkeypatch.setattr(rq, "is_fresh", lambda sym, **k: True)
    monkeypatch.setattr(rq, "get_orderbook", lambda sym: {"best_bid": 69990.0, "best_ask": 70010.0, "bids": [], "asks": []})

    def fake_axes(sym, mk, df, feats, **_kwargs):
        return {
            "orb": None, "vwap": 0.75, "volspike": 0.2, "ofi": 0.0,
            "news": None, "ema": 0.5, "rsi": 0.4, "bb": 0.3,
            "_meta": {"close": 70000.0, "atr": 10.0, "bar_ts": df.index[-1].isoformat(),
                      "epoch_min": int(df.index[-1].timestamp() // 60), "symbol": sym,
                      "vol_z_tod": 1.0, "regime_er": 0.2},
        }

    monkeypatch.setattr(eng, "_symbol_axes", fake_axes)
    monkeypatch.setitem(eng._OPEN_MIN, "KR", bars_df.index[0].hour * 60 + bars_df.index[0].minute)
    monkeypatch.setitem(eng._CLOSE_MIN, "KR", bars_df.index[-1].hour * 60 + bars_df.index[-1].minute + 180)

    cfg = {**eng.load_cfg(), "markets": ["KR"], "shadow": True,
           "sleeve_frac": 0.20, "risk_per_trade": 0.02, "daily_loss_halt": 0.01,
           "min_notional": {"KR": 0.0, "US": 0.0}, "entry_cutoff_min": 30,
           "candidate_base_dir": str(tmp_path / "candidates")}
    state = eng._blank_state()

    eng.run_market("KR", state, cfg)

    from ml.intraday_candidate_ledger import CandidateLedger
    rows = CandidateLedger("kr", base_dir=tmp_path / "candidates").read_candidates()
    assert len(rows) == 1
    assert rows[0]["setup_type"] == "vwap_reclaim"
    assert rows[0]["sample_mode"] == "observe_only"
    assert state["positions"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_intraday_leverage.py::test_intraday_engine_records_observe_only_candidate_without_entry -q
```

Expected: FAIL because no candidate row is recorded.

- [ ] **Step 3: Add config keys**

Modify `crons/intraday_mock_track.py` in `load_cfg()` to include:

```python
        "candidate_base_dir": os.getenv("INTRADAY_CANDIDATE_BASE_DIR", ""),
        "minimum_hold_min": int(_env_f("INTRADAY_MINIMUM_HOLD_MIN", 3)),
        "micro_risk_mult": {
            "KR": _env_f("INTRADAY_MICRO_RISK_MULT_KR", _env_f("INTRADAY_MICRO_RISK_MULT", 0.10)),
            "US": _env_f("INTRADAY_MICRO_RISK_MULT_US", _env_f("INTRADAY_MICRO_RISK_MULT", 0.10)),
        },
```

- [ ] **Step 4: Record candidates before entry gating**

In `run_market()`, after `trade_axes` is produced and before `cands.append(...)`, add candidate setup creation. Store candidates as tuples with setup info:

```python
        from ml import intraday_sample_factory as sf
        ob = obs.get(trade_sym)
        sp_abs = None
        if ob and ob.get("best_bid") and ob.get("best_ask"):
            sp_abs = float(ob["best_ask"]) - float(ob["best_bid"])
        setups = sf.detect_setups(trade_axes, bars.get(trade_sym), market=mk)
        for setup in setups:
            estimated_cost = sf.estimated_cost_per_share(trade_axes["_meta"]["close"], mk, sp_abs)
            sample = sf.classify_sample(
                setup,
                market=mk,
                confirm_bars=int(setup.get("confirm_bars") or 0),
                expected_move=float(setup.get("expected_move") or 0.0),
                estimated_cost=estimated_cost,
            )
            sample["id"] = sf.candidate_id(today, mk, trade_sym, trade_axes["_meta"].get("epoch_min") or ep, sample["setup_type"])
            sample.update({
                "date": today,
                "market": mk,
                "ticker": trade_sym,
                "signal_ticker": signal_sym if signal_sym != trade_sym else None,
                "bar_ts": trade_axes["_meta"].get("bar_ts"),
                "entry_price": trade_axes["_meta"].get("close"),
                "score": round(score, 4),
                "features": {k: v for k, v in trade_axes.items() if k != "_meta"},
            })
            try:
                from ml.intraday_candidate_ledger import CandidateLedger
                base_dir = cfg.get("candidate_base_dir") or None
                CandidateLedger(mk.lower(), base_dir=base_dir).log_candidate(sample)
            except Exception as e:
                logger.warning("[%s] candidate 기록 실패 %s: %s", mk, trade_sym, e)
            if sample["sample_mode"] in {"micro", "normal"}:
                cands.append((score, signal_sym, trade_sym, trade_axes, sample))
```

Then remove or replace the old `cands.append((score, sym, trade_sym, trade_axes))`.

- [ ] **Step 5: Use sample mode for risk multiplier**

Update the entry loop signature:

```python
    for score, signal_sym, sym, axes, sample in sorted(cands, reverse=True, key=lambda x: x[0]):
```

Replace `_entry_risk_mult(...)` with:

```python
        if sample.get("sample_mode") == "normal":
            risk_mult, entry_mode = 1.0, "normal"
        elif sample.get("sample_mode") == "micro":
            risk_mult = float(_cfg_market_value(cfg, "micro_risk_mult", mk, 0.10))
            entry_mode = "micro"
        else:
            continue
```

- [ ] **Step 6: Run focused test**

Run:

```bash
uv run pytest tests/test_intraday_leverage.py::test_intraday_engine_records_observe_only_candidate_without_entry -q
```

Expected: PASS.

- [ ] **Step 7: Run existing leverage tests**

Run:

```bash
uv run pytest tests/test_intraday_leverage.py -q
```

Expected: PASS. If `test_entry_risk_mult_opens_explore_band` still asserts old explore behavior, keep `_entry_risk_mult` intact for backward compatibility but ensure the engine no longer uses it for candidate-based entries.

- [ ] **Step 8: Commit**

```bash
git add crons/intraday_mock_track.py tests/test_intraday_leverage.py
git commit -m "fix) 단기 엔진 후보 표본 기록 연결" -m "단기투자 엔진이 KR/US 셋업 후보를 candidate ledger에 기록하고, micro/normal 후보만 shadow 진입으로 연결되도록 바꿨습니다.\n\n성과는 observe_only 후보도 학습 표본으로 누적하면서 기존 실제 진입 원장은 유지한 점입니다. trade-off는 첫 통합 단계에서 후보 감지 휴리스틱이 단순하고, 대시보드 표시는 아직 별도 작업으로 남는다는 점입니다."
```

---

### Task 4: Minimum Hold For Soft Signal Collapse

**Files:**
- Modify: `ml/intraday_axes.py`
- Modify: `crons/intraday_mock_track.py`
- Test: `tests/test_intraday_leverage.py`

**Interfaces:**
- Consumes: `pos["entry_min"]`, current `now_min`, and cfg `minimum_hold_min`.
- Produces: `check_exit()` ignores `signal_collapse` until minimum hold has elapsed; hard stop and target still trigger immediately.

- [ ] **Step 1: Write failing unit test**

Append to `tests/test_intraday_leverage.py`:

```python
def test_signal_collapse_waits_until_minimum_hold_but_stop_does_not():
    from ml import intraday_axes as ax

    pos = {"entry_price": 100.0, "stop": 95.0, "target": 110.0, "entry_min": 600, "risk_per_share": 5.0}
    cfg = {"theta_exit": 0.25, "timestop_min": 90, "flat_buffer_min": 15, "minimum_hold_min": 3}

    assert ax.check_exit(pos, {"h": 101.0, "l": 99.0, "c": 100.0}, 0.1, 601, 960, cfg) is None
    assert ax.check_exit(pos, {"h": 101.0, "l": 99.0, "c": 100.0}, 0.1, 603, 960, cfg) == ("signal_collapse", 100.0)
    assert ax.check_exit(pos, {"h": 100.0, "l": 94.0, "c": 94.5}, 0.8, 601, 960, cfg) == ("stop", 94.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_intraday_leverage.py::test_signal_collapse_waits_until_minimum_hold_but_stop_does_not -q
```

Expected: FAIL because `signal_collapse` fires before 3 minutes.

- [ ] **Step 3: Modify `check_exit()`**

In `ml/intraday_axes.py`, change:

```python
        if score is not None and score < float(cfg.get("theta_exit", 0.25)):
            return "signal_collapse", c
```

to:

```python
        min_hold = int(cfg.get("minimum_hold_min", 0) or 0)
        if score is not None and score < float(cfg.get("theta_exit", 0.25)) and held_min >= min_hold:
            return "signal_collapse", c
```

- [ ] **Step 4: Pass `minimum_hold_min` from engine**

In `crons/intraday_mock_track.py`, update `cfg_exit`:

```python
        cfg_exit = {"timestop_min": params.get("timestop_min", 90),
                    "theta_exit": params.get("theta_exit", 0.25),
                    "flat_buffer_min": cfg["flat_buffer_min"],
                    "minimum_hold_min": cfg.get("minimum_hold_min", 3)}
```

- [ ] **Step 5: Run focused and full intraday tests**

Run:

```bash
uv run pytest tests/test_intraday_leverage.py::test_signal_collapse_waits_until_minimum_hold_but_stop_does_not -q
uv run pytest tests/test_intraday_leverage.py tests/test_intraday_signal.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ml/intraday_axes.py crons/intraday_mock_track.py tests/test_intraday_leverage.py
git commit -m "fix) 단기 신호붕괴 청산 최소 보유시간 적용" -m "단기투자 signal_collapse 청산이 진입 직후 0~1분에 반복되지 않도록 minimum_hold_min을 추가했습니다.\n\n성과는 hard stop과 target은 즉시 유지하면서 soft signal collapse만 최소 보유 이후에 작동하게 만든 점입니다. trade-off는 일부 빠른 실패 포지션이 몇 분 더 남아 있을 수 있어 리스크 예산 관리가 더 중요해진다는 점입니다."
```

---

### Task 5: Candidate Outcome Backfill

**Files:**
- Create: `crons/intraday_candidate_backfill.py`
- Test: `tests/test_intraday_candidate_backfill.py`

**Interfaces:**
- Consumes: `CandidateLedger.pending()`, `providers.intraday_bars.load_bars(ticker)`.
- Produces:
  - `build_candidate_outcome(candidate: dict, bars, horizon_min: int) -> dict | None`
  - `run_market(market: str, base_dir: Path | None = None, horizons: tuple[int, ...] = (5, 15, 30)) -> int`

- [ ] **Step 1: Write failing test**

Create `tests/test_intraday_candidate_backfill.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


def test_candidate_backfill_writes_forward_returns(tmp_path, monkeypatch):
    from ml.intraday_candidate_ledger import CandidateLedger

    ledger = CandidateLedger("kr", base_dir=tmp_path)
    cid = ledger.log_candidate({
        "id": "2026-07-22:KR:005930:600:vwap_reclaim",
        "date": "2026-07-22",
        "market": "KR",
        "ticker": "005930",
        "setup_type": "vwap_reclaim",
        "sample_mode": "observe_only",
        "entry_price": 100.0,
        "estimated_cost": 0.2,
        "bar_ts": "2026-07-22T09:30:00+09:00",
    })

    idx = pd.date_range(datetime(2026, 7, 22, 9, 30, tzinfo=ZoneInfo("Asia/Seoul")), periods=20, freq="min")
    bars = pd.DataFrame({
        "Open": [100.0] * 20,
        "High": [101.0 + i * 0.1 for i in range(20)],
        "Low": [99.5] * 20,
        "Close": [100.0 + i * 0.2 for i in range(20)],
        "Volume": [1000] * 20,
    }, index=idx)

    import crons.intraday_candidate_backfill as backfill
    monkeypatch.setattr(backfill, "_load_bars", lambda ticker, market: bars)

    assert backfill.run_market("kr", base_dir=tmp_path, horizons=(5, 15)) == 2
    assert backfill.run_market("kr", base_dir=tmp_path, horizons=(5, 15)) == 0

    rows = ledger.read_outcomes()
    assert {(r["candidate_id"], r["horizon_min"]) for r in rows} == {(cid, 5), (cid, 15)}
    assert rows[0]["gross_return"] > 0
    assert rows[0]["net_return_est"] < rows[0]["gross_return"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_intraday_candidate_backfill.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'crons.intraday_candidate_backfill'`.

- [ ] **Step 3: Implement backfill script**

Create `crons/intraday_candidate_backfill.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_bars(ticker: str, market: str):
    from providers import intraday_bars
    return intraday_bars.load_bars(ticker)


def _parse_ts(value: str):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def build_candidate_outcome(candidate: dict, bars, horizon_min: int) -> dict | None:
    if bars is None or getattr(bars, "empty", True):
        return None
    ts = _parse_ts(candidate.get("bar_ts"))
    entry = float(candidate.get("entry_price") or 0.0)
    if ts is None or entry <= 0:
        return None
    future = bars[bars.index >= ts]
    if len(future) <= horizon_min:
        return None
    window = future.iloc[: horizon_min + 1]
    exit_price = float(window["Close"].iloc[-1])
    gross_return = exit_price / entry - 1.0
    estimated_cost = float(candidate.get("estimated_cost") or 0.0)
    net_return_est = (exit_price - entry - estimated_cost) / entry
    mfe = float(window["High"].max()) / entry - 1.0
    mae = float(window["Low"].min()) / entry - 1.0
    return {
        "candidate_id": candidate["id"],
        "horizon_min": int(horizon_min),
        "entry_price": round(entry, 6),
        "exit_price": round(exit_price, 6),
        "gross_return": round(gross_return, 6),
        "net_return_est": round(net_return_est, 6),
        "mfe": round(mfe, 6),
        "mae": round(mae, 6),
        "success": bool(net_return_est > 0),
    }


def run_market(market: str, base_dir: Path | None = None, horizons: tuple[int, ...] = (5, 15, 30)) -> int:
    from ml.intraday_candidate_ledger import CandidateLedger
    ledger = CandidateLedger(market, base_dir=base_dir)
    added = 0
    cache = {}
    for candidate, horizon in ledger.pending(horizons):
        ticker = candidate.get("ticker")
        if not ticker:
            continue
        key = (ticker, market)
        if key not in cache:
            cache[key] = _load_bars(ticker, market)
        outcome = build_candidate_outcome(candidate, cache[key], horizon)
        if outcome is None:
            continue
        ledger.log_outcome(outcome)
        added += 1
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", default="kr,us")
    args = parser.parse_args(argv)
    total = 0
    for market in [m.strip().lower() for m in args.markets.split(",") if m.strip()]:
        if market in {"kr", "us"}:
            total += run_market(market)
    print(f"candidate outcomes added={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_intraday_candidate_backfill.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crons/intraday_candidate_backfill.py tests/test_intraday_candidate_backfill.py
git commit -m "add) 단기 후보 결과 백필 추가" -m "단기 후보 표본의 5/15/30분 forward outcome을 append-only로 백필하는 크론 스크립트를 추가했습니다.\n\n성과는 실제 진입하지 않은 observe_only 후보도 학습 가능한 결과 데이터로 전환되는 점입니다. trade-off는 첫 버전이 로컬 분봉 저장분에 의존하므로 데이터가 부족한 후보는 다음 실행까지 pending으로 남는다는 점입니다."
```

---

### Task 6: Learning Summary By Setup

**Files:**
- Modify: `crons/intraday_mock_learn.py`
- Test: `tests/test_intraday_candidate_backfill.py` or create `tests/test_intraday_candidate_learning.py`

**Interfaces:**
- Consumes: `CandidateLedger.read_candidates()` and `read_outcomes()`.
- Produces: `candidate_setup_summary(market: str, base_dir: Path | None = None) -> dict` for reporting setup-level sample counts and average returns.

- [ ] **Step 1: Write failing test**

Create `tests/test_intraday_candidate_learning.py`:

```python
def test_candidate_setup_summary_groups_by_setup_and_mode(tmp_path):
    from ml.intraday_candidate_ledger import CandidateLedger
    from crons.intraday_mock_learn import candidate_setup_summary

    ledger = CandidateLedger("kr", base_dir=tmp_path)
    ledger.log_candidate({"id": "c1", "setup_type": "vwap_reclaim", "sample_mode": "observe_only"})
    ledger.log_candidate({"id": "c2", "setup_type": "vwap_reclaim", "sample_mode": "micro"})
    ledger.log_candidate({"id": "c3", "setup_type": "volume_shock", "sample_mode": "normal"})
    ledger.log_outcome({"candidate_id": "c1", "horizon_min": 15, "net_return_est": 0.01, "success": True})
    ledger.log_outcome({"candidate_id": "c2", "horizon_min": 15, "net_return_est": -0.02, "success": False})

    summary = candidate_setup_summary("kr", base_dir=tmp_path)

    assert summary["vwap_reclaim"]["n_candidates"] == 2
    assert summary["vwap_reclaim"]["n_outcomes_15m"] == 2
    assert summary["vwap_reclaim"]["avg_net_return_15m"] == -0.005
    assert summary["vwap_reclaim"]["modes"] == {"observe_only": 1, "micro": 1}
    assert summary["volume_shock"]["n_candidates"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_intraday_candidate_learning.py -q
```

Expected: FAIL because `candidate_setup_summary` does not exist.

- [ ] **Step 3: Implement summary function**

Add to `crons/intraday_mock_learn.py` near the gate helpers:

```python
def candidate_setup_summary(market: str, base_dir=None) -> dict:
    from collections import Counter, defaultdict
    from ml.intraday_candidate_ledger import CandidateLedger

    ledger = CandidateLedger(market, base_dir=base_dir)
    candidates = ledger.read_candidates()
    outcomes = ledger.read_outcomes()
    by_id = {row.get("id"): row for row in candidates}
    out = defaultdict(lambda: {"n_candidates": 0, "modes": Counter(), "n_outcomes_15m": 0, "avg_net_return_15m": None})
    returns_15 = defaultdict(list)
    for row in candidates:
        setup = row.get("setup_type") or "unknown"
        out[setup]["n_candidates"] += 1
        out[setup]["modes"][row.get("sample_mode") or "unknown"] += 1
    for row in outcomes:
        if row.get("horizon_min") != 15:
            continue
        candidate = by_id.get(row.get("candidate_id")) or {}
        setup = candidate.get("setup_type") or "unknown"
        if row.get("net_return_est") is not None:
            returns_15[setup].append(float(row["net_return_est"]))
    final = {}
    for setup, data in out.items():
        vals = returns_15.get(setup, [])
        final[setup] = {**data, "modes": dict(data["modes"]),
                        "n_outcomes_15m": len(vals),
                        "avg_net_return_15m": round(sum(vals) / len(vals), 6) if vals else None}
    return final
```

- [ ] **Step 4: Include summary in weekly report line**

In `run_market()`, after `gate = gate_eval(rows, market)`, add:

```python
    candidate_summary = candidate_setup_summary(market)
```

When recording learning, include:

```python
"candidate_summary": candidate_summary,
```

Append a compact text line to the return message:

```python
    setup_line = " · ".join(
        f"{k} n{v['n_candidates']}/15m {v['avg_net_return_15m']}"
        for k, v in sorted(candidate_summary.items())[:4]
    )
```

Then include `\n후보셋업 {setup_line}` if `setup_line` is non-empty.

- [ ] **Step 5: Run focused test**

Run:

```bash
uv run pytest tests/test_intraday_candidate_learning.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add crons/intraday_mock_learn.py tests/test_intraday_candidate_learning.py
git commit -m "add) 단기 후보 셋업별 학습 요약 추가" -m "단기 후보 표본을 setup_type과 sample_mode별로 집계해 주간 학습 리포트에 연결할 수 있게 했습니다.\n\n성과는 실제 진입 원장과 별개로 observe/micro/normal 후보의 15분 기대 성과를 비교할 수 있게 된 점입니다. trade-off는 첫 요약이 평균 수익률 중심이라 충분한 표본 전에는 통계적 유의성 판단까지 하지는 않는다는 점입니다."
```

---

### Task 7: Final Verification And Deployment Prep

**Files:**
- No new files unless previous tasks require docs updates.
- Test: all touched tests.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified branch ready for push/deploy.

- [ ] **Step 1: Run focused regression tests**

Run:

```bash
uv run pytest tests/test_intraday_candidate_ledger.py tests/test_intraday_sample_factory.py tests/test_intraday_candidate_backfill.py tests/test_intraday_candidate_learning.py tests/test_intraday_leverage.py tests/test_intraday_signal.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run smoke script if feasible**

Run:

```bash
uv run python tests/intraday_smoke_test.py
```

Expected: exit code 0. If it requires unavailable live services, record the exact failure and keep the focused tests as the verified baseline.

- [ ] **Step 3: Run syntax checks**

Run:

```bash
python3 -m py_compile ml/intraday_candidate_ledger.py ml/intraday_sample_factory.py crons/intraday_candidate_backfill.py crons/intraday_mock_track.py crons/intraday_mock_learn.py ml/intraday_axes.py
```

Expected: exit code 0.

- [ ] **Step 4: Remove test-generated `uv.lock` if it appears untracked**

Run:

```bash
git status --short
```

If it shows `?? uv.lock`, run:

```bash
rm uv.lock
```

Then run `git status --short` again.

- [ ] **Step 5: Push current branch**

```bash
git push origin codex/llm-console-fallback-fixes
```

- [ ] **Step 6: Optional production deployment after user approval**

Only if the user explicitly asks to deploy:

```bash
vercel deploy --prod --yes --project stock-report
```

Then verify:

```bash
curl -I -L https://stock-report-bice.vercel.app/mock-invest
```

Expected: HTTP 200.
