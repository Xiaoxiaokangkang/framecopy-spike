from framecopy.engine.merge import is_progressive, merge_frames, similarity
from framecopy.models import OCRFrame, OCRLine


def frame(time: float, text: str, confidence: float = 0.95) -> OCRFrame:
    return OCRFrame(time, f"{time:.3f}.jpg", [OCRLine(text, confidence, [0.1, 0.1, 0.8, 0.2])], 100, "0")


def test_progressive_animation_keeps_complete_state() -> None:
    segments = merge_frames([
        frame(0.0, "AI"),
        frame(0.5, "AI时代"),
        frame(1.0, "AI时代的职业"),
        frame(1.5, "AI时代的职业图谱"),
    ])
    assert len(segments) == 1
    assert segments[0].clean_text == "AI时代的职业图谱"
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 1.5


def test_unrelated_text_becomes_new_segment() -> None:
    segments = merge_frames([frame(0, "第一章"), frame(3, "第二章")])
    assert [item.clean_text for item in segments] == ["第一章", "第二章"]


def test_similarity_ignores_punctuation_and_spaces() -> None:
    assert similarity("你好，世界！", "你好 世界") == 1.0
    assert is_progressive("AI时代", "AI时代的职业图谱")


def test_numeric_animation_uses_last_stable_state_not_longest_text() -> None:
    segments = merge_frames([
        frame(0.0, "196000年"),
        frame(0.5, "1958年"),
        frame(1.0, "1960年"),
        frame(1.5, "1960年"),
    ])
    assert len(segments) == 1
    assert segments[0].clean_text == "1960年"
    assert segments[0].evidence_frame == "1.500.jpg"


def test_numeric_animation_prefers_single_final_frame_and_joins_unit_fragment() -> None:
    earlier = OCRFrame(0.0, "0.jpg", [
        OCRLine("1958年", .9, [.3, .2, .5, .25]),
        OCRLine("4420万匹", .8, [.1, .3, .8, .4]),
    ])
    repeated = OCRFrame(0.5, "0.5.jpg", [
        OCRLine("1958年", .9, [.3, .2, .5, .25]),
        OCRLine("4420万匹", .8, [.1, .3, .8, .4]),
    ])
    final = OCRFrame(1.0, "1.jpg", [
        OCRLine("1960年", .8, [.3, .2, .5, .25]),
        OCRLine("”300，", .6, [.1, .3, .6, .4]),
        OCRLine("万匹", .8, [.58, .34, .8, .4]),
    ])
    segment = merge_frames([earlier, repeated, final])[0]
    assert segment.evidence_frame == "1.jpg"
    assert segment.clean_text == "1960年\n约300万匹"


def test_low_confidence_isolated_character_is_excluded_from_clean_output() -> None:
    noisy = OCRFrame(
        0.0,
        "noise.jpg",
        [OCRLine("了", 0.4, [0.1, 0.1, 0.2, 0.2])],
        provider="paddleocr",
    )
    segment = merge_frames([noisy])[0]
    assert segment.consensus_text == "了"
    assert segment.clean_eligible is False
    assert segment.needs_review is True


def test_noise_line_is_removed_but_body_remains_clean_eligible() -> None:
    noisy = OCRFrame(
        0.0,
        "mixed.jpg",
        [
            OCRLine("工业革命", 0.97, [0.1, 0.1, 0.8, 0.2]),
            OCRLine("了", 0.4, [0.85, 0.8, 0.9, 0.85]),
        ],
        provider="paddleocr",
    )
    segment = merge_frames([noisy])[0]
    assert segment.clean_text == "工业革命"
    assert segment.clean_eligible is True
    assert segment.consensus_text == "工业革命\n了"


def test_adjacent_accumulated_frame_only_keeps_new_lines_in_clean_copy() -> None:
    first = OCRFrame(0, "a.jpg", [
        OCRLine("1764年", .95, [.1, .1, .3, .2]),
        OCRLine("珍妮纺纱机", .95, [.1, .2, .5, .3]),
    ])
    second = OCRFrame(3, "b.jpg", [
        OCRLine("1764年", .95, [.1, .1, .3, .2]),
        OCRLine("珍妮纺纱机", .95, [.1, .2, .5, .3]),
        OCRLine("1769年", .95, [.1, .5, .3, .6]),
        OCRLine("瓦特蒸汽机", .95, [.1, .6, .5, .7]),
    ])
    segments = merge_frames([first, second])
    assert len(segments) == 2
    assert segments[1].clean_text == "1769年\n瓦特蒸汽机"

