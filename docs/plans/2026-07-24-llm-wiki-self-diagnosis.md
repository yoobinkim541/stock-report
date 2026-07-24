# LLM Wiki — Self-Diagnosis & Usage Tracking

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** 위키가 스스로 자신의 상태를 진단하고, 사용량을 추적하며, 정기 헬스 체크를 통해 자동으로 개선 결정을 내린다.

**Architecture:** 세 가지 기능을 하나의 일관된 시스템으로 연결:
1. `wiki.stats()` 결과를 `auto_curate_from_chat` LLM 프롬프트에 주입
2. 페이지별 사용량 트래킹 (`lastUsedAt`, `useCount`) → `lint_pages()`에 `zero_usage` 플래그
3. 정기 헬스 체크 cron: 통계 + 린트 + 스테일 정보를 LLM에 넘겨 자동 조치

**Tech Stack:** Python 3.11, pytest, agent_console/wiki.py, agent_console/agent.py, deploy/crontab.stock-report

---

### Task 1: 위키 통계를 LLM 큐레이션 프롬프트에 주입

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`

**Step 1: `_build_wiki_context_section()` 추가**

`agent_console/wiki.py`에 다음 함수 추가:

```python
def _build_wiki_context_section() -> str:
    """LLM이 위키 전체 상태를 인지할 수 있도록 stats + lint 요약을 생성"""
    stats_data = stats()
    lint_data = lint_pages()
    
    lines = ["[현재 위키 상태]"]
    lines.append(f"- 전체 페이지: {stats_data.get('total', 0)}")
    lines.append(f"- 활성: {stats_data.get('status_counts', {}).get('active', 0)}")
    lines.append(f"- Archived: {stats_data.get('status_counts', {}).get('archived', 0)}")
    lines.append(f"- 미검증(unverified): {stats_data.get('trust_counts', {}).get('unverified', 0)}")
    lines.append(f"- 검증됨(source-backed): {stats_data.get('trust_counts', {}).get('source-backed', 0)}")
    
    # 린트 이슈 요약
    lint_issues = lint_data.get("issues", [])
    if lint_issues:
        lines.append(f"- 린트 이슈: {len(lint_issues)}개")
        for issue in lint_issues[:5]:  # 상위 5개
            lines.append(f"  • {issue.get('page_title', '?')}: {issue.get('type', '?')}")
    
    # 페이지 유형 분포
    kind_counts = stats_data.get("kind_counts", {})
    if kind_counts:
        kinds = ", ".join(f"{k}: {c}" for k, c in sorted(kind_counts.items()))
        lines.append(f"- 유형: {kinds}")
    
    return "\n".join(lines)
```

**Step 2: `_build_auto_curation_prompt()`에 위키 컨텍스트 섹션 추가**

현재 프롬프트 상단에 `_build_wiki_context_section()` 결과를 삽입:

```python
def _build_auto_curation_prompt(question, response, existing_pages, pack, history):
    wiki_context = _build_wiki_context_section()
    # 기존 프롬프트 앞에 위키 상태 정보 추가
    prompt_lines = [
        "당신은 위키 큐레이터입니다. 아래 현재 위키 상태를 참고하여 결정하세요.",
        wiki_context,
        "",
        # ... 기존 프롬프트 내용 ...
    ]
```

이렇게 하면 LLM이 "미검증 페이지가 많으니 오늘은 검증 위주로" 또는 "사용량 0인 페이지가 있으니 archived 검토" 같은 메타 결정 가능.

**Step 3: `auto_curate_from_chat()` 호출 시 통계 주입 확인 테스트**

호출 결과에 wiki_context_section 내용이 포함되었는지 확인 (mock LLM으로).

Verify: `cd /home/ubuntu/projects/stock-report && .venv/bin/python -m pytest tests/test_agent_console.py -q -k wiki_context`

---

### Task 2: 페이지 사용량 트래킹 + 제로 유즈 플래그

**Files:**
- Modify: `agent_console/wiki.py`
- Modify: `agent_console/agent.py`
- Modify: `tests/test_agent_console.py`

**Step 1: `track_page_usage(page_id: str, query: str) -> None`**

`wiki.py`에 추가:
```python
def track_page_usage(page_id: str, query: str) -> None:
    """페이지가 LLM 컨텍스트로 제공될 때 호출. useCount 증가, lastUsedAt 갱신."""
    ensure_store()
    page = get_page(page_id)
    if not page:
        return
    now = datetime.now(timezone.utc).isoformat()
    page["useCount"] = page.get("useCount", 0) + 1
    page["lastUsedAt"] = now
    page["lastQuery"] = query[:200]  # 어떤 검색어로 찾아졌는지
    upsert_page(page)
    rebuild_artifacts()
