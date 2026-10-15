const $ = (selector) => document.querySelector(selector);
const views = ['upload-view', 'progress-view', 'result-view', 'error-view'];
const state = { file: null, jobId: null, token: null, segments: [], selected: 0, saveTimer: null, pollTimer: null };

function show(name) { views.forEach((id) => $(`#${id}`).classList.toggle('hidden', id !== name)); window.scrollTo({ top: 0, behavior: 'smooth' }); }
function bytes(value) { return value > 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${Math.ceil(value / 1024)} KB`; }
function time(value) { const total = Math.max(0, Math.round(value || 0)); return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`; }
function apiUrl(suffix = '') { return `/api/jobs/${state.jobId}${suffix}?token=${encodeURIComponent(state.token)}`; }

function choose(file) {
  if (!file) return;
  if (file.size > 200 * 1024 * 1024) return alert('视频不能超过 200 MB。');
  state.file = file;
  $('#file-name').textContent = file.name;
  $('#file-info').textContent = `${bytes(file.size)} · ${file.type || '待检测媒体格式'}`;
  $('#file-card').classList.remove('hidden'); $('#start-button').classList.remove('hidden');
}

$('#choose-button').addEventListener('click', (event) => { event.stopPropagation(); $('#file-input').click(); });
$('#drop-zone').addEventListener('click', () => $('#file-input').click());
$('#drop-zone').addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') $('#file-input').click(); });
$('#file-input').addEventListener('change', (event) => choose(event.target.files[0]));
['dragenter', 'dragover'].forEach((name) => $('#drop-zone').addEventListener(name, (event) => { event.preventDefault(); $('#drop-zone').classList.add('dragging'); }));
['dragleave', 'drop'].forEach((name) => $('#drop-zone').addEventListener(name, (event) => { event.preventDefault(); $('#drop-zone').classList.remove('dragging'); }));
$('#drop-zone').addEventListener('drop', (event) => choose(event.dataTransfer.files[0]));
$('#clear-file').addEventListener('click', () => { state.file = null; $('#file-input').value = ''; $('#file-card').classList.add('hidden'); $('#start-button').classList.add('hidden'); });

$('#start-button').addEventListener('click', async () => {
  if (!state.file) return;
  show('progress-view'); setProgress({ progress: 1, stage: '正在上传视频', detail: `${state.file.name} · ${bytes(state.file.size)}`, status: 'uploading' });
  try {
    const response = await fetch('/api/jobs/upload', { method: 'POST', headers: { 'content-type': state.file.type || 'application/octet-stream', 'x-file-name': encodeURIComponent(state.file.name), 'x-processing-mode': 'accurate' }, body: state.file });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || payload.error || '上传失败');
    state.jobId = payload.id; state.token = payload.token;
    history.replaceState(null, '', `/#${state.jobId}.${state.token}`);
    $('#job-short-id').textContent = state.jobId.slice(0, 8).toUpperCase();
    poll();
  } catch (error) { fail('无法创建任务', error.message); }
});

const stageOrder = ['validating', 'analyzing', 'recognizing', 'merging', 'generating'];
function setProgress(job) {
  $('#progress-number').textContent = Math.min(100, Math.max(0, job.progress || 0));
  $('#progress-bar').style.width = `${job.progress || 0}%`; $('#progress-stage').textContent = job.stage; $('#progress-detail').textContent = job.detail || '';
  const index = stageOrder.indexOf(job.status);
  document.querySelectorAll('#stage-list li').forEach((element, item) => { element.classList.toggle('done', index > item || job.status === 'ready'); element.classList.toggle('active', index === item); });
}

async function poll() {
  try {
    const response = await fetch(apiUrl()); const job = await response.json(); if (!response.ok) throw new Error(job.error);
    setProgress(job);
    if (job.status === 'ready') return loadResult();
    if (job.status === 'failed' || job.status === 'cancelled') return fail(job.stage, job.detail);
    state.pollTimer = setTimeout(poll, 700);
  } catch (error) { state.pollTimer = setTimeout(poll, 1600); }
}

