"""위키 온톨로지 — claim 에서 뽑은 타입 있는 개체·관계 그래프.

페이지 저장소(events.jsonl)는 건드리지 않는 파생 산출물이다. 원본은 각 페이지의
``source_knowledge.claims`` 이고, 여기서 만든 것은 모두 다시 만들 수 있다.

- 개체(entity): ``config/wiki-ontology.json`` 시드 + 종목 사전(``ticker_names``) + LLM 이 제안해
  claim 원문에 실제로 나오는 것이 확인된 이름. 타입은 ``ENTITY_TYPES`` 로 닫혀 있다.
- 관계(relation): ``RELATION_TYPES`` 어휘만. 두 개체가 인용한 claim 원문에 함께 나와야 받아들인다
  (모델이 지어낸 연결 차단).
- 중립성: 추출한 모델과 다른 검토자가 반대편에서 각 관계를 따진다 (``build_review_request``). 판정은 닫힌
  어휘로만 받고 반영 규칙은 코드에 있다 — 기각은 claim 이 그 관계를 말하지 않을 때만, 전망·평가는 지우지 않고
  ``modality=view`` 로 낮추며, 반대 근거 claim 이 붙으면 contested. 같은 발행자의 글 여러 건은 한 목소리로 세고
  (``reports.source_publishers``), 서로 다른 발행자 2곳 이상이 말해야 corroborated 다.
- 산출물: ``<artifacts>/ontology/{extractions.json,ontology.json,ontology.md}``.

이 모듈은 LLM 을 부르지 않는다. 호출·예산·스케줄은 ``reports.wiki_ontology_insights`` 가 맡는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from reports.source_publishers import ROLES as PERSPECTIVES
from reports.source_publishers import publisher as _publisher

SCHEMA_VERSION = 2
INSIGHT_KIND = "insight"
ENTITY_TAG_PREFIX = "entity:"

ENTITY_TYPES: dict[str, str] = {
    "company": "기업",
    "asset": "자산·지수",
    "indicator": "경제지표",
    "institution": "기관",
    "person": "인물",
    "region": "국가·지역",
    "sector": "산업",
    "theme": "테마",
    "event": "이벤트",
}

# (라벨, 대칭 여부). 방향은 from → to.
RELATION_TYPES: dict[str, tuple[str, bool]] = {
    "belongs_to": ("소속", False),          # 기업·자산 → 산업·테마·지역
    "supplies": ("공급", False),            # 공급자 → 수요자
    "competes_with": ("경쟁", True),
    "partners_with": ("협력·계약", True),
    "owns": ("보유·투자", False),            # 보유자 → 대상
    "leads": ("이끎", False),               # 인물 → 기관·기업·국가
    "regulates": ("규제·정책 대상", False),   # 기관·국가 → 대상
    "positive_for": ("호재·상승 요인", False),  # 원인 → 영향받는 쪽
    "negative_for": ("악재·하락 요인", False),
    "exposed_to": ("노출", False),          # 기업·산업·자산 → 테마·이벤트·지표
    "conflicts_with": ("갈등·대립", True),
}
RELATION_STATES = {"asserted": "단일 발행자", "corroborated": "복수 발행자 일치", "contested": "엇갈림"}
RELATION_MODALITIES = {"reported": "보도·서술", "view": "견해·전망"}
_OPPOSITE = {"positive_for": "negative_for", "negative_for": "positive_for"}
# 검토자가 고를 수 있는 판정과 사유. reject 는 _REJECT_REASONS 중 하나가 있어야 반영된다.
REVIEW_VERDICTS = ("keep", "view", "reject")
REVIEW_REASONS: dict[str, str] = {
    "not_stated": "claim 이 그 관계를 말하지 않음",
    "reversed": "방향이 반대",
    "listed_only": "함께 나열됐을 뿐",
    "trivial": "시장·정책·산업 의미 없음",
    "forecast": "전망·목표·예상",
    "opinion": "평가·추천·홍보성 서술",
    "conditional": "조건부",
    "contradicted": "반대 근거 claim 있음",
}
_REJECT_REASONS = frozenset({"not_stated", "reversed", "listed_only", "trivial"})
_VIEW_REASONS = frozenset({"forecast", "opinion", "conditional"})
UNKNOWN_PUBLISHER = "unknown"
UNKNOWN_MODEL = "unknown"
# 인용 claim 이 모두 이런 어투면 검토자 판정과 무관하게 견해로 적는다. '예상치·전망치' 같은 컨센서스 명사는 뺀다.
_VIEW_WORDING = re.compile(
    r"전망(?!치)|예상(?!치)|예측|추정(?!치)|목표|가능성|우려|것으로\s?보|수\s?있|관측|권고|추천|유망|선호"
    r"|\b(?:forecast\w*|expect(?:s|ed|ing)?|estimat(?:e|es|ed)|targets?|could|might|likely|outlook|guidance"
    r"|recommend\w*|prefer\w*)\b", re.IGNORECASE)

MAX_ALIASES = 12
MAX_EVIDENCE_PER_RELATION = 24
MAX_CO_MENTIONS = 600
MAX_REJECTED_LISTED = 300
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "wiki-ontology.json"
_HANGUL = re.compile(r"[가-힣]")
# 두 글자 한글 별칭(이란·인도·금리…) 뒤에 붙어도 같은 개체로 보는 조사·접미.
_SHORT_SUFFIXES = ("으로", "에서", "에게", "이나", "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "과", "와",
                   "로", "께", "산", "발", "측", "군", "계", "주", "향", "내", "간", "제", "권")


def normalize(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def slug(value: object) -> str:
    text = re.sub(r"[^0-9a-z가-힣]+", "-", normalize(value)).strip("-")
    return text[:48] or hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _usable_alias(alias: str) -> bool:
    return len(alias) >= 2 and not alias.isdigit()


def _hangul_hit_ok(text: str, start: int, end: int, alias: str) -> bool:
    """한글 별칭 경계. 두 글자 별칭은 앞이 한글이면 버리고('인플레이션이란'), 뒤는 조사·접미만 허용('인도량' 제외)."""
    if len(alias) > 2:
        return True
    if start > 0 and "가" <= text[start - 1] <= "힣":
        return False
    tail_end = end
    while tail_end < len(text) and "가" <= text[tail_end] <= "힣":
        tail_end += 1
    tail = text[end:tail_end]
    return not tail or (len(tail) <= 3 and tail.startswith(_SHORT_SUFFIXES))


class Lexicon:
    """개체 사전. ``match`` 는 텍스트에 나온 개체 id 를 등장 순서로 돌려준다."""

    def __init__(self, entities: Iterable[dict] = (), *, etf_tickers: Iterable[str] = (), use_tickers: bool = True):
        self.entities: dict[str, dict] = {}
        self._alias_owner: dict[str, str] = {}
        self._ticker_owner: dict[str, str] = {}
        self._etf = {str(t).upper() for t in etf_tickers}
        self._use_tickers = use_tickers
        self._latin: re.Pattern | None = None
        self._hangul: re.Pattern | None = None
        for entity in entities:
            self.add(entity)

    def add(self, entity: dict) -> str | None:
        entity_id = str(entity.get("id") or "")
        entity_type = str(entity.get("type") or "")
        name = " ".join(str(entity.get("name") or "").split())
        if not entity_id or entity_type not in ENTITY_TYPES or not name:
            return None
        if entity_id in self.entities:
            return entity_id
        aliases = []
        for raw in [name, *(entity.get("aliases") or [])]:
            alias = normalize(raw)
            if _usable_alias(alias) and alias not in aliases and self._alias_owner.get(alias, entity_id) == entity_id:
                aliases.append(alias)
        tickers = [str(ticker).upper() for ticker in entity.get("tickers") or [entity.get("ticker")] if ticker]
        self.entities[entity_id] = {"id": entity_id, "type": entity_type, "name": name,
                                    "aliases": aliases[:MAX_ALIASES + 1], **({"ticker": tickers[0]} if tickers else {})}
        for alias in aliases[:MAX_ALIASES + 1]:
            self._alias_owner[alias] = entity_id
        for ticker in tickers:
            self._ticker_owner[ticker] = entity_id
        self._latin = self._hangul = None
        return entity_id

    def _compile(self) -> None:
        latin = sorted((a for a in self._alias_owner if not _HANGUL.search(a)), key=len, reverse=True)
        hangul = sorted((a for a in self._alias_owner if _HANGUL.search(a)), key=len, reverse=True)
        self._latin = re.compile(r"(?<![0-9a-z])(?:" + "|".join(map(re.escape, latin)) + r")(?![0-9a-z])") if latin else None
        self._hangul = re.compile("|".join(map(re.escape, hangul))) if hangul else None

    def _ticker_entity(self, ticker: str) -> str:
        owner = self._ticker_owner.get(ticker)
        if owner:
            return owner
        import ticker_names

        is_asset = ticker in self._etf or ticker_names.is_macro(ticker) or ticker in getattr(ticker_names, "_KR_ETF", ())
        entity_id = f"{'asset' if is_asset else 'company'}:{ticker}"
        if entity_id not in self.entities:
            name = ticker_names.KR.get(ticker) or (ticker_names.KO.get(ticker) or (None,))[0] or ticker_names.EN.get(ticker) or ticker
            self.entities[entity_id] = {"id": entity_id, "type": "asset" if is_asset else "company",
                                        "name": str(name), "aliases": [], "ticker": ticker}
        return entity_id

    def match(self, text: object) -> list[str]:
        raw = unicodedata.normalize("NFKC", str(text or ""))
        if not raw.strip():
            return []
        if self._latin is None and self._hangul is None:
            self._compile()
        folded = raw.casefold()
        hits: list[tuple[int, str]] = []
        if self._latin is not None:
            hits += [(m.start(), self._alias_owner[m.group(0)]) for m in self._latin.finditer(folded)]
        if self._hangul is not None:
            for m in self._hangul.finditer(folded):
                if _hangul_hit_ok(folded, m.start(), m.end(), m.group(0)):
                    hits.append((m.start(), self._alias_owner[m.group(0)]))
        if self._use_tickers:
            import ticker_names

            for ticker in ticker_names.mentions_in(raw, limit=12):
                hits.append((max(0, raw.find(ticker)), self._ticker_entity(ticker)))
        return list(dict.fromkeys(entity_id for _, entity_id in sorted(hits, key=lambda item: item[0])))

    def resolve(self, value: object) -> str | None:
        """LLM 이 쓴 id·이름을 사전의 개체 id 로. 모르면 None."""
        text = str(value or "").strip()
        if text in self.entities:
            return text
        owner = self._alias_owner.get(normalize(text))
        if owner:
            return owner
        if ":" in text and text.split(":", 1)[0] in {"company", "asset"} and self._use_tickers:
            import ticker_names

            ticker = text.split(":", 1)[1].upper()
            if ticker in ticker_names._ALL_TICKERS:
                return self._ticker_entity(ticker)
        hits = self.match(text)
        return hits[0] if len(hits) == 1 else None


@lru_cache(maxsize=1)
def _seed_config() -> dict:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


def seed_lexicon(learned: Iterable[dict] = ()) -> Lexicon:
    config = _seed_config()
    return Lexicon([*config.get("entities", []), *learned], etf_tickers=config.get("etf_tickers", []))


# ── 페이지·claim ────────────────────────────────────────────────────────

def knowledge_pages(pages: Iterable[dict]) -> list[dict]:
    """온톨로지 원본이 되는 활성 페이지 (claim 이 있고 인사이트·허브가 아닌 것)."""
    return [page for page in pages or [] if isinstance(page, dict) and page.get("status") != "archived"
            and page.get("kind") not in {INSIGHT_KIND, "overview", "source_index"} and page_claims(page)]


def page_claims(page: dict) -> list[dict]:
    knowledge = page.get("source_knowledge")
    if not isinstance(knowledge, dict):
        knowledge = (page.get("raw") or {}).get("source_knowledge") if isinstance(page.get("raw"), dict) else None
    claims = knowledge.get("claims") if isinstance(knowledge, dict) else None
    return [claim for claim in claims or []
            if isinstance(claim, dict) and claim.get("claim_id") and str(claim.get("text") or "").strip()]


def page_input_hash(page: dict) -> str:
    rows = sorted(f"{claim['claim_id']}\x1f{claim['text']}" for claim in page_claims(page))
    return hashlib.sha256("\x1e".join(rows).encode("utf-8")).hexdigest()


def _iso_day(*values: object) -> str:
    for value in values:
        match = re.match(r"(\d{4}-\d{2}-\d{2})", str(value or ""))
        if match:
            return match.group(1)
    return ""


# ── LLM 추출 검증 ───────────────────────────────────────────────────────

def _claim_blocks(pages: list[dict], lexicon: Lexicon) -> tuple[str, dict[str, dict], dict[str, dict]]:
    """프롬프트의 claim 블록, ``c1..cN`` → claim 매핑, claim 에 나온 기존 개체."""
    keymap: dict[str, dict] = {}
    known: dict[str, dict] = {}
    blocks = []
    for page in pages:
        lines = [f"## {str(page.get('title') or '')[:120]}"]
        for claim in page_claims(page):
            key = f"c{len(keymap) + 1}"
            keymap[key] = {"page_id": str(page.get("id")), "claim_id": str(claim["claim_id"]), "text": str(claim["text"])}
            for entity_id in lexicon.match(claim["text"]):
                known[entity_id] = lexicon.entities[entity_id]
            lines.append(f"- [{key}] {' '.join(str(claim['text']).split())[:600]}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks), keymap, known


def _entity_rows(entities: Iterable[dict]) -> str:
    return "\n".join(f"- {e['id']} | {ENTITY_TYPES[e['type']]} | {e['name']}" for e in entities) or "- (없음)"


def _relation_type_rows() -> str:
    return "\n".join(f"- {name}: {label}{' (대칭)' if symmetric else ' (from → to)'}"
                     for name, (label, symmetric) in RELATION_TYPES.items())


def build_extraction_request(pages: list[dict], lexicon: Lexicon) -> tuple[str, dict[str, dict]]:
    """한 번의 LLM 호출에 넣을 프롬프트와 ``c1..cN`` → claim 매핑."""
    claims, keymap, known = _claim_blocks(pages, lexicon)
    known_rows = _entity_rows(known.values())
    relation_rows = _relation_type_rows()
    prompt = f"""You extract a typed knowledge graph from investment-wiki claims. Return ONE JSON object only.

