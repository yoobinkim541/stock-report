"""위키 온톨로지 — claim 에서 뽑은 타입 있는 개체·관계 그래프.

페이지 저장소(events.jsonl)는 건드리지 않는 파생 산출물이다. 원본은 각 페이지의
``source_knowledge.claims`` 이고, 여기서 만든 것은 모두 다시 만들 수 있다.

- 개체(entity): ``config/wiki-ontology.json`` 시드 + 종목 사전(``ticker_names``) + LLM 이 제안해
  claim 원문에 실제로 나오는 것이 확인된 이름. 타입은 ``ENTITY_TYPES`` 로 닫혀 있다.
- 관계(relation): ``RELATION_TYPES`` 어휘만. 두 개체가 인용한 claim 원문에 함께 나와야 받아들인다
  (모델이 지어낸 연결 차단). 출처(source revision) 수로 asserted/corroborated, 부호가 엇갈리면 contested.
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

SCHEMA_VERSION = 1
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
RELATION_STATES = {"asserted": "단일 출처", "corroborated": "복수 출처", "contested": "엇갈림"}
_OPPOSITE = {"positive_for": "negative_for", "negative_for": "positive_for"}

MAX_ALIASES = 12
MAX_EVIDENCE_PER_RELATION = 24
MAX_CO_MENTIONS = 600
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

def build_extraction_request(pages: list[dict], lexicon: Lexicon) -> tuple[str, dict[str, dict]]:
    """한 번의 LLM 호출에 넣을 프롬프트와 ``c1..cN`` → claim 매핑."""
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
    known_rows = "\n".join(f"- {e['id']} | {ENTITY_TYPES[e['type']]} | {e['name']}" for e in known.values()) or "- (없음)"
    relation_rows = "\n".join(f"- {name}: {label}{' (대칭)' if symmetric else ' (from → to)'}"
                              for name, (label, symmetric) in RELATION_TYPES.items())
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
{chr(10).join(blocks)}
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

JSON schema:
{{"entities": [{{"name": "...", "type": "<entity type>", "aliases": ["..."]}}],
  "relations": [{{"type": "<relation type>", "from": "<known id or new entity name>", "to": "<...>",
                 "claims": ["c1"], "note": "..."}}]}}"""
    return prompt, keymap


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


def compile_ontology(pages: Iterable[dict], extractions: dict | None = None, *, generated_at: str,
                     lexicon: Lexicon | None = None) -> dict:
    """활성 페이지의 claim 과 추출 캐시로 전체 그래프를 만든다. 순수 함수."""
    extractions = extractions or {"pages": {}}
    lexicon = lexicon or seed_lexicon(learned_entities(extractions))
    sources = knowledge_pages(pages)
    claim_index: dict[tuple[str, str], dict] = {}
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"claims": [], "pages": set(), "revisions": set(), "days": []})
    co_mentions: Counter[tuple[str, str]] = Counter()
    extracted_pages = 0
    for page in sources:
        page_id = str(page.get("id"))
        seen_day = _iso_day(page.get("updated_at"), page.get("created_at"))
        record = (extractions.get("pages") or {}).get(page_id) or {}
        extracted_pages += record.get("input_hash") == page_input_hash(page)
        for claim in page_claims(page):
            claim_id = str(claim["claim_id"])
            revision = str(claim.get("source_revision_id") or page_id)
            day = _iso_day(claim.get("as_of")) or seen_day
            mentions = lexicon.match(claim["text"])
            claim_index[(page_id, claim_id)] = {"revision": revision, "day": day, "mentions": mentions,
                                                "competing": bool(claim.get("competing_claim_ids"))}
            for entity_id in mentions:
                row = stats[entity_id]
                row["claims"].append([page_id, claim_id])
                row["pages"].add(page_id)
                row["revisions"].add(revision)
                if day:
                    row["days"].append(day)
            for index, left in enumerate(mentions):
                for right in mentions[index + 1:]:
                    co_mentions[tuple(sorted((left, right)))] += 1

    merged: dict[tuple[str, str, str], dict] = {}
    for page_id, record in (extractions.get("pages") or {}).items():
        for relation in record.get("relations") or []:
            relation_type, source, target = relation.get("type"), relation.get("from"), relation.get("to")
            if relation_type not in RELATION_TYPES or source not in lexicon.entities or target not in lexicon.entities:
                continue
            evidence = [item for item in relation.get("evidence") or []
                        if (item.get("page_id"), item.get("claim_id")) in claim_index]
            if not evidence:
                continue  # 근거 claim 이 사라진 관계는 버린다
            row = merged.setdefault((relation_type, source, target), {"evidence": [], "notes": []})
            for item in evidence:
                pair = [item["page_id"], item["claim_id"]]
                if pair not in row["evidence"]:
                    row["evidence"].append(pair)
            if relation.get("note") and relation["note"] not in row["notes"]:
                row["notes"].append(relation["note"])

    relations = []
    for (relation_type, source, target), row in merged.items():
        facts = [claim_index[(page_id, claim_id)] for page_id, claim_id in row["evidence"]]
        revisions = {fact["revision"] for fact in facts}
        opposite = _OPPOSITE.get(relation_type)
        contested = any(fact["competing"] for fact in facts) or bool(opposite and (opposite, source, target) in merged)
        relations.append({
            "id": relation_id(relation_type, source, target), "type": relation_type, "from": source, "to": target,
            "evidence": row["evidence"][:MAX_EVIDENCE_PER_RELATION], "source_count": len(revisions),
            "state": "contested" if contested else "corroborated" if len(revisions) >= 2 else "asserted",
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
            "relation_count": degree[entity_id],
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
        },
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
            lines.append(f"  · {relation_phrase(ontology, relation)} — {RELATION_STATES.get(relation['state'], relation['state'])}"
                         f", 출처 {relation['source_count']}개")
        insight = by_entity.get(entity["id"].casefold())
        if insight:
            thesis = " ".join(str(insight.get("summary") or "").split())[:300]
            lines.append(f"  · 인사이트(모델 추론, 미검증): {thesis} [[{insight.get('id')}]]")
    return "\n".join(lines) if len(lines) > 1 else ""


def render_markdown(ontology: dict) -> str:
    stats = ontology.get("stats") or {}
    entities = ontology.get("entities") or {}
    lines = [
        "# LLM Wiki Ontology", "", f"Generated: {ontology.get('generated_at', '')}", "",
        f"- 원본: 활성 페이지 {stats.get('pages', 0)}개 · claim {stats.get('claims', 0)}건 "
        f"(관계 추출 완료 {stats.get('extracted_pages', 0)}개 페이지)",
        f"- 개체 {stats.get('entities', 0)}개 · 관계 {stats.get('relations', 0)}개", "",
        "## 스키마", "",
        "개체 타입: " + ", ".join(f"`{name}` {label}" for name, label in ENTITY_TYPES.items()), "",
        "관계 타입: " + ", ".join(f"`{name}` {label}" for name, (label, _) in RELATION_TYPES.items()), "",
        "관계 상태: " + ", ".join(f"`{name}` {label}" for name, label in RELATION_STATES.items()), "",
    ]
    relations = ontology.get("relations") or []
    if relations:
        lines += ["## 관계", ""]
        for relation in relations[:400]:
            note = f" — {relation['notes'][0]}" if relation.get("notes") else ""
            lines.append(f"- {relation_phrase(ontology, relation)} · {relation['state']} · 출처 {relation['source_count']}개"
                         f" · 근거 claim {len(relation['evidence'])}건{note}")
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
