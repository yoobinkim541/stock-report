# 기사 본문 큐와 투자 위키 파이프라인

뉴스 수집기는 기사 메타데이터를 먼저 `source-cache`에 보존한 뒤, 같은 소스 배치의 수집 이벤트를 기사 큐에 등록한다. 큐 장애는 원본 소스 저장을 되돌리지 않으며 수집 결과와 `source_health.json`의 `article_queue` 항목에 제한된 건수와 최대 500자 오류로 남는다. 시장 스냅샷, 리포트 등 기사로 분류되지 않은 이벤트와 위키 비대상 이벤트는 큐에 넣지 않는다.

## 저장 위치와 상태

기본 큐 루트는 `reports.raw_archive.reports_root()/article-cache`이다. `ARTICLE_CACHE_DIR` 환경 변수나 CLI의 `--root`로 별도 루트를 지정할 수 있다. `index.json`에는 URL·제목·출처·발행 시각·상태·시도 횟수·오류·content hash 같은 메타데이터만 저장하며, 정제 본문은 `bodies/` 아래 별도 파일에 둔다. URL fragment와 알려진 추적 파라미터는 제거되지만 의미 있는 query와 path 대소문자는 보존된다.

일반 상태 전이는 다음과 같다.

```text
discovery -> pending -> fetching -> ready
                         |-> retry -> fetching
                         |-> failed / unavailable / blocked / needs_browser / capacity
ready + 24시간 이후 재발견 -> refresh_pending -> fetching -> ready
```

`get_article()`은 `ready` 상태이면서 메타데이터와 본문 파일의 content hash가 일치할 때만 본문을 반환한다. `needs_browser`는 준비 완료가 아니며 위키 입력으로 사용할 수 없다. 성공한 갱신의 정제 본문 SHA-256이 기존 값과 다를 때만 새 content hash가 반영된다. 동일 본문은 기존 버전을 재사용한다. 갱신 실패 시 마지막 정상 본문은 유지한다.

위키 편집기는 선택된 source 하나당 `ready` 본문을 최대 8개, 각 3,000자까지 사용한다. 같은 evidence ID가 다시 들어와도 article content hash가 바뀌면 해당 source를 다시 편집 대상으로 연다. 편집 결과는 기존의 stable document ID를 갱신하며, 본문 인용 번호와 문서 하단의 연속된 숫자 출처 목록이 일치해야 한다. 모델이 알 수 없는 출처를 인용하거나 번호 검증에 실패하면 기존 문서를 byte-for-byte 보존하고 source를 retry 가능한 상태로 남긴다. 기존 위키 생성 cron 주기는 이 crawler 추가로 변경하지 않는다.

큐 메타데이터는 최대 10,000건, 완료 메타데이터는 14일 동안 유지한다. 본문 캐시는 직렬화 크기 기준 최대 512 MiB 또는 10,000개 artifact이며, 현재 참조 중인 마지막 정상 본문은 정리하지 않는다.

## 실행과 관찰

상태 조회는 읽기 전용이다.

```bash
uv run python -m reports.article_crawler --status
```

최근 소스 이벤트를 한 번만 seed한 뒤 최대 20건을 처리하려면 다음과 같이 실행한다. 정기 cron은 반복 seed를 하지 않는다.

```bash
uv run python -m reports.article_crawler --seed-hours 48 --seed-limit 100 --limit 20
```

일반 worker 실행은 `uv run python -m reports.article_crawler --limit 20`이다. 저장소의 `deploy/crontab.stock-report`는 8, 18, 28, 38, 48, 58분에 `flock`으로 단일 worker를 실행한다. 설치된 cron과 저장소 기준의 차이는 `uv run python scripts/check_crontab_drift.py`로 확인한다. 저장소 작업 중에는 crontab을 설치하지 않는다.

일시 오류는 최대 3회 시도하며 첫 두 재시도 대기는 5분, 30분이다. 재발견된 완료 URL은 24시간이 지난 경우에만 재검증 대상으로 바뀐다. worker는 한 host에 요청 사이 최소 2초 간격을 두고, redirect는 최대 3회, 응답은 2 MiB로 제한한다. 실제 HTTP transport는 별도 단일 프로세스에서 요청당 15초 deadline을 적용하고 종료 시 정리한다. daemon이나 다른 장기 실행 호출자는 이 프로세스 수명·정리 계약을 별도로 검증해야 한다.

## 호스트 정책과 한계

기본 허용 host는 `saveticker.com`, `www.saveticker.com`이다. 추가 공개 host는 `ARTICLE_CRAWLER_ALLOWED_HOSTS`에 명시한다. HTTP(S)만 지원하고 credentials가 포함된 URL, 사설·link-local 주소, 금지된 redirect, robots 거부 경로는 처리하지 않는다. robots 확인은 최초 URL과 redirect마다 적용된다. 접근 제한을 우회하거나 cookie·credential을 보내지 않는다.

현재 transport는 HTTP 전용이다. JavaScript 실행이 필요한 페이지는 `needs_browser`, 허용 목록 밖 host는 미지원 상태로 남고 `ready`로 간주하지 않는다. SaveTicker API의 짧은 preview는 source 메타데이터일 뿐 crawl 완료 본문이 아니다. 정상 polling은 기사 페이지를 동기적으로 가져오지 않으며, 명시적인 레거시 helper 호출만 기존 전체 본문 fetch 동작을 유지한다.

## 롤백

문제가 생기면 먼저 `deploy/crontab.stock-report`에서 `reports.article_crawler --limit 20` 한 줄만 비활성화해 source of truth를 변경한다. 그 변경을 승인된 설치 절차로 반영하고 drift 검사로 일치 여부를 확인한 뒤 ingress 기능 변경을 되돌린다. 다른 source 수집, 기존 위키 생성 주기와 runtime 데이터를 삭제하거나 초기화하지 않는다. 재가동 전 `--status`와 drift 검사를 수행하고, 보존된 source 이벤트와 마지막 정상 `ready` 본문을 확인한다.
