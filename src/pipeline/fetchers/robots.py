"""robots.txt compliance: fetches and caches each origin's rules (Hard Rule 3, CLAUDE.md §2)."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

_FULLY_PERMISSIVE = ["User-agent: *"]
_FULLY_DISALLOWED = ["User-agent: *", "Disallow: /"]


class RobotsChecker:
    """Fetches, parses, and caches robots.txt per origin using the stdlib parser.

    A missing robots.txt (404, or any other 4xx) is treated as fully permissive — the file simply
    does not exist, so no rules apply. A server error or network failure while fetching it is
    treated as fully disallowed: the site's actual policy is unknown, and a robots violation must
    fail the task rather than guess "allowed".
    """

    def __init__(self, client: httpx.AsyncClient, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser] = {}
        self._lock = asyncio.Lock()

    async def is_allowed(self, url: str) -> bool:
        """Return whether this checker's user agent may fetch `url`."""
        parser = await self._get_parser(url)
        return parser.can_fetch(self._user_agent, url)

    async def crawl_delay(self, url: str) -> float | None:
        """Return the `Crawl-delay` declared for `url`'s origin, or `None` if not declared."""
        parser = await self._get_parser(url)
        delay = parser.crawl_delay(self._user_agent)
        return float(delay) if delay is not None else None

    async def _get_parser(self, url: str) -> RobotFileParser:
        origin = self._origin(url)
        async with self._lock:
            if origin not in self._parsers:
                self._parsers[origin] = await self._fetch_parser(origin)
            return self._parsers[origin]

    async def _fetch_parser(self, origin: str) -> RobotFileParser:
        parser = RobotFileParser()
        robots_url = f"{origin}/robots.txt"
        try:
            response = await self._client.get(robots_url, headers={"User-Agent": self._user_agent})
        except httpx.HTTPError:
            parser.parse(_FULLY_DISALLOWED)
            return parser

        if response.status_code == 200:
            parser.parse(response.text.splitlines())
        elif 400 <= response.status_code < 500:
            parser.parse(_FULLY_PERMISSIVE)
        else:
            parser.parse(_FULLY_DISALLOWED)
        return parser

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlsplit(url)
        return f"{parsed.scheme}://{parsed.netloc}"
