# 추천 엔진 v2 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 기존 추천 표면의 point-in-time 데이터와 성과 원장을 정규화하고, 증분 소스 인덱스·뉴스 특징량·점수 보정·shadow feedback을 연결해 추천 품질과 조회 성능을 개선한다.

**Architecture:** 원본 JSONL은 append-only 감사 원천으로 유지한다. 표준 라이브러리 SQLite 증분 인덱스가 이벤트 메타데이터와 티커-일 특징량을 materialize하고, 세 추천 표면은 공통 RecommendationSnapshot으로 감싼다. v2는 기본 shadow 모드에서 기존 결과와 동시 평가하며 OOS 관문을 통과한 경우에만 active 모드로 전환한다.

**Tech Stack:** Python 3.11, pandas, NumPy, SQLite3 표준 라이브러리, 기존 LightGBM/XGBoost adapter, pytest.

**Spec:** docs/superpowers/specs/2026-09-27-recommendation-engine-v2-design.md

## Global Constraints

- 기존 append-only JSONL 원본은 삭제·재작성하지 않는다.
- 기존 20/20d 별칭은 정규화 뷰에서 20d로 합치고, 과거 원본 행은 보존한다.
- NaN, +inf, -inf는 새 원장에 저장하지 않고 null로 정규화한다.
- RECOMMENDER_V2_MODE 기본값은 shadow이며 active는 명시적으로 설정한 경우에만 사용한다.
- 뉴스 축의 실전 가중치는 OOS 관문 전까지 0이다.
- GBDT/뉴스 축 채택은 최소 3개 purged walk-forward fold, 기준선 대비 OOS IC +0.01, 비용 차감 상위 10% 초과수익, MDD 악화 2%p 이내를 모두 요구한다.
- 보정기 표본은 100개 미만이면 생성하지 않고, OOS Brier 악화 또는 ECE 악화 0.02 이상이면 raw score를 유지한다.
- warm-cache 100개 후보 특징량 조회 p95 300ms 이하, 추천 실행 중 전체 source-cache 재스캔 0회를 목표로 한다.
- 현재 작업 트리의 기존 사용자 변경사항은 되돌리거나 일괄 포맷하지 않는다.

## Review Focus

- legacy 20/canonical 20d outcome이 하나의 학습 표본으로만 계산되는지: Task 1의 canonical horizon·dedupe 테스트.
- nested NaN/무한값이 원장에 그대로 직렬화되지 않는지: Task 1의 JSON-safe append 테스트.
- append 중인 일별 파일과 파일 교체/offset 축소가 중복 없이 재색인되는지: Task 2의 checkpoint 테스트.
- 미래 라벨과 차단된 소스가 뉴스 신호로 사용되지 않고 degraded 상태로 표시되는지: Task 3의 point-in-time·availability 테스트.
- 오래된 음의 OOS GBDT가 active 추천을 강제하지 않는지: Task 6의 stale/champion gate 테스트.

---

### Task 1: 원장 계약과 성과 정규화

**Files:**
- Create: ml/recommendation_contracts.py
- Modify: ml/adaptive/ledger.py
- Modify: ml/entry_feedback.py
- Test: tests/test_recommendation_contracts.py
- Test: tests/test_entry_feedback.py

**Interfaces:**
- Produces canonical_horizon(value: int | str) -> str.
- Produces finite_json_value(value: object) -> object | None.
- Produces sanitize_record(value: object) -> object.
- Produces canonical_outcome_key(base_decision_id: str, horizon: int | str) -> tuple[str, str].
- Produces dedupe_canonical_outcomes(rows: Iterable[dict]) -> list[dict].
- Existing Ledger.read_*, Ledger.log_*, entry_feedback.training_rows signatures remain backward compatible.

- [ ] Step 1: Write failing tests

  Add tests for 20 and 20d mapping to 20d, 60 and 60d mapping to 60d, unsupported horizons remaining explicit, nested non-finite values becoming None, and duplicate canonical outcomes preferring an already-canonical row deterministically.

- [ ] Step 2: Run tests to verify failure

  Run: .venv/bin/pytest -q tests/test_recommendation_contracts.py tests/test_entry_feedback.py

  Expected: FAIL because the contract module and canonicalized training path do not exist.

