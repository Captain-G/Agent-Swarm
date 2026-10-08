"""
Polite HTTP fetching for research: honest User-Agent, per-host rate limits,
robots.txt checks for page scraping, retries with back-off, and injectable
transports for testing.
"""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger("baraza.research")

HOST_INTERVALS = {
    "api.gdeltproject.org": 5.5,  # GDELT asks for one request every 5 seconds
    "www.reddit.com": 2.0,
    "oauth.reddit.com": 1.0,
    "news.google.com": 1.0,
    "html.duckduckgo.com": 2.0,
    "en.wikipedia.org": 0.3,
    "t.me": 1.5,
    "r.jina.ai": 3.1,
    "www.googleapis.com": 0.2,
}
DEFAULT_INTERVAL = 0.6


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 20,
        respect_robots: bool = True,
        client: httpx.Client | None = None,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Language": "en;q=0.9"},
        )
        self._lock = threading.Lock()
        self._host_next: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    def close(self):
        self.client.close()

    # -- politeness -----------------------------------------------------
    def _wait_turn(self, host: str):
        interval = HOST_INTERVALS.get(host, DEFAULT_INTERVAL)
        with self._lock:
            now = time.monotonic()
            next_ok = self._host_next.get(host, now)
            wait = max(0.0, next_ok - now)
            self._host_next[host] = max(now, next_ok) + interval
        if wait:
            time.sleep(wait)

    def allowed_by_robots(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            known = base in self._robots
            parser = self._robots.get(base)
        if not known:
            parser = None
            try:
                response = self.client.get(f"{base}/robots.txt", timeout=8.0)
                if response.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(response.text.splitlines())
            except httpx.HTTPError:
                parser = None
            with self._lock:
                self._robots[base] = parser
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    # -- requests ---------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        json: dict | list | None = None,
        data: dict | None = None,
        auth=None,
        timeout: float | None = None,
        retries: int = 2,
    ) -> httpx.Response:
        host = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            self._wait_turn(host)
            try:
                response = self.client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json,
                    data=data,
                    auth=auth,
                    timeout=timeout or self.timeout,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code in (429, 502, 503, 504) and attempt < retries:
                retry_after = response.headers.get("retry-after", "")
                wait = float(retry_after) if retry_after.replace(".", "").isdigit() else 3.0 * (attempt + 1)
                time.sleep(min(wait, 20.0))
                continue
            return response
        raise FetchError(f"{method} {url} failed: {last_exc}")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def get_json(self, url: str, **kwargs):
        response = self.get(url, **kwargs)
        if response.status_code >= 400:
            raise FetchError(f"HTTP {response.status_code} from {urlparse(url).netloc}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"Non-JSON response from {urlparse(url).netloc}: {response.text[:200]}") from exc
