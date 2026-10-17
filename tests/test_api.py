from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from framecopy.api import _download_filename, create_router
from framecopy.config import Settings
from framecopy.database import Database
from framecopy.engine.speech import SpeechProviderStatus
from framecopy.models import ExtractionMode, JobStatus, Segment
from framecopy.secret_store import LocalSecretStore, WX_YUANBAO_COOKIE


class RecordingQueue:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    def enqueue(self, job_id: str) -> None:
        self.job_ids.append(job_id)


def test_upload_can_be_submitted_without_invite_code(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")
    database = Database(settings)
    database.initialize()
    queue = RecordingQueue()
    app = FastAPI()
    app.include_router(create_router(settings, database, queue))

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("clip.mp4", b"fake-video", "video/mp4")},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["job_id"]
    assert payload["access_token"]
    assert queue.job_ids == [payload["job_id"]]


def test_home_page_has_no_invite_code_field() -> None:
    page = Path("framecopy/web/index.html").read_text(encoding="utf-8")
    script = Path("framecopy/web/static/home.js").read_text(encoding="utf-8")
    assert "invite-code" not in page
    assert "invite_code" not in script
    assert "location.protocol === 'file:'" in page
    assert "http://127.0.0.1:8000/" in page
    assert page.count('name="extraction-mode"') == 2
    assert "「分享」→「复制链接」" in page


def test_result_page_explains_how_to_return_for_another_video() -> None:
    page = Path("framecopy/web/job.html").read_text(encoding="utf-8")
    assert "点击左上角「帧文案」返回首页" in page


def test_wx_credential_is_stored_encrypted_and_never_returned(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")
    database = Database(settings)
    database.initialize()
    app = FastAPI()
    app.include_router(create_router(settings, database, RecordingQueue()))
    cookie = "session_key=secret-value; another=value"

    with TestClient(app) as client:
        saved = client.put("/api/providers/wx-channel/credential", json={"cookie": cookie})
        encrypted_path = tmp_path / "secrets" / f"{WX_YUANBAO_COOKIE}.enc"
        assert encrypted_path.is_file()
        assert cookie.encode("utf-8") not in encrypted_path.read_bytes()
        deleted = client.delete("/api/providers/wx-channel/credential")

    assert saved.status_code == 200
    assert cookie not in saved.text
    assert deleted.json() == {"deleted": True}
    assert LocalSecretStore(settings).load(WX_YUANBAO_COOKIE) is None


class FakeSpeechProvider:
    def __init__(self, available: bool) -> None:
        self.available = available

    def check_environment(self) -> SpeechProviderStatus:
        return SpeechProviderStatus(
            self.available,
            "本地语音识别可用" if self.available else "本地语音识别未安装",
            "fake",
            "test-model",
        )


def test_upload_routes_explicit_speech_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("framecopy.api.create_speech_provider", lambda _: FakeSpeechProvider(True))
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")
    database = Database(settings)
    database.initialize()
    queue = RecordingQueue()
    app = FastAPI()
    app.include_router(create_router(settings, database, queue))

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("clip.mp4", b"fake-video", "video/mp4")},
            data={"extraction_mode": "speech"},
        )

    assert response.status_code == 202
    row = database.get_job(response.json()["job_id"])
    assert row is not None
    assert row["extraction_mode"] == "speech"


def test_unavailable_speech_engine_rejects_before_upload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("framecopy.api.create_speech_provider", lambda _: FakeSpeechProvider(False))
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")
    database = Database(settings)
    database.initialize()
    app = FastAPI()
    app.include_router(create_router(settings, database, RecordingQueue()))

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("private.mp4", b"must-not-be-saved", "video/mp4")},
            data={"extraction_mode": "speech"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SPEECH_ENVIRONMENT_UNAVAILABLE"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert not list((tmp_path / "jobs").glob("*/input/*"))


def test_invalid_extraction_mode_is_rejected(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")
    database = Database(settings)
    database.initialize()
    app = FastAPI()
    app.include_router(create_router(settings, database, RecordingQueue()))

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("clip.mp4", b"fake-video", "video/mp4")},
            data={"extraction_mode": "guess"},
        )

    assert response.status_code == 422


def test_download_returns_one_markdown_with_every_saved_segment(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")
    database = Database(settings)
    database.initialize()
    app = FastAPI()
    app.include_router(create_router(settings, database, RecordingQueue()))
    job_id, token = database.create_job(
        "wx_channel_link", "weixin.qq.com/sph/…", ExtractionMode.VISUAL
    )
    database.replace_segments(job_id, [
        Segment(1, 0, 1, "来吧各位 接着赚点小钱的商业闭环", "来吧各位 接着赚点小钱的商业闭环", "来吧各位 接着赚点小钱的商业闭环", .95, "one.jpg"),
        Segment(2, 2, 3, "原始二", "自动二", "自动二", .30, "two.jpg", True, False),
    ])
    database.update_job(job_id, JobStatus.READY, label="已完成", progress=100)
    paths = tmp_path / "jobs" / job_id
    (paths / "output").mkdir(parents=True, exist_ok=True)
    (paths / "output" / "原始OCR.jsonl").write_text("legacy", encoding="utf-8")
    (paths / "核查资料.zip").write_bytes(b"legacy")

    with TestClient(app) as client:
        response = client.get(
            f"/api/jobs/{job_id}/files",
            headers={"X-Access-Token": token},
        )
        old_file = client.get(
            f"/api/jobs/{job_id}/files/原始OCR.jsonl",
            headers={"X-Access-Token": token},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "赚点小钱的商业闭环.md" in unquote(response.headers["content-disposition"])
    assert "来吧各位 接着赚点小钱的商业闭环" in response.text
    assert "自动二" in response.text
    assert "## 00:02" not in response.text
    assert old_file.status_code == 404
    assert not (paths / "output" / "原始OCR.jsonl").exists()
    assert not (paths / "核查资料.zip").exists()


def test_download_filename_prefers_upload_title_and_removes_unsafe_characters() -> None:
    filename = _download_filename(
        {"source_type": "upload", "source_name": '我的:视频?".mp4'},
        [{"effective_text": "这句不应该被使用"}],
    )
    assert filename == "我的 视频.md"