- [ ] Step 3: Implement the contract

  In ml/recommendation_contracts.py, implement the alias map, recursive JSON-safe normalization, and deterministic outcome dedupe. Dedupe priority is: canonical horizon row, valid status, latest matured_at, then original order.

- [ ] Step 4: Make new ledger writes safe

  In ml/adaptive/ledger.py, pass decision/outcome records through sanitize_record immediately before json.dumps. Do not alter existing raw rows. Keep append-only and idempotent behavior unchanged.

- [ ] Step 5: Canonicalize entry outcomes

  In ml/entry_feedback.py, make build_outcome and backfill_outcomes write canonical string horizons, make training_rows canonicalize and dedupe legacy rows, and exclude invalid non-finite outcome metrics from summary aggregates while retaining their rows for diagnostics.

- [ ] Step 6: Run focused tests

  Run: .venv/bin/pytest -q tests/test_recommendation_contracts.py tests/test_entry_feedback.py

  Expected: PASS, including all existing entry feedback regression tests.

- [ ] Step 7: Commit

  git add ml/recommendation_contracts.py ml/adaptive/ledger.py ml/entry_feedback.py tests/test_recommendation_contracts.py tests/test_entry_feedback.py
  git commit -m "fix) 추천 성과 원장 horizon과 결측값 정규화"

### Task 2: 증분 추천 소스 인덱스

**Files:**
- Create: reports/recommendation_index.py
- Test: tests/test_recommendation_index.py

**Interfaces:**
- RecommendationIndex(db_path: Path | None = None, cache_dir: Path | None = None).
- RecommendationIndex.initialize() -> None.
- RecommendationIndex.ingest(paths: Iterable[Path] | None = None) -> dict.
- RecommendationIndex.query_events(tickers: Iterable[str] | None = None, start: datetime | None = None, end: datetime | None = None, source_families: Iterable[str] | None = None, limit: int | None = None) -> list[dict].
- RecommendationIndex.ticker_day_features(ticker: str, asof: datetime | None = None, window_days: int = 7) -> list[dict].
- RecommendationIndex.status() -> dict.

- [ ] Step 1: Write failing SQLite/checkpoint tests

  Use a temporary cache with content and mutable observation rows. Assert schema creation, raw path/line references, source-family filtering, duplicate content handling, and ticker-day counts.

- [ ] Step 2: Run tests to verify failure

  Run: .venv/bin/pytest -q tests/test_recommendation_index.py

  Expected: FAIL because reports.recommendation_index does not exist.

- [ ] Step 3: Implement schema and discovery

  Create ingest_checkpoint, event_index, and ticker_day_features using SQLite. Discover only events-*.jsonl files, excluding lock files. Store raw file and line references but never copy raw bodies into SQLite.

- [ ] Step 4: Implement checkpointed ingestion

  Read each file in binary mode, record byte offset before each line, decode valid JSON, reuse existing classification/identity fields, and update the checkpoint only after the row is committed. If inode or file size moves backwards, reset that file checkpoint and reindex it. Invalid JSON increments an error counter without aborting the batch.

- [ ] Step 5: Implement bounded queries and materialized features

  Add indexed queries by ticker, date, family, and source. Aggregate counts, article/community split, source diversity, trust prior, body availability, and freshness into ticker_day_features. Return health metadata with lag, errors, and fallback count.

- [ ] Step 6: Run storage tests

  Run: .venv/bin/pytest -q tests/test_recommendation_index.py

  Expected: PASS, including restart idempotence, append-only tail ingestion, rotation recovery, and raw reference lookup.

- [ ] Step 7: Commit

  git add reports/recommendation_index.py tests/test_recommendation_index.py
  git commit -m "add) 추천 소스 증분 SQLite 인덱스 추가"

### Task 3: Point-in-time 뉴스 특징량

**Files:**
- Create: ml/recommendation_features.py
- Modify: ml/data_sources.py
- Test: tests/test_recommendation_features.py
- Modify: tests/test_ml_features.py only if the legacy merge contract needs an explicit regression assertion.

**Interfaces:**
- build_news_feature_frame(ticker: str, start: str, as_of: str | None = None, index: RecommendationIndex | None = None, labels: list[dict] | None = None) -> pd.DataFrame.
- ml.data_sources.fetch_news_features(...) -> pd.DataFrame delegates to the feature builder and no longer raises NotImplementedError.
- Returned frame keeps legacy columns date,ticker,count,sentiment,theme,event and adds article_count, community_count, source_diversity, trust_score, body_ratio, freshness_seconds, news_axis, data_quality.

