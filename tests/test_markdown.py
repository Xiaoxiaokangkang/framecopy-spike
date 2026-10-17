import json
from pathlib import Path

from framecopy.engine.markdown import format_timestamp, generate_outputs
from framecopy.engine.probe import VideoInfo
from framecopy.models import OCRFrame, OCRLine, Segment


def test_timestamp_format() -> None:
    assert format_timestamp(40.9) == "00:40"
    assert format_timestamp(3661) == "01:01:01"


def test_readable_output_includes_every_segment_and_manifest_records_provider(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test")
    frame = OCRFrame(
        0,
        "frame.jpg",
        [OCRLine("了", .4, [0, 0, 1, 1])],
        provider="apple_vision",
        provider_version="Version test",
        model="VNRecognizeTextRequest.accurate",
        provider_revision="3",
        languages=["zh-Hans"],
        duration_ms=12.5,
    )
    segment = Segment(1, 0, 0, "了", "了", "了", .4, "frame.jpg", True, False)
    generate_outputs(
        tmp_path / "output",
        video,
        VideoInfo(1, 100, 100, 25, "h264", 4),
        [frame],
        [segment],
    )
    assert "了" in (tmp_path / "output" / "可读文案.md").read_text(encoding="utf-8")
    assert {path.name for path in (tmp_path / "output").iterdir()} == {"可读文案.md", "manifest.json"}
    manifest = json.loads((tmp_path / "output" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ocr"]["providers"][0]["name"] == "apple_vision"
    assert manifest["ocr"]["providers"][0]["revision"] == "3"

