"""
Runs a SimulationRun as a background job: builds the World from the database,
applies scheduled interventions (including live web refreshes), persists every
round (posts, reactions, agent states, metrics) so the UI can stream progress
and runs can be resumed after a restart.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.core.geo import get_country
from apps.core.llm import LLMError, as_float, as_str, get_llm
from apps.core.text.textutils import truncate

from .engine import TZ_OFFSETS, NullWriter, SimAgent, SimConfig, SimPost, TemplateWriter, World, summarize_history
from .models import Action, Agent, AgentState, Intervention, Post, RoundSnapshot, SimulationRun

logger = logging.getLogger("baraza.swarm")

PERSISTED_ACTIONS = {"like", "dislike", "repost", "follow", "unfollow", "mute"}


def agent_to_sim(a: Agent, state: AgentState | None) -> SimAgent:
    b5 = a.big5 or {}
    sim = SimAgent(
        idx=a.idx,
        pk=a.pk,
        handle=a.handle,
        name=a.name,
        kind=a.kind,
        archetype=a.archetype,
        archetype_label=a.archetype_label,
        stance=state.stance if state else a.initial_stance,
        initial=a.initial_stance,
        o=b5.get("o", 0.5),
        c=b5.get("c", 0.5),
        e=b5.get("e", 0.5),
        a=b5.get("a", 0.5),
        n=b5.get("n", 0.5),
        region=a.region,
        region_group=a.region_group,
        age=a.age,
        gender=a.gender,
        leaning=a.leaning,
        urban=a.urban,
        salience=a.salience,
        credibility=a.credibility,
        activity=a.activity,
        mu=a.susceptibility,
        eps=a.accept_latitude,
        rho=a.reject_latitude,
        gamma=a.stubbornness,
        platforms=dict(a.platforms or {}),
        following=set(state.following if state else a.following or []),
        muted=set(state.muted if state else []),
        groups=list(a.groups or []),
        bot_target=a.bot_target,
        persona=a.persona,
        bio=a.bio,
        voice=a.voice,
        concerns=list(a.concerns or []),
        occupation=a.occupation,
    )
    if state:
        sim.arousal = state.arousal
        sim.emotion = state.emotion
        sim.history = list(state.history or [])
        sim.posts_count = state.posts
        sim.reactions_count = state.reactions
        sim.last_active = state.last_active_round
        for m in state.memory or []:
            sim.memory.append(m)
    return sim


def load_arguments(project) -> list[dict]:
    return [
        {"id": a.pk, "side": a.side, "claim": a.claim, "short": a.short, "strength": a.strength, "frequency": a.frequency, "emotion": a.emotion}
        for a in project.arguments.all()
    ]


def knowledge_bits(project) -> tuple[list[str], list[str], list[str]]:
    from apps.knowledge.retrieval import stakeholder_lines

    try:
        dossier = project.dossier
    except Exception:
        return [], [], []
    facts = [f.get("fact", "") for f in dossier.key_facts or [] if f.get("fact")]
    return facts, stakeholder_lines(dossier), list(dossier.rumors or [])


def run_config(run: SimulationRun) -> SimConfig:
    data = dict(run.config or {})
    data.setdefault("tz_offset_hours", TZ_OFFSETS.get(run.project.country, 3))
    data.setdefault("language_style", run.project.language_style)
    return SimConfig.from_dict(data)


def build_world(run: SimulationRun, *, writer=None, log=None) -> World:
    project = run.project
    cfg = run_config(run)
    states = {s.agent_id: s for s in run.states.all()} if run.current_round > 0 else {}
    agents = [agent_to_sim(a, states.get(a.pk)) for a in project.agents.order_by("idx")]
    groups = {g.idx: list(g.members) for g in project.chat_groups.all()}
    facts, stakeholders, rumors = knowledge_bits(project)
    if writer is None:
        if cfg.mode == "dynamics":
            writer = NullWriter()
        elif cfg.mode == "template":
            country = get_country(project.country)
            writer = TemplateWriter(hashtags=(project.brief or {}).get("hashtags", {}), flavor=country.flavor, language_style=cfg.language_style, max_chars=cfg.max_post_chars, facts=facts, demonym=country.demonym)
        else:
            from .writer import LLMWriter

            writer = LLMWriter(project, cfg, facts=facts, stakeholders=stakeholders, topic=project.topic, log=log)
    # Runs created before start times were pinned fall back to when they started.
    start = cfg.start_datetime() if cfg.start_time else (run.started_at or timezone.now())
    world = World(agents, groups, cfg, writer=writer, arguments=load_arguments(project), rumors=rumors, start_time=start, start_round=run.current_round)
    if run.current_round > 0:
        by_pk = {a.pk: a.idx for a in agents}
        sims = []
        seq_by_pk = {}
        rows = list(run.posts.all().values())
        for row in rows:
            seq_by_pk[row["id"]] = row["seq"]
        for row in rows:
            sims.append(
                SimPost(
                    seq=row["seq"], platform=row["platform"], kind=row["kind"], author=by_pk.get(row["author_id"], 0), round=row["round"],
                    text=row["text"], title=row["title"], hashtags=row["hashtags"] or [], stance=row["stance"], strength=row["strength"],
                    arousal=row["arousal"], emotion=row["emotion"], argument_id=row["argument_id"], source=row["source"], is_misinfo=row["is_misinfo"],
                    intervention_id=row["intervention_id"], parent=seq_by_pk.get(row["parent_id"]), root=seq_by_pk.get(row["root_id"]),
                    group=row["group_idx"], sim_time=row["sim_time"], likes=row["likes"], dislikes=row["dislikes"], reposts=row["reposts"],
                    replies=row["replies"], views=row["views"], influence=row["influence"], persuaded=row["persuaded"], backfired=row["backfired"],
                    sentiment=row["sentiment"],
                )
            )
        world.load_posts(sims)
        last = run.snapshots.order_by("-round").first()
        if last:
            world.salience = last.metrics.get("salience", 1.0)
    return world


class Persister:
    """Writes each round to the database in a single transaction."""

    def __init__(self, run: SimulationRun, world: World):
        self.run = run
        self.world = world
        self.agent_pk = {a.idx: a.pk for a in world.agents}
        self.post_pk: dict[int, int] = dict(run.posts.values_list("seq", "id"))
        self.states: dict[int, AgentState] = {s.agent_id: s for s in run.states.all()}
        if not self.states:
            AgentState.objects.bulk_create(
                [AgentState(run=run, agent_id=a.pk, stance=a.stance, arousal=a.arousal, emotion=a.emotion, following=sorted(a.following), history=[round(a.stance, 3)]) for a in world.agents],
                batch_size=500,
            )
            self.states = {s.agent_id: s for s in run.states.all()}
            for a in world.agents:
                a.history = [round(a.stance, 3)]

    def initial_snapshot(self):
        from .engine.metrics import round_metrics

        if self.run.snapshots.filter(round=0).exists():
            return
        metrics = round_metrics(self.world, 0, [], [])
        metrics["round"] = 0
        RoundSnapshot.objects.create(run=self.run, round=0, sim_time=self.world.sim_time(0), metrics=metrics)

    def persist(self, result) -> None:
        world = self.world
        with transaction.atomic():
            new_posts = sorted(result.new_posts, key=lambda p: p.seq)
            objs = [
                Post(
                    run=self.run, seq=p.seq, round=p.round, platform=p.platform, kind=p.kind, author_id=self.agent_pk[p.author],
                    group_idx=p.group, title=truncate(p.title or "", 200), text=p.text or "", hashtags=p.hashtags, stance=round(p.stance, 4),
                    strength=p.strength, arousal=p.arousal, emotion=p.emotion, sentiment=p.sentiment, argument_id=p.argument_id,
                    source=p.source, is_misinfo=p.is_misinfo, intervention_id=p.intervention_id, sim_time=p.sim_time,
                    likes=p.likes, dislikes=p.dislikes, reposts=p.reposts, replies=p.replies, views=p.views,
                    influence=round(p.influence, 5), persuaded=p.persuaded, backfired=p.backfired,
                )
                for p in new_posts
            ]
            Post.objects.bulk_create(objs, batch_size=300)
            if any(o.pk is None for o in objs):  # backends that do not return ids
                lookup = dict(Post.objects.filter(run=self.run, seq__in=[p.seq for p in new_posts]).values_list("seq", "id"))
            else:
                lookup = {o.seq: o.pk for o in objs}
            self.post_pk.update(lookup)
            linked = []
            for p in new_posts:
                if p.parent or p.root:
                    linked.append(Post(pk=self.post_pk[p.seq], parent_id=self.post_pk.get(p.parent), root_id=self.post_pk.get(p.root)))
            if linked:
                Post.objects.bulk_update(linked, ["parent_id", "root_id"], batch_size=300)
            new_seqs = {p.seq for p in new_posts}
            touched = [world.posts[s] for s in result.touched_posts if s in world.posts and s not in new_seqs and s in self.post_pk]
            if touched:
                Post.objects.bulk_update(
                    [
                        Post(pk=self.post_pk[p.seq], likes=p.likes, dislikes=p.dislikes, reposts=p.reposts, replies=p.replies, views=p.views,
                             influence=round(p.influence, 5), persuaded=p.persuaded, backfired=p.backfired)
                        for p in touched
                    ],
                    ["likes", "dislikes", "reposts", "replies", "views", "influence", "persuaded", "backfired"],
                    batch_size=300,
                )
            Action.objects.bulk_create(
                [
                    Action(run=self.run, round=result.round, agent_id=self.agent_pk[a_idx], kind=kind, post_id=self.post_pk.get(seq) if seq else None, target_id=self.agent_pk.get(t_idx) if t_idx is not None else None)
                    for kind, a_idx, seq, t_idx in result.actions
                    if kind in PERSISTED_ACTIONS
                ],
                batch_size=500,
            )
            updates = []
            for agent in world.agents:
                state = self.states.get(agent.pk)
                if state is None:
                    continue
                state.stance = round(agent.stance, 4)
                state.arousal = round(agent.arousal, 4)
                state.emotion = agent.emotion
                state.followers = len(agent.followers)
                state.following = sorted(agent.following)
                state.muted = sorted(agent.muted)
                state.memory = list(agent.memory)
                state.history = agent.history
                state.posts = agent.posts_count
                state.reactions = agent.reactions_count
                state.last_active_round = agent.last_active
                updates.append(state)
            AgentState.objects.bulk_update(
                updates,
                ["stance", "arousal", "emotion", "followers", "following", "muted", "memory", "history", "posts", "reactions", "last_active_round"],
                batch_size=300,
            )
            RoundSnapshot.objects.update_or_create(run=self.run, round=result.round, defaults={"sim_time": result.sim_time, "metrics": result.metrics})
            SimulationRun.objects.filter(pk=self.run.pk).update(current_round=result.round)


NEWS_SYSTEM = "You turn real news headlines into short, neutral social media news posts and rate how they bear on a policy debate. Reply with JSON only."
NEWS_USER = """POLICY DEBATE: {title}
LATEST HEADLINE: {headline}
DETAILS: {details}

