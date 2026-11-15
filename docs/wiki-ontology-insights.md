# 위키 온톨로지·인사이트

2026-11-15 추가. 위키는 그동안 페이지(문서)와 claim(문장)만 쌓았고, claim 사이의 관계는 0건이었다
(`source_knowledge.relations` 가 비어 있었다). 이 계층은 claim 에서 **무엇이 무엇과 어떤 관계인지**를 뽑아
그래프로 만들고, 여러 출처에 걸친 claim 을 엮어 **인사이트 페이지**를 쓴다.

## 구성

| 파일 | 역할 |
|---|---|
| `config/wiki-ontology.json` | 시드 개체 사전 (기관·인물·지역·지표·자산·산업·테마·이벤트). 종목은 `ticker_names` 에서 가져온다. |
| `agent_console/wiki_ontology.py` | 스키마, 개체 매칭, LLM 응답 검증, 그래프 컴파일, 조회. LLM 을 부르지 않는다. |
| `reports/wiki_ontology_insights.py` | 배치. 관계 추출 → 컴파일 → 인사이트 작성. |
| `dashboard/wiki_ontology_view.py` | AI 위키 화면의 "온톨로지·인사이트" 구역. |
| `<llm-wiki>/ontology/extractions.json` | 페이지별 관계 추출 캐시 (claim 해시가 같으면 다시 부르지 않는다). |
| `<llm-wiki>/ontology/ontology.json`, `ontology.md` | 컴파일된 그래프와 사람이 읽는 요약. 매 실행마다 전체 재계산. |

그래프는 페이지 레코드에 저장하지 않는다. 원본은 claim 이고 그래프는 언제든 다시 만들 수 있는 파생물이다.
페이지가 보관·삭제되거나 claim 이 바뀌면 그 claim 에 기댄 관계는 다음 컴파일에서 사라진다.

## 스키마

- 개체 타입: `company` `asset` `indicator` `institution` `person` `region` `sector` `theme` `event`
- 관계 타입: `belongs_to` `supplies` `competes_with` `partners_with` `owns` `leads` `regulates`
  `positive_for` `negative_for` `exposed_to` `conflicts_with`
- 관계 상태: `asserted` 출처 1개 · `corroborated` 출처 2개 이상 · `contested` 경쟁 claim 이 있거나 같은 쌍에
  `positive_for` 와 `negative_for` 가 함께 있음

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
uv run python -m reports.wiki_ontology_insights --apply    # 추출·컴파일·인사이트 작성
```

호출 상한은 `--extract-calls` / `WIKI_ONTOLOGY_EXTRACT_CALLS` (기본 6), `--insights` / `WIKI_INSIGHT_MAX_PER_RUN`
(기본 3). 크론은 6시간마다 (`deploy/crontab.stock-report`, 잠금 `/tmp/wiki_ontology.lock`, 로그 `/tmp/wiki_ontology.log`).
LLM 은 `lib.llm_cli.structured_chat` (Codex → OpenRouter → Hermes) 를 쓴다. 관계 추출 프롬프트는 claim 30건 묶음이라
Codex 응답이 60초를 넘기도 해서 (운영 실측: 13건 51초, 44건 78초) 공급자별 한도를 120초로 올려 부른다.
전체 180초 상한은 그대로다.

## 한계

- 개체 매칭은 사전 기반이다. 사전에 없는 개체는 LLM 추출이 그 페이지를 지나가야 생긴다.
- 관계와 인사이트의 근거는 공개 텔레그램 채널을 모델이 정리한 claim 이다. 원문 검증을 거치지 않았으므로 가설로만 쓴다.
- 인사이트는 `draft` 로만 만든다. 검토 승격은 사람이 한다.
