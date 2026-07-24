# Wiki Audit Fixes — P0 + P2 (v1)

## 배경
superpowers audit 결과, Agent Console Wiki가 학습/수집에서 7.2/10.
아래 3개 수정으로 8.5/10까지 올리는 것이 목표.

## Task 1: 제목 품질 개선 (P0 — 10min)
**파일**: `agent_console/wiki.py`

**현재 문제**: `_derive_title()`가 질문 텍스트를 그대로 80자 잘라서 제목으로 씀.
→ "왜 오늘 시장이 떨어졌을까?" 같은 페이지 제목 생성
→ LLM이 생성한 title이 plan에 있으면 우선 사용하지만, 히스틱 폴백 시 질문이 제목이 됨

**수정안**:
1. `_derive_title()` 로직 개선:
   ```python
   def _derive_title(question: str, answer: str) -> str:
       """답변에서 핵심 주제를 추출해 제목으로."""
       # 구조화된 답변 (예: bullet list) 에서 첫 요점 추출
       lines = _clean(answer, 300).splitlines()
       for line in lines:
           stripped = line.strip().lstrip("-•*").strip()
           # 길이가 적당하고 쉼표/콜론이 있으면 좋은 제목
           if 12 <= len(stripped) <= 60 and any(c in stripped for c in (",", ":", "→", "-")):
               return stripped[:80]
       # 1문장 요약 추출 — "은/는/이/가" 앞까지 자르지 말고 첫 문장 사용
       # 또는 question에서 "~방법", "~기준", "~규칙" 패턴 추출
   ```
2. 히스틱 큐레이션 plan에서도 `_derive_title()` 개선된 로직 사용

**기대효과**: 제목이 "수집 주기와 신뢰도 기준" 형태로 개선됨

## Task 2: 소스↔회화 위키 교차 링크 (P0 — 20min)
**파일**: `agent_console/wiki.py`, `reports/source_wiki_curator.py`

**현재 문제**:
- source_wiki_curator가 만든 `source_digest` 페이지와 회화 큐레이션이 만든 `playbook`/`decision` 페이지가 완전히 분리됨
- `build_context_section()`이 회화 위키만 검색, source_digest는 컨텍스트에서 누락
- source curator가 새 페이지 만들 때 기존 회화 위키를 참조하지 않음

**수정안**:
1. `_build_wiki_context_section()`에 `kind="source_digest"` 포함:
   ```python
   # 기존: pages = list_pages(status="all", surface="all", limit=400)
   # change: 이미 모든 kind를 포함 -> 확인해보면 source_digest도 있음. 문제는 별도.
   ```
   → `_build_wiki_context_section()`을 실제 확인해보니 line 1188에서 `list_pages(status="all", surface="all", limit=400)`으로 source_digest도 포함됨.
   → BUT `build_context_section()`에서 `list_pages(query=...)`는 텍스트 검색 기반이므로 source_digest 본문이 query와 매칭되어야 검색됨.
   → **실제 이슈는 source_digest 페이지가 회화와 연결되지 않아 같은 주제여도 서로 링크가 없음**

2. `source_wiki_curator.py`에서 페이지 생성 전 기존 회화 위키 검색해서 links 추가:
   ```python
   # _build_event_wiki_page() 내에서:
   from agent_console.wiki import search_pages  # or list_pages
   existing = list_pages(query=title or topic, surface=surface, limit=3)
   if existing:
       links = [p["id"] for p in existing if p.get("kind") in ("playbook", "decision")]
       # 기존 links와 병합
   ```

3. `auto_curate_from_chat()`에서 source_digest도 후보로 검색:
   ```python
   # line 1087: candidates = list_pages(query=question, surface=surface, limit=5)
   # 이미 'all' kind를 검색하므로 source_digest도 포함됨
   # -> BUT _candidate_score()가 kind=source_digest을 낮게 평가할 가능성
   ```
   → `_candidate_score()`에서 source_digest도 링크 대상이 되도록 kind 편향 수정

**기대효과**: "인플레/고용" 수집 위키를 볼 때 관련 playbook(예: "금리 인하 타이밍")도 함께 표시됨

## Task 3: 중복 방지 (P2 — 15min)
**파일**: `agent_console/wiki.py`

**현재 문제**: `_should_auto_curate()`가 점수 기반이라 동일 주제 페이지가 연속 생성될 수 있음.
예: "레버리지 ETF 롤오버" → 같은 주제로 24h 내 2~3개 페이지 생성됨.

**수정안**:
1. `auto_curate_from_chat()` 진입 직후 빠른 중복 체크:
   ```python
   def _recently_created_dedup(question: str, surface: str, *, hours: int = 24) -> bool:
       """최근 hours 시간 내 비슷한 제목/본문의 페이지가 있으면 skip."""
       recent = list_pages(surface=surface, limit=20)
       cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
       for page in recent:
           created = _parse_dt(page.get("created_at"))
           if created and created < cutoff:
               continue
           # 제목/본문 유사도 체크 (빠른 텍스트 오버랩)
           q_clean = _clean(question, 200).lower()
           p_title = _clean(page.get("title", ""), 200).lower()
           p_summary = _clean(page.get("summary", ""), 300).lower()
           overlap = len(set(q_clean.split()) & set((p_title + " " + p_summary).split()))
           if overlap >= 5:  # 5개 이상 키워드 일치 → 중복 의심
               return True
       return False
   ```
2. `auto_curate_from_chat()` 시작 부분에:
   ```python
   if _recently_created_dedup(question, surface):
       return {"ok": False, "action": "skipped_dedup", "reason": "24h 내 유사 페이지 존재"}
   ```

**기대효과**: 토론 반복 중 같은 내용의 중복 페이지 생성을 방지

## 수정 순서
1. Task 3 (중복 방지) — 가장 단순, 의존성 없음
2. Task 1 (제목 품질) — 독립적
3. Task 2 (교차 링크) — 앞 Task 결과 불필요

## 검증
1. 수정 후 `uv run pytest tests/ -x -q` 통과
2. Python import 검사: `uv run python -c "from agent_console import wiki; wiki.stats()"`
3. 수동 시나리오: 중복 메시지에서 중복 페이지 생성 안 됨 확인
4. `_derive_title("왜 시장이 떨어졌나요?", "핵심은 CPI와 연준 금리 결정입니다.")` 결과 확인
