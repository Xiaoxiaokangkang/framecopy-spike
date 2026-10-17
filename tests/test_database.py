import sqlite3
import time
from pathlib import Path

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.models import ExtractionMode, Segment, SpeechSegment


def make_settings(path: Path) -> Settings:
    return Settings(data_dir=path, database_path=path / "test.db")


def test_job_access_token_and_edit_history(tmp_path: Path) -> None:
    database = Database(make_settings(tmp_path))
    database.initialize()
    job_id, token = database.create_job("upload", "test.mp4")
    assert database.authorize(job_id, token)
    assert database.authorize(job_id, "wrong") is None
    database.replace_segments(job_id, [Segment(1, 0, 1, "原文", "原文", "整理", .9, "frame.jpg")])
    database.update_segments(job_id, [{"sequence": 1, "text": "人工修改"}])
    assert database.get_segments(job_id)[0]["effective_text"] == "人工修改"
    database.restore_segments(job_id)
    assert database.get_segments(job_id)[0]["effective_text"] == "整理"


def test_authorize_reads_committed_snapshot_during_writer_transaction(tmp_path: Path) -> None:
    database = Database(make_settings(tmp_path))
    database.initialize()
    job_id, token = database.create_job("upload", "test.mp4")

    writer = sqlite3.connect(database.path, timeout=0)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute(
            "UPDATE jobs SET stage_label=? WHERE id=?", ("正在写入", job_id)
        )

        started = time.monotonic()
        authorized = database.authorize(job_id, token)
        elapsed = time.monotonic() - started

        assert authorized is not None
        assert elapsed < 1
        assert database.authorize(job_id, "wrong") is None
    finally:
        writer.rollback()
        writer.close()


def test_speech_job_has_asset_and_editable_segments(tmp_path: Path) -> None:
    database = Database(make_settings(tmp_path))
    database.initialize()
    job_id, token = database.create_job("upload", "speech.mp4", ExtractionMode.SPEECH)

    row = database.authorize(job_id, token)
    assert row is not None
    assert row["extraction_mode"] == "speech"
    assert row["asset_id"]
    assert row["asset_status"] == "created"
    assert row["source_cleanup_status"] == "pending"

    database.replace_speech_segments(
        job_id,
        [SpeechSegment(1, 1.2, 3.4, " 原始语音 ", "原始语音", .8, "zh")],
    )
    database.update_speech_segments(job_id, [{"sequence": 1, "text": "人工修改"}])
    assert database.get_speech_segments(job_id)[0]["effective_text"] == "人工修改"
    database.restore_speech_segments(job_id)
    assert database.get_speech_segments(job_id)[0]["effective_text"] == "原始语音"


def test_initialize_migrates_legacy_job_once(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_directories()
    with sqlite3.connect(settings.database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs (
              id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_name TEXT NOT NULL,
              invite_hash TEXT NOT NULL, access_token_hash TEXT NOT NULL,
              status TEXT NOT NULL, stage_label TEXT NOT NULL DEFAULT '',
              progress INTEGER NOT NULL DEFAULT 0, progress_current INTEGER,
              progress_total INTEGER, duration REAL, input_size INTEGER, media_codec TEXT,
              width INTEGER, height INTEGER, fps REAL, attempt_count INTEGER NOT NULL DEFAULT 0,
              cancel_requested_at TEXT, heartbeat_at TEXT, processing_started_at TEXT,
              completed_at TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
              error_code TEXT, error_message TEXT, result_version INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        connection.execute(
            """INSERT INTO jobs(
              id, source_type, source_name, invite_hash, access_token_hash, status,
              created_at, expires_at
            ) VALUES ('legacy', 'upload', 'old.mp4', 'x', 'y', 'ready',
                      '2026-01-01T00:00:00+00:00', '2027-01-01T00:00:00+00:00')"""
        )

    database = Database(settings)
    database.initialize()
    database.initialize()
    row = database.get_job("legacy")
    assert row is not None
    assert row["extraction_mode"] == "visual"
    assert row["asset_status"] == "legacy"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM video_assets").fetchone()[0] == 1

