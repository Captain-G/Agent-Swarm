"""
The simulated world: three platforms, a population of agents and the round loop.

Each round (a slice of simulated time):
1. Scheduled interventions are applied (breaking news, official statements,
   policy concessions, influencer campaigns, rumours, fact-checks).
2. Agents wake up with a probability driven by their activity level,
   time of day, emotional arousal, how much they care and how hot the topic is.
3. Each active agent opens one platform, reads a ranked feed, updates their
   opinion and emotions after every post, reacts (like / downvote / repost /
   forward / follow / mute) and may decide to reply or post.
4. Speaking intentions are turned into text: the most influential ones by the
   LLM (within a per-round budget), the rest by the zero-cost template writer.
5. Opinions are anchored, emotions decay, metrics are recorded.
"""

from __future__ import annotations

import math
import random
import re
from datetime import datetime, timedelta, timezone

import numpy as np

from apps.core.text.sentiment import sentiment as score_sentiment

from .config import SimConfig, hour_factor
from .content import TemplateWriter
from .dynamics import EMOTION_AROUSAL, clip, end_of_round, expose, policy_shift
from .feeds import FeedIndex, chat_view, thread_comments
from .metrics import round_metrics, sigmoid
from .state import ContentRequest, Draft, RoundResult, SimAgent, SimPost

HASHTAG_RE = re.compile(r"#\w{2,60}")


