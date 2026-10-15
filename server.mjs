import http from 'node:http';
import { createReadStream, createWriteStream, existsSync } from 'node:fs';
import { mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { markdownFor } from './lib/merge.mjs';

const root = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.join(root, 'web');
const dataRoot = path.join(root, 'data', 'jobs');
const extractor = path.join(root, 'bin', 'framecopy-extractor');
const port = Number(process.env.PORT || 4173);
const maxBytes = 200 * 1024 * 1024;
const active = new Map();
await mkdir(dataRoot, { recursive: true });

const mimeTypes = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.jpg': 'image/jpeg',
  '.json': 'application/json; charset=utf-8', '.md': 'text/markdown; charset=utf-8',
};

function json(res, status, value) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(JSON.stringify(value));
}

function safeName(value) {
  return String(value || 'video').replace(/[\\/\0\r\n]/g, '_').slice(0, 180);
}

function parseJobUrl(url) {
  const parts = url.pathname.split('/').filter(Boolean);
  return { parts, jobId: parts[2], action: parts.slice(3).join('/') };
}

function hashToken(token) {
  return createHash('sha256').update(String(token)).digest('hex');
}

async function readJsonBody(req, limit = 1024 * 1024) {
  const chunks = []; let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new Error('请求内容过大');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
}