```

**Step 2: `build_context_section()`에서 `track_page_usage()` 호출**

`agent.py`의 `_build_context_section()`에서 위키 검색 결과 각 페이지에 대해 track_page_usage 호출:

```python
def _safe_context_pack(surface):
    pack = ...
    wiki_section = _build_wiki_context(pack)
    # 각 위키 페이지 사용량 트래킹
    if wiki_section:
        for page_id in wiki_section.get("page_ids", []):
            try:
                wiki.track_page_usage(page_id, pack.get("query", ""))
            except Exception:
                pass
    return pack
```

**Step 3: `list_unused_pages(days=30) -> list[dict]`**

`wiki.py`에 추가:
```python
def list_unused_pages(days: int = 30) -> list[dict]:
    """지정된 일수 이상 사용되지 않은 페이지 반환"""
    ensure_store()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    all_pages = list_pages(kind=None)
    unused = []
    for page in all_pages:
        last_used = page.get("lastUsedAt") or page.get("createdAt") or ""
        if last_used < cutoff:
            unused.append(page)
    return unused
```

**Step 4: `lint_pages()`에 `zero_usage` 플래그 추가**

린트 검사에 `zero_usage` 타입 추가. 30일 이상 사용되지 않은 페이지를 플래그.

```python
# lint_pages() 내부
unused = list_unused_pages(30)
for page in unused:
    issues.append({
        "type": "zero_usage",
        "page_id": page["id"],
        "page_title": page.get("title", "?"),
        "last_used": page.get("lastUsedAt") or "never",
        "severity": "minor",
        "suggestion": "이 페이지가 30일간 사용되지 않았습니다. archived 또는 삭제를 고려하세요."
    })
```

**Step 5: `auto_curate_from_chat()`에서 `zero_usage` 페이지를 LLM에 제안**

`_build_auto_curation_prompt()`의 위키 컨텍스트 섹션에 미사용 페이지 정보 포함.

Verify: `cd /home/ubuntu/projects/stock-report && .venv/bin/python -m pytest tests/test_agent_console.py -q -k "usage or unused"`

---

### Task 3: 정기 위키 헬스 체크 cron

**Files:**
- Create: `reports/wiki_health_check.py`
- Create: `tests/test_wiki_health_check.py`
- Modify: `deploy/crontab.stock-report`

**Step 1: `reports/wiki_health_check.py` 생성**

```python
"""
위키 헬스 체크 — 주기적으로 위키 상태를 진단하고 LLM이 개선 결정을 내린다.

사용법:
    uv run python -m reports.wiki_health_check --dry-run
    uv run python -m reports.wiki_health_check
"""
import argparse
import json
import sys
import os

# 상위 디렉토리를 PYTHONPATH에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_console import wiki
from agent_console.shared_memory import ensure_store


def build_health_report(dry_run: bool = False) -> dict:
    """위키 상태를 수집하고 LLM 판단이 필요하면 report에 포함"""
    ensure_store()
    
    stats_data = wiki.stats()
    lint_data = wiki.lint_pages()
    stale_pages = wiki.list_stale_pages(max_age_days=14)
    unused_pages = wiki.list_unused_pages(days=30)
    
    report = {
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "dry_run": dry_run,
        "stats": stats_data,
        "lint_issues": lint_data.get("issues", []),
        "stale_count": len(stale_pages),
        "stale_pages": [
            {"id": p["id"], "title": p.get("title", "?"), "updatedAt": p.get("updatedAt")}
            for p in stale_pages[:10]
        ],
        "unused_count": len(unused_pages),
        "unused_pages": [
            {"id": p["id"], "title": p.get("title", "?"), "lastUsedAt": p.get("lastUsedAt", "never")}
            for p in unused_pages[:10]
        ],
        "recommendations": [],
    }
    
    # 결정적 추천 (LLM 없이 기본 액션)
    if not dry_run:
        archived_count = 0
        for page in stale_pages:
            try:
                result = wiki.archive_stale_pages(max_age_days=14, dry_run=False)
                archived_count = result.get("archived", 0)
            except Exception:
                pass
        if archived_count:
            report["recommendations"].append(f"auto-archived {archived_count} stale pages")
        
        # 60일 이상 미사용 페이지 삭제 제안
        very_unused = [p for p in unused_pages 
                       if (p.get("lastUsedAt") or "2000-01-01") < 
                       (__import__("datetime").datetime.now() - 
                        __import__("datetime").timedelta(days=60)).isoformat()]
        if very_unused:
            # 결정적 삭제는 위험하므로 dry_run 모드에서만 제안
            report["recommendations"].append(
                f"{len(very_unused)} pages unused for 60+ days. Run with --delete-unused to archive them."
            )
    
    return report


