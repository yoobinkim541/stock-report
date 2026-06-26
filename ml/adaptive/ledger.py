"""
ledger.py — 불변(append-only) 의사결정/결과 원장 + 사람용 MD 저널.

★ 유빈님 핵심 요청: **매일의 성공/실패 데이터를 절대 삭제하지 말고 영구 누적.**

설계 = 이벤트 소싱(두 개의 불변 로그):
  - <surface>_decisions.jsonl : 결정 시점 1건(피처는 point-in-time → 룩어헤드 없음).
  - <surface>_outcomes.jsonl  : 보상 성숙 시 1건(decision_id 로 결정과 조인).
두 로그 모두 **오직 append** — 기존 줄을 수정/삭제하지 않는다(감사·학습 무결성).
백필은 결정 줄을 고치는 게 아니라 outcomes 에 *새 줄 추가*로만 한다.

학습셋 = decisions ⋈ outcomes (decision_id). 사람용 = kr_journal/YYYY-MM.md 누적.

위치: ~/reports/ml-data/  (모델 캐시 ml-cache 와 분리 — 영구 데이터 원천).
JSONL append 관례 출처: crons/fundamental_snapshot.py.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.path.expanduser("~/reports/ml-data"))


class Ledger:
    """표면별 불변 원장. decision_id = f"{date}:{ticker}" (일·종목당 1결정 가정)."""

    def __init__(self, surface: str, base_dir: Path | None = None):
        self.surface = surface
        self.base = Path(base_dir) if base_dir else _DATA_DIR
        self.decisions_path = self.base / f"{surface}_decisions.jsonl"
        self.outcomes_path = self.base / f"{surface}_outcomes.jsonl"
        self.journal_dir = self.base / f"{surface}_journal"

    # ── 내부 ──────────────────────────────────────────────────────────────────
    def _append_line(self, path: Path, obj: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:   # append-only
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_lines(path: Path) -> list[dict]:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue   # 손상 줄 건너뛰기(원장은 보존 — 삭제 안 함)
        return out

    @staticmethod
    def make_id(date: str, ticker: str) -> str:
        return f"{date}:{ticker}"

    # ── 결정 로그 ─────────────────────────────────────────────────────────────
    def log_decision(self, rec: dict) -> str:
        """결정 1건 append. id 없으면 date:ticker 로 생성. 같은 id 이미 있으면 재기록 안 함(멱등)."""
        date = rec.get("date", "")
        ticker = rec.get("ticker", "")
        did = rec.get("id") or self.make_id(date, ticker)
        rec = {**rec, "id": did}
        existing = {d.get("id") for d in self._read_lines(self.decisions_path)}
        if did in existing:
            return did   # 멱등 — 같은 날 같은 종목 재실행 시 중복 줄 방지
        self._append_line(self.decisions_path, rec)
        return did

    # ── 결과 로그 ─────────────────────────────────────────────────────────────
    def log_outcome(self, rec: dict) -> None:
        """결과 1건 append(decision_id 필수). 결정 줄은 절대 고치지 않음."""
        if not rec.get("decision_id"):
            raise ValueError("outcome 에 decision_id 필수")
        # 이미 결과가 있는 decision_id 는 중복 적재 안 함(멱등)
        done = {o.get("decision_id") for o in self._read_lines(self.outcomes_path)}
        if rec["decision_id"] in done:
            return
        self._append_line(self.outcomes_path, rec)

    # ── 조회 ──────────────────────────────────────────────────────────────────
    def read_decisions(self) -> list[dict]:
        return self._read_lines(self.decisions_path)

    def read_outcomes(self) -> list[dict]:
        return self._read_lines(self.outcomes_path)

    def pending(self) -> list[dict]:
        """결과가 아직 없는 결정(보상 백필 대상)."""
        done = {o.get("decision_id") for o in self.read_outcomes()}
        return [d for d in self.read_decisions() if d.get("id") not in done]

    def training_set(self) -> list[dict]:
        """결정 ⋈ 결과 (보상 성숙분만). 각 행 = {**decision, **outcome}."""
        outcomes = {o["decision_id"]: o for o in self.read_outcomes() if o.get("decision_id")}
        rows = []
        for d in self.read_decisions():
            o = outcomes.get(d.get("id"))
            if o is not None:
                rows.append({**d, **o})
        return rows

    # ── 사람용 MD 저널 ────────────────────────────────────────────────────────
    def append_journal(self, date: str, line: str) -> None:
        """kr_journal/YYYY-MM.md 에 한 줄 누적(append-only). date='YYYY-MM-DD'."""
        ym = date[:7] if len(date) >= 7 else "unknown"
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        path = self.journal_dir / f"{ym}.md"
        header_needed = not path.exists()
        with open(path, "a", encoding="utf-8") as f:
            if header_needed:
                f.write(f"# {self.surface} 저널 {ym}\n\n")
            f.write(line.rstrip("\n") + "\n")
