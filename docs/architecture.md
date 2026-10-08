# Architecture

Baraza is a single Django project. Everything runs in one process on a laptop (SQLite, background jobs in threads, a local Ollama model) and scales up by configuration (PostgreSQL, a separate worker process, a hosted model API).

```
                    +--------------------------------------------------------------+
  Browser  <------> |  Django views + templates (htmx, Alpine.js, Chart.js, D3)     |
  (htmx polling)    +--------------------------------------------------------------+
                         |                    |                      |
                         v                    v                      v
                 +---------------+    +---------------+      +----------------+
                 |  Job queue    |    |  SQLite /     |      |  LLM client    |
                 |  (Job model;  |<-->|  PostgreSQL   |<---->|  Ollama | API  |
                 |  threads or   |    |  (WAL mode)   |      |  cache, usage  |
                 |  run_worker)  |    +---------------+      +----------------+
                 +---------------+                                   ^
                         |                                           |
   +---------+-----------+-----------+-------------+-----------------+--+
   |         |                       |             |                    |
   v         v                       v             v                    v
 brief   research (connectors) -> knowledge -> population -> simulation -> insights
         web/news/social APIs      GraphRAG     personas      engine        report, polls,
         (rate-limited, robots)    dossier      network       (pure Python) validation
```

## Repository layout

```
baraza/                 Django project: settings, URLs, WSGI/ASGI
apps/
  core/                 Shared foundation
    llm/                Providers (Ollama, OpenAI-compatible, offline demo), client with
                        tiers, caching, usage accounting, JSON repair
    text/               Cleaning, chunking, keywords, sentiment (with African lexicon),
                        BM25 + vector retrieval, safe Markdown rendering
    geo/                Country profiles (demographics, platforms, trust, names, regions)
                        and the 25 population archetypes
    jobs.py             Background jobs: progress, logs, cancellation, heartbeats
    config.py           Settings: .env defaults + overrides saved from the Settings page
    graphs.py           PageRank (NumPy power iteration)
  projects/             Project model, the brief (prompt architect), scenarios, Autopilot
  research/             Connectors, polite HTTP fetcher, article/file extraction, pipeline
  knowledge/            Chunks, entities, relations, arguments, communities, dossier,
                        GraphRAG builder and retrieval
  swarm/                Agents, chat groups, runs, interventions, posts, actions, snapshots
    engine/             The simulation engine (no Django imports): state, dynamics,
                        feeds, content templates, metrics, world loop
    population.py       Synthetic population generator
    writer.py           Language-model post writer with budget and fallbacks
    runner.py           Loads a run into the engine, persists each round, resumes
    ensemble.py         Monte Carlo ensembles and parameter sweeps
  insights/             Analytics, Observer report, interviews, polls, validation, comparison
templates/              Server-rendered pages and htmx partials
static/                 CSS design system, app.js, fonts, icons, vendored libraries
tests/                  Test suite (offline; uses the demo model and mocked HTTP)
docs/                   Documentation
design-system/          UI design system generated with UI UX Pro Max
```

## Request and job flow

1. A view validates input and calls `apps.core.jobs.enqueue(func, kind=..., payload=...)`. A `Job` row is created; in thread mode a daemon thread starts after the transaction commits. At most `MAX_CONCURRENT_JOBS` run at once.
2. Job functions have the signature `func(ctx, **payload)`. They report progress with `ctx.progress(pct, stage, message)`, log with `ctx.log`/`ctx.warn`, check `ctx.check_cancel()` and may delegate a range of the progress bar to a sub-step with `ctx.sub(start, end)` (Autopilot chains six steps this way).
3. Pages poll `/jobs/<id>/` with htmx every 2 seconds. When a job finishes the partial sends an `HX-Trigger: jobFinished` header and the page reloads the relevant content. The live simulation page polls a small JSON endpoint for metrics and the feed.
4. A heartbeat thread updates running jobs every 20 seconds; jobs without a heartbeat for 3 minutes (for example after a restart) are marked *interrupted*, and simulation runs can be resumed from their last saved round.

With `JOB_RUNNER=worker`, jobs stay queued until `python manage.py run_worker` claims them, so restarting the web server never interrupts a job.

## The language-model layer

`apps/core/llm/client.py` exposes `get_llm(project).json(system=..., user=..., purpose=..., tier="smart"|"fast", schema=...)` and `.complete(...)`, `.embed(...)`.

