# 위키 온톨로지·인사이트

2026-11-15 추가. 위키는 그동안 페이지(문서)와 claim(문장)만 쌓았고, claim 사이의 관계는 0건이었다
(`source_knowledge.relations` 가 비어 있었다). 이 계층은 claim 에서 **무엇이 무엇과 어떤 관계인지**를 뽑아
그래프로 만들고, 여러 출처에 걸친 claim 을 엮어 **인사이트 페이지**를 쓴다.

## 구성

| 파일 | 역할 |
|---|---|
| `config/wiki-ontology.json` | 시드 개체 사전 (기관·인물·지역·지표·자산·산업·테마·이벤트). 종목은 `ticker_names` 에서 가져온다. |
| `agent_console/wiki_ontology.py` | 스키마, 개체 매칭, LLM 응답 검증, 그래프 컴파일, 조회. LLM 을 부르지 않는다. |
| `reports/wiki_ontology_insights.py` | 배치. 관계 추출 → 반대 검토 → 컴파일 → 인사이트 작성. |
| `reports/source_publishers.py` | 출처 이름 → 발행자·관점 표. 수집 분류와 온톨로지의 독립 출처 계산이 같이 쓴다. |
| `dashboard/wiki_ontology_view.py` | AI 위키 화면의 "온톨로지·인사이트" 구역. |
| `<llm-wiki>/ontology/extractions.json` | 페이지별 관계 추출 캐시 (claim 해시가 같으면 다시 부르지 않는다). |
| `<llm-wiki>/ontology/ontology.json`, `ontology.md` | 컴파일된 그래프와 사람이 읽는 요약. 매 실행마다 전체 재계산. |

그래프는 페이지 레코드에 저장하지 않는다. 원본은 claim 이고 그래프는 언제든 다시 만들 수 있는 파생물이다.
페이지가 보관·삭제되거나 claim 이 바뀌면 그 claim 에 기댄 관계는 다음 컴파일에서 사라진다.

## 스키마

- 개체 타입: `company` `asset` `indicator` `institution` `person` `region` `sector` `theme` `event`
- 관계 타입: `belongs_to` `supplies` `competes_with` `partners_with` `owns` `leads` `regulates`
  `positive_for` `negative_for` `exposed_to` `conflicts_with`
- 관계 상태: `asserted` 발행자 1곳 · `corroborated` 서로 다른 발행자 2곳 이상 · `contested` 경쟁 claim 이 있거나,
  같은 쌍에 `positive_for` 와 `negative_for` 가 함께 있거나, 검토자가 반대 근거 claim 을 댐
- 서술 방식: `reported` 일어난 일·있는 사실로 전해짐 · `view` 누군가의 전망·평가·추천

## 한쪽으로 쏠리지 않게 하는 규칙

2026-11-16 추가. 그 전까지 claim 2,241건이 모두 한 텔레그램 채널에서 왔고, "복수 출처"는 같은 채널의 글 여러
건이었다. 증권사 채널을 더하면서 누가 말했는지와 사실인지 견해인지를 그래프에 남기도록 바꿨다.

- 발행자 단위로 센다. 같은 채널의 글 여러 건, 같은 증권사의 채널 글과 리포트 PDF 는 한 목소리다. 발행자를 모르는
  revision 은 모두 `unknown` 한 곳으로 묶는다. 관계마다 `publishers`, `perspectives`(증권사 리서치·커뮤니티·언론…)를 남긴다.
- 반대 검토 (`build_review_request` → `validate_review`). 추출한 모델과 다른 제공자를 먼저 부르는 검토자가 같은
  claim 을 보고 제안된 관계를 반박한다. 추출 모델의 설명(`note`)은 보여 주지 않는다. 검토자는 판정
  (`keep`·`view`·`reject`)과 닫힌 사유 코드, 반대 근거 claim, 추출 모델이 빠뜨린 관계를 낸다.
