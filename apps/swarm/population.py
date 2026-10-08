"""
Persona engine: builds a synthetic, heterogeneous population of agents.

* Citizens are sampled from country-specific demographics (regions/counties,
  urban/rural, online age structure, gender) and archetypes (boda boda
  riders, market vendors, Gen Z activists, civil servants, ...).
* Each agent gets Big Five (OCEAN) traits that drive behaviour: openness ->
  willingness to update, agreeableness -> tolerance of disagreement,
  extraversion -> activity, conscientiousness -> stubbornness and scepticism,
  neuroticism -> emotional reactivity.
* Initial stance on the topic comes from how the research says each segment is
  affected (impact matrix), political leaning relative to the proposer, and
  noise; optionally calibrated to the stance observed in real online posts.
* Institutions, media outlets and stakeholders from the knowledge graph become
  institutional agents; optional coordinated "bot" accounts model manipulation.
* A homophilous follow network and WhatsApp-style chat groups connect them.
"""

from __future__ import annotations

import logging
import math
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

import numpy as np
from django.db import transaction

from apps.core.geo import ARCHETYPES, ARCHETYPES_BY_KEY, get_country
from apps.core.geo.archetypes import archetype_label
from apps.core.geo.countries import CountryProfile
from apps.core.llm import LLMError, as_float, as_str, get_llm
from apps.core.text.textutils import camel_tag, slug_handle, truncate

from .models import Agent, ChatGroup

logger = logging.getLogger("baraza.population")

AGE_BANDS = {"18-24": (18, 24), "25-34": (25, 34), "35-44": (35, 44), "45-54": (45, 54), "55-64": (55, 64), "65+": (65, 80)}
INSTITUTION_TRUST_KEY = {
    "government": "government", "legislature": "parliament", "political_party": "government", "union": "civil_society",
    "business": "business", "civil_society": "civil_society", "religious": "religious", "media": "media",
    "international": "international", "organization": "civil_society",
}


