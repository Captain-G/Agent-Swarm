"""
Per-chunk knowledge extraction (GraphRAG step 1).

The LLM reads one chunk of evidence and returns entities, relations,
arguments, checkable facts and circulating rumours. A transparent heuristic
extractor is used when no model is available.
"""

from __future__ import annotations

import re
from collections import Counter

from apps.core.llm import LLMError, as_float, as_str, as_str_list
from apps.core.text.sentiment import emotion as lexicon_emotion
from apps.core.text.sentiment import sentiment
from apps.core.text.textutils import STOPWORDS, sentences, truncate

ENTITY_TYPES = [
    "person", "government", "legislature", "political_party", "organization", "business", "union",
    "civil_society", "religious", "media", "international", "group", "policy", "location", "event",
]
RELATION_TYPES = [
    "SUPPORTS", "OPPOSES", "CRITICIZES", "PROPOSES", "IMPLEMENTS", "AFFECTS", "BENEFITS", "HARMS",
    "REGULATES", "PART_OF", "ALLIED_WITH", "REPORTS_ON", "FUNDS", "RELATED_TO",
]
EMOTIONS = ["anger", "fear", "sadness", "disgust", "joy", "hope", "trust", "neutral"]

TYPE_SYNONYMS = {
    "government body": "government", "government agency": "government", "ministry": "government", "agency": "government",
    "regulator": "government", "authority": "government", "state": "government", "county government": "government",
    "parliament": "legislature", "senate": "legislature", "assembly": "legislature",
    "party": "political_party", "political party": "political_party", "coalition": "political_party",
    "company": "business", "corporation": "business", "bank": "business", "firm": "business", "private sector": "business",
    "trade union": "union", "association": "union", "federation": "union",
    "ngo": "civil_society", "civil society": "civil_society", "advocacy group": "civil_society", "think tank": "civil_society",
    "church": "religious", "religious organization": "religious", "religious body": "religious",
    "newspaper": "media", "media outlet": "media", "tv": "media", "broadcaster": "media", "journalist": "person",
    "people": "group", "citizens": "group", "demographic": "group", "population group": "group", "community": "group",
    "law": "policy", "bill": "policy", "act": "policy", "tax": "policy", "levy": "policy", "regulation": "policy", "program": "policy", "programme": "policy",
    "country": "location", "county": "location", "city": "location", "region": "location", "place": "location",
    "org": "organization", "institution": "organization", "international organization": "international",
}

EXTRACT_SYSTEM = """You extract structured knowledge for a public-opinion simulation about "{title}" in {country}.
Be faithful to the text: never add facts that are not in it. Reply with JSON only."""

EXTRACT_USER = """TOPIC: {title}
QUESTION: {question}
TEXT ({label}):
\"\"\"{text}\"\"\"

Extract from the TEXT:
- "entities": up to 10 important actors or things. "type" is one of: {types}. "stance" = the entity's position on the topic from -1 (against) to 1 (for), or null if unknown.
- "relations": up to 8 links between those entities. "type" is one of: {relations}.
- "arguments": up to 6 arguments for or against the topic. "side" is support, oppose or neutral; "by" = who makes it; "emotion" is one of {emotions}; "strength" 0-1 = how persuasive it is likely to be for ordinary people.
- "facts": up to 6 concrete, checkable facts (numbers, dates, decisions) about the topic.
- "rumors": unverified, misleading or false claims circulating about the topic (often empty).

Return JSON exactly like:
{{"entities":[{{"name":"","type":"","description":"","stance":null}}],"relations":[{{"source":"","target":"","type":"","description":""}}],"arguments":[{{"claim":"","side":"oppose","by":"","emotion":"anger","strength":0.6}}],"facts":[""],"rumors":[]}}"""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "stance": {"type": ["number", "null"]},
                },
                "required": ["name", "type"],
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["source", "target", "type"],
            },
        },
        "arguments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "side": {"type": "string"},
                    "by": {"type": "string"},
                    "emotion": {"type": "string"},
                    "strength": {"type": "number"},
                },
                "required": ["claim", "side"],
            },
        },
        "facts": {"type": "array", "items": {"type": "string"}},
        "rumors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["entities", "relations", "arguments", "facts"],
}


def normalize_type(value: str) -> str:
    t = as_str(value).lower().replace("-", " ").replace("_", " ").strip()
    if t.replace(" ", "_") in ENTITY_TYPES:
        return t.replace(" ", "_")
    return TYPE_SYNONYMS.get(t, "organization" if t else "other")


def normalize_side(value: str) -> str:
    v = as_str(value).lower()
    if v.startswith(("supp", "for", "pro", "favour", "favor")):
        return "support"
    if v.startswith(("opp", "against", "anti", "crit", "reject")):
        return "oppose"
    return "neutral"


