"""
The Observer: writes a decision-ready report about a finished simulation.

Numbers are computed first (analytics) and handed to the language model as a
compact facts sheet; the model only writes narrative around them, section by
section, so it cannot invent statistics. Every section also has a
deterministic template version, used when no model is available.
"""

from __future__ import annotations

import json
import logging

from django.utils import timezone

from apps.core.geo import get_country
from apps.core.llm import LLMError, get_llm
from apps.core.text.markdown import render_markdown
from apps.core.text.textutils import truncate
from apps.swarm.models import SimulationRun, stance_label

from .analytics import full_analytics
from .models import Report

logger = logging.getLogger("baraza.report")

SECTIONS = [
    ("summary", "Executive summary", "Write 4-6 sentences: the likely reception, how opinion changed, which groups are most opposed and most supportive, the biggest risk, and the single most important recommendation. Then add a bullet list titled **Key numbers** with 3-5 bullets."),
    ("trajectory", "How opinion evolved", "Describe the trajectory over simulated time in 2 short paragraphs: the starting point, turning points (tipping rounds and the events around them), and the end state."),
    ("segments", "Who supports and who opposes", "Explain which segments and regions ended up most opposed and most supportive, and which moved the most. Include a short markdown table with columns Segment | Final view | Change."),
    ("drivers", "What drove opinion", "Use bullet points: which arguments persuaded people and which backfired (with their persuasion rates), the dominant emotions, the most influential accounts and the trending hashtags."),
    ("polarisation", "Polarisation, echo chambers and misinformation", "Interpret the polarisation indices, echo-chamber indicators (modularity, E-I index, cross-cutting exposure) and any misinformation in plain language for a non-technical reader."),
    ("risks", "Risks and early-warning signals", "List 3-6 concrete risks or early-warning signals (for example mobilisation, misinformation, backlash in specific groups), each with what to watch for."),
    ("recommendations", "Recommendations", "Give 5-8 specific, actionable recommendations: what to say, to whom, through which channels and messengers, in what tone and when; policy adjustments worth considering; how to respond to misinformation. Base them on what persuaded versus what backfired in the simulation. Recommend transparency and genuine engagement; never manipulation or targeting of individuals."),
    ("limitations", "Confidence and limitations", "Explain confidence honestly: the population is synthetic, the opinion model has assumptions, research coverage may be uneven, validation results (if any). Say what would increase confidence (e.g. more real data, Monte Carlo ensembles, a real survey)."),
]

SYSTEM = """You are the Observer: an impartial senior analyst who watched a simulated online public debate in {country} about "{topic}".
You write for decision-makers: clear, specific, evidence-based and neutral. Use ONLY the numbers in the facts sheet; never invent statistics.
This is a simulation, not a poll: say "in the simulation". Use Markdown (paragraphs, bullets, tables) and do not add a top-level heading."""


def pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def signed(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}"


