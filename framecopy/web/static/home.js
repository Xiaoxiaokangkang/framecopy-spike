const tabs = document.querySelectorAll('.tab');
const panels = { link: document.querySelector('#link-form'), upload: document.querySelector('#upload-form') };
const message = document.querySelector('#message');

function selectTab(name) {
  tabs.forEach(tab => tab.classList.toggle('active', tab.dataset.tab === name));
  Object.entries(panels).forEach(([key, panel]) => panel.classList.toggle('active', key === name));
  message.hidden = true;
}
tabs.forEach(tab => tab.addEventListener('click', () => selectTab(tab.dataset.tab)));
if (new URLSearchParams(location.search).get('tab') === 'upload') selectTab('upload');

document.querySelector('#paste-button').addEventListener('click', async () => {
  try { document.querySelector('#share-text').value = await navigator.clipboard.readText(); }
  catch { showError('浏览器没有授予剪贴板权限，请长按输入框后选择“粘贴”。'); }
});

document.querySelector('#video-file').addEventListener('change', event => {
  const file = event.target.files[0];
  document.querySelector('#file-name').textContent = file ? `${file.name} · ${formatBytes(file.size)}` : '';
});

async function refreshProviderStatus() {
  const response = await fetch('/api/providers/wx-channel/status');
  const status = await response.json();
  const element = document.querySelector('#provider-status');
  element.textContent = status.message;
  element.classList.remove('ok', 'bad');
  element.classList.add(status.available ? 'ok' : 'bad');
  document.querySelector('#delete-credential').hidden = !status.direct_configured;
  if (!status.available && !status.direct_configured) document.querySelector('#provider-setup').open = true;
}

refreshProviderStatus().catch(() => {
  const element = document.querySelector('#provider-status');
  element.textContent = '无法读取视频号解析状态';
  element.classList.add('bad');
});

document.querySelector('#save-credential').addEventListener('click', async () => {
  const input = document.querySelector('#yuanbao-cookie');
  const button = document.querySelector('#save-credential');
  const status = document.querySelector('#credential-message');
  if (!input.value.trim()) return showCredentialMessage('请先粘贴 Cookie。', true);
  button.disabled = true;
  try {
    const response = await fetch('/api/providers/wx-channel/credential', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cookie: input.value})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '凭证保存失败');
    input.value = '';
    status.hidden = false;
    status.textContent = '已加密保存。现在可以粘贴视频号链接测试。';
    status.classList.remove('danger');
    await refreshProviderStatus();
  } catch (error) {
    showCredentialMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
});

document.querySelector('#delete-credential').addEventListener('click', async () => {
  await fetch('/api/providers/wx-channel/credential', {method: 'DELETE'});
  showCredentialMessage('已删除本机保存的自动解析凭证。', false);
  await refreshProviderStatus();
});

document.querySelector('#link-form').addEventListener('submit', async event => {
  event.preventDefault();
  await submit('/api/jobs/link', {
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: document.querySelector('#share-text').value})
  });
});

document.querySelector('#upload-form').addEventListener('submit', async event => {
  event.preventDefault();
  const file = document.querySelector('#video-file').files[0];
  if (!file) return showError('请先选择视频文件。');
  if (file.size > 200 * 1024 * 1024) return showError('视频超过 200 MB 限制。');
  const data = new FormData();
  data.append('file', file);
  await submit('/api/jobs/upload', {body: data});
});

async function submit(url, options) {
  message.hidden = true;
  const button = document.querySelector('.panel.active .primary');
  const original = button.innerHTML;
  button.disabled = true;
  button.textContent = '正在创建任务…';
  try {
    const response = await fetch(url, {method: 'POST', ...options});
    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === 'object' ? detail.message : detail || '创建任务失败');
    }
    sessionStorage.setItem(`framecopy:${data.job_id}`, data.access_token);
    location.href = `/jobs/${data.job_id}/view#token=${encodeURIComponent(data.access_token)}`;
  } catch (error) {
    showError(error.message);
    button.disabled = false;
    button.innerHTML = original;
  }
}

function showError(text) { message.textContent = text; message.hidden = false; }
function showCredentialMessage(text, danger) {
  const element = document.querySelector('#credential-message');
  element.textContent = text;
  element.hidden = false;
  element.classList.toggle('danger', danger);
}
function formatBytes(value) { return value < 1024 ** 2 ? `${(value / 1024).toFixed(0)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`; }

