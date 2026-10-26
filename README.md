# 帧文案 Framecopy

一个本地优先、移动端友好的视频文案提取工具。提交前选择“画面文字”或“语音文案”，只运行被选中的本地处理流程。

> 当前版本：`0.5.0`。画面 OCR 延用现有流程；语音转写在 Apple Silicon Mac 上通过可选的 MLX Whisper 本地运行。

## 已实现功能

- 粘贴视频号链接或上传本地视频。
- 在获取或上传前明确选择画面文字或语音文案，不自动猜测。
- 视频号优先通过本机加密保存的元宝 Cookie 自动解析，失败时回退到电脑版微信页面通道。
- 最大 200 MB、最长 30 分钟、最高 4K 的输入校验。
- FFmpeg/ffprobe 视频探测与 OpenCV 候选帧筛选。
- macOS 默认使用 Apple Vision Accurate，其他环境可使用 PaddleOCR。
- 清晰度筛选、感知哈希去重、文字坐标轨迹、稳定末态选择和多帧纠错。
- 在线编辑、自动保存、恢复自动结果和单个 Markdown 可读文案下载。
- 可取消、可重试的本地单并发任务队列。
- SQLite WAL、高熵任务访问令牌与自动过期清理。
- 语音模式生成包含全部已保存文案的 Markdown 可读版。
- 长音频默认按4分钟分块识别，相邻分块重叠2秒，页面显示实时分块进度。
- 每个分块完成后原子保存临时检查点；子进程中断时可在本次任务时限内自动续跑一次。
- 任务成功、失败或取消后删除应用私有目录中的原视频、临时音轨和工作文件。

## 处理流程

```text
视频号链接 / 本地视频 + 明确的提取模式
            ↓
      临时获取并校验视频
            ↓
   画面 OCR 或 MLX Whisper 语音识别
            ↓
     可编辑结果与核查资料
            ↓
  自动删除原视频和临时工作文件
```

## 输出内容

每个完成的任务只向用户提供一个下载文件：

- 可读文案：包含页面上当前已保存的全部文案，不含时间戳、字幕、JSON 或关键帧。下载文件名优先使用视频标题，没有标题时使用第一句文案，并自动清理不合法字符。
- `manifest.json`仅作为程序内部的处理和清理记录，不对用户下载。

## 技术架构

- Web/API：FastAPI + Uvicorn
- 任务存储：SQLite WAL
- 默认队列：本地单工作线程
- 可选队列：Dramatiq + Redis
- 视频处理：FFmpeg/ffprobe + OpenCV
- macOS OCR：Apple Vision Accurate
- 跨平台 OCR：PaddleOCR
- macOS 语音识别：MLX Whisper（可选依赖）
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

### Apple 芯片 Mac 一键安装（推荐）

从 GitHub 下载或克隆项目后，在 Finder 中打开项目文件夹：

1. 双击 `安装帧文案.command`。
2. 根据 macOS 提示完成 Apple 命令行工具或 Homebrew 安装；如果安装器暂停，完成系统安装后再双击一次。
3. 等待 FFmpeg、Python、项目依赖和本地语音模型安装完成。首次安装需要下载较大的模型，并会进行一次实际加载校验。
4. 双击 `启动帧文案.command`，程序会在后台启动并自动打开浏览器。

> **等待提示：**Homebrew 第一次下载通常需要约 3–10 分钟，网络较慢时可能更久。终端可能暂时没有明显变化，这通常不是卡死；请保持网络连接，不要关闭窗口或重复运行安装器。

安装器面向 M1/M2/M3/M4 等 Apple 芯片 Mac，统一使用 Python 3.12，不再要求分别维护主程序和语音识别两个 Python 环境。已有 `.env` 会被保留，不会覆盖。