- 판정을 어떻게 반영할지는 코드가 정한다.
  - `reject` 는 claim 이 그 관계를 말하지 않는다는 사유(`not_stated` `reversed` `listed_only` `trivial`)가 있을 때만
    받는다. 전망·평가라는 이유만이면 지우지 않고 `view` 로 낮춘다. 사유가 없으면 판정을 버린다.
  - `contradicted` 는 그 관계가 인용하지 않은 다른 claim 을 반대 근거로 댈 때만 받고, 그러면 `contested` 가 된다.
  - 검토자가 빠졌다고 낸 관계는 추출과 같은 근거 검증을 통과해야 들어온다 (`origin: reviewer`). 부호가 반대면
    원래 관계와 함께 `contested` 가 된다.
  - 검토자가 더한 관계도 그대로 믿지 않는다. 추출 쪽 모델이 같은 규칙으로 되검토한다 (`_cross_review`,
    `review.cross`). 되검토는 판정만 내고 관계를 더하지 못하므로 주고받기는 여기서 끝난다. 되검토 전의 추가분은
    `added_unchecked` 로 센다.
  - 추출 모델·검토자 중 한쪽이라도 견해라 했거나, 인용 claim 이 모두 전망·평가 어투면 `view` 다.
  - 기각된 관계는 그래프에서 빠지지만 추출 캐시에는 사유와 함께 남고, `ontology.json` 의 `rejected` 와
    `ontology.md` 의 "검토에서 기각된 관계" 목록으로 사람이 되짚어 볼 수 있다. 기각 사유는 검토 모델의 판단이며
    코드로 다시 검증하지 않는다.
- 검토는 추출 한 번에 한 번이다. claim 이 바뀌어 다시 추출하면 다시 검토한다. 검토 호출이 실패하면 그 페이지는
  대기로 남고 관계는 `unreviewed` 로 그래프에 들어간다.
- `ontology.md` 와 화면은 발행자별 claim 비중과 검토 결과(유지·견해로 낮춤·기각·검토자 추가와 그 되검토·미검토)를
  맨 위에 보여 준다. 검토가 끝난 페이지를 어느 제공자가 추출·검토·되검토했는지도 같이 적는다 (`stats.models`).
- 인사이트 프롬프트는 claim 마다 발행자를 붙이고, 한 발행자의 claim 만 인용한 판단은 그렇게 밝히게 한다.
- 위키 컴파일 대상 선정(`scripts/run_public_wiki_rollout.py`)은 아직 시도하지 않은 증권사 글(`sell_side`)을 먼저
  고르고, 나머지는 발행자별로 번갈아 고른다. 글이 많은 채널 하나가 남은 몫을 다 쓰지 못한다.
  각 발행자 안에서는 아직 시도하지 않은 최신 글부터 고른다. 들어오는 글이 하루 한도보다 많아서 오래된 글부터
  고르면 일주일 지난 글만 들어가고 나머지는 스캔 창 밖으로 밀려난다.
- 하루 한도는 80건이다 (`wiki_public_budget.DEFAULT_LIMIT`, KST 기준). 2026-11-09~15 실측 유입은 증권사 하루 28건
  (평일 약 30건, 월요일 69건), 전달 채널 하루 133건이다. 이 유입을 실제 실행 주기(끝난 뒤 30분, 한 번에 8건)로
  다시 돌리면 80건에서 증권사 글은 모두 들어가고 대기 시간은 중앙값 0.5시간, 90% 0.8시간이다. 60건이면 90% 가
  2.8시간으로 늘고, 100건이면 월요일처럼 몰린 날의 마지막 글까지 한 시간 안에 들어간다 (80건은 최대 6시간).
  한도를 더 올리면 그 밖에는 전달 채널 처리 비율만 는다 (80건 43%, 100건 59%, 120건 75%).
  한 건은 Codex 5시간 창의 약 0.22% 를 쓴다 (정리·라우팅 호출, 2026-11-16 실측). 증권사 글 80건을 넣는 동안
  주간 창은 73% 에서 75% 로, 5시간 창은 8건마다 약 1%p 올랐다 (2026-11-16~17 실측, 실행 요약의 `usage`).
  위 대기 시간은 한 건을 1.5분으로 잡은 값이다. 증권사 글은 제안 호출까지 세 번을 불러 실제로는 한 건에 약 3분,
  8건에 26분이 걸렸다 (2026-11-16 첫 실행). 그래서 서비스 `TimeoutStartSec` 은 60분이어야 한다.
- 시간 배분: 아직 시도하지 않은 증권사 글은 하루 한도 안에서 올라오는 대로 들어간다. 그 밖의 글(전달 채널, 다시
  시도하는 글)은 하루가 지난 만큼만 들어간다 (`wiki_public_budget.paced_limit`: 한도 × KST 하루 중 지난 비율, 최소 1).
  아침에 한도를 다 쓰고 저녁 글을 놓치지 않게 하기 위해서다. 실패한 글은 6시간 뒤에 다시 고른다 (`RETRY_AFTER`).
