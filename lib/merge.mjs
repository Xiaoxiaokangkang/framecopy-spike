export function normalizeText(text) {
  return String(text ?? '')
    .normalize('NFKC')
    .replace(/[\s\p{P}\p{S}]/gu, '')
    .toLowerCase();
}

export function similarity(left, right) {
  const a = normalizeText(left);
  const b = normalizeText(right);
  if (!a && !b) return 1;
  if (!a || !b) return 0;
  if (a.includes(b) || b.includes(a)) return Math.min(a.length, b.length) / Math.max(a.length, b.length);

  const previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let i = 1; i <= a.length; i += 1) {
    let diagonal = previous[0];
    previous[0] = i;
    for (let j = 1; j <= b.length; j += 1) {
      const above = previous[j];
      previous[j] = Math.min(
        previous[j] + 1,
        previous[j - 1] + 1,
        diagonal + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
      diagonal = above;
    }
  }
  return 1 - previous[b.length] / Math.max(a.length, b.length);
}

export function mergeProgressiveSegments(frames, threshold = 0.72) {
  const segments = [];
  for (const frame of frames) {
    const text = (frame.lines ?? []).map((line) => line.text.trim()).filter(Boolean).join('\n');
    if (!text) continue;
    const confidence = frame.lines.reduce((sum, line) => sum + Number(line.confidence || 0), 0) / frame.lines.length;
    const last = segments.at(-1);
    const normalized = normalizeText(text);
    const lastNormalized = normalizeText(last?.clean_text);
    const progressive = last && (normalized.startsWith(lastNormalized) || lastNormalized.startsWith(normalized));
    const duplicate = last && similarity(text, last.clean_text) >= threshold;
    if (last && (progressive || duplicate) && frame.time - last.end_time <= 3.25) {
      last.end_time = frame.time;
      if (normalized.length > lastNormalized.length || confidence > last.confidence + 0.08) {
        last.raw_text = text;
        last.consensus_text = text;
        last.clean_text = text;
        last.confidence = confidence;
        last.evidence_frame = frame.image;
      }
      last.needs_review = last.confidence < 0.72;
      continue;
    }
    segments.push({
      sequence: segments.length + 1,
      start_time: frame.time,
      end_time: frame.time,
      raw_text: text,
      consensus_text: text,
      clean_text: text,
      user_text: null,
      confidence,
      evidence_frame: frame.image,
      needs_review: confidence < 0.72,
    });
  }
  return segments;
}

export function formatTimestamp(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = value % 60;
  return hours > 0
    ? [hours, minutes, secs].map((item) => String(item).padStart(2, '0')).join(':')
    : [minutes, secs].map((item) => String(item).padStart(2, '0')).join(':');
}

export function markdownFor(segments, timed = false) {
  const title = timed ? '# 视频画面文案（带时间戳版）' : '# 视频画面文案（纯净版）';
  const blocks = segments.map((segment) => {
    const text = segment.user_text ?? segment.clean_text;
    return timed ? `## ${formatTimestamp(segment.start_time)}\n\n${text}` : text;
  });
  return `${title}\n\n${blocks.join('\n\n')}\n`;
}
