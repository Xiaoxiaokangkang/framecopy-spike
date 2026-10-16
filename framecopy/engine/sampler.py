from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


@dataclass(frozen=True)
class CandidateFrame:
    timestamp: float
    path: Path
    sharpness: float
    perceptual_hash: str


def _dhash(gray: np.ndarray) -> str:
    resized = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    difference = resized[:, 1:] > resized[:, :-1]
    value = sum(int(bit) << index for index, bit in enumerate(difference.flatten()))
    return f"{value:016x}"


def _hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def extract_candidates(
    video_path: Path,
    output_dir: Path,
    *,
    progress: Callable[[int, int], None],
    cancelled: Callable[[], bool],
    scan_interval: float = 0.35,
    min_sharpness: float = 20.0,
    min_hash_distance: int = 5,
    max_candidate_gap: float = 0.8,
    burst_followup_frames: int = 2,
) -> list[CandidateFrame]:
    """低成本扫描 + 清晰度/感知哈希去重。

    变化点补帧会在哈希显著变化时额外保留当前帧；精细文字区域筛选由 OCR 层完成。
    """
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError("video cannot be opened")
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(fps * scan_interval))
    total = max(1, frame_count // step)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[CandidateFrame] = []
    last_hash: str | None = None
    last_candidate_timestamp: float | None = None
    burst_remaining = 0
    index = 0
    sampled = 0
    try:
        while True:
            if cancelled():
                raise InterruptedError("cancelled")
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            frame_hash = _dhash(gray)
            changed = last_hash is None or _hamming(last_hash, frame_hash) >= min_hash_distance
            if changed:
                burst_remaining = max(burst_remaining, burst_followup_frames)
            timestamp = index / fps
            periodic_stable_sample = (
                last_candidate_timestamp is None
                or timestamp - last_candidate_timestamp >= max_candidate_gap
            )
            if sharpness >= min_sharpness and (changed or periodic_stable_sample or burst_remaining > 0):
                filename = f"{timestamp:010.3f}.jpg"
                target = output_dir / filename
                cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                candidates.append(CandidateFrame(timestamp, target, sharpness, frame_hash))
                last_hash = frame_hash
                last_candidate_timestamp = timestamp
            if burst_remaining > 0:
                burst_remaining -= 1
            sampled += 1
            progress(sampled, total)
            index += step
            if frame_count and index >= frame_count:
                break
    finally:
        capture.release()
    return candidates