- [ ] Step 1: Write failing feature tests

  Fixture content rows from news, community, macro, and prediction sources. Assert macro/market/prediction rows do not enter the direct news count, future events are excluded by as_of, future LLM labels are excluded, source diversity is not simple event-count duplication, and blocked source health produces degraded rather than a zero-news claim.

- [ ] Step 2: Run tests to verify failure

  Run: .venv/bin/pytest -q tests/test_recommendation_features.py

  Expected: FAIL because fetch_news_features currently raises NotImplementedError.

- [ ] Step 3: Implement point-in-time event aggregation

  Query RecommendationIndex by ticker and date window. Use published_at, then observed_at, then collected_at as the event time; exclude rows newer than as_of. Aggregate source-family and body/provenance fields without loading the raw cache.

- [ ] Step 4: Join labels safely

  Load or inject labels, retain only published_at <= as_of and labeled_at <= as_of, and compute the existing direction-strength decay axis. Missing labels remain null with a quality flag; they are not converted to neutral evidence.

- [ ] Step 5: Preserve the public data-source contract

  Replace the placeholder in ml/data_sources.py with the builder call, keep empty-frame behavior for missing cache/index, and attach feature version and data-quality metadata to the returned frame.

- [ ] Step 6: Run feature and legacy tests

  Run: .venv/bin/pytest -q tests/test_recommendation_features.py tests/test_ml_features.py tests/test_news_labels.py

  Expected: PASS.

- [ ] Step 7: Commit

  git add ml/recommendation_features.py ml/data_sources.py tests/test_recommendation_features.py tests/test_ml_features.py tests/test_news_labels.py
  git commit -m "add) 뉴스 point-in-time 특징량 파이프라인 연결"

### Task 4: 공통 RecommendationSnapshot과 점수 보정

**Files:**
- Create: ml/recommendation_engine.py
- Test: tests/test_recommendation_engine.py

**Interfaces:**
- build_snapshot(surface: str, candidate: Mapping, *, source_features: Mapping | None = None, as_of: str | None = None, model_version: str | None = None) -> dict.
- shrink_probability(wins: int, samples: int, alpha: float = 2.0, beta: float = 2.0) -> float.
- fit_score_calibrator(rows: Sequence[Mapping], score_key: str = "raw_score", target_key: str = "positive_excess", min_bin_size: int = 20, min_samples: int = 100) -> dict | None.
- calibrate_score(score: float, calibrator: Mapping | None) -> float | None.
- rank_snapshots(snapshots: Sequence[Mapping], *, top_n: int = 5, holdings: Sequence[str] = (), active: bool = False, gate: Mapping | None = None) -> list[dict].

- [ ] Step 1: Write failing engine tests

  Assert the snapshot schema, Beta(2,2) shrinkage, minimum sample behavior, monotonic five-bin calibration, Brier/ECE metrics, stale/missing data quality, and deterministic top-N ranking with risk/concentration penalties.

- [ ] Step 2: Run tests to verify failure

  Run: .venv/bin/pytest -q tests/test_recommendation_engine.py

  Expected: FAIL because the engine module does not exist.

- [ ] Step 3: Implement snapshot and uncertainty

  Normalize raw candidate fields into the contract from the spec. Preserve original surface, horizon, evidence references, model version, missing fields, and selection status. Apply the Beta(2,2) posterior and expose uncertainty bounds without changing the legacy raw score.

- [ ] Step 4: Implement calibration

  Fit five time-ordered score bins only on training rows, merge bins below 20 observations, and return calibration metadata. Calculate Brier/ECE on held-out rows. Return None when fewer than 100 valid rows exist.

- [ ] Step 5: Implement ranking and mode behavior

  Calculate total_rank_score = calibrated_alpha - risk_penalty - concentration_penalty. In active=False, annotate shadow results but leave the legacy ordering untouched. Active ranking is allowed only when active=True and gate.passed is true; keep news contribution zero until the caller provides a passed gate.

- [ ] Step 6: Run engine tests

  Run: .venv/bin/pytest -q tests/test_recommendation_engine.py

  Expected: PASS.

- [ ] Step 7: Commit

  git add ml/recommendation_engine.py tests/test_recommendation_engine.py
  git commit -m "add) 추천 스냅샷과 점수 보정 엔진 추가"

