"""The opinion-dynamics engine (pure Python, no database)."""

from __future__ import annotations

import random

import numpy as np
from django.test import SimpleTestCase

from apps.swarm.engine import SimAgent, SimConfig, TemplateWriter, World, summarize_history
from apps.swarm.engine.config import PRESETS
from apps.swarm.engine.dynamics import end_of_round, expose, policy_shift
from apps.swarm.engine.metrics import bimodality, distribution_metrics, esteban_ray
from apps.swarm.engine.state import SimPost

ARGUMENTS = [
    {"id": 1, "side": "oppose", "claim": "VAT on bread will hurt poor families", "short": "VAT on bread hurts poor families", "strength": 0.7, "frequency": 5, "emotion": "anger"},
    {"id": 2, "side": "oppose", "claim": "Mobile money tax punishes the unbanked", "short": "Taxing M-Pesa punishes the unbanked", "strength": 0.6, "frequency": 3, "emotion": "anger"},
    {"id": 3, "side": "support", "claim": "We need revenue to reduce borrowing", "short": "We must reduce borrowing", "strength": 0.6, "frequency": 3, "emotion": "trust"},
    {"id": 4, "side": "support", "claim": "Funds will build roads and hospitals", "short": "Taxes build hospitals", "strength": 0.5, "frequency": 2, "emotion": "hope"},
    {"id": 5, "side": "neutral", "claim": "We need more public participation", "short": "More public participation please", "strength": 0.4, "frequency": 2, "emotion": "neutral"},
]


def make_agents(n: int = 120, seed: int = 1, mean: float = -0.2):
    rng = random.Random(seed)
    agents = []
    for i in range(n):
        b = {t: min(0.98, max(0.02, rng.gauss(0.5, 0.15))) for t in "ocean"}
        s = max(-1.0, min(1.0, rng.gauss(mean, 0.4)))
        eps = 0.2 + 0.4 * b["o"]
        agents.append(
            SimAgent(
                idx=i, pk=None, handle=f"u{i}", name=f"User {i}", kind="citizen", archetype=rng.choice(["student", "farmer", "vendor"]),
                archetype_label="X", stance=s, initial=s, o=b["o"], c=b["c"], e=b["e"], a=b["a"], n=b["n"],
                region=rng.choice(["Nairobi", "Kisumu", "Mombasa"]), region_group="R", age=rng.randint(18, 70), gender=rng.choice(["male", "female"]),
                leaning="non_aligned", salience=rng.uniform(0.3, 0.9), credibility=0.35, activity=0.12 * (0.5 + b["e"]),
                mu=0.04 + 0.16 * (0.5 * b["o"] + 0.3 * b["a"] + 0.2 * (1 - b["c"])), eps=eps, rho=min(1.9, eps + 0.3 + 0.5 * b["a"]),
                gamma=0.02 + 0.1 * b["c"], platforms={"microblog": 0.4, "forum": 0.3, "chat": 0.3},
            )
        )
    for j, st in enumerate([0.8, -0.6]):
        i = len(agents)
        agents.append(SimAgent(idx=i, pk=None, handle=f"inst{j}", name=f"Inst {j}", kind="institution", archetype="government", archetype_label="Gov", stance=st, initial=st, credibility=0.5, activity=0.5, mu=0.01, eps=0.3, rho=1.2, gamma=0.35, salience=0.9, platforms={"microblog": 0.6, "forum": 0.4}))
    i = len(agents)
    agents.append(SimAgent(idx=i, pk=None, handle="news", name="News", kind="media", archetype="media", archetype_label="Media", stance=0.0, initial=0.0, credibility=0.65, activity=0.6, mu=0.01, gamma=0.4, salience=0.8, platforms={"microblog": 0.6, "forum": 0.4}))
    for a in agents:
        a.following = set(rng.sample(range(len(agents)), rng.randint(5, 20))) - {a.idx}
    groups = {}
    for g in range(12):
        members = rng.sample(range(n), 10)
        groups[g] = members
        for m in members:
            agents[m].groups.append(g)
    return agents, groups


def simulate(cfg_overrides: dict | None = None, interventions: dict | None = None, seed: int = 1, n: int = 120, mean: float = -0.2):
    agents, groups = make_agents(n=n, seed=seed, mean=mean)
    cfg = SimConfig.from_dict({"mode": "template", "rounds": 12, **(cfg_overrides or {})})
    world = World(agents, groups, cfg, writer=TemplateWriter(hashtags={"oppose": ["#RejectFinanceBill"], "support": ["#FinanceBillWorks"]}), arguments=ARGUMENTS, rumors=["The bill will tax all savings"])
    snapshots = []
    for r in range(cfg.rounds):
        snapshots.append(world.step((interventions or {}).get(r + 1, [])).metrics)
    return world, snapshots


