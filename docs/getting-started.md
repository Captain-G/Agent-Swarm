# Getting started

This guide takes you from nothing to your first rehearsal. It assumes no programming experience beyond copying commands into a terminal.

## What you need

* **A computer** with Windows 10/11, macOS or Linux. 8 GB RAM is enough to try Baraza; **16 GB RAM** is recommended for the `llama3` model. A graphics card (NVIDIA with 8 GB VRAM, or an Apple Silicon Mac) makes it several times faster but is not required.
* **Python 3.10 or newer** ([python.org/downloads](https://www.python.org/downloads/); on Windows tick "Add python.exe to PATH" during installation).
* **Ollama** with `llama3` ([ollama.com/download](https://ollama.com/download)). If `ollama run llama3` already works on your computer, you are ready.
* About 6 GB of free disk space (most of it for the model).
* An internet connection for research (the simulation itself runs offline).

## 1. Install Baraza

Open a terminal (Windows: *PowerShell*; macOS: *Terminal*) and run:

```bash
git clone https://github.com/captain-g/agent-swarm.git baraza
cd baraza
python -m venv venv
```

Activate the virtual environment:

* Windows PowerShell: `venv\Scripts\Activate.ps1` (if blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once)
* macOS/Linux: `source venv/bin/activate`

Then install and create the database:

```bash
pip install -r requirements.txt
python manage.py migrate
```

No `git`? Download the repository as a ZIP from GitHub, unzip it and open a terminal in that folder.

## 2. Prepare the language model

```bash
ollama pull llama3              # the default model (about 4.7 GB)
ollama pull nomic-embed-text    # optional, small: better search over the evidence
```

Check that Baraza can reach it:

```bash
python manage.py check_llm
```

You should see `Health: Ollama ...` and `JSON generation works`. If not, see [Troubleshooting](#troubleshooting).

## 3. Start Baraza

```bash
python manage.py runserver
```

Open **http://127.0.0.1:8000** in your browser. Keep the terminal open while you use Baraza; stop it with `Ctrl+C`. Next time you only need to activate the virtual environment and run `python manage.py runserver`.

## 4. Your first rehearsal

1. On the home page, describe a policy, action or reform in a sentence or two, for example: *"The Ministry of Transport requires all matatus to accept only cashless fare payments from January. How will commuters and operators react?"* Or click one of the ready-made scenarios.
2. Choose the **country**, the **depth** (*Quick* simulates about 80 people: a few minutes with a graphics card, up to an hour on a processor alone) and the **agent language** (plain English, English with local expressions, or mixed English and Swahili/Sheng). *More options* lets you focus on particular regions.
3. Press **Run full rehearsal** (Autopilot: runs everything) or **Step by step** (review the brief, research and population before simulating). For your first try, use Run full rehearsal with Quick depth.
4. Watch the progress. When it finishes, open **Insights** for charts and the **Observer report** for the written analysis, and open **Interviews & polls** to interview a simulated person or poll a virtual focus group.
5. Rehearse a response: open the run and use **Change the story** to inject an official statement or a policy concession, or click **Run again with changes**. Compare the two runs under **Insights → Compare**.

## Try it without AI

To see the whole interface in a minute without installing a model:

```bash
# macOS/Linux
LLM_PROVIDER=fake python manage.py baraza_demo
LLM_PROVIDER=fake python manage.py runserver

# Windows PowerShell
$env:LLM_PROVIDER="fake"; python manage.py baraza_demo
python manage.py runserver
```

The demo uses an offline stand-in model whose writing is deliberately simple. Switch to Ollama in **Settings** (or remove the variable) for real results.

## Optional: a `.env` file

Copy `.env.example` to `.env` to change defaults (model, keys for extra data sources, login requirement). Most options can also be changed in the app under **Settings**.

## Speed tips for laptops

* Use the **Quick** depth and the *Quick look* simulation preset first.
* Set a smaller model for agent posts: `ollama pull llama3.2:3b`, then in **Settings** set *Fast model* to `llama3.2:3b`. Keep `llama3` for the brief and the report.
* Keep *AI-written posts per round* low (3-6). Everything else is written instantly from templates.
* The *Instant* preset runs a whole simulation without any AI text in seconds; useful for exploring scenarios before an AI-written run.
* Free hosted models (Groq, OpenRouter) can do the heavy analysis while your laptop handles the agents: see [cost-guide.md](cost-guide.md).

## Troubleshooting

| Problem | Fix |
|---|---|
| `Cannot reach Ollama at http://localhost:11434` | Start Ollama (open the Ollama app, or run `ollama serve`). |
| `Model 'llama3' is not installed` | `ollama pull llama3`, or set the model name you have in Settings (`ollama list` shows them). |
| Everything is very slow | See the speed tips above; close other heavy programs; set `LLM_CONCURRENCY=1`. |
| `Ollama timed out` | Raise `LLM_TIMEOUT` (seconds) in Settings, or use a smaller model. |
| Research finds few sources | Some free search engines rate-limit; wait a few minutes, add a YouTube key and Telegram channels, run SearXNG, or paste/upload your own evidence. |
| A task shows "interrupted" | The server was stopped while it ran. Simulations can be resumed from the run page; other steps can be restarted. |
| `python` not found on Windows | Reinstall Python with "Add python.exe to PATH", or use `py` instead of `python`. |
| Port 8000 in use | `python manage.py runserver 8080` and open http://127.0.0.1:8080 |

Logs are written to `data/logs/baraza.log`.
