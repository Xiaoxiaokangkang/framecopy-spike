import json
from pathlib import Path

from framecopy.engine.probe import VideoInfo
from framecopy.engine.speech import SpeechResult
from framecopy.engine.speech_output import generate_speech_outputs, regenerate_speech_outputs
from framecopy.models import SpeechSegment


def test_speech_outputs_only_include_readable_markdown_and_internal_manifest(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test-video")
    output = tmp_path / "output"
    result = SpeechResult(
        segments=[
            SpeechSegment(1, 1.25, 3.5, " 你好 世界 ", "你好 世界", .91, "zh"),
            SpeechSegment(2, 63.001, 65.2, "第二段", "第二段", .3, "zh", True),
        ],
        provider="mlx_whisper",
        provider_version="test",
        model="local-test-model",
        language="zh",
        duration_ms=123.4,
    )

    generated = generate_speech_outputs(
        output,
        video,
        VideoInfo(70, 1920, 1080, 25, "h264", video.stat().st_size),
        result,
    )

    assert {item.name for item in generated} == {"可读文案.md", "manifest.json"}
    readable = (output / "可读文案.md").read_text(encoding="utf-8")
    assert "你好 世界" in readable
    assert "第二段" in readable
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["extraction_mode"] == "speech"
    assert manifest["speech"]["model"] == "local-test-model"
    assert manifest["source_cleanup"]["status"] == "pending"


def test_regenerate_speech_outputs_uses_user_text_and_removes_legacy_files(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    legacy = output / "原始语音识别.jsonl"
    legacy.write_text('{"raw_text":"原始"}\n', encoding="utf-8")

    regenerate_speech_outputs(output, [{
        "sequence": 1,
        "start_time": 0,
        "end_time": 2,
        "raw_text": "原始",
        "clean_text": "自动",
        "effective_text": "人工修改",
    }])

    assert "人工修改" in (output / "可读文案.md").read_text(encoding="utf-8")
    assert not legacy.exists()
