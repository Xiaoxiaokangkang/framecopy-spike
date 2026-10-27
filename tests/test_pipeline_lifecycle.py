import json
from pathlib import Path

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.engine.pipeline import ProcessingPipeline
from framecopy.engine.probe import VideoInfo
from framecopy.errors import error
from framecopy.models import ExtractionMode, SpeechSegment
from framecopy.storage import job_paths
from framecopy.source_payload import save_bilibili_source
from framecopy.sources.base import AcquiredMedia


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
        "framecopy.engine.pipeline.probe_media",
        lambda *_args, **_kwargs: VideoInfo(10, 1280, 720, 25, "h264", source.stat().st_size),
    )

    def fake_speech(current_job_id, _video_path, _info, _cancelled, **_kwargs):
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

    def fail_probe(*_args, **_kwargs):
        raise error("MEDIA_CORRUPTED")

    monkeypatch.setattr("framecopy.engine.pipeline.probe_media", fail_probe)
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
        "framecopy.engine.pipeline.probe_media",
        lambda *_args, **_kwargs: VideoInfo(10, 1280, 720, 25, "h264", source.stat().st_size),
    )
    database.request_cancel(job_id)

    pipeline.run(job_id)

    row = database.get_job(job_id)
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["source_cleanup_status"] == "deleted"
    assert not source.exists()


def test_bilibili_published_subtitle_bypasses_whisper_and_is_cleaned(
    monkeypatch, tmp_path: Path
) -> None:
    pipeline, database, settings = make_pipeline(tmp_path)
    job_id, _ = database.create_job(
        "bilibili_link", "B站标题", ExtractionMode.SPEECH
    )
    paths = job_paths(settings, job_id)
    save_bilibili_source(settings, paths.root, {
        "url": "https://www.bilibili.com/video/BV1TEST?p=1",
        "transcript_source": "subtitle",
        "language": "zh",
    })

    class FakeBilibiliProvider:
        def __init__(self, _settings):
            pass

        def acquire(self, _source, _job_dir, progress, _cancelled):
            subtitle = paths.input / "subtitle.vtt"
            subtitle.write_text("published", encoding="utf-8")
            progress("B站发布字幕获取完成", 18)
            return AcquiredMedia(
                path=subtitle,
                kind="subtitle",
                title="B站标题",
                duration=12,
                transcript_segments=(
                    SpeechSegment(1, 0, 2, "发布字幕", "发布字幕", 1.0, "zh"),
                ),
                transcript_provider="bilibili_subtitle",
                transcript_provider_version="test",
                transcript_model="published-subtitle",
                transcript_language="zh",
            )

    monkeypatch.setattr("framecopy.engine.pipeline.BilibiliProvider", FakeBilibiliProvider)
    monkeypatch.setattr(
        "framecopy.engine.pipeline.create_speech_provider",
        lambda _: (_ for _ in ()).throw(AssertionError("published subtitle must bypass Whisper")),
    )

    pipeline.run(job_id)

    row = database.get_job(job_id)
    assert row is not None
    assert row["status"] == "ready"
    assert row["source_cleanup_status"] == "deleted"
    assert database.get_speech_segments(job_id)[0]["clean_text"] == "发布字幕"
    assert not (paths.input / "source.enc").exists()
    assert not (paths.input / "subtitle.vtt").exists()