<entity_types>
{chr(10).join(f"- {name}: {label}" for name, label in ENTITY_TYPES.items())}
</entity_types>
<relation_types>
{relation_rows}
</relation_types>
<known_entities>
{known_rows}
</known_entities>
<claims>
{claims}
</claims>

Rules:
- Use a known entity id when the claim names that entity. Declare a new entity only for a specific named
  company, person, institution, country/region, asset, indicator, sector, theme or event that appears
  verbatim in a claim. Never declare generic words (시장, 투자자, 정부, 기업, 채널, 출처, 메시지).
- "name" and every alias must be copied verbatim from the claims. At most 3 aliases.
- A relation needs both entities named in the claims it cites, and the claim text must state that relation.
  Do not infer relations from background knowledge. Cite 1-4 claim keys.
- positive_for / negative_for: "from" is the cause, "to" is what gains or loses (price, earnings, supply, odds).
- Skip trivia with no market, policy, industry or company meaning (a city being in a state, an agency
  being in its country, who attended an event). Being listed together is not a relation: an earnings
  calendar, a top-N ranking or a watchlist relates nothing. An empty "relations" list is valid.
- "note" is one short Korean phrase (max 60 chars) quoting how the claims state the relation.
- "modality": "view" when the claims present the relation as someone's forecast, target, expectation, evaluation
  or recommendation; "reported" when they report it as something that happened or exists.

