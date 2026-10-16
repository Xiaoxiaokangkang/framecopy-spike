from pathlib import Path

from framecopy.config import Settings
from framecopy.source_payload import delete_link_source, load_job_source, save_link_source


def test_link_source_is_encrypted_and_removed(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "test.db",
        secret_key="test-secret",
    )
    job_dir = tmp_path / "jobs" / ("a" * 32)
    (job_dir / "input").mkdir(parents=True)
    url = "https://weixin.qq.com/sph/secret-token"
    save_link_source(settings, job_dir, url)
    stored = (job_dir / "input" / "source.enc").read_bytes()
    assert url.encode() not in stored
    assert load_job_source(settings, job_dir, "wx_channel_link") == url
    delete_link_source(job_dir)
    assert not (job_dir / "input" / "source.enc").exists()
