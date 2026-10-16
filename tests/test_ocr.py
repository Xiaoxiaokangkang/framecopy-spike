from pathlib import Path

from framecopy.engine.ocr import FallbackOCRProvider, _reading_order
from framecopy.engine.ocr.base import OCRProviderError, OCRProviderInfo, OCRProviderStatus, OCRResult
from framecopy.models import OCRLine


def test_reading_order_sorts_rows_then_columns() -> None:
    lines = [
        OCRLine("右下", .9, [200, 100, 260, 130]),
        OCRLine("左上", .9, [10, 10, 70, 40]),
        OCRLine("右上", .9, [200, 12, 260, 42]),
        OCRLine("左下", .9, [10, 98, 70, 128]),
    ]
    assert [item.text for item in _reading_order(lines)] == ["左上", "右上", "左下", "右下"]


class FakeProvider:
    def __init__(self, name: str, *, fails: bool = False) -> None:
        self._info = OCRProviderInfo(name, "1", "test")
        self.fails = fails

    @property
    def info(self) -> OCRProviderInfo:
        return self._info

    def check_environment(self) -> OCRProviderStatus:
        return OCRProviderStatus(True, "ok")

    def recognize(self, image_path: Path, cancelled=None) -> OCRResult:
        if self.fails:
            raise OCRProviderError("boom")
        return OCRResult([OCRLine("结果", .9, [0, 0, 1, 1])], self.info, 1)


def test_fallback_is_visible_and_sticky() -> None:
    primary = FakeProvider("apple_vision", fails=True)
    fallback = FakeProvider("paddleocr")
    provider = FallbackOCRProvider(primary, fallback)
    first = provider.recognize(Path("frame.jpg"))
    second = provider.recognize(Path("frame-2.jpg"))
    assert first.provider.name == "paddleocr"
    assert first.fallback_from == "apple_vision"
    assert first.fallback_reason == "boom"
    assert second.fallback_from == "apple_vision"