class World:
    def __init__(
        self,
        agents: list[SimAgent],
        groups: dict[int, list[int]],
        cfg: SimConfig,
        *,
        writer=None,
        arguments: list[dict] | None = None,
        rumors: list[str] | None = None,
        start_time: datetime | None = None,
        start_round: int = 0,
    ):
        self.agents = agents
        self.groups = groups
        self.cfg = cfg
        self.writer = writer or TemplateWriter()
        self.arguments = arguments or []
        self.rumors = [r for r in (rumors or []) if r]
        self.start_time = start_time or cfg.start_datetime()
        self.round = start_round
        self.rng = random.Random(cfg.seed * 7919 + start_round)
        self.nrng = np.random.default_rng(cfg.seed * 104729 + start_round)
        self.posts: dict[int, SimPost] = {}
        self.microblog: list[SimPost] = []
        self.threads: list[SimPost] = []
        self.comments: dict[int, list[SimPost]] = {}
        self.group_msgs: dict[int, list[SimPost]] = {}
        self.next_seq = 1
        self.salience = 1.0
        self.campaigns: list[dict] = []
        self.result: RoundResult | None = None
        self._forwards: list[ContentRequest] = []
        self._reindex_followers()

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------
    def _reindex_followers(self):
        for agent in self.agents:
            agent.followers = set()
        for agent in self.agents:
            agent.following = {f for f in agent.following if 0 <= f < len(self.agents) and f != agent.idx}
            for f in agent.following:
                self.agents[f].followers.add(agent.idx)

    def load_posts(self, posts: list[SimPost]):
        """Restore existing posts (resume)."""
        for post in sorted(posts, key=lambda p: p.seq):
            self._index(post)
            self.next_seq = max(self.next_seq, post.seq + 1)

    def sim_time(self, round_index: int) -> datetime:
        return self.start_time + timedelta(hours=self.cfg.step_hours * round_index)

    def _index(self, post: SimPost):
        self.posts[post.seq] = post
        if post.platform == "microblog":
            self.microblog.append(post)
        elif post.platform == "forum":
            if post.kind == "thread":
                self.threads.append(post)
                self.comments.setdefault(post.seq, [])
            elif post.root is not None:
                self.comments.setdefault(post.root, []).append(post)
        elif post.platform == "chat" and post.group is not None:
            self.group_msgs.setdefault(post.group, []).append(post)

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------
    def _request_seed(self, agent_idx: int, kind: str, salt: int = 0) -> int:
        return (self.cfg.seed * 1_000_003 + self.round * 7_919 + agent_idx * 131 + len(kind) * 17 + salt) % (2**31)

    def choose_argument(self, rng: random.Random, stance: float, exclude) -> dict | None:
        side = "support" if stance > 0.15 else "oppose" if stance < -0.15 else "neutral"
        pool = [a for a in self.arguments if a["side"] == side and a.get("id") not in exclude]
        if not pool:
            pool = [a for a in self.arguments if a["side"] == side] or self.arguments
        if not pool:
            return None
        weights = [(1 + a.get("frequency", 1)) * (0.5 + a.get("strength", 0.5)) for a in pool]
        return rng.choices(pool, weights=weights, k=1)[0]

    def prepare(self, req: ContentRequest) -> ContentRequest:
        agent = self.agents[req.agent]
        rng = random.Random(self._request_seed(req.agent, req.kind, req.parent or 0))
        req.seed = rng.randrange(2**31)
        if req.intent not in ("campaign", "inform") and not req.forced_text:
            req.stance = agent.bot_target if agent.kind == "bot" and agent.bot_target is not None else agent.stance
        if req.argument is None and not req.forced_text:
            if agent.kind == "bot" and self.rumors and (req.is_misinfo or rng.random() < 0.3):
                rumor = rng.choice(self.rumors)
                req.argument = {"id": None, "side": "oppose" if req.stance < 0 else "support", "claim": rumor, "short": rumor, "strength": 0.6, "emotion": "fear"}
                req.is_misinfo = True
            else:
                req.argument = self.choose_argument(rng, req.stance, set(agent.recent_args))
                if req.argument and req.argument.get("id") is not None:
                    agent.recent_args.append(req.argument["id"])
        arg_emotion = (req.argument or {}).get("emotion", "neutral")
        if req.intent not in ("inform",) and not req.forced_text:
            req.emotion = agent.emotion if agent.arousal > 0.3 and agent.emotion != "neutral" else arg_emotion
            req.arousal = clip(0.5 * agent.arousal + 0.5 * EMOTION_AROUSAL.get(req.emotion, 0.3), 0.0, 1.0)
        return req

    def create_post(self, req: ContentRequest, draft: Draft) -> SimPost:
        agent = self.agents[req.agent]
        noise = 0.0 if agent.kind in ("institution", "media", "bot") or req.forced_text else self.rng.gauss(0.0, 0.04)
        strength = (req.argument or {}).get("strength", 0.6 if req.forced_text else 0.5)
        if agent.kind in ("institution", "media") or agent.credibility > 0.6:
            strength = min(1.0, strength + 0.1)
        text = (draft.text or "").strip()
        hashtags = draft.hashtags or HASHTAG_RE.findall(text)
        post = SimPost(
            seq=self.next_seq,
            platform=req.platform,
            kind=req.kind,
            author=agent.idx,
            round=self.round,
            text=text,
            title=draft.title,
            hashtags=hashtags[:6],
            stance=clip(req.stance + noise),
            strength=round(strength, 3),
            arousal=round(req.arousal, 3),
            emotion=req.emotion,
            argument_id=(req.argument or {}).get("id"),
            source=draft.source if not req.forced_text else "intervention",
            is_misinfo=req.is_misinfo,
            factcheck=req.factcheck,
            intervention_id=req.intervention_id,
            parent=req.parent,
            root=req.root,
            group=req.group,
            sim_time=self.sim_time(self.round - 1) + timedelta(minutes=self.rng.randint(0, max(1, int(self.cfg.step_hours * 60) - 1))),
            boost=req.boost,
            boost_until=self.round + req.boost_rounds if req.boost_rounds else -1,
            sentiment=round(score_sentiment(text), 4) if text else 0.0,
        )
        self.next_seq += 1
        self._index(post)
        if req.parent and req.parent in self.posts:
            parent = self.posts[req.parent]
            parent.replies += 1
            self.result.touched_posts.add(parent.seq)
            if req.kind == "forward":
                parent.reposts += 1
        agent.posts_count += 1
        agent.recent_posts.append(post.seq)
        if text:
            agent.memory.append(f"Round {self.round}: I posted on {post.platform}: \"{text[:110]}\"")
        self.result.new_posts.append(post)
        self.result.actions.append(("post", agent.idx, post.seq, None))
        return post

    # ------------------------------------------------------------------
    # Interventions ("God mode")
    # ------------------------------------------------------------------
    def _matches(self, agent: SimAgent, segments: list[str]) -> bool:
        if not segments or "all" in [s.lower() for s in segments]:
            return True
        values = {agent.archetype.lower(), agent.archetype_label.lower(), agent.region.lower(), agent.region_group.lower(), agent.age_band.lower(), agent.leaning.lower(), ("urban" if agent.urban else "rural")}
        return any(s.lower() in values for s in segments)

    def _default_actor(self, kind: str) -> int:
        def best(kinds, key=lambda a: len(a.followers)):
            pool = [a for a in self.agents if a.kind in kinds]
            return max(pool, key=key).idx if pool else None

        if kind in ("breaking_news", "fact_check", "web_refresh"):
            choice = best(("media",), key=lambda a: a.credibility)
        elif kind == "misinformation":
            choice = best(("bot",)) or min((a for a in self.agents if a.is_public), key=lambda a: a.credibility).idx
        else:
            pool = [a for a in self.agents if a.kind == "institution" and a.archetype == "government"]
            pool = pool or [a for a in self.agents if a.kind == "institution" and a.archetype == "legislature"]
            choice = max(pool, key=lambda a: len(a.followers)).idx if pool else best(("institution",))
        if choice is None:
            choice = max(self.agents, key=lambda a: a.credibility).idx
        return choice

    def apply_intervention(self, spec: dict):
        kind = spec.get("kind", "breaking_news")
        strength = float(spec.get("strength", 0.7))
        stance = clip(float(spec.get("stance", 0.0)))
        duration = max(1, int(spec.get("duration", 1)))
        title = spec.get("title") or ""
        content = spec.get("content") or title
        iid = spec.get("id")
        self.salience = min(1.6, self.salience + 0.35 * strength)
        self.result.events.append(f"{kind.replace('_', ' ').title()}: {title or content[:80]}")

        if kind == "policy_change":
            delta = stance * strength * 0.5
            shifted = [policy_shift(a, delta) for a in self.agents if a.is_public and self._matches(a, spec.get("target_segments") or [])]
            spec["result"] = {"agents_affected": len(shifted), "mean_shift": round(sum(shifted) / len(shifted), 4) if shifted else 0.0}

        if kind == "influencer_campaign":
            pool = sorted([a for a in self.agents if a.kind == "influencer"], key=lambda a: -len(a.followers))
            if len(pool) < 3:
                pool += sorted([a for a in self.agents if a.kind == "citizen"], key=lambda a: -(a.e + len(a.followers) / 1000))[: 5 - len(pool)]
            k = max(3, int(spec.get("agents", 0)) or len(pool))
            members = [a.idx for a in pool[:k]]
            self.campaigns.append({"agents": members, "stance": stance, "until": self.round + duration - 1, "id": iid, "message": content})
            spec["result"] = {"campaigners": len(members)}
            return

        actor = spec.get("actor_idx")
        if actor is None:
            actor = self._default_actor(kind)
        boost = {"official_statement": 3.0, "breaking_news": 3.0, "fact_check": 2.5, "misinformation": 2.0, "policy_change": 3.0, "web_refresh": 2.0}.get(kind, 2.0)
        arousal = {"misinformation": 0.85, "breaking_news": 0.55, "official_statement": 0.4, "fact_check": 0.35, "policy_change": 0.5, "web_refresh": 0.5}.get(kind, 0.4)
        emotion = {"misinformation": "fear", "breaking_news": "neutral", "official_statement": "trust", "fact_check": "trust", "policy_change": "hope" if stance > 0 else "anger", "web_refresh": "neutral"}.get(kind, "neutral")
        base = dict(
            agent=actor, stance=stance, intent="inform", emotion=emotion, arousal=arousal, is_misinfo=kind == "misinformation", factcheck=kind == "fact_check",
            forced_text=content, forced_title=title, intervention_id=iid, boost=boost, boost_rounds=duration,
            argument={"id": None, "side": "support" if stance > 0.15 else "oppose" if stance < -0.15 else "neutral", "claim": content, "short": content, "strength": strength, "emotion": emotion},
        )
        platforms = self.cfg.platforms
        posts = []
        if "microblog" in platforms:
            posts.append(self.create_post(ContentRequest(platform="microblog", kind="news" if kind in ("breaking_news", "web_refresh", "fact_check") else "statement", **base), Draft(text=content, title=title)))
        if "forum" in platforms:
            posts.append(self.create_post(ContentRequest(platform="forum", kind="thread", **base), Draft(text=content, title=title or content[:90])))
        if "chat" in platforms and kind in ("misinformation", "breaking_news", "web_refresh") and self.groups:
            seeds = self.rng.sample(sorted(self.groups), min(len(self.groups), 3 if kind == "misinformation" else 2))
            for g in seeds:
                members = [m for m in self.groups[g] if self.agents[m].is_public or self.agents[m].kind == "bot"]
                if not members:
                    continue
                sender = self.rng.choice(members)
                req = ContentRequest(platform="chat", kind="forward", group=g, **{**base, "agent": sender})
                posts.append(self.create_post(req, Draft(text=f"Forwarded: {content}")))
        spec["result"] = {"posts": len(posts), "actor": self.agents[actor].handle}

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------
    def activity_prob(self, agent: SimAgent, hf: float, step_factor: float) -> float:
        if agent.kind == "bot":
            return 0.9
        # Poisson visits: agent.activity is roughly the chance of opening a social
        # app about this topic in a 4-hour slot at peak attention.
        rate = 3.0 * agent.activity * self.cfg.activity_scale * step_factor * hf
        rate *= (0.6 + 0.8 * agent.arousal) * (0.5 + agent.salience) * (0.4 + 0.6 * min(1.4, self.salience))
        return min(0.95, 1.0 - math.exp(-rate))

    def choose_platform(self, agent: SimAgent) -> str | None:
        options = []
        for platform in self.cfg.platforms:
            weight = agent.platforms.get(platform, 0.0)
            if platform == "chat" and not agent.groups:
                weight = 0.0
            if weight > 0:
                options.append((platform, weight))
        if not options:
            return None
        return self.rng.choices([o[0] for o in options], weights=[o[1] for o in options], k=1)[0]

    def react(self, agent: SimAgent, post: SimPost, platform: str) -> None:
        result = self.result
        author = self.agents[post.author]
        agree = 1.0 - abs(post.stance - agent.stance) / 2.0
        followed = post.author in agent.following
        if agent.kind == "bot":
            like_p = 0.9 if (post.stance > 0) == ((agent.bot_target or 0) > 0) and abs(post.stance) > 0.15 else 0.0
        else:
            like_p = sigmoid(-4.6 + 3.5 * agree + 0.9 * (agent.e - 0.5) + 0.25 * math.log1p(post.likes) + (0.5 if followed else 0.0) + 0.6 * post.arousal * agree)
        liked = self.rng.random() < like_p
        if liked:
            post.likes += 1
            agent.reactions_count += 1
            result.actions.append(("like", agent.idx, post.seq, None))
        elif platform == "forum" and agent.kind != "institution":
            if self.rng.random() < sigmoid(-4.6 + 3.5 * (1 - agree) + 0.6 * (0.5 - agent.a)):
                post.dislikes += 1
                agent.reactions_count += 1
                result.actions.append(("dislike", agent.idx, post.seq, None))
        if liked and platform == "microblog" and agent.kind != "institution":
            if self.rng.random() < sigmoid(-3.2 + 2.0 * agent.e + 1.5 * agent.arousal + 1.0 * post.arousal + (2.0 if agent.kind == "bot" else 0.0)):
                post.reposts += 1
                post.reposted_by.add(agent.idx)
                result.actions.append(("repost", agent.idx, post.seq, None))
        if liked and platform == "chat" and len(agent.groups) > 1 and post.text:
            forward_p = sigmoid(-3.5 + 1.5 * agent.arousal + 1.5 * post.arousal + (1.0 if post.is_misinfo else 0.0) - 1.2 * agent.c)
            if self.rng.random() < forward_p:
                target = self.rng.choice([g for g in agent.groups if g != post.group] or agent.groups)
                self._forwards.append(
                    ContentRequest(
                        agent=agent.idx, platform="chat", kind="forward", group=target, parent=post.seq, stance=post.stance,
                        intent="inform", emotion=post.emotion, arousal=post.arousal, is_misinfo=post.is_misinfo,
                        forced_text=post.text if post.text.startswith("Forwarded") else f"Forwarded: {post.text}",
                        argument={"id": post.argument_id, "strength": post.strength * 0.9, "side": "support" if post.stance > 0.15 else "oppose" if post.stance < -0.15 else "neutral"},
                    )
                )
        if platform != "chat" and post.author != agent.idx:
            can_follow = author.kind != "bot" or agent.kind == "bot"
            if liked and not followed and can_follow and self.rng.random() < 0.10 + 0.22 * agree:
                agent.following.add(post.author)
                author.followers.add(agent.idx)
                result.actions.append(("follow", agent.idx, None, post.author))
            elif not liked and agree < 0.3 and agent.a < 0.35 and agent.kind == "citizen" and self.rng.random() < 0.05:
                agent.muted.add(post.author)
                if post.author in agent.following:
                    agent.following.discard(post.author)
                    author.followers.discard(agent.idx)
                    result.actions.append(("unfollow", agent.idx, None, post.author))
                result.actions.append(("mute", agent.idx, None, post.author))

    def reply_intent(self, agent: SimAgent, post: SimPost) -> str | None:
        if post.author == agent.idx or agent.kind == "media":
            return None
        ad = abs(post.stance - agent.stance)
        directed = post.parent is not None and post.parent in self.posts and self.posts[post.parent].author == agent.idx
        logit = -4.2 + 2.2 * agent.arousal + 1.4 * agent.e
        if ad > agent.rho:
            logit += 1.8 * (1.0 - agent.a)
        elif ad < agent.eps:
            logit += 0.8 * agent.e
        if directed:
            logit += 1.0
        if agent.kind == "bot":
            logit = 1.0 if ad > 0.6 else -2.0
        elif agent.kind == "institution":
            logit -= 1.5
        if self.rng.random() < sigmoid(logit):
            return "rebut" if ad > agent.eps else "agree"
        return None

    def post_intent(self, agent: SimAgent) -> bool:
        if agent.kind == "bot":
            return self.rng.random() < 0.7
        logit = -3.0 + 2.0 * agent.e + 1.5 * agent.arousal + 1.2 * abs(agent.stance) + 0.8 * agent.salience
        if agent.kind in ("institution", "media"):
            logit = -0.8 + 1.2 * min(1.4, self.salience)
        return self.rng.random() < sigmoid(logit)

    def priority(self, agent: SimAgent) -> float:
        return 2.0 * agent.credibility + 0.5 * math.log10(1 + len(agent.followers)) + agent.arousal + 0.5 * self.rng.random() + (1.0 if agent.kind in ("influencer", "institution", "media") else 0.0)

    # ------------------------------------------------------------------
    # The round
    # ------------------------------------------------------------------
    def step(self, interventions: list[dict] | None = None) -> RoundResult:
        self.round += 1
        r = self.round
        start = self.sim_time(r - 1)
        self.result = RoundResult(round=r, sim_time=start)
        self._forwards: list[ContentRequest] = []
        self._exposures = 0
        self._cross_cutting = 0
        self._exposure_distance = 0.0
        for spec in interventions or []:
            self.apply_intervention(spec)

        cfg = self.cfg
        hf = hour_factor(start.astimezone(timezone(timedelta(hours=cfg.tz_offset_hours))).hour, cfg.step_hours)
        step_factor = math.sqrt(cfg.step_hours / 4.0)
        echo = cfg.echo_chamber
        micro_index = FeedIndex([p for p in self.microblog if p.kind != "reply" or p.likes > 1], self.agents, r, cfg.recency_window) if "microblog" in cfg.platforms else None
        forum_index = FeedIndex(self.threads, self.agents, r, cfg.recency_window * 2) if "forum" in cfg.platforms else None
        campaign_members = {}
        for campaign in self.campaigns:
            if campaign["until"] >= r:
                for idx in campaign["agents"]:
                    campaign_members[idx] = campaign

        requests: list[ContentRequest] = []
        order = list(range(len(self.agents)))
        self.rng.shuffle(order)
        active = 0
        for i in order:
            agent = self.agents[i]
            campaign = campaign_members.get(i)
            if campaign is None and self.rng.random() >= self.activity_prob(agent, hf, step_factor):
                continue
            platform = self.choose_platform(agent)
            if platform is None:
                continue
            active += 1
            agent.last_active = r
            exposures: list[SimPost] = []
            thread = None
            group = None
            if platform == "microblog" and micro_index is not None:
                exposures = micro_index.microblog(agent, cfg.feed_size, echo, self.nrng)
            elif platform == "forum" and forum_index is not None:
                picked = forum_index.forum_threads(agent, echo, self.nrng, 1)
                if picked:
                    thread = picked[0]
                    exposures = [thread] + thread_comments(thread, self.comments.get(thread.seq, []), agent, cfg.feed_size - 1, echo, self.nrng)
            elif platform == "chat":
                group, exposures = chat_view(agent, self.group_msgs, r, cfg.recency_window, cfg.feed_size, self.rng)

            intent_req: ContentRequest | None = None
            for post in exposures:
                author = self.agents[post.author]
                post.views += 1
                self.result.touched_posts.add(post.seq)
                delta = expose(agent, author, post, cfg, platform)
                if agent.is_public:
                    self._exposures += 1
                    self._exposure_distance += abs(post.stance - agent.stance)
                    if post.stance * agent.stance < 0 and abs(post.stance) > 0.15 and abs(agent.stance) > 0.15:
                        self._cross_cutting += 1
                if abs(delta) > 0.04:
                    agent.memory.append(f"Round {r}: @{author.handle}'s post {'moved me' if delta * (post.stance - agent.stance + delta) > 0 else 'pushed me away'}: \"{post.text[:80]}\"")
                self.react(agent, post, platform)
                if intent_req is None and campaign is None:
                    intent = self.reply_intent(agent, post)
                    if intent:
                        kind = {"microblog": "reply", "forum": "comment", "chat": "message"}[platform]
                        root = (thread.seq if thread else post.root) if platform == "forum" else None
                        intent_req = ContentRequest(agent=i, platform=platform, kind=kind, parent=post.seq, root=root, group=group if platform == "chat" else None, intent=intent, priority=self.priority(agent))
            if intent_req is None and (campaign is not None or self.post_intent(agent)):
                if platform == "forum":
                    if thread is not None and self.rng.random() < 0.65:
                        intent_req = ContentRequest(agent=i, platform="forum", kind="comment", parent=thread.seq, root=thread.seq, priority=self.priority(agent))
                    else:
                        intent_req = ContentRequest(agent=i, platform="forum", kind="thread", priority=self.priority(agent))
                elif platform == "chat" and group is not None:
                    intent_req = ContentRequest(agent=i, platform="chat", kind="message", group=group, priority=self.priority(agent))
                else:
                    intent_req = ContentRequest(agent=i, platform="microblog" if "microblog" in cfg.platforms else platform, kind="post", priority=self.priority(agent))
                if campaign is not None:
                    intent_req.intent = "campaign"
                    intent_req.stance = campaign["stance"]
                    intent_req.intervention_id = campaign["id"]
                    intent_req.boost = 1.8
                    intent_req.boost_rounds = 1
                    intent_req.priority += 2.0
            if intent_req is not None:
                requests.append(intent_req)

        self._resolve(requests)
        for req in self._forwards:
            req.seed = self._request_seed(req.agent, "forward", req.parent or 0)
            self.create_post(req, Draft(text=req.forced_text))
        for agent in self.agents:
            end_of_round(agent, cfg, self.rng)
        self.salience = max(cfg.min_salience, self.salience * cfg.salience_decay)
        self.result.metrics = round_metrics(self, active, self.result.new_posts, self.result.actions)
        self.result.metrics["round"] = r
        self.result.metrics["exposures"] = self._exposures
        self.result.metrics["cross_cutting"] = round(self._cross_cutting / self._exposures, 4) if self._exposures else 0.0
        self.result.metrics["exposure_distance"] = round(self._exposure_distance / self._exposures, 4) if self._exposures else 0.0
        self.result.metrics["events"] = list(self.result.events)
        return self.result

    def _resolve(self, requests: list[ContentRequest]) -> None:
        if not requests:
            return
        for req in requests:
            self.prepare(req)
        budget = self.cfg.llm_posts_per_round if self.cfg.mode == "hybrid" else 0
        llm_reqs: list[ContentRequest] = []
        if budget > 0:
            by_side = {"oppose": [], "neutral": [], "support": []}
            for req in sorted(requests, key=lambda q: -q.priority):
                side = "support" if req.stance > 0.15 else "oppose" if req.stance < -0.15 else "neutral"
                by_side[side].append(req)
            while len(llm_reqs) < budget and any(by_side.values()):
                for side in ("oppose", "support", "neutral"):
                    if by_side[side] and len(llm_reqs) < budget:
                        llm_reqs.append(by_side[side].pop(0))
        chosen = {id(r) for r in llm_reqs}
        template_reqs = [r for r in requests if id(r) not in chosen]
        drafts: dict[int, Draft] = {}
        if llm_reqs:
            for req, draft in zip(llm_reqs, self.writer.write(self, llm_reqs, use_llm=True)):
                drafts[id(req)] = draft
        if template_reqs:
            for req, draft in zip(template_reqs, self.writer.write(self, template_reqs, use_llm=False)):
                drafts[id(req)] = draft
        for req in requests:
            self.create_post(req, drafts[id(req)])
