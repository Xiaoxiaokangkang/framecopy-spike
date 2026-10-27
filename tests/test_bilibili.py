from __future__ import annotations

import json
from pathlib import Path

import pytest

from framecopy.config import Settings
from framecopy.errors import FramecopyError
from framecopy.sources.bilibili import (
    BilibiliProvider,
    _parse_subtitle,
    _subtitle_languages,
    extract_bilibili_url,
)


def make_downloader(tmp_path: Path) -> Path:
    path = tmp_path / "fake-yt-dlp"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "a=sys.argv[1:]\n"
        "if '--version' in a: print('test-2026'); raise SystemExit(0)\n"
        "url=a[-1]\n"
        "if '--dump-single-json' in a:\n"
        "  if '--flat-playlist' in a and 'MULTI' in url:\n"
        "    print(json.dumps({'_type':'playlist','title':'\u591aP\u6d4b\u8bd5','entries':[{'title':'\u7b2c\u4e00P','url':'https://www.bilibili.com/video/BV1MULTI?p=1'},{'title':'\u7b2c\u4e8cP','url':'https://www.bilibili.com/video/BV1MULTI?p=2'}]})); raise SystemExit(0)\n"
        "  print(json.dumps({'title':'\u6d4b\u8bd5\u89c6\u9891','duration':120,'webpage_url':url,'subtitles':{'zh-Hans':[{'url':'https://example.invalid/sub'}]}})); raise SystemExit(0)\n"
        "template=pathlib.Path(a[a.index('--output')+1])\n"
        "template.parent.mkdir(parents=True, exist_ok=True)\n"
        "if '--write-subs' in a:\n"
        "  out=template.parent/'bilibili-subtitle.zh-Hans.vtt'\n"
        "  out.write_text('WEBVTT\\n\\n00:00:00.000 --> 00:00:02.000\\n\u4f60\u597d\\n\\n00:00:02.000 --> 00:00:04.000\\n\u4e16\u754c\\n', encoding='utf-8')\n"
        "else:\n"
        "  out=template.parent/'bilibili-audio.m4a'; out.write_bytes(b'audio')\n"
        "  print('PROGRESS:50.0%'); print('PROGRESS:100.0%'); print('RESULT:'+str(out))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def make_settings(tmp_path: Path, downloader: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "test.db",
        bilibili_downloader=str(downloader),
    )


def test_extracts_supported_bilibili_links() -> None:
    assert extract_bilibili_url(
        "\u770b\u770b https://www.bilibili.com/video/BV1ABC123?p=2 \u5c31\u8fd9\u4e2a"
    ) == "https://www.bilibili.com/video/BV1ABC123?p=2"
    assert extract_bilibili_url("https://b23.tv/AbC123") == "https://b23.tv/AbC123"
    assert extract_bilibili_url(
        "https://m.bilibili.com/video/BV1ABC123"
    ) == "https://m.bilibili.com/video/BV1ABC123"


@pytest.mark.parametrize("value", [
    "http://www.bilibili.com/video/BV1ABC",
    "https://www.bilibili.com/bangumi/play/ep123",
    "https://example.com/video/BV1ABC",
])
def test_rejects_non_video_or_insecure_bilibili_links(value: str) -> None:
    with pytest.raises(FramecopyError) as captured:
        extract_bilibili_url(value)
    assert captured.value.code == "BILIBILI_INVALID_LINK"


def test_inspection_requires_explicit_part_selection(tmp_path: Path) -> None:
    provider = BilibiliProvider(make_settings(tmp_path, make_downloader(tmp_path)))

    result = provider.inspect("https://www.bilibili.com/video/BV1MULTI")

    assert result.requires_part_selection is True
    assert [item.title for item in result.parts] == ["\u7b2c\u4e00P", "\u7b2c\u4e8cP"]
    assert result.parts[1].url.endswith("?p=2")


def test_audio_download_returns_audio_only_media_and_progress(tmp_path: Path) -> None:
    provider = BilibiliProvider(make_settings(tmp_path, make_downloader(tmp_path)))
    job_dir = tmp_path / "job"
    values: list[int] = []

    result = provider.acquire(
        {"url": "https://www.bilibili.com/video/BV1ABC?p=1", "language": "zh"},
        job_dir,
        lambda _label, value: values.append(value),
        lambda: False,
    )

    assert result.kind == "audio"
    assert result.path.name == "bilibili-audio.m4a"
    assert result.language_hint == "zh"
    assert max(values) == 10


def test_published_subtitle_is_parsed_into_speech_segments(tmp_path: Path) -> None:
    provider = BilibiliProvider(make_settings(tmp_path, make_downloader(tmp_path)))

    result = provider.acquire(
        {
            "url": "https://www.bilibili.com/video/BV1ABC?p=1",
            "transcript_source": "subtitle",
            "language": "zh",
        },
        tmp_path / "job",
        lambda *_: None,
        lambda: False,
    )

    assert result.kind == "subtitle"
    assert [item.clean_text for item in result.transcript_segments] == ["\u4f60\u597d", "\u4e16\u754c"]
    assert result.transcript_provider == "bilibili_subtitle"


def test_json_subtitle_parser_supports_bilibili_body(tmp_path: Path) -> None:
    path = tmp_path / "subtitle.json"
    path.write_text(json.dumps({
        "body": [{"from": 1.25, "to": 3.5, "content": "\u6d4b\u8bd5\u5b57\u5e55"}],
    }, ensure_ascii=False), encoding="utf-8")

    segments = _parse_subtitle(path, "zh")

    assert len(segments) == 1
    assert segments[0].start_time == 1.25
    assert segments[0].clean_text == "\u6d4b\u8bd5\u5b57\u5e55"


def test_danmaku_is_not_reported_as_a_published_subtitle() -> None:
    assert _subtitle_languages(
        {"danmaku": [{"url": "https://example.invalid/chat"}]},
        {"zh-Hans": [{"url": "https://example.invalid/sub"}], "en": []},
    ) == ("zh-Hans",)
