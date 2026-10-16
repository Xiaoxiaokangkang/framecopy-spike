# 帧文案 Framecopy

一个本地优先、移动端友好的视频画面文案提取工具。它从视频画面中提取标题、正文、图表和字幕，合并逐字动画与重复画面，最终输出可编辑、可核查的 Markdown。

> 当前版本：`0.3.0`。已实现画面 OCR；语音转写仍在产品设计阶段，不包含在当前代码中。

## 已实现功能

- 粘贴视频号链接或上传本地视频。
- 视频号优先通过本机加密保存的元宝 Cookie 自动解析，失败时回退到电脑版微信页面通道。
- 最大 200 MB、最长 10 分钟、最高 4K 的输入校验。
- FFmpeg/ffprobe 视频探测与 OpenCV 候选帧筛选。
- macOS 默认使用 Apple Vision Accurate，其他环境可使用 PaddleOCR。
- 清晰度筛选、感知哈希去重、文字坐标轨迹、稳定末态选择和多帧纠错。
- 在线编辑、自动保存、恢复自动结果和整包下载。
- 可取消、可重试的本地单并发任务队列。
- SQLite WAL、高熵任务访问令牌与自动过期清理。

## 处理流程

```text
视频号链接 / 本地视频
            ↓
      获取并校验视频
            ↓
   候选帧筛选与画面去重
            ↓
     Apple Vision / PaddleOCR
            ↓
   重复文字合并与多帧纠错
            ↓
  可编辑结果 + 时间戳 + 关键帧
```

## 输出内容

每个完成的任务可生成：

- `视频文案-纯净版.md`：过滤低置信度孤立内容后的可用文案。
- `视频文案-带时间戳.md`：保留每段文字的视频时间。
- `视频文案-核查说明.md`：低置信度和建议核查项。
- `原始OCR.jsonl`：原始识别数据。
- `manifest.json`：视频、OCR Provider、模型和回退信息。
- 关键帧图片与核查资料 ZIP。

## 技术架构

- Web/API：FastAPI + Uvicorn
- 任务存储：SQLite WAL
- 默认队列：本地单工作线程
- 可选队列：Dramatiq + Redis
- 视频处理：FFmpeg/ffprobe + OpenCV
- macOS OCR：Apple Vision Accurate
- 跨平台 OCR：PaddleOCR
- 前端：原生 HTML/CSS/JavaScript

## 目录结构

```text
framecopy/             Web、API、数据库、队列和处理引擎
framecopy/engine/ocr/  OCR Provider 及 Apple Vision Helper
framecopy/sources/     本地上传与视频号来源 Provider
framecopy/web/         移动端优先页面
scripts/               系统检查与视频号下载适配器
config/                视频号下载器示例配置
tests/                 API、数据库、OCR、合并和来源测试
data/                  运行数据，已被 Git 忽略
```

## 运行要求

- Python 3.10–3.12
- FFmpeg/ffprobe
- `uv`
- macOS：建议安装 Xcode Command Line Tools，供 Swift/Apple Vision Helper 编译
- Linux/Windows：需安装 PaddleOCR 与 PaddlePaddle
- 视频号链接：需另外安装 `wx_channels_download`

## macOS 本地启动

```bash
brew install ffmpeg uv
uv python install 3.10
uv sync --python 3.10 --extra dev
cp .env.example .env
uv run framecopy
```

打开 <http://127.0.0.1:8000>。本地测试阶段不需要邀请码。

macOS 默认使用 Apple Vision，不需要下载 OCR 模型。首次识别会使用系统 Swift 编译器生成本地 Helper。

## PaddleOCR 回退

需要在非 macOS 环境运行，或显式测试 PaddleOCR 时：

```bash
uv sync --python 3.10 --extra dev --extra ocr
uv pip install paddlepaddle==3.0.0 --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/
```

将 `.env` 中的 `FRAMECOPY_OCR_PROVIDER` 改为 `paddleocr`。PaddleOCR 首次运行会下载免费模型文件。

