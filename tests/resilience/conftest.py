"""Shared harness for running a real hostile-server instance over a real loopback socket.

`tests/hostile_server/test_app.py` uses `httpx.ASGITransport` for pure in-process endpoint checks.
These resilience tests need a real socket instead: `ASGITransport` calls the ASGI app directly
with no transport-level timeout wrapping, so httpx's own connect/read timeout never fires against
it — which would silently defeat the timeout-handling scenario (PRD §4.2, scenario 3).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn

from tests.hostile_server.app import create_app


@asynccontextmanager
async def hostile_server(
    *, flaky_seed: int = 0, drift_after_requests: int = 3
) -> AsyncIterator[str]:
    """Run a real hostile-server instance on a free loopback port for the lifetime of the block.

    Yields the server's base URL (e.g. `http://127.0.0.1:54321`).
    """
    app = create_app(flaky_seed=flaky_seed, drift_after_requests=drift_after_requests)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await serve_task
