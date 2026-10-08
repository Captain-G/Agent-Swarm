"""
Talk to the simulation:

* Interview any agent: they answer in character, remembering what they posted,
  how their view changed and what moved them.
* Ask the Observer: questions about the whole simulation, answered from the
  analytics, the knowledge graph (GraphRAG) and the simulated posts.
* Virtual polls / focus groups: ask a stratified sample of agents a question.
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict

from django.utils import timezone

from apps.core.geo import get_country
from apps.core.llm import LLMError, as_float, as_str, get_llm
from apps.core.text.retrieval import BM25
from apps.core.text.textutils import truncate
from apps.swarm.engine.population_text import describe_traits
from apps.swarm.models import Agent, AgentState, Post, SimulationRun, stance_label

from .models import Interview, Poll


class _Traits:
    def __init__(self, big5: dict):
        for t in "ocean":
            setattr(self, t, (big5 or {}).get(t, 0.5))


def agent_system_prompt(run: SimulationRun, agent: Agent, state: AgentState | None) -> str:
    country = get_country(run.project.country)
    topic = run.project.topic
    final = state.stance if state else agent.initial_stance
    posts = list(Post.objects.filter(run=run, author=agent).order_by("-seq")[:4])
    memory = (state.memory if state else [])[-5:]
    lines = [
        f"You are {agent.name} (@{agent.handle}), {agent.archetype_label.lower()} from {agent.region}, {country.name}." if agent.kind in ("citizen", "influencer") else f"You speak for {agent.name}, {agent.archetype_label.lower()} in {country.name}.",
        agent.persona,
        f"Personality: {describe_traits(_Traits(agent.big5))}." if agent.kind in ("citizen", "influencer") else "",
        f"Over the last few days people debated \"{topic}\" online. Your view started as '{stance_label(agent.initial_stance)}' and is now '{stance_label(final)}'.",
        f"You currently feel {state.emotion if state else 'neutral'}.",
    ]
    if posts:
        lines.append("Things you posted: " + " | ".join(f'"{truncate(p.text, 140)}"' for p in posts))
    if memory:
        lines.append("What happened to you: " + " | ".join(truncate(m, 140) for m in memory))
    lines.append(
        "A researcher is interviewing you. Answer in character, in the first person, in 2-5 sentences, honestly and specifically "
        "(your life, your worries, what would change your mind). Never say you are an AI or simulated."
    )
    return "\n".join(line for line in lines if line)


def fallback_agent_answer(agent: Agent, state: AgentState | None, topic: str) -> str:
    final = state.stance if state else agent.initial_stance
    concern = (agent.concerns or ["my family"])[0]
    if final < -0.2:
        return f"Honestly, I'm against {topic}. As a {agent.archetype_label.lower()}, my biggest worry is {concern}. Show me how it won't make life harder and I might reconsider."
    if final > 0.2:
        return f"I support {topic}, mostly because of {concern}. But the government must explain it better and make sure the money is used well."
    return f"I'm still undecided about {topic}. I care about {concern}, and I need clear facts before I take a side."


def interview_agent(run: SimulationRun, agent: Agent, question: str) -> Interview:
    interview, _ = Interview.objects.get_or_create(run=run, agent=agent)
    state = AgentState.objects.filter(run=run, agent=agent).first()
    llm = get_llm(run.project)
    messages = [{"role": "system", "content": agent_system_prompt(run, agent, state)}]
    for m in interview.messages[-8:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})
    try:
        if not llm.available("fast"):
            raise LLMError("unavailable")
        answer = llm.chat(messages, tier="fast", purpose="interview", temperature=0.8, max_tokens=300, use_cache=False).text.strip()
    except LLMError:
        answer = fallback_agent_answer(agent, state, run.project.topic)
    now = timezone.now().isoformat()
    interview.messages = interview.messages + [{"role": "user", "content": question, "time": now}, {"role": "assistant", "content": answer or "...", "time": now}]
    interview.save()
    return interview


def observer_facts(run: SimulationRun) -> dict:
    summary = run.summary or {}
    last = run.snapshots.order_by("-round").first()
    metrics = last.metrics if last else {}
    facts = {
        "start_mean": summary.get("start_mean"),
        "end_mean": summary.get("end_mean"),
        "end_support": summary.get("end_support"),
        "end_oppose": summary.get("end_oppose"),
        "trend": summary.get("trend"),
        "segments_final": {k: v["mean"] for k, v in (metrics.get("segments", {}).get("archetype", {}) or {}).items()},
        "regions_final": {k: v["mean"] for k, v in (metrics.get("segments", {}).get("region", {}) or {}).items()},
        "emotions": metrics.get("emotions"),
        "hashtags": metrics.get("hashtags"),
    }
    return facts


def describe_facts(facts: dict) -> str:
    """Plain-language headline answer from the Observer's facts (used without a language model)."""
    parts = [
        f"Mean stance moved from {facts.get('start_mean') or 0:+.2f} to {facts.get('end_mean') or 0:+.2f} ({facts.get('trend') or 'n/a'}); "
        f"final support {100 * (facts.get('end_support') or 0):.0f}%, opposition {100 * (facts.get('end_oppose') or 0):.0f}%."
    ]
    segments = sorted((facts.get("segments_final") or {}).items(), key=lambda kv: kv[1])
    if segments:
        parts.append("Most opposed: " + ", ".join(f"{k} ({v:+.2f})" for k, v in segments[:3]) + ".")
        parts.append("Most supportive: " + ", ".join(f"{k} ({v:+.2f})" for k, v in segments[::-1][:3]) + ".")
    emotions = facts.get("emotions") or {}
    if emotions:
        top = max(emotions, key=emotions.get)
        parts.append(f"The most common emotion at the end was {top}.")
    return " ".join(parts)