## 视频号链接配置

项目不包含视频号下载器二进制文件。请从 [`ltaoo/wx_channels_download` Releases](https://github.com/ltaoo/wx_channels_download/releases) 下载与当前平台匹配的构建包，并将可执行文件放在：

```text
.tools/wx_video_download/wx_video_download
```

macOS/Linux 需要确保文件可执行：

```bash
chmod +x .tools/wx_video_download/wx_video_download
```

然后在首页打开“视频号自动解析设置”，保存元宝 Web Cookie。程序会：

1. 优先在隔离进程中直接解析、下载和解密。
2. 直接解析失败时，尝试回退到电脑版微信页面通道。
3. Cookie 过期时在首页重新保存。

`wx_channels_download` 是独立第三方项目，具有自己的授权和使用条件；本项目不再分发其二进制文件。

## 配置

环境变量使用 `FRAMECOPY_` 前缀，完整示例见 [`.env.example`](.env.example)。本地启动时自动读取项目根目录的 `.env`，已有系统环境变量优先级更高。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FRAMECOPY_DATA_DIR` | `./data` | 视频、结果、数据库和本机密钥的保存位置 |
| `FRAMECOPY_QUEUE_BACKEND` | `local` | `local` 或 `dramatiq` |
| `FRAMECOPY_MAX_UPLOAD_BYTES` | `209715200` | 上传大小上限 |
| `FRAMECOPY_MAX_DURATION_SECONDS` | `600` | 视频时长上限 |
| `FRAMECOPY_OCR_PROVIDER` | `auto` | `auto`、`apple_vision` 或 `paddleocr` |
| `FRAMECOPY_WX_PROVIDER_COMMAND` | 见示例 | 视频号 Provider 命令 |

## 测试

```bash
./scripts/check-system.sh
uv run pytest
```

测试覆盖 API、任务数据库、密钥存储、视频来源、OCR Provider、文字合并与 Markdown 生成。

## Docker / Redis 模式

默认本地模式不要求 Redis。需要持久任务队列时，可启用 Dramatiq + Redis：

```bash
FRAMECOPY_SECRET_KEY='replace-with-a-long-random-value' docker compose up --build
```

Docker 环境默认使用 PaddleOCR。容器内不会自动获得宿主机的视频号下载器，如果需要链接下载，需要额外挂载和配置。

## 本地数据与安全

- 元宝 Cookie 由随机本机密钥加密保存在 `data/secrets/`。
- Cookie 不写入任务数据库、日志、命令行或页面回显。
- 直连下载使用权限为 `0600` 的临时配置，子进程结束后删除。
- `data/`、`.env`、`.tools/` 和所有下载视频已被 `.gitignore` 排除。
- 请只处理自己拥有或已获授权的视频内容。
- 如果要将服务开放到局域网或公网，必须更换 `FRAMECOPY_SECRET_KEY`，并另行补充认证、HTTPS 和访问频率限制。

## API 概览

- `POST /api/jobs/link`：创建视频号链接任务。
- `POST /api/jobs/upload`：创建本地上传任务。
- `GET /api/jobs/{job_id}`：读取任务状态。
- `GET/PATCH /api/jobs/{job_id}/result`：读取或保存结果。
- `POST /api/jobs/{job_id}/cancel`：取消任务。
- `POST /api/jobs/{job_id}/retry`：对失败任务重试一次。
- `DELETE /api/jobs/{job_id}`：删除任务及本地文件。

## 当前边界

- 当前版本只提取画面文字，不做语音转写或 AI 文案改写。
- 不下载私密、无权访问或需要绕过平台权限的内容。
- 视频号解析受第三方下载器、Cookie 有效期和平台接口变化影响。
- 默认为单机本地工具，尚未按公网多用户服务的标准加固。

完整变更见 [`CHANGELOG.md`](CHANGELOG.md)。
