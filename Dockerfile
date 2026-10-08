# Baraza - public sentiment rehearsal studio.
# Build:  docker build -t baraza .
# Run:    docker run -p 8000:8000 -v baraza-data:/app/data \
#           -e OLLAMA_BASE_URL=http://host.docker.internal:11434 baraza
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BARAZA_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt requirements-server.txt ./
RUN pip install -r requirements-server.txt

COPY . .

RUN useradd --create-home --uid 1000 baraza \
    && mkdir -p /app/data \
    && chown -R baraza:baraza /app
USER baraza

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=4)" || exit 1

# One process with several threads: background jobs run in threads of the same
# process (JOB_RUNNER=thread). For multiple processes use JOB_RUNNER=worker and
# start `python manage.py run_worker` in a second container.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn baraza.wsgi:application --bind 0.0.0.0:8000 --workers 1 --threads 8 --timeout 120"]
