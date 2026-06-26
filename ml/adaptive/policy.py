"""
policy.py — 적응형 정책 파라미터 저장/로드/클램프.

정책 = 의사결정을 좌우하는 수치 파라미터 묶음(가중치·임계값). 학습기가 갱신하고
의사결정 코드가 소비한다. 핵심 안전장치는 **clamp** — 학습기가 무슨 값을 내든
정의된 안전 범위(bounds)를 벗어나지 못한다.

패턴 출처: ml/entry_analyzer.get_score_params(TTL 캐시+기본 폴백),
           barbell_strategy.leverage_dca_guard(범위 클램프), ml/_safe_cache(디렉터리 하드닝).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache"))


def _harden(directory: Path) -> None:
    try:
        from ml._safe_cache import harden_cache_dir
        harden_cache_dir(directory)
    except Exception:
        directory.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, obj: dict) -> None:
    try:
        import safe_io
        safe_io.atomic_write_json(str(path), obj)
    except Exception:
        # 폴백: temp→rename (safe_io 미가용 환경)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


class Policy:
    """이름 붙은 정책 파라미터 세트.

    Args:
        name:     정책 이름 → 파일 `~/reports/ml-cache/policy_<name>.json`.
        defaults: 기본 파라미터(콜드스타트·폴백).
        bounds:   {param: (min, max)} 안전 범위. 모든 load/save 시 강제 클램프.
        ttl_s:    load 캐시 TTL(초). 기본 6h(월간/주간 재학습 자동 반영).
    """

    def __init__(self, name: str, defaults: dict, bounds: dict | None = None,
                 *, ttl_s: float = 6 * 3600):
        self.name = name
        self.defaults = dict(defaults)
        self.bounds = dict(bounds or {})
        self.ttl_s = ttl_s
        self.path = _CACHE_DIR / f"policy_{name}.json"
        self._cache: dict | None = None
        self._cache_ts = 0.0

    # ── 클램프 ────────────────────────────────────────────────────────────────
    def clamp(self, params: dict) -> dict:
        """bounds 범위로 강제(학습기 극단값 차단). bounds 없는 키는 그대로."""
        out = dict(params)
        for k, (lo, hi) in self.bounds.items():
            if k in out:
                try:
                    out[k] = min(hi, max(lo, float(out[k])))
                except (TypeError, ValueError):
                    out[k] = self.defaults.get(k)
        return out

    # ── 로드 ──────────────────────────────────────────────────────────────────
    def load(self, *, use_cache: bool = True) -> dict:
        """정책 로드 = 기본값 위에 저장본 머지 후 클램프. 파일 없거나 오류면 기본값."""
        now = time.time()
        if use_cache and self._cache is not None and now - self._cache_ts < self.ttl_s:
            return dict(self._cache)
        params = dict(self.defaults)
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                # 기본값에 있는 키만 수용(스키마 드리프트 방지)
                for k, v in loaded.items():
                    if k in params:
                        params[k] = v
        except Exception as e:
            logger.warning("정책 '%s' 로드 실패 — 기본값 사용: %s", self.name, e)
            params = dict(self.defaults)
        params = self.clamp(params)
        self._cache, self._cache_ts = dict(params), now
        return dict(params)

    # ── 저장 ──────────────────────────────────────────────────────────────────
    def save(self, params: dict, *, meta: dict | None = None) -> dict:
        """클램프 후 원자적 저장. 클램프된 파라미터를 반환."""
        clamped = self.clamp({**self.defaults, **params})
        payload = dict(clamped)
        if meta:
            payload["_meta"] = meta
        _harden(_CACHE_DIR)
        _atomic_write_json(self.path, payload)
        self._cache, self._cache_ts = dict(clamped), time.time()
        logger.info("정책 '%s' 저장: %s", self.name, clamped)
        return clamped