def format_report(report: dict) -> str:
    lines = ["[위키 헬스 체크]", f"시간: {report['timestamp']}"]
    if report["dry_run"]:
        lines.append("(DRY RUN — 실제 변경 없음)")
    lines.append("")
    
    stats = report["stats"]
    lines.append(f"전체: {stats.get('total', 0)} 페이지")
    lines.append(f"  활성: {stats.get('status_counts', {}).get('active', 0)}")
    lines.append(f"  Archived: {stats.get('status_counts', {}).get('archived', 0)}")
    lines.append(f"  미검증: {stats.get('trust_counts', {}).get('unverified', 0)}")
    lines.append(f"  스테일(14일+): {report['stale_count']}")
    lines.append(f"  미사용(30일+): {report['unused_count']}")
    lines.append("")
    
    if report["lint_issues"]:
        lines.append(f"린트 이슈: {len(report['lint_issues'])}개")
        for issue in report["lint_issues"][:5]:
            lines.append(f"  • {issue.get('page_title', '?')}: {issue.get('type', '?')}")
        lines.append("")
    
    if report["recommendations"]:
        lines.append("권장 액션:")
        for rec in report["recommendations"]:
            lines.append(f"  • {rec}")
        lines.append("")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="위키 헬스 체크")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 리포트만 출력")
    args = parser.parse_args()
    
    report = build_health_report(dry_run=args.dry_run)
    print(format_report(report))
    return 0 if not report.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
```

**Step 2: 실행 확인**

```bash
uv run python -m reports.wiki_health_check --dry-run
```

Expected: 위키 상태 요약 출력, 실제 변경 없음.

**Step 3: cron에 헬스 체크 추가**

`deploy/crontab.stock-report`:

```
# 위키 헬스 체크 (매 2시간, :15분)
15 */2 * * * cd /home/ubuntu/projects/stock-report && uv run python -m reports.wiki_health_check >> /tmp/wiki_health_check.log 2>&1
```

Verify: `cd /home/ubuntu/projects/stock-report && .venv/bin/python -m pytest tests/test_wiki_health_check.py -q`

---

### Task 4: 헬스 체크에 LLM 게이트 (선택 고도화)

**Files:**
- Modify: `reports/wiki_health_check.py`
- Test: `tests/test_wiki_health_check.py`

**Step 1: `run_llm_health_review(report: dict) -> list[dict]`**

헬스 체크 리포트를 LLM에 보내서 추가 추천을 받음:

```python
def run_llm_health_review(report: dict) -> list[dict]:
    """LLM에게 위키 상태를 보여주고 구체적인 큐레이션 액션 추천받기"""
    from agent_console.agent import _try_llm_prompt
    
    prompt = f"""위키 상태 리포트:
- 전체: {report['stats']['total']}페이지
- 미검증: {report['stats']['trust_counts']['unverified']}
- Archived: {report['stats']['status_counts']['archived']}
- 스테일(14일+): {report['stale_count']}
- 미사용(30일+): {report['unused_count']}

린트 이슈:
{json.dumps(report['lint_issues'][:10], indent=2, ensure_ascii=False)}

다음 중 어떤 액션이 필요할까요?
1. 어떤 페이지를 archived/삭제할까?
2. 어떤 페이지들을 병합할까?
3. 어떤 페이지를 재활성화(unarchive)할까?
4. 전반적인 위키 건강도 평가 (1-10)

JSON으로 응답해주세요:
{{"actions": [{{"page_id": "...", "action": "archive|delete|merge|reactivate", "reason": "..."}}], "health_score": 8, "summary": "..."}}"""
    
    try:
        llm_response = _try_llm_prompt(prompt)
        return json.loads(llm_response).get("actions", [])
    except Exception:
        return []
```

**Step 2: LLM 추천 자동 실행 (dry_run 모드만)**

```python
if not dry_run and llm_actions:
    for action in llm_actions:
        page_id = action.get("page_id")
        act = action.get("action")
        if act == "archive":
            wiki.archive_stale_pages(max_age_days=0)  # 즉시
        elif act == "delete":
            wiki.delete_page(page_id)
        # etc.
```

Verify: `cd /home/ubuntu/projects/stock-report && .venv/bin/python -m pytest tests/test_wiki_health_check.py -q`

---

### Final Verification

```bash
cd /home/ubuntu/projects/stock-report && .venv/bin/python -m pytest tests/test_agent_console.py tests/test_wiki_health_check.py tests/test_wiki_lifecycle.py tests/test_wiki_storage_window.py tests/test_qmd_search.py -q
```

Expected: all existing tests pass + new tests pass. 100+ total.

### Deliverables
1. Working code with passing tests.
2. Each task committed separately.
3. Final output: test results, git log.
