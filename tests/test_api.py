from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from framecopy.api import create_router
from framecopy.config import Settings
from framecopy.database import Database
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
