# Research notes

These notes are for researchers using Baraza in academic work, for example an MSc or PhD thesis on simulating public sentiment with large-language-model agents. They summarise how Baraza relates to MiroFish and OASIS, what is new, and how the system can be evaluated rigorously.

## From MiroFish to Baraza

[MiroFish](https://github.com/666ghj/MiroFish) showed an appealing workflow: upload seed material, build a knowledge graph, generate agent personas, simulate them on social platforms with [OASIS](https://github.com/camel-ai/oasis) (CAMEL-AI), and interrogate the result through a report agent. Baraza keeps that workflow and re-engineers each part for cost, grounding, rigour and an African context.

| Concern | MiroFish | Baraza |
|---|---|---|
| Application | Flask API + Vue front end; Chinese-first interface | Single Django application; English interface; server-rendered, accessible UI; works offline |
| Language model | Hosted API (Qwen via DashScope by default) | Local Llama 3 through Ollama by default; any OpenAI-compatible API; hybrid routing; offline demo model; graceful no-model mode |
| Memory / knowledge graph | Zep Cloud (hosted, paid beyond a free tier) | Local GraphRAG in the project database: LLM extraction, entity resolution, PageRank, Louvain communities, hybrid BM25 + embedding retrieval |
| Seed data | User uploads | Automatic deep research from free web, news and social sources (plus uploads), relevance ranking and stance labelling |
| Prompt handling | Used largely as given | Restructured into an editable research brief with affected groups, stakeholders, queries in local languages and testable hypotheses |
| Agents | One LLM call per agent action (OASIS) | Explicit opinion-dynamics model decides behaviour; LLM writes only the most visible posts within a per-round budget; template writer for the rest |
| Population | LLM-generated profiles from graph entities | Census-based demographic sampling (47 Kenyan counties and seven more countries), 25 local archetypes, Big Five traits, evidence-derived starting opinions, calibration to observed stance, institutions from the graph, coordinated accounts |
| Platforms | Twitter- and Reddit-like | X-style microblog, Facebook/Reddit-style forum and WhatsApp-style group chats (forwarding, close ties) |
| Interventions | Variable injection | Typed interventions with modelled effects, scheduled or live, including **live web refresh** of real headlines |
| Uncertainty | Single runs | Seeded reproducibility, Monte Carlo ensembles with confidence intervals, parameter sweeps |
| Evaluation | Qualitative | Validation against real posts (JSD, Wasserstein, KS, emotion, vocabulary), interaction-network metrics, argument effectiveness |
| Cost of a typical study | Pay per LLM call for every agent action + Zep | KES 0 on a laptop; cents on hosted open models |

## Contributions that can be claimed and tested

1. **A closed loop between the web and the simulation.** Real-world evidence shapes the simulation at four points: the argument bank (what agents say), starting opinions (how affected groups feel), calibration (matching the observed distribution of online stance) and live web refresh (real headlines injected mid-run). Simulated discourse is then validated against real posts. Each link can be switched off for ablation.
2. **Decoupling behaviour from language.** An interpretable, literature-grounded opinion-dynamics model (social judgement theory with backfire, group polarisation, Friedkin-Johnsen anchoring, misinformation and correction) determines who says what; a language model only verbalises. This makes LLM-agent simulation affordable on consumer hardware, reproducible, and amenable to Monte Carlo analysis, while keeping realistic text where it matters.
3. **Contextualisation for African publics.** Census-based populations, locally meaningful archetypes (boda boda riders, chama members, M-Pesa agents), WhatsApp-style group dynamics, country platform mixes and institutional trust, and a sentiment lexicon covering Swahili, Sheng, Nigerian Pidgin and South African expressions. Ethnicity is deliberately excluded.
4. **A free-first data strategy** for platforms without free APIs (search-indexed public posts, researcher programmes, CSV import), with low-cost fallbacks.
5. **Ethics by design**: request screening, no AI-written disinformation, pseudonymisation, no publishing capability, and honest uncertainty reporting.

## Suggested research questions

* **RQ1 (fidelity):** How closely do simulated opinion distributions and discourse match observed online reactions to past African policy events?
* **RQ2 (grounding):** How much does each grounding mechanism (evidence-derived priors, calibration, argument bank, live web refresh) improve fidelity compared with an ungrounded simulation?
* **RQ3 (language models):** Does LLM-written text change aggregate dynamics or only realism? How do small local models compare with larger hosted ones on realism and cost?
* **RQ4 (usefulness):** Do policy communicators find the scenario comparisons and recommendations useful and credible?

## Evaluation plan

### Retrospective case studies

Choose events with a clear announcement date and plenty of public online discussion, for example:

| Country | Event | Approximate period |
|---|---|---|
| Kenya | Finance Bill 2024 (VAT on bread, mobile money excise, #RejectFinanceBill2024) | May-June 2024 |
| Kenya | Affordable Housing Levy | 2023-2024 |
| Kenya | Social Health Insurance Fund (SHIF) contributions | 2024 |
| Nigeria | Removal of the petrol subsidy | May-June 2023 |
| Ghana | Electronic Transactions Levy (E-Levy) | late 2021-2022 |
| Uganda | Over-the-top (social media) tax | 2018 |

Protocol for each case:

1. **Freeze the evidence** to before the announcement (or to the first 24-48 hours), using the research step's time window or uploaded archives. This prevents leakage of the outcome into the inputs.
2. **Build** the brief, knowledge base and population as a user would (record any manual edits).
3. **Simulate** the first one to two weeks with the actual sequence of major events as scheduled interventions (announcement, official statements, concessions, protests reported as breaking news). Run an ensemble (for example 30 seeds).
4. **Collect ground truth** for the same period: posts from X (via an archive or paid API), YouTube comments, Reddit and news, labelled for stance (by the same stance labeller and, for a subsample, by human annotators to measure labeller accuracy).
5. **Compare** with the validation metrics: Jensen-Shannon divergence and Wasserstein distance of stance and sentiment distributions per day, the direction and timing of shifts, which groups were most opposed (rank correlation against surveys or media analysis where available), dominant arguments (overlap with arguments in real posts) and emotions.
6. **Baselines:** (a) no change from the initial distribution, (b) an ungrounded population (uniform priors, no calibration), (c) a pure-LLM baseline where a model is asked directly to predict reactions.

### Ablations

Switch off one component at a time and repeat the ensemble: evidence-derived priors, calibration, group polarisation (`intensify=0`), backfire (`backfire=0`), echo chambers (`echo_chamber=0`), group chats, coordinated accounts, live web refresh, and LLM text (template mode). Report the change in fit and in substantive conclusions.

### Sensitivity analysis

Use parameter sweeps for every behavioural parameter and report which conclusions are robust. A finding such as "conceding on bread reduces opposition more than an explanatory statement" should hold across the plausible parameter range.

### Realism of text

A discrimination test: show annotators (ideally from the country) mixed real and simulated posts and ask which are real. Report accuracy (50% means indistinguishable), separately for template, small-local-model and large-model text. Also rate persona consistency across rounds.

### Usefulness

Structured interviews or a survey with communication officers, policy analysts or civil-society practitioners who use Baraza on a current issue: usefulness, credibility, clarity, and whether it changed their plans.

### Reproducibility

Record the Baraza version (Git commit), model names and versions, the seed, the project export (`/p/<id>/export.json`) and run exports (CSV). Ensembles and template-mode runs are bit-for-bit reproducible for a fixed seed and population; LLM-written text varies with model version.

## Running experiments at scale

* `python manage.py rehearse "<prompt>" --country KE --depth standard --evidence notes.md --no-research` runs a whole rehearsal from the command line.
* The engine in `apps/swarm/engine` has no Django dependency; import it in a notebook to run thousands of simulations (see `tests/test_engine.py` for a minimal example).
* All results are in the database (SQLite file `data/baraza.sqlite3`) and can be analysed directly with pandas.

## Threats to validity

* **Ground truth is itself biased**: online posts over-represent young, urban and connected people, and platform access limits which posts are visible.
* **Stance labelling error** propagates into calibration and validation; measure it against human labels.
* **Model knowledge leakage**: a model trained after an event may "know" the outcome. Prefer models with training cut-offs before the event for retrospective studies, or rely on template mode for the dynamics.
* **Parameter tuning to fit one case** risks overfitting; hold out cases.
* **Offline events** (protests, police action, court rulings) affect online opinion; include them as interventions or acknowledge their absence.

## Citing

If you use Baraza in academic work, cite the repository (with the commit you used) and the works it builds on: MiroFish, OASIS (Yang et al., 2024), GraphRAG (Edge et al., 2024) and the opinion-dynamics literature listed in [methodology.md](methodology.md#references).