### Task 5: 세 추천 표면에 shadow 연결

**Files:**
- Modify: ml/entry_feedback.py
- Modify: reports/investment_report.py
- Modify: crons/us_mock_track.py
- Modify: crons/kiwoom_mock_track.py
- Test: tests/test_recommendation_surfaces.py
- Modify: tests/test_entry_feedback.py
- Modify: tests/test_investment_report.py
- Modify: tests/test_us_mock_track.py
- Modify: tests/test_kiwoom_mock.py

**Interfaces:**
- Entry decision records gain optional selection_status, selection_rank, data_quality, source_coverage, and evidence_refs fields without breaking existing callers.
- reports.investment_report._select_top_buy_candidates keeps its existing result when RECOMMENDER_V2_MODE=shadow, while attaching a recommendation_v2_shadow payload to the report data.
- US/KR mock decisions persist the same snapshot metadata for selected and shadow candidates.

- [ ] Step 1: Write failing integration tests

  Assert entry, investment-report, and US/KR mock records all expose the common metadata; assert shadow mode preserves legacy ordering and active mode is opt-in.

- [ ] Step 2: Run tests to verify failure

  Run: .venv/bin/pytest -q tests/test_recommendation_surfaces.py tests/test_entry_feedback.py tests/test_investment_report.py tests/test_us_mock_track.py tests/test_kiwoom_mock.py

  Expected: FAIL on missing common metadata or shadow payload.

- [ ] Step 3: Adapt entry records

  Add optional snapshot metadata to score_to_decision and the record path. Use current entry features, canonical horizon, feedback factors, and source-feature quality; do not change the current enter/wait/avoid threshold in shadow mode.

- [ ] Step 4: Adapt investment report selection

  Build snapshots from existing report rows, call rank_snapshots in shadow mode, and store both legacy picks and v2 diagnostics. Only RECOMMENDER_V2_MODE=active may replace the displayed top candidates after the gate result is true.

- [ ] Step 5: Adapt US/KR mock decision logging

  Preserve existing policy weights and action decisions. Add snapshot metadata and non-selected shadow candidates so future learning sees the candidate set instead of only executed actions.

- [ ] Step 6: Run surface regression tests

  Run the focused command from Step 2 and then .venv/bin/pytest -q tests/test_entry_feedback.py tests/test_investment_report.py tests/test_us_mock_track.py tests/test_kiwoom_mock.py.

  Expected: PASS with legacy default behavior unchanged.

- [ ] Step 7: Commit

  git add ml/entry_feedback.py reports/investment_report.py crons/us_mock_track.py crons/kiwoom_mock_track.py tests/test_recommendation_surfaces.py tests/test_entry_feedback.py tests/test_investment_report.py tests/test_us_mock_track.py tests/test_kiwoom_mock.py
  git commit -m "add) 추천 표면에 공통 shadow 스냅샷 연결"

### Task 6: GBDT freshness와 champion/challenger 관문

**Files:**
- Modify: ml/ranker.py
- Modify: crons/weekly_ranker_retrain.py
- Create: ml/recommendation_validation.py
- Test: tests/test_recommendation_validation.py
- Modify: tests/test_index_rsi_and_gate.py

**Interfaces:**
- ranker_freshness(result: RankerResult, now: datetime | None = None) -> dict.
- recommendation_gate(metrics: Mapping, baseline: Mapping | None = None) -> dict.
- validate_calibration(y_true: Sequence[float], probabilities: Sequence[float]) -> dict.

- [ ] Step 1: Write failing validation tests

  Cover an old model, negative OOS IC, insufficient folds, MDD breach, cost-negative top decile, Brier/ECE regression, and a fully passing challenger.

- [ ] Step 2: Run tests to verify failure

  Run: .venv/bin/pytest -q tests/test_recommendation_validation.py tests/test_index_rsi_and_gate.py

  Expected: FAIL because freshness and combined gate APIs do not exist.

- [ ] Step 3: Implement freshness and metrics

  Expose model age, training end date, OOS metrics, and data coverage. Treat stale or negative models as degraded shadow candidates; do not delete or silently replace cached models.

- [ ] Step 4: Implement the combined gate

  Require all thresholds from the spec and return structured reasons for each failed condition. Keep existing adopt_if_better champion behavior and make the new gate an additional safety layer.