def normalize_extraction(data: dict, chunk_id: int | None = None, source_id: int | None = None) -> dict:
    data = data if isinstance(data, dict) else {}
    entities = []
    for e in data.get("entities") or []:
        if not isinstance(e, dict):
            continue
        name = as_str(e.get("name"), max_len=160)
        if len(name) < 2 or name.lower() in STOPWORDS:
            continue
        stance = e.get("stance")
        entities.append(
            {
                "name": name,
                "type": normalize_type(e.get("type")),
                "description": as_str(e.get("description"), max_len=400),
                "stance": None if stance in (None, "", "null") else as_float(stance, 0.0, -1.0, 1.0),
            }
        )
    relations = []
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        src, tgt = as_str(r.get("source"), max_len=160), as_str(r.get("target"), max_len=160)
        if not src or not tgt or src.lower() == tgt.lower():
            continue
        rtype = re.sub(r"[^A-Z_]", "_", as_str(r.get("type"), "RELATED_TO").upper().replace(" ", "_")).strip("_")
        relations.append({"source": src, "target": tgt, "type": rtype if rtype in RELATION_TYPES else "RELATED_TO", "description": as_str(r.get("description"), max_len=300)})
    arguments = []
    for a in data.get("arguments") or []:
        if not isinstance(a, dict):
            a = {"claim": as_str(a)}
        claim = as_str(a.get("claim"), max_len=400)
        if len(claim) < 12:
            continue
        emo = as_str(a.get("emotion"), "neutral").lower()
        arguments.append(
            {
                "claim": claim,
                "side": normalize_side(a.get("side")),
                "by": as_str(a.get("by"), max_len=120),
                "emotion": emo if emo in EMOTIONS else "neutral",
                "strength": as_float(a.get("strength"), 0.5, 0.0, 1.0),
                "chunk": chunk_id,
                "source": source_id,
            }
        )
    facts = [{"fact": f, "chunk": chunk_id, "source": source_id} for f in as_str_list(data.get("facts"), 8, 300) if len(f) > 15]
    rumors = [{"rumor": r, "chunk": chunk_id, "source": source_id} for r in as_str_list(data.get("rumors"), 5, 300) if len(r) > 15]
    return {"entities": entities[:12], "relations": relations[:10], "arguments": arguments[:8], "facts": facts, "rumors": rumors}


def llm_extract(llm, chunk_text: str, *, title: str, question: str, country: str, label: str, chunk_id=None, source_id=None) -> dict:
    data = llm.json(
        system=EXTRACT_SYSTEM.format(title=title, country=country),
        user=EXTRACT_USER.format(
            title=title,
            question=truncate(question, 300),
            label=label,
            text=truncate(chunk_text, 2200),
            types=", ".join(ENTITY_TYPES),
            relations=", ".join(RELATION_TYPES),
            emotions=", ".join(EMOTIONS),
        ),
        purpose="extract",
        tier="fast",
        schema=EXTRACT_SCHEMA,
        max_tokens=1100,
        temperature=0.1,
    )
    return normalize_extraction(data, chunk_id, source_id)


# ---------------------------------------------------------------------------
# Heuristic extraction (no LLM)
# ---------------------------------------------------------------------------

_CAP_PHRASE = re.compile(r"\b((?:[A-Z][a-zA-Z'&\-]+|of|for|and|the)(?:\s+(?:[A-Z][a-zA-Z'&\-]+|of|for|and|the)){0,5})\b")
_ACRONYM = re.compile(r"\b([A-Z]{2,7})\b")
TYPE_HINTS = [
    (re.compile(r"\b(ministry|treasury|authority|commission|agency|department|cabinet|state house|government|county|regulator|board|bureau|office of)\b", re.I), "government"),
    (re.compile(r"\b(parliament|senate|national assembly|committee|mps?)\b", re.I), "legislature"),
    (re.compile(r"\b(party|movement|alliance|coalition|azimio|kenya kwanza|anc|apc|pdp|npp|ndc)\b", re.I), "political_party"),
    (re.compile(r"\b(union|association|federation|chamber|guild|cotu|knut|kmpdu|sacco)\b", re.I), "union"),
    (re.compile(r"\b(bank|ltd|limited|plc|company|group|safaricom|airtel|mtn|equity|kcb|holdings)\b", re.I), "business"),
    (re.compile(r"\b(church|council of churches|catholic|bishops|supkem|mosque|diocese|imams?)\b", re.I), "religious"),
    (re.compile(r"\b(tv|radio|news|daily|standard|nation|citizen|times|post|herald|star|fm|media|broadcasting|bbc|reuters)\b", re.I), "media"),
    (re.compile(r"\b(imf|world bank|united nations|un|african union|eac|ecowas|usaid|eu)\b", re.I), "international"),
    (re.compile(r"\b(bill|act|levy|tax|policy|programme|program|fund|scheme|regulations?)\b", re.I), "policy"),
    (re.compile(r"\b(society|institute|foundation|ngo|watch|transparency|amnesty|initiative|network|centre|center)\b", re.I), "civil_society"),
]
COMMON_PLACES = {"kenya", "nairobi", "mombasa", "kisumu", "nakuru", "eldoret", "nigeria", "lagos", "abuja", "ghana", "accra", "uganda", "kampala", "tanzania", "dar es salaam", "rwanda", "kigali", "ethiopia", "addis ababa", "south africa", "johannesburg", "cape town", "africa", "east africa"}
OPPOSE_CUES = re.compile(r"\b(oppos|reject|against|unfair|burden|punitive|criticis|criticiz|condemn|warn|protest|hurt|harm|expensive|unaffordable|scrap|withdraw)\w*", re.I)
SUPPORT_CUES = re.compile(r"\b(support|welcom|benefit|necessary|help|improv|boost|fund|praise|defend|create jobs|growth|relief|fair share)\w*", re.I)
OPINION_CUES = re.compile(r"\b(should|must|will|would|argue|said|says|warned|claimed|believe|according to|critics|supporters|we|our)\b", re.I)


