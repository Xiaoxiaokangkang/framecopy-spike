const jobId = location.pathname.split('/')[2];
const fragment = new URLSearchParams(location.hash.slice(1));
const token = fragment.get('token') || sessionStorage.getItem(`framecopy:${jobId}`);
if (token) {
  sessionStorage.setItem(`framecopy:${jobId}`, token);
  history.replaceState(null, '', location.pathname);
}
const headers = {'X-Access-Token': token || ''};
let saveTimer = null;
let polling = null;
let currentMode = 'visual';

async function request(path, options = {}) {
  const response = await fetch(path, {...options, headers: {...headers, ...(options.headers || {})}});
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('json') ? await response.json() : response;
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || '请求失败');
  return data;
}

async function poll() {
  try {
    const job = await request(`/api/jobs/${jobId}`);
    renderStatus(job);
    if (job.status === 'ready') {
      clearInterval(polling);
      await loadResult();
    } else if (['failed', 'cancelled'].includes(job.status)) {
      clearInterval(polling);
      showFailure(job.error_message || (job.status === 'cancelled' ? '任务已取消。' : '处理失败。'));
    }
  } catch (error) { showFailure(error.message); clearInterval(polling); }
}

function renderStatus(job) {
  currentMode = job.extraction_mode || 'visual';
  document.querySelector('#stage-label').textContent = job.stage_label || '正在处理';
  document.querySelector('#progress-bar').style.width = `${job.progress}%`;
  document.querySelector('#progress-number').textContent = `${job.progress}%`;
  const unit = currentMode === 'speech' ? '个识别分块' : '个候选画面';
  document.querySelector('#progress-text').textContent = job.progress_total ? `${job.progress_current || 0}/${job.progress_total} ${unit}` : '任务编号 ' + job.id.slice(0, 8);
}

async function loadResult() {
  const data = await request(`/api/jobs/${jobId}/result`);
  document.querySelector('#processing').hidden = true;
  document.querySelector('#result').hidden = false;
  currentMode = data.job.extraction_mode || 'visual';
  const otherMode = currentMode === 'speech' ? 'visual' : 'speech';
  const reextract = document.querySelector('#reextract-link');
  reextract.href = `/?mode=${otherMode}`;
  reextract.textContent = currentMode === 'speech'
    ? '重新选择视频并提取画面文字'
    : '重新选择视频并提取语音文案';
  const cleanup = data.job.source_cleanup_status === 'deleted' ? ' · 临时视频已清理' : ' · 请检查临时视频清理状态';
  if (currentMode === 'speech') {
    document.querySelector('#result-title').textContent = '语音文案';
    const engine = data.speech?.provider ? ` · ${data.speech.provider}` : '';
    document.querySelector('#result-meta').textContent = `${data.segments.length} 段语音${engine}${cleanup} · 截止 ${new Date(data.job.expires_at).toLocaleString()} 保留`;
    document.querySelector('#evidence-panel').hidden = true;
    document.querySelector('.result-grid').classList.add('speech');
  } else {
    document.querySelector('#result-title').textContent = '画面文案';
    const providers = (data.ocr?.providers || []).map(item => item.name === 'apple_vision' ? 'Apple Vision Accurate' : 'PaddleOCR').join(' + ');
    const fallback = (data.ocr?.fallbacks || []).length ? ' · 已发生 OCR 回退，请重点核查' : '';
    document.querySelector('#result-meta').textContent = `${data.segments.length} 段文字${providers ? ` · ${providers}` : ''}${fallback}${cleanup} · 截止 ${new Date(data.job.expires_at).toLocaleString()} 保留`;
  }
  renderSegments(data.segments);
  renderDownloads();
}

