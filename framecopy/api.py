from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.engine.markdown import regenerate_markdown, remove_legacy_exports, write_readable_markdown
from framecopy.engine.ocr import create_ocr_provider
from framecopy.engine.speech import create_speech_provider
from framecopy.engine.speech_output import regenerate_speech_outputs
from framecopy.errors import FramecopyError
from framecopy.models import ExtractionMode, JobStatus
from framecopy.runner import QueueFacade
from framecopy.secret_store import LocalSecretStore, WX_YUANBAO_COOKIE
from framecopy.sources import WxChannelCloudProvider, extract_wx_channel_url
from framecopy.source_payload import save_link_source
from framecopy.source_payload import load_job_source
from framecopy.storage import delete_job_files, job_paths


class LinkRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    extraction_mode: ExtractionMode = ExtractionMode.VISUAL


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

    @router.get("/providers/speech/status")
    def speech_status() -> dict[str, Any]:
        try:
            provider = create_speech_provider(settings)
            status = provider.check_environment()
        except ValueError as exc:
            return {"available": False, "message": str(exc), "provider": settings.speech_provider}
        return {
            "available": status.available,
            "message": status.message,
            "provider": status.provider,
            "model": status.model,
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
        _require_extraction_environment(payload.extraction_mode, settings)
        job_id, token = database.create_job(
            "wx_channel_link", "weixin.qq.com/sph/…", payload.extraction_mode
        )
        paths = job_paths(settings, job_id)
        save_link_source(settings, paths.root, url)
        queue.enqueue(job_id)
        return {"job_id": job_id, "access_token": token}

    @router.post("/jobs/upload", status_code=202)
    async def create_upload_job(
        file: Annotated[UploadFile, File()],
        extraction_mode: Annotated[ExtractionMode, Form()] = ExtractionMode.VISUAL,
    ) -> dict[str, str]:
        _require_extraction_environment(extraction_mode, settings)
        safe_name = Path(file.filename or "video").name[:255]
        job_id, token = database.create_job("upload", safe_name, extraction_mode)
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
                asset = connection.execute("SELECT asset_id FROM jobs WHERE id=?", (job_id,)).fetchone()
                connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
                if asset and asset["asset_id"]:
                    connection.execute("DELETE FROM video_assets WHERE id=?", (asset["asset_id"],))
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
            "segments": (
                database.get_speech_segments(job_id)
                if row.get("extraction_mode") == ExtractionMode.SPEECH
                else database.get_segments(job_id)
            ),
            "ocr": manifest.get("ocr", {}),
            "speech": manifest.get("speech", {}),
        }

    @router.patch("/jobs/{job_id}/result")
    def update_result(payload: ResultUpdate, job_id: str,
                      x_access_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        row = authorize(job_id, x_access_token)
        if row["status"] != JobStatus.READY:
            raise HTTPException(409, "任务尚未完成")
        mode = ExtractionMode(row.get("extraction_mode") or ExtractionMode.VISUAL)
        updates = [item.model_dump() for item in payload.segments]
        if mode is ExtractionMode.SPEECH:
            database.update_speech_segments(job_id, updates)
        else:
            database.update_segments(job_id, updates)
        paths = job_paths(settings, job_id, create=False)
        if mode is ExtractionMode.SPEECH:
            segments = database.get_speech_segments(job_id)
            regenerate_speech_outputs(paths.output, segments)
        else:
            segments = database.get_segments(job_id)
            regenerate_markdown(paths.output, segments)
        return {"saved": True, "segments": segments}

    @router.post("/jobs/{job_id}/result/restore")
    def restore_result(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        row = authorize(job_id, x_access_token)
        if row["status"] != JobStatus.READY:
            raise HTTPException(409, "任务尚未完成")
        mode = ExtractionMode(row.get("extraction_mode") or ExtractionMode.VISUAL)
        paths = job_paths(settings, job_id, create=False)
        if mode is ExtractionMode.SPEECH:
            database.restore_speech_segments(job_id)
            segments = database.get_speech_segments(job_id)
            regenerate_speech_outputs(paths.output, segments)
        else:
            database.restore_segments(job_id)
            segments = database.get_segments(job_id)
            regenerate_markdown(paths.output, segments)
        return {"restored": True, "segments": segments}

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
        if row["source_type"] != "wx_channel_link":
            raise HTTPException(409, "本地上传视频已按隐私规则删除，请重新选择文件提交")
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
        row = authorize(job_id, x_access_token)
        if filename != "可读文案.md":
            raise HTTPException(404)
        return _readable_download(settings, database, row, job_id)

    @router.get("/jobs/{job_id}/files")
    def download_all(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> FileResponse:
        row = authorize(job_id, x_access_token)
        return _readable_download(settings, database, row, job_id)

    @router.delete("/jobs/{job_id}")
    def delete_job(job_id: str, x_access_token: Annotated[str | None, Header()] = None) -> dict[str, bool]:
        row = authorize(job_id, x_access_token)
        delete_job_files(settings, job_id)
        with database.connect() as connection:
            connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            if row.get("asset_id"):
                connection.execute("DELETE FROM video_assets WHERE id=?", (row["asset_id"],))
        return {"deleted": True}

    return router


def _public_job(row: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "id", "source_type", "source_name", "status", "stage_label", "progress",
        "progress_current", "progress_total", "duration", "width", "height", "fps",
        "created_at", "completed_at", "expires_at", "error_code", "error_message",
        "extraction_mode", "engine_name", "model_name", "asset_status",
        "source_cleanup_status", "source_deleted_at",
    }
    return {key: row.get(key) for key in keys}


def _readable_download(
    settings: Settings,
    database: Database,
    row: dict[str, Any],
    job_id: str,
) -> FileResponse:
    if row["status"] != JobStatus.READY:
        raise HTTPException(409, "任务尚未完成")
    mode = ExtractionMode(row.get("extraction_mode") or ExtractionMode.VISUAL)
    segments = (
        database.get_speech_segments(job_id)
        if mode is ExtractionMode.SPEECH
        else database.get_segments(job_id)
    )
    paths = job_paths(settings, job_id, create=False)
    title = "语音文案" if mode is ExtractionMode.SPEECH else "视频画面文案"
    target = write_readable_markdown(paths.output, title, segments)
    remove_legacy_exports(paths.output)
    (paths.root / "核查资料.zip").unlink(missing_ok=True)
    return FileResponse(
        target,
        media_type="text/markdown; charset=utf-8",
        filename=_download_filename(row, segments),
    )


def _download_filename(row: dict[str, Any], segments: list[dict]) -> str:
    source_name = str(row.get("source_name") or "").strip()
    candidate = ""
    if _has_usable_source_title(row.get("source_type"), source_name):
        candidate = Path(source_name).stem
    if not candidate:
        for segment in segments:
            text = str(segment.get("effective_text") or "").strip()
            if text:
                candidate = _first_spoken_phrase(text)
                break
    safe = _safe_filename_stem(candidate)
    return f"{safe or '可读文案'}.md"


def _has_usable_source_title(source_type: object, source_name: str) -> bool:
    if not source_name or source_name.lower() in {"video", "video.mp4", "source.bin"}:
        return False
    if source_name.startswith(("http://", "https://", "weixin.qq.com/")) or "…" in source_name:
        return False
    return str(source_type or "") == "upload" or Path(source_name).suffix.lower() in {
        ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts",
    }


def _first_spoken_phrase(text: str) -> str:
    phrase = re.split(r"[\n。！？!?;；]", text, maxsplit=1)[0].strip()
    fillers = (
        "哈喽大家好", "大家好", "各位好", "来吧", "各位",
        "我们接着", "接着", "我们继续", "继续",
    )
    changed = True
    while changed and phrase:
        changed = False
        for filler in fillers:
            if phrase.startswith(filler):
                phrase = phrase[len(filler):].lstrip(" ，,：:")
                changed = True
                break
    return phrase


def _safe_filename_stem(value: str, max_length: int = 48) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:max_length].rstrip(" .")


def _require_extraction_environment(mode: ExtractionMode, settings: Settings) -> None:
    if mode is not ExtractionMode.SPEECH:
        return
    try:
        status = create_speech_provider(settings).check_environment()
    except ValueError as exc:
        raise HTTPException(503, detail={
            "code": "SPEECH_ENVIRONMENT_UNAVAILABLE",
            "message": str(exc),
        }) from exc
    if not status.available:
        raise HTTPException(503, detail={
            "code": "SPEECH_ENVIRONMENT_UNAVAILABLE",
            "message": status.message,
        })