需要粘贴视频号链接时，还需从 [`ltaoo/wx_channels_download` Releases](https://github.com/ltaoo/wx_channels_download/releases) 下载 macOS Apple 芯片版本并解压，然后双击 `配置视频号解析器.command`，在弹窗中选择解压得到的 `wx_video_download` 文件。第三方解析器未配置时，本地视频上传仍可正常使用。

以后使用时只需双击 `启动帧文案.command`；需要停止后台服务时，双击 `停止帧文案.command`。运行日志保存在 `data/logs/framecopy.log`。

如果 macOS 首次打开 `.command` 文件时阻止运行，可在 Finder 中按住 Control 点击该文件，选择“打开”，再确认一次。不要把其他电脑的 `.env`、`data/` 或 Cookie 文件复制过来。

面向第一次使用的完整分步说明见 [`使用说明.md`](使用说明.md)，其中重点说明了如何在腾讯元宝网页的开发者工具中找到并复制完整 Cookie。

### 手动安装与启动

```bash
brew install ffmpeg uv
uv python install 3.12
uv sync --python 3.12 --extra dev --extra speech
cp .env.example .env
uv run framecopy
```

打开 <http://127.0.0.1:8000>。本地测试阶段不需要邀请码。

macOS 默认使用 Apple Vision，不需要下载 OCR 模型。首次识别会使用系统 Swift 编译器生成本地 Helper。

## 本地语音识别

语音模式当前面向 Apple Silicon macOS。推荐直接使用上方的一键安装器；手动安装时可以与主程序共用 Python 3.12 环境：

```bash
uv sync --python 3.12 --extra dev --extra speech
uv run python scripts/prepare_speech_model.py --verify-load
```

如果需要保留已有的 Python 3.10 主环境，也可以创建独立语音环境：

```bash
uv venv --python 3.12 .venv-speech
uv pip install --python .venv-speech/bin/python mlx-whisper==0.4.3
```

应用会自动优先使用 `.venv-speech/bin/python`，画面 OCR 继续使用原有 `.venv`，两者不会互相污染。

默认模型为 `mlx-community/whisper-large-v3-turbo`。模型首次使用时可能需要下载；识别时音轨不上传到按次收费的语音 API。未安装时，首页会明确提示，并在获取或上传视频前拒绝语音任务；画面文字不受影响。

长音频默认按4分钟分块，分块之间重叠2秒并自动合并时间戳。识别期间任务页会显示已完成分块数，不再长时间停留在42%。最终长转写结果通过临时文件传递，避免大结果堵塞子进程输出管道。

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

寻找 Cookie 时，请在 Chrome/Edge 登录腾讯元宝，打开开发者工具的 **Network**，刷新页面后选择 `yuanbao.tencent.com` 请求，再到 **Headers → Request Headers → Cookie** 复制冒号后的完整一行。不要复制 Response Headers 的 `Set-Cookie`，也不要只复制其中一个字段。详细步骤和安全提醒见 [`使用说明.md`](使用说明.md)。

`wx_channels_download` 是独立第三方项目，具有自己的授权和使用条件；本项目不再分发其二进制文件。

## 配置

环境变量使用 `FRAMECOPY_` 前缀，完整示例见 [`.env.example`](.env.example)。本地启动时自动读取项目根目录的 `.env`，已有系统环境变量优先级更高。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FRAMECOPY_DATA_DIR` | `./data` | 视频、结果、数据库和本机密钥的保存位置 |
| `FRAMECOPY_QUEUE_BACKEND` | `local` | `local` 或 `dramatiq` |
| `FRAMECOPY_MAX_UPLOAD_BYTES` | `209715200` | 上传大小上限 |
| `FRAMECOPY_MAX_DURATION_SECONDS` | `1800` | 视频时长上限（30 分钟） |
| `FRAMECOPY_OCR_PROVIDER` | `auto` | `auto`、`apple_vision` 或 `paddleocr` |
| `FRAMECOPY_SPEECH_PROVIDER` | `mlx_whisper` | 本地语音 Provider |
| `FRAMECOPY_SPEECH_MODEL` | `mlx-community/whisper-large-v3-turbo` | MLX Whisper 模型 |
| `FRAMECOPY_SPEECH_PYTHON` | 自动检测 | 可选，指定独立语音 Python 路径 |
| `FRAMECOPY_SPEECH_TIMEOUT_SECONDS` | `2700` | 单个语音识别任务超时秒数 |
| `FRAMECOPY_SPEECH_CHUNK_SECONDS` | `240` | 长音频识别分块时长（秒） |
| `FRAMECOPY_SPEECH_CHUNK_OVERLAP_SECONDS` | `2` | 相邻识别分块的重叠时长（秒） |
| `FRAMECOPY_WX_PROVIDER_COMMAND` | 见示例 | 视频号 Provider 命令 |

## 测试

```bash
./scripts/check-system.sh
uv run pytest
```

测试覆盖 API 模式路由、任务数据库迁移、源文件清理、密钥存储、视频来源、OCR Provider、画面合并与语音输出生成。

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

- 当前一次任务只运行画面或语音其中一种，不自动合并，不做 AI 改写。
- 语音引擎尚未在本仓库内的真实中文金标视频上完成 MLX Whisper / whisper.cpp A/B；当前实现只启用 MLX Whisper 本地 Provider。
- 不下载私密、无权访问或需要绕过平台权限的内容。
- 视频号解析受第三方下载器、Cookie 有效期和平台接口变化影响。
- 默认为单机本地工具，尚未按公网多用户服务的标准加固。

完整变更见 [`CHANGELOG.md`](CHANGELOG.md)。