- 주소와 공백을 뺀 글자가 20자 미만인 새 글은 후보에서 뺀다 (`MIN_TEXT_CHARS`, 요약의 `contentless_sources`).
  링크만 있는 글, 사진 설명("프리마켓 현황"), 시세 한 줄이 여기에 든다. 2026-11-16 실측으로 미국 주식 인사이더 글의
  18% 다. 그보다 긴 잡담은 정리 호출이 `skip` 으로 거른다.
- 실행을 시작할 때 Codex 계정 사용량을 읽는다 (`llm_cli.codex_usage`: `codex app-server` 의
  `account/rateLimits/read`, 모델 호출 없음, 약 1초). 5시간 창을 70% 이상 썼거나, 주간 창을 90% 이상 썼거나, 한도에
  이미 걸렸으면 그 계정을 쓰는 제공자(Codex, Hermes)를 빼고 남은 제공자로만 실행한다 (요약의 `providers`).
  남은 제공자가 없으면 스캔도 예약도 하지 않고 끝낸다 (`skipped: provider_busy`). Codex CLI 와 Hermes `openai-codex` 는
  같은 계정을 쓴다. 한도가 차면 둘 다 답하지 않고, 2026-11-07~13 의 무응답은 모두 이 경우였다 (`usage_limit_reached`).
  사용량을 읽지 못하거나 Codex 가 첫 제공자가 아니면 그대로 실행한다. 실행 요약의 `usage` 에 실행 전후 값이 남는다.
- Hive DeepSeek (`hive`, 모델 `deepseek-ai/deepseek-v4.1-flash-1017`) 는 ChatGPT 계정과 따로 과금되는 제공자다. 키는
  `.env` 의 `HIVE_API_KEY`, 순서는 서비스(`stock-report-public-wiki.service`)의 `WIKI_STRUCTURED_PROVIDERS=codex,hive,hermes` 로 정한다
  (코드 기본 순서 `codex,hermes` 에는 없다). 증권사 글 15건 정리에서 13건이 검증을 통과했고 한 건에 13–31초가 걸렸다. 같은 글에서
  Codex 는 12건 통과, 35–63초였다 (2026-11-17 실측). 그래서 정리(Compile) 호출은 글의 첫 시도에서만 Hive 를 먼저
  부른다. 첫날 밤 80건에서는 Hive 정리의 9건(대부분 `citation_invalid`)이 검증에 걸려 전날 Codex 의 72건 중 2건보다
  많았으므로, 6시간 뒤 재시도는 설정 순서(Codex 먼저)로 다시 정리한다. 라우팅과 통합 제안은 늘 설정 순서를 따른다.
  요금은 100만 토큰당 입력 $0.10, 출력 $0.35 다 (2026-10-26 인하).
  추론은 라우팅 호출에만 켠다 (`low`). 끄면 라우팅이 새 문서를
  제안하지 않고, 정리와 통합 제안은 켜면 검증 통과가 줄거나 제한 시간을 넘는다.
- 라우팅 프롬프트는 후보마다 짧은 이름(`c1`, `c2`…)을 붙이고 모델이 그 이름으로 답한다. 64자리 해시 ID 를 20개씩
  옮겨 적게 하자 한 개를 틀리게 베끼는 일이 잦아, 2026-11-17 밤 80건 중 15건이 `routing_candidates_invalid` 로 끝났다
  (재현 두 건 모두 ID 하나가 빠지고 비슷한 가짜 ID 가 하나 생김). 정확한 페이지 ID 로 답해도 받지만, 같은 후보를 두 이름으로
  분류하거나 없는 이름을 쓰면 여전히 실패한다.
- OpenRouter 는 2026-11-17 에 충전액 $35 를 다 써서 402 만 돌려준다. 위키 순서와 코드 기본 순서에서 뺐다. 다시 쓰려면
  충전한 뒤 `WIKI_STRUCTURED_PROVIDERS` 에 넣는다.