def heuristic_extract(text: str, *, chunk_id=None, source_id=None) -> dict:
    counts: Counter = Counter()
    for match in _CAP_PHRASE.finditer(text):
        phrase = match.group(1).strip()
        phrase = re.sub(r"^(the|of|for|and)\s+", "", phrase, flags=re.I)
        phrase = re.sub(r"\s+(the|of|for|and)$", "", phrase, flags=re.I)
        words = phrase.split()
        if not words or len(phrase) < 4 or words[0].lower() in STOPWORDS:
            continue
        if len(words) == 1 and phrase.lower() not in COMMON_PLACES:
            continue
        counts[phrase] += 1
    for match in _ACRONYM.finditer(text):
        acro = match.group(1)
        if acro not in {"THE", "AND", "FOR", "BUT", "NOT", "ALL", "NEW", "US", "OK"}:
            counts[acro] += 1
    entities = []
    for name, n in counts.most_common(14):
        etype = "location" if name.lower() in COMMON_PLACES else "organization"
        for pattern, label in TYPE_HINTS:
            if pattern.search(name):
                etype = label
                break
        else:
            if etype != "location" and len(name.split()) in (2, 3) and not name.isupper():
                etype = "person"
        entities.append({"name": name, "type": etype, "description": "", "stance": None, "mentions": n})

    relations = []
    names = [e["name"] for e in entities]
    for sent in sentences(text):
        present = [n for n in names if n in sent]
        for i in range(len(present)):
            for j in range(i + 1, min(len(present), i + 3)):
                relations.append({"source": present[i], "target": present[j], "type": "RELATED_TO", "description": truncate(sent, 200)})

    arguments, facts = [], []
    for sent in sentences(text):
        if len(sent) < 40 or len(sent) > 400:
            continue
        if re.search(r"\d", sent) and re.search(r"(%|per cent|percent|kes|ksh|ngn|naira|ghs|cedi|rand|billion|million|shillings|\b20\d\d\b)", sent, re.I):
            facts.append({"fact": truncate(sent, 300), "chunk": chunk_id, "source": source_id})
        opp, sup = len(OPPOSE_CUES.findall(sent)), len(SUPPORT_CUES.findall(sent))
        if (opp or sup) and OPINION_CUES.search(sent):
            side = "oppose" if opp > sup else "support" if sup > opp else ("oppose" if sentiment(sent) < -0.2 else "support" if sentiment(sent) > 0.2 else "neutral")
            emo, arousal = lexicon_emotion(sent)
            arguments.append(
                {"claim": truncate(sent, 380), "side": side, "by": "", "emotion": emo, "strength": round(0.4 + 0.3 * arousal, 2), "chunk": chunk_id, "source": source_id}
            )
    return {"entities": entities, "relations": relations[:20], "arguments": arguments[:8], "facts": facts[:8], "rumors": []}


def extract_chunk(llm, use_llm: bool, chunk_text: str, **kwargs) -> tuple[dict, str]:
    """Return (extraction, method)."""
    chunk_id, source_id = kwargs.get("chunk_id"), kwargs.get("source_id")
    if use_llm:
        try:
            return llm_extract(llm, chunk_text, **kwargs), "llm"
        except LLMError:
            pass
    return heuristic_extract(chunk_text, chunk_id=chunk_id, source_id=source_id), "heuristic"