def public_mean(world) -> float:
    return float(np.mean([a.stance for a in world.agents if a.is_public]))


def post(stance: float, author: int = 1, **kw) -> SimPost:
    defaults = dict(seq=1, platform="microblog", kind="post", author=author, round=0, text="t", stance=stance, strength=0.6, arousal=0.4)
    defaults.update(kw)
    return SimPost(**defaults)


class DynamicsTests(SimpleTestCase):
    def _agent(self, stance: float, **kw) -> SimAgent:
        base = dict(idx=0, pk=None, handle="a", name="A", kind="citizen", archetype="x", archetype_label="X", stance=stance, initial=stance, mu=0.2, eps=0.4, rho=1.0, salience=0.8)
        base.update(kw)
        return SimAgent(**base)

    def _author(self) -> SimAgent:
        return SimAgent(idx=1, pk=None, handle="b", name="B", kind="citizen", archetype="x", archetype_label="X", stance=0.0, initial=0.0, credibility=0.5)

    def test_assimilation_within_latitude_of_acceptance(self):
        agent = self._agent(0.0)
        change = expose(agent, self._author(), post(0.3), SimConfig(intensify=0), "microblog")
        self.assertGreater(change, 0)
        self.assertLess(agent.stance, 0.3)

    def test_backfire_beyond_latitude_of_rejection(self):
        agent = self._agent(-0.6, rho=0.8)
        change = expose(agent, self._author(), post(0.9), SimConfig(), "microblog")
        self.assertLess(change, 0)  # moved away from the post

    def test_group_polarisation_intensifies_aligned_aroused_agents(self):
        cfg_off, cfg_on = SimConfig(intensify=0.0), SimConfig(intensify=1.0)
        calm, hot = self._agent(-0.4, arousal=0.9), self._agent(-0.4, arousal=0.9)
        expose(calm, self._author(), post(-0.5, arousal=0.9), cfg_off, "microblog")
        expose(hot, self._author(), post(-0.5, arousal=0.9), cfg_on, "microblog")
        self.assertLess(hot.stance, calm.stance)

    def test_fact_check_partially_corrects_misinformation(self):
        agent = self._agent(0.0, c=0.5)
        expose(agent, self._author(), post(-0.4, is_misinfo=True), SimConfig(), "microblog")
        misled = agent.stance
        self.assertLess(misled, 0)
        expose(agent, self._author(), post(0.0, factcheck=True), SimConfig(), "microblog")
        self.assertGreater(agent.stance, misled)
        self.assertLess(agent.stance, 0.0 + 1e-9)  # continued-influence effect: not fully undone

    def test_conscientious_readers_resist_misinformation(self):
        careless, careful = self._agent(0.0, c=0.1), self._agent(0.0, c=0.9)
        expose(careless, self._author(), post(-0.4, is_misinfo=True), SimConfig(), "microblog")
        expose(careful, self._author(), post(-0.4, is_misinfo=True), SimConfig(), "microblog")
        self.assertLess(abs(careful.stance), abs(careless.stance))

    def test_bots_are_not_persuaded_and_anchor_to_target(self):
        bot = self._agent(-0.8, kind="bot", bot_target=-0.8)
        self.assertEqual(expose(bot, self._author(), post(0.9), SimConfig(), "microblog"), 0.0)
        bot.stance = 0.0
        end_of_round(bot, SimConfig(), random.Random(1))
        self.assertEqual(bot.stance, -0.8)

    def test_anchoring_pulls_toward_initial_view(self):
        agent = self._agent(0.5, gamma=0.5)
        agent.initial = 0.0
        end_of_round(agent, SimConfig(noise=0.0, anchoring=0.5), random.Random(1))
        self.assertLess(agent.stance, 0.5)
        self.assertGreater(agent.stance, 0.0)

    def test_policy_shift_moves_anchor(self):
        agent = self._agent(-0.5, gamma=0.05)
        shift = policy_shift(agent, 0.3)
        self.assertGreater(shift, 0)
        self.assertGreater(agent.initial, -0.5)


