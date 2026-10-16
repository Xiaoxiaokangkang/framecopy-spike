from __future__ import annotations

import importlib.metadata
import time
from pathlib import Path
from typing import Any

from PIL import Image

from framecopy.engine.ocr.base import (
    CancelCheck,
    OCRProviderError,
    OCRProviderInfo,
    OCRProviderStatus,
    OCRResult,
)
from framecopy.models import OCRLine


class PaddleOCRProvider:
    def __init__(self) -> None:
        self._engine: Any = None
        try:
            version = importlib.metadata.version("paddleocr")
        except importlib.metadata.PackageNotFoundError:
            version = "not-installed"
        self._info = OCRProviderInfo(
            name="paddleocr",
            version=version,
            model="PP-OCRv5_mobile_det+PP-OCRv5_mobile_rec",
            languages=("ch",),
        )

    @property
    def info(self) -> OCRProviderInfo:
        return self._info

    def check_environment(self) -> OCRProviderStatus:
        if self._info.version == "not-installed":
            return OCRProviderStatus(False, "PaddleOCR 尚未安装")
        return OCRProviderStatus(True, "PaddleOCR 可用", {"version": self._info.version})

    def initialize(self) -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OCRProviderError("请安装项目的 ocr 可选依赖") from exc
        try:
            self._engine = PaddleOCR(
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device="cpu",
            )
        except Exception as exc:
            raise OCRProviderError(f"PaddleOCR 初始化失败：{type(exc).__name__}") from exc

    def recognize(self, image_path: Path, cancelled: CancelCheck | None = None) -> OCRResult:
        if cancelled and cancelled():
            raise InterruptedError("cancelled")
        if self._engine is None:
            self.initialize()
        started = time.monotonic()
        try:
            results = self._engine.predict(str(image_path))
        except AttributeError:
            results = self._engine.ocr(str(image_path), cls=False)
        except Exception as exc:
            raise OCRProviderError(f"PaddleOCR 识别失败：{type(exc).__name__}") from exc
        width, height = Image.open(image_path).size
        return OCRResult(
            lines=_normalize_results(results, width=width, height=height),
            provider=self.info,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )


def _normalize_results(results: Any, *, width: int = 1, height: int = 1) -> list[OCRLine]:
    lines: list[OCRLine] = []
    for result in results or []:
        payload = getattr(result, "json", None)
        if callable(payload):
            payload = payload()
        if isinstance(payload, dict):
            content = payload.get("res", payload)
            texts = content.get("rec_texts", [])
            scores = content.get("rec_scores", [])
            boxes = content.get("rec_boxes", [])
            for text, score, box in zip(texts, scores, boxes):
                coords = [float(value) for value in list(box)[:4]]
                lines.append(OCRLine(str(text), float(score), _normalized_box(coords, width, height)))
            continue
        legacy = result if isinstance(result, list) else []
        for item in legacy:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            box, recognition = item
            if not isinstance(recognition, (list, tuple)) or len(recognition) < 2:
                continue
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            coords = [min(xs), min(ys), max(xs), max(ys)]
            lines.append(OCRLine(str(recognition[0]), float(recognition[1]), _normalized_box(coords, width, height)))
    return _reading_order(lines)


def _normalized_box(box: list[float], width: int, height: int) -> list[float]:
    if len(box) < 4:
        return [0.0, 0.0, 0.0, 0.0]
    divisor_x = float(max(1, width))
    divisor_y = float(max(1, height))
    return [
        max(0.0, min(1.0, box[0] / divisor_x)),
        max(0.0, min(1.0, box[1] / divisor_y)),
        max(0.0, min(1.0, box[2] / divisor_x)),
        max(0.0, min(1.0, box[3] / divisor_y)),
    ]


def _reading_order(lines: list[OCRLine]) -> list[OCRLine]:
    """Order normalized boxes from top to bottom and left to right."""
    if len(lines) < 2:
        return lines
    heights = [max(0.001, line.bbox[3] - line.bbox[1]) for line in lines]
    line_tolerance = max(0.008, sorted(heights)[len(heights) // 2] * 0.55)
    rows: list[list[OCRLine]] = []
    for line in sorted(lines, key=lambda item: ((item.bbox[1] + item.bbox[3]) / 2, item.bbox[0])):
        center_y = (line.bbox[1] + line.bbox[3]) / 2
        for row in rows:
            row_center = sum((item.bbox[1] + item.bbox[3]) / 2 for item in row) / len(row)
            if abs(center_y - row_center) <= line_tolerance:
                row.append(line)
                break
        else:
            rows.append([line])
    output: list[OCRLine] = []
    for row in rows:
        output.extend(sorted(row, key=lambda item: item.bbox[0]))
    return output