def build_facts(run: SimulationRun, analytics: dict) -> dict:
    project = run.project
    country = get_country(project.country)
    summary = analytics["summary"] or {}
    traj = analytics["trajectory"]
    segs = analytics["segments"]
    net = analytics["network"]
    posts = analytics["posts"]
    labels = traj["labels"]
    archetypes = segs["archetype"]["rows"]
    regions = segs["region"]["rows"]
    args = analytics["arguments"]
    cross = [c for c in traj["series"].get("cross_cutting", []) if c is not None]
    final_emotions = (run.snapshots.order_by("-round").first().metrics.get("emotions") if run.snapshots.exists() else {}) or {}
    try:
        dossier = project.dossier
        stakeholders = [{"name": s["name"], "position": s.get("position")} for s in (dossier.stakeholders or [])[:6]]
        context = truncate(dossier.summary, 600)
    except Exception:
        stakeholders, context = [], truncate((project.brief or {}).get("summary", project.prompt), 600)
    validation = run.validations.first()
    experiment = project.experiments.filter(status="completed", config__run_id=str(run.pk)).first()
    facts = {
        "topic": project.topic,
        "question": project.question,
        "country": country.name,
        "context": context,
        "stakeholders": stakeholders,
        "simulation": {
            "agents": project.agents.count(),
            "rounds": run.total_rounds,
            "hours_per_round": (run.config or {}).get("step_hours", 4),
            "simulated_days": round(run.total_rounds * float((run.config or {}).get("step_hours", 4)) / 24, 1),
            "posts": posts["total"],
            "ai_written_posts": posts["sources"].get("llm", 0),
            "echo_chamber_setting": (run.config or {}).get("echo_chamber", 0.5),
        },
        "opinion": {
            "start_mean": summary.get("start_mean"),
            "end_mean": summary.get("end_mean"),
            "change": summary.get("change"),
            "trend": summary.get("trend"),
            "start_support": summary.get("start_support"),
            "end_support": summary.get("end_support"),
            "start_oppose": summary.get("start_oppose"),
            "end_oppose": summary.get("end_oppose"),
            "peak_oppose": summary.get("peak_oppose"),
            "tipping_points": [labels[r] if 0 <= r < len(labels) else f"round {r}" for r in summary.get("tipping_rounds", [])],
        },
        "events": [{"when": labels[e["round"]] if e["round"] < len(labels) else e["round"], "what": e["events"]} for e in traj["events"]],
        "most_opposed_segments": [{"segment": r["name"], "final": r["final"], "change": r["change"], "n": r["n"]} for r in archetypes[:4]],
        "most_supportive_segments": [{"segment": r["name"], "final": r["final"], "change": r["change"], "n": r["n"]} for r in archetypes[-4:][::-1]],
        "biggest_shifts": sorted(({"segment": r["name"], "change": r["change"]} for r in archetypes), key=lambda r: -abs(r["change"]))[:4],
        "regions": [{"region": r["name"], "final": r["final"]} for r in regions[:3]] + [{"region": r["name"], "final": r["final"]} for r in regions[-3:]],
        "persuasive_arguments": [{"side": a["side"], "claim": truncate(a["claim"], 160), "persuasion_rate_per_100_views": a["persuasion_rate"], "views": a["views"]} for a in args[:5]],
        "backfiring_arguments": [{"side": a["side"], "claim": truncate(a["claim"], 160), "backfire_rate_per_100_views": a["backfire_rate"]} for a in sorted(args, key=lambda a: -a["backfire_rate"])[:3] if a["backfired"]],
        "influential_accounts": [{"account": f"@{i['handle']}", "type": i["kind"], "segment": i["archetype"], "view": i["label"]} for i in net.get("influencers", [])[:5]],
        "emotions_end": final_emotions,
        "peak_arousal": summary.get("max_arousal"),
        "hashtags": [h for h, _ in posts["hashtags"][:6]],
        "polarisation": {
            "esteban_ray_end": summary.get("end_polarisation"),
            "bimodality_end": summary.get("end_bimodality"),
            "bimodality_note": "values above 0.555 suggest two camps",
            "network_modularity": net.get("modularity"),
            "ei_index": net.get("ei_index"),
            "ei_note": "-1 = all interaction within one's own side, +1 = all across sides",
            "stance_assortativity": net.get("assortativity"),
            "cross_cutting_exposure": round(sum(cross) / len(cross), 3) if cross else None,
        },
        "mobilisation": {"peak_index": summary.get("peak_mobilisation"), "note": "share of public that is strongly opposed AND highly aroused"},
        "misinformation": posts["misinfo"],
    }
    if validation:
        facts["validation"] = {k: validation.metrics.get(k) for k in ("fit_score", "sentiment_jsd", "stance_jsd", "emotion_jsd", "n_real", "n_sim")}
    if experiment:
        facts["ensemble"] = {"runs": experiment.config.get("runs"), "effect": experiment.results.get("effect"), "final": {k: v.get("final", {}).get("mean") for k, v in experiment.results.get("variants", {}).items()}}
    return facts