class MetricsTests(SimpleTestCase):
    def test_polarisation_measures(self):
        consensus = np.zeros(100)
        split = np.array([-0.9] * 50 + [0.9] * 50)
        self.assertAlmostEqual(esteban_ray(consensus), 0.0, places=4)
        self.assertGreater(esteban_ray(split), 0.5)
        self.assertGreater(bimodality(split), 0.555)  # above the uniform-distribution threshold
        m = distribution_metrics(split)
        self.assertEqual((m["support"], m["oppose"]), (0.5, 0.5))
        self.assertAlmostEqual(m["divergence"], 1.8)
        self.assertEqual(sum(m["histogram"]), 100)

    def test_presets_are_valid(self):
        for key, preset in PRESETS.items():
            cfg = SimConfig.from_dict(preset["config"])
            self.assertGreater(cfg.rounds, 0, key)

    def test_default_clock_starts_at_six_local(self):
        from datetime import timedelta, timezone

        from apps.swarm.engine.config import default_start

        start = default_start(3)
        self.assertEqual(start.astimezone(timezone(timedelta(hours=3))).hour, 6)
        self.assertEqual(SimConfig.from_dict({"start_time": start.isoformat()}).start_datetime(), start)
        self.assertEqual(SimConfig.from_dict({"start_time": "not a date", "tz_offset_hours": 1}).start_datetime(), default_start(1))

    def test_country_time_zones(self):
        from datetime import timedelta

        from apps.swarm.engine.config import country_timezone

        self.assertEqual(country_timezone("KE").utcoffset(None), timedelta(hours=3))
        self.assertEqual(country_timezone("NG").utcoffset(None), timedelta(hours=1))
        self.assertEqual(country_timezone("GH").tzname(None), "GMT")

    def test_config_is_clamped(self):
        cfg = SimConfig.from_dict({"rounds": 10_000, "echo_chamber": 3, "platforms": ["myspace"], "mode": "magic", "unknown": 1})
        self.assertEqual((cfg.rounds, cfg.echo_chamber, cfg.platforms, cfg.mode), (500, 1.0, ["microblog"], "hybrid"))


class WorldTests(SimpleTestCase):
    def test_same_seed_same_result(self):
        w1, s1 = simulate({"seed": 3})
        w2, s2 = simulate({"seed": 3})
        self.assertEqual([m["mean"] for m in s1], [m["mean"] for m in s2])
        self.assertEqual(len(w1.posts), len(w2.posts))
        _, s3 = simulate({"seed": 4})
        self.assertNotEqual([m["mean"] for m in s1], [m["mean"] for m in s3])

    def test_world_produces_activity_on_all_platforms(self):
        world, snapshots = simulate()
        platforms = {p.platform for p in world.posts.values()}
        self.assertEqual(platforms, {"microblog", "forum", "chat"})
        self.assertTrue(all(m["active"] > 0 for m in snapshots))
        self.assertTrue(all(-1 <= a.stance <= 1 for a in world.agents))
        texts = [p.text for p in world.posts.values() if p.platform == "microblog"]
        self.assertTrue(all(len(t) <= 280 for t in texts))
        summary = summarize_history(snapshots)
        self.assertEqual(summary["total_posts"], len(world.posts))

    def test_concession_reduces_opposition(self):
        concession = {3: [{"kind": "policy_change", "title": "Bread exempted", "content": "VAT on bread dropped", "stance": 0.7, "strength": 0.9, "target_segments": ["all"]}]}
        base, _ = simulate({"seed": 5})
        conceded, _ = simulate({"seed": 5}, concession)
        self.assertGreater(public_mean(conceded), public_mean(base))

    def test_misinformation_raises_opposition_and_fact_check_helps(self):
        rumour = {3: [{"kind": "misinformation", "title": "Rumour", "content": "They will tax all M-Pesa savings!", "stance": -0.9, "strength": 0.9, "duration": 3}]}
        corrected = {**rumour, 5: [{"kind": "fact_check", "title": "FALSE", "content": "Savings are not taxed.", "stance": 0.2, "strength": 0.9, "duration": 3}]}
        means = {}
        for label, plan in (("base", None), ("rumour", rumour), ("corrected", corrected)):
            values = [public_mean(simulate({"seed": s}, plan)[0]) for s in (1, 2, 3)]
            means[label] = sum(values) / 3
        self.assertLess(means["rumour"], means["base"])
        self.assertGreater(means["corrected"], means["rumour"])

    def test_echo_chambers_reduce_cross_cutting_exposure(self):
        _, open_feed = simulate({"seed": 2, "echo_chamber": 0.0})
        _, bubble = simulate({"seed": 2, "echo_chamber": 1.0})
        cross = lambda snaps: np.mean([m.get("cross_cutting", 0) for m in snaps])
        self.assertGreater(cross(open_feed), cross(bubble))

    def test_template_mode_never_calls_llm_writer(self):
        class ExplodingWriter(TemplateWriter):
            def write(self, world, requests, use_llm=False):
                assert not use_llm, "template mode must not request LLM text"
                return super().write(world, requests, use_llm)

        agents, groups = make_agents(n=40)
        world = World(agents, groups, SimConfig.from_dict({"mode": "template", "rounds": 3}), writer=ExplodingWriter(), arguments=ARGUMENTS)
        for _ in range(3):
            world.step()

    def test_dynamics_mode_runs(self):
        world, snapshots = simulate({"mode": "dynamics", "rounds": 4})
        self.assertEqual(len(snapshots), 4)