async function loadResult() {
  const response = await fetch(apiUrl('/result')); const payload = await response.json();
  if (!response.ok) return fail('结果读取失败', payload.error || '请稍后重试');
  state.segments = payload.segments; show('result-view');
  $('#result-job-id').textContent = state.jobId.slice(0, 8).toUpperCase();
  const reviewCount = state.segments.filter((segment) => segment.needs_review).length;
  $('#result-summary').textContent = `识别 ${state.segments.length} 段 · ${payload.manifest.candidate_frames} 个候选画面 · ${Math.round(payload.manifest.duration_seconds)} 秒视频`;
  $('#review-banner').classList.toggle('hidden', reviewCount === 0);
  const files = (name) => apiUrl(`/files/${encodeURIComponent(name)}`);
  $('#download-clean').href = files('视频文案-纯净版.md'); $('#download-timed').href = files('视频文案-带时间戳.md'); $('#download-raw').href = files('raw-ocr.jsonl'); $('#download-manifest').href = files('manifest.json');
  renderSegments(); if (state.segments.length) selectSegment(0);
}

function renderSegments() {
  const container = $('#segments'); container.replaceChildren();
  state.segments.forEach((segment, index) => {
    const item = document.createElement('article'); item.className = `segment${segment.needs_review ? ' review' : ''}`; item.dataset.index = index;
    const meta = document.createElement('div'); meta.className = 'segment-meta'; meta.textContent = `${String(index + 1).padStart(2, '0')} / ${time(segment.start_time)}`;
    const body = document.createElement('div'); const editor = document.createElement('textarea'); editor.value = segment.user_text ?? segment.clean_text; editor.rows = Math.max(2, editor.value.split('\n').length);
    const confidence = document.createElement('span'); confidence.className = 'confidence'; confidence.textContent = `${segment.needs_review ? '● 建议核查' : '● 识别稳定'} · 置信度 ${Math.round(segment.confidence * 100)}%`;
    editor.addEventListener('input', () => { segment.user_text = editor.value; editor.style.height = 'auto'; editor.style.height = `${editor.scrollHeight}px`; scheduleSave(); });
    item.addEventListener('click', () => selectSegment(index)); body.append(editor, confidence); item.append(meta, body); container.append(item);
    requestAnimationFrame(() => { editor.style.height = `${editor.scrollHeight}px`; });
  });
}

function selectSegment(index) {
  state.selected = index; document.querySelectorAll('.segment').forEach((item, i) => item.classList.toggle('active', i === index));
  const segment = state.segments[index]; if (!segment) return;
  $('#evidence-time').textContent = time(segment.start_time); $('#raw-text').textContent = segment.raw_text;
  const image = $('#evidence-image'); image.src = apiUrl(`/evidence/${encodeURIComponent(segment.evidence_frame)}`); image.classList.add('visible'); $('#frame-empty').classList.add('hidden');
}

function scheduleSave() {
  $('#save-status').textContent = '正在编辑…'; clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    $('#save-status').textContent = '正在保存…';
    try { const response = await fetch(apiUrl('/result'), { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ segments: state.segments }) }); if (!response.ok) throw new Error(); $('#save-status').textContent = '已自动保存'; }
    catch { $('#save-status').textContent = '保存失败，请继续编辑后重试'; }
  }, 550);
}

$('#restore-button').addEventListener('click', async () => {
  if (!confirm('恢复自动识别结果？当前人工修改会被替换。')) return;
  const response = await fetch(apiUrl('/restore'), { method: 'POST' }); const payload = await response.json();
  if (response.ok) { state.segments = payload.segments; renderSegments(); selectSegment(0); $('#save-status').textContent = '已恢复自动结果'; }
});
$('#cancel-button').addEventListener('click', async () => { if (!state.jobId) return show('upload-view'); await fetch(apiUrl('/cancel'), { method: 'POST' }); fail('任务已取消', '你可以返回首页，换一段视频重新开始。'); });
document.querySelectorAll('.back-home').forEach((button) => button.addEventListener('click', () => { clearTimeout(state.pollTimer); history.replaceState(null, '', '/'); show('upload-view'); }));
function fail(title, detail) { clearTimeout(state.pollTimer); $('#error-title').textContent = title || '处理失败'; $('#error-detail').textContent = detail || '请换一个视频后重试。'; show('error-view'); }

async function restoreFromHash() {
  const match = location.hash.slice(1).match(/^([0-9a-f-]{36})\.([\w-]+)$/);
  if (!match) return;
  [, state.jobId, state.token] = match; $('#job-short-id').textContent = state.jobId.slice(0, 8).toUpperCase(); show('progress-view'); poll();
}
restoreFromHash();