* **Routing.** `LLM_PROVIDER` selects Ollama, an OpenAI-compatible API, the offline demo model or none. `HYBRID_SMART_TIER` sends only the smart tier to the API. `OLLAMA_FAST_MODEL`/`OPENAI_FAST_MODEL` optionally use a smaller model for the fast tier.
* **Ollama protocol.** `/api/chat` with `format` set to a JSON schema (structured outputs) and automatic fallback to `format: "json"` for older servers; `num_ctx`, `num_predict`, `seed` and `keep_alive` options; `/api/embed` with fallback to the legacy `/api/embeddings`; `/api/tags` and `/api/version` for health checks. Errors carry actionable hints (`ollama serve`, `ollama pull llama3`).
* **Robust JSON.** `extract_json` recovers JSON from prose, code fences, trailing commas, smart quotes, Python literals, unquoted keys and truncated output; one corrective retry is attempted before falling back to heuristics.
* **Caching.** Deterministic requests (temperature ≤ 0.5 or a fixed seed) are cached in the database, so re-running a step is instant and free.
* **Accounting.** Calls, tokens, time and estimated cost are aggregated per project, purpose and model and shown on the project overview and Settings page.
* **Concurrency.** A per-provider semaphore (`LLM_CONCURRENCY`) protects a local Ollama server from overload.

## Data model (main tables)

| App | Model | Purpose |
|---|---|---|
| core | `Job`, `SiteConfig`, `LLMCacheEntry`, `LLMUsage` | Jobs, settings overrides, model cache and usage |
| projects | `Project` | Prompt, country, depth, language style, brief (JSON) |
| research | `Source` | One piece of evidence: platform, kind, URL, text, sentiment, stance, relevance, metrics |
| knowledge | `Chunk`, `Entity`, `Relation`, `Argument`, `Community`, `Dossier` | The knowledge graph and dossier |
| swarm | `Agent`, `ChatGroup` | The population (per project) |
| swarm | `SimulationRun`, `Intervention`, `AgentState`, `Post`, `Action`, `RoundSnapshot`, `Experiment` | Runs, their events and results; ensembles/sweeps |
| insights | `Report`, `Interview`, `Poll`, `ValidationDataset`, `ValidationResult` | Outputs |

A project's population is shared by all its runs, so scenarios are compared on the same people. Each run stores per-agent state, every post and action, and a metrics snapshot per round, which makes runs fully inspectable and exportable (CSV).

## The simulation engine

`apps/swarm/engine` is plain Python with NumPy and no Django imports, so it can be unit-tested, profiled and reused (for example in a notebook). `runner.py` converts database rows to `SimAgent` objects, builds a `World`, steps it round by round, applies interventions from the database (including ones injected while it runs), and persists each round in bulk. `ensemble.py` runs the same engine many times in memory with template text and stores only aggregated results.

Performance: a population of 300 agents runs a 24-round simulation in a few seconds without AI text. With AI text the cost is dominated by the language model (about 6 calls per round by default).

## Front end

Server-rendered Django templates with htmx for partial updates and polling, Alpine.js for small interactions (tabs, the God-mode panel, the live run), Chart.js for charts and D3 for the force-directed knowledge and interaction graphs. All libraries, fonts and icons are vendored in `static/` and served by WhiteNoise, so Baraza works offline and needs no Node.js build. The design system (tokens in `static/css/baraza.css`) follows the UI UX Pro Max "accessible and ethical" recommendations: Atkinson Hyperlegible type, WCAG AA contrast, visible focus rings, 44-pixel touch targets, light and dark themes, reduced-motion support, and a colour-blind-safe teal/orange pair for support/opposition that is always paired with text labels. See [design-system/baraza](../design-system/baraza/MASTER.md).

## Extending Baraza

* **A new data source:** subclass `apps.research.connectors.base.Connector`, implement `search(query, ctx) -> list[RawItem]`, set `key`, `name`, `platform`, `cost`, `query_kind`, `requires`, `description` and `setup`, and add an instance to `CONNECTORS` in `apps/research/connectors/__init__.py`. Use `ctx.fetcher` for HTTP so rate limits and test mocks apply. Add a parsing test with a recorded payload in `tests/test_research.py`.
* **A new country:** add a `CountryProfile` to `apps/core/geo/countries.py` (regions with populations, age structure, platform mix, political leaning distribution, institutional trust, names, subreddits, Google News edition, GDELT country name) and its UTC offset to `TZ_OFFSETS` in `apps/swarm/engine/config.py`.
* **A new archetype:** add an `Archetype` to `apps/core/geo/archetypes.py` with its weight, concerns, keywords and trait/platform shifts.
* **A new intervention type:** add it to `Intervention.Kind` and handle it in `World.apply_intervention`.
* **A new model provider:** anything OpenAI-compatible works through `LLM_PROVIDER=openai`; otherwise subclass `BaseProvider` in `apps/core/llm/providers.py`.
