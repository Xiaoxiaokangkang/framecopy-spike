import json
from pathlib import Path

from framecopy.config import Settings
from framecopy.storage import delete_source_files, job_paths, update_source_cleanup_manifest


def test_delete_source_files_preserves_results_and_optional_link(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")
    paths = job_paths(settings, "a" * 32)
    (paths.input / "source.bin").write_bytes(b"video")
    (paths.input / "source.enc").write_bytes(b"encrypted-link")
    (paths.work / "speech.wav").write_bytes(b"audio")
    (paths.evidence / "frame.jpg").write_bytes(b"frame")
    (paths.output / "result.md").write_text("result", encoding="utf-8")

    delete_source_files(settings, "a" * 32, preserve_link=True)

    assert not (paths.input / "source.bin").exists()
    assert (paths.input / "source.enc").is_file()
    assert paths.work.is_dir() and not list(paths.work.iterdir())
    assert (paths.evidence / "frame.jpg").is_file()
    assert (paths.output / "result.md").is_file()


def test_cleanup_status_is_written_to_manifest(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    manifest = output / "manifest.json"
    manifest.write_text('{"source_cleanup":{"status":"pending"}}', encoding="utf-8")

    update_source_cleanup_manifest(output, "deleted", "2026-10-17T00:00:00+00:00")

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["source_cleanup"] == {
        "status": "deleted",
        "deleted_at": "2026-10-17T00:00:00+00:00",
    }
