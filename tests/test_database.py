from pathlib import Path

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.models import Segment


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

