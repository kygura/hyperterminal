from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mock_signals import MockSignalWriter
from routers.signals import router as signals_router
from routers.signals import signal_broadcaster


@asynccontextmanager
async def lifespan(app: FastAPI):
    await signal_broadcaster.start()

    mock_writer_task: asyncio.Task | None = None
    if os.getenv("ENABLE_MOCK_SIGNALS", "true").lower() not in {
        "0",
        "false",
        "no",
    }:
        mock_writer_task = asyncio.create_task(MockSignalWriter().run_forever())

    try:
        yield
    finally:
        if mock_writer_task is not None:
            mock_writer_task.cancel()
            with suppress(asyncio.CancelledError):
                await mock_writer_task

        await signal_broadcaster.stop()


app = FastAPI(title="Hypertrade Signal Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(signals_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "hypertrade-signal-backend"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}
