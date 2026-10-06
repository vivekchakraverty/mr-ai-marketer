"""Bounded public HTTPS reads shared with buyer persona research."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import threading
import time
from collections import defaultdict
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = "MrAIMarketer-PersonaResearch/1.0 (local desktop app; research contact: https://github.com/vivekchakraverty/mr-ai-marketer)"
_last_request: dict[str, float] = defaultdict(float)
_cache: dict[str, tuple[float, requests.Response]] = {}
_lock = threading.Lock()


def _public_host(host: str) -> bool:
    if not host or host.lower() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(addresses) and all(ipaddress.ip_address(entry[4][0]).is_global for entry in addresses)


def get(url: str, *, params: dict | None = None, session: requests.Session | None = None,
        robots: bool = False) -> requests.Response:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not _public_host(parsed.hostname or ""):
        raise ValueError("Only public HTTPS hosts are allowed.")
    host = parsed.hostname or ""
    if robots:
        parser = RobotFileParser()
        parser.set_url(f"https://{host}/robots.txt")
        try:
            rules = get(f"https://{host}/robots.txt", session=session)
            parser.parse(rules.text.splitlines())
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            parser.parse([])
        if not parser.can_fetch(USER_AGENT, url):
            raise PermissionError("The site's robots.txt disallows this page.")
    key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < 3600:
            return cached[1]
        wait = max(0, 1.0 - (time.monotonic() - _last_request[host]))
        if wait:
            time.sleep(wait)
        _last_request[host] = time.monotonic()
    requester = session or requests
    response = requester.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=12,
                             allow_redirects=False)
    if response.status_code in (429, 500, 502, 503, 504):
        time.sleep(min(float(response.headers.get("Retry-After", 1) or 1), 3))
        response = requester.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=12,
                                 allow_redirects=False)
    response.raise_for_status()
    if 300 <= response.status_code < 400:
        raise ValueError("Redirected sources are not followed.")
    with _lock:
        _cache[key] = (time.time(), response)
    return response
