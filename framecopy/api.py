from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.engine.markdown import regenerate_markdown
from framecopy.engine.ocr import create_ocr_provider
from framecopy.errors import FramecopyError
from framecopy.models import JobStatus
from framecopy.runner import QueueFacade
from framecopy.secret_store import LocalSecretStore, WX_YUANBAO_COOKIE
from framecopy.sources import WxChannelCloudProvider, extract_wx_channel_url
from framecopy.source_payload import save_link_source
from framecopy.source_payload import load_job_source
from framecopy.storage import delete_job_files, job_paths


class LinkRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class WxCredentialRequest(BaseModel):
    cookie: str = Field(min_length=20, max_length=65_536)


class SegmentUpdate(BaseModel):
    sequence: int
    text: str = Field(max_length=20_000)


class ResultUpdate(BaseModel):
    segments: list[SegmentUpdate]


def create_router(settings: Settings, database: Database, queue: QueueFacade) -> APIRouter:
    router = APIRouter(prefix="/api")
    secret_store = LocalSecretStore(settings)

    def authorize(job_id: str, token: str | None) -> dict[str, Any]:
        if not token:
            raise HTTPException(401, "缺少任务访问令牌")
        row = database.authorize(job_id, token)
        if not row:
            raise HTTPException(403, "任务访问令牌无效")
        return row

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/providers/wx-channel/status")
    def wx_status() -> dict[str, Any]:
        status = WxChannelCloudProvider(settings.wx_provider_command).check_environment()
        return {
            "available": status.available,
            "message": status.message,
            "mode": status.mode,
            "direct_configured": secret_store.has(WX_YUANBAO_COOKIE),
        }

    @router.put("/providers/wx-channel/credential")
    def save_wx_credential(payload: WxCredentialRequest) -> dict[str, bool]:
        cookie = payload.cookie.strip()
        if cookie.lower().startswith("cookie:"):
            cookie = cookie.split(":", 1)[1].strip()
        if "\r" in cookie or "\n" in cookie or "=" not in cookie:
            raise HTTPException(422, "Cookie 格式不正确，请粘贴完整的 Cookie 请求头内容")
        secret_store.save(WX_YUANBAO_COOKIE, cookie)
        return {"saved": True}

    @router.delete("/providers/wx-channel/credential")
    def delete_wx_credential() -> dict[str, bool]:
        return {"deleted": secret_store.delete(WX_YUANBAO_COOKIE)}

    @router.get("/providers/ocr/status")
    def ocr_status() -> dict[str, Any]:
        provider = create_ocr_provider(settings)
        status = provider.check_environment()
        return {
            "available": status.available,
            "message": status.message,
            "provider": provider.info.to_dict(),
            "configured": settings.ocr_provider,
        }

    @router.post("/jobs/link", status_code=202)
    def create_link_job(payload: LinkRequest) -> dict[str, str]:
        try:
            url = extract_wx_channel_url(payload.text)
        except FramecopyError as exc:
            raise HTTPException(422, detail={"code": exc.code, "message": exc.public_message})
        provider_status = WxChannelCloudProvider(settings.wx_provider_command).check_environment()
        if not provider_status.available:
            raise HTTPException(503, detail={
                "code": "LINK_ENVIRONMENT_UNAVAILABLE",
                "message": provider_status.message,
                "fallback": "upload",
            })
        job_id, token = database.create_job("wx_channel_link", "weixin.qq.com/sph/…")
        paths = job_paths(settings, job_id)
        save_link_source(settings, paths.root, url)
        queue.enqueue(job_id)
        return {"job_id": job_id, "access_token": token}

    @router.post("/jobs/upload", status_code=202)
    async def create_upload_job(
        file: Annotated[UploadFile, File()],
    ) -> dict[str, str]:
        safe_name = Path(file.filename or "video").name[:255]
        job_id, token = database.create_job("upload", safe_name)
        paths = job_paths(settings, job_id)
        target = paths.input / "source.bin"
        total = 0
        try:
            with target.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.max_upload_bytes:
                        raise HTTPException(413, "视频超过 200 MB 限制")
                    stream.write(chunk)
        except Exception:
            delete_job_files(settings, job_id)
            with database.connect() as connection:
                connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            raise
        finally:
            await file.close()
        queue.enqueue(job_id)
        return {"job_id": job_id, "access_token": token}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        row = authorize(job_id, x_access_token)
        return _public_job(row)

    @router.get("/jobs/{job_id}/result")
    def get_result(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        row = authorize(job_id, x_access_token)
        if row["status"] != JobStatus.READY:
            raise HTTPException(409, "任务尚未完成")
        paths = job_paths(settings, job_id, create=False)
        manifest: dict[str, Any] = {}
        manifest_path = paths.output / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}
        return {
            "job": _public_job(row),
            "segments": database.get_segments(job_id),
            "ocr": manifest.get("ocr", {}),
        }

    @router.patch("/jobs/{job_id}/result")
    def update_result(payload: ResultUpdate, job_id: str,
                      x_access_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        row = authorize(job_id, x_access_token)
        if row["status"] != JobStatus.READY:
            raise HTTPException(409, "任务尚未完成")
        database.update_segments(job_id, [item.model_dump() for item in payload.segments])
        paths = job_paths(settings, job_id, create=False)
        regenerate_markdown(paths.output, database.get_segments(job_id))
        return {"saved": True, "segments": database.get_segments(job_id)}

    @router.post("/jobs/{job_id}/result/restore")
    def restore_result(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        authorize(job_id, x_access_token)
        database.restore_segments(job_id)
        paths = job_paths(settings, job_id, create=False)
        regenerate_markdown(paths.output, database.get_segments(job_id))
        return {"restored": True, "segments": database.get_segments(job_id)}

    @router.post("/jobs/{job_id}/cancel", status_code=202)
    def cancel_job(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> dict[str, bool]:
        row = authorize(job_id, x_access_token)
        if row["status"] in {JobStatus.READY, JobStatus.FAILED, JobStatus.CANCELLED}:
            raise HTTPException(409, "当前任务状态不能取消")
        database.request_cancel(job_id)
        return {"cancel_requested": True}

    @router.post("/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> dict[str, bool]:
        row = authorize(job_id, x_access_token)
        if row["status"] != JobStatus.FAILED or int(row["attempt_count"] or 0) >= 1:
            raise HTTPException(409, "当前任务不能重试或已达到重试上限")
        paths = job_paths(settings, job_id, create=False)
        try:
            load_job_source(settings, paths.root, row["source_type"])
        except (FileNotFoundError, ValueError):
            raise HTTPException(409, "任务来源已清理，请重新提交")
        database.update_job(
            job_id, JobStatus.QUEUED, label="等待重新处理", progress=0,
            attempt_count=int(row["attempt_count"] or 0) + 1,
            cancel_requested_at=None, completed_at=None, error_code=None, error_message=None,
        )
        queue.enqueue(job_id)
        return {"queued": True}

    @router.get("/jobs/{job_id}/evidence/{filename}")
    def evidence(job_id: str, filename: str,
                 x_access_token: Annotated[str | None, Header()] = None) -> FileResponse:
        authorize(job_id, x_access_token)
        if Path(filename).name != filename:
            raise HTTPException(404)
        path = job_paths(settings, job_id, create=False).evidence / filename
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @router.get("/jobs/{job_id}/files/{filename}")
    def download_file(job_id: str, filename: str,
                      x_access_token: Annotated[str | None, Header()] = None) -> FileResponse:
        authorize(job_id, x_access_token)
        allowed = {
            "视频文案-纯净版.md", "视频文案-带时间戳.md", "视频文案-核查说明.md",
            "原始OCR.jsonl", "manifest.json",
        }
        if filename not in allowed:
            raise HTTPException(404)
        path = job_paths(settings, job_id, create=False).output / filename
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, filename=filename)

    @router.get("/jobs/{job_id}/files")
    def download_all(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> FileResponse:
        authorize(job_id, x_access_token)
        paths = job_paths(settings, job_id, create=False)
        target = paths.root / "核查资料.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for directory in (paths.output, paths.evidence):
                if directory.exists():
                    for path in directory.rglob("*"):
                        if path.is_file():
                            archive.write(path, path.relative_to(paths.root))
        return FileResponse(target, filename="视频文案-核查资料.zip")

    @router.delete("/jobs/{job_id}")
    def delete_job(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> dict[str, bool]:
        authorize(job_id, x_access_token)
        delete_job_files(settings, job_id)
        with database.connect() as connection:
            connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return {"deleted": True}

    return router


def _public_job(row: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "id", "source_type", "source_name", "status", "stage_label", "progress",
        "progress_current", "progress_total", "duration", "width", "height", "fps",
        "created_at", "completed_at", "expires_at", "error_code", "error_message",
    }
    return {key: row.get(key) for key in keys}