JSON schema:
{{"entities": [{{"name": "...", "type": "<entity type>", "aliases": ["..."]}}],
  "relations": [{{"type": "<relation type>", "from": "<known id or new entity name>", "to": "<...>",
                 "claims": ["c1"], "note": "...", "modality": "reported|view"}}]}}"""
    return prompt, keymap


def build_review_request(pages: list[dict], relations: list[dict], lexicon: Lexicon, *, additions: bool = True
                         ) -> tuple[str, dict[str, dict], dict[str, dict]]:
    """반대편 검토자에게 보낼 프롬프트, claim 매핑, ``r1..rN`` → 검토 대상 관계.

    검토자는 추출 모델의 설명(note)을 보지 않는다 — 제안된 관계와 claim 원문만으로 판단한다.
    ``additions=False`` 는 검토자가 더한 관계를 되검토하는 단계로, 판정만 받고 새 관계는 받지 않는다.
    """
    claims, keymap, known = _claim_blocks(pages, lexicon)
    key_of = {(row["page_id"], row["claim_id"]): key for key, row in keymap.items()}
    relmap: dict[str, dict] = {}
    rows = []
    for relation in relations:
        keys = [key_of[pair] for item in relation.get("evidence") or []
                if (pair := (item.get("page_id"), item.get("claim_id"))) in key_of]
        if not keys or relation.get("from") not in lexicon.entities or relation.get("to") not in lexicon.entities:
            continue
        relation_key = f"r{len(relmap) + 1}"
        relmap[relation_key] = relation
        for side in ("from", "to"):
            known[relation[side]] = lexicon.entities[relation[side]]
        rows.append(f"- {relation_key}: {relation['type']} | {relation['from']} → {relation['to']} | cites {', '.join(keys)}")
    reason_rows = "\n".join(f"- {name}: {label}" for name, label in REVIEW_REASONS.items())
    missed_rule = """- "missed": relations the claims state that the proposer omitted — first of all the ones that run against the
  proposed relations (the losing side of a positive_for, a stated risk, a competing party). Same grounding as the
  proposer: both entities named in the cited claims, 1-4 claim keys, entity ids from <entities> or exact names
  from the claims. An empty list is valid.""" if additions else """- Additions are closed at this stage: return "entities": [] and "missed": []."""
    prompt = f"""You are the opposing reviewer of a knowledge graph built from investment-wiki claims. Another model