Return JSON: {{"post": "neutral news post under 240 characters", "stance": -1..1 (does this news make the policy look worse (-) or better (+)?), "strength": 0..1 (how significant)}}"""


def web_refresh_specs(run: SimulationRun, intervention: Intervention, log) -> list[dict]:
    """Live web grounding: fetch the latest real headlines and inject them as news."""
    from apps.research.pipeline import fresh_news

    project = run.project
    try:
        sources = fresh_news(project, max_items=3)
    except Exception as exc:  # network down, etc.
        log(f"Web refresh failed: {exc}")
        return []
    if not sources:
        log("Web refresh: no new headlines found.")
        return []
    llm = get_llm(project)
    specs = []
    for source in sources[:3]:
        text, stance, strength = truncate(source.title, 240), 0.0, 0.6
        if llm.available("fast"):
            try:
                data = llm.json(system=NEWS_SYSTEM, user=NEWS_USER.format(title=project.topic, headline=source.title, details=truncate(source.body, 600)), purpose="news_rewrite", tier="fast", max_tokens=200)
                text = as_str(data.get("post"), text, max_len=280)
                stance = as_float(data.get("stance"), 0.0, -1.0, 1.0)
                strength = as_float(data.get("strength"), 0.6, 0.0, 1.0)
            except LLMError:
                pass
        specs.append({"kind": "web_refresh", "title": truncate(source.title, 120), "content": text, "stance": stance, "strength": strength, "duration": 2, "id": intervention.pk})
    log(f"Web refresh: injected {len(specs)} real headline(s).")
    return specs


def intervention_spec(iv: Intervention, world: World) -> dict:
    actor_idx = None
    if iv.actor:
        handle = iv.actor.lstrip("@").lower()
        for a in world.agents:
            if a.handle.lower() == handle or a.name.lower() == iv.actor.lower():
                actor_idx = a.idx
                break
    return {
        "id": iv.pk,
        "kind": iv.kind,
        "title": iv.title,
        "content": iv.content or iv.title,
        "stance": iv.stance,
        "strength": iv.strength,
        "duration": iv.duration,
        "target_segments": iv.target_segments or [],
        "actor_idx": actor_idx,
    }


def run_simulation(ctx, run_id):
    run = SimulationRun.objects.select_related("project").get(pk=run_id)
    if not run.project.agents.exists():
        raise RuntimeError("This project has no population yet. Generate a population first.")
    if not run.started_at:
        run.started_at = timezone.now()
    run.status = SimulationRun.Status.RUNNING
    run.job_id = ctx.job_id
    run.save(update_fields=["status", "started_at", "job"])

    world = build_world(run, log=lambda m: ctx.log(m, "warning"))
    persister = Persister(run, world)
    persister.initial_snapshot()
    total = run.total_rounds
    ctx.log(f"Simulating {len(world.agents)} agents for {total} rounds of {world.cfg.step_hours:g}h ({world.cfg.mode} mode).")
    try:
        while world.round < total:
            ctx.check_cancel()
            next_round = world.round + 1
            specs = []
            for iv in Intervention.objects.filter(run=run, round__lte=next_round, applied=False).order_by("round", "pk"):
                if iv.kind == "web_refresh":
                    specs.extend(web_refresh_specs(run, iv, ctx.log))
                else:
                    specs.append(intervention_spec(iv, world))
                iv.applied = True
                iv.save(update_fields=["applied"])
            result = world.step(specs)
            for spec in specs:
                if spec.get("id") and spec.get("result"):
                    Intervention.objects.filter(pk=spec["id"]).update(result=spec["result"])
            persister.persist(result)
            m = result.metrics
            for event in result.events:
                ctx.log(f"Round {result.round}: {event}")
            ctx.progress(
                100.0 * result.round / total,
                f"Round {result.round} of {total}",
                f"{m.get('posts', 0)} new posts, {m.get('active', 0)} active agents, support {m.get('support', 0):.0%}, oppose {m.get('oppose', 0):.0%}",
            )
    except Exception:
        SimulationRun.objects.filter(pk=run.pk).update(status=SimulationRun.Status.STOPPED if ctx.is_cancelled() else SimulationRun.Status.FAILED)
        raise
    snapshots = list(run.snapshots.order_by("round").values_list("metrics", flat=True))
    summary = summarize_history([s for s in snapshots])
    writer_stats = getattr(world.writer, "stats", None)
    if writer_stats:
        summary["writer"] = writer_stats
    SimulationRun.objects.filter(pk=run.pk).update(status=SimulationRun.Status.COMPLETED, finished_at=timezone.now(), summary=summary)
    ctx.log(f"Done. Mean stance {summary.get('start_mean', 0):+.2f} -> {summary.get('end_mean', 0):+.2f} ({summary.get('trend')}).")
    return {"rounds": world.round, "posts": len(world.posts), "summary": summary}
