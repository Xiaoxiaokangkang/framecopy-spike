import test from 'node:test';
import assert from 'node:assert/strict';
import { markdownFor, mergeProgressiveSegments, similarity } from '../lib/merge.mjs';

test('progressive animation keeps its final state', () => {
  const frames = [
    { time: 0, image: 'a.jpg', lines: [{ text: 'AI', confidence: 0.9 }] },
    { time: 0.5, image: 'b.jpg', lines: [{ text: 'AI时代', confidence: 0.91 }] },
    { time: 1, image: 'c.jpg', lines: [{ text: 'AI时代的职业图谱', confidence: 0.94 }] },
  ];
  const result = mergeProgressiveSegments(frames);
  assert.equal(result.length, 1);
  assert.equal(result[0].clean_text, 'AI时代的职业图谱');
  assert.equal(result[0].evidence_frame, 'c.jpg');
});

test('similarity tolerates one OCR error', () => {
  assert.ok(similarity('1764年珍妮纺纱机', '1764年珍妮纺沙机') > 0.85);
});

test('timed markdown uses user edits', () => {
  const markdown = markdownFor([{ start_time: 65, clean_text: '自动', user_text: '人工修改' }], true);
  assert.match(markdown, /## 01:05/);
  assert.match(markdown, /人工修改/);
});