function renderSegments(segments) {
  const root = document.querySelector('#segments');
  root.innerHTML = '';
  segments.forEach(segment => {
    const article = document.createElement('article');
    article.className = 'segment';
    article.dataset.sequence = segment.sequence;
    const excluded = currentMode === 'visual' && !segment.clean_eligible;
    const confidenceClass = segment.needs_review || excluded ? 'low' : '';
    const notice = excluded ? '仅保留在核查版 · ' : (segment.needs_review ? '建议核查 · ' : '');
    article.innerHTML = `<span class="segment-time">${formatTime(segment.start_time)}</span><textarea aria-label="第 ${segment.sequence} 段文案"></textarea><span class="confidence ${confidenceClass}">${notice}质量分 ${(segment.confidence * 100).toFixed(0)}%</span>`;
    const textarea = article.querySelector('textarea');
    textarea.value = segment.effective_text;
    textarea.addEventListener('input', scheduleSave);
    if (currentMode === 'visual') article.addEventListener('click', () => showEvidence(article, segment));
    root.appendChild(article);
  });
  if (segments.length && currentMode === 'visual') showEvidence(root.firstElementChild, segments[0]);
}

async function showEvidence(element, segment) {
  document.querySelectorAll('.segment').forEach(item => item.classList.remove('active'));
  element.classList.add('active');
  const image = document.querySelector('#evidence-image');
  const response = await fetch(`/api/jobs/${jobId}/evidence/${encodeURIComponent(segment.evidence_frame.split('/').pop())}`, {headers});
  if (!response.ok) return;
  image.src = URL.createObjectURL(await response.blob());
  image.hidden = false;
  document.querySelector('#evidence-placeholder').hidden = true;
  document.querySelector('#evidence-caption').textContent = `${formatTime(segment.start_time)} · 原始识别：${segment.raw_text.replaceAll('\n', ' / ')}`;
}

function scheduleSave() {
  document.querySelector('#save-state').textContent = '有未保存修改';
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 650);
}

async function save() {
  const segments = [...document.querySelectorAll('.segment')].map(element => ({
    sequence: Number(element.dataset.sequence), text: element.querySelector('textarea').value
  }));
  document.querySelector('#save-state').textContent = '正在保存…';
  try {
    await request(`/api/jobs/${jobId}/result`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({segments})});
    document.querySelector('#save-state').textContent = '已自动保存';
  } catch { document.querySelector('#save-state').textContent = '保存失败，请重试'; }
}

document.querySelector('#restore-button').addEventListener('click', async () => {
  if (!confirm('确定恢复自动识别结果吗？当前人工修改将被清除。')) return;
  const data = await request(`/api/jobs/${jobId}/result/restore`, {method: 'POST'});
  renderSegments(data.segments);
  document.querySelector('#save-state').textContent = '已恢复自动结果';
});

document.querySelector('#cancel-button').addEventListener('click', async () => {
  if (!confirm('确定取消当前任务吗？')) return;
  await request(`/api/jobs/${jobId}/cancel`, {method: 'POST'});
});

function renderDownloads() {
  document.querySelector('#download-all').addEventListener('click', () => download(`/api/jobs/${jobId}/files`), {once: true});
}

async function download(url, filename = '') {
  const response = await fetch(url, {headers});
  if (!response.ok) return alert('下载失败');
  const disposition = response.headers.get('content-disposition') || '';
  const encodedName = disposition.match(/filename\*=utf-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="([^"]+)"/i)?.[1];
  const serverFilename = encodedName ? decodeURIComponent(encodedName) : plainName;
  const anchor = document.createElement('a');
  anchor.href = URL.createObjectURL(await response.blob());
  anchor.download = filename || serverFilename || '可读文案.md';
  anchor.click();
  URL.revokeObjectURL(anchor.href);
}

function showFailure(message) {
  document.querySelector('#processing').hidden = true;
  document.querySelector('#failure').hidden = false;
  document.querySelector('#error-message').textContent = message;
}
function formatTime(seconds) { const total = Math.max(0, Math.floor(seconds)); return `${String(Math.floor(total / 60)).padStart(2,'0')}:${String(total % 60).padStart(2,'0')}`; }

if (!token) showFailure('这个任务链接缺少访问令牌，请从创建任务后的页面重新进入。');
else { poll(); polling = setInterval(poll, 1500); }
