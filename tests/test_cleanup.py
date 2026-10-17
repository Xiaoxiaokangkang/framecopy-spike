from datetime import datetime, timedelta, timezone
from pathlib import Path

from framecopy.cleanup import CleanupService
from framecopy.config import Settings
from framecopy.database import Database
from framecopy.storage import job_paths


def make_service(tmp_path: Path) -> tuple[CleanupService, Database, Settings]:
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "test.db",
        speech_timeout_seconds=60,
    )
    database = Database(settings)
    database.initialize()
    return CleanupService(settings, database), database, settings


def test_cleanup_recovers_abandoned_job_and_deletes_private_source(tmp_path: Path) -> None:
    service, database, settings = make_service(tmp_path)
    job_id, _ = database.create_job("upload", "private.mp4")
    paths = job_paths(settings, job_id)
    (paths.input / "source.bin").write_bytes(b"private-video")
    (paths.work / "temporary.wav").write_bytes(b"temporary-audio")
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with database.connect() as connection:
        connection.execute("UPDATE jobs SET created_at=? WHERE id=?", (old, job_id))

    summary = service.run_once()

    row = database.get_job(job_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_code"] == "INTERRUPTED_RECOVERED"
    assert row["source_cleanup_status"] == "deleted"
    assert not (paths.input / "source.bin").exists()
    assert not (paths.work / "temporary.wav").exists()
    assert summary["recovered_jobs"] == 1


def test_cleanup_failure_increments_attempt_count(monkeypatch, tmp_path: Path) -> None:
    service, database, _ = make_service(tmp_path)
    job_id, _ = database.create_job("upload", "private.mp4")
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with database.connect() as connection:
        connection.execute("UPDATE jobs SET created_at=? WHERE id=?", (old, job_id))

    def fail_delete(*_args, **_kwargs):
        raise OSError("simulated")

    monkeypatch.setattr("framecopy.cleanup.delete_source_files", fail_delete)
    service.run_once()
    service.run_once()

    row = database.get_job(job_id)
    assert row is not None
    assert row["asset_status"] == "delete_failed"
    with database.connect() as connection:
        attempts = connection.execute(
            "SELECT cleanup_attempts FROM video_assets WHERE id=?", (row["asset_id"],)
        ).fetchone()[0]
    assert attempts == 2
