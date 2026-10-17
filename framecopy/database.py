from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from framecopy.config import Settings
from framecopy.models import ExtractionMode, JobStatus, Segment, SpeechSegment


# Kept only to satisfy the legacy database column. It is not an invite or a
# user-supplied credential; every local task uses the same internal scope.
LOCAL_JOB_SCOPE = "local-user"


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  asset_id TEXT,
  source_type TEXT NOT NULL,
  source_name TEXT NOT NULL,
  extraction_mode TEXT NOT NULL DEFAULT 'visual',
  invite_hash TEXT NOT NULL,
  access_token_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  stage_label TEXT NOT NULL DEFAULT '',
  progress INTEGER NOT NULL DEFAULT 0,
  progress_current INTEGER,
  progress_total INTEGER,
  duration REAL,
  input_size INTEGER,
  media_codec TEXT,
  width INTEGER,
  height INTEGER,
  fps REAL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  cancel_requested_at TEXT,
  heartbeat_at TEXT,
  processing_started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  error_code TEXT,
  error_message TEXT,
  engine_name TEXT,
  model_name TEXT,
  result_version INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_invite_created ON jobs(invite_hash, created_at);
CREATE TABLE IF NOT EXISTS segments (
  job_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  start_time REAL NOT NULL,
  end_time REAL NOT NULL,
  raw_text TEXT NOT NULL,
  consensus_text TEXT NOT NULL,
  clean_text TEXT NOT NULL,
  user_text TEXT,
  confidence REAL NOT NULL,
  evidence_frame TEXT NOT NULL,
  needs_review INTEGER NOT NULL DEFAULT 0,
  clean_eligible INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(job_id, sequence),
  FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS video_assets (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'created',
  local_path TEXT,
  size INTEGER,
  duration REAL,
  media_codec TEXT,
  width INTEGER,
  height INTEGER,
  fps REAL,
  cleanup_status TEXT NOT NULL DEFAULT 'pending',
  cleanup_attempts INTEGER NOT NULL DEFAULT 0,
  last_cleanup_error TEXT,
  created_at TEXT NOT NULL,
  validated_at TEXT,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS speech_segments (
  job_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  start_time REAL NOT NULL,
  end_time REAL NOT NULL,
  raw_text TEXT NOT NULL,
  clean_text TEXT NOT NULL,
  user_text TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  language TEXT NOT NULL DEFAULT '',
  needs_review INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(job_id, sequence),
  FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path: Path = settings.database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.settings.ensure_directories()
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(segments)")}
            if "clean_eligible" not in columns:
                connection.execute(
                    "ALTER TABLE segments ADD COLUMN clean_eligible INTEGER NOT NULL DEFAULT 1"
                )
            job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            additions = {
                "asset_id": "TEXT",
                "extraction_mode": "TEXT NOT NULL DEFAULT 'visual'",
                "engine_name": "TEXT",
                "model_name": "TEXT",
            }
            for name, definition in additions.items():
                if name not in job_columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            legacy_jobs = connection.execute(
                "SELECT id, source_type, source_name, created_at FROM jobs WHERE asset_id IS NULL"
            ).fetchall()
            for row in legacy_jobs:
                asset_id = secrets.token_hex(16)
                connection.execute(
                    """INSERT INTO video_assets(
                      id, source_type, source_name, status, cleanup_status, created_at
                    ) VALUES (?, ?, ?, 'legacy', 'unknown', ?)""",
                    (asset_id, row["source_type"], row["source_name"], row["created_at"]),
                )
                connection.execute("UPDATE jobs SET asset_id=? WHERE id=?", (asset_id, row["id"]))

    def create_job(
        self,
        source_type: str,
        source_name: str,
        extraction_mode: ExtractionMode | str = ExtractionMode.VISUAL,
    ) -> tuple[str, str]:
        job_id = secrets.token_hex(16)
        asset_id = secrets.token_hex(16)
        access_token = secrets.token_urlsafe(32)
        mode = ExtractionMode(extraction_mode).value
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.settings.retention_hours)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO video_assets(
                  id, source_type, source_name, status, cleanup_status, created_at
                ) VALUES (?, ?, ?, 'created', 'pending', ?)""",
                (asset_id, source_type, source_name[:255], now.isoformat()),
            )
            connection.execute(
                """INSERT INTO jobs(
                  id, asset_id, source_type, source_name, extraction_mode,
                  invite_hash, access_token_hash, status, stage_label, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id, asset_id, source_type, source_name[:255], mode, LOCAL_JOB_SCOPE,
                    hash_secret(access_token),
                    JobStatus.QUEUED, "等待处理", now.isoformat(), expires.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO job_events(job_id, status, message, created_at) VALUES (?, ?, ?, ?)",
                (job_id, JobStatus.QUEUED, "任务已创建", now.isoformat()),
            )
        return job_id, access_token

    def authorize(self, job_id: str, token: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = self._job_query(connection, job_id)
        if not row or not secrets.compare_digest(row["access_token_hash"], hash_secret(token)):
            return None
        return dict(row)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = self._job_query(connection, job_id)
        return dict(row) if row else None

    @staticmethod
    def _job_query(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT jobs.*,
                      video_assets.status AS asset_status,
                      video_assets.cleanup_status AS source_cleanup_status,
                      video_assets.deleted_at AS source_deleted_at
               FROM jobs
               LEFT JOIN video_assets ON video_assets.id=jobs.asset_id
               WHERE jobs.id=?""",
            (job_id,),
        ).fetchone()

    def update_job(self, job_id: str, status: JobStatus, *, label: str, progress: int,
                   current: int | None = None, total: int | None = None,
                   **fields: Any) -> None:
        allowed = {
            "duration", "input_size", "media_codec", "width", "height", "fps",
            "heartbeat_at", "processing_started_at", "completed_at", "error_code", "error_message",
            "cancel_requested_at", "attempt_count", "engine_name", "model_name",
        }
        values: dict[str, Any] = {
            "status": status.value, "stage_label": label, "progress": max(0, min(progress, 100)),
            "progress_current": current, "progress_total": total,
        }
        values.update({key: value for key, value in fields.items() if key in allowed})
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?", (*values.values(), job_id)
            )
            connection.execute(
                "INSERT INTO job_events(job_id, status, message, created_at) VALUES (?, ?, ?, ?)",
                (job_id, status.value, label, utcnow()),
            )

    def update_asset(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status", "local_path", "size", "duration", "media_codec", "width", "height",
            "fps", "cleanup_status", "cleanup_attempts", "last_cleanup_error",
            "validated_at", "deleted_at",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connect() as connection:
            connection.execute(
                f"""UPDATE video_assets SET {assignments}
                    WHERE id=(SELECT asset_id FROM jobs WHERE id=?)""",
                (*values.values(), job_id),
            )

    def record_asset_cleanup_failure(self, job_id: str, error_name: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE video_assets
                   SET status='delete_failed', cleanup_status='failed',
                       cleanup_attempts=cleanup_attempts+1, last_cleanup_error=?
                   WHERE id=(SELECT asset_id FROM jobs WHERE id=?)""",
                (error_name[:120], job_id),
            )

    def request_cancel(self, job_id: str) -> None:
        self.update_job(
            job_id, JobStatus.CANCEL_REQUESTED, label="正在取消", progress=0,
            cancel_requested_at=utcnow(),
        )

    def is_cancel_requested(self, job_id: str) -> bool:
        row = self.get_job(job_id)
        return bool(row and row["cancel_requested_at"])

    def replace_segments(self, job_id: str, segments: list[Segment]) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM segments WHERE job_id=?", (job_id,))
            connection.executemany(
                """INSERT INTO segments(
                  job_id, sequence, start_time, end_time, raw_text, consensus_text,
                  clean_text, user_text, confidence, evidence_frame, needs_review, clean_eligible
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        job_id, item.sequence, item.start_time, item.end_time, item.raw_text,
                        item.consensus_text, item.clean_text, item.user_text, item.confidence,
                        item.evidence_frame, int(item.needs_review), int(item.clean_eligible),
                    )
                    for item in segments
                ],
            )

    def get_segments(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM segments WHERE job_id=? ORDER BY sequence", (job_id,)
            ).fetchall()
        return [dict(row) | {"effective_text": row["user_text"] if row["user_text"] is not None else row["clean_text"]} for row in rows]

    def update_segments(self, job_id: str, updates: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            for item in updates:
                connection.execute(
                    "UPDATE segments SET user_text=? WHERE job_id=? AND sequence=?",
                    (str(item.get("text", "")), job_id, int(item["sequence"])),
                )
            connection.execute("UPDATE jobs SET result_version=result_version+1 WHERE id=?", (job_id,))

    def restore_segments(self, job_id: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE segments SET user_text=NULL WHERE job_id=?", (job_id,))
            connection.execute("UPDATE jobs SET result_version=result_version+1 WHERE id=?", (job_id,))

    def replace_speech_segments(self, job_id: str, segments: list[SpeechSegment]) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM speech_segments WHERE job_id=?", (job_id,))
            connection.executemany(
                """INSERT INTO speech_segments(
                  job_id, sequence, start_time, end_time, raw_text, clean_text, user_text,
                  confidence, language, needs_review
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        job_id, item.sequence, item.start_time, item.end_time, item.raw_text,
                        item.clean_text, item.user_text, item.confidence, item.language,
                        int(item.needs_review),
                    )
                    for item in segments
                ],
            )

    def get_speech_segments(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM speech_segments WHERE job_id=? ORDER BY sequence", (job_id,)
            ).fetchall()
        return [
            dict(row) | {
                "effective_text": row["user_text"] if row["user_text"] is not None else row["clean_text"]
            }
            for row in rows
        ]

    def update_speech_segments(self, job_id: str, updates: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            for item in updates:
                connection.execute(
                    "UPDATE speech_segments SET user_text=? WHERE job_id=? AND sequence=?",
                    (str(item.get("text", "")), job_id, int(item["sequence"])),
                )
            connection.execute("UPDATE jobs SET result_version=result_version+1 WHERE id=?", (job_id,))

    def restore_speech_segments(self, job_id: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE speech_segments SET user_text=NULL WHERE job_id=?", (job_id,))
            connection.execute("UPDATE jobs SET result_version=result_version+1 WHERE id=?", (job_id,))