- agy(Antigravity CLI)는 2026-11-15 18:40 UTC 부터 계정이 이용약관 위반으로 막혀 모든 호출이 오류다. 이것을 쓰던 작업
  다섯 개(`NEWS_SPIKE_LLM_PROVIDER`, `NEWS_LLM_LABELS_PROVIDER`, `DASH_LLM_ANALYSIS_PROVIDER`, `DASH_AI_BRIEFING_PROVIDER`,
  `DASH_LLM_RELATED_PROVIDER`)는 `hive` 로 옮겼다. `lib.llm_direct.run` 이 `hermes chat --provider hive` 명령을 Hive 직접
  호출(추론 끔, 응답 형식은 호출부가 검증)로 바꾼다. 직접 호출이 실패하면 Hermes 가 모르는 제공자로 보고 Codex 로 답한다.
  agy 일 때도 같은 일이 나서, 11-17 하루에 51번 Codex 가 대신 답했다.
- 정리(Compile) 호출은 제공자 한 곳에 90초까지 준다 (증권사 글 실측 39–62초, 기본 몫 60초). 전체 180초 상한은 그대로다.
  어느 제공자도 답하지 않은 시도는 하루 한도를 돌려주고 그 실행을 끝낸다. 다음 실행은 다른 글부터 시작한다.
  시도 기록(`automatic-attempts/*.json`)의 `model_calls` 에 호출별 소요 시간과 제공자별 결과
  (`answered`·`no_text`·`not_json`·`late`·`error`·`disabled`)가 남는다. 프롬프트·응답·오류 문구는 남기지 않는다.
- 통합에 실패하면 요약의 `reason_counts` 에 `integration_failed` 와 함께 이유 코드가 남는다. 검증기가 낸 코드
  (`ValueError` 의 식별자, 예: `public_target_changed`)는 그대로, 그 밖의 예외는 `integration_error:<예외 종류>` 로만 남긴다.
  오류 문구는 출처 본문을 담을 수 있어 남기지 않는다.
- 기존 문서를 고칠 수 있는 대상(`public_target_ids`)은 편입 기록이 남긴 상태와 같은 문서뿐이다. 교차 링크를 다시 걸거나
  본문에서 글자를 조금 지운 변경(본문의 10% 이하, 덧붙이거나 순서를 바꾼 글자는 없음)은 같은 문서로 본다. 2026-11-12
  06:40–06:47 UTC 에 편입 문서 252개의 문체를 한꺼번에 고친 일이 있다 (쉼표·이모지 삭제, 영문 머리말 번역). 그중
  지우기만 한 95개(가장 많이 지운 문서가 본문의 9.5%)가 이 규칙으로 다시 대상이 됐고, 글자를 더하거나 바꾼 157개는 계속 빠진다.
- 매일 도는 `reports.wiki_backfill` 은 `source_knowledge.claims` 가 있는 문서(원문에서 정리한 문서)를 다시 쓰지 않는다.
  다시 쓰면 본문이 claim 과 어긋나고 그 문서는 고칠 수 있는 대상에서 빠진다. 이 규칙 전에 편입 문서 35개가 이렇게 빠졌다.

## 지어내지 못하게 막는 규칙

관계 추출 (`validate_extraction`)

- 관계의 양 끝 개체가 인용한 claim 문장에 실제로 등장해야 한다. 아니면 `relation_not_grounded` 로 버린다.
- 새 개체의 이름과 별칭은 claim 에 글자 그대로 있어야 한다.
- 타입이 스키마에 없거나 `leads` 의 주어가 인물이 아니면 버린다.

인사이트 (`validate_insight`)

- 판단(`points`)마다 claim 2건 이상, 서로 다른 출처 2개 이상을 인용해야 한다. 통과한 판단이 2개 미만이면 쓰지 않는다.
- 판단·핵심 문장에 들어간 숫자는 인용한 claim 에 있어야 한다.
- 대상: `region` 을 뺀 개체 중 출처 3개 이상·claim 4건 이상. 보유 종목은 우선순위를 올린다 (티커만 쓰고 수량·금액은 보내지 않는다).
- 다시 쓰는 조건: 새 근거 claim 3건 이상, 관계 변경, 또는 7일 경과.

인사이트 페이지는 `kind: insight`, `status: draft`, 태그 `insight` `entity:<id>` 를 갖는다. 본문은 검증을 통과한
JSON 으로 코드가 조립한다 (핵심 판단 / 근거를 이은 판단 / 엇갈리는 신호 / 시사점 / 다음에 확인할 것 / 관계).
인용한 근거 페이지가 보관되면 린트 `insight_evidence_missing` 이 뜬다.

## 쓰이는 곳

