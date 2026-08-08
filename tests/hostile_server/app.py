"""A FastAPI server that deterministically simulates nine adversarial scenarios (PRD §4.2).

Never deployed as part of a live backend — this is dev-only test infrastructure that
`tests/resilience/` runs the real fetchers against, so pipeline resilience is proven against
failure modes we control, never against a real third party (Hard Rule 4, `docs/adr/0004`).

`create_app()` is a factory, not a module-level singleton, so every test gets its own isolated
in-memory state — the `/flaky` and `/drift` and `/strict-rate-limit` scenarios are stateful across
requests by design, and sharing that state across tests would make them order-dependent.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

_CHALLENGE_PAGE = """<!doctype html>
<html><body>
<h1>Just a moment...</h1>
<p>Checking your browser before accessing this site.</p>
</body></html>"""

_DRIFT_OLD_MARKUP = '<div class="price">£12.99</div>'
_DRIFT_NEW_MARKUP = '<span class="cost">12.99</span>'  # selector renamed mid-run

_CHUNK_SIZE_BYTES = 1024 * 1024


@dataclass
class _ServerState:
    """Mutable, per-`create_app()`-instance state for the stateful scenarios."""

    drift_count: int = 0
    strict_rate_last_call: float | None = None


def create_app(*, flaky_seed: int = 0, drift_after_requests: int = 3) -> FastAPI:
    """Build one isolated hostile-server instance.

    `flaky_seed` makes `/flaky`'s ~20% failure rate reproducible instead of genuinely random, so
    a resilience test can assert on it deterministically. `drift_after_requests` controls how many
    calls to `/drift` return the original markup before it switches to the "redeployed" markup.
    """
    app = FastAPI(title="hostile-test-server")
    flaky_rng = random.Random(flaky_seed)
    state = _ServerState()

    @app.get("/retry-after")
    async def retry_after(seconds: int = 30) -> Response:
        """Scenario 1: 429 with a `Retry-After` header the client must honor."""
        return PlainTextResponse(
            "slow down", status_code=429, headers={"Retry-After": str(seconds)}
        )

    @app.get("/flaky")
    async def flaky() -> Response:
        """Scenario 2: ~20% of requests fail with 503; the rest succeed."""
        if flaky_rng.random() < 0.2:
            return PlainTextResponse("service unavailable", status_code=503)
        return PlainTextResponse("ok")

    @app.get("/always-down")
    async def always_down() -> Response:
        """Not one of the nine PRD scenarios — a deterministic always-503 endpoint, used by
        `tests/resilience/` to trip the circuit breaker without depending on `/flaky`'s
        randomness for a clean "N consecutive failures" signal."""
        return PlainTextResponse("service unavailable", status_code=503)

    @app.get("/timeout")
    async def timeout(delay: float = 30.0) -> Response:
        """Scenario 3: hangs for `delay` seconds before responding at all."""
        await asyncio.sleep(delay)
        return PlainTextResponse("finally")

    @app.get("/drift")
    async def drift() -> Response:
        """Scenario 4: markup changes mid-run, simulating a live selector-breaking deploy."""
        state.drift_count += 1
        markup = (
            _DRIFT_OLD_MARKUP if state.drift_count <= drift_after_requests else _DRIFT_NEW_MARKUP
        )
        return Response(content=f"<html><body>{markup}</body></html>", media_type="text/html")

    @app.get("/challenge")
    async def challenge() -> Response:
        """Scenario 5: HTTP 200 with a soft-block interstitial page as the body."""
        return Response(content=_CHALLENGE_PAGE, media_type="text/html")

    @app.get("/bad-encoding")
    async def bad_encoding() -> Response:
        """Scenario 6: body is Latin-1 bytes, declared as UTF-8."""
        body = "Café - déjà vu".encode("latin-1")
        return Response(content=body, media_type="text/html; charset=utf-8")

    @app.get("/unexpected-json")
    async def unexpected_json() -> Response:
        """Scenario 7: an endpoint that normally serves HTML instead returns JSON."""
        return JSONResponse({"unexpected": True, "reason": "endpoint returned JSON, not HTML"})

    @app.get("/strict-rate-limit")
    async def strict_rate_limit() -> Response:
        """Scenario 8: the server itself enforces 1 request/second, 429-ing anyone faster."""
        now = time.monotonic()
        last_call = state.strict_rate_last_call
        state.strict_rate_last_call = now
        if last_call is not None and (now - last_call) < 1.0:
            return PlainTextResponse("too fast", status_code=429, headers={"Retry-After": "1"})
        return PlainTextResponse("ok")

    @app.get("/huge")
    async def huge(size_mb: float = 50.0) -> StreamingResponse:
        """Scenario 9: streams a `size_mb`-megabyte body without holding it all in memory."""

        async def _chunks() -> AsyncIterator[bytes]:
            remaining_mb = size_mb
            chunk = b"x" * _CHUNK_SIZE_BYTES
            while remaining_mb > 0:
                if remaining_mb >= 1:
                    yield chunk
                else:
                    yield chunk[: int(remaining_mb * _CHUNK_SIZE_BYTES)]
                remaining_mb -= 1

        return StreamingResponse(_chunks(), media_type="application/octet-stream")

    @app.get("/js-rendered")
    async def js_rendered() -> Response:
        """Not one of the nine PRD scenarios — proves a browser fetcher actually executes
        JavaScript and waits for it, unlike a plain HTTP fetch. The server-rendered body is an
        empty shell; the real content only exists after the inline script runs, and the script
        also fires a `fetch()` call so a browser fetcher's XHR capture has something to see."""
        html = """<!doctype html>
        <html><body>
        <div id="root">loading...</div>
        <script>
          fetch('/strict-rate-limit').then(r => r.text()).then(() => {
            document.getElementById('root').textContent = 'rendered by JS';
          });
        </script>
        </body></html>"""
        return Response(content=html, media_type="text/html")

    return app
