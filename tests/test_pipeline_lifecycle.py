import json
from pathlib import Path

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.engine.pipeline import ProcessingPipeline
from framecopy.engine.probe import VideoInfo
from framecopy.errors import error
from framecopy.models import ExtractionMode, SpeechSegment
from framecopy.storage import job_paths


def make_pipeline(tmp_path: Path) -> tuple[ProcessingPipeline, Database, Settings]:
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")
    database = Database(settings)
    database.initialize()
    return ProcessingPipeline(settings, database), database, settings


def create_upload(database: Database, settings: Settings, mode=ExtractionMode.SPEECH) -> tuple[str, Path]:
    job_id, _ = database.create_job("upload", "private.mp4", mode)
    paths = job_paths(settings, job_id)
    source = paths.input / "source.bin"
    source.write_bytes(b"private-video")
    return job_id, source


def test_speech_pipeline_finishes_then_deletes_source_and_work(monkeypatch, tmp_path: Path) -> None:
    pipeline, database, settings = make_pipeline(tmp_path)
    job_id, source = create_upload(database, settings)
    paths = job_paths(settings, job_id)
    monkeypatch.setattr(
        "framecopy.engine.pipeline.probe_video",
        lambda *_: VideoInfo(10, 1280, 720, 25, "h264", source.stat().st_size),
    )

    def fake_speech(current_job_id, _video_path, _info, _cancelled):
        database.replace_speech_segments(
            current_job_id,
            [SpeechSegment(1, 0, 1, "原文", "原文", .9, "zh")],
        )
        (paths.work / "temporary.wav").write_bytes(b"audio")
        (paths.output / "result.md").write_text("结果", encoding="utf-8")
        (paths.output / "manifest.json").write_text(
            '{"source_cleanup":{"status":"pending","deleted_at":null}}',
            encoding="utf-8",
        )

    monkeypatch.setattr(pipeline, "_run_speech", fake_speech)

    pipeline.run(job_id)

    row = database.get_job(job_id)
    assert row is not None
    assert row["status"] == "ready"
    assert row["source_cleanup_status"] == "deleted"
    assert not source.exists()
    assert paths.work.is_dir() and not list(paths.work.iterdir())
    assert (paths.output / "result.md").is_file()
    manifest = json.loads((paths.output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_cleanup"]["status"] == "deleted"
    assert manifest["source_cleanup"]["deleted_at"]


def test_pipeline_failure_deletes_uploaded_source(monkeypatch, tmp_path: Path) -> None:
    pipeline, database, settings = make_pipeline(tmp_path)
    job_id, source = create_upload(database, settings)

    def fail_probe(*_args):
        raise error("MEDIA_CORRUPTED")

    monkeypatch.setattr("framecopy.engine.pipeline.probe_video", fail_probe)
    pipeline.run(job_id)

    row = database.get_job(job_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_code"] == "MEDIA_CORRUPTED"
    assert row["source_cleanup_status"] == "deleted"
    assert not source.exists()


def test_pipeline_cancellation_deletes_uploaded_source(monkeypatch, tmp_path: Path) -> None:
    pipeline, database, settings = make_pipeline(tmp_path)
    job_id, source = create_upload(database, settings)
    monkeypatch.setattr(
        "framecopy.engine.pipeline.probe_video",
        lambda *_: VideoInfo(10, 1280, 720, 25, "h264", source.stat().st_size),
    )
    database.request_cancel(job_id)

    pipeline.run(job_id)

    row = database.get_job(job_id)
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["source_cleanup_status"] == "deleted"
    assert not source.exists()