proposed the relations below. Argue the other side: look for every ground on which a proposed relation should NOT
enter the graph as fact, and for what the proposer left out. Return ONE JSON object only.

<relation_types>
{_relation_type_rows()}
</relation_types>
<entities>
{_entity_rows(known.values())}
</entities>
<claims>
{claims}
</claims>
<proposed_relations>
{chr(10).join(rows)}
</proposed_relations>
<reason_codes>
{reason_rows}
</reason_codes>

Rules:
- Judge from the claim text only. No background knowledge, and no benefit of the doubt for the proposer.
- "reject": the cited claims do not state the relation (not_stated), state the opposite direction (reversed), only
  list the two entities together (listed_only), or the relation has no market, policy or industry meaning (trivial).
- "view": the claims do state it, but as a forecast, target or expectation (forecast), as an evaluation,
  recommendation or one-sided promotional framing (opinion), or only under a condition (conditional). A broker's or
  commentator's outlook is a view even when it is worded confidently.
- "keep": the claims report it as something that happened or exists and you found no ground above.
- When ANOTHER claim in <claims> points the opposite way or disputes the relation, add reason "contradicted" and put
  those claim keys in "counter". Never list the relation's own cited claims there.
{missed_rule}
- Give exactly one review per proposed relation.

JSON schema:
{{"reviews": [{{"id": "r1", "verdict": "keep|view|reject", "reasons": ["<reason code>"], "counter": ["c2"]}}],
  "entities": [{{"name": "...", "type": "<entity type>", "aliases": ["..."]}}],
  "missed": [{{"type": "<relation type>", "from": "<id or name>", "to": "<...>", "claims": ["c1"], "note": "...",
              "modality": "reported|view"}}]}}"""
    return prompt, keymap, relmap


def validate_review(payload: object, relmap: dict[str, dict], keymap: dict[str, dict], lexicon: Lexicon) -> dict:
    """검토 응답을 닫힌 규칙으로 걸러 ``{"reviews": {r키: 판정}, "missed": 추출 결과, "rejected": 사유별 수}``.

    - reject 는 claim 이 관계를 말하지 않는다는 사유가 있을 때만 받는다. 전망·평가라는 이유만이면 view 로 낮춘다.
    - contradicted 는 그 관계가 인용하지 않은 다른 claim 을 반대 근거로 댈 때만 받는다.
    - 검토자가 빠졌다고 제안한 관계는 추출과 같은 근거 검증(``validate_extraction``)을 통과해야 한다.
    """
    rejected: Counter[str] = Counter()
    if not isinstance(payload, dict) or not isinstance(payload.get("reviews"), list):
        return {"reviews": {}, "missed": {"entities": [], "relations": [], "rejected": {}},
                "rejected": {"payload_invalid": 1}}
    key_of = {(row["page_id"], row["claim_id"]): key for key, row in keymap.items()}
    reviews: dict[str, dict] = {}
    for item in payload["reviews"]:
        relation = relmap.get(item.get("id")) if isinstance(item, dict) else None
        if relation is None or item.get("verdict") not in REVIEW_VERDICTS or item["id"] in reviews:
            rejected["review_invalid"] += 1
            continue
        verdict = item["verdict"]
        reasons = list(dict.fromkeys(reason for reason in item.get("reasons") or []
                                     if isinstance(reason, str) and reason in REVIEW_REASONS))
        own = {key_of.get((row.get("page_id"), row.get("claim_id"))) for row in relation.get("evidence") or []}
        counter = list(dict.fromkeys(key for key in item.get("counter") or []
                                     if isinstance(key, str) and key in keymap and key not in own))[:4]
        if "contradicted" in reasons and not counter:
            reasons.remove("contradicted")
            rejected["counter_unsupported"] += 1
        if "contradicted" not in reasons:
            counter = []
        if verdict == "reject" and not _REJECT_REASONS.intersection(reasons):
            if not _VIEW_REASONS.intersection(reasons):
                rejected["reject_unfounded"] += 1
                continue
            verdict = "view"
            rejected["reject_downgraded"] += 1
        if verdict == "keep" and _VIEW_REASONS.intersection(reasons):
            verdict = "view"
        reviews[item["id"]] = {
            "verdict": verdict, "reasons": reasons[:4],
            "counter": [{"page_id": keymap[key]["page_id"], "claim_id": keymap[key]["claim_id"]} for key in counter],
        }
    missed = validate_extraction({"entities": payload.get("entities"), "relations": payload.get("missed") or []},
                                 keymap, lexicon)
    proposed = {(relation["type"], relation["from"], relation["to"]) for relation in relmap.values()}
    missed["relations"] = [relation for relation in missed["relations"]
                           if (relation["type"], relation["from"], relation["to"]) not in proposed]
    used = {relation[side] for relation in missed["relations"] for side in ("from", "to")}
    missed["entities"] = [entity for entity in missed["entities"] if entity["id"] in used]
    return {"reviews": reviews, "missed": missed, "rejected": dict(rejected)}


def validate_extraction(payload: object, keymap: dict[str, dict], lexicon: Lexicon) -> dict:
    """LLM 응답에서 근거가 확인되는 개체·관계만 남긴다. 개별 항목만 버리고 사유를 센다."""
    rejected: Counter[str] = Counter()
    if not isinstance(payload, dict):
        return {"entities": [], "relations": [], "rejected": {"payload_invalid": 1}}
    corpus = normalize(" \n ".join(row["text"] for row in keymap.values()))
    local = Lexicon(lexicon.entities.values(), etf_tickers=lexicon._etf, use_tickers=lexicon._use_tickers)
    new_entities: dict[str, dict] = {}
    for item in payload.get("entities") if isinstance(payload.get("entities"), list) else []:
        if not isinstance(item, dict):
            rejected["entity_invalid"] += 1
            continue
        name = " ".join(str(item.get("name") or "").split())
        entity_type = str(item.get("type") or "")
        if entity_type not in ENTITY_TYPES or not 2 <= len(name) <= 60 or name.isdigit():
            rejected["entity_invalid"] += 1
            continue
        if local.resolve(name):
            continue  # 이미 아는 개체
        if normalize(name) not in corpus:
            rejected["entity_not_in_claims"] += 1
            continue
        aliases = [" ".join(str(alias).split()) for alias in item.get("aliases") or [] if isinstance(alias, str)]
        aliases = [alias for alias in aliases if 2 <= len(alias) <= 60 and normalize(alias) in corpus][:3]
        entity = {"id": f"{entity_type}:{slug(name)}", "type": entity_type, "name": name, "aliases": aliases}
        if local.add(entity):
            new_entities[entity["id"]] = entity
    relations = []
    seen: set[tuple] = set()
    for item in payload.get("relations") if isinstance(payload.get("relations"), list) else []:
        if not isinstance(item, dict) or item.get("type") not in RELATION_TYPES:
            rejected["relation_type_invalid"] += 1
            continue
        keys = [key for key in item.get("claims") or [] if isinstance(key, str) and key in keymap][:4]
        source, target = local.resolve(item.get("from")), local.resolve(item.get("to"))
        if not keys or not source or not target or source == target:
            rejected["relation_unresolved"] += 1
            continue
        if item["type"] == "leads" and local.entities[source]["type"] != "person":
            rejected["relation_domain_invalid"] += 1
            continue
        mentioned = {entity_id for key in keys for entity_id in local.match(keymap[key]["text"])}
        if source not in mentioned or target not in mentioned:
            rejected["relation_not_grounded"] += 1
            continue
        if RELATION_TYPES[item["type"]][1] and source > target:
            source, target = target, source
        identity = (item["type"], source, target, tuple(sorted(keys)))
        if identity in seen:
            continue
        seen.add(identity)
        relations.append({
            "type": item["type"], "from": source, "to": target,
            "evidence": [{"page_id": keymap[key]["page_id"], "claim_id": keymap[key]["claim_id"]} for key in keys],
            "note": " ".join(str(item.get("note") or "").split())[:80],
            **({"modality": item["modality"]} if item.get("modality") in RELATION_MODALITIES else {}),
        })
    used = {relation[side] for relation in relations for side in ("from", "to")}
    return {"entities": [entity for entity_id, entity in new_entities.items() if entity_id in used],
            "relations": relations, "rejected": dict(rejected)}


# ── 컴파일 ──────────────────────────────────────────────────────────────

def relation_id(relation_type: str, source: str, target: str) -> str:
    return hashlib.sha256(f"{relation_type}|{source}|{target}".encode("utf-8")).hexdigest()[:16]


def learned_entities(extractions: dict) -> list[dict]:
    rows: dict[str, dict] = {}
    for record in (extractions.get("pages") or {}).values():
        for entity in record.get("entities") or []:
            if isinstance(entity, dict) and entity.get("id"):
                rows.setdefault(entity["id"], entity)
    return list(rows.values())


def needs_review(record: dict, page: dict) -> bool:
    """추출은 끝났는데 반대 검토가 아직 없는 페이지 (검토할 추출 모델 제안이 있는 경우만)."""
    return (record.get("input_hash") == page_input_hash(page)
            and any(relation.get("origin") != "reviewer" for relation in record.get("relations") or [])
            and (record.get("review") or {}).get("input_hash") != record.get("input_hash"))


def needs_cross_review(record: dict, page: dict) -> bool:
    """반대 검토는 끝났는데, 검토자가 더한 관계를 다른 쪽이 아직 되검토하지 않은 페이지."""
    review = record.get("review") or {}
    return (record.get("input_hash") == page_input_hash(page) and review.get("input_hash") == record.get("input_hash")
            and "cross" not in review
            and any(relation.get("origin") == "reviewer" for relation in record.get("relations") or []))


def compile_ontology(pages: Iterable[dict], extractions: dict | None = None, *, generated_at: str,
                     lexicon: Lexicon | None = None, source_providers: dict[str, str] | None = None) -> dict:
    """활성 페이지의 claim 과 추출 캐시로 전체 그래프를 만든다. 순수 함수.

    ``source_providers`` 는 source revision id → 수집 출처 이름(``telegram:kwusa``). 발행자를 모르는 revision 은
    모두 ``unknown`` 한 곳으로 묶는다 — 누가 말했는지 모르는 글끼리는 서로 독립이라고 세지 않는다.
    """
    extractions = extractions or {"pages": {}}
    lexicon = lexicon or seed_lexicon(learned_entities(extractions))
    source_providers = source_providers or {}
    sources = knowledge_pages(pages)
    claim_index: dict[tuple[str, str], dict] = {}
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"claims": [], "pages": set(), "revisions": set(), "publishers": set(), "days": []})
    co_mentions: Counter[tuple[str, str]] = Counter()
    publishers: dict[str, dict] = {}
    review_stats: Counter[str] = Counter()
    # 검토까지 끝난 페이지를 어느 제공자가 추출·검토·되검토했는지. 한 모델이 혼자 다 했으면 여기서 드러난다.
    models: dict[str, Counter[str]] = {"extractor": Counter(), "reviewer": Counter(), "cross": Counter()}
    extracted_pages = 0
    for page in sources:
        page_id = str(page.get("id"))
        seen_day = _iso_day(page.get("updated_at"), page.get("created_at"))
        record = (extractions.get("pages") or {}).get(page_id) or {}
        extracted_pages += record.get("input_hash") == page_input_hash(page)
        if record.get("input_hash") == page_input_hash(page) and (record.get("review") or {}).get("input_hash") == record["input_hash"]:
            review_stats["pages_reviewed"] += 1
            models["extractor"][record.get("extractor") or UNKNOWN_MODEL] += 1
            models["reviewer"][record["review"].get("reviewer") or UNKNOWN_MODEL] += 1
            if "cross" in record["review"]:
                models["cross"][record["review"]["cross"].get("reviewer") or UNKNOWN_MODEL] += 1
        elif needs_review(record, page):
            review_stats["pages_pending"] += 1
        if needs_cross_review(record, page):
            review_stats["pages_cross_pending"] += 1
        for claim in page_claims(page):
            claim_id = str(claim["claim_id"])
            revision = str(claim.get("source_revision_id") or page_id)
            day = _iso_day(claim.get("as_of")) or seen_day
            mentions = lexicon.match(claim["text"])
            provider = source_providers.get(revision)
            who = _publisher(provider) if provider else {"id": UNKNOWN_PUBLISHER, "name": "발행자 미상", "role": "other"}
            publishers.setdefault(who["id"], {**who, "claims": 0})["claims"] += 1
            claim_index[(page_id, claim_id)] = {"revision": revision, "day": day, "mentions": mentions,
                                                "competing": bool(claim.get("competing_claim_ids")),
                                                "publisher": who["id"], "role": who["role"],
                                                "view": _VIEW_WORDING.search(str(claim["text"])) is not None}
            for entity_id in mentions:
                row = stats[entity_id]
                row["claims"].append([page_id, claim_id])
                row["pages"].add(page_id)
                row["revisions"].add(revision)
                row["publishers"].add(who["id"])
                if day:
                    row["days"].append(day)
            for index, left in enumerate(mentions):
                for right in mentions[index + 1:]:
                    co_mentions[tuple(sorted((left, right)))] += 1

    merged: dict[tuple[str, str, str], dict] = {}
    dropped: list[dict] = []
    for page_id, record in (extractions.get("pages") or {}).items():
        for relation in record.get("relations") or []:
            relation_type, source, target = relation.get("type"), relation.get("from"), relation.get("to")
            if relation_type not in RELATION_TYPES or source not in lexicon.entities or target not in lexicon.entities:
                continue
            evidence = [item for item in relation.get("evidence") or []
                        if (item.get("page_id"), item.get("claim_id")) in claim_index]
            if not evidence:
                continue  # 근거 claim 이 사라진 관계는 버린다
            review = relation.get("review") if isinstance(relation.get("review"), dict) else {}
            # 검토자가 더한 관계도 다른 쪽의 되검토를 받는다. 그 전까지는 "added"(되검토 대기)로 센다.
            added = relation.get("origin") == "reviewer"
            verdict = review.get("verdict") or ("added" if added else "unreviewed")
            if added:
                review_stats["added"] += 1
                review_stats["added_rejected"] += verdict == "reject"
                review_stats["added_unchecked"] += verdict == "added"
            else:
                review_stats[verdict] += 1
            if verdict == "reject":
                # 반대편이 claim 에 없는 관계라고 판정 — 그래프에는 넣지 않고, 사람이 되짚어 볼 수 있게 목록으로 남긴다.
                dropped.append({"type": relation_type, "from": source, "to": target,
                                "reasons": list(review.get("reasons") or []), "note": str(relation.get("note") or ""),
                                "evidence": [[item["page_id"], item["claim_id"]] for item in evidence],
                                **({"origin": "reviewer"} if added else {})})
                continue
            row = merged.setdefault((relation_type, source, target),
                                    {"evidence": [], "notes": [], "counter": [], "reported": 0, "verdicts": Counter(),
                                     "reasons": []})
            for item in evidence:
                pair = [item["page_id"], item["claim_id"]]
                if pair not in row["evidence"]:
                    row["evidence"].append(pair)
            if relation.get("note") and relation["note"] not in row["notes"]:
                row["notes"].append(relation["note"])
            # 한 번의 진술이 견해인지: 추출 모델·검토자 중 한쪽이라도 견해라 했거나, 인용 claim 이 모두 전망·평가 어투.
            is_view = (relation.get("modality") == "view" or verdict == "view"
                       or all(claim_index[(item["page_id"], item["claim_id"])]["view"] for item in evidence))
            row["reported"] += not is_view
            row["verdicts"][verdict] += 1
            row["reasons"] += [reason for reason in review.get("reasons") or [] if reason not in row["reasons"]]
            for item in review.get("counter") or []:
                pair = [item.get("page_id"), item.get("claim_id")]
                if tuple(pair) in claim_index and pair not in row["counter"] and pair not in row["evidence"]:
                    row["counter"].append(pair)

    relations = []
    for (relation_type, source, target), row in merged.items():
        facts = [claim_index[(page_id, claim_id)] for page_id, claim_id in row["evidence"]]
        revisions = {fact["revision"] for fact in facts}
        voices = sorted({fact["publisher"] for fact in facts})
        opposite = _OPPOSITE.get(relation_type)
        contested = (any(fact["competing"] for fact in facts) or bool(row["counter"])
                     or bool(opposite and (opposite, source, target) in merged))
        relations.append({
            "id": relation_id(relation_type, source, target), "type": relation_type, "from": source, "to": target,
            "evidence": row["evidence"][:MAX_EVIDENCE_PER_RELATION], "source_count": len(revisions),
            "publisher_count": len(voices), "publishers": voices,
            "perspectives": sorted({fact["role"] for fact in facts}),
            "state": "contested" if contested else "corroborated" if len(voices) >= 2 else "asserted",
            "modality": "reported" if row["reported"] else "view",
            "review": {**dict(row["verdicts"]), **({"reasons": row["reasons"][:4]} if row["reasons"] else {})},
            **({"counter": row["counter"][:8]} if row["counter"] else {}),
            "last_seen": max((fact["day"] for fact in facts), default=""), "notes": row["notes"][:3],
        })
    relations.sort(key=lambda item: (-item["source_count"], item["type"], item["from"], item["to"]))
    degree: Counter[str] = Counter()
    for relation in relations:
        degree[relation["from"]] += 1
        degree[relation["to"]] += 1

    entities = {}
    for entity_id in sorted(set(stats) | set(degree), key=lambda key: (-len(stats[key]["claims"]), key)):
        row, entity = stats[entity_id], lexicon.entities[entity_id]
        entities[entity_id] = {
            "id": entity_id, "type": entity["type"], "name": entity["name"],
            "aliases": [alias for alias in entity.get("aliases") or [] if alias != normalize(entity["name"])],
            **({"ticker": entity["ticker"]} if entity.get("ticker") else {}),
            "claim_count": len(row["claims"]), "page_count": len(row["pages"]), "source_count": len(row["revisions"]),
            "publisher_count": len(row["publishers"]), "relation_count": degree[entity_id],
            "first_seen": min(row["days"], default=""), "last_seen": max(row["days"], default=""),
            "claims": row["claims"],
        }
    pairs = [[left, right, count] for (left, right), count in co_mentions.most_common(MAX_CO_MENTIONS) if count >= 2]
    return {
        "schema_version": SCHEMA_VERSION, "generated_at": generated_at,
        "stats": {
            "pages": len(sources), "claims": len(claim_index), "extracted_pages": int(extracted_pages),
            "entities": len(entities), "relations": len(relations),
            "entity_types": dict(Counter(entity["type"] for entity in entities.values())),
            "relation_types": dict(Counter(relation["type"] for relation in relations)),
            "relation_states": dict(Counter(relation["state"] for relation in relations)),
            "relation_modalities": dict(Counter(relation["modality"] for relation in relations)),
            "review": {key: count for key, count in review_stats.items() if count},
            "models": {role: dict(counts) for role, counts in models.items() if counts},
            "perspectives": dict(Counter(fact["role"] for fact in claim_index.values())),
        },
        "publishers": dict(sorted(publishers.items(), key=lambda item: -item[1]["claims"])),
        "rejected": dropped[:MAX_REJECTED_LISTED],
        "entities": entities, "relations": relations, "co_mentions": pairs,
    }


# ── 저장·조회 ───────────────────────────────────────────────────────────

def ontology_dir() -> Path:
    from agent_console import wiki

    return wiki.wiki_artifacts_dir() / "ontology"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_extractions(root: Path | None = None) -> dict:
    path = (root or ontology_dir()) / "extractions.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": SCHEMA_VERSION, "pages": {}}
    return data if isinstance(data, dict) and isinstance(data.get("pages"), dict) else {"schema_version": SCHEMA_VERSION, "pages": {}}


def save_extractions(extractions: dict, root: Path | None = None) -> None:
    _write_atomic((root or ontology_dir()) / "extractions.json", json.dumps(extractions, ensure_ascii=False))


def save_ontology(ontology: dict, root: Path | None = None) -> Path:
    root = root or ontology_dir()
    _write_atomic(root / "ontology.json", json.dumps(ontology, ensure_ascii=False))
    _write_atomic(root / "ontology.md", render_markdown(ontology))
    return root


_LOADED: dict[str, tuple[tuple[int, int], dict, Lexicon]] = {}


def load_ontology(root: Path | None = None) -> dict | None:
    loaded = _load(root)
    return loaded[0] if loaded else None


def _load(root: Path | None = None) -> tuple[dict, Lexicon] | None:
    path = (root or ontology_dir()) / "ontology.json"
    try:
        stat = path.stat()
    except OSError:
        return None
    # 원자적 교체는 매번 새 inode 를 만든다 — 같은 틱 안에 두 번 저장돼도 구분된다.
    version = (stat.st_mtime_ns, stat.st_ino)
    cached = _LOADED.get(str(path))
    if cached and cached[0] == version:
        return cached[1], cached[2]
    try:
        ontology = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(ontology, dict) or not isinstance(ontology.get("entities"), dict):
        return None
    lexicon = Lexicon(ontology["entities"].values(), etf_tickers=_seed_config().get("etf_tickers", []))
    _LOADED[str(path)] = (version, ontology, lexicon)
    return ontology, lexicon


def entity_relations(ontology: dict, entity_id: str) -> list[dict]:
    return [relation for relation in ontology.get("relations") or []
            if entity_id in (relation.get("from"), relation.get("to"))]


def relation_phrase(ontology: dict, relation: dict) -> str:
    entities = ontology.get("entities") or {}
    label, symmetric = RELATION_TYPES.get(relation.get("type"), (str(relation.get("type")), False))
    left = (entities.get(relation.get("from")) or {}).get("name") or relation.get("from")
    right = (entities.get(relation.get("to")) or {}).get("name") or relation.get("to")
    return f"{left} {'↔' if symmetric else '→'} {right} [{label}]"


def relation_label(relation: dict) -> str:
    """관계 한 줄 요약: 상태 · (견해) · 발행자 수 · 출처 수. 발행자 정보가 없는 옛 산출물도 읽는다."""
    parts = [RELATION_STATES.get(relation.get("state"), str(relation.get("state")))]
    if relation.get("modality") == "view":
        parts.append(RELATION_MODALITIES["view"])
    if relation.get("publisher_count"):
        parts.append(f"발행자 {relation['publisher_count']}곳")
    parts.append(f"출처 {relation.get('source_count', 0)}개")
    return " · ".join(parts)


def find_entities(text: object, *, limit: int = 3, root: Path | None = None) -> list[dict]:
    """질문·문장에 나온 개체를 온톨로지에서 찾는다 (근거가 많은 순)."""
    loaded = _load(root)
    if not loaded or not str(text or "").strip():
        return []
    ontology, lexicon = loaded
    hits = [ontology["entities"][entity_id] for entity_id in lexicon.match(text) if entity_id in ontology["entities"]]
    hits.sort(key=lambda entity: (-entity.get("relation_count", 0), -entity.get("source_count", 0)))
    return hits[:max(0, limit)]


def insight_entity_id(page: dict) -> str:
    """인사이트가 다루는 개체 id. 태그는 저장하면서 소문자가 되므로 (`company:NVDA` → `company:nvda`) 메타를 먼저 본다."""
    knowledge = page.get("source_knowledge")
    meta = knowledge.get("insight") if isinstance(knowledge, dict) else None
    if isinstance(meta, dict) and meta.get("entity_id"):
        return str(meta["entity_id"])
    for tag in page.get("tags") or []:
        if str(tag).startswith(ENTITY_TAG_PREFIX):
            return str(tag)[len(ENTITY_TAG_PREFIX):]
    return ""


def context_section(query: object, *, insights: Iterable[dict] = (), limit: int = 3, root: Path | None = None) -> str:
    """LLM 프롬프트에 넣을 온톨로지 블록. 질문에 나온 개체의 관계와 연결된 인사이트만."""
    entities = find_entities(query, limit=limit, root=root)
    if not entities:
        return ""
    ontology = load_ontology(root) or {}
    by_entity = {insight_entity_id(page).casefold(): page for page in insights or [] if page.get("status") != "archived"}
    lines = ["[위키 온톨로지]"]
    for entity in entities:
        lines.append(f"- {entity['name']} ({ENTITY_TYPES.get(entity['type'], entity['type'])} · {entity['id']}): "
                     f"claim {entity.get('claim_count', 0)}건 · 출처 {entity.get('source_count', 0)}개")
        for relation in entity_relations(ontology, entity["id"])[:4]:
            lines.append(f"  · {relation_phrase(ontology, relation)} — {relation_label(relation)}")
        insight = by_entity.get(entity["id"].casefold())
        if insight:
            thesis = " ".join(str(insight.get("summary") or "").split())[:300]
            lines.append(f"  · 인사이트(모델 추론, 미검증): {thesis} [[{insight.get('id')}]]")
    return "\n".join(lines) if len(lines) > 1 else ""


def models_label(stats: dict) -> str:
    """검토가 끝난 페이지를 맡은 제공자: ``추출 codex 61 → 검토 hermes 61 → 되검토 codex 40``."""
    models = stats.get("models") or {}
    parts = [f"{label} " + "·".join(f"{'기록 없음' if name == UNKNOWN_MODEL else name} {count}"
                                    for name, count in sorted(models[role].items(), key=lambda item: -item[1]))
             for role, label in (("extractor", "추출"), ("reviewer", "검토"), ("cross", "되검토")) if models.get(role)]
    return " → ".join(parts)


def _neutrality_lines(ontology: dict) -> list[str]:
    """누가 말한 것인지와 반대 검토 결과. 한 발행자에 쏠린 그래프를 쏠린 그대로 보여 준다."""
    stats = ontology.get("stats") or {}
    publishers = ontology.get("publishers") or {}
    if not publishers:
        return []
    total = sum(row.get("claims", 0) for row in publishers.values()) or 1
    review = stats.get("review") or {}
    lines = ["## 출처 구성과 검토", "",
             "- 발행자: " + ", ".join(
                 f"{row.get('name', key)}({PERSPECTIVES.get(row.get('role'), row.get('role'))}) "
                 f"{row.get('claims', 0)}건 {row.get('claims', 0) / total:.0%}"
                 for key, row in list(publishers.items())[:12]),
             (f"- 반대 검토: 완료 {review.get('pages_reviewed', 0)}개 페이지 · 대기 {review.get('pages_pending', 0)}개 — "
              f"유지 {review.get('keep', 0)} · 견해로 낮춤 {review.get('view', 0)} · 기각 {review.get('reject', 0)} · "
              f"검토자가 추가 {review.get('added', 0)}"
              f"(되검토에서 기각 {review.get('added_rejected', 0)} · 되검토 대기 {review.get('added_unchecked', 0)})"
              f" · 미검토 {review.get('unreviewed', 0)}")]
    if stats.get("models"):
        lines.append(f"- 맡은 모델 (검토가 끝난 페이지 수): {models_label(stats)}. 제안한 모델은 자기 제안을 판정하지 않는다.")
    return [*lines, ""]


def render_markdown(ontology: dict) -> str:
    stats = ontology.get("stats") or {}
    entities = ontology.get("entities") or {}
    lines = [
        "# LLM Wiki Ontology", "", f"Generated: {ontology.get('generated_at', '')}", "",
        f"- 원본: 활성 페이지 {stats.get('pages', 0)}개 · claim {stats.get('claims', 0)}건 "
        f"(관계 추출 완료 {stats.get('extracted_pages', 0)}개 페이지)",
        f"- 개체 {stats.get('entities', 0)}개 · 관계 {stats.get('relations', 0)}개", "",
        *_neutrality_lines(ontology),
        "## 스키마", "",
        "개체 타입: " + ", ".join(f"`{name}` {label}" for name, label in ENTITY_TYPES.items()), "",
        "관계 타입: " + ", ".join(f"`{name}` {label}" for name, (label, _) in RELATION_TYPES.items()), "",
        "관계 상태: " + ", ".join(f"`{name}` {label}" for name, label in RELATION_STATES.items()), "",
        "서술 방식: " + ", ".join(f"`{name}` {label}" for name, label in RELATION_MODALITIES.items()), "",
    ]
    relations = ontology.get("relations") or []
    if relations:
        lines += ["## 관계", ""]
        for relation in relations[:400]:
            note = f" — {relation['notes'][0]}" if relation.get("notes") else ""
            lines.append(f"- {relation_phrase(ontology, relation)} · {relation_label(relation)}"
                         f" · 근거 claim {len(relation['evidence'])}건{note}")
        lines.append("")
    rejected = ontology.get("rejected") or []
    if rejected:
        lines += ["## 검토에서 기각된 관계", "",
                  "한쪽 모델이 제안했지만 반대편 모델이 claim 에 없다고 본 관계다. 그래프와 인사이트에는 쓰지 않는다.", ""]
        for relation in rejected[:120]:
            reasons = ", ".join(REVIEW_REASONS.get(reason, reason) for reason in relation.get("reasons") or [])
            who = "검토자" if relation.get("origin") == "reviewer" else "추출 모델"
            note = f" — {who} 설명: {relation['note']}" if relation.get("note") else f" — {who} 제안"
            lines.append(f"- {relation_phrase(ontology, relation)} · {reasons}{note}")
        lines.append("")
    for entity_type, label in ENTITY_TYPES.items():
        rows = [entity for entity in entities.values() if entity["type"] == entity_type]
        if not rows:
            continue
        lines += [f"## {label} ({entity_type})", ""]
        for entity in rows[:150]:
            lines.append(f"- **{entity['name']}** `{entity['id']}` — claim {entity['claim_count']}건 · 출처 {entity['source_count']}개"
                         f" · 관계 {entity['relation_count']}개 · 최근 {entity.get('last_seen') or '날짜 없음'}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