def observer_context(run: SimulationRun, question: str) -> str:
    from apps.knowledge.retrieval import KnowledgeBase

    facts = observer_facts(run)
    posts = list(Post.objects.filter(run=run).exclude(text="").select_related("author").order_by("-likes")[:400])
    picked = []
    if posts:
        bm = BM25([p.text for p in posts])
        picked = [posts[i] for i, _ in bm.top(question, 6)] or posts[:4]
    post_lines = "\n".join(f"- @{p.author.handle} ({p.author.archetype_label}, {stance_label(p.stance)}): \"{truncate(p.text, 200)}\" [{p.likes} likes]" for p in picked)
    kb = KnowledgeBase(run.project, use_embeddings=True)
    evidence = kb.answer_context(question, k=4, max_chars=2000)
    return f"SIMULATION FACTS (JSON): {json.dumps(facts, default=str)[:3000]}\n\nRELEVANT SIMULATED POSTS:\n{post_lines or '- none'}\n\nREAL-WORLD EVIDENCE FROM RESEARCH:\n{evidence or '- none'}"


def ask_observer(run: SimulationRun, question: str) -> Interview:
    interview, _ = Interview.objects.get_or_create(run=run, agent=None)
    llm = get_llm(run.project)
    country = get_country(run.project.country)
    system = (
        f"You are the Observer, an impartial analyst of a simulated online debate in {country.name} about \"{run.project.topic}\". "
        "Answer the user's question using ONLY the context provided; cite posts or evidence when useful; say clearly when the data cannot answer. "
        "Remember this is a simulation, not a poll. Be concise (under 200 words)."
    )
    context = observer_context(run, question)
    messages = [{"role": "system", "content": system}]
    for m in interview.messages[-6:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": f"{context}\n\nQUESTION: {question}"})
    try:
        if not llm.available("smart"):
            raise LLMError("unavailable")
        answer = llm.chat(messages, tier="smart", purpose="observer_chat", temperature=0.3, max_tokens=500, use_cache=False).text.strip()
    except LLMError:
        answer = "No language model is connected, so here are the headline numbers. " + describe_facts(observer_facts(run))
    now = timezone.now().isoformat()
    interview.messages = interview.messages + [{"role": "user", "content": question, "time": now}, {"role": "assistant", "content": answer, "time": now}]
    interview.save()
    return interview


# ---------------------------------------------------------------------------
# Virtual polls
# ---------------------------------------------------------------------------

POLL_SYSTEM = "You simulate how specific synthetic people would answer a survey question, staying true to each person's profile and current views. Reply with JSON only."
POLL_USER = """CONTEXT: an online debate in {country} about "{topic}".
QUESTION: {question}
{options_rule}

PEOPLE:
{people}

For each person give their answer, a "score" from -1 (strongly negative toward what the question proposes) to 1 (strongly positive), and a one-sentence "reason" in their own voice.
Return JSON: {{"answers": [{{"id": 1, "answer": "", "score": 0.0, "reason": ""}}]}}"""


def stratified_sample(run: SimulationRun, n: int, seed: int) -> list[tuple[Agent, AgentState]]:
    rng = random.Random(seed)
    states = list(AgentState.objects.filter(run=run, agent__kind__in=["citizen", "influencer"]).select_related("agent"))
    by_seg = defaultdict(list)
    for s in states:
        by_seg[s.agent.archetype].append(s)
    total = len(states) or 1
    picked = []
    for seg, members in by_seg.items():
        k = max(1, round(n * len(members) / total))
        picked += rng.sample(members, min(k, len(members)))
    rng.shuffle(picked)
    return [(s.agent, s) for s in picked[:n]]


def heuristic_answer(options: list[str], score: float) -> str:
    if not options:
        return "Agree" if score > 0.2 else "Disagree" if score < -0.2 else "Not sure"
    if len(options) == 1:
        return options[0]
    lowered = [o.lower() for o in options]
    positive = next((o for o, lo in zip(options, lowered) if lo.startswith(("yes", "agree", "support", "approve"))), options[0])
    negative = next((o for o, lo in zip(options, lowered) if lo.startswith(("no", "disagree", "oppose", "reject"))), options[-1])
    neutral = next((o for o, lo in zip(options, lowered) if "sure" in lo or "know" in lo or "neutral" in lo or "undecided" in lo), None)
    if score > 0.2:
        return positive
    if score < -0.2:
        return negative
    return neutral or (positive if score >= 0 else negative)


def run_poll(ctx, poll_id):
    poll = Poll.objects.select_related("run__project").get(pk=poll_id)
    run = poll.run
    country = get_country(run.project.country)
    llm = get_llm(run.project)
    use_llm = llm.available("fast")
    sample = stratified_sample(run, poll.sample_size, seed=poll.pk)
    options_rule = f"Each answer MUST be exactly one of: {', '.join(poll.options)}." if poll.options else "Answer freely in a few words."
    responses = []
    batches = [sample[i : i + 4] for i in range(0, len(sample), 4)]
    for b, batch in enumerate(batches):
        ctx.check_cancel()
        ctx.progress(100.0 * b / max(1, len(batches)), "Interviewing respondents", f"{len(responses)} answers so far")
        data = {}
        if use_llm:
            people = "\n".join(
                f"{i + 1}. {a.name}, {a.age or ''} {a.archetype_label} from {a.region}; current view on the topic: {stance_label(s.stance)} ({s.stance:+.2f}); feels {s.emotion}; concerns: {', '.join((a.concerns or [])[:2])}."
                for i, (a, s) in enumerate(batch)
            )
            try:
                data = llm.json(
                    system=POLL_SYSTEM,
                    user=POLL_USER.format(country=country.name, topic=run.project.topic, question=poll.question, options_rule=options_rule, people=people),
                    purpose="poll",
                    tier="fast",
                    max_tokens=120 * len(batch) + 60,
                    temperature=0.5,
                )
            except LLMError:
                data = {}
        answers = {int(as_float(r.get("id"), 0)) - 1: r for r in (data.get("answers") or []) if isinstance(r, dict)}
        for i, (agent, state) in enumerate(batch):
            row = answers.get(i, {})
            score = as_float(row.get("score"), state.stance, -1.0, 1.0) if row else state.stance
            answer = as_str(row.get("answer")) if row else ""
            if poll.options and answer not in poll.options:
                match = next((o for o in poll.options if o.lower() in answer.lower() or answer.lower() in o.lower()), None) if answer else None
                answer = match or heuristic_answer(poll.options, score)
            elif not answer:
                answer = heuristic_answer(poll.options, score)
            responses.append(
                {
                    "agent": agent.pk, "handle": agent.handle, "name": agent.name, "segment": agent.archetype_label,
                    "region": agent.region_group or agent.region, "answer": answer, "score": round(score, 3),
                    "reason": as_str(row.get("reason"), max_len=240) if row else fallback_agent_answer(agent, state, run.project.topic),
                    "by": "llm" if row else "heuristic",
                }
            )
    counts = Counter(r["answer"] for r in responses)
    by_segment = defaultdict(Counter)
    for r in responses:
        by_segment[r["segment"]][r["answer"]] += 1
    poll.responses = responses
    poll.summary = {
        "counts": dict(counts.most_common()),
        "shares": {k: round(v / len(responses), 3) for k, v in counts.items()} if responses else {},
        "by_segment": {k: dict(v) for k, v in by_segment.items()},
        "mean_score": round(sum(r["score"] for r in responses) / len(responses), 3) if responses else None,
        "n": len(responses),
    }
    poll.status = "completed"
    poll.save()
    return {"poll": poll.pk, "n": len(responses)}
