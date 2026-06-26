"""
ml.adaptive — 성과 피드백(적응형) 학습 공유 프레임워크.

모든 의사결정 표면(KR 모의·해외 단기진입·장기타점·포트폴리오 advice)이 공유하는
`features → action → realized reward → OOS게이트 재학습` 루프의 재사용 부품.

불변식 (전 표면 공통):
  - 클램프(안전범위) — 학습기가 극단 파라미터를 산출 불가.
  - OOS 게이트 + MIN_SAMPLES — 표본 충족 + out-of-sample 개선 시만 채택.
  - 룩어헤드 금지 — 피처는 point-in-time, 라벨은 shift.
  - 챔피언/챌린저 — 신규 정책은 섀도 평가 후 우위 시만 승격.
  - ★목적함수 — 지수 대비 아웃퍼폼(최우선) + MDD ≤ 지수 MDD(제약).
  - 원장 불변 — 결정/결과는 append-only, 절대 삭제/수정 안 함.
"""
from ml.adaptive.policy import Policy
from ml.adaptive.ledger import Ledger
from ml.adaptive import reward, learner, regime, champion_challenger

__all__ = ["Policy", "Ledger", "reward", "learner", "regime", "champion_challenger"]