- 챗봇 컨텍스트: `wiki.build_context_section(query=...)` 가 질문에 나온 개체의 관계와 인사이트 한 줄을
  `[위키 온톨로지]` 블록으로 붙인다. 인사이트에는 "모델 추론" 경고가 함께 들어간다.
- 리포트: `wiki.for_report_targets` 가 `ticker:<T>` 태그가 달린 인사이트를 종목 인용 후보에 넣는다.
- `index.md`: 맨 위 "인사이트 (Insights)" 구역.
- 헬스 체크: 온톨로지 수치 한 줄, 컴파일이 24시간 넘게 멈추거나 `index.md` 가 6시간 넘게 뒤처지면 권장 액션에 올린다.

## 실행

```
uv run python -m reports.wiki_ontology_insights            # 점검만 (대기 페이지, 인사이트 후보)
uv run python -m reports.wiki_ontology_insights --apply    # 추출·반대 검토·컴파일·인사이트 작성
```

호출 상한은 `--extract-calls` / `WIKI_ONTOLOGY_EXTRACT_CALLS` (기본 6), `--review-calls` / `WIKI_ONTOLOGY_REVIEW_CALLS`
(기본 6, 반대 검토와 되검토에 각각 적용), `--insights` / `WIKI_INSIGHT_MAX_PER_RUN` (기본 3). 검토자 제공자 순서는 `WIKI_ONTOLOGY_REVIEW_PROVIDERS`
(기본: `WIKI_STRUCTURED_PROVIDERS` 를 한 칸 돌린 순서 — 운영 순서 `codex,hive,hermes` 에서는 Codex 가 추출하면 Hive 가 먼저 검토하고, 응답이 없으면 Hermes 로 넘어간다). 되검토는 추출과 같은 순서로 부른다. 이 잡은 `.env` 를 직접 읽는다 — 읽지 않으면 Codex 가 계정에서 못 쓰는 기본 모델로 떠서 Hermes 혼자 세 역할을 다 한다 (2026-11-16 운영에서 확인). 크론은 6시간마다 (`deploy/crontab.stock-report`, 잠금 `/tmp/wiki_ontology.lock`, 로그 `/tmp/wiki_ontology.log`).
LLM 은 `lib.llm_cli.structured_chat` (`WIKI_STRUCTURED_PROVIDERS` 순서, 운영은 Codex → Hive → Hermes) 를 쓴다. 관계 추출 프롬프트는 claim 30건 묶음이라
Codex 응답이 60초를 넘기도 해서 (운영 실측: 13건 51초, 44건 78초) 공급자별 한도를 120초로 올려 부른다. Codex 호출의 자체 상한(90초)도 이 잡에서는 120초로 맞춘다
(`AGENT_CONSOLE_CODEX_TIMEOUT` 을 따로 정했으면 그 값). 검토 프롬프트 한 건이 77초 걸린 실측이 있다.
전체 180초 상한은 그대로다.

## 한계

- 개체 매칭은 사전 기반이다. 사전에 없는 개체는 LLM 추출이 그 페이지를 지나가야 생긴다.
- 관계와 인사이트의 근거는 공개 텔레그램 채널을 모델이 정리한 claim 이다. 원문 검증을 거치지 않았으므로 가설로만 쓴다.
- 인사이트는 `draft` 로만 만든다. 검토 승격은 사람이 한다.
- 반대 검토도 모델이다. claim 문장만 보고 판단하므로 claim 자체가 원문을 잘못 옮겼으면 잡지 못한다.
- 제안한 제공자는 자기 제안을 판정하지 못한다. 검토는 추출한 제공자를, 되검토는 그 관계를 더한 검토자를 빼고 부른다.
  그래서 검토 묶음은 제안한 제공자가 같은 페이지끼리만 만든다.
  그 제공자가 답했으면 판정을 버리고 (`refused_same_model`) 페이지를 대기로 둔다. 다른 제공자가 모두 죽어 있으면
  검토가 멈추고 관계는 `unreviewed`·`added_unchecked` 로 남는다. 추출 제공자를 기록하기 전의 페이지
  (`extractor` 없음)는 누가 추출했는지 몰라 이 규칙으로 가려내지 못한다.
- 증권사 리포트 PDF 본문은 수집 캐시에는 들어가지만 위키 컴파일 대상은 아니다 (공개 텔레그램 메시지만 받는다).
