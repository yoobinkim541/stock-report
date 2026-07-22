from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DATA_DIR = Path(os.path.expanduser("~/reports/ml-data"))


class CandidateLedger:
    def __init__(self, market: str, base_dir: Path | None = None):
        mk = str(market or "").strip().lower()
        if mk not in {"kr", "us"}:
            raise ValueError("market must be kr or us")
        self.market = mk
        self.base = Path(base_dir) if base_dir is not None else _DATA_DIR
        self.candidates_path = self.base / f"{mk}_intraday_candidates.jsonl"
        self.outcomes_path = self.base / f"{mk}_intraday_candidate_outcomes.jsonl"

    @staticmethod
    def _read_lines(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                rows.append(rec)
        return rows

    @staticmethod
    def _append_line(path: Path, rec: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _candidate_id(rec: dict[str, Any]) -> str:
        cid = str(rec.get("id") or "").strip()
        if not cid:
            raise ValueError("candidate id required")
        return cid

    @staticmethod
    def _outcome_key(rec: dict[str, Any]) -> tuple[str, int]:
        cid = str(rec.get("candidate_id") or "").strip()
        if not cid:
            raise ValueError("candidate_id required")
        horizon = rec.get("horizon_min")
        if horizon is None:
            raise ValueError("horizon_min required")
        try:
            horizon_i = int(horizon)
        except (TypeError, ValueError) as exc:
            raise ValueError("horizon_min must be an integer") from exc
        return cid, horizon_i

    def read_candidates(self) -> list[dict[str, Any]]:
        return self._read_lines(self.candidates_path)

    def read_outcomes(self) -> list[dict[str, Any]]:
        return self._read_lines(self.outcomes_path)

    def log_candidate(self, rec: dict[str, Any]) -> str:
        cid = self._candidate_id(rec)
        existing = {str(row.get("id") or "") for row in self.read_candidates()}
        if cid not in existing:
            self._append_line(self.candidates_path, {**rec, "id": cid})
        return cid

    def log_outcome(self, rec: dict[str, Any]) -> None:
        key = self._outcome_key(rec)
        done = {self._outcome_key(row) for row in self.read_outcomes() if row.get("candidate_id")}
        if key not in done:
            candidate_id, horizon_min = key
            payload = {**rec, "candidate_id": candidate_id, "horizon_min": horizon_min}
            self._append_line(self.outcomes_path, payload)

    def pending(self, horizons: tuple[int, ...] = (5, 15, 30)) -> list[tuple[dict[str, Any], int]]:
        wanted = tuple(int(h) for h in horizons)
        done = {self._outcome_key(row) for row in self.read_outcomes() if row.get("candidate_id")}
        out: list[tuple[dict[str, Any], int]] = []
        for rec in self.read_candidates():
            cid = str(rec.get("id") or "").strip()
            if not cid:
                continue
            for horizon in wanted:
                if (cid, horizon) not in done:
                    out.append((rec, horizon))
        return out
