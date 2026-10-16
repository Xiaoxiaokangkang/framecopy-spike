from __future__ import annotations

import shutil
from datetime import datetime, timezone

from framecopy.config import Settings
from framecopy.database import Database
from framecopy.engine.markdown import generate_outputs
from framecopy.engine.merge import merge_frames
from framecopy.engine.ocr import OCRProviderError, create_ocr_provider
from framecopy.engine.probe import probe_video
from framecopy.engine.sampler import extract_candidates
from framecopy.errors import FramecopyError, error
from framecopy.models import JobStatus, OCRFrame
from framecopy.sources import UploadedFileProvider, WxChannelCloudProvider
from framecopy.source_payload import delete_link_source, load_job_source
from framecopy.storage import job_paths


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
        try:
            source = load_job_source(self.settings, paths.root, row["source_type"])
            self._stage(job_id, JobStatus.RESOLVING_LINK if row["source_type"] == "wx_channel_link" else JobStatus.VALIDATING,
                        "正在解析视频来源" if row["source_type"] == "wx_channel_link" else "正在校验视频", 3)
            provider = (
                WxChannelCloudProvider(self.settings.wx_provider_command)
                if row["source_type"] == "wx_channel_link"
                else UploadedFileProvider()
            )
            video_path = provider.acquire(
                source, paths.root,
                lambda label, value: self._stage(job_id, JobStatus.DOWNLOADING, label, value),
                cancelled,
            )
            if row["source_type"] == "wx_channel_link":
                delete_link_source(paths.root)
            self._check_cancelled(job_id)
            self._stage(job_id, JobStatus.VALIDATING, "正在校验视频", 12)
            info = probe_video(video_path, self.settings)
            self.database.update_job(
                job_id, JobStatus.ANALYZING, label="正在分析画面", progress=18,
                duration=info.duration, input_size=info.size, media_codec=info.codec,
                width=info.width, height=info.height, fps=info.fps,
                processing_started_at=datetime.now(timezone.utc).isoformat(),
            )
            candidates = extract_candidates(
                video_path, paths.work / "candidates",
                progress=lambda current, total: self.database.update_job(
                    job_id, JobStatus.EXTRACTING_FRAMES, label="正在筛选候选画面", progress=20 + int(25 * current / max(total, 1)),
                    current=current, total=total, heartbeat_at=datetime.now(timezone.utc).isoformat(),
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
                    image_path=str(evidence_path),
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
                    job_id, JobStatus.RECOGNIZING,
                    label=f"正在识别文字 {index}/{total}", progress=45 + int(35 * index / max(total, 1)),
                    current=index, total=total, heartbeat_at=datetime.now(timezone.utc).isoformat(),
                )
            self._stage(job_id, JobStatus.MERGING, "正在合并重复内容", 84)
            segments = merge_frames(frames)
            if not segments:
                self.database.replace_segments(job_id, [])
                generate_outputs(paths.output, video_path, info, frames, [])
                self.database.update_job(
                    job_id, JobStatus.READY, label="处理完成，未检测到有效文字", progress=100,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                return
            self.database.replace_segments(job_id, segments)
            self._stage(job_id, JobStatus.GENERATING, "正在生成 Markdown", 94)
            generate_outputs(paths.output, video_path, info, frames, segments)
            shutil.rmtree(paths.work, ignore_errors=True)
            paths.work.mkdir(parents=True, exist_ok=True)
            self.database.update_job(
                job_id, JobStatus.READY, label="已完成", progress=100,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except InterruptedError:
            self.database.update_job(job_id, JobStatus.CANCELLED, label="任务已取消", progress=0,
                                     completed_at=datetime.now(timezone.utc).isoformat())
            shutil.rmtree(paths.work, ignore_errors=True)
        except FramecopyError as exc:
            self.database.update_job(
                job_id, JobStatus.FAILED, label=exc.public_message, progress=0,
                error_code=exc.code, error_message=exc.public_message,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except OCRProviderError as exc:
            failure = error("OCR_INITIALIZATION_FAILED", detail=str(exc)[:500])
            self.database.update_job(
                job_id, JobStatus.FAILED, label=failure.public_message, progress=0,
                error_code=failure.code, error_message=failure.public_message,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            self.database.update_job(
                job_id, JobStatus.FAILED, label="处理失败，请记录任务编号", progress=0,
                error_code="INTERNAL_ERROR", error_message=type(exc).__name__,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    def _stage(self, job_id: str, status: JobStatus, label: str, progress: int) -> None:
        self.database.update_job(job_id, status, label=label, progress=progress,
                                 heartbeat_at=datetime.now(timezone.utc).isoformat())

    def _check_cancelled(self, job_id: str) -> None:
        if self.database.is_cancel_requested(job_id):
            raise InterruptedError("cancelled")
