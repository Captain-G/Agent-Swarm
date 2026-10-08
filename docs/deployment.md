# Deployment

Most people run Baraza on their own computer (see [getting-started.md](getting-started.md)). This guide covers sharing it with a team.

## Option A: Docker Compose (simplest)

```bash
cp .env.example .env        # edit: DEBUG=False, ALLOWED_HOSTS, REQUIRE_LOGIN=True, model settings
docker compose up -d --build
docker compose exec baraza python manage.py createsuperuser
```

Open http://localhost:8000. Data (database, uploads, logs) lives in the `baraza-data` volume.

* **Language model on the host.** By default the container talks to Ollama on the host machine at `http://host.docker.internal:11434`. On Linux, make Ollama listen on all interfaces: `sudo systemctl edit ollama` and add `Environment="OLLAMA_HOST=0.0.0.0"`, then restart it.
* **Language model in Docker.** `docker compose --profile ollama up -d`, then `docker compose exec ollama ollama pull llama3`, and set `OLLAMA_BASE_URL=http://ollama:11434` in `.env`. For an NVIDIA GPU, add a `deploy.resources.reservations.devices` section to the `ollama` service as described in the Ollama Docker documentation.
* **Private search engine.** `docker compose --profile searxng up -d`, change `secret_key` in `deploy/searxng/settings.yml`, and set `SEARXNG_URL=http://searxng:8080`.

## Option B: a Linux server without Docker

```bash
sudo apt install python3 python3-venv git
git clone https://github.com/captain-g/agent-swarm.git /opt/baraza && cd /opt/baraza
python3 -m venv venv && . venv/bin/activate
pip install -r requirements-server.txt
cp .env.example .env    # set DEBUG=False, SECRET_KEY, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, REQUIRE_LOGIN=True
python manage.py migrate
python manage.py createsuperuser
gunicorn baraza.wsgi:application --bind 127.0.0.1:8000 --workers 1 --threads 8 --timeout 120
```

Run gunicorn as a systemd service and put a reverse proxy with HTTPS in front of it (Caddy is the easiest: `baraza.example.org { reverse_proxy 127.0.0.1:8000 }`). With HTTPS, set `SECURE_COOKIES=True` and add your address to `CSRF_TRUSTED_ORIGINS` (for example `https://baraza.example.org`).

Static files are served by WhiteNoise from the application itself; no separate static file server is needed.

## Background jobs

* **`JOB_RUNNER=thread` (default)** runs jobs in threads of the web process. Use a single gunicorn worker process with several threads (`--workers 1 --threads 8`) so that every request sees the same running jobs. Restarting the server interrupts running jobs; simulations can be resumed.
* **`JOB_RUNNER=worker`** queues jobs in the database; run `python manage.py run_worker` as a second service (or a second container with the same image and volume, command `python manage.py run_worker`). Web restarts no longer affect jobs, and you can run more web workers. `MAX_CONCURRENT_JOBS` limits parallel jobs per worker.

## Database

SQLite (in `data/baraza.sqlite3`, WAL mode) is fine for a small team. For more concurrent users set `DATABASE_URL=postgres://user:password@host:5432/baraza` (the `psycopg` driver is in `requirements-server.txt`) and run `python manage.py migrate`.

## Language model for a team

A single Ollama instance serves requests one or two at a time. For several simultaneous users either:

* use a hosted OpenAI-compatible API (Groq, OpenRouter, Together...) with `LLM_PROVIDER=openai`, or the hybrid mode; or
* run a GPU server with Ollama (`OLLAMA_NUM_PARALLEL=4`) or vLLM, and raise `LLM_CONCURRENCY` accordingly.

## Security checklist

* `DEBUG=False`, a strong `SECRET_KEY` (or let Baraza generate one in `data/secret_key.txt` and keep that file private), correct `ALLOWED_HOSTS`.
* `REQUIRE_LOGIN=True`; create accounts with `createsuperuser` or the admin site (`/admin/`).
* HTTPS with `SECURE_COOKIES=True`.
* Keep `.env` and the `data/` directory out of version control (already in `.gitignore`) and backed up.
* API keys entered in Settings are stored in the database; restrict database access accordingly.
* Review [ethics.md](ethics.md) for data protection duties when several people use collected data.

## Health check and logs

* `GET /health/` returns `{"ok": true, ...}` with the language model status (used by the Docker health check).
* Logs: `data/logs/baraza.log` (rotated), plus the console.

## Upgrading

```bash
git pull
pip install -r requirements.txt
python manage.py migrate
# restart the service
```
