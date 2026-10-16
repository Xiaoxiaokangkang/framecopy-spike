from __future__ import annotations

try:
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker
except ImportError as exc:  # pragma: no cover - 仅在 queue extra 未安装时发生
    raise ImportError("Dramatiq worker requires the queue optional dependencies") from exc

from framecopy.config import settings
from framecopy.database import Database
from framecopy.engine import ProcessingPipeline


broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(broker)


@dramatiq.actor(max_retries=1, time_limit=45 * 60 * 1000)
def process_job(job_id: str) -> None:
    database = Database(settings)
    database.initialize()
    ProcessingPipeline(settings, database).run(job_id)
