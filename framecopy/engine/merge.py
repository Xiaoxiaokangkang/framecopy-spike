from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

from framecopy.engine.ocr.calibration import calibrated_confidence, calibration_for
from framecopy.models import OCRFrame, OCRLine, Segment


PUNCTUATION = re.compile(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()【】\[\]《》<>·—_-]+")
DIGIT_STATE = re.compile(r"^[\d年月日万亿千百十点约%％./+-]+$")


def normalize_text(value: str) -> str:
    return PUNCTUATION.sub("", value).lower()


def similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def is_progressive(left: str, right: str) -> bool:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return False
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= 2 and longer.startswith(shorter) and len(longer) > len(shorter)


def clean_text(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    deduped: list[str] = []
    for line in lines:
        if not deduped or normalize_text(line) != normalize_text(deduped[-1]):
            deduped.append(line)
    return "\n".join(deduped)


def merge_frames(frames: list[OCRFrame], *, similar_threshold: float = 0.86,
                 max_gap_seconds: float = 2.5) -> list[Segment]:
    """Merge by spatial text tracks, keeping the last stable state."""
    visible = [frame for frame in frames if normalize_text(frame.text)]
    if not visible:
        return []
    groups: list[list[OCRFrame]] = []
    current: list[OCRFrame] = [visible[0]]
    for frame in visible[1:]:
        previous = current[-1]
        close_in_time = frame.timestamp - previous.timestamp <= max_gap_seconds
        if close_in_time and _frames_related(previous, frame, similar_threshold):
            current.append(frame)
        else:
            groups.append(current)
            current = [frame]
    groups.append(current)

    segments: list[Segment] = []
    for group in groups:
        stable_run, selected = _last_stable_state(group)
        consensus_lines = _join_line_fragments(_consensus_lines(selected, stable_run))
        consensus = clean_text("\n".join(line.text for line in consensus_lines))
        clean_lines, filtered_count = _filter_clean_lines(consensus_lines, group, selected.provider)
        cleaned = clean_text("\n".join(line.text for line in clean_lines))
        clean_eligible = bool(normalize_text(cleaned))
        if not clean_eligible:
            cleaned = consensus
        raw_confidence = _frame_confidence(selected)
        confidence = _group_confidence(stable_run, selected)
        rules = calibration_for(selected.provider)
        if len(stable_run) == 1 and raw_confidence < rules.clean_single_frame_threshold \
                and len(normalize_text(consensus)) <= 1:
            clean_eligible = False
        needs_review = (
            raw_confidence < rules.review_threshold
            or len(stable_run) == 1
            or filtered_count > 0
            or selected.fallback_from is not None
        )
        segments.append(Segment(
            sequence=len(segments) + 1,
            start_time=group[0].timestamp,
            end_time=group[-1].timestamp,
            raw_text=selected.text,
            consensus_text=consensus,
            clean_text=cleaned,
            confidence=confidence,
            evidence_frame=selected.image_path,
            needs_review=needs_review,
            clean_eligible=clean_eligible,
        ))
    return _dedupe_adjacent(segments)


def _frames_related(left: OCRFrame, right: OCRFrame, threshold: float) -> bool:
    if similarity(left.text, right.text) >= threshold or is_progressive(left.text, right.text):
        return True
    if not left.lines or not right.lines:
        return False
    matches = sum(1 for line in left.lines if any(_same_track(line, other) for other in right.lines))
    return matches / max(1, min(len(left.lines), len(right.lines))) >= 0.5


def _same_track(left: OCRLine, right: OCRLine) -> bool:
    if not _nearby(left.bbox, right.bbox):
        return False
    a, b = normalize_text(left.text), normalize_text(right.text)
    if not a or not b:
        return False
    return (
        similarity(a, b) >= 0.55
        or is_progressive(a, b)
        or (bool(DIGIT_STATE.fullmatch(a)) and bool(DIGIT_STATE.fullmatch(b)))
    )


def _nearby(left: list[float], right: list[float]) -> bool:
    if len(left) < 4 or len(right) < 4:
        return False
    left_x, left_y = (left[0] + left[2]) / 2, (left[1] + left[3]) / 2
    right_x, right_y = (right[0] + right[2]) / 2, (right[1] + right[3]) / 2
    left_w, left_h = max(0.01, left[2] - left[0]), max(0.01, left[3] - left[1])
    right_w, right_h = max(0.01, right[2] - right[0]), max(0.01, right[3] - right[1])
    return (
        abs(left_x - right_x) <= max(0.045, (left_w + right_w) * 0.35)
        and abs(left_y - right_y) <= max(0.035, (left_h + right_h) * 0.7)
    )


def _last_stable_state(group: list[OCRFrame]) -> tuple[list[OCRFrame], OCRFrame]:
    runs: list[list[OCRFrame]] = [[group[0]]]
    for frame in group[1:]:
        if _same_state(runs[-1][-1], frame):
            runs[-1].append(frame)
        else:
            runs.append([frame])
    stable = [run for run in runs if len(run) >= 2 or run[-1].timestamp - run[0].timestamp >= 0.35]
    # Rolling counters/years are defined by their final value. An earlier value
    # may remain on screen longer and must not beat the last observed state.
    if _is_numeric_animation(group):
        chosen_run = [runs[-1][-1]]
    else:
        chosen_run = stable[-1] if stable else runs[-1]
    return chosen_run, chosen_run[-1]


def _is_numeric_animation(group: list[OCRFrame]) -> bool:
    states = {
        tuple(re.findall(r"\d+", normalize_text(frame.text)))
        for frame in group
        if re.search(r"\d", frame.text)
    }
    return len(states) >= 2


def _same_state(left: OCRFrame, right: OCRFrame) -> bool:
    return similarity(left.text, right.text) >= 0.92 and _layout_overlap(left, right) >= 0.5


def _layout_overlap(left: OCRFrame, right: OCRFrame) -> float:
    if not left.lines or not right.lines:
        return 0.0
    matches = sum(1 for line in left.lines if any(_nearby(line.bbox, other.bbox) for other in right.lines))
    return matches / max(1, min(len(left.lines), len(right.lines)))


def _consensus_lines(selected: OCRFrame, stable_run: list[OCRFrame]) -> list[OCRLine]:
    output: list[OCRLine] = []
    for selected_line in selected.lines:
        candidates: list[OCRLine] = []
        for frame in stable_run:
            matches = [line for line in frame.lines if _nearby(selected_line.bbox, line.bbox)]
            if matches:
                candidates.append(max(matches, key=lambda item: similarity(selected_line.text, item.text)))
        if not candidates:
            output.append(selected_line)
            continue
        counts = Counter(normalize_text(item.text) for item in candidates if normalize_text(item.text))
        winner, count = counts.most_common(1)[0]
        if count >= 2:
            output.append(next(item for item in reversed(candidates) if normalize_text(item.text) == winner))
        else:
            output.append(selected_line)
    return output


def _join_line_fragments(lines: list[OCRLine]) -> list[OCRLine]:
    if len(lines) < 2:
        return [OCRLine(_clean_line_artifacts(line.text), line.confidence, line.bbox) for line in lines]
    output: list[OCRLine] = []
    for line in sorted(lines, key=lambda item: ((item.bbox[1] + item.bbox[3]) / 2, item.bbox[0])):
        if output and _same_visual_row(output[-1].bbox, line.bbox):
            previous = output[-1]
            combined = previous.text + line.text
            previous_width = max(0.001, previous.bbox[2] - previous.bbox[0])
            line_width = max(0.001, line.bbox[2] - line.bbox[0])
            output[-1] = OCRLine(
                combined,
                (previous.confidence * previous_width + line.confidence * line_width) / (previous_width + line_width),
                [
                    min(previous.bbox[0], line.bbox[0]),
                    min(previous.bbox[1], line.bbox[1]),
                    max(previous.bbox[2], line.bbox[2]),
                    max(previous.bbox[3], line.bbox[3]),
                ],
            )
        else:
            output.append(OCRLine(line.text.strip(), line.confidence, list(line.bbox)))
    return [OCRLine(_clean_line_artifacts(line.text), line.confidence, line.bbox) for line in output]


def _same_visual_row(left: list[float], right: list[float]) -> bool:
    if len(left) < 4 or len(right) < 4:
        return False
    overlap = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    min_height = max(0.001, min(left[3] - left[1], right[3] - right[1]))
    horizontal_gap = right[0] - left[2]
    return overlap / min_height >= 0.45 and -0.08 <= horizontal_gap <= 0.04


def _clean_line_artifacts(value: str) -> str:
    value = value.strip()
    # Vision commonly reads the small Chinese qualifier “约” beside a large
    # counter as a quote mark. Restrict the correction to numeric quantity units.
    value = re.sub(r"^[\"“”‘’](?=\d+[，,]?(?:万|亿|千|百))", "约", value)
    value = re.sub(r"^[\"“”‘’※]+(?=\d)", "", value)
    return re.sub(r"(?<=\d)[，,](?=[万亿千百十%％年月日匹])", "", value)


def _filter_clean_lines(lines: list[OCRLine], group: list[OCRFrame], provider: str) -> tuple[list[OCRLine], int]:
    kept: list[OCRLine] = []
    filtered = 0
    rules = calibration_for(provider)
    for line in lines:
        normalized = normalize_text(line.text)
        repeated = sum(
            1 for frame in group
            if any(_nearby(line.bbox, other.bbox) and similarity(line.text, other.text) >= 0.8 for other in frame.lines)
        )
        isolated_noise = len(normalized) <= 1 and line.confidence < rules.clean_isolated_threshold and repeated < 2
        if isolated_noise:
            filtered += 1
        else:
            kept.append(line)
    return kept, filtered


def _frame_confidence(frame: OCRFrame) -> float:
    if not frame.lines:
        return 0.0
    weighted = sum(max(1, len(normalize_text(line.text))) * line.confidence for line in frame.lines)
    total = sum(max(1, len(normalize_text(line.text))) for line in frame.lines)
    return weighted / total if total else 0.0


def _group_confidence(group: list[OCRFrame], selected: OCRFrame) -> float:
    base = calibrated_confidence(selected.provider, _frame_confidence(selected))
    if len(group) == 1:
        return round(min(base, 0.79), 4)
    agreement = sum(similarity(selected.text, item.text) for item in group) / len(group)
    return round(min(0.99, base * 0.75 + agreement * 0.25), 4)


def _dedupe_adjacent(segments: list[Segment]) -> list[Segment]:
    output: list[Segment] = []
    for segment in segments:
        if output and similarity(output[-1].consensus_text, segment.consensus_text) >= 0.94:
            output[-1].end_time = segment.end_time
            output[-1].raw_text = segment.raw_text
            output[-1].consensus_text = segment.consensus_text
            output[-1].clean_text = segment.clean_text
            output[-1].confidence = segment.confidence
            output[-1].evidence_frame = segment.evidence_frame
            output[-1].needs_review = output[-1].needs_review or segment.needs_review
            output[-1].clean_eligible = output[-1].clean_eligible or segment.clean_eligible
        else:
            if output and segment.start_time - output[-1].end_time <= 3.0:
                segment.clean_text = _remove_repeated_lines(output[-1].clean_text, segment.clean_text)
                if not normalize_text(segment.clean_text):
                    # Keep it in the timestamped/raw evidence, but not in the pure copy.
                    segment.clean_text = segment.consensus_text
                    segment.clean_eligible = False
            segment.sequence = len(output) + 1
            output.append(segment)
    return output


def _remove_repeated_lines(previous: str, current: str) -> str:
    previous_lines = [line for line in previous.splitlines() if normalize_text(line)]
    kept: list[str] = []
    for line in current.splitlines():
        normalized = normalize_text(line)
        repeated = any(
            normalized == normalize_text(old)
            or (len(normalized) >= 3 and similarity(line, old) >= 0.94)
            for old in previous_lines
        )
        if not repeated:
            kept.append(line)
    return clean_text("\n".join(kept))
