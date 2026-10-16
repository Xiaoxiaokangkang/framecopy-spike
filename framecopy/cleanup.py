from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.storage import delete_job_files, job_paths


class CleanupService:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    def run_once(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        removed_jobs = 0
        removed_inputs = 0
        with self.database.connect() as connection:
            expired = connection.execute(
                "SELECT id FROM jobs WHERE expires_at < ?", (now.isoformat(),)
            ).fetchall()
            input_cutoff = (now - timedelta(hours=self.settings.input_retention_hours)).isoformat()
            stale_inputs = connection.execute(
                """SELECT id FROM jobs
                   WHERE completed_at IS NOT NULL AND completed_at < ? AND expires_at >= ?""",
                (input_cutoff, now.isoformat()),
            ).fetchall()
        for row in stale_inputs:
            input_dir = job_paths(self.settings, row["id"], create=False).input
            if input_dir.exists():
                shutil.rmtree(input_dir)
                removed_inputs += 1
        for row in expired:
            delete_job_files(self.settings, row["id"])
            with self.database.connect() as connection:
                connection.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
            removed_jobs += 1
        return {"removed_jobs": removed_jobs, "removed_inputs": removed_inputs}
