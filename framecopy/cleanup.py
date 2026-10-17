from __future__ import annotations

from datetime import datetime, timedelta, timezone

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.models import JobStatus
from framecopy.storage import (
    delete_job_files,
    delete_source_files,
    job_paths,
    update_source_cleanup_manifest,
)


class CleanupService:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    def run_once(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        removed_jobs = 0
        removed_inputs = 0
        recovered_jobs = 0
        stale_before = now - timedelta(
            seconds=max(2 * 60 * 60, self.settings.speech_timeout_seconds * 2)
        )
        with self.database.connect() as connection:
            expired = connection.execute(
                "SELECT id FROM jobs WHERE expires_at < ?", (now.isoformat(),)
            ).fetchall()
            stale_inputs = connection.execute(
                """SELECT jobs.id, jobs.asset_id
                   FROM jobs
                   LEFT JOIN video_assets ON video_assets.id=jobs.asset_id
                   WHERE jobs.completed_at IS NOT NULL
                     AND jobs.expires_at >= ?
                     AND COALESCE(video_assets.cleanup_status, 'pending') != 'deleted'""",
                (now.isoformat(),),
            ).fetchall()
            abandoned = connection.execute(
                """SELECT jobs.id, jobs.asset_id
                   FROM jobs
                   LEFT JOIN video_assets ON video_assets.id=jobs.asset_id
                   WHERE jobs.completed_at IS NULL
                     AND jobs.status NOT IN ('ready', 'failed', 'cancelled')
                     AND COALESCE(jobs.heartbeat_at, jobs.processing_started_at, jobs.created_at) < ?
                     AND COALESCE(video_assets.cleanup_status, 'pending') != 'deleted'""",
                (stale_before.isoformat(),),
            ).fetchall()
        abandoned_ids = {row["id"] for row in abandoned}
        stale_ids = {row["id"] for row in stale_inputs}
        stale_inputs.extend(row for row in abandoned if row["id"] not in stale_ids)
        for row in stale_inputs:
            try:
                delete_source_files(self.settings, row["id"])
                self.database.update_asset(
                    row["id"], status="deleted", cleanup_status="deleted",
                    local_path=None, deleted_at=now.isoformat(), last_cleanup_error=None,
                )
                try:
                    update_source_cleanup_manifest(
                        job_paths(self.settings, row["id"], create=False).output,
                        "deleted",
                        now.isoformat(),
                    )
                except OSError:
                    pass
                removed_inputs += 1
                if row["id"] in abandoned_ids:
                    message = "上次处理意外中断，临时视频已清理，请重新提交。"
                    self.database.update_job(
                        row["id"],
                        JobStatus.FAILED,
                        label=message,
                        progress=0,
                        completed_at=now.isoformat(),
                        error_code="INTERRUPTED_RECOVERED",
                        error_message=message,
                    )
                    recovered_jobs += 1
            except OSError as exc:
                self.database.record_asset_cleanup_failure(row["id"], type(exc).__name__)
        for row in expired:
            with self.database.connect() as connection:
                asset = connection.execute(
                    "SELECT asset_id FROM jobs WHERE id=?", (row["id"],)
                ).fetchone()
            delete_job_files(self.settings, row["id"])
            with self.database.connect() as connection:
                connection.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
                if asset and asset["asset_id"]:
                    connection.execute("DELETE FROM video_assets WHERE id=?", (asset["asset_id"],))
            removed_jobs += 1
        return {
            "removed_jobs": removed_jobs,
            "removed_inputs": removed_inputs,
            "recovered_jobs": recovered_jobs,
        }
