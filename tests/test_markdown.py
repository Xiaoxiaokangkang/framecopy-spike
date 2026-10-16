import json
from pathlib import Path

from framecopy.engine.markdown import format_timestamp, generate_outputs
from framecopy.engine.probe import VideoInfo
from framecopy.models import OCRFrame, OCRLine, Segment


def test_timestamp_format() -> None:
    assert format_timestamp(40.9) == "00:40"
    assert format_timestamp(3661) == "01:01:01"


def test_clean_output_filters_review_only_segment_and_manifest_records_provider(tmp_path: Path) -> None:
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
    assert "了" not in (tmp_path / "output" / "视频文案-纯净版.md").read_text(encoding="utf-8")
    assert "了" in (tmp_path / "output" / "视频文案-带时间戳.md").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "output" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ocr"]["providers"][0]["name"] == "apple_vision"
    assert manifest["ocr"]["providers"][0]["revision"] == "3"

