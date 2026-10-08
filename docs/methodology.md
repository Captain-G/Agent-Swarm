# Methodology

This document describes how Baraza turns a prompt into a simulated public debate, what each number in the interface means, and how far the results can be trusted. It is written for analysts, reviewers and researchers. Code references point to the modules that implement each part.

## Contents

1. [Design principles](#design-principles)
2. [The brief](#1-the-brief)
3. [Research and evidence](#2-research-and-evidence)
4. [Knowledge graph and dossier (GraphRAG)](#3-knowledge-graph-and-dossier-graphrag)
5. [Synthetic population](#4-synthetic-population)
6. [The simulated world](#5-the-simulated-world)
7. [Opinion dynamics](#6-opinion-dynamics)
8. [Interventions](#7-interventions)
9. [Where language models write text](#8-where-language-models-write-text)
10. [Metrics](#9-metrics)
11. [Uncertainty: ensembles and sweeps](#10-uncertainty-ensembles-and-sweeps)
12. [Validation](#11-validation)
13. [How far to trust the results](#how-far-to-trust-the-results)
14. [References](#references)

## Design principles

* **Separate the maths from the words.** Who is active, what they read, how their opinion changes and whether they post is decided by an explicit, seeded agent-based model (`apps/swarm/engine`). A language model only turns already-decided intentions ("this agent rebuts that post with argument #3, angry, stance -0.7") into text. This makes runs reproducible, cheap to repeat hundreds of times, comparable with and without AI text, and auditable.
* **Ground every agent in evidence.** Starting opinions come from how the research says each group is affected, optionally calibrated to the stance observed in real posts; arguments come from an argument bank extracted from real sources.
* **Every AI step has a transparent fallback.** With no model at all (`LLM_PROVIDER=none`) Baraza still produces a heuristic brief, a keyword-based knowledge graph, a population, a full simulation written from templates and a template report.
* **Prefer relative conclusions.** The tool is designed for comparing scenarios ("what if we exempt bread?") rather than for point predictions.

## 1. The brief

`apps/projects/brief.py`. The user's prompt is sent to the "smart" model tier with a structured-output schema. The model returns a title, a refined research question, a neutral summary, concrete policy details, the proposer, its status, stakeholders with likely positions (-1..+1) and influence (0..1), affected groups with impact **direction** (-1 harmed .. +1 benefits) and **intensity** (0..1), key issues, search queries (including 2-4 Swahili queries for East African countries), social keywords, support and opposition hashtags, testable hypotheses, the time horizon, assumptions and risks to watch.

Output is normalised and clamped; any missing part is filled by heuristics (`heuristic_brief`): a title from the main verb phrase, the proposer from keywords, a burden/benefit lexicon for the direction of impact, archetype keyword matching for affected groups, and query templates.

A light **safety screen** (`screen_request`) blocks requests to incite violence or hatred or to target private individuals, and adds a caution note for manipulative campaigns or ethnic framing (the population does not model ethnicity).

## 2. Research and evidence

`apps/research`. Connectors are run in parallel with per-host rate limits, an honest user agent and robots.txt checks for page fetching (`apps/research/http.py`). Each connector receives a query type: web/news queries, short social keywords, or reference topics. Results are de-duplicated by normalised URL, scored for relevance with BM25 against the brief, and stored as `Source` rows.

* **Full text.** The most relevant web/news hits are fetched and cleaned with trafilatura, then a BeautifulSoup paragraph heuristic, then (optionally) the Jina Reader service for JavaScript-heavy pages.
* **Sentiment.** Every item gets a VADER compound score extended with a lexicon of Swahili, Sheng, Nigerian Pidgin and South African expressions (`apps/core/text/sentiment.py`), e.g. *wizi* (theft), *tumechoka* (we are tired), *wahala*, *eish*.
* **Stance.** Sentiment is not stance ("great, another tax" is sarcastic). Public posts are therefore labelled by the fast model in batches for their stance toward the policy (-1..+1) with a confidence score. The distribution of observed stance is later used to calibrate the population.
* **Anonymisation.** Authors of social posts are replaced by default with a pseudonym derived from a keyed one-way hash (HMAC-SHA256 with the installation's secret key), e.g. `user_3fa2c1d07b`. The same author gets the same pseudonym within one installation, so repeated posters can still be counted.

Manual evidence (pasted text, URLs, PDF, Word, HTML, text and CSV exports of posts) enters the same pipeline.

## 3. Knowledge graph and dossier (GraphRAG)

`apps/knowledge`. Evidence is split into overlapping chunks (about 1,600 characters with 200 characters of overlap). The most informative chunks (by relevance and diversity across sources, up to a limit set by the research depth) are sent to the model, which extracts **entities** (people, organisations, government bodies, places, laws, groups, events), **relations** between them, **arguments** for and against with strength and emotion, **facts** and **rumours**. Without a model, a heuristic extractor (capitalised-phrase and acronym detection, cue-phrase argument mining) is used instead. All chunks remain available for retrieval.

* **Entity resolution** (`EntityResolver`) merges acronyms and long forms ("KRA" and "Kenya Revenue Authority"), case and punctuation variants, and near-duplicates (string similarity of at least 0.9 within the same type).
* **Graph analysis.** Relations and within-chunk co-occurrence form a weighted graph. Salience is PageRank (alpha 0.85, power iteration implemented in NumPy); communities come from Louvain modularity optimisation and are summarised by the model.
* **Arguments** are consolidated (near-duplicates merged, frequency counted), polished into short, quotable lines, and topped up if one side has too few (generated by the model, marked as such).
* **Dossier.** A neutral overview with numbered citations to sources, a stakeholder map, and an **impact matrix** (group, impact, direction, intensity, and the population archetypes that represent the group).
* **Retrieval.** Questions ("What does the Treasury say about bread?") are answered with hybrid retrieval: BM25 plus embeddings (if an embedding model is available) fused with reciprocal rank fusion, then expanded with graph neighbours of the entities mentioned.

## 4. Synthetic population

`apps/swarm/population.py`, `apps/core/geo`.

**Who.** Each citizen is drawn from one of 25 archetypes (students, Gen Z activists, job-seeking graduates, motorbike taxi riders, market vendors, smallholder farmers, small business owners, minibus operators, teachers, health workers, civil servants, corporate and tech professionals, content creators, gig workers, landlords, pensioners, religious leaders, journalists, policy analysts, the diaspora, casual workers, parents and homemakers, civil society workers and lawyers). Archetype weights reflect how present each group is in online debate, not just in the population. Ages follow the country's adult age structure within the archetype's range; region is drawn in proportion to population (all 47 Kenyan counties from the 2019 census; regional data for the other countries), with an optional boost for focus regions and a rural/urban split by archetype. Names are synthetic combinations of common first names and surnames. **Ethnicity is not modelled**, so that results cannot be read as ethnic stereotypes.

**Personality.** Big Five traits (openness O, conscientiousness C, extraversion E, agreeableness A, neuroticism N) are drawn from N(0.5 + archetype shift, 0.15), clipped to [0.02, 0.98]. They map to model parameters:

| Parameter | Formula | Meaning |
|---|---|---|
| Latitude of acceptance ε | 0.2 + 0.4·O | Views this close are assimilated |
| Latitude of rejection ρ | min(1.9, ε + 0.3 + 0.5·A) | Views this far away provoke backfire |
| Susceptibility μ | (0.04 + 0.16·(0.5·O + 0.3·A + 0.2·(1-C))) × age factor | How much one post can move the agent |
| Stubbornness γ | 0.02 + 0.1·C (×1.2 over 45) + 0.04 if partisan | Pull back toward the initial view |
| Activity | 0.12 × archetype activity × (0.5 + E), lower over 35 | Chance of opening an app about the topic in a 4-hour peak slot |
| Credibility | archetype credibility ± noise | How much others trust the agent |

**Platforms.** Each agent has a preference over microblog (X-style), forum (Facebook/Reddit-style) and group chats (WhatsApp-style), from the country's platform mix, adjusted for age and archetype.

**Starting opinion.** For each archetype, a stance prior and a salience (how much they care) come from (a) the model's reading of the brief and impact matrix ("how would small traders in Kenya initially feel about X?") or (b) the impact matrix alone: an intensity-weighted mean of the direction of the groups the archetype represents, with low-income archetypes weighted more heavily for burdens. A political-leaning effect is added (+0.28 for pro-government, -0.28 for opposition supporters when the government proposes the measure), plus N(0, 0.22) noise.

**Calibration.** If at least 15 real posts were stance-labelled, modelled stances are quantile-mapped toward the observed distribution with weight w (default 0.5): `x_i ← (1-w)·x_i + w·Q_obs(rank_i)`. The interface reports the mean before and after.

**Institutions, media, influencers and bots.** Organisations and government bodies from the knowledge graph (not individual people) become institutional accounts with their extracted positions, high credibility and low susceptibility. Media accounts are neutral and credible. Influencers are citizens with high activity and reach. Optional coordinated accounts ("bots") hold a fixed target stance, never change their minds, and amplify aligned content; their posts are always written from templates.

**Network.** A directed follow network is built by homophilous preferential attachment: the chance that *i* follows *j* grows with *j*'s popularity (seed followers^0.8) and is multiplied by 2 for the same region, 1.6 for the same archetype and exp(-1.4·|x_i - x_j|) for opinion similarity. Extraverts follow more accounts; coordinated accounts follow each other. Group chats are formed from shared geography and occupation: family groups (mixed ages within a macro-region), neighbourhood groups, chamas/savings groups, faith fellowships and work networks.

## 5. The simulated world

`apps/swarm/engine/world.py`. Time advances in rounds (default 4 simulated hours). In each round:

1. **Interventions** scheduled for this round are applied (section 7).
2. **Activation.** Each agent is active with probability `1 - exp(-rate)`, where `rate = 3 · activity · activity_scale · step_factor · hour_factor · (0.6 + 0.8·arousal) · (0.5 + salience_i) · (0.4 + 0.6·min(1.4, topic heat))`. The hour factor follows an East African daily rhythm (quiet at night, peaks at breakfast, lunch and 7-9 pm) shifted to the country's time zone. Topic heat rises with events and decays each round.
3. **Reading.** Active agents choose a platform and read a feed:
   * *Microblog*: up to `feed_size` posts from the last `recency_window` rounds, sampled without replacement (Efraimidis-Spirakis) with weight `recency · follow(×3) · reposted-by-followee(×1.6) · (1 + log(1 + engagement)) · popularity · boost · exp(-5·echo·|s_post - x_i|)`.
   * *Forum*: a "hot" thread (net votes, replies, recency, homophily with factor 3·echo) and its top comments.
   * *Group chat*: the latest messages in one of their groups.
4. **Updating** after each post (section 6), then **reacting**: like (logistic in agreement, extraversion, social proof, following and arousal), downvote (forum), repost (microblog), forward (chats; more likely for arousing content and misinformation, less likely for conscientious agents), follow, unfollow and mute.
5. **Speaking.** Agents may reply (more likely when aroused, extraverted, addressed directly, or when the post is beyond their latitude of rejection and they are disagreeable) or write a new post (more likely when extraverted, aroused, extreme and involved). Each request carries a stance, an argument from the bank (chosen by side and not recently used), an emotion and an intent (support, rebut, agree, inform, mobilise).
6. **Writing.** Requests are ranked by expected reach; the top `llm_posts_per_round` go to the language model, the rest to the template writer.
7. **End of round.** Anchoring, noise, arousal decay and emotion labels are applied; metrics are recorded.

## 6. Opinion dynamics

`apps/swarm/engine/dynamics.py`. Opinions are continuous, x ∈ [-1, 1]. When agent *i* reads post *p* with stance *s* written by *j*, let `d = s - x_i`. The influence weight is

```
w = min(0.5, κ · μ_i · (0.4 + cred_j) · tie · (1 + 0.12·ln(1 + likes + reposts)) · (0.5 + strength_p) · m · (0.5 + 0.5·salience_i))
```

where κ is `influence_scale` (default 1.2), tie = 1.5 if *i* follows *j* (×1.4 in group chats, where ties are close), and m = 1 - 0.7·C_i for misinformation (conscientious readers resist rumours) and 1 otherwise. The update follows social judgement theory (Sherif and Hovland; formalised by Jager and Amblard, 2005):

* **Assimilation** if |d| ≤ ε_i: `Δ = w·d`. In addition, **group polarisation** (persuasive-arguments theory): if the post is on the same side and at least as extreme, `Δ += η · w · arousal_p · arousal_i · sign(x_i) · (1 - |x_i|)` with η = `intensify` (default 0.6).
* **Backfire** (boomerang) if |d| ≥ ρ_i: `Δ = -β · w · sign(d) · (|d| - ρ_i) · (0.5 + N_i)` with β = `backfire` (default 0.3).
* **Non-commitment** in between: `Δ = 0.25 · w · d`.

**Misinformation and correction.** Shifts caused by rumours are tracked per agent. Reading a fact-check reverses part of that shift: `Δ = -0.6 · shift · min(1, 0.5 + C_i) · (0.5 + cred_j)`, after which 30% of the shift remains (the continued-influence effect: corrections rarely undo misinformation completely).

**Anchoring.** At the end of each round opinions are pulled back toward each agent's initial view (Friedkin and Johnsen, 1990): `x_i ← x_i + p·(x_i(0) - x_i)` with `p = 1 - (1 - min(0.95, a·γ_i))^(step_hours/4)`, so results do not depend on the round length. Default a = 0.35. Public agents also receive N(0, 0.01) noise.

**Emotion.** Arousal rises by contagion when reading arousing posts (more for neurotic agents, more for disagreement) and decays toward a personal baseline each round. Discrete emotions (anger, fear, disgust, sadness, joy, hope, trust, neutral) are derived from stance, arousal and personality.

**Policy changes** move the opinions of affected agents directly by `δ · (1 - min(0.8, 3γ)) · (0.5 + 0.5·salience)` and shift their anchor by half that amount, because people re-evaluate a changed policy.

## 7. Interventions

Interventions can be scheduled before a run or injected live ("God mode"):

| Type | Effect |
|---|---|
| Breaking news | A news post from a media account on the microblog and forum, seeded into a few group chats; raises topic heat |
| Official statement / message test | A post from the proposer (or a named actor) with your text and stance, boosted in feeds |
| Policy change | Direct opinion shift for targeted segments (section 6) plus an announcement |
| Influencer campaign | Influencers (or the most connected citizens) post your message for several rounds |
| Rumour / misinformation | An arousing false claim seeded in chats and feeds; always written from your text or templates, never by the model |
| Fact-check | A credible correction that partially reverses rumour-induced shifts |
| Live web refresh | Fetches the latest real headlines about the topic (Google News, news search) and injects them as breaking news: the closed loop between the simulation and the real world |

Each intervention records its measured effect (posts, agents affected, mean shift).

## 8. Where language models write text

* **Smart tier:** brief, extraction, dossier, stance priors, personas, report, interviews, polls, Observer questions.
* **Fast tier:** stance labelling of real posts and agent posts.
* Each agent post prompt re-injects the full persona, current opinion, emotion, the argument to make and the post being answered, to prevent identity drift across rounds. Output is cleaned (preambles such as "As an AI..." or "Here is the post:" are stripped and length limits enforced); if the model fails six times in a row, the run continues with templates instead of stalling. Rumours and posts by coordinated accounts are never written by the model.
* The **template writer** composes posts from the argument bank, hashtags, the brief's vocabulary and short local expressions, according to the chosen language style (plain English, English with local expressions, or mixed English/Swahili/Sheng).

## 9. Metrics

Recorded for the public (citizens and influencers) after every round:

| Metric | Definition |
|---|---|
| Mean, median, SD | Of stance x |
| Support / oppose / neutral | Share with x > 0.15, x < -0.15, otherwise |
| Net support | Support share minus oppose share |
| Extreme share | Share with \|x\| > 0.6 |
| Esteban-Ray polarisation | ER index with α = 1.6 on 10 bins, normalised by its maximum (two equal groups at the extremes) |
| Bimodality coefficient | (skew² + 1) / (excess kurtosis + 3(n-1)²/((n-2)(n-3))); above 0.555 suggests two camps |
| Divergence | Mean stance of supporters minus mean stance of opponents |
| Cross-cutting exposure | Share of posts read whose stance was on the other side of the reader's |
| Mobilisation index | Share of the public strongly opposed (x < -0.5) and highly aroused (arousal > 0.55) |
| Mobilising posts | Posts calling for protest, boycott or action |
| Emotions, hashtags, activity | Counts per round |
| Segment means | By archetype, region, age band, gender, political leaning and urban/rural |

After a run, the interaction network (replies, reposts, likes and follows) is analysed for PageRank influence, Louvain **modularity**, **stance assortativity** and the Krackhardt **E-I index** (external minus internal ties over all ties; negative values mean ties stay within camps). **Argument effectiveness** is the number of readers persuaded (moved toward the post by more than 0.004) and backfired (moved away by more than 0.002) per 100 exposures, by argument.

## 10. Uncertainty: ensembles and sweeps

`apps/swarm/ensemble.py`. A single run is one draw from a stochastic process. **Monte Carlo ensembles** re-run a scenario (and its baseline, if any) with different random seeds (activity, feeds, reactions; the population is fixed), report the 10th, 50th and 90th percentile bands of each metric over time, final values with 95% confidence intervals (Student's t), the share of runs in which opinion moved toward support, and the effect of the scenario relative to its baseline (difference of the final means). **Parameter sweeps** vary one model parameter (echo-chamber strength, influence, backfire, group polarisation, anchoring, activity, or the share of coordinated accounts) over a grid and report final outcomes with intervals, which shows how sensitive a conclusion is to modelling assumptions. Ensembles use template text only, so they cost nothing.

## 11. Validation

`apps/insights/validation.py`. Simulated discourse is compared with real posts, either those collected in research or an uploaded CSV (for example a historical dataset from a past policy debate):

* **Sentiment distribution:** Jensen-Shannon divergence (base 2, 0 = identical, 1 = disjoint), Wasserstein distance and the Kolmogorov-Smirnov statistic.
* **Stance distribution:** the same, when real stance labels exist.
* **Emotion distribution:** Jensen-Shannon divergence over eight emotion categories.
* **Vocabulary overlap:** Jaccard similarity of the top 40 terms.
* **Fit score:** 1 minus the mean of the available divergences.

A suggested retrospective protocol for research use is in [research-notes.md](research-notes.md#evaluation-plan).

## How far to trust the results

* **Use directions and comparisons, not point estimates.** "Exempting bread reduces opposition more than an explanatory statement" is far more robust than "62% will oppose".
* **Check robustness.** Run an ensemble; if the 10-90% bands of two scenarios overlap heavily, the difference is not meaningful. Run a sweep over echo-chamber strength and backfire; a conclusion that flips is fragile.
* **Ground it.** More real posts give better calibration and validation. Read the dossier and correct the brief and argument bank before simulating.
* **Known limitations.** Synthetic populations simplify real people. Online discourse over-represents young, urban and connected people, and Baraza's archetype weights reflect online presence. Data coverage differs by platform and country; X, Facebook and LinkedIn are only partially visible through free channels. Small local models write less nuanced text and can misjudge local context. Opinion-dynamics parameters are literature-informed estimates, not fitted to every context. Offline events (protests, police action, court rulings) only enter through interventions or live web refresh.
* **Complement it.** Before high-stakes decisions, test key findings with real people: a survey, focus groups or structured public participation.

## References

* Deffuant, G., Neau, D., Amblard, F., and Weisbuch, G. (2000). Mixing beliefs among interacting agents. *Advances in Complex Systems*, 3, 87-98.
* Edge, D., et al. (2024). From local to global: A graph RAG approach to query-focused summarization. Microsoft Research, arXiv:2404.16130.
* Efraimidis, P. S., and Spirakis, P. G. (2006). Weighted random sampling with a reservoir. *Information Processing Letters*, 97(5), 181-185.
* Esteban, J.-M., and Ray, D. (1994). On the measurement of polarization. *Econometrica*, 62(4), 819-851.
* Friedkin, N. E., and Johnsen, E. C. (1990). Social influence and opinions. *Journal of Mathematical Sociology*, 15(3-4), 193-206.
* Hegselmann, R., and Krause, U. (2002). Opinion dynamics and bounded confidence: models, analysis and simulation. *JASSS*, 5(3).
* Hutto, C. J., and Gilbert, E. (2014). VADER: A parsimonious rule-based model for sentiment analysis of social media text. *ICWSM*.
* Isenberg, D. J. (1986). Group polarization: A critical review and meta-analysis. *Journal of Personality and Social Psychology*, 50(6), 1141-1151.
* Jager, W., and Amblard, F. (2005). Uniformity, bipolarization and pluriformity captured as generic stylized behavior with an agent-based simulation model of attitude change. *Computational and Mathematical Organization Theory*, 10, 295-303.
* Krackhardt, D., and Stern, R. N. (1988). Informal networks and organizational crises: An experimental simulation. *Social Psychology Quarterly*, 51(2), 123-140.
* Lewandowsky, S., Ecker, U. K. H., Seifert, C. M., Schwarz, N., and Cook, J. (2012). Misinformation and its correction: Continued influence and successful debiasing. *Psychological Science in the Public Interest*, 13(3), 106-131.
* Park, J. S., et al. (2023). Generative agents: Interactive simulacra of human behavior. *UIST*.
* Pfister, R., Schwarz, K. A., Janczyk, M., Dale, R., and Freeman, J. B. (2013). Good things peak in pairs: a note on the bimodality coefficient. *Frontiers in Psychology*, 4, 700.
* Sherif, M., and Hovland, C. I. (1961). *Social Judgment: Assimilation and Contrast Effects in Communication and Attitude Change*. Yale University Press.
* Yang, Z., et al. (2024). OASIS: Open agent social interaction simulations with one million agents. arXiv:2411.11581.
