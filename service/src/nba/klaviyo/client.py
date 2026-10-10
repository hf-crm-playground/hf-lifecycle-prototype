"""Klaviyo REST client (JSON:API), standard library only.

Three modes, chosen once per client:

- "offline": no network at all. Reads raise; writes go to the outbox.
- "dry_run": reads (GET) hit the live API; writes (POST/PATCH/DELETE) are
  never sent. Each is appended to a JSONL outbox exactly as it would be sent
  (method, path, headers without the key, body), so the outbox can be
  reviewed, diffed or replayed later.
- "live": writes are sent. Requires both mode="live" and the environment
  variable NBA_KLAVIYO_LIVE_WRITES=yes, and only paths in WRITE_ALLOWLIST can
  be written: events and profile imports. The service can never create,
  edit or delete flows, campaigns, lists, segments or templates.

Rate limits follow Klaviyo's per-endpoint tiers with a token bucket per
tier. 429 honours Retry-After; 5xx and network errors back off
exponentially. Every request carries the pinned `revision` header.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

BASE_URL = "https://a.klaviyo.com"
DEFAULT_REVISION = "2026-07-15"
LIVE_ENV = "NBA_KLAVIYO_LIVE_WRITES"

# Paths the service may write to in live mode (prefix match on the path).
WRITE_ALLOWLIST = (
    "/api/events",
    "/api/event-bulk-create-jobs",
    "/api/profile-import",
    "/api/profile-bulk-import-jobs",
)

# (burst per second, steady per minute) by Klaviyo rate-limit tier.
RATE_TIERS = {
    "events_write": (350, 3500),
    "events_read": (350, 3500),
    "profile_import": (75, 700),
    "bulk_jobs": (10, 150),
    "default": (10, 150),
}


def _tier_for(method: str, path: str) -> str:
    if path.startswith("/api/events"):
        return "events_write" if method != "GET" else "events_read"
    if path.startswith("/api/profile-import"):
        return "profile_import"
    if "bulk" in path:
        return "bulk_jobs"
    return "default"


class KlaviyoError(Exception):
    def __init__(self, status: int, body: Any, path: str):
        self.status, self.body, self.path = status, body, path
        detail = ""
        if isinstance(body, dict) and body.get("errors"):
            e = body["errors"][0]
            detail = f": {e.get('title', '')} {e.get('detail', '')}".rstrip()
        super().__init__(f"Klaviyo {status} on {path}{detail}")


class WriteBlocked(Exception):
    pass


@dataclass
class Response:
    status: int
    headers: Dict[str, str]
    body: Any


# transport(method, url, headers, data, timeout) -> (status, headers, raw bytes)
Transport = Callable[[str, str, Dict[str, str], Optional[bytes], float], Tuple[int, Dict[str, str], bytes]]


def urllib_transport(method, url, headers, data, timeout):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b""


class _Bucket:
    def __init__(self, burst: int, per_minute: int, clock, sleep):
        self.burst_cap, self.rate = float(burst), per_minute / 60.0
        self.tokens = float(burst)
        self.last = clock()
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()

    def take(self) -> None:
        with self.lock:
            while True:
                now = self.clock()
                self.tokens = min(self.burst_cap, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                self.sleep((1 - self.tokens) / self.rate)


class KlaviyoClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        mode: str = "dry_run",
        outbox_path: Optional[str] = None,
        revision: str = DEFAULT_REVISION,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_retries: int = 5,
        timeout: float = 30.0,
        base_url: str = BASE_URL,
    ):
        if mode not in ("offline", "dry_run", "live"):
            raise ValueError(f"unknown mode {mode!r}")
        if mode == "live" and os.environ.get(LIVE_ENV) != "yes":
            raise WriteBlocked(f"live mode needs {LIVE_ENV}=yes in the environment")
        if mode != "offline" and not api_key:
            raise ValueError("an API key is needed for dry_run and live (reads are live)")
        self.api_key = api_key
        self.mode = mode
        self.outbox_path = outbox_path
        self.revision = revision
        self.transport = transport or urllib_transport
        self.sleep, self.clock = sleep, clock
        self.max_retries, self.timeout = max_retries, timeout
        self.base_url = base_url.rstrip("/")
        self._buckets: Dict[str, _Bucket] = {}
        self.outbox_count = 0
        self.requests_sent = 0

    # ---------------------------------------------------------- helpers

    def _headers(self, with_body: bool) -> Dict[str, str]:
        h = {
            "Authorization": f"Klaviyo-API-Key {self.api_key}",
            "revision": self.revision,
            "accept": "application/vnd.api+json",
            "User-Agent": "holafly-nba-decision-service/0.1",
        }
        if with_body:
            h["content-type"] = "application/vnd.api+json"
        return h

    def _bucket(self, tier: str) -> _Bucket:
        if tier not in self._buckets:
            self._buckets[tier] = _Bucket(*RATE_TIERS[tier], clock=self.clock, sleep=self.sleep)
        return self._buckets[tier]

    def _url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        if path.startswith("http"):
            return path  # a links.next cursor URL
        q = ("?" + urllib.parse.urlencode(params, safe='(),"[]:')) if params else ""
        return f"{self.base_url}{path}{q}"

    def _write_outbox(self, method: str, path: str, body: Any, note: Optional[str]) -> Response:
        rec = {
            "at": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "method": method,
            "url": f"{self.base_url}{path}",
            "headers": {k: v for k, v in self._headers(body is not None).items() if k != "Authorization"},
            "body": body,
        }
        if note:
            rec["note"] = note
        if self.outbox_path:
            os.makedirs(os.path.dirname(os.path.abspath(self.outbox_path)), exist_ok=True)
            with open(self.outbox_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
        self.outbox_count += 1
        return Response(202, {"x-nba-dry-run": "true"}, None)

    # ----------------------------------------------------------- request

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                body: Any = None, note: Optional[str] = None) -> Response:
        method = method.upper()
        is_write = method != "GET"
        if is_write:
            if self.mode in ("offline", "dry_run"):
                return self._write_outbox(method, path, body, note)
            if not path.startswith(WRITE_ALLOWLIST):
                raise WriteBlocked(f"{method} {path} is not in the write allowlist")
        elif self.mode == "offline":
            raise RuntimeError("offline mode: reads are disabled")

        url = self._url(path, params)
        data = json.dumps(body).encode() if body is not None else None
        tier = _tier_for(method, urllib.parse.urlparse(url).path)
        attempt = 0
        while True:
            self._bucket(tier).take()
            try:
                status, headers, raw = self.transport(method, url, self._headers(data is not None), data, self.timeout)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt >= self.max_retries:
                    raise
                self.sleep(self._backoff(attempt))
                attempt += 1
                continue
            self.requests_sent += 1
            parsed: Any = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = raw.decode("utf-8", "replace")
            if status == 429 or status >= 500:
                if attempt >= self.max_retries:
                    raise KlaviyoError(status, parsed, path)
                wait = self._retry_after(headers) if status == 429 else None
                self.sleep(wait if wait is not None else self._backoff(attempt))
                attempt += 1
                continue
            if status >= 400:
                raise KlaviyoError(status, parsed, path)
            return Response(status, headers, parsed)

    @staticmethod
    def _retry_after(headers: Dict[str, str]) -> Optional[float]:
        v = headers.get("retry-after")
        if v is None:
            return None
        try:
            return max(0.0, float(v))
        except ValueError:
            return None

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(60.0, (2 ** attempt) * 0.5) + random.uniform(0, 0.25)

    # ------------------------------------------------------- read helpers

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.request("GET", path, params).body

    def paginate(self, path: str, params: Optional[Dict[str, Any]] = None,
                 max_pages: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Yield each page body, following links.next cursors."""
        url, p, pages = path, params, 0
        while url:
            body = self.request("GET", url, p).body
            yield body
            pages += 1
            if max_pages and pages >= max_pages:
                return
            url, p = (body.get("links") or {}).get("next"), None

    def iter_records(self, path: str, params: Optional[Dict[str, Any]] = None,
                     max_pages: Optional[int] = None) -> Iterator[Tuple[Dict[str, Any], Dict[Tuple[str, str], Dict[str, Any]]]]:
        """Yield (record, included-by-(type, id)) for every record on every page."""
        for page in self.paginate(path, params, max_pages):
            included = {(i["type"], i["id"]): i for i in page.get("included", []) or []}
            for rec in page.get("data", []) or []:
                yield rec, included

    # ------------------------------------------------------ write helpers

    def create_event(self, body: Dict[str, Any], note: Optional[str] = None) -> Response:
        return self.request("POST", "/api/events", body=body, note=note)

    def import_profile(self, body: Dict[str, Any], note: Optional[str] = None) -> Response:
        return self.request("POST", "/api/profile-import", body=body, note=note)

    def bulk_import_profiles(self, body: Dict[str, Any], note: Optional[str] = None) -> Response:
        return self.request("POST", "/api/profile-bulk-import-jobs", body=body, note=note)

    def bulk_create_events(self, body: Dict[str, Any], note: Optional[str] = None) -> Response:
        return self.request("POST", "/api/event-bulk-create-jobs", body=body, note=note)


def client_from_env(mode: Optional[str] = None, outbox_path: Optional[str] = None, **kw) -> KlaviyoClient:
    """KLAVIYO_API_KEY (private key, read scopes for dry_run: accounts, events,
    flows, metrics, profiles, segments), KLAVIYO_REVISION, NBA_KLAVIYO_MODE."""
    mode = mode or os.environ.get("NBA_KLAVIYO_MODE", "dry_run")
    return KlaviyoClient(
        api_key=os.environ.get("KLAVIYO_API_KEY"),
        mode=mode,
        outbox_path=outbox_path,
        revision=os.environ.get("KLAVIYO_REVISION", DEFAULT_REVISION),
        **kw,
    )
