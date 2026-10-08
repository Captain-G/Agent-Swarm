# Cost guide

Baraza is designed so that the default costs nothing: a local model through Ollama, a local SQLite database and free data sources. This guide explains where time and money go and how to trade them off.

## Where the work goes

Approximate language-model calls per rehearsal (Autopilot, defaults):

| Step | Quick | Standard | Deep |
|---|---|---|---|
| Brief | 1 | 1 | 1 |
| Stance labelling of real posts (batches of 8) | 4 | 10 | 20 |
| Knowledge extraction (chunks) | 10 | 22 | 40 |
| Arguments, dossier, community summaries | 4 | 9 | 11 |
| Population (segment priors, personas) | 6 | 6 | 6 |
| Simulation (AI-written posts) | 36 (12 rounds × 3) | 144 (24 × 6) | 420 (42 × 10) |
| Observer report | 8 | 8 | 8 |
| **Total** | **about 70** | **about 200** | **about 500** |

Roughly, a standard rehearsal processes about 180,000 input tokens and generates about 50,000 output tokens. Interviews, polls and Observer questions add a call each (polls one per respondent). Monte Carlo ensembles and parameter sweeps use **no** model calls.

## Option 1: everything local (KES 0)

Ollama with `llama3` (8 billion parameters, about 4.7 GB). Speed depends mostly on how fast the model generates text:

| Hardware | Typical generation speed | Quick | Standard |
|---|---|---|---|
| Laptop, processor only (16 GB RAM) | 5-10 tokens/s | 30-60 min | 1.5-3 h |
| Apple Silicon Mac (M1-M4, 16 GB) | 20-35 tokens/s | about 10 min | 25-45 min |
| NVIDIA GPU with 8+ GB (e.g. RTX 3060) | 50-90 tokens/s | 3-6 min | 10-20 min |

Ways to go faster for free:

* **Smaller model for agent posts.** `ollama pull llama3.2:3b` and set *Fast model* to `llama3.2:3b` in Settings. Posts are short; a 3B model writes them two to three times faster. Keep `llama3` (or `llama3.1:8b`) for the brief, extraction and report.
* **Fewer AI-written posts.** Lower *AI-written posts per round* (Simulate → Advanced). Every other post is written instantly from the argument bank.
* **Instant runs.** The *Instant* preset uses no AI text at all; use it to explore scenarios, then run AI text only on the ones that matter.
* **Caching.** Repeating a step with the same inputs is instant (identical requests are cached).
* **Context window.** `OLLAMA_NUM_CTX=8192` is enough; larger windows use more memory and are slower.

Other good free local models: `llama3.1:8b`, `qwen2.5:7b`, `gemma2:9b`, `mistral:7b`. Any model available in Ollama can be set as the main or fast model.

## Option 2: hybrid (KES 0 within free limits, best quality per shilling)

Keep the many small agent calls on your computer and send only the analysis-heavy steps (brief, extraction, dossier, report: about 50 calls) to a larger hosted model:

```
LLM_PROVIDER=ollama
HYBRID_SMART_TIER=True
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=gsk_...
OPENAI_MODEL=llama-3.3-70b-versatile
```

Groq and OpenRouter (models tagged `:free`) offer free tiers with rate limits that comfortably cover occasional rehearsals. Baraza retries politely when it hits a rate limit.

## Option 3: everything hosted

Set `LLM_PROVIDER=openai` with any OpenAI-compatible API. Indicative costs for a standard rehearsal (about 180k input and 50k output tokens), using list prices at the time of writing:

| Model class | Example | Indicative cost per standard rehearsal |
|---|---|---|
| Small open model | Llama 3.1 8B on Groq | about USD 0.01 |
| Large open model | Llama 3.3 70B on Groq | about USD 0.15 |
| Small proprietary model | GPT-4o-mini class | about USD 0.05 |

Set `LLM_COST_PER_1M_INPUT_USD` and `LLM_COST_PER_1M_OUTPUT_USD` to see estimated spending per project on the project overview and Settings pages.

## Data costs

Everything enabled by default is free. Optional paid sources and their typical use per research run:

| Source | Free allowance | Beyond that |
|---|---|---|
| YouTube Data API | 10,000 units/day (free) | Not needed |
| Tavily | About 1,000 searches/month | Pay as you go |
| Brave Search API | Free monthly credits | A few dollars per 1,000 queries |
| Serper | About 2,500 queries on sign-up | About USD 1 per 1,000 queries |
| Apify | Free monthly platform credit | Typically a few dollars per 1,000 posts, depending on the actor |
| twitterapi.io | - | About USD 0.15 per 1,000 tweets |
| X API | - | Paid plans / pay per use |

Baraza caps how many queries it sends to each paid source per research run (2-5).

## Hosting for a team

A small cloud server (2 vCPUs, 4 GB RAM) can host Baraza for a team if the language model is hosted (Option 3) or runs on a separate GPU machine. See [deployment.md](deployment.md).
