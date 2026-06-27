# 6-Tier 퀀트 프로그램 — 성장최적 복리 머신 + 기관급 리스크 계측

> 제인스트리트·블랙록식 systematic 투자 구조를 개인 포트폴리오에 적용. 각 티어는
> **plan → 게이트(백테스트) → 정직 판정(GO/NO-GO) → 머지** 사이클로 검증됐다.
> **모든 산출은 표시·advisory·shadow 전용 — 라이브 배분 불변, 실계좌 자동집행 0.**

## 0. 북극성
작은 시드·긴 시계·환매압박 없음 = 헤지펀드가 못 하는 *보상받는 공격* 가능. 단 **"보상받는 위험감수"만**
유효하고 *예측/선택*은 무엣지(게이트가 입증). 리스크 계측이 공격의 활주로 — 복리는 큰 낙폭에서 죽으므로
측정한 뒤에만 레버리지를 얹는다.

## 1. 스코어카드

| Tier | 엔진 | 판정 | 핵심 수치 | 게이트 / 산출 |
|---|---|---|---|---|
| 1 | 리스크 계측 + Kelly 레버리지 계기판 | ✅ ship | ORCL 위험 30%·유효분산 ~5/9 | `ml/risk_model.py` · `/risk` |
| 2 | 검증 formalism | ✅ ship | Deflated Sharpe·PBO·Purged CV | `ml/validation.py` |
| 3 | **성장최적 구조적 레버리지** | ✅ **GO 1.3x** | 닷컴·2008·2020·2022 양프록시(SPY·QQQ) 예산 50% 내, 초과PSR 0.96 | `backtest/leverage_structural_backtest.py` |
| 4 | 팩터 프리미엄 틸트 | ❌ NO-GO | 밸류·사이즈·퀄리티·최소변동 SPY 미돌파; 모멘텀=SPMO 기보유 | `backtest/factor_premium_backtest.py` |
| 5 | 인컴 복리 재투자 | ❌ NO-GO | 커버드콜 세후 CAGR −12.9%p 열위; 재투자>비축 +33%(규율) | `backtest/income_compounding_backtest.py` |
| 6 | 검증된 집중 | ❌ NO-GO | 무스킬 집중 분산 이길확률 26%·승자 OOS 미지속(PBO 0.64) | `backtest/concentration_validated_backtest.py` |

**★결론**: 6개 게이트 중 통과 = **구조적 레버리지 1개**(보상받는 위험감수). NO-GO = 횡보타이밍·KR랭커·평균회귀·
팩터틸트·인컴엔진·종목집중(전부 예측/선택/현재수익). → **개인 복리 엔진 = 분산책 1.3x 레버리지 + 폭락 디리스크.
나머지 에너지는 예측이 아니라 비용·세금·버티기(행동규율)에.**

## 2. ADAPTIVE_* 플래그 가이드 (`.env`)

**중요: 어떤 플래그도 실계좌를 자동매매하지 않는다.** `true` = 게이트가 GO일 때 *권고를 shadow 파일로 기록 →
`/risk`·리포트에 한 줄 표시*까지만. 실제 집행은 항상 사람이 수동. NO-GO 티어는 `true`여도 기록 없음(표시 안 함).

| 플래그 | 기본 | true 로 바꾸면 | 현재 효과 |
|---|---|---|---|
| `ADAPTIVE_LEVERAGE_ENABLED` | false | Tier3 GO 권고(1.3x)를 shadow→`/risk` 표시 | **GO라 표시됨** (켜면 권고 노출) |
| `ADAPTIVE_FACTOR_TILT_ENABLED` | false | Tier4 GO 팩터를 shadow→`/risk` | NO-GO라 무효(표시 없음) |
| `ADAPTIVE_INCOME_ENGINE_ENABLED` | false | Tier5 GO(세후 우위) 시 shadow | NO-GO라 무효 |
| `ADAPTIVE_CONCENTRATION_DISPLAY_ENABLED` | false | Tier6 GO(집중>분산) 시 shadow | NO-GO라 무효 |
| `ADAPTIVE_ENTRY_ENABLED` | false | 해외 진입 임계값 적응 shadow→라이브 | (기존) |
| `ADAPTIVE_LONGTERM_ENABLED` | false | 장기 악화 시 보수적 레버리지 축소 shadow | (기존) |
| `ADAPTIVE_ADVICE_ENABLED` | false | MetaAllocator A/B blend 신뢰도 shadow | (기존) |

