from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from framecopy.api import create_router
from framecopy.cleanup import CleanupService
from framecopy.config import settings
from framecopy.database import Database
from framecopy.engine import ProcessingPipeline
from framecopy.runner import LocalJobRunner, QueueFacade


BASE_DIR = Path(__file__).resolve().parent
database = Database(settings)
database.initialize()
pipeline = ProcessingPipeline(settings, database)
queue = QueueFacade(settings, LocalJobRunner(pipeline))


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = Event()
    cleanup = CleanupService(settings, database)

    def cleanup_loop() -> None:
        cleanup.run_once()
        while not stop.wait(3600):
            cleanup.run_once()

    thread = Thread(target=cleanup_loop, name="framecopy-cleanup", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()

app = FastAPI(
    title="帧文案",
    description="本地优先的视频画面与语音文案提取工具",
    version="0.4.2",
    lifespan=lifespan,
)
app.include_router(create_router(settings, database, queue))
app.mount("/static", StaticFiles(directory=BASE_DIR / "web" / "static"), name="static")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "web" / "index.html")


@app.get("/jobs/{job_id}/view", include_in_schema=False)
def job_view(job_id: str) -> FileResponse:
    return FileResponse(BASE_DIR / "web" / "job.html")

