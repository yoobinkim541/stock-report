"""Point-in-time holdout split shared by learners that adopt models from outcomes.

Two rules (ml/recommendation_calibration 에서 추출, 동작 동일):
- 경계는 행 번호가 아니라 **결정 날짜 전체** — 같은 날의 다종목이 train/holdout 양쪽에 걸치지 않는다.
- train 라벨의 **실제 이용가능 시각**이 첫 holdout 결정 시각 이전인 행만 학습에 남긴다(purge).
  이용가능 시각을 모르는 행은 미래 정보일 수 있으므로 학습에서 제외한다.

Timestamps are normalized to UTC. Date-only values are the start (decision) or
end (availability, ``end_of_day=True``) of that UTC day; naive intraday
timestamps are rejected rather than guessed.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timezone
from typing import Any, TypeVar

Row = TypeVar("Row")


def utc_timestamp(value, *, end_of_day: bool = False) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, date):
        stamp = datetime.combine(value, time.max if end_of_day else time.min, timezone.utc)
    elif isinstance(value, str):
        try:
            if len(value) == 10:
                return utc_timestamp(date.fromisoformat(value), end_of_day=end_of_day)
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc)


def purged_day_holdout_split(
    rows: Sequence[Row], *, holdout_size: int,
    day_of: Callable[[Row], Any], decision_at: Callable[[Row], Any],
    available_at: Callable[[Row], Any],
) -> tuple[list[Row], list[Row], list[Row]]:
    """rows(결정 시각 오름차순) → (training, held_out, purged).

    holdout 은 뒤에서 ``holdout_size`` 번째 행이 속한 **결정일부터** 끝까지. 고유 결정일이
    2개 미만이면 holdout 을 만들지 않는다. ``available_at`` 이 None 이거나 첫 holdout
    결정 시각 이상인 training 행은 purged 로 옮긴다.
    """
    days = {day_of(r) for r in rows}
    boundary = day_of(rows[max(0, len(rows) - holdout_size)]) if len(days) >= 2 else None
    held_out = [r for r in rows if boundary is not None and day_of(r) >= boundary]
    training = [r for r in rows if boundary is None or day_of(r) < boundary]
    first_holdout = min((decision_at(r) for r in held_out), default=None)
    if first_holdout is None:
        return training, held_out, []
    purged, kept = [], []
    for r in training:
        available = available_at(r)
        (purged if available is None or available >= first_holdout else kept).append(r)
    return kept, held_out, purged