- [ ] Step 5: Wire weekly retraining diagnostics

  Have weekly_ranker_retrain.py report freshness, calibration, source-quality coverage, and gate decision. Keep XGBoost challenger evaluation shadow-only until the combined gate passes.

- [ ] Step 6: Run ranker validation tests

  Run: .venv/bin/pytest -q tests/test_recommendation_validation.py tests/test_index_rsi_and_gate.py tests/test_ranker_scores_by_ticker.py

  Expected: PASS.

- [ ] Step 7: Commit

  git add ml/ranker.py crons/weekly_ranker_retrain.py ml/recommendation_validation.py tests/test_recommendation_validation.py tests/test_index_rsi_and_gate.py tests/test_ranker_scores_by_ticker.py
  git commit -m "fix) GBDT 신선도와 추천 채택 관문 강화"

### Task 7: 운영 색인, 배치 성능, 통합 검증

**Files:**
- Create: crons/recommendation_index_refresh.py
- Create: reports/recommendation_health.py
- Modify: deploy/crontab.stock-report
- Test: tests/test_recommendation_index_refresh.py
- Test: tests/test_recommendation_performance.py
- Test: tests/test_recommendation_end_to_end.py

**Interfaces:**
- crons.recommendation_index_refresh.main(argv: list[str] | None = None) -> int.
- reports.recommendation_health.collect() -> dict.
- reports.recommendation_health.format_report(status: Mapping) -> str.

- [ ] Step 1: Write failing operational tests

  Assert refresh is idempotent, health reports index lag/errors/fallbacks, the end-to-end fixture flows from raw event to feature frame to snapshot, and a warm-cache 100-candidate query does not read raw files.

- [ ] Step 2: Run tests to verify failure

  Run: .venv/bin/pytest -q tests/test_recommendation_index_refresh.py tests/test_recommendation_performance.py tests/test_recommendation_end_to_end.py

  Expected: FAIL because the refresh command and health module do not exist.

- [ ] Step 3: Implement the refresh command

  Add a bounded CLI that initializes and ingests the index, exits non-zero only for unrecoverable database errors, and logs fetched/indexed/invalid/lag counts. It must not call external providers or rewrite raw files.

- [ ] Step 4: Implement health reporting

  Combine index status, source availability, canonical duplicate count, invalid metric count, model freshness, and fallback count into a compact structured report for existing cron/Telegram observability.

- [ ] Step 5: Schedule incremental refresh

  Add one cron entry using the repository’s existing execution conventions. Keep refresh frequency separate from provider collection and avoid overlapping SQLite writers with a lock.

- [ ] Step 6: Run performance and end-to-end tests

  Run: .venv/bin/pytest -q tests/test_recommendation_index_refresh.py tests/test_recommendation_performance.py tests/test_recommendation_end_to_end.py

  Expected: PASS; the fixture must show zero raw-cache scans during a warm query.

- [ ] Step 7: Run the complete focused regression bundle

  Run: .venv/bin/pytest -q tests/test_recommendation_contracts.py tests/test_recommendation_index.py tests/test_recommendation_features.py tests/test_recommendation_engine.py tests/test_recommendation_surfaces.py tests/test_recommendation_validation.py tests/test_recommendation_index_refresh.py tests/test_recommendation_performance.py tests/test_recommendation_end_to_end.py tests/test_entry_feedback.py tests/test_news_labels.py tests/test_source_collector.py tests/test_investment_report.py

  Expected: PASS. If a broad network-dependent suite hangs, report it separately rather than claiming success.

- [ ] Step 8: Commit

  git add crons/recommendation_index_refresh.py reports/recommendation_health.py deploy/crontab.stock-report tests/test_recommendation_index_refresh.py tests/test_recommendation_performance.py tests/test_recommendation_end_to_end.py
  git commit -m "add) 추천 인덱스 운영 갱신과 성능 검증 추가"

## Completion Checklist

- [ ] All task-level commits contain only their listed files.
- [ ] Existing raw source and ML JSONL files remain unchanged.
- [ ] Default runtime remains legacy-compatible with v2 in shadow mode.
- [ ] Recommendation health exposes source blocks, index lag, invalid metrics, and stale model state.
- [ ] No model or news axis is promoted without the combined OOS gate.
- [ ] Verification report records exact test commands and any network-dependent gaps.