def template_section(key: str, f: dict) -> str:
    o = f["opinion"]
    if key == "summary":
        opp = ", ".join(s["segment"] for s in f["most_opposed_segments"][:2]) or "n/a"
        sup = ", ".join(s["segment"] for s in f["most_supportive_segments"][:2]) or "n/a"
        return (
            f"In the simulation, public reaction to **{f['topic']}** in {f['country']} ended {stance_label(o['end_mean']).lower()} on average "
            f"(mean {signed(o['end_mean'])}, {o['trend']}). Support moved from {pct(o['start_support'])} to {pct(o['end_support'])} and opposition from "
            f"{pct(o['start_oppose'])} to {pct(o['end_oppose'])}. The most opposed groups were {opp}; the most supportive were {sup}.\n\n"
            f"**Key numbers**\n\n- Opposition peaked at {pct(o['peak_oppose'])}.\n- Mobilisation index peaked at {pct(f['mobilisation']['peak_index'])}.\n"
            f"- {f['simulation']['posts']} posts by {f['simulation']['agents']} agents over {f['simulation']['simulated_days']} simulated days."
        )
    if key == "trajectory":
        events = "; ".join(f"{e['when']}: {', '.join(e['what'])}" for e in f["events"]) or "no scheduled interventions"
        tips = ", ".join(f["opinion"]["tipping_points"]) or "none detected"
        return f"Opinion started at {signed(o['start_mean'])} and ended at {signed(o['end_mean'])} (change {signed(o['change'])}).\n\nEvents: {events}. Tipping points: {tips}."
    if key == "segments":
        rows = "\n".join(f"| {s['segment']} | {stance_label(s['final'])} | {signed(s['change'])} |" for s in f["most_opposed_segments"] + f["most_supportive_segments"])
        return f"| Segment | Final view | Change |\n|---|---|---|\n{rows}"
    if key == "drivers":
        lines = [f"- **{a['side'].title()}**: {a['claim']} ({a['persuasion_rate_per_100_views']} persuaded per 100 views)" for a in f["persuasive_arguments"]]
        lines += [f"- Backfired: {a['claim']} ({a['backfire_rate_per_100_views']} per 100 views)" for a in f["backfiring_arguments"]]
        lines.append(f"- Trending hashtags: {', '.join(f['hashtags']) or 'none'}")
        lines.append(f"- Influential accounts: {', '.join(i['account'] for i in f['influential_accounts']) or 'none'}")
        return "\n".join(lines)
    if key == "polarisation":
        p = f["polarisation"]
        return (
            f"- Esteban-Ray polarisation: {p['esteban_ray_end']} (0 = consensus, 1 = two extreme camps).\n- Bimodality coefficient: {p['bimodality_end']} ({p['bimodality_note']}).\n"
            f"- Network modularity: {p['network_modularity']}; E-I index: {p['ei_index']} ({p['ei_note']}).\n- Average cross-cutting exposure: {p['cross_cutting_exposure']}.\n"
            f"- Misinformation: {f['misinformation']['posts']} posts seen {f['misinformation']['views']} times."
        )
    if key == "risks":
        risks = []
        if (f["mobilisation"]["peak_index"] or 0) > 0.1:
            risks.append("- High mobilisation: a sizeable angry, strongly opposed group could organise offline action. Watch hashtags calling for action.")
        if f["misinformation"]["posts"]:
            risks.append("- Misinformation circulated; prepare fact-checks through trusted messengers.")
        for s in f["most_opposed_segments"][:2]:
            risks.append(f"- Backlash among {s['segment']} (final view {signed(s['final'])}).")
        return "\n".join(risks) or "- No major risk signals in this run."
    if key == "recommendations":
        best = [a for a in f["persuasive_arguments"] if a["side"] == "support"][:2]
        recs = ["- Lead with concrete, verifiable benefits and address the main objections directly."]
        recs += [f"- Amplify this message, which persuaded best: \"{a['claim']}\"." for a in best]
        recs += [
            f"- Engage the most opposed groups ({', '.join(s['segment'] for s in f['most_opposed_segments'][:2])}) through trusted local messengers.",
            "- Avoid extreme or dismissive messaging: messages far from people's views backfire.",
            "- Monitor WhatsApp-style groups for rumours and respond early with clear fact-checks.",
        ]
        return "\n".join(recs)
    return (
        "This is a simulation of synthetic people, calibrated with publicly available information. It explores plausible dynamics; it is not a forecast or a poll. "
        "Confidence improves with more real data, Monte Carlo ensembles (see Experiments) and validation against real posts or surveys."
    )


def generate_report(ctx, run_id):
    run = SimulationRun.objects.select_related("project").get(pk=run_id)
    report = Report.objects.create(run=run, status="running", title=f"Observer report: {run.project.topic}")
    try:
        ctx.progress(5, "Computing analytics")
        analytics = full_analytics(run)
        facts = build_facts(run, analytics)
        country = get_country(run.project.country)
        llm = get_llm(run.project)
        use_llm = llm.available("smart")
        if not use_llm:
            ctx.warn("No language model: writing the report from templates.")
        sections = []
        for i, (key, title, guidance) in enumerate(SECTIONS):
            ctx.check_cancel()
            ctx.progress(10 + 85 * i / len(SECTIONS), f"Writing: {title}")
            text, by = "", "template"
            if use_llm:
                try:
                    text = llm.complete(
                        SYSTEM.format(country=country.name, topic=run.project.topic),
                        f"Write the report section \"{title}\".\n{guidance}\n\nFACTS SHEET (JSON):\n{json.dumps(facts, ensure_ascii=False, default=str)[:7000]}",
                        purpose="report_section",
                        tier="smart",
                        max_tokens=700,
                        temperature=0.3,
                    )
                    by = "llm"
                except LLMError as exc:
                    ctx.warn(f"Section '{title}' fell back to template: {exc}")
            if not text.strip():
                text = template_section(key, facts)
                by = "template"
            sections.append({"key": key, "title": title, "markdown": text.strip(), "by": by})
        md = [f"# {report.title}", "", f"*Simulated scenario for {country.name}, generated {timezone.localtime().strftime('%d %b %Y %H:%M')}. Synthetic agents; not a poll or forecast.*", ""]
        for s in sections:
            md += [f"## {s['title']}", "", s["markdown"], ""]
        md += ["## Appendix: simulation settings", "", "| Setting | Value |", "|---|---|"]
        for k, v in (run.config or {}).items():
            md.append(f"| {k} | {v} |")
        report.sections = sections
        report.markdown = "\n".join(md)
        report.data = json.loads(json.dumps(facts, default=str))
        report.generated_by = "llm" if any(s["by"] == "llm" for s in sections) else "template"
        report.status = "completed"
        report.save()
    except Exception:
        Report.objects.filter(pk=report.pk).update(status="failed")
        raise
    return {"report": report.pk}


def report_html(report: Report) -> list[dict]:
    return [{"key": s["key"], "title": s["title"], "html": render_markdown(s["markdown"], heading_offset=2), "by": s.get("by")} for s in report.sections]