설정 예: `.env` 에 `ADAPTIVE_LEVERAGE_ENABLED=true` 추가 → 주간 크론(토 04:15)이 GO 권고를 기록 →
`/risk` 에 "구조적 레버리지 권고 ×1.3" 줄 노출. 끄면(기본) 게이트 평가·텔레그램만, `/risk` 무표시.
**과집중 경고(`/risk` 단일종목 위험)는 플래그와 무관하게 상시 표시.**

## 3. 게이트 재검증 · 파라미터 튜닝

각 게이트는 독립 실행 가능 — 언제든 다시 돌려 판정 확인:
```bash
uv run python backtest/leverage_structural_backtest.py     # Tier3 (GO 1.3x)
uv run python backtest/factor_premium_backtest.py          # Tier4
uv run python backtest/income_compounding_backtest.py      # Tier5
uv run python backtest/concentration_validated_backtest.py # Tier6
```
출력 JSON 의 `verdict`(GO/조건부/NO-GO) + 근거(초과PSR·DSR·PBO·objective·낙폭예산). 주간 크론이 자동
재검증(토 04:15~05:15 UTC)하므로 **밸류 부활·레짐 변화 시 NO-GO→GO 자동 포착**.

**게이트 판정 기준(공통)**: `psr_excess≥0.95`(벤치마크 초과 유의) ∧ `dsr≥0.95`(다중검정 deflate 후) ∧
`objective>0`(아웃퍼폼+MDD≤지수) ∧ `pbo≤0.5`(과적합 아님). Tier별 추가: 레버리지=낙폭예산≤50%+양프록시 일치,
팩터=약세슬라이스, 집중=분산 이길확률>50%.

**파라미터 튜닝(env override, 재실행 시 반영)**:
| 대상 | env | 기본 | 예 |
|---|---|---|---|
| 낙폭예산(레버리지) | `TIER3_BUDGET` | 0.50 | `TIER3_BUDGET=0.40` → 더 보수적 상한(1.3x 통과 여부 재확인) |
| LETF 파이낸싱·비용 | `TIER3_LETF_SPREAD`·`TIER3_LETF_EXPENSE`·`TIER3_RF_FALLBACK` | 0.005·0.009·0.03 | 금리 가정 변경 |
| 유의수준 게이트 | `TIER{4,5,6}_PSR_GATE`·`_DSR_GATE`·`_PBO_MAX` | 0.95·0.95·0.50 | 더 엄격: `=0.99` |
| 배당세(인컴) | `TIER5_DIV_TAX` | 0.154 | 세율 변경 |
| 집중 MC | `TIER6_SEED`·`TIER6_MC_SAMPLES` | 6·500 | 재현 seed·표본수 |

튜닝 워크플로: ① env 설정 → ② 게이트 재실행 → ③ `verdict` 확인 → ④ GO면 플래그(2절) on → `/risk` 노출.

## 4. 안전 불변식 (전 티어)
- **표시·advisory·shadow 전용** — DCA/ALLOC/`leverage_state` 불변. 실계좌 자동집행 **0**(권고만, 수동).
- 검증 전 라이브 배분 변경 0. NO-GO는 이름 명시 정직 공개(과대광고 금지).
- 레버리지 증액은 사람이. 폭락 디리스크 서킷브레이커 전제·갭리스크 잔존.
- 수학은 폐형해 단위테스트로 입증(113 테스트). 게이트 도구 = `ml/validation`(PSR·DSR·PBO)·`ml/risk_model`.

> 관련: [adaptive 루프 다이어그램](../README.md#-적응-학습-루프-전략-자동-조절) · 게이트 크론 = `deploy/crontab.stock-report` (토 04:15~05:15 UTC).
