from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock

from framecopy.config import Settings
from framecopy.engine import ProcessingPipeline


class LocalJobRunner:
    """本地开发用单并发执行器；公网部署时切换 Dramatiq + Redis。"""

    def __init__(self, pipeline: ProcessingPipeline):
        self.pipeline = pipeline
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="framecopy-worker")
        self.futures: dict[str, Future[None]] = {}
        self.lock = Lock()

    def enqueue(self, job_id: str) -> None:
        with self.lock:
            existing = self.futures.get(job_id)
            if existing and not existing.done():
                return
            future = self.executor.submit(self.pipeline.run, job_id)
            self.futures[job_id] = future


class QueueFacade:
    def __init__(self, settings: Settings, local: LocalJobRunner):
        self.settings = settings
        self.local = local

    def enqueue(self, job_id: str) -> None:
        if self.settings.queue_backend == "dramatiq":
            try:
                from framecopy.worker import process_job
            except ImportError as exc:
                raise RuntimeError("请安装 queue 可选依赖后再启用 Dramatiq") from exc
            process_job.send(job_id)
            return
        self.local.enqueue(job_id)