function authorized(url, metadata) {
  const supplied = url.searchParams.get('token') || '';
  if (metadata.access_token) return supplied === metadata.access_token; // Spike 0.1 旧任务兼容
  const expected = Buffer.from(metadata.access_token_hash || '', 'hex');
  const actual = Buffer.from(hashToken(supplied), 'hex');
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

async function metadataFor(jobId) {
  if (!/^[0-9a-f-]{36}$/.test(jobId || '')) return null;
  try { return JSON.parse(await readFile(path.join(dataRoot, jobId, 'job.json'), 'utf8')); }
  catch { return null; }
}

async function stateFor(jobId) {
  const jobDir = path.join(dataRoot, jobId);
  const meta = await metadataFor(jobId);
  if (!meta) return null;
  let progress = { status: 'queued', progress: 1, stage: '等待处理', detail: '任务已创建' };
  try { progress = JSON.parse(await readFile(path.join(jobDir, 'progress.json'), 'utf8')); } catch {}
  return { id: jobId, source_name: meta.source_name, created_at: meta.created_at, mode: meta.mode, ...progress };
}

async function regenerate(jobDir, segments) {
  await writeFile(path.join(jobDir, 'segments.user.json'), JSON.stringify(segments, null, 2));
  await Promise.all([
    writeFile(path.join(jobDir, '视频文案-纯净版.md'), markdownFor(segments, false)),
    writeFile(path.join(jobDir, '视频文案-带时间戳.md'), markdownFor(segments, true)),
  ]);
}

function startExtractor(jobId, inputPath, mode) {
  const jobDir = path.join(dataRoot, jobId);
  const child = spawn(extractor, [inputPath, jobDir, mode], { stdio: ['ignore', 'ignore', 'pipe'] });
  active.set(jobId, child);
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
  child.on('exit', async (code) => {
    active.delete(jobId);
    if (code !== 0 && !existsSync(path.join(jobDir, 'progress.json'))) {
      await writeFile(path.join(jobDir, 'progress.json'), JSON.stringify({ status: 'failed', progress: 100, stage: '处理失败', detail: stderr.trim() || `提取器退出码 ${code}` }, null, 2));
    }
  });
}

async function handleApi(req, res, url) {
  if (req.method === 'GET' && url.pathname === '/api/health') {
    return json(res, 200, { ok: true, extractor_ready: existsSync(extractor), platform: process.platform });
  }

  if (req.method === 'POST' && url.pathname === '/api/jobs/upload') {
    if (!existsSync(extractor)) return json(res, 503, { error: '提取器尚未构建，请先运行 npm run build:extractor' });
    const length = Number(req.headers['content-length'] || 0);
    if (!length) return json(res, 411, { error: '缺少文件大小' });
    if (length > maxBytes) return json(res, 413, { error: 'FILE_TOO_LARGE', message: '视频不能超过 200 MB' });
    const sourceName = safeName(decodeURIComponent(String(req.headers['x-file-name'] || 'video')));
    // Spike 默认只走高精度链路，避免快速 OCR 对小字和中文产生较多乱码。
    const mode = 'accurate';
    const extension = path.extname(sourceName).slice(0, 12) || '.video';
    const jobId = randomUUID();
    const accessToken = randomBytes(24).toString('base64url');
    const jobDir = path.join(dataRoot, jobId);
    const inputDir = path.join(jobDir, 'input');
    await mkdir(inputDir, { recursive: true });
    const inputPath = path.join(inputDir, `source${extension}`);
    await writeFile(path.join(jobDir, 'job.json'), JSON.stringify({ id: jobId, source_name: sourceName, mode, access_token_hash: hashToken(accessToken), created_at: new Date().toISOString() }, null, 2));
    await writeFile(path.join(jobDir, 'progress.json'), JSON.stringify({ status: 'uploading', progress: 1, stage: '正在接收视频', detail: sourceName }, null, 2));
    const sink = createWriteStream(inputPath, { flags: 'wx' });
    let received = 0; let aborted = false;
    req.on('data', (chunk) => {
      received += chunk.length;
      if (received > maxBytes) { aborted = true; req.destroy(); sink.destroy(); }
    });
    req.pipe(sink);
    sink.on('error', async () => {
      await rm(jobDir, { recursive: true, force: true });
      if (!res.headersSent) json(res, aborted ? 413 : 500, { error: aborted ? 'FILE_TOO_LARGE' : 'UPLOAD_INTERRUPTED' });
    });
    sink.on('finish', async () => {
      await writeFile(path.join(jobDir, 'progress.json'), JSON.stringify({ status: 'queued', progress: 3, stage: '准备处理', detail: '视频上传完成' }, null, 2));
      json(res, 201, { id: jobId, token: accessToken, status_url: `/api/jobs/${jobId}?token=${accessToken}` });
      startExtractor(jobId, inputPath, mode);
    });
    return;
  }

  if (!url.pathname.startsWith('/api/jobs/')) return json(res, 404, { error: 'NOT_FOUND' });
  const { jobId, action } = parseJobUrl(url);
  const metadata = await metadataFor(jobId);
  if (!metadata) return json(res, 404, { error: 'TASK_NOT_FOUND' });
  if (!authorized(url, metadata)) return json(res, 403, { error: 'ACCESS_DENIED' });
  const jobDir = path.join(dataRoot, jobId);

  if (req.method === 'GET' && !action) return json(res, 200, await stateFor(jobId));
  if (req.method === 'GET' && action === 'result') {
    try {
      const segments = JSON.parse(await readFile(path.join(jobDir, 'segments.user.json'), 'utf8'));
      const manifest = JSON.parse(await readFile(path.join(jobDir, 'manifest.json'), 'utf8'));
      return json(res, 200, { segments, manifest });
    } catch { return json(res, 409, { error: 'RESULT_NOT_READY' }); }
  }
  if (req.method === 'PATCH' && action === 'result') {
    try {
      const body = await readJsonBody(req);
      if (!Array.isArray(body.segments) || body.segments.length > 5000) throw new Error('结果格式无效');
      const automatic = JSON.parse(await readFile(path.join(jobDir, 'segments.auto.json'), 'utf8'));
      const segments = body.segments.map((segment, index) => ({ ...automatic[index], ...segment, sequence: index + 1, user_text: String(segment.user_text ?? segment.clean_text ?? '').slice(0, 20_000) }));
      await regenerate(jobDir, segments);
      return json(res, 200, { saved: true, saved_at: new Date().toISOString() });
    } catch (error) { return json(res, 400, { error: error.message }); }
  }
  if (req.method === 'POST' && action === 'restore') {
    const segments = JSON.parse(await readFile(path.join(jobDir, 'segments.auto.json'), 'utf8'));
    await regenerate(jobDir, segments);
    return json(res, 200, { restored: true, segments });
  }
  if (req.method === 'POST' && action === 'cancel') {
    const child = active.get(jobId);
    if (child) child.kill('SIGTERM');
    await writeFile(path.join(jobDir, 'progress.json'), JSON.stringify({ status: 'cancelled', progress: 100, stage: '已取消', detail: '处理已由用户取消' }, null, 2));
    return json(res, 200, { cancelled: true });
  }
  if (req.method === 'DELETE' && !action) {
    const child = active.get(jobId);
    if (child) child.kill('SIGTERM');
    await rm(jobDir, { recursive: true, force: true });
    return json(res, 200, { deleted: true });
  }
  if (req.method === 'GET' && action.startsWith('files/')) {
    const name = decodeURIComponent(action.slice(6));
    const allowed = new Set(['视频文案-纯净版.md', '视频文案-带时间戳.md', 'raw-ocr.jsonl', 'manifest.json']);
    if (!allowed.has(name)) return json(res, 404, { error: 'FILE_NOT_FOUND' });
    const file = path.join(jobDir, name);
    try {
      const info = await stat(file);
      res.writeHead(200, { 'content-type': mimeTypes[path.extname(name)] || 'application/octet-stream', 'content-length': info.size, 'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(name)}` });
      createReadStream(file).pipe(res);
    } catch { json(res, 404, { error: 'FILE_NOT_FOUND' }); }
    return;
  }
  if (req.method === 'GET' && action.startsWith('evidence/')) {
    const name = path.basename(action.slice(9));
    const file = path.join(jobDir, 'evidence', name);
    try {
      const info = await stat(file);
      res.writeHead(200, { 'content-type': 'image/jpeg', 'content-length': info.size, 'cache-control': 'private, max-age=3600' });
      createReadStream(file).pipe(res);
    } catch { json(res, 404, { error: 'FRAME_NOT_FOUND' }); }
    return;
  }
  return json(res, 404, { error: 'NOT_FOUND' });
}

async function serveStatic(res, pathname) {
  const relative = pathname === '/' ? 'index.html' : pathname.slice(1);
  const file = path.resolve(webRoot, relative);
  if (!file.startsWith(`${webRoot}${path.sep}`) && file !== path.join(webRoot, 'index.html')) return false;
  try {
    const info = await stat(file);
    if (!info.isFile()) return false;
    res.writeHead(200, { 'content-type': mimeTypes[path.extname(file)] || 'application/octet-stream', 'content-length': info.size });
    createReadStream(file).pipe(res);
    return true;
  } catch { return false; }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  try {
    if (url.pathname.startsWith('/api/')) return await handleApi(req, res, url);
    if (await serveStatic(res, url.pathname)) return;
    res.writeHead(404); res.end('Not found');
  } catch (error) {
    console.error(error);
    if (!res.headersSent) json(res, 500, { error: 'INTERNAL_ERROR', message: error.message });
    else res.end();
  }
});

server.listen(port, '127.0.0.1', () => {
  console.log(`帧文案 Spike: http://127.0.0.1:${port}`);
  console.log(`提取器: ${existsSync(extractor) ? '已就绪' : '未构建'}`);
});