@dataclass
class PopulationConfig:
    size: int = 150
    focus_regions: list[str] = field(default_factory=list)
    focus_weight: float = 3.0
    include_institutions: bool = True
    max_institutions: int = 10
    media_agents: int = 3
    influencer_share: float = 0.04
    bot_share: float = 0.0
    bot_stance: float = -0.8
    calibrate: bool = True
    calibration_weight: float = 0.5
    llm_priors: bool = True
    llm_bios: int = 24
    stance_noise: float = 0.22
    seed: int = 42
    archetype_weights: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "PopulationConfig":
        data = dict(data or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------


def sample_age(rng: random.Random, country: CountryProfile, lo: int, hi: int) -> int:
    bands = list(country.online_age_dist.items())
    for _ in range(25):
        band = rng.choices([b for b, _ in bands], weights=[w for _, w in bands], k=1)[0]
        b_lo, b_hi = AGE_BANDS[band]
        a, b = max(lo, b_lo), min(hi, b_hi)
        if a <= b:
            return rng.randint(a, b)
    return rng.randint(lo, hi)


def sample_region(rng: random.Random, country: CountryProfile, urban: bool, focus: list[str], focus_weight: float):
    focus_l = {f.lower() for f in focus}
    weights = []
    for region in country.regions:
        share = region.urban_share if urban else (1 - region.urban_share)
        w = region.population * max(0.02, share)
        if focus_l and (region.name.lower() in focus_l or region.group.lower() in focus_l):
            w *= focus_weight
        weights.append(w)
    return rng.choices(country.regions, weights=weights, k=1)[0]


def platform_prefs(country: CountryProfile, age: int, shift: dict) -> dict[str, float]:
    reach = country.platform_reach
    micro = reach.get("x", 0.2) + 0.6 * reach.get("tiktok", 0.3) + 0.4 * reach.get("instagram", 0.3)
    forum = reach.get("facebook", 0.6) + reach.get("reddit", 0.02) + 0.5 * reach.get("youtube", 0.5)
    chat = reach.get("whatsapp", 0.8) + 0.8 * reach.get("telegram", 0.2)
    if age < 30:
        micro, forum = micro * 1.4, forum * 0.8
    elif age > 45:
        micro, forum, chat = micro * 0.6, forum * 1.3, chat * 1.2
    micro *= shift.get("microblog", 1.0)
    forum *= shift.get("forum", 1.0)
    chat *= shift.get("chat", 1.0)
    total = micro + forum + chat
    return {"microblog": round(micro / total, 3), "forum": round(forum / total, 3), "chat": round(chat / total, 3)}


def describe_big5(b5: dict) -> str:
    parts = []
    if b5["o"] > 0.65:
        parts.append("curious and open to new ideas")
    elif b5["o"] < 0.35:
        parts.append("traditional and wary of change")
    if b5["c"] > 0.65:
        parts.append("careful and detail-oriented")
    elif b5["c"] < 0.35:
        parts.append("spontaneous")
    if b5["e"] > 0.65:
        parts.append("outgoing and enjoys debate")
    elif b5["e"] < 0.35:
        parts.append("reserved; reads more than posts")
    if b5["a"] > 0.65:
        parts.append("warm and conciliatory")
    elif b5["a"] < 0.35:
        parts.append("blunt and combative")
    if b5["n"] > 0.65:
        parts.append("anxious and easily upset")
    elif b5["n"] < 0.35:
        parts.append("calm under pressure")
    return ", ".join(parts) or "even-tempered and pragmatic"


class HandleFactory:
    def __init__(self, rng: random.Random, country: CountryProfile):
        self.rng = rng
        self.country = country
        self.used: set[str] = set()

    def make(self, first: str, last: str = "") -> str:
        first_s, last_s = slug_handle(first, 12), slug_handle(last, 12)
        patterns = [
            f"{first_s}_{last_s}", f"{first_s}{last_s[:1]}", f"{last_s}{first_s[:1]}", f"{first_s}.{last_s}",
            f"the_{first_s}", f"{first_s}{self.rng.choice(self.country.handle_suffixes)}",
        ]
        for _ in range(12):
            handle = self.rng.choice(patterns).replace(".", "_").strip("_")[:15] or "user"
            if self.rng.random() < 0.35:
                handle = f"{handle[:12]}{self.rng.randint(1, 99)}"
            if handle not in self.used:
                self.used.add(handle)
                return handle
        handle = f"{first_s[:10]}{self.rng.randint(100, 9999)}"
        self.used.add(handle)
        return handle

    def reserve(self, handle: str) -> str:
        base = handle[:15] or "account"
        candidate, n = base, 1
        while candidate in self.used:
            n += 1
            candidate = f"{base[:13]}{n}"
        self.used.add(candidate)
        return candidate


# ---------------------------------------------------------------------------
# Stance priors
# ---------------------------------------------------------------------------

PRIORS_SYSTEM = "You estimate, realistically and without stereotyping, how different segments of the public are likely to initially feel about a policy. Reply with JSON only."
PRIORS_USER = """COUNTRY: {country}
POLICY / ACTION: {title}
SUMMARY: {summary}
PROPOSED BY: {proposer}
WHO IS AFFECTED (from research):
{impacts}

SEGMENTS:
{segments}

For EACH segment estimate its average initial "stance" toward the policy from -1 (strongly against) to 1 (strongly for), and "salience" from 0 (does not care) to 1 (cares intensely), with a short reason.
Return JSON: {{"segments": [{{"key": "segment_key", "stance": -0.4, "salience": 0.7, "reason": ""}}]}}"""


def impact_priors(impact_matrix: list[dict], base_direction: float) -> dict[str, tuple[float, float]]:
    """Archetype -> (stance prior, salience) from the dossier's impact matrix (no LLM)."""
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for row in impact_matrix or []:
        for rank, key in enumerate(row.get("archetypes") or []):
            w = row.get("intensity", 0.5) * (1.0 / (1 + rank))
            sums[key][0] += w * row.get("direction", 0.0)
            sums[key][1] += w
            sums[key][2] += row.get("intensity", 0.5)
    priors = {}
    for arch in ARCHETYPES:
        income_factor = {"low": 1.2, "lower_middle": 1.0, "middle": 0.85, "upper": 0.7}[arch.income]
        if arch.key in sums and sums[arch.key][1] > 0:
            s = sums[arch.key]
            priors[arch.key] = (clip(s[0] / s[1]), min(1.0, s[2] / max(1, len([1 for r in impact_matrix if arch.key in (r.get("archetypes") or [])]))))
        else:
            priors[arch.key] = (clip(base_direction * 0.6 * (income_factor if base_direction < 0 else 1.0)), 0.35)
    return priors


def llm_priors(llm, *, country: CountryProfile, brief: dict, impact_matrix: list[dict], present: list[str]) -> dict[str, tuple[float, float, str]]:
    segments = "\n".join(
        f"- {key}: {archetype_label(ARCHETYPES_BY_KEY[key], country)} ({ARCHETYPES_BY_KEY[key].income.replace('_', '-')} income; main concerns: {', '.join(ARCHETYPES_BY_KEY[key].concerns[:3])})"
        for key in present
    )
    impacts = "\n".join(f"- {r.get('group')}: {r.get('impact')} (direction {r.get('direction', 0):+.1f})" for r in impact_matrix[:12]) or "- (not available)"
    data = llm.json(
        system=PRIORS_SYSTEM,
        user=PRIORS_USER.format(
            country=country.name,
            title=brief.get("title", ""),
            summary=truncate(brief.get("summary", ""), 600),
            proposer=f"{brief.get('proposer', 'government')} {brief.get('proposer_name', '')}".strip(),
            impacts=impacts,
            segments=segments,
        ),
        purpose="stance_priors",
        tier="smart",
        max_tokens=90 * len(present) + 150,
        temperature=0.2,
    )
    out = {}
    for row in data.get("segments") or []:
        if not isinstance(row, dict):
            continue
        key = as_str(row.get("key")).strip()
        if key in ARCHETYPES_BY_KEY:
            out[key] = (as_float(row.get("stance"), 0.0, -1.0, 1.0), as_float(row.get("salience"), 0.5, 0.0, 1.0), as_str(row.get("reason"), max_len=200))
    return out


def leaning_effect(leaning: str, proposer: str) -> float:
    if proposer in ("government", "legislature"):
        return {"pro_government": 0.28, "opposition": -0.28}.get(leaning, 0.0)
    if proposer == "opposition":
        return {"pro_government": -0.25, "opposition": 0.25}.get(leaning, 0.0)
    return 0.0


def calibrate(stances: list[float], observed: list[float], weight: float, rng: random.Random) -> list[float]:
    """Quantile-map modelled stances toward the observed online distribution."""
    if not observed or weight <= 0:
        return stances
    order = sorted(range(len(stances)), key=lambda i: stances[i])
    obs = sorted(observed)
    n, m = len(stances), len(obs)
    out = list(stances)
    for rank, i in enumerate(order):
        q = (rank + 0.5) / n
        pos = q * (m - 1)
        lo, hi = int(math.floor(pos)), int(math.ceil(pos))
        target = obs[lo] + (obs[hi] - obs[lo]) * (pos - lo)
        target = clip(target + rng.gauss(0, 0.05))
        out[i] = clip((1 - weight) * stances[i] + weight * target)
    return out


# ---------------------------------------------------------------------------
# Bios
# ---------------------------------------------------------------------------

BIO_SYSTEM = "You write short, realistic social media profile bios and persona notes for synthetic people in a public-opinion simulation. Never use real people's names. Reply with JSON only."
BIO_USER = """COUNTRY: {country}. TOPIC being discussed: {title}.
For each person below write:
- "bio": a social media bio under 150 characters, in their own voice.
- "persona": 2 sentences describing their life situation, what they care about regarding the topic and how they talk online.

PEOPLE:
{people}

Return JSON: {{"people": [{{"id": 1, "bio": "", "persona": ""}}]}}"""


def template_bio(a: dict, country: CountryProfile) -> str:
    bits = {
        "student": "Student. Dreamer. Broke but hopeful.",
        "genz_activist": "Asking hard questions. Accountability now.",
        "unemployed_youth": "Graduate. Hustling every day. Open to opportunities.",
        "boda_rider": "Boda boda rider. Fast, reliable, always on the road.",
        "market_vendor": "Fresh produce every day. Customers are family.",
        "smallholder_farmer": "Farmer. Feeding the nation one harvest at a time.",
        "sme_owner": "Small business owner. Building something that lasts.",
        "matatu_operator": "Public transport. Moving people since day one.",
        "teacher": "Teacher. Shaping the next generation.",
        "health_worker": "Health worker. Caring for patients, fighting for better care.",
        "civil_servant": "Public servant. Views are my own.",
        "corporate_professional": "Corporate by day. Opinions by night.",
        "tech_worker": "Building software. Data over drama.",
        "content_creator": "Creator. Collabs in DMs.",
        "gig_worker": "Ride-hailing driver. Five stars, zero savings.",
        "landlord_property": "Property and investments. Hard work pays.",
        "pensioner": "Retired. Grandparent. I have seen it all.",
        "religious_leader": "Servant of God. Peace and unity.",
        "journalist": "Journalist. Facts first.",
        "policy_analyst": "Economist. Numbers tell the story.",
        "diaspora": f"{country.demonym} abroad. Home is always home.",
        "casual_labourer": "Hard worker. Daily bread is not guaranteed.",
        "parent_homemaker": "Parent. Family first, always.",
        "ngo_worker": "Rights, inclusion, accountability.",
        "lawyer": "Advocate. The constitution is not optional.",
    }
    return f"{bits.get(a['archetype'], 'Citizen.')} {a['region']}."


def template_persona(a: dict, country: CountryProfile) -> str:
    age = f"{a['age']}-year-old " if a.get("age") else ""
    area = "urban" if a.get("urban") else "rural"
    return (
        f"{a['name']} is a {age}{a['occupation'].lower()} living in {a['region']} ({area} {country.name}). "
        f"Main concerns: {', '.join(a['concerns'][:3])}. Personality: {describe_big5(a['big5'])}. "
        f"Politically {country.leaning_labels.get(a['leaning'], 'non-aligned').lower()}. Writes in a {a['voice']} style."
    )


def llm_bios(llm, agents: list[dict], *, country: CountryProfile, title: str) -> int:
    done = 0
    for start in range(0, len(agents), 5):
        batch = agents[start : start + 5]
        people = "\n".join(
            f"{i + 1}. {a['name']}, {a.get('age', '?')}, {a['gender']}, {a['occupation']} in {a['region']}; concerns: {', '.join(a['concerns'][:3])}; personality: {describe_big5(a['big5'])}; initial view on topic: {'supportive' if a['initial_stance'] > 0.2 else 'opposed' if a['initial_stance'] < -0.2 else 'undecided'}."
            for i, a in enumerate(batch)
        )
        try:
            data = llm.json(
                system=BIO_SYSTEM,
                user=BIO_USER.format(country=country.name, title=title, people=people),
                purpose="bios",
                tier="fast",
                max_tokens=140 * len(batch) + 80,
                temperature=0.7,
                seed=start,
            )
        except LLMError:
            continue
        for row in data.get("people") or []:
            if not isinstance(row, dict):
                continue
            idx = int(as_float(row.get("id"), 0)) - 1
            if 0 <= idx < len(batch):
                bio, persona = as_str(row.get("bio"), max_len=200), as_str(row.get("persona"), max_len=600)
                if bio:
                    batch[idx]["bio"] = bio
                if persona:
                    batch[idx]["persona"] = f"{persona} {batch[idx]['persona_suffix']}"
                    done += 1
    return done


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


def build_follow_network(agents: list[dict], rng: np.random.Generator) -> None:
    """Homophilous preferential attachment: who follows whom at the start."""
    n = len(agents)
    if n < 2:
        return
    stance = np.array([a["initial_stance"] for a in agents])
    popularity = np.array([max(1.0, a["followers_seed"]) for a in agents]) ** 0.8
    region = np.array([hash(a.get("region_group") or a.get("region") or "") for a in agents])
    arch = np.array([hash(a["archetype"]) for a in agents])
    kind = [a["kind"] for a in agents]
    for i, agent in enumerate(agents):
        if kind[i] in ("institution", "media"):
            k = int(rng.integers(3, 10))
        elif kind[i] == "bot":
            k = int(rng.integers(10, 25))
        else:
            k = int(min(n - 1, 4 + rng.poisson(6 + 12 * agent["big5"]["e"])))
        weights = popularity.copy()
        weights *= np.where(region == region[i], 2.0, 1.0)
        weights *= np.where(arch == arch[i], 1.6, 1.0)
        weights *= np.exp(-1.4 * np.abs(stance - stance[i]))
        if kind[i] == "bot":
            weights *= np.array([3.0 if kk == "bot" else 1.0 for kk in kind])
        weights[i] = 0.0
        total = weights.sum()
        if total <= 0:
            continue
        k = min(k, int((weights > 0).sum()))
        chosen = rng.choice(n, size=k, replace=False, p=weights / total)
        agent["following"] = sorted(int(c) for c in chosen)
    followers = Counter(j for a in agents for j in a["following"])
    for i, agent in enumerate(agents):
        agent["followers_count"] = followers.get(i, 0)


GROUP_KINDS = [
    ("family", "Family", 0.9),
    ("neighbourhood", "Neighbours", 0.7),
    ("chama", "Savings group", 0.45),
    ("faith", "Fellowship", 0.5),
    ("work", "Colleagues", 0.6),
]


def build_chat_groups(agents: list[dict], rng: random.Random, country: CountryProfile) -> list[dict]:
    """WhatsApp-style groups: family, neighbours, savings (chama), faith and work groups."""
    people = [a for a in agents if a["kind"] in ("citizen", "influencer")]
    if len(people) < 6:
        return []
    groups: list[dict] = []

    def add_group(kind: str, label: str, members: list[dict]):
        if len(members) < 3:
            return
        idx = len(groups)
        groups.append({"idx": idx, "kind": kind, "name": label, "members": sorted({m["idx"] for m in members})})
        for m in members:
            m["groups"].append(idx)

    by_region_group = defaultdict(list)
    by_region = defaultdict(list)
    by_arch = defaultdict(list)
    for a in people:
        by_region_group[a.get("region_group") or a["region"]].append(a)
        by_region[a["region"]].append(a)
        by_arch[a["archetype"]].append(a)
    chama_label = "Chama" if country.code in ("KE", "TZ", "UG") else "Savings group"
    # Family groups: mixed ages within a macro-region.
    for region, members in by_region_group.items():
        pool = members[:]
        rng.shuffle(pool)
        for i in range(0, len(pool), rng.randint(7, 12)):
            chunk = pool[i : i + rng.randint(6, 12)]
            surname = (chunk[0]["name"].split()[-1] if chunk else "Family")
            add_group("family", f"{surname} family", chunk)
    # Neighbourhood groups.
    for region, members in by_region.items():
        if len(members) >= 4:
            sample = rng.sample(members, min(len(members), rng.randint(6, 20)))
            add_group("neighbourhood", f"{region} residents", sample)
    # Savings groups for low/lower-middle income.
    savers = [a for a in people if a["income"] in ("low", "lower_middle")]
    rng.shuffle(savers)
    for i in range(0, len(savers), 10):
        chunk = savers[i : i + rng.randint(6, 12)]
        if chunk:
            add_group("chama", f"{chunk[0]['region']} {chama_label}", chunk)
    # Faith groups (religion itself is not modelled; just a community group).
    for region, members in by_region_group.items():
        if len(members) >= 6 and rng.random() < 0.8:
            add_group("faith", f"{region} fellowship", rng.sample(members, min(len(members), rng.randint(8, 18))))
    # Work groups by archetype.
    for key, members in by_arch.items():
        if len(members) >= 4:
            label = archetype_label(ARCHETYPES_BY_KEY[key], country) if key in ARCHETYPES_BY_KEY else key
            add_group("work", f"{label}s network", rng.sample(members, min(len(members), rng.randint(6, 18))))
    # Bots infiltrate a few large groups.
    bots = [a for a in agents if a["kind"] == "bot"]
    big = sorted(groups, key=lambda g: -len(g["members"]))[: max(1, len(groups) // 4)]
    for bot in bots:
        for g in rng.sample(big, min(len(big), 2)):
            g["members"] = sorted(set(g["members"]) | {bot["idx"]})
            bot["groups"].append(g["idx"])
    return groups


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_population(project, config: PopulationConfig, ctx=None) -> dict:
    from apps.projects.brief import policy_direction
    from apps.research.pipeline import observed_stance_summary

    rng = random.Random(config.seed)
    np_rng = np.random.default_rng(config.seed)
    country = get_country(project.country)
    brief = project.brief or {}
    try:
        dossier = project.dossier
    except Exception:
        dossier = None
    impact_matrix = (dossier.impact_matrix if dossier else None) or brief.get("affected_groups") or []
    if impact_matrix and "archetypes" not in impact_matrix[0]:
        from apps.knowledge.dossier import annotate_impact_matrix

        impact_matrix = annotate_impact_matrix([dict(r) for r in impact_matrix])
    proposer = brief.get("proposer", "government")
    base_direction = policy_direction(f"{brief.get('title', '')} {brief.get('summary', '')} {project.prompt}")
    llm = get_llm(project)
    use_llm = llm.available("smart")

    def progress(pct, stage, message=None):
        if ctx:
            ctx.progress(pct, stage, message)
            ctx.check_cancel()

    # 1. Archetype mix.
    weights = {a.key: a.weight * country.archetype_weights.get(a.key, 1.0) * config.archetype_weights.get(a.key, 1.0) for a in ARCHETYPES}
    n_bots = int(round(config.size * config.bot_share))
    n_citizens = max(5, config.size - n_bots)
    keys = list(weights)
    chosen = rng.choices(keys, weights=[weights[k] for k in keys], k=n_citizens)
    present = sorted(set(chosen), key=lambda k: -weights[k])

    # 2. Stance priors per archetype.
    progress(5, "Estimating how each segment feels")
    priors = {k: (v[0], v[1], "") for k, v in impact_priors(impact_matrix, base_direction).items()}
    prior_source = "impact matrix"
    if use_llm and config.llm_priors:
        try:
            estimated = llm_priors(llm, country=country, brief=brief, impact_matrix=impact_matrix, present=present)
            for key, value in estimated.items():
                old = priors.get(key, (0.0, 0.5, ""))
                priors[key] = (round(0.65 * value[0] + 0.35 * old[0], 3), round(0.65 * value[1] + 0.35 * old[1], 3), value[2])
            if estimated:
                prior_source = "language model + impact matrix"
        except LLMError as exc:
            if ctx:
                ctx.warn(f"Segment stance estimation failed ({exc}); using the impact matrix.")

    # 3. Citizens.
    progress(15, "Sampling citizens")
    handles = HandleFactory(rng, country)
    agents: list[dict] = []
    for i, key in enumerate(chosen):
        arch = ARCHETYPES_BY_KEY[key]
        female = rng.random() < arch.female_share
        age = sample_age(rng, country, *arch.age)
        urban = rng.random() < arch.urban_prob
        region = sample_region(rng, country, urban, config.focus_regions or project.focus_regions or [], config.focus_weight)
        first = rng.choice(country.first_names_female if female else country.first_names_male)
        last = rng.choice(country.surnames)
        lean_w = {k: v * arch.leaning_shift.get(k, 1.0) for k, v in country.leaning_dist.items()}
        leaning = rng.choices(list(lean_w), weights=list(lean_w.values()), k=1)[0]
        b5 = {t: round(clip(rng.gauss(0.5 + arch.big5_shift.get(t, 0.0), 0.15), 0.02, 0.98), 3) for t in "ocean"}
        age_factor = 1.1 if age < 30 else 0.9 if age > 50 else 1.0
        eps = 0.2 + 0.4 * b5["o"]
        prior, salience, _reason = priors.get(key, (0.0, 0.4, ""))
        stance = clip(prior + leaning_effect(leaning, proposer) + rng.gauss(0, config.stance_noise))
        agents.append(
            {
                "idx": i,
                "kind": "citizen",
                "archetype": key,
                "archetype_label": archetype_label(arch, country),
                "name": f"{first} {last}",
                "handle": handles.make(first, last),
                "gender": "female" if female else "male",
                "age": age,
                "urban": urban,
                "region": region.name,
                "region_group": region.group,
                "occupation": arch.occupation if key not in country.archetype_labels else country.archetype_labels[key],
                "education": arch.education,
                "income": arch.income,
                "leaning": leaning,
                "big5": b5,
                "platforms": platform_prefs(country, age, arch.platform_shift),
                "initial_stance": round(stance, 4),
                "salience": round(clip(salience + rng.gauss(0, 0.1), 0.05, 1.0), 3),
                "credibility": round(clip(arch.credibility + rng.gauss(0, 0.05), 0.05, 0.95), 3),
                "activity": round(clip(0.12 * arch.activity * (0.5 + b5["e"]) * (1.0 if age < 35 else 0.8), 0.01, 0.9), 4),
                "susceptibility": round(clip((0.04 + 0.16 * (0.5 * b5["o"] + 0.3 * b5["a"] + 0.2 * (1 - b5["c"]))) * age_factor, 0.01, 0.4), 4),
                "accept_latitude": round(eps, 3),
                "reject_latitude": round(min(1.9, eps + 0.3 + 0.5 * b5["a"]), 3),
                "stubbornness": round(clip(0.02 + 0.1 * b5["c"] * (1.2 if age > 45 else 1.0) + (0.04 if leaning != "non_aligned" else 0.0), 0.0, 0.5), 4),
                "followers_seed": int(max(5, np_rng.lognormal(4.0 + 0.8 * b5["e"], 0.9))),
                "following": [],
                "groups": [],
                "concerns": list(arch.concerns),
                "voice": arch.voice,
                "bot_target": None,
                "entity_id": None,
            }
        )

    # 4. Influencers.
    n_infl = max(1, int(round(n_citizens * config.influencer_share))) if config.influencer_share > 0 else 0
    candidates = sorted(
        [a for a in agents if a["archetype"] in ("content_creator", "journalist", "genz_activist", "policy_analyst", "tech_worker", "religious_leader", "lawyer", "corporate_professional")],
        key=lambda a: -a["big5"]["e"],
    )
    for a in candidates[:n_infl]:
        a["kind"] = "influencer"
        a["followers_seed"] = int(a["followers_seed"] * rng.uniform(15, 60))
        a["credibility"] = round(min(0.9, a["credibility"] + 0.15), 3)
        a["activity"] = round(min(0.9, a["activity"] * 1.8), 4)

    # 5. Calibration to observed online stance.
    observed = observed_stance_summary(project)
    calibration = {"applied": False, "observed_n": observed.get("n_stance", 0)}
    if config.calibrate and observed.get("n_stance", 0) >= 15:
        before = sum(a["initial_stance"] for a in agents) / len(agents)
        new = calibrate([a["initial_stance"] for a in agents], observed["values"], config.calibration_weight, rng)
        for a, s in zip(agents, new):
            a["initial_stance"] = round(s, 4)
        after = sum(a["initial_stance"] for a in agents) / len(agents)
        calibration.update({"applied": True, "mean_before": round(before, 3), "mean_after": round(after, 3), "observed_mean": observed.get("mean")})

    # 6. Institutions and media.
    progress(35, "Adding institutions and media")
    next_idx = len(agents)
    if config.include_institutions and dossier and dossier.stakeholders:
        from apps.knowledge.graph import entity_key
        from apps.knowledge.models import Entity

        entities = {e.key: e for e in Entity.objects.filter(project=project, is_stakeholder=True)}
        stakeholders = sorted(dossier.stakeholders, key=lambda s: -s.get("influence", 0.5))
        for s in stakeholders:
            if sum(1 for a in agents if a["kind"] in ("institution", "media")) >= config.max_institutions:
                break
            stype = s.get("type", "organization")
            if stype == "person":
                continue  # Real individuals are represented through their institutions (ethics).
            trust = country.trust.get(INSTITUTION_TRUST_KEY.get(stype, "civil_society"), 0.5)
            ent = entities.get(entity_key(s["name"]))
            agents.append(
                {
                    "idx": next_idx,
                    "kind": "media" if stype == "media" else "institution",
                    "archetype": stype,
                    "archetype_label": stype.replace("_", " ").title(),
                    "name": s["name"],
                    "handle": handles.reserve(camel_tag(s["name"], 3)[:15]),
                    "gender": "",
                    "age": None,
                    "urban": True,
                    "region": country.capital,
                    "region_group": "",
                    "occupation": stype.replace("_", " ").title(),
                    "education": "",
                    "income": "",
                    "leaning": "",
                    "big5": {"o": 0.4, "c": 0.8, "e": 0.6, "a": 0.5, "n": 0.3},
                    "platforms": {"microblog": 0.55, "forum": 0.45, "chat": 0.0},
                    "initial_stance": round(clip(s.get("position", 0.0)), 3),
                    "salience": round(0.5 + 0.5 * s.get("influence", 0.5), 3),
                    "credibility": round(clip(trust + 0.1, 0.1, 0.95), 3),
                    "activity": round(0.25 + 0.35 * s.get("influence", 0.5), 3),
                    "susceptibility": 0.01,
                    "accept_latitude": 0.3,
                    "reject_latitude": 1.2,
                    "stubbornness": 0.35,
                    "followers_seed": int(5000 + 100000 * s.get("influence", 0.5) * rng.uniform(0.5, 1.5)),
                    "following": [],
                    "groups": [],
                    "concerns": [s.get("summary", "")] if s.get("summary") else [],
                    "voice": "official, measured" if stype in ("government", "legislature") else "assertive, organisational",
                    "bot_target": None,
                    "entity_id": ent.pk if ent else None,
                    "bio": truncate(s.get("summary") or f"Official account of {s['name']}.", 200),
                }
            )
            next_idx += 1
    existing_media = sum(1 for a in agents if a["kind"] == "media")
    for name in country.media_outlets[: max(0, config.media_agents - existing_media)]:
        agents.append(
            {
                "idx": next_idx,
                "kind": "media",
                "archetype": "media",
                "archetype_label": "News outlet",
                "name": name,
                "handle": handles.reserve(name[:15]),
                "gender": "",
                "age": None,
                "urban": True,
                "region": country.capital,
                "region_group": "",
                "occupation": "News outlet",
                "education": "",
                "income": "",
                "leaning": "",
                "big5": {"o": 0.6, "c": 0.8, "e": 0.7, "a": 0.5, "n": 0.3},
                "platforms": {"microblog": 0.6, "forum": 0.4, "chat": 0.0},
                "initial_stance": round(rng.uniform(-0.1, 0.1), 3),
                "salience": 0.8,
                "credibility": round(country.trust.get("media", 0.5) + 0.1, 3),
                "activity": 0.6,
                "susceptibility": 0.01,
                "accept_latitude": 0.3,
                "reject_latitude": 1.5,
                "stubbornness": 0.4,
                "followers_seed": int(rng.uniform(50000, 400000)),
                "following": [],
                "groups": [],
                "concerns": ["breaking news", "accuracy"],
                "voice": "neutral newsroom style",
                "bot_target": None,
                "entity_id": None,
                "bio": f"Simulated news outlet covering {country.name}.",
            }
        )
        next_idx += 1

    # 7. Coordinated accounts.
    for b in range(n_bots):
        first = rng.choice(country.first_names_male + country.first_names_female)
        agents.append(
            {
                "idx": next_idx,
                "kind": "bot",
                "archetype": "coordinated",
                "archetype_label": "Coordinated account",
                "name": f"{first} {rng.choice(country.surnames)}",
                "handle": handles.make(first, str(rng.randint(10, 9999))),
                "gender": "",
                "age": None,
                "urban": True,
                "region": rng.choice(country.regions).name,
                "region_group": "",
                "occupation": "Unknown",
                "education": "",
                "income": "",
                "leaning": "",
                "big5": {"o": 0.2, "c": 0.5, "e": 0.95, "a": 0.2, "n": 0.6},
                "platforms": {"microblog": 0.6, "forum": 0.2, "chat": 0.2},
                "initial_stance": round(clip(config.bot_stance), 3),
                "salience": 1.0,
                "credibility": 0.12,
                "activity": 0.85,
                "susceptibility": 0.0,
                "accept_latitude": 0.1,
                "reject_latitude": 0.2,
                "stubbornness": 1.0,
                "followers_seed": int(rng.uniform(20, 400)),
                "following": [],
                "groups": [],
                "concerns": ["pushing a narrative"],
                "voice": "repetitive slogans, many hashtags",
                "bot_target": round(clip(config.bot_stance), 3),
                "entity_id": None,
            }
        )
        next_idx += 1

    # 8. Network and groups.
    progress(45, "Wiring the social network")
    build_follow_network(agents, np_rng)
    groups = build_chat_groups(agents, rng, country)

    # 9. Bios and personas.
    progress(55, "Writing personas")
    for a in agents:
        if a["kind"] in ("citizen", "influencer", "bot"):
            a["persona_suffix"] = f"Main concerns: {', '.join(a['concerns'][:3])}."
            a.setdefault("bio", template_bio(a, country) if a["kind"] != "bot" else "Patriot. Truth teller. Follow back.")
            a["persona"] = template_persona(a, country) if a["kind"] != "bot" else "A coordinated account that pushes one narrative relentlessly with slogans and hashtags."
        else:
            a["persona_suffix"] = ""
            a["persona"] = f"The official account of {a['name']}. Communicates its position clearly and defends it. {a.get('bio', '')}"
    bios_written = 0
    if use_llm and config.llm_bios > 0:
        targets = [a for a in agents if a["kind"] == "influencer"]
        others = [a for a in agents if a["kind"] == "citizen"]
        rng.shuffle(others)
        targets = (targets + others)[: config.llm_bios]
        bios_written = llm_bios(llm, targets, country=country, title=brief.get("title", project.title))

    # 10. Persist.
    progress(85, "Saving the population")
    with transaction.atomic():
        project.runs.all().delete()
        Agent.objects.filter(project=project).delete()
        ChatGroup.objects.filter(project=project).delete()
        Agent.objects.bulk_create(
            [
                Agent(
                    project=project,
                    idx=a["idx"],
                    handle=a["handle"],
                    name=a["name"][:120],
                    kind=a["kind"],
                    archetype=a["archetype"],
                    archetype_label=a["archetype_label"][:80],
                    age=a["age"],
                    gender=a["gender"],
                    region=a["region"][:80],
                    region_group=a["region_group"][:60],
                    urban=a["urban"],
                    occupation=a["occupation"][:120],
                    education=a["education"],
                    income=a["income"],
                    leaning=a["leaning"],
                    platforms=a["platforms"],
                    big5=a["big5"],
                    initial_stance=a["initial_stance"],
                    salience=a["salience"],
                    credibility=a["credibility"],
                    activity=a["activity"],
                    susceptibility=a["susceptibility"],
                    accept_latitude=a["accept_latitude"],
                    reject_latitude=a["reject_latitude"],
                    stubbornness=a["stubbornness"],
                    followers_seed=a["followers_seed"],
                    following=a["following"],
                    groups=a["groups"],
                    concerns=a["concerns"],
                    bio=a.get("bio", "")[:500],
                    persona=a["persona"],
                    voice=a["voice"][:200],
                    entity_id=a.get("entity_id"),
                    bot_target=a["bot_target"],
                )
                for a in agents
            ],
            batch_size=300,
        )
        ChatGroup.objects.bulk_create(
            [ChatGroup(project=project, idx=g["idx"], name=g["name"][:120], kind=g["kind"], members=g["members"]) for g in groups]
        )
    project.save(update_fields=["updated_at"])
    kinds = Counter(a["kind"] for a in agents)
    return {
        "agents": len(agents),
        "kinds": dict(kinds),
        "archetypes": len(present),
        "chat_groups": len(groups),
        "prior_source": prior_source,
        "calibration": calibration,
        "llm_bios": bios_written,
        "mean_initial_stance": round(sum(a["initial_stance"] for a in agents) / len(agents), 3),
        "priors": {k: {"stance": v[0], "salience": v[1], "reason": v[2]} for k, v in priors.items() if k in present},
        "config": asdict(config),
    }


def run_population(ctx, project_id, config: dict | None = None):
    from apps.projects.models import Project

    project = Project.objects.get(pk=project_id)
    cfg = PopulationConfig.from_dict(config)
    result = generate_population(project, cfg, ctx)
    ctx.log(
        f"Created {result['agents']} agents ({', '.join(f'{v} {k}' for k, v in result['kinds'].items())}) "
        f"in {result['chat_groups']} chat groups. Mean initial stance {result['mean_initial_stance']:+.2f}."
    )
    if result["calibration"].get("applied"):
        c = result["calibration"]
        ctx.log(f"Calibrated to {c['observed_n']} observed public posts: mean {c['mean_before']:+.2f} -> {c['mean_after']:+.2f}.")
    from apps.core.models import Job

    Job.objects.filter(pk=ctx.job_id).update(result=result)
    return result
