from __future__ import annotations

import shutil
from datetime import datetime, timezone

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.engine.markdown import generate_outputs
from framecopy.engine.merge import merge_frames
from framecopy.engine.ocr import OCRProviderError, create_ocr_provider
from framecopy.engine.probe import VideoInfo, probe_video
from framecopy.engine.sampler import extract_candidates
from framecopy.engine.speech import SpeechProviderError, create_speech_provider
from framecopy.engine.speech_output import generate_speech_outputs
from framecopy.errors import FramecopyError, error
from framecopy.models import ExtractionMode, JobStatus, OCRFrame
from framecopy.sources import UploadedFileProvider, WxChannelCloudProvider
from framecopy.source_payload import load_job_source
from framecopy.storage import delete_source_files, job_paths, update_source_cleanup_manifest


class ProcessingPipeline:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    def run(self, job_id: str) -> None:
        row = self.database.get_job(job_id)
        if not row:
            return
        paths = job_paths(self.settings, job_id)
        cancelled = lambda: self.database.is_cancel_requested(job_id)
        mode = ExtractionMode(row.get("extraction_mode") or ExtractionMode.VISUAL)
        try:
            source = load_job_source(self.settings, paths.root, row["source_type"])
            self.database.update_asset(job_id, status="acquiring", cleanup_status="pending")
            self._stage(
                job_id,
                JobStatus.RESOLVING_LINK if row["source_type"] == "wx_channel_link" else JobStatus.VALIDATING,
                "正在解析视频来源" if row["source_type"] == "wx_channel_link" else "正在校验视频",
                3,
            )
            provider = (
                WxChannelCloudProvider(self.settings.wx_provider_command)
                if row["source_type"] == "wx_channel_link"
                else UploadedFileProvider()
            )
            video_path = provider.acquire(
                source,
                paths.root,
                lambda label, value: self._stage(job_id, JobStatus.DOWNLOADING, label, value),
                cancelled,
            )
            self.database.update_asset(job_id, status="validating", local_path=str(video_path))
            self._check_cancelled(job_id)
            self._stage(job_id, JobStatus.VALIDATING, "正在校验视频", 12)
            info = probe_video(video_path, self.settings)
            now = datetime.now(timezone.utc).isoformat()
            self.database.update_asset(
                job_id,
                status="ready_for_processing",
                size=info.size,
                duration=info.duration,
                media_codec=info.codec,
                width=info.width,
                height=info.height,
                fps=info.fps,
                validated_at=now,
            )
            self.database.update_job(
                job_id,
                JobStatus.PREPARING if mode is ExtractionMode.SPEECH else JobStatus.ANALYZING,
                label="正在准备语音提取" if mode is ExtractionMode.SPEECH else "正在分析画面",
                progress=18,
                duration=info.duration,
                input_size=info.size,
                media_codec=info.codec,
                width=info.width,
                height=info.height,
                fps=info.fps,
                processing_started_at=now,
            )
            self.database.update_asset(job_id, status="in_use")
            if mode is ExtractionMode.SPEECH:
                self._run_speech(job_id, video_path, info, cancelled)
            else:
                self._run_visual(job_id, video_path, info, cancelled)
            self._stage(job_id, JobStatus.CLEANING_SOURCE, "正在清理临时视频", 98)
            cleaned_at = self._cleanup_source(job_id, preserve_link=False)
            if cleaned_at:
                self.database.update_job(
                    job_id,
                    JobStatus.READY,
                    label="已完成，临时视频已清理",
                    progress=100,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                self.database.update_job(
                    job_id,
                    JobStatus.READY,
                    label="已完成，但临时视频清理失败",
                    progress=100,
                    error_code="SOURCE_CLEANUP_FAILED",
                    error_message=error("SOURCE_CLEANUP_FAILED").public_message,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
        except InterruptedError:
            self._cleanup_source(job_id, preserve_link=False)
            self.database.update_job(
                job_id,
                JobStatus.CANCELLED,
                label="任务已取消，临时文件已清理",
                progress=0,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except FramecopyError as exc:
            self._fail(job_id, row, exc.code, exc.public_message)
        except OCRProviderError as exc:
            failure = error("OCR_INITIALIZATION_FAILED", detail=str(exc)[:500])
            self._fail(job_id, row, failure.code, failure.public_message)
        except SpeechProviderError:
            failure = error("TRANSCRIPTION_FAILED")
            self._fail(job_id, row, failure.code, failure.public_message)
        except Exception as exc:
            self._fail(job_id, row, "INTERNAL_ERROR", f"处理失败，请记录任务编号（{type(exc).__name__}）")

    def _run_visual(self, job_id: str, video_path, info: VideoInfo, cancelled) -> None:
        paths = job_paths(self.settings, job_id)
        candidates = extract_candidates(
            video_path,
            paths.work / "candidates",
            progress=lambda current, total: self.database.update_job(
                job_id,
                JobStatus.EXTRACTING_FRAMES,
                label="正在筛选候选画面",
                progress=20 + int(25 * current / max(total, 1)),
                current=current,
                total=total,
                heartbeat_at=datetime.now(timezone.utc).isoformat(),
            ),
            cancelled=cancelled,
        )
        self._check_cancelled(job_id)
        ocr = create_ocr_provider(self.settings)
        ocr_status = ocr.check_environment()
        if not ocr_status.available:
            raise error("OCR_INITIALIZATION_FAILED", detail=ocr_status.message)
        frames: list[OCRFrame] = []
        total = len(candidates)
        for index, candidate in enumerate(candidates, 1):
            self._check_cancelled(job_id)
            result = ocr.recognize(candidate.path, cancelled)
            evidence_path = paths.evidence / candidate.path.name
            shutil.copy2(candidate.path, evidence_path)
            frames.append(OCRFrame(
                timestamp=candidate.timestamp,
                # Persist only the evidence filename; absolute local paths must not leak into exports.
                image_path=evidence_path.name,
                lines=result.lines,
                sharpness=candidate.sharpness,
                image_hash=candidate.perceptual_hash,
                provider=result.provider.name,
                provider_version=result.provider.version,
                model=result.provider.model,
                provider_revision=result.provider.revision,
                languages=list(result.provider.languages),
                duration_ms=result.duration_ms,
                fallback_from=result.fallback_from,
                fallback_reason=result.fallback_reason,
            ))
            self.database.update_job(
                job_id,
                JobStatus.RECOGNIZING,
                label=f"正在识别文字 {index}/{total}",
                progress=45 + int(35 * index / max(total, 1)),
                current=index,
                total=total,
                heartbeat_at=datetime.now(timezone.utc).isoformat(),
            )
        self._stage(job_id, JobStatus.MERGING, "正在合并重复内容", 84)
        segments = merge_frames(frames)
        self.database.replace_segments(job_id, segments)
        self._stage(job_id, JobStatus.GENERATING, "正在生成画面文案", 94)
        generate_outputs(paths.output, video_path, info, frames, segments)
        self.database.update_job(
            job_id,
            JobStatus.GENERATING,
            label="画面文案已生成",
            progress=96,
            engine_name=" + ".join(sorted({frame.provider for frame in frames})) or "unknown",
            model_name=" + ".join(sorted({frame.model for frame in frames})) or "unknown",
        )

    def _run_speech(self, job_id: str, video_path, info: VideoInfo, cancelled) -> None:
        paths = job_paths(self.settings, job_id)
        provider = create_speech_provider(self.settings)
        status = provider.check_environment()
        if not status.available:
            raise error("SPEECH_ENVIRONMENT_UNAVAILABLE", detail=status.message)

        def progress(label: str, value: int) -> None:
            status_value = JobStatus.EXTRACTING_AUDIO if "音轨" in label else JobStatus.TRANSCRIBING
            self._stage(job_id, status_value, label, value)

        result = provider.transcribe(video_path, paths.work / "speech", progress, cancelled)
        self._check_cancelled(job_id)
        self.database.replace_speech_segments(job_id, result.segments)
        self._stage(job_id, JobStatus.GENERATING, "正在生成逐字稿和字幕", 92)
        generate_speech_outputs(paths.output, video_path, info, result)
        self.database.update_job(
            job_id,
            JobStatus.GENERATING,
            label="语音文案已生成",
            progress=96,
            engine_name=result.provider,
            model_name=result.model,
        )

    def _fail(self, job_id: str, row: dict, code: str, message: str) -> None:
        preserve_link = row["source_type"] == "wx_channel_link" and int(row.get("attempt_count") or 0) < 1
        cleaned = self._cleanup_source(job_id, preserve_link=preserve_link)
        if not cleaned:
            message = f"{message}；临时视频清理失败，请检查本机任务目录。"
        self.database.update_job(
            job_id,
            JobStatus.FAILED,
            label=message,
            progress=0,
            error_code=code,
            error_message=message,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    def _cleanup_source(self, job_id: str, *, preserve_link: bool) -> str | None:
        paths = job_paths(self.settings, job_id, create=False)
        try:
            self.database.update_asset(job_id, status="deleting")
            delete_source_files(self.settings, job_id, preserve_link=preserve_link)
            deleted_at = datetime.now(timezone.utc).isoformat()
            self.database.update_asset(
                job_id,
                status="metadata_retained" if preserve_link else "deleted",
                local_path=None,
                cleanup_status="deleted",
                last_cleanup_error=None,
                deleted_at=deleted_at,
            )
            try:
                update_source_cleanup_manifest(paths.output, "deleted", deleted_at)
            except OSError:
                pass
            return deleted_at
        except Exception as exc:
            self.database.record_asset_cleanup_failure(job_id, type(exc).__name__)
            try:
                update_source_cleanup_manifest(paths.output, "failed", None)
            except OSError:
                pass
            return None

    def _stage(self, job_id: str, status: JobStatus, label: str, progress: int) -> None:
        self.database.update_job(
            job_id,
            status,
            label=label,
            progress=progress,
            heartbeat_at=datetime.now(timezone.utc).isoformat(),
        )

    def _check_cancelled(self, job_id: str) -> None:
        if self.database.is_cancel_requested(job_id):
            raise InterruptedError("cancelled")
